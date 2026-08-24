//! 임베딩(전파/--vector-only 용) — e5-small-ko ONNX 를 onnxruntime 으로 돌린다.
//! 모델·런타임은 바이너리에 넣지 않고 '외부 파일'로 로딩한다(pdfium 방식):
//!   · onnxruntime.dll : 환경변수 ORT_DYLIB_PATH → exe 옆 onnxruntime.dll
//!   · 모델 폴더        : 환경변수 CSO_MODEL → exe 옆 models/e5-small-ko/
//! ko-pii(embed) 파이프라인과 동일: 'passage: ' 프리픽스 + 슬라이딩청크 + 평균풀링 + 청크평균 + L2.

use std::path::PathBuf;

use ort::session::{builder::GraphOptimizationLevel, Session};
use ort::value::Tensor;
use tokenizers::Tokenizer;

// e5-small-ko 스펙(config.MODELS 와 동일).
const PASSAGE_PREFIX: &str = "passage: ";
const MODEL_MAX_TOKENS: usize = 512;
const CHUNK_MAX_TOKENS: usize = 512; // DEFAULT_MAX_TOKENS
const CHUNK_OVERLAP: usize = 32;     // DEFAULT_OVERLAP
pub const DIM: usize = 384;

/// 임베더 — onnxruntime 세션 + 토크나이저.
pub struct Embedder {
    session: Session,
    tokenizer: Tokenizer,
    prefix_ids_len: usize,
}

/// 모델 폴더 결정: CSO_MODEL → exe 옆 models/e5-small-ko.
fn model_dir() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("CSO_MODEL") {
        let pb = PathBuf::from(p);
        if pb.is_dir() { return Some(pb); }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let pb = dir.join("models").join("e5-small-ko");
            if pb.is_dir() { return Some(pb); }
        }
    }
    None
}

/// onnxruntime 동적 라이브러리 경로: ORT_DYLIB_PATH 유지 or exe 옆 onnxruntime.dll 지정.
fn ensure_dylib() {
    if std::env::var("ORT_DYLIB_PATH").map(|v| !v.is_empty()).unwrap_or(false) {
        return;
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            for name in ["onnxruntime.dll", "libonnxruntime.so", "libonnxruntime.dylib"] {
                let pb = dir.join(name);
                if pb.is_file() {
                    std::env::set_var("ORT_DYLIB_PATH", pb);
                    return;
                }
            }
        }
    }
}

impl Embedder {
    /// 모델 로드. 실패(모델/런타임 없음·초기화 실패)면 None.
    pub fn load() -> Option<Embedder> {
        ensure_dylib();
        let dir = model_dir()?;
        let tok = Tokenizer::from_file(dir.join("tokenizer.json")).ok()?;
        let session = Session::builder().ok()?
            .with_optimization_level(GraphOptimizationLevel::Level3).ok()?
            .commit_from_file(dir.join("model.onnx")).ok()?;
        // 프리픽스 토큰 길이(특수토큰 제외) — 청크 예산 계산용.
        let prefix_ids_len = tok.encode(PASSAGE_PREFIX, false).map(|e| e.get_ids().len()).unwrap_or(0);
        Some(Embedder { session, tokenizer: tok, prefix_ids_len })
    }

    /// 문서 임베딩(정규화된 384벡터). 청크별 평균풀링 → 청크평균 → L2.
    pub fn embed_document(&mut self, text: &str) -> Option<Vec<f32>> {
        if text.trim().is_empty() { return None; }
        let chunks = self.split_chunks(text);
        if chunks.is_empty() { return None; }
        let mut acc = vec![0f32; DIM];
        let mut n = 0usize;
        for c in &chunks {
            let v = self.embed_one(c)?;
            for i in 0..DIM { acc[i] += v[i]; }
            n += 1;
        }
        if n == 0 { return None; }
        for i in 0..DIM { acc[i] /= n as f32; }
        l2_normalize(&mut acc);
        Some(acc)
    }

    /// 본문을 프리픽스+특수토큰 예산에 맞춰 슬라이딩 청크(텍스트)로 분할.
    fn split_chunks(&self, text: &str) -> Vec<String> {
        let enc = match self.tokenizer.encode(text, false) { Ok(e) => e, Err(_) => return vec![] };
        let ids = enc.get_ids();
        if ids.is_empty() { return vec![]; }
        let hard_cap = CHUNK_MAX_TOKENS.min(MODEL_MAX_TOKENS);
        let effective = hard_cap.saturating_sub(self.prefix_ids_len + 2).max(8);
        let eff_overlap = CHUNK_OVERLAP.min(effective - 1);
        let mut out = vec![];
        for (s, e) in sliding_windows(ids.len(), effective, eff_overlap) {
            let sub = &ids[s..e];
            if let Ok(txt) = self.tokenizer.decode(sub, false) {
                out.push(txt);
            }
        }
        out
    }

    /// 청크 문자열 1개 → 평균풀링 벡터(정규화 전).
    fn embed_one(&mut self, chunk_text: &str) -> Option<Vec<f32>> {
        let full = format!("{}{}", PASSAGE_PREFIX, chunk_text);
        let enc = self.tokenizer.encode(full, true).ok()?;
        let mut ids: Vec<i64> = enc.get_ids().iter().map(|&x| x as i64).collect();
        let mut mask: Vec<i64> = enc.get_attention_mask().iter().map(|&x| x as i64).collect();
        if ids.len() > MODEL_MAX_TOKENS { ids.truncate(MODEL_MAX_TOKENS); mask.truncate(MODEL_MAX_TOKENS); }
        let seq = ids.len();
        let tt = vec![0i64; seq];
        let input_ids = Tensor::from_array(([1usize, seq], ids)).ok()?;
        let attn = Tensor::from_array(([1usize, seq], mask.clone())).ok()?;
        let ttids = Tensor::from_array(([1usize, seq], tt)).ok()?;
        let outputs = self.session.run(ort::inputs![
            "input_ids" => input_ids,
            "attention_mask" => attn,
            "token_type_ids" => ttids,
        ]).ok()?;
        let (shape, data) = outputs["last_hidden_state"].try_extract_tensor::<f32>().ok()?;
        // shape = [1, seq, DIM]
        let sq = shape[1] as usize;
        let dim = shape[2] as usize;
        // 마스크 평균 풀링.
        let mut vec = vec![0f32; dim];
        let mut denom = 0f32;
        for t in 0..sq {
            let m = mask.get(t).copied().unwrap_or(0) as f32;
            if m == 0.0 { continue; }
            denom += m;
            let base = t * dim;
            for d in 0..dim { vec[d] += data[base + d] * m; }
        }
        let denom = if denom > 1e-9 { denom } else { 1.0 };
        for d in 0..dim { vec[d] /= denom; }
        Some(vec)
    }
}

/// L2 정규화(0벡터는 그대로).
pub fn l2_normalize(v: &mut [f32]) {
    let n: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    if n > 1e-9 { for x in v.iter_mut() { *x /= n; } }
}

/// 슬라이딩 윈도우 경계(ko-pii chunk.sliding_windows 동일).
fn sliding_windows(seq_len: usize, max_tokens: usize, overlap: usize) -> Vec<(usize, usize)> {
    if seq_len == 0 { return vec![]; }
    if seq_len <= max_tokens { return vec![(0, seq_len)]; }
    let step = max_tokens - overlap;
    let mut spans = vec![];
    let mut start = 0;
    while start < seq_len {
        let end = (start + max_tokens).min(seq_len);
        spans.push((start, end));
        if end >= seq_len { break; }
        start += step;
    }
    spans
}
