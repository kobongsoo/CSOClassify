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
    ///
    /// 입력 텍스트는 먼저 `clean_for_embed` 로 다듬는다 — 파이썬은 추출 직후
    /// `clean.clean_text()` 를 거친 텍스트를 임베딩하는데, Rust 가 추출 원문을 그대로
    /// 넣으면 끝 개행 하나 때문에 같은 문서인데도 벡터가 달라진다(실측 코사인 0.98).
    /// 벡터는 `class_seed.jsonl` 과 코사인으로 비교되므로, 이 차이는 전파 판정을
    /// 조용히 흔든다. 그래서 '임베딩에 넣기 직전' 에 파이썬과 같은 모양으로 맞춘다.
    pub fn embed_document(&mut self, text: &str) -> Option<Vec<f32>> {
        let text = &clean_for_embed(text);
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

/// 임베딩 입력 정제 — 파이썬 `csoclassify/clean.py::clean_text()` 와 같은 규칙.
///
/// 순서까지 파이썬과 맞춰야 결과가 같아진다:
///   1) `..PAGE:N` 만 있는 줄 제거(사이냅 페이지 마커 — 본문이 아니다)
///   2) 제어문자 제거(탭·개행은 남긴다 — 문서 구조를 지워버리면 안 되므로)
///   3) CRLF·CR → LF
///   4) 줄 안의 연속 공백/탭 → 공백 1개
///   5) 각 줄 끝 공백 제거
///   6) 빈 줄 3개 이상 → 2개(문단 경계는 남긴다)
///   7) 양끝 공백 제거  ← 실측된 차이의 대부분이 여기(파일 끝 개행)에서 났다
///
/// 파이썬의 (1) NFC 유니코드 정규화만 빠져 있다 — 새 크레이트를 들이지 않는다는
/// 제약 때문이다. 추출기들이 이미 완성형 한글을 내놓아 실측 18건에서는 차이가
/// 없었지만, 자모가 분리된 문서가 들어오면 그 문서만 벡터가 달라질 수 있다.
pub fn clean_for_embed(text: &str) -> String {
    if text.is_empty() { return String::new(); }

    // (1) 페이지 마커 라인 제거. 정규식 대신 줄 단위로 직접 본다 — 규칙이 단순하고
    //     (공백* "..PAGE:" 숫자+ 공백*) 정규식 크레이트를 여기까지 끌고 올 이유가 없다.
    let mut s = String::with_capacity(text.len());
    for (i, line) in text.split('\n').enumerate() {
        if i > 0 { s.push('\n'); }
        if !is_page_marker(line) { s.push_str(line); }
    }

    // (2)(3) 제어문자 제거 + 개행 통일.
    //     CRLF 는 개행 하나여야 하므로 CR 을 볼 때 바로 뒤 LF 를 삼킨다(peek).
    //     단독 CR(옛 맥 개행)은 그 자리에서 LF 로 바꾼다.
    let mut t = String::with_capacity(s.len());
    let mut it = s.chars().peekable();
    while let Some(ch) = it.next() {
        match ch {
            '\r' => {
                if it.peek() == Some(&'\n') { it.next(); }   // CRLF → 개행 하나
                t.push('\n');
            }
            '\t' | '\n' => t.push(ch),
            c if (c as u32) < 0x20 || (c as u32) == 0x7f => {}   // 나머지 제어문자는 버린다
            c => t.push(c),
        }
    }

    // (4)(5) 줄 안 연속 공백 축약 + 줄 끝 공백 제거.
    let mut u = String::with_capacity(t.len());
    for (i, line) in t.split('\n').enumerate() {
        if i > 0 { u.push('\n'); }
        let mut prev_ws = false;
        let mut line_out = String::with_capacity(line.len());
        for c in line.chars() {
            if c == ' ' || c == '\t' {
                // 연속 공백/탭은 공백 하나로 — 파이썬 [ \t]{2,} → " " 와 같다.
                if !prev_ws { line_out.push(' '); }
                prev_ws = true;
            } else {
                prev_ws = false;
                line_out.push(c);
            }
        }
        u.push_str(line_out.trim_end());
    }

    // (6) 빈 줄 3개 이상 → 2개. 줄 수를 세며 한 번에 처리한다.
    let mut out = String::with_capacity(u.len());
    let mut nl = 0usize;
    for c in u.chars() {
        if c == '\n' {
            nl += 1;
            if nl <= 2 { out.push('\n'); }
        } else {
            nl = 0;
            out.push(c);
        }
    }

    // (7) 양끝 공백 제거.
    out.trim().to_string()
}

/// `..PAGE:12` 처럼 페이지 마커만 있는 줄인가(앞뒤 공백 허용).
fn is_page_marker(line: &str) -> bool {
    let t = line.trim();
    match t.strip_prefix("..PAGE:") {
        Some(rest) => !rest.is_empty() && rest.chars().all(|c| c.is_ascii_digit()),
        None => false,
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

#[cfg(test)]
mod tests {
    use super::clean_for_embed;

    // 파이썬 clean.clean_text() 와 같은 결과가 나오는지 — 실측 차이의 원인이었던
    // '파일 끝 개행'이 핵심이다. 이게 남으면 같은 문서인데 벡터가 달라진다.
    #[test]
    fn trims_trailing_newline() {
        assert_eq!(clean_for_embed("hello world test document\n"), "hello world test document");
        assert_eq!(clean_for_embed("\n\n  본문  \n\n"), "본문");
    }

    #[test]
    fn removes_page_markers() {
        assert_eq!(clean_for_embed("가\n..PAGE:12\n나"), "가\n\n나");
        assert_eq!(clean_for_embed("가\n  ..PAGE:3  \n나"), "가\n\n나");
        // 숫자가 아니면 마커가 아니다 — 본문을 지우면 안 된다.
        assert_eq!(clean_for_embed("..PAGE:abc"), "..PAGE:abc");
    }

    #[test]
    fn normalizes_newlines_and_controls() {
        // CRLF 는 개행 하나여야 한다(둘로 늘어나면 빈 줄이 생겨 토큰이 달라진다).
        assert_eq!(clean_for_embed("가\r\n나"), "가\n나");
        // 단독 CR(옛 맥 개행)도 개행 하나.
        assert_eq!(clean_for_embed("가\r나"), "가\n나");
        // 탭·개행 외 제어문자는 버린다.
        assert_eq!(clean_for_embed("가\u{0}\u{1}나"), "가나");
        // 탭은 남되 연속 공백 축약 규칙을 탄다.
        assert_eq!(clean_for_embed("가\t나"), "가 나");
    }

    #[test]
    fn collapses_spaces_and_blank_lines() {
        assert_eq!(clean_for_embed("가   나"), "가 나");
        assert_eq!(clean_for_embed("가  \t 나"), "가 나");
        // 줄 끝 공백 제거.
        assert_eq!(clean_for_embed("가   \n나"), "가\n나");
        // 빈 줄 3개 이상 → 2개(문단 경계는 남긴다).
        assert_eq!(clean_for_embed("가\n\n\n\n\n나"), "가\n\n나");
        assert_eq!(clean_for_embed("가\n\n나"), "가\n\n나");
    }

    #[test]
    fn empty_stays_empty() {
        assert_eq!(clean_for_embed(""), "");
        assert_eq!(clean_for_embed("   \n\t\n  "), "");
    }
}
