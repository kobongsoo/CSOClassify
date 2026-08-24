//! csoclassify-rs — CSOClassify rule-only 분류 코어의 Rust 포팅 (PoC).
//! 추출(text/html/ooxml) + 규칙 분류(PII·키워드·민감·스탬프·경로·파일명·융합) + CLI.

mod detect;
mod districts;
mod embed;
mod extract;
mod pii;
mod propagate;
mod rules;

use std::path::{Path, PathBuf};
use std::time::Instant;

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use rules::{build_record, load_rules, Grade, RuleSet};

struct Opts {
    file: Option<String>,
    dir: Option<String>,
    rules_path: Option<String>,
    out: Option<String>,
    fmt: String,        // "json" | "jsonl"
    simple: bool,
    summary_only: bool,
    no_summary: bool,
    hash: bool,
    with_pii: bool,
    rule_only: bool,
    vector_only: bool,
    auto_propagate: bool,
    seeds: Option<String>,
    failsafe: Option<String>,
}

fn usage() {
    eprintln!("csoclassify-rs — 추출 + 규칙 분류(rule-only) [Rust PoC]");
    eprintln!("사용법:");
    eprintln!("  csoclassify-rs --file <파일> [옵션]");
    eprintln!("  csoclassify-rs --dir  <폴더> [옵션]   (재귀)");
    eprintln!("옵션:");
    eprintln!("  --rules <cso_rules.yaml>  규칙셋(미지정 시 exe 옆/CSOCLASSIFY_POLICY_DIR)");
    eprintln!("  --format json|jsonl       출력 형식(기본 json 배열)");
    eprintln!("  --out <파일>              결과 저장(미지정 시 stdout)");
    eprintln!("  --simple                  파일별 문서명·등급·해시 3필드만");
    eprintln!("  --hash                    각 문서 SHA-256 포함");
    eprintln!("  --with-pii                [프라이버시 예외] 검출된 원문 PII 값 포함(pii 필드)");
    eprintln!("  --rule-only               규칙만으로 분류 — 임베딩·전파 없음(가장 빠름). --vector-only 와 배타");
    eprintln!("  --vector-only             규칙 없이 임베딩 벡터를 seed 와 비교해서만 분류(--seeds 필수). --rule-only 와 배타");
    eprintln!("  --auto-propagate          보류 문서를 seed 로 전파(seed 있으면 기본 on)");
    eprintln!("  --seeds <cso_seed.jsonl>  전파 비교 기준 seed 저장소(미지정 시 exe 옆 cso_seed.jsonl)");
    eprintln!("  --summary                 요약만 출력");
    eprintln!("  --nosummary               요약 제거(파일별만)");
    eprintln!("  --failsafe [등급]         무신호 기본등급(예: S)");
}

fn parse_args() -> Result<Opts, String> {
    let mut o = Opts {
        file: None, dir: None, rules_path: None, out: None,
        fmt: "json".into(), simple: false, summary_only: false,
        no_summary: false, hash: false, with_pii: false,
        rule_only: false, vector_only: false, auto_propagate: false, seeds: None,
        failsafe: None,
    };
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut i = 0;
    while i < args.len() {
        let a = &args[i];
        let mut next = || { i += 1; args.get(i).cloned().unwrap_or_default() };
        match a.as_str() {
            "--file" | "-file" => o.file = Some(next()),
            "--dir" | "-dir" => o.dir = Some(next()),
            "--rules" => o.rules_path = Some(next()),
            "--out" => o.out = Some(next()),
            "--format" => o.fmt = next(),
            "--simple" => o.simple = true,
            "--summary" => o.summary_only = true,
            "--nosummary" => o.no_summary = true,
            "--hash" => o.hash = true,
            "--with-pii" => o.with_pii = true,
            "--rule-only" => o.rule_only = true,
            "--vector-only" => o.vector_only = true,
            "--auto-propagate" => o.auto_propagate = true,
            "--seeds" => o.seeds = Some(next()),
            "--failsafe" => o.failsafe = Some("S".into()),
            "--hybridparse" => {} // 추출은 항상 내용감지 라우팅
            "-h" | "--help" => { usage(); std::process::exit(0); }
            _ => eprintln!("[warn] 알 수 없는 인자: {}", a),
        }
        i += 1;
    }
    Ok(o)
}

/// 규칙셋 경로 결정: --rules → exe 옆 → CSOCLASSIFY_POLICY_DIR.
fn resolve_rules(opts: &Opts) -> Option<PathBuf> {
    if let Some(p) = &opts.rules_path {
        return Some(PathBuf::from(p));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let p = dir.join("cso_rules.yaml");
            if p.is_file() { return Some(p); }
        }
    }
    if let Ok(d) = std::env::var("CSOCLASSIFY_POLICY_DIR") {
        let p = Path::new(&d).join("cso_rules.yaml");
        if p.is_file() { return Some(p); }
    }
    None
}

/// 대상 파일 수집: --file 하나, --dir 재귀.
fn collect_files(opts: &Opts) -> Vec<PathBuf> {
    if let Some(f) = &opts.file {
        return vec![PathBuf::from(f)];
    }
    if let Some(d) = &opts.dir {
        return walkdir::WalkDir::new(d).into_iter()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_type().is_file())
            .map(|e| e.into_path())
            .collect();
    }
    vec![]
}

/// 기본 seed 저장소 경로: CSOCLASSIFY_POLICY_DIR/cso_seed.jsonl → exe 옆 cso_seed.jsonl.
fn default_seed_path() -> Option<String> {
    if let Ok(dir) = std::env::var("CSOCLASSIFY_POLICY_DIR") {
        let p = Path::new(&dir).join("cso_seed.jsonl");
        return Some(p.to_string_lossy().into_owned());
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            return Some(dir.join("cso_seed.jsonl").to_string_lossy().into_owned());
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
    // 히든: 임베딩 검증용(--emb-test "<텍스트>") → 384벡터 JSON 출력.
    let raw: Vec<String> = std::env::args().skip(1).collect();
    if raw.first().map(|s| s == "--emb-test").unwrap_or(false) {
        let text = raw.get(1).cloned().unwrap_or_default();
        match embed::Embedder::load() {
            Some(mut e) => match e.embed_document(&text) {
                Some(v) => println!("{}", serde_json::to_string(&v).unwrap()),
                None => { eprintln!("embed 실패(빈 텍스트?)"); std::process::exit(1); }
            },
            None => { eprintln!("모델/런타임 로드 실패(models/e5-small-ko, onnxruntime.dll 확인)"); std::process::exit(1); }
        }
        return;
    }

    let opts = match parse_args() { Ok(o) => o, Err(e) => { eprintln!("{}", e); std::process::exit(2); } };
    let t0 = Instant::now();

    let rules_path = match resolve_rules(&opts) {
        Some(p) => p,
        None => { eprintln!("[csoclassify-rs] 규칙셋(cso_rules.yaml)을 찾을 수 없습니다. --rules 로 지정하세요."); std::process::exit(2); }
    };
    let rs: RuleSet = match load_rules(&rules_path) {
        Ok(rs) => rs,
        Err(e) => { eprintln!("[csoclassify-rs] {}", e); std::process::exit(2); }
    };

    let files = collect_files(&opts);
    if files.is_empty() {
        eprintln!("[csoclassify-rs] 처리할 파일이 없습니다. --file 또는 --dir 지정.");
        std::process::exit(2);
    }

    // 분류 모드 결정(ko-pii cli.py) — 배타: 벡터만 / 규칙만 / 기본(규칙+자동전파).
    let seeds_path: Option<String> = opts.seeds.clone().or_else(default_seed_path);
    let seed_exists = seeds_path.as_deref().map(|p| Path::new(p).is_file()).unwrap_or(false);
    let mut auto_prop = opts.auto_propagate;
    let (rules_enabled, embed_mode): (bool, &str) = if opts.vector_only {
        if !seed_exists {
            eprintln!("[csoclassify-rs] --vector-only 는 비교 기준 seed 파일이 필요합니다: --seeds <cso_seed.jsonl>(또는 exe 옆)");
            std::process::exit(3);
        }
        auto_prop = true;
        (false, "all")
    } else if opts.rule_only {
        auto_prop = false;
        (true, "none")
    } else {
        // 기본: seed 가 있으면 자동 전파 on(명시 --auto-propagate 없이도).
        if !auto_prop && seed_exists { auto_prop = true; }
        (true, if auto_prop { "needed" } else { "none" })
    };

    let need_hash = opts.hash || opts.simple;
    let mut fail = 0u32;

    // 1차: 추출 + (규칙) 분류 → 아이템 수집(전파 위해 text/seed_eligible/vector 보관).
    struct Item { rec: Value, grade: Option<Grade>, text: String, seed_eligible: bool, vector: Option<Vec<f32>>, file: String }
    let mut items: Vec<Item> = vec![];
    for path in &files {
        let fmt = detect::detect_format(path);
        let text = match extract::extract_text(path, fmt) {
            Some(t) => t,
            None => { eprintln!("[추출실패] {} (감지={})", path.display(), fmt.as_str()); fail += 1; continue; }
        };
        let display = path.to_string_lossy().replace('\\', "/");
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
        let seed_elig = rec.get("seed_eligible").and_then(|b| b.as_bool()).unwrap_or(false);
        items.push(Item { rec, grade, text, seed_eligible: seed_elig, vector: None, file: display });
    }

    // 2차: 전파(auto_prop && 임베딩 필요). 임베딩 → seed 비교 → 보류 문서 재융합.
    let mut prop_stats: Option<(usize, u32, u32)> = None;
    if auto_prop && embed_mode != "none" {
        match embed::Embedder::load() {
            Some(mut embedder) => {
                // 임베딩 대상: all=전량, needed=보류 or seed_eligible.
                for it in items.iter_mut() {
                    let need = embed_mode == "all" || it.grade.is_none() || it.seed_eligible;
                    if need { it.vector = embedder.embed_document(&it.text); }
                }
                // 내부 seed(코퍼스 seed_eligible) + 외부 seed(cso_seed.jsonl) 병합.
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
            }
            None => eprintln!("[csoclassify-rs] 임베딩 모델/런타임 로드 실패 → 전파 생략(models/e5-small-ko, onnxruntime.dll 확인)."),
        }
    }

    // 3차: 집계 + 출력 레코드 조립.
    let (mut c, mut s_, mut o_, mut none) = (0u32, 0u32, 0u32, 0u32);
    let mut records: Vec<Value> = vec![];
    for it in items {
        match it.grade {
            Some(Grade::C) => c += 1,
            Some(Grade::S) => s_ += 1,
            Some(Grade::O) => o_ += 1,
            None => none += 1,
        }
        if opts.summary_only { continue; }
        let out_rec = if opts.simple {
            json!({"file": it.rec["file"], "grade": it.rec["grade"], "hash": it.rec.get("hash").cloned().unwrap_or(Value::Null)})
        } else { it.rec };
        records.push(out_rec);
    }
    if let Some((seeds_n, decided, still)) = prop_stats {
        eprintln!("[전파] seed={} 전파결정={} 미분류잔여={}", seeds_n, decided, still);
    }

    let total = files.len();
    let detected = c + s_ + o_;
    let total_ms = round1(t0.elapsed().as_secs_f64() * 1000.0);
    let summary = json!({
        "total": total, "detected": detected,
        "C": c, "S": s_, "O": o_, "unclassified": none,
        "extract_failed": fail,
        "elapsed_total_ms": total_ms, "rule_version": rs.version
    });

    // 출력 조립
    let body = render(&records, &summary, &opts);
    match &opts.out {
        Some(p) => {
            if let Err(e) = std::fs::write(p, &body) {
                eprintln!("[csoclassify-rs] 출력 저장 실패 {}: {}", p, e);
                std::process::exit(1);
            }
        }
        None => { print!("{}", body); }
    }

    // 화면 요약(stderr)
    if !opts.no_summary {
        eprintln!("[summary] 총 {}개 / 검출 {}, C={} S={} O={} 미분류={} 추출실패={}, 총시간={}ms",
            total, detected, c, s_, o_, none, fail, total_ms);
    }
}

/// json 배열 / jsonl / summary-only 를 CSOClassify 와 같은 형태로 렌더.
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
