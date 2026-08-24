//! 규칙 엔진 (Python classify/{rules,context,fuse}.py 포팅).
//! 규칙 '데이터'는 cso_rules.yaml 그대로 읽고, 스캔/융합 '로직'만 Rust 로 옮긴다.

use std::collections::HashSet;

use serde_json::{json, Value};

use crate::pii;

// ---- 등급 ----
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Grade { O, S, C }

impl Grade {
    pub fn from_str(s: &str) -> Option<Grade> {
        match s.trim() {
            "C" => Some(Grade::C),
            "S" => Some(Grade::S),
            "O" => Some(Grade::O),
            _ => None,
        }
    }
    pub fn as_str(&self) -> &'static str {
        match self { Grade::C => "C", Grade::S => "S", Grade::O => "O" }
    }
    fn rank(&self) -> i32 {
        match self { Grade::O => 0, Grade::S => 1, Grade::C => 2 }
    }
}

/// 여러 등급 후보 중 최고 위험(C>S>O). 전부 None 이면 None.
pub fn max_grade<I: IntoIterator<Item = Option<Grade>>>(it: I) -> Option<Grade> {
    let mut best: Option<Grade> = None;
    for g in it {
        if let Some(g) = g {
            if best.map_or(true, |b| g.rank() > b.rank()) {
                best = Some(g);
            }
        }
    }
    best
}

fn grade_json(g: Option<Grade>) -> Value {
    match g { Some(g) => json!(g.as_str()), None => Value::Null }
}

// ---- 신뢰도 표 ----
#[derive(Clone)]
pub struct Conf {
    pub regex: (f64, f64, f64),      // high, medium, low
    pub keyword: (f64, f64, f64),
    pub sensitive: (f64, f64, f64),
    pub stamp: (f64, f64, f64),
    pub path: (f64, f64, f64),
    pub name: f64,
}
impl Default for Conf {
    fn default() -> Self {
        Conf {
            regex: (0.90, 0.70, 0.50),
            keyword: (0.85, 0.70, 0.50),
            sensitive: (0.85, 0.70, 0.55),
            stamp: (0.95, 0.80, 0.65),
            path: (0.90, 0.70, 0.50),
            name: 0.60,
        }
    }
}
fn pick(t: (f64, f64, f64), weight: &str) -> f64 {
    match weight { "high" => t.0, "medium" => t.1, "low" => t.2, _ => t.1 }
}

// ---- 규칙 구조체 ----
pub struct RegexRule { pub id: String, pub name: String, pub label: String, pub base_grade: String, pub bulk_grade: Option<String>, pub bulk_threshold: Option<i64>, pub weight: String, pub seed_eligible: bool }
pub struct ComboRule { pub id: String, pub name: String, pub all_of: Vec<String>, pub of: Vec<String>, pub min_types: usize, pub grade: String, pub weight: String, pub seed_eligible: bool }
pub struct KeywordRule { pub id: String, pub name: String, pub terms: Vec<String>, pub exclude: Vec<String>, pub base_grade: String, pub bulk_grade: Option<String>, pub bulk_threshold: Option<i64>, pub weight: String, pub seed_eligible: bool }
pub struct SensitiveRule { pub id: String, pub name: String, pub category: String, pub terms: Vec<String>, pub exclude: Vec<String>, pub grade: String, pub weight: String, pub seed_eligible: bool }
pub struct StampRule { pub id: String, pub name: String, pub terms: Vec<String>, pub grade: String, pub weight: String, pub seed_eligible: bool, pub always: bool }
pub struct PathRule { pub id: String, pub name: String, pub matches: Vec<String>, pub grade: String, pub acl_restricted: bool, pub weight: String, pub seed_eligible: bool }

pub struct RuleSet {
    pub version: String,
    pub bulk_threshold: i64,
    pub case_insensitive: bool,
    pub conf: Conf,
    pub regex_rules: Vec<RegexRule>,
    pub pii_combos: Vec<ComboRule>,
    pub keyword_rules: Vec<KeywordRule>,
    pub sensitive_rules: Vec<SensitiveRule>,
    pub stamp_rules: Vec<StampRule>,
    pub path_rules: Vec<PathRule>,
}

const STAMP_HEAD_CHARS: usize = 400;
const STAMP_REPEAT_MIN: u32 = 2;

// ---- YAML 로더 ----
fn s(v: &Value, k: &str, def: &str) -> String {
    v.get(k).and_then(|x| x.as_str()).unwrap_or(def).to_string()
}
fn os(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(|x| x.as_str()).map(|s| s.to_string())
}
fn b(v: &Value, k: &str, def: bool) -> bool {
    v.get(k).and_then(|x| x.as_bool()).unwrap_or(def)
}
fn oi(v: &Value, k: &str) -> Option<i64> {
    v.get(k).and_then(|x| x.as_i64())
}
fn strvec(v: &Value, k: &str) -> Vec<String> {
    v.get(k).and_then(|x| x.as_array()).map(|a| {
        a.iter().filter_map(|e| e.as_str().map(|s| s.to_string())).collect()
    }).unwrap_or_default()
}

/// cso_rules.yaml 을 읽어 RuleSet 로. (Python load_rules 대응)
pub fn load_rules(path: &std::path::Path) -> Result<RuleSet, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("규칙셋 읽기 실패 {}: {}", path.display(), e))?;
    // serde_yaml::Value 로 파싱 후 serde_json::Value 로 변환해 통일된 접근.
    let yv: serde_yaml::Value = serde_yaml::from_str(&text).map_err(|e| format!("YAML 파싱 실패: {}", e))?;
    let data: Value = serde_json::to_value(&yv).map_err(|e| format!("변환 실패: {}", e))?;

    let mut conf = Conf::default();
    if let Some(c) = data.get("confidence") {
        let mut apply = |group: &str, t: &mut (f64, f64, f64)| {
            if let Some(g) = c.get(group) {
                if let Some(h) = g.get("high").and_then(|x| x.as_f64()) { t.0 = h; }
                if let Some(m) = g.get("medium").and_then(|x| x.as_f64()) { t.1 = m; }
                if let Some(l) = g.get("low").and_then(|x| x.as_f64()) { t.2 = l; }
            }
        };
        apply("regex", &mut conf.regex);
        apply("keyword", &mut conf.keyword);
        apply("sensitive", &mut conf.sensitive);
        apply("stamp", &mut conf.stamp);
        apply("path", &mut conf.path);
        if let Some(n) = c.get("name").and_then(|x| x.as_f64()) { conf.name = n; }
    }

    let empty: Vec<Value> = vec![];
    let arr = |k: &str| data.get(k).and_then(|x| x.as_array()).cloned().unwrap_or(empty.clone());

    let regex_rules = arr("regex_pii").iter().filter_map(|r| {
        let label = os(r, "label").or_else(|| os(r, "kopii_label"))?;
        Some(RegexRule {
            id: s(r, "id", ""), name: s(r, "name", &s(r, "id", "")),
            label, base_grade: s(r, "base_grade", "S"),
            bulk_grade: os(r, "bulk_grade"), bulk_threshold: oi(r, "bulk_threshold"),
            weight: s(r, "weight", "medium"), seed_eligible: b(r, "seed_eligible", false),
        })
    }).collect();

    let pii_combos = arr("pii_combos").iter().map(|r| ComboRule {
        id: s(r, "id", ""), name: s(r, "name", &s(r, "id", "")),
        all_of: strvec(r, "all_of"), of: strvec(r, "of"),
        min_types: oi(r, "min_types").unwrap_or(0).max(0) as usize,
        grade: s(r, "grade", "C"), weight: s(r, "weight", "high"),
        seed_eligible: b(r, "seed_eligible", false),
    }).collect();

    let keyword_rules = arr("keywords").iter().map(|r| KeywordRule {
        id: s(r, "id", ""), name: s(r, "name", &s(r, "id", "")),
        terms: strvec(r, "terms"), exclude: strvec(r, "exclude"),
        base_grade: s(r, "base_grade", "S"), bulk_grade: os(r, "bulk_grade"),
        bulk_threshold: oi(r, "bulk_threshold"), weight: s(r, "weight", "medium"),
        seed_eligible: b(r, "seed_eligible", false),
    }).collect();

    let sensitive_rules = arr("sensitive").iter().map(|r| SensitiveRule {
        id: s(r, "id", ""), name: s(r, "name", &s(r, "id", "")),
        category: s(r, "category", ""), terms: strvec(r, "terms"),
        exclude: strvec(r, "exclude"), grade: s(r, "grade", "C"),
        weight: s(r, "weight", "high"), seed_eligible: b(r, "seed_eligible", false),
    }).collect();

    let stamp_rules = arr("stamps").iter().map(|r| StampRule {
        id: s(r, "id", ""), name: s(r, "name", &s(r, "id", "")),
        terms: strvec(r, "terms"), grade: s(r, "grade", "C"),
        weight: s(r, "weight", "high"), seed_eligible: b(r, "seed_eligible", false),
        always: b(r, "always", false),
    }).collect();

    let path_rules = arr("paths").iter().map(|r| PathRule {
        id: s(r, "id", ""), name: s(r, "name", &s(r, "id", "")),
        matches: strvec(r, "match").iter().map(|m| m.replace('\\', "/").to_lowercase()).collect(),
        grade: s(r, "grade", "S"), acl_restricted: b(r, "acl_restricted", false),
        weight: s(r, "weight", "high"), seed_eligible: b(r, "seed_eligible", false),
    }).collect();

    let d = data.get("defaults").cloned().unwrap_or(json!({}));
    Ok(RuleSet {
        version: s(&data, "version", "unknown"),
        bulk_threshold: oi(&d, "bulk_threshold").unwrap_or(5),
        case_insensitive: b(&d, "case_insensitive", true),
        conf, regex_rules, pii_combos, keyword_rules, sensitive_rules, stamp_rules, path_rules,
    })
}

// ---- 스캔 헬퍼 ----
/// 제외어 구간 (byte offset). 비중첩.
fn exclude_spans(hay: &str, excludes: &[String], ci: bool) -> Vec<(usize, usize)> {
    let mut spans = vec![];
    for p in excludes {
        if p.is_empty() { continue; }
        let needle = if ci { p.to_lowercase() } else { p.clone() };
        if needle.is_empty() { continue; }
        let mut start = 0;
        while let Some(i) = hay[start..].find(&needle) {
            let abs = start + i;
            spans.push((abs, abs + needle.len()));
            start = abs + needle.len();
        }
    }
    spans
}

/// 제외 구간에 통째로 든 매치는 빼고 needle 등장 횟수(비중첩).
fn count_outside(hay: &str, needle: &str, ex: &[(usize, usize)]) -> u32 {
    if needle.is_empty() { return 0; }
    let mut total = 0;
    let mut start = 0;
    let nlen = needle.len();
    while let Some(i) = hay[start..].find(needle) {
        let abs = start + i;
        let end = abs + nlen;
        let inside = ex.iter().any(|&(s, e)| s <= abs && end <= e);
        if !inside { total += 1; }
        start = end;
    }
    total
}

fn escalate(base: &str, bulk: &Option<String>, bulk_thr: Option<i64>, count: u32, default_thr: i64) -> String {
    match bulk {
        None => base.to_string(),
        Some(bg) => {
            let thr = bulk_thr.filter(|&t| t > 0).unwrap_or(default_thr);
            if count as i64 >= thr { bg.clone() } else { base.to_string() }
        }
    }
}

// ---- 스캔 결과(신호) ----
pub struct Sig {
    pub grade: Option<Grade>,
    pub confidence: f64,
    pub seed_eligible: bool,
    pub acl_restricted: bool,
    pub dict: Value,
}

/// Signal A — 내용 규칙(PII+콤보+키워드).
/// [프라이버시 예외] --with-pii 전용: 검출된 원문 PII 값 목록.
/// ko-pii collect_pii 대응 — 활성 라벨(regex_rules) 범위로 {label,value,start,end} 반환.
pub fn collect_pii(text: &str, rs: &RuleSet) -> Vec<Value> {
    let wanted: HashSet<String> = rs.regex_rules.iter().map(|r| r.label.clone()).collect();
    pii::pii_records(text, &wanted)
        .into_iter()
        .map(|(label, value, start, end)| json!({"label": label, "value": value, "start": start, "end": end}))
        .collect()
}

pub fn scan_text(text: &str, rs: &RuleSet) -> Sig {
    let mut hits: Vec<(Option<Grade>, f64, bool, Value)> = vec![]; // grade, conf, seed, hit-json

    // L1: PII (ko-pii 재구현)
    let wanted: HashSet<String> = rs.regex_rules.iter().map(|r| r.label.clone()).collect();
    let counts = pii::pii_counts(text, &wanted);
    for rule in &rs.regex_rules {
        if let Some(&c) = counts.get(&rule.label) {
            if c == 0 { continue; }
            let g = escalate(&rule.base_grade, &rule.bulk_grade, rule.bulk_threshold, c, rs.bulk_threshold);
            let conf = pick(rs.conf.regex, &rule.weight);
            hits.push((Grade::from_str(&g), conf, rule.seed_eligible, json!({
                "id": rule.id, "name": rule.name, "layer": "L1",
                "count": c, "validated": c, "grade": g, "terms": []
            })));
        }
    }
    // L1-combo: 결합식별성
    let present: HashSet<&String> = counts.iter().filter(|(_, &c)| c > 0).map(|(l, _)| l).collect();
    for rule in &rs.pii_combos {
        let mut has_cond = false;
        let mut ok = true;
        let mut matched: Vec<(String, u32)> = vec![];
        if !rule.all_of.is_empty() {
            has_cond = true;
            if rule.all_of.iter().all(|l| present.contains(l)) {
                for l in &rule.all_of { matched.push((l.clone(), counts[l])); }
            } else { ok = false; }
        }
        if !rule.of.is_empty() && rule.min_types > 0 {
            has_cond = true;
            let got: Vec<&String> = rule.of.iter().filter(|l| present.contains(l)).collect();
            if got.len() >= rule.min_types {
                for l in got { matched.push((l.clone(), counts[l])); }
            } else { ok = false; }
        }
        if !has_cond || !ok { continue; }
        matched.sort();
        let conf = pick(rs.conf.regex, &rule.weight);
        let terms: Vec<Value> = matched.iter().map(|(l, c)| json!([l, c])).collect();
        hits.push((Grade::from_str(&rule.grade), conf, rule.seed_eligible, json!({
            "id": rule.id, "name": rule.name, "layer": "COMBO",
            "count": matched.len(), "validated": matched.len(), "grade": rule.grade, "terms": terms
        })));
    }
    // L2: 키워드
    let hay = if rs.case_insensitive { text.to_lowercase() } else { text.to_string() };
    for rule in &rs.keyword_rules {
        let ex = exclude_spans(&hay, &rule.exclude, rs.case_insensitive);
        let mut total = 0u32;
        let mut per_term: Vec<Value> = vec![];
        for term in &rule.terms {
            if term.is_empty() { continue; }
            let needle = if rs.case_insensitive { term.to_lowercase() } else { term.clone() };
            let c = count_outside(&hay, &needle, &ex);
            if c > 0 { per_term.push(json!({"term": term, "count": c})); total += c; }
        }
        if total == 0 { continue; }
        let g = escalate(&rule.base_grade, &rule.bulk_grade, rule.bulk_threshold, total, rs.bulk_threshold);
        let conf = pick(rs.conf.keyword, &rule.weight);
        hits.push((Grade::from_str(&g), conf, rule.seed_eligible, json!({
            "id": rule.id, "name": rule.name, "layer": "L2",
            "count": total, "validated": 0, "grade": g, "terms": per_term
        })));
    }

    finalize(hits, "rule")
}

/// Signal F — 법상 민감정보군.
pub fn scan_sensitive(text: &str, rs: &RuleSet) -> Sig {
    let hay = if rs.case_insensitive { text.to_lowercase() } else { text.to_string() };
    let mut hits: Vec<(Option<Grade>, f64, bool, Value)> = vec![];
    for rule in &rs.sensitive_rules {
        let ex = exclude_spans(&hay, &rule.exclude, rs.case_insensitive);
        let mut total = 0u32;
        let mut per_term: Vec<Value> = vec![];
        for term in &rule.terms {
            if term.is_empty() { continue; }
            let needle = if rs.case_insensitive { term.to_lowercase() } else { term.clone() };
            let c = count_outside(&hay, &needle, &ex);
            if c > 0 { per_term.push(json!({"term": term, "count": c})); total += c; }
        }
        if total == 0 { continue; }
        let conf = pick(rs.conf.sensitive, &rule.weight);
        hits.push((Grade::from_str(&rule.grade), conf, rule.seed_eligible, json!({
            "id": rule.id, "name": rule.name, "category": rule.category,
            "grade": rule.grade, "count": total, "terms": per_term
        })));
    }
    finalize(hits, "sensitive")
}

/// Signal E — 보안분류 스탬프(머리/반복 게이트).
pub fn scan_stamp(text: &str, rs: &RuleSet) -> Sig {
    let ci = rs.case_insensitive;
    let hay = if ci { text.to_lowercase() } else { text.to_string() };
    let mut hits: Vec<(Option<Grade>, f64, bool, Value)> = vec![];
    for rule in &rs.stamp_rules {
        let mut total = 0u32;
        let mut head = false;
        for term in &rule.terms {
            if term.is_empty() { continue; }
            let needle = if ci { term.to_lowercase() } else { term.clone() };
            let c = hay.matches(&needle).count() as u32;
            if c == 0 { continue; }
            total += c;
            if !head {
                if let Some(pos) = hay.find(&needle) {
                    // '머리' 판정은 문자수 기준(byte→char 근사): 앞부분 여부.
                    let char_pos = hay[..pos].chars().count();
                    if char_pos < STAMP_HEAD_CHARS { head = true; }
                }
            }
        }
        if total == 0 { continue; }
        let repeat = total >= STAMP_REPEAT_MIN;
        let mode = if rule.always { "always" }
            else if head && repeat { "head+repeat" }
            else if head { "head" }
            else if repeat { "repeat" }
            else { continue };
        let conf = pick(rs.conf.stamp, &rule.weight);
        hits.push((Grade::from_str(&rule.grade), conf, rule.seed_eligible, json!({
            "id": rule.id, "name": rule.name, "grade": rule.grade,
            "mode": mode, "count": total
        })));
    }
    finalize(hits, "stamp")
}

/// Signal C — 경로.
pub fn scan_path(file: &str, rs: &RuleSet) -> Sig {
    let np = file.replace('\\', "/").to_lowercase();
    for rule in &rs.path_rules {
        if rule.matches.iter().any(|m| np.contains(m)) {
            let conf = pick(rs.conf.path, &rule.weight);
            return Sig {
                grade: Grade::from_str(&rule.grade), confidence: conf,
                seed_eligible: rule.seed_eligible, acl_restricted: rule.acl_restricted,
                dict: json!({"grade": rule.grade, "confidence": round3(conf),
                    "seed_eligible": rule.seed_eligible, "acl_restricted": rule.acl_restricted, "source": rule.id}),
            };
        }
    }
    Sig { grade: None, confidence: 0.0, seed_eligible: false, acl_restricted: false,
        dict: json!({"grade": Value::Null, "confidence": 0.0, "seed_eligible": false, "acl_restricted": false, "source": Value::Null}) }
}

/// Signal D — 파일명(키워드 재사용).
pub fn scan_filename(file: &str, rs: &RuleSet) -> Sig {
    let base = std::path::Path::new(file).file_name().and_then(|x| x.to_str()).unwrap_or("").to_lowercase();
    let mut grades: Vec<Option<Grade>> = vec![];
    let mut hits: Vec<Value> = vec![];
    for rule in &rs.keyword_rules {
        let ex = exclude_spans(&base, &rule.exclude, true);
        for term in &rule.terms {
            if term.is_empty() { continue; }
            if count_outside(&base, &term.to_lowercase(), &ex) > 0 {
                grades.push(Grade::from_str(&rule.base_grade));
                hits.push(json!({"id": rule.id, "name": rule.name, "grade": rule.base_grade, "term": term}));
                break;
            }
        }
    }
    if hits.is_empty() {
        return Sig { grade: None, confidence: 0.0, seed_eligible: false, acl_restricted: false,
            dict: json!({"grade": Value::Null, "confidence": 0.0, "seed_eligible": false, "hits": []}) };
    }
    let final_g = max_grade(grades);
    let conf = rs.conf.name;
    Sig { grade: final_g, confidence: conf, seed_eligible: false, acl_restricted: false,
        dict: json!({"grade": grade_json(final_g), "confidence": round3(conf), "seed_eligible": false, "hits": hits}) }
}

/// 히트 목록 → 신호(최고등급·그 등급의 최고신뢰도·seed). hits 리스트가 그대로 dict.hits.
fn finalize(hits: Vec<(Option<Grade>, f64, bool, Value)>, _kind: &str) -> Sig {
    if hits.is_empty() {
        return Sig { grade: None, confidence: 0.0, seed_eligible: false, acl_restricted: false,
            dict: json!({"grade": Value::Null, "confidence": 0.0, "seed_eligible": false, "hits": []}) };
    }
    let final_g = max_grade(hits.iter().map(|h| h.0));
    let deciding: Vec<&(Option<Grade>, f64, bool, Value)> = hits.iter().filter(|h| h.0 == final_g).collect();
    let confidence = deciding.iter().map(|h| h.1).fold(0.0_f64, f64::max);
    let seed = deciding.iter().any(|h| h.2);
    let hit_json: Vec<Value> = hits.iter().map(|h| h.3.clone()).collect();
    Sig { grade: final_g, confidence, seed_eligible: seed, acl_restricted: false,
        dict: json!({"grade": grade_json(final_g), "confidence": round3(confidence), "seed_eligible": seed, "hits": hit_json}) }
}

// ---- 융합 ----
pub struct Fused { pub grade: Option<Grade>, pub confidence: f64, pub seed_eligible: bool, pub method: String, pub decided_by: Vec<String> }

/// 보수적 최댓값 + fail-safe 융합 (Python fuse_signals).
pub fn fuse(signals: &[(&str, &Sig)], failsafe: Option<&str>) -> Fused {
    let present: Vec<&(&str, &Sig)> = signals.iter().filter(|(_, s)| s.grade.is_some()).collect();
    let acl = signals.iter().any(|(_, s)| s.acl_restricted);
    if !present.is_empty() {
        let final_g = max_grade(present.iter().map(|(_, s)| s.grade));
        let deciding: Vec<&&(&str, &Sig)> = present.iter().filter(|(_, s)| s.grade == final_g).collect();
        let confidence = deciding.iter().map(|(_, s)| s.confidence).fold(0.0_f64, f64::max);
        let seed = deciding.iter().any(|(_, s)| s.seed_eligible);
        let decided_by = deciding.iter().map(|(n, _)| n.to_string()).collect();
        return Fused { grade: final_g, confidence, seed_eligible: seed, method: "fusion".into(), decided_by };
    }
    if acl {
        return Fused { grade: Some(Grade::C), confidence: 0.70, seed_eligible: true, method: "failsafe_acl".into(), decided_by: vec!["path".into()] };
    }
    if let Some(fs) = failsafe {
        if let Some(g) = Grade::from_str(fs) {
            return Fused { grade: Some(g), confidence: 0.0, seed_eligible: false, method: "failsafe_default".into(), decided_by: vec![] };
        }
    }
    Fused { grade: None, confidence: 0.0, seed_eligible: false, method: "unclassified".into(), decided_by: vec![] }
}

pub fn round3(x: f64) -> f64 { (x * 1000.0).round() / 1000.0 }

/// 저장된 신호 dict → 융합용 Sig 복원(grade/confidence/seed_eligible/acl 만 사용).
fn sig_from_json(v: &Value) -> Sig {
    Sig {
        grade: v.get("grade").and_then(|g| g.as_str()).and_then(Grade::from_str),
        confidence: v.get("confidence").and_then(|c| c.as_f64()).unwrap_or(0.0),
        seed_eligible: v.get("seed_eligible").and_then(|b| b.as_bool()).unwrap_or(false),
        acl_restricted: v.get("acl_restricted").and_then(|b| b.as_bool()).unwrap_or(false),
        dict: Value::Null,
    }
}

/// [전파] embed 신호를 더해 재융합(Python propagate_records 의 재융합부).
/// rec 의 저장된 rule/sensitive/stamp/path/name 을 되살려 embed 와 함께 max 융합하고
/// rec 의 grade/confidence/method/decided_by/seed_eligible/signals.embed 를 갱신한다.
/// -out: 갱신된 등급.
pub fn refuse_with_embed(rec: &mut Value, embed_grade: Option<&str>, embed_conf: f64,
                         embed_dict: Value, failsafe: Option<&str>) -> Option<Grade> {
    let embed = Sig {
        grade: embed_grade.and_then(Grade::from_str),
        confidence: embed_conf,
        seed_eligible: false,
        acl_restricted: false,
        dict: Value::Null,
    };
    // 저장된 신호 복원.
    let empty = Value::Null;
    let sg = rec.get("signals").cloned().unwrap_or(json!({}));
    let get = |k: &str| sig_from_json(sg.get(k).unwrap_or(&empty));
    let (rule, sens, stamp, path, name) = (get("rule"), get("sensitive"), get("stamp"), get("path"), get("name"));
    let sigs: [(&str, &Sig); 6] = [
        ("rule", &rule), ("sensitive", &sens), ("stamp", &stamp),
        ("path", &path), ("name", &name), ("embed", &embed),
    ];
    let fused = fuse(&sigs, failsafe);
    rec["grade"] = grade_json(fused.grade);
    rec["confidence"] = json!(round3(fused.confidence));
    rec["method"] = json!(fused.method);
    rec["decided_by"] = json!(fused.decided_by);
    rec["seed_eligible"] = json!(fused.seed_eligible);
    if let Some(sigs_obj) = rec.get_mut("signals").and_then(|s| s.as_object_mut()) {
        sigs_obj.insert("embed".into(), embed_dict);
    }
    fused.grade
}

/// 분류 레코드(JSON) 조립 — Python build_record(rule_only) 대응.
pub fn build_record(file: &str, text: &str, rs: &RuleSet, failsafe: Option<&str>) -> (Value, Option<Grade>) {
    let rule = scan_text(text, rs);
    let sens = scan_sensitive(text, rs);
    let stamp = scan_stamp(text, rs);
    let path = scan_path(file, rs);
    let name = scan_filename(file, rs);
    let sigs = [("rule", &rule), ("sensitive", &sens), ("stamp", &stamp), ("path", &path), ("name", &name)];
    let fused = fuse(&sigs, failsafe);
    let rec = json!({
        "file": file,
        "grade": grade_json(fused.grade),
        "confidence": round3(fused.confidence),
        "method": fused.method,
        "decided_by": fused.decided_by,
        "seed_eligible": fused.seed_eligible,
        "signals": {
            "rule": rule.dict, "sensitive": sens.dict, "stamp": stamp.dict,
            "path": path.dict, "name": name.dict
        },
        "rule_version": rs.version,
        "ts": Value::Null
    });
    (rec, fused.grade)
}
