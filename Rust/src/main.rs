//! csoclassify-rs — CSOClassify rule-only 분류 코어의 Rust 포팅 (PoC).
//! 추출(text/html/ooxml) + 규칙 분류(PII·키워드·민감·스탬프·경로·파일명·융합) + CLI.

mod axes;
mod conflict;
mod detect;
mod districts;
mod doc_rules;
mod doctype;
mod docvocab;
mod embed;
mod errcodes;
mod errlog;
mod extract;
mod filelist;
mod hwp5;
mod office_legacy;
mod ole;
mod pii;
mod pii_fold;
mod propagate;
mod rules;
mod xls;

use std::path::{Path, PathBuf};
use std::time::Instant;

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use axes::Taxonomy;
use doc_rules::{ConflictSpec, DocRuleSet};
use rules::{build_record, load_rules, Grade, RuleSet};

#[derive(Clone)]
struct Opts {
    file: Option<String>,
    dir: Option<String>,
    files_from: Option<String>, // 경로 목록 파일("-" 이면 stdin). 흩어진 파일을 한 프로세스로
    filelist: Option<String>,   // {path, sfile_id} 목록. 결과의 doc_id 를 채운다(설계 §7-5-2-1)
    no_doc_id: bool,            // 결과에 doc_id/key 를 넣지 않는다(기본은 넣는다)
    report_missing_id: Option<String>, // sfile_id 를 못 얻은 문서 목록을 쓸 경로
    rules_path: Option<String>,
    taxonomy: Option<String>,   // doc_taxonomy.yaml(업무분류 어휘)
    doc_rules: Option<String>,  // doc_rule.yaml(업무분류 규칙)
    axis: Option<String>,       // "security" | "doctype" — 이번 실행에서 쓸 축을 하나로 좁힘
    conflict: Option<String>,   // 실행 시 축 전략 덮어쓰기(예: doctype=top_n:3)
    glob: Option<String>,       // --dir 에서 고를 파일 패턴(콤마·중괄호로 여러 개)
    export_taxonomy: bool,      // 분류체계 스냅샷 생성 전용 모드(문서를 읽지 않음)
    export_input: Option<String>,   // 위 모드의 원본 JSON(MpowerV11 내보내기)
    scaffold_doc_rule: bool,    // 위 모드에서 doc_rule.yaml 골격도 함께 생성
    out: Option<String>,
    fmt: String,        // "json" | "jsonl"
    fmt_explicit: bool, // --format 을 직접 줬는가(안 줬으면 --out 확장자로 판단)
    simple: bool,
    summary_only: bool,
    no_summary: bool,
    hash: bool,
    with_pii: bool,
    rule_only: bool,
    vector_only: bool,
    doctype_vector_only: bool,   // 업무분류만 규칙 없이 seed 비교로
    progress: bool,              // 파일마다 '[progress] 처리수/총수 경로' 를 stderr 로(화면 진행바용)
    embed_needed: bool,          // 임베딩을 '아직 못 정한 문서'에만(Python --embed-needed 와 같은 뜻)
    no_timing: bool,             // 처리 시간 출력 끄기(요약줄의 총시간)
    sync_doc_rule: bool,         // 분류 체계를 훑어 doc_rule.yaml 을 채운다
    sync_fill_blank: bool,       // 빈 규칙도 시작값으로 채울지(기본 켬)
    sync_enrich: bool,           // 이미 말이 있는 규칙에도 빠진 유의어를 더할지
    with_vector: bool,      // 모든 문서를 임베딩해 결과 레코드에 vector 필드로 실어 보냄
    propagate: Option<String>,  // 1차 결과(jsonl/json)를 읽어 전파만 다시 도는 2차 패스
    auto_propagate: bool,
    seeds: Option<String>,
    failsafe: Option<String>,
    check_rules: bool,      // 규칙셋만 검사하고 종료(문서는 읽지 않음)
    json_errors: bool,      // 실패할 때 stdout 에 오류 JSON 한 줄(계약: plan/CLI-오류출력-설계.html)
    simple_why: bool,       // 축약본에 판정 근거 요약(why)을 더한다(--simple 포함)
}

//------------------------------------------------------------------
// 버전 문자열 만들기 (앱 버전 + PII 포팅 기준 ko-pii 버전)
//=> Python 판(csoclassify.exe)은 ko-pii 를 exe 안에 넣어 다니므로 '번들된 버전'을
//   찍는다. 이 판은 ko-pii 를 쓰지 않고 알고리즘을 옮겨 적었으므로 찍을 번들이
//   없다 — 대신 '어느 ko-pii 를 보고 옮겼는지'를 찍는다. 두 판의 이 값이 같아야
//   같은 기준으로 도는 것이고, 다르면 검출 결과가 갈릴 수 있다는 신호다.
//
// -in: 없음
//
// -out: text = "csoclassify-rs <앱버전> (ko-pii 포팅 기준 <버전>)" 한 줄
// -out: error = 예외 없음(전부 컴파일 시점에 정해진 상수)
//------------------------------------------------------------------
fn version_text() -> String {
    format!("csoclassify-rs {} (ko-pii 포팅 기준 {})",
            env!("CARGO_PKG_VERSION"), pii::KOPII_PORTED_FROM)
}

fn usage() {
    note!("csoclassify-rs — 추출 + 규칙 분류(rule-only) [Rust PoC]");
    note!("사용법:");
    note!("  csoclassify-rs --file <파일> [옵션]");
    note!("  csoclassify-rs --dir  <폴더> [옵션]   (재귀)");
    note!("옵션:");
    note!("  --rules <cso_rules.yaml>  규칙셋(미지정 시 exe 옆/CSOCLASSIFY_POLICY_DIR)");
    note!("  --taxonomy <doc_taxonomy.yaml>  업무분류 체계 스냅샷(없으면 업무분류 축을 끔)");
    note!("  --doc-rules <doc_rule.yaml>     업무분류 규칙셋(없으면 업무분류 축을 끔)");
    note!("  --axis security|doctype   이번 실행에 쓸 축만 지정(doctype 이면 보안등급 계산 생략)");
    note!("  --conflict <축>=<전략>    업무분류 축 전략 덮어쓰기(예: doctype=top_n:3, doctype=all)");
    note!("  --export-taxonomy         DOC_CLASSIFICATION JSON → --taxonomy 경로에 스냅샷 생성 후 종료(문서 안 읽음)");
    note!("  --export-input <파일>     --export-taxonomy 의 원본 JSON(미지정 시 exe 옆 doc_classification_export.json)");
    note!("  --scaffold-doc-rule       --export-taxonomy 와 함께 쓰면 --doc-rules 경로에 규칙 골격도 생성(이미 있으면 건너뜀)");
    // 중괄호는 포맷 자리표시자로 읽히므로 {{ }} 로 escape 한다.
    note!("  --files-from <목록>       처리할 파일 경로를 한 줄에 하나씩 적은 파일(\"-\" 이면 표준입력).");
    note!("                            여러 폴더에 흩어진 파일을 한 프로세스로 처리 — --file/--dir 과 배타");
    note!("  --glob <패턴>             --dir 에서 고를 파일 패턴(예: \"*.hwp,*.pdf\" · \"*.{{hwp,pdf}}\")");
    note!("  --format json|jsonl       출력 형식(미지정 시 --out 확장자로 판단, 그것도 없으면 json 배열)");
    note!("  --out <파일>              결과 저장(미지정 시 stdout)");
    note!("  --simple                  파일별 결과를 문서명·등급·해시(+업무분류 dc_id)로 줄여서 출력");
    note!("  --hash                    각 문서 SHA-256 포함");
    note!("  --with-pii                [프라이버시 예외] 검출된 원문 PII 값 포함(pii 필드)");
    note!("  --rule-only               규칙만으로 분류 — 임베딩·전파 없음(가장 빠름). --vector-only 와 배타");
    note!("  --vector-only             규칙 없이 임베딩 벡터를 seed 와 비교해서만 분류(--seeds 필수). --rule-only 와 배타");
    note!("  --doctype-vector-only     업무분류를 규칙 없이 기준 문서(class_seed.jsonl) 비교로만 분류(security 축은 그대로)");
    eprintln!("  --progress                파일마다 진행 상황을 stderr 로 출력(형식: '[progress] 처리수/총수 경로'). 화면 진행바용");
    note!("  --embed-needed            임베딩을 '아직 못 정한 문서'에만 수행(확정 문서는 건너뜀, 기본 동작과 같음)");
    note!("  --no-timing               요약줄에서 총시간 표기를 뺀다");
    note!("  --sync-doc-rule           분류 체계를 훑어 --doc-rules 파일에 규칙을 채운다(유의어 사전 적용, 이미 있는 파일에도 덧붙임)");
    note!("    --no-fill-blank         └ 단어가 하나도 없는 기존 규칙은 채우지 않는다(기본은 채움)");
    note!("    --sync-enrich           └ 이미 말이 있는 규칙에도 빠진 유의어만 더한다(기본 끔)");
    note!("  --with-vector             모든 문서를 임베딩해 결과에 vector 필드(384차원)로 포함(RAG 등 전량 벡터가 필요할 때)");
    note!("  --propagate <결과파일>    1차 결과(jsonl/json)를 읽어 전파만 다시 수행(문서·모델 불필요). --file/--dir 대신 씀");
    note!("  --auto-propagate          보류 문서를 seed 로 전파(seed 있으면 기본 on)");
    note!("  --seeds <class_seed.jsonl>  전파 비교 기준 seed 저장소(미지정 시 exe 옆 class_seed.jsonl)");
    note!("  --summary                 요약만 출력");
    note!("  --nosummary               요약 제거(파일별만)");
    note!("  --failsafe [등급]         무신호 기본등급. O/S/C 만 허용(값 생략 시 S)");
    note!("  --check-rules             규칙셋의 등급 값만 검사하고 종료(정상 0, 검증 실패 4)");
    note!("  --json-errors             실패할 때 stdout 에 오류 JSON 한 줄({{\"error\":{{code,kind,message,path}}}})");
    note!("  --simple-why              --simple 에 판정 근거 요약(why)을 더한다");
    note!("  -V, --version             버전 출력(앱 버전 + PII 포팅 기준 ko-pii 버전)");
}

fn parse_args() -> Result<Opts, String> {
    let mut o = Opts {
        file: None, dir: None, files_from: None, rules_path: None,
        taxonomy: None, doc_rules: None, axis: None, conflict: None,
        export_taxonomy: false, export_input: None, scaffold_doc_rule: false,
        glob: None, out: None, json_errors: false, simple_why: false,
        fmt: "json".into(), fmt_explicit: false, simple: false, summary_only: false,
        no_summary: false, hash: false, with_pii: false,
        rule_only: false, vector_only: false, doctype_vector_only: false,
        progress: false, embed_needed: false, no_timing: false,
        sync_doc_rule: false, sync_fill_blank: true, sync_enrich: false,
        with_vector: false, propagate: None,
        auto_propagate: false, seeds: None,
        failsafe: None, check_rules: false,
        filelist: None, no_doc_id: false, report_missing_id: None,
    };
    // args_os() 를 쓴다. std::env::args() 는 인자에 UTF-8 이 아닌 바이트가 섞이면
    // **패닉한다**(리눅스에서 CP949 로 깨진 경로를 받으면 실제로 그렇게 죽었다).
    // 인자로 실제 쓰는 값은 ASCII 플래그와 경로뿐이므로, 깨진 바이트는 U+FFFD 로
    // 바꿔 받고 "그런 경로 없음"이라는 평범한 오류로 흘려보낸다 — 죽는 것보다 낫다.
    let args: Vec<String> = std::env::args_os()
        .skip(1)
        .map(|a| a.to_string_lossy().into_owned())
        .collect();
    let mut i = 0;
    while i < args.len() {
        let a = &args[i];
        // 옵션의 값을 집어 온다. optional=true 면 '값이 없어도 되는' 옵션이다
        // (--failsafe 처럼 단독으로도 쓰는 것). 다음 토큰이 '-' 로 시작하면 그건
        // 값이 아니라 다음 옵션이므로 집어 오지 않는다 — 그렇게 하지 않으면
        // `--failsafe --json-errors` 가 등급 "--json-errors" 로 읽힌다.
        let mut take = |optional: bool| -> Option<String> {
            match args.get(i + 1) {
                Some(v) if !v.starts_with('-') => {
                    i += 1;
                    Some(v.clone())
                }
                // 값이 빠진 옵션을 빈 문자열로 흘려보내면 "그런 경로 없음"처럼
                // 엉뚱한 오류로 나타나 원인을 못 찾는다. 여기서 끝낸다.
                _ if !optional => errcodes::fail("bad_args",
                    &format!("[csoclassify-rs] {} 뒤에 값이 없습니다.", args[i]), None),
                _ => None,
            }
        };
        match a.as_str() {
            "--file" | "-file" => o.file = Some(take(false).unwrap()),
            "--dir" | "-dir" => o.dir = Some(take(false).unwrap()),
            "--files-from" => o.files_from = Some(take(false).unwrap()),
            "--rules" => o.rules_path = Some(take(false).unwrap()),
            "--taxonomy" => o.taxonomy = Some(take(false).unwrap()),
            "--doc-rules" => o.doc_rules = Some(take(false).unwrap()),
            "--axis" => o.axis = Some(take(false).unwrap()),
            "--conflict" => o.conflict = Some(take(false).unwrap()),
            "--glob" => o.glob = Some(take(false).unwrap()),
            "--export-taxonomy" => o.export_taxonomy = true,
            "--export-input" => o.export_input = Some(take(false).unwrap()),
            "--scaffold-doc-rule" => o.scaffold_doc_rule = true,
            "--out" => o.out = Some(take(false).unwrap()),
            "--format" => { o.fmt = take(false).unwrap(); o.fmt_explicit = true; }
            "--simple" => o.simple = true,
            "--summary" => o.summary_only = true,
            "--nosummary" => o.no_summary = true,
            "--hash" => o.hash = true,
            "--with-pii" => o.with_pii = true,
            "--rule-only" => o.rule_only = true,
            "--vector-only" => o.vector_only = true,
            "--doctype-vector-only" => o.doctype_vector_only = true,
            "--progress" => o.progress = true,
            "--embed-needed" => o.embed_needed = true,
            "--no-timing" => o.no_timing = true,
            "--sync-doc-rule" => o.sync_doc_rule = true,
            "--no-fill-blank" => o.sync_fill_blank = false,
            "--sync-enrich" => o.sync_enrich = true,
            "--with-vector" => o.with_vector = true,
            "--propagate" => o.propagate = Some(take(false).unwrap()),
            "--auto-propagate" => o.auto_propagate = true,
            "--seeds" => o.seeds = Some(take(false).unwrap()),
            "--filelist" => o.filelist = Some(take(false).unwrap()),
            "--no-doc-id" => o.no_doc_id = true,
            "--report-missing-id" => o.report_missing_id = Some(take(false).unwrap()),
            // 값이 있으면 그 값, 없으면 S(파이썬 판 nargs="?" const="S" 와 같다).
            // 검증은 인자를 다 읽은 뒤에 한다 — 여기서 하면 --failsafe 가
            // 여러 번 나올 때 마지막 값만 검사하는 것이 어색해진다.
            "--failsafe" => o.failsafe = Some(take(true).unwrap_or_else(|| "S".into())),
            "--check-rules" => o.check_rules = true,
            "--json-errors" => o.json_errors = true,
            // 축약본만 받으면 "이 문서가 왜 C 인가"에 답할 수 없다.
            "--simple-why" => { o.simple_why = true; o.simple = true; }
            "--hybridparse" => {} // 추출은 항상 내용감지 라우팅
            "-h" | "--help" => { usage(); std::process::exit(0); }
            // 이 판은 ko-pii 를 번들하지 않고 '옮겨 적은' 사본이라, 번들 버전 대신
            // '어느 ko-pii 를 보고 옮겼는지'를 찍는다. Python 판의 (ko-pii x.y.z)
            // 와 이 값이 같아야 두 판이 같은 기준으로 도는 것이다.
            "-V" | "--version" => { println!("{}", version_text()); std::process::exit(0); }

            // 파이썬 판에만 있는 옵션들 — 이 판에서는 할 일이 없다. 그래도 '모르는
            // 인자'로 막으면, 두 판을 같은 명령으로 부르던 호출부가 깨진다.
            // 값이 없는 것들:
            "--classify" | "--daemon" | "--no-daemon" | "--serve" | "--status" | "--stop"
            | "--embed" | "--text-only" | "--per-chunk" | "--normalize" | "--no-normalize"
            | "--timing" | "--recursive" | "-r" | "--verbose" | "-v"
            // ※ "--nosummary" 는 위에서 실제로 처리하므로 여기 두면 안 된다
            //   (도달할 수 없는 갈래가 되어 unreachable_patterns 경고가 난다).
            | "--synap-only" | "--with-text" => {}
            // 값을 하나 데리고 오는 것들 — 그 값까지 함께 삼켜야 뒤가 밀리지 않는다.
            "--model" | "--max-tokens" | "--overlap" | "--precision" | "--num-threads"
            | "--idle-timeout" | "--log" | "--make-doctype-seeds" | "--seed-per-dir"
            | "--seed-per-node" => { take(false); }
            // 값이 있어도 되고 없어도 되는 것.
            "--save-text" => { take(true); }

            // 여기까지 안 걸렸으면 정말 모르는 인자다. 조용히 넘어가면 오타 하나가
            // 옵션을 통째로 무효로 만든다(예: --json-erros). 파이썬 판도 여기서
            // 막으므로, 그 자리에서 끝내는 것이 두 판이 같아지는 길이다.
            _ => errcodes::fail("bad_args",
                &format!("[csoclassify-rs] 알 수 없는 인자: {}\n\
                          철자를 확인하세요. 쓸 수 있는 인자는 --help 로 볼 수 있습니다.", a),
                None),
        }
        i += 1;
    }
    // --axis 는 오타가 조용히 "축 전체 무시"로 이어지면 안 되므로 여기서 막는다.
    if let Some(a) = &o.axis {
        if a != "security" && a != "doctype" {
            // 축 오타는 조용히 '축 전체 무시'로 이어지면 안 된다. 계약상 1004.
            errcodes::fail("bad_axis",
                &format!("[csoclassify-rs] --axis 는 security 또는 doctype 이어야 합니다: {:?}", a),
                None);
        }
    }
    // --failsafe 는 규칙셋을 거치지 않고 곧바로 최종 등급이 된다. 오타가 있으면
    // 정의되지 않은 등급이 그대로 결과에 박히므로(규칙셋 오타와 같은 종류의
    // fail-open) 스캔 시작 전에 막는다. 파이썬 판과 같은 자리·같은 코드(1005).
    if let Some(g) = &o.failsafe {
        if !rules::GRADES.contains(&g.as_str()) {
            errcodes::fail("bad_failsafe",
                &format!("[csoclassify-rs] --failsafe 값이 올바르지 않습니다: {:?}\n\
                          정의된 등급: {}", g, rules::GRADES.join(" < ")),
                None);
        }
    }
    // --format 을 안 줬으면 --out 확장자로 형식을 정한다.
    if !o.fmt_explicit {
        if let Some(g) = guess_format(&o.out) {
            if g != o.fmt {
                note!("[csoclassify-rs] --out 확장자에 맞춰 --format {} 로 저장합니다. \
                           (다르게 하려면 --format 을 직접 지정하세요)", g);
            }
            o.fmt = g;
        }
    }
    Ok(o)
}

/// 전체 레코드를 '읽는 차례'로 다시 담는다(파이썬 판 `_order_record()` 와 같은 표).
///
/// 만들어진 순서 그대로 내보내면 눈에 안 들어온다 — 가장 궁금한 업무분류가
/// `labels` 안에 묻혀 열 번째에 있고, 판정 근거(`signals`)가 결과보다 먼저 나온다.
///   1. 무엇을      `file` · `hash`
///   2. 어떻게 됐나  `grade` · `doctype` · `confidence` · `method` · `decided_by` · `error`
///   3. 왜          `labels`(축별 상세) · `signals`(근거)
///   4. 부속        `seed_eligible` · 버전 3개 · `ts` · `elapsed_ms`
///
/// 앞 네 칸이 `--simple` 과 같아, 축약본이 전체의 '앞부분만 떼어낸 것'이 된다.
/// `doctype` 은 `labels.doctype.values` 에서 뽑은 같은 값이라 새 정보가 아니고,
/// 축이 돌았을 때만 넣는다(빈 배열이면 "분류 못 함"과 "축 안 씀"이 안 갈린다).
/// 표에 없는 칸은 **뒤에 그대로 붙인다** — 새 칸이 생겼을 때 이 함수가 조용히
/// 지워 버리면 가장 찾기 어려운 사고가 된다.
const REC_ORDER: &[&str] = &[
    "file", "doc_id", "doc_id_source", "key", "rematched_by",
    "hash", "grade", "doctype", "confidence", "method", "decided_by",
    "error", "labels", "signals", "pii", "vector", "seed_eligible",
    "rule_version", "taxonomy_version", "doctype_rule_version", "ts", "elapsed_ms",
];

fn order_record(rec: &Value) -> Value {
    let src = match rec.as_object() {
        Some(o) => o,
        None => return rec.clone(),
    };
    let mut out = serde_json::Map::new();
    for key in REC_ORDER {
        if *key == "doctype" {
            // 축이 돌았을 때만 — labels.doctype 이 객체로 있을 때가 그때다.
            if let Some(dt) = rec.get("labels").and_then(|l| l.get("doctype")) {
                if dt.is_object() {
                    let ids: Vec<Value> = dt.get("values").and_then(|v| v.as_array())
                        .map(|a| a.iter().filter_map(|v| v.get("dc_id").cloned()).collect())
                        .unwrap_or_default();
                    out.insert("doctype".into(), Value::Array(ids));
                }
            }
            continue;
        }
        if let Some(v) = src.get(*key) {
            out.insert((*key).to_string(), v.clone());
        }
    }
    // 표에 없는 칸은 잃지 않고 뒤에 붙인다.
    for (k, v) in src {
        if !out.contains_key(k) {
            out.insert(k.clone(), v.clone());
        }
    }
    Value::Object(out)
}

/// 감사용 전체 결과 파일 경로 — `result.jsonl` → `result.full.jsonl`.
///
/// 화면(UI)이 축약본을 `<이름>.simple.<확장자>` 로 부르는 것과 같은 결의 이름이다.
fn full_out_path(out: &str) -> String {
    let p = Path::new(out);
    let ext = p.extension().map(|e| e.to_string_lossy().into_owned())
        .unwrap_or_else(|| "jsonl".into());
    let stem = p.with_extension("");
    format!("{}.full.{}", stem.display(), ext)
}

/// 판정 근거 요약을 만든다(`--simple-why`).
///
/// 축약본 4칸으로는 "왜 이 등급인가"를 댈 수 없다. 그렇다고 전체 레코드를 주면
/// 26배로 커진다. 판정에 실제로 쓰인 것만 추린다 — 전체의 15% 쯤이다.
/// 레코드 자신의 `labels.security` / `labels.doctype` 갈래를 그대로 따라 **축별로
/// 묶는다**. 평평하게 늘어놓으면 보안등급의 `conf` 를 업무분류의 값으로 오해한다
/// (게다가 이 판은 키를 사전순으로 내보내 `dt` 가 `conf` 와 `hits` 사이에 끼었다).
///   * `security.by`   어느 신호가 등급을 정했는가
///   * `security.conf` 그 판정의 확신     * `security.hits` 걸린 규칙 id 와 건수
///   * `doctype`       업무분류 후보의 dc_id·확신과 **판정 경로(`by`)**
///
/// `doctype.by` 는 같은 0.91 이라도 뜻이 다르기 때문에 넣는다 —
/// `rule` 은 "정한 낱말이 제목·머리·파일명 여러 군데서 나왔다", `embed` 는
/// "이미 분류해 둔 기준 문서와 닮았다". 검토자가 다르게 봐야 하는 값이다.
///
/// 매칭된 **원문 값은 넣지 않는다** — 이 도구의 불변식이다(주민번호 12건 검출,
/// 이지 그 번호 자체는 남기지 않는다). 파이썬 판 `_why_record()` 와 같은 칸이다.
fn why_record(rec: &Value) -> Value {
    let mut hits: Vec<Value> = vec![];
    if let Some(sigs) = rec.get("signals").and_then(|v| v.as_object()) {
        for (sig, val) in sigs {
            if val.get("grade").map_or(true, |g| g.is_null()) {
                continue;
            }
            match val.get("hits").and_then(|h| h.as_array()) {
                Some(list) if !list.is_empty() => {
                    for h in list {
                        hits.push(json!({"sig": sig, "id": h.get("id"), "n": h.get("count")}));
                    }
                }
                // path 처럼 '걸린 규칙 목록'이 없는 신호는 출처만 남긴다.
                _ => hits.push(json!({"sig": sig, "src": val.get("source")})),
            }
        }
    }
    let mut sec = serde_json::Map::new();
    sec.insert("by".into(), rec.get("decided_by").cloned().unwrap_or(json!([])));
    sec.insert("conf".into(), rec.get("confidence").cloned().unwrap_or(json!(0.0)));
    sec.insert("hits".into(), Value::Array(hits));
    let mut why = serde_json::Map::new();
    why.insert("security".into(), Value::Object(sec));
    if let Some(vals) = rec.get("labels").and_then(|l| l.get("doctype"))
        .and_then(|d| d.get("values")).and_then(|v| v.as_array()) {
        let dt: Vec<Value> = vals.iter().filter_map(|v| v.get("dc_id").map(|id| json!({
            "dc": id,
            "c": (v.get("confidence").and_then(|c| c.as_f64()).unwrap_or(0.0) * 100.0).round() / 100.0,
            // stage 는 이 후보가 규칙 스캔에서 나왔는지 기준 문서 비교에서
            // 나왔는지 말해 준다 — 같은 숫자라도 뜻이 달라 함께 낸다.
            "by": v.get("stage").and_then(|s| s.as_str()).unwrap_or_else(|| {
                let embed = v.get("from").and_then(|f| f.as_array())
                    .map_or(false, |a| a.iter().any(|x| x.as_str() == Some("embed")));
                if embed { "embed" } else { "rule" }
            }),
        }))).collect();
        if !dt.is_empty() {
            why.insert("doctype".into(), Value::Array(dt));
        }
    }
    Value::Object(why)
}

/// `--out` 파일 이름의 확장자로 출력 형식을 추측한다.
///
/// "`--out result.jsonl` 로 저장했는데 안에는 JSON 배열이라 뷰어가 못 연다"는 함정을
/// 없애려는 것이다 — 파일 이름이 사실상 형식을 말하고 있으므로 그대로 따른다.
/// 모르는 확장자면 None 을 돌려 기본값(json)을 그대로 쓰게 둔다.
/// 파이썬 판 `resolve_format()` 과 같은 표를 쓴다.
fn guess_format(out: &Option<String>) -> Option<String> {
    let p = out.as_ref()?;
    let ext = Path::new(p).extension()?.to_string_lossy().to_lowercase();
    match ext.as_str() {
        "jsonl" | "ndjson" => Some("jsonl".to_string()),
        "json" => Some("json".to_string()),
        _ => None,   // txt 등은 Rust 판에 text 출력이 없으므로 건드리지 않는다
    }
}

/// 정책 파일 경로 결정: 명시 인자 → CSOCLASSIFY_POLICY_DIR → exe 옆.
/// 명시 인자는 파일이 없어도 그대로 돌려준다 — "지정했는데 없다"는 조용히
/// 다른 파일로 대체되면 안 되고, 로더가 그 경로로 실패해 알려 줘야 하기 때문이다.
fn resolve_policy_file(explicit: &Option<String>, filename: &str) -> Option<PathBuf> {
    if let Some(p) = explicit {
        return Some(PathBuf::from(p));
    }
    // 환경변수가 exe 옆보다 먼저다. 환경변수는 관리자가 "이 정책을 써라"고 **직접
    // 지시한 것**이고, exe 옆 파일은 그냥 거기 있을 뿐이다. 지시가 우선해야 한다.
    // [2026-09-01 순서 교정] 예전에는 exe 옆이 먼저였다. 파이썬 판은 환경변수가
    // 먼저였으므로, 두 조건이 함께 성립하면 **두 판이 서로 다른 규칙셋을 읽었다**.
    // 실측에서 15건 중 11건의 등급이 갈렸다 — 그것도 조용히. 정책이 바뀐 줄
    // 모르는 채로 등급이 달라지는 것은 거버넌스 도구에서 가장 나쁜 실패다.
    if let Ok(d) = std::env::var("CSOCLASSIFY_POLICY_DIR") {
        let p = Path::new(&d).join(filename);
        if p.is_file() { return Some(p); }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let p = dir.join(filename);
            if p.is_file() { return Some(p); }
        }
    }
    None
}

/// 규칙셋 경로 결정: --rules → CSOCLASSIFY_POLICY_DIR → exe 옆 (파이썬 판과 같은 차례).
fn resolve_rules(opts: &Opts) -> Option<PathBuf> {
    resolve_policy_file(&opts.rules_path, "cso_rules.yaml")
}

/// 그레고리력 날짜 → 1970-01-01 기준 일수(Howard Hinnant days_from_civil).
/// 스냅샷 노후 판정(T13)에 90일 비교만 하면 되므로 날짜 라이브러리를 새로
/// 들이지 않는다 — 시간대 한 칸 차이는 이 판정에 영향이 없다.
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

/// 분류체계 스냅샷 노후 경고(T13). exported_at 은 "YYYYMMDDHHMMSS" 형식이며,
/// 형식이 다르면 판단하지 않는다(오탐보다 조용히 넘기는 편이 안전).
fn check_stale_taxonomy(taxonomy: &Taxonomy, max_age_days: i64) -> Option<String> {
    let s = &taxonomy.exported_at;
    if s.len() < 8 || !s.is_char_boundary(8) {
        return None;
    }
    let y: i64 = s[0..4].parse().ok()?;
    let m: i64 = s[4..6].parse().ok()?;
    let d: i64 = s[6..8].parse().ok()?;
    if !(1..=12).contains(&m) || !(1..=31).contains(&d) {
        return None;
    }
    let now_days = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH).ok()?.as_secs() as i64 / 86_400;
    let age_days = now_days - days_from_civil(y, m, d);
    if age_days <= max_age_days {
        return None;
    }
    Some(format!(
        "[csoclassify-rs] 분류체계 스냅샷(doc_taxonomy.yaml)이 {}일 전 것입니다(exported_at={}) — \
         DB 와 어긋났을 수 있습니다. export_taxonomy 로 다시 내보내는 것을 권장합니다.",
        age_days, s))
}

/// 업무분류(doctype) 축 로드 — 있으면 켜고 없으면 끄는 외장 자산(설계서 4-6·T13·T14·T16).
///  1) --axis security 면 시도조차 하지 않는다(이번 실행이 안 쓰는 축이라 검증할 이유가 없다)
///  2) 파일이 없으면: --axis doctype 명시 시 종료(4), 아니면 경고 후 축을 끈다
///  3) 파일은 있는데 내용이 깨졌으면 항상 치명적(4) — "안 쓴다"가 아니라 "고쳐야 한다"는 뜻
///  4) 스냅샷이 오래됐으면 경고만(T13), T6·T12 경고도 그대로 보여 준다
///
/// -out: Ok(None) = 축을 끄고 security 만 / Ok(Some(..)) = 축 켜짐 / Err(code) = 즉시 종료
fn load_doctype_axis(opts: &Opts) -> Result<Option<(Taxonomy, DocRuleSet)>, i32> {
    if opts.axis.as_deref() == Some("security") {
        return Ok(None);
    }
    let explicit = opts.axis.as_deref() == Some("doctype");

    // ── 분류체계 스냅샷(어휘)
    let tpath = match resolve_policy_file(&opts.taxonomy, axes::default_taxonomy_filename()) {
        Some(p) if p.is_file() => p,
        other => {
            let shown = other.map(|p| p.display().to_string())
                .unwrap_or_else(|| axes::default_taxonomy_filename().into());
            if explicit {
                // '파일 없음'은 내용 오류(4)가 아니라 부른 쪽이 고칠 문제(3)다.
                errcodes::fail("taxonomy_missing",
                    &format!("[csoclassify-rs] 분류체계 스냅샷을 찾을 수 없습니다: {}", shown),
                    Some(&shown));
            }
            note!("[csoclassify-rs] doc_taxonomy.yaml 이 없어 업무분류(doctype) 축을 건너뜁니다.");
            note!("                 보안등급(security)만 판정합니다.");
            return Ok(None);
        }
    };
    let taxonomy = match axes::load_taxonomy(&tpath) {
        Ok(t) => t,
        Err(e) => errcodes::fail("taxonomy_invalid",
            &format!("[csoclassify-rs] {}", e), tpath.to_str()),
    };
    if let Some(msg) = check_stale_taxonomy(&taxonomy, 90) {
        note!("{}", msg);
    }

    // ── 업무분류 규칙셋(규칙). taxonomy 를 넘겨 T5·T6·T12 교차검증까지 함께.
    // 규칙 파일이 없다고 축을 끄지는 않는다 — 규칙이 없을 뿐 분류할 방법이 하나 더
    // 남아 있다(class_seed.jsonl 과의 임베딩 전파). 빈 규칙셋으로 축을 켜 두면 1차
    // 스캔은 빈손이지만 전파가 라벨을 채울 수 있고, seed 마저 없으면 미분류로 남아
    // 사람이 나중에 분류한다. --axis doctype 이어도 마찬가지로 멈추지 않는다(축을
    // 못 쓰는 게 아니라 규칙만 없는 것이므로).
    let rpath = match resolve_policy_file(&opts.doc_rules, doc_rules::default_doc_rule_filename()) {
        Some(p) if p.is_file() => p,
        other => {
            // 경로가 잡혔으면(=명시했는데 파일이 없음) 그 경로를, 아무 데도 없으면
            // 어디를 뒤졌는지를 알려 준다 — 없는 경로를 지어내 보여주지 않는다.
            let where_ = match other {
                Some(p) => format!("찾은 경로: {} (파일 없음)", p.display()),
                None => format!("찾아본 곳: exe 옆 · CSOCLASSIFY_POLICY_DIR ({})",
                                doc_rules::default_doc_rule_filename()),
            };
            note!("[csoclassify-rs] doc_rule.yaml 이 없어 업무분류(doctype)를 'seed 전파 전용'으로 돌립니다.");
            note!("                 규칙 대신 class_seed.jsonl 과의 임베딩 유사도로만 분류합니다(seed 도 없으면 전부 미분류).");
            note!("                 {}", where_);
            return Ok(Some((taxonomy, DocRuleSet::seed_only())));
        }
    };
    let drs = match doc_rules::load_doc_rules(&rpath, Some(&taxonomy)) {
        Ok(d) => d,
        Err(e) => errcodes::fail("doc_rules_invalid",
            &format!("[csoclassify-rs] {}", e), rpath.to_str()),
    };
    for w in &drs.warnings {
        note!("[csoclassify-rs] {}", w);
    }
    Ok(Some((taxonomy, drs)))
}

/// 분류체계 스냅샷 내보내기 전용 모드(--export-taxonomy).
///
/// MpowerV11 이 내보낸 DOC_CLASSIFICATION JSON 을 이 도구가 읽는 doc_taxonomy.yaml
/// 로 바꾼다. 문서는 한 건도 읽지 않으므로 --file/--dir 이 필요 없다.
///  1) 원본 JSON → doc_taxonomy.yaml 변환 + round-trip 검증(방금 쓴 파일이 실제로
///     읽히는지 여기서 확인 — 안 그러면 다음 실행 때에야 문제를 만난다)
///  2) --scaffold-doc-rule 이면 doc_rule.yaml 골격도 --doc-rules 경로에 생성
///
/// -out: 종료코드(0 성공 / 3 원본을 못 찾거나 못 읽음 / 4 변환 결과가 검증 실패)
/// 업무분류 규칙 채우기(--sync-doc-rule) — 화면 [분류 불러오기] 와 같은 일.
/// 분류 체계를 훑어 doc_rule.yaml 에 규칙을 채운다. 어휘를 만드는 층(docvocab)은
/// Python 판과 골든 테스트로 묶여 있어, 화면과 같은 결과가 나온다.
fn run_sync_doc_rule(opts: &Opts) -> i32 {
    // 경로 규약은 다른 모드와 같다 — --taxonomy 우선, 없으면 정책 폴더/exe 옆.
    let tax_path = match resolve_policy_file(&opts.taxonomy, axes::default_taxonomy_filename()) {
        Some(p) => p,
        None => {
            errcodes::fail("taxonomy_missing",
                "[csoclassify-rs] 회사 분류 체계를 찾을 수 없습니다 — --taxonomy <파일경로> 로 지정하세요.",
                None);
        }
    };
    if !tax_path.is_file() {
        errcodes::fail("taxonomy_missing",
            &format!("[csoclassify-rs] 회사 분류 체계를 찾을 수 없습니다: {}\n\
                      --taxonomy 로 지정하거나 --export-taxonomy 로 먼저 만드세요.",
                     tax_path.display()),
            tax_path.to_str());
    }
    let taxonomy = match axes::load_taxonomy(&tax_path) {
        Ok(t) => t,
        // 파일은 있는데 못 읽는다 = 내용 문제다(없음과 구분해 4 로 나간다).
        Err(e) => errcodes::fail("taxonomy_invalid",
            &format!("[csoclassify-rs] 분류 체계를 읽지 못했습니다: {}", e), tax_path.to_str()),
    };
    let out_path = match resolve_policy_file(&opts.doc_rules,
                                             doc_rules::default_doc_rule_filename()) {
        Some(p) => p,
        None => {
            errcodes::fail("doc_rules_write_failed",
                "[csoclassify-rs] 규칙 파일 경로를 정할 수 없습니다 — --doc-rules <파일경로> 로 지정하세요.",
                None);
        }
    };

    match docvocab::sync_doc_rule(&taxonomy, &out_path, opts.sync_fill_blank, opts.sync_enrich) {
        Err(e) => errcodes::fail("doc_rules_write_failed",
            &format!("[csoclassify-rs] {}", e), out_path.to_str()),
        Ok((0, 0, 0, _)) => {
            note!("[csoclassify-rs] 바뀐 것이 없습니다 — 규칙 파일은 그대로 둡니다: {}",
                      out_path.display());
            0
        }
        Ok((added, filled, enriched, layers)) => {
            let names: Vec<String> = layers.iter()
                .map(|p| std::path::Path::new(p).file_name()
                         .map(|s| s.to_string_lossy().into_owned()).unwrap_or_default())
                .collect();
            note!("[csoclassify-rs] {} 갱신 — 새 분류 {}개 · 빈 규칙 채움 {}개 · 유의어 더함 {}개{}",
                      out_path.display(), added, filled, enriched,
                      if names.is_empty() { " (유의어 사전 없음)".to_string() }
                      else { format!(" (유의어 사전: {})", names.join(" → ")) });
            0
        }
    }
}

fn run_export_taxonomy(opts: &Opts) -> i32 {
    // 두 경로 모두 다른 정책 파일과 같은 규약으로 찾는다(인자 → 환경변수 → exe 옆).
    // 단 출력 경로는 '아직 없는 파일'을 만드는 자리라 is_file() 로 거르면 안 된다.
    let input = match resolve_policy_file(&opts.export_input, axes::default_export_input_filename()) {
        Some(p) if p.is_file() => p,
        other => {
            let shown = other.map(|p| p.display().to_string())
                .unwrap_or_else(|| axes::default_export_input_filename().into());
            if !errcodes::quiet() {
                note!("  · --export-input <파일경로> 로 지정하거나,");
                note!("  · exe 옆(또는 CSOCLASSIFY_POLICY_DIR)에 {} 를 두세요.",
                          axes::default_export_input_filename());
            }
            errcodes::fail("export_input_missing",
                &format!("[csoclassify-rs] 원본 JSON을 찾을 수 없습니다: {}", shown),
                Some(&shown));
        }
    };
    let output = match &opts.taxonomy {
        Some(p) => PathBuf::from(p),
        // 미지정이면 exe 옆에 만든다 — 분류할 때 찾는 자리와 같아야 바로 쓰인다.
        None => policy_dir_for_new_file().join(axes::default_taxonomy_filename()),
    };

    let (taxonomy, warnings) = match axes::export_from_mpower_json(&input, &output) {
        Ok(v) => v,
        // 여기까지 왔다는 것은 원본 파일이 있다는 뜻이다(없으면 위에서 끝난다).
        // 그러니 남은 실패는 전부 '내용이 틀림'(2008) 이다 — 파일 없음(2007)과
        // 갈라 두면 부르는 쪽이 "경로를 다시 묻는다 / 원본 데이터를 고친다"를
        // 구분할 수 있다.
        Err(msg) => errcodes::fail("export_input_invalid",
            &format!("[csoclassify-rs] {}", msg), input.to_str()),
    };

    for w in &warnings {
        note!("[csoclassify-rs] 경고: {}", w);
    }
    println!("[csoclassify-rs] {} 생성 완료 — 노드 {}개, 최상위 {}개, exported_at={}",
             output.display(), taxonomy.len(), taxonomy.roots().len(), taxonomy.exported_at);
    // 어떤 분류가 들어왔는지 눈으로 확인할 수 있게 앞쪽 몇 개만 예로 보여 준다.
    for root in taxonomy.roots().iter().take(3) {
        if let Some(leaf) = taxonomy.children_of(Some(&root.dc_id)).first() {
            if let Ok(p) = taxonomy.path(&leaf.dc_id) {
                println!("  예: {}", p);
            }
        }
    }

    if opts.scaffold_doc_rule {
        let rpath = match &opts.doc_rules {
            Some(p) => PathBuf::from(p),
            None => policy_dir_for_new_file().join(doc_rules::default_doc_rule_filename()),
        };
        match doc_rules::write_scaffold(&taxonomy, &rpath, false) {
            Ok(Some(n)) => println!("[csoclassify-rs] {} 골격 생성 완료 — 규칙 {}건\
(terms 는 비어 있음, 채워야 동작).", rpath.display(), n),
            Ok(None) => note!("[csoclassify-rs] {} 이 이미 있어 골격 생성을 건너뜁니다\
(사람이 채운 내용을 덮어쓰지 않기 위함).", rpath.display()),
            Err(msg) => {
                errcodes::fail("doc_rules_write_failed",
                    &format!("[csoclassify-rs] 골격 생성 실패: {}", msg), rpath.to_str());
            }
        }
    }
    0
}

/// '새로 만들 정책 파일'을 둘 폴더.
///
/// resolve_policy_file 은 이미 있는 파일을 찾는 함수라, 아직 없는 파일을 만들 때는
/// 쓸 수 없다(찾지 못하고 None 을 준다). 만들 때의 우선순위는 환경변수 → exe 옆이다.
fn policy_dir_for_new_file() -> PathBuf {
    if let Ok(d) = std::env::var("CSOCLASSIFY_POLICY_DIR") {
        if !d.is_empty() {
            return PathBuf::from(d);
        }
    }
    std::env::current_exe().ok()
        .and_then(|e| e.parent().map(|p| p.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."))
}

/// --conflict "doctype=top_n:3" → (축 id, ConflictSpec). 값 검증은 doc_rule.yaml
/// 로더의 검증기를 그대로 재사용해, 파일에 적을 때와 실행 시 덮어쓸 때가 항상
/// 같은 기준으로 걸러지게 한다(설계서 6-5).
fn parse_conflict_override(spec: &str) -> Result<(String, ConflictSpec), String> {
    let (axis, value) = match spec.split_once('=') {
        Some((a, v)) => (a.trim().to_string(), v.trim().to_string()),
        None => return Err(format!("--conflict 형식이 올바르지 않습니다: {:?} (예: doctype=top_n:3)", spec)),
    };
    let raw: Value = match value.split_once(':') {
        Some((strategy, n_str)) => {
            let n: i64 = n_str.parse()
                .map_err(|_| format!("--conflict 의 n 값이 정수가 아닙니다: {:?}", n_str))?;
            json!({"strategy": strategy, "n": n})
        }
        None => json!(value),
    };
    if let Some(v) = doc_rules::check_conflict(&raw) {
        return Err(format!("--conflict {:?} — {}", spec, v.detail));
    }
    Ok((axis, doc_rules::parse_conflict(&raw)))
}

/// 레코드의 최상위 등급 필드 + signals 로부터 labels.security 를 조립(설계서 7-1).
/// security 축은 항상 conflict:max 고정이라 strategy 는 상수다(3-1). 후보는 이미
/// 만들어진 signals 에서 등급이 있는 것만 추린다 — 융합을 다시 돌리지 않는다.
fn security_label(rec: &Value) -> Value {
    let empty = json!({});
    let signals = rec.get("signals").unwrap_or(&empty);
    let mut candidates = vec![];
    // Python 판과 같은 순서로 나열해, 두 구현의 결과 파일을 나란히 비교할 수 있게 한다.
    for name in ["rule", "sensitive", "stamp", "path", "name", "embed"] {
        let s = match signals.get(name) { Some(s) => s, None => continue };
        let grade = match s.get("grade") { Some(g) if !g.is_null() => g.clone(), _ => continue };
        candidates.push(json!({
            "value": grade,
            "confidence": s.get("confidence").cloned().unwrap_or(json!(0.0)),
            "from": name,
        }));
    }
    json!({
        "value": rec.get("grade").cloned().unwrap_or(Value::Null),
        "confidence": rec.get("confidence").cloned().unwrap_or(json!(0.0)),
        "strategy": "max",
        "method": rec.get("method").cloned().unwrap_or(json!("unclassified")),
        "decided_by": rec.get("decided_by").cloned().unwrap_or(json!([])),
        "candidates": candidates,
    })
}

/// labels.doctype 의 후보들에서 (뿌리 카테고리, 실제 걸린 노드) 이름 쌍을 뽑는다.
/// 이미 레코드에 박힌 path 문자열("A > B > C")만 쓰므로 taxonomy 없이도 집계된다.
fn doctype_breakdown(sig: &doctype::DoctypeSignal) -> Vec<(String, String)> {
    sig.values.iter().map(|v| {
        let segments: Vec<&str> = v.path.split(" > ").filter(|s| !s.is_empty()).collect();
        match segments.as_slice() {
            [] => (v.dc_id.clone(), v.dc_id.clone()),
            segs => (segs[0].to_string(), segs[segs.len() - 1].to_string()),
        }
    }).collect()
}

/// `--files-from` 목록을 읽어 대상 경로로 바꾼다("-" 이면 표준입력).
///
/// 여러 폴더에 흩어진 문서를 **한 프로세스**로 처리하려고 만든 입력 방식이다.
/// `--dir` 은 폴더 하나 아래만 훑을 수 있어서, 경로가 흩어져 있으면 파일마다
/// 프로세스를 새로 띄우게 되고 그때마다 ONNX 모델을 다시 읽어 느려진다.
/// 목록을 통째로 받으면 모델을 한 번만 읽고 전부 처리한다.
/// 규칙은 Python 판 `read_files_from()` 과 같다 — 빈 줄·`#` 주석 무시, 감싼
/// 따옴표 제거, 실제 파일만, 중복은 첫 것만(입력 순서 보존).
fn read_files_from(src: &str) -> Vec<PathBuf> {
    let raw = if src == "-" {
        let mut buf = String::new();
        use std::io::Read;
        // 표준입력을 못 읽으면 대상 0건이 된다 — 아래 '처리할 파일이 없습니다'가 받는다.
        let _ = std::io::stdin().read_to_string(&mut buf);
        buf
    } else {
        std::fs::read_to_string(src).unwrap_or_default()
    };
    // 메모장이 붙이는 BOM 을 떼어 낸다(안 떼면 첫 경로가 통째로 어긋난다).
    let raw = raw.trim_start_matches('\u{feff}');

    let mut files: Vec<PathBuf> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut skipped = 0usize;
    for line in raw.lines() {
        // 공백이 든 경로를 따옴표로 감싼 목록(dir /b 결과 등)도 받아 준다.
        let path = line.trim().trim_matches('"');
        if path.is_empty() || path.starts_with('#') { continue; }
        let p = PathBuf::from(path);
        // 같은 문서를 두 번 임베딩하지 않도록 여기서 미리 중복을 접는다.
        // 존재하는 경로는 canonicalize 로, 없는 경로는 문자열로 비교한다.
        let key = std::fs::canonicalize(&p)
            .map(|q| q.to_string_lossy().to_lowercase())
            .unwrap_or_else(|_| path.to_lowercase());
        if !seen.insert(key) { continue; }
        if p.is_file() { files.push(p); } else { skipped += 1; }
    }
    // 조용히 버리면 "왜 결과 건수가 모자라지?" 로 이어진다 — 건수만이라도 남긴다.
    if skipped > 0 {
        note!("[csoclassify-rs] --files-from: 파일이 아니어서 건너뜀 {}건", skipped);
    }
    files
}

/// 대상 파일 수집: --files-from 목록, --file 하나, --dir 재귀.
fn collect_files(opts: &Opts, flist: Option<&filelist::FileList>) -> Vec<PathBuf> {
    // 목록 입력이 있으면 그것이 대상이다(--file/--dir 과의 동시 사용은 호출부에서 막는다).
    if let Some(lst) = &opts.files_from {
        return read_files_from(lst);
    }
    if let Some(f) = &opts.file {
        // 실제 파일일 때만 대상으로 삼는다(파이썬 판과 같은 규칙). 없는 경로·폴더를
        // 그대로 넘기면 '추출 실패' 레코드가 만들어져 결과처럼 나간다.
        let p = PathBuf::from(f);
        return if p.is_file() { vec![p] } else { vec![] };
    }
    if let Some(d) = &opts.dir {
        // --glob 패턴(콤마·중괄호로 여러 개)을 펼쳐 파일 '이름'에 맞춰 거른다.
        // 패턴이 없거나 "*" 면 전부 통과시킨다(예전과 같은 동작).
        let pats = expand_glob_patterns(opts.glob.as_deref().unwrap_or("*"));
        let pass_all = pats.is_empty() || pats.iter().any(|p| p == "*");
        return walkdir::WalkDir::new(d).into_iter()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_type().is_file())
            .map(|e| e.into_path())
            .filter(|p| {
                if pass_all { return true; }
                let name = p.file_name().map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_default();
                pats.iter().any(|pat| glob_match(pat, &name))
            })
            .collect();
    }
    // --file/--dir/--files-from 이 하나도 없고 목록만 준 경우 — 목록이 대상을 정한다
    // (설계 §7-5-2-1 "정식 운영"). --dir 과 함께 주면 위에서 이미 돌아갔고, 그때
    // 목록은 'ID 사전' 역할만 한다.
    if let Some(fl) = flist {
        return fl.target_paths().into_iter().map(PathBuf::from).collect();
    }
    vec![]
}

/// --glob 값 하나를 개별 패턴 목록으로 펼친다(Python cli.expand_glob_patterns 포팅).
///
/// 두 표기를 모두 받는다 — 콤마 나열("*.hwp,*.pdf")과 중괄호("*.{hwp,pdf}").
/// 중괄호 안의 콤마는 그룹 구분자이므로, 먼저 '중괄호 밖' 최상위 콤마로만 자른 뒤
/// 각 토큰의 중괄호를 펼친다. 두 표기를 섞어 써도 된다.
fn expand_glob_patterns(spec: &str) -> Vec<String> {
    let mut tokens: Vec<String> = vec![];
    let (mut buf, mut depth) = (String::new(), 0i32);
    for ch in spec.chars() {
        match ch {
            '{' => { depth += 1; buf.push(ch); }
            '}' => { depth = (depth - 1).max(0); buf.push(ch); }
            ',' if depth == 0 => { tokens.push(std::mem::take(&mut buf)); }
            _ => buf.push(ch),
        }
    }
    tokens.push(buf);

    let mut out: Vec<String> = vec![];
    for tok in tokens {
        let tok = tok.trim();
        if tok.is_empty() { continue; }
        for p in expand_braces(tok) {
            if !out.contains(&p) { out.push(p); }
        }
    }
    out
}

/// "a.{x,y}" → ["a.x", "a.y"]. 중괄호가 없으면 그대로 한 개.
fn expand_braces(pat: &str) -> Vec<String> {
    let open = match pat.find('{') { Some(i) => i, None => return vec![pat.to_string()] };
    // 짝이 되는 닫는 괄호를 깊이를 세며 찾는다(중첩 대비).
    let mut depth = 0i32;
    let mut close = None;
    for (i, ch) in pat.char_indices().skip(open) {
        match ch {
            '{' => depth += 1,
            '}' => { depth -= 1; if depth == 0 { close = Some(i); break; } }
            _ => {}
        }
    }
    let close = match close { Some(i) => i, None => return vec![pat.to_string()] };
    let (pre, post) = (&pat[..open], &pat[close + 1..]);
    let inner = &pat[open + 1..close];

    let mut out = vec![];
    let (mut buf, mut d) = (String::new(), 0i32);
    let mut opts_: Vec<String> = vec![];
    for ch in inner.chars() {
        match ch {
            '{' => { d += 1; buf.push(ch); }
            '}' => { d -= 1; buf.push(ch); }
            ',' if d == 0 => opts_.push(std::mem::take(&mut buf)),
            _ => buf.push(ch),
        }
    }
    opts_.push(buf);
    for o in opts_ {
        for expanded in expand_braces(&format!("{}{}{}", pre, o.trim(), post)) {
            if !out.contains(&expanded) { out.push(expanded); }
        }
    }
    out
}

/// glob 한 개를 파일 이름에 맞춰 본다(`*` 와 `?` 만 지원 — 확장자 필터가 목적).
/// 대소문자는 무시한다(윈도우 관습, "*.PDF" 로 적어도 .pdf 가 잡히게).
fn glob_match(pat: &str, name: &str) -> bool {
    let p: Vec<char> = pat.to_lowercase().chars().collect();
    let n: Vec<char> = name.to_lowercase().chars().collect();
    // 고전적인 두 포인터 방식 — '*' 를 만나면 되돌아올 지점을 기억해 둔다.
    let (mut pi, mut ni) = (0usize, 0usize);
    let (mut star, mut mark) = (None, 0usize);
    while ni < n.len() {
        if pi < p.len() && (p[pi] == '?' || p[pi] == n[ni]) {
            pi += 1; ni += 1;
        } else if pi < p.len() && p[pi] == '*' {
            star = Some(pi); mark = ni; pi += 1;
        } else if let Some(s) = star {
            // 직전 '*' 가 한 글자 더 먹도록 되돌린다.
            pi = s + 1; mark += 1; ni = mark;
        } else {
            return false;
        }
    }
    while pi < p.len() && p[pi] == '*' { pi += 1; }
    pi == p.len()
}

/// 기본 seed 저장소 경로: CSOCLASSIFY_POLICY_DIR/class_seed.jsonl → exe 옆 class_seed.jsonl.
fn default_seed_path() -> Option<String> {
    if let Ok(dir) = std::env::var("CSOCLASSIFY_POLICY_DIR") {
        let p = Path::new(&dir).join("class_seed.jsonl");
        return Some(p.to_string_lossy().into_owned());
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            return Some(dir.join("class_seed.jsonl").to_string_lossy().into_owned());
        }
    }
    None
}

fn file_hash(path: &Path) -> Option<String> {
    let bytes = std::fs::read(path).ok()?;
    let mut h = Sha256::new();
    h.update(&bytes);
    Some(format!("{:x}", h.finalize()))
}

// -----------------------------------------------------------------
// 메인함수
// -----------------------------------------------------------------
fn main() {
    // 예상 못 한 내부 오류(패닉)도 파일에 남긴다 — 화면이 없는 환경(UI·배치)에서
    // 죽으면 원인을 알 방법이 사라진다. 가장 먼저 건다.
    errlog::install_panic_hook();

    // 히든: 임베딩 검증용(--emb-test "<텍스트>") → 384벡터 JSON 출력.
    let raw: Vec<String> = std::env::args_os()
        .skip(1)
        .map(|a| a.to_string_lossy().into_owned())
        .collect();
    if raw.first().map(|s| s == "--emb-test").unwrap_or(false) {
        let text = raw.get(1).cloned().unwrap_or_default();
        match embed::Embedder::load() {
            Some(mut e) => match e.embed_document(&text) {
                Some(v) => println!("{}", serde_json::to_string(&v).unwrap()),
                None => errcodes::fail("embed_failed",
                    "[csoclassify-rs] embed 실패(빈 텍스트?)", None),
            },
            None => errcodes::fail("model_load_failed",
                &format!("[csoclassify-rs] 모델/런타임 로드 실패({}/{}, onnxruntime.dll 확인)",
                         embed::MODEL_ROOT, embed::MODEL_NAME),
                None),
        }
        return;
    }

    // 인자 해석이 실패해도 JSON 으로 알려야 하므로, 파싱 전에 날것의 argv 를 훑는다.
    errcodes::enable_json(errcodes::wants_json(std::env::args()));
    
    // [2026-09-01] 예전에는 인자 오류를 2(임베딩 실패)로 냈다. 받는 쪽이 그 2 를 보고
    // "모델이 없구나" 하고 재설치를 안내하면 실제 원인(경로 오타)과 전혀 다른 대응을
    // 하게 된다. 파이썬 판·실행가이드와 같은 3 으로 맞춘다.
    let opts = match parse_args() {
        Ok(o) => o,
        Err(e) => errcodes::fail("bad_args", &format!("[csoclassify-rs] {}", e), None),
    };
    let t0 = Instant::now();

    // 분류체계 내보내기 전용 모드 — 문서도 규칙셋도 필요 없다. 규칙셋을 먼저 읽는
    // 아래 흐름을 타면 "cso_rules.yaml 이 없다"고 엉뚱한 곳에서 멈추고, 만들려던
    // doc_taxonomy.yaml 이 아직 없다는 이유로 축 로드에서 또 걸린다 — 그래서 여기서 끝낸다.
    if opts.export_taxonomy {
        std::process::exit(errcodes::finish(run_export_taxonomy(&opts), None));
    }

    // 업무분류 규칙 채우기 전용 모드 — 분류 체계와 규칙 파일만 있으면 된다.
    // 화면의 [분류 불러오기] 버튼이 부르는 것이 이 길이다(문서도 모델도 안 읽는다).
    if opts.sync_doc_rule {
        std::process::exit(errcodes::finish(run_sync_doc_rule(&opts), None));
    }

    // 전파 전용 모드 — 입력이 1차 '레코드 파일'이라 문서도 규칙셋도 모델도 필요 없다.
    // 규칙셋을 먼저 읽는 아래 흐름을 타면 cso_rules.yaml 이 없다는 이유로 엉뚱하게
    // 멈추므로, 규칙셋 로드 전에 끝낸다(파이썬 cli.py 의 (1.5) 와 같은 자리).
    if opts.propagate.is_some() {
        std::process::exit(errcodes::finish(run_propagate(&opts),
                                            opts.propagate.as_deref()));
    }

    let rules_path = match resolve_rules(&opts) {
        Some(p) => p,
        None => errcodes::fail("rules_missing",
            "[csoclassify-rs] 규칙셋(cso_rules.yaml)을 찾을 수 없습니다. --rules 로 지정하세요.",
            opts.rules_path.as_deref()),
    };
    if !rules_path.is_file() {
        errcodes::fail("rules_missing",
            &format!("[csoclassify-rs] 규칙셋(cso_rules.yaml)을 찾을 수 없습니다: {}",
                     rules_path.display()),
            rules_path.to_str());
    }
    let rs: RuleSet = match load_rules(&rules_path) {
        Ok(rs) => rs,
        // 파일은 있는데 등급 값이 틀린 경우만 코드 4 로 나눠, 배치가 "파일 없음"과
        // "내용 오류"에 다르게 대응할 수 있게 한다(Python 판 EXIT_RULES_INVALID 와 동일).
        // 파일은 있는데 등급 값이 틀린 경우도, 읽다가 깨진 경우도 '내용 문제'(4)다.
        // 파일이 아예 없는 경우(3)와 갈라 두면 배치가 대응을 나눌 수 있다.
        Err(rules::RulesError::Invalid(msg)) => errcodes::fail("rules_invalid",
            &format!("[csoclassify-rs] {}", msg), rules_path.to_str()),
        Err(e) => errcodes::fail("rules_invalid",
            &format!("[csoclassify-rs] {}", e), rules_path.to_str()),
    };

    // 업무분류(doctype) 축 — 있으면 켜고 없으면 security 만(4-6). --axis doctype 이면 필수.
    let mut dt_axis = match load_doctype_axis(&opts) {
        Ok(v) => v,
        Err(code) => std::process::exit(code),
    };

    let doctype_strategy_override = apply_conflict_override(&opts, &mut dt_axis);

    // --doctype-vector-only : 업무분류 1차(규칙 스캔)를 통째로 건너뛴다. 규칙 '목록'만
    // 비우고 embed 임계값·conflict 전략·defaults 는 파일에 적힌 그대로 둔다 —
    // 빈 규칙셋(DocRuleSet::empty)으로 갈아치우면 관리자가 정해 둔 임계값까지 기본값으로
    // 되돌아가 조용히 무시된다. 1차가 빈손이 되면 그 뒤는 'doc_rule.yaml 이 없는 배포'와
    // 똑같은 길을 타므로(2차 전파가 라벨을 채운다) 새 분기를 만들 필요가 없다.
    if opts.doctype_vector_only {
        if let Some((_, drs)) = dt_axis.as_mut() {
            drs.rules.clear();
            note!("[csoclassify-rs] 업무분류: 규칙을 쓰지 않고 기준 문서(class_seed) 비교로만 분류합니다(--doctype-vector-only).");
        }
    }

    // 규칙셋 검사 모드: 여기까지 왔다는 것은 검증을 통과했다는 뜻 → 요약만 알리고 종료.
    // 문서·모델이 필요 없으므로 파일 수집 전에 끝낸다.
    if opts.check_rules {
        // 검사만 하는 모드도 끝을 알린다 — 부르는 쪽이 한 가지 방법으로만 읽게.
        let _guard = ();
        println!("[csoclassify-rs] 규칙셋 정상: {}", rules_path.display());
        println!("  version={}  등급={}", rs.version, rules::GRADES.join("/"));
        println!("  regex_pii={} · pii_combos={} · keywords={} · sensitive={} · stamps={} · paths={}",
                 rs.regex_rules.len(), rs.pii_combos.len(), rs.keyword_rules.len(),
                 rs.sensitive_rules.len(), rs.stamp_rules.len(), rs.path_rules.len());
        match &dt_axis {
            Some((taxonomy, drs)) => {
                println!("[csoclassify-rs] 업무분류(doctype) 축 정상");
                println!("  분류체계 노드={} (사용중={}) · exported_at={}",
                         taxonomy.len(), taxonomy.active_nodes().len(), taxonomy.exported_at);
                if drs.version == "none" {
                    // 규칙 없이 seed 전파로만 도는 배포 — 규칙 건수 대신 "무엇으로
                    // 분류하게 되는지"를 알려 준다(seed 가 있어야 실제로 분류된다).
                    let sp: Option<String> = opts.seeds.clone().or_else(default_seed_path);
                    let n_seed = match &sp {
                        Some(p) => propagate::DoctypeSeedIndex::from_seed_file(p).size(),
                        None => 0,
                    };
                    let shown = sp.clone().unwrap_or_else(|| "(경로 없음)".into());
                    println!("  규칙셋 없음 → 'seed 전파 전용' 모드");
                    if n_seed > 0 {
                        println!("  업무분류 seed {}건 사용 가능: {}", n_seed, shown);
                    } else {
                        println!("  쓸 수 있는 업무분류 seed 가 없습니다({}) — 업무분류는 전부 미분류로 남습니다", shown);
                    }
                } else {
                    println!("  doctype_rules={} (활성={}) · version={} · conflict={}",
                             drs.rules.len(), drs.active_rules().len(), drs.version, drs.conflict.strategy);
                }
            }
            None => println!("[csoclassify-rs] 업무분류(doctype) 축은 이번 실행에서 꺼져 있습니다."),
        }
        return;
    }

    // 목록과 --file/--dir 을 같이 주면 어느 쪽이 진짜 대상인지 알 수 없다 —
    // 조용히 하나를 고르면 '왜 저 파일이 빠졌지?' 로 이어지므로 그 자리에서 막는다.
    if opts.files_from.is_some() && (opts.file.is_some() || opts.dir.is_some()) {
        errcodes::fail("bad_args",
            "[csoclassify-rs] --files-from 은 --file/--dir 과 함께 쓸 수 없습니다.", None);
    }

    // 입력 목록(--filelist) 로드 — 대상 수집보다 먼저 한다. 목록이 깨져 있으면
    // (F1·F3) 문서를 한 건도 읽기 전에 멈추는 것이 맞다. 절반쯤 잘못된 ID 가 붙은
    // 결과가 나가는 것이 최악이기 때문이다(규칙셋 검증과 같은 원칙).
    let flist: Option<filelist::FileList> = match opts.filelist.as_deref() {
        None => None,
        Some(lp) => match filelist::load(lp) {
            Ok(fl) => {
                eprintln!("[csoclassify-rs] 목록 {}: {}건 적재(전체 {}줄)",
                          Path::new(lp).file_name()
                              .map(|s| s.to_string_lossy().into_owned())
                              .unwrap_or_else(|| lp.to_string()),
                          fl.len(), fl.lines);
                for w in &fl.warnings {
                    eprintln!("[csoclassify-rs] {}", w);
                }
                Some(fl)
            }
            // '파일이 없다'와 '내용이 잘못됐다'는 부르는 쪽의 대응이 다르므로 코드를 나눈다.
            Err(e) => {
                let kind = if Path::new(lp).is_file() { "filelist_invalid" } else { "filelist_missing" };
                errcodes::fail(kind, &format!("[csoclassify-rs] {}", e), None);
                unreachable!()
            }
        },
    };

    let files = collect_files(&opts, flist.as_ref());
    if files.is_empty() {
        // 부르는 쪽의 대응이 다르므로 두 상황을 갈라 준다.
        //   · 대상을 아예 안 줌(1002) → 명령 자체를 고쳐야 한다
        //   · 줬는데 0건(1001)        → 사용자에게 폴더를 다시 물으면 된다
        match opts.file.as_deref().or(opts.dir.as_deref()).or(opts.files_from.as_deref())
                  .or(opts.filelist.as_deref()) {
            None => errcodes::fail("no_target_arg",
                "[csoclassify-rs] 처리할 파일이 없습니다. --file · --dir · --files-from · --filelist 중 하나 지정.", None),
            Some(t) => {
                // 왜 0건인지를 상황에 맞게 말해 준다 — "없다"만으로는 무엇을
                // 고칠지 모른다.
                let why = if opts.file.is_some() {
                    if Path::new(t).is_dir() {
                        "폴더입니다 — 폴더는 --dir 로 지정하세요.".to_string()
                    } else {
                        "그런 파일이 없습니다.".to_string()
                    }
                } else {
                    format!("폴더가 없거나, --glob 패턴({})에 맞는 파일이 없습니다.",
                            opts.glob.as_deref().unwrap_or("*"))
                };
                errcodes::fail("no_input",
                    &format!("[csoclassify-rs] 처리할 파일이 없습니다: {}\n{}", t, why),
                    Some(t))
            }
        }
    }

    // 분류 모드 결정(ko-pii cli.py) — 배타: 벡터만 / 규칙만 / 기본(규칙+자동전파).
    let seeds_path: Option<String> = opts.seeds.clone().or_else(default_seed_path);
    let seed_exists = seeds_path.as_deref().map(|p| Path::new(p).is_file()).unwrap_or(false);
    let mut auto_prop = opts.auto_propagate;
    let (mut rules_enabled, embed_mode): (bool, &str) = if opts.vector_only {
        if !seed_exists {
            errcodes::fail("seeds_missing",
                "[csoclassify-rs] --vector-only 는 비교 기준 seed 파일이 필요합니다: --seeds <class_seed.jsonl>(또는 exe 옆)",
                opts.seeds.as_deref());
        }
        auto_prop = true;
        (false, "all")
    } else if opts.rule_only {
        auto_prop = false;
        (true, "none")
    } else {
        // 기본: seed 가 있으면 자동 전파 on(명시 --auto-propagate 없이도).
        if !auto_prop && seed_exists {
            auto_prop = true;
        } else if !auto_prop {
            // seed 가 없으면 전파(Signal B)가 통째로 꺼진다 — 규칙이 못 정한 문서는
            // 구제받지 못하고 등급 없이(null) 나간다. 예전에는 두 판 모두 이걸 아무
            // 말 없이 넘어갔고, 배포 폴더에 파일이 빠진 것을 아무도 몰랐다(실측:
            // Python 배포본에 class_seed.jsonl 이 없어 보류 21건이 미분류로 나갔다 —
            // 부록 C-5). 파일이 없는 것 자체는 정상 배포일 수도 있으므로(규칙만 쓰는
            // 배포) 오류로 막지는 않고, '무엇이 꺼졌는지'만 분명히 알린다.
            // 문구는 cli.py 와 같게 맞춘다 — 두 판을 같은 눈으로 보게 하려는 것이다.
            let shown = seeds_path.clone().unwrap_or_else(|| "class_seed.jsonl".into());
            eprintln!("[csoclassify-rs] 전파용 seed 파일이 없어 자동 전파를 건너뜁니다: {}\n\
                       \x20               규칙이 못 정한 문서는 미분류로 남습니다. \
                      의도한 것이면 --rule-only 로 명시하세요.", shown);
        }
        // 임베딩 범위: --embed-needed(명시) > --with-vector 는 '전량'(레코드에 벡터를
        // 실어야 하므로 확정 문서도 빠뜨리면 안 된다) > 전파용 'needed'(못 정한 문서만) > 'none'.
        // 전파가 켜져 있어도 --with-vector 면 all 이 이긴다 — needed 로 내리면 등급이
        // 이미 확정된 문서에 벡터가 안 실려 "전량 벡터"라는 약속이 깨진다(cli.py 와 동일).
        //
        // [2026-09-02] --embed-needed 를 맨 앞에 둔다. 예전에는 이 인자를 Opts 에 받아만
        // 두고 아무도 읽지 않아, seed 파일이 없으면 'none' 으로 떨어져 **인자가 통째로
        // 무시**됐다(파이썬은 cli.py 의 `if args.embed_needed` 로 언제나 needed 다).
        // 부르는 쪽이 "못 정한 문서만 임베딩하라"고 명시했는데 조용히 아무것도 안 하면,
        // --serve 를 무시하던 것과 같은 종류의 함정이 된다.
        let m = if opts.embed_needed { "needed" }
                else if opts.with_vector { "all" }
                else if auto_prop { "needed" }
                else { "none" };
        (true, m)
    };

    // --axis doctype 이면 security 신호 자체를 계산하지 않는다(6-4) — PII 검출까지
    // 통째로 생략해, 가장 비싼 단계를 건너뛴 대량 업무분류 스캔이 크게 빨라진다.
    if opts.axis.as_deref() == Some("doctype") {
        rules_enabled = false;
    }

    // 'seed 전파 전용'(doc_rule.yaml 없음)인데 전파까지 못 하는 상황이면 미리 알린다.
    // 조용히 빈 결과를 내면 "분류할 게 없었다"로 오해되는데, 실제로는 "분류할 수단이
    // 없었다"라서 대응이 완전히 다르다(규칙을 쓰거나 seed 를 채워야 한다).
    // seed 유무를 먼저 본다 — seed 가 없으면 embed_mode 도 덩달아 none 이 되므로,
    // 순서를 반대로 하면 진짜 원인인 'seed 없음'을 '--rule-only 탓'으로 잘못 짚는다.
    if dt_axis.as_ref().map_or(false, |(_, drs)| drs.version == "none") {
        let n_dt_seed = match &seeds_path {
            Some(p) => propagate::DoctypeSeedIndex::from_seed_file(p).size(),
            None => 0,
        };
        let why = if n_dt_seed == 0 {
            let shown = seeds_path.clone().unwrap_or_else(|| "class_seed.jsonl".into());
            Some(format!("쓸 수 있는 seed(labels.doctype)가 없어({})", shown))
        } else if embed_mode == "none" {
            Some("임베딩을 하지 않아(--rule-only 등)".to_string())
        } else {
            None
        };
        if let Some(why) = why {
            note!("[csoclassify-rs] 업무분류: 규칙(doc_rule.yaml)도 없고 {} 전파도 못 합니다 \
— 전부 미분류로 두니 관리자가 분류한 뒤 seed 로 승격하세요.", why);
        }
    }

    // 모델·런타임 배포 점검(값싼 확인) — 세션은 만들지 않고 파일 존재만 본다.
    //
    // [2026-09-02] 임베딩 모델을 '필요할 때만' 올리게 되면서, 규칙만으로 끝나는 배치는
    // 모델을 전혀 건드리지 않는다. 그러면 onnxruntime.dll·models/ 가 빠진 배포가
    // 조용히 성공하고, 한참 뒤 미분류가 나오는 배치를 만나서야 드러난다 —
    // "그때는 되고 지금은 안 되네" 는 거버넌스 도구에서 가장 나쁜 실패다.
    // 이 판은 --status 도 인자만 받고 무시하므로, 배포 이상을 알릴 자리가 여기밖에 없다.
    //
    // 점검 결과를 '알리고 끝'이 아니라 뒤에서 실제로 쓴다. onnxruntime 을 못 찾으면
    // ort 는 PATH 에 있는 아무 onnxruntime.dll 이나 집어 들고, 버전이 다르면 Err 가
    // 아니라 **패닉**으로 죽는다(실측: "expected 1.22.x, but got 1.17.1", 종료코드 101).
    // Embedder::load() 가 None 을 준다는 전제가 거기서 깨지므로, 확인이 실패했으면
    // 아예 부르지 않는다 — 결과 파일도 못 내고 죽는 것보다 '전파만 못 한' 결과가 낫다.
    let deploy_ok = if embed_mode != "none" {
        match embed::check_deployment() {
            Ok(()) => true,
            Err(why) => {
                errlog::err(&format!(
                    "[csoclassify-rs] 임베딩 모델/런타임 확인 실패 — {} \
→ 임베딩·전파를 건너뜁니다(규칙으로 정해진 등급은 그대로 나갑니다)", why));
                false
            }
        }
    } else {
        false   // 임베딩을 안 하는 모드에서는 이 값이 쓰이지 않는다.
    };

    let need_hash = opts.hash || opts.simple;
    let mut fail = 0u32;
    // 본문에 쓸 만한 글자가 이보다 적으면 '읽을 글자가 없었다'로 본다(파이썬 판
    // config.MIN_TEXT_LEN 과 같은 값·같은 뜻). 스캔본(이미지) PDF 는 추출기가
    // 실패를 알리지 않고 장식기호 몇 개를 돌려주므로, 길이로 가리지 않으면
    // '미분류'(=읽었는데 신호 없음)와 구분되지 않는다.
    let min_text_len: usize = std::env::var("CSOCLASSIFY_MIN_TEXT_LEN")
        .ok().and_then(|v| v.parse().ok()).unwrap_or(20);

    // 1차: 추출 + (규칙) 분류 → 아이템 수집(전파 위해 text/seed_eligible/vector 보관).
    struct Item { rec: Value, grade: Option<Grade>, text: String, seed_eligible: bool,
                  vector: Option<Vec<f32>>, file: String, dt: Option<doctype::DoctypeSignal> }
    let mut items: Vec<Item> = vec![];
    // 문서 식별자 집계 — ID 를 어떤 출처로 채웠는지 센다(요약 · R14).
    let (mut n_sfile, mut n_content, mut n_pathid) = (0usize, 0usize, 0usize);
    let (mut n_case, mut n_hash_mismatch) = (0usize, 0usize);
    let mut missing_id_rows: Vec<Value> = vec![];
    let docid_on = !opts.no_doc_id;
    // 화면(UI)이 진행바를 그리려면 '몇 개 중 몇 개째'를 알아야 한다. 첫 줄은 0/N 으로
    // 내보낸다 — 규칙셋·분류체계를 읽고 대상을 훑는 준비 단계가 있어, 첫 파일이 끝나기
    // 전까지 화면이 멈춘 것처럼 보이지 않게 하려는 것이다(Python cli.py 와 같은 형식·차례).
    // ※ 임베딩 모델은 여기가 아니라 아래 2차 단계에서, 그것도 필요할 때만 올린다.
    //    예전 주석은 "모델 로딩 때문에 첫 파일이 오래 걸려"라고 적혀 있었는데, 로딩은
    //    이 루프가 '끝난 뒤'라 사실과 달랐다.
    if opts.progress {
        eprintln!("[progress] 0/{} ", files.len());
    }
    for (done, path) in files.iter().enumerate() {
        let fmt = detect::detect_format(path);
        let display = path.to_string_lossy().replace('\\', "/");
        let text = match extract::extract_text(path, fmt) {
            Some(t) => t,
            None => {
                // 못 읽은 문서도 '결과'다 — 레코드를 안 만들면 그 문서는 결과에서
                // 통째로 사라져, 화면에는 "18개 중 11건"처럼 조용히 줄어든 숫자만
                // 남는다. 무엇이 왜 빠졌는지 알 수 없는 것은 거버넌스 도구에서
                // 가장 나쁜 실패다. 등급 없이(=보류) 실패 사유를 실어 내보낸다.
                errlog::err(&format!("[추출실패] {} (감지={})", path.display(), fmt.as_str()));
                fail += 1;
                items.push(Item {
                    rec: json!({
                        "file": display, "grade": Value::Null, "confidence": 0.0,
                        "method": "extract_failed", "decided_by": [], "seed_eligible": false,
                        "signals": {},
                        "error": {"stage": "extract", "detected": fmt.as_str(),
                                  "reason": "텍스트를 추출하지 못했습니다"},
                        "rule_version": rs.version, "ts": Value::Null
                    }),
                    grade: None, text: String::new(), seed_eligible: false,
                    vector: None, file: display.clone(),
                    // 본문이 없어도 업무분류 축이 켜져 있으면 '축은 돌았다'를 남긴다 —
                    // 그래야 화면이 "분류 안 됨(사람이 봐야 함)"으로 셀 수 있다.
                    dt: dt_axis.as_ref().map(|(taxonomy, drs)|
                        doctype::scan_doctype("", &display, drs, taxonomy)),
                });
                // 못 읽은 문서에도 식별자는 단다 — 그 문서도 사람이 손볼 '결과'라
                // 나중에 수정을 이으려면 키가 필요하다(내용을 못 읽으면 경로 해시로 내려간다).
                if docid_on {
                    let (did, src, key, matched) =
                        filelist::resolve_doc_id(&display, path, flist.as_ref());
                    match src {
                        "sfile_id" => n_sfile += 1,
                        "content" => n_content += 1,
                        _ => n_pathid += 1,
                    }
                    if matched == Some("case") { n_case += 1; }
                    let r = &mut items.last_mut().unwrap().rec;
                    r["doc_id"] = json!(did);
                    r["doc_id_source"] = json!(src);
                    r["key"] = json!(key);
                    if matched == Some("case") { r["rematched_by"] = json!("case"); }
                    if src != "sfile_id" {
                        missing_id_rows.push(json!({
                            "file": r["file"].clone(), "key": r["key"].clone(),
                            "doc_id": r["doc_id"].clone(),
                            "doc_id_source": r["doc_id_source"].clone()}));
                    }
                }
                continue;
            }
        };
        let (mut rec, grade) = if rules_enabled {
            build_record(&display, &text, &rs, opts.failsafe.as_deref())
        } else {
            // 벡터-only: 규칙 미실행 → 보류 레코드(빈 signals). failsafe 는 전파 재융합에서 적용.
            (json!({
                "file": display, "grade": Value::Null, "confidence": 0.0,
                "method": "unclassified", "decided_by": [], "seed_eligible": false,
                "signals": {}, "rule_version": rs.version, "ts": Value::Null
            }), None)
        };
        // 본문을 사실상 못 읽었으면 표식을 단다(분류 결과 자체는 건드리지 않는다 —
        // 파일명·경로 신호는 본문과 무관하고, 몇 글자라도 규칙에 걸렸으면 그 등급이
        // 맞다). 다만 아무것도 못 정했다면 method 를 'unclassified' 로 두지 않는다.
        // 그 말은 "봤는데 없더라"라는 뜻이라 사실과 다르다.
        let body_chars = text.chars().filter(|c| !c.is_whitespace()).count();
        if body_chars < min_text_len {
            fail += 1;
            errlog::err(&format!("[본문없음] {} ({}자) — 스캔본(이미지)일 수 있습니다",
                                 path.display(), body_chars));
            rec["error"] = json!({
                "stage": "extract", "detected": fmt.as_str(), "text_len": body_chars,
                "reason": "본문 텍스트가 거의 없습니다 — 스캔본(이미지)이거나 빈 문서일 수 \
                           있습니다. OCR 이나 사람 확인이 필요합니다."
            });
            if rec["grade"].is_null()
                && rec["method"].as_str().map_or(true, |m| m == "unclassified") {
                rec["method"] = json!("extract_failed");
            }
        }
        // 문서 식별자(doc_id·key) — 설계 §7-5. 해시 계산보다 먼저 달아 둔다.
        if docid_on {
            let (did, src, key, matched) =
                filelist::resolve_doc_id(&display, path, flist.as_ref());
            match src {
                "sfile_id" => n_sfile += 1,
                "content" => n_content += 1,
                _ => n_pathid += 1,
            }
            if matched == Some("case") {
                n_case += 1;
                rec["rematched_by"] = json!("case");
            }
            // F6 — 목록이 준 해시와 실제 파일이 다르면 문서가 수정된 것이다.
            // 같은 문서이므로 처리는 그대로 계속하고 건수만 센다.
            if src == "sfile_id" {
                if let Some(fl) = flist.as_ref() {
                    if let Some((e, _)) = fl.lookup(&display) {
                        if let Some(want) = &e.hash {
                            if file_hash(path).as_deref() != Some(want.as_str()) {
                                n_hash_mismatch += 1;
                            }
                        }
                    }
                }
            }
            rec["doc_id"] = json!(did);
            rec["doc_id_source"] = json!(src);
            rec["key"] = json!(key);
            if src != "sfile_id" {
                missing_id_rows.push(json!({
                    "file": rec["file"].clone(), "key": rec["key"].clone(),
                    "doc_id": rec["doc_id"].clone(),
                    "doc_id_source": rec["doc_id_source"].clone()}));
            }
        }
        if need_hash { rec["hash"] = json!(file_hash(path)); }
        if opts.with_pii { rec["pii"] = json!(rules::collect_pii(&text, &rs)); }
        // 업무분류 축은 security 모드(rule-only/vector-only)와 무관하게 독립적으로 돈다(6-4).
        let dt = dt_axis.as_ref().map(|(taxonomy, drs)| doctype::scan_doctype(&text, &display, drs, taxonomy));
        let seed_elig = rec.get("seed_eligible").and_then(|b| b.as_bool()).unwrap_or(false);
        if opts.progress {
            // 형식은 Python 판과 같아야 한다 — 화면이 같은 규칙으로 읽는다.
            eprintln!("[progress] {}/{} {}", done + 1, files.len(), path.display());
        }
        items.push(Item { rec, grade, text, seed_eligible: seed_elig, vector: None, file: display, dt });
    }

    // 2차: 임베딩 → (auto_prop 이면) seed 비교 → 보류 문서 재융합.
    //   임베딩 자체는 전파와 분리해서 돌린다 — --with-vector 는 전파를 안 하더라도
    //   레코드에 벡터를 실어야 하므로, 전파 조건에 묶어 두면 벡터가 통째로 비게 된다.
    let mut prop_stats: Option<(usize, u32, u32)> = None;
    // 모델을 올릴 필요가 '한 건이라도' 있는가 — 세션을 만들기 전에 먼저 답한다.
    //
    // [2026-09-02] 예전에는 embed_mode 만 보고 무조건 Embedder::load() 했다. 전 문서가
    // 1차 규칙에서 확정된 배치에서는 model.onnx(118MB)+tokenizer.json(17MB)을 읽고
    // ORT 세션까지 구성한 뒤, 아래 루프에서 need 가 전부 false 라 **한 건도 임베딩하지
    // 않고** 끝났다. 이 판에는 상주 데몬이 없어 그 비용을 프로세스마다 새로 낸다
    // (실측 건당 약 1.4초, Rust/README.md 성능표). 그래서 아래 need 와 같은 판정을
    // 배치 전체에 대해 미리 한 번 돌려, 필요 없으면 모델을 아예 읽지 않는다.
    //
    // seed_eligible 은 '벡터가 쓰이는 곳이 있느냐'에 따라 갈린다 — 두 경우를 나눠 본다.
    //   · 전파용으로만 쓸 때: 내부 seed 인덱스(SeedIndex::from_records)는 security 전파
    //     루프에서만 쓰이는데 그 루프는 '보류 문서만' 돈다. 보류가 0건이면 seed_eligible
    //     문서의 벡터는 만들어 놓고 아무도 쓰지 않는다 → has_undecided 가 흡수한다.
    //   · --embed-needed 로 부를 때: 그 벡터가 '결과 레코드에 실려 나가는 산출물'이 된다.
    //     seed_eligible 문서는 seed 승격 후보이고, 이 인자의 존재 이유가 바로
    //     "보류거나 seed_eligible 인 문서만 임베딩"(설계서 §185)이다. 여기서 빼면
    //     규칙으로 등급이 확정된 승격 후보의 벡터가 통째로 안 나가, 인자가 무의미해진다.
    //     (파이썬 cli.py 의 need_vec 도 seed_eligible 을 조건에 포함한다.)
    let has_undecided = items.iter().any(|it| it.grade.is_none());
    let dt_undecided_any = items.iter()
        .any(|it| it.dt.as_ref().map_or(false, |s| s.values.is_empty()));
    let seed_elig_any = items.iter().any(|it| it.seed_eligible);
    let need_any = embed_mode == "all" || has_undecided || dt_undecided_any
        || (opts.embed_needed && seed_elig_any);
    if embed_mode != "none" && !need_any {
        // 조용히 넘어가면 "임베딩이 안 돌았다"와 "임베딩이 필요 없었다"가 구분되지 않는다.
        // 뒤에 붙던 [전파] 요약줄도 이 경우엔 안 나가므로, 그 자리를 이 줄이 대신한다.
        note!("[csoclassify-rs] 임베딩 불필요(전 문서 규칙 확정) → 모델 로드 생략");
    }
    if embed_mode != "none" && need_any && deploy_ok {
        match embed::Embedder::load() {
            Some(mut embedder) => {
                // 임베딩 대상: all=전량, needed=아직 못 정한 축이 하나라도 있을 때.
                //   · security — 보류(grade=None) 이거나 seed_eligible
                //   · doctype  — 축은 켜졌는데 라벨이 하나도 안 붙음(values 가 빔)
                // doctype 조건이 꼭 필요한 이유: doc_rule.yaml 이 없어 'seed 전파 전용'
                // 으로 도는 배포는 1차 스캔이 언제나 빈손인데, security 등급은 멀쩡히
                // 나올 수 있다. security 기준만 보면 벡터를 안 만들고 → 전파도 못 하고
                // → 업무분류가 영영 미분류로 남는다.
                for it in items.iter_mut() {
                    let dt_undecided = it.dt.as_ref().map_or(false, |s| s.values.is_empty());
                    let need = embed_mode == "all" || it.grade.is_none() || it.seed_eligible
                        || dt_undecided;
                    if need { it.vector = embedder.embed_document(&it.text); }
                }
                // 여기부터는 '전파'다. --with-vector 만 준 경우(전파 off)는 위에서 벡터만
                // 만들고 끝내야 하므로 통째로 건너뛴다.
                if auto_prop {
                // 내부 seed(코퍼스 seed_eligible) + 외부 seed(class_seed.jsonl) 병합.
                let rec_tuples: Vec<(Option<Vec<f32>>, Option<String>, bool, String)> = items.iter()
                    .map(|it| (it.vector.clone(), it.grade.map(|g| g.as_str().to_string()), it.seed_eligible, it.file.clone()))
                    .collect();
                let internal = propagate::SeedIndex::from_records(&rec_tuples);
                let external = match &seeds_path {
                    Some(p) => propagate::SeedIndex::from_seed_file(p),
                    None => propagate::SeedIndex::empty(),
                };
                let seeds = propagate::SeedIndex::merge(external, internal);
                // 보류 문서만 전파(이미 등급 확정 문서는 손대지 않음).
                let (mut decided, mut still) = (0u32, 0u32);
                for it in items.iter_mut() {
                    if it.grade.is_some() { continue; }
                    let vec = match &it.vector { Some(v) => v.clone(), None => continue };
                    let esig = propagate::propagate(&vec, &seeds);
                    let dict = esig.as_dict();
                    it.grade = rules::refuse_with_embed(&mut it.rec, esig.grade.as_deref(), esig.confidence as f64, dict, opts.failsafe.as_deref());
                    if it.grade.is_some() { decided += 1; } else { still += 1; }
                }
                prop_stats = Some((seeds.size(), decided, still));

                // doctype 축 전파(설계서 5-4). security 와 달리 "이미 후보가 있어도"
                // embed 가 후보를 더할 수 있다 — 다중 라벨이라 상향 전용이 아니라
                // 추가 전용이다. 그래서 "이미 분류됐으면 건너뛴다"는 조건이 없다.
                if let Some((taxonomy, drs)) = dt_axis.as_ref() {
                    let dt_seeds = match &seeds_path {
                        Some(p) => propagate::DoctypeSeedIndex::from_seed_file(p),
                        None => propagate::DoctypeSeedIndex::empty(),
                    };
                    // 정책 파일의 embed: 블록이 이 단계의 스위치이자 임계값이다.
                    // 스위치가 꺼져 있으면 조용히 넘어가지 말고 이유를 남긴다 —
                    // 그러지 않으면 "왜 벡터가 안 도는지"를 아무도 못 찾는다.
                    if dt_seeds.size() > 0 && !drs.embed.enabled {
                        note!("[전파][업무분류] seed {}건이 있으나 doc_rule.yaml 의 \
embed.enabled 가 false 라 2단계를 건너뜁니다", dt_seeds.size());
                    } else if dt_seeds.size() > 0 {
                        let dt_params = propagate::DoctypeParams {
                            k: drs.embed.k as usize,
                            dup_threshold: drs.embed.dup_threshold as f32,
                            min_sim: drs.embed.min_sim as f32,
                            min_share: drs.embed.min_share as f32,
                        };
                        let mut contributed = 0u32;
                        for it in items.iter_mut() {
                            let existing = match &it.dt { Some(sig) => sig.values.clone(), None => continue };
                            let vec = match &it.vector { Some(v) => v.clone(), None => continue };
                            let esig = propagate::propagate_doctype(&vec, &dt_seeds, dt_params);
                            if let Some(sigs) = it.rec.get_mut("signals").and_then(|s| s.as_object_mut()) {
                                sigs.insert("doctype_embed".into(), esig.as_dict());
                            }
                            let embed_values: Vec<(String, f64)> = esig.values.iter()
                                .map(|(id, c)| (id.clone(), *c as f64)).collect();
                            let merged = doctype::merge_embed_candidates(&existing, &embed_values, taxonomy, &drs.conflict,
                                                       Some(drs.defaults.embed_cap));
                            if merged.values.iter().any(|v| v.from.iter().any(|f| f == "embed")) {
                                contributed += 1;
                            }
                            it.dt = Some(merged);
                        }
                        note!("[전파][업무분류] seed={} embed기여={}", dt_seeds.size(), contributed);
                    }
                    // seed 가 없는 경우의 안내는 위(모드 결정 직후)에서 이미 냈다 — 그쪽은
                    // auto_prop 이 아예 꺼진 경우까지 잡아 주므로 여기서 또 내지 않는다.
                }
                } // if auto_prop
            }
            None => errlog::err(&format!(
                "[csoclassify-rs] 임베딩 모델/런타임 로드 실패 → 임베딩·전파 생략({}/{}, onnxruntime.dll 확인).",
                embed::MODEL_ROOT, embed::MODEL_NAME)),
        }
    }

    // 3차: 집계 + 출력 레코드 조립.
    let (mut c, mut s_, mut o_, mut none) = (0u32, 0u32, 0u32, 0u32);
    // 업무분류 집계(7-3 뿌리 카테고리 롤업). 이 축이 한 건도 안 돌았으면 0 으로
    // 남아 요약에서 섹션 자체가 빠진다 — "축을 안 씀"과 "미분류"의 구분(4-6).
    let mut dt_total = 0u32;
    let mut dt_unclassified = 0u32;
    let mut dt_root_totals: std::collections::BTreeMap<String, u32> = Default::default();
    let mut dt_node_totals: std::collections::BTreeMap<(String, String), u32> = Default::default();
    let mut records: Vec<Value> = vec![];
    // --simple 일 때만 채운다 — 감사용 전체 결과 파일에 쓸 원본 레코드.
    let mut full_records: Vec<Value> = vec![];
    for it in items {
        match it.grade {
            Some(Grade::C) => c += 1,
            Some(Grade::S) => s_ += 1,
            Some(Grade::O) => o_ += 1,
            None => none += 1,
        }
        let mut rec = it.rec;
        // labels 는 전파까지 모두 끝난 '최종' 신호로 만들어야 하므로 여기서 조립한다.
        let mut labels = json!({"security": security_label(&rec)});
        if let Some(sig) = &it.dt {
            dt_total += 1;
            let pairs = doctype_breakdown(sig);
            if pairs.is_empty() { dt_unclassified += 1; }
            for (root, node) in pairs {
                *dt_root_totals.entry(root.clone()).or_insert(0) += 1;
                *dt_node_totals.entry((root, node)).or_insert(0) += 1;
            }
            let mut d = sig.as_dict();
            if let Some(ov) = &doctype_strategy_override {
                d["strategy"] = json!(ov);
            }
            labels["doctype"] = d;
            if let Some((taxonomy, drs)) = dt_axis.as_ref() {
                rec["doctype_rule_version"] = json!(drs.version);
                rec["taxonomy_version"] = json!(taxonomy.exported_at);
            }
        }
        rec["labels"] = labels;

        // --with-vector(전량) · --embed-needed(못 정한 문서만): 문서벡터를 레코드에
        // 실어 보낸다(cli.py 의 rec["vector"] 와 같은 형식).
        //   f32 를 f64 로 넓혀 담는다 — 값 자체는 f32 그대로라 손실이 없고,
        //   파이썬 쪽 numpy float32 → float 변환과 같은 수를 낸다.
        //   모델 로드에 실패했거나 추출 텍스트가 비어 벡터가 없으면 필드를 아예 넣지 않는다
        //   ('벡터 없음'을 빈 배열로 적으면 0차원 벡터와 구분이 안 된다).
        //
        // [2026-09-02] --embed-needed 를 조건에 더했다. 이 인자는 파이썬에서 "못 정한
        // 문서의 벡터를 결과에 실어 달라"는 뜻인데(cli.py 는 벡터를 만들었으면 언제나
        // 싣는다), Rust 는 --with-vector 만 봐서 그 벡터가 나갈 구멍이 없었다. 실측에서
        // 임베딩에 1.1초를 쓰고도 결과 레코드가 바이트 단위로 똑같았다 — 순수한 낭비다.
        // 파이썬처럼 '만들었으면 언제나'로 하지 않은 이유: 그러면 아무 인자도 안 준
        // 기본 실행(UI 경로)에서도 미분류 문서마다 384개 실수가 붙어 결과 파일이 커진다.
        // 인자를 명시한 실행에서만 싣는다 — 기존 기본 출력은 그대로 둔다.
        if opts.with_vector || opts.embed_needed {
            if let Some(v) = &it.vector {
                rec["vector"] = Value::Array(v.iter().map(|x| json!(*x as f64)).collect());
            }
        }

        if opts.summary_only { continue; }
        let out_rec = if opts.simple {
            // 읽는 차례로 넣는다 — 무엇을(file·hash) → 어떻게 됐나(grade·doctype) →
            // 왜(why). Cargo.toml 의 serde_json preserve_order 가 이 차례를 지켜 준다
            // (기본값은 사전순이라 conf 가 업무분류 값 사이에 끼는 식으로 읽혔다).
            let mut m = serde_json::Map::new();
            m.insert("file".into(), rec["file"].clone());
            m.insert("hash".into(), rec.get("hash").cloned().unwrap_or(Value::Null));
            m.insert("grade".into(), rec["grade"].clone());
            // 업무분류 축이 돌았을 때만 doctype 키를 붙인다. 축을 안 쓰는 배포에서 빈
            // 배열이 나가면 "분류를 못 했다"와 "축을 안 썼다"가 구분되지 않는다.
            if let Some(dt) = rec.get("labels").and_then(|l| l.get("doctype")) {
                if dt.is_object() {
                    let ids: Vec<Value> = dt.get("values").and_then(|v| v.as_array())
                        .map(|a| a.iter().filter_map(|v| v.get("dc_id").cloned()).collect())
                        .unwrap_or_default();
                    m.insert("doctype".into(), Value::Array(ids));
                }
            }
            // 못 읽은 문서에는 그 사실을 함께 싣는다. 없으면 '읽었는데 미분류'와
            // 글자 그대로 같은 모습이라, --simple 만 받는 쪽은 스캔본을 영영
            // 못 가려낸다. 성공한 문서에는 이 칸이 아예 없다(기존 모양 그대로).
            if let Some(e) = rec.get("error") {
                m.insert("error".into(), e.get("reason").cloned()
                    .unwrap_or_else(|| Value::String("extract_failed".into())));
            }
            if opts.simple_why {
                m.insert("why".into(), why_record(&rec));
            }
            // 축약본만으로는 "왜 이 등급인가"를 나중에 댈 수 없다 — 원본을 따로
            // 들고 있다가 감사용 파일로 함께 남긴다(분류를 다시 하지 않는다).
            full_records.push(order_record(&rec));
            Value::Object(m)
        } else { order_record(&rec) };
        records.push(out_rec);
    }
    if let Some((seeds_n, decided, still)) = prop_stats {
        note!("[전파] seed={} 전파결정={} 미분류잔여={}", seeds_n, decided, still);
    }

    let total = files.len();
    let detected = c + s_ + o_;
    let total_ms = round1(t0.elapsed().as_secs_f64() * 1000.0);
    let mut summary = json!({
        "total": total, "detected": detected,
        "C": c, "S": s_, "O": o_, "unclassified": none,
        "extract_failed": fail,
        "elapsed_total_ms": total_ms, "rule_version": rs.version
    });
    if dt_total > 0 {
        let by_root: serde_json::Map<String, Value> = dt_root_totals.iter()
            .map(|(k, v)| (k.clone(), json!(v))).collect();
        summary["doctype"] = json!({
            "total": dt_total,
            "unclassified": dt_unclassified,
            "by_root": by_root,
            "doctype_rule_version": dt_axis.as_ref().map(|(_, d)| d.version.clone()),
            "taxonomy_version": dt_axis.as_ref().map(|(t, _)| t.exported_at.clone()),
        });
    }

    // 상태 줄에 실을 건수를 여기서 먼저 남긴다 — 아래 status_object 가 그 값을 쓴다.
    errcodes::set_counts(total as i64, fail as i64);
    // 정책 버전도 함께 — 결과만 있고 '어떤 규칙으로 판정했는지'가 없으면 재현할 수 없다.
    errcodes::set_versions(&[
        ("rule_version", Some(rs.version.clone())),
        ("taxonomy_version", dt_axis.as_ref().map(|(t, _)| t.exported_at.clone())),
        ("doctype_rule_version", dt_axis.as_ref().map(|(_, d)| d.version.clone())),
    ]);
    let target = opts.file.clone().or_else(|| opts.dir.clone());
    let code = if fail > 0 { errcodes::exit_of("extract_failed") } else { 0 };

    // 출력 조립. --out 으로 저장할 때는 상태도 파일 안에 남긴다 — 파일만 받아
    // 나중에 읽는 쪽은 stdout 을 이미 흘려보낸 뒤라, 파일 자체가 "이 결과가
    // 온전한가"를 말해 줘야 한다. --out 이 없으면 결과가 stdout 으로 나가고
    // 상태 줄도 거기 붙으므로 여기서 또 넣지 않는다(같은 줄이 두 번 나간다).
    let status = if errcodes::quiet() && opts.out.is_some() {
        Some(errcodes::status_object(code, target.as_deref()))
    } else {
        None
    };
    let body = render(&records, &summary, &opts, status.as_ref());
    match &opts.out {
        Some(p) => {
            if let Err(e) = std::fs::write(p, &body) {
                errcodes::fail("output_write_failed",
                    &format!("[csoclassify-rs] 출력 저장 실패 {}: {}", p, e), Some(p));
            }
            // --simple 이면 전체 레코드를 '<out>.full.<확장자>' 에 함께 남긴다.
            //   · --out 이 가리키는 파일은 지금까지처럼 축약본이다(계약 유지)
            //   · 이 파일에는 --nosummary 와 무관하게 요약을 남긴다 — 연동용이
            //     아니라 '나중에 되짚어 보는' 파일이라 정책 버전이 있어야 한다
            if opts.simple {
                let fp = full_out_path(p);
                let mut fopts = opts.clone();
                fopts.simple = false;
                fopts.no_summary = false;
                fopts.summary_only = false;
                let fbody = render(&full_records, &summary, &fopts, status.as_ref());
                if let Err(e) = std::fs::write(&fp, &fbody) {
                    errcodes::fail("output_write_failed",
                        &format!("[csoclassify-rs] 감사용 전체 결과 저장 실패 {}: {}", fp, e),
                        Some(&fp));
                }
                note!("[csoclassify-rs] 감사용 전체 결과: {}", fp);
            }
        }
        None => { print!("{}", body); }
    }

    // --report-missing-id — sfile_id 를 못 얻은 문서를 따로 뽑아 둔다. 이 문서들은
    // 매핑 테이블 적재 대상이 아니므로(R14), 왜 못 얻었는지 확인할 수 있게 남긴다.
    if let Some(mp) = opts.report_missing_id.as_deref() {
        if !missing_id_rows.is_empty() {
            let body: String = missing_id_rows.iter()
                .map(|r| format!("{}
", r)).collect();
            match std::fs::write(mp, body) {
                Ok(_) => eprintln!("[csoclassify-rs] 문서 ID 미획득 목록: {} ({}건)",
                                   mp, missing_id_rows.len()),
                // 본 작업은 끝난 뒤라, 리포트를 못 썼다고 결과까지 버릴 이유는 없다.
                Err(e) => eprintln!("[csoclassify-rs] 미획득 목록을 쓰지 못했습니다: {}", e),
            }
        }
    }

    // 화면 요약(stderr)
    if !opts.no_summary {
        // 문서 ID 획득 현황 — 설계 §7-5-2-1. 폴백 건수를 등급 줄보다 먼저 보여 준다.
        // 이 수치가 조용히 커지면 매핑에 들어가지 못하는 문서가 쌓이는데, 아무 오류도
        // 나지 않아 알아채기 어려운 종류의 실패이기 때문이다(R14).
        if docid_on && (n_sfile + n_content + n_pathid) > 0 {
            eprintln!("[summary][문서 ID] sfile_id {} · 폴백(content) {} · 폴백(path) {}",
                      n_sfile, n_content, n_pathid);
            let n_fallback = n_content + n_pathid;
            if n_fallback > 0 {
                let hint = if opts.report_missing_id.is_some() { "" }
                           else { " --report-missing-id 로 목록 확인" };
                eprintln!("[summary][문서 ID] ※ 폴백 {}건은 매핑 테이블 적재 대상이 아닙니다.{}",
                          n_fallback, hint);
            }
            if n_case > 0 {
                eprintln!("[summary][문서 ID] 대소문자만 달라 목록과 이어진 문서 {}건(rematched_by=case)",
                          n_case);
            }
            if n_hash_mismatch > 0 {
                eprintln!("[summary][문서 ID] [F6] 목록의 hash 와 실제 파일이 다른 문서 {}건 — 수정된 것으로 보이며 같은 문서로 처리했습니다", n_hash_mismatch);
            }
        }
        // --no-timing 이면 총시간을 뺀다(Python 판 --no-timing 과 같은 뜻).
        // 시간은 실행마다 달라지는 값이라, 결과를 비교·기록할 때 걸리적거린다.
        if opts.no_timing {
            eprintln!("[summary] 총 {}개 / 검출 {}, C={} S={} O={} 미분류={} 추출실패={}",
                total, detected, c, s_, o_, none, fail);
        } else {
            eprintln!("[summary] 총 {}개 / 검출 {}, C={} S={} O={} 미분류={} 추출실패={}, 총시간={}ms",
                total, detected, c, s_, o_, none, fail, total_ms);
        }
        // 업무분류 롤업 — 뿌리 카테고리별 총계(괄호 안은 실제 걸린 노드별 내역) + 미분류.
        if dt_total > 0 {
            let mut roots: Vec<(&String, &u32)> = dt_root_totals.iter().collect();
            roots.sort_by(|a, b| b.1.cmp(a.1).then_with(|| a.0.cmp(b.0)));
            let parts: Vec<String> = roots.iter().map(|(root, total)| {
                let mut nodes: Vec<(&String, &u32)> = dt_node_totals.iter()
                    .filter(|((r, _), _)| &r == root)
                    .map(|((_, n), c)| (n, c)).collect();
                nodes.sort_by(|a, b| b.1.cmp(a.1).then_with(|| a.0.cmp(b.0)));
                let breakdown: Vec<String> = nodes.iter().map(|(n, c)| format!("{} {}", n, c)).collect();
                format!("{} {} ({})", root, total, breakdown.join(" · "))
            }).collect();
            // 한 건도 못 맞히면 앞부분이 빈 문자열이 되어 "[summary][업무분류]  / 미분류 3"
            // 처럼 공백이 뜬다 — 값이 없다는 걸 말로 적어 준다(파이썬 판과 같은 문구).
            let head = if parts.is_empty() { "(분류된 문서 없음)".to_string() } else { parts.join(" / ") };
            eprintln!("[summary][업무분류] {} / 미분류 {}", head, dt_unclassified);
        }
    }

    // 본문을 못 읽은 문서가 하나라도 있으면 종료코드 1 로 끝낸다(계약상 3001).
    // 결과 파일은 정상적으로 만들어진다 — 실패한 문서도 레코드로 들어 있다.
    // [2026-09-01] 여기가 없어서 Rust 판은 추출에 실패해도 늘 0 을 냈다. 파이썬
    // 판·실행가이드는 1 을 약속하고 있었으므로, 배치가 두 판에서 다르게 굴렀다.
    std::process::exit(errcodes::finish(code, target.as_deref()));
}

/// json 배열 / jsonl / summary-only 를 CSOClassify 와 같은 형태로 렌더.
/// 전파 입력(1차 결과 파일)을 레코드 목록으로 읽는다.
///
/// 1차 분류는 형식이 세 가지로 나올 수 있다 — `--dir` 기본은 JSON 배열, `--file` 기본은
/// 객체 1개, `--format jsonl` 은 한 줄 1건. 어느 것이든 그대로 받아야 사용자가
/// "1차를 어떻게 냈는지" 신경 쓰지 않아도 된다(파이썬 run_propagate 와 같은 순서).
///   1) 파일 전체를 JSON 으로 파싱 → 배열이면 그대로, 객체면 1건
///   2) 실패하면 jsonl 로 폴백(깨진 줄은 건너뛰고 알린다)
/// 마지막 `{"summary":{...}}` 줄은 문서가 아니므로 걸러낸다 — 안 거르면 전파 통계가
/// 문서 1건을 더 센 것처럼 나온다.
fn load_propagate_input(path: &str) -> Vec<Value> {
    let raw = match std::fs::read_to_string(path) {
        Ok(s) => s,
        Err(e) => {
            errcodes::fail("propagate_input_missing",
                &format!("[csoclassify-rs] 전파 입력 파일을 읽을 수 없습니다 {}: {}", path, e),
                Some(path));
        }
    };
    let mut recs: Vec<Value> = vec![];
    match serde_json::from_str::<Value>(&raw) {
        Ok(Value::Array(a)) => recs = a,
        Ok(Value::Object(o)) => recs.push(Value::Object(o)),
        Ok(_) => {}
        Err(_) => {
            // jsonl 폴백.
            for (i, line) in raw.lines().enumerate() {
                let line = line.trim();
                if line.is_empty() { continue; }
                match serde_json::from_str::<Value>(line) {
                    Ok(v) => recs.push(v),
                    Err(e) => errlog::err(&format!("[csoclassify-rs] 전파 입력 {}행 파싱 실패: {}", i + 1, e)),
                }
            }
        }
    }
    // 요약 줄 제거 — 'file' 이 있는 것만 문서 레코드로 본다.
    recs.retain(|r| r.get("file").map_or(false, |f| f.is_string()));
    recs
}

/// 임베딩 라벨 전파 2차 패스(`--propagate`) — 파이썬 `run_propagate` 대응.
///
/// `--with-vector` 로 만든 1차 레코드(벡터 포함)를 읽어, 고신뢰 등급을 seed 삼아
/// 미분류 문서에 라벨을 전파하고 등급을 다시 융합해 출력한다.
/// **임베딩 모델이 필요 없다** — 이미 뽑힌 벡터로 벡터연산만 한다. 그래서 모델을
/// 못 올리는 서버에서도 1차 결과만 옮겨 와 전파를 돌릴 수 있다.
///
/// -in: opts = 파싱된 CLI 옵션(propagate=입력파일, seeds/out/fmt/failsafe/taxonomy/doc-rules)
///
/// -out: 종료코드(정상 0, 입력·seed 파일 문제 3, 축 검증 실패 4)
fn run_propagate(opts: &Opts) -> i32 {
    let input = opts.propagate.as_deref().unwrap_or("");
    if !Path::new(input).is_file() {
        errcodes::fail("propagate_input_missing",
            &format!("[csoclassify-rs] 전파 입력 파일이 없습니다: {}", input), Some(input));
    }
    let mut records = load_propagate_input(input);

    // 업무분류 축은 분류 때와 같은 규칙으로 켜고 --conflict 도 똑같이 적용한다.
    let mut dt_axis = match load_doctype_axis(opts) {
        Ok(v) => v,
        Err(code) => return code,
    };
    let doctype_strategy_override = apply_conflict_override(opts, &mut dt_axis);

    // 외부 seed. --seeds 를 '명시했는데' 파일이 없으면 조용히 내부 seed 로 넘어가지
    // 않고 멈춘다 — 기준을 지정했는데 다른 기준으로 판정되면 결과를 믿을 수 없다.
    let seeds_path: Option<String> = opts.seeds.clone().or_else(default_seed_path);
    if let Some(p) = &opts.seeds {
        if !Path::new(p).is_file() {
            errcodes::fail("seeds_missing",
                &format!("[csoclassify-rs] seed 파일이 없습니다: {}", p), Some(p));
        }
    }
    let external = match &seeds_path {
        Some(p) if Path::new(p).is_file() => {
            let idx = propagate::SeedIndex::from_seed_file(p);
            note!("[csoclassify-rs] 외부 seed {}건 로드: {}", idx.size(), p);
            idx
        }
        _ => propagate::SeedIndex::empty(),
    };

    // 내부 seed(입력 코퍼스 안의 seed_eligible 문서) + 외부 seed 병합.
    let tuples: Vec<(Option<Vec<f32>>, Option<String>, bool, String)> = records.iter()
        .map(|r| (
            r.get("vector").and_then(vector_from_json),
            r.get("grade").and_then(|g| g.as_str().map(|s| s.to_string())),
            r.get("seed_eligible").and_then(|b| b.as_bool()).unwrap_or(false),
            r.get("file").and_then(|f| f.as_str()).unwrap_or("").to_string(),
        ))
        .collect();
    let internal = propagate::SeedIndex::from_records(&tuples);
    let seeds = if external.size() > 0 {
        propagate::SeedIndex::merge(external, internal)
    } else {
        internal
    };

    // security 전파 — 이미 등급이 확정된 문서는 손대지 않는다(상향 전용, 과분류 방지).
    let (mut already, mut decided, mut still, mut novec) = (0u32, 0u32, 0u32, 0u32);
    for rec in records.iter_mut() {
        if rec.get("grade").map_or(false, |g| !g.is_null()) { already += 1; continue; }
        let vec = match rec.get("vector").and_then(vector_from_json) {
            Some(v) => v,
            None => { novec += 1; continue; }   // 보류인데 벡터도 없으면 전파 불가
        };
        let esig = propagate::propagate(&vec, &seeds);
        let dict = esig.as_dict();
        // signals 가 없는 옛 레코드도 받아들인다 — 없으면 embed 신호를 넣을 자리가 없다.
        if rec.get("signals").map_or(true, |s| !s.is_object()) {
            rec["signals"] = json!({});
        }
        let grade = rules::refuse_with_embed(rec, esig.grade.as_deref(), esig.confidence as f64,
                                             dict, opts.failsafe.as_deref());
        // labels.security 는 embed 가 섞인 최종 signals 로 다시 만들어야 한다.
        let label = security_label(rec);
        if rec.get("labels").map_or(true, |l| !l.is_object()) {
            rec["labels"] = json!({});
        }
        rec["labels"]["security"] = label;
        if rec.get("decided_by").and_then(|d| d.as_array())
              .map_or(false, |a| a.iter().any(|v| v.as_str() == Some("embed"))) {
            decided += 1;
        }
        if grade.is_none() { still += 1; }
    }

    // doctype 전파 — security 와 달리 '이미 후보가 있어도' embed 가 후보를 더한다
    // (다중 라벨은 상향이 아니라 추가가 원칙, 설계서 5-4).
    if let Some((taxonomy, drs)) = dt_axis.as_ref() {
        let mut dt_seeds = match &seeds_path {
            Some(p) => propagate::DoctypeSeedIndex::from_seed_file(p),
            None => propagate::DoctypeSeedIndex::empty(),
        };
        if dt_seeds.size() > 0 {
            note!("[csoclassify-rs] 업무분류 seed {}건 로드: {}",
                      dt_seeds.size(), seeds_path.clone().unwrap_or_default());
        }
        // 임계값·스위치는 정책 파일(embed: 블록)이 정한다. 1차 분류 경로와
        // 같은 값을 써야 "분류할 때와 다시 전파할 때 결과가 다른" 일이 없다.
        let dt_params = propagate::DoctypeParams {
            k: drs.embed.k as usize,
            dup_threshold: drs.embed.dup_threshold as f32,
            min_sim: drs.embed.min_sim as f32,
            min_share: drs.embed.min_share as f32,
        };
        if dt_seeds.size() > 0 && !drs.embed.enabled {
            note!("[propagate][업무분류] seed {}건이 있으나 doc_rule.yaml 의 \
embed.enabled 가 false 라 2단계를 건너뜁니다", dt_seeds.size());
            dt_seeds = propagate::DoctypeSeedIndex::empty();
        }
        let (mut contributed, mut dt_novec, mut axis_off) = (0u32, 0u32, 0u32);
        for rec in records.iter_mut() {
            // labels.doctype 이 없으면 그 축을 안 쓴 배포의 레코드다 — 건너뛴다.
            let existing = match rec.pointer("/labels/doctype/values") {
                Some(v) => candidates_from_json(v),
                None => { axis_off += 1; continue; }
            };
            let vec = match rec.get("vector").and_then(vector_from_json) {
                Some(v) => v,
                None => { dt_novec += 1; continue; }
            };
            let esig = propagate::propagate_doctype(&vec, &dt_seeds, dt_params);
            if let Some(sigs) = rec.get_mut("signals").and_then(|s| s.as_object_mut()) {
                sigs.insert("doctype_embed".into(), esig.as_dict());
            }
            let embed_values: Vec<(String, f64)> = esig.values.iter()
                .map(|(id, c)| (id.clone(), *c as f64)).collect();
            let merged = doctype::merge_embed_candidates(&existing, &embed_values, taxonomy, &drs.conflict,
                                                       Some(drs.defaults.embed_cap));
            if merged.values.iter().any(|v| v.from.iter().any(|f| f == "embed")) {
                contributed += 1;
            }
            let mut d = merged.as_dict();
            if let Some(ov) = &doctype_strategy_override {
                d["strategy"] = json!(ov);
            }
            rec["labels"]["doctype"] = d;
        }
        note!("[propagate][업무분류] seeds={} embed_contributed={} no_vector={} axis_off={}",
                  dt_seeds.size(), contributed, dt_novec, axis_off);
    }

    // 출력 — 전파 입력은 '레코드 묶음'이라 요약을 붙이지 않는다(파이썬과 동일).
    // json 은 항상 배열로 감싸 유효한 JSON 파일이 되게 한다.
    let records: Vec<Value> = records.iter().map(order_record).collect();
    let body = if opts.fmt == "jsonl" {
        let mut out = String::new();
        for r in &records {
            out.push_str(&serde_json::to_string(r).unwrap());
            out.push('\n');
        }
        out
    } else {
        format!("{}\n", serde_json::to_string_pretty(&records).unwrap())
    };
    match &opts.out {
        Some(p) => {
            if let Err(e) = std::fs::write(p, &body) {
                errcodes::fail("output_write_failed",
                    &format!("[csoclassify-rs] 출력 저장 실패 {}: {}", p, e), Some(p));
            }
        }
        None => print!("{}", body),
    }

    note!("[propagate] seeds={} already_graded={} embed_decided={} still_unclassified={} no_vector={}",
              seeds.size(), already, decided, still, novec);
    0
}

/// 레코드의 `vector` 필드(JSON 숫자 배열) → `Vec<f32>`.
/// 배열이 아니거나 비어 있으면 None — '벡터 없음'과 같이 취급해 전파에서 건너뛴다.
fn vector_from_json(v: &Value) -> Option<Vec<f32>> {
    let arr = v.as_array()?;
    if arr.is_empty() { return None; }
    let mut out = Vec::with_capacity(arr.len());
    for x in arr {
        out.push(x.as_f64()? as f32);
    }
    Some(out)
}

/// `labels.doctype.values`(JSON) → 병합에 넣을 후보 목록.
/// 형식이 어긋난 원소는 조용히 버린다 — 손으로 고친 결과 파일이 들어와도
/// 전파 자체가 죽지는 않아야 한다.
fn candidates_from_json(v: &Value) -> Vec<doctype::Candidate> {
    let arr = match v.as_array() { Some(a) => a, None => return vec![] };
    let mut out = vec![];
    for c in arr {
        let dc_id = match c.get("dc_id").and_then(|x| x.as_str()) { Some(s) => s.to_string(), None => continue };
        out.push(doctype::Candidate {
            dc_id,
            path: c.get("path").and_then(|x| x.as_str()).unwrap_or("").to_string(),
            path_ids: c.get("path_ids").and_then(|x| x.as_array())
                .map(|a| a.iter().filter_map(|s| s.as_str().map(|s| s.to_string())).collect())
                .unwrap_or_default(),
            confidence: c.get("confidence").and_then(|x| x.as_f64()).unwrap_or(0.0),
            from: c.get("from").and_then(|x| x.as_array())
                .map(|a| a.iter().filter_map(|s| s.as_str().map(|s| s.to_string())).collect())
                .unwrap_or_default(),
            // 근거 블록은 왕복에서 살려 둔다 — 여기서 버리면 전파를 한 번
            // 거친 문서만 "왜 이 라벨인지"를 설명할 수 없게 된다.
            stage: c.get("stage").and_then(|x| x.as_str()).unwrap_or("rule").to_string(),
            evidence: c.get("evidence").and_then(|x| x.as_object()).cloned()
                .unwrap_or_default(),
            score_parts: c.get("score_parts").and_then(|x| x.as_array())
                .map(|a| a.iter().filter_map(|p| {
                    let sig = p.get("signal")?.as_str()?.to_string();
                    let cf = p.get("c")?.as_f64()?;
                    Some((sig, cf))
                }).collect())
                .unwrap_or_default(),
        });
    }
    out
}

/// `--conflict` 로 축 전략을 실행 시 덮어쓴다(6-5). security 는 서열 축이라 금지(T11).
///
/// 분류 모드와 전파 모드가 같은 규칙으로 움직여야 하므로 함수로 뺐다 — 한쪽에만
/// 고쳐 넣으면 "같은 --conflict 를 줬는데 전파에서만 안 먹는" 상황이 생긴다.
/// 반환값(적용된 전략 문자열)은 결과 레코드에 그대로 남긴다: 같은 규칙셋인데
/// 결과가 다르면 왜 달랐는지 레코드만 보고 알 수 있어야 하기 때문이다.
fn apply_conflict_override(opts: &Opts, dt_axis: &mut Option<(Taxonomy, DocRuleSet)>) -> Option<String> {
    let spec = opts.conflict.as_ref()?;
    let (axis, parsed) = match parse_conflict_override(spec) {
        Ok(v) => v,
        Err(msg) => errcodes::fail("bad_conflict_axis",
            &format!("[csoclassify-rs] {}", msg), None),
    };
    if let Err(msg) = conflict::ensure_overridable_axis(&axis) {
        errcodes::fail("bad_conflict_axis", &format!("[csoclassify-rs] {}", msg), None);
    }
    if axis != "doctype" {
        errcodes::fail("bad_conflict_axis",
            &format!("[csoclassify-rs] --conflict 에 알 수 없는 축입니다: {:?}", axis), None);
    }
    if let Some((_, drs)) = dt_axis.as_mut() {
        drs.conflict = parsed;
    }
    spec.split_once('=').map(|(_, v)| v.trim().to_string())
}

fn render(records: &[Value], summary: &Value, opts: &Opts, status: Option<&Value>) -> String {
    if opts.summary_only {
        // 단일 요약 객체
        return format!("{}\n", serde_json::to_string_pretty(&json!({"summary": summary})).unwrap());
    }
    if opts.fmt == "jsonl" {
        let mut out = String::new();
        for r in records {
            out.push_str(&serde_json::to_string(r).unwrap());
            out.push('\n');
        }
        if !opts.no_summary {
            out.push_str(&serde_json::to_string(&json!({"summary": summary})).unwrap());
            out.push('\n');
        }
        if let Some(st) = status {
            out.push_str(&serde_json::to_string(st).unwrap());
            out.push('\n');
        }
        out
    } else {
        // json 배열(들여쓰기), 마지막에 summary 원소
        let mut arr: Vec<Value> = records.to_vec();
        if !opts.no_summary {
            arr.push(json!({"summary": summary}));
        }
        // 배열 '밖'에 객체를 붙이면 파일이 통째로 깨진다 — 마지막 원소로 넣는다.
        if let Some(st) = status {
            arr.push(st.clone());
        }
        format!("{}\n", serde_json::to_string_pretty(&arr).unwrap())
    }
}

fn round1(x: f64) -> f64 { (x * 10.0).round() / 10.0 }

#[cfg(test)]
mod tests {
    use super::*;
    use doctype::{Candidate, DoctypeSignal};

    fn sig(values: Vec<Candidate>) -> DoctypeSignal {
        DoctypeSignal { values, strategy: "all".into(), status: "proposed".into(),
                        truncated: 0, conflicts: vec![] }
    }

    fn cand(dc_id: &str, path: &str) -> Candidate {
        Candidate::bare(dc_id.into(), path.into(), vec![], 0.7, vec![])
    }

    // doc_rule.yaml 이 없을 때 쓰는 빈 규칙셋 — 축을 끄는 것과는 다른 상태다.
    #[test]
    fn seed전용_규칙셋은_규칙0건이지만_켜진_축이다() {
        let drs = DocRuleSet::seed_only();
        assert!(drs.rules.is_empty() && drs.active_rules().is_empty());
        // version "none" 이 "규칙 없이 전파로만 돌았다"의 표식이다(결과에도 각인된다).
        assert_eq!(drs.version, "none");
        // 전략은 기본값 all — 전파가 붙인 라벨을 임의로 잘라내지 않는다.
        assert_eq!(drs.conflict.strategy, "all");
        assert!(drs.conflict.n.is_none());
    }


    #[test]
    fn security_label은_등급있는_신호만_후보로_담는다() {
        let rec = json!({
            "grade": "S", "confidence": 0.7, "method": "fusion", "decided_by": ["rule"],
            "signals": {
                "rule": {"grade": "S", "confidence": 0.7},
                "path": {"grade": Value::Null, "confidence": 0.0},
                "name": {"grade": "O", "confidence": 0.6},
            }
        });
        let label = security_label(&rec);
        assert_eq!(label["value"], json!("S"));
        assert_eq!(label["strategy"], json!("max"));
        let froms: Vec<&str> = label["candidates"].as_array().unwrap().iter()
            .map(|c| c["from"].as_str().unwrap()).collect();
        // Python 판과 같은 나열 순서(rule→sensitive→stamp→path→name→embed).
        assert_eq!(froms, vec!["rule", "name"]);
    }

    #[test]
    fn security_label은_보류_레코드도_형태를_지킨다() {
        let rec = json!({"grade": Value::Null, "confidence": 0.0, "method": "unclassified",
                         "decided_by": [], "signals": {}});
        let label = security_label(&rec);
        assert_eq!(label["value"], Value::Null);
        assert_eq!(label["candidates"], json!([]));
    }

    #[test]
    fn doctype_breakdown은_뿌리와_말단만_뽑는다() {
        let pairs = doctype_breakdown(&sig(vec![cand("DC_1", "기술/개발 > 설계문서 > 요구사항정의서")]));
        assert_eq!(pairs, vec![("기술/개발".to_string(), "요구사항정의서".to_string())]);
    }

    #[test]
    fn doctype_breakdown은_경로가_비면_dc_id로_대체() {
        let pairs = doctype_breakdown(&sig(vec![cand("DC_9", "")]));
        assert_eq!(pairs, vec![("DC_9".to_string(), "DC_9".to_string())]);
    }

    #[test]
    fn conflict_덮어쓰기_파싱() {
        let (axis, spec) = parse_conflict_override("doctype=top_n:3").unwrap();
        assert_eq!(axis, "doctype");
        assert_eq!(spec.strategy, "top_n");
        assert_eq!(spec.n, Some(3));
        let (_, spec2) = parse_conflict_override("doctype=all").unwrap();
        assert_eq!(spec2.strategy, "all");
    }

    #[test]
    fn conflict_덮어쓰기_잘못된_값은_오류() {
        assert!(parse_conflict_override("doctype").is_err());       // '=' 없음
        assert!(parse_conflict_override("doctype=max").is_err());   // T2
        assert!(parse_conflict_override("doctype=top_n").is_err()); // T10(n 없음)
        assert!(parse_conflict_override("doctype=top_n:x").is_err());
    }

    #[test]
    fn 그레고리력_일수_변환() {
        assert_eq!(days_from_civil(1970, 1, 1), 0);
        assert_eq!(days_from_civil(1970, 1, 2), 1);
        assert_eq!(days_from_civil(2000, 3, 1), 11017);
    }

    // ---- --propagate 입력 파서 ----

    #[test]
    fn 전파입력은_요약줄을_문서로_세지_않는다() {
        // 1차 결과 jsonl 의 마지막 {"summary":{...}} 줄은 문서가 아니다. 이걸 문서로
        // 세면 전파 통계가 한 건 더 있는 것처럼 나오고, 결과 파일에도 '내용 없는
        // 문서' 한 줄이 따라다닌다(화면에서 빈 행으로 보이던 그 문제).
        let dir = std::env::temp_dir().join("csors_prop_test");
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("in.jsonl");
        std::fs::write(&p, "{\"file\":\"a.txt\",\"grade\":\"C\"}
{\"summary\":{\"total\":1}}
").unwrap();
        let recs = load_propagate_input(p.to_str().unwrap());
        assert_eq!(recs.len(), 1);
        assert_eq!(recs[0]["file"], json!("a.txt"));
    }

    #[test]
    fn 전파입력은_json배열도_객체하나도_받는다() {
        let dir = std::env::temp_dir().join("csors_prop_test");
        std::fs::create_dir_all(&dir).unwrap();
        // --dir 기본 출력(JSON 배열, 끝에 summary 원소)
        let a = dir.join("arr.json");
        std::fs::write(&a, "[{\"file\":\"a\"},{\"file\":\"b\"},{\"summary\":{}}]").unwrap();
        assert_eq!(load_propagate_input(a.to_str().unwrap()).len(), 2);
        // --file 기본 출력(객체 1개)
        let o = dir.join("one.json");
        std::fs::write(&o, "{\"file\":\"a\",\"grade\":null}").unwrap();
        assert_eq!(load_propagate_input(o.to_str().unwrap()).len(), 1);
    }

    #[test]
    fn vector_from_json은_빈배열과_비배열을_없음으로_본다() {
        assert_eq!(vector_from_json(&json!([0.5, -0.25])), Some(vec![0.5f32, -0.25f32]));
        // 빈 배열은 '0차원 벡터'가 아니라 '벡터 없음'으로 다뤄야 전파에서 건너뛴다.
        assert_eq!(vector_from_json(&json!([])), None);
        assert_eq!(vector_from_json(&json!(null)), None);
        assert_eq!(vector_from_json(&json!("0.5")), None);
        // 숫자가 아닌 원소가 섞이면 통째로 버린다(반쪽 벡터로 비교하면 안 된다).
        assert_eq!(vector_from_json(&json!([0.1, "x"])), None);
    }

    #[test]
    fn candidates_from_json은_형식이_틀린_원소를_버린다() {
        let v = json!([
            {"dc_id":"DC_1","path":"A > B","path_ids":["DC_0","DC_1"],"confidence":0.7,"from":["rule"]},
            {"path":"dc_id 없음"},
            {"dc_id":"DC_2"}
        ]);
        let out = candidates_from_json(&v);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].dc_id, "DC_1");
        assert_eq!(out[0].path_ids, vec!["DC_0", "DC_1"]);
        assert_eq!(out[0].from, vec!["rule"]);
        // 빠진 항목은 기본값으로 채운다 — 손으로 고친 결과 파일이 와도 죽지 않아야 한다.
        assert_eq!(out[1].dc_id, "DC_2");
        assert_eq!(out[1].confidence, 0.0);
        assert!(out[1].from.is_empty());
    }

    #[test]
    fn out_확장자로_형식을_추측한다() {
        // "--out result.jsonl 인데 안에는 JSON 배열"이라는 함정을 없애려는 것이다.
        assert_eq!(guess_format(&Some("result.jsonl".into())), Some("jsonl".into()));
        assert_eq!(guess_format(&Some("result.ndjson".into())), Some("jsonl".into()));
        assert_eq!(guess_format(&Some("result.json".into())), Some("json".into()));
        // 대소문자 무시 — 윈도우에서 .JSONL 로 적는 경우가 있다.
        assert_eq!(guess_format(&Some("D:/out/RESULT.JSONL".into())), Some("jsonl".into()));
        // 모르는 확장자·확장자 없음·--out 없음 → 기본값을 그대로 쓰게 None.
        assert_eq!(guess_format(&Some("result.csv".into())), None);
        assert_eq!(guess_format(&Some("result".into())), None);
        assert_eq!(guess_format(&None), None);
        // 경로에 점이 있어도 파일명 확장자만 본다.
        assert_eq!(guess_format(&Some("D:/a.b.c/result.jsonl".into())), Some("jsonl".into()));
    }
}
