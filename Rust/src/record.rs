//! 분류 레코드 읽기 도우미 — 새 모양과 옛 모양을 한 자리에서 흡수한다.
//!
//! 2026-09-10 부터 레코드가 축별로 평평해졌다.
//!
//! ```text
//! 전:  {grade, confidence, method, decided_by, seed_eligible,
//!       doctype:[dc_id…], labels:{security:{value,…}, doctype:{…}},
//!       signals:{…}, rule_version, ts}
//! 후:  {security:{grade, confidence, method, decided_by, seed_eligible, signals:{…}},
//!       doctype:{status, strategy, values, …},
//!       meta:{rule_version, taxonomy_version, doctype_rule_version, ts}}
//! ```
//!
//! 같은 값을 'labels 안'과 '최상위'에 두 벌 적던 것을 한 벌로 줄이고,
//! 등급(security)과 문서분류체계(doctype)를 나란한 두 칸으로 갈랐다.
//!
//! [왜 별도 모듈인가] 레코드를 읽는 곳이 여기저기 흩어져 있다. 각자
//! "새 모양이면 …, 옛 모양이면 …" 을 적으면 그 조건문이 스무 군데로 퍼지고,
//! 한 군데만 빠뜨려도 옛 결과 파일이 조용히 안 보이게 된다.
//! 파이썬 판의 `csoclassify/record.py` 와 짝이다 — 한쪽만 고치면 두 판이 갈린다.

use serde_json::Value;

/// 옛 `labels` 껍데기를 안전하게 꺼낸다.
/// seed 파일(class_seed.jsonl)도 `labels` 라는 이름의 칸을 쓰는데 모양이 다르다
/// (거기서는 `labels.security` 가 등급 '문자열'이다). 객체일 때만 통과시킨다.
fn labels(rec: &Value) -> Option<&Value> {
    rec.get("labels").filter(|l| l.is_object())
}

/// 근거 묶음(`why`). 3세대에만 있는 칸이다.
fn why(rec: &Value) -> Option<&Value> {
    rec.get("why").filter(|w| w.is_object())
}

/// 등급 축의 근거 묶음. 3세대는 `why.security`, 2세대는 최상위 `security`.
pub fn security<'a>(rec: &'a Value) -> Option<&'a Value> {
    why(rec).and_then(|w| w.get("security")).filter(|s| s.is_object())
        .or_else(|| rec.get("security").filter(|s| s.is_object()))
}

/// 최종 등급("C"/"S"/"O"). 아무 신호도 못 정했으면 `None`(보류).
/// 새 모양 → 옛 `labels.security.value` → 옛 최상위 `grade` 순으로 본다.
pub fn grade_of(rec: &Value) -> Option<&str> {
    // 3세대·1세대는 최상위 grade 가 곧 답이다(같은 이름을 그대로 되살렸다).
    if rec.get("grade").is_some() {
        return rec.get("grade").and_then(|g| g.as_str());
    }
    // 2세대만 security.grade 안에 있었다.
    if let Some(s) = rec.get("security").filter(|s| s.is_object()) {
        return s.get("grade").and_then(|g| g.as_str());
    }
    labels(rec).and_then(|l| l.pointer("/security/value")).and_then(|g| g.as_str())
}

/// 최종 등급을 만든 신호 이름들(`decided_by`).
pub fn decided_by<'a>(rec: &'a Value) -> Option<&'a Value> {
    security(rec).and_then(|s| s.get("decided_by"))
        .or_else(|| labels(rec).and_then(|l| l.pointer("/security/decided_by")))
        .or_else(|| rec.get("decided_by"))
}

/// 신호별 근거(`security.signals`). 옛 레코드는 최상위 `signals`.
pub fn signals<'a>(rec: &'a Value) -> Option<&'a Value> {
    security(rec).and_then(|s| s.get("signals")).filter(|s| s.is_object())
        .or_else(|| rec.get("signals").filter(|s| s.is_object()))
}

/// 문서분류체계 축.
///
/// [축을 안 쓰는 배포와 구분] 이 축을 아예 안 돌린 배포는 칸 자체가 없고,
/// 돌렸는데 후보를 못 찾은 문서는 `values` 가 빈 목록이다. 이 둘은 뜻이 완전히
/// 다르므로(전자는 "안 봄", 후자는 "봤는데 없음") `None` 과 `Some` 으로 가른다.
/// 옛 레코드의 최상위 `doctype` 은 dc_id **배열**이라 객체 검사가 둘을 갈라 준다.
pub fn doctype<'a>(rec: &'a Value) -> Option<&'a Value> {
    if let Some(d) = why(rec).and_then(|w| w.get("doctype")).filter(|d| d.is_object()) {
        return Some(d);
    }
    if let Some(d) = rec.get("doctype").filter(|d| d.is_object()) {
        return Some(d);          // 2세대는 최상위가 객체였다
    }
    labels(rec).and_then(|l| l.get("doctype")).filter(|d| d.is_object())
}

/// 확정된 문서분류 dc_id 목록. 예전에는 이 목록을 최상위 `doctype` 칸에 따로
/// 적어 뒀는데, `values` 에서 기계적으로 나오는 값이라 두 번 적는 것이었다.
pub fn doctype_ids(rec: &Value) -> Vec<Value> {
    // 옛 레코드의 최상위 doctype 은 이미 dc_id 목록이다.
    if let Some(a) = rec.get("doctype").and_then(|d| d.as_array()) {
        return a.clone();
    }
    doctype(rec)
        .and_then(|d| d.get("values"))
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|v| v.get("dc_id").cloned()).collect())
        .unwrap_or_default()
}

/// seed 승격 후보 표식(`security.seed_eligible`).
///
/// [왜 접근자가 필요한가] 2026-09-10 평탄화로 이 칸이 최상위에서 `security`
/// 안으로 들어갔다. 그런데 읽는 쪽 두 군데(1차 임베딩 판정·`--propagate` 의 내부
/// seed 수집)가 여전히 최상위를 보고 있어 **언제나 false** 였다. 오류가 안 나고
/// 값만 조용히 뒤집히는 종류라, 규칙이 고신뢰로 찍어 준 문서가 임베딩에서
/// 통째로 빠지고 업무분류 2단계가 사라지는데도 아무도 못 알아챘다.
/// 파이썬 `record.security_of(rec).get("seed_eligible")` 과 같은 순서로 본다.
pub fn seed_eligible_of(rec: &Value) -> bool {
    if let Some(s) = security(rec) {
        return s.get("seed_eligible").and_then(|b| b.as_bool()).unwrap_or(false);
    }
    // 옛 레코드는 최상위에 있었다(파이썬 security_of 의 옛 모양 가지와 같다).
    rec.get("seed_eligible").and_then(|b| b.as_bool()).unwrap_or(false)
}
