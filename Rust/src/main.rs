//! csoclassify-rs — CSOClassify rule-only 분류 코어의 Rust 포팅 (PoC).
//! 추출(text/html/ooxml) + 규칙 분류(PII·키워드·민감·스탬프·경로·파일명·융합) + CLI.

mod axes;
mod conflict;
mod detect;
mod districts;
mod doc_rules;
mod doctype;
mod embed;
mod errlog;
mod extract;
mod hwp5;
mod office_legacy;
mod ole;
mod pii;
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

struct Opts {
    file: Option<String>,
    dir: Option<String>,
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
    with_vector: bool,      // 모든 문서를 임베딩해 결과 레코드에 vector 필드로 실어 보냄
    propagate: Option<String>,  // 1차 결과(jsonl/json)를 읽어 전파만 다시 도는 2차 패스
    auto_propagate: bool,
    seeds: Option<String>,
    failsafe: Option<String>,
    check_rules: bool,      // 규칙셋만 검사하고 종료(문서는 읽지 않음)
}

fn usage() {
    eprintln!("csoclassify-rs — 추출 + 규칙 분류(rule-only) [Rust PoC]");
    eprintln!("사용법:");
    eprintln!("  csoclassify-rs --file <파일> [옵션]");
    eprintln!("  csoclassify-rs --dir  <폴더> [옵션]   (재귀)");
    eprintln!("옵션:");
    eprintln!("  --rules <cso_rules.yaml>  규칙셋(미지정 시 exe 옆/CSOCLASSIFY_POLICY_DIR)");
    eprintln!("  --taxonomy <doc_taxonomy.yaml>  업무분류 체계 스냅샷(없으면 업무분류 축을 끔)");
    eprintln!("  --doc-rules <doc_rule.yaml>     업무분류 규칙셋(없으면 업무분류 축을 끔)");
    eprintln!("  --axis security|doctype   이번 실행에 쓸 축만 지정(doctype 이면 보안등급 계산 생략)");
    eprintln!("  --conflict <축>=<전략>    업무분류 축 전략 덮어쓰기(예: doctype=top_n:3, doctype=all)");
    eprintln!("  --export-taxonomy         DOC_CLASSIFICATION JSON → --taxonomy 경로에 스냅샷 생성 후 종료(문서 안 읽음)");
    eprintln!("  --export-input <파일>     --export-taxonomy 의 원본 JSON(미지정 시 exe 옆 doc_classification_export.json)");
    eprintln!("  --scaffold-doc-rule       --export-taxonomy 와 함께 쓰면 --doc-rules 경로에 규칙 골격도 생성(이미 있으면 건너뜀)");
    // 중괄호는 포맷 자리표시자로 읽히므로 {{ }} 로 escape 한다.
    eprintln!("  --glob <패턴>             --dir 에서 고를 파일 패턴(예: \"*.hwp,*.pdf\" · \"*.{{hwp,pdf}}\")");
    eprintln!("  --format json|jsonl       출력 형식(미지정 시 --out 확장자로 판단, 그것도 없으면 json 배열)");
    eprintln!("  --out <파일>              결과 저장(미지정 시 stdout)");
    eprintln!("  --simple                  파일별 문서명·등급·해시 3필드만");
    eprintln!("  --hash                    각 문서 SHA-256 포함");
    eprintln!("  --with-pii                [프라이버시 예외] 검출된 원문 PII 값 포함(pii 필드)");
    eprintln!("  --rule-only               규칙만으로 분류 — 임베딩·전파 없음(가장 빠름). --vector-only 와 배타");
    eprintln!("  --vector-only             규칙 없이 임베딩 벡터를 seed 와 비교해서만 분류(--seeds 필수). --rule-only 와 배타");
    eprintln!("  --with-vector             모든 문서를 임베딩해 결과에 vector 필드(384차원)로 포함(RAG 등 전량 벡터가 필요할 때)");
    eprintln!("  --propagate <결과파일>    1차 결과(jsonl/json)를 읽어 전파만 다시 수행(문서·모델 불필요). --file/--dir 대신 씀");
    eprintln!("  --auto-propagate          보류 문서를 seed 로 전파(seed 있으면 기본 on)");
    eprintln!("  --seeds <class_seed.jsonl>  전파 비교 기준 seed 저장소(미지정 시 exe 옆 class_seed.jsonl)");
    eprintln!("  --summary                 요약만 출력");
    eprintln!("  --nosummary               요약 제거(파일별만)");
    eprintln!("  --failsafe [등급]         무신호 기본등급(예: S)");
    eprintln!("  --check-rules             규칙셋의 등급 값만 검사하고 종료(정상 0, 검증 실패 4)");
}

fn parse_args() -> Result<Opts, String> {
    let mut o = Opts {
        file: None, dir: None, rules_path: None,
        taxonomy: None, doc_rules: None, axis: None, conflict: None,
        export_taxonomy: false, export_input: None, scaffold_doc_rule: false,
        glob: None, out: None,
        fmt: "json".into(), fmt_explicit: false, simple: false, summary_only: false,
        no_summary: false, hash: false, with_pii: false,
        rule_only: false, vector_only: false, with_vector: false, propagate: None,
        auto_propagate: false, seeds: None,
        failsafe: None, check_rules: false,
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
        let mut next = || { i += 1; args.get(i).cloned().unwrap_or_default() };
        match a.as_str() {
            "--file" | "-file" => o.file = Some(next()),
            "--dir" | "-dir" => o.dir = Some(next()),
            "--rules" => o.rules_path = Some(next()),
            "--taxonomy" => o.taxonomy = Some(next()),
            "--doc-rules" => o.doc_rules = Some(next()),
            "--axis" => o.axis = Some(next()),
            "--conflict" => o.conflict = Some(next()),
            "--glob" => o.glob = Some(next()),
            "--export-taxonomy" => o.export_taxonomy = true,
            "--export-input" => o.export_input = Some(next()),
            "--scaffold-doc-rule" => o.scaffold_doc_rule = true,
            "--out" => o.out = Some(next()),
            "--format" => { o.fmt = next(); o.fmt_explicit = true; }
            "--simple" => o.simple = true,
            "--summary" => o.summary_only = true,
            "--nosummary" => o.no_summary = true,
            "--hash" => o.hash = true,
            "--with-pii" => o.with_pii = true,
            "--rule-only" => o.rule_only = true,
            "--vector-only" => o.vector_only = true,
            "--with-vector" => o.with_vector = true,
            "--propagate" => o.propagate = Some(next()),
            "--auto-propagate" => o.auto_propagate = true,
            "--seeds" => o.seeds = Some(next()),
            "--failsafe" => o.failsafe = Some("S".into()),
            "--check-rules" => o.check_rules = true,
            "--hybridparse" => {} // 추출은 항상 내용감지 라우팅
            "-h" | "--help" => { usage(); std::process::exit(0); }
            _ => errlog::err(&format!("[warn] 알 수 없는 인자: {}", a)),
        }
        i += 1;
    }
    // --axis 는 오타가 조용히 "축 전체 무시"로 이어지면 안 되므로 여기서 막는다.
    if let Some(a) = &o.axis {
        if a != "security" && a != "doctype" {
            return Err(format!("--axis 는 security 또는 doctype 이어야 합니다: {:?}", a));
        }
    }
    // --format 을 안 줬으면 --out 확장자로 형식을 정한다.
    if !o.fmt_explicit {
        if let Some(g) = guess_format(&o.out) {
            if g != o.fmt {
                eprintln!("[csoclassify-rs] --out 확장자에 맞춰 --format {} 로 저장합니다. \
                           (다르게 하려면 --format 을 직접 지정하세요)", g);
            }
            o.fmt = g;
        }
    }
    Ok(o)
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

/// 정책 파일 경로 결정: 명시 인자 → exe 옆 → CSOCLASSIFY_POLICY_DIR.
/// 명시 인자는 파일이 없어도 그대로 돌려준다 — "지정했는데 없다"는 조용히
/// 다른 파일로 대체되면 안 되고, 로더가 그 경로로 실패해 알려 줘야 하기 때문이다.
fn resolve_policy_file(explicit: &Option<String>, filename: &str) -> Option<PathBuf> {
    if let Some(p) = explicit {
        return Some(PathBuf::from(p));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let p = dir.join(filename);
            if p.is_file() { return Some(p); }
        }
    }
    if let Ok(d) = std::env::var("CSOCLASSIFY_POLICY_DIR") {
        let p = Path::new(&d).join(filename);
        if p.is_file() { return Some(p); }
    }
    None
}

/// 규칙셋 경로 결정: --rules → exe 옆 → CSOCLASSIFY_POLICY_DIR.
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
                errlog::err(&format!("[csoclassify-rs] 분류체계 스냅샷을 찾을 수 없습니다: {}", shown));
                return Err(4);
            }
            eprintln!("[csoclassify-rs] doc_taxonomy.yaml 이 없어 업무분류(doctype) 축을 건너뜁니다.");
            eprintln!("                 보안등급(security)만 판정합니다.");
            return Ok(None);
        }
    };
    let taxonomy = match axes::load_taxonomy(&tpath) {
        Ok(t) => t,
        Err(e) => { errlog::err(&format!("[csoclassify-rs] {}", e)); return Err(4); }
    };
    if let Some(msg) = check_stale_taxonomy(&taxonomy, 90) {
        eprintln!("{}", msg);
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
            eprintln!("[csoclassify-rs] doc_rule.yaml 이 없어 업무분류(doctype)를 'seed 전파 전용'으로 돌립니다.");
            eprintln!("                 규칙 대신 class_seed.jsonl 과의 임베딩 유사도로만 분류합니다(seed 도 없으면 전부 미분류).");
            eprintln!("                 {}", where_);
            return Ok(Some((taxonomy, DocRuleSet::seed_only())));
        }
    };
    let drs = match doc_rules::load_doc_rules(&rpath, Some(&taxonomy)) {
        Ok(d) => d,
        Err(e) => { errlog::err(&format!("[csoclassify-rs] {}", e)); return Err(4); }
    };
    for w in &drs.warnings {
        eprintln!("[csoclassify-rs] {}", w);
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
fn run_export_taxonomy(opts: &Opts) -> i32 {
    // 두 경로 모두 다른 정책 파일과 같은 규약으로 찾는다(인자 → exe 옆 → 환경변수).
    // 단 출력 경로는 '아직 없는 파일'을 만드는 자리라 is_file() 로 거르면 안 된다.
    let input = match resolve_policy_file(&opts.export_input, axes::default_export_input_filename()) {
        Some(p) if p.is_file() => p,
        other => {
            let shown = other.map(|p| p.display().to_string())
                .unwrap_or_else(|| axes::default_export_input_filename().into());
            errlog::err(&format!("[csoclassify-rs] 원본 JSON을 찾을 수 없습니다: {}", shown));
            eprintln!("  · --export-input <파일경로> 로 지정하거나,");
            eprintln!("  · exe 옆(또는 CSOCLASSIFY_POLICY_DIR)에 {} 를 두세요.",
                      axes::default_export_input_filename());
            return 3;
        }
    };
    let output = match &opts.taxonomy {
        Some(p) => PathBuf::from(p),
        // 미지정이면 exe 옆에 만든다 — 분류할 때 찾는 자리와 같아야 바로 쓰인다.
        None => policy_dir_for_new_file().join(axes::default_taxonomy_filename()),
    };

    let (taxonomy, warnings) = match axes::export_from_mpower_json(&input, &output) {
        Ok(v) => v,
        Err(msg) => {
            eprintln!("[csoclassify-rs] {}", msg);
            // 검증 실패는 '내용이 틀림'(4), 나머지는 '입력을 못 씀'(3)으로 나눈다.
            return if msg.contains("검증을 통과하지 못했습니다") { 4 } else { 3 };
        }
    };

    for w in &warnings {
        eprintln!("[csoclassify-rs] 경고: {}", w);
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
            Ok(None) => eprintln!("[csoclassify-rs] {} 이 이미 있어 골격 생성을 건너뜁니다\
(사람이 채운 내용을 덮어쓰지 않기 위함).", rpath.display()),
            Err(msg) => {
                errlog::err(&format!("[csoclassify-rs] 골격 생성 실패: {}", msg));
                return 3;
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

/// 대상 파일 수집: --file 하나, --dir 재귀.
fn collect_files(opts: &Opts) -> Vec<PathBuf> {
    if let Some(f) = &opts.file {
        return vec![PathBuf::from(f)];
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
                None => errlog::fail("embed 실패(빈 텍스트?)", 1),
            },
            None => errlog::fail("모델/런타임 로드 실패(models/e5-small-ko, onnxruntime.dll 확인)", 1),
        }
        return;
    }

    let opts = match parse_args() { Ok(o) => o, Err(e) => errlog::fail(&e, 2) };
    let t0 = Instant::now();

    // 분류체계 내보내기 전용 모드 — 문서도 규칙셋도 필요 없다. 규칙셋을 먼저 읽는
    // 아래 흐름을 타면 "cso_rules.yaml 이 없다"고 엉뚱한 곳에서 멈추고, 만들려던
    // doc_taxonomy.yaml 이 아직 없다는 이유로 축 로드에서 또 걸린다 — 그래서 여기서 끝낸다.
    if opts.export_taxonomy {
        std::process::exit(run_export_taxonomy(&opts));
    }

    // 전파 전용 모드 — 입력이 1차 '레코드 파일'이라 문서도 규칙셋도 모델도 필요 없다.
    // 규칙셋을 먼저 읽는 아래 흐름을 타면 cso_rules.yaml 이 없다는 이유로 엉뚱하게
    // 멈추므로, 규칙셋 로드 전에 끝낸다(파이썬 cli.py 의 (1.5) 와 같은 자리).
    if opts.propagate.is_some() {
        std::process::exit(run_propagate(&opts));
    }

    let rules_path = match resolve_rules(&opts) {
        Some(p) => p,
        None => errlog::fail("[csoclassify-rs] 규칙셋(cso_rules.yaml)을 찾을 수 없습니다. --rules 로 지정하세요.", 2),
    };
    let rs: RuleSet = match load_rules(&rules_path) {
        Ok(rs) => rs,
        // 파일은 있는데 등급 값이 틀린 경우만 코드 4 로 나눠, 배치가 "파일 없음"과
        // "내용 오류"에 다르게 대응할 수 있게 한다(Python 판 EXIT_RULES_INVALID 와 동일).
        Err(rules::RulesError::Invalid(msg)) => errlog::fail(&format!("[csoclassify-rs] {}", msg), 4),
        Err(e) => errlog::fail(&format!("[csoclassify-rs] {}", e), 2),
    };

    // 업무분류(doctype) 축 — 있으면 켜고 없으면 security 만(4-6). --axis doctype 이면 필수.
    let mut dt_axis = match load_doctype_axis(&opts) {
        Ok(v) => v,
        Err(code) => std::process::exit(code),
    };

    let doctype_strategy_override = apply_conflict_override(&opts, &mut dt_axis);

    // 규칙셋 검사 모드: 여기까지 왔다는 것은 검증을 통과했다는 뜻 → 요약만 알리고 종료.
    // 문서·모델이 필요 없으므로 파일 수집 전에 끝낸다.
    if opts.check_rules {
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

    let files = collect_files(&opts);
    if files.is_empty() {
        errlog::fail("[csoclassify-rs] 처리할 파일이 없습니다. --file 또는 --dir 지정.", 2);
    }

    // 분류 모드 결정(ko-pii cli.py) — 배타: 벡터만 / 규칙만 / 기본(규칙+자동전파).
    let seeds_path: Option<String> = opts.seeds.clone().or_else(default_seed_path);
    let seed_exists = seeds_path.as_deref().map(|p| Path::new(p).is_file()).unwrap_or(false);
    let mut auto_prop = opts.auto_propagate;
    let (mut rules_enabled, embed_mode): (bool, &str) = if opts.vector_only {
        if !seed_exists {
            errlog::fail("[csoclassify-rs] --vector-only 는 비교 기준 seed 파일이 필요합니다: --seeds <class_seed.jsonl>(또는 exe 옆)", 3);
        }
        auto_prop = true;
        (false, "all")
    } else if opts.rule_only {
        auto_prop = false;
        (true, "none")
    } else {
        // 기본: seed 가 있으면 자동 전파 on(명시 --auto-propagate 없이도).
        if !auto_prop && seed_exists { auto_prop = true; }
        // 임베딩 범위: --with-vector 는 '전량'(레코드에 벡터를 실어야 하므로 확정 문서도
        // 빠뜨리면 안 된다) > 전파용 'needed'(못 정한 문서만) > 'none'.
        // 전파가 켜져 있어도 --with-vector 면 all 이 이긴다 — needed 로 내리면 등급이
        // 이미 확정된 문서에 벡터가 안 실려 "전량 벡터"라는 약속이 깨진다(cli.py 와 동일).
        let m = if opts.with_vector { "all" } else if auto_prop { "needed" } else { "none" };
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
            eprintln!("[csoclassify-rs] 업무분류: 규칙(doc_rule.yaml)도 없고 {} 전파도 못 합니다 \
— 전부 미분류로 두니 관리자가 분류한 뒤 seed 로 승격하세요.", why);
        }
    }

    let need_hash = opts.hash || opts.simple;
    let mut fail = 0u32;

    // 1차: 추출 + (규칙) 분류 → 아이템 수집(전파 위해 text/seed_eligible/vector 보관).
    struct Item { rec: Value, grade: Option<Grade>, text: String, seed_eligible: bool,
                  vector: Option<Vec<f32>>, file: String, dt: Option<doctype::DoctypeSignal> }
    let mut items: Vec<Item> = vec![];
    for path in &files {
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
        if need_hash { rec["hash"] = json!(file_hash(path)); }
        if opts.with_pii { rec["pii"] = json!(rules::collect_pii(&text, &rs)); }
        // 업무분류 축은 security 모드(rule-only/vector-only)와 무관하게 독립적으로 돈다(6-4).
        let dt = dt_axis.as_ref().map(|(taxonomy, drs)| doctype::scan_doctype(&text, &display, drs, taxonomy));
        let seed_elig = rec.get("seed_eligible").and_then(|b| b.as_bool()).unwrap_or(false);
        items.push(Item { rec, grade, text, seed_eligible: seed_elig, vector: None, file: display, dt });
    }

    // 2차: 임베딩 → (auto_prop 이면) seed 비교 → 보류 문서 재융합.
    //   임베딩 자체는 전파와 분리해서 돌린다 — --with-vector 는 전파를 안 하더라도
    //   레코드에 벡터를 실어야 하므로, 전파 조건에 묶어 두면 벡터가 통째로 비게 된다.
    let mut prop_stats: Option<(usize, u32, u32)> = None;
    if embed_mode != "none" {
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
                        eprintln!("[전파][업무분류] seed {}건이 있으나 doc_rule.yaml 의 \
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
                        eprintln!("[전파][업무분류] seed={} embed기여={}", dt_seeds.size(), contributed);
                    }
                    // seed 가 없는 경우의 안내는 위(모드 결정 직후)에서 이미 냈다 — 그쪽은
                    // auto_prop 이 아예 꺼진 경우까지 잡아 주므로 여기서 또 내지 않는다.
                }
                } // if auto_prop
            }
            None => errlog::err("[csoclassify-rs] 임베딩 모델/런타임 로드 실패 → 임베딩·전파 생략(models/e5-small-ko, onnxruntime.dll 확인)."),
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

        // --with-vector: 문서벡터를 레코드에 실어 보낸다(cli.py 의 rec["vector"] 와 같은 형식).
        //   f32 를 f64 로 넓혀 담는다 — 값 자체는 f32 그대로라 손실이 없고,
        //   파이썬 쪽 numpy float32 → float 변환과 같은 수를 낸다.
        //   모델 로드에 실패했거나 추출 텍스트가 비어 벡터가 없으면 필드를 아예 넣지 않는다
        //   ('벡터 없음'을 빈 배열로 적으면 0차원 벡터와 구분이 안 된다).
        if opts.with_vector {
            if let Some(v) = &it.vector {
                rec["vector"] = Value::Array(v.iter().map(|x| json!(*x as f64)).collect());
            }
        }

        if opts.summary_only { continue; }
        let out_rec = if opts.simple {
            json!({"file": rec["file"], "grade": rec["grade"], "hash": rec.get("hash").cloned().unwrap_or(Value::Null)})
        } else { rec };
        records.push(out_rec);
    }
    if let Some((seeds_n, decided, still)) = prop_stats {
        eprintln!("[전파] seed={} 전파결정={} 미분류잔여={}", seeds_n, decided, still);
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

    // 출력 조립
    let body = render(&records, &summary, &opts);
    match &opts.out {
        Some(p) => {
            if let Err(e) = std::fs::write(p, &body) {
                errlog::fail(&format!("[csoclassify-rs] 출력 저장 실패 {}: {}", p, e), 1);
            }
        }
        None => { print!("{}", body); }
    }

    // 화면 요약(stderr)
    if !opts.no_summary {
        eprintln!("[summary] 총 {}개 / 검출 {}, C={} S={} O={} 미분류={} 추출실패={}, 총시간={}ms",
            total, detected, c, s_, o_, none, fail, total_ms);
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
            errlog::fail(&format!("[csoclassify-rs] 전파 입력 파일을 읽을 수 없습니다 {}: {}", path, e), 3);
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
        errlog::err(&format!("[csoclassify-rs] 전파 입력 파일이 없습니다: {}", input));
        return 3;
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
            errlog::err(&format!("[csoclassify-rs] seed 파일이 없습니다: {}", p));
            return 3;
        }
    }
    let external = match &seeds_path {
        Some(p) if Path::new(p).is_file() => {
            let idx = propagate::SeedIndex::from_seed_file(p);
            eprintln!("[csoclassify-rs] 외부 seed {}건 로드: {}", idx.size(), p);
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
            eprintln!("[csoclassify-rs] 업무분류 seed {}건 로드: {}",
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
            eprintln!("[propagate][업무분류] seed {}건이 있으나 doc_rule.yaml 의 \
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
        eprintln!("[propagate][업무분류] seeds={} embed_contributed={} no_vector={} axis_off={}",
                  dt_seeds.size(), contributed, dt_novec, axis_off);
    }

    // 출력 — 전파 입력은 '레코드 묶음'이라 요약을 붙이지 않는다(파이썬과 동일).
    // json 은 항상 배열로 감싸 유효한 JSON 파일이 되게 한다.
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
                errlog::err(&format!("[csoclassify-rs] 출력 저장 실패 {}: {}", p, e));
                return 1;
            }
        }
        None => print!("{}", body),
    }

    eprintln!("[propagate] seeds={} already_graded={} embed_decided={} still_unclassified={} no_vector={}",
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
        Err(msg) => errlog::fail(&format!("[csoclassify-rs] {}", msg), 4),
    };
    if let Err(msg) = conflict::ensure_overridable_axis(&axis) {
        errlog::fail(&format!("[csoclassify-rs] {}", msg), 4);
    }
    if axis != "doctype" {
        errlog::fail(&format!("[csoclassify-rs] --conflict 에 알 수 없는 축입니다: {:?}", axis), 4);
    }
    if let Some((_, drs)) = dt_axis.as_mut() {
        drs.conflict = parsed;
    }
    spec.split_once('=').map(|(_, v)| v.trim().to_string())
}

fn render(records: &[Value], summary: &Value, opts: &Opts) -> String {
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
        out
    } else {
        // json 배열(들여쓰기), 마지막에 summary 원소
        let mut arr: Vec<Value> = records.to_vec();
        if !opts.no_summary {
            arr.push(json!({"summary": summary}));
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
