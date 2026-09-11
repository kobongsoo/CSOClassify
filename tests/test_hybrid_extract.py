#------------------------------------------------------------------
# 하이브리드 추출기 단위 테스트 (내용감지 라우팅 + 포맷별 파서 + snf 폴백)
#=> detect_format(내용 감지), HybridExtractor(감지→파서 라우팅·폴백·저장),
#   HwpxZipExtractor·PlainTextExtractor·구형 office 파서 등을 실제 snf 없이 검증한다.
#   합성 파일과 가짜 추출기(FakeSnf)로 어떤 환경에서도 동작하게 만든다.
#------------------------------------------------------------------

import struct
import zipfile

import pytest

from csoclassify.extract.detect import detect_format
from csoclassify.extract.hwpx_zip import HwpxZipExtractor
from csoclassify.extract.hwp5 import _decode_para_text, _iter_records
from csoclassify.extract.plaintext import PlainTextExtractor
from csoclassify.extract.html_text import HtmlTextExtractor
from csoclassify.extract.hybrid import HybridExtractor
from csoclassify.extract.base import ExtractError, save_extracted_text


#------------------------------------------------------------------
# 합성 HWPX(zip) 만들기 — Contents/section0.xml 에 <hp:t> 런
# -in: path=경로, runs=본문 런 리스트
# -out: 없음(파일 생성)
#------------------------------------------------------------------
def _make_hwpx(path, runs):
    body = "".join(f"<hp:p><hp:run><hp:t>{r}</hp:t></hp:run></hp:p>" for r in runs)
    with zipfile.ZipFile(str(path), "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<hs:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
                   + body + "</hs:sec>")


#------------------------------------------------------------------
# 합성 OOXML(zip) 만들기 — 감지용 대표 엔트리만 넣는다
# -in: path=경로, kind="docx"|"xlsx"|"pptx"
# -out: 없음(파일 생성)
#------------------------------------------------------------------
def _make_ooxml(path, kind):
    entry = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml",
             "pptx": "ppt/presentation.xml"}[kind]
    with zipfile.ZipFile(str(path), "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(entry, "<xml/>")


#------------------------------------------------------------------
# 가짜 snf 추출기(폴백/라우팅 검증용)
# -필드: text=반환값, fail=True 면 실패, called=호출여부
#------------------------------------------------------------------
class FakeSnf:
    def __init__(self, text="SNF본문", fail=False):
        self.text = text
        self.fail = fail
        self.called = False

    def extract(self, input_path, save_dir=None):
        self.called = True
        if self.fail:
            raise ExtractError("snf 실패(가짜)")
        return self.text


class FakeEngine:
    def __init__(self, text="ENG본문", fail=False):
        self.text = text
        self.fail = fail
        self.called = False

    def extract(self, input_path, save_dir=None):
        self.called = True
        if self.fail:
            raise ExtractError("엔진 실패(가짜)")
        return self.text


# ── 내용 감지 detect_format ──────────────────────────────
#------------------------------------------------------------------
# 감지: HWPX(zip) → 'hwpx'
#------------------------------------------------------------------
def test_detect_hwpx(tmp_path):
    p = tmp_path / "x.hwpx"; _make_hwpx(p, ["가"])
    assert detect_format(str(p)) == "hwpx"


#------------------------------------------------------------------
# 감지: OOXML → docx/xlsx/pptx (확장자와 무관, 내부 엔트리로)
#------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["docx", "xlsx", "pptx"])
def test_detect_ooxml(tmp_path, kind):
    p = tmp_path / f"x.{kind}"; _make_ooxml(p, kind)
    assert detect_format(str(p)) == kind


#------------------------------------------------------------------
# 감지: 위장(확장자만 다름) → 내용으로 판별
#=> OOXML(docx)을 .txt 로 저장해도 'docx' 로 감지돼야 한다.
#------------------------------------------------------------------
def test_detect_disguised(tmp_path):
    p = tmp_path / "disguised.txt"; _make_ooxml(p, "docx")
    assert detect_format(str(p)) == "docx"


#------------------------------------------------------------------
# 감지: PDF 매직 / 텍스트 / 바이너리
#------------------------------------------------------------------
def test_detect_pdf_text_binary(tmp_path):
    pdf = tmp_path / "a.pdf"; pdf.write_bytes(b"%PDF-1.7\n...")
    assert detect_format(str(pdf)) == "pdf"
    txt = tmp_path / "a.txt"; txt.write_text("한글 텍스트", encoding="utf-8")
    assert detect_format(str(txt)) == "text"
    binf = tmp_path / "a.bin"; binf.write_bytes(b"\x00\x01\x02\x03rubbish")
    assert detect_format(str(binf)) == "binary"


# ── HWPX zip 파서 ────────────────────────────────────────
def test_hwpx_extract(tmp_path):
    p = tmp_path / "s.hwpx"; _make_hwpx(p, ["첫째", "둘째"])
    t = HwpxZipExtractor().extract(str(p))
    assert "첫째" in t and "둘째" in t


def test_hwpx_entities(tmp_path):
    p = tmp_path / "e.hwpx"; _make_hwpx(p, ["A &lt;b&gt; &amp; C"])
    assert "A <b> & C" in HwpxZipExtractor().extract(str(p))


def test_hwpx_badzip(tmp_path):
    p = tmp_path / "n.hwpx"; p.write_bytes(b"not a zip")
    with pytest.raises(ExtractError):
        HwpxZipExtractor().extract(str(p))


# ── HWP5 레코드/텍스트 디코더 ─────────────────────────────
def test_decode_para_text_basic():
    assert _decode_para_text("가나\n다".encode("utf-16-le")) == "가나\n다"


def test_decode_para_text_inline_control():
    payload = "가".encode("utf-16-le") + struct.pack("<8H", 4, 0, 0, 0, 0, 0, 0, 0) + "나".encode("utf-16-le")
    assert _decode_para_text(payload) == "가나"


def test_iter_records():
    body = "본문".encode("utf-16-le")
    header = (67 & 0x3FF) | ((len(body) & 0xFFF) << 20)
    recs = list(_iter_records(struct.pack("<I", header) + body))
    assert len(recs) == 1 and recs[0][0] == 67 and recs[0][1] == body


# ── PlainText 파서 ───────────────────────────────────────
def test_plaintext_utf8_cp949(tmp_path):
    u = tmp_path / "u.txt"; u.write_text("한글 UTF8", encoding="utf-8")
    assert PlainTextExtractor().extract(str(u)) == "한글 UTF8"
    c = tmp_path / "c.txt"; c.write_bytes("한글 CP949".encode("cp949"))
    assert "한글 CP949" in PlainTextExtractor().extract(str(c))


# ── HTML 감지/추출 ───────────────────────────────────────
#------------------------------------------------------------------
# 감지: HTML 마커 있으면 'html', 없으면 'text'
#------------------------------------------------------------------
def test_detect_html(tmp_path):
    h = tmp_path / "a.html"
    h.write_text("<!DOCTYPE html><html><body><p>안녕</p></body></html>", encoding="utf-8")
    assert detect_format(str(h)) == "html"
    # 확장자만 .txt 여도 내용에 <html> 있으면 html 로 감지
    d = tmp_path / "b.txt"
    d.write_text("<html><head></head><body>본문</body></html>", encoding="utf-8")
    assert detect_format(str(d)) == "html"
    # html 마커 없는 순수 텍스트는 text
    t = tmp_path / "c.txt"; t.write_text("그냥 텍스트 <없음", encoding="utf-8")
    assert detect_format(str(t)) == "text"


#------------------------------------------------------------------
# HtmlTextExtractor: 태그 제거 + script/style 제외 + 엔티티 복원
#------------------------------------------------------------------
def test_html_extract(tmp_path):
    p = tmp_path / "x.html"
    p.write_text("<html><head><style>.a{color:red}</style></head>"
                 "<body><p>제목 &amp; 부제</p><script>var x=1;</script>"
                 "<div>본문 내용</div></body></html>", encoding="utf-8")
    t = HtmlTextExtractor().extract(str(p))
    assert "제목 & 부제" in t and "본문 내용" in t
    assert "color:red" not in t and "var x" not in t   # style/script 제거
    assert "<" not in t                                 # 태그 제거


# ── HybridExtractor 라우팅/폴백 ──────────────────────────
#------------------------------------------------------------------
# 라우팅: 감지타입의 엔진 1차, snf 폴백
#------------------------------------------------------------------
def test_hybrid_routing_by_detect(tmp_path):
    p = tmp_path / "d.docx"; _make_ooxml(p, "docx")
    dx = FakeEngine("DOCX본문")
    hyb = HybridExtractor(engines={"docx": dx}, snf=FakeSnf())
    ftype, chain = hyb._route(str(p))
    assert ftype == "docx"
    assert [n for n, _ in chain] == ["docx", "snf"]


#------------------------------------------------------------------
# 1차 성공 시 폴백 안 함
#------------------------------------------------------------------
def test_hybrid_primary(tmp_path):
    p = tmp_path / "d.docx"; _make_ooxml(p, "docx")
    snf = FakeSnf()
    # 본문은 MIN_TEXT_LEN(기본 20자)보다 길어야 한다 — 그보다 짧으면 프로젝트
    # 자신의 정의로 "본문 없음"이라, 1차가 성공했다고 볼 수 없어 폴백이 걸린다
    # (그 동작은 test_hybrid_short_falls_back 이 따로 지킨다).
    hyb = HybridExtractor(engines={"docx": FakeEngine("본문A 이 문서는 정상적인 분량의 본문을 가진 시험용 문서입니다.")}, snf=snf)
    assert hyb.extract(str(p)) == "본문A 이 문서는 정상적인 분량의 본문을 가진 시험용 문서입니다."
    assert snf.called is False


#------------------------------------------------------------------
# 1차 실패 시 snf 폴백
#------------------------------------------------------------------
def test_hybrid_fallback(tmp_path):
    p = tmp_path / "d.docx"; _make_ooxml(p, "docx")
    snf = FakeSnf("폴백본문")
    hyb = HybridExtractor(engines={"docx": FakeEngine(fail=True)}, snf=snf)
    assert hyb.extract(str(p)) == "폴백본문"
    assert snf.called is True


#------------------------------------------------------------------
# snf 없이 1차 실패 → 미분류(ExtractError)
#------------------------------------------------------------------
def test_hybrid_no_snf_unclassified(tmp_path):
    p = tmp_path / "d.docx"; _make_ooxml(p, "docx")
    hyb = HybridExtractor(engines={"docx": FakeEngine(fail=True)}, snf=None)
    with pytest.raises(ExtractError):
        hyb.extract(str(p))


#------------------------------------------------------------------
# 미지원 타입(전용엔진 없음) → snf 로 (있으면), 없으면 미분류
#------------------------------------------------------------------
def test_hybrid_unsupported_type(tmp_path):
    # 일반 zip(감지='zip', 전용엔진 없음) → snf 폴백
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(str(z), "w") as zf:
        zf.writestr("readme.txt", "hi")
    snf = FakeSnf("SNF처리")
    hyb = HybridExtractor(engines={"docx": FakeEngine()}, snf=snf)
    assert hyb.extract(str(z)) == "SNF처리"
    # snf 도 없으면 미분류
    hyb2 = HybridExtractor(engines={"docx": FakeEngine()}, snf=None)
    with pytest.raises(ExtractError):
        hyb2.extract(str(z))


#------------------------------------------------------------------
# save_dir 은 승자 텍스트 1개만 저장
#------------------------------------------------------------------
def test_hybrid_save_once(tmp_path):
    p = tmp_path / "s.hwpx"; _make_hwpx(p, ["저장대상 이 문서는 정상적인 분량의 본문을 가진 시험용 문서입니다."])
    save_dir = tmp_path / "kept"
    hyb = HybridExtractor(engines={"hwpx": HwpxZipExtractor()}, snf=FakeSnf())
    hyb.extract(str(p), save_dir=str(save_dir))
    kept = list(save_dir.glob("*.txt"))
    assert len(kept) == 1 and "저장대상" in kept[0].read_text(encoding="utf-8")


#------------------------------------------------------------------
# 1차가 '본문 없음' 수준이면 snf 로 넘긴다
#=> 예전에는 완전히 빈 문자열일 때만 폴백했다. 그래서 전용 파서가 장식기호 몇 개를
#   돌려주면 그것을 성공으로 받아들이고 snf 를 써 보지도 않아, snf 는 읽을 수 있는
#   문서가 미분류로 나갔다(실측: 스캔성 PDF 1건이 이 경우였다).
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 폴백이 안 걸리면 AssertionError
#------------------------------------------------------------------
def test_hybrid_short_falls_back(tmp_path):
    p = tmp_path / "d.docx"; _make_ooxml(p, "docx")
    snf = FakeSnf("사이냅이 제대로 읽어 낸 충분한 길이의 본문입니다.")
    # 장식기호 몇 개 = MIN_TEXT_LEN 미만 → '성공'으로 보지 않는다
    hyb = HybridExtractor(engines={"docx": FakeEngine("〮 ∽ …")}, snf=snf)
    assert hyb.extract(str(p)) == "사이냅이 제대로 읽어 낸 충분한 길이의 본문입니다."
    assert snf.called is True


#------------------------------------------------------------------
# 마지막 엔진까지 짧으면 실패가 아니라 '가장 긴 결과'를 돌려준다
#=> 문서가 원래 짧을 수도 있다. 그런 문서를 ExtractError 로 바꾸면 예전에 성공하던
#   것이 실패가 되는 회귀다. 분류 쪽이 같은 임계로 '본문 없음' 표식을 달아 준다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 예외가 나거나 결과가 비면 AssertionError
#------------------------------------------------------------------
def test_hybrid_all_short_returns_longest(tmp_path):
    p = tmp_path / "d.docx"; _make_ooxml(p, "docx")
    snf = FakeSnf("짧다")
    hyb = HybridExtractor(engines={"docx": FakeEngine("조금 더 긴 쪽")}, snf=snf)
    assert hyb.extract(str(p)) == "조금 더 긴 쪽"
