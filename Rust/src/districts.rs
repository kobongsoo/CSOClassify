//! 행정구역/국가 사전 조회 (ko-pii dictionaries/districts.py + legal_dongs.py 포팅).
//! 데이터 본체(districts_data.rs)는 Python 원본에서 자동 추출했다(전사오류 방지).
//! legal_dongs 는 정제 텍스트(resources/legal_dongs.txt)를 컴파일타임 임베딩해 단일 바이너리 유지.

use once_cell::sync::Lazy;
use std::collections::{HashMap, HashSet};

include!("districts_data.rs");

// ---- 집합/맵 구성 (최초 접근 시 1회) ----

static PROVINCES: Lazy<HashSet<&'static str>> = Lazy::new(|| PROVINCES_ARR.iter().copied().collect());
static PROVINCE_ABBREV: Lazy<HashSet<&'static str>> = Lazy::new(|| PROVINCE_ABBREV_ARR.iter().copied().collect());
static COUNTRIES: Lazy<HashSet<&'static str>> = Lazy::new(|| COUNTRIES_ARR.iter().copied().collect());
static EXTRA_CITY: Lazy<HashSet<&'static str>> = Lazy::new(|| EXTRA_CITY_ARR.iter().copied().collect());
static COMMON_DONGS: Lazy<HashSet<&'static str>> = Lazy::new(|| COMMON_DONGS_ARR.iter().copied().collect());
static ALL_DISTRICTS: Lazy<HashSet<&'static str>> = Lazy::new(|| ALL_DISTRICTS_ARR.iter().copied().collect());
static PROVINCE_DISTRICTS: Lazy<HashMap<&'static str, HashSet<&'static str>>> = Lazy::new(|| {
    PROVINCE_DISTRICTS_ARR
        .iter()
        .map(|(k, v)| (*k, v.iter().copied().collect::<HashSet<&'static str>>()))
        .collect()
});

// 법정동 가제티어 — 컴파일타임 임베딩(정제본, 약 1만개). 최초 조회 시 HashSet 구성.
static LEGAL_DONGS_TXT: &str = include_str!("../resources/legal_dongs.txt");
static LEGAL_DONGS: Lazy<HashSet<&'static str>> =
    Lazy::new(|| LEGAL_DONGS_TXT.lines().map(|l| l.trim()).filter(|l| !l.is_empty()).collect());

// ---- 조회 함수 (ko-pii districts.py 동일 시맨틱) ----

/// 국가명 토큰인지.
pub fn is_country(tok: &str) -> bool { COUNTRIES.contains(tok) }

/// 빈출 동 사전 매칭.
pub fn is_common_dong(tok: &str) -> bool { COMMON_DONGS.contains(tok) }

/// 광역 외 빈출 시 약칭.
pub fn is_extra_city(tok: &str) -> bool { EXTRA_CITY.contains(tok) }

/// 광역지자체(정식명 또는 약칭)인지.
pub fn is_province(tok: &str) -> bool { PROVINCES.contains(tok) || PROVINCE_ABBREV.contains(tok) }

/// 자치구·군·시(기초자치단체)인지.
pub fn is_district(tok: &str) -> bool { ALL_DISTRICTS.contains(tok) }

/// 법정동(동/읍/면/리) 가제티어 멤버십.
pub fn is_legal_dong(tok: &str) -> bool { LEGAL_DONGS.contains(tok) }

/// (광역, 기초) 조합이 실제 한국 행정구역인지 검증. 예: (경기도,강남구)=false.
pub fn is_valid_province_district(province: &str, district: &str) -> bool {
    if province.is_empty() || district.is_empty() { return false; }
    match PROVINCE_DISTRICTS.get(province) {
        Some(set) => set.contains(district),
        None => false,
    }
}
