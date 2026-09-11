#------------------------------------------------------------------
# 현대 Office(OOXML) 추출기 — docx/xlsx/pptx  (stdlib 전용, 무의존)
#=> 2007+ Office(docx/xlsx/pptx) 는 ZIP 컨테이너 안에 XML(OOXML)로 본문을 담는다.
#   과거엔 python-docx/openpyxl/python-pptx 로 파싱했으나, 이들은 텍스트만 필요한
#   우리 용도엔 과하고 PIL(13MB)·lxml(7MB) 같은 대형 의존을 끌어와 배포 용량을 키웠다.
#   여기서는 HWPX 추출기(hwpx_zip.py)와 동일하게 파이썬 표준 zipfile + ElementTree
#   만으로 본문 텍스트를 뽑는다 → 외부 의존 0, 배포 ~23MB 감량, 크로스플랫폼(CentOS7).
#   텍스트만 뽑으므로 서식은 버리며, 실측상 snf/기존 라이브러리와 본문 sim≈1.0.
#------------------------------------------------------------------

import re
import zipfile
from xml.etree import ElementTree as ET

from ..logsetup import get_logger
from . import notes
from .base import TextExtractor, ExtractError, ParserDeadline

log = get_logger(__name__)

# 본문 section/slide/sheet 파일명 매칭용(번호 정렬에 사용).
_PPTX_SLIDE = re.compile(r"ppt/slides/slide(\d+)\.xml$", re.I)
_XLSX_SHEET = re.compile(r"xl/worksheets/sheet(\d+)\.xml$", re.I)


#------------------------------------------------------------------
# 네임스페이스 접두 제거(로컬 태그명만)
#=> OOXML 태그는 '{긴URL}w:t' 처럼 네임스페이스가 붙는다. 우리는 태그의 '지역명'
#   (예: 't','p','c','v')만 필요하므로 '}' 뒤만 남긴다. 네임스페이스 버전 차이에
#   흔들리지 않게 하려는 목적.
#
# -in: tag = ElementTree 요소의 tag 문자열(예: "{...}t")
#
# -out: str = 지역 태그명(예: "t")
# -out: error = 없음
#------------------------------------------------------------------
def _local(tag):
    # '}' 가 있으면 그 뒤가 지역명, 없으면(네임스페이스 없음) 원본 그대로.
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


#------------------------------------------------------------------
# 파일명 속 숫자로 정렬키
#=> "slide10.xml" 이 "slide2.xml" 보다 뒤에 오도록 숫자를 뽑아 정수 비교한다
#   (문자열 정렬이면 10 이 2 앞에 오는 오류 방지).
#
# -in: name = zip 내부 항목 이름
# -in: rx   = 숫자를 뽑을 정규식(그룹1이 번호)
#
# -out: int = 번호(못 찾으면 0)
# -out: error = 없음
#------------------------------------------------------------------
def _num_key(name, rx):
    m = rx.search(name)
    return int(m.group(1)) if m else 0


class DocxExtractor(TextExtractor):
    #------------------------------------------------------------------
    # docx → 텍스트 (핵심)
    #=> word/document.xml 을 파싱해 문단(<w:p>) 단위로 텍스트 런(<w:t>)을 모은다.
    #    1) zip 에서 word/document.xml 을 읽어 XML 파싱
    #    2) 문서 순서대로 <w:p> 를 돌며, 그 문단의 <w:t> 텍스트를 이어 한 줄로
    #       (표 셀도 내부가 <w:p> 라 같은 순회로 자연스럽게 포함된다)
    #    3) 탭(<w:tab>)은 '\t' 로 반영해 표/열 구분을 살린다
    #
    #    4) 본문 뒤에 머리말·꼬리말(word/header*.xml · word/footer*.xml)을 잇는다
    #
    #   [왜 머리말·꼬리말까지 읽나] 이 도구가 찾는 신호가 바로 거기 있다 —
    #   '대외비' 스탬프와 회사명은 본문이 아니라 머리말·꼬리말에 찍히는 일이
    #   훨씬 흔하다. 안 읽으면 그 문서는 스탬프가 없는 것처럼 보인다.
    #   (실측 d:/sample 의 docx 4건 머리말·꼬리말에 회사명이 들어 있었다.)
    #
    #   [왜 본문 뒤인가] 앞 400자(표제부)가 가리키는 범위를 그대로 두기 위해서다.
    #   머리말을 앞에 붙이면 표제부 규칙이 보던 글자가 통째로 밀려, 이 변경과
    #   상관없는 문서의 판정까지 흔들린다. Rust 판 docx_text 와 같은 차례다.
    #
    # -in: input_path = .docx 경로
    # -in: save_dir   = (호환용) 사용 안 함(저장은 상위 HybridExtractor 담당)
    #
    # -out: text = 본문 텍스트(문단 개행 구분) + 머리말·꼬리말
    # -out: error = 파일없음/ZIP아님/XML손상 시 ExtractError(→ snf 폴백)
    #------------------------------------------------------------------
    def extract(self, input_path, save_dir=None):
        try:
            with zipfile.ZipFile(input_path) as z:
                # 본문 XML. 없으면 docx 로 볼 수 없음 → 실패로 폴백.
                data = z.read("word/document.xml")
                # 머리말·꼬리말은 header1.xml, footer2.xml 처럼 번호가 붙어 이름이
                # 고정이 아니다. 목록을 훑어 모으고, 이름순으로 정렬해 실행마다
                # 같은 차례가 되게 한다(Rust 판 extras.sort() 와 같은 기준).
                extras = sorted(
                    n for n in z.namelist()
                    if n.endswith(".xml")
                    and (n.startswith("word/header") or n.startswith("word/footer")))
                extra_data = [z.read(n) for n in extras]
        except FileNotFoundError:
            raise ExtractError("입력 파일 없음")
        except KeyError:
            raise ExtractError("docx 본문(word/document.xml) 없음")
        except (zipfile.BadZipFile, OSError) as e:   # noqa: BLE001
            raise ExtractError(f"docx 열기 실패: {type(e).__name__}")
        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            raise ExtractError(f"docx XML 파싱 실패: {type(e).__name__}")

        lines = []
        # 문서 순서대로 문단을 돈다. 표 셀 안의 문단도 이 순회에 포함된다.
        self._collect_paragraphs(root, lines)
        # 머리말·꼬리말도 같은 문단 규칙으로 읽는다. 하나가 깨져 있어도 본문까지
        # 버리지는 않는다 — 부속물 때문에 문서 전체를 못 읽는 것이 더 나쁘다.
        for blob in extra_data:
            try:
                self._collect_paragraphs(ET.fromstring(blob), lines)
            except ET.ParseError:
                continue
        return "\n".join(lines)

    #------------------------------------------------------------------
    # 한 XML 트리에서 문단 텍스트를 모은다
    #=> 본문·머리말·꼬리말이 모두 같은 <w:p>/<w:t> 구조라 규칙이 하나뿐이다.
    #   따로 적으면 한쪽만 고쳐져 두 곳이 어긋난다.
    #
    # -in: root  = 파싱된 XML 루트(word/document.xml 또는 header/footer)
    # -in: lines = 결과를 덧붙일 리스트(제자리에서 늘어난다)
    #
    # -out: 없음(lines 에 문단마다 한 줄씩 덧붙인다)
    # -out: error = 없음
    #------------------------------------------------------------------
    @staticmethod
    def _collect_paragraphs(root, lines):
        for p in root.iter():
            if _local(p.tag) != "p":
                continue
            buf = []
            # 이 문단의 '런(<w:r>)' 안에서만 텍스트와 탭을 순서대로 모은다.
            #
            # [왜 런 안으로 좁혔나] 예전에는 문단 전체를 p.iter() 로 훑으며 이름이
            # 'tab' 인 것을 모두 탭 문자로 셌다. 그런데 <w:pPr><w:tabs> 안의 <w:tab>
            # 은 **탭 정지 위치 '정의'** 이지 본문의 탭 문자가 아니다. 실측에서 한
            # 문단이 그 정의를 32개 갖고 있었고, 그것이 탭 32개로 새어 들어가
            # 정제 단계에서 공백 하나로 뭉쳐 문단 앞에 없던 들여쓰기를 만들었다
            # (' 나. 개정 시행일'). 오류가 안 나서 못 알아챘다.
            # 진짜 탭 문자는 언제나 런 안의 <w:tab/> 다.
            for r in p.iter():
                if _local(r.tag) != "r":
                    continue
                for e in r.iter():
                    ln = _local(e.tag)
                    if ln == "t":
                        buf.append(e.text or "")
                    elif ln == "tab":
                        buf.append("\t")
            lines.append("".join(buf))


class XlsxExtractor(TextExtractor):
    #------------------------------------------------------------------
    # xlsx → 텍스트 (핵심)
    #=> 모든 시트(xl/worksheets/sheet*.xml)의 셀 값을 행 단위로 이어 붙인다.
    #    1) 공유문자열표(xl/sharedStrings.xml)를 먼저 읽어 인덱스→문자열 목록 구성
    #       (엑셀은 반복 문자열을 이 표에 모아두고 셀은 번호만 가진다)
    #    2) 각 시트에서 행(<row>)·셀(<c>)을 돌며 값을 만든다:
    #       · t="s"  → <v> 는 공유문자열 인덱스 → 표에서 실제 문자열로 치환
    #       · t="inlineStr" → <is><t> 안의 인라인 문자열
    #       · 그 외  → <v> 리터럴(숫자 등)
    #    3) 셀은 탭, 행은 개행으로 잇는다
    #
    # -in: input_path = .xlsx 경로
    # -in: save_dir   = (호환용) 사용 안 함
    #
    # -out: text = 셀 텍스트(탭/개행 구분)
    # -out: error = 파일없음/ZIP아님 시 ExtractError(→ snf 폴백)
    #------------------------------------------------------------------
    def extract(self, input_path, save_dir=None):
        try:
            with zipfile.ZipFile(input_path) as z:
                names = z.namelist()
                shared = self._shared_strings(z, names)
                # 시트를 번호순으로 정렬해 통합문서 순서를 유지한다.
                sheets = sorted((n for n in names if _XLSX_SHEET.search(n)),
                                key=lambda n: _num_key(n, _XLSX_SHEET))
                out = []
                # 시간 상한(G5) — 시트가 많거나 한 장이 거대한 통합문서에서 이 루프가
                # 무한정 늘어난다. 넘으면 그때까지 읽은 시트까지만 쓴다(예외 아님).
                deadline = ParserDeadline()
                for i, n in enumerate(sheets):
                    if deadline.expired():
                        log.warning("xlsx 부분 추출 시간 상한(%ss) file=%s sheets=%d/%d",
                                    deadline.seconds, input_path, i, len(sheets))
                        # 결과를 보는 사람에게 닿도록 레코드까지 표식을 보낸다.
                        notes.set_partial(f"시간 상한({deadline.seconds}s)",
                                          "sheets", i, len(sheets))
                        break
                    out.extend(self._read_sheet(z.read(n), shared))
                return "\n".join(out)
        except FileNotFoundError:
            raise ExtractError("입력 파일 없음")
        except (zipfile.BadZipFile, OSError) as e:   # noqa: BLE001
            raise ExtractError(f"xlsx 열기 실패: {type(e).__name__}")

    #------------------------------------------------------------------
    # 공유문자열표 로드
    #=> xl/sharedStrings.xml 을 파싱해 <si>(공유문자열 항목)마다 내부 <t> 텍스트를
    #   모두 이어 하나의 문자열로 만들고, 등장 순서를 인덱스로 하는 리스트를 만든다.
    #
    # -in: z     = 열린 ZipFile
    # -in: names = zip 항목 이름 목록
    #
    # -out: list[str] = 인덱스→문자열(표 없으면 빈 리스트)
    # -out: error = XML 손상 시 빈 리스트(추출은 계속)
    #------------------------------------------------------------------
    def _shared_strings(self, z, names):
        # 공유문자열표가 없는 통합문서(전부 인라인/숫자)도 있으므로 없으면 빈 표.
        if "xl/sharedStrings.xml" not in names:
            return []
        try:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        except ET.ParseError:
            return []
        out = []
        for si in root:
            if _local(si.tag) != "si":
                continue
            # 하나의 <si> 안에 여러 런(<r><t>..)으로 쪼개진 텍스트를 모두 잇는다.
            out.append("".join(e.text or "" for e in si.iter() if _local(e.tag) == "t"))
        return out

    #------------------------------------------------------------------
    # 시트 XML → 행 텍스트 목록
    #=> 한 시트의 <row> 마다 셀(<c>) 값을 만들어 탭으로 이어 한 줄을 만든다.
    #
    # -in: data   = 시트 XML 바이트
    # -in: shared = 공유문자열표(인덱스→문자열)
    #
    # -out: list[str] = 행 텍스트 목록
    # -out: error = XML 손상 시 빈 목록
    #------------------------------------------------------------------
    def _read_sheet(self, data, shared):
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            return []
        rows = []
        for row in root.iter():
            if _local(row.tag) != "row":
                continue
            cells = []
            for c in row:
                if _local(c.tag) != "c":
                    continue
                cells.append(self._cell_value(c, shared))
            rows.append("\t".join(cells))
        return rows

    #------------------------------------------------------------------
    # 셀 하나의 표시 문자열
    #=> 셀 타입(t 속성)에 따라 값을 해석한다. 공유문자열/인라인/리터럴을 구분.
    #
    # -in: c      = 셀 요소(<c>)
    # -in: shared = 공유문자열표
    #
    # -out: str = 셀 표시 문자열(빈 셀이면 "")
    # -out: error = 인덱스 이상/파싱 실패 시 "" (해당 셀만 비움)
    #------------------------------------------------------------------
    def _cell_value(self, c, shared):
        t = c.get("t")
        if t == "s":
            # 공유문자열 참조: <v> 안 숫자가 표의 인덱스.
            for e in c:
                if _local(e.tag) == "v":
                    try:
                        return shared[int(e.text)]
                    except (ValueError, IndexError, TypeError):
                        return ""
            return ""
        if t == "inlineStr":
            # 인라인 문자열: <is> 하위 <t> 들을 모두 잇는다.
            return "".join(e.text or "" for e in c.iter() if _local(e.tag) == "t")
        # 그 외(숫자·불리언·수식 결과 등): <v> 리터럴을 그대로.
        for e in c:
            if _local(e.tag) == "v":
                return e.text or ""
        return ""


class PptxExtractor(TextExtractor):
    #------------------------------------------------------------------
    # pptx → 텍스트 (핵심)
    #=> 모든 슬라이드(ppt/slides/slide*.xml)의 텍스트 런(<a:t>)을 문단(<a:p>) 단위로 모은다.
    #    1) 슬라이드 파일을 번호순 정렬(발표 순서 유지)
    #    2) 각 슬라이드에서 문단(<a:p>)마다 <a:t> 텍스트를 이어 한 줄로
    #       (도형·표 셀의 텍스트도 결국 <a:p>/<a:t> 구조라 같은 순회로 포함)
    #
    # -in: input_path = .pptx 경로
    # -in: save_dir   = (호환용) 사용 안 함
    #
    # -out: text = 슬라이드 본문 텍스트(문단 개행 구분)
    # -out: error = 파일없음/ZIP아님 시 ExtractError(→ snf 폴백)
    #------------------------------------------------------------------
    def extract(self, input_path, save_dir=None):
        try:
            with zipfile.ZipFile(input_path) as z:
                slides = sorted((n for n in z.namelist() if _PPTX_SLIDE.search(n)),
                                key=lambda n: _num_key(n, _PPTX_SLIDE))
                out = []
                # 시간 상한(G5) — 슬라이드가 수천 장인 발표자료에서 이 루프가 무한정
                # 늘어난다. 넘으면 그때까지 읽은 슬라이드까지만 쓴다(예외 아님).
                deadline = ParserDeadline()
                for i, n in enumerate(slides):
                    if deadline.expired():
                        log.warning("pptx 부분 추출 시간 상한(%ss) file=%s slides=%d/%d",
                                    deadline.seconds, input_path, i, len(slides))
                        # 결과를 보는 사람에게 닿도록 레코드까지 표식을 보낸다.
                        notes.set_partial(f"시간 상한({deadline.seconds}s)",
                                          "slides", i, len(slides))
                        break
                    out.extend(self._read_slide(z.read(n)))
                return "\n".join(out)
        except FileNotFoundError:
            raise ExtractError("입력 파일 없음")
        except (zipfile.BadZipFile, OSError) as e:   # noqa: BLE001
            raise ExtractError(f"pptx 열기 실패: {type(e).__name__}")

    #------------------------------------------------------------------
    # 슬라이드 XML → 문단 텍스트 목록
    #=> 한 슬라이드의 문단(<a:p>)마다 텍스트 런(<a:t>)을 이어 한 줄을 만든다.
    #
    # -in: data = 슬라이드 XML 바이트
    #
    # -out: list[str] = 문단 텍스트 목록(빈 문단은 건너뜀)
    # -out: error = XML 손상 시 빈 목록
    #------------------------------------------------------------------
    def _read_slide(self, data):
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            return []
        lines = []
        for p in root.iter():
            if _local(p.tag) != "p":
                continue
            txt = "".join(e.text or "" for e in p.iter() if _local(e.tag) == "t")
            # 빈 문단(장식용 도형 등)은 노이즈라 건너뛴다.
            if txt:
                lines.append(txt)
        return lines
