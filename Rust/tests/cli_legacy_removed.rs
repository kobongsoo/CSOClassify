//! 설계 7단계 — 옛 방식(분류체계 doc_taxonomy.yaml 을 따로 두던 길)을 없앤 뒤의 CLI 계약.
//!
//! 실제 실행 파일을 불러 종료코드와 오류 JSON(--json-errors)을 본다. 파이썬 판과
//! 같은 코드 이름·같은 종료코드여야 화면·배치가 두 판을 같은 방법으로 다룰 수 있다.
//!   · 없어진 옵션 → unsupported_option(1012, 종료 3)
//!   · 옛 모양 규칙 파일 → doc_rules_invalid(종료 4)
//!   · 규칙 파일 없음 → 기본은 축을 건너뛰고 정상(0), --axis doctype 이면 taxonomy_missing(3)
//!   · 자동 생성 규칙 → 정상(0)

use std::path::{Path, PathBuf};
use std::process::{Command, Output};

/// 저장소에 들어 있는 배포용 정책 폴더(보안등급 규칙 + 자동 생성 업무분류 규칙).
fn policy_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("resources").join("policy")
}

/// 시험마다 따로 쓰는 임시 폴더 — 병렬 실행에서 파일이 서로 밟히지 않게 이름을 가른다.
fn tmp_dir(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("csors_legacy_{}_{}", tag, std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// 실행 파일을 부른다. 정책 폴더 환경변수는 빈 임시 폴더로 막아, 개발자 PC 의 설정이
/// 시험 결과를 바꾸지 못하게 한다. 로그도 임시 폴더로 보낸다(저장소를 어지르지 않게).
fn run(tag: &str, args: &[&str]) -> Output {
    let d = tmp_dir(tag);
    Command::new(env!("CARGO_BIN_EXE_MpowerClassify-rs"))
        .args(args)
        .env("CSOCLASSIFY_POLICY_DIR", d.join("no_policy"))
        .env("CSOCLASSIFY_LOGDIR", d.join("log"))
        .output()
        .expect("실행 파일을 부르지 못했다")
}

/// stdout 마지막 줄의 오류 JSON 에서 (code, kind, message) 를 꺼낸다.
fn last_error(out: &Output) -> (i64, String, String) {
    let text = String::from_utf8_lossy(&out.stdout);
    let line = text.lines().filter(|l| l.trim_start().starts_with('{')).last()
        .unwrap_or_else(|| panic!("오류 JSON 이 없다. stdout={} stderr={}",
                                  text, String::from_utf8_lossy(&out.stderr)));
    let v: serde_json::Value = serde_json::from_str(line).unwrap();
    let e = &v["error"];
    (e["code"].as_i64().unwrap_or(-1), e["kind"].as_str().unwrap_or("").to_string(),
     e["message"].as_str().unwrap_or("").to_string())
}

#[test]
fn 없어진_옵션은_unsupported_option_으로_끝난다() {
    for flag in ["--export-taxonomy", "--scaffold-doc-rule", "--sync-doc-rule",
                 "--no-fill-blank", "--sync-enrich"] {
        let out = run("opt", &[flag, "--json-errors"]);
        assert_eq!(out.status.code(), Some(3), "{}", flag);
        let (code, kind, msg) = last_error(&out);
        assert_eq!((code, kind.as_str()), (1012, "unsupported_option"), "{}", flag);
        assert!(msg.contains(&format!("{} 는 없어졌습니다", flag)), "{}: {}", flag, msg);
        assert!(msg.contains("--build-doc-rule"), "{}: {}", flag, msg);
    }
}

#[test]
fn taxonomy_옵션은_값이_있든_없든_막는다() {
    // 값이 빠져도 '값이 없습니다'(bad_args)가 아니라 '없어진 옵션'으로 답해야 한다.
    for args in [vec!["--taxonomy", "--json-errors"],
                 vec!["--json-errors", "--taxonomy"],
                 vec!["--taxonomy", "doc_taxonomy.yaml", "--json-errors"]] {
        let out = run("tax", &args);
        assert_eq!(out.status.code(), Some(3), "{:?}", args);
        let (code, kind, msg) = last_error(&out);
        assert_eq!((code, kind.as_str()), (1012, "unsupported_option"), "{:?}", args);
        assert!(msg.contains("--taxonomy 는 없어졌습니다"), "{:?}: {}", args, msg);
    }
}

#[test]
fn 옛_모양_규칙은_check_rules_에서_종료4() {
    let d = tmp_dir("old");
    let old = d.join("doc_rule.yaml");
    std::fs::write(&old, "conflict: all\ndoctype_rules:\n- id: dt_a\n  node: A\n  terms: [x]\n").unwrap();
    let rules = policy_dir().join("cso_rule.yaml");
    for axis in [None, Some("doctype")] {
        let mut args = vec!["--check-rules", "--json-errors",
                            "--rules", rules.to_str().unwrap(), "--doc-rules", old.to_str().unwrap()];
        if let Some(a) = axis { args.extend(["--axis", a]); }
        let out = run("old", &args);
        assert_eq!(out.status.code(), Some(4), "axis={:?}", axis);
        let (_, kind, msg) = last_error(&out);
        assert_eq!(kind, "doc_rules_invalid", "axis={:?}", axis);
        assert!(msg.contains("[G0] 옛 모양 규칙 파일입니다"), "axis={:?}: {}", axis, msg);
    }
}

#[test]
fn 규칙_파일이_없으면_건너뛰고_axis_doctype_이면_종료3() {
    let d = tmp_dir("missing");
    let missing = d.join("없는_doc_rule.yaml");
    let rules = policy_dir().join("cso_rule.yaml");
    let base = ["--check-rules", "--rules", rules.to_str().unwrap(),
                "--doc-rules", missing.to_str().unwrap()];

    // 기본: 알리고 보안등급만 — 정상 종료.
    let out = run("missing", &base);
    assert_eq!(out.status.code(), Some(0), "stderr={}", String::from_utf8_lossy(&out.stderr));
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(err.contains("업무분류(doctype) 축을 건너뜁니다"), "{}", err);
    assert!(String::from_utf8_lossy(&out.stdout).contains("꺼져 있습니다"));

    // --axis doctype: 부른 쪽이 고칠 문제(3).
    let mut args = base.to_vec();
    args.extend(["--axis", "doctype", "--json-errors"]);
    let out = run("missing", &args);
    assert_eq!(out.status.code(), Some(3));
    let (code, kind, msg) = last_error(&out);
    assert_eq!((code, kind.as_str()), (2003, "taxonomy_missing"));
    assert!(msg.contains("--build-doc-rule 로 만드세요"), "{}", msg);
}

#[test]
fn 자동_생성_규칙은_check_rules_를_통과한다() {
    let p = policy_dir();
    let out = run("gen", &["--check-rules",
                           "--rules", p.join("cso_rule.yaml").to_str().unwrap(),
                           "--doc-rules", p.join("doc_rule.yaml").to_str().unwrap()]);
    assert_eq!(out.status.code(), Some(0), "stderr={}", String::from_utf8_lossy(&out.stderr));
    assert!(String::from_utf8_lossy(&out.stdout).contains("업무분류(doctype) 축 정상"));
}
