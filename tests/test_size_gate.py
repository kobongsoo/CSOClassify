#------------------------------------------------------------------
# 문서 크기 상한(Size Gate) 단위 테스트 — 설계: plan/문서크기-상한-설계-20260904.html
#=> 다섯 게이트 전부를 검증한다 — G1·G2(원본 바이트) · G3(정제 글자수) ·
#   G4(압축 해제 총량) · G5(파서 시간 예산).
#   이 테스트의 핵심은 "무엇을 잘랐는가"가 아니라 "정상 문서를 건드리지 않는가"다
#   (설계 T5·T9). 상한이 정상 문서의 출력을 바꾸면 그게 가장 나쁜 회귀다.
#------------------------------------------------------------------

import pytest

from csoclassify import config
from csoclassify.clean import truncate_text
from csoclassify.cli import _mark_truncated, _resolve_text_limit


#------------------------------------------------------------------
# 더미 인자 객체
#=> argparse.Namespace 대신 필요한 두 속성만 갖는 최소 객체를 쓴다. 파서 전체를
#   끌어오지 않아야 테스트가 CLI 변경에 덜 흔들린다.
#
# -필드: max_text_chars = --max-text-chars 값(None 이면 미지정)
# -필드: no_size_limit  = --no-size-limit 여부
#------------------------------------------------------------------
class _Args:
    def __init__(self, max_text_chars=None, no_size_limit=False):
        self.max_text_chars = max_text_chars
        self.no_size_limit = no_size_limit


#------------------------------------------------------------------
# 더미 인자 객체 (바이트 상한용)
#=> --max-file-mb 만 보는 게이트를 시험할 때 쓴다.
#
# -필드: max_file_mb   = --max-file-mb 값(MB)
# -필드: no_size_limit = --no-size-limit 여부
#------------------------------------------------------------------
class _Args2:
    def __init__(self, max_file_mb=None, no_size_limit=False):
        self.max_file_mb = max_file_mb
        self.no_size_limit = no_size_limit


#------------------------------------------------------------------
# T5 — 상한 미만은 손대지 않는다 (가장 중요한 무해성 검증)
#=> 상한보다 짧은 본문은 문자열이 그대로여야 하고 truncated 가 False 여야 한다.
#   여기가 깨지면 정상 문서 전부의 판정이 흔들린다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_under_limit_untouched():
    text = "가나다라마" * 100          # 500자
    out, n_orig, truncated = truncate_text(text, 2000)
    assert out == text
    assert n_orig == 500
    assert truncated is False


#------------------------------------------------------------------
# 경계값 — 정확히 상한이면 자르지 않는다
#=> '초과'일 때만 자른다. 경계에서 한 글자라도 더 자르면 상한값의 의미가 흐려진다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_exactly_at_limit_untouched():
    text = "가" * 1000
    out, n_orig, truncated = truncate_text(text, 1000)
    assert out == text
    assert n_orig == 1000
    assert truncated is False


#------------------------------------------------------------------
# T4 — 상한 초과는 앞부분만 남긴다
#=> 남은 길이가 정확히 상한이고, 원래 길이를 따로 알려 줘야 한다(얼마나 잘렸는지
#   모르면 재검토 우선순위를 정할 수 없다). 남은 내용은 '앞부분'이어야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_over_limit_truncates_head():
    text = "머리말" + ("본문" * 5000) + "꼬리말"
    out, n_orig, truncated = truncate_text(text, 100)
    assert truncated is True
    assert len(out) == 100
    assert n_orig == len(text)
    # 앞부분을 남기는 설계(D2) — 등급 신호가 문서 앞쪽에 몰려 있기 때문이다.
    assert out.startswith("머리말")
    assert "꼬리말" not in out


#------------------------------------------------------------------
# 상한 해제 — None/0/음수는 '제한 없음'으로 읽는다
#=> --no-size-limit 과 --max-text-chars 0 이 같은 뜻이 되도록 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_limit_disabled_variants():
    text = "가" * 10000
    for limit in (None, 0, -1):
        out, n_orig, truncated = truncate_text(text, limit)
        assert out == text
        assert n_orig == 10000
        assert truncated is False


#------------------------------------------------------------------
# 빈 입력 방어
#=> None/빈 문자열이 들어와도 죽지 않고 ("", 0, False) 여야 한다. 추출이 실패한
#   문서가 이 경로로 흘러들 수 있다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_empty_input():
    assert truncate_text(None, 100) == ("", 0, False)
    assert truncate_text("", 100) == ("", 0, False)


#------------------------------------------------------------------
# 상한 결정 우선순위 — CLI > 환경변수(config) > 기본값
#=> 다른 옵션들과 같은 우선순위를 지키는지 본다. --no-size-limit 이 가장 세다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_resolve_text_limit_priority():
    # 아무것도 안 주면 config 기본값(환경변수가 이미 반영된 값).
    assert _resolve_text_limit(_Args()) == config.MAX_TEXT_CHARS
    # CLI 로 준 값이 config 를 이긴다.
    assert _resolve_text_limit(_Args(max_text_chars=5000)) == 5000
    # 0 이하는 '해제'.
    assert _resolve_text_limit(_Args(max_text_chars=0)) is None
    # --no-size-limit 은 --max-text-chars 보다 세다.
    assert _resolve_text_limit(_Args(max_text_chars=5000, no_size_limit=True)) is None


#------------------------------------------------------------------
# T6 대비 — 기본 상한은 데몬 IPC 상한(16MB) 안에 들어간다
#=> 한글 UTF-8 3바이트가 최악이므로 그 가정으로 계산해도 16MB 미만이어야 한다.
#   이 관계가 깨지면 예전의 '조용한 임베딩 실패'(R4)가 되살아난다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_default_limit_fits_ipc_budget():
    from csoclassify.daemon import ipc
    worst_case_bytes = config.MAX_TEXT_CHARS * 3      # 한글 UTF-8 최악 가정
    assert worst_case_bytes < ipc.MAX_MSG


#------------------------------------------------------------------
# 레코드 표식 — 세 칸을 모두 남긴다
#=> '잘렸다'만 있으면 얼마나 잘렸는지 몰라 재검토 우선순위를 못 정한다.
#   그리고 분류 결과(grade 등)는 절대 건드리지 않아야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_mark_truncated_fields():
    rec = {"file": "a.pdf", "grade": "S", "why": {"security": {"method": "fusion"}}}
    _mark_truncated(rec, 8431022, 2000000)
    assert rec["text_truncated"] is True
    assert rec["text_chars"] == 2000000
    assert rec["text_chars_original"] == 8431022
    # 판정은 그대로 — 앞부분만으로도 규칙에 걸렸다면 그 등급이 맞다.
    assert rec["grade"] == "S"
    assert rec["why"]["security"]["method"] == "fusion"


#------------------------------------------------------------------
# 기본값 자체의 타당성 — 설계 근거와 어긋나지 않는지 고정
#=> 실측 854자/페이지 기준으로 최악 케이스(581페이지)를 넉넉히 덮어야 한다.
#   누군가 기본값을 무심코 낮추면 여기서 걸린다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_default_limit_covers_worst_case_document():
    worst_case_chars = 581 * 854          # 발표자료 최악 케이스 실측 환산
    assert config.MAX_TEXT_CHARS > worst_case_chars * 3


#------------------------------------------------------------------
# T1 — G1: 원본 바이트 상한을 넘으면 파서를 부르지 않는다
#=> 상한 초과는 SizeLimitError 여야 하고, 이 예외는 ExtractError 를 물려받아
#   기존 처리 경로(보류 레코드 생성)가 그대로 동작해야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_g1_blocks_oversized_file(tmp_path):
    from csoclassify.extract.base import ExtractError, SizeLimitError, check_size_limit

    p = tmp_path / "big.bin"
    p.write_bytes(b"\x00" * 5000)

    with pytest.raises(SizeLimitError) as ei:
        check_size_limit(str(p), ftype="binary", max_bytes=1000)
    # 사람이 바로 판단할 수 있게 실제 크기와 어느 상한인지가 메시지에 있어야 한다.
    assert "MAX_FILE_BYTES" in str(ei.value)
    # 기존 코드가 ExtractError 로 잡고 있으므로 그 관계가 깨지면 안 된다.
    assert isinstance(ei.value, ExtractError)


#------------------------------------------------------------------
# T2 — G1 경계: 정확히 상한이면 통과한다
#=> '초과'일 때만 막는다. 경계에서 막으면 상한값의 뜻이 흐려진다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_g1_boundary_passes(tmp_path):
    from csoclassify.extract.base import check_size_limit

    p = tmp_path / "exact.bin"
    p.write_bytes(b"\x00" * 1000)
    check_size_limit(str(p), ftype="binary", max_bytes=1000)   # 예외가 없어야 정상


#------------------------------------------------------------------
# T3 — G2: 텍스트 계열은 별도(더 낮은) 상한을 쓴다
#=> 같은 크기라도 text 면 낮은 상한, pdf 면 일반 상한이 적용돼야 한다. 이 분기가
#   깨지면 55MB PDF(본문 343자) 같은 정상 문서를 잃거나, 거대 tsv 를 통과시킨다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_g2_text_has_own_limit(tmp_path):
    from csoclassify.extract.base import SizeLimitError, check_size_limit

    p = tmp_path / "data.tsv"
    p.write_bytes(b"a\tb\n" * 500)      # 2000바이트

    # 텍스트 계열: 낮은 상한(1000)에 걸린다.
    with pytest.raises(SizeLimitError) as ei:
        check_size_limit(str(p), ftype="text", max_bytes=100000, max_bytes_text=1000)
    assert "MAX_FILE_BYTES_TEXT" in str(ei.value)

    # 같은 파일이라도 텍스트가 아니면 일반 상한(100000)을 쓰므로 통과한다.
    check_size_limit(str(p), ftype="pdf", max_bytes=100000, max_bytes_text=1000)


#------------------------------------------------------------------
# 상한 해제 · 크기를 못 재는 경우
#=> 0 이하는 무제한이어야 하고, 없는 파일은 여기서 막지 않고 뒤의 파서에게
#   맡겨야 한다(상한과 무관한 이유로 정상 문서를 잃으면 안 된다).
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_g1_disabled_and_unstattable(tmp_path):
    from csoclassify.extract.base import check_size_limit

    p = tmp_path / "big.bin"
    p.write_bytes(b"\x00" * 5000)
    check_size_limit(str(p), ftype="binary", max_bytes=0)       # 0 = 무제한
    check_size_limit(str(tmp_path / "없는파일.bin"), ftype="binary", max_bytes=1)


#------------------------------------------------------------------
# T7 — G4: 압축 해제 누적 상한을 넘으면 펼치지 않고 원본 1건으로 되돌린다
#=> 깊이는 1이지만 폭이 큰 zip(폭탄)을 만들어, expand_paths 가 내부 파일로
#   펼치지 않고 압축 자체 1건만 돌려주는지 본다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_g4_archive_budget(tmp_path, monkeypatch):
    import zipfile
    from csoclassify.extract import archive

    z = tmp_path / "bomb.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for i in range(10):
            zf.writestr(f"m{i}.txt", "x" * 1000)     # 해제 시 총 10,000바이트

    work = tmp_path / "work"
    work.mkdir()

    # 상한을 넉넉히 주면 10건으로 펼쳐진다(정상 동작 확인).
    monkeypatch.setattr(archive.config, "MAX_ARCHIVE_BYTES", 1000000)
    monkeypatch.setattr(archive.config, "MAX_ARCHIVE_MEMBERS", 1000)
    assert len(archive.expand_paths([str(z)], str(work))) == 10

    # 상한을 낮추면 펼치지 않고 압축 자체 1건으로 폴백해야 한다.
    monkeypatch.setattr(archive.config, "MAX_ARCHIVE_BYTES", 2000)
    jobs = archive.expand_paths([str(z)], str(work))
    assert len(jobs) == 1
    assert jobs[0][0] == str(z)

    # 개수 상한도 같은 방식으로 걸려야 한다(0바이트 파일 폭탄 대비).
    monkeypatch.setattr(archive.config, "MAX_ARCHIVE_BYTES", 1000000)
    monkeypatch.setattr(archive.config, "MAX_ARCHIVE_MEMBERS", 3)
    assert len(archive.expand_paths([str(z)], str(work))) == 1


#------------------------------------------------------------------
# T8 — G5: 시간 예산은 만료 전엔 False, 0 이하면 항상 False(무제한)
#=> 파서 루프가 이 값 하나로 멈춤을 판단하므로 두 상태가 정확해야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_g5_deadline():
    from csoclassify.extract.base import ParserDeadline

    assert ParserDeadline(60).expired() is False
    # 0 = 무제한. --no-size-limit 이 이 값을 0 으로 만든다.
    assert ParserDeadline(0).expired() is False
    # 음수도 0 과 같이 '무제한'으로 읽는다(설정 실수로 파서가 멈춰 버리면 안 된다).
    assert ParserDeadline(-1).expired() is False


#------------------------------------------------------------------
# --no-size-limit 이 모든 게이트를 끄는지
#=> 게이트가 늘어날 때마다 여기 빠뜨리기 쉬워, 한 곳에서 묶어 확인한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_no_size_limit_disables_every_gate(monkeypatch):
    from csoclassify.cli import _apply_size_limit_overrides

    for name in ("MAX_FILE_BYTES", "MAX_FILE_BYTES_TEXT", "MAX_ARCHIVE_BYTES",
                 "MAX_ARCHIVE_MEMBERS", "PARSER_TIMEOUT", "MAX_PDF_PAGES"):
        monkeypatch.setattr(config, name, 999)

    _apply_size_limit_overrides(_Args(no_size_limit=True))

    for name in ("MAX_FILE_BYTES", "MAX_FILE_BYTES_TEXT", "MAX_ARCHIVE_BYTES",
                 "MAX_ARCHIVE_MEMBERS", "PARSER_TIMEOUT", "MAX_PDF_PAGES"):
        assert getattr(config, name) == 0, name
    # G3 는 값이 아니라 None 으로 꺼진다.
    assert _resolve_text_limit(_Args(no_size_limit=True)) is None


#------------------------------------------------------------------
# --max-file-mb 는 텍스트 상한도 함께 내린다
#=> 텍스트 상한이 일반 상한보다 크면 뒤집힌 설정이라 의미가 없다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_max_file_mb_keeps_text_limit_below(monkeypatch):
    from csoclassify.cli import _apply_size_limit_overrides

    monkeypatch.setattr(config, "MAX_FILE_BYTES", 100 * 1048576)
    monkeypatch.setattr(config, "MAX_FILE_BYTES_TEXT", 20 * 1048576)

    _apply_size_limit_overrides(_Args2(max_file_mb=5))
    assert config.MAX_FILE_BYTES == 5 * 1048576
    # 20MB 로 남아 있으면 '일반 5MB, 텍스트 20MB' 라는 앞뒤 안 맞는 설정이 된다.
    assert config.MAX_FILE_BYTES_TEXT == 5 * 1048576


#------------------------------------------------------------------
# 더미 인자 객체 (전체 상한 플래그용)
#=> _apply_size_limit_overrides 가 보는 속성을 모두 갖춘다.
#
# -필드: 각 --max-* 플래그 값(None 이면 미지정)
#------------------------------------------------------------------
class _ArgsAll:
    def __init__(self, **kw):
        for name in ("max_file_mb", "max_file_mb_text", "max_archive_mb",
                     "max_archive_members", "parser_timeout", "max_pdf_pages"):
            setattr(self, name, kw.get(name))
        self.no_size_limit = kw.get("no_size_limit", False)


#------------------------------------------------------------------
# 바이트 크기 문구 — 작은 값이 "0MB" 로 뭉개지지 않는다
#=> 실측에서 "0MB > 0MB 초과" 같은 아무 정보 없는 사유가 나왔다. 사유 문구는
#   사람이 상한을 조정할 유일한 근거라 뭉개지면 안 된다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_human_bytes_readable_at_small_sizes():
    from csoclassify.extract.base import human_bytes
    assert human_bytes(900) == "900B"
    assert human_bytes(10 * 1024) == "10.0KB"
    assert human_bytes(328 * 1048576).endswith("MB")
    # 1MB 미만이 "0MB" 가 되면 안 된다.
    assert "0MB" not in human_bytes(1000)


#------------------------------------------------------------------
# 새 플래그가 각각 제 상한을 바꾼다
#=> 플래그 하나가 엉뚱한 상한을 건드리면 운영자가 요약을 보고 조정해도 안 듣는다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_each_flag_sets_its_own_limit(monkeypatch):
    from csoclassify.cli import _apply_size_limit_overrides
    for name, val in (("MAX_FILE_BYTES", 1), ("MAX_FILE_BYTES_TEXT", 1),
                      ("MAX_ARCHIVE_BYTES", 1), ("MAX_ARCHIVE_MEMBERS", 1),
                      ("PARSER_TIMEOUT", 1), ("MAX_PDF_PAGES", 1)):
        monkeypatch.setattr(config, name, val)

    _apply_size_limit_overrides(_ArgsAll(
        max_file_mb=7, max_file_mb_text=3, max_archive_mb=11,
        max_archive_members=222, parser_timeout=33, max_pdf_pages=444))

    assert config.MAX_FILE_BYTES == 7 * 1048576
    assert config.MAX_FILE_BYTES_TEXT == 3 * 1048576
    assert config.MAX_ARCHIVE_BYTES == 11 * 1048576
    assert config.MAX_ARCHIVE_MEMBERS == 222
    assert config.PARSER_TIMEOUT == 33
    assert config.MAX_PDF_PAGES == 444


#------------------------------------------------------------------
# --max-file-mb-text 는 --max-file-mb 의 자동 보정보다 우선한다
#=> --max-file-mb 는 텍스트 상한이 더 크면 같이 내린다. 그런데 사용자가 텍스트
#   상한을 '명시'했다면 그 값이 이겨야 한다 — 자동 보정이 명시값을 덮으면
#   플래그를 준 의미가 없다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_text_flag_beats_auto_lowering(monkeypatch):
    from csoclassify.cli import _apply_size_limit_overrides
    monkeypatch.setattr(config, "MAX_FILE_BYTES", 100 * 1048576)
    monkeypatch.setattr(config, "MAX_FILE_BYTES_TEXT", 20 * 1048576)

    # 명시 없이 일반 상한만 낮추면 텍스트 상한이 따라 내려간다(종전 동작).
    _apply_size_limit_overrides(_ArgsAll(max_file_mb=5))
    assert config.MAX_FILE_BYTES_TEXT == 5 * 1048576

    # 텍스트 상한을 명시하면 그 값이 남는다(일반 상한보다 커도 사용자 뜻이다).
    monkeypatch.setattr(config, "MAX_FILE_BYTES_TEXT", 20 * 1048576)
    _apply_size_limit_overrides(_ArgsAll(max_file_mb=5, max_file_mb_text=9))
    assert config.MAX_FILE_BYTES_TEXT == 9 * 1048576


#------------------------------------------------------------------
# G5 표식 — 어디까지 읽었는지가 레코드에 남는다
#=> '일부만 읽었다'만으로는 3,000쪽 중 2,999쪽과 10쪽이 구분되지 않아 재검토
#   우선순위를 정할 수 없다. 분류 결과는 건드리지 않아야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_mark_partial_extract_fields():
    from csoclassify.cli import _mark_partial_extract
    rec = {"grade": "C", "why": {"security": {"method": "fusion"}}}
    _mark_partial_extract(rec, {"reason": "페이지 상한(3000p)", "unit": "pages",
                                "read": 3000, "total": 8100})
    assert rec["partial_extract"] is True
    assert rec["partial_extract_info"]["read"] == 3000
    assert rec["partial_extract_info"]["total"] == 8100
    assert rec["grade"] == "C"          # 판정은 그대로
    assert rec["why"]["security"]["method"] == "fusion"


#------------------------------------------------------------------
# G5 표식 통로 — 스레드마다 따로 보관되고, 가져가면 비워진다
#=> 데몬은 요청을 스레드로 받는다. 전역 한 칸이면 A 문서 표식이 B 결과에 붙는다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_notes_is_thread_local_and_consumed():
    import threading
    from csoclassify.extract import notes

    notes.reset()
    notes.set_partial("시간 상한(60s)", "pages", 10, 500)
    seen = {}

    def worker():
        # 다른 스레드에는 이 표식이 보이면 안 된다.
        seen["other"] = notes.take()

    t = threading.Thread(target=worker)
    t.start(); t.join()
    assert seen["other"] is None

    got = notes.take()
    assert got["read"] == 10 and got["total"] == 500
    # 가져가면 비워진다 — 다음 문서에 지난 표식이 남으면 안 된다.
    assert notes.take() is None


#------------------------------------------------------------------
# G4 표식 — 압축을 못 펼쳤다는 사실이 레코드에 남는다
#=> 이게 없으면 내부 문서 수백 건이 빠졌는데 결과에는 '압축 1건 처리'로만 보인다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_mark_archive_unexpanded_fields():
    from csoclassify.cli import _mark_archive_unexpanded
    rec = {"grade": "S"}
    _mark_archive_unexpanded(rec, "압축 해제 누적 상한 초과(600.0MB > 500.0MB)")
    assert rec["archive_unexpanded"] is True
    assert "상한 초과" in rec["archive_unexpanded_reason"]
    assert rec["grade"] == "S"


#------------------------------------------------------------------
# G4 보고 통로 — expand_paths 가 못 펼친 압축을 stats 로 알려 준다
#=> 로그만 남기면 결과를 보는 사람에게 안 닿는다(이번 작업의 출발점).
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_expand_paths_reports_unexpanded(tmp_path, monkeypatch):
    import zipfile
    from csoclassify.extract import archive

    z = tmp_path / "wide.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for i in range(10):
            zf.writestr(f"m{i}.txt", "x" * 1000)
    work = tmp_path / "w"; work.mkdir()

    monkeypatch.setattr(archive.config, "MAX_ARCHIVE_BYTES", 2000)
    stats = {}
    jobs = archive.expand_paths([str(z)], str(work), stats=stats)

    assert len(jobs) == 1                       # 펼치지 않고 1건으로 폴백
    assert len(stats["unexpanded"]) == 1        # 그리고 그 사실을 알려 준다
    assert stats["unexpanded"][0]["path"] == str(z)
    assert "MAX_ARCHIVE_BYTES" in stats["unexpanded"][0]["reason"]

    # 상한이 넉넉하면 보고할 것이 없어야 한다(정상 경로 무해성).
    monkeypatch.setattr(archive.config, "MAX_ARCHIVE_BYTES", 10_000_000)
    stats2 = {}
    assert len(archive.expand_paths([str(z)], str(work), stats=stats2)) == 10
    assert stats2["unexpanded"] == []


#------------------------------------------------------------------
# 상한이 아닌 이유로 압축을 못 펼쳐도 조용히 넘기지 않는다
#=> 예전에는 손상·미지원·분할볼륨으로 확장이 실패하면 stats 에 아무것도 남기지
#   않고 '원본 1건'으로만 폴백했다. 그러면 압축 안 문서 수백 건이 통째로 빠졌는데
#   결과에는 "못 읽은 파일 1건"으로만 보여 원인을 찾을 수 없었다.
#   상한 초과(limit)와 그 밖의 실패(error)를 kind 로 갈라 담아야, 안내가 엉뚱한
#   처방("상한을 올리세요")을 내놓지 않는다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_expand_reports_non_limit_failures(tmp_path):
    from csoclassify.extract import archive

    # 7z 매직만 가진 깨진 파일 — 감지는 7z 로 되지만 py7zr 이 열지 못한다.
    bad = tmp_path / "broken.7z"
    bad.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 200)
    assert archive._archive_type(str(bad)) == "7z", "감지는 7z 여야 한다"

    work = tmp_path / "w"; work.mkdir()
    stats = {}
    jobs = archive.expand_paths([str(bad)], str(work), stats=stats)

    assert len(jobs) == 1, "못 펼쳤으면 원본 1건으로 둔다"
    assert len(stats["unexpanded"]) == 1, "실패를 조용히 넘기면 안 된다"
    row = stats["unexpanded"][0]
    assert row["kind"] == "error", "상한이 아니라 실패다"
    assert row["path"] == str(bad)
    assert row["reason"], "사유가 비면 사람이 원인을 못 찾는다"


#------------------------------------------------------------------
# 상한 초과는 kind="limit" 으로 갈린다
#=> 두 경우의 안내 문구가 달라야 하므로 종류가 정확해야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_expand_marks_limit_kind(tmp_path, monkeypatch):
    import zipfile
    from csoclassify.extract import archive

    z = tmp_path / "wide.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for i in range(10):
            zf.writestr(f"m{i}.txt", "x" * 1000)
    work = tmp_path / "w2"; work.mkdir()

    monkeypatch.setattr(archive.config, "MAX_ARCHIVE_BYTES", 2000)
    stats = {}
    archive.expand_paths([str(z)], str(work), stats=stats)
    assert stats["unexpanded"][0]["kind"] == "limit"
    assert "MAX_ARCHIVE_BYTES" in stats["unexpanded"][0]["reason"]


#------------------------------------------------------------------
# 레코드 표식 — 종류까지 실린다
#=> 상한이면 값을 올리면 되고, 실패면 원본·포맷을 봐야 한다. 사람이 할 일이
#   다르므로 결과만 보고 구분할 수 있어야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_mark_archive_unexpanded_carries_kind():
    from csoclassify.cli import _mark_archive_unexpanded
    rec = {"grade": None}
    _mark_archive_unexpanded(rec, "Bad7zFile: invalid header data", "error")
    assert rec["archive_unexpanded"] is True
    assert rec["archive_unexpanded_kind"] == "error"
    # 기본값은 상한(예전 호출부 호환).
    rec2 = {}
    _mark_archive_unexpanded(rec2, "상한 초과")
    assert rec2["archive_unexpanded_kind"] == "limit"


#------------------------------------------------------------------
# 분할 압축 조각을 이름으로 알아본다
#=> 내용으로는 구분이 안 된다 — 첫 조각은 정상 압축과 같은 매직이고, 두 번째
#   이후는 매직이 없어 '그냥 이진 파일'이다(실측에서 세 번째 조각이 text 로
#   감지돼 이진 쓰레기가 분류될 뻔했다). 오탐을 막는 것도 같이 확인한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_split_volume_hint():
    from csoclassify.extract.archive import split_volume_hint as h
    # 조각으로 봐야 하는 것
    for n in ("split.7z.0001", "a.7z.002", "x.zip.001", "노트.tar.gz.003",
              "p.z01", "p.z12", "doc.part01.rar", "doc.part2.rar", "a.r00", "a.r15"):
        assert h(n), f"조각으로 봐야 한다: {n}"
    # 조각이 아닌 것 — 여기서 오탐하면 정상 문서를 읽지 않고 버린다
    for n in ("backup.001", "report.pdf", "a.rar", "a.zip", "a.7z", "메모.txt"):
        assert h(n) is None, f"조각이 아니다: {n}"


#------------------------------------------------------------------
# 분할 압축의 첫 조각은 '깨짐'이 아니라 '분할'로 보고된다
#=> 사유가 "Bad7zFile: invalid header data" 뿐이면 사람은 파일이 깨진 줄 알고
#   원본을 뒤진다. 실제로는 멀쩡한 압축인데 조각을 못 잇는 것뿐이다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_split_volume_reported_as_split_not_corrupt(tmp_path):
    from csoclassify.extract import archive
    # 7z 매직으로 시작하지만 뒤가 없는 '첫 조각' 흉내
    part = tmp_path / "big.7z.001"
    part.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 300)
    assert archive._archive_type(str(part)) == "7z", "첫 조각은 압축으로 감지된다"

    work = tmp_path / "w"; work.mkdir()
    stats = {}
    archive.expand_paths([str(part)], str(work), stats=stats)
    row = stats["unexpanded"][0]
    assert row["kind"] == "split_volume", f"분할로 봐야 한다: {row}"
    assert "분할" in row["reason"]
    # 원인도 함께 남긴다 — 진짜 손상일 가능성도 사람이 볼 수 있어야 한다.
    assert "원인:" in row["reason"]


#------------------------------------------------------------------
# 압축 집계 — 업무분류는 대표를 고르지 않고 '분포'를 싣는다
#=> 보안등급은 C>S>O 서열이 있어 최고 위험 하나로 롤업되지만, 업무분류는 서열이
#   없고 한 문서가 여러 라벨을 갖는다. 대표 하나를 고르면 반드시 무언가를 버린다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_archive_record_carries_doctype_distribution():
    from csoclassify.cli import _archive_record
    grades = ["S", "O", "C", None]
    dist = {"DC_001_001": 2, "DC_003_002": 1, "unclassified": 1}
    rec = _archive_record("d:/x/a.zip", grades, "2026-09-07T00:00:00+09:00", dist)

    # 보안등급은 여전히 '최고 위험' 하나로 롤업된다.
    assert rec["grade"] == "C", "C 한 건이 있으면 압축은 C 다"
    assert rec["contains"] == {"total": 4, "C": 1, "S": 1, "O": 1, "unclassified": 1}
    # 업무분류는 분포 그대로.
    assert rec["contains_doctype"] == dist
    # 키 순서가 흔들리면 결과 파일 비교가 어려워지므로 정렬해 싣는다.
    assert list(rec["contains_doctype"]) == sorted(dist)


#------------------------------------------------------------------
# 업무분류 축을 안 쓰면 키 자체를 넣지 않는다
#=> '축 미사용'과 '축은 돌았는데 못 맞힘'은 다른 상태다. 빈 dict 를 실으면
#   둘이 구분되지 않는다(기존 labels.doctype 규칙과 같은 정신).
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_archive_record_omits_doctype_when_axis_off():
    from csoclassify.cli import _archive_record
    for empty in (None, {}):
        rec = _archive_record("d:/x/a.zip", ["S"], "t", empty)
        assert "contains_doctype" not in rec, f"축 미사용이면 키가 없어야 한다: {empty!r}"


#------------------------------------------------------------------
# 분포 수집 — 다중 라벨은 각각 세고, 축이 꺼졌으면 아무것도 안 센다
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_collect_archive_doctype():
    from csoclassify.cli import _collect_archive_doctype
    store = {}
    # 한 문서가 두 라벨을 가지면 둘 다 센다.
    _collect_archive_doctype(store, "a.zip", {"doctype": {
        "values": [{"dc_id": "DC_1"}, {"dc_id": "DC_2"}]}})
    _collect_archive_doctype(store, "a.zip", {"doctype": {
        "values": [{"dc_id": "DC_1"}]}})
    # 축은 돌았는데 라벨이 없으면 'unclassified' 로 센다.
    _collect_archive_doctype(store, "a.zip", {"doctype": {"values": []}})
    assert store["a.zip"] == {"DC_1": 2, "DC_2": 1, "unclassified": 1}

    # 축을 안 쓰는 배포(doctype 칸 없음)에서는 아무것도 모으지 않는다.
    store2 = {}
    _collect_archive_doctype(store2, "b.zip", {"security": {}})
    _collect_archive_doctype(store2, "b.zip", {})
    assert store2 == {}
