#------------------------------------------------------------------
# PDF 추출기 — pypdfium2 (설계서 CSO_HybridParse)
#=> PDF 텍스트층을 pdfium(구글 PDF 엔진) 바인딩으로 뽑는다. 순수 네이티브라
#   빠르고(실측 snf 2배·xberg 40배), 한글 PDF 읽기순서도 snf 와 사실상 동일
#   (실측 sim 0.96~1.00). manylinux2014(glibc 2.17) wheel 이 있어 CentOS 7 에서도
#   동작한다(xberg 가 못 하던 것). 스캔(이미지) PDF 는 텍스트층이 없어 빈 결과 →
#   상위가 snf 로 폴백한다(OCR 은 별도).
#------------------------------------------------------------------

from .base import TextExtractor, ExtractError

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
            for page in doc:
                # 페이지 텍스트층에서 전체 범위 문자열을 얻는다.
                tp = page.get_textpage()
                parts.append(tp.get_text_range())
            return "\n".join(parts)
        finally:
            doc.close()
