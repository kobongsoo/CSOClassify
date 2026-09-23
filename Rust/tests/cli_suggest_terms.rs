//! 규칙 단어 제안(--suggest-terms) 의 CLI 계약.
//!
//! 실제 실행 파일을 불러 화면에 찍히는 글과 --suggest-json 결과를 본다. 파이썬 판
//! (cli.py::run_suggest_terms)과 같은 후보·차례·값이어야 화면·배치가 두 판을 같은
//! 방법으로 다룰 수 있다. 여기서는 파이썬을 부르지 않고, 파이썬 판이 내던 값을
//! 붙박이 기대값으로 적어 둔다 — 파이썬이 깔리지 않은 빌드 서버에서도 돌아야 한다.
//!   · 재료가 없으면 seeds_missing(종료 3)
//!   · 확정 문서가 모자라면 까닭만 남기고 후보는 비운다
//!   · 이 분류에만 나오는 말이 후보로 오르고, 나온 자리에 따라 넣을 칸이 달라진다
//!   · 한 폴더에 몰린 말은 '한 폴더 전용' 묶음으로 따로 나온다

use std::path::PathBuf;
use std::process::{Command, Output};

/// 시험마다 따로 쓰는 임시 폴더.
fn tmp_dir(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("csors_suggest_{}_{}", tag, std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// 실행 파일을 부른다. 개발자 PC 의 정책 폴더가 시험 결과를 바꾸지 못하게 막는다.
fn run(tag: &str, args: &[&str]) -> Output {
    let d = tmp_dir(tag);
    Command::new(env!("CARGO_BIN_EXE_MpowerClassify-rs"))
        .args(args)
        .env("CSOCLASSIFY_POLICY_DIR", d.join("no_policy"))
        .env("CSOCLASSIFY_LOGDIR", d.join("log"))
        .output()
        .expect("실행 파일을 부르지 못했다")
}

/// 재료(기준 문서 + 저장된 본문)를 만든다. 분류마다 폴더를 갈라 둔다 —
/// 폴더 보정 때문에 한 폴더짜리는 문턱을 못 넘기 때문이다.
fn fixture(tag: &str) -> (PathBuf, PathBuf) {
    let d = tmp_dir(tag);
    let text_dir = d.join("text");
    std::fs::create_dir_all(&text_dir).unwrap();
    let seeds = d.join("class_seed.jsonl");
    let mut seed_lines = String::new();
    let mut index = String::new();
    let mut add = |file: &str, dc: &str, body: &str, seed: &mut String, idx: &mut String| {
        // 지문은 본문에서 만든다 — 저장된 본문 파일 이름이 곧 지문이다.
        let hash = format!("{:x}", md5_like(body));
        std::fs::write(text_dir.join(format!("{}.txt", hash)), body).unwrap();
        seed.push_str(&format!(
            "{{\"v\":3,\"file\":\"{}\",\"hash\":\"{}\",\"doctype\":[\"{}\"],\"approved_by\":\"tester\"}}\n",
            file, hash, dc));
        idx.push_str(&format!("{{\"hash\":\"{}\",\"file\":\"{}\"}}\n", hash, file));
    };
    for i in 1..=5 {
        add(&format!("D:/f{}/문서{}.hwp", i, i), "DC_001_001",
            &format!("사업 제안 안내\n품목보고서 현황분석 이 들어 있다{}.\n도움말 항목도 있다", i),
            &mut seed_lines, &mut index);
    }
    for i in 1..=4 {
        add(&format!("D:/g{}/회의{}.hwp", i, i), "DC_001_002",
            &format!("회의 결과\n참석자 명단과 안건 정리{}\n도움말 항목도 있다", i),
            &mut seed_lines, &mut index);
    }
    for i in 1..=6 {
        add(&format!("D:/h1/도움서{}.hwp", i), "DC_001_003",
            &format!("관리자 안내\n클라우드저장소 설치안내 연결확인 절차{}", i),
            &mut seed_lines, &mut index);
    }
    // 한 폴더에 넉 장 — 폴더 둘도, 한 폴더 전용(다섯 장)도 못 넘는 자리.
    for i in 1..=4 {
        add(&format!("D:/k1/작업{}.hwp", i), "DC_001_004",
            &format!("작업계획서
작업구분 작업일 세부작업 내용{}", i),
            &mut seed_lines, &mut index);
    }
    std::fs::write(&seeds, seed_lines).unwrap();
    std::fs::write(text_dir.join("_index.jsonl"), index).unwrap();
    (seeds, text_dir)
}

/// 시험용 지문 — 본문마다 다른 이름이면 되므로 암호학적 세기는 필요 없다.
fn md5_like(s: &str) -> u128 {
    let mut h: u128 = 0xcbf29ce484222325;
    for b in s.bytes() {
        h = h.wrapping_mul(0x100000001b3).wrapping_add(b as u128);
    }
    h
}

//------------------------------------------------------------------
// 재료가 한 톨도 없으면 오류로 끝낸다
//=> 조용히 빈 목록을 내면 "제안할 말이 없다"와 "재료가 없다"를 구분할 수 없다.
//------------------------------------------------------------------
#[test]
fn 재료가_없으면_seeds_missing() {
    let d = tmp_dir("nomat");
    let out = run("nomat", &["--suggest-terms", "all", "--json-errors",
                             "--seeds", d.join("없는파일.jsonl").to_str().unwrap()]);
    assert_eq!(out.status.code(), Some(3), "{}", String::from_utf8_lossy(&out.stdout));
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(text.contains("seeds_missing"), "{}", text);
}

//------------------------------------------------------------------
// 확정 문서가 모자라면 까닭만 남긴다
//=> --suggest-min-docs 로 문턱을 올려 '재료 부족' 길을 확인한다.
//------------------------------------------------------------------
#[test]
fn 재료가_적으면_까닭만() {
    let (seeds, text_dir) = fixture("few");
    let out = run("few", &["--suggest-terms", "DC_001_001",
                           "--seeds", seeds.to_str().unwrap(),
                           "--text-dir", text_dir.to_str().unwrap(),
                           "--suggest-min-docs", "9"]);
    let text = String::from_utf8_lossy(&out.stdout);
    assert_eq!(out.status.code(), Some(0), "{}", text);
    assert!(text.contains("확정 문서 5건 — 9건부터 제안합니다"), "{}", text);
    assert!(!text.contains("품목보고서"), "{}", text);
}

//------------------------------------------------------------------
// 이 분류에만 나오는 말이 후보로 오른다(파이썬 판과 같은 줄)
//=> 제목·파일 이름에서 나온 말은 제목·파일이름 칸으로, 앞부분에서만 나온 말은
//   앞부분 칸으로 간다. 두 분류에 다 나오는 '도움말'은 df_neg 문턱에 걸려 빠진다.
//------------------------------------------------------------------
#[test]
fn 이_분류에만_나오는_말이_오른다() {
    let (seeds, text_dir) = fixture("cand");
    let out = run("cand", &["--suggest-terms", "DC_001_001",
                            "--seeds", seeds.to_str().unwrap(),
                            "--text-dir", text_dir.to_str().unwrap()]);
    let text = String::from_utf8_lossy(&out.stdout);
    assert_eq!(out.status.code(), Some(0), "{}", text);
    assert!(text.contains("■ DC_001_001 — 확정 5건 / 폴더 5곳"), "{}", text);
    // 제목 첫 줄의 '사업'은 제목·파일이름 칸으로, 근거가 단단해 기본 체크가 켜진다.
    assert!(text.contains("[v] 사업"), "{}", text);
    assert!(text.lines().any(|l| l.contains("품목보고서") && l.contains("앞부분")), "{}", text);
    // 다른 분류에도 나오는 말은 오르지 않는다.
    assert!(!text.contains("도움말"), "{}", text);
}

//------------------------------------------------------------------
// 한 폴더에 몰린 말은 따로 모아 보여 준다
//=> 폴더 보정 때문에 문턱을 못 넘지만 버리기 아까운 자리다. 기본 체크는 꺼 둔다.
//------------------------------------------------------------------
#[test]
fn 한_폴더_전용_묶음() {
    let (seeds, text_dir) = fixture("clus");
    let out = run("clus", &["--suggest-terms", "DC_001_003",
                            "--seeds", seeds.to_str().unwrap(),
                            "--text-dir", text_dir.to_str().unwrap()]);
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(text.contains("[한 폴더 전용]"), "{}", text);
    assert!(text.lines().any(|l| l.contains("클라우드저장소") && l.contains("<한 폴더 전용>")),
            "{}", text);
    // 한 폴더짜리는 근거가 약해 기본 체크를 켜지 않는다.
    assert!(!text.lines().any(|l| l.contains("클라우드저장소") && l.contains("[v]")), "{}", text);
}

//------------------------------------------------------------------
// --suggest-json 은 화면에 찍힌 것과 같은 값을 파일로 남긴다
//=> 화면 글을 다시 파싱하지 않고도 배치가 결과를 쓸 수 있어야 한다.
//------------------------------------------------------------------
#[test]
fn json_으로도_남긴다() {
    let (seeds, text_dir) = fixture("json");
    let path = tmp_dir("json").join("out.json");
    let out = run("json", &["--suggest-terms", "all",
                            "--seeds", seeds.to_str().unwrap(),
                            "--text-dir", text_dir.to_str().unwrap(),
                            "--suggest-json", path.to_str().unwrap()]);
    assert_eq!(out.status.code(), Some(0), "{}", String::from_utf8_lossy(&out.stdout));
    let text = std::fs::read_to_string(&path).unwrap();
    let got: serde_json::Value = serde_json::from_str(&text).unwrap();
    let arr = got.as_array().expect("목록이어야 한다");
    assert_eq!(arr.len(), 4, "{}", text);
    let first = &arr[0];
    assert_eq!(first["node"], "DC_001_001");
    assert_eq!(first["docs"], 5);
    assert_eq!(first["clusters"], 5);
    // 후보 한 개의 칸 이름·값 모양이 파이썬 판과 같아야 한다.
    let cand = &first["candidates"][0];
    assert!(cand["term"].is_string() && cand["fields"].is_array());
    assert!(cand["df_pos"].is_number() && cand["df_neg"].is_number()
            && cand["score"].is_number());
    assert!(cand["extra"].is_object() && cand["flags"].is_array());
}

//------------------------------------------------------------------
// 문턱을 못 넘은 말도 까닭을 달아 보여 준다 (참고 목록)
//=> "제안할 말이 없습니다" 한 줄만 나오면 관리자는 '뽑을 말이 없는 것'인지
//   '문턱에 걸린 것'인지 구분할 수 없다. 판단할 자료는 내보낸다.
//------------------------------------------------------------------
#[test]
fn 문턱_미달_말도_보여_준다() {
    let (seeds, text_dir) = fixture("below");
    let out = run("below", &["--suggest-terms", "DC_001_004",
                             "--seeds", seeds.to_str().unwrap(),
                             "--text-dir", text_dir.to_str().unwrap()]);
    let text = String::from_utf8_lossy(&out.stdout);
    assert_eq!(out.status.code(), Some(0), "{}", text);
    // 확정 4건이라 '근거가 얕습니다' 까닭이 먼저 붙는다 — 그 아래 참고 목록이 나온다.
    assert!(text.contains("[문턱 미달 — 참고]"), "{}", text);
    assert!(text.lines().any(|l| l.contains("작업계획서")
                             && l.contains("폴더 1곳 — 2곳 이상이어야 합니다")), "{}", text);
    // 기본 체크는 켜지 않는다 — 고르는 것은 사람의 일이다.
    assert!(!text.lines().any(|l| l.contains("작업계획서") && l.contains("[v]")), "{}", text);
}

//------------------------------------------------------------------
// 후보가 있는 분류에는 참고 목록이 붙지 않는다
//=> 후보 아래 긴 목록이 따라붙으면 정작 후보가 묻힌다.
//------------------------------------------------------------------
#[test]
fn 후보가_있으면_참고_목록은_없다() {
    let (seeds, text_dir) = fixture("nobelow");
    let out = run("nobelow", &["--suggest-terms", "DC_001_001",
                               "--seeds", seeds.to_str().unwrap(),
                               "--text-dir", text_dir.to_str().unwrap()]);
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(text.contains("[v] 사업"), "{}", text);
    assert!(!text.contains("[문턱 미달"), "{}", text);
}
