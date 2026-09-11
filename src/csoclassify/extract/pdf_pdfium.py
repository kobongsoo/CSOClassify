#------------------------------------------------------------------
# PDF 추출기 — pypdfium2 (설계서 CSO_HybridParse)
#=> PDF 텍스트층을 pdfium(구글 PDF 엔진) 바인딩으로 뽑는다. 순수 네이티브라
#   빠르고(실측 snf 2배·xberg 40배), 한글 PDF 읽기순서도 snf 와 사실상 동일
#   (실측 sim 0.96~1.00). manylinux2014(glibc 2.17) wheel 이 있어 CentOS 7 에서도
#   동작한다(xberg 가 못 하던 것). 스캔(이미지) PDF 는 텍스트층이 없어 빈 결과 →
#   상위가 snf 로 폴백한다(OCR 은 별도).
#------------------------------------------------------------------

from .. import config
from ..logsetup import get_logger
from . import notes
from .base import TextExtractor, ExtractError, ParserDeadline

log = get_logger(__name__)

# pypdfium2 가 없으면 이 경로만 비활성(→ snf 폴백)되게 지연 취급.
try:
    import pypdfium2 as pdfium
except ImportError:
    pdfium = None


class PdfiumExtractor(TextExtractor):
    #------------------------------------------------------------------
    # 문서 → 원시 텍스트 (핵심)
    #=> PDF 를 열어 페이지마다 텍스트층을 뽑아 이어 붙인다.
    #    1) pypdfium2 없으면 실패(→ snf 폴백)
    #    2) 페이지마다 get_textpage().get_text_range() 로 본문 추출
    #       (G5: 페이지 수·시간 상한을 넘으면 거기서 멈추고 읽은 만큼만 쓴다)
    #    3) 페이지들을 개행으로 결합(빈 결과면 상위가 폴백 판단)
    #   ※ save_dir 저장은 상위(HybridExtractor)가 승자 텍스트만 한 번 처리한다.
    #
    # -in: input_path = 추출할 .pdf 경로
    # -in: save_dir   = (호환용) 여기서는 사용 안 함
    #
    # -out: text = 추출 본문(텍스트층 없으면 빈 문자열 → snf 폴백)
    # -out: error = pypdfium2 없음/열기 실패(손상·암호화) 시 ExtractError
    #------------------------------------------------------------------
    def extract(self, input_path, save_dir=None):
        if pdfium is None:
            raise ExtractError("pypdfium2 미설치(PDF 파서 비활성)")
        try:
            doc = pdfium.PdfDocument(input_path)
        except Exception as e:   # noqa: BLE001  (손상/암호화 PDF 등)
            raise ExtractError(f"PDF 열기 실패: {type(e).__name__}")
        try:
            parts = []
            # 페이지·시간 상한(G5) — 설계: plan/문서크기-상한-설계-20260904.html
            # 지금까지 이 루프에는 아무 제한이 없어, 수천 페이지 PDF 한 건이
            # 배치 전체를 무한정 붙잡을 수 있었다. 두 겹으로 막는다:
            #   ① 시간 예산(B안) — 실제로 오래 걸리는 것을 직접 막는다
            #   ② 페이지 수(A안) — 시간이 널널해도 터무니없는 분량은 자른다
            # 넘겨도 예외를 던지지 않고 '그때까지 읽은 부분'을 돌려준다. 부분
            # 텍스트라도 등급 판정에는 대개 충분하고, 예외로 버리면 그 문서가
            # 미분류로 쌓이기 때문이다(base.ParserDeadline 주석 참고).
            deadline = ParserDeadline()
            max_pages = config.MAX_PDF_PAGES
            stopped = None
            for i, page in enumerate(doc):
                if max_pages and 0 < max_pages <= i:
                    stopped = f"페이지 상한({max_pages}p)"
                    break
                if deadline.expired():
                    stopped = f"시간 상한({deadline.seconds}s)"
                    break
                # 페이지 텍스트층에서 전체 범위 문자열을 얻는다.
                tp = page.get_textpage()
                parts.append(tp.get_text_range())
            # 잘렸다면 반드시 로그로 남긴다 — 남기지 않으면 '왜 이 문서만 본문이
            # 적지'를 나중에 추적할 근거가 사라진다.
            if stopped:
                log.warning("PDF 부분 추출 %s 에서 중단 file=%s pages=%d/%d",
                            stopped, input_path, len(parts), len(doc))
                # 로그만으로는 결과를 보는 사람에게 안 닿는다 — 레코드까지 가도록
                # 표식을 남긴다(notes 모듈 주석 참고).
                notes.set_partial(stopped, "pages", len(parts), len(doc))
            return "\n".join(parts)
        finally:
            doc.close()
