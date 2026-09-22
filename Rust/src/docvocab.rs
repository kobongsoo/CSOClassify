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

/// 붙여 쓴 이름을 끊을 자리(꼬리말)의 기본값. 원본 DOC_SUFFIXES 와 같은 차례여야 한다.
/// 2026-09-15 "메뉴얼"(흔한 표기 변형) 추가 — "OO메뉴얼" 도 "OO_메뉴얼" 파일명을 받는다.
/// 2026-09-18 "예산서" 추가 — 문서종류 명사인데 빠져 있었다(분류체계 제목 시험 ⓪-b 에서 발견).
/// [2026-09-15 사전 이관] 원본은 core 사전의 suffixes: 칸이다. 이 배열은 사전에 그 칸이
/// 없을 때(사전 없음·옛 core) 쓰인다. 예전에 마지막 항목을 DOC_SUFFIX_LAST 로 따로 두던
/// 사정(원본과 개수 맞추기)은 의미가 없어져 한 배열로 합쳤다(결과는 같다).
pub const DOC_SUFFIXES: [&str; 44] = [
    "회의록", "계획서", "정의서", "설계서", "명세서", "제안서",
    "보고서", "결과서", "확인서", "신청서", "승인서", "의뢰서", "합의서", "계약서",
    "계산서", "예산서", "견적서", "발주서", "검수서", "내역서", "산출물", "매뉴얼", "메뉴얼", "가이드",
    "지침서", "표준서", "규정집", "일지", "일보", "대장", "양식", "규정", "지침",
    "약관", "정관", "각서", "조서", "명부", "목록", "현황",
    "서류", "자료", "문서", "기록",
];

/// 범용어(filename_only)가 개수 제한에서 따로 갖는 자리 수.
pub const GENERIC_SLOTS: usize = 6;

/// 규칙을 '어느 제목에서 구웠는지' 적어 두는 칸(2026-09-21, 설계서 7장 ③).
/// 분류 제목이 바뀌어도 구워 넣은 말은 지우지 않되, 바뀐 사실은 화면이 알려야 한다.
/// 판정 엔진은 이 칸을 읽지 않는다. Python classify/docvocab.py 의 FILLED_FROM 과 같다.
pub const FILLED_FROM: &str = "filled_from_title";

// ── 핵어 판정용 내장 목록 (2026-09-21, 설계서 7장 ⓐ·13장 D1) ──────────
// 분류체계 제목을 '핵어'로 써도 되는지 가릴 때 쓴다. 원본은 core 사전의
// broad_words·container_tails·common_endings·noise_tails 칸이고, 아래는 그 칸이
// 없는 배포(사전 없음·옛 core)에서 쓰는 기본값이다.
// Python classify/docvocab.py 의 같은 목록과 내용이 같아야 한다.

/// 제목이 통째로 이 말이면 너무 넓어 핵어로 쓸 수 없다.
pub const BROAD_WORDS: [&str; 14] = [
    "자료", "서류", "문서", "기록", "목록", "현황", "양식", "서식",
    "파일", "기타", "일반", "공통", "참고", "첨부",
];
/// 제목이 이 말로 끝나면 앞은 주제이고 끝은 '묶음'이다 — 문서종류를 말하지 않는다.
pub const CONTAINER_TAILS: [&str; 7] = ["자료", "서류", "문서", "파일", "기타", "관련", "일반"];
/// 문서종류 명사이긴 하나 본문·파일명에 너무 흔해, 끝말이 이것이면 한 분류로 못 보낸다.
pub const COMMON_ENDINGS: [&str; 6] = ["규정", "지침", "세칙", "준칙", "규약", "규칙"];
/// 파일명·제목 끝에 붙는 관리용 꼬리. 핵어 자리를 보기 전에 걷어낸다.
pub const NOISE_TAILS: [&str; 26] = [
    "최종본", "최종", "수정본", "수정안", "수정", "복사본", "사본", "회람용",
    "배포용", "검토용", "제출용", "보고용", "공개용", "내부용", "참고용",
    "초안", "원본", "백업", "샘플", "완료",
    "final", "draft", "copy", "backup", "sample", "new",
];

/// 핵어 판정용 목록 칸 이름(사전에서 읽는 차례대로).
pub const SYN_HEAD_LISTS: [&str; 4] =
    ["broad_words", "container_tails", "common_endings", "noise_tails"];

const SYN_CORE: &str = "doc_synonyms.core.yaml";
const SYN_LOCAL: &str = "doc_synonyms.local.yaml";
const SYN_DIR: &str = "synonyms";

const SYN_SUFFIXES: &str = "suffixes";
const SYN_EXTRA: &str = "extra_terms";
const EXTRA_CELLS: [&str; 5] = ["title_terms", "head_terms", "terms", "filename", "exclude"];

//------------------------------------------------------------------
// 칸별 추가 단어 한 겹 정리하기(원본 _clean_extra_terms 와 같은 규칙)
//=> 사람이 조정한 규칙 단어를 사전에 남겨 두는 칸. 틀린 칸·틀린 말만 버린다.
//    1) map 이 아니면 빈 목록
//    2) 분류 이름은 공백을 지운 모양(aliases 를 찾는 방식과 같다)
//    3) 칸 이름은 EXTRA_CELLS 다섯 개만, 값은 목록만 받는다
//    4) 말은 앞뒤 공백을 지우고 빈 말·중복은 버린다
//
// -in: raw = yaml 에서 읽은 extra_terms 값(없으면 None)
// -out: Vec<(분류이름, [(칸, [말…])])>
//------------------------------------------------------------------
fn clean_extra_terms(raw: Option<&serde_yaml::Value>) -> Vec<(String, Vec<(String, Vec<String>)>)> {
    let map = match raw.and_then(|v| v.as_mapping()) { Some(m) => m, None => return vec![] };
    let mut out: Vec<(String, Vec<(String, Vec<String>)>)> = vec![];
    for (name, cells) in map {
        let key: String = match name.as_str() { Some(s) => s.chars().filter(|c| *c != ' ').collect(), None => continue };
        let key = key.trim().to_string();
        let cells = match cells.as_mapping() { Some(m) => m, None => continue };
        if key.is_empty() { continue; }
        let mut got: Vec<(String, Vec<String>)> = vec![];
        for (cell, words) in cells {
            let cell = match cell.as_str() { Some(c) if EXTRA_CELLS.contains(&c) => c.to_string(), _ => continue };
            let seq = match words.as_sequence() { Some(s) => s, None => continue };
            let mut uniq: Vec<String> = vec![];
            for x in seq {
                // 파이썬 str(x) 와 맞추려고 숫자도 글자로 받는다.
                let w = match x {
                    serde_yaml::Value::String(s) => s.trim().to_string(),
                    serde_yaml::Value::Number(n) => n.to_string(),
                    _ => continue,
                };
                if !w.is_empty() && !uniq.contains(&w) { uniq.push(w); }
            }
            if !uniq.is_empty() { got.push((cell, uniq)); }
        }
        if got.is_empty() { continue; }
        // 같은 이름이 공백만 다르게 두 번 적혔으면 앞의 것에 잇는다.
        let slot = match out.iter().position(|(k, _)| *k == key) {
            Some(i) => i,
            None => { out.push((key, vec![])); out.len() - 1 }
        };
        for (cell, words) in got {
            let cells_v = &mut out[slot].1;
            match cells_v.iter_mut().find(|(c, _)| *c == cell) {
                Some((_, have)) => { for w in words { if !have.contains(&w) { have.push(w); } } }
                None => cells_v.push((cell, words)),
            }
        }
    }
    out
}

//------------------------------------------------------------------
// 끝말 목록 한 겹 정리하기(원본 _clean_suffixes 와 같은 규칙)
//=> 사람이 적는 칸이라 모양이 틀릴 수 있다. 틀린 항목만 버린다.
//    1) 목록이 아니면 빈 목록
//    2) 문자열·숫자 항목만 받아 앞뒤 공백을 지운다
//    3) 비었거나 · 안에 공백이 있거나 · 2글자 미만이면 버린다(글자 단위로 센다)
//    4) 중복은 처음 나온 것만
//
// -in: raw = yaml 에서 읽은 suffixes 값(없으면 None)
// -out: Vec<String> = 쓸 수 있는 끝말 목록
//------------------------------------------------------------------
fn clean_suffixes(raw: Option<&serde_yaml::Value>) -> Vec<String> {
    let seq = match raw.and_then(|v| v.as_sequence()) { Some(s) => s, None => return vec![] };
    let mut out: Vec<String> = vec![];
    for x in seq {
        // 파이썬 str(x) 와 맞추려고 숫자도 글자로 받는다(그래도 대부분 2글자 검사에서 걸러진다).
        let w = match x {
            serde_yaml::Value::String(s) => s.trim().to_string(),
            serde_yaml::Value::Number(n) => n.to_string(),
            _ => continue,
        };
        // 바이트가 아니라 글자로 센다 — 한글 한 글자는 UTF-8 로 3바이트다.
        if clen(&w) < 2 || w.chars().any(|c| c.is_whitespace()) || out.contains(&w) {
            continue;
        }
        out.push(w);
    }
    out
}

//------------------------------------------------------------------
// 목록 칸 한 겹 정리하기(핵어 판정용 네 목록)
//=> 끝말(clean_suffixes)과 거의 같지만 한 글자 말도 받는다. 빈 말·공백이 든
//   말·중복만 버린다(Python _clean_endings 과 같은 규약).
//
// -in: raw = yaml 의 그 칸 값
// -out: Vec<String> = 쓸 수 있는 말 목록
//------------------------------------------------------------------
fn clean_words(raw: Option<&serde_yaml::Value>) -> Vec<String> {
    let seq = match raw.and_then(|v| v.as_sequence()) { Some(s) => s, None => return vec![] };
    let mut out: Vec<String> = vec![];
    for x in seq {
        let w = match x {
            serde_yaml::Value::String(s) => s.trim().to_string(),
            serde_yaml::Value::Number(n) => n.to_string(),
            _ => continue,
        };
        if w.is_empty() || w.chars().any(|c| c.is_whitespace()) || out.contains(&w) {
            continue;
        }
        out.push(w);
    }
    out
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
    /// 띄어쓰기 끝말(core 기준 + 업종·local 추가, 긴 것부터). 비면 내장 DOC_SUFFIXES.
    pub suffixes: Vec<String>,
    /// 칸별 추가 단어 — (분류이름, [(칸, [말…])]). 규칙 칸 뒤에 개수 제한 없이 붙는다.
    pub extra_terms: Vec<(String, Vec<(String, Vec<String>)>)>,
    /// 핵어 판정용 네 목록(SYN_HEAD_LISTS 차례). 비면 내장 기본값을 쓴다.
    pub head_lists: Vec<Vec<String>>,
    pub layers: Vec<String>,
}

impl Syn {
    pub fn is_empty(&self) -> bool { self.layers.is_empty() }

    //--------------------------------------------------------------
    // 핵어 판정용 목록 하나 꺼내기
    //=> 사전에 그 칸이 있으면 그것을, 없으면 내장 기본값을 돌려준다.
    //   사전이 깔리지 않은 배포에서 판정이 조용히 꺼지지 않게 한다.
    //
    // -in: sec = 칸 이름(SYN_HEAD_LISTS 중 하나)
    // -out: Vec<String> = 말 목록(칸 이름이 틀리면 빈 목록)
    //--------------------------------------------------------------
    pub fn head_list(&self, sec: &str) -> Vec<String> {
        let idx = match SYN_HEAD_LISTS.iter().position(|s| *s == sec) {
            Some(i) => i, None => return vec![],
        };
        if let Some(got) = self.head_lists.get(idx) {
            if !got.is_empty() { return got.clone(); }
        }
        let builtin: &[&str] = match idx {
            0 => &BROAD_WORDS, 1 => &CONTAINER_TAILS, 2 => &COMMON_ENDINGS, _ => &NOISE_TAILS,
        };
        builtin.iter().map(|s| s.to_string()).collect()
    }

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
// -in: word     = 분류의 최하위 명칭
// -in: suffixes = 끊을 자리로 쓸 끝말(사전의 suffixes). 비었으면 내장 DOC_SUFFIXES
// -out: Vec<String> = 표기 후보(차례가 중요 — 확실한 것이 앞)
//------------------------------------------------------------------
fn spacing_forms(word: &str, suffixes: &[String]) -> Vec<String> {
    let mut out = vec![word.to_string()];
    let tight: String = word.chars().filter(|c| *c != ' ').collect();
    if !tight.is_empty() && tight != word {
        out.push(tight.clone());
    }
    // 사전이 준 끝말이 있으면 그것을, 없으면 내장 목록을 쓴다.
    let list: Vec<&str> = if suffixes.is_empty() {
        DOC_SUFFIXES.to_vec()
    } else {
        suffixes.iter().map(|s| s.as_str()).collect()
    };
    for suf in list {
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
pub fn synonyms_of(title: &str, syn: &Syn, for_filename: bool) -> Vec<String> {
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
    let mut strong = spacing_forms(&base, &syn.suffixes);
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

    let mut vocab = Vocab {
        title_terms: blend(&strong, &name_only, limit + 4),
        head_terms: cut(&narrow, limit),
        terms: cut(&narrow, limit),
        filename: cut(&fname, 2 * (limit + 6 + GENERIC_SLOTS)),
        exclude: cut(&excl, usize::MAX),
    };

    // 사람이 조정해 둔 칸별 추가 단어는 개수 제한 밖에서 맨 뒤에, 적힌 칸에만 붙인다.
    let tight_name: String = base.chars().filter(|c| *c != ' ').collect();
    if let Some((_, cells)) = syn.extra_terms.iter().find(|(k, _)| *k == tight_name) {
        for (cell, words) in cells {
            let have = match cell.as_str() {
                "title_terms" => &mut vocab.title_terms,
                "head_terms" => &mut vocab.head_terms,
                "terms" => &mut vocab.terms,
                "filename" => &mut vocab.filename,
                "exclude" => &mut vocab.exclude,
                _ => continue,
            };
            for w in words { if !have.contains(w) { have.push(w.clone()); } }
        }
    }
    vocab
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
    // [2026-09-15 수정] 파일이 아직 없거나(처음 만들 때) industry 칸이 아예 없으면
    // 본보기(doc_rule_template.yaml)의 값을 쓴다. 저장할 때 sync_doc_rule 이 빠진 칸을
    // 본보기로 채우므로, 파일에는 industry 가 적히는데 규칙을 만드는 순간에는 업종
    // 사전이 빠지던 어긋남을 막는다. 칸이 있으면(빈 목록이라도) 본보기를 보지 않는다.
    let mut industries: Vec<String> = vec![];
    let mut raw: Option<serde_yaml::Value> = None;
    let mut use_template = true;
    if doc_rules_path.is_file() {
        match std::fs::read_to_string(doc_rules_path).ok()
            .and_then(|t| serde_yaml::from_str::<serde_yaml::Value>(&t).ok()) {
            Some(v) => {
                if let Some(x) = v.get("industry") { raw = Some(x.clone()); use_template = false; }
            }
            // 읽기·파싱 실패는 원본(Python)처럼 업종 없음으로 본다.
            None => use_template = false,
        }
    }
    if use_template {
        let tpl = crate::doc_rules::load_scaffold_template(Some(doc_rules_path));
        raw = tpl.get(serde_yaml::Value::String("industry".to_string())).cloned();
    }
    industries.extend(clean_industry(raw.as_ref()));
    load_synonyms_with(base_dir, &industries)
}

/// 업종 값 다듬기 — 문자열 하나 또는 목록을 업종 이름 목록으로(Python clean_industry).
/// 공백을 다듬고 중복을 빼며, 파일 이름에 그대로 들어가는 값이라 경로 문자가 든 이름은 버린다.
pub fn clean_industry(raw: Option<&serde_yaml::Value>) -> Vec<String> {
    let mut items: Vec<String> = vec![];
    match raw {
        Some(serde_yaml::Value::String(s)) => items.push(s.clone()),
        Some(serde_yaml::Value::Sequence(seq)) => {
            for x in seq {
                if let Some(s) = x.as_str() { items.push(s.to_string()); }
            }
        }
        _ => {}
    }
    let mut out: Vec<String> = vec![];
    for it in items {
        let name = it.trim().to_string();
        // 경로로 새어 나갈 수 있는 글자가 든 업종 이름은 버린다.
        if name.is_empty() || name.contains('/') || name.contains('\\')
            || name.contains('.') || name.contains(':') || out.contains(&name) { continue; }
        out.push(name);
    }
    out
}

/// 사전 3겹 읽기 — 업종을 호출자가 정한다(규칙 자동 생성은 doc_rule.local.yaml 의 업종을 쓴다).
/// base_dir 는 정책 폴더(synonyms/ 하위를 먼저 보고, 없으면 폴더 바로 아래도 본다).
pub fn load_synonyms_with(base_dir: &Path, industries: &[String]) -> Syn {
    let mut names = vec![SYN_CORE.to_string()];
    for ind in industries.iter().rev() {
        names.push(format!("doc_synonyms.{}.yaml", ind));
    }
    names.push(SYN_LOCAL.to_string());

    let mut syn = Syn::default();
    // 끝말은 core 가 기준 목록을 정하고, 업종·local 은 더하기만 한다.
    let mut core_suffixes: Vec<String> = vec![];
    let mut extra_suffixes: Vec<String> = vec![];
    // 핵어 판정용 목록도 끝말과 같은 규약 — core 가 기준, 업종·local 은 더하기만.
    let mut core_heads: Vec<Vec<String>> = vec![vec![]; SYN_HEAD_LISTS.len()];
    let mut extra_heads: Vec<Vec<String>> = vec![vec![]; SYN_HEAD_LISTS.len()];
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
        let sufs = clean_suffixes(doc.get(SYN_SUFFIXES));
        if name == SYN_CORE { core_suffixes = sufs; } else { extra_suffixes.extend(sufs); }
        for (i, sec) in SYN_HEAD_LISTS.iter().enumerate() {
            // 한 글자 말도 받는다(끝말과 달리 '뜻이 한 글자'인 잡음 꼬리가 있을 수 있다).
            let words = clean_words(doc.get(*sec));
            if name == SYN_CORE { core_heads[i] = words; } else { extra_heads[i].extend(words); }
        }
        // 칸별 추가 단어 — 칸마다 따로 합치고, 센 겹(나중 파일)의 말이 앞에 온다.
        for (key, cells) in clean_extra_terms(doc.get(SYN_EXTRA)) {
            let slot = match syn.extra_terms.iter().position(|(k, _)| *k == key) {
                Some(i) => i,
                None => { syn.extra_terms.push((key, vec![])); syn.extra_terms.len() - 1 }
            };
            for (cell, words) in cells {
                let cells_v = &mut syn.extra_terms[slot].1;
                match cells_v.iter_mut().find(|(c, _)| *c == cell) {
                    Some((_, have)) => {
                        let mut merged = words;
                        for w in have.iter() { if !merged.contains(w) { merged.push(w.clone()); } }
                        *have = merged;
                    }
                    None => cells_v.push((cell, words)),
                }
            }
        }
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

    // 기준 목록은 core 에서만 받는다 — local 에만 적힌 끝말을 기준으로 삼으면 옛 core 가
    // 깔린 곳에서 내장 43개가 조용히 사라진다(원본 load_synonyms 와 같은 규칙).
    let base: Vec<String> = if core_suffixes.is_empty() {
        DOC_SUFFIXES.iter().map(|s| s.to_string()).collect()
    } else { core_suffixes };
    let mut uniq: Vec<String> = vec![];
    for w in base.into_iter().chain(extra_suffixes) {
        if !uniq.contains(&w) { uniq.push(w); }
    }
    // 긴 끝말이 먼저 — sort_by_key 는 안정 정렬이라 파이썬 sorted 와 같은 차례가 된다.
    uniq.sort_by_key(|w| std::cmp::Reverse(clen(w)));
    syn.suffixes = uniq;

    // 핵어 판정용 목록 — 같은 규약(core 가 기준, 나머지는 더하기, 긴 말 먼저).
    // 긴 말이 먼저 걸려야 '최종본' 이 '최종' 보다 먼저 걷힌다.
    syn.head_lists = (0..SYN_HEAD_LISTS.len()).map(|i| {
        let base: Vec<String> = if core_heads[i].is_empty() { vec![] } else { core_heads[i].clone() };
        let mut out: Vec<String> = vec![];
        for w in base.into_iter().chain(extra_heads[i].clone()) {
            if !out.contains(&w) { out.push(w); }
        }
        out.sort_by_key(|w| std::cmp::Reverse(clen(w)));
        out
    }).collect();
    syn
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 띄어쓰기_표기는_꼬리말_앞에서_한_번만_끊는다() {
        // 빈 목록 = 사전 없음 → 내장 DOC_SUFFIXES 를 쓴다.
        assert_eq!(spacing_forms("요구사항정의서", &[]),
                   vec!["요구사항정의서", "요구사항 정의서"]);
        // 이미 띄어쓰기가 있으면 붙임형도 만든다.
        assert_eq!(spacing_forms("사업 계획서", &[])[0..2].to_vec(),
                   vec!["사업 계획서".to_string(), "사업계획서".to_string()]);
        // '메뉴얼' 표기 변형도 끊는다(2026-09-15) — 파일명 "설치_메뉴얼" 을 받게 하려고.
        assert_eq!(spacing_forms("설치메뉴얼", &[]),
                   vec!["설치메뉴얼", "설치 메뉴얼"]);
    }

    //--------------------------------------------------------------
    // 테스트용 사전 폴더 만들기
    //=> 임시 폴더에 doc_rule.yaml 과 synonyms/ 아래 사전 파일들을 깐다.
    //   테스트마다 폴더 이름이 달라야 병렬로 돌아도 서로 덮어쓰지 않는다.
    //
    // -in: tag    = 폴더 이름에 붙일 테스트 표시
    // -in: layers = (파일 이름, yaml 본문) 목록
    // -out: PathBuf = doc_rule.yaml 경로(load_synonyms 에 넘길 값)
    //--------------------------------------------------------------
    fn syn_dir(tag: &str, layers: &[(&str, &str)]) -> PathBuf {
        let dir = std::env::temp_dir()
            .join(format!("mpc_suffix_{}_{}", tag, std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join(SYN_DIR)).unwrap();
        for (name, body) in layers {
            std::fs::write(dir.join(SYN_DIR).join(name), body).unwrap();
        }
        let rule = dir.join("doc_rule.yaml");
        std::fs::write(&rule, "doctype_rules: []\n").unwrap();
        rule
    }

    #[test]
    fn core_끝말이_기준_목록이_된다() {
        let rule = syn_dir("core", &[("doc_synonyms.core.yaml",
                                      "aliases: {}\nsuffixes:\n- 품의안\n")]);
        let syn = load_synonyms(&rule);
        assert_eq!(syn.suffixes, vec!["품의안".to_string()]);
        let v = rule_vocab("결재품의안", &syn, 10);
        assert!(v.title_terms.contains(&"결재 품의안".to_string()));
        assert!(v.filename.contains(&"결재_품의안".to_string()));
        // core 가 기준이므로 내장 끝말(보고서)은 쓰이지 않는다.
        assert!(!rule_vocab("월간보고서", &syn, 10).title_terms
                .contains(&"월간 보고서".to_string()));
    }

    #[test]
    fn 옛_core_와_local_끝말은_내장_목록에_더해진다() {
        let rule = syn_dir("local", &[("doc_synonyms.core.yaml", "aliases: {}\n"),
                                      ("doc_synonyms.local.yaml", "suffixes: [품의안]\n")]);
        let syn = load_synonyms(&rule);
        assert_eq!(syn.suffixes.len(), DOC_SUFFIXES.len() + 1);
        assert!(rule_vocab("결재품의안", &syn, 10).title_terms
                .contains(&"결재 품의안".to_string()));
        assert!(rule_vocab("월간보고서", &syn, 10).title_terms
                .contains(&"월간 보고서".to_string()));
    }

    #[test]
    fn 규칙파일이_없으면_본보기_업종을_쓴다() {
        let rule = syn_dir("industry", &[
            ("doc_synonyms.core.yaml", "aliases:\n  보고서: [리포트]\n"),
            ("doc_synonyms.finance.yaml", "aliases:\n  보고서: [여신보고]\n")]);
        let dir = rule.parent().unwrap().to_path_buf();
        std::fs::write(dir.join(crate::doc_rules::TEMPLATE_NAME), "industry: [finance]\n").unwrap();
        let alias = |syn: &Syn| Syn::get(&syn.aliases, "보고서").cloned().unwrap_or_default();

        // 1) 파일 없음 → 본보기 업종
        std::fs::remove_file(&rule).unwrap();
        assert_eq!(alias(&load_synonyms(&rule)), vec!["여신보고", "리포트"]);
        // 2) 파일은 있고 칸이 없음 → 본보기 업종
        std::fs::write(&rule, "doctype_rules: []\n").unwrap();
        assert_eq!(alias(&load_synonyms(&rule)), vec!["여신보고", "리포트"]);
        // 3) 칸이 있으면 빈 목록이라도 본보기를 보지 않는다
        std::fs::write(&rule, "industry: []\ndoctype_rules: []\n").unwrap();
        assert_eq!(alias(&load_synonyms(&rule)), vec!["리포트"]);
    }

    #[test]
    fn 칸별_추가_단어는_적힌_칸_뒤에만_붙는다() {
        let rule = syn_dir("extra", &[
            ("doc_synonyms.core.yaml",
             "aliases:\n  보고서: [결과보고, 리포트]\nextra_terms:\n  보고서:\n    filename: [동향보고]\n"),
            ("doc_synonyms.local.yaml",
             "extra_terms:\n  보 고서:\n    title_terms: [현황분석, 보고서, 참관]\n    head_terms: [현황분석]\n    terms: 현황분석\n    titel_terms: [오타칸]\n")]);
        let syn = load_synonyms(&rule);
        let mut plain = syn.clone();
        plain.extra_terms.clear();
        let base = rule_vocab("보고서", &plain, 2);
        let v = rule_vocab("보고서", &syn, 2);
        let with = |a: &Vec<String>, more: &[&str]| {
            let mut x = a.clone(); x.extend(more.iter().map(|s| s.to_string())); x
        };
        // 자동 말은 앞에 그대로, 조정한 말은 뒤에(이미 있는 '보고서' 는 안 겹친다)
        assert_eq!(v.title_terms, with(&base.title_terms, &["현황분석", "참관"]));
        assert_eq!(v.head_terms, with(&base.head_terms, &["현황분석"]));
        // 목록이 아닌 칸·모르는 칸은 버린다 → 본문은 그대로
        assert_eq!(v.terms, base.terms);
        // core 에만 적힌 칸(filename)은 local 이 다른 칸을 적었어도 남는다
        assert_eq!(v.filename, with(&base.filename, &["동향보고"]));
    }

    #[test]
    fn 끝말_검증과_긴_것부터_정렬() {
        let rule = syn_dir("clean", &[("doc_synonyms.core.yaml",
            "suffixes: [서, '관 리', '', 정의서, 정의서, 사항정의서]\n")]);
        let syn = load_synonyms(&rule);
        // 한 글자·공백 포함·빈 값·중복은 버리고, 긴 끝말이 앞에 온다.
        assert_eq!(syn.suffixes, vec!["사항정의서".to_string(), "정의서".to_string()]);
        // 모양이 목록이 아니면 그 칸은 없는 것으로 보고 내장 목록을 쓴다.
        let rule2 = syn_dir("shape", &[("doc_synonyms.core.yaml", "suffixes: 품의안\n")]);
        assert_eq!(load_synonyms(&rule2).suffixes.len(), DOC_SUFFIXES.len());
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
                // 어느 제목에서 구운 말인지 적어 둔다(설계서 7장 ③) — 나중에 제목이
                // 바뀌면 화면이 그 사실을 정확히 알릴 수 있다. 판정은 읽지 않는다.
                m.insert(ystr(FILLED_FROM), ystr(title));
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
                        m.insert(ystr(FILLED_FROM), ystr(title));
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
                    if grew {
                        // 지금 제목에서 나온 말을 덧붙였으니 기준 제목도 옮긴다.
                        // 안 옮기면 방금 맞춘 규칙이 계속 "제목이 바뀌었습니다"로 뜬다.
                        m.insert(ystr(FILLED_FROM), ystr(title));
                        enriched += 1;
                    }
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
