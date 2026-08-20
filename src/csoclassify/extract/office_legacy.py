#------------------------------------------------------------------
# 구형 Office(97-2003 바이너리) 추출기 — doc/xls/ppt
#=> 2007 이전 .doc/.xls/.ppt 는 OLE/CFB 안에 독자 포맷으로 들어 있어, 현대
#   OOXML 라이브러리(python-docx 등)로는 못 읽는다. hwp5 와 같은 방식으로
#   olefile 로 열어 순수 파이썬으로 직접 파싱한다(외부 exe·JVM 불필요).
#     · doc → WordDocument 스트림의 piece table 을 따라 텍스트 조각을 잇는다.
#     · ppt → PowerPoint Document 스트림의 텍스트 원자(TextChars/TextBytes)를 모은다.
#     · xls → 성숙한 라이브러리 xlrd 로 BIFF 셀을 읽는다.
#   실측 snf 대비 sim≈0.99, 2~10배 빠름. 비표준/암호화 등 예외는 ExtractError →
#   상위가 snf 로 폴백한다.
#------------------------------------------------------------------

import struct

from .base import TextExtractor, ExtractError

try:
    import olefile
except ImportError:
    olefile = None


# ── .doc : WordDocument + piece table ───────────────────────
#------------------------------------------------------------------
# CLX 에서 piece table(Pcdt) 찾기
#=> Word 의 CLX 는 Prc(0x01)와 Pcdt(0x02) 조각의 나열이다. Pcdt 안에 piece
#   목록(PlcPcd)이 들어 있으므로 그것만 뽑아 준다.
#
# -in: clx = Table 스트림에서 잘라낸 CLX 바이트
#
# -out: bytes = PlcPcd(피스 목록) 바이트(없으면 b"")
# -out: error = 없음
#------------------------------------------------------------------
def _find_pcdt(clx):
    i = 0
    while i < len(clx):
        t = clx[i]
        if t == 0x01:            # Prc: 다음 2바이트 길이만큼 건너뜀
            if i + 3 > len(clx):
                break
            cb = struct.unpack_from("<H", clx, i + 1)[0]
            i += 3 + cb
        elif t == 0x02:          # Pcdt: 다음 4바이트가 PlcPcd 길이
            lcb = struct.unpack_from("<I", clx, i + 1)[0]
            return clx[i + 5: i + 5 + lcb]
        else:
            break
    return b""


class DocExtractor(TextExtractor):
    #------------------------------------------------------------------
    # doc → 텍스트 (핵심)
    #=> WordDocument 스트림과 Table 스트림(0/1Table)을 읽어, piece table 을 따라
    #   각 조각을 CP949(압축) 또는 UTF-16LE(비압축)로 디코드해 이어 붙인다.
    #    1) FIB 플래그로 어느 Table 스트림인지(0Table/1Table) 결정
    #    2) FIB 의 fcClx/lcbClx 로 CLX 위치를 얻어 piece table 추출
    #    3) 각 piece: fc 의 최상위비트로 압축(1바이트/cp949) vs UTF-16 구분해 디코드
    #    4) Word 제어문자(문단끝 등) 정리 후 반환
    #
    # -in: input_path = .doc 경로
    # -in: save_dir   = (호환용) 사용 안 함
    #
    # -out: text = 본문 텍스트
    # -out: error = olefile 없음/구조 이상/비표준 시 ExtractError(→ snf 폴백)
    #------------------------------------------------------------------
    def extract(self, input_path, save_dir=None):
        if olefile is None:
            raise ExtractError("olefile 미설치")
        try:
            ole = olefile.OleFileIO(input_path)
            try:
                wd = ole.openstream("WordDocument").read()
                # FIB base 의 플래그(offset 0x0A)에서 fWhichTblStm 비트로 Table 선택.
                flags = struct.unpack_from("<H", wd, 0x0A)[0]
                tbl_name = "1Table" if (flags & 0x0200) else "0Table"
                if not ole.exists(tbl_name):
                    tbl_name = "1Table" if ole.exists("1Table") else "0Table"
                tbl = ole.openstream(tbl_name).read()
                # Word97 FIB: fcClx offset=0x01A2, lcbClx=0x01A6.
                fc_clx, lcb_clx = struct.unpack_from("<II", wd, 0x01A2)
                clx = tbl[fc_clx: fc_clx + lcb_clx]
            finally:
                ole.close()
        except ExtractError:
            raise
        except Exception as e:   # noqa: BLE001
            raise ExtractError(f"doc 열기 실패: {type(e).__name__}")

        pcd = _find_pcdt(clx)
        if not pcd:
            raise ExtractError("doc piece table 없음")
        # PlcPcd = CP 배열((nP+1)*4) + Pcd 배열(nP*8).
        n_pieces = (len(pcd) - 4) // (4 + 8)
        if n_pieces <= 0:
            raise ExtractError("doc piece 수 0")
        cps = [struct.unpack_from("<I", pcd, k * 4)[0] for k in range(n_pieces + 1)]
        base = (n_pieces + 1) * 4
        out = []
        for k in range(n_pieces):
            fc = struct.unpack_from("<I", pcd, base + k * 8 + 2)[0]
            n_ch = cps[k + 1] - cps[k]
            if fc & 0x40000000:                 # 압축: 1바이트/글자(cp949 근사)
                off = (fc & 0x3FFFFFFF) // 2
                out.append(wd[off: off + n_ch].decode("cp949", "replace"))
            else:                               # 비압축: UTF-16LE
                out.append(wd[fc: fc + n_ch * 2].decode("utf-16-le", "replace"))
        txt = "".join(out)
        # Word 특수문자 정리(셀 구분 0x07→탭, 문단/줄바꿈 → 개행, 필드기호 제거).
        for a, b in [("\x07", "\t"), ("\x0b", "\n"), ("\r", "\n"),
                     ("\x08", ""), ("\x01", ""), ("\x02", ""), ("\x13", ""),
                     ("\x14", ""), ("\x15", "")]:
            txt = txt.replace(a, b)
        return txt


# ── .ppt : PowerPoint Document 텍스트 원자 ──────────────────
# TextCharsAtom(UTF-16LE) / TextBytesAtom(cp949) 레코드 타입.
_PPT_TEXTCHARS = 0x0FA0
_PPT_TEXTBYTES = 0x0FA8


#------------------------------------------------------------------
# PowerPoint 레코드 트리 순회 → 텍스트 원자 수집
#=> ppt 스트림은 [8바이트 헤더][데이터] 레코드 나열이며, 컨테이너(recVer=0xF)는
#   자식 레코드를 품는다. 재귀로 훑어 TextChars/TextBytes 원자를 모은다.
#
# -in: data = 레코드 바이트(스트림 또는 컨테이너 본문)
# -in: out  = 텍스트 조각을 누적할 리스트(부작용)
#
# -out: 없음(out 에 append)
# -out: error = 없음(끝에서 잘리면 중단)
#------------------------------------------------------------------
def _ppt_walk(data, out):
    i, n = 0, len(data)
    while i + 8 <= n:
        ver_inst, rec_type, rec_len = struct.unpack_from("<HHI", data, i)
        i += 8
        body = data[i: i + rec_len]
        if (ver_inst & 0x0F) == 0xF:            # 컨테이너 → 재귀
            _ppt_walk(body, out)
        elif rec_type == _PPT_TEXTCHARS:        # UTF-16LE 텍스트
            out.append(body.decode("utf-16-le", "replace"))
        elif rec_type == _PPT_TEXTBYTES:        # cp949 근사 텍스트
            out.append(body.decode("cp949", "replace"))
        i += rec_len


class PptExtractor(TextExtractor):
    #------------------------------------------------------------------
    # ppt → 텍스트 (핵심)
    #=> "PowerPoint Document" 스트림을 재귀 순회해 텍스트 원자를 모은다.
    #    1) olefile 로 스트림 읽기
    #    2) _ppt_walk 로 TextChars/TextBytes 수집
    #    3) 슬라이드 줄바꿈 코드 정리 후 반환
    #
    # -in: input_path = .ppt 경로
    # -in: save_dir   = (호환용) 사용 안 함
    #
    # -out: text = 슬라이드 본문 텍스트
    # -out: error = olefile 없음/스트림 없음 시 ExtractError(→ snf 폴백)
    #------------------------------------------------------------------
    def extract(self, input_path, save_dir=None):
        if olefile is None:
            raise ExtractError("olefile 미설치")
        try:
            ole = olefile.OleFileIO(input_path)
            try:
                if not ole.exists("PowerPoint Document"):
                    raise ExtractError("PowerPoint Document 스트림 없음")
                data = ole.openstream("PowerPoint Document").read()
            finally:
                ole.close()
        except ExtractError:
            raise
        except Exception as e:   # noqa: BLE001
            raise ExtractError(f"ppt 열기 실패: {type(e).__name__}")
        out = []
        _ppt_walk(data, out)
        txt = "\n".join(t for t in out if t.strip())
        return txt.replace("\x0b", "\n").replace("\r", "\n")


class XlsExtractor(TextExtractor):
    #------------------------------------------------------------------
    # xls → 텍스트 (핵심)
    #=> 성숙한 xlrd 로 BIFF(97-2003) 워크북을 열어 모든 시트의 셀을 행 단위로 잇는다.
    #
    # -in: input_path = .xls 경로
    # -in: save_dir   = (호환용) 사용 안 함
    #
    # -out: text = 셀 텍스트(탭/개행 구분)
    # -out: error = xlrd 없음/열기 실패 시 ExtractError(→ snf 폴백)
    #------------------------------------------------------------------
    def extract(self, input_path, save_dir=None):
        try:
            import xlrd
        except ImportError:
            raise ExtractError("xlrd 미설치")
        try:
            wb = xlrd.open_workbook(input_path)
        except Exception as e:   # noqa: BLE001
            raise ExtractError(f"xls 열기 실패: {type(e).__name__}")
        out = []
        for sh in wb.sheets():
            for r in range(sh.nrows):
                out.append("\t".join(str(sh.cell_value(r, c)) for c in range(sh.ncols)))
        return "\n".join(out)
