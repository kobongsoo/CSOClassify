#------------------------------------------------------------------
# 하이브리드 추출기 (설계서 CSO_HybridParse §5·§6)
#=> "파일 내용으로 실제 포맷을 감지"해서 그 포맷 전용 파서로 보낸다. 확장자가
#   틀리거나 위장돼 있어도(.txt 인데 실제 doc 등) 올바른 파서로 라우팅된다.
#   상위 코드는 기존 TextExtractor.extract(path, save_dir) 만 부르므로, 이 클래스를
#   SynapExeExtractor 대신 끼워 넣기만 하면 된다(인터페이스 불변).
#
#   라우팅: detect_format(path) → 그 타입의 전용 파서 1차, 실패하면 snf 폴백.
#     pdf→pypdfium2 · hwp→hwp5 · hwpx→zip/OWPML · doc/ppt→자체 파서 · xls→xlrd
#     · docx/xlsx/pptx→python-* · text→직접읽기 · (ole/zip/binary 등 미지원)→snf
#   snf 는 '폴백 전용'이라 없어도 되며, snf 도 없이 전용 파서가 실패하면 그 파일만
#   미분류(ExtractError)로 둔다(크래시 없음).
#------------------------------------------------------------------

from ..logsetup import get_logger
from .base import TextExtractor, ExtractError, save_extracted_text
from .detect import detect_format

log = get_logger(__name__)


class HybridExtractor(TextExtractor):
    #------------------------------------------------------------------
    # 생성자 — 감지타입→파서 표 + snf 폴백 주입
    #=> engines 는 {"pdf": PdfiumExtractor(), "hwp": Hwp5Extractor(), …} 처럼
    #   감지 타입(detect_format 반환값)을 키로 하는 파서 표다. snf 는 폴백 전용
    #   (없으면 전용 파서 실패 시 그 파일만 미분류).
    #
    # -in: engines  = {감지타입: TextExtractor} 표
    # -in: snf      = SynapExeExtractor 또는 None(폴백 전용)
    # -in: detector = 포맷 감지 함수(기본 detect_format; 테스트 주입용)
    #
    # -out: 없음
    # -out: error = 없음
    #------------------------------------------------------------------
    def __init__(self, engines=None, snf=None, detector=detect_format):
        self.engines = dict(engines or {})
        self.snf = snf
        self.detect = detector

    #------------------------------------------------------------------
    # 감지타입 → 시도할 엔진 사슬
    #=> 내용으로 포맷을 감지하고, 그 타입 전용 파서를 1차로, snf 를 폴백으로 붙인다.
    #   전용 파서가 없거나(미지원 타입) snf 만 있으면 그것만, 둘 다 없으면 빈 사슬.
    #
    # -in: input_path = 대상 파일 경로
    #
    # -out: (ftype, chain) = 감지타입, [(엔진이름, 추출기), ...] 시도 순서
    # -out: error = 없음
    #------------------------------------------------------------------
    def _route(self, input_path):
        # detext_format(input_path) 호출
        # => 512만큼 읽어서 파일 포멧(.doc,.hwp,.ppt 등등) 확인함
        ftype = self.detect(input_path) 
        chain = []
        eng = self.engines.get(ftype)
        if eng is not None:
            chain.append((ftype, eng))
        # snf 는 폴백 전용으로 맨 뒤에(있을 때만).
        if self.snf is not None:
            chain.append(("snf", self.snf))
        return ftype, chain

    #------------------------------------------------------------------
    # 문서 → 원시 텍스트 (핵심)
    #=> 감지타입으로 엔진 사슬을 정해 순서대로 시도하고, 본문이 나온 첫 엔진을 채택한다.
    #    1) 감지 → 사슬 구성(비면 즉시 실패=미분류)
    #    2) 각 엔진을 save_dir=None 으로 호출해 성공 판정(예외/0자 → 다음 엔진)
    #    3) 본문 있으면 승자 확정: 폴백이면 로그로 남기고, save_dir 있으면 저장
    #    4) 전 엔진 실패면 ExtractError(현행 snf 단독과 동일한 '진짜 실패')
    #
    # -in: input_path = 추출할 문서 경로
    # -in: save_dir   = 추출 텍스트 보존 폴더(None 이면 저장 안 함)
    #
    # -out: text = 채택된 추출 텍스트
    # -out: error = 전 엔진 실패/사슬 없음 시 ExtractError
    #------------------------------------------------------------------
    def extract(self, input_path, save_dir=None):
        # _route 호출
        # => 내부적으로 detect_format() 호출해서 문서포멧 얻어옴.
        ftype, chain = self._route(input_path)
        # 시도할 엔진이 하나도 없으면(전용 엔진도 snf 도 없음) 그 파일은 미분류.
        if not chain:
            raise ExtractError(f"사용 가능한 추출기 없음(감지={ftype})")
        primary = chain[0][0]
        reasons = []

        for name, engine in chain:
            try:
                text = engine.extract(input_path, save_dir=None)
            except ExtractError as e:
                reasons.append(f"{name}:{e}")
                continue
            except Exception as e:   # noqa: BLE001  (예상 못한 예외도 폴백)
                reasons.append(f"{name}:{type(e).__name__}")
                log.warning("추출 엔진 예외 engine=%s file=%s :: %s", name, input_path, e)
                continue

            if not text or not text.strip():
                reasons.append(f"{name}:empty")
                continue

            if name != primary:
                log.info("추출 폴백 from=%s to=%s file=%s reason=%s",
                         primary, name, input_path, ";".join(reasons))
            else:
                log.info("추출 engine=%s(감지=%s) file=%s chars=%d",
                         name, ftype, input_path, len(text))
            if save_dir:
                save_extracted_text(save_dir, input_path, text)
            return text

        raise ExtractError(f"하이브리드 전엔진 실패(감지={ftype}; " + "; ".join(reasons) + ")")
