//! PII 검출기 (ko-pii 전면 포팅 — 체크섬·문맥앵커·행정구역 사전·겹침해소 포함).
//! 구현(20라벨): RRN·FRN·CARD·BUSINESS_REG·CORP_REG·PHONE·EMAIL·ACCOUNT·PASSPORT·IP·URL·
//!   VEHICLE·NATIONALITY·ADDRESS·DRIVER_LICENSE·MEDICAL_INSURANCE·PRESCRIPTION_ID·EDI_DRUG·
//!   COURT_CASE·PNU. (CSOClassify 가 cso_rules.yaml 에서 쓰는 라벨 전량.)
//!
//! 동작 순서(ko-pii detect_all 동일):
//!   1) 요청 라벨의 검출기만 실행 → 각자 (span, risk, confidence) 방출
//!   2) core.overlap.resolve_overlaps 로 라벨 간 겹침 해소(위험도→확신도→길이→시작 우선)
//!   3) 살아남은 span 을 라벨별로 집계
//! ※ Rust regex 는 look-around 미지원 → 앞뒤 경계·문맥은 코드로 검사한다.

use std::collections::{HashMap, HashSet};
use std::time::{SystemTime, UNIX_EPOCH};

use once_cell::sync::Lazy;
use regex::{Match, Regex};

use crate::districts;

/// 검출 1건 — 라벨 간 겹침 해소에 필요한 최소 정보(ko-pii DetectionResult 축약).
#[derive(Clone)]
struct Det {
    start: usize,
    end: usize,
    risk: i32,   // RiskLevel: INFO=1 LOW=2 MEDIUM=3 HIGH=4 CRITICAL=5
    conf: f64,
    label: &'static str,
}

// RiskLevel 상수(ko-pii core/types.RiskLevel).
const R_INFO: i32 = 1;
const R_LOW: i32 = 2;
const R_MED: i32 = 3;
const R_HIGH: i32 = 4;
const R_CRIT: i32 = 5;

/// 유니코드 정규화(경량): 전각 숫자/영문 → ASCII, 제로폭 문자 제거.
pub fn normalize(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        let c = ch as u32;
        if c == 0x200B || c == 0x200C || c == 0x200D || c == 0xFEFF {
            continue;
        }
        if (0xFF10..=0xFF19).contains(&c) {
            out.push((b'0' + (c - 0xFF10) as u8) as char);
        } else if (0xFF21..=0xFF3A).contains(&c) {
            out.push((b'A' + (c - 0xFF21) as u8) as char);
        } else if (0xFF41..=0xFF5A).contains(&c) {
            out.push((b'a' + (c - 0xFF41) as u8) as char);
        } else {
            out.push(ch);
        }
    }
    out
}

/// 원하는 라벨들에 대해 텍스트에서 건수를 센다(겹침해소 후 집계).
pub fn pii_counts(text: &str, wanted: &HashSet<String>) -> HashMap<String, u32> {
    let mut counts: HashMap<String, u32> = HashMap::new();
    if text.is_empty() || wanted.is_empty() {
        return counts;
    }
    let norm = normalize(text);
    let kept = kept_dets(&norm, wanted);
    // 라벨별 집계. 처방전은 검출라벨이 PRESCRIPTION_ID 이나 yaml 별칭도 함께 채운다.
    for d in kept {
        if wanted.contains(d.label) {
            *counts.entry(d.label.to_string()).or_insert(0) += 1;
        }
        if d.label == "PRESCRIPTION_ID" && wanted.contains("PRESCRIPTION") {
            *counts.entry("PRESCRIPTION".to_string()).or_insert(0) += 1;
        }
    }
    counts
}

/// [프라이버시 예외] 검출된 PII '원문 값'을 반환(--with-pii 전용). ko-pii collect_pii 대응.
/// 반환: (label, value, start, end). value/offset 은 정규화 텍스트 기준.
pub fn pii_records(text: &str, wanted: &HashSet<String>) -> Vec<(&'static str, String, usize, usize)> {
    if text.is_empty() || wanted.is_empty() { return Vec::new(); }
    let norm = normalize(text);
    kept_dets(&norm, wanted)
        .into_iter()
        .filter(|d| wanted.contains(d.label))
        .map(|d| (d.label, norm[d.start..d.end].to_string(), d.start, d.end))
        .collect()
}

/// 요청 라벨의 검출기만 실행 → 겹침해소된 Det 목록(집계·원문수집 공통).
fn kept_dets(norm: &str, wanted: &HashSet<String>) -> Vec<Det> {
    let has = |l: &str| wanted.contains(l);

    // 1) 요청 라벨의 검출기만 실행 → span 수집.
    let mut dets: Vec<Det> = Vec::new();
    if has("RRN") { dets.extend(det_rrn(&norm)); }
    if has("FRN") { dets.extend(det_frn(&norm)); }
    if has("CARD") { dets.extend(det_card(&norm)); }
    if has("BUSINESS_REG") { dets.extend(det_brn(&norm)); }
    if has("CORP_REG") { dets.extend(det_corp(&norm)); }
    if has("PHONE") { dets.extend(det_phone(&norm)); }
    if has("EMAIL") { dets.extend(det_simple(&norm, &RE_EMAIL, "EMAIL", R_MED, 1.0)); }
    if has("ACCOUNT") { dets.extend(det_account(&norm)); }
    if has("PASSPORT") { dets.extend(det_passport(&norm)); }
    if has("IP") { dets.extend(det_ipv4(&norm)); }
    if has("URL") { dets.extend(det_simple(&norm, &RE_URL, "URL", R_INFO, 0.9)); }
    if has("VEHICLE") { dets.extend(det_vehicle(&norm)); }
    if has("NATIONALITY") { dets.extend(det_nationality(&norm)); }
    if has("ADDRESS") { dets.extend(det_address(&norm)); }
    if has("DRIVER_LICENSE") { dets.extend(det_driver_license(&norm)); }
    if has("MEDICAL_INSURANCE") { dets.extend(det_medical_insurance(&norm)); }
    if has("PRESCRIPTION_ID") || has("PRESCRIPTION") { dets.extend(det_prescription(&norm)); }
    if has("EDI_DRUG") { dets.extend(det_edi_drug(&norm)); }
    if has("COURT_CASE") { dets.extend(det_court_case(&norm)); }
    if has("PNU") { dets.extend(det_pnu(&norm)); }

    // 2) 겹침 해소(ko-pii core.overlap.resolve_overlaps).
    resolve_overlaps(dets)
}

/// 겹침 해소: 위험도↑ → 확신도↑ → 길이↑ → 시작↑ 순으로 채택, 채택된 span 과 겹치면 드롭.
/// (ko-pii core/overlap.py resolve_overlaps 동일 정책.)
fn resolve_overlaps(mut items: Vec<Det>) -> Vec<Det> {
    items.sort_by(|a, b| {
        b.risk.cmp(&a.risk)
            .then(b.conf.partial_cmp(&a.conf).unwrap_or(std::cmp::Ordering::Equal))
            .then((b.end - b.start).cmp(&(a.end - a.start)))
            .then(a.start.cmp(&b.start))
    });
    let mut accepted: Vec<Det> = Vec::new();
    for d in items {
        if accepted.iter().any(|a| d.start < a.end && a.start < d.end) {
            continue;
        }
        accepted.push(d);
    }
    accepted
}

// ---- 정규식 (look-around 없음) ----
// RRN/FRN 구분자: 하이픈/점/공백(래핑) — ko-pii 와 동일 규칙.
static RE_RRN: Lazy<Regex> = Lazy::new(|| Regex::new(r"([0-9]{6})(?:\s?[-./]\s?|[-./\s]{0,2})([0-9]{7})").unwrap());
static RE_RRN_PREFIXED: Lazy<Regex> = Lazy::new(|| Regex::new(r"[0-9]([0-9]{6})(?:\s?[-./]\s?|[-./\s]{0,2})([0-9]{7})").unwrap());
// 카드(ko-pii card.py): 구분자 그룹형 또는 무구분 13~19자리.
static RE_CARD: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?:[0-9]{4}[-. /]\s?[0-9]{4}[-. /]\s?[0-9]{4}[-. /]\s?[0-9]{1,7}|[0-9]{13,19})").unwrap());
static RE_BRN: Lazy<Regex> = Lazy::new(|| Regex::new(r"\d{3}[-\s]?\d{2}[-\s]?\d{5}").unwrap());
// 법인등록번호: 구분자는 RRN 과 같은 규약(하이픈·점·슬래시·공백, 줄바꿈 래핑 포함).
// 예전에는 `[-\s]?` 뿐이라 "110111.0000002" 처럼 점으로 쓴 표기를 놓쳤다 —
// 못 잡으면 문서가 실제보다 낮은 등급을 받으므로 파이썬과 같은 폭으로 맞춘다.
static RE_CORP: Lazy<Regex> = Lazy::new(||
    Regex::new(r"([0-9]{6})(?:\s?[-./]\s?|[-./\s]{0,2})([0-9]{7})").unwrap());
// 전화번호(ko-pii phone.py 와 1:1). 국제 접두는 +82 / 0082 / 82, 그 뒤에 (0) 이
// 끼는 표기까지 받는다. 국제표기에서는 지역번호·통신사번호의 맨 앞 0 이 빠진다(E.123).
const PH_INTL: &str = r"(?:\+82|0082|82)[-.\s]?(?:\(0\)[-.\s]?)?";
static RE_PH_MOBILE: Lazy<Regex> = Lazy::new(||
    Regex::new(r"(01[01679])[-.\s]{0,3}(\d{3,4})[-.\s]{0,3}(\d{4})").unwrap());
static RE_PH_MOBILE_INTL: Lazy<Regex> = Lazy::new(||
    Regex::new(&format!(r"{}(1[01679])[-.\s]{{0,3}}(\d{{3,4}})[-.\s]{{0,3}}(\d{{4}})", PH_INTL)).unwrap());
static RE_PH_SEOUL: Lazy<Regex> = Lazy::new(||
    Regex::new(r"(02)[-.\s)\]]{0,3}(\d{3,4})[-.\s]{0,3}(\d{4})").unwrap());
static RE_PH_SEOUL_INTL: Lazy<Regex> = Lazy::new(||
    Regex::new(&format!(r"{}(2)[-.\s]{{0,3}}(\d{{3,4}})[-.\s]{{0,3}}(\d{{4}})", PH_INTL)).unwrap());
static RE_PH_REGIONAL: Lazy<Regex> = Lazy::new(||
    Regex::new(r"(03[1-3]|04[1-4]|05[1-5]|06[1-4]|070)[-.\s)\]]{0,3}(\d{3,4})[-.\s]{0,3}(\d{4})").unwrap());
static RE_PH_REGION_INTL: Lazy<Regex> = Lazy::new(||
    Regex::new(&format!(r"{}(3[1-3]|4[1-4]|5[1-5]|6[1-4]|70)[-.\s]{{0,3}}(\d{{3,4}})[-.\s]{{0,3}}(\d{{4}})", PH_INTL)).unwrap());
// 대표번호(15xx~18xx, KISA 번호자원 가이드). 4-4 라 제품번호와 모양이 같아 오탐이
// 남지만, 파이썬 판이 recall 을 택한 것과 같은 판단을 그대로 따른다.
static RE_PH_REPRESENT: Lazy<Regex> = Lazy::new(||
    Regex::new(r"(1[5-8]\d{2})[-.\s]{0,3}(\d{4})").unwrap());
static RE_EMAIL: Lazy<Regex> = Lazy::new(|| Regex::new(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}").unwrap());
// 여권(ko-pii passport.py): 대문자 prefix 화이트리스트(2자 우선) + 8자리.
static RE_PASSPORT: Lazy<Regex> = Lazy::new(|| Regex::new(r"(PP|PM|PS|PO|PD|PR|PT|M|S|G|O|D|R|T)([0-9]{8})").unwrap());
static RE_URL: Lazy<Regex> = Lazy::new(|| Regex::new(r#"https?://[^\s"'<>]+"#).unwrap());
// 차량(ko-pii vehicle.py): 2~3자리 + 용도한글 + 4자리.
static RE_VEHICLE: Lazy<Regex> = Lazy::new(|| Regex::new(r"([0-9]{2,3})\s?([가-힣])\s?([0-9]{4})").unwrap());
static RE_IPV4: Lazy<Regex> = Lazy::new(|| Regex::new(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}").unwrap());
// 계좌(ko-pii account.py parity): 은행명/‘계좌’ 앵커가 있어야 인정.
const BANK_NAMES: &[&str] = &[
    "국민은행","신한은행","우리은행","하나은행","기업은행","IBK","KEB하나은행","KEB","KB국민은행","KB",
    "농협은행","농협","NH농협","NH","수협은행","수협","Sh수협","SC제일은행","제일은행","씨티은행","한국씨티",
    "외환은행","스탠다드차타드","카카오뱅크","카뱅","카카오페이","토스뱅크","토스뱅킹","토스페이","케이뱅크",
    "K뱅크","네이버페이","부산은행","BNK부산","대구은행","DGB대구","경남은행","BNK경남","광주은행","전북은행",
    "제주은행","JB제주","산업은행","KDB산업","수출입은행","EXIM","한국은행","BOK","우체국","우정사업본부","우정청",
    "새마을금고","MG새마을","MG","신협","신용협동조합","수산업협동조합","농업협동조합","국민","신한","우리","하나",
];
fn bank_alt() -> String { BANK_NAMES.iter().map(|b| regex::escape(b)).collect::<Vec<_>>().join("|") }
static RE_ACCT_KW: Lazy<Regex> = Lazy::new(|| Regex::new(r"계좌\s*(?:번호|번)?\s*:?\s*([0-9][\s\-]*(?:[0-9][\s\-]*){9,19})").unwrap());
static RE_ACCT_BANK_BEFORE: Lazy<Regex> = Lazy::new(|| Regex::new(&format!(r"(?:{})\s*:?\s*([0-9]+(?:[\s\-][0-9]+){{1,4}}|[0-9]{{10,16}})", bank_alt())).unwrap());
static RE_ACCT_BANK_AFTER: Lazy<Regex> = Lazy::new(|| Regex::new(&format!(r"([0-9]+(?:[\s\-][0-9]+){{1,4}}|[0-9]{{10,16}})\s*(?:{})", bank_alt())).unwrap());

fn is_digit(b: u8) -> bool { b.is_ascii_digit() }
fn is_alnum(b: u8) -> bool { b.is_ascii_alphanumeric() }
fn is_digit_or_dot(b: u8) -> bool { b.is_ascii_digit() || b == b'.' }

/// 매치가 앞/뒤 '금지 이웃'에 붙어있지 않은지(=고립된 토큰인지) 검사.
fn isolated(text: &str, m: &Match, before_bad: fn(u8) -> bool, after_bad: fn(u8) -> bool) -> bool {
    let b = text.as_bytes();
    let ok_before = m.start() == 0 || !before_bad(b[m.start() - 1]);
    let ok_after = m.end() >= b.len() || !after_bad(b[m.end()]);
    ok_before && ok_after
}

fn digits(s: &str) -> Vec<u8> {
    s.bytes().filter(|b| b.is_ascii_digit()).map(|b| b - b'0').collect()
}

// ---- 오늘 날짜(미래 생년월일 배제용). SystemTime → (년,월,일). ----
static TODAY: Lazy<(i64, u32, u32)> = Lazy::new(|| {
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0);
    civil_from_days(secs / 86400)
});
/// UNIX epoch 기준 일수 → (년,월,일). Howard Hinnant civil_from_days.
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as i64;              // [0,146096]
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365; // [0,399]
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100); // [0,365]
    let mp = (5 * doy + 2) / 153;                       // [0,11]
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;      // [1,31]
    let m = (if mp < 10 { mp + 3 } else { mp - 9 }) as u32;
    (y + if m <= 2 { 1 } else { 0 }, m, d)
}
/// 그레고리력 유효 날짜인지(윤년 포함).
fn valid_ymd(y: i64, m: u32, d: u32) -> bool {
    if m < 1 || m > 12 || d < 1 { return false; }
    let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    let dim = [31, if leap {29} else {28}, 31,30,31,30,31,31,30,31,30,31][(m-1) as usize];
    d <= dim
}
/// (y,m,d) 가 오늘 이후(미래)인지.
fn is_future(y: i64, m: u32, d: u32) -> bool { (y, m, d) > *TODAY }

// ---- 체크섬(ko-pii checksum/*) ----
/// RRN mod-11 체크섬. 13자리.
fn rrn_checksum_ok(d: &[u8]) -> bool {
    if d.len() != 13 { return false; }
    const W: [u32; 12] = [2,3,4,5,6,7,8,9,2,3,4,5];
    let sum: u32 = (0..12).map(|i| d[i] as u32 * W[i]).sum();
    ((11 - sum % 11) % 10) == d[12] as u32
}
/// 법인등록번호 체크섬(가중치 1,2 교대 straight-sum). 13자리.
fn corp_checksum_ok(d: &[u8]) -> bool {
    if d.len() != 13 { return false; }
    let mut sum = 0u32;
    for i in 0..12 { sum += d[i] as u32 * if i % 2 == 0 { 1 } else { 2 }; }
    ((10 - sum % 10) % 10) == d[12] as u32
}
/// 사업자등록번호 체크섬(국세청). 10자리.
fn brn_checksum_ok(d: &[u8]) -> bool {
    if d.len() != 10 { return false; }
    const W: [u32; 9] = [1,3,7,1,3,7,1,3,5];
    let mut sum: u32 = (0..9).map(|i| d[i] as u32 * W[i]).sum();
    sum += (d[8] as u32 * 5) / 10;
    ((10 - sum % 10) % 10) == d[9] as u32
}
fn luhn_ok(d: &[u8]) -> bool {
    let mut sum = 0u32;
    let mut alt = false;
    for &x in d.iter().rev() {
        let mut v = x as u32;
        if alt { v *= 2; if v > 9 { v -= 9; } }
        sum += v;
        alt = !alt;
    }
    sum % 10 == 0
}

// ================= 주민등록번호 RRN (ko-pii rrn.py) =================
// 성별자리(7번째) → 세기. 내국인 {1,2,3,4,9,0}.
fn rrn_century(g: u8) -> Option<i64> {
    match g { 1|2 => Some(1900), 3|4 => Some(2000), 9|0 => Some(1800), _ => None }
}
fn frn_century(g: u8) -> Option<i64> {
    match g { 5|6 => Some(1900), 7|8 => Some(2000), _ => None }
}
/// RRN 한 후보 판정 → Det(또는 None). span=[start,end)(prefix 제외분은 호출측에서 offset 반영).
fn rrn_emit(front: &[u8], back: &[u8], start: usize, end: usize, real_no_sep: bool) -> Option<Det> {
    let g = back[0];
    let century = rrn_century(g)?;
    let y = century + front[0] as i64 * 10 + front[1] as i64;
    let m = front[2] as u32 * 10 + front[3] as u32;
    let d = front[4] as u32 * 10 + front[5] as u32;
    if !valid_ymd(y, m, d) { return None; }
    if is_future(y, m, d) { return None; }   // 미래 생년 → GS1 880 바코드 등 FP 차단
    let all: Vec<u8> = front.iter().chain(back.iter()).copied().collect();
    let checksum_ok = rrn_checksum_ok(&all);
    // 체크섬 실패 + 성별0 + 법인체크섬 통과 → 법인등록번호로 보고 RRN 미방출.
    if !checksum_ok && g == 0 && corp_checksum_ok(&all) { return None; }
    // 무구분자 880~ GS1/EAN-13 바코드 → RRN 아님.
    if !checksum_ok && real_no_sep && front[0]==8 && front[1]==8 && front[2]==0 { return None; }
    let conf = if checksum_ok { 1.0 } else { 0.7 };
    Some(Det { start, end, risk: R_CRIT, conf, label: "RRN" })
}
fn det_rrn(text: &str) -> Vec<Det> {
    let mut out: Vec<Det> = Vec::new();
    let mut seen: Vec<(usize, usize)> = Vec::new();
    let bytes = text.as_bytes();
    // 기본 패턴.
    for m in RE_RRN.captures_iter(text) {
        let whole = m.get(0).unwrap();
        // (?<![0-9]) / (?![0-9])
        if whole.start() > 0 && bytes[whole.start()-1].is_ascii_digit() { continue; }
        if whole.end() < bytes.len() && bytes[whole.end()].is_ascii_digit() { continue; }
        let front = digits(&m[1]); let back = digits(&m[2]);
        let real_no_sep = m[0].chars().filter(|c| !c.is_ascii_digit()).count() == 0;
        if let Some(det) = rrn_emit(&front, &back, whole.start(), whole.end(), real_no_sep) {
            seen.push((det.start, det.end));
            out.push(det);
        }
    }
    // PDF prefix 패턴(관계코드 1자리 뒤 RRN). span 은 offset 1.
    for m in RE_RRN_PREFIXED.captures_iter(text) {
        let whole = m.get(0).unwrap();
        if whole.start() > 0 && bytes[whole.start()-1].is_ascii_digit() { continue; }
        if whole.end() < bytes.len() && bytes[whole.end()].is_ascii_digit() { continue; }
        let start = whole.start() + 1;
        let front = digits(&m[1]); let back = digits(&m[2]);
        let real = &text[start..whole.end()];
        let real_no_sep = real.chars().filter(|c| !c.is_ascii_digit()).count() == 0;
        if seen.iter().any(|&(s,e)| s==start && e==whole.end()) { continue; }
        if let Some(det) = rrn_emit(&front, &back, start, whole.end(), real_no_sep) {
            seen.push((det.start, det.end));
            out.push(det);
        }
    }
    out
}
fn det_frn(text: &str) -> Vec<Det> {
    let mut out: Vec<Det> = Vec::new();
    let bytes = text.as_bytes();
    for m in RE_RRN.captures_iter(text) {
        let whole = m.get(0).unwrap();
        if whole.start() > 0 && bytes[whole.start()-1].is_ascii_digit() { continue; }
        if whole.end() < bytes.len() && bytes[whole.end()].is_ascii_digit() { continue; }
        let front = digits(&m[1]); let back = digits(&m[2]);
        let century = match frn_century(back[0]) { Some(c) => c, None => continue };
        let y = century + front[0] as i64 * 10 + front[1] as i64;
        let mm = front[2] as u32 * 10 + front[3] as u32;
        let dd = front[4] as u32 * 10 + front[5] as u32;
        if !valid_ymd(y, mm, dd) { continue; }
        let all: Vec<u8> = front.iter().chain(back.iter()).copied().collect();
        let conf = if rrn_checksum_ok(&all) { 1.0 } else { 0.7 };
        out.push(Det { start: whole.start(), end: whole.end(), risk: R_CRIT, conf, label: "FRN" });
    }
    out
}

// ================= 카드/사업자/법인 =================
/// 카드(ko-pii card.py): BIN 첫자리 화이트리스트 + 길이-브랜드 일관성 + Luhn.
fn det_card(text: &str) -> Vec<Det> {
    let mut out = Vec::new();
    for m in RE_CARD.find_iter(text) {
        if !isolated(text, &m, is_digit, is_digit) { continue; }
        let d = digits(m.as_str());
        if !(13..=19).contains(&d.len()) { continue; }
        // BIN 첫자리 화이트리스트({2,3,4,5,6,9}) — 0/1/7/8 은 미할당.
        if !matches!(d[0], 2|3|4|5|6|9) { continue; }
        // 길이-브랜드 일관성: 13자리=Visa(4)만, 15자리=Amex(34/37)만.
        if d.len() == 13 && d[0] != 4 { continue; }
        if d.len() == 15 && !(d[0]==3 && (d[1]==4 || d[1]==7)) { continue; }
        if !luhn_ok(&d) { continue; }
        out.push(Det { start: m.start(), end: m.end(), risk: R_HIGH, conf: 1.0, label: "CARD" });
    }
    out
}
fn det_brn(text: &str) -> Vec<Det> {
    let mut out = Vec::new();
    for m in RE_BRN.find_iter(text) {
        if !isolated(text, &m, is_digit, is_digit) { continue; }
        let d = digits(m.as_str());
        // 모두 0 인 placeholder 는 체크섬을 통과하지만(합=0 → 검증숫자 0) 실제
        // 사업자가 아니다. 표의 빈칸을 0 으로 채운 문서에서 무더기로 잡히고,
        // 겹침 해소에서 전화번호 자리를 빼앗아 두 라벨의 집계까지 어긋난다
        // (실측: 주소록 한 건에서 사업자등록번호 1→13, 전화 647→636).
        if d.iter().all(|&x| x == 0) { continue; }
        if brn_checksum_ok(&d) {
            out.push(Det { start: m.start(), end: m.end(), risk: R_HIGH, conf: 1.0, label: "BUSINESS_REG" });
        }
    }
    out
}
fn det_corp(text: &str) -> Vec<Det> {
    let mut out = Vec::new();
    for m in RE_CORP.find_iter(text) {
        if !isolated(text, &m, is_digit, is_digit) { continue; }
        let d = digits(m.as_str());
        if corp_checksum_ok(&d) {
            out.push(Det { start: m.start(), end: m.end(), risk: R_MED, conf: 1.0, label: "CORP_REG" });
        }
    }
    out
}

// ================= 전화 / 단순 정규식 / 여권 =================
/// 전화번호 검출(ko-pii phone.py 전면 대응).
///
/// 예전에는 정규식 하나로 뭉뚱그렸는데, 그러면 **국제표기(+82/0082)·070·대표번호
/// (15xx~18xx)·구분자가 여러 칸인 표기**를 통째로 놓쳤다(실측: 파이썬 5건 중 1건만
/// 검출). 전화번호를 못 잡으면 문서가 실제보다 낮은 등급을 받으므로, 덜 잡는 쪽이
/// 더 위험하다. 그래서 파이썬과 같은 패턴 7개를 같은 순서로 적용한다.
///
/// [순서가 중요한 이유] 국제표기가 먼저다. "+82-10-1234-5678" 은 안쪽에 국내표기
/// 모양("10-1234-5678")을 품고 있어서, 국내 패턴이 먼저 잡으면 `+82` 가 잘려 나간다.
/// 파이썬과 똑같이 이미 잡은 구간과 겹치면 건너뛴다.
fn det_phone(text: &str) -> Vec<Det> {
    let mut out: Vec<Det> = Vec::new();
    let mut seen: Vec<(usize, usize)> = Vec::new();

    // (정규식, 모바일인가, 앞에 '+' 도 금지인가)
    //   국제표기는 앞에 '+' 가 오는 게 정상이라 '+' 를 금지하지 않는다.
    //   국내표기는 '+' 가 앞에 오면 국제표기의 일부이므로 금지한다(= 국제 패턴에 양보).
    let steps: [(&Lazy<Regex>, bool, bool); 7] = [
        (&RE_PH_MOBILE_INTL, true,  false),
        (&RE_PH_SEOUL_INTL,  false, false),
        (&RE_PH_REGION_INTL, false, false),
        (&RE_PH_MOBILE,      true,  true),
        (&RE_PH_REGIONAL,    false, true),
        (&RE_PH_SEOUL,       false, true),
        (&RE_PH_REPRESENT,   false, true),
    ];

    for (re, mobile, no_plus) in steps {
        for m in re.find_iter(text) {
            // look-around 이 없으므로 앞뒤 경계는 코드로 본다.
            let b = text.as_bytes();
            let before_ok = m.start() == 0 || {
                let p = b[m.start() - 1];
                !p.is_ascii_digit() && !(no_plus && p == b'+')
            };
            let after_ok = m.end() >= b.len() || !b[m.end()].is_ascii_digit();
            if !before_ok || !after_ok { continue; }
            // 앞선 패턴이 이미 가져간 구간이면 양보한다(파이썬 _overlaps 와 동일).
            if seen.iter().any(|&(s, e)| m.start() < e && s < m.end()) { continue; }
            seen.push((m.start(), m.end()));
            // 휴대전화는 개인 직통(HIGH), 유선·VoIP·대표번호는 사업장 다수(MEDIUM).
            let risk = if mobile { R_HIGH } else { R_MED };
            out.push(Det { start: m.start(), end: m.end(), risk, conf: 1.0, label: "PHONE" });
        }
    }
    out
}
fn det_simple(text: &str, re: &Regex, label: &'static str, risk: i32, conf: f64) -> Vec<Det> {
    re.find_iter(text).map(|m| Det { start: m.start(), end: m.end(), risk, conf, label }).collect()
}
/// 여권(ko-pii passport.py): 앞뒤 영숫자 경계 + 8자리 all-zero 거부.
fn det_passport(text: &str) -> Vec<Det> {
    let mut out = Vec::new();
    for m in RE_PASSPORT.captures_iter(text) {
        let whole = m.get(0).unwrap();
        // (?<![A-Za-z0-9]) / (?![A-Za-z0-9])
        let b = text.as_bytes();
        if whole.start() > 0 && b[whole.start()-1].is_ascii_alphanumeric() { continue; }
        if whole.end() < b.len() && b[whole.end()].is_ascii_alphanumeric() { continue; }
        if &m[2] == "00000000" { continue; }
        out.push(Det { start: whole.start(), end: whole.end(), risk: R_CRIT, conf: 0.9, label: "PASSPORT" });
    }
    out
}

// 차량 용도 한글 화이트리스트(자동차관리법 시행규칙 별표7) — 약 50자.
const VEHICLE_HANGUL: &[&str] = &[
    "가","나","다","라","마","거","너","더","러","머","버","서","어","저",
    "고","노","도","로","모","보","소","오","조","구","누","두","루","무","부","수","우","주",
    "바","사","아","자","배","하","허","호","외","영","준","협","대","국","합","육","해","공",
];
// 차량번호 뒤 한국어 수량/통화 단위 → 차량 아님(ko-pii _FOLLOWING_UNIT_REJECT).
const VEHICLE_UNIT_REJECT: &[&str] = &[
    "원","달러","엔","위안","유로","파운드","프랑","억","만","천","백","조",
    "%","퍼센트","퍼센트포인트","포인트","포",
    "건","건수","명","년","월","일","시간","분","초","톤","kg","g","m","km","mm",
];
/// 차량 등록번호(ko-pii vehicle.py): 용도한글 화이트리스트 + 4자리(0000 제외) + 단위어 거부.
fn det_vehicle(text: &str) -> Vec<Det> {
    let mut out = Vec::new();
    for m in RE_VEHICLE.captures_iter(text) {
        let whole = m.get(0).unwrap();
        // (?<![0-9가-힣]) : 앞 글자가 숫자·한글이면 거부.
        if whole.start() > 0 {
            let c = text[..whole.start()].chars().next_back().unwrap();
            if c.is_ascii_digit() || is_hangul(c) { continue; }
        }
        // (?![0-9]) : 뒤 글자가 숫자면 거부(한글 조사는 허용).
        if digit_after(text, whole.end()) { continue; }
        let purpose = &m[2];
        if !VEHICLE_HANGUL.contains(&purpose) { continue; }
        if &m[3] == "0000" { continue; }
        // 뒤에 수량/통화 단위가 붙으면 차량 아님.
        let tail = right_window(text, whole.end(), 8);
        let tail = tail.trim_start();
        if VEHICLE_UNIT_REJECT.iter().any(|u| tail.starts_with(u)) { continue; }
        out.push(Det { start: whole.start(), end: whole.end(), risk: R_LOW, conf: 0.85, label: "VEHICLE" });
    }
    out
}

/// IPv4: 옥텟검증 + 예약대역 제외 + 버전/섹션 문맥 제외(ko-pii ip.py parity).
fn det_ipv4(text: &str) -> Vec<Det> {
    let mut out = Vec::new();
    for m in RE_IPV4.find_iter(text) {
        if !isolated(text, &m, is_digit_or_dot, is_digit_or_dot) { continue; }
        let oct: Vec<u32> = m.as_str().split('.').filter_map(|o| o.parse().ok()).collect();
        if oct.len() != 4 || oct.iter().any(|&v| v > 255) { continue; }
        if is_reserved_ipv4(&oct) { continue; }
        let left = left_window(text, m.start(), 12);
        if RE_IP_CTX_LEFT.is_match(&left) { continue; }
        let right = right_window(text, m.end(), 8);
        if RE_IP_CTX_RIGHT.is_match(&right) { continue; }
        out.push(Det { start: m.start(), end: m.end(), risk: R_MED, conf: 1.0, label: "IP" });
    }
    out
}
fn is_reserved_ipv4(o: &[u32]) -> bool {
    let (a, b, c) = (o[0], o[1], o[2]);
    a == 127 || a == 0
        || (a == 169 && b == 254)
        || (a == 192 && b == 0 && c == 2)
        || (a == 198 && b == 51 && c == 100)
        || (a == 203 && b == 0 && c == 113)
        || a >= 224
}
static RE_IP_CTX_LEFT: Lazy<Regex> = Lazy::new(|| Regex::new(
    r"(?i)(?:버전|버젼|펌웨어|릴리[스즈]|빌드|패치|표|그림|별표|도표|붙임|항목|조항|조|항|단계|절|장|챕터|version|firmware|release|build|patch|section|chapter|figure|table|appendix|clause)\s*[.·:]?\s*$"
).unwrap());
static RE_IP_CTX_RIGHT: Lazy<Regex> = Lazy::new(|| Regex::new(
    r"(?i)^\s*(?:버전|버젼|version|빌드|build|릴리[스즈])"
).unwrap());

// ---- 문자 유틸 ----
fn is_hangul(c: char) -> bool { ('\u{AC00}'..='\u{D7A3}').contains(&c) }

/// start(byte) 앞 nchars 글자(순서 유지).
fn left_window(text: &str, start: usize, nchars: usize) -> String {
    let pre = &text[..start];
    let mut chars: Vec<char> = pre.chars().rev().take(nchars).collect();
    chars.reverse();
    chars.into_iter().collect()
}
/// end(byte) 뒤 nchars 글자.
fn right_window(text: &str, end: usize, nchars: usize) -> String {
    text[end..].chars().take(nchars).collect()
}

// ---- 국적/조사 사전 ----
// ko-pii context/particles.py 원본 순서 그대로(긴 조사 우선).
const PARTICLES: &[&str] = &[
    "에게서","한테서","께서",
    "에게","한테","에서","으로","보다",
    "이가","이는","이도","이를",
    "은","는","이","가","을","를","와","과","의",
    "에","도","만","야","라","여",
];
fn is_country(tok: &str) -> bool { districts::is_country(tok) }
/// 조사 최장일치 제거 → (남는 토큰, 제거된 조사).
fn strip_particle(tok: &str) -> (&str, Option<&'static str>) {
    for p in PARTICLES {
        if tok.len() > p.len() && tok.ends_with(p) {
            return (&tok[..tok.len() - p.len()], Some(p));
        }
    }
    (tok, None)
}

/// 국적/국가명(ko-pii nationality.py): 고립 한글토큰(2-8) → 조사제거 → '인'제거 → 국가사전.
fn det_nationality(text: &str) -> Vec<Det> {
    let mut out = Vec::new();
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    let mut i = 0;
    while i < chars.len() {
        if !is_hangul(chars[i].1) { i += 1; continue; }
        let start = i;
        while i < chars.len() && is_hangul(chars[i].1) { i += 1; }
        let run_len = i - start;
        let before_ok = start == 0 || !chars[start - 1].1.is_ascii_alphanumeric();
        let after_ok = i >= chars.len() || !chars[i].1.is_ascii_alphanumeric();
        if !(2..=8).contains(&run_len) || !before_ok || !after_ok { continue; }
        let start_b = chars[start].0;
        let tok: String = chars[start..i].iter().map(|(_, c)| *c).collect();
        let (stem, particle) = strip_particle(&tok);
        let mut t = stem.to_string();
        if t.chars().count() < 2 { continue; }
        let mut people = false;
        if t.chars().count() >= 3 && t.ends_with('인') {
            let s = &t[..t.len() - '인'.len_utf8()];
            if is_country(s) { t = s.to_string(); people = true; }
        }
        if !is_country(&t) { continue; }
        // 실제 end: 원 토큰 끝에서 조사/‘인’ 제외.
        let raw_end = start_b + tok.len();
        let end_b = raw_end - particle.map(|p| p.len()).unwrap_or(0) - if people { '인'.len_utf8() } else { 0 };
        out.push(Det { start: start_b, end: end_b, risk: R_LOW, conf: 0.7, label: "NATIONALITY" });
    }
    out
}

// ---- 주소(ko-pii address.py 전면 포팅: 도로명·지번·대화체·단독행정구역 4브랜치) ----
static RE_ROAD: Lazy<Regex> = Lazy::new(|| Regex::new(
    r"(?:([가-힣]{1,8}(?:특별시|광역시|특별자치도|특별자치시|도))\s*)?((?:[가-힣]{1,8}(?:시|군|구)\s*){0,2})([가-힣A-Za-z0-9]{1,16}(?:대로|로|길))\s*([0-9]+(?:-[0-9]+)?)"
).unwrap());
static RE_JIBUN: Lazy<Regex> = Lazy::new(|| Regex::new(
    r"(?:([가-힣]{1,8}(?:특별시|광역시|특별자치도|특별자치시|도))\s*)?((?:[가-힣]{1,8}(?:시|군|구)\s*){0,2})([가-힣]{1,8}(?:동|읍|면|리))\s*([0-9]+(?:-[0-9]+)?)"
).unwrap());
static RE_LOOSE: Lazy<Regex> = Lazy::new(|| Regex::new(
    r"([가-힣]{2,}(?:시|군|구|동|읍|면|리))(?:\s+([가-힣]+(?:동|읍|면|리)))?(?:\s+([0-9]+(?:-[0-9]+)?))?"
).unwrap());
const LOOSE_ANCHORS: &[&str] = &[
    "주소","자택","거주","본적",
    "사세요","사신다","사셨","사신","사세",
    "사는","사니까","살던","살아","살고","산다","산대",
    "이사","이사하","이사했",
    "명함",
];
const LOOSE_BIG_CITIES: &[&str] = &["서울시","부산시","대구시","광주시","대전시","울산시","인천시","수원시","고양시","용인시"];
const ADMIN_PROVINCE_ALIASES: &[&str] = &["강원도","충청도","전라도","경상도","제주도","서울시","부산시","대구시","인천시","광주시","대전시","울산시"];

/// 주소 검출: ko-pii address.py 4브랜치. 각 브랜치는 자체 seen(±slack)으로 내부 겹침 관리.
fn det_address(text: &str) -> Vec<Det> {
    let mut seen: Vec<(usize, usize)> = vec![];
    let mut out: Vec<Det> = vec![];
    // 1) 도로명 — MEDIUM 0.8
    for m in RE_ROAD.captures_iter(text) {
        let mm = m.get(0).unwrap();
        if !addr_struct_ok(&m) { continue; }
        if !addr_tail_ok(text, mm.end()) { continue; }
        if addr_anchor(text, &m, mm.start()).is_none() { continue; }
        seen.push((mm.start(), mm.end()));
        out.push(Det { start: mm.start(), end: mm.end(), risk: R_MED, conf: 0.8, label: "ADDRESS" });
    }
    // 2) 지번 — MEDIUM 0.75
    for m in RE_JIBUN.captures_iter(text) {
        let mm = m.get(0).unwrap();
        if overlaps(&seen, mm.start(), mm.end(), 0) { continue; }
        if !addr_struct_ok(&m) { continue; }
        if !addr_tail_ok(text, mm.end()) { continue; }
        if addr_anchor(text, &m, mm.start()).is_none() { continue; }
        seen.push((mm.start(), mm.end()));
        out.push(Det { start: mm.start(), end: mm.end(), risk: R_MED, conf: 0.75, label: "ADDRESS" });
    }
    // 3) 대화체 단독 — MEDIUM 0.6
    for m in RE_LOOSE.captures_iter(text) {
        let mm = m.get(0).unwrap();
        if !hangul_boundary(text, mm.start(), mm.end()) { continue; }
        if overlaps(&seen, mm.start(), mm.end(), 30) { continue; }
        let first = m.get(1).unwrap().as_str();
        let ok = LOOSE_BIG_CITIES.contains(&first) || districts::is_district(first);
        if !ok { continue; }
        if loose_anchor(text, mm.start(), mm.end()).is_none() { continue; }
        seen.push((mm.start(), mm.end()));
        out.push(Det { start: mm.start(), end: mm.end(), risk: R_MED, conf: 0.6, label: "ADDRESS" });
    }
    // 4) 단독 행정구역 토큰 — LOW 0.7 (국가명 제외)
    for (s, e, tok) in admin_tokens(text) {
        if overlaps(&seen, s, e, 50) { continue; }
        if is_country(&tok) { continue; }
        if tok.chars().count() >= 3 && tok.ends_with('인') {
            let stem = &tok[..tok.len() - '인'.len_utf8()];
            if is_country(stem) { continue; }
        }
        let is_admin = districts::is_province(&tok)
            || ADMIN_PROVINCE_ALIASES.contains(&tok.as_str())
            || districts::is_district(&tok)
            || districts::is_extra_city(&tok)
            || districts::is_common_dong(&tok)
            || districts::is_legal_dong(&tok);
        if !is_admin { continue; }
        if loose_anchor(text, s, e).is_none() { continue; }
        seen.push((s, e));
        out.push(Det { start: s, end: e, risk: R_LOW, conf: 0.7, label: "ADDRESS" });
    }
    out
}

/// 도로명/지번 구조 검증: 광역 prefix 실존 + (광역+기초) 조합 유효.
fn addr_struct_ok(cap: &regex::Captures) -> bool {
    let city = cap.get(1).map(|m| m.as_str()).unwrap_or("");
    if !city.is_empty() && !districts::is_province(city) { return false; }
    let districts_str = cap.get(2).map(|m| m.as_str().trim()).unwrap_or("");
    let first_district = districts_str.split_whitespace().next().unwrap_or("");
    if !city.is_empty() && !first_district.is_empty()
        && !districts::is_valid_province_district(city, first_district) {
        return false;
    }
    true
}
/// 매치 끝 뒤에 숫자/하이픈이 이어지면 거부(ko-pii `(?![0-9-])`).
fn addr_tail_ok(text: &str, end: usize) -> bool {
    match text.as_bytes().get(end) {
        Some(&b) => !(b.is_ascii_digit() || b == b'-'),
        None => true,
    }
}
/// 앵커(ko-pii _has_anchor): 광역/시군구 prefix 또는 좌측20자 '주소'.
fn addr_anchor(text: &str, cap: &regex::Captures, start: usize) -> Option<&'static str> {
    let has_city = cap.get(1).map(|m| !m.as_str().is_empty()).unwrap_or(false);
    let has_dist = cap.get(2).map(|m| !m.as_str().trim().is_empty()).unwrap_or(false);
    if has_city || has_dist { return Some("prefix"); }
    if left_window(text, start, 20).contains("주소") { return Some("keyword"); }
    None
}
/// 대화체 anchor(ko-pii _has_loose_anchor): 앞25/뒤12자에 강한 keyword.
fn loose_anchor(text: &str, start: usize, end: usize) -> Option<&'static str> {
    let head = left_window(text, start, 25);
    let tail = right_window(text, end, 12);
    for kw in LOOSE_ANCHORS {
        if head.contains(kw) || tail.contains(kw) { return Some(kw); }
    }
    None
}
/// 단독 행정구역 토큰: 최대 한글런(2~6, 영숫자 인접 배제) → 조사 제거한 stem.
fn admin_tokens(text: &str) -> Vec<(usize, usize, String)> {
    let mut out = vec![];
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    let mut i = 0;
    while i < chars.len() {
        if !is_hangul(chars[i].1) { i += 1; continue; }
        let start = i;
        while i < chars.len() && is_hangul(chars[i].1) { i += 1; }
        let run_len = i - start;
        let before_ok = start == 0 || !chars[start - 1].1.is_ascii_alphanumeric();
        let after_ok = i >= chars.len() || !chars[i].1.is_ascii_alphanumeric();
        if !(2..=6).contains(&run_len) || !before_ok || !after_ok { continue; }
        let raw: String = chars[start..i].iter().map(|(_, c)| *c).collect();
        let (stem, particle) = strip_particle(&raw);
        if stem.chars().count() < 2 { continue; }
        let start_b = chars[start].0;
        let end_b = start_b + raw.len() - particle.map(|p| p.len()).unwrap_or(0);
        out.push((start_b, end_b, stem.to_string()));
    }
    out
}
/// [s,e) 앞뒤 글자가 한글이 아닌지. ko-pii (?<![가-힣])/(?![가-힣]).
fn hangul_boundary(text: &str, start: usize, end: usize) -> bool {
    let before_ok = start == 0 || {
        let c = text[..start].chars().next_back().unwrap();
        !is_hangul(c)
    };
    let after_ok = end >= text.len() || {
        let c = text[end..].chars().next().unwrap();
        !is_hangul(c)
    };
    before_ok && after_ok
}
/// span 목록 중 [s,e) 와 (±slack 확장) 겹치는 게 있는지.
fn overlaps(seen: &[(usize, usize)], s: usize, e: usize, slack: usize) -> bool {
    seen.iter().any(|&(a, b)| s < b + slack && a.saturating_sub(slack) < e)
}

/// 계좌: 세 앵커 중 하나 + 10~16자리. HIGH 0.9. 동일 숫자 span 중복 제거.
fn det_account(text: &str) -> Vec<Det> {
    let mut spans: Vec<(usize, usize)> = vec![];
    let collect = |re: &Regex, spans: &mut Vec<(usize, usize)>, check_before: bool| {
        for cap in re.captures_iter(text) {
            let g = cap.get(1).unwrap();
            if check_before && g.start() > 0 && text.as_bytes()[g.start() - 1].is_ascii_digit() {
                continue;
            }
            // 정규식 꼬리의 `[\s\-]*` 가 뒤따르는 공백·줄바꿈까지 삼킨다. 그대로 두면
            // 검출값에 "\r\n" 이 붙어 나오고(--with-pii), span 이 실제보다 길어져
            // 겹침 해소에서 다른 라벨을 부당하게 밀어낸다. 파이썬 판이 group(1) 을
            // rstrip 해 span 을 줄이는 것과 똑같이 맞춘다.
            let raw = g.as_str().trim_end();
            let end = g.start() + raw.len();
            let d = digits(raw);
            if (10..=16).contains(&d.len()) {
                spans.push((g.start(), end));
            }
        }
    };
    collect(&RE_ACCT_KW, &mut spans, false);
    collect(&RE_ACCT_BANK_BEFORE, &mut spans, false);
    collect(&RE_ACCT_BANK_AFTER, &mut spans, true);
    spans.sort();
    spans.dedup();
    spans.into_iter().map(|(s, e)| Det { start: s, end: e, risk: R_HIGH, conf: 0.9, label: "ACCOUNT" }).collect()
}

// ================= 공통 문맥/경계 헬퍼 (신규 검출기 6종 공용) =================
fn kw_before(text: &str, start: usize, window: usize, kws: &[&str]) -> bool {
    let head = left_window(text, start, window);
    kws.iter().any(|kw| head.contains(kw))
}
fn digit_before(text: &str, pos: usize) -> bool {
    pos > 0 && text.as_bytes()[pos - 1].is_ascii_digit()
}
fn digit_after(text: &str, pos: usize) -> bool {
    text.as_bytes().get(pos).map_or(false, |b| b.is_ascii_digit())
}
fn num_isolated(text: &str, s: usize, e: usize) -> bool {
    !digit_before(text, s) && !digit_after(text, e)
}
fn num2(d: &[u8], i: usize) -> u32 { d[i] as u32 * 10 + d[i+1] as u32 }

// ================= 운전면허번호 (ko-pii driver_license.py) =================
static RE_DL_HYPHEN: Lazy<Regex> = Lazy::new(|| Regex::new(r"([0-9]{2})-([0-9]{2})-([0-9]{6})-([0-9]{2})").unwrap());
static RE_DL_PLAIN: Lazy<Regex> = Lazy::new(|| Regex::new(r"[0-9]{12}").unwrap());
const DL_KEYWORDS: &[&str] = &["운전면허", "면허번호", "면허증"];
fn dl_region_ok(r: &str) -> bool { r.parse::<u32>().map_or(false, |v| (11..=28).contains(&v)) }
fn det_driver_license(text: &str) -> Vec<Det> {
    let mut out = Vec::new();
    for m in RE_DL_HYPHEN.captures_iter(text) {
        let whole = m.get(0).unwrap();
        if digit_before(text, whole.start()) || digit_after(text, whole.end()) { continue; }
        if !dl_region_ok(&m[1]) { continue; }
        out.push(Det { start: whole.start(), end: whole.end(), risk: R_CRIT, conf: 0.85, label: "DRIVER_LICENSE" });
    }
    for m in RE_DL_PLAIN.find_iter(text) {
        if !num_isolated(text, m.start(), m.end()) { continue; }
        if !dl_region_ok(&m.as_str()[..2]) { continue; }
        if !kw_before(text, m.start(), 15, DL_KEYWORDS) { continue; }
        out.push(Det { start: m.start(), end: m.end(), risk: R_CRIT, conf: 0.85, label: "DRIVER_LICENSE" });
    }
    out
}

// ================= 건강보험증 번호 (ko-pii medical_insurance.py) =================
static RE_MI_NUM: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?:[0-9]-[0-9]{10})|(?:[0-9]{11})").unwrap());
static RE_MI_KW: Lazy<Regex> = Lazy::new(|| Regex::new(r"건강\s*보험|의료\s*보험|보험증").unwrap());
fn det_medical_insurance(text: &str) -> Vec<Det> {
    let mut out = Vec::new();
    for m in RE_MI_NUM.find_iter(text) {
        let bad_before = m.start() > 0 && {
            let b = text.as_bytes()[m.start()-1]; b.is_ascii_digit() || b == b'-'
        };
        let bad_after = text.as_bytes().get(m.end()).map_or(false, |&b| b.is_ascii_digit() || b == b'-');
        if bad_before || bad_after { continue; }
        if !RE_MI_KW.is_match(&left_window(text, m.start(), 25)) { continue; }
        out.push(Det { start: m.start(), end: m.end(), risk: R_HIGH, conf: 0.9, label: "MEDICAL_INSURANCE" });
    }
    out
}

// ================= 처방전 발행번호 (ko-pii prescription.py) =================
static RE_PRESC_ISSUE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[0-9]{12}").unwrap());
static RE_PRESC_LABELED: Lazy<Regex> = Lazy::new(|| Regex::new(r"[A-Za-z]{2,6}-[0-9][0-9A-Za-z-]{3,22}").unwrap());
static RE_PRESC_INST: Lazy<Regex> = Lazy::new(|| Regex::new(r"[0-9]{8}").unwrap());
const PRESC_KEYWORDS: &[&str] = &["처방번호","처방전번호","처방전 번호","처방전 발행번호","처방전교부번호","교부번호","Rx 번호","Rx번호","Rx"];
const PRESC_INST_KEYWORDS: &[&str] = &["의료기관기호","기관기호","요양기관기호","요양기관번호","병원코드"];
fn det_prescription(text: &str) -> Vec<Det> {
    let mut out: Vec<Det> = Vec::new();
    let mut seen: Vec<(usize, usize)> = vec![];
    // (1) 발행번호 12자리
    for m in RE_PRESC_ISSUE.find_iter(text) {
        if !num_isolated(text, m.start(), m.end()) { continue; }
        let d = digits(m.as_str());
        let y = d[0] as i64*1000 + d[1] as i64*100 + d[2] as i64*10 + d[3] as i64;
        if y < 1990 || y > 2099 || !valid_ymd(y, num2(&d,4), num2(&d,6)) { continue; }
        if !kw_before(text, m.start(), 20, PRESC_KEYWORDS) { continue; }
        seen.push((m.start(), m.end()));
        out.push(Det { start: m.start(), end: m.end(), risk: R_HIGH, conf: 0.9, label: "PRESCRIPTION_ID" });
    }
    // (2) 영문접두 ID
    for m in RE_PRESC_LABELED.find_iter(text) {
        let before_bad = m.start() > 0 && text.as_bytes()[m.start()-1].is_ascii_alphanumeric();
        let after_bad = text.as_bytes().get(m.end()).map_or(false, |b| b.is_ascii_alphanumeric());
        if before_bad || after_bad { continue; }
        if overlaps(&seen, m.start(), m.end(), 0) { continue; }
        if !kw_before(text, m.start(), 18, PRESC_KEYWORDS) { continue; }
        seen.push((m.start(), m.end()));
        out.push(Det { start: m.start(), end: m.end(), risk: R_HIGH, conf: 0.88, label: "PRESCRIPTION_ID" });
    }
    // (3) 의료기관기호 8자리
    for m in RE_PRESC_INST.find_iter(text) {
        if !num_isolated(text, m.start(), m.end()) { continue; }
        if overlaps(&seen, m.start(), m.end(), 0) { continue; }
        if !kw_before(text, m.start(), 15, PRESC_INST_KEYWORDS) { continue; }
        seen.push((m.start(), m.end()));
        out.push(Det { start: m.start(), end: m.end(), risk: R_MED, conf: 0.85, label: "PRESCRIPTION_ID" });
    }
    out
}

// ================= EDI 약품코드 (ko-pii edi_drug.py) =================
static RE_EDI_13: Lazy<Regex> = Lazy::new(|| Regex::new(r"[0-9]{13}").unwrap());
static RE_EDI_9: Lazy<Regex> = Lazy::new(|| Regex::new(r"[0-9]{9}").unwrap());
const EDI_KEYWORDS: &[&str] = &["EDI","edi","약품코드","의약품코드","주성분코드","KD코드","KD 코드","약가코드"];
fn det_edi_drug(text: &str) -> Vec<Det> {
    let mut out: Vec<Det> = Vec::new();
    let mut seen: Vec<(usize, usize)> = vec![];
    for m in RE_EDI_13.find_iter(text) {
        if !num_isolated(text, m.start(), m.end()) { continue; }
        if !kw_before(text, m.start(), 18, EDI_KEYWORDS) { continue; }
        let s = m.as_str();
        if !(s.starts_with("880") || s.starts_with("881") || s.starts_with("888")) { continue; }
        seen.push((m.start(), m.end()));
        out.push(Det { start: m.start(), end: m.end(), risk: R_LOW, conf: 0.9, label: "EDI_DRUG" });
    }
    for m in RE_EDI_9.find_iter(text) {
        if !num_isolated(text, m.start(), m.end()) { continue; }
        if overlaps(&seen, m.start(), m.end(), 0) { continue; }
        if !kw_before(text, m.start(), 18, EDI_KEYWORDS) { continue; }
        seen.push((m.start(), m.end()));
        out.push(Det { start: m.start(), end: m.end(), risk: R_LOW, conf: 0.85, label: "EDI_DRUG" });
    }
    out
}

// ================= 법원 사건번호 (ko-pii court_case.py) =================
const COURT_CODES: &[&str] = &[
    "가합","가단","가소","카합","카단","카기","고합","고단","고정","형보","고약","고전",
    "구합","구단","구약","드합","드단","수단","수합","후단","후합",
    "헌가","헌나","헌다","헌라","헌마","헌바",
    "나","다","차","머","노","도","초","누","두","르","므","호","자","보","사","허","라","마","바",
];
static RE_COURT: Lazy<Regex> = Lazy::new(|| {
    let mut codes: Vec<&str> = COURT_CODES.to_vec();
    codes.sort_by(|a, b| b.chars().count().cmp(&a.chars().count()));
    let alts = codes.iter().map(|c| regex::escape(c)).collect::<Vec<_>>().join("|");
    Regex::new(&format!(r"((?:19|20)[0-9]{{2}})({})([0-9]{{1,6}})", alts)).unwrap()
});
fn det_court_case(text: &str) -> Vec<Det> {
    let mut out = Vec::new();
    for m in RE_COURT.captures_iter(text) {
        let whole = m.get(0).unwrap();
        if !digit_hangul_boundary(text, whole.start(), whole.end()) { continue; }
        let serial: u64 = m[3].parse().unwrap_or(0);
        if serial == 0 { continue; }
        out.push(Det { start: whole.start(), end: whole.end(), risk: R_MED, conf: 0.9, label: "COURT_CASE" });
    }
    out
}
/// [s,e) 앞뒤 글자가 숫자도 한글도 아님. ko-pii (?<![0-9가-힣])/(?![0-9가-힣]).
fn digit_hangul_boundary(text: &str, s: usize, e: usize) -> bool {
    let before_ok = s == 0 || {
        let c = text[..s].chars().next_back().unwrap();
        !c.is_ascii_digit() && !is_hangul(c)
    };
    let after_ok = e >= text.len() || {
        let c = text[e..].chars().next().unwrap();
        !c.is_ascii_digit() && !is_hangul(c)
    };
    before_ok && after_ok
}

// ================= 필지고유번호 PNU (ko-pii pnu.py) =================
static RE_PNU: Lazy<Regex> = Lazy::new(|| Regex::new(r"[0-9]{19}").unwrap());
const PNU_SIDO: &[&str] = &["11","21","22","23","24","25","26","29","31","32","33","34","35","36","37","38","39","41","42","43","44","45","46","47","48","50"];
fn det_pnu(text: &str) -> Vec<Det> {
    let mut out = Vec::new();
    for m in RE_PNU.find_iter(text) {
        if !num_isolated(text, m.start(), m.end()) { continue; }
        let s = m.as_str();
        if !PNU_SIDO.contains(&&s[0..2]) { continue; }
        if &s[10..11] == "0" { continue; }
        if &s[11..15] == "0000" { continue; }
        out.push(Det { start: m.start(), end: m.end(), risk: R_LOW, conf: 0.9, label: "PNU" });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 편의 함수 — 한 라벨만 켜고 검출 건수를 센다.
    fn count(text: &str, label: &str) -> u32 {
        let want: HashSet<String> = [label.to_string()].into_iter().collect();
        *pii_counts(text, &want).get(label).unwrap_or(&0)
    }

    // 체크섬이 맞는 실제 번호는 잡아야 한다.
    #[test]
    fn 유효한_사업자등록번호는_검출한다() {
        // 국세청 규칙을 통과하는 값(하이픈 유무 모두).
        assert_eq!(count("사업자등록번호 220-81-62517 입니다.", "BUSINESS_REG"), 1);
        assert_eq!(count("2208162517", "BUSINESS_REG"), 1);
    }

    // 체크섬이 틀리면 걸러야 한다(3-2-5 모양만 맞는 숫자).
    #[test]
    fn 체크섬_틀리면_거른다() {
        assert_eq!(count("220-81-62518", "BUSINESS_REG"), 0);
    }

    // 회귀 핵심 — 모두 0 인 placeholder 는 체크섬을 통과하지만 실제 사업자가 아니다.
    // 표의 빈칸을 0 으로 채운 문서에서 무더기로 오검출되던 것을 막는다.
    #[test]
    fn 전부0인_placeholder는_거른다() {
        assert_eq!(count("0000000000", "BUSINESS_REG"), 0);
        assert_eq!(count("000-00-00000", "BUSINESS_REG"), 0);
        // 0 이 섞여 있어도 전부 0 이 아니면 체크섬 규칙대로 판단한다.
        assert_eq!(count("000-00-00001", "BUSINESS_REG"), 0);   // 체크섬 불일치
    }

    // ── 전화번호(ko-pii phone.py 7패턴 대응) ─────────────────────
    // 예전에는 정규식 하나뿐이라 아래 넷을 통째로 놓쳤다. 전화번호를 못 잡으면
    // 문서가 실제보다 낮은 등급을 받으므로 '덜 잡는' 쪽이 더 위험하다.
    #[test]
    fn 국제표기_전화번호를_잡는다() {
        assert_eq!(count("연락 +82-10-1234-5678", "PHONE"), 1);
        assert_eq!(count("연락 0082 10 9876 5432", "PHONE"), 1);
        assert_eq!(count("서울 +82-2-123-4567", "PHONE"), 1);
        assert_eq!(count("지역 +82-31-123-4567", "PHONE"), 1);
    }

    #[test]
    fn voip_070과_대표번호를_잡는다() {
        assert_eq!(count("070-1234-5678", "PHONE"), 1);
        assert_eq!(count("대표 1588-1234", "PHONE"), 1);
        assert_eq!(count("대표 1666-9999", "PHONE"), 1);
    }

    // 구분자가 여러 칸이어도 한 건으로 본다(파이썬 [-.\s]{0,3} 과 동일).
    #[test]
    fn 구분자_여러칸도_한건이다() {
        assert_eq!(count("010 - 1234 - 5678", "PHONE"), 1);
        assert_eq!(count("010.1234.5678", "PHONE"), 1);
    }

    // 국제표기가 안쪽 국내표기보다 먼저다 — 순서가 뒤집히면 "+82" 가 잘려 나간다.
    #[test]
    fn 국제표기가_국내표기보다_우선이다() {
        let want: HashSet<String> = ["PHONE".to_string()].into_iter().collect();
        let recs = pii_records("+82-10-1234-5678", &want);
        assert_eq!(recs.len(), 1);
        assert!(recs[0].1.starts_with("+82"), "값={:?}", recs[0].1);
    }

    // ── 법인등록번호 구분자(ko-pii corp_reg.py 와 동일 폭) ────────
    // 점·슬래시로 쓴 표기를 놓치면 그 문서가 낮은 등급을 받는다.
    #[test]
    fn 법인등록번호는_점_슬래시_구분자도_받는다() {
        assert_eq!(count("110111-0000002", "CORP_REG"), 1);
        assert_eq!(count("110111.0000002", "CORP_REG"), 1);
        assert_eq!(count("110111/0000002", "CORP_REG"), 1);
        assert_eq!(count("110111 0000002", "CORP_REG"), 1);
    }

    // ── 계좌번호 span 꼬리 ────────────────────────────────────────
    // 정규식 꼬리가 줄바꿈까지 삼키면 검출값이 오염되고, span 이 길어져
    // 겹침 해소에서 다른 라벨을 부당하게 밀어낸다.
    #[test]
    fn 계좌번호_span은_공백을_물지_않는다() {
        let want: HashSet<String> = ["ACCOUNT".to_string()].into_iter().collect();
        let recs = pii_records("계좌번호 110-234-567890

다음 줄", &want);
        assert_eq!(recs.len(), 1);
        assert_eq!(recs[0].1, "110-234-567890");
    }

    // placeholder 를 거르면 그 자리를 전화번호가 되찾는다(겹침 해소 확인).
    #[test]
    fn placeholder자리는_다른_라벨이_가져간다() {
        let want: HashSet<String> = ["BUSINESS_REG".to_string(), "PHONE".to_string()]
            .into_iter().collect();
        let got = pii_counts("연락처 010-0000-0000", &want);
        assert_eq!(*got.get("BUSINESS_REG").unwrap_or(&0), 0);
    }
}
