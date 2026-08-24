//! 임베딩 라벨 전파(Signal B) — ko-pii classify/propagate.py 포팅.
//! seed(등급 붙은 문서 벡터)와 코사인 비교로 보류(등급 없음) 문서에 등급 '후보'를 만든다.
//! 정책: near-dup 상속 / 최근접 너무 멀면 보류 / 그 외 k-최근접 유사도 가중 다수결.

use std::fs;

use serde_json::{json, Value};

// 전파 임계값(propagate.py 상수).
const DEFAULT_K: usize = 10;
const DUP_THRESHOLD: f32 = 0.950; // 1등 sim ≥ → 그 등급 상속
const MIN_SIM: f32 = 0.55;        // 1등 sim < → 보류
const MIN_SHARE: f32 = 0.70;      // 1등 득표율 < → 보류

/// seed 인덱스 — L2 정규화된 벡터 + 등급 + 파일경로.
pub struct SeedIndex {
    vectors: Vec<Vec<f32>>, // 각 행 L2 정규화
    grades: Vec<String>,
    files: Vec<String>,
}

impl SeedIndex {
    pub fn empty() -> Self { SeedIndex { vectors: vec![], grades: vec![], files: vec![] } }
    pub fn size(&self) -> usize { self.grades.len() }

    /// 리스트로 구성(행별 L2 정규화). 공통 내부.
    fn from_lists(mut vecs: Vec<Vec<f32>>, grades: Vec<String>, files: Vec<String>) -> Self {
        for v in vecs.iter_mut() { l2(v); }
        SeedIndex { vectors: vecs, grades, files }
    }

    /// 외부 seed 저장소(cso_seed.jsonl): 각 줄 {file, grade, vector}. 깨진 줄은 건너뜀.
    pub fn from_seed_file(path: &str) -> Self {
        let mut vecs = vec![]; let mut grades = vec![]; let mut files = vec![];
        if let Ok(txt) = fs::read_to_string(path) {
            for line in txt.lines() {
                let line = line.trim();
                if line.is_empty() { continue; }
                let e: Value = match serde_json::from_str(line) { Ok(v) => v, Err(_) => continue };
                let g = e.get("grade").and_then(|g| g.as_str()).unwrap_or("");
                if !matches!(g, "C" | "S" | "O") { continue; }
                let v = match e.get("vector").and_then(|v| v.as_array()) { Some(a) => a, None => continue };
                let vec: Vec<f32> = v.iter().filter_map(|x| x.as_f64().map(|f| f as f32)).collect();
                if vec.is_empty() { continue; }
                vecs.push(vec);
                grades.push(g.to_string());
                files.push(e.get("file").and_then(|f| f.as_str()).unwrap_or("").to_string());
            }
        }
        Self::from_lists(vecs, grades, files)
    }

    /// 코퍼스 내부 seed: seed_eligible + 유효등급 + 벡터 존재 문서만.
    pub fn from_records(items: &[(Option<Vec<f32>>, Option<String>, bool, String)]) -> Self {
        let mut vecs = vec![]; let mut grades = vec![]; let mut files = vec![];
        for (vec, grade, seed_eligible, file) in items {
            if !seed_eligible { continue; }
            let g = match grade { Some(g) if matches!(g.as_str(), "C"|"S"|"O") => g.clone(), _ => continue };
            let v = match vec { Some(v) if !v.is_empty() => v.clone(), _ => continue };
            vecs.push(v); grades.push(g); files.push(file.clone());
        }
        Self::from_lists(vecs, grades, files)
    }

    /// 두 인덱스 병합(한쪽 비면 나머지 반환). 벡터는 이미 정규화됨.
    pub fn merge(mut a: SeedIndex, b: SeedIndex) -> SeedIndex {
        if a.size() == 0 { return b; }
        if b.size() == 0 { return a; }
        a.vectors.extend(b.vectors);
        a.grades.extend(b.grades);
        a.files.extend(b.files);
        a
    }

    /// 질의 벡터와 모든 seed 의 코사인 유사도.
    fn cosine(&self, vec: &[f32]) -> Vec<f32> {
        if self.size() == 0 { return vec![]; }
        let mut q = vec.to_vec();
        let n: f32 = q.iter().map(|x| x * x).sum::<f32>().sqrt();
        let n = if n > 0.0 { n } else { 1.0 };
        for x in q.iter_mut() { *x /= n; }
        self.vectors.iter().map(|sv| {
            sv.iter().zip(q.iter()).map(|(a, b)| a * b).sum::<f32>()
        }).collect()
    }
}

/// 전파 신호(Signal B) 결과.
pub struct EmbedSignal {
    pub grade: Option<String>,       // "C"/"S"/"O" 또는 None(보류)
    pub confidence: f32,
    pub method: String,              // dup_inherit|knn_vote|too_far|mixed|no_seeds
    pub top_sim: f32,
    pub neighbors: Vec<(String, String, f32)>, // (file, grade, sim) 상위 5
}

impl EmbedSignal {
    /// signals.embed 칸용 dict(Python EmbedSignal.as_dict 동일 스키마).
    pub fn as_dict(&self) -> Value {
        json!({
            "grade": self.grade,
            "confidence": round3(self.confidence),
            "seed_eligible": false,
            "method": self.method,
            "top_sim": round3(self.top_sim),
            "neighbors": self.neighbors.iter().map(|(f, g, s)| json!({
                "file": f, "grade": g, "sim": round3(*s)
            })).collect::<Vec<_>>(),
        })
    }
}

fn round3(x: f32) -> f32 { (x * 1000.0).round() / 1000.0 }
fn l2(v: &mut [f32]) {
    let n: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    if n > 1e-9 { for x in v.iter_mut() { *x /= n; } }
}

/// 벡터 1개 전파 — propagate.py 판정순서 그대로.
pub fn propagate(vec: &[f32], seeds: &SeedIndex) -> EmbedSignal {
    if seeds.size() == 0 {
        return EmbedSignal { grade: None, confidence: 0.0, method: "no_seeds".into(), top_sim: 0.0, neighbors: vec![] };
    }
    let sims = seeds.cosine(vec);
    // 유사도 내림차순 상위 k.
    let mut order: Vec<usize> = (0..sims.len()).collect();
    order.sort_by(|&a, &b| sims[b].partial_cmp(&sims[a]).unwrap_or(std::cmp::Ordering::Equal));
    order.truncate(DEFAULT_K);
    let top: Vec<(String, String, f32)> = order.iter().map(|&i| {
        (seeds.files.get(i).cloned().unwrap_or_default(), seeds.grades[i].clone(), sims[i])
    }).collect();
    let (_, top1_grade, top1_sim) = top[0].clone();
    let neigh: Vec<(String, String, f32)> = top.iter().take(5).cloned().collect();

    // ② near-dup 상속.
    if top1_sim >= DUP_THRESHOLD {
        return EmbedSignal { grade: Some(top1_grade), confidence: top1_sim, method: "dup_inherit".into(), top_sim: top1_sim, neighbors: neigh };
    }
    // ③ 너무 멂 → 보류.
    if top1_sim < MIN_SIM {
        return EmbedSignal { grade: None, confidence: top1_sim, method: "too_far".into(), top_sim: top1_sim, neighbors: neigh };
    }
    // ④ 유사도 가중 다수결.
    let mut buckets: std::collections::HashMap<String, f32> = std::collections::HashMap::new();
    for (_, g, s) in &top {
        *buckets.entry(g.clone()).or_insert(0.0) += s.max(0.0);
    }
    let total: f32 = buckets.values().sum::<f32>().max(1e-9);
    let (winner, wsum) = buckets.iter().max_by(|a, b| a.1.partial_cmp(b.1).unwrap_or(std::cmp::Ordering::Equal)).unwrap();
    let share = wsum / total;
    if share < MIN_SHARE {
        return EmbedSignal { grade: None, confidence: share, method: "mixed".into(), top_sim: top1_sim, neighbors: neigh };
    }
    EmbedSignal { grade: Some(winner.clone()), confidence: share, method: "knn_vote".into(), top_sim: top1_sim, neighbors: neigh }
}
