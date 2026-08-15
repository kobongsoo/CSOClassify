#------------------------------------------------------------------
# 전역 설정/기본값 모음
#=> 모델 목록, 청킹 기본값, 데몬 기본값 등 "숫자·문자열 상수"를 한 곳에 모아
#   코드 곳곳에 흩어지지 않게 한다. 여기 값만 바꾸면 전체 동작 기본값이 바뀐다.
#------------------------------------------------------------------

from dataclasses import dataclass


#------------------------------------------------------------------
# 임베딩 모델 1개의 메타정보
#=> "이름(별칭) → 실제 폴더/HF 저장소/차원/프리픽스 규약"을 묶어 둔 표의 한 행.
#   e5 계열은 입력 앞에 'passage: ' / 'query: ' 를 붙여야 성능이 나오므로 그 규약도 담는다.
#
# -필드: key        = CLI --model 에서 쓰는 짧은 별칭 (예: "e5-small-ko")
# -필드: hf_id      = 원본 HuggingFace 저장소 id (ONNX 변환 시 사용)
# -필드: local_dir  = resources/models 아래 폴더명 (model.onnx, tokenizer.json 위치)
# -필드: dim        = 출력 벡터 차원
# -필드: passage_prefix = 문서(임베딩 대상)에 붙일 접두어 (없으면 빈 문자열)
# -필드: query_prefix   = 질의에 붙일 접두어 (본 도구는 문서용이라 참고용)
# -필드: max_tokens_model = 모델이 받을 수 있는 최대 토큰 길이(안전 상한)
#------------------------------------------------------------------
@dataclass(frozen=True)
class ModelSpec:
    key: str
    hf_id: str
    local_dir: str
    dim: int
    passage_prefix: str
    query_prefix: str
    max_tokens_model: int


# 지원 모델 레지스트리(별칭 → 스펙). 기본은 소형+한국어인 e5-small-ko.
MODELS = {
    "e5-small-ko": ModelSpec(
        key="e5-small-ko",
        hf_id="dragonkue/multilingual-e5-small-ko",
        local_dir="e5-small-ko",
        dim=384,
        passage_prefix="passage: ",
        query_prefix="query: ",
        max_tokens_model=512,
    ),
    "e5-small": ModelSpec(
        key="e5-small",
        hf_id="intfloat/multilingual-e5-small",
        local_dir="e5-small",
        dim=384,
        passage_prefix="passage: ",
        query_prefix="query: ",
        max_tokens_model=512,
    ),
    "ko-sroberta": ModelSpec(
        key="ko-sroberta",
        hf_id="jhgan/ko-sroberta-multitask",
        local_dir="ko-sroberta",
        dim=768,
        passage_prefix="",   # sroberta 계열은 프리픽스 규약 없음
        query_prefix="",
        max_tokens_model=512,
    ),
}

# ── 기본값들 ─────────────────────────────────────────────
DEFAULT_MODEL = "e5-small-ko"    # 기본 임베딩 모델 별칭
DEFAULT_MAX_TOKENS = 512         # 청크 1개의 최대 토큰 수(e5 계열 모델 최대 시퀀스 길이)
DEFAULT_OVERLAP = 32             # 인접 청크가 겹치는 토큰 수
DEFAULT_NORMALIZE = True         # 출력 벡터 L2 정규화 여부
DEFAULT_FORMAT = "json"          # 출력 형식(text/json/jsonl) — 기본 JSON(사람이 보기 좋은 들여쓰기)
DEFAULT_PRECISION = "f32"        # 출력 벡터 정밀도(f32/f16)

# 데몬 관련 기본값
DEFAULT_DAEMON = True            # 상주 데몬 모드 기본 on (설계서 결정5)
DEFAULT_IDLE_TIMEOUT = 600       # 유휴 자동 종료(초)
DAEMON_HOST = "127.0.0.1"        # 데몬은 반드시 loopback 에만 바인딩(외부 노출 금지)
CLIENT_CONNECT_TIMEOUT = 0.3     # 데몬 접속/PING 타임아웃(초)
DAEMON_START_WAIT = 20.0         # 데몬 자동기동 후 준비 대기 최대(초)

# 사이냅 추출 관련
SNF_TIMEOUT = 60                 # snf_exe 1파일 처리 타임아웃(초)
MIN_TEXT_LEN_WARN = 5            # 추출 텍스트가 이보다 짧으면 경고(손상파일 의심)

# 종료 코드(설계서 §7)
EXIT_OK = 0
EXIT_EXTRACT_FAIL = 1
EXIT_EMBED_FAIL = 2
EXIT_ARG_ERROR = 3


#------------------------------------------------------------------
# 모델 별칭 → 스펙 조회
#=> 사용자가 준 --model 별칭으로 실제 모델 스펙을 찾아 준다.
#   없는 별칭이면 어떤 별칭이 가능한지 알려주는 친절한 에러를 낸다.
#
# -in: name = 모델 별칭 (예: "e5-small-ko")
#
# -out: ModelSpec = 해당 모델 메타정보
# -out: error = 등록되지 않은 별칭이면 KeyError
#------------------------------------------------------------------
def get_model_spec(name):
    # 등록 표에 없으면, 쓸 수 있는 이름 목록을 붙여 알려 준다.
    if name not in MODELS:
        avail = ", ".join(MODELS.keys())
        raise KeyError(f"알 수 없는 모델 '{name}'. 사용 가능: {avail}")
    return MODELS[name]
