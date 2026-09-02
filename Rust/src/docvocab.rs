//! 업무분류 규칙의 '말 만들기'(어휘) — Python 판 classify/docvocab.py 포팅.
//!
//! "요구사항정의서" 라는 분류 이름 하나에서, 실제 문서에 적혀 있을 법한 표기를
//! 만들어 낸다(띄어쓰기 변형 · 유의어 · 파일명 구분자 표기).
//!
//! [왜 이 파일이 있나] 화면의 [분류 불러오기] 버튼과 CLI 가 서로 다른 결과를 내던
//! 문제를 없애기 위해서다. Python 쪽은 화면과 엔진이 같은 모듈을 쓰게 모았고, Rust
//! 판은 그 모듈을 그대로 옮겨 왔다. 두 판이 갈라지지 않도록 골든 테스트
//! (tests/docvocab_golden.json — Python 이 만든 정답표)로 묶어 둔다.
//!
//! [글자 단위 주의] 원본은 파이썬이라 문자열 길이·자르기가 모두 '글자' 단위다.
//! 한글은 UTF-8 로 3바이트라 바이트 단위로 다루면 결과가 달라진다. 이 파일은
//! 자르고 비교하는 곳마다 Vec<char> 를 쓴다.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

/// 붙여 쓴 이름을 끊을 자리(꼬리말). 원본 DOC_SUFFIXES 와 같은 차례여야 한다 —
/// 먼저 걸린 하나에서 멈추므로 차례가 결과를 바꾼다.
pub const DOC_SUFFIXES: [&str; 41] = [
    "회의록", "계획서", "정의서", "설계서", "명세서", "제안서",
    "보고서", "결과서", "확인서", "신청서", "승인서", "의뢰서", "합의서", "계약서",
    "계산서", "견적서", "발주서", "검수서", "내역서", "산출물", "매뉴얼", "가이드",
    "지침서", "표준서", "규정집", "일지", "일보", "대장", "양식", "규정", "지침",
    "약관", "정관", "각서", "조서", "명부", "목록", "현황",
    "서류", "자료", "문서",
];
/// 위 배열에 "기록" 까지 넣으면 41개가 아니라 42개다. 원본과 개수를 맞추기 위해
/// 마지막 항목은 아래 상수로 따로 둔다(배열 길이를 바꾸면 컴파일이 막아 준다).
pub const DOC_SUFFIX_LAST: &str = "기록";

/// 범용어(filename_only)가 개수 제한에서 따로 갖는 자리 수.
pub const GENERIC_SLOTS: usize = 6;

const SYN_CORE: &str = "doc_synonyms.core.yaml";
const SYN_LOCAL: &str = "doc_synonyms.local.yaml";
const SYN_DIR: &str = "synonyms";

//------------------------------------------------------------------
// 꼬리말 전체 목록(배열 + 마지막 항목)
//=> DOC_SUFFIXES 는 길이를 못박아 실수를 막고, 실제로 훑을 때는 이 함수를 쓴다.
//
// -in: 없음
// -out: Vec<&str> = 원본과 같은 차례의 꼬리말 목록
//------------------------------------------------------------------
fn doc_suffixes() -> Vec<&'static str> {
    let mut v: Vec<&str> = DOC_SUFFIXES.to_vec();
    v.push(DOC_SUFFIX_LAST);
    v
}

//------------------------------------------------------------------
// 이 분류는 '서랍'인가 — 규칙을 걸지 않는 노드인가
//=> 규칙을 만들 곳과, 규칙이 없다고 알릴 곳(T12)이 같은 기준을 써야 한다.
//   서랍 = 최상위이면서 자식이 있는 노드.
//     · "경영/관리" 같은 최상위 이름은 문서에 그대로 적히는 말이 아니라 서랍이다
//     · 자식이 없다면 서랍이 아니라 '그 자체가 최종 분류'다 → 규칙을 건다
//     · 중간 노드("기술/개발 > 설계문서")는 문서에 적히는 말이다 → 규칙을 건다
//   (Python 판 classify/docvocab.is_drawer 와 같은 규약)
//
// -in: has_parent   = 부모가 있는가(= 최상위가 아닌가)
// -in: has_children = 살아 있는 자식이 있는가
// -out: bool = true 면 서랍
//------------------------------------------------------------------
pub fn is_drawer(has_parent: bool, has_children: bool) -> bool {
    !has_parent && has_children
}

/// 유의어 사전 한 벌. 차례가 결과를 바꾸므로 map 이 아니라 '순서 있는 목록'이다.
#[derive(Default, Clone)]
pub struct Syn {
    pub aliases: Vec<(String, Vec<String>)>,
    pub filename_only: Vec<(String, Vec<String>)>,
    pub excludes: Vec<(String, Vec<String>)>,
    pub tails: Vec<(String, Vec<String>)>,
    pub heads: Vec<(String, Vec<String>)>,
    pub layers: Vec<String>,
}

impl Syn {
    pub fn is_empty(&self) -> bool { self.layers.is_empty() }

    fn get<'a>(list: &'a [(String, Vec<String>)], key: &str) -> Option<&'a Vec<String>> {
        list.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }
}

//------------------------------------------------------------------
// 글자 단위 도우미
//=> 파이썬의 len()·슬라이스는 글자 단위다. 한글에서 바이트 단위로 다루면
//   "요구사항" 을 자르다 글자가 쪼개져 전혀 다른 말이 나온다.
//------------------------------------------------------------------
#[allow(dead_code)]
fn chars(s: &str) -> Vec<char> { s.chars().collect() }
fn clen(s: &str) -> usize { s.chars().count() }
fn take_chars(s: &str, n: usize) -> String { s.chars().take(n).collect() }
fn drop_chars(s: &str, n: usize) -> String { s.chars().skip(n).collect() }

//------------------------------------------------------------------
// 같은 토막이 두 번 이어 붙었는가
//=> 치환으로 만들어진 "류서식류서식" 같은 말을 걸러낸다. 한 글자 반복
//   ("공공"·"각각")은 멀쩡한 말이라 두 글자 토막부터 본다.
//
// -in: word = 검사할 낱말
// -out: bool = 겹쳐 붙은 말이면 true
//------------------------------------------------------------------
fn is_doubled(word: &str) -> bool {
    let tight: Vec<char> = word.chars().filter(|c| *c != ' ').collect();
    let len = tight.len();
    let mut n = 2;
    while n <= len / 2 {
        for i in 0..=(len - 2 * n) {
            if tight[i..i + n] == tight[i + n..i + 2 * n] {
                return true;
            }
        }
        n += 1;
    }
    false
}

//------------------------------------------------------------------
// 띄어쓰기만 다른 표기 만들기
//=> ① 이름 그대로 ② 띄어쓰기가 있으면 붙임형 ③ 붙여 쓴 이름은 꼬리말 앞에서
//   '한 번만' 끊는다. 여러 번 끊으면 "요 구 사 항" 같은 말이 생긴다.
//
// -in: word = 분류의 최하위 명칭
// -out: Vec<String> = 표기 후보(차례가 중요 — 확실한 것이 앞)
//------------------------------------------------------------------
fn spacing_forms(word: &str) -> Vec<String> {
    let mut out = vec![word.to_string()];
    let tight: String = word.chars().filter(|c| *c != ' ').collect();
    if !tight.is_empty() && tight != word {
        out.push(tight.clone());
    }
    for suf in doc_suffixes() {
        if tight.ends_with(suf) && clen(&tight) > clen(suf) + 1 {
            let head = take_chars(&tight, clen(&tight) - clen(suf));
            out.push(format!("{} {}", head, suf));
            break;
        }
    }
    out
}

//------------------------------------------------------------------
// 분류 이름의 유의어 뽑기
//=> ① 통째 별칭이 있으면 그것만 쓰고 끝낸다 ② 없으면 꼬리말 치환 ③ 머리말 치환.
//   ②③은 각각 처음 걸린 것 하나만 쓴다(겹쳐 바꾸면 아무도 안 쓰는 말이 된다).
//
// -in: title        = 분류의 최하위 명칭
// -in: syn          = 유의어 사전
// -in: for_filename = true 면 파일명 전용 말(filename_only)도 더한다
//
// -out: Vec<String> = 유의어 목록
//------------------------------------------------------------------
fn synonyms_of(title: &str, syn: &Syn, for_filename: bool) -> Vec<String> {
    if syn.is_empty() { return vec![]; }
    let tight: String = title.chars().filter(|c| *c != ' ').collect();
    let tight = tight.trim().to_string();
    if tight.is_empty() { return vec![]; }

    let fname_only: Vec<String> = if for_filename {
        Syn::get(&syn.filename_only, &tight).cloned().unwrap_or_default()
    } else { vec![] };

    if let Some(alias) = Syn::get(&syn.aliases, &tight) {
        if !alias.is_empty() {
            // 사람이 손질해 둔 별칭이 있으면 거기서 끝낸다.
            let mut out = alias.clone();
            out.extend(fname_only);
            return out;
        }
    }

    let mut out: Vec<String> = vec![];
    for (tail, alts) in &syn.tails {
        if tight.ends_with(tail.as_str()) && clen(&tight) > clen(tail) + 1 {
            let head = take_chars(&tight, clen(&tight) - clen(tail));
            for a in alts { out.push(format!("{}{}", head, a)); }
            break;
        }
    }
    for (head, alts) in &syn.heads {
        if tight.starts_with(head.as_str()) && clen(&tight) > clen(head) + 1 {
            let rest = drop_chars(&tight, clen(head));
            for a in alts { out.push(format!("{}{}", a, rest)); }
            break;
        }
    }
    // 치환으로 만든 말만 검사한다(사람이 적은 말은 겹쳐 보여도 그렇게 쓸 수 있다).
    let mut out: Vec<String> = out.into_iter().filter(|w| !is_doubled(w)).collect();
    out.extend(fname_only);
    out
}

//------------------------------------------------------------------
// 유의어를 '강한 말'과 '파일명 전용 말'로 가르기
//
// -in: title = 최하위 명칭
// -in: syn   = 유의어 사전
// -out: (강한 말, 파일명 전용 말)
//------------------------------------------------------------------
fn split_synonyms(title: &str, syn: &Syn) -> (Vec<String>, Vec<String>) {
    if syn.is_empty() { return (vec![], vec![]); }
    let tight: String = title.chars().filter(|c| *c != ' ').collect();
    let tight = tight.trim().to_string();
    if tight.is_empty() { return (vec![], vec![]); }
    let name_only = Syn::get(&syn.filename_only, &tight).cloned().unwrap_or_default();
    (synonyms_of(title, syn, false), name_only)
}

/// 규칙 한 줄에 들어갈 네 칸 + 제외어.
#[derive(Default, Debug, Clone)]
pub struct Vocab {
    pub title_terms: Vec<String>,
    pub head_terms: Vec<String>,
    pub terms: Vec<String>,
    pub filename: Vec<String>,
    pub exclude: Vec<String>,
}

//------------------------------------------------------------------
// 차례를 지키며 중복만 걷어내고 개수를 자른다
//------------------------------------------------------------------
fn cut(words: &[String], cap: usize) -> Vec<String> {
    let mut uniq: Vec<String> = vec![];
    for w in words {
        let w = w.trim().to_string();
        if !w.is_empty() && !uniq.contains(&w) {
            uniq.push(w);
        }
    }
    uniq.truncate(cap);
    uniq
}

//------------------------------------------------------------------
// 강한 말 + 범용어를 한 칸에 담되 범용어 자리를 먼저 떼어 둔다
//=> 범용어를 그냥 뒤에 붙이면 유의어가 많은 분류에서 개수 제한에 걸려 범용어가
//   통째로 잘려 나간다(실측에서 '매뉴얼'의 '가이드'·'manual' 이 그렇게 빠졌다).
//------------------------------------------------------------------
fn blend(strong: &[String], generic: &[String], cap: usize) -> Vec<String> {
    let mut merged = cut(strong, cap);
    merged.extend(cut(generic, GENERIC_SLOTS));
    cut(&merged, cap + GENERIC_SLOTS)
}

//------------------------------------------------------------------
// 분류 이름 → 규칙 네 칸의 시작값
//=> Python 판 rule_vocab 과 같은 결과를 내야 한다(골든 테스트로 묶여 있다).
//
// -in: title = 분류의 최하위 명칭
// -in: syn   = 유의어 사전
// -in: limit = 기본 개수 제한(원본 기본값 10)
//
// -out: Vocab = 제목·표제부·본문·파일명 칸과 제외어
//------------------------------------------------------------------
pub fn rule_vocab(title: &str, syn: &Syn, limit: usize) -> Vocab {
    let base = title.trim().to_string();
    if base.is_empty() { return Vocab::default(); }

    let (strong_syn, name_only) = split_synonyms(&base, syn);
    let mut strong = spacing_forms(&base);
    strong.extend(strong_syn);

    let mut fname = blend(&strong, &name_only, limit + 6);
    // 파일 이름은 공백 자리에 '_'·'-' 가 오는 일이 흔하다. 표기 변형이라 새 어휘가
    // 아니므로 이미 뽑힌 말에서만 만들고 개수 제한 밖에 둔다.
    for v in fname.clone() {
        if v.contains(' ') {
            fname.push(v.replace(' ', "_"));
            fname.push(v.replace(' ', "-"));
        }
    }

    // filename_only 에 적힌 말은 '범용 접미어' 라는 뜻이다 — 어디에 적혔든
    // 표제부·본문 칸에서는 뺀다(본문 문장을 포함해 오탐이 쏟아진다).
    let generic: HashSet<String> = name_only.iter()
        .map(|w| w.replace(' ', "").to_lowercase()).collect();
    let narrow: Vec<String> = strong.iter()
        .filter(|w| !generic.contains(&w.replace(' ', "").to_lowercase()))
        .cloned().collect();

    let tight: String = base.chars().filter(|c| *c != ' ').collect();
    let excl = Syn::get(&syn.excludes, &tight).cloned().unwrap_or_default();

    Vocab {
        title_terms: blend(&strong, &name_only, limit + 4),
        head_terms: cut(&narrow, limit),
        terms: cut(&narrow, limit),
        filename: cut(&fname, 2 * (limit + 6 + GENERIC_SLOTS)),
        exclude: cut(&excl, usize::MAX),
    }
}

//------------------------------------------------------------------
// 유의어 사전 읽기(세 겹)
//=> ① core → ② 업종별(doc_rule.yaml 의 industry, 앞에 적은 것이 더 셈) → ③ local.
//   아래로 갈수록 세다. 규칙 파일 옆 synonyms/ 폴더에서 찾는다.
//
// -in: doc_rules_path = doc_rule.yaml 경로
// -out: Syn = 합쳐진 사전(한 겹도 없으면 layers 가 비어 있다)
//------------------------------------------------------------------
pub fn load_synonyms(doc_rules_path: &Path) -> Syn {
    let base_dir = doc_rules_path.parent().unwrap_or(Path::new("."));

    // doc_rule.yaml 의 industry 값(문자열 하나 또는 목록).
    let mut industries: Vec<String> = vec![];
    if let Ok(txt) = std::fs::read_to_string(doc_rules_path) {
        if let Ok(v) = serde_yaml::from_str::<serde_yaml::Value>(&txt) {
            match v.get("industry") {
                Some(serde_yaml::Value::String(s)) => industries.push(s.clone()),
                Some(serde_yaml::Value::Sequence(seq)) => {
                    for x in seq {
                        if let Some(s) = x.as_str() { industries.push(s.to_string()); }
                    }
                }
                _ => {}
            }
        }
    }
    // 경로로 새어 나갈 수 있는 글자가 든 업종 이름은 버린다.
    industries.retain(|s| !s.is_empty()
        && !s.contains('/') && !s.contains('\\') && !s.contains('.') && !s.contains(':'));

    let mut names = vec![SYN_CORE.to_string()];
    for ind in industries.iter().rev() {
        names.push(format!("doc_synonyms.{}.yaml", ind));
    }
    names.push(SYN_LOCAL.to_string());

    let mut syn = Syn::default();
    for name in names {
        // synonyms/ 하위를 먼저 보고, 없으면 규칙 파일 옆도 본다(옛 배치 호환).
        let mut p: PathBuf = base_dir.join(SYN_DIR).join(&name);
        if !p.is_file() { p = base_dir.join(&name); }
        if !p.is_file() { continue; }
        let txt = match std::fs::read_to_string(&p) { Ok(t) => t, Err(_) => continue };
        let doc: serde_yaml::Value = match serde_yaml::from_str(&txt) {
            Ok(v) => v, Err(_) => continue,
        };
        syn.layers.push(p.to_string_lossy().into_owned());
        for (sec, dst) in [("aliases", 0usize), ("filename_only", 1), ("excludes", 2),
                           ("tails", 3), ("heads", 4)] {
            let m = match doc.get(sec).and_then(|v| v.as_mapping()) { Some(m) => m, None => continue };
            let target = match dst {
                0 => &mut syn.aliases, 1 => &mut syn.filename_only, 2 => &mut syn.excludes,
                3 => &mut syn.tails, _ => &mut syn.heads,
            };
            for (k, v) in m {
                let key = match k.as_str() { Some(s) => s.to_string(), None => continue };
                let words: Vec<String> = v.as_sequence().map(|seq| seq.iter()
                    .filter_map(|x| x.as_str()).map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty()).collect()).unwrap_or_default();
                // 센 겹(나중 파일)의 말이 앞에 온다 — 원본 _merge_layer 와 같다.
                match target.iter_mut().find(|(kk, _)| *kk == key) {
                    Some((_, have)) => {
                        let mut merged = words;
                        for w in have.iter() {
                            if !merged.contains(w) { merged.push(w.clone()); }
                        }
                        *have = merged;
                    }
                    None => target.push((key, words)),
                }
            }
        }
    }
    if syn.layers.is_empty() { return Syn::default(); }
    // 그물은 합친 뒤 반드시 다시 세운다 — 긴 꼬리말이 먼저 걸려야 한다.
    // 파이썬 sorted 는 안정 정렬이라, 길이가 같으면 넣은 차례가 유지된다.
    syn.tails.sort_by_key(|(k, _)| std::cmp::Reverse(clen(k)));
    syn.heads.sort_by_key(|(k, _)| std::cmp::Reverse(clen(k)));
    syn
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 띄어쓰기_표기는_꼬리말_앞에서_한_번만_끊는다() {
        assert_eq!(spacing_forms("요구사항정의서"),
                   vec!["요구사항정의서", "요구사항 정의서"]);
        // 이미 띄어쓰기가 있으면 붙임형도 만든다.
        assert_eq!(spacing_forms("사업 계획서")[0..2].to_vec(),
                   vec!["사업 계획서".to_string(), "사업계획서".to_string()]);
    }

    #[test]
    fn 겹쳐_붙은_말을_걸러낸다() {
        assert!(is_doubled("류서식류서식"));
        assert!(!is_doubled("공공"));       // 한 글자 반복은 멀쩡한 말
        assert!(!is_doubled("계약서"));
    }

    #[test]
    fn 사전이_없으면_이름_표기만_나온다() {
        let v = rule_vocab("계약서", &Syn::default(), 10);
        assert_eq!(v.terms, vec!["계약서"]);
        assert_eq!(v.filename, vec!["계약서"]);
        assert!(v.exclude.is_empty());
    }
}

#[cfg(test)]
mod golden {
    use super::*;

    //--------------------------------------------------------------
    // Python 판과 글자 하나까지 같은가(골든 테스트)
    //=> tests/docvocab_golden.json 은 Python classify/docvocab.rule_vocab 이
    //   실제 분류 체계·유의어 사전으로 만들어 낸 정답표다. 이 테스트가 있어야
    //   "화면과 CLI 가 같은 결과를 낸다"는 약속이 말이 아니라 사실이 된다.
    //   사전이나 규칙을 고쳐 결과가 달라지면 이 테스트가 먼저 깨진다 —
    //   그때는 Python 쪽에서 정답표를 다시 뽑아 함께 갱신해야 한다.
    //--------------------------------------------------------------
    #[test]
    fn python_판과_같은_어휘를_만든다() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        let golden = root.join("tests").join("docvocab_golden.json");
        let txt = std::fs::read_to_string(&golden)
            .unwrap_or_else(|e| panic!("정답표를 못 읽었다 {:?}: {}", golden, e));
        let doc: serde_json::Value = serde_json::from_str(&txt).unwrap();

        let syn = load_synonyms(&root.join("tests").join("fixtures")
                                    .join("policy").join("doc_rule.yaml"));
        assert!(!syn.is_empty(), "유의어 사전을 못 읽었다(fixtures/policy/synonyms)");

        let want_list = |v: &serde_json::Value, k: &str| -> Vec<String> {
            v.get(k).and_then(|x| x.as_array()).map(|a| a.iter()
                .filter_map(|s| s.as_str()).map(|s| s.to_string()).collect())
                .unwrap_or_default()
        };

        let cases = doc["cases"].as_array().unwrap();
        assert!(cases.len() > 100, "정답표가 너무 작다: {}", cases.len());
        let mut bad = 0;
        for c in cases {
            let title = c["title"].as_str().unwrap_or("");
            let got = rule_vocab(title, &syn, 10);
            for (key, mine) in [("title_terms", &got.title_terms),
                                ("head_terms", &got.head_terms),
                                ("terms", &got.terms),
                                ("filename", &got.filename),
                                ("exclude", &got.exclude)] {
                let want = want_list(c, key);
                if *mine != want {
                    bad += 1;
                    if bad <= 5 {
                        eprintln!("[다름] {:?} {}\n   Python: {:?}\n   Rust  : {:?}",
                                  title, key, want, mine);
                    }
                }
            }
        }
        assert_eq!(bad, 0, "Python 판과 다른 칸이 {}곳 있다", bad);
    }
}

//------------------------------------------------------------------
// 규칙 파일 채우기(--sync-doc-rule) — 화면 [분류 불러오기] 와 같은 일
//=> 분류 체계를 훑어 doc_rule.yaml 에 규칙을 채운다. 이미 있는 파일에도 덧붙이는
//   것이 --scaffold-doc-rule 과 다른 점이다(그쪽은 파일이 있으면 건너뛴다).
//    1) 규칙을 만들 분류를 고른다 — 꺼 둔 분류와 대분류(뿌리)는 뺀다.
//       차례는 전체경로 가나다순으로, 화면과 같은 줄 차례가 되게 맞춘다
//    2) 유의어 사전을 규칙 파일 옆 synonyms/ 에서 찾아 얹는다
//    3) 빠진 분류는 새로 만들고, 옵션에 따라 빈 규칙을 채우거나 유의어를 덧붙인다
//    4) 사람이 적어 둔 말은 절대 지우지 않는다 — 뒤에 덧붙이기만 한다
//
// -in: taxonomy   = 회사 분류 체계
// -in: out_path   = doc_rule.yaml 경로(있으면 읽어서 덧붙인다)
// -in: fill_blank = 단어가 하나도 없는 기존 규칙도 시작값으로 채울지
// -in: enrich     = 이미 말이 있는 규칙에도 빠진 유의어를 더할지
//
// -out: Ok((added, filled, enriched, 사전층)) · Err(사람이 읽을 실패 이유)
//------------------------------------------------------------------
pub fn sync_doc_rule(taxonomy: &crate::axes::Taxonomy, out_path: &Path,
                     fill_blank: bool, enrich: bool)
    -> Result<(usize, usize, usize, Vec<String>), String>
{
    use serde_yaml::Value as Y;
    let ystr = |v: &str| Y::String(v.to_string());
    let yseq = |v: &[String]| Y::Sequence(v.iter().map(|s| ystr(s)).collect());

    let syn = load_synonyms(out_path);
    let tpl = crate::doc_rules::load_scaffold_template(Some(out_path));

    // 기존 파일을 읽는다(없으면 본보기 머리로 시작).
    let mut doc: serde_yaml::Mapping = if out_path.is_file() {
        let txt = std::fs::read_to_string(out_path)
            .map_err(|e| format!("규칙 파일을 읽지 못했습니다({}): {}", out_path.display(), e))?;
        match serde_yaml::from_str::<Y>(&txt) {
            Ok(Y::Mapping(m)) => m,
            Ok(_) => serde_yaml::Mapping::new(),
            Err(e) => return Err(format!("규칙 파일이 YAML 로 읽히지 않습니다({}): {}",
                                         out_path.display(), e)),
        }
    } else {
        serde_yaml::Mapping::new()
    };
    // 본보기의 머리 값은 '빠진 것만' 채운다 — 이미 적힌 값은 건드리지 않는다.
    for (k, v) in tpl.iter() {
        let key = match k.as_str() { Some(x) => x, None => continue };
        if crate::doc_rules::UI_ONLY_KEYS.contains(&key) || key == "doctype_rules" { continue; }
        match (doc.get(&ystr(key)), v) {
            (Some(Y::Mapping(_)), Y::Mapping(tv)) => {
                // defaults·embed 처럼 묶음인 값은 한 겹 안까지 본다.
                if let Some(Y::Mapping(cur)) = doc.get_mut(&ystr(key)) {
                    for (k2, v2) in tv.iter() {
                        if !cur.contains_key(k2) { cur.insert(k2.clone(), v2.clone()); }
                    }
                }
            }
            (None, _) => { doc.insert(ystr(key), v.clone()); }
            _ => {}
        }
    }
    if !doc.contains_key(&ystr("conflict")) { doc.insert(ystr("conflict"), ystr("all")); }

    let new_rule: Vec<(String, Y)> = tpl.get("new_rule").and_then(|v| v.as_mapping())
        .map(|m| m.iter().filter_map(|(k, v)| k.as_str().map(|k| (k.to_string(), v.clone()))).collect())
        .unwrap_or_else(|| vec![("weight".to_string(), ystr("medium"))]);

    let mut rules: Vec<Y> = doc.get(&ystr("doctype_rules"))
        .and_then(|v| v.as_sequence()).cloned().unwrap_or_default();

    // 분류 목록 — 화면(syncable_nodes)과 같은 기준·같은 차례.
    let kids: std::collections::HashSet<&str> = taxonomy.nodes.iter()
        .filter(|n| n.active())
        .filter_map(|n| n.parent.as_deref()).collect();
    let mut nodes: Vec<(String, String, String)> = vec![];   // (path, dc_id, title)
    for n in taxonomy.nodes.iter() {
        if !n.active() || is_drawer(n.parent.is_some(), kids.contains(n.dc_id.as_str())) {
            continue;
        }
        // 전체경로가 없으면(꼬인 데이터) dc_id 로 줄을 세운다 — 화면도 같은 규칙이다.
        let path = taxonomy.path(&n.dc_id).unwrap_or_else(|_| n.dc_id.clone());
        nodes.push((path, n.dc_id.clone(), n.title.clone()));
    }
    nodes.sort_by(|a, b| a.0.cmp(&b.0));

    let (mut added, mut filled, mut enriched) = (0usize, 0usize, 0usize);
    for (_, dc_id, title) in &nodes {
        let v = rule_vocab(title, &syn, 10);
        let pos = rules.iter().position(|r| r.get("node").and_then(|x| x.as_str())
                                             == Some(dc_id.as_str()));
        match pos {
            None => {
                let mut m = serde_yaml::Mapping::new();
                m.insert(ystr("id"), ystr(&format!("dt_{}", dc_id.to_lowercase())));
                m.insert(ystr("node"), ystr(dc_id));
                for (k, val) in &new_rule { m.insert(ystr(k), val.clone()); }
                m.insert(ystr("title_terms"), yseq(&v.title_terms));
                m.insert(ystr("head_terms"), yseq(&v.head_terms));
                m.insert(ystr("terms"), yseq(&v.terms));
                m.insert(ystr("filename"), yseq(&v.filename));
                if !v.exclude.is_empty() { m.insert(ystr("exclude"), yseq(&v.exclude)); }
                rules.push(Y::Mapping(m));
                added += 1;
            }
            Some(i) => {
                let has_any = ["title_terms", "head_terms", "terms", "filename"].iter()
                    .any(|k| rules[i].get(*k).and_then(|x| x.as_sequence())
                                     .map(|s| !s.is_empty()).unwrap_or(false));
                let m = match rules[i].as_mapping_mut() { Some(m) => m, None => continue };
                if !has_any {
                    if fill_blank {
                        m.insert(ystr("title_terms"), yseq(&v.title_terms));
                        m.insert(ystr("head_terms"), yseq(&v.head_terms));
                        m.insert(ystr("terms"), yseq(&v.terms));
                        m.insert(ystr("filename"), yseq(&v.filename));
                        if !v.exclude.is_empty() { m.insert(ystr("exclude"), yseq(&v.exclude)); }
                        filled += 1;
                    }
                } else if enrich {
                    // 사람이 적은 말은 앞에 그대로 두고, 빠진 말만 뒤에 잇는다.
                    let mut grew = false;
                    for (key, want) in [("title_terms", &v.title_terms),
                                        ("head_terms", &v.head_terms),
                                        ("terms", &v.terms), ("filename", &v.filename),
                                        ("exclude", &v.exclude)] {
                        let have: Vec<String> = m.get(&ystr(key)).and_then(|x| x.as_sequence())
                            .map(|s| s.iter().filter_map(|x| x.as_str())
                                      .map(|s| s.to_string()).collect()).unwrap_or_default();
                        let more: Vec<String> = want.iter()
                            .filter(|w| !have.contains(w)).cloned().collect();
                        if !more.is_empty() {
                            let mut all = have; all.extend(more);
                            m.insert(ystr(key), yseq(&all));
                            grew = true;
                        }
                    }
                    if grew { enriched += 1; }
                }
            }
        }
    }

    if added == 0 && filled == 0 && enriched == 0 {
        return Ok((0, 0, 0, syn.layers));
    }

    doc.insert(ystr("doctype_rules"), Y::Sequence(rules));
    // 화면과 같은 자리에 백업을 남긴다(.bak) — 되돌릴 여지를 준다.
    if out_path.is_file() {
        let _ = std::fs::copy(out_path, out_path.with_extension("yaml.bak"));
    }
    let header = "# 업무분류 판단 기준 — 화면(설정 ③ 판단 기준)이 저장할 때마다 다시 쓰는 파일이라\n# 주석이 남지 않는다. 각 값의 뜻과 그 값을 고른 이유는 doc_rule_template.yaml 에 있다.\n";
    let body = serde_yaml::to_string(&Y::Mapping(doc))
        .map_err(|e| format!("YAML 직렬화 실패: {}", e))?;
    if let Some(dir) = out_path.parent() {
        if !dir.as_os_str().is_empty() {
            std::fs::create_dir_all(dir)
                .map_err(|e| format!("폴더를 만들 수 없습니다({}): {}", dir.display(), e))?;
        }
    }
    std::fs::write(out_path, format!("{}{}", header, body))
        .map_err(|e| format!("쓰기 실패({}): {}", out_path.display(), e))?;
    Ok((added, filled, enriched, syn.layers))
}
