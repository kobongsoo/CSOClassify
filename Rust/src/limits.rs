//! 문서 크기 상한(Size Gate) — 설계: plan/문서크기-상한-설계-20260904.html
//!
//! 파이썬 판 `config.py` 의 크기 상한 상수·환경변수와 **1:1로 대응**한다. 두 구현이
//! 같은 문서에서 같은 판정을 내야 하므로(설계 원칙 4), 기본값과 환경변수 이름을
//! 여기서 그대로 맞춘다. 값을 한쪽만 고치면 같은 배치를 두 구현으로 돌렸을 때
//! 결과가 갈린다.
//!
//! 적용 게이트:
//!   G1 `MAX_FILE_BYTES`      — 추출 전 원본 바이트(안전핀)
//!   G2 `MAX_FILE_BYTES_TEXT` — 텍스트 계열 전용(바이트=글자수라 더 낮게)
//!   G3 `MAX_TEXT_CHARS`      — 추출 후 글자수(본체). 자르고 표식을 단다
//!   G5 `PARSER_TIMEOUT` / `MAX_PDF_PAGES` — 파서 루프의 시간·분량 상한
//!
//!   G4 `MAX_ARCHIVE_BYTES` / `MAX_ARCHIVE_MEMBERS` — 압축 해제 누적 상한
//!
//! (2026-09-04 압축 확장을 이 판에도 포팅하면서 G4 가 실제로 동작하게 됐다.
//!  7z·rar 만은 여전히 펼치지 않는다 — `archive.rs` 머리말 참고.)

use std::path::Path;
use std::time::Instant;

use crate::detect::Fmt;

//------------------------------------------------------------------
// 환경변수 → 정수
//=> 값이 없거나 숫자가 아니면 기본값을 쓴다. 파이썬 판이
//   `int(os.environ.get(...), 기본값)` 로 읽는 것과 같은 동작이다.
//
// -in: name    = 환경변수 이름
// -in: default = 미설정/해석불가일 때 쓸 값
//
// -out: u64 = 해석된 값
// -out: error = 없음(항상 값을 돌려준다)
//------------------------------------------------------------------
fn env_u64(name: &str, default: u64) -> u64 {
    std::env::var(name).ok().and_then(|v| v.trim().parse().ok()).unwrap_or(default)
}

//------------------------------------------------------------------
// CLI 로 덮어쓴 상한값 모음
//=> 우선순위를 파이썬 판과 같게 'CLI 플래그 > 환경변수 > 코드 기본값' 으로 맞추기
//   위한 자리다. 아래 getter 들이 이 값을 먼저 보고, 없을 때만 환경변수를 읽는다.
//
//   [왜 전역인가] 상한을 실제로 보는 곳은 추출기·PDF 파서처럼 CLI 인자를 볼 수
//   없는 깊은 자리다. 거기까지 값을 실어 나르면 함수 시그니처 대여섯 개가 상한
//   하나 때문에 바뀐다. 파이썬 판이 config 모듈 값을 덮어쓰는 것과 같은 방식이다.
//   [왜 OnceLock 인가] 시작할 때 한 번만 정해지고 그 뒤로는 읽기만 하는 값이다.
//   Mutex 는 매 페이지마다 잠금을 걸어야 하고, 가변 static 은 unsafe 다.
//
// -필드: 각 상한의 덮어쓴 값. None 이면 '지정 안 함'(환경변수/기본값을 쓴다)
//------------------------------------------------------------------
#[derive(Default, Clone, Copy)]
pub struct Overrides {
    pub max_file_bytes: Option<u64>,
    pub max_file_bytes_text: Option<u64>,
    pub max_text_chars: Option<u64>,
    pub parser_timeout: Option<u64>,
    pub max_pdf_pages: Option<u64>,
    pub max_archive_bytes: Option<u64>,
    pub max_archive_members: Option<u64>,
}

static OVERRIDES: std::sync::OnceLock<Overrides> = std::sync::OnceLock::new();

//------------------------------------------------------------------
// CLI 덮어쓰기 등록 (시작 시 한 번)
//=> parse_args 직후에 부른다. 두 번째 호출은 조용히 무시된다 — 상한이 실행 도중
//   바뀌면 같은 배치 안에서 앞뒤 문서의 판정 기준이 달라지기 때문이다.
//
// -in: o = 덮어쓸 값 모음(지정 안 한 항목은 None)
//
// -out: 없음
// -out: error = 없음(이미 정해져 있으면 무시)
//------------------------------------------------------------------
pub fn set_overrides(o: Overrides) {
    let _ = OVERRIDES.set(o);
}

//------------------------------------------------------------------
// 덮어쓴 값 꺼내기(내부 헬퍼)
//=> 등록된 것이 없거나 그 항목이 None 이면 None 을 준다.
//
// -in: pick = Overrides 에서 볼 항목을 고르는 클로저
//
// -out: Option<u64> = 덮어쓴 값
// -out: error = 없음
//------------------------------------------------------------------
fn overridden(pick: impl Fn(&Overrides) -> Option<u64>) -> Option<u64> {
    OVERRIDES.get().and_then(pick)
}

// 기본값 상수 — 파이썬 판 config.py 와 같은 값이어야 한다(설계 원칙 4).
// getter 와 테스트가 '같은 하나'를 보게 상수로 뽑아 둔다. 숫자를 양쪽에 적어 두면
// 한쪽만 고쳐도 테스트가 통과해 버려, 정합을 지키라는 테스트가 무력해진다.
pub const DEFAULT_MAX_FILE_BYTES: u64 = 100 * 1024 * 1024;
pub const DEFAULT_MAX_FILE_BYTES_TEXT: u64 = 20 * 1024 * 1024;
pub const DEFAULT_MAX_TEXT_CHARS: u64 = 2_000_000;
pub const DEFAULT_PARSER_TIMEOUT: u64 = 60;
pub const DEFAULT_MAX_PDF_PAGES: u64 = 3000;
pub const DEFAULT_MAX_ARCHIVE_BYTES: u64 = 500 * 1024 * 1024;
pub const DEFAULT_MAX_ARCHIVE_MEMBERS: u64 = 5000;

/// G1 — 추출 전 원본 파일 바이트 상한(기본 100MB).
/// [값의 근거] D:\분류함 2,254파일 실측에서 100MB 초과는 2건(둘 다 학습 코퍼스 tsv)뿐.
/// 50MB 로 낮추면 정상 업무문서인 55.3MB 회사소개서가 걸리므로 낮추면 안 된다.
pub fn max_file_bytes() -> u64 {
    overridden(|o| o.max_file_bytes)
        .unwrap_or_else(|| env_u64("CSOCLASSIFY_MAX_FILE_BYTES", DEFAULT_MAX_FILE_BYTES))
}

/// G2 — 텍스트 계열(txt/csv/tsv/json/html) 전용 상한(기본 20MB).
/// 이 포맷군만은 '바이트 수 = 글자 수'라 바이트가 비용을 정확히 대변한다.
pub fn max_file_bytes_text() -> u64 {
    overridden(|o| o.max_file_bytes_text)
        .unwrap_or_else(|| env_u64("CSOCLASSIFY_MAX_FILE_BYTES_TEXT", DEFAULT_MAX_FILE_BYTES_TEXT))
}

/// G3 — 추출 후 글자수 상한(기본 200만 자). 다섯 게이트 중 본체.
/// 실측 854자/페이지 기준 약 2,340페이지로, 최악 케이스(581p)의 4배라 정상 문서를
/// 자를 위험이 없다.
pub fn max_text_chars() -> usize {
    overridden(|o| o.max_text_chars)
        .unwrap_or_else(|| env_u64("CSOCLASSIFY_MAX_TEXT_CHARS", DEFAULT_MAX_TEXT_CHARS)) as usize
}

/// G4 — 압축 1건당 해제 누적 바이트 상한(기본 500MB). 0 이면 무제한.
/// [왜 압축파일 크기가 아닌가] 7KB zip 이 1.4MB 로, 악성 zip 은 수만 배로 부푼다.
/// 압축된 크기로는 위험을 알 수 없어, '풀었을 때 누적'을 센다.
pub fn max_archive_bytes() -> u64 {
    overridden(|o| o.max_archive_bytes)
        .unwrap_or_else(|| env_u64("CSOCLASSIFY_MAX_ARCHIVE_BYTES", DEFAULT_MAX_ARCHIVE_BYTES))
}

/// G4 — 압축 1건당 내부 파일 개수 상한(기본 5000). 0 이면 무제한.
/// 0바이트 파일 수백만 개짜리 폭탄은 용량으로는 안 걸려 개수를 따로 센다.
pub fn max_archive_members() -> u64 {
    overridden(|o| o.max_archive_members)
        .unwrap_or_else(|| env_u64("CSOCLASSIFY_MAX_ARCHIVE_MEMBERS", DEFAULT_MAX_ARCHIVE_MEMBERS))
}

/// G5 — 파서 루프 시간 상한(초, 기본 60). 0 이면 무제한.
pub fn parser_timeout_secs() -> u64 {
    overridden(|o| o.parser_timeout)
        .unwrap_or_else(|| env_u64("CSOCLASSIFY_PARSER_TIMEOUT", DEFAULT_PARSER_TIMEOUT))
}

/// G5 — PDF 페이지 수 상한(기본 3000). 0 이면 무제한.
pub fn max_pdf_pages() -> usize {
    overridden(|o| o.max_pdf_pages)
        .unwrap_or_else(|| env_u64("CSOCLASSIFY_MAX_PDF_PAGES", DEFAULT_MAX_PDF_PAGES)) as usize
}

//------------------------------------------------------------------
// 원본 파일 크기 상한 검사 (G1·G2)
//=> 파서를 부르기 '전에' 원본 바이트를 보고, 상한을 넘으면 아예 읽지 않는다.
//   파서를 부른 뒤에 막으면 이미 메모리를 다 쓴 뒤라 의미가 없다.
//    1) 크기를 잰다(못 재면 통과 — 상한과 무관한 이유로 정상 문서를 잃으면 안 된다)
//    2) 텍스트 계열이면 낮은 상한(G2), 아니면 일반 상한(G1)을 고른다
//    3) 넘으면 사람이 읽을 사유 문자열을 돌려준다
//
//   [왜 텍스트 계열만 다른가] txt/csv/tsv/json/html 은 바이트가 곧 글자수다.
//   반대로 pdf·pptx 는 이미지가 대부분이라 바이트와 본문 길이가 따로 논다
//   (실측: 55.3MB PDF 의 본문이 343자). 한 값으로 묶으면 둘 중 하나는 틀린다.
//
// -in: path = 검사할 원본 경로
// -in: fmt  = 감지된 포맷(텍스트 계열 판정에 쓴다)
//
// -out: Option<String> = 상한 초과 시 사유 문자열, 통과면 None
// -out: error = 없음(크기를 못 재면 None = 통과)
//------------------------------------------------------------------
pub fn check_size_limit(path: &Path, fmt: Fmt) -> Option<String> {
    // 크기를 못 재는 상황(권한·경쟁 삭제 등)에서 막아 버리면 상한과 무관한 이유로
    // 정상 문서를 잃는다. 그 판단은 뒤의 파서에게 맡긴다.
    let size = std::fs::metadata(path).ok()?.len();

    let (limit, which) = match fmt {
        Fmt::Text | Fmt::Html => (max_file_bytes_text(), "MAX_FILE_BYTES_TEXT"),
        _ => (max_file_bytes(), "MAX_FILE_BYTES"),
    };

    // 0 은 '상한 없음'으로 읽는다(파이썬 판 --no-size-limit 과 같은 규칙).
    if limit == 0 || size <= limit {
        return None;
    }
    Some(format!(
        "크기 상한 초과: {} > {} ({})",
        human_bytes(size),
        human_bytes(limit),
        which
    ))
}

//------------------------------------------------------------------
// 바이트 수 → 사람이 읽는 크기 문자열
//=> 상한 초과 사유 문구에 쓴다. MB 로만 찍으면 작은 값이 전부 "0MB" 로 뭉개져
//   "0MB > 0MB 초과" 같은 아무 정보 없는 문장이 된다(파이썬 판에서 실제로 나왔다).
//   사유는 운영자가 상한을 조정할 유일한 근거라 뭉개지면 안 된다.
//
// -in: n = 바이트 수
//
// -out: String = "328.8MB" · "512.0KB" · "900B" 처럼 읽히는 문자열
// -out: error = 없음
//------------------------------------------------------------------
pub fn human_bytes(n: u64) -> String {
    let f = n as f64;
    if n >= 1_048_576 {
        format!("{:.1}MB", f / 1_048_576.0)
    } else if n >= 1024 {
        format!("{:.1}KB", f / 1024.0)
    } else {
        format!("{}B", n)
    }
}

//------------------------------------------------------------------
// 글자수 상한 적용 (G3)
//=> 본문이 상한보다 길면 "앞부분만" 남긴다. 문서를 버리는 게 아니라 앞부분만 보고
//   판단하게 하는 것이며, 부른 쪽은 결과에 '일부만 봤다'는 표식을 달아야 한다.
//
//   [왜 앞부분인가] 등급을 정하는 신호 — 표지의 문서 종류, 머리말의 '대외비',
//   결재 스탬프, 업무분류 어휘 — 가 문서 앞쪽에 몰려 있다. 뒤를 버리는 쪽이 판정
//   손실이 가장 작다.
//   [왜 글자 단위인가] 파이썬 판은 `text[:limit]` 로 '글자' 수를 센다. 여기서
//   바이트로 자르면 한글 한 글자가 3바이트라 두 구현의 절단 위치가 달라지고,
//   심하면 문자 중간을 잘라 패닉이 난다. 그래서 char 경계로만 자른다.
//
// -in: text  = 추출·정규화된 본문
// -in: limit = 남길 최대 글자수(0 이면 제한 없음)
//
// -out: (String, usize, bool) = 잘린(또는 그대로인) 본문, 자르기 전 글자수, 잘렸는지
// -out: error = 없음
//------------------------------------------------------------------
pub fn truncate_text(text: String, limit: usize) -> (String, usize, bool) {
    if limit == 0 {
        // 상한 해제 — 글자수를 셀 이유도 없다(대용량에서 이 계산 자체가 비싸다).
        return (text, 0, false);
    }
    // char 경계 오프셋을 찾는다. limit 번째 char 가 없으면 = 상한 이하 = 그대로 통과.
    let cut = match text.char_indices().nth(limit) {
        None => return (text, 0, false),
        Some((byte_idx, _)) => byte_idx,
    };
    // 여기까지 왔으면 실제로 잘린다 — 이때만 전체 글자수를 센다(한 번만 훑는다).
    let n_orig = text.chars().count();
    let mut t = text;
    t.truncate(cut);
    (t, n_orig, true)
}

//------------------------------------------------------------------
// 파서 시간 예산 (G5)
//=> "이 파일에 쓸 수 있는 시간"을 들고 다니며, 페이지·시트 루프가 한 바퀴 돌 때마다
//   expired() 로 물어보게 한다. 넘기면 루프를 멈추고 그때까지 모은 텍스트만 쓴다 —
//   실패로 처리하지 않는다.
//
//   [왜 실패가 아닌가] 3,000페이지 중 800페이지를 읽었다면 그 800페이지로도 등급
//   판정은 대개 정확하다. 실패로 버리면 그 문서는 '미분류'가 되어 사람이 손봐야 할
//   목록에 쌓인다. 부분 결과가 미분류보다 낫다.
//
// -필드: started = 생성 시각
// -필드: secs    = 허용 시간(초). 0 이면 무제한
//------------------------------------------------------------------
pub struct ParserDeadline {
    started: Instant,
    secs: u64,
}

impl ParserDeadline {
    //------------------------------------------------------------------
    // 생성자 — 시계를 지금 시작한다
    //=> 파서가 실제 일을 시작하기 직전에 만들어야 측정이 정확하다.
    //
    // -in: 없음(허용 시간은 PARSER_TIMEOUT 환경변수/기본값에서 읽는다)
    //
    // -out: ParserDeadline
    // -out: error = 없음
    //------------------------------------------------------------------
    pub fn new() -> Self {
        ParserDeadline { started: Instant::now(), secs: parser_timeout_secs() }
    }

    //------------------------------------------------------------------
    // 예산을 다 썼나
    //=> 루프 안에서 매 반복 호출한다. 무제한(0) 설정이면 항상 false.
    //
    // -in: 없음
    //
    // -out: bool = true 면 시간 초과(호출부가 루프를 멈춰야 한다)
    // -out: error = 없음
    //------------------------------------------------------------------
    pub fn expired(&self) -> bool {
        if self.secs == 0 {
            return false;
        }
        self.started.elapsed().as_secs() >= self.secs
    }
}

impl Default for ParserDeadline {
    fn default() -> Self {
        Self::new()
    }
}

//------------------------------------------------------------------
// 추출 부가정보 전달 통로 (G5 관측용) — 파이썬 판 extract/notes.py 와 같은 역할
//=> "이 문서는 끝까지 못 읽고 상한에서 멈췄다"를 파서에서 결과 레코드까지 전달한다.
//
//   [왜 이런 게 필요한가] 파서는 `Option<String>` 하나만 돌려주도록 약속돼 있다.
//   상한에서 멈췄다는 사실은 '본문'이 아니라 '본문에 대한 사실'이라 그 문자열에
//   담을 수 없고, 반환형을 바꾸면 추출기와 그 호출부가 전부 바뀐다.
//   [왜 스레드 로컬인가] 전역 한 칸에 적으면 A 문서의 표식이 B 문서 결과에 붙는다.
//   그런 오염은 조용히 일어나 추적이 거의 불가능하다.
//------------------------------------------------------------------

use std::cell::RefCell;

/// 부분 추출 표식 — (사유, 단위, 읽은 수, 전체 수).
pub type PartialNote = (String, &'static str, usize, usize);

thread_local! {
    static PARTIAL: RefCell<Option<PartialNote>> = const { RefCell::new(None) };
}

//------------------------------------------------------------------
// 표식 비우기
//=> 추출을 시작하기 전에 부른다. 지난 문서가 남긴 표식이 섞이지 않게 한다.
//
// -in: 없음
// -out: 없음
// -out: error = 없음
//------------------------------------------------------------------
pub fn notes_reset() {
    PARTIAL.with(|c| *c.borrow_mut() = None);
}

//------------------------------------------------------------------
// '상한에서 멈췄다' 적어 두기
//=> 페이지·시트 루프가 상한에 걸려 중단됐을 때 파서가 부른다.
//
// -in: reason = 왜 멈췄나 · unit = 단위 이름 · read = 읽은 수 · total = 전체 수
// -out: 없음
// -out: error = 없음
//------------------------------------------------------------------
pub fn notes_set_partial(reason: String, unit: &'static str, read: usize, total: usize) {
    PARTIAL.with(|c| *c.borrow_mut() = Some((reason, unit, read, total)));
}

//------------------------------------------------------------------
// 표식 가져가며 비우기
//=> 추출 직후 호출부가 한 번 가져간다. 가져가면 비워지므로 같은 표식을 두 문서가
//   나눠 갖는 일이 없다.
//
// -in: 없음
// -out: Option<PartialNote> = 표식 또는 없음
// -out: error = 없음
//------------------------------------------------------------------
pub fn notes_take() -> Option<PartialNote> {
    PARTIAL.with(|c| c.borrow_mut().take())
}

#[cfg(test)]
mod tests {
    use super::*;

    //------------------------------------------------------------------
    // 상한 미만은 손대지 않는다 (무해성 — 가장 중요)
    //=> 정상 문서의 본문이 한 글자라도 바뀌면 판정 전체가 흔들린다.
    //------------------------------------------------------------------
    #[test]
    fn truncate_under_limit_untouched() {
        let (t, n, cut) = truncate_text("가나다라마".to_string(), 100);
        assert_eq!(t, "가나다라마");
        assert!(!cut);
        assert_eq!(n, 0); // 안 잘렸으면 굳이 세지 않는다
    }

    //------------------------------------------------------------------
    // 경계값 — 정확히 상한이면 자르지 않는다
    //------------------------------------------------------------------
    #[test]
    fn truncate_at_boundary_untouched() {
        let (t, _, cut) = truncate_text("가나다".to_string(), 3);
        assert_eq!(t, "가나다");
        assert!(!cut);
    }

    //------------------------------------------------------------------
    // 초과분은 앞부분만 남기고, 한글도 글자 단위로 잘린다
    //=> 바이트로 자르면 3바이트 한글이 깨지거나 파이썬 판과 절단 위치가 달라진다.
    //------------------------------------------------------------------
    #[test]
    fn truncate_over_limit_by_chars() {
        let (t, n, cut) = truncate_text("머리말본문본문꼬리말".to_string(), 3);
        assert!(cut);
        assert_eq!(t, "머리말");          // 바이트가 아니라 '글자' 3개
        assert_eq!(t.chars().count(), 3);
        assert_eq!(n, 10);
    }

    //------------------------------------------------------------------
    // 상한 0 은 '제한 없음'
    //------------------------------------------------------------------
    #[test]
    fn truncate_disabled() {
        let (t, _, cut) = truncate_text("가나다".to_string(), 0);
        assert_eq!(t, "가나다");
        assert!(!cut);
    }

    //------------------------------------------------------------------
    // 기본값이 파이썬 판과 같은지 — 두 구현의 결과가 갈리지 않게 고정한다
    //=> 한쪽만 고치면 같은 배치에서 다른 판정이 나온다(설계 원칙 4).
    //------------------------------------------------------------------
    #[test]
    fn defaults_match_python() {
        // 상수를 직접 본다 — getter 를 부르면 환경변수나 다른 테스트가 등록한
        // CLI 덮어쓰기(OVERRIDES 는 프로세스에 하나뿐)에 값이 흔들려, 정합을
        // 지키라는 이 테스트가 실행 순서에 따라 통과/실패하는 신뢰 못 할
        // 테스트가 된다(실제로 그렇게 깨졌다).
        assert_eq!(DEFAULT_MAX_FILE_BYTES, 100 * 1024 * 1024);
        assert_eq!(DEFAULT_MAX_FILE_BYTES_TEXT, 20 * 1024 * 1024);
        assert_eq!(DEFAULT_MAX_TEXT_CHARS, 2_000_000);
        assert_eq!(DEFAULT_MAX_PDF_PAGES, 3000);
        assert_eq!(DEFAULT_PARSER_TIMEOUT, 60);
    }

    //------------------------------------------------------------------
    // 바이트 크기 문구 — 작은 값이 "0MB" 로 뭉개지지 않는다
    //=> 사유 문구는 운영자가 상한을 조정할 유일한 근거라 뭉개지면 안 된다.
    //   파이썬 판에서 실제로 "0MB > 0MB 초과" 가 나왔던 자리다.
    //------------------------------------------------------------------
    #[test]
    fn human_bytes_readable_at_small_sizes() {
        assert_eq!(human_bytes(900), "900B");
        assert_eq!(human_bytes(10 * 1024), "10.0KB");
        assert!(human_bytes(328 * 1_048_576).ends_with("MB"));
        assert!(!human_bytes(1000).contains("0MB"));
    }

    //------------------------------------------------------------------
    // CLI 덮어쓰기가 환경변수·기본값을 이긴다
    //=> 우선순위(CLI > 환경변수 > 기본값)가 파이썬 판과 같아야 한다. OnceLock 은
    //   프로세스에 한 번만 설정되므로, 이 테스트가 등록에 성공하든 다른 테스트가
    //   먼저 등록했든 '읽은 값이 등록된 값과 같다'만 확인한다.
    //------------------------------------------------------------------
    #[test]
    fn cli_overrides_win() {
        set_overrides(Overrides {
            max_file_bytes: Some(7 * 1_048_576),
            max_pdf_pages: Some(123),
            ..Default::default()
        });
        // 등록된 값이 있으면 그 값이 나와야 한다.
        let ov = OVERRIDES.get().expect("등록됨");
        if let Some(n) = ov.max_file_bytes {
            assert_eq!(max_file_bytes(), n);
        }
        if let Some(n) = ov.max_pdf_pages {
            assert_eq!(max_pdf_pages(), n as usize);
        }
        // 지정하지 않은 항목은 환경변수/기본값으로 내려간다.
        if ov.max_text_chars.is_none() && std::env::var("CSOCLASSIFY_MAX_TEXT_CHARS").is_err() {
            assert_eq!(max_text_chars(), 2_000_000);
        }
    }

    //------------------------------------------------------------------
    // 시간 예산 — 0 은 무제한, 넉넉하면 아직 안 지났다
    //------------------------------------------------------------------
    #[test]
    fn deadline_states() {
        let d = ParserDeadline { started: Instant::now(), secs: 0 };
        assert!(!d.expired());
        let d = ParserDeadline { started: Instant::now(), secs: 60 };
        assert!(!d.expired());
    }
}
