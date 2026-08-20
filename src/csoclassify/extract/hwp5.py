#------------------------------------------------------------------
# HWP 5.0 순수 파이썬 텍스트 추출기 (설계서 CSO_HybridParse §7)
#=> 구형 한글 문서(.hwp, HWP 5.0)를 외부 실행파일·Rust 라이브러리 없이 파이썬만으로
#   추출한다. HWP5 는 OLE/CFB 컨테이너(=구형 .doc 와 같은 복합문서) 안에
#   BodyText/SectionN 스트림을 두고, 그 안에 태그 레코드로 문단을 담는다. 본문
#   텍스트는 PARA_TEXT 레코드(UTF-16LE + 인라인 제어문자)에 들어 있다.
#    · OLE 읽기: olefile(순수 파이썬, 소형) 사용
#    · 압축: FileHeader 속성비트가 켜져 있으면 각 Section 이 raw-deflate(zlib) 압축
#   In-process 로 동작하고 크래시가 없어(파이썬 예외만 발생) 프로세스 격리가 필요 없다.
#   실패(HWP5 아님·암호화·구형 3.x·손상)는 ExtractError 로 알려 상위가 snf 로 폴백한다.
#   (실측: snf 와 sim 1.00, 파일당 ~2ms — tests/compare_extractors_4way.ipynb)
#------------------------------------------------------------------

import re
import struct
import zlib

from .base import TextExtractor, ExtractError

# olefile 은 순수 파이썬 소형 라이브러리. 없으면 hwp5 만 비활성(→ snf 폴백)되게 지연 취급.
try:
    import olefile
except ImportError:   # 번들 누락 등 예외 상황 — hwp5 경로만 포기하고 snf 로 폴백된다.
    olefile = None

# PARA_TEXT 레코드 태그 = HWPTAG_BEGIN(0x10) + 51 = 67. 본문 글자가 여기 담긴다.
_HWPTAG_PARA_TEXT = 67

# 문단 텍스트 내 '1글자만 차지하는' 제어문자(그 외 1~31 은 8글자=16바이트 차지).
#   10(줄바꿈)·13(문단끝)만 개행으로 살리고 0 은 버린다.
_CHAR_CTRL = {0, 10, 13}


#------------------------------------------------------------------
# PARA_TEXT 페이로드 → 순수 텍스트
#=> UTF-16LE 로 저장된 문단 글자열을 훑어, 실제 글자만 남기고 제어문자를 처리한다.
#   HWP5 는 문단 안에 표·그림 같은 '컨트롤'을 제어문자로 끼워 넣는데, 그 컨트롤은
#   8글자(16바이트)를 차지하므로 그만큼 건너뛰지 않으면 뒷글자가 다 밀려 깨진다.
#    1) 2바이트씩 읽어 wchar 값을 만든다
#    2) 0/10/13 은 1글자 컨트롤(10·13→개행), 그 외 1~31 은 8글자 컨트롤(16바이트 skip)
#    3) 32 이상은 실제 글자 → 그대로 채택
#
# -in: payload = PARA_TEXT 레코드의 원시 바이트(UTF-16LE + 제어문자)
#
# -out: text = 해독된 문단 텍스트
# -out: error = 없음(형식이 이상하면 가능한 만큼만 뽑음)
#------------------------------------------------------------------
def _decode_para_text(payload):
    out = []
    i, n = 0, len(payload)
    while i + 2 <= n:
        wc = payload[i] | (payload[i + 1] << 8)
        if wc in _CHAR_CTRL:
            # 10(줄바꿈)·13(문단끝)만 개행으로 남기고 0 은 버린다.
            if wc in (10, 13):
                out.append("\n")
            i += 2
        elif wc < 32:
            # 인라인/확장 컨트롤: 총 8 wchar(16바이트)를 차지 → 통째로 건너뛴다.
            i += 16
        else:
            out.append(chr(wc))
            i += 2
    return "".join(out)


#------------------------------------------------------------------
# Section 스트림 → 태그 레코드 순회
#=> HWP5 레코드는 [32비트 헤더][페이로드] 나열이다. 헤더에서 태그ID·크기를 뽑아
#   페이로드를 잘라 준다. 크기가 0xFFF 면 '초과' 표식이라 다음 4바이트가 실제 크기다.
#    1) 4바이트 헤더 파싱: tag_id(하위 10비트), size(상위 12비트)
#    2) size==0xFFF 면 확장 크기(다음 4바이트) 사용
#    3) (태그, 페이로드)를 하나씩 내보낸다
#
# -in: data = 압축해제된 Section 스트림 전체 바이트
#
# -out: (tag_id, payload) 를 순서대로 yield
# -out: error = 없음(끝에서 잘리면 순회 중단)
#------------------------------------------------------------------
def _iter_records(data):
    i, n = 0, len(data)
    while i + 4 <= n:
        header = struct.unpack("<I", data[i:i + 4])[0]
        i += 4
        tag_id = header & 0x3FF                 # 하위 10비트 = 태그
        size = (header >> 20) & 0xFFF           # 상위 12비트 = 크기
        if size == 0xFFF:
            # 크기 초과 표식: 다음 4바이트가 진짜 크기.
            if i + 4 > n:
                break
            size = struct.unpack("<I", data[i:i + 4])[0]
            i += 4
        payload = data[i:i + size]
        i += size
        yield tag_id, payload


class Hwp5Extractor(TextExtractor):
    #------------------------------------------------------------------
    # 문서 → 원시 텍스트 (핵심)
    #=> HWP5(OLE) 를 열어 BodyText/SectionN 들의 PARA_TEXT 를 순서대로 이어 붙인다.
    #    1) olefile 로 OLE 인지 확인하고 연다(아니면 실패 → snf 폴백)
    #    2) FileHeader 속성비트로 압축(bit0)·암호화(bit1) 여부 확인. 암호화면 포기
    #    3) BodyText/Section0,1,... 을 번호순으로 읽고, 압축이면 raw-deflate 해제
    #    4) 각 Section 의 PARA_TEXT 레코드를 해독해 본문을 만든다
    #   ※ save_dir 저장은 상위(HybridExtractor)가 승자 텍스트만 한 번 처리한다.
    #
    # -in: input_path = 추출할 .hwp 경로
    # -in: save_dir   = (호환용) 여기서는 사용 안 함
    #
    # -out: text = 추출된 본문(레코드가 없으면 빈 문자열 → 상위가 폴백 판단)
    # -out: error = olefile 없음/HWP5 아님/암호화/손상 시 ExtractError(→ snf 폴백)
    #------------------------------------------------------------------
    def extract(self, input_path, save_dir=None):
        # olefile 이 없으면 이 경로는 쓸 수 없다 → 상위가 snf 로 폴백하게 알린다.
        if olefile is None:
            raise ExtractError("olefile 미설치(HWP5 파서 비활성)")
        # OLE(복합문서)가 아니면 HWP5 가 아니다(구형 3.x·손상 포함) → 폴백.
        if not olefile.isOleFile(input_path):
            raise ExtractError("HWP5(OLE) 형식 아님")

        try:
            ole = olefile.OleFileIO(input_path)
        except OSError as e:
            raise ExtractError(f"HWP5 열기 실패: {type(e).__name__}")

        try:
            # (2) 압축/암호화 판별: FileHeader offset 36 의 속성 DWORD.
            if not ole.exists("FileHeader"):
                raise ExtractError("HWP5 FileHeader 없음")
            header = ole.openstream("FileHeader").read()
            flags = struct.unpack("<I", header[36:40])[0] if len(header) >= 40 else 0
            compressed = bool(flags & 0x01)      # bit0 = 압축
            encrypted = bool(flags & 0x02)       # bit1 = 암호 설정
            # 암호화 문서는 해독 불가 → snf(사이냅)에 맡긴다.
            if encrypted:
                raise ExtractError("HWP5 암호화 문서")

            # (3) BodyText/SectionN 스트림을 번호순으로 모은다.
            secs = [e for e in ole.listdir()
                    if len(e) == 2 and e[0] == "BodyText" and e[1].startswith("Section")]
            secs.sort(key=lambda e: int(re.sub(r"\D", "", e[1]) or 0))

            parts = []
            for e in secs:
                data = ole.openstream(e).read()
                if compressed:
                    # HWP5 는 zlib 헤더 없는 raw-deflate → wbits=-15.
                    try:
                        data = zlib.decompress(data, -15)
                    except zlib.error:
                        # 한 섹션이 깨져도 나머지는 살린다(부분 추출).
                        continue
                for tag_id, payload in _iter_records(data):
                    if tag_id == _HWPTAG_PARA_TEXT:
                        parts.append(_decode_para_text(payload))
            return "\n".join(parts)
        finally:
            ole.close()
