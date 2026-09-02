//! 업무분류(doctype) 축 매칭 엔진 (Python classify/doctype.py 포팅).
//! doc_rule.yaml 의 규칙을 문서 하나에 걸어 "이 문서가 어떤 분류체계 노드에
//! 해당하는가" 후보를 만들고, 조상 흡수(5-2)와 축 전략(6장, conflict.rs)을
//! 적용해 최종 labels.doctype 을 만든다. security 축의 스캔 엔진(키워드 매칭·
//! 제외어)을 그대로 재사용한다(rules::exclude_spans/count_outside).
//!
//! [재설계 1단계 반영] 내용 신호를 위치별로 쪼개고 점수를 누적한다.
//!   · title     : 첫 비어있지 않은 줄       — 가장 강한 증거
//!   · head      : 앞 head_chars 자(표제부)  — 강한 증거
//!   · form      : 서식 필드어 세트          — 강한 증거
//!   · structure : 구조 정규식               — 보조(단독 채택 불가)
//!   · body      : 문서 전체 terms           — 약한 증거(단독 채택 불가)
//!   · name/path : 파일명·경로               — 중간 증거
//! 결합은 noisy-OR(1 - Π(1-c))라 근거가 쌓일수록 점수가 오른다.
//!
//! [scoring 모드] defaults.scoring 이
//!   · "legacy"(기본) — 종전과 동일. body 1건이면 히트, 신호 결합은 max
//!   · "staged"       — 위 재설계 방식
//! Python 구현과 값·판정이 같아야 한다(설계서 14-3 교차 검증).

use std::collections::{HashMap, HashSet};

use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Map, Value};

use crate::axes::Taxonomy;
use crate::conflict::resolve_doctype;
use crate::doc_rules::{ConflictSpec, Defaults, DocRuleSet, DoctypeRule, FormSpec};
use crate::rules::{count_outside, exclude_spans};

// ── legacy 모드 신뢰도(종전 값 그대로) ───────────────────────────────
// security 축의 같은 이름 상수와 값을 맞춘다. 두 축이 "같은 확신 수준은 같은
// 숫자"를 쓰게 해, 결과를 함께 볼 때 신뢰도가 다른 잣대로 보이지 않게 한다.
fn pick_keyword(weight: &str) -> f64 {
    match weight { "high" => 0.85, "low" => 0.50, _ => 0.70 }
}
fn pick_path_legacy(weight: &str) -> f64 {
    match weight { "high" => 0.90, "low" => 0.50, _ => 0.70 }
}
const DT_NAME_CONF: f64 = 0.60;

// ── staged 모드 신호별 신뢰도(재설계 8-2, 실측 전 잠정치) ─────────────
// 상대 순서(title ≳ head ≳ form > path ≳ name > body ≳ structure)가 핵심이며,
// 절대값은 검증셋 측정 후 재보정 대상이다. Python _SIG_CONF 와 같은 값.
fn sig_conf(signal: &str, weight: &str) -> f64 {
    match (signal, weight) {
        ("title", "high") => 0.80, ("title", "low") => 0.50, ("title", _) => 0.65,
        ("head", "high") => 0.70, ("head", "low") => 0.40, ("head", _) => 0.55,
        ("form", "high") => 0.65, ("form", "low") => 0.35, ("form", _) => 0.50,
        ("structure", _) => 0.20,
        ("body", "high") => 0.25, ("body", "low") => 0.10, ("body", _) => 0.15,
        ("name", _) => 0.30,
        ("path", "high") => 0.40, ("path", "low") => 0.20, ("path", _) => 0.30,
        _ => 0.30,
    }
}

/// 이 신호들은 단독으로 후보를 만들지 못한다(재설계 8-4). 반드시 다른 신호와
/// 결합해야 한다 — 이 규칙이 없으면 신뢰도를 아무리 낮춰도 conflict: all 에서
/// 약한 후보가 그대로 남아 P1(본문 단어 1건 = 확정)이 재발한다.
const WEAK_ONLY: [&str; 2] = ["structure", "body"];

/// 첫 줄이 제목이 아닌 포맷 — title 신호를 건너뛴다(재설계 6-3, 실측 근거).
///   .ppt       : 첫 줄이 제목인 비율 16.7%(도형 순서가 시각 순서와 다름)
///   .xls/.xlsx : 50%(첫 행이 데이터 행이라 제목 개념이 없음)
const TITLE_UNSAFE_EXTS: [&str; 3] = ["ppt", "xls", "xlsx"];

fn norm_path(s: &str) -> String {
    s.replace('\\', "/").to_lowercase()
}

/// 첫 비어있지 않은 줄. 실측(354건)에서 개행이 없는 문서는 0건이었고 첫 줄
/// 길이 중앙값은 13자, 60자 이하가 91% 였다.
fn first_line(text: &str) -> &str {
    text.lines().find(|l| !l.trim().is_empty()).unwrap_or("")
}

/// 공백 제거 사본(제목의 '자간 벌리기' 대응). 한국어 공문·규정은 제목을
/// "출 장 여 비 규 정"처럼 한 글자씩 띄어 쓰는 관행이 있다. 실측에서 첫 줄에
/// 이 관행을 쓴 문서가 10건이었고 전부 회수 대상이었다.
/// [적용 범위] 제목·표제부·파일명처럼 '짧은 구간'에만 쓴다 — 문서 전체에 쓰면
/// 줄바꿈까지 붙어 단어 경계를 넘는 오탐이 생긴다.
fn despace(s: &str) -> String {
    s.chars().filter(|c| !c.is_whitespace()).collect()
}

/// 순수 ASCII 영숫자(+공백·하이픈·점·밑줄)로만 이루어진 말인지. 한글이 섞이면 false.
fn is_ascii_term(s: &str) -> bool {
    let mut chars = s.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphanumeric() => {}
        _ => return false,
    }
    s.chars().all(|c| c.is_ascii_alphanumeric() || " .-_".contains(c))
}

static WORD_RE_CACHE: Lazy<std::sync::Mutex<HashMap<String, Regex>>> =
    Lazy::new(|| std::sync::Mutex::new(HashMap::new()));

/// 영문 약어를 단어 경계로 센다. 한국어는 띄어쓰기가 없어 부분문자열 매칭이
/// 맞지만 영문은 정반대다 — 'erd'(개체관계도)를 부분문자열로 찾으면 "GERD"·
/// "ordered" 안에서 걸린다. 실측에서 실제로 의학 논문이 DB설계서로 분류됐다.
fn count_word(hay: &str, needle: &str, ex: &[(usize, usize)]) -> u32 {
    let pat = format!(r"\b{}\b", regex::escape(needle));
    let mut cache = match WORD_RE_CACHE.lock() { Ok(c) => c, Err(e) => e.into_inner() };
    let re = cache.entry(pat.clone()).or_insert_with(|| {
        // escape 한 문자열이라 컴파일이 실패할 수 없다. 그래도 방어적으로
        // 실패하면 아무 것도 안 맞는 패턴을 넣어 크래시를 막는다.
        Regex::new(&pat).unwrap_or_else(|_| Regex::new(r"$^").expect("빈 패턴"))
    });
    re.find_iter(hay)
        .filter(|m| !ex.iter().any(|&(s, e)| s <= m.start() && m.end() <= e))
        .count() as u32
}

/// 단어 1개 세기 — 언어에 맞는 방식을 고른다. ASCII 면 경계 기반, 그 밖(한글
/// 포함)이면 종전 부분문자열 기반.
fn count_term(hay: &str, needle: &str, ex: &[(usize, usize)]) -> u32 {
    if is_ascii_term(needle) {
        count_word(hay, needle, ex)
    } else {
        count_outside(hay, needle, ex)
    }
}

/// 지정 구간에서 단어 찾기(title/head/body/name 공통).
/// compact=true 면 공백을 지운 사본에서도 찾아 본다(자간 벌리기 대응). 건수는
/// 두 결과의 최댓값을 쓴다 — 합치면 같은 매치를 두 번 세게 된다.
fn find_terms(scope: &str, terms: &[String], exclude: &[String], compact: bool)
              -> Vec<(String, u32)> {
    if terms.is_empty() || scope.is_empty() {
        return vec![];
    }
    let hay = scope.to_lowercase();
    let ex = exclude_spans(&hay, exclude, true);

    let (hay_c, ex_c) = if compact {
        let hc = despace(&hay);
        let exc: Vec<String> = exclude.iter().map(|e| despace(e)).collect();
        let exs = exclude_spans(&hc, &exc, true);
        (hc, exs)
    } else {
        (String::new(), vec![])
    };

    let mut hits = vec![];
    for t in terms {
        if t.is_empty() {
            continue;
        }
        let needle = t.to_lowercase();
        let mut c = count_term(&hay, &needle, &ex);
        // 공백 제거 사본에서는 ASCII 단어의 경계가 무너지므로("user guide" →
        // "userguide") 이 경로는 한글 단어에만 의미가 있다.
        if compact && !is_ascii_term(&needle) {
            c = c.max(count_outside(&hay_c, &despace(&needle), &ex_c));
        }
        if c > 0 {
            hits.push((t.clone(), c));
        }
    }
    hits
}

/// 서식 필드어 세트 판정(재설계 7장). 문서종류명이 아니라 '그 양식에만 있는
/// 항목명'의 조합을 본다 — 회의록 본문에 "계약서"가 언급될 수는 있어도
/// 갑·을·제O조·계약기간이 함께 나오지는 않는다.
/// 탐색 범위는 문서 전체다 — 필드어는 종류명과 달리 다른 문서에 우연히 함께
/// 등장하지 않으므로 범위를 좁힐 이유가 없다.
fn match_form(text: &str, form: Option<&FormSpec>, exclude: &[String]) -> (bool, Vec<String>) {
    let form = match form {
        Some(f) if f.usable() => f,
        _ => return (false, vec![]),
    };
    if text.is_empty() {
        return (false, vec![]);
    }
    let hay = text.to_lowercase();
    let ex = exclude_spans(&hay, exclude, true);
    let present = |item: &String| -> bool {
        !item.is_empty() && count_term(&hay, &item.to_lowercase(), &ex) > 0
    };

    let mut matched = vec![];
    if !form.all_of.is_empty() {
        if form.all_of.iter().any(|x| !present(x)) {
            return (false, vec![]);
        }
        matched.extend(form.all_of.iter().cloned());
    }
    if !form.any_of.is_empty() && form.min_types > 0 {
        // '건수'가 아니라 '서로 다른 항목의 종수'를 센다 — 같은 단어가 여러 번
        // 나오는 것은 서식의 증거가 아니다.
        let found: Vec<String> = form.any_of.iter().filter(|x| present(x)).cloned().collect();
        if found.len() < form.min_types {
            return (false, vec![]);
        }
        matched.extend(found);
    }
    (true, matched)
}

/// 구조 신호 판정. "제N조"·서명란처럼 어휘가 아닌 문서 골격을 정규식으로 잡는다.
/// 컴파일 실패 패턴은 조용히 건너뛴다 — 로드 단계(T22)에서 이미 막았다.
fn match_structure(text: &str, patterns: &[String]) -> Vec<String> {
    if patterns.is_empty() || text.is_empty() {
        return vec![];
    }
    let mut hits = vec![];
    for p in patterns {
        let pat = format!("(?i){}", p);
        if let Ok(re) = Regex::new(&pat) {
            if re.is_match(text) {
                hits.push(p.clone());
            }
        }
    }
    hits
}

/// 파일명 매칭. 파일명은 짧고 의도적으로 붙인 이름이라 우연 일치가 드물어
/// 재설계에서도 매칭 로직을 유지한다(비중만 재조정).
fn match_filename(file: &str, rule: &DoctypeRule) -> Vec<(String, u32)> {
    let base = std::path::Path::new(file).file_name()
        .and_then(|x| x.to_str()).unwrap_or("");
    if base.is_empty() || rule.filename.is_empty() {
        return vec![];
    }
    find_terms(base, &rule.filename, &rule.exclude, true)
}

/// 경로 매칭(이미 로드 시점에 정규화됨). security 의 path_rules 와 달리 "첫
/// 매칭에서 멈추는" 정책이 없다 — 노드마다 독립 판단이다.
fn match_paths(np: &str, rule: &DoctypeRule) -> Vec<String> {
    rule.paths.iter().filter(|p| np.contains(p.as_str())).cloned().collect()
}

/// noisy-OR 결합(재설계 8-1). score = 1 - Π(1-cᵢ).
/// 단순 덧셈과 달리 항상 [0,1) 안에 머물고, 근거가 늘수록 단조 증가하되
/// 포화한다 — 약한 증거를 아무리 모아도 강한 증거 하나를 넘지 못한다.
fn noisy_or(parts: &[(String, f64)]) -> f64 {
    let mut remain = 1.0_f64;
    for (_s, c) in parts {
        remain *= 1.0 - *c;
    }
    1.0 - remain
}

/// 규칙별 임계값 고르기 — 규칙에 값이 있으면 그것을, 없으면 전역 defaults.
fn thr_head_chars(rule: &DoctypeRule, d: &Defaults) -> usize {
    rule.head_chars.unwrap_or(d.head_chars)
}
fn thr_min_distinct(rule: &DoctypeRule, d: &Defaults) -> usize {
    rule.min_distinct.unwrap_or(d.min_distinct)
}
fn thr_min_count(rule: &DoctypeRule, d: &Defaults) -> u32 {
    rule.min_count.unwrap_or(d.min_count)
}

/// title 신호를 쓸 수 있는 포맷인지(재설계 6-3, 실측 3-B-3).
fn title_allowed(file: &str) -> bool {
    let ext = std::path::Path::new(file).extension()
        .and_then(|x| x.to_str()).unwrap_or("").to_lowercase();
    !TITLE_UNSAFE_EXTS.contains(&ext.as_str())
}

/// 앞에서부터 n '글자'(char)만 잘라낸다. 바이트로 자르면 한글이 깨진다.
fn take_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// 규칙 1개 스캔 결과.
struct ScanHit {
    score: f64,
    sources: Vec<String>,
    evidence: Map<String, Value>,
    parts: Vec<(String, f64)>,
}

fn terms_json(hits: &[(String, u32)]) -> Value {
    Value::Array(hits.iter()
        .map(|(t, c)| json!({ "term": t, "count": c }))
        .collect())
}

/// 규칙 1개 스캔 — 신호원 수집 + 점수화(핵심).
/// scoring 모드에 따라 결합 방식이 달라진다. 근거(evidence)는 두 모드 모두
/// 남긴다 — 근거 미보존 해소는 계측의 전제조건이라 모드와 무관하게 필요하다.
fn scan_rule(text: &str, file: &str, np: &str, rule: &DoctypeRule,
             defaults: &Defaults, allow_title: bool) -> Option<ScanHit> {
    let staged = defaults.scoring == "staged";
    let mut evidence = Map::new();
    let mut parts: Vec<(String, f64)> = vec![];

    // title: 첫 비어있지 않은 줄
    if allow_title && !rule.title_terms.is_empty() {
        let hits = find_terms(first_line(text), &rule.title_terms, &rule.exclude, true);
        if !hits.is_empty() {
            evidence.insert("title".into(), json!({ "terms": terms_json(&hits) }));
            parts.push(("title".into(), sig_conf("title", &rule.weight)));
        }
    }

    // head: 앞 head_chars 자
    if !rule.head_terms.is_empty() {
        let n = thr_head_chars(rule, defaults);
        let scope = take_chars(text, n);
        let hits = find_terms(&scope, &rule.head_terms, &rule.exclude, true);
        if !hits.is_empty() {
            evidence.insert("head".into(),
                json!({ "terms": terms_json(&hits), "window": n }));
            parts.push(("head".into(), sig_conf("head", &rule.weight)));
        }
    }

    // form: 서식 필드어 세트
    let (form_ok, form_matched) = match_form(text, rule.form.as_ref(), &rule.exclude);
    if form_ok {
        let (all_of, min_types) = match rule.form.as_ref() {
            Some(f) => (f.all_of.clone(), f.min_types),
            None => (vec![], 0),
        };
        evidence.insert("form".into(),
            json!({ "matched": form_matched, "all_of": all_of, "min_types": min_types }));
        parts.push(("form".into(), sig_conf("form", &rule.weight)));
    }

    // structure: 구조 정규식
    let st = match_structure(text, &rule.structure);
    if !st.is_empty() {
        evidence.insert("structure".into(), json!({ "matched": st }));
        parts.push(("structure".into(), sig_conf("structure", &rule.weight)));
    }

    // body: 문서 전체 terms
    if !rule.terms.is_empty() {
        let hits = find_terms(text, &rule.terms, &rule.exclude, false);
        let total: u32 = hits.iter().map(|(_t, c)| *c).sum();
        let distinct = hits.len();
        let need_d = thr_min_distinct(rule, defaults);
        let need_c = thr_min_count(rule, defaults);
        if !hits.is_empty() && distinct >= need_d && total >= need_c {
            let conf = if staged { sig_conf("body", &rule.weight) } else { pick_keyword(&rule.weight) };
            evidence.insert("body".into(), json!({
                "terms": terms_json(&hits), "distinct": distinct, "total": total,
                "min_distinct": need_d, "min_count": need_c,
            }));
            parts.push(("body".into(), conf));
        }
    }

    // name: 파일명
    let nm = match_filename(file, rule);
    if !nm.is_empty() {
        let conf = if staged { sig_conf("name", &rule.weight) } else { DT_NAME_CONF };
        let words: Vec<String> = nm.iter().map(|(t, _c)| t.clone()).collect();
        evidence.insert("name".into(), json!({ "terms": words }));
        parts.push(("name".into(), conf));
    }

    // path: 폴더 경로
    let ph = match_paths(np, rule);
    if !ph.is_empty() {
        let conf = if staged { sig_conf("path", &rule.weight) } else { pick_path_legacy(&rule.weight) };
        evidence.insert("path".into(), json!({ "matched": ph }));
        parts.push(("path".into(), conf));
    }

    if parts.is_empty() {
        return None;
    }
    let sources: Vec<String> = parts.iter().map(|(s, _c)| s.clone()).collect();

    if !staged {
        // legacy: 종전과 완전히 같은 계산 — 걸린 신호 중 가장 높은 값 하나.
        let score = parts.iter().map(|(_s, c)| *c).fold(0.0_f64, f64::max);
        return Some(ScanHit { score, sources, evidence, parts });
    }

    // staged: 보조 신호만으로는 후보를 만들지 않는다(8-4).
    if sources.iter().all(|s| WEAK_ONLY.contains(&s.as_str())) {
        return None;
    }
    let score = noisy_or(&parts);
    // 약한 후보 임계 미만은 후보에서 뺀다(8-3). 이 컷이 없으면 conflict: all
    // 에서 약한 증거가 그대로 라벨로 남아 다중 라벨이 다시 늘어난다.
    if score < defaults.t_low {
        return None;
    }
    Some(ScanHit { score, sources, evidence, parts })
}

/// 조상 흡수(설계서 5-2). 같은 가지의 조상-자손이 함께 걸리면 조상은 버리고
/// 자손만 남긴다 — 자손의 path_ids 에 조상이 이미 들어 있으므로 중복 라벨을
/// 낼 이유가 없다. 서로 다른 가지끼리 걸린 경우(계약서+제안서)는 그대로 둘 다 남는다.
fn absorb_ancestors(dc_ids: &HashSet<String>, taxonomy: &Taxonomy) -> HashSet<String> {
    let mut survivors = dc_ids.clone();
    for dc_id in dc_ids {
        if let Ok(path_ids) = taxonomy.path_ids(dc_id) {
            let ancestors = &path_ids[..path_ids.len().saturating_sub(1)];
            for ancestor in ancestors {
                if dc_ids.contains(ancestor) {
                    survivors.remove(ancestor);
                }
            }
        }
    }
    survivors
}

/// doctype 축 후보 1개(설계서 7-1 labels.doctype.values[] 대응).
#[derive(Clone)]
pub struct Candidate {
    pub dc_id: String,
    pub path: String,
    pub path_ids: Vec<String>,
    pub confidence: f64,
    pub from: Vec<String>,
    /// "rule" | "embed" | "both" — 이 후보가 어디서 왔는지(재설계 13-2).
    pub stage: String,
    /// 신호별 근거. 규칙에 정의된 단어와 건수만 담는다(원문 조각 금지).
    pub evidence: Map<String, Value>,
    /// noisy-OR 구성요소. 임계값을 바꿨을 때를 재실행 없이 시뮬레이션하는 데 쓴다.
    pub score_parts: Vec<(String, f64)>,
}

impl Candidate {
    /// 근거 없이 후보만 만들 때(테스트·전파 경로). Python 쪽 기본값과 맞춘다.
    /// 현재 호출부가 테스트 코드뿐이라 릴리스 빌드에서는 dead 로 잡힌다.
    #[allow(dead_code)]
    pub fn bare(dc_id: String, path: String, path_ids: Vec<String>,
                confidence: f64, from: Vec<String>) -> Self {
        Candidate { dc_id, path, path_ids, confidence, from,
                    stage: "rule".into(), evidence: Map::new(), score_parts: vec![] }
    }
}

/// doctype 축 최종 결과(labels.doctype).
pub struct DoctypeSignal {
    pub values: Vec<Candidate>,
    pub strategy: String,
    pub status: String,
    pub truncated: usize,
    /// 계층 정합성 경고(재설계 11-3). 자동으로 후보를 버리지 않고 검토 큐
    /// 우선순위 신호로만 쓴다.
    pub conflicts: Vec<Value>,
}

impl DoctypeSignal {
    /// 분류 레코드의 labels.doctype 칸에 넣을 순수 Value.
    /// [프라이버시] evidence 에는 규칙에 정의된 단어와 건수만 담는다 —
    /// 문서 원문 조각(스니펫)은 절대 넣지 않는다.
    pub fn as_dict(&self) -> Value {
        json!({
            "values": self.values.iter().map(|v| {
                let mut m = Map::new();
                m.insert("dc_id".into(), json!(v.dc_id));
                m.insert("path".into(), json!(v.path));
                m.insert("path_ids".into(), json!(v.path_ids));
                m.insert("confidence".into(), json!(round3(v.confidence)));
                m.insert("from".into(), json!(v.from));
                // 아래 세 칸은 없으면 키 자체를 만들지 않는다 — 소비자가
                // "안 씀"과 "비었음"을 구분할 수 있어야 한다.
                if !v.stage.is_empty() {
                    m.insert("stage".into(), json!(v.stage));
                }
                if !v.evidence.is_empty() {
                    m.insert("evidence".into(), Value::Object(v.evidence.clone()));
                }
                if !v.score_parts.is_empty() {
                    m.insert("score_parts".into(), Value::Array(v.score_parts.iter()
                        .map(|(s, c)| json!({ "signal": s, "c": round3(*c) }))
                        .collect()));
                }
                Value::Object(m)
            }).collect::<Vec<_>>(),
            "strategy": self.strategy,
            "status": self.status,
            "truncated": self.truncated,
            "conflicts": self.conflicts,
        })
    }
}

fn round3(x: f64) -> f64 {
    (x * 1000.0).round() / 1000.0
}

/// 노드별 병합 중간 상태.
struct Merged {
    confidence: f64,
    from: HashSet<String>,
    evidence: Map<String, Value>,
    parts: Vec<(String, f64)>,
}

impl Merged {
    fn empty() -> Self {
        Merged { confidence: 0.0, from: HashSet::new(), evidence: Map::new(), parts: vec![] }
    }
}

/// 계층 정합성 검사(재설계 11-3). 1계층(주제)과 2계층(양식)은 서로 다른 엔진이
/// 결정하므로 모순이 생길 수 있다. 서로 다른 1계층 가지에 걸리면 플래그만
/// 남긴다 — 자동으로 하나를 버리지 않는다(자동 판정은 확정하지 않는다).
fn check_hierarchy(dc_ids: &HashSet<String>, taxonomy: &Taxonomy) -> Vec<Value> {
    if dc_ids.len() < 2 {
        return vec![];
    }
    let mut ids: Vec<String> = dc_ids.iter().cloned().collect();
    ids.sort();
    let mut roots: HashSet<String> = HashSet::new();
    for id in &ids {
        if let Ok(p) = taxonomy.path_ids(id) {
            if let Some(root) = p.first() {
                roots.insert(root.clone());
            }
        }
    }
    if roots.len() < 2 {
        return vec![];
    }
    vec![json!({
        "type": "cross_branch",
        "dc_ids": ids,
        "detail": "서로 다른 1계층 가지의 후보가 동시에 제안됐습니다 — 검토 우선순위 상향",
    })]
}

/// 노드별 병합 상태 → 최종 DoctypeSignal(scan_doctype·merge_embed_candidates 공유).
fn finalize(merged: HashMap<String, Merged>, taxonomy: &Taxonomy, conflict: &ConflictSpec)
            -> DoctypeSignal {
    if merged.is_empty() {
        return DoctypeSignal { values: vec![], strategy: conflict.strategy.clone(),
                               status: "proposed".into(), truncated: 0, conflicts: vec![] };
    }
    let ids: HashSet<String> = merged.keys().cloned().collect();
    let survivors = absorb_ancestors(&ids, taxonomy);
    let conflicts = check_hierarchy(&survivors, taxonomy);

    let candidates: Vec<Candidate> = survivors.into_iter().filter_map(|dc_id| {
        let path_ids = taxonomy.path_ids(&dc_id).ok()?;
        let path = taxonomy.path(&dc_id).ok()?;
        let m = merged.get(&dc_id)?;
        let mut from: Vec<String> = m.from.iter().cloned().collect();
        from.sort();
        // stage: 규칙에서 왔는지, 벡터에서 왔는지, 둘 다인지.
        let has_embed = m.from.contains("embed");
        let stage = if has_embed && m.from.len() == 1 { "embed" }
                    else if has_embed { "both" } else { "rule" };
        Some(Candidate {
            dc_id, path, path_ids, confidence: m.confidence, from,
            stage: stage.into(), evidence: m.evidence.clone(), score_parts: m.parts.clone(),
        })
    }).collect();

    let (values, truncated) = resolve_doctype(candidates, conflict);
    DoctypeSignal { values, strategy: conflict.strategy.clone(),
                    status: "proposed".into(), truncated, conflicts }
}

/// doctype 축 스캔(핵심 진입점). 문서 하나를 doc_rule.yaml 규칙 전체에 걸어
/// 최종 DoctypeSignal 을 만든다.
pub fn scan_doctype(text: &str, file: &str, doc_rule_set: &DocRuleSet, taxonomy: &Taxonomy)
                    -> DoctypeSignal {
    let np = norm_path(file);
    let defaults = &doc_rule_set.defaults;
    let allow_title = title_allowed(file);
    let mut merged: HashMap<String, Merged> = HashMap::new();

    for rule in doc_rule_set.active_rules() {
        let hit = match scan_rule(text, file, &np, rule, defaults, allow_title) {
            Some(h) => h,
            None => continue,
        };
        // T5 가 로드 시점에 이미 이런 규칙을 막지만, taxonomy 없이 로드된
        // doc_rule_set(교차검증 생략)이 넘어올 수도 있으니 여기서도 방어한다.
        if taxonomy.get(&rule.node).is_none() {
            continue;
        }
        let entry = merged.entry(rule.node.clone()).or_insert_with(Merged::empty);
        for src in &hit.sources {
            entry.from.insert(src.clone());
        }
        // 같은 노드를 두 규칙이 맞혔다면 더 강하게 맞힌 쪽의 근거를 남긴다 —
        // 근거와 점수가 서로 다른 규칙에서 나오면 감사 때 설명이 안 된다.
        if hit.score > entry.confidence {
            entry.confidence = hit.score;
            entry.evidence = hit.evidence;
            entry.parts = hit.parts;
        }
    }
    finalize(merged, taxonomy, &doc_rule_set.conflict)
}

/// 임베딩 전파 후보를 규칙 후보와 병합(D7, 설계서 5-4·재설계 11-1).
/// embed 는 후보를 "추가"만 한다 — 이미 있던 규칙 기반 후보를 빼앗지 않는다.
/// [상한] 벡터 '단독' 후보는 embed_cap 을 넘지 못한다(재설계 9-3a) — 득표율은
/// "이웃 중 몇 %인가"이지 "이 문서가 그 라벨일 확률"이 아니라서, 근거를 제시할
/// 수 있는 규칙 히트보다 세지면 안 된다.
///
/// -in: existing     = 병합 전 최종 후보(scan_doctype 결과의 values)
/// -in: embed_values = propagate::propagate_doctype() 이 찾은 (dc_id, confidence) 목록
/// -in: embed_cap    = 벡터 단독 후보 신뢰도 상한. None 이면 상한 없음(종전 동작)
pub fn merge_embed_candidates(existing: &[Candidate], embed_values: &[(String, f64)],
                              taxonomy: &Taxonomy, conflict: &ConflictSpec,
                              embed_cap: Option<f64>) -> DoctypeSignal {
    let mut merged: HashMap<String, Merged> = HashMap::new();
    for v in existing {
        merged.insert(v.dc_id.clone(), Merged {
            confidence: v.confidence,
            from: v.from.iter().cloned().collect(),
            evidence: v.evidence.clone(),
            parts: v.score_parts.clone(),
        });
    }
    for (dc_id, conf) in embed_values {
        if taxonomy.get(dc_id).is_none() {
            continue; // 삭제된 노드를 가리키는 낡은 seed — 조용히 무시
        }
        match merged.get_mut(dc_id) {
            Some(entry) => {
                entry.from.insert("embed".into());
                entry.evidence.insert("embed".into(), json!({ "share": round3(*conf) }));
                if *conf > entry.confidence {
                    entry.confidence = *conf;
                }
            }
            None => {
                // 벡터 단독 후보 — 상한을 건다.
                let capped = match embed_cap { Some(c) => conf.min(c), None => *conf };
                let mut ev = Map::new();
                ev.insert("embed".into(), json!({ "share": round3(*conf) }));
                merged.insert(dc_id.clone(), Merged {
                    confidence: capped,
                    from: HashSet::from(["embed".to_string()]),
                    evidence: ev,
                    parts: vec![("embed".into(), capped)],
                });
            }
        }
    }
    finalize(merged, taxonomy, conflict)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::axes::load_taxonomy;
    use crate::doc_rules::{Defaults, FormSpec};

    // cargo test 는 기본적으로 테스트를 여러 스레드에서 병렬 실행한다. 임시파일
    // 이름이 겹치면 한 스레드의 remove_file 이 다른 스레드의 read 보다 먼저
    // 끝나 "파일을 찾을 수 없음"으로 흔들린다 — 프로세스 id 만으로는 유일하지
    // 않으므로 호출마다 증가하는 카운터를 더한다.
    static TMP_COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    fn unique_tmp(prefix: &str) -> std::path::PathBuf {
        let n = TMP_COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        std::env::temp_dir().join(format!("{}_{}_{}.yaml", prefix, std::process::id(), n))
    }

    fn mk_taxonomy() -> Taxonomy {
        let yaml = "taxonomy:\n  source: t\n  exported_at: '20260824000000'\n  node_count: 7\n  nodes:\n\
            \x20 - {dc_id: LEGAL, parent: null, order: 1, title: 법무/규정, status: 1}\n\
            \x20 - {dc_id: CONTRACT, parent: LEGAL, order: 1, title: 계약서, status: 1}\n\
            \x20 - {dc_id: SALES, parent: null, order: 2, title: 영업/마케팅, status: 1}\n\
            \x20 - {dc_id: PROPOSAL, parent: SALES, order: 1, title: 제안서, status: 1}\n\
            \x20 - {dc_id: TECH, parent: null, order: 3, title: 기술/개발, status: 1}\n\
            \x20 - {dc_id: DESIGN, parent: TECH, order: 1, title: 설계문서, status: 1}\n\
            \x20 - {dc_id: REQSPEC, parent: DESIGN, order: 1, title: 요구사항정의서, status: 1}\n";
        let tmp = unique_tmp("cso_test_doctype_taxonomy");
        std::fs::write(&tmp, yaml).unwrap();
        let t = load_taxonomy(&tmp).unwrap();
        let _ = std::fs::remove_file(&tmp);
        t
    }

    /// 빈 규칙 하나. 필요한 칸만 뒤에서 채워 쓴다.
    fn blank(id: &str, node: &str, weight: &str) -> DoctypeRule {
        DoctypeRule {
            id: id.into(), node: node.into(), weight: weight.into(),
            title_terms: vec![], head_terms: vec![], head_chars: None,
            form: None, structure: vec![], terms: vec![],
            min_distinct: None, min_count: None,
            exclude: vec![], filename: vec![], paths: vec![], active: true,
        }
    }

    fn sv(xs: &[&str]) -> Vec<String> {
        xs.iter().map(|s| s.to_string()).collect()
    }

    fn rule(id: &str, node: &str, weight: &str, terms: &[&str], exclude: &[&str],
            filename: &[&str], paths: &[&str]) -> DoctypeRule {
        let mut r = blank(id, node, weight);
        r.terms = sv(terms);
        r.exclude = sv(exclude);
        r.filename = sv(filename);
        r.paths = sv(paths);
        r
    }

    fn mk_set(rules: Vec<DoctypeRule>, defaults: Defaults) -> DocRuleSet {
        DocRuleSet {
            conflict: ConflictSpec { strategy: "all".into(), n: None, min_confidence: None },
            defaults,
            embed: crate::doc_rules::EmbedSpec::default(),
            version: "t".into(),
            rules,
            warnings: vec![],
        }
    }

    fn staged() -> Defaults {
        Defaults { scoring: "staged".into(), ..Defaults::default() }
    }

    fn mk_ruleset() -> DocRuleSet {
        mk_set(vec![
            rule("dt_contract", "CONTRACT", "high", &["계약서", "용역계약", "갑과 을"], &["계약서 양식"], &["계약서"], &[]),
            rule("dt_proposal", "PROPOSAL", "high", &["제안서", "제안 내용"], &[], &["제안서"], &[]),
            rule("dt_reqspec", "REQSPEC", "high", &["요구사항정의서", "요구사항 명세"], &[], &[], &[]),
            rule("dt_design", "DESIGN", "medium", &["설계문서"], &[], &[], &[]),
            rule("dt_legal_root", "LEGAL", "low", &["법무"], &[], &[], &[]),
        ], Defaults::default())
    }

    // ── legacy(하위호환) — 종전 동작이 그대로여야 한다 ────────────────

    #[test]
    fn 매칭없으면_빈결과() {
        let sig = scan_doctype("아무 관련 없는 회의록입니다.", "D:/x/회의록.hwp", &mk_ruleset(), &mk_taxonomy());
        assert!(sig.values.is_empty());
    }

    #[test]
    fn exclude된_매치는_제외된다() {
        let sig = scan_doctype("이 파일은 계약서 양식일 뿐이다.", "D:/x/서식.hwp", &mk_ruleset(), &mk_taxonomy());
        assert!(sig.values.is_empty());
    }

    #[test]
    fn filename만으로도_매칭된다() {
        let sig = scan_doctype("", "D:/x/계약서_초안.hwp", &mk_ruleset(), &mk_taxonomy());
        let hit = sig.values.iter().find(|v| v.dc_id == "CONTRACT").expect("CONTRACT 매칭돼야 함");
        assert_eq!(hit.from, vec!["name".to_string()]);
    }

    #[test]
    fn terms와_filename이_함께_걸리면_from_합쳐진다() {
        let sig = scan_doctype("본 계약서는 용역계약을 다룬다.", "D:/x/계약서_2026.hwp", &mk_ruleset(), &mk_taxonomy());
        let hit = sig.values.iter().find(|v| v.dc_id == "CONTRACT").unwrap();
        let mut from = hit.from.clone();
        from.sort();
        // 재설계로 from 어휘가 신호원별로 세분화됐다: 본문 terms 는 "body".
        assert_eq!(from, vec!["body".to_string(), "name".to_string()]);
    }

    #[test]
    fn legacy는_본문_1건으로_히트하고_종전_신뢰도를_쓴다() {
        let rs = mk_set(vec![rule("r", "CONTRACT", "medium", &["계약서"], &[], &[], &[])],
                        Defaults::default());
        let sig = scan_doctype("계약서 한 번 언급", "D:/x/문서.hwp", &rs, &mk_taxonomy());
        assert_eq!(sig.values.len(), 1);
        assert!((sig.values[0].confidence - 0.70).abs() < 1e-9);
    }

    #[test]
    fn legacy의_결합은_max다() {
        let rs = mk_set(vec![rule("r", "CONTRACT", "medium", &["계약서"], &[], &["계약서"], &[])],
                        Defaults::default());
        let sig = scan_doctype("계약서", "D:/x/계약서.hwp", &rs, &mk_taxonomy());
        // body(0.70) 와 name(0.60) 중 큰 값. 가산이면 0.70 을 넘었을 것이다.
        assert!((sig.values[0].confidence - 0.70).abs() < 1e-9);
    }

    #[test]
    fn paths_매칭() {
        let rs = mk_set(vec![rule("dt_contract", "CONTRACT", "medium", &[], &[], &[], &["/법무/", "/계약/"])],
                        Defaults::default());
        let sig = scan_doctype("", "D:/collected/법무/무제.docx", &rs, &mk_taxonomy());
        assert_eq!(sig.values.len(), 1);
        assert_eq!(sig.values[0].from, vec!["path".to_string()]);
    }

    #[test]
    fn 조상_자손_동시매칭시_자손만_남는다() {
        let sig = scan_doctype("이 문서는 설계문서 중 요구사항정의서에 해당한다.", "D:/x/문서.hwp",
                               &mk_ruleset(), &mk_taxonomy());
        let ids: Vec<&str> = sig.values.iter().map(|v| v.dc_id.as_str()).collect();
        assert_eq!(ids, vec!["REQSPEC"]);
    }

    #[test]
    fn 서로_다른_가지는_둘_다_남는다() {
        let sig = scan_doctype("이 계약서에는 제안 내용이 별첨돼 있다.", "D:/x/문서.hwp",
                               &mk_ruleset(), &mk_taxonomy());
        let mut ids: Vec<&str> = sig.values.iter().map(|v| v.dc_id.as_str()).collect();
        ids.sort();
        assert_eq!(ids, vec!["CONTRACT", "PROPOSAL"]);
        // 서로 다른 1계층 가지 → 모순 플래그(11-3)
        assert_eq!(sig.conflicts.len(), 1);
    }

    #[test]
    fn top_n_전략은_잘라내고_truncated_보고() {
        let mut rs = mk_ruleset();
        rs.conflict = ConflictSpec { strategy: "top_n".into(), n: Some(1), min_confidence: None };
        let sig = scan_doctype("이 계약서에는 제안 내용이 별첨돼 있다.", "D:/x/문서.hwp", &rs, &mk_taxonomy());
        assert_eq!(sig.values.len(), 1);
        assert_eq!(sig.truncated, 1);
        assert_eq!(sig.strategy, "top_n");
    }

    // ── staged(재설계 1단계) ──────────────────────────────────────────

    #[test]
    fn staged에서_본문만으로는_후보가_되지_않는다() {
        let rs = mk_set(vec![rule("r", "CONTRACT", "medium", &["계약서"], &[], &[], &[])], staged());
        let sig = scan_doctype("이 문서는 계약서를 첨부합니다.", "D:/x/공문.hwp", &rs, &mk_taxonomy());
        assert!(sig.values.is_empty());
    }

    #[test]
    fn structure만으로는_후보가_되지_않는다() {
        let mut r = blank("r", "CONTRACT", "medium");
        r.structure = sv(&[r"제\d+조"]);
        let sig = scan_doctype("제1조 목적 제2조 범위", "D:/x/문서.hwp",
                               &mk_set(vec![r], staged()), &mk_taxonomy());
        assert!(sig.values.is_empty());
    }

    #[test]
    fn 표제부_밖의_언급은_head신호가_아니다() {
        let mut r = blank("r", "CONTRACT", "medium");
        r.head_terms = sv(&["계약서"]);
        r.head_chars = Some(20);
        let text = format!("제목 없는 공문입니다.\n{}\n첨부: 계약서를 참조하십시오.", "가".repeat(200));
        let sig = scan_doctype(&text, "D:/x/공문.hwp", &mk_set(vec![r], staged()), &mk_taxonomy());
        assert!(sig.values.is_empty());
    }

    #[test]
    fn 표제부_안이면_head신호가_잡힌다() {
        let mut r = blank("r", "CONTRACT", "medium");
        r.head_terms = sv(&["계약서"]);
        r.head_chars = Some(50);
        let sig = scan_doctype("2026년 용역 계약서\n갑과 을은...", "D:/x/a.hwp",
                               &mk_set(vec![r], staged()), &mk_taxonomy());
        assert_eq!(sig.values.len(), 1);
        assert!(sig.values[0].from.contains(&"head".to_string()));
    }

    #[test]
    fn 첫줄이_제목이_아닌_포맷은_title신호를_건너뛴다() {
        let mut r = blank("r", "CONTRACT", "medium");
        r.title_terms = sv(&["계약서"]);
        for (ext, expect) in [("hwp", true), ("docx", true), ("ppt", false), ("xlsx", false)] {
            let file = format!("D:/x/a.{}", ext);
            let sig = scan_doctype("계약서\n본문", &file, &mk_set(vec![r.clone()], staged()), &mk_taxonomy());
            assert_eq!(!sig.values.is_empty(), expect, "ext={}", ext);
        }
    }

    #[test]
    fn 자간_벌리기_제목을_잡는다() {
        let mut r = blank("r", "CONTRACT", "medium");
        r.title_terms = sv(&["계약서"]);
        let sig = scan_doctype("계 약 서\n갑과 을", "D:/x/a.hwp",
                               &mk_set(vec![r], staged()), &mk_taxonomy());
        assert!(sig.values[0].from.contains(&"title".to_string()));
    }

    #[test]
    fn 영문약어는_단어_경계로만_걸린다() {
        let mut r = blank("r", "REQSPEC", "medium");
        r.title_terms = sv(&["erd"]);
        let t = mk_taxonomy();
        let ng = scan_doctype("Gastroesophageal Reflux Disease (GERD)", "D:/x/a.pdf",
                              &mk_set(vec![r.clone()], staged()), &t);
        assert!(ng.values.is_empty());
        let ok = scan_doctype("ERD 및 테이블 정의", "D:/x/a.pdf", &mk_set(vec![r], staged()), &t);
        assert!(!ok.values.is_empty());
    }

    #[test]
    fn form_any_of는_서로_다른_항목_종수를_센다() {
        let mut r = blank("r", "CONTRACT", "medium");
        r.form = Some(FormSpec { all_of: vec![], any_of: sv(&["갑", "을", "계약기간"]), min_types: 2 });
        r.filename = sv(&["문서"]);
        let t = mk_taxonomy();
        let ok = scan_doctype("갑과 을은 합의한다", "D:/x/문서.hwp", &mk_set(vec![r.clone()], staged()), &t);
        assert!(ok.values[0].from.contains(&"form".to_string()));
        let ng = scan_doctype("갑 갑 갑 갑", "D:/x/문서.hwp", &mk_set(vec![r], staged()), &t);
        assert!(!ng.values[0].from.contains(&"form".to_string()));
    }

    #[test]
    fn 근거가_쌓이면_점수가_오른다() {
        let t = mk_taxonomy();
        let mut one = blank("r", "CONTRACT", "medium");
        one.head_terms = sv(&["계약서"]);
        let mut many = blank("r", "CONTRACT", "medium");
        many.head_terms = sv(&["계약서"]);
        many.filename = sv(&["계약서"]);
        many.terms = sv(&["계약서"]);
        let s1 = scan_doctype("계약서", "D:/x/문서.hwp", &mk_set(vec![one], staged()), &t);
        let s2 = scan_doctype("계약서", "D:/x/계약서.hwp", &mk_set(vec![many], staged()), &t);
        assert!(s2.values[0].confidence > s1.values[0].confidence);
    }

    #[test]
    fn noisy_or_계산값() {
        let got = noisy_or(&[("a".into(), 0.7), ("b".into(), 0.5), ("c".into(), 0.3)]);
        assert!((got - (1.0 - 0.3 * 0.5 * 0.7)).abs() < 1e-12);
        // 아무리 쌓아도 1.0 을 넘지 않는다.
        let many: Vec<(String, f64)> = (0..10).map(|i| (format!("s{}", i), 0.9)).collect();
        let big = noisy_or(&many);
        assert!(big < 1.0 && big > 0.99);
    }

    #[test]
    fn 동점_후보_정렬은_재현가능하다() {
        let t = mk_taxonomy();
        let rules = vec![
            { let mut r = blank("a", "CONTRACT", "medium"); r.filename = sv(&["문서"]); r },
            { let mut r = blank("b", "PROPOSAL", "medium"); r.filename = sv(&["문서"]); r },
            { let mut r = blank("c", "REQSPEC", "medium"); r.filename = sv(&["문서"]); r },
        ];
        let mut seen: HashSet<Vec<String>> = HashSet::new();
        for _ in 0..20 {
            let sig = scan_doctype("본문", "D:/x/문서.hwp", &mk_set(rules.clone(), staged()), &t);
            seen.insert(sig.values.iter().map(|v| v.dc_id.clone()).collect());
        }
        assert_eq!(seen.len(), 1);
        // 동점 → dc_id 오름차순
        assert_eq!(seen.into_iter().next().unwrap(),
                   vec!["CONTRACT".to_string(), "PROPOSAL".to_string(), "REQSPEC".to_string()]);
    }

    #[test]
    fn 근거블록이_남는다() {
        let mut r = blank("r", "CONTRACT", "high");
        r.title_terms = sv(&["계약서"]);
        r.terms = sv(&["계약서"]);
        r.filename = sv(&["계약서"]);
        let sig = scan_doctype("계약서\n본 계약서는", "D:/x/용역_계약서.hwp",
                               &mk_set(vec![r], staged()), &mk_taxonomy());
        let d = sig.as_dict();
        let v = &d["values"][0];
        assert_eq!(v["stage"], "rule");
        assert!(v["evidence"]["title"]["terms"][0]["term"] == "계약서");
        assert!(v["score_parts"].as_array().unwrap().len() == 3);
    }

    // ── embed 병합 ────────────────────────────────────────────────────

    #[test]
    fn merge_embed는_새_후보를_추가한다() {
        let t = mk_taxonomy();
        let conflict = ConflictSpec::default();
        let existing = vec![Candidate::bare("CONTRACT".into(), t.path("CONTRACT").unwrap(),
                                            t.path_ids("CONTRACT").unwrap(), 0.8, vec!["body".into()])];
        let sig = merge_embed_candidates(&existing, &[("PROPOSAL".to_string(), 0.4)], &t, &conflict, None);
        let mut ids: Vec<&str> = sig.values.iter().map(|v| v.dc_id.as_str()).collect();
        ids.sort();
        assert_eq!(ids, vec!["CONTRACT", "PROPOSAL"]);
        let p = sig.values.iter().find(|v| v.dc_id == "PROPOSAL").unwrap();
        assert_eq!(p.from, vec!["embed".to_string()]);
        assert_eq!(p.stage, "embed");
    }

    #[test]
    fn merge_embed는_같은_노드를_강화한다() {
        let t = mk_taxonomy();
        let existing = vec![Candidate::bare("CONTRACT".into(), t.path("CONTRACT").unwrap(),
                                            t.path_ids("CONTRACT").unwrap(), 0.5, vec!["body".into()])];
        let sig = merge_embed_candidates(&existing, &[("CONTRACT".to_string(), 0.9)],
                                         &t, &ConflictSpec::default(), None);
        assert_eq!(sig.values.len(), 1);
        assert_eq!(sig.values[0].confidence, 0.9);
        assert_eq!(sig.values[0].stage, "both");
    }

    #[test]
    fn 벡터_단독_후보는_상한을_넘지_못한다() {
        let t = mk_taxonomy();
        let sig = merge_embed_candidates(&[], &[("CONTRACT".to_string(), 0.95)],
                                         &t, &ConflictSpec::default(), Some(0.65));
        assert!((sig.values[0].confidence - 0.65).abs() < 1e-9);
    }

    #[test]
    fn merge_embed_후에도_조상_흡수가_적용된다() {
        let t = mk_taxonomy();
        let existing = vec![Candidate::bare("CONTRACT".into(), t.path("CONTRACT").unwrap(),
                                            t.path_ids("CONTRACT").unwrap(), 0.8, vec!["body".into()])];
        let sig = merge_embed_candidates(&existing, &[("LEGAL".to_string(), 0.4)],
                                         &t, &ConflictSpec::default(), None);
        let ids: Vec<&str> = sig.values.iter().map(|v| v.dc_id.as_str()).collect();
        assert_eq!(ids, vec!["CONTRACT"]);
    }

    #[test]
    fn taxonomy에_없는_embed_후보는_무시() {
        let t = mk_taxonomy();
        let sig = merge_embed_candidates(&[], &[("삭제된노드".to_string(), 0.9)],
                                         &t, &ConflictSpec::default(), None);
        assert!(sig.values.is_empty());
    }

    // doc_rule.yaml 이 없는 배포(seed 전파 전용)에서는 1차 스캔이 반드시 빈손이어야
    // 한다 — 그래야 "규칙으로는 못 정했다"가 되고 전파가 유일한 분류 수단이 된다.
    #[test]
    fn 규칙0건_스캔은_아무것도_못맞힌다() {
        let drs = crate::doc_rules::DocRuleSet::seed_only();
        let sig = scan_doctype("계약서 내용입니다", "계약서.txt", &drs, &mk_taxonomy());
        assert!(sig.values.is_empty());
        assert_eq!(sig.truncated, 0);
    }

    #[test]
    fn 규칙0건이어도_embed후보는_라벨이_된다() {
        let t = mk_taxonomy();
        let drs = crate::doc_rules::DocRuleSet::seed_only();
        let sig = merge_embed_candidates(&[], &[("REQSPEC".to_string(), 0.8)], &t, &drs.conflict, None);
        assert_eq!(sig.values.len(), 1);
        assert_eq!(sig.values[0].path, "기술/개발 > 설계문서 > 요구사항정의서");
        assert_eq!(sig.values[0].from, vec!["embed".to_string()]);
    }
}
