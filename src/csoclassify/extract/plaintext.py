#------------------------------------------------------------------
# 텍스트 파일 추출기 (txt/md/csv/json/ipynb/py 등)
#=> 이미 "텍스트"인 파일은 파서가 필요 없다 — 인코딩만 맞춰 그대로 읽는다.
#   한국어 환경을 고려해 UTF-8(BOM 포함) → CP949 → Latin-1 순으로 시도한다.
#   (감지기가 내용상 text 로 판정한 파일만 이 추출기로 온다.)
#------------------------------------------------------------------

from .base import TextExtractor, ExtractError

# 시도할 인코딩 순서: BOM 있는 UTF-8 → UTF-8 → 윈도우 한글(CP949) → 마지막 보루.
_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "latin-1")


class PlainTextExtractor(TextExtractor):
    #------------------------------------------------------------------
    # 문서 → 원시 텍스트 (핵심)
    #=> 텍스트 파일을 바이트로 읽어 인코딩을 차례로 시도해 문자열로 만든다.
    #    1) 파일이 없으면 실패
    #    2) utf-8-sig→utf-8→cp949 순으로 엄격 디코드 시도(성공하면 채택)
    #    3) 다 실패하면 latin-1 로 손실 없이(1:1) 디코드(항상 성공)
    #   ※ save_dir 저장은 상위(HybridExtractor)가 처리한다.
    #
    # -in: input_path = 텍스트 파일 경로
    # -in: save_dir   = (호환용) 여기서는 사용 안 함
    #
    # -out: text = 파일 내용 문자열
    # -out: error = 파일 없음/읽기 실패 시 ExtractError
    #------------------------------------------------------------------
    def extract(self, input_path, save_dir=None):
        try:
            with open(input_path, "rb") as f:
                raw = f.read()
        except OSError as e:
            raise ExtractError(f"텍스트 읽기 실패: {type(e).__name__}")
        # 엄격 디코드를 앞 인코딩부터 시도 — 처음 성공하는 것이 실제 인코딩일 확률이 높다.
        for enc in _ENCODINGS[:-1]:
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        # 모두 실패하면 latin-1(바이트 1:1 매핑)로 디코드해 최소한 내용은 건진다.
        return raw.decode("latin-1", errors="replace")
