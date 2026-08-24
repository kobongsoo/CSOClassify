//! 포맷별 텍스트 추출 (Python extract/* 포팅)
//! PoC 지원: text · html · docx · xlsx · pptx · hwpx.
//! 미지원(별도 과제): 구형 doc/xls/ppt(OLE) · hwp5(OLE) · pdf(후속) · 이미지.

use std::fs;
use std::io::Read;
use std::path::Path;

use once_cell::sync::Lazy;
use regex::Regex;

use crate::detect::Fmt;

/// 포맷에 맞는 추출기를 호출. 미지원/실패면 None(→ 상위에서 추출실패 처리).
pub fn extract_text(path: &Path, fmt: Fmt) -> Option<String> {
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

/// HTML → 텍스트(태그 제거 + 엔티티 복원).
fn html(path: &Path) -> Option<String> {
    let bytes = fs::read(path).ok()?;
    let raw = decode_bytes(&bytes);
    let no_head = RE_SCRIPT.replace_all(&raw, " ");
    // 블록 태그를 개행으로 바꿔 낱말이 붙지 않게.
    let block = Regex::new(r"(?is)</?(p|div|br|tr|li|h[1-6]|table|ul|ol|section|article|header|footer)[^>]*>").unwrap();
    let spaced = block.replace_all(&no_head, "\n");
    let stripped = RE_TAG.replace_all(&spaced, "");
    let unescaped = unescape_entities(&stripped);
    Some(RE_WS.replace_all(&unescaped, " ").to_string())
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
            let inner = RE_TAG.replace_all(&tcap[1], "");
            line.push_str(&unescape_entities(&inner));
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
        let inner = &cap[1];
        let no_tag = RE_TAG.replace_all(inner, "");
        let text = unescape_entities(&no_tag);
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
