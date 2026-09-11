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
    // 이번 문서의 '부분 추출' 표식 자리를 비우고 시작한다(G5 관측).
    crate::limits::notes_reset();
    extract_raw(path, fmt).map(|t| clean_text(&t))
}

// 페이지 마커 라인: 사이냅 기본 출력의 '..PAGE:12' 같은 줄.
static PAGE_MARKER: once_cell::sync::Lazy<regex::Regex> =
    once_cell::sync::Lazy::new(|| regex::Regex::new(r"(?m)^\s*\.\.PAGE:\d+\s*$").unwrap());
// 제어문자(탭/개행 제외): 임베딩에 무의미하고 토크나이저를 흔들 수 있어 제거.
static CTRL: once_cell::sync::Lazy<regex::Regex> =
    once_cell::sync::Lazy::new(|| regex::Regex::new(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]").unwrap());
// 줄 안의 연속 공백/탭 → 공백 1개.
static MULTI_SPACE: once_cell::sync::Lazy<regex::Regex> =
    once_cell::sync::Lazy::new(|| regex::Regex::new(r"[ \t]{2,}").unwrap());
// 3줄 이상 연속 빈 줄 → 2줄로 축약(문단 경계는 보존).
static MULTI_BLANK: once_cell::sync::Lazy<regex::Regex> =
    once_cell::sync::Lazy::new(|| regex::Regex::new(r"\n{3,}").unwrap());

/// 추출 원시 텍스트 정제 — 파이썬 `clean.clean_text` 와 **단계·차례가 같다**.
///
/// [왜 이 함수가 생겼나] 예전에는 여기에 개행 통일 한 가지만 있었다. 파이썬은
/// 여섯 단계를 거치는데 다섯 단계가 통째로 빠져 있었다. 그래서 같은 문서에서
/// 두 판이 **서로 다른 본문**을 보고 판정했다 — 실측 d:/sample 18건이 전부
/// 갈렸다(연속공백 11 · 빈줄 17 · 탭 4건).
///
/// 갈린 본문은 곧 갈린 판정이다. 표제부(앞 400자)가 가리키는 범위가 달라지고,
/// PII 정규식의 경계 검사 결과가 달라지고, 임베딩 벡터가 달라진다. 그런데
/// 임베더가 512토큰에서 자르는 탓에 **짧은 문서에서만** 벡터 차이가 드러나,
/// 18건 중 1건만 어긋난 것처럼 보였다. 오류도 경고도 안 나는 종류였다.
///
/// 개행 통일(3-2)만은 예전부터 있었다 — CR+LF 를 2글자로 세면 표제부 경계가
/// 밀리기 때문이다(설계서 14-3 교차 검증). 그 자리를 그대로 물려받았다.
///
/// -in: text = 추출기가 뽑은 원시 텍스트
///
/// -out: 정제된 텍스트(양끝 공백 제거). 입력이 비면 빈 문자열
/// -out: error = 없음(예외 없이 언제나 문자열)
fn clean_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    // (1) 자모가 분리된 한글 등을 완성형(NFC)으로 통일 → 토큰 일관성 확보.
    //     맥에서 만든 문서·일부 HWP 본문이 분해형(NFD)으로 들어온다. 그대로 두면
    //     같은 낱말이 두 판에서 다른 토큰이 되어 벡터가 갈린다.
    use unicode_normalization::UnicodeNormalization;
    let mut t: String = text.nfc().collect();

    // (2) 페이지 마커는 본문이 아니므로 라인 통째로 제거.
    t = PAGE_MARKER.replace_all(&t, "").into_owned();

    // (3-1) 눈에 안 보이는 제어문자 제거(탭/개행은 남겨 구조 보존).
    t = CTRL.replace_all(&t, "").into_owned();

    // (3-2) 윈도우 개행(CR+LF)·맥 개행(CR)을 LF 로 통일.
    if t.as_bytes().contains(&b'\r') {
        t = t.replace("\r\n", "\n").replace('\r', "\n");
    }

    // (3-3) 줄 안 연속 공백 축약. 탭도 함께 걸린다 — 표 셀이 빈 칸이면 탭이
    //       여러 개 이어지는데, 그것을 공백 하나로 줄이는 것이 파이썬 동작이다.
    t = MULTI_SPACE.replace_all(&t, " ").into_owned();

    // (3-4) 각 줄 끝의 잉여 공백 제거.
    let mut out = String::with_capacity(t.len());
    for (i, line) in t.split('\n').enumerate() {
        if i > 0 { out.push('\n'); }
        out.push_str(line.trim_end());
    }

    // (3-5) 빈 줄이 3줄 이상 이어지면 2줄로 축약.
    let out = MULTI_BLANK.replace_all(&out, "\n\n").into_owned();

    out.trim().to_string()
}

fn extract_raw(path: &Path, fmt: Fmt) -> Option<String> {
    match fmt {
        Fmt::Text => plaintext(path),
        Fmt::Html => html(path),
        // 문단 인식: 문단 내 런(run)을 이어붙이고 문단 사이만 개행. Python 과 동일하게
        // 하여 런 경계로 쪼개진 값(예: 절번호 "4.2.2.2.")이 갈라져 오탐되는 것을 막는다.
        Fmt::Docx => docx_text(path),
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
    // 페이지·시간 상한(G5) — 설계: plan/문서크기-상한-설계-20260904.html
    // 지금까지 이 루프에는 아무 제한이 없어, 수천 페이지 PDF 한 건이 배치 전체를
    // 무한정 붙잡을 수 있었다. 두 겹으로 막는다: ① 시간 예산 ② 페이지 수.
    // 넘겨도 실패로 처리하지 않고 '그때까지 읽은 부분'을 돌려준다 — 부분 텍스트라도
    // 등급 판정에는 대개 충분하고, 버리면 그 문서가 미분류로 쌓인다.
    let deadline = crate::limits::ParserDeadline::new();
    let max_pages = crate::limits::max_pdf_pages();
    let mut n_read = 0usize;
    let mut stopped: Option<String> = None;
    for page in doc.pages().iter() {
        if max_pages > 0 && n_read >= max_pages {
            stopped = Some(format!("페이지 상한({}p)", max_pages));
            break;
        }
        if deadline.expired() {
            stopped = Some("시간 상한".to_string());
            break;
        }
        if let Ok(t) = page.text() {
            out.push_str(&t.all());
            out.push('\n');
        }
        n_read += 1;
    }
    // 잘렸다면 반드시 남긴다 — 남기지 않으면 '왜 이 문서만 본문이 적지'를 나중에
    // 추적할 근거가 사라진다.
    if let Some(why) = stopped {
        crate::errlog::err(&format!("[PDF 부분추출] {} — {} 에서 중단(pages={})",
                                    path.display(), why, n_read));
        // 로그만으로는 결과를 보는 사람에게 안 닿는다 — 레코드까지 표식을 보낸다.
        crate::limits::notes_set_partial(why, "pages", n_read, doc.pages().len() as usize);
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

// 버릴 구역(script·style·head)은 태그마다 따로 지운다.
//
// [왜 하나로 묶으면 안 되나] 예전에는
//   `<(script|style|head)[^>]*>.*?</\s*(script|style|head)\s*>`
// 한 개로 처리했는데 결함이 둘 겹쳐 있었다.
//   ① `head` 가 `<header>` 에도 걸린다 — `head` 뒤에 경계가 없어 `[^>]*` 가 "er ..." 를 삼킨다.
//   ② 여는 태그와 닫는 태그가 서로 짝일 필요가 없다 — `<head…>` 로 열고 `</script>` 로 닫아도 성립한다.
// 그래서 본문이 `<header>` 로 시작하고 문서 끝에 `<script>` 가 있는 흔한 페이지에서,
// `<header>` 부터 마지막 `</script>` 까지가 통째로 지워졌다(실측: 301,654자 중 301,603자가
// 날아가 최종 텍스트 0자 — PII 2,707건이 든 보고서 하나가 통째로 미검출됐다).
//
// Rust regex 는 역참조(\1)가 없어 "여는 것과 같은 이름으로 닫기"를 한 정규식으로 못 쓴다.
// 그래서 태그별로 하나씩 두고 차례로 지운다. `\b` 로 `<header>`·`<heading>` 을 배제한다.
static RE_DROP_BLOCKS: Lazy<[Regex; 4]> = Lazy::new(|| [
    Regex::new(r"(?is)<script\b[^>]*>.*?</\s*script\s*>").unwrap(),
    Regex::new(r"(?is)<style\b[^>]*>.*?</\s*style\s*>").unwrap(),
    Regex::new(r"(?is)<head\b[^>]*>.*?</\s*head\s*>").unwrap(),
    // noscript 안은 스크립트가 꺼졌을 때만 보이는 대체 문구라 본문이 아니다
    // (파이썬 판 _DROP 과 같은 목록으로 맞춘다).
    Regex::new(r"(?is)<noscript\b[^>]*>.*?</\s*noscript\s*>").unwrap(),
]);
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

/// 경계에 개행을 넣을 블록 태그(파이썬 판 `html_text._BLOCK` 과 같은 목록).
///
/// [td·th 가 왜 들어 있나] 표를 `tr` 로만 끊으면 **같은 행의 셀 값이 구분자 없이
/// 이어붙는다**:
///
/// ```text
/// <td>900101-1234568</td><td>010-1234-5678</td>  ->  900101-1234568010-1234-5678
/// ```
///
/// 그러면 검출기의 경계 검사(앞뒤가 숫자면 거부)에 걸려 **두 값이 함께 사라진다**.
/// 파이썬 판은 2026-09-04 에 td·th 를 넣었는데(실측: 표가 빼곡한 문서에서 검출이
/// 37% 적었다) Rust 판이 그 수정을 못 받아 두 판의 PII 건수가 조용히 갈렸다
/// (2026-09-10 실측: ip_address 7↔5 · biz_reg_no 8↔6 · phone_mobile 7↔8).
/// 등급이 아직 안 갈렸을 뿐, 임계값 근처에서는 같은 문서가 판마다 다른 등급을
/// 받게 되는 자리였다.
///
/// [매번 컴파일하지 않는 이유] 예전에는 이 정규식을 html() 안에서 만들어 HTML
/// 파일 하나당 한 번씩 새로 컴파일했다. 목록은 고정이라 한 번만 만들면 된다.
static RE_BLOCK_TAG: Lazy<Regex> = Lazy::new(|| Regex::new(
    r"(?is)</?(p|div|br|tr|td|th|li|table|h[1-6]|ul|ol|section|article|header|footer|hr)[^>]*>"
).unwrap());

/// HTML → 텍스트(태그 제거 + 엔티티 복원).
fn html(path: &Path) -> Option<String> {
    let bytes = fs::read(path).ok()?;
    let raw = decode_bytes(&bytes);
    // script → style → head 순서로 하나씩 지운다(head 안의 style 은 어느 쪽이 먼저든 사라진다).
    let mut no_head = raw.clone();
    for re in RE_DROP_BLOCKS.iter() {
        no_head = re.replace_all(&no_head, " ").into_owned();
    }
    // 블록 태그를 개행으로 바꿔 낱말이 붙지 않게(목록은 RE_BLOCK_TAG 주석 참고).
    let spaced = RE_BLOCK_TAG.replace_all(&no_head, "\n");
    // 태그 제거·엔티티 복원은 CDATA 를 살리는 공통 헬퍼로 (xhtml 에도 CDATA 가 온다).
    let text = xml_inner_text(&spaced);
    Some(RE_WS.replace_all(&text, " ").to_string())
}

// 필드코드에서 링크 주소만 꺼내는 정규식. 따옴표가 없는 표기도 받는다.
static RE_FIELD_LINK: Lazy<Regex> = Lazy::new(|| Regex::new(
    r#"(?i)HYPERLINK\s+(?:"([^"]*)"|(\S+))"#
).unwrap());

//------------------------------------------------------------------
// 필드코드(w:instrText)에서 '링크 주소'만 골라 담는다
//=> 워드의 필드코드에는 두 종류가 섞여 있다. 하나는 사람이 넣은 **내용**
//   (HYPERLINK 의 주소 — http://… 나 mailto:someone@example.com), 다른 하나는
//   **서식 부스러기**(PAGEREF·SEQ·TOC·STYLEREF 같은 쪽번호/목차 장치)다.
//
//   [왜 통째로 안 넣나] 실측(docx 56개)에서 필드코드 조각 809개를 종류별로 세어 보니
//     PAGEREF 584 · SEQ 61 · TOC 17 · STYLEREF 12 … 그리고 HYPERLINK 는 9개뿐이었다.
//   통째로 넣으면 800줄 남짓한 부스러기를 본문에 섞어 놓고 링크 9개를 얻는 셈이다.
//   게다가 `PAGEREF _Toc123456789 \h` 같은 조각은 **긴 숫자열**이라, 자릿수가 맞으면
//   사업자등록번호(10자리) 같은 검출기에 헛걸릴 여지까지 생긴다. 이득보다 위험이 크다.
//
//   [한 문단 안에서 이어 붙이는 이유] 워드는 필드코드를 아무 자리에서나 여러
//   조각으로 쪼개 저장한다("HYPERLINK \"http://exa" + "mple.com\""). 조각을 따로
//   보면 주소가 잘려 못 찾으므로, 한 문단(w:p) 안의 조각을 먼저 이어 붙인 뒤 찾는다.
//
// -in: xml = 문서 XML(본문·머리말·꼬리말 어느 것이든)
// -in: out = 찾은 주소를 한 줄씩 덧붙일 곳
//
// -out: 없음 (out 에 덧붙인다)
// -out: error = 예외 없음
//------------------------------------------------------------------
fn collect_field_links(xml: &str, out: &mut String) {
    let p_re = tag_re("w:p");
    let instr_re = tag_re("w:instrText");
    for p in p_re.captures_iter(xml) {
        // 한 문단 안의 조각을 먼저 이어 붙인다(위 헤더의 '쪼개 저장' 참고).
        let mut joined = String::new();
        for m in instr_re.captures_iter(&p[1]) {
            joined.push_str(&xml_inner_text(&m[1]));
        }
        if joined.is_empty() { continue; }
        for c in RE_FIELD_LINK.captures_iter(&joined) {
            let target = c.get(1).or_else(|| c.get(2)).map(|m| m.as_str().trim()).unwrap_or("");
            if !target.is_empty() {
                out.push_str(target);
                out.push('\n');
            }
        }
    }
}

//------------------------------------------------------------------
// docx → 텍스트 (본문 + 머리말·꼬리말)
//=> 예전에는 word/document.xml 만 읽었다. 그런데 회사 주소·대표전화 같은 값은
//   본문이 아니라 **머리말·꼬리말**에 박혀 있는 일이 흔하다(실측: docx 56개 중
//   30개가 머리말·꼬리말을 갖고 있었고, 거기서 ADDRESS 1건·PHONE 2건이 더 나왔다).
//   그 자리를 안 읽으면 문서에 분명히 적힌 개인정보를 놓친다.
//
//   [순서가 중요하다] 머리말을 **뒤에** 붙인다. 앞에 붙이면 '앞 400자(표제부)' 를
//   보는 신호들(제목·업무분류)이 가리키는 범위가 통째로 달라져, 이번 변경과
//   상관없는 문서까지 분류가 흔들린다. 본문을 그대로 두고 뒤에 잇는 쪽이 안전하다.
//
//   [각주·미주는 왜 안 넣나] 45개 파일에 있었지만 추가로 잡히는 PII 가 0건이었다.
//   읽는 만큼 오탐 여지만 늘어 일부러 뺐다. 필요해지면 아래 목록에 이름만 더하면 된다.
//
// -in: path = docx 경로
//
// -out: 본문 다음에 머리말·꼬리말을 이어 붙인 텍스트
// -out: error = zip 이 아니거나 못 열면 None
//------------------------------------------------------------------
fn docx_text(path: &Path) -> Option<String> {
    let file = fs::File::open(path).ok()?;
    let mut zip = zip::ZipArchive::new(file).ok()?;

    // 머리말·꼬리말은 header1.xml, footer2.xml 처럼 번호가 붙어 이름이 고정이 아니다.
    // 그래서 목록을 먼저 훑어 모은다(zip 을 빌린 채로는 by_name 을 못 쓴다).
    let mut extras: Vec<String> = Vec::new();
    for i in 0..zip.len() {
        if let Ok(zf) = zip.by_index(i) {
            let n = zf.name().to_string();
            if n.ends_with(".xml")
                && (n.starts_with("word/header") || n.starts_with("word/footer")) {
                extras.push(n);
            }
        }
    }
    extras.sort();      // header1 → header2 → footer1 … 실행마다 같은 차례가 되게

    let mut out = String::new();
    let mut links = String::new();      // 필드코드의 링크는 맨 뒤에 모아 붙인다
    // 본문이 먼저 — 앞 400자(표제부)를 예전과 똑같이 유지한다.
    if let Ok(mut f) = zip.by_name("word/document.xml") {
        let mut data = String::new();
        if f.read_to_string(&mut data).is_ok() {
            collect_docx_paragraphs(&data, &mut out);
            collect_field_links(&data, &mut links);
        }
    }
    for name in &extras {
        if let Ok(mut f) = zip.by_name(name) {
            let mut data = String::new();
            if f.read_to_string(&mut data).is_ok() {
                collect_docx_paragraphs(&data, &mut out);
                collect_field_links(&data, &mut links);
            }
        }
    }
    // 링크는 본문 흐름에 끼워 넣지 않고 뒤에 붙인다 — 본문 낱말 사이에 주소가
    // 끼어들면 '앞뒤가 붙어 있어야 성립하는' 문맥 규칙이 엉뚱하게 성립/실패한다.
    out.push_str(&links);
    Some(out)
}

// (ooxml_para 제거) — '정해진 이름의 항목들에서 문단을 뽑는' 도우미였다.
// 유일한 사용처가 docx 였는데, 머리말·꼬리말(header1.xml 처럼 번호가 붙어 이름이
// 고정이 아니다)까지 읽게 되면서 docx_text 가 그 일을 직접 하게 됐다.

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
    // 시간 상한(G5) — 슬라이드·섹션이 수천 개인 문서에서 이 루프가 무한정 늘어난다.
    // 넘으면 그때까지 읽은 항목까지만 쓴다(실패 아님).
    let deadline = crate::limits::ParserDeadline::new();
    let n_total = names.len();
    for (n_done, name) in names.into_iter().enumerate() {
        if deadline.expired() {
            crate::errlog::err(&format!("[OOXML 부분추출] {} — 시간 상한에서 중단(prefix={})",
                                        path.display(), prefix));
            crate::limits::notes_set_partial("시간 상한".to_string(), "parts", n_done, n_total);
            break;
        }
        if let Ok(mut zf) = zip.by_name(&name) {
            let mut data = String::new();
            if zf.read_to_string(&mut data).is_ok() {
                f(&data, &mut out);
            }
        }
    }
    Some(out)
}

/// docx 전용 문단 수집 — 파이썬 `DocxExtractor._collect_paragraphs` 와 짝이다.
///
/// 공용 `collect_paragraphs` 와 두 가지가 다르고, 그 둘이 실측에서 실제로 갈렸다:
///
///   · **빈 문단을 버리지 않는다.** 공용판은 `if !line.is_empty()` 로 건너뛰는데,
///     파이썬 docx 는 빈 문단도 한 줄로 남긴다. 그래서 문단 사이 빈 줄이 Rust 에서만
///     사라졌다(실측 d:/sample 에서 10건). 빈 줄은 장식이 아니다 — 표제부(앞 400자)가
///     닿는 범위와 임베딩 토큰 배열이 함께 밀린다.
///     pptx 는 반대다. 파이썬 `PptxExtractor._read_slide` 가 "빈 문단은 건너뜀"이라
///     공용판이 맞다 — 그래서 이 함수를 docx 전용으로 따로 둔다.
///
///   · **`<w:tab>` 을 탭 문자로 살린다.** 공용판은 `<w:t>` 만 모아 들여쓰기와 표
///     열 구분이 통째로 사라졌다. 파이썬은 `p.iter()` 로 `<w:t>`·`<w:tab>` 을
///     **문서 차례 그대로** 훑으므로, 여기서도 두 정규식의 매치를 위치순으로 섞는다.
///
/// -in: xml = word/document.xml · header*.xml · footer*.xml 본문
/// -in: out = 결과를 덧붙일 문자열(제자리에서 늘어난다)
///
/// -out: 없음(out 에 문단마다 한 줄씩 덧붙인다)
/// -out: error = 없음
fn collect_docx_paragraphs(xml: &str, out: &mut String) {
    // 탭 문자는 자기닫힘 `<w:tab/>` 로 온다. tag_re 는 `<태그>…</태그>` 짝만 받으므로
    // 여기에 쓸 수 없다 — 그것을 몰라 예전 판은 탭을 통째로 놓쳤다.
    static TAB: Lazy<Regex> = Lazy::new(|| Regex::new(r"<w:tab\b[^>]*>").unwrap());
    // 빈 문단은 `<w:p/>` 로 온다. tag_re 는 짝이 있는 태그만 받아 이것을 통째로
    // 놓쳤고, 그만큼 빈 줄이 사라졌다(실측 '15.의료비 지원 규정' 에서 8개).
    // 자기닫힘 갈래를 **앞에** 둔다 — 뒤에 두면 `<w:p w:rsidR="…"/>` 의 `/` 를
    // 여는 태그의 속성으로 삼켜, 다음 `</w:p>` 까지의 본문을 통째로 먹는다.
    static PARA: Lazy<Regex> = Lazy::new(|| Regex::new(
        r"(?s)<w:p(?:\s[^>]*?)?/>|<w:p(?:\s[^>]*)?>(.*?)</w:p>").unwrap());
    let run_re = tag_re("w:r");
    let text_re = tag_re("w:t");
    for pcap in PARA.captures_iter(xml) {
        // 자기닫힘 `<w:p/>` 는 안쪽이 없다 — 빈 문단으로 보고 빈 줄을 남긴다.
        let body = pcap.get(1).map_or("", |m| m.as_str());
        // (문단 안 위치, 조각) 을 모아 위치순으로 이어야 파이썬의 훑는 차례와 같다.
        let mut parts: Vec<(usize, String)> = Vec::new();
        // 런(<w:r>) 안만 본다. <w:pPr><w:tabs> 안의 <w:tab> 은 탭 정지 위치 '정의'
        // 이지 본문의 탭 문자가 아니다 — 실측에서 한 문단이 그 정의를 32개 갖고
        // 있었고, 문단 전체를 훑으면 그것이 탭 32개로 새어 들어가 없던 들여쓰기를
        // 만든다(파이썬 판이 실제로 그랬다). 진짜 탭은 언제나 런 안에 있다.
        for rcap in run_re.captures_iter(body) {
            let (rstart, rbody) = match rcap.get(1) {
                Some(m) => (m.start(), m.as_str()),
                None => continue,          // 자기닫힘 `<w:r/>` — 내용이 없다
            };
            for tcap in text_re.captures_iter(rbody) {
                let at = rstart + tcap.get(0).map_or(0, |m| m.start());
                parts.push((at, xml_inner_text(tcap.get(1).map_or("", |m| m.as_str()))));
            }
            for m in TAB.find_iter(rbody) {
                parts.push((rstart + m.start(), "\t".to_string()));
            }
        }
        parts.sort_by_key(|(at, _)| *at);
        for (_, s) in parts {
            out.push_str(&s);
        }
        out.push('\n');
    }
}

/// 문단 인식: 각 <para>...</para> 안의 <text> 런을 '이어붙여' 한 줄, 문단 사이 개행.
fn collect_paragraphs(xml: &str, para_tag: &str, text_tag: &str, out: &mut String) {
    let para_re = tag_re(para_tag);
    let text_re = tag_re(text_tag);
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
// 셀·행·공유문자열 항목을 찾는 정규식(네임스페이스 접두 허용, 자기닫힘 <c/> 도 받음).
//=> `<c r="B2"/>` 처럼 빈 셀은 자기닫힘으로 온다. 이걸 못 받으면 그 자리에서
//   매치가 어긋나 한 행을 통째로 놓친다.
static RE_XL_ROW: Lazy<Regex> = Lazy::new(|| Regex::new(
    r"(?s)<(?:[A-Za-z_][A-Za-z0-9_.\-]*:)?row\b[^>]*?(?:/>|>(.*?)</(?:[A-Za-z_][A-Za-z0-9_.\-]*:)?row>)"
).unwrap());
static RE_XL_CELL: Lazy<Regex> = Lazy::new(|| Regex::new(
    r"(?s)<(?:[A-Za-z_][A-Za-z0-9_.\-]*:)?c\b([^>]*?)(?:/>|>(.*?)</(?:[A-Za-z_][A-Za-z0-9_.\-]*:)?c>)"
).unwrap());
static RE_XL_SI: Lazy<Regex> = Lazy::new(|| Regex::new(
    r"(?s)<(?:[A-Za-z_][A-Za-z0-9_.\-]*:)?si\b[^>]*?(?:/>|>(.*?)</(?:[A-Za-z_][A-Za-z0-9_.\-]*:)?si>)"
).unwrap());
static RE_XL_TATTR: Lazy<Regex> = Lazy::new(|| Regex::new(r#"\bt="([^"]*)""#).unwrap());

//------------------------------------------------------------------
// 공유문자열표를 순서대로 읽는다
//=> 워크시트의 문자열 셀은 글자를 직접 담지 않고 `<v>3</v>` 처럼 **번호**만 적는다.
//   그 번호가 가리키는 곳이 sharedStrings 다. 번호를 글자로 바꾸려면 순서가
//   그대로 유지된 목록이 필요하다.
//    1) `<si>` 를 순서대로 훑는다(빈 항목 `<si/>` 도 자리를 차지하므로 같이 센다)
//    2) 한 `<si>` 안의 `<t>` 를 모두 이어 붙인다(서식이 섞이면 `<r><t>` 로 쪼개져 온다)
//
// -in: xml = sharedStrings.xml 본문
//
// -out: 번호 순서대로 담긴 문자열 목록
// -out: error = 없음(모양이 달라도 빈 목록)
//------------------------------------------------------------------
fn xlsx_shared_strings(xml: &str) -> Vec<String> {
    let t_re = tag_re("t");
    let mut out = Vec::new();
    for m in RE_XL_SI.captures_iter(xml) {
        let inner = m.get(1).map(|g| g.as_str()).unwrap_or("");
        let mut s = String::new();
        for t in t_re.captures_iter(inner) {
            s.push_str(&xml_inner_text(&t[1]));
        }
        out.push(s);
    }
    out
}

//------------------------------------------------------------------
// xlsx → 텍스트 (셀 값을 행 단위로 이어 붙인다)
//=> 예전에는 `<t>`(문자열 셀)만 모아 개행으로 이었다. 그래서 두 가지를 잃었다.
//    ① **숫자 셀을 통째로 못 읽었다.** 엑셀은 숫자·날짜·수식 결과를 `<t>` 가 아니라
//       `<v>` 에 넣는다. 하이픈 없이 적은 전화번호·계좌번호·사업자번호가 바로 그
//       숫자 셀이라, 검출기 눈에 아예 안 보였다(실측: 한 파일에서 18,531자 중
//       5,445자만 읽어 29%).
//    ② **한 행의 칸들이 개행으로 갈라졌다.** 엑셀에서 옆으로 나란한 칸은 사람 눈에
//       한 줄이다. 개행으로 끊으면 "앵커 낱말 + 숫자" 처럼 **붙어 있어야 성립하는
//       모양**이 만들어지지 않아, 주소·계좌 같은 문맥 규칙이 통째로 놓친다.
//   그래서 행(`<row>`)마다 칸(`<c>`)을 공백으로 이어 한 줄로 만든다.
//
//   [셀 종류별 처리] `t` 속성이 값의 종류를 말해 준다.
//     · t="s"        → `<v>` 는 **번호**다. 공유문자열표에서 글자를 꺼내 쓴다.
//                       (번호를 그대로 쓰면 뜻 없는 숫자가 본문에 섞인다)
//     · t="inlineStr"→ 셀 안에 `<is><t>` 로 글자가 직접 들어 있다.
//     · t="e"        → 수식 오류(#N/A 등). 본문이 아니라 버린다.
//     · 그 밖(t 없음·"n"·"str"·"b") → `<v>` 를 글자 그대로 쓴다. 숫자와 수식 결과
//                       문자열이 여기 해당한다.
//
// -in: path = xlsx 경로
//
// -out: 행 단위로 이어 붙인 텍스트
// -out: error = zip 이 아니거나 못 열면 None
//------------------------------------------------------------------
fn xlsx(path: &Path) -> Option<String> {
    let file = fs::File::open(path).ok()?;
    let mut zip = zip::ZipArchive::new(file).ok()?;

    // 워크시트 이름을 모은다(번호순으로 읽어야 sheet2 가 sheet10 보다 앞선다).
    let mut sheets: Vec<String> = Vec::new();
    let mut has_shared = false;
    for i in 0..zip.len() {
        if let Ok(zf) = zip.by_index(i) {
            let n = zf.name().to_string();
            if n == "xl/sharedStrings.xml" {
                has_shared = true;
            } else if n.starts_with("xl/worksheets/") && n.ends_with(".xml") {
                sheets.push(n);
            }
        }
    }
    sheets.sort_by_key(|n| num_in_name(n));

    // 공유문자열표 먼저(워크시트가 번호로 이걸 가리킨다).
    let mut shared: Vec<String> = Vec::new();
    if has_shared {
        if let Ok(mut f) = zip.by_name("xl/sharedStrings.xml") {
            let mut data = String::new();
            if f.read_to_string(&mut data).is_ok() {
                shared = xlsx_shared_strings(&data);
            }
        }
    }

    let t_re = tag_re("t");
    let v_re = tag_re("v");
    let mut out = String::new();
    // 시간 상한(G5) — 시트가 많거나 한 장이 거대한 통합문서에서 이 루프가 무한정
    // 늘어난다. 넘으면 그때까지 읽은 시트까지만 쓴다(실패 아님).
    let deadline = crate::limits::ParserDeadline::new();
    let n_sheets = sheets.len();
    for (n_sheets_done, name) in sheets.iter().enumerate() {
        if deadline.expired() {
            crate::errlog::err(&format!("[xlsx 부분추출] {} — 시간 상한에서 중단",
                                        path.display()));
            crate::limits::notes_set_partial("시간 상한".to_string(), "sheets",
                                             n_sheets_done, n_sheets);
            break;
        }
        let mut data = String::new();
        match zip.by_name(name) {
            Ok(mut f) => { if f.read_to_string(&mut data).is_err() { continue; } }
            Err(_) => continue,
        }
        for row in RE_XL_ROW.captures_iter(&data) {
            let inner = match row.get(1) { Some(g) => g.as_str(), None => continue };
            let mut cells: Vec<String> = Vec::new();
            for c in RE_XL_CELL.captures_iter(inner) {
                let attrs = c.get(1).map(|g| g.as_str()).unwrap_or("");
                let body = c.get(2).map(|g| g.as_str()).unwrap_or("");
                if body.is_empty() { continue; }        // 빈 셀
                let kind = RE_XL_TATTR.captures(attrs)
                    .map(|m| m[1].to_string()).unwrap_or_default();
                let val = match kind.as_str() {
                    "e" => String::new(),               // 수식 오류는 버린다
                    "s" => v_re.captures(body)
                        .and_then(|m| xml_inner_text(&m[1]).trim().parse::<usize>().ok())
                        .and_then(|i| shared.get(i).cloned())
                        .unwrap_or_default(),
                    _ => {
                        // inlineStr 은 <is><t>, 그 밖은 <v>. 둘 다 있으면 글자 쪽을 쓴다.
                        let mut s = String::new();
                        for t in t_re.captures_iter(body) {
                            s.push_str(&xml_inner_text(&t[1]));
                        }
                        if s.is_empty() {
                            if let Some(m) = v_re.captures(body) {
                                s = xml_inner_text(&m[1]);
                            }
                        }
                        s
                    }
                };
                let val = val.trim();
                if !val.is_empty() { cells.push(val.to_string()); }
            }
            if !cells.is_empty() {
                out.push_str(&cells.join(" "));
                out.push('\n');
            }
        }
    }

    // 워크시트에서 한 줄도 못 얻었으면(모양이 낯선 파일) 공유문자열표라도 내놓는다.
    // 예전 동작의 안전망 — 아무것도 안 주는 것보다는 낫다.
    if out.is_empty() && !shared.is_empty() {
        out = shared.join("\n");
    }
    Some(out)
}

//------------------------------------------------------------------
// 태그 하나를 찾는 정규식을 만든다 (네임스페이스 접두 허용)
//=> `<t>` 를 찾으랬는데 파일이 `<x:t>` 로 적어 두면 한 글자도 못 뽑는다. 엑셀이
//   아닌 도구(ClosedXML·EPPlus 등)로 저장한 xlsx 가 실제로 그렇게 쓴다
//   (실측: `<x:sst><x:si><x:t>…` 인 파일에서 추출 0자 → 문서 통째로 미분류).
//   그래서 접두 없는 이름으로 부르면 접두가 붙은 것도 함께 받게 한다.
//    1) 부른 이름에 ':' 가 있으면(예: "w:t") 예전 그대로 — 그 이름만 찾는다
//    2) 없으면(예: "t") 앞에 "아무개:" 가 붙은 것도 받는다
//
// -in: tag = 찾을 태그 이름("t", "w:t" 처럼)
//
// -out: 여는 태그~닫는 태그 사이를 1번 그룹으로 잡는 정규식
// -out: error = 정규식이 안 만들어지면 panic(태그 이름은 코드 상수라 실제로 안 난다)
//------------------------------------------------------------------
fn tag_re(tag: &str) -> Regex {
    let t = regex::escape(tag);
    // 접두를 이미 지정해 부른 곳(docx w:t · pptx a:t · hwpx hp:t)의 동작은 건드리지 않는다.
    // 그쪽까지 접두를 풀면 문서 안 다른 이름공간의 같은 이름(예: 그림 속 a:t)까지
    // 딸려 들어와 기존 결과가 바뀐다.
    let px = if tag.contains(':') { "" } else { r"(?:[A-Za-z_][A-Za-z0-9_.\-]*:)?" };
    let pat = format!(r"(?s)<{px}{t}(?:\s[^>]*)?>(.*?)</{px}{t}>", px = px, t = t);
    Regex::new(&pat).unwrap()
}

/// 문서 XML 에서 <TAG ...>...</TAG> 안의 텍스트를 뽑아(내부태그 제거·엔티티 복원) out 에 개행으로 추가.
fn collect_tag_text(xml: &str, tag: &str, out: &mut String) {
    let re = tag_re(tag);
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

    // 탭 정지 '정의'(<w:pPr><w:tabs>)를 탭 문자로 세면 안 된다.
    //=> 실측에서 한 문단이 그 정의를 32개 갖고 있었다. 문단 전체를 훑으면
    //   탭 32개가 새어 들어가고, 정제 단계가 그것을 공백 하나로 뭉쳐 본문에
    //   없던 들여쓰기를 만든다. 파이썬 판이 실제로 그렇게 갈려 있었다.
    #[test]
    fn 탭정지_정의는_탭문자가_아니다() {
        let mut out = String::new();
        collect_docx_paragraphs(
            "<w:p><w:pPr><w:tabs><w:tab w:val=\"left\" w:pos=\"806\"/>\
             <w:tab w:val=\"left\" w:pos=\"1612\"/></w:tabs></w:pPr>\
             <w:r><w:t>나.</w:t></w:r><w:r><w:t xml:space=\"preserve\"> </w:t></w:r>\
             <w:r><w:t>개정 시행일</w:t></w:r></w:p>", &mut out);
        assert_eq!(out, "나. 개정 시행일\n");
    }

    // 런 안의 진짜 탭(<w:tab/>)은 문서 차례 그대로 살아야 한다.
    //=> tag_re 는 짝이 있는 태그만 받아 자기닫힘 <w:tab/> 을 못 잡았고,
    //   그래서 표 열 구분과 들여쓰기가 통째로 사라졌다.
    #[test]
    fn 런_안의_탭은_살린다() {
        let mut out = String::new();
        collect_docx_paragraphs(
            "<w:p><w:r><w:t>가</w:t><w:tab/><w:t>나</w:t></w:r></w:p>", &mut out);
        assert_eq!(out, "가\t나\n");
    }

    // 빈 문단은 빈 줄로 남는다 — 파이썬 docx 와 같다(pptx 는 반대다).
    //=> 문단 사이 빈 줄이 사라지면 표제부(앞 400자)가 닿는 범위가 밀린다.
    #[test]
    fn 빈_문단은_빈_줄로_남는다() {
        let mut out = String::new();
        collect_docx_paragraphs(
            "<w:p><w:r><w:t>앞</w:t></w:r></w:p><w:p></w:p>\
             <w:p><w:r><w:t>뒤</w:t></w:r></w:p>", &mut out);
        assert_eq!(out, "앞\n\n뒤\n");
    }

    // 빈 문단은 자기닫힘 `<w:p/>` 로도 온다 — 이쪽이 실제 문서에서 더 흔하다.
    //=> 짝 있는 태그만 받는 정규식이 이것을 통째로 놓쳐, 실측 '15.의료비 지원
    //   규정'에서 빈 줄 8개가 사라졌다.
    #[test]
    fn 자기닫힘_빈_문단도_빈_줄로_남는다() {
        let mut out = String::new();
        collect_docx_paragraphs(
            "<w:p><w:r><w:t>앞</w:t></w:r></w:p><w:p/>\
             <w:p w:rsidR=\"00A1\"/><w:p><w:r><w:t>뒤</w:t></w:r></w:p>", &mut out);
        assert_eq!(out, "앞\n\n\n뒤\n");
    }

    // 정제는 파이썬 clean_text 와 단계·차례가 같아야 한다.
    #[test]
    fn 정제가_파이썬과_같은_단계를_거친다() {
        // 연속 공백/탭 → 공백 1개, 줄끝 공백 제거, 빈 줄 3+ → 2, 양끝 trim.
        assert_eq!(clean_text("  가   나  \n\n\n\n다  \n"), "가 나\n\n다");
        // 페이지 마커 줄과 제어문자는 사라진다.
        assert_eq!(clean_text("앞\n..PAGE:12\n\u{0007}뒤"), "앞\n\n뒤");
        // CR+LF 는 LF 하나로.
        assert_eq!(clean_text("가\r\n나"), "가\n나");
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

