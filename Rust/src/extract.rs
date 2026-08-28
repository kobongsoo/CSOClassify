//! 포맷별 텍스트 추출 (Python extract/* 포팅)
//! PoC 지원: text · html · docx · xlsx · pptx · hwpx.
//! 구형 doc/ppt(OLE)는 ole.rs + office_legacy.rs 로 직접 파싱한다(상용 컴포넌트 없이).
//! 구형 xls(BIFF8)·hwp5(OLE+raw deflate)도 직접 파싱한다.
//! 미지원: 이미지(OCR) · 암호화 문서.

use std::fs;
use std::io::Read;
use std::path::Path;

use once_cell::sync::Lazy;
use regex::Regex;

use crate::detect::Fmt;

/// 포맷에 맞는 추출기를 호출. 미지원/실패면 None(→ 상위에서 추출실패 처리).
pub fn extract_text(path: &Path, fmt: Fmt) -> Option<String> {
    extract_raw(path, fmt).map(|t| normalize_newlines(&t))
}

/// 윈도우 개행(CR+LF)·맥 개행(CR)을 LF 하나로 통일한다.
/// [왜 필요한가] Python 쪽 clean.clean_text 가 같은 정규화를 하는데 여기에는
/// 없었다. 그래서 같은 문서인데도 "앞 400자"(표제부)가 가리키는 범위가 두
/// 구현 사이에서 달라졌다 — CR+LF 가 2글자로 세어지기 때문이다. 실측에서
/// 표제부 경계(390번째 글자)에 걸친 단어 하나가 Rust 에서만 안 걸리는
/// 차이로 나타났다(설계서 14-3 교차 검증).
fn normalize_newlines(s: &str) -> String {
    // 절대다수 경로 — CR 이 없으면 굳이 문자열을 새로 만들지 않는다.
    if !s.as_bytes().contains(&b'\r') {
        return s.to_string();
    }
    s.replace("\r\n", "\n").replace('\r', "\n")
}

fn extract_raw(path: &Path, fmt: Fmt) -> Option<String> {
    match fmt {
        Fmt::Text => plaintext(path),
        Fmt::Html => html(path),
        // 문단 인식: 문단 내 런(run)을 이어붙이고 문단 사이만 개행. Python 과 동일하게
        // 하여 런 경계로 쪼개진 값(예: 절번호 "4.2.2.2.")이 갈라져 오탐되는 것을 막는다.
        Fmt::Docx => ooxml_para(path, &["word/document.xml"], "w:p", "w:t"),
        Fmt::Pptx => ooxml_prefix_para(path, "ppt/slides/slide", "a:p", "a:t"),
        // HWPX 는 <hp:t> 태그 단위(실측 sim 1.0) 유지.
        Fmt::Hwpx => ooxml_prefix_tag(path, "Contents/section", "hp:t"),
        Fmt::Xlsx => xlsx(path),
        Fmt::Pdf => pdf_text(path),
        // 구형 Office(OLE): 파일 전체를 읽어 CFB 컨테이너에서 본문 스트림을 꺼낸다.
        Fmt::Doc => fs::read(path).ok().and_then(crate::office_legacy::doc_text),
        Fmt::Ppt => fs::read(path).ok().and_then(crate::office_legacy::ppt_text),
        Fmt::Xls => fs::read(path).ok().and_then(crate::xls::xls_text),
        Fmt::Hwp => fs::read(path).ok().and_then(crate::hwp5::hwp5_text),
        // 미지원 포맷: 추출 불가(별도 과제)
        _ => None,
    }
}

/// pdfium 라이브러리 경로 결정: 환경변수 CSO_PDFIUM → exe 옆 pdfium.dll → 시스템.
fn pdfium_lib_path() -> Option<std::path::PathBuf> {
    if let Ok(p) = std::env::var("CSO_PDFIUM") {
        let pb = std::path::PathBuf::from(p);
        if pb.is_file() { return Some(pb); }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            for name in ["pdfium.dll", "libpdfium.so", "libpdfium.dylib"] {
                let pb = dir.join(name);
                if pb.is_file() { return Some(pb); }
            }
        }
    }
    None
}

/// PDF 텍스트층 추출 (pdfium-render). pdfium 동적 라이브러리가 있어야 한다.
/// 스캔(이미지)본은 텍스트층이 없어 빈 문자열이 될 수 있다(그건 상위에서 미분류 처리).
fn pdf_text(path: &Path) -> Option<String> {
    use pdfium_render::prelude::*;
    // 바인딩: 지정 경로 우선, 없으면 시스템 라이브러리.
    let bindings = match pdfium_lib_path() {
        Some(p) => Pdfium::bind_to_library(p).ok()?,
        None => Pdfium::bind_to_system_library().ok()?,
    };
    let pdfium = Pdfium::new(bindings);
    let doc = pdfium.load_pdf_from_file(path, None).ok()?;
    let mut out = String::new();
    for page in doc.pages().iter() {
        if let Ok(t) = page.text() {
            out.push_str(&t.all());
            out.push('\n');
        }
    }
    Some(out)
}

/// 바이트 → 문자열 디코드: UTF-8(BOM) → cp949(EUC-KR) → latin-1.
fn decode_bytes(bytes: &[u8]) -> String {
    // UTF-8 BOM
    let b = if bytes.starts_with(&[0xEF, 0xBB, 0xBF]) { &bytes[3..] } else { bytes };
    if let Ok(s) = std::str::from_utf8(b) {
        return s.to_string();
    }
    // cp949 / EUC-KR
    let (cow, _enc, had_err) = encoding_rs::EUC_KR.decode(b);
    if !had_err {
        return cow.into_owned();
    }
    // latin-1 (무손실 폴백)
    b.iter().map(|&c| c as char).collect()
}

/// 평문 파일 읽기.
fn plaintext(path: &Path) -> Option<String> {
    let bytes = fs::read(path).ok()?;
    Some(decode_bytes(&bytes))
}

static RE_SCRIPT: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?is)<(script|style|head)[^>]*>.*?</\s*(script|style|head)\s*>").unwrap());
static RE_TAG: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?s)<[^>]+>").unwrap());
static RE_WS: Lazy<Regex> = Lazy::new(|| Regex::new(r"[ \t\x0b\x0c\r]+").unwrap());
static RE_CDATA: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?s)<!\[CDATA\[(.*?)\]\]>").unwrap());

/// XML 요소 안쪽 → 순수 텍스트(태그 제거 · 엔티티 복원 · CDATA 보존).
///
/// [CDATA 를 따로 다루는 이유] `<t><![CDATA[이름]]></t>` 처럼 값이 CDATA 로 감싸여
/// 오는 문서가 있다(엑셀의 inlineStr 이 대표적). 그런데 태그 제거 정규식 `<[^>]+>`
/// 은 `<![CDATA[이름]]>` 을 통째로 '태그 하나'로 보고 지워 버린다 — `<` 부터 처음
/// 만나는 `>`(=`]]>` 의 `>`)까지가 한 덩어리로 잡히기 때문이다. 그러면 셀 값이 전부
/// 사라져 **문서 전체가 빈 텍스트**가 되고, 아무 신호도 못 잡는다(실측: 이메일 787건·
/// 전화 647건이 든 주소록이 '검출 없음'으로 나왔다).
/// 그래서 CDATA 구간을 먼저 떼어 내 있는 그대로 살리고, 나머지 구간만 태그 제거·
/// 엔티티 복원을 한다(CDATA 안의 `&amp;` 는 원문 그대로가 규약이라 복원하지 않는다).
fn xml_inner_text(inner: &str) -> String {
    let mut out = String::new();
    let mut last = 0usize;
    for c in RE_CDATA.captures_iter(inner) {
        let whole = c.get(0).unwrap();
        let before = &inner[last..whole.start()];
        out.push_str(&unescape_entities(&RE_TAG.replace_all(before, "")));
        out.push_str(c.get(1).unwrap().as_str());
        last = whole.end();
    }
    out.push_str(&unescape_entities(&RE_TAG.replace_all(&inner[last..], "")));
    out
}

/// HTML → 텍스트(태그 제거 + 엔티티 복원).
fn html(path: &Path) -> Option<String> {
    let bytes = fs::read(path).ok()?;
    let raw = decode_bytes(&bytes);
    let no_head = RE_SCRIPT.replace_all(&raw, " ");
    // 블록 태그를 개행으로 바꿔 낱말이 붙지 않게.
    let block = Regex::new(r"(?is)</?(p|div|br|tr|li|h[1-6]|table|ul|ol|section|article|header|footer)[^>]*>").unwrap();
    let spaced = block.replace_all(&no_head, "\n");
    // 태그 제거·엔티티 복원은 CDATA 를 살리는 공통 헬퍼로 (xhtml 에도 CDATA 가 온다).
    let text = xml_inner_text(&spaced);
    Some(RE_WS.replace_all(&text, " ").to_string())
}

/// 정확 이름 항목들에서 문단 인식 추출.
fn ooxml_para(path: &Path, entries: &[&str], para_tag: &str, text_tag: &str) -> Option<String> {
    let file = fs::File::open(path).ok()?;
    let mut zip = zip::ZipArchive::new(file).ok()?;
    let mut out = String::new();
    for name in entries {
        if let Ok(mut f) = zip.by_name(name) {
            let mut data = String::new();
            if f.read_to_string(&mut data).is_ok() {
                collect_paragraphs(&data, para_tag, text_tag, &mut out);
            }
        }
    }
    Some(out)
}

/// 이름 접두 항목들(정렬)에서 문단 인식 추출.
fn ooxml_prefix_para(path: &Path, prefix: &str, para_tag: &str, text_tag: &str) -> Option<String> {
    for_prefix(path, prefix, |data, out| collect_paragraphs(data, para_tag, text_tag, out))
}

/// 이름 접두 항목들(정렬)에서 태그 단위 추출(개행 구분).
fn ooxml_prefix_tag(path: &Path, prefix: &str, tag: &str) -> Option<String> {
    for_prefix(path, prefix, |data, out| collect_tag_text(data, tag, out))
}

/// 접두 매칭 항목을 번호순으로 읽어 콜백에 넘긴다(공통).
fn for_prefix<F: Fn(&str, &mut String)>(path: &Path, prefix: &str, f: F) -> Option<String> {
    let file = fs::File::open(path).ok()?;
    let mut zip = zip::ZipArchive::new(file).ok()?;
    let mut names: Vec<String> = Vec::new();
    for i in 0..zip.len() {
        if let Ok(zf) = zip.by_index(i) {
            let n = zf.name().to_string();
            if n.starts_with(prefix) && n.ends_with(".xml") {
                names.push(n);
            }
        }
    }
    names.sort_by_key(|n| num_in_name(n));
    let mut out = String::new();
    for name in names {
        if let Ok(mut zf) = zip.by_name(&name) {
            let mut data = String::new();
            if zf.read_to_string(&mut data).is_ok() {
                f(&data, &mut out);
            }
        }
    }
    Some(out)
}

/// 문단 인식: 각 <para>...</para> 안의 <text> 런을 '이어붙여' 한 줄, 문단 사이 개행.
fn collect_paragraphs(xml: &str, para_tag: &str, text_tag: &str, out: &mut String) {
    let para_re = Regex::new(&format!(r"(?s)<{p}(?:\s[^>]*)?>(.*?)</{p}>", p = regex::escape(para_tag))).unwrap();
    let text_re = Regex::new(&format!(r"(?s)<{t}(?:\s[^>]*)?>(.*?)</{t}>", t = regex::escape(text_tag))).unwrap();
    for pcap in para_re.captures_iter(xml) {
        let mut line = String::new();
        for tcap in text_re.captures_iter(&pcap[1]) {
            line.push_str(&xml_inner_text(&tcap[1]));
        }
        if !line.is_empty() {
            out.push_str(&line);
            out.push('\n');
        }
    }
}

/// xlsx: 공유문자열표 + 워크시트 인라인 문자열(inlineStr)의 <t> 를 모은다(분류엔 충분).
/// ※ sharedStrings 없이 시트에 인라인 문자열만 쓰는 파일도 있어 워크시트도 함께 훑는다.
fn xlsx(path: &Path) -> Option<String> {
    let file = fs::File::open(path).ok()?;
    let mut zip = zip::ZipArchive::new(file).ok()?;
    // 읽을 항목 이름을 먼저 모은다(공유문자열표 + 모든 워크시트).
    let mut names: Vec<String> = Vec::new();
    for i in 0..zip.len() {
        if let Ok(zf) = zip.by_index(i) {
            let n = zf.name().to_string();
            if n == "xl/sharedStrings.xml"
                || (n.starts_with("xl/worksheets/") && n.ends_with(".xml")) {
                names.push(n);
            }
        }
    }
    // 공유문자열표를 먼저(있으면), 그 다음 워크시트 순.
    names.sort_by_key(|n| if n.contains("sharedStrings") { 0 } else { 1 });
    let mut out = String::new();
    for name in names {
        if let Ok(mut f) = zip.by_name(&name) {
            let mut data = String::new();
            if f.read_to_string(&mut data).is_ok() {
                collect_tag_text(&data, "t", &mut out);
            }
        }
    }
    Some(out)
}

/// 문서 XML 에서 <TAG ...>...</TAG> 안의 텍스트를 뽑아(내부태그 제거·엔티티 복원) out 에 개행으로 추가.
fn collect_tag_text(xml: &str, tag: &str, out: &mut String) {
    // <tag ...>content</tag> — DOTALL, 최소매치. tag 의 ':' 는 정규식에서 리터럴.
    let pat = format!(r"(?s)<{t}(?:\s[^>]*)?>(.*?)</{t}>", t = regex::escape(tag));
    let re = Regex::new(&pat).unwrap();
    for cap in re.captures_iter(xml) {
        let text = xml_inner_text(&cap[1]);
        if !text.is_empty() {
            out.push_str(&text);
            out.push('\n');
        }
    }
}

/// zip 항목 이름 속 숫자(정렬키). 예: section10.xml > section2.xml.
fn num_in_name(name: &str) -> u32 {
    let digits: String = name.chars().filter(|c| c.is_ascii_digit()).collect();
    digits.parse().unwrap_or(0)
}

/// XML/HTML 기본 엔티티 복원.
fn unescape_entities(s: &str) -> String {
    let mut r = s.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&apos;", "'")
        .replace("&#39;", "'")
        .replace("&nbsp;", " ");
    // 숫자 엔티티 &#NN; / &#xHH;
    static RE_NUM: Lazy<Regex> = Lazy::new(|| Regex::new(r"&#(x?)([0-9A-Fa-f]+);").unwrap());
    r = RE_NUM.replace_all(&r, |c: &regex::Captures| {
        let hex = &c[1] == "x";
        let code = u32::from_str_radix(&c[2], if hex { 16 } else { 10 }).unwrap_or(0);
        char::from_u32(code).map(|ch| ch.to_string()).unwrap_or_default()
    }).to_string();
    // &amp; 는 마지막(이중복원 방지)
    r.replace("&amp;", "&")
}

#[cfg(test)]
mod tests {
    use super::*;

    // 회귀 핵심 — CDATA 로 감싼 값이 통째로 사라지면 안 된다.
    // 태그 제거 정규식이 <![CDATA[..]]> 를 '태그 하나'로 삼켜, 엑셀 inlineStr
    // 문서 전체가 빈 텍스트가 되던 버그를 막는다.
    #[test]
    fn cdata_안의_글자를_살린다() {
        let mut out = String::new();
        collect_tag_text("<t><![CDATA[이름]]></t><t><![CDATA[회사]]></t>", "t", &mut out);
        assert_eq!(out, "이름\n회사\n");
    }

    // CDATA 와 일반 텍스트가 섞여도 둘 다 살아야 한다.
    #[test]
    fn cdata와_일반텍스트가_섞여도_된다() {
        assert_eq!(xml_inner_text("앞<![CDATA[가운데]]>뒤"), "앞가운데뒤");
        // 일반 구간의 태그는 지우고 엔티티는 복원한다.
        assert_eq!(xml_inner_text("<b>A&amp;B</b><![CDATA[C&amp;D]]>"), "A&BC&amp;D");
    }

    // CDATA 가 없으면 예전과 똑같이 동작해야 한다(기존 문서 회귀 방지).
    #[test]
    fn cdata가_없으면_종전과_같다() {
        let mut out = String::new();
        collect_tag_text("<t>보고서</t><t xml:space=\"preserve\"> 초안</t>", "t", &mut out);
        assert_eq!(out, "보고서\n 초안\n");
    }

    // 문단 단위 수집(docx)에서도 CDATA 가 살아야 한다.
    #[test]
    fn 문단수집도_cdata를_살린다() {
        let mut out = String::new();
        collect_paragraphs("<w:p><w:t><![CDATA[계약서]]></w:t><w:t>초안</w:t></w:p>",
                           "w:p", "w:t", &mut out);
        assert_eq!(out, "계약서초안\n");
    }

    // 닫히지 않은 CDATA 에 패닉하거나 무한루프에 빠지지 않는다.
    #[test]
    fn 깨진_cdata에도_안전하다() {
        let _ = xml_inner_text("<t><![CDATA[끝나지 않음");
        let _ = xml_inner_text("]]>혼자 있는 종료");
        let _ = xml_inner_text("");
    }
}

