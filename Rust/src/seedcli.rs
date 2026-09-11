//! 기준 문서 CLI 등록(--seed-add / --seed-add-from) — 파이썬 판 seedcli.py 포팅.
//!
//! [두 판이 같은 줄을 내야 한다]
//! 같은 문서·같은 축·같은 검토자로 두 판이 각각 등록하면, ts·vector 를 뺀 나머지가
//! 키 차례까지 같아야 한다. 그래서 검사 규칙·오류 문구·칸 이름을 파이썬 판과
//! 한 줄씩 맞춰 두었다. 한쪽만 고치면 두 판으로 만든 기준 문서가 미묘하게 달라지고
//! 아무도 눈치채지 못한다 — 이 프로젝트에서 가장 나쁜 종류의 버그다.
//!
//! [문서를 읽기 전에 다 검사한다]
//! 절반 읽고 나서 인자가 틀린 것을 알면 이미 쓴 것과 안 쓴 것이 섞여 되돌리기
//! 어렵다. build_plan 이 문서를 한 글자도 읽지 않는 이유가 그것이다.

use std::collections::BTreeSet;
use std::io::Read;

use serde_json::{json, Value};

/// 이 경로로 들어온 씨앗의 출처 표시. 화면은 "upload"/"phase4" 를 쓴다 —
/// 어디서 들어온 씨앗인지 나중에 셀 수 있어야 한다. 파이썬 판과 같은 값.
pub const SOURCE: &str = "cli";

/// 목록 파일 한 줄에서 받아들이는 칸. 모르는 칸이 오면 알려 준다 —
/// 오타(doctypes·grades)를 조용히 무시하면 축이 통째로 빠진 채 등록된다.
const LIST_KEYS: &[&str] = &["file", "doc_id", "grade", "doctype", "note", "reviewer"];

/// 등록 계획 한 줄 — 문서 하나에 얹을 값 전부.
#[derive(Clone, Debug)]
pub struct Plan {
    pub file: String,
    pub doc_id: Option<String>,
    pub grade: Option<String>,
    pub doctype: Vec<String>,
    pub note: String,
    pub reviewer: String,
}

/// 인자에서 모아 온 등록 관련 설정(main.rs 의 Opts 가 채운다).
#[derive(Clone, Debug, Default)]
pub struct SeedArgs {
    pub add: Vec<String>,
    pub add_from: Option<String>,
    pub grade: Option<String>,
    pub doctype: Option<String>,
    pub reviewer: Option<String>,
    pub note: String,
    pub audit: Option<String>,
}

impl SeedArgs {
    /// 이 실행이 기준 문서 등록 모드인가.
    pub fn is_on(&self) -> bool {
        !self.add.is_empty() || self.add_from.is_some()
    }
}

//------------------------------------------------------------------
// 업무분류 문자열을 dc_id 목록으로
//=> "DC_1, DC_2" 처럼 콤마·공백이 섞여 와도 같게 다룬다. 목록 파일에서는
//   문자열 하나가 와도 배열로 만든다 — 엔진이 배열만 읽기 때문이다.
//
// -in: v = 콤마로 이은 문자열
// -out: Vec<String> = dc_id 목록(없으면 빈 목록)
// -out: error = 없음
//------------------------------------------------------------------
pub fn dc_list(v: &str) -> Vec<String> {
    v.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect()
}

//------------------------------------------------------------------
// JSON 값에서 dc_id 목록 뽑기(문자열 하나도 허용)
//
// -in: v = 문자열 또는 배열
// -out: Vec<String>
// -out: error = 없음
//------------------------------------------------------------------
fn dc_from_json(v: Option<&Value>) -> Vec<String> {
    match v {
        Some(Value::String(s)) => dc_list(s),
        Some(Value::Array(a)) => a.iter()
            .filter_map(|x| x.as_str().map(|s| s.trim().to_string()))
            .filter(|s| !s.is_empty()).collect(),
        _ => vec![],
    }
}

//------------------------------------------------------------------
// 등록 계획 만들기 — 인자·목록을 문서별 한 줄로 편다
//=> 문서를 읽기 전에 전부 검사한다(파이썬 build_plan 과 같은 차례·같은 문구).
//    1) 검토자·모드 충돌을 먼저 본다
//    2) 목록이면 한 줄씩 읽어 칸을 정규화하고, 아니면 플래그 값을 모든 문서에 편다
//    3) 축이 하나도 없는 줄·이상한 등급·중복 doc_id 를 막는다
//
// -in: a           = 등록 관련 인자
// -in: has_class   = --file/--dir 을 함께 줬는가(분류 모드와 섞이면 안 된다)
// -out: Ok(Vec<Plan>) / Err((종류, 사람이 읽는 한 줄))
//       종류는 "bad_args" 또는 "mode_conflict" — 둘 다 종료코드 3
// -out: error = 위 Err 로만 알린다(패닉 없음)
//------------------------------------------------------------------
pub fn build_plan(a: &SeedArgs, has_class: bool) -> Result<Vec<Plan>, (&'static str, String)> {
    let reviewer = a.reviewer.clone().unwrap_or_default().trim().to_string();
    if reviewer.is_empty() {
        return Err(("bad_args",
            "--seed-reviewer <이름> 이 필요합니다 — \
             누가 확정했는지 모르는 기준 문서는 만들지 않습니다".into()));
    }
    if !a.add.is_empty() && a.add_from.is_some() {
        return Err(("mode_conflict",
            "--seed-add 와 --seed-add-from 은 함께 쓸 수 없습니다 — \
             값이 다를 때 무엇이 이기는지 매번 되짚게 됩니다".into()));
    }
    if has_class {
        return Err(("mode_conflict",
            "--seed-add 는 --file/--dir 과 함께 쓸 수 없습니다".into()));
    }

    let mut plan: Vec<Plan> = vec![];
    if let Some(src) = &a.add_from {
        for (n, row) in read_list(src)? {
            let file = row.get("file").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
            if file.is_empty() {
                return Err(("bad_args", format!("목록 {}번째 줄에 file 이 없습니다", n)));
            }
            if let Some(m) = row.as_object() {
                let unknown: Vec<&String> = m.keys()
                    .filter(|k| !LIST_KEYS.contains(&k.as_str())).collect();
                if !unknown.is_empty() {
                    // 조용히 무시하면 grade 오타 하나로 축이 통째로 빠진 채 등록된다.
                    let mut got: Vec<String> = unknown.iter().map(|s| (*s).clone()).collect();
                    got.sort();
                    return Err(("bad_args", format!(
                        "목록 {}번째 줄에 모르는 칸이 있습니다: {} (쓸 수 있는 칸: {})",
                        n, got.join(", "),
                        LIST_KEYS.iter().collect::<BTreeSet<_>>().iter()
                            .map(|s| s.to_string()).collect::<Vec<_>>().join(", "))));
                }
            }
            let note = row.get("note").and_then(|v| v.as_str())
                .map(|s| s.to_string()).unwrap_or_else(|| a.note.clone());
            let rev = row.get("reviewer").and_then(|v| v.as_str())
                .map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
                .unwrap_or_else(|| reviewer.clone());
            plan.push(Plan {
                file,
                doc_id: row.get("doc_id").and_then(|v| v.as_str())
                    .map(|s| s.trim().to_string()).filter(|s| !s.is_empty()),
                grade: row.get("grade").and_then(|v| v.as_str())
                    .map(|s| s.trim().to_string()).filter(|s| !s.is_empty()),
                doctype: dc_from_json(row.get("doctype")),
                note, reviewer: rev,
            });
        }
    } else {
        let grade = a.grade.clone().map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
        let dcs = a.doctype.as_deref().map(dc_list).unwrap_or_default();
        for f in &a.add {
            plan.push(Plan {
                file: f.clone(), doc_id: None, grade: grade.clone(),
                doctype: dcs.clone(), note: a.note.clone(), reviewer: reviewer.clone(),
            });
        }
    }

    if plan.is_empty() {
        return Err(("bad_args", "등록할 문서가 없습니다".into()));
    }
    for p in &plan {
        // 축을 하나도 안 정한 줄은 아무 뜻이 없다 — 저장해도 기준으로 쓰이지 않는다.
        if p.grade.is_none() && p.doctype.is_empty() {
            return Err(("bad_args", format!(
                "{}: 보안등급도 업무분류도 없습니다 — \
                 --seed-grade 나 --seed-doctype 중 하나는 필요합니다", p.file)));
        }
        if let Some(g) = &p.grade {
            if !matches!(g.as_str(), "C" | "S" | "O") {
                return Err(("bad_args", format!(
                    "{}: 보안등급은 C·S·O 중 하나여야 합니다(받은 값: {})", p.file, g)));
            }
        }
    }
    // 같은 신분증을 두 문서에 붙이면 받는 쪽에서 한 문서가 다른 문서를 덮는다.
    let mut seen: Vec<(String, String)> = vec![];
    for p in &plan {
        if let Some(d) = &p.doc_id {
            if let Some((_, prev)) = seen.iter().find(|(x, _)| x == d) {
                return Err(("bad_args", format!(
                    "같은 doc_id 를 두 문서에 줬습니다: {} ({} · {})", d, prev, p.file)));
            }
            seen.push((d.clone(), p.file.clone()));
        }
    }
    Ok(plan)
}

//------------------------------------------------------------------
// 목록 파일(jsonl) 읽기
//=> '-' 면 표준입력을 읽는다 — 웹이 임시파일을 만들지 않아도 되게.
//   깨진 줄은 건너뛰지 않고 실패시킨다. 등록은 '몇 건을 넣었나'가 계약인데,
//   조용히 건너뛰면 부르는 쪽이 몇 건이 빠졌는지 알 수 없다.
//
// -in: path = 목록 경로 또는 "-"
// -out: Ok(Vec<(줄번호, 값)>) / Err((종류, 한 줄))
// -out: error = 위 Err 로만 알린다
//------------------------------------------------------------------
fn read_list(path: &str) -> Result<Vec<(usize, Value)>, (&'static str, String)> {
    let text = if path == "-" {
        let mut s = String::new();
        std::io::stdin().read_to_string(&mut s)
            .map_err(|e| ("bad_args", format!("표준입력을 읽지 못했습니다: {}", e)))?;
        s
    } else {
        std::fs::read_to_string(path)
            .map_err(|e| ("bad_args", format!("목록을 읽지 못했습니다: {} :: {}", path, e)))?
    };
    let mut out = vec![];
    for (i, line) in text.lines().enumerate() {
        let n = i + 1;
        let line = line.trim();
        if line.is_empty() { continue; }
        // '#' 로 시작하는 줄은 사람이 적어 둔 설명으로 보고 건너뛴다.
        //=> --filelist 와 같은 규칙을 써야 한다. 판정을 여기 복사해 두면 나중에
        //   한쪽만 고쳐져 같은 목록이 두 옵션에서 다르게 읽힌다 — 그래서 그쪽
        //   함수를 그대로 쓴다(경로가 '#' 로 시작하는 경우까지 같이 처리된다).
        //   [줄 번호는 그대로] 오류의 N 번째 줄은 눈으로 세는 줄이어야 하므로
        //   건너뛴 줄도 번호에는 포함된다.
        if crate::filelist::is_comment(line) { continue; }
        let v: Value = serde_json::from_str(line)
            .map_err(|e| ("bad_args", format!("목록 {}번째 줄이 JSON 이 아닙니다: {}", n, e)))?;
        if !v.is_object() {
            return Err(("bad_args", format!("목록 {}번째 줄이 객체가 아닙니다", n)));
        }
        out.push((n, v));
    }
    Ok(out)
}

//------------------------------------------------------------------
// 변경 기록 파일 경로 정하기
//=> --seed-audit 을 주면 그것, 아니면 --seeds 와 같은 폴더의
//   class_seed_audit.jsonl. 화면의 기본값과 다를 수 있어 실행 요약에 찍는다.
//
// -in: a     = 등록 관련 인자
// -in: seeds = --seeds 경로(없으면 stdout 모드라 감사 기록도 없다)
// -out: Option<String>
// -out: error = 없음
//------------------------------------------------------------------
pub fn audit_path(a: &SeedArgs, seeds: Option<&String>) -> Option<String> {
    if let Some(p) = &a.audit { return Some(p.clone()); }
    let s = seeds?;
    let dir = std::path::Path::new(s).parent()?;
    Some(dir.join("class_seed_audit.jsonl").to_string_lossy().to_string())
}

//------------------------------------------------------------------
// 등록에 쓸 doc_id 고르기
//=> 목록이 준 값이 가장 세다(부르는 쪽이 아는 진짜 신분증). 없으면 레코드의
//   sfile_id 만 쓰고, 그것도 없으면 칸을 아예 안 만든다.
//
//   [왜 폴백을 안 쓰나] 폴백 doc_id 는 '파일 해시 앞 40자'라 이미 hash 칸에
//   있고, 그 값을 실으면 외부 문서 ID 처럼 생긴 값이 적재 쪽으로 흘러가
//   같은 문서가 두 건으로 들어간다(파이썬 _simple_record 와 같은 규약).
//
// -in: plan = 이 문서의 등록 계획
// -in: rec  = 분류 결과 레코드
// -out: Option<String>
// -out: error = 없음
//------------------------------------------------------------------
pub fn pick_doc_id(plan: &Plan, rec: &Value) -> Option<String> {
    if let Some(d) = &plan.doc_id { return Some(d.clone()); }
    if rec.get("doc_id_source").and_then(|v| v.as_str()) == Some("sfile_id") {
        return rec.get("doc_id").and_then(|v| v.as_str()).map(|s| s.to_string());
    }
    None
}

//------------------------------------------------------------------
// stdout 모드로 내보낼 줄 만들기 — 벡터는 맨 뒤
//=> --seeds 를 안 준 경우에는 파일을 건드리지 않고 등록될 줄만 낸다.
//   받는 쪽이 그대로 병합하면 되도록 저장 모양과 똑같이 만든다.
//
// -in: row = seedstore 가 만든 v3 줄
// -out: String = jsonl 한 줄
// -out: error = 없음
//------------------------------------------------------------------
pub fn to_line(row: &Value) -> String {
    serde_json::to_string(&crate::seedstore::ordered(row)).unwrap_or_default()
}

//------------------------------------------------------------------
// 등록 결과 한 줄 요약(사람이 읽는 글 — stderr 로 나간다)
//
// -in: added  = 등록된 건수
// -in: failed = 실패한 건수
// -in: where_ = 저장한 곳(파일 경로 또는 None=stdout)
// -in: total  = 파일에 남은 전체 줄 수
// -out: String
// -out: error = 없음
//------------------------------------------------------------------
pub fn summary(added: usize, failed: usize, where_: Option<&String>, total: usize) -> String {
    let mut s = format!("[seed-add] {}건 등록", added);
    if failed > 0 { s.push_str(&format!(" · {}건 실패", failed)); }
    match where_ {
        Some(p) => s.push_str(&format!(" → {} (전체 {}줄)", p, total)),
        None => s.push_str(" → stdout (파일은 건드리지 않았습니다)"),
    }
    s
}

//------------------------------------------------------------------
// 실패 한 건을 사람이 읽는 줄로
//
// -in: file = 문서 경로
// -in: why  = 사유
// -out: String
// -out: error = 없음
//------------------------------------------------------------------
pub fn fail_line(file: &str, why: &str) -> String {
    format!("[seed-add] 실패: {} — {}", file, why)
}

//------------------------------------------------------------------
// 감사 기록에 넣을 '바뀐 뒤 값'
//=> 축에 따라 문자열이거나 배열이다. 파이썬 판이 그렇게 남기므로 맞춘다.
//
// -in: axis = "security" | "doctype"
// -in: p    = 등록 계획
// -out: Value
// -out: error = 없음
//------------------------------------------------------------------
pub fn after_value(axis: &str, p: &Plan) -> Value {
    if axis == "security" {
        p.grade.as_ref().map(|g| json!(g)).unwrap_or(Value::Null)
    } else {
        json!(p.doctype)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 목록에 임시 파일을 하나 만들어 경로를 돌려준다.
    fn write(name: &str, body: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join("csoc_seedcli");
        std::fs::create_dir_all(&d).unwrap();
        let p = d.join(name);
        std::fs::write(&p, body).unwrap();
        p
    }

    //--------------------------------------------------------------
    // 목록의 '#' 주석 줄은 건너뛴다 (--filelist 와 같은 규칙)
    //=> 같은 모양의 목록인데 한쪽만 주석이 되면 쓰는 사람이 매번 어느 쪽인지
    //   되짚어야 한다. 판정은 filelist::is_comment 를 그대로 쓴다.
    //   Python 판 tests/test_seed_add.py 에 같은 벡터가 있다 — 한쪽만 바뀌면
    //   다른 쪽 시험이 빨간불이 된다.
    //--------------------------------------------------------------
    #[test]
    fn 목록의_주석줄은_건너뛴다() {
        let p = write("c.jsonl",
            "# 이 목록은 2026-09 등록분입니다
#
## 굵은 구분선
             {\"file\": \"a.txt\", \"grade\": \"C\"}
");
        let rows = read_list(p.to_str().unwrap()).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].1.get("file").unwrap().as_str().unwrap(), "a.txt");
    }

    //--------------------------------------------------------------
    // '#' 로 시작하는 경로는 주석이 아니다 (오탐 방지)
    //=> '#외부유출금지#' 같은 폴더가 실제로 있다. '#' 하나로 잘라 내면 그런
    //   문서가 조용히 빠진다.
    //--------------------------------------------------------------
    #[test]
    fn 샵으로_시작하는_경로는_주석이_아니다() {
        let p = write("c2.jsonl",
            "{\"file\": \"#외부유출금지#/b.docx\", \"grade\": \"S\"}
");
        let rows = read_list(p.to_str().unwrap()).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].1.get("file").unwrap().as_str().unwrap(),
                   "#외부유출금지#/b.docx");
    }

    //--------------------------------------------------------------
    // 주석을 건너뛰어도 오류의 줄 번호는 실제 줄 번호다
    //=> "N 번째 줄"은 사람이 편집기에서 눈으로 세는 번호여야 한다.
    //--------------------------------------------------------------
    #[test]
    fn 주석을_건너뛰어도_줄번호는_실제줄() {
        let p = write("c3.jsonl",
            "# 주석
#
## 두번
{\"file\": \"a.txt\", \"grade\": \"C\"}
깨진줄
");
        let err = read_list(p.to_str().unwrap()).unwrap_err();
        assert!(err.1.contains("5번째 줄"), "실제: {}", err.1);
    }
}
