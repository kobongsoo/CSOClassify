//! 업무분류(doctype) 축 — doc_rule.yaml 로더 (Python classify/doc_rules.py 포팅).
//! "무엇을 계약서로 볼 것인가" 규칙(doctype_rules)과 축 전체 충돌 전략(conflict)을
//! 읽는다. cso_rules.yaml(security 축)과는 완전히 다른 파일이며 이 로더는 그
//! 파일을 전혀 건드리지 않는다. doc_rule.yaml 이 없어도 시스템은 계속 동작해야
//! 한다 — "있으면 켜지고 없으면 꺼지는 외장 자산"(설계서 4-6, T16).

use std::collections::HashSet;

use serde_json::Value;

use crate::axes::Taxonomy;
use crate::rules::{s, strvec};

/// doc_rule.yaml 이 정의할 수 있는 conflict 전략. taxonomy 축(트리)은 서열이
/// 없어 max 는 애초에 문법에 없다 — 오타로 적혀도 T2 로 막는다.
pub const CONFLICT_STRATEGIES: [&str; 2] = ["all", "top_n"];

/// 축 전체 충돌 해소 전략 설정. doctype 축에서 유일하게 파일에 적는 값이다
/// (id·kind·cardinality·required·taxonomy 경로는 전부 코드에 고정돼 사라졌다, 3-2-1).
pub struct ConflictSpec {
    pub strategy: String,
    pub n: Option<i64>,
    pub min_confidence: Option<f64>,
}

impl Default for ConflictSpec {
    fn default() -> Self {
        ConflictSpec { strategy: "all".into(), n: None, min_confidence: None }
    }
}

/// 서식 필드어 세트(재설계 7장). 문서종류명이 아니라 '그 양식에만 있는 항목명'
/// 의 조합으로 판별한다 — 회의록 본문에 "계약서"가 언급될 수는 있어도 갑·을·
/// 제O조·계약기간이 함께 나오지는 않는다.
#[derive(Clone, Default, Debug, PartialEq)]
pub struct FormSpec {
    pub all_of: Vec<String>,
    pub any_of: Vec<String>,
    pub min_types: usize,
}

impl FormSpec {
    /// 실제로 판정 가능한 조건이 있는지. 빈 껍데기가 "항상 성립"으로 오작동하지
    /// 않도록 스캔 쪽에서 이 값을 먼저 확인한다.
    pub fn usable(&self) -> bool {
        !self.all_of.is_empty() || (!self.any_of.is_empty() && self.min_types > 0)
    }
}

/// 전역 기본값 블록(doc_rule.yaml 의 defaults:). 규칙에 같은 이름 필드가 있으면
/// 그쪽이 이긴다.
/// [하위호환] min_distinct/min_count 기본값은 1 — 종전 "본문에 1건이라도 있으면
/// 히트"와 완전히 같게 동작한다. 조이는 것은 정책 파일에서 켜는 옵트인이다.
#[derive(Clone, Debug, PartialEq)]
pub struct Defaults {
    pub head_chars: usize,
    pub min_distinct: usize,
    pub min_count: u32,
    pub t_high: f64,
    pub t_low: f64,
    pub t_seed: f64,
    pub embed_cap: f64,
    /// "legacy"(기본) | "staged". Python doc_rules.SCORING_MODES 와 같은 값.
    pub scoring: String,
}

impl Default for Defaults {
    fn default() -> Self {
        Defaults {
            head_chars: 400, min_distinct: 1, min_count: 1,
            t_high: 0.70, t_low: 0.30, t_seed: 0.85, embed_cap: 0.65,
            scoring: "legacy".into(),
        }
    }
}

pub const SCORING_MODES: [&str; 2] = ["legacy", "staged"];

/// 임베딩 전파 설정 블록(doc_rule.yaml 의 embed:).
#[derive(Clone, Debug, PartialEq)]
pub struct EmbedSpec {
    pub enabled: bool,
    pub k: usize,
    pub dup_threshold: f64,
    pub min_sim: f64,
    pub min_share: f64,
    pub prototype: String,
}

impl Default for EmbedSpec {
    fn default() -> Self {
        EmbedSpec { enabled: false, k: 10, dup_threshold: 0.950,
                    min_sim: 0.550, min_share: 0.250, prototype: "off".into() }
    }
}

/// 업무 분류 규칙 1개. cso_rules.yaml 의 KeywordRule 과 문법은 거의 같지만
/// (terms·exclude·weight), grade 대신 node(dc_id)를 갖는다.
#[derive(Clone)]
pub struct DoctypeRule {
    pub id: String,
    pub node: String,
    pub weight: String,
    /// 첫 비어있지 않은 줄에서만 찾을 문서종류어(재설계 6-3). 가장 강한 증거.
    pub title_terms: Vec<String>,
    /// 앞 head_chars 자(표제부)에서만 찾을 문서종류어(재설계 6-2).
    pub head_terms: Vec<String>,
    /// 이 규칙의 표제부 범위. None 이면 defaults.head_chars.
    pub head_chars: Option<usize>,
    /// 서식 필드어 세트(재설계 7장). 조건이 없으면 None.
    pub form: Option<FormSpec>,
    /// 구조 신호 정규식. 보조 전용이라 단독으로는 후보를 만들지 못한다.
    pub structure: Vec<String>,
    /// 본문 키워드. [재설계 8-2] 약한 증거로 격하 — 단독 채택 불가.
    pub terms: Vec<String>,
    /// terms 중 서로 다른 단어 최소 종수. None 이면 defaults.
    pub min_distinct: Option<usize>,
    /// terms 총 등장 건수 하한. None 이면 defaults.
    pub min_count: Option<u32>,
    pub exclude: Vec<String>,
    pub filename: Vec<String>,
    /// '/' 정규화 + 소문자화된 경로 조각(로드 시점에 정규화 — rules::PathRule.matches 와 동일 규약).
    pub paths: Vec<String>,
    /// node 가 doc_taxonomy.yaml 에서 status=0(미사용)이면 false — 매칭을 시도하지 않는다(T6).
    /// taxonomy 없이 로드했으면 항상 true(교차검증을 안 했으므로 판단 불가).
    pub active: bool,
}

/// doc_rule.yaml 전체. rules::RuleSet 이 security 축에서 하는 역할을 doctype 축에서 한다.
pub struct DocRuleSet {
    pub conflict: ConflictSpec,
    pub defaults: Defaults,
    pub embed: EmbedSpec,
    pub version: String,
    pub rules: Vec<DoctypeRule>,
    /// 로드 중 발견한 경고(T6·T12 등 — 로드를 막지 않는 문제).
    pub warnings: Vec<String>,
}

impl DocRuleSet {
    /// 규칙 없는 빈 규칙셋 — "seed 전파 전용" 모드용.
    ///
    /// doc_rule.yaml 이 없을 때 쓴다. 이걸 쓰면 doctype 축이 꺼지지 않고 켜진 채
    /// 돌되, 규칙이 하나도 없어 1차 스캔은 아무것도 못 맞힌다. 그 상태로
    /// class_seed.jsonl 과의 임베딩 전파가 라벨을 채운다(seed 도 없으면 미분류로
    /// 남고, 사람이 나중에 분류한다).
    ///
    /// 축을 아예 끄면(None) 레코드에 labels.doctype 키가 안 생기고, 그러면 전파
    /// 단계가 "이 배포는 축을 안 쓴다"고 보고 건너뛴다 — 그래서 '빈 규칙셋으로
    /// 켜 두기'와 '끄기'는 다른 뜻이다. version 을 "none" 으로 박아 두면 결과만
    /// 보고도 "규칙 없이 전파로만 돌았다"를 구분할 수 있다.
    pub fn seed_only() -> Self {
        DocRuleSet {
            conflict: ConflictSpec::default(),
            defaults: Defaults::default(),
            embed: EmbedSpec::default(),
            version: "none".into(),
            rules: Vec::new(),
            warnings: Vec::new(),
        }
    }

    /// 실제로 매칭에 쓸 규칙만(active=false 인 T6 규칙은 뺀다).
    pub fn active_rules(&self) -> Vec<&DoctypeRule> {
        self.rules.iter().filter(|r| r.active).collect()
    }
}

/// doc_rule.yaml 로드 실패 종류. rules::RulesError 와 같은 구분 원칙.
#[derive(Debug)]
pub enum DocRulesError {
    Read(String),
    Invalid(String),
}

impl std::fmt::Display for DocRulesError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DocRulesError::Read(s) | DocRulesError::Invalid(s) => write!(f, "{}", s),
        }
    }
}

/// 검증 위반 1건. code 는 "T0"(구조)·"T2"(conflict 값)·"T5"(스냅샷에 없는 노드)·"T10"(top_n 설정).
pub(crate) struct DVio {
    code: &'static str,
    rule_id: Option<String>,
    field: &'static str,
    value: String,
    pub(crate) detail: String,
}

/// conflict: 값 검사(T2·T10). 정상이면 None.
/// CLI 의 --conflict 실행 시 덮어쓰기도 이 함수를 그대로 쓴다 — doc_rule.yaml 에
/// 적을 때와 실행 시 덮어쓸 때가 항상 같은 기준으로 걸러지게 하기 위해서다(6-5).
pub(crate) fn check_conflict(raw: &Value) -> Option<DVio> {
    if raw.is_null() {
        return None;
    }
    if let Some(v) = raw.as_str() {
        if v == "all" {
            return None;
        }
        if v == "top_n" {
            return Some(DVio { code: "T10", rule_id: None, field: "conflict", value: v.into(),
                detail: "top_n 은 n 값 없이 쓸 수 없습니다".into() });
        }
        return Some(DVio { code: "T2", rule_id: None, field: "conflict", value: v.into(),
            detail: format!(
                "conflict 값이 올바르지 않습니다(taxonomy 축엔 서열이 없어 max 는 쓸 수 없습니다) — {} 중 하나여야 합니다",
                CONFLICT_STRATEGIES.join(" 또는 ")) });
    }
    if raw.is_object() {
        let strategy = raw.get("strategy").and_then(|v| v.as_str()).unwrap_or("");
        if !CONFLICT_STRATEGIES.contains(&strategy) {
            return Some(DVio { code: "T2", rule_id: None, field: "conflict.strategy", value: strategy.into(),
                detail: format!("conflict.strategy 는 {} 중 하나여야 합니다", CONFLICT_STRATEGIES.join(" 또는 ")) });
        }
        if strategy == "top_n" {
            let n = raw.get("n").and_then(|v| v.as_i64());
            if n.map_or(true, |v| v < 1) {
                let shown = raw.get("n").map(|v| v.to_string()).unwrap_or_else(|| "(없음)".into());
                return Some(DVio { code: "T10", rule_id: None, field: "conflict.n", value: shown,
                    detail: "top_n 은 n 이 1 이상의 정수여야 합니다".into() });
            }
        }
        return None;
    }
    Some(DVio { code: "T2", rule_id: None, field: "conflict", value: raw.to_string(),
        detail: "conflict 값의 형식이 올바르지 않습니다".into() })
}

/// conflict: 값 파싱(검증 통과분만 들어온다는 전제).
pub(crate) fn parse_conflict(raw: &Value) -> ConflictSpec {
    if raw.is_null() {
        return ConflictSpec::default();
    }
    if let Some(v) = raw.as_str() {
        return ConflictSpec { strategy: v.to_string(), n: None, min_confidence: None };
    }
    if raw.is_object() {
        return ConflictSpec {
            strategy: raw.get("strategy").and_then(|v| v.as_str()).unwrap_or("all").to_string(),
            n: raw.get("n").and_then(|v| v.as_i64()),
            min_confidence: raw.get("min_confidence").and_then(|v| v.as_f64()),
        };
    }
    ConflictSpec::default()
}

/// defaults: 블록 검사·파싱(T20). 숫자여야 할 자리에 다른 게 오거나 범위를
/// 벗어나면 스캔 단계에서 조용히 이상해지는 대신 로드 시점에 막는다 —
/// 임계값 하나가 틀리면 전체 분류 결과가 통째로 틀어진다.
pub(crate) fn parse_defaults(raw: &Value) -> (Defaults, Vec<DVio>) {
    let mut d = Defaults::default();
    if raw.is_null() {
        return (d, vec![]);
    }
    if !raw.is_object() {
        return (d, vec![DVio { code: "T20", rule_id: None, field: "defaults",
            value: raw.to_string(), detail: "defaults 는 매핑(mapping)이어야 합니다".into() }]);
    }
    let mut out: Vec<DVio> = vec![];

    // (필드, 정수여야 하는가, 하한, 상한). 상한이 없으면 f64::INFINITY.
    // bool 은 숫자로 보지 않는다 — YAML 의 true 가 1 로 새어 들어오면 안 된다.
    fn num(raw: &Value, key: &'static str, is_int: bool, lo: f64, hi: f64,
           out: &mut Vec<DVio>) -> Option<f64> {
        let v = raw.get(key)?;
        if v.is_boolean() || !v.is_number() {
            out.push(DVio { code: "T20", rule_id: None, field: key, value: v.to_string(),
                detail: "숫자여야 합니다".into() });
            return None;
        }
        let f = v.as_f64().unwrap_or(f64::NAN);
        if is_int && f.fract() != 0.0 {
            out.push(DVio { code: "T20", rule_id: None, field: key, value: v.to_string(),
                detail: "정수여야 합니다".into() });
            return None;
        }
        if f < lo || f > hi {
            let rng = if hi.is_finite() { format!("{} 이상 {} 이하", lo, hi) }
                      else { format!("{} 이상", lo) };
            out.push(DVio { code: "T20", rule_id: None, field: key, value: v.to_string(),
                detail: format!("{}여야 합니다", rng) });
            return None;
        }
        Some(f)
    }

    if let Some(v) = num(raw, "head_chars", true, 1.0, f64::INFINITY, &mut out) { d.head_chars = v as usize; }
    if let Some(v) = num(raw, "min_distinct", true, 1.0, f64::INFINITY, &mut out) { d.min_distinct = v as usize; }
    if let Some(v) = num(raw, "min_count", true, 1.0, f64::INFINITY, &mut out) { d.min_count = v as u32; }
    if let Some(v) = num(raw, "t_high", false, 0.0, 1.0, &mut out) { d.t_high = v; }
    if let Some(v) = num(raw, "t_low", false, 0.0, 1.0, &mut out) { d.t_low = v; }
    if let Some(v) = num(raw, "t_seed", false, 0.0, 1.0, &mut out) { d.t_seed = v; }
    if let Some(v) = num(raw, "embed_cap", false, 0.0, 1.0, &mut out) { d.embed_cap = v; }

    if let Some(v) = raw.get("scoring") {
        match v.as_str() {
            Some(m) if SCORING_MODES.contains(&m) => d.scoring = m.to_string(),
            _ => out.push(DVio { code: "T20", rule_id: None, field: "defaults.scoring",
                value: v.to_string(),
                detail: format!("{} 중 하나여야 합니다", SCORING_MODES.join(" | ")) }),
        }
    }

    // 값 하나하나가 정상이어도 조합이 뒤집히면 "약한 후보가 확정 후보보다 세다"
    // 같은 모순이 생긴다.
    if d.t_low > d.t_high {
        out.push(DVio { code: "T20", rule_id: None, field: "defaults.t_low",
            value: d.t_low.to_string(),
            detail: format!("t_low 는 t_high({}) 이하여야 합니다", d.t_high) });
    }
    if d.t_high > d.t_seed {
        out.push(DVio { code: "T20", rule_id: None, field: "defaults.t_seed",
            value: d.t_seed.to_string(),
            detail: format!("t_seed 는 t_high({}) 이상이어야 합니다", d.t_high) });
    }
    (d, out)
}

/// embed: 블록 검사·파싱(T20). defaults 와 같은 원칙으로 로드 시점에 막는다.
pub(crate) fn parse_embed(raw: &Value) -> (EmbedSpec, Vec<DVio>) {
    let mut e = EmbedSpec::default();
    if raw.is_null() {
        return (e, vec![]);
    }
    if !raw.is_object() {
        return (e, vec![DVio { code: "T20", rule_id: None, field: "embed",
            value: raw.to_string(), detail: "embed 는 매핑(mapping)이어야 합니다".into() }]);
    }
    let mut out: Vec<DVio> = vec![];
    if let Some(v) = raw.get("enabled") {
        match v.as_bool() {
            Some(b) => e.enabled = b,
            None => out.push(DVio { code: "T20", rule_id: None, field: "embed.enabled",
                value: v.to_string(), detail: "true 또는 false 여야 합니다".into() }),
        }
    }
    if let Some(v) = raw.get("k") {
        let k = if v.is_boolean() { None } else { v.as_i64() };
        match k {
            Some(x) if x >= 1 => e.k = x as usize,
            _ => out.push(DVio { code: "T20", rule_id: None, field: "embed.k",
                value: v.to_string(), detail: "1 이상의 정수여야 합니다".into() }),
        }
    }
    for key in ["dup_threshold", "min_sim", "min_share"] {
        if let Some(v) = raw.get(key) {
            let f = if v.is_boolean() { None } else { v.as_f64() };
            match f {
                Some(x) if (0.0..=1.0).contains(&x) => match key {
                    "dup_threshold" => e.dup_threshold = x,
                    "min_sim" => e.min_sim = x,
                    _ => e.min_share = x,
                },
                _ => out.push(DVio { code: "T20", rule_id: None, field: "embed",
                    value: v.to_string(),
                    detail: format!("{} 은 0.0~1.0 사이 숫자여야 합니다", key) }),
            }
        }
    }
    if let Some(v) = raw.get("prototype") {
        match v.as_str() {
            Some(p) if ["off", "l1_only", "all"].contains(&p) => e.prototype = p.to_string(),
            _ => out.push(DVio { code: "T20", rule_id: None, field: "embed.prototype",
                value: v.to_string(), detail: "off | l1_only | all 중 하나여야 합니다".into() }),
        }
    }
    (e, out)
}

/// form: 블록 → FormSpec. 조건이 하나도 없는 빈 블록은 None 으로 접는다 —
/// 그대로 두면 스캔 쪽에서 "조건 0개를 모두 만족"으로 오해할 여지가 생긴다.
pub(crate) fn parse_form(raw: Option<&Value>) -> Option<FormSpec> {
    let raw = raw?;
    if !raw.is_object() {
        return None;
    }
    let list = |key: &str| -> Vec<String> {
        raw.get(key).and_then(|v| v.as_array())
            .map(|a| a.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect())
            .unwrap_or_default()
    };
    let spec = FormSpec {
        all_of: list("all_of"),
        any_of: list("any_of"),
        min_types: raw.get("min_types").and_then(|v| v.as_u64()).unwrap_or(0) as usize,
    };
    if spec.usable() { Some(spec) } else { None }
}

/// 규칙 1개의 신규 필드 검사(T20·T21·T22). 정규식 오타는 스캔이 시작돼야 터지는데
/// 그때는 어느 규칙이 범인인지 알기 어렵다 — 로드 시점에 규칙 id 와 함께 잡는다.
fn check_rule_fields(item: &Value) -> Vec<DVio> {
    let mut out: Vec<DVio> = vec![];
    let rid = item.get("id").and_then(|v| v.as_str()).map(|x| x.to_string());

    if let Some(form) = item.get("form") {
        if !form.is_object() {
            out.push(DVio { code: "T21", rule_id: rid.clone(), field: "form",
                value: form.to_string(), detail: "form 은 매핑(mapping)이어야 합니다".into() });
        } else {
            for key in ["all_of", "any_of"] {
                if let Some(v) = form.get(key) {
                    if !v.is_array() {
                        out.push(DVio { code: "T21", rule_id: rid.clone(), field: "form",
                            value: v.to_string(),
                            detail: format!("form.{} 는 목록(list)이어야 합니다", key) });
                    }
                }
            }
            if let Some(v) = form.get("min_types") {
                let n = if v.is_boolean() { None } else { v.as_i64() };
                match n {
                    Some(x) if x >= 1 => {
                        // any_of 보다 큰 min_types 는 절대 성립하지 않는다 —
                        // 조용히 죽은 규칙이 되므로 미리 알린다.
                        let len = form.get("any_of").and_then(|a| a.as_array())
                            .map(|a| a.len()).unwrap_or(0);
                        if x as usize > len {
                            out.push(DVio { code: "T21", rule_id: rid.clone(),
                                field: "form.min_types", value: v.to_string(),
                                detail: format!("any_of 항목 수({})보다 클 수 없습니다 — 이 조건은 절대 성립하지 않습니다", len) });
                        }
                    }
                    _ => out.push(DVio { code: "T21", rule_id: rid.clone(),
                        field: "form.min_types", value: v.to_string(),
                        detail: "1 이상의 정수여야 합니다".into() }),
                }
            }
        }
    }

    if let Some(arr) = item.get("structure").and_then(|v| v.as_array()) {
        for pat in arr {
            if let Some(p) = pat.as_str() {
                if let Err(e) = regex::Regex::new(p) {
                    out.push(DVio { code: "T22", rule_id: rid.clone(), field: "structure",
                        value: p.to_string(),
                        detail: format!("정규식으로 해석할 수 없습니다: {}", e) });
                }
            }
        }
    }

    for key in ["min_distinct", "min_count", "head_chars"] {
        if let Some(v) = item.get(key) {
            let n = if v.is_boolean() { None } else { v.as_i64() };
            if n.map_or(true, |x| x < 1) {
                out.push(DVio { code: "T20", rule_id: rid.clone(), field: "",
                    value: format!("{}={}", key, v),
                    detail: "1 이상의 정수여야 합니다".into() });
            }
        }
    }
    out
}

/// doc_rule.yaml 원본(YAML→Value) 구조 검증 — T0·T2·T10. taxonomy 교차검증(T5)은
/// 별도 함수(cross_validate_nodes)의 몫이다 — Taxonomy 가 있어야 판단 가능하므로.
fn validate_doc_rule_data(data: &Value) -> Vec<DVio> {
    let mut out = vec![];
    if let Some(v) = check_conflict(data.get("conflict").unwrap_or(&Value::Null)) {
        out.push(v);
    }

    // 전역 블록(defaults/embed)은 규칙보다 먼저 본다 — 여기가 틀리면 모든 규칙의
    // 판정이 함께 틀어지므로 규칙 오류보다 먼저 보여 주는 게 낫다.
    out.extend(parse_defaults(data.get("defaults").unwrap_or(&Value::Null)).1);
    out.extend(parse_embed(data.get("embed").unwrap_or(&Value::Null)).1);

    let raw_rules = match data.get("doctype_rules") {
        None => return out,
        Some(v) if v.is_null() => return out,
        Some(v) => match v.as_array() {
            Some(a) => a,
            None => {
                out.push(DVio { code: "T0", rule_id: None, field: "doctype_rules", value: "(목록 아님)".into(),
                    detail: "'doctype_rules' 가 목록(list)이 아닙니다".into() });
                return out;
            }
        },
    };

    let mut seen_ids: HashSet<String> = HashSet::new();
    for (idx, item) in raw_rules.iter().enumerate() {
        if !item.is_object() {
            out.push(DVio { code: "T0", rule_id: None, field: "", value: format!("doctype_rules[{}]", idx),
                detail: "항목이 매핑(mapping)이 아닙니다".into() });
            continue;
        }
        let rule_id = match item.get("id").and_then(|v| v.as_str()) {
            Some(v) if !v.is_empty() => v.to_string(),
            _ => {
                out.push(DVio { code: "T0", rule_id: None, field: "id", value: format!("doctype_rules[{}]", idx),
                    detail: "id 가 비어 있거나 문자열이 아닙니다".into() });
                continue;
            }
        };
        if seen_ids.contains(&rule_id) {
            out.push(DVio { code: "T0", rule_id: Some(rule_id.clone()), field: "id", value: rule_id.clone(),
                detail: "id 가 중복됩니다".into() });
        }
        seen_ids.insert(rule_id.clone());

        let node_ok = item.get("node").and_then(|v| v.as_str()).map(|v| !v.is_empty()).unwrap_or(false);
        if !node_ok {
            let shown = item.get("node").map(|v| v.to_string()).unwrap_or_else(|| "(없음)".into());
            out.push(DVio { code: "T0", rule_id: Some(rule_id.clone()), field: "node", value: shown,
                detail: "node 가 비어 있거나 문자열이 아닙니다".into() });
        }

        out.extend(check_rule_fields(item));
    }
    out
}

/// node 참조 교차검증 — T5(스냅샷에 없는 노드). validate_doc_rule_data 를 통과한
/// (id·node 가 유효한 문자열인) 항목만 들어온다는 전제.
fn cross_validate_nodes(raw_rules: &[Value], taxonomy: &Taxonomy) -> Vec<DVio> {
    let mut out = vec![];
    for item in raw_rules {
        let id = item.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let node = item.get("node").and_then(|v| v.as_str()).unwrap_or("");
        if !taxonomy.contains(node) {
            out.push(DVio { code: "T5", rule_id: Some(id), field: "node", value: node.to_string(),
                detail: "분류체계 스냅샷(doc_taxonomy.yaml)에 없는 dc_id 입니다 — 노드가 삭제됐거나 스냅샷이 낡았을 수 있습니다".into() });
        }
    }
    out
}

/// 위반 목록 → 사람이 읽는 보고문(rules::format_violations 와 같은 형식).
fn format_doc_rule_violations(path: &str, violations: &[DVio]) -> String {
    let title = |code: &str| match code {
        "T0" => "[T0] doc_rule.yaml 구조 오류",
        "T2" => "[T2] conflict 값이 올바르지 않음",
        "T5" => "[T5] 분류체계 스냅샷에 없는 노드 참조",
        "T10" => "[T10] top_n 설정이 올바르지 않음",
        "T20" => "[T20] 임계값·수치 설정이 올바르지 않음",
        "T21" => "[T21] form(서식 필드어) 형식 오류",
        _ => "[T22] structure 정규식 오류",
    };
    let mut lines = vec![
        format!("[규칙셋 오류] {} — 검증 실패 {}건. doctype 축을 로드하지 않았습니다.", path, violations.len()),
        String::new(),
    ];
    for code in ["T0", "T2", "T5", "T10", "T20", "T21", "T22"] {
        let group: Vec<&DVio> = violations.iter().filter(|v| v.code == code).collect();
        if group.is_empty() { continue; }
        lines.push(format!("  {}", title(code)));
        for v in group {
            let where_ = match &v.rule_id { Some(id) => format!("id={}", id), None => "(구조)".into() };
            let field = if v.field.is_empty() { String::new() } else { format!(".{}", v.field) };
            lines.push(format!("    {}{} = {:?}  — {}", where_, field, v.value, v.detail));
        }
        lines.push(String::new());
    }
    lines.push(format!("  고치는 법: {} 를 열어 위 항목을 고치세요.", path));
    lines.join("\n")
}

/// doc_rule.yaml 고정 파일명.
pub fn default_doc_rule_filename() -> &'static str {
    "doc_rule.yaml"
}

/// 새 doc_rule.yaml 의 머리 부분을 가져올 본보기 파일 이름(화면·CLI 공용 규약).
pub const TEMPLATE_NAME: &str = "doc_rule_template.yaml";

/// 본보기에는 있지만 만들어진 doc_rule.yaml 에는 넣지 않는 섹션 —
/// 규칙을 "만들 때" 쓰는 값이라 결과물에 남길 이유가 없다.
const UI_ONLY_KEYS: [&str; 1] = ["new_rule"];

/// 본보기(doc_rule_template.yaml)를 읽는다. 찾는 순서는 '가까운 곳부터'다.
///   1) 만들려는 doc_rule.yaml 과 같은 폴더
///   2) exe 옆
///   3) CSOCLASSIFY_POLICY_DIR
/// 하나도 없으면 빈 매핑 — 본보기가 없다고 골격 생성이 실패하면 안 된다.
fn load_scaffold_template(near: Option<&std::path::Path>) -> serde_yaml::Mapping {
    let mut cands: Vec<std::path::PathBuf> = vec![];
    if let Some(n) = near {
        if let Some(dir) = n.parent() {
            cands.push(dir.join(TEMPLATE_NAME));
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            cands.push(dir.join(TEMPLATE_NAME));
        }
    }
    if let Ok(d) = std::env::var("CSOCLASSIFY_POLICY_DIR") {
        cands.push(std::path::Path::new(&d).join(TEMPLATE_NAME));
    }
    for c in cands {
        if !c.is_file() { continue; }
        let text = match std::fs::read_to_string(&c) { Ok(t) => t, Err(_) => continue };
        match serde_yaml::from_str::<serde_yaml::Value>(&text) {
            Ok(serde_yaml::Value::Mapping(m)) => return m,
            _ => continue,
        }
    }
    serde_yaml::Mapping::new()
}

/// 분류체계로부터 doc_rule.yaml '골격'을 만든다(Python build_scaffold 포팅).
///
/// 사용 중인 노드마다 규칙 한 건씩을 만들되, 본문 키워드(terms)는 **비워 둔다** —
/// "무엇을 계약서로 볼 것인가"는 업무를 아는 사람이 채워야 정확하기 때문이다.
/// 파일명 신호(filename)만 노드 제목으로 채워 두어, 채우기 전에도 파일 이름이
/// 분류명과 같은 문서는 잡히게 한다.
/// 노드 순서는 트리를 위에서 아래로(부모 → 자식) 훑은 순서라 사람이 읽기 좋다.
pub fn build_scaffold(taxonomy: &Taxonomy) -> Value {
    let mut rules: Vec<Value> = vec![];

    // 재귀 대신 명시적 스택을 쓴다 — 분류체계가 깊어져도 스택이 터지지 않는다.
    // children_of 가 (order, dc_id) 순으로 주므로, 역순으로 넣어야 꺼낼 때 정순이 된다.
    fn walk(taxonomy: &Taxonomy, parent: Option<&str>, out: &mut Vec<Value>) {
        for child in taxonomy.children_of(parent) {
            if child.active() {
                out.push(serde_json::json!({
                    "id": format!("dt_{}", child.dc_id.to_lowercase()),
                    "node": child.dc_id,
                    // 제목·앞부분 신호는 분류 이름을 최초 제안값으로 깔아 둔다
                    // (파이썬 build_scaffold 와 같은 필드·같은 값).
                    "title_terms": vec![child.title.clone()],
                    "head_terms": vec![child.title.clone()],
                    "terms": Vec::<String>::new(),
                    "filename": vec![child.title.clone()],
                }));
            }
            // 비활성 노드라도 그 밑의 자식은 살아 있을 수 있으므로 계속 내려간다.
            walk(taxonomy, Some(&child.dc_id), out);
        }
    }
    walk(taxonomy, None, &mut rules);

    serde_json::json!({ "conflict": "all", "doctype_rules": rules })
}

/// doc_rule.yaml 골격을 파일로 저장(이미 있으면 건너뜀).
///
/// 사람이 이미 채워 둔 규칙을 실수로 빈 골격으로 덮어쓰지 않도록, 기본은 파일이
/// 있으면 아무것도 하지 않는다(force=true 로만 덮어쓴다).
///
/// -out: Ok(Some(규칙수)) = 실제로 씀 / Ok(None) = 이미 있어 건너뜀 / Err = 쓰기 실패
pub fn write_scaffold(taxonomy: &Taxonomy, out_path: &std::path::Path, force: bool)
    -> Result<Option<usize>, String>
{
    if out_path.is_file() && !force {
        return Ok(None);
    }
    let scaffold = build_scaffold(taxonomy);
    let rules = scaffold.get("doctype_rules").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let n = rules.len();

    // 머리 부분(scoring·문턱·embed 등)과 새 규칙에 얹을 값(weight)은 본보기에서
    // 가져온다. 코드에 박아 두면 회사마다 다른 기본값을 주려 할 때 프로그램을
    // 고쳐야 하고, 본보기가 없던 시절처럼 conflict 한 줄만 있는 파일이 만들어지면
    // 엔진이 조용히 기본값(legacy 채점)으로 돌아 화면과 판정이 달라진다.
    let tpl = load_scaffold_template(Some(out_path));
    let new_rule: Vec<(String, serde_yaml::Value)> = tpl.get("new_rule")
        .and_then(|v| v.as_mapping())
        .map(|m| m.iter()
            .filter_map(|(k, v)| k.as_str().map(|k| (k.to_string(), v.clone())))
            .collect())
        .unwrap_or_else(|| vec![("weight".to_string(),
                                 serde_yaml::Value::String("medium".into()))]);

    // 사람이 열어 terms 를 채워 넣는 파일이라 키 순서를 읽기 좋게 못박는다
    // (id → node → weight → 말 목록). 알파벳순으로 섞이면 "어디를 채워야 하나"가
    // 한눈에 안 들어온다.
    let ystr = |v: &str| serde_yaml::Value::String(v.to_string());
    let mut items = vec![];
    for r in &rules {
        let strs = |key: &str| serde_yaml::Value::Sequence(
            r.get(key).and_then(|v| v.as_array()).cloned().unwrap_or_default()
                .iter().filter_map(|v| v.as_str()).map(ystr).collect());
        let mut m = serde_yaml::Mapping::new();
        m.insert(ystr("id"), ystr(r.get("id").and_then(|v| v.as_str()).unwrap_or("")));
        m.insert(ystr("node"), ystr(r.get("node").and_then(|v| v.as_str()).unwrap_or("")));
        for (k, v) in &new_rule {
            m.insert(ystr(k), v.clone());
        }
        for key in ["title_terms", "head_terms", "terms", "filename"] {
            m.insert(ystr(key), strs(key));
        }
        items.push(serde_yaml::Value::Mapping(m));
    }

    // 본보기의 머리 부분을 적힌 순서 그대로 옮긴다(UI 전용 섹션은 뺀다).
    let mut doc = serde_yaml::Mapping::new();
    for (k, v) in tpl.iter() {
        let key = match k.as_str() { Some(x) => x, None => continue };
        if UI_ONLY_KEYS.contains(&key) || key == "doctype_rules" { continue; }
        doc.insert(ystr(key), v.clone());
    }
    if !doc.contains_key(ystr("conflict")) {
        doc.insert(ystr("conflict"), ystr("all"));
    }
    doc.insert(ystr("doctype_rules"), serde_yaml::Value::Sequence(items));

    // 이 파일은 화면이 저장할 때마다 통째로 다시 쓰여 주석이 남지 않는다.
    // 값의 뜻은 본보기에 적어 두고, 여기서는 어디를 볼지만 가리킨다
    // (ui/docruleedit.py · classify/doc_rules.py 와 같은 문구).
    let header = "# 업무분류 판단 기준 — 화면(설정 ③ 판단 기준)이 저장할 때마다 다시 쓰는 파일이라\n# 주석이 남지 않는다. 각 값의 뜻과 그 값을 고른 이유는 doc_rule_template.yaml 에 있다.\n";
    if let Some(dir) = out_path.parent() {
        if !dir.as_os_str().is_empty() {
            std::fs::create_dir_all(dir)
                .map_err(|e| format!("폴더를 만들 수 없습니다({}): {}", dir.display(), e))?;
        }
    }
    let body = serde_yaml::to_string(&serde_yaml::Value::Mapping(doc))
        .map_err(|e| format!("YAML 직렬화 실패: {}", e))?;
    std::fs::write(out_path, format!("{}{}", header, body))
        .map_err(|e| format!("쓰기 실패({}): {}", out_path.display(), e))?;
    Ok(Some(n))
}

/// doc_rule.yaml 을 읽어 DocRuleSet 으로. taxonomy 를 주면 T5·T6·T12 교차검증까지
/// 함께 한다(없으면(None) node 값을 그대로 믿고 로드 — Python load_doc_rules 와 동일 규약).
pub fn load_doc_rules(path: &std::path::Path, taxonomy: Option<&Taxonomy>) -> Result<DocRuleSet, DocRulesError> {
    let text = std::fs::read_to_string(path).map_err(|e| {
        DocRulesError::Read(format!(
            "업무분류 규칙셋({})을 찾을 수 없습니다: {} ({})",
            default_doc_rule_filename(), path.display(), e))
    })?;
    let yv: serde_yaml::Value = serde_yaml::from_str(&text)
        .map_err(|e| DocRulesError::Read(format!("YAML 파싱 실패: {}", e)))?;
    let data: Value = serde_json::to_value(&yv)
        .map_err(|e| DocRulesError::Read(format!("변환 실패: {}", e)))?;

    let raw_rules: Vec<Value> = data.get("doctype_rules").and_then(|v| v.as_array()).cloned().unwrap_or_default();

    let mut violations = validate_doc_rule_data(&data);
    if violations.is_empty() {
        if let Some(t) = taxonomy {
            violations.extend(cross_validate_nodes(&raw_rules, t));
        }
    }
    if !violations.is_empty() {
        return Err(DocRulesError::Invalid(
            format_doc_rule_violations(&path.display().to_string(), &violations)));
    }

    let conflict = parse_conflict(data.get("conflict").unwrap_or(&Value::Null));
    // 검증을 이미 통과했으므로 여기서 나오는 위반 목록은 버린다(값만 필요).
    let defaults = parse_defaults(data.get("defaults").unwrap_or(&Value::Null)).0;
    let embed = parse_embed(data.get("embed").unwrap_or(&Value::Null)).0;

    let mut warnings = vec![];
    let mut rules = vec![];
    let mut referenced: HashSet<String> = HashSet::new();
    for item in &raw_rules {
        let id = s(item, "id", "");
        let node = s(item, "node", "");
        let mut active = true;
        if let Some(t) = taxonomy {
            if let Some(tn) = t.get(&node) {
                referenced.insert(node.clone());
                if !tn.active() {
                    active = false;
                    warnings.push(format!(
                        "[T6] doctype_rules[{}].node={:?} ({}) 는 미사용(status=0) 노드입니다 — 이 규칙은 비활성 처리됩니다",
                        id, node, t.path(&node).unwrap_or_default()));
                }
            }
        }
        rules.push(DoctypeRule {
            id, node,
            weight: s(item, "weight", "medium"),
            title_terms: strvec(item, "title_terms"),
            head_terms: strvec(item, "head_terms"),
            head_chars: item.get("head_chars").and_then(|v| v.as_u64()).map(|v| v as usize),
            form: parse_form(item.get("form")),
            structure: strvec(item, "structure"),
            min_distinct: item.get("min_distinct").and_then(|v| v.as_u64()).map(|v| v as usize),
            min_count: item.get("min_count").and_then(|v| v.as_u64()).map(|v| v as u32),
            terms: strvec(item, "terms"),
            exclude: strvec(item, "exclude"),
            filename: strvec(item, "filename"),
            paths: strvec(item, "paths").iter().map(|p| p.replace('\\', "/").to_lowercase()).collect(),
            active,
        });
    }

    if let Some(t) = taxonomy {
        for node in t.active_nodes() {
            if !referenced.contains(&node.dc_id) {
                warnings.push(format!(
                    "[T12] {}({}) 를 참조하는 규칙이 없습니다 — 이 분류로는 자동분류되는 문서가 없습니다",
                    node.dc_id, t.path(&node.dc_id).unwrap_or_default()));
            }
        }
    }
    if rules.is_empty() {
        warnings.push("doctype_rules 가 비어 있어 doctype 축이 아무 문서도 분류하지 않습니다".into());
    }

    Ok(DocRuleSet { conflict, defaults, embed,
                    version: s(&data, "version", "unknown"), rules, warnings })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::axes::{Taxonomy, TaxonomyNode};

    // cargo test 는 기본적으로 테스트를 여러 스레드에서 병렬 실행한다. 여러
    // 테스트 함수가 build_test_taxonomy 를 부르며 같은 임시파일 이름을 쓰면
    // 한 스레드의 remove_file 이 다른 스레드의 read 보다 먼저 끝나 "파일을
    // 찾을 수 없음"으로 흔들린다 — 호출마다 증가하는 카운터로 유일하게 만든다.
    static TMP_COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    fn unique_tmp(prefix: &str) -> std::path::PathBuf {
        let n = TMP_COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        std::env::temp_dir().join(format!("{}_{}_{}.yaml", prefix, std::process::id(), n))
    }

    // 골격은 '사용 중' 노드마다 한 건씩, terms 는 비우고 filename 만 제목으로 채운다.
    #[test]
    fn 골격은_활성노드마다_한건씩_terms는_빈다() {
        let t = build_test_taxonomy(vec![
            TaxonomyNode { dc_id: "A".into(), parent: None, order: 1, title: "법무".into(), status: 1 },
            TaxonomyNode { dc_id: "A_1".into(), parent: Some("A".into()), order: 1,
                           title: "계약서".into(), status: 1 },
            TaxonomyNode { dc_id: "B".into(), parent: None, order: 2, title: "폐기".into(), status: 0 },
        ]);
        let sc = build_scaffold(&t);
        let rules = sc["doctype_rules"].as_array().unwrap();
        // status=0(미사용)인 B 는 골격에 넣지 않는다 — 넣어도 T6 로 비활성될 뿐이다.
        assert_eq!(rules.len(), 2);
        assert_eq!(sc["conflict"], "all");
        // 부모 → 자식 순서(트리를 위에서 아래로)라 사람이 읽기 좋다.
        assert_eq!(rules[0]["node"], "A");
        assert_eq!(rules[1]["node"], "A_1");
        assert_eq!(rules[1]["id"], "dt_a_1");
        assert_eq!(rules[1]["filename"][0], "계약서");
        assert!(rules[1]["terms"].as_array().unwrap().is_empty());
    }

    // 사람이 채워 둔 규칙을 빈 골격으로 덮어쓰면 안 된다(force 로만 덮어쓴다).
    #[test]
    fn 골격은_이미_있으면_덮어쓰지_않는다() {
        let t = build_test_taxonomy(vec![
            TaxonomyNode { dc_id: "A".into(), parent: None, order: 1, title: "법무".into(), status: 1 },
        ]);
        let tmp = unique_tmp("cso_test_scaffold");
        std::fs::write(&tmp, "conflict: all\ndoctype_rules: []\n# 사람이 채운 것\n").unwrap();

        assert_eq!(doc_rules_write(&t, &tmp, false), None);          // 건너뜀
        let kept = std::fs::read_to_string(&tmp).unwrap();
        assert!(kept.contains("사람이 채운 것"), "덮어썼다: {}", kept);

        assert_eq!(doc_rules_write(&t, &tmp, true), Some(1));        // force 면 덮어씀
        let over = std::fs::read_to_string(&tmp).unwrap();
        assert!(!over.contains("사람이 채운 것"));
        let _ = std::fs::remove_file(&tmp);
    }

    fn doc_rules_write(t: &Taxonomy, p: &std::path::Path, force: bool) -> Option<usize> {
        write_scaffold(t, p, force).unwrap()
    }

    fn mk_data(rules: Value, conflict: Option<Value>) -> Value {
        let mut m = serde_json::Map::new();
        m.insert("doctype_rules".into(), rules);
        if let Some(c) = conflict { m.insert("conflict".into(), c); }
        Value::Object(m)
    }

    fn mk_taxonomy() -> Taxonomy {
        build_test_taxonomy(vec![
            TaxonomyNode { dc_id: "A".into(), parent: None, order: 1, title: "루트A".into(), status: 1 },
            TaxonomyNode { dc_id: "A1".into(), parent: Some("A".into()), order: 1, title: "자식A1".into(), status: 1 },
            TaxonomyNode { dc_id: "A2".into(), parent: Some("A".into()), order: 2, title: "자식A2(미사용)".into(), status: 0 },
        ])
    }

    // Taxonomy::build 는 private 이라 테스트 전용 소형 헬퍼로 대체 생성.
    fn build_test_taxonomy(nodes: Vec<TaxonomyNode>) -> Taxonomy {
        // axes.rs 에 테스트 목적의 public 생성자가 없으므로, 실제 로더를 거쳐
        // 만드는 대신 아주 작은 YAML 문자열을 통해 load_taxonomy 로 구성한다.
        let mut yaml = String::from("taxonomy:\n  source: t\n  exported_at: '20260824000000'\n  node_count: 3\n  nodes:\n");
        for n in &nodes {
            let parent = match &n.parent { Some(p) => format!("{:?}", p), None => "null".into() };
            yaml.push_str(&format!(
                "  - {{dc_id: {:?}, parent: {}, order: {}, title: {:?}, status: {}}}\n",
                n.dc_id, parent, n.order, n.title, n.status));
        }
        let tmp = unique_tmp("cso_test_taxonomy");
        std::fs::write(&tmp, yaml).unwrap();
        let t = crate::axes::load_taxonomy(&tmp).unwrap();
        let _ = std::fs::remove_file(&tmp);
        t
    }

    #[test]
    fn conflict_생략시_기본값_all() {
        let data = mk_data(serde_json::json!([]), None);
        assert!(validate_doc_rule_data(&data).is_empty());
    }

    #[test]
    fn doctype_rules_생략은_위반이_아니다() {
        let data: Value = serde_json::json!({});
        assert!(validate_doc_rule_data(&data).is_empty());
    }

    #[test]
    fn doctype_rules가_목록이_아니면_t0() {
        let data: Value = serde_json::json!({"doctype_rules": {"id": "매핑"}});
        let v = validate_doc_rule_data(&data);
        assert_eq!(v.iter().map(|x| x.code).collect::<Vec<_>>(), vec!["T0"]);
    }

    #[test]
    fn id_없으면_t0() {
        let data = mk_data(serde_json::json!([{"node": "A"}]), None);
        assert_eq!(validate_doc_rule_data(&data).iter().map(|x| x.code).collect::<Vec<_>>(), vec!["T0"]);
    }

    #[test]
    fn node_없으면_t0() {
        let data = mk_data(serde_json::json!([{"id": "dt_a"}]), None);
        assert_eq!(validate_doc_rule_data(&data).iter().map(|x| x.code).collect::<Vec<_>>(), vec!["T0"]);
    }

    #[test]
    fn id_중복은_t0() {
        let data = mk_data(serde_json::json!([{"id": "dt_a", "node": "A"}, {"id": "dt_a", "node": "A1"}]), None);
        assert_eq!(validate_doc_rule_data(&data).iter().map(|x| x.code).collect::<Vec<_>>(), vec!["T0"]);
    }

    #[test]
    fn conflict_max는_t2() {
        let data = mk_data(serde_json::json!([]), Some(serde_json::json!("max")));
        assert_eq!(validate_doc_rule_data(&data).iter().map(|x| x.code).collect::<Vec<_>>(), vec!["T2"]);
    }

    #[test]
    fn top_n_n없이_쓰면_t10() {
        let data = mk_data(serde_json::json!([]), Some(serde_json::json!("top_n")));
        assert_eq!(validate_doc_rule_data(&data).iter().map(|x| x.code).collect::<Vec<_>>(), vec!["T10"]);
        let data2 = mk_data(serde_json::json!([]), Some(serde_json::json!({"strategy": "top_n"})));
        assert_eq!(validate_doc_rule_data(&data2).iter().map(|x| x.code).collect::<Vec<_>>(), vec!["T10"]);
    }

    #[test]
    fn top_n_n이_0이면_t10() {
        let data = mk_data(serde_json::json!([]), Some(serde_json::json!({"strategy": "top_n", "n": 0})));
        assert_eq!(validate_doc_rule_data(&data).iter().map(|x| x.code).collect::<Vec<_>>(), vec!["T10"]);
    }

    #[test]
    fn 유효한_top_n은_통과() {
        let data = mk_data(serde_json::json!([]), Some(serde_json::json!({"strategy": "top_n", "n": 3, "min_confidence": 0.6})));
        assert!(validate_doc_rule_data(&data).is_empty());
        let spec = parse_conflict(data.get("conflict").unwrap());
        assert_eq!(spec.strategy, "top_n");
        assert_eq!(spec.n, Some(3));
        assert_eq!(spec.min_confidence, Some(0.6));
    }

    #[test]
    fn taxonomy_없으면_존재하지_않는_node도_그대로_로드() {
        let tmp = unique_tmp("cso_test_docrule");
        std::fs::write(&tmp, "doctype_rules:\n- id: dt_a\n  node: 없는노드\n  terms: [x]\n").unwrap();
        let drs = load_doc_rules(&tmp, None).unwrap();
        let _ = std::fs::remove_file(&tmp);
        assert_eq!(drs.rules[0].node, "없는노드");
        assert!(drs.rules[0].active);
    }

    #[test]
    fn taxonomy_주면_없는_node는_t5() {
        let taxonomy = mk_taxonomy();
        let tmp = unique_tmp("cso_test_docrule2");
        std::fs::write(&tmp, "doctype_rules:\n- id: dt_a\n  node: 없는노드\n  terms: [x]\n").unwrap();
        let err = load_doc_rules(&tmp, Some(&taxonomy)).err().unwrap();
        let _ = std::fs::remove_file(&tmp);
        match err { DocRulesError::Invalid(msg) => assert!(msg.contains("T5")), _ => panic!("Invalid 여야 함") }
    }

    #[test]
    fn 미사용_노드_참조는_t6_경고와_active_false() {
        let taxonomy = mk_taxonomy();
        let tmp = unique_tmp("cso_test_docrule3");
        std::fs::write(&tmp, "doctype_rules:\n- id: dt_a1\n  node: A1\n  terms: [x]\n- id: dt_a2\n  node: A2\n  terms: [y]\n").unwrap();
        let drs = load_doc_rules(&tmp, Some(&taxonomy)).unwrap();
        let _ = std::fs::remove_file(&tmp);
        let a1 = drs.rules.iter().find(|r| r.id == "dt_a1").unwrap();
        let a2 = drs.rules.iter().find(|r| r.id == "dt_a2").unwrap();
        assert!(a1.active);
        assert!(!a2.active);
        assert_eq!(drs.active_rules().len(), 1);
        assert!(drs.warnings.iter().any(|w| w.starts_with("[T6]")));
    }

    #[test]
    fn 참조되지_않는_사용_노드는_t12_경고() {
        let taxonomy = mk_taxonomy();
        let tmp = unique_tmp("cso_test_docrule4");
        std::fs::write(&tmp, "doctype_rules:\n- id: dt_a1\n  node: A1\n  terms: [x]\n").unwrap();
        let drs = load_doc_rules(&tmp, Some(&taxonomy)).unwrap();
        let _ = std::fs::remove_file(&tmp);
        assert!(drs.warnings.iter().any(|w| w.starts_with("[T12]") && w.contains("A(")));
    }

    #[test]
    fn 빈_doctype_rules는_경고를_남긴다() {
        let tmp = unique_tmp("cso_test_docrule5");
        std::fs::write(&tmp, "doctype_rules: []\n").unwrap();
        let drs = load_doc_rules(&tmp, None).unwrap();
        let _ = std::fs::remove_file(&tmp);
        assert!(drs.rules.is_empty());
        assert_eq!(drs.warnings.len(), 1);
    }
}
