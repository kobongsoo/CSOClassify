//! 분류체계 제목에서 '핵어' 유도하기 — Python 판 classify/taxhead.py 포팅.
//!
//! 한국어 복합명사는 마지막 명사가 그 말의 정체다 — "보안전송관리시스템_작업
//! 계획서" 는 계획서이지 시스템이 아니다. 그래서 파일 이름의 '끝자리'에 어떤
//! 분류의 제목(=핵어)이 오면, 그 분류의 약한 후보로 삼는다(설계서 13장 D1).
//!
//! [핵어는 어디서 오나] 새 파일을 만들지 않는다. 고객 분류체계 파일
//! (doc_taxonomy.yaml)의 잎 제목이 곧 핵어이고, 거기에 core 사전의 유의어를
//! 얹는다. 판정할 때마다 다시 계산하므로 고객이 제목을 바꾸면 저절로 따라간다.
//!
//! [제목이 다 핵어는 아니다] 세 겹으로 거르고 기본값은 "쓴다"로 둔다.
//!   ⓐ 자동 판정   — judge_title(). 대부분 여기서 끝난다
//!   ⓑ 말 단위 제외 — doc_rule.yaml 의 taxonomy_title_exclude
//!   ⓒ 노드 단위 끄기 — doc_rule.yaml 의 taxonomy_node_off(끌 당시 제목을 함께 적는다)
//!
//! [글자 단위 주의] 원본이 파이썬이라 길이·자르기가 '글자' 단위다. 한글은 UTF-8
//! 로 3바이트이므로 자르는 곳마다 글자 수로 센다.

use std::collections::{HashMap, HashSet};

use once_cell::sync::Lazy;
use regex::Regex;

use crate::axes::Taxonomy;
use crate::docvocab::{self, Syn};

/// 잡음 꼬리 중 '말'이 아닌 것들 — 구분자·괄호 묶음·버전·일련번호·날짜.
/// 사전에 적을 수 없는 모양이라(정규식) 코드가 들고 있다.
static TAIL_PATTERNS: Lazy<Vec<Regex>> = Lazy::new(|| {
    vec![
        Regex::new(r"[\s_\-.~,+]+$").unwrap(),
        Regex::new(r"\([^()]*\)$").unwrap(),
        Regex::new(r"\[[^\[\]]*\]$").unwrap(),
        Regex::new(r"(?i)(?:ver(?:sion)?|rev|v|r)\.?\s*\d+(?:\.\d+)*$").unwrap(),
        Regex::new(r"#\s*\d+$").unwrap(),
        Regex::new(r"\d+(?:[.\-]\d+)*$").unwrap(),
    ]
});

fn clen(s: &str) -> usize { s.chars().count() }

/// 공백을 모두 지운 사본(파이썬 replace(" ","")·\s+ 제거와 같은 자리).
fn tight(s: &str) -> String {
    s.chars().filter(|c| !c.is_whitespace()).collect()
}

//------------------------------------------------------------------
// 문서종류 끝말 모으기
//=> 제목이 문서종류를 말하는지는 끝말로 가린다. core 사전의 꼬리말(tails) 열쇠와
//   띄어쓰기 끝말(suffixes)이 이미 "문서종류 명사" 목록이라 그대로 합쳐 쓴다.
//   긴 끝말이 먼저 걸려야 "규정집" 이 "규정" 보다 우선한다.
//
// -in: syn = docvocab::load_synonyms() 결과
// -out: Vec<String> = 문서종류 끝말(긴 것부터, 같은 길이면 가나다순)
//------------------------------------------------------------------
pub fn doctype_endings(syn: &Syn) -> Vec<String> {
    let mut set: HashSet<String> = syn.tails.iter().map(|(k, _)| k.clone()).collect();
    if syn.suffixes.is_empty() {
        set.extend(docvocab::DOC_SUFFIXES.iter().map(|s| s.to_string()));
    } else {
        set.extend(syn.suffixes.iter().cloned());
    }
    let mut out: Vec<String> = set.into_iter().collect();
    // 파이썬 sorted(set, key=len, reverse=True) 는 집합 차례가 들쭉날쭉하다.
    // 두 판이 같은 답을 내도록 길이가 같으면 가나다순으로 못 박는다.
    out.sort_by(|a, b| clen(b).cmp(&clen(a)).then(a.cmp(b)));
    out
}

//------------------------------------------------------------------
// 잡음 꼬리 걷어내기
//=> 파일명 끝에 붙는 관리용 꼬리를 오른쪽부터 더 걷을 게 없을 때까지 걷는다.
//   "제안서_최종_v2(수정본)" → "제안서". 남은 끝이 핵어 자리다.
//
// -in: s     = 파일명 줄기(확장자 뺀 것) 또는 제목 줄
// -in: tails = 잡음 꼬리 말 목록(비면 내장 NOISE_TAILS)
// -out: String = 꼬리를 걷은 문자열
//------------------------------------------------------------------
pub fn strip_noise(s: &str, tails: &[String]) -> String {
    let mut cur = s.trim().to_string();
    if cur.is_empty() { return String::new(); }
    let owned: Vec<String> = if tails.is_empty() {
        docvocab::NOISE_TAILS.iter().map(|x| x.to_string()).collect()
    } else { tails.to_vec() };
    loop {
        let before = cur.clone();
        for p in TAIL_PATTERNS.iter() {
            cur = p.replace(&cur, "").trim().to_string();
        }
        let low = cur.to_lowercase();
        for w in &owned {
            let w = w.to_lowercase();
            // 꼬리만 걷는다 — 제목 전체가 그 말이면 걷지 않는다("초안" 자체는 남긴다)
            if !w.is_empty() && clen(&low) > clen(&w) && low.ends_with(&w) {
                let keep = clen(&cur) - clen(&w);
                cur = cur.chars().take(keep).collect::<String>().trim().to_string();
                break;
            }
        }
        if cur == before || cur.is_empty() {
            return if cur.is_empty() { before } else { cur };
        }
    }
}

//------------------------------------------------------------------
// 어떤 말이 '끝자리'에 오는가
//=> 공백을 뺀 소문자 문자열이 그 말로 끝나는지 본다. 영문 말은 앞 글자가
//   영숫자면 인정하지 않는다("preport" 가 "report" 로 걸리지 않게).
//
// -in: s     = 잡음 걷은 문자열
// -in: terms = 찾을 말 목록
// -out: Option<String> = 끝자리에 온 말(없으면 None)
//------------------------------------------------------------------
pub fn head_hit(s: &str, terms: &[String]) -> Option<String> {
    let hay = tight(s).to_lowercase();
    if hay.is_empty() { return None; }
    let hay_chars: Vec<char> = hay.chars().collect();
    for t in terms {
        let n = tight(t).to_lowercase();
        if n.is_empty() || !hay.ends_with(&n) { continue; }
        let nlen = clen(&n);
        if n.is_ascii() && hay_chars.len() > nlen {
            let prev = hay_chars[hay_chars.len() - nlen - 1];
            if prev.is_ascii_alphanumeric() { continue; }
        }
        return Some(t.clone());
    }
    None
}

//------------------------------------------------------------------
// 제목 하나를 핵어로 써도 되는지 자동 판정 (ⓐ)
//=> 위에서부터 처음 걸린 이유 하나로 거른다. 기본값은 "쓴다" 다 — 1차 분류
//   실패의 대부분은 못 잡는 것(재현율)이라 제목을 괜히 끄는 손해가 더 크다.
//    1) 제목이 통째로 넓은 말이면 안 쓴다               (broad_exact)
//    2) 끝이 묶음 말(○○자료·○○서류)이면 안 쓴다       (container)
//    3) 끝이 문서종류 명사가 아니면 — 주제형 제목 — 안 쓴다 (not_doctype)
//    4) 가장 긴 끝말이 흔한 말(규정·지침…)이면 안 쓴다   (common_head)
//    5) 다른 분류 제목이 이 제목으로 끝나거나 같으면 모호해서 안 쓴다 (ambiguous)
//
// -in: title   = 분류 제목(띄어쓰기는 무시한다)
// -in: others  = 다른 분류들의 제목(공백 뺀 것)
// -in: endings = doctype_endings() 결과
// -in: syn     = 사전(목록 출처)
// -out: (bool, &'static str) = (쓸 수 있으면 true, 걸린 이유 — 쓰면 "ok")
//------------------------------------------------------------------
pub fn judge_title(title: &str, others: &[String], endings: &[String], syn: &Syn)
                   -> (bool, &'static str) {
    let t = tight(title);
    if t.is_empty() { return (false, "empty"); }
    if syn.head_list("broad_words").iter().any(|w| *w == t) {
        return (false, "broad_exact");
    }
    // 앞부분이 한 글자뿐이면 묶음 판정을 하지 않는다("서류" 자체는 1)에서 걸린다)
    if syn.head_list("container_tails").iter()
        .any(|c| t.ends_with(c.as_str()) && clen(&t) > clen(c)) {
        return (false, "container");
    }
    // endings 는 긴 것부터라 처음 걸린 것이 가장 긴 끝말이다
    let ending = match endings.iter().find(|e| t.ends_with(e.as_str())) {
        Some(e) => e.clone(), None => return (false, "not_doctype"),
    };
    if syn.head_list("common_endings").iter().any(|w| *w == ending) {
        return (false, "common_head");
    }
    // 같은 핵어가 여러 잎에 걸리면 어디로 보낼지 정할 수 없다
    if others.iter().any(|o| *o == t || (o.ends_with(t.as_str()) && *o != t)) {
        return (false, "ambiguous");
    }
    (true, "ok")
}

/// 분류 하나의 핵어 묶음.
#[derive(Clone, Debug)]
pub struct HeadEntry {
    /// 분류 제목(공백 뺀 것) — 판정 근거에 출처로 적는다.
    pub title: String,
    /// 제목 + core 사전 유의어.
    pub heads: Vec<String>,
}

//------------------------------------------------------------------
// 핵어를 끌어다 쓸 분류 고르기
//=> docvocab::is_drawer 와 같은 기준 — 꺼 둔 분류(status≠1)와 서랍(자식이 있는
//   뿌리)은 뺀다. 화면의 [분류 불러오기]와 판정이 같은 범위를 보게 한다.
//
// -in: taxonomy = 분류체계
// -out: Vec<(dc_id, 제목)> = dc_id 순
//------------------------------------------------------------------
pub fn headable_nodes(taxonomy: &Taxonomy) -> Vec<(String, String)> {
    let active = taxonomy.active_nodes();
    let has_kids: HashSet<String> = active.iter()
        .filter_map(|n| n.parent.clone()).collect();
    let mut out: Vec<(String, String)> = active.iter()
        .filter(|n| !docvocab::is_drawer(n.parent.is_some(), has_kids.contains(&n.dc_id)))
        .map(|n| (n.dc_id.clone(), n.title.clone()))
        .collect();
    out.sort_by(|a, b| a.0.cmp(&b.0));
    out
}

//------------------------------------------------------------------
// 분류체계 → 분류별 핵어 사전 만들기 (ⓐ+ⓑ+ⓒ)
//=> 잎 제목을 ⓐ 로 거르고, 통과한 제목과 core 사전의 유의어를 그 분류의 핵어로
//   삼는다. 파일명 전용 말(filename_only — 규정·지침 계열)은 넣지 않는다.
//   그 말들이야말로 파일명에 흔해서, 끝자리에 와도 한 분류로 보낼 수 없다.
//
// -in: taxonomy = 분류체계
// -in: syn      = 사전
// -in: excludes = ⓑ 말 단위 제외 목록
// -in: node_off = ⓒ 노드 끄기 목록 (dc_id, 끌 당시 제목)
// -out: (HashMap<dc_id, HeadEntry>, Vec<String>) = 핵어 사전, 경고 목록
//------------------------------------------------------------------
pub fn build_head_lexicon(taxonomy: &Taxonomy, syn: &Syn,
                          excludes: &[String], node_off: &[(String, String)])
                          -> (HashMap<String, HeadEntry>, Vec<String>) {
    let nodes = headable_nodes(taxonomy);
    if nodes.is_empty() { return (HashMap::new(), vec![]); }
    let endings = doctype_endings(syn);
    let tights: Vec<(String, String)> = nodes.iter()
        .map(|(id, title)| (id.clone(), tight(title))).collect();
    let skip: HashSet<String> = excludes.iter()
        .map(|w| tight(w)).filter(|w| !w.is_empty()).collect();

    let mut warnings: Vec<String> = vec![];
    let mut off: HashSet<String> = HashSet::new();
    for (dc_id, was_raw) in node_off {
        let now = match tights.iter().find(|(id, _)| id == dc_id) {
            Some((_, t)) => t.clone(),
            None => continue,   // 분류체계에 없는 노드 — 다른 검증이 이미 알린다
        };
        let was = tight(was_raw);
        if !was.is_empty() && was != now {
            // 끄기가 저절로 무효가 되는 자리다. 조용히 계속 꺼져 있지도,
            // 조용히 다시 켜지지도 않게 사람에게 알린다.
            warnings.push(format!(
                "[taxonomy_node_off] {}: 끌 당시 제목과 지금 제목이 다릅니다 \
                 — 이 끄기는 무효로 두고 핵어를 씁니다. 재검토하세요", dc_id));
            continue;
        }
        off.insert(dc_id.clone());
    }

    let mut lexicon: HashMap<String, HeadEntry> = HashMap::new();
    for (dc_id, title) in &nodes {
        let t = tight(title);
        if t.is_empty() || skip.contains(&t) || off.contains(dc_id) { continue; }
        let others: Vec<String> = tights.iter()
            .filter(|(id, _)| id != dc_id).map(|(_, x)| x.clone()).collect();
        let (use_it, _why) = judge_title(title, &others, &endings, syn);
        if !use_it { continue; }
        let mut heads = vec![t.clone()];
        for w in docvocab::synonyms_of(&t, syn, false) {
            if !skip.contains(&w) { heads.push(w); }
        }
        lexicon.insert(dc_id.clone(), HeadEntry { title: t, heads });
    }
    (lexicon, warnings)
}

//------------------------------------------------------------------
// 파일 이름의 끝자리가 어느 분류의 핵어인가
//=> 폴더를 뺀 파일 이름에서 확장자를 떼고, 잡음 꼬리를 걷은 뒤 끝자리를 본다.
//   여러 분류에 걸리면 가장 긴 말이 걸린 쪽 하나만 돌려준다 — 짧은 말이 긴 말을
//   이기면 "결과보고서" 가 "보고서" 분류로 가 버린다.
//
// -in: file    = 파일 경로
// -in: lexicon = build_head_lexicon() 결과
// -in: tails   = 잡음 꼬리 말 목록(비면 내장 기본값)
// -out: Option<(dc_id, 걸린 말, 분류 제목)>
//------------------------------------------------------------------
pub fn match_filename_head(file: &str, lexicon: &HashMap<String, HeadEntry>, tails: &[String])
                           -> Option<(String, String, String)> {
    if lexicon.is_empty() { return None; }
    let base = std::path::Path::new(file).file_stem()
        .map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    let stem = strip_noise(&base, tails);
    if stem.is_empty() { return None; }
    // dc_id 순으로 봐야 길이가 같을 때 두 판의 답이 같다.
    let mut ids: Vec<&String> = lexicon.keys().collect();
    ids.sort();
    let mut best: Option<(String, String, String)> = None;
    for dc_id in ids {
        let item = &lexicon[dc_id];
        if let Some(hit) = head_hit(&stem, &item.heads) {
            let better = match &best { None => true, Some((_, w, _)) => clen(&hit) > clen(w) };
            if better { best = Some((dc_id.clone(), hit, item.title.clone())); }
        }
    }
    best
}

#[cfg(test)]
mod tests {
    use super::*;

    fn syn_empty() -> Syn { Syn::default() }

    fn sv(xs: &[&str]) -> Vec<String> { xs.iter().map(|s| s.to_string()).collect() }

    #[test]
    fn 잡음_꼬리를_오른쪽부터_걷는다() {
        assert_eq!(strip_noise("제안서_최종_v2", &[]), "제안서");
        assert_eq!(strip_noise("회의록(수정본)", &[]), "회의록");
        assert_eq!(strip_noise("계약서 20260101", &[]), "계약서");
        // 제목 전체가 잡음 말이면 걷지 않는다 — 빈 문자열을 돌려주면 안 된다.
        assert_eq!(strip_noise("최종", &[]), "최종");
    }

    #[test]
    fn 끝자리에_온_말만_잡는다() {
        assert_eq!(head_hit("사업계획서", &sv(&["계획서"])), Some("계획서".to_string()));
        // 끝이 아니면 안 잡는다 — 가방 매칭과 다른 점이다.
        assert_eq!(head_hit("계획서검토회의록", &sv(&["계획서"])), None);
        // 영문은 단어 경계를 본다.
        assert_eq!(head_hit("preport", &sv(&["report"])), None);
        assert_eq!(head_hit("2026_report", &sv(&["report"])), Some("report".to_string()));
    }

    #[test]
    fn 넓은_제목과_묶음_제목은_핵어가_아니다() {
        let syn = syn_empty();
        let end = doctype_endings(&syn);
        assert_eq!(judge_title("자료", &[], &end, &syn), (false, "broad_exact"));
        assert_eq!(judge_title("행사자료", &[], &end, &syn), (false, "container"));
        assert_eq!(judge_title("복리후생", &[], &end, &syn), (false, "not_doctype"));
        assert_eq!(judge_title("사내규정", &[], &end, &syn), (false, "common_head"));
        assert_eq!(judge_title("회의록", &[], &end, &syn), (true, "ok"));
    }

    #[test]
    fn 같은_끝말을_쓰는_분류가_있으면_모호하다() {
        let syn = syn_empty();
        let end = doctype_endings(&syn);
        // '점검보고서' 가 함께 있으면 '보고서' 는 어디로 보낼지 정할 수 없다.
        assert_eq!(judge_title("보고서", &sv(&["점검보고서"]), &end, &syn), (false, "ambiguous"));
        assert_eq!(judge_title("점검보고서", &sv(&["보고서"]), &end, &syn), (true, "ok"));
    }

    #[test]
    fn 파일명_끝자리에서_가장_긴_핵어가_이긴다() {
        let mut lex = HashMap::new();
        lex.insert("DC_A".to_string(),
                   HeadEntry { title: "보고서".into(), heads: sv(&["보고서"]) });
        lex.insert("DC_B".to_string(),
                   HeadEntry { title: "결과보고서".into(), heads: sv(&["결과보고서"]) });
        let hit = match_filename_head(r"D:\x\2026 사업 결과보고서_최종.hwp", &lex, &[]);
        assert_eq!(hit.map(|(id, w, _)| (id, w)),
                   Some(("DC_B".to_string(), "결과보고서".to_string())));
        // 끝자리가 아니면 아무 분류도 안 붙는다.
        assert!(match_filename_head(r"D:\x\보고서_양식.hwp", &lex, &[]).is_none());
    }
}
