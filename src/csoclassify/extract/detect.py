#------------------------------------------------------------------
# 내용 기반 포맷 감지 (설계서 CSO_HybridParse §라우팅)
#=> 확장자가 아니라 "파일 내용"으로 실제 포맷을 판별한다. 확장자가 틀리거나
#   위장돼 있어도(.txt 인데 실제 doc 등) 올바른 파서로 보내기 위함이다.
#    1) 앞 8바이트 매직으로 큰 갈래를 나눈다: %PDF / OLE(D0CF11E0) / ZIP(PK)
#    2) OLE·ZIP 계열은 매직이 서로 같으므로(예: doc·xls·ppt·hwp 모두 OLE) 컨테이너
#       "내부 스트림/엔트리"를 열어 세부 포맷을 구분한다.
#    3) 매직이 없으면 널바이트 유무로 텍스트/바이너리를 가른다.
#   반환 타입은 라우팅 키로 쓰인다(pdf/hwp/hwpx/doc/docx/xls/xlsx/ppt/pptx/text/…).
#------------------------------------------------------------------

import zipfile

# olefile 은 hwp5 용으로 이미 번들. 없으면 OLE 세부구분만 포기(→ 'ole').
try:
    import olefile
except ImportError:
    olefile = None

# 복합문서(OLE/CFB) 시그니처 — doc/xls/ppt/hwp 가 모두 이 매직으로 시작한다.
_OLE_MAGIC = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
# ZIP 시그니처 — docx/xlsx/pptx/hwpx 가 모두 이 매직으로 시작한다.
_ZIP_MAGIC = b"PK\x03\x04"


#------------------------------------------------------------------
# OLE 내부 스트림으로 세부 포맷 구분
#=> doc/xls/ppt/hwp 는 매직이 같으므로 컨테이너 안의 대표 스트림 이름으로 가른다.
#    · FileHeader 스트림에 "HWP Document File" 시그니처 → hwp
#    · WordDocument → doc / Workbook|Book → xls / PowerPoint Document → ppt
#
# -in: path = OLE 파일 경로
#
# -out: str = "hwp"|"doc"|"xls"|"ppt"|"ole"(알 수 없는 OLE)
# -out: error = 없음(열기 실패 시 "unknown")
#------------------------------------------------------------------
def _detect_ole(path):
    try:
        ole = olefile.OleFileIO(path)
    except Exception:   # noqa: BLE001  (손상 OLE 등)
        return "unknown"
    try:
        top = {e[0] for e in ole.listdir()}
        # HWP5 는 FileHeader 스트림의 32바이트 시그니처로 확정한다.
        if "FileHeader" in top:
            try:
                if ole.openstream("FileHeader").read(32).startswith(b"HWP Document File"):
                    return "hwp"
            except Exception:   # noqa: BLE001
                pass
        if "WordDocument" in top:
            return "doc"
        if "Workbook" in top or "Book" in top:   # BIFF8/BIFF5
            return "xls"
        if "PowerPoint Document" in top:
            return "ppt"
        # 그 외 OLE(예: .msg) 는 전용 파서가 없다 → snf 폴백 대상.
        return "ole"
    finally:
        ole.close()


#------------------------------------------------------------------
# ZIP 내부 엔트리로 세부 포맷 구분
#=> docx/xlsx/pptx/hwpx 는 매직이 같으므로 컨테이너 안의 대표 파일로 가른다.
#
# -in: path = ZIP 파일 경로
#
# -out: str = "docx"|"xlsx"|"pptx"|"hwpx"|"zip"(일반 zip)
# -out: error = 없음(열기 실패 시 "unknown")
#------------------------------------------------------------------
def _detect_zip(path):
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "word/document.xml" in names:
                return "docx"
            if "xl/workbook.xml" in names:
                return "xlsx"
            if "ppt/presentation.xml" in names:
                return "pptx"
            # HWPX: Contents/section0.xml… 또는 mimetype 에 hwp 표기
            if any(n.startswith("Contents/section") for n in names):
                return "hwpx"
            if "mimetype" in names:
                try:
                    if b"hwp" in z.read("mimetype"):
                        return "hwpx"
                except Exception:   # noqa: BLE001
                    pass
        # 일반 zip 은 전용 파서 없음 → snf 폴백 대상.
        return "zip"
    except Exception:   # noqa: BLE001  (손상 zip 등)
        return "unknown"


#------------------------------------------------------------------
# 파일 → 포맷 타입 (핵심)
#=> 파일 내용으로 실제 포맷을 판별해 라우팅 키를 돌려준다(확장자 무시).
#    1) 앞 8바이트 읽기 → %PDF / OLE / ZIP 매직 판별
#    2) OLE·ZIP 은 내부를 열어 세부 구분(_detect_ole/_detect_zip)
#    3) 매직 없으면 널바이트 유무로 text/binary 판정
#
# -in: path = 대상 파일 경로
#
# -out: str = pdf|hwp|hwpx|doc|docx|xls|xlsx|ppt|pptx|text|ole|zip|binary|unknown
# -out: error = 없음(읽기 실패 등은 "unknown")
#------------------------------------------------------------------
def detect_format(path):
    try:
        with open(path, "rb") as f:
            # 앞부분을 넉넉히 읽는다: 매직(8B)뿐 아니라 HTML 마커(<html 등)도 보려면 필요.
            head = f.read(2048)
    except OSError:
        return "unknown"
    if not head:
        return "unknown"
    # PDF: 앞이 "%PDF-".
    if head[:5] == b"%PDF-":
        return "pdf"
    # 이미지: 전용 텍스트 파서가 없다(OCR 별도) → 'image'로 두고 snf 폴백/미분류.
    #   (PNG 는 널바이트가 없어 아래 text 판정에 잘못 걸리므로 여기서 먼저 잡는다.)
    if (head[:8] == b"\x89PNG\r\n\x1a\n" or head[:3] == b"\xFF\xD8\xFF"
            or head[:6] in (b"GIF87a", b"GIF89a") or head[:2] == b"BM"):
        return "image"
    # OLE 복합문서: 내부 스트림으로 세부 구분(olefile 없으면 'ole' 로 두고 snf 폴백).
    #   head 는 2048바이트라 8바이트 매직과 통째로 비교하면 절대 참이 되지 않는다.
    #   반드시 앞 8바이트만 잘라 비교한다(바로 아래 ZIP 판정과 같은 방식).
    if head[:8] == _OLE_MAGIC:
        return _detect_ole(path) if olefile is not None else "ole"
    # ZIP 컨테이너: 내부 엔트리로 세부 구분.
    if head[:4] == _ZIP_MAGIC:
        return _detect_zip(path)
    # 매직 없음: 널바이트가 없으면 텍스트 계열로 본다(txt/md/csv/json/ipynb/py/html 등).
    if b"\x00" not in head:
        # HTML 은 태그를 벗겨야 하므로 별도 타입으로 잡는다(앞부분에 html 마커가 있으면).
        low = head.lower()
        if (b"<!doctype html" in low or b"<html" in low
                or b"<head" in low or b"<body" in low):
            return "html"
        return "text"
    return "binary"
