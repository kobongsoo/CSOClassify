//! 업무 분류(doctype) 축 — 분류체계 스냅샷 로드 (Python classify/axes.py 포팅).
//! doc_taxonomy.yaml 을 읽어 dc_id 색인과 전체경로("기술/개발 > 설계문서 > ...")를
//! 만든다. security 축(enum Grade, 서열 있음)과 달리 이 축은 트리이고 서열이 없다.
//! rules.rs 와 같은 관례를 따른다: cso_rules.yaml/doc_taxonomy.yaml 모두
//! `#[derive(Deserialize)]` 대신 serde_yaml::Value → serde_json::Value 변환 후
//! s()/os()/oi() 헬퍼로 수동 추출한다(이 파일이라고 새 관례를 만들지 않는다).

use std::collections::{HashMap, HashSet};

use serde_json::{json, Value};

use crate::rules::{oi, os, s};

/// 분류체계 노드 1개(DOC_CLASSIFICATION 한 행 대응). 이름(title)이 아니라
/// dc_id 로 참조한다 — 관리 화면에서 이름을 바꿔도 규칙이 끊어지지 않게(설계서 4-3).
pub struct TaxonomyNode {
    pub dc_id: String,
    pub parent: Option<String>,
    pub order: i64,
    pub title: String,
    pub status: i64,
}

impl TaxonomyNode {
    /// status==1 이면 사용 중.
    pub fn active(&self) -> bool {
        self.status == 1
    }
}

/// 분류체계 트리(스냅샷 전체). dc_id 색인·부모→자식 색인·전체경로 계산을 제공한다.
pub struct Taxonomy {
    pub source: String,
    pub exported_at: String,
    pub node_count: i64,
    pub nodes: Vec<TaxonomyNode>,
    by_id: HashMap<String, usize>,
    children: HashMap<Option<String>, Vec<usize>>,
}

impl Taxonomy {
    /// 노드 목록으로부터 색인을 만든다. children 은 (order, dc_id) 순으로 정렬해
    /// 관리 화면과 같은 순서를 보장한다(Python Taxonomy.__init__ 과 동일 규약).
    fn build(source: String, exported_at: String, node_count: i64, nodes: Vec<TaxonomyNode>) -> Self {
        let mut by_id = HashMap::new();
        for (i, n) in nodes.iter().enumerate() {
            by_id.insert(n.dc_id.clone(), i);
        }
        let mut children: HashMap<Option<String>, Vec<usize>> = HashMap::new();
        for (i, n) in nodes.iter().enumerate() {
            children.entry(n.parent.clone()).or_default().push(i);
        }
        for idxs in children.values_mut() {
            idxs.sort_by(|&a, &b| {
                nodes[a].order.cmp(&nodes[b].order).then_with(|| nodes[a].dc_id.cmp(&nodes[b].dc_id))
            });
        }
        Taxonomy { source, exported_at, node_count, nodes, by_id, children }
    }

    pub fn get(&self, dc_id: &str) -> Option<&TaxonomyNode> {
        self.by_id.get(dc_id).map(|&i| &self.nodes[i])
    }

    pub fn contains(&self, dc_id: &str) -> bool {
        self.by_id.contains_key(dc_id)
    }

    pub fn len(&self) -> usize {
        self.nodes.len()
    }

    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    /// dc_id=None 이면 최상위(root) 목록.
    pub fn children_of(&self, parent: Option<&str>) -> Vec<&TaxonomyNode> {
        let key = parent.map(|p| p.to_string());
        self.children.get(&key).map(|idxs| idxs.iter().map(|&i| &self.nodes[i]).collect()).unwrap_or_default()
    }

    pub fn roots(&self) -> Vec<&TaxonomyNode> {
        self.children_of(None)
    }

    pub fn active_nodes(&self) -> Vec<&TaxonomyNode> {
        self.nodes.iter().filter(|n| n.active()).collect()
    }

    /// 루트부터 dc_id 까지의 dc_id 나열(자기 자신 포함). 트리는 로드 시점에 이미
    /// T7(순환)·T8(고아) 검증을 통과했다고 가정하지만, 검증을 건너뛰고 만든
    /// Taxonomy(테스트 등)에 대한 안전망으로 순환을 여기서도 다시 막는다.
    pub fn path_ids(&self, dc_id: &str) -> Result<Vec<String>, String> {
        let mut node = self.get(dc_id).ok_or_else(|| format!("분류체계에 없는 dc_id 입니다: {:?}", dc_id))?;
        let mut chain = vec![];
        let mut seen = HashSet::new();
        loop {
            if !seen.insert(node.dc_id.clone()) {
                return Err(format!(
                    "분류체계 트리에 순환이 있습니다(dc_id={:?}) — export_taxonomy 로 다시 내보내 확인하세요",
                    node.dc_id));
            }
            chain.push(node.dc_id.clone());
            match &node.parent {
                None => break,
                Some(p) => match self.get(p) {
                    Some(n) => node = n,
                    None => break, // 고아 부모 — 정상 로드 경로에서는 T8 이 이미 막음
                },
            }
        }
        chain.reverse();
        Ok(chain)
    }

    pub fn path_titles(&self, dc_id: &str) -> Result<Vec<String>, String> {
        let ids = self.path_ids(dc_id)?;
        Ok(ids.iter().map(|id| self.get(id).map(|n| n.title.clone()).unwrap_or_default()).collect())
    }

    /// 관리 화면과 같은 표기: "기술/개발 > 설계문서 > 요구사항정의서".
    pub fn path(&self, dc_id: &str) -> Result<String, String> {
        Ok(self.path_titles(dc_id)?.join(" > "))
    }
}

/// 분류체계 스냅샷 로드 실패 종류. rules::RulesError 와 같은 구분 원칙 —
/// Invalid 만 종료코드 4, 나머지는 파일/파싱 문제로 다르게 다룬다.
#[derive(Debug)]
pub enum TaxonomyError {
    Read(String),
    Invalid(String),
}

impl std::fmt::Display for TaxonomyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TaxonomyError::Read(s) | TaxonomyError::Invalid(s) => write!(f, "{}", s),
        }
    }
}

/// 검증 위반 1건. code 는 "T0"(구조)·"T7"(순환)·"T8"(고아).
struct TVio {
    code: &'static str,
    dc_id: Option<String>,
    field: &'static str,
    value: String,
    detail: String,
}

/// 부모를 따라 root 방향으로 걷다가 이미 지나온 dc_id 를 다시 만나면 순환.
/// 고아 부모(스냅샷에 없는 parent)는 T8 소관이라 여기서는 조용히 걷기를 멈춘다.
fn find_cycle(by_id: &HashMap<String, Option<String>>) -> Option<Vec<String>> {
    for start in by_id.keys() {
        let mut seen: Vec<String> = vec![];
        let mut cur = start.clone();
        loop {
            if let Some(idx) = seen.iter().position(|x| x == &cur) {
                let mut cyc = seen[idx..].to_vec();
                cyc.push(cur);
                return Some(cyc);
            }
            seen.push(cur.clone());
            match by_id.get(&cur) {
                Some(Some(p)) => {
                    if !by_id.contains_key(p) {
                        break; // 고아 부모 — T8 담당, 순환 아님
                    }
                    cur = p.clone();
                }
                _ => break,
            }
        }
    }
    None
}

/// 분류체계 스냅샷 원본(YAML→Value) 검증. 위반을 전부 모아 돌려준다
/// (rules::validate_rules_data 와 같은 원칙 — 첫 오류에서 멈추지 않는다).
fn validate_taxonomy_data(data: &Value) -> Vec<TVio> {
    let mut out = vec![];

    let root = match data.get("taxonomy") {
        Some(v) if v.is_object() => v,
        _ => {
            out.push(TVio { code: "T0", dc_id: None, field: "taxonomy", value: "(매핑 아님)".into(),
                detail: "최상위 'taxonomy:' 매핑이 없습니다".into() });
            return out;
        }
    };
    let raw_nodes = match root.get("nodes").and_then(|v| v.as_array()) {
        Some(a) => a,
        None => {
            out.push(TVio { code: "T0", dc_id: None, field: "taxonomy.nodes", value: "(목록 아님)".into(),
                detail: "'nodes' 가 목록(list)이 아닙니다".into() });
            return out;
        }
    };

    let mut seen_ids: HashSet<String> = HashSet::new();
    let mut parsed: Vec<(String, Option<String>)> = vec![];
    for (idx, item) in raw_nodes.iter().enumerate() {
        if !item.is_object() {
            out.push(TVio { code: "T0", dc_id: None, field: "", value: format!("nodes[{}]", idx),
                detail: "항목이 매핑(mapping)이 아닙니다".into() });
            continue;
        }
        let dc_id = match item.get("dc_id").and_then(|v| v.as_str()) {
            Some(v) if !v.is_empty() => v.to_string(),
            _ => {
                out.push(TVio { code: "T0", dc_id: None, field: "dc_id", value: format!("nodes[{}]", idx),
                    detail: "dc_id 가 비어 있거나 문자열이 아닙니다".into() });
                continue;
            }
        };
        if seen_ids.contains(&dc_id) {
            out.push(TVio { code: "T0", dc_id: Some(dc_id.clone()), field: "dc_id", value: dc_id.clone(),
                detail: "dc_id 가 중복됩니다".into() });
            continue;
        }
        if item.get("title").and_then(|v| v.as_str()).unwrap_or("").is_empty() {
            out.push(TVio { code: "T0", dc_id: Some(dc_id.clone()), field: "title", value: "(없음)".into(),
                detail: "title 이 비어 있습니다".into() });
        }
        seen_ids.insert(dc_id.clone());
        parsed.push((dc_id, item.get("parent").and_then(|v| v.as_str()).map(|s| s.to_string())));
    }
    // 구조가 흔들리면(중복 dc_id 등) 부모-자식 관계를 신뢰할 수 없어 T7·T8 은 건너뛴다.
    if out.iter().any(|v| v.code == "T0") {
        return out;
    }

    let by_id: HashMap<String, Option<String>> = parsed.iter().cloned().collect();
    for (dc_id, parent) in &parsed {
        if let Some(p) = parent {
            if !by_id.contains_key(p) {
                out.push(TVio { code: "T8", dc_id: Some(dc_id.clone()), field: "parent", value: p.clone(),
                    detail: format!("상위 dc_id {:?} 가 스냅샷에 없습니다(삭제됐거나 스냅샷이 낡았을 수 있습니다)", p) });
            }
        }
    }
    if let Some(cycle) = find_cycle(&by_id) {
        out.push(TVio { code: "T7", dc_id: Some(cycle[0].clone()), field: "parent", value: cycle.join(" -> "),
            detail: "parent 를 따라가면 자기 자신으로 되돌아옵니다".into() });
    }
    out
}

/// 위반 목록 → 사람이 읽는 보고문(rules::format_violations 와 같은 형식).
fn format_taxonomy_violations(path: &str, violations: &[TVio]) -> String {
    let title = |code: &str| match code {
        "T0" => "[T0] 분류체계 스냅샷 구조 오류",
        "T7" => "[T7] 분류체계 트리에 순환이 있음",
        _ => "[T8] 상위 dc_id 가 스냅샷에 없음(고아 노드)",
    };
    let mut lines = vec![
        format!("[분류체계 오류] {} — 검증 실패 {}건. doctype 축을 로드하지 않았습니다.", path, violations.len()),
        String::new(),
    ];
    for code in ["T0", "T7", "T8"] {
        let group: Vec<&TVio> = violations.iter().filter(|v| v.code == code).collect();
        if group.is_empty() { continue; }
        lines.push(format!("  {}", title(code)));
        for v in group {
            let where_ = match &v.dc_id {
                Some(id) => format!("dc_id={}", id),
                None => "(구조)".into(),
            };
            let field = if v.field.is_empty() { String::new() } else { format!(".{}", v.field) };
            lines.push(format!("    {}{} = {:?}  — {}", where_, field, v.value, v.detail));
        }
        lines.push(String::new());
    }
    lines.push(format!("  고치는 법: {} 를 열어 위 항목을 고치거나, DOC_CLASSIFICATION 을 다시 내보내세요.", path));
    lines.join("\n")
}

/// doc_taxonomy.yaml 고정 파일명(설계서 4-2-1 — 타임스탬프를 붙이지 않는다).
pub fn default_taxonomy_filename() -> &'static str {
    "doc_taxonomy.yaml"
}

/// doc_taxonomy.yaml 을 읽어 Taxonomy 로. 구조·트리 검증(T0·T7·T8)을 파싱 직후·
/// 객체 생성 전에 한다 — 절반쯤 잘못된 트리로 경로 계산이 돌아가는 것을 막기 위해서다
/// (rules::load_rules 와 같은 원칙).
pub fn load_taxonomy(path: &std::path::Path) -> Result<Taxonomy, TaxonomyError> {
    let text = std::fs::read_to_string(path).map_err(|e| {
        TaxonomyError::Read(format!(
            "분류체계 스냅샷({})을 찾을 수 없습니다: {} ({})",
            default_taxonomy_filename(), path.display(), e))
    })?;
    let yv: serde_yaml::Value = serde_yaml::from_str(&text)
        .map_err(|e| TaxonomyError::Read(format!("YAML 파싱 실패: {}", e)))?;
    let data: Value = serde_json::to_value(&yv)
        .map_err(|e| TaxonomyError::Read(format!("변환 실패: {}", e)))?;

    let violations = validate_taxonomy_data(&data);
    if !violations.is_empty() {
        return Err(TaxonomyError::Invalid(
            format_taxonomy_violations(&path.display().to_string(), &violations)));
    }

    let root = data.get("taxonomy").cloned().unwrap_or(json!({}));
    let raw_nodes = root.get("nodes").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let nodes: Vec<TaxonomyNode> = raw_nodes.iter().map(|n| TaxonomyNode {
        dc_id: s(n, "dc_id", ""),
        parent: os(n, "parent"),
        order: oi(n, "order").unwrap_or(0),
        title: s(n, "title", ""),
        status: oi(n, "status").unwrap_or(1),
    }).collect();
    let node_count = oi(&root, "node_count").unwrap_or(nodes.len() as i64);

    Ok(Taxonomy::build(s(&root, "source", ""), s(&root, "exported_at", ""), node_count, nodes))
}

/// doc_classification_export.json 고정 파일명(MpowerV11 내보내기 원본).
pub fn default_export_input_filename() -> &'static str {
    "doc_classification_export.json"
}

/// 지금 시각을 "YYYYMMDDHHMMSS"(로컬 시간)로.
///
/// 파이썬 판이 `datetime.datetime.now()`(로컬)를 쓰므로 여기서도 로컬 시간을 쓴다.
/// UTC 로 찍으면 한국(KST, +9)에서는 자정~오전 9시 사이에 내보낼 때 '어제 날짜'가
/// 박혀, 화면의 "가져온 날짜"가 하루 어긋난다. 새 크레이트를 들이지 않는다는
/// 이 저장소 관례에 맞춰 OS 함수를 직접 부른다.
pub fn now_stamp() -> String {
    #[cfg(windows)]
    {
        // Windows: GetLocalTime 이 이미 연·월·일·시·분·초로 쪼개 주므로
        // tm 구조체 배치를 신경 쓸 필요가 없다(kernel32 는 자동 링크된다).
        #[repr(C)]
        struct SystemTime {
            year: u16, month: u16, day_of_week: u16, day: u16,
            hour: u16, minute: u16, second: u16, milliseconds: u16,
        }
        extern "system" {
            fn GetLocalTime(t: *mut SystemTime);
        }
        let mut st = SystemTime { year: 0, month: 0, day_of_week: 0, day: 0,
                                  hour: 0, minute: 0, second: 0, milliseconds: 0 };
        unsafe { GetLocalTime(&mut st) };
        format!("{:04}{:02}{:02}{:02}{:02}{:02}",
                st.year, st.month, st.day, st.hour, st.minute, st.second)
    }
    #[cfg(not(windows))]
    {
        // Unix: localtime_r 로 epoch 초를 지역시각으로 쪼갠다. 앞 9개 int 필드는
        // glibc·musl 모두 같은 배치라 이만큼만 선언해도 안전하다.
        #[repr(C)]
        struct Tm {
            sec: i32, min: i32, hour: i32, mday: i32, mon: i32,
            year: i32, wday: i32, yday: i32, isdst: i32,
            gmtoff: i64, zone: *const i8,
        }
        extern "C" {
            fn localtime_r(t: *const i64, tm: *mut Tm) -> *mut Tm;
        }
        let secs = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs() as i64).unwrap_or(0);
        let mut tm = Tm { sec: 0, min: 0, hour: 0, mday: 0, mon: 0, year: 0,
                          wday: 0, yday: 0, isdst: 0, gmtoff: 0, zone: std::ptr::null() };
        let ok = unsafe { !localtime_r(&secs, &mut tm).is_null() };
        if !ok {
            return "00000000000000".to_string();
        }
        // tm_year 는 1900 기준, tm_mon 은 0 기준이다.
        format!("{:04}{:02}{:02}{:02}{:02}{:02}",
                tm.year + 1900, tm.mon + 1, tm.mday, tm.hour, tm.min, tm.sec)
    }
}

/// MpowerV11 내보내기 JSON 노드 1개 → 스냅샷 노드.
/// dc_id 가 없으면 그 노드는 쓸 수 없으므로 None 을 돌려 호출부가 건너뛰게 한다.
fn convert_export_node(raw: &Value) -> Option<Value> {
    let dc_id = raw.get("dc_id").and_then(|v| v.as_str())?;
    // order_num·status 는 문자열로 올 수도 있어(DB 내보내기 관례) 둘 다 받아 준다.
    let as_i64 = |v: Option<&Value>, dflt: i64| -> i64 {
        match v {
            Some(Value::Number(n)) => n.as_i64().unwrap_or(dflt),
            Some(Value::String(s)) => s.trim().parse().unwrap_or(dflt),
            _ => dflt,
        }
    };
    Some(json!({
        "dc_id": dc_id,
        "parent": raw.get("parent_dc_id").and_then(|v| v.as_str()),
        "order": as_i64(raw.get("order_num"), 0),
        "title": raw.get("title").and_then(|v| v.as_str()).unwrap_or(""),
        "status": as_i64(raw.get("status"), 1),
    }))
}

/// MpowerV11 내보내기 JSON → doc_taxonomy.yaml 로 쓸 스냅샷 구조 + 경고 목록.
///
///  1) source 는 원본 값을 그대로 옮긴다(예: "Mpower10U.DOC_CLASSIFICATION")
///  2) exported_at 은 '지금' — DB 조회 시각이 아니라 "CSOClassify 가 이 스냅샷을
///     받아들인 시각"이 감사에 더 값지다(파이썬 판과 같은 규약)
///  3) 원본 node_count 가 실제 노드 수와 달라도 막지 않고 경고만 한다 — 원본이
///     낡았을 수는 있어도 변환 자체를 못 할 이유는 아니다
pub fn convert_mpower_json(raw_data: &Value) -> Result<(Value, Vec<String>), String> {
    if !raw_data.is_object() {
        return Err("원본 JSON 최상위가 매핑(mapping)이 아닙니다".into());
    }
    let raw_nodes = match raw_data.get("nodes").and_then(|v| v.as_array()) {
        Some(a) => a,
        None => return Err("원본 JSON 에 'nodes' 목록이 없습니다".into()),
    };

    let mut warnings = vec![];
    let mut nodes = vec![];
    for (idx, raw) in raw_nodes.iter().enumerate() {
        match convert_export_node(raw) {
            Some(n) => nodes.push(n),
            None => warnings.push(format!("nodes[{}] 에 dc_id 필드가 없어 건너뜁니다: {}", idx, raw)),
        }
    }
    if let Some(declared) = raw_data.get("node_count").and_then(|v| v.as_i64()) {
        if declared != nodes.len() as i64 {
            warnings.push(format!(
                "원본의 node_count({})와 실제 변환된 노드 수({})가 다릅니다", declared, nodes.len()));
        }
    }

    let snapshot = json!({
        "taxonomy": {
            "source": raw_data.get("source").and_then(|v| v.as_str())
                .unwrap_or("DOC_CLASSIFICATION"),
            "exported_at": now_stamp(),
            "node_count": nodes.len(),
            "nodes": nodes,
        }
    });
    Ok((snapshot, warnings))
}

/// 키 순서를 지정해 YAML 매핑 만들기.
///
/// serde_json::Value 는 이 저장소 설정에서 BTreeMap 이라 키가 알파벳순으로 섞인다
/// (분류 결과 JSON 은 그래도 상관없지만, 여기서 만드는 두 YAML 은 **사람이 열어
/// 보고 채우는 파일**이라 순서가 곧 읽기 쉬움이다). serde_yaml::Mapping 은 넣은
/// 순서를 지키므로, 내보내기 경로에서만 이 헬퍼로 순서를 못박는다.
/// (serde_json 에 preserve_order 를 켜면 분류 결과 JSON 의 키 순서까지 바뀌어
///  파이썬 판과 대조하던 기준이 흔들리므로 그쪽은 건드리지 않는다.)
pub(crate) fn ymap(pairs: Vec<(&str, serde_yaml::Value)>) -> serde_yaml::Value {
    let mut m = serde_yaml::Mapping::new();
    for (k, v) in pairs {
        m.insert(serde_yaml::Value::String(k.to_string()), v);
    }
    serde_yaml::Value::Mapping(m)
}

/// 스냅샷 구조 → doc_taxonomy.yaml 파일 기록.
///
/// 키 순서를 파이썬 판과 똑같이(source → exported_at → node_count → nodes,
/// 노드는 dc_id → parent → order → title → status) 맞춰 쓴다.
/// exported_at 은 "20260825103000" 처럼 숫자로만 이뤄져 있어 문자열로 넣어야
/// YAML 이 정수로 읽지 않는다(로더가 문자열을 기대한다).
pub fn write_taxonomy_yaml(snapshot: &Value, out_path: &std::path::Path) -> Result<(), String> {
    let root = snapshot.get("taxonomy").cloned().unwrap_or(json!({}));
    let ystr = |v: &str| serde_yaml::Value::String(v.to_string());

    let mut nodes = vec![];
    for n in root.get("nodes").and_then(|v| v.as_array()).cloned().unwrap_or_default() {
        nodes.push(ymap(vec![
            ("dc_id", ystr(n.get("dc_id").and_then(|v| v.as_str()).unwrap_or(""))),
            ("parent", match n.get("parent").and_then(|v| v.as_str()) {
                Some(p) => ystr(p),
                None => serde_yaml::Value::Null,
            }),
            ("order", serde_yaml::Value::Number(
                n.get("order").and_then(|v| v.as_i64()).unwrap_or(0).into())),
            ("title", ystr(n.get("title").and_then(|v| v.as_str()).unwrap_or(""))),
            ("status", serde_yaml::Value::Number(
                n.get("status").and_then(|v| v.as_i64()).unwrap_or(1).into())),
        ]));
    }
    let doc = ymap(vec![("taxonomy", ymap(vec![
        ("source", ystr(root.get("source").and_then(|v| v.as_str()).unwrap_or(""))),
        ("exported_at", ystr(root.get("exported_at").and_then(|v| v.as_str()).unwrap_or(""))),
        ("node_count", serde_yaml::Value::Number((nodes.len() as i64).into())),
        ("nodes", serde_yaml::Value::Sequence(nodes)),
    ]))]);

    write_yaml_file(&doc, out_path)
}

/// YAML 값 하나를 파일로 쓴다(상위 폴더가 없으면 만든다). 내보내기 두 곳이 공유.
pub(crate) fn write_yaml_file(doc: &serde_yaml::Value, out_path: &std::path::Path)
    -> Result<(), String>
{
    if let Some(dir) = out_path.parent() {
        if !dir.as_os_str().is_empty() {
            std::fs::create_dir_all(dir)
                .map_err(|e| format!("폴더를 만들 수 없습니다({}): {}", dir.display(), e))?;
        }
    }
    let text = serde_yaml::to_string(doc)
        .map_err(|e| format!("YAML 직렬화 실패: {}", e))?;
    std::fs::write(out_path, text)
        .map_err(|e| format!("쓰기 실패({}): {}", out_path.display(), e))
}

/// MPOWER JSON → doc_taxonomy.yaml 내보내기(핵심 진입점).
///
///  1) JSON 을 읽는다(BOM 허용 — 윈도우 내보내기가 BOM 을 붙이는 일이 흔하다)
///  2) convert_mpower_json 으로 변환
///  3) write_taxonomy_yaml 로 저장
///  4) load_taxonomy 로 다시 읽어 검증(T0·T7·T8) — 방금 쓴 파일이 실제로 읽히는지
///     여기서 확인하지 않으면 문제를 다음 실행(실제 분류) 때에야 알게 된다
pub fn export_from_mpower_json(input_path: &std::path::Path, output_path: &std::path::Path)
    -> Result<(Taxonomy, Vec<String>), String>
{
    let bytes = std::fs::read(input_path)
        .map_err(|e| format!("원본 JSON 을 읽을 수 없습니다({}): {}", input_path.display(), e))?;
    // UTF-8 BOM 이 있으면 떼고 파싱한다(있는 채로 넘기면 serde_json 이 실패한다).
    let text = String::from_utf8_lossy(&bytes);
    let text = text.strip_prefix('\u{feff}').unwrap_or(&text);
    let raw: Value = serde_json::from_str(text)
        .map_err(|e| format!("원본 JSON 파싱 실패({}): {}", input_path.display(), e))?;

    let (snapshot, warnings) = convert_mpower_json(&raw)?;
    write_taxonomy_yaml(&snapshot, output_path)?;
    let taxonomy = load_taxonomy(output_path).map_err(|e| match e {
        TaxonomyError::Read(m) => m,
        TaxonomyError::Invalid(m) => format!("변환 결과가 검증을 통과하지 못했습니다:\n{}", m),
    })?;
    Ok((taxonomy, warnings))
}

#[cfg(test)]
mod export_tests {
    use super::*;

    // exported_at 은 로더가 문자열로 기대하고 T13(노후 판정)이 앞 8자리를 잘라 쓴다.
    #[test]
    fn now_stamp는_14자리_숫자다() {
        let s = now_stamp();
        assert_eq!(s.len(), 14, "stamp={}", s);
        assert!(s.chars().all(|c| c.is_ascii_digit()), "stamp={}", s);
        // 로컬 시간이므로 '지금'과 크게 어긋나면 안 된다 — 연도만 상식 범위로 본다.
        let year: i64 = s[0..4].parse().unwrap();
        assert!((2020..=2100).contains(&year), "year={}", year);
        let month: i64 = s[4..6].parse().unwrap();
        let day: i64 = s[6..8].parse().unwrap();
        assert!((1..=12).contains(&month) && (1..=31).contains(&day), "stamp={}", s);
    }

    // MpowerV11 내보내기의 필드 이름(parent_dc_id·order_num)을 스냅샷 이름으로 바꾼다.
    #[test]
    fn 내보내기_필드명을_스냅샷_필드명으로_바꾼다() {
        let raw = json!({
            "source": "Mpower10U.DOC_CLASSIFICATION",
            "node_count": 2,
            "nodes": [
                {"dc_id": "A", "parent_dc_id": null, "order_num": 1, "title": "가", "status": 1},
                {"dc_id": "A_1", "parent_dc_id": "A", "order_num": 2, "title": "나", "status": 0},
            ],
        });
        let (snap, warns) = convert_mpower_json(&raw).unwrap();
        assert!(warns.is_empty(), "warns={:?}", warns);
        let t = &snap["taxonomy"];
        assert_eq!(t["source"], "Mpower10U.DOC_CLASSIFICATION");
        assert_eq!(t["node_count"], 2);
        assert_eq!(t["nodes"][0]["parent"], Value::Null);
        assert_eq!(t["nodes"][1]["parent"], "A");
        assert_eq!(t["nodes"][1]["order"], 2);
        assert_eq!(t["nodes"][1]["status"], 0);
    }

    // DB 내보내기가 숫자를 문자열로 주는 일이 흔하다 — 그래도 받아 준다.
    #[test]
    fn 숫자가_문자열로_와도_받는다() {
        let raw = json!({"nodes": [
            {"dc_id": "A", "parent_dc_id": null, "order_num": "3", "title": "가", "status": "0"}]});
        let (snap, _) = convert_mpower_json(&raw).unwrap();
        assert_eq!(snap["taxonomy"]["nodes"][0]["order"], 3);
        assert_eq!(snap["taxonomy"]["nodes"][0]["status"], 0);
    }

    // dc_id 없는 노드는 쓸 수 없으므로 건너뛰되, 조용히 넘기지 않고 경고를 남긴다.
    #[test]
    fn dc_id_없는_노드는_경고하고_건너뛴다() {
        let raw = json!({"node_count": 2, "nodes": [
            {"dc_id": "A", "title": "가"},
            {"title": "dc_id 가 없음"}]});
        let (snap, warns) = convert_mpower_json(&raw).unwrap();
        assert_eq!(snap["taxonomy"]["node_count"], 1);
        // 건너뛴 노드 경고 + node_count 불일치 경고 = 2건
        assert_eq!(warns.len(), 2, "warns={:?}", warns);
    }

    // 구조가 아예 다르면 변환을 시작하지 않는다(무엇이 잘못됐는지 말해 준다).
    #[test]
    fn nodes_가_없으면_오류() {
        assert!(convert_mpower_json(&json!({"source": "x"})).is_err());
        assert!(convert_mpower_json(&json!([1, 2, 3])).is_err());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mk_taxonomy(nodes: Vec<TaxonomyNode>) -> Taxonomy {
        Taxonomy::build("t".into(), "20260824000000".into(), nodes.len() as i64, nodes)
    }

    fn node(dc_id: &str, parent: Option<&str>, order: i64, title: &str, status: i64) -> TaxonomyNode {
        TaxonomyNode { dc_id: dc_id.into(), parent: parent.map(|s| s.into()), order, title: title.into(), status }
    }

    #[test]
    fn 경로_계산이_관리화면과_같은_형식() {
        let t = mk_taxonomy(vec![
            node("A", None, 1, "기술/개발", 1),
            node("A1", Some("A"), 1, "설계문서", 1),
            node("A1a", Some("A1"), 1, "요구사항정의서", 1),
        ]);
        assert_eq!(t.path("A1a").unwrap(), "기술/개발 > 설계문서 > 요구사항정의서");
        assert_eq!(t.path_ids("A1a").unwrap(), vec!["A", "A1", "A1a"]);
    }

    #[test]
    fn 최상위_노드는_order_순() {
        let t = mk_taxonomy(vec![
            node("B", None, 2, "루트B", 1),
            node("A", None, 1, "루트A", 1),
        ]);
        let roots: Vec<&str> = t.roots().iter().map(|n| n.dc_id.as_str()).collect();
        assert_eq!(roots, vec!["A", "B"]);
    }

    #[test]
    fn taxonomy_키_없으면_t0() {
        let data: Value = serde_json::from_str("{}").unwrap();
        let v = validate_taxonomy_data(&data);
        assert_eq!(v.len(), 1);
        assert_eq!(v[0].code, "T0");
    }

    #[test]
    fn nodes_목록_아니면_t0() {
        let data: Value = serde_json::json!({"taxonomy": {"nodes": {"이건": "매핑"}}});
        let v = validate_taxonomy_data(&data);
        assert_eq!(v.len(), 1);
        assert_eq!(v[0].code, "T0");
    }

    #[test]
    fn dc_id_중복은_t0이고_t7_t8은_건너뜀() {
        let data: Value = serde_json::json!({"taxonomy": {"nodes": [
            {"dc_id": "A", "parent": null, "order": 1, "title": "루트A", "status": 1},
            {"dc_id": "A", "parent": "없는부모", "order": 2, "title": "중복A", "status": 1},
        ]}});
        let v = validate_taxonomy_data(&data);
        assert_eq!(v.iter().map(|x| x.code).collect::<Vec<_>>(), vec!["T0"]);
    }

    #[test]
    fn 고아_parent는_t8() {
        let data: Value = serde_json::json!({"taxonomy": {"nodes": [
            {"dc_id": "A1", "parent": "삭제된부모", "order": 1, "title": "자식A1", "status": 1},
        ]}});
        let v = validate_taxonomy_data(&data);
        assert_eq!(v.iter().map(|x| x.code).collect::<Vec<_>>(), vec!["T8"]);
    }

    #[test]
    fn 자기참조는_t7() {
        let data: Value = serde_json::json!({"taxonomy": {"nodes": [
            {"dc_id": "A", "parent": "A", "order": 1, "title": "A", "status": 1},
        ]}});
        let v = validate_taxonomy_data(&data);
        assert_eq!(v.iter().map(|x| x.code).collect::<Vec<_>>(), vec!["T7"]);
    }

    #[test]
    fn 두_노드가_서로를_가리키면_t7() {
        let data: Value = serde_json::json!({"taxonomy": {"nodes": [
            {"dc_id": "A", "parent": "B", "order": 1, "title": "A", "status": 1},
            {"dc_id": "B", "parent": "A", "order": 1, "title": "B", "status": 1},
        ]}});
        let v = validate_taxonomy_data(&data);
        assert_eq!(v.iter().map(|x| x.code).collect::<Vec<_>>(), vec!["T7"]);
    }

    #[test]
    fn 검증을_건너뛴_순환_트리는_path_ids에서_err() {
        let t = mk_taxonomy(vec![
            node("A", Some("B"), 1, "A", 1),
            node("B", Some("A"), 1, "B", 1),
        ]);
        assert!(t.path_ids("A").is_err());
    }

    #[test]
    fn active_nodes_는_status_1만() {
        let t = mk_taxonomy(vec![
            node("A", None, 1, "사용", 1),
            node("B", None, 2, "미사용", 0),
        ]);
        let ids: Vec<&str> = t.active_nodes().iter().map(|n| n.dc_id.as_str()).collect();
        assert_eq!(ids, vec!["A"]);
    }
}
