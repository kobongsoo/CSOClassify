#------------------------------------------------------------------
# 텍스트 추출기 공통 인터페이스
#=> "문서 파일 경로를 주면 본문 텍스트(str)를 돌려준다"는 약속만 정의한다.
#   지금은 사이냅 exe 구현 하나지만, 나중에 DLL(ctypes) 방식으로 바꿔도
#   이 인터페이스만 지키면 상위 코드는 손댈 필요가 없다.
#------------------------------------------------------------------

import os
from abc import ABC, abstractmethod


#------------------------------------------------------------------
# 추출 텍스트를 보존 폴더에 저장 (하이브리드 공용)
#=> 추출에 성공한 "승자 엔진"의 텍스트를 save_dir 에 파일로 남긴다. 하이브리드
#   추출기는 하위 엔진을 save_dir=None 으로 호출해 성공 여부를 먼저 판정하고,
#   이긴 엔진의 텍스트만 이 함수로 한 번 저장한다(엔진마다 중복 저장 방지).
#
#   [2026-09-07 파일명이 바뀌었다] 예전에는 '원본명.txt', 충돌하면 '원본명__2.txt'
#   였다. 그런데 __2 가 어느 문서인지 결과만 보고는 되짚을 수가 없어서, 저장은
#   되는데 쓸 수가 없었다. 이제 원본 파일의 SHA-256 을 이름으로 삼는다 —
#   결과 레코드의 hash 칸과 그대로 이어지고, 같은 내용은 한 파일이 된다.
#   설계: plan/추출텍스트-저장-설계-20260907.html
#
# -in: save_dir   = 저장할 폴더
# -in: input_path = 원본 문서 경로(해시를 뜨는 대상이자 색인에 남길 경로)
# -in: text       = 저장할 추출 텍스트(UTF-8)
#
# -out: path = 실제로 저장된 파일 경로
# -out: error = 폴더 생성/쓰기 실패 시 OSError 전파
#------------------------------------------------------------------
def save_extracted_text(save_dir, input_path, text):
    import hashlib
    from .. import textsave

    # 폴더 준비(안내문 포함) — 분류 경로와 같은 모양의 폴더가 되게 한다.
    save_dir = textsave.prepare(save_dir)
    # 이름의 근거는 '원본 파일 내용'이다. 분류 경로가 레코드에 싣는 hash 와 같은 값.
    h = hashlib.sha256()
    with open(input_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    sha = h.hexdigest()
    name, _deduped = textsave.save(save_dir, sha, text)
    # 이 경로(--text-only·--embed)는 doc_id·절단 여부를 모른다 — 모르는 것을
    # 아는 척하지 않고 None/False 로 남긴다.
    textsave.index_append(save_dir, sha, name, input_path, None,
                          len(text), False, _now_iso())
    return os.path.join(save_dir, name)


#------------------------------------------------------------------
# 색인에 적을 시각 문자열
#=> 결과 레코드의 ts 와 같은 모양(초 단위 + 로컬 타임존)이어야 나중에 두 파일을
#   나란히 놓고 볼 수 있다.
#
# -in: 없음
# -out: str = 예) "2026-09-07T15:02:11+09:00"
# -out: error = 없음
#------------------------------------------------------------------
def _now_iso():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


#------------------------------------------------------------------
# 추출 실패 예외
#=> 파일이 없거나, 추출 산출물이 비었거나, 타임아웃 등 "텍스트를 못 얻은"
#   모든 경우를 하나의 예외로 묶어 상위(파이프라인)가 일관되게 처리하게 한다.
#
# -필드: (메시지 문자열만 사용)
#------------------------------------------------------------------
class ExtractError(Exception):
    pass


#------------------------------------------------------------------
# 바이트 수 → 사람이 읽는 크기 문자열
#=> 상한 초과 사유 문구에 쓴다. MB 로만 찍으면 작은 값이 전부 "0MB" 로 뭉개져
#   "0MB > 0MB 초과" 같은 아무 정보 없는 문장이 된다(실측에서 실제로 나왔다).
#   그래서 크기대에 맞춰 단위를 바꾼다.
#
# -in: n = 바이트 수
#
# -out: str = "328.8MB" · "512.0KB" · "900B" 처럼 읽히는 문자열
# -out: error = 없음
#------------------------------------------------------------------
def human_bytes(n):
    n = float(n or 0)
    # 1MB 이상은 MB 로, 1KB 이상은 KB 로, 그보다 작으면 바이트 그대로 보여 준다.
    if n >= 1048576:
        return f"{n / 1048576:.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.1f}KB"
    return f"{int(n)}B"


#------------------------------------------------------------------
# 크기 상한 초과 예외 (G1·G2)
#=> "너무 커서 안 읽었다"를 "깨져서 못 읽었다"와 구분하려고 만든 예외다.
#   ExtractError 를 물려받으므로 기존 처리 경로는 손대지 않아도 그대로 동작하고,
#   구분이 필요한 곳(결과 레코드의 error_kind)만 isinstance 로 갈라 본다.
#
#   [왜 구분하나] 운영 대응이 다르다. 깨진 파일은 사람이 원본을 고쳐야 하지만,
#   크기 초과는 상한을 올리거나 비동기 큐로 넘기면 그대로 처리되는 정상 문서다.
#
# -필드: (메시지 문자열만 사용)
#------------------------------------------------------------------
class SizeLimitError(ExtractError):
    pass


#------------------------------------------------------------------
# 원본 파일 크기 상한 검사 (G1·G2) — 설계: plan/문서크기-상한-설계-20260904.html
#=> 어떤 파서를 부르기 '전에' 원본 바이트 수를 보고, 상한을 넘으면 아예 읽지
#   않는다. 파서를 부른 뒤에 막으면 이미 메모리를 다 쓴 뒤라 의미가 없다.
#    1) 크기를 잰다(못 재면 통과 — 상한 때문에 정상 파일을 잃으면 안 된다)
#    2) 텍스트 계열이면 낮은 상한(G2), 아니면 일반 상한(G1)을 고른다
#    3) 넘으면 SizeLimitError
#
#   [왜 텍스트 계열만 상한이 다른가] txt/csv/tsv/json/html 은 '바이트 수 = 글자
#   수'라 바이트가 비용을 정확히 대변한다. 반대로 pdf·pptx 는 이미지가 대부분을
#   차지해 바이트와 본문 길이가 따로 논다(실측: 55.3MB PDF 의 본문이 343자).
#   그래서 한 값으로 묶으면 둘 중 하나는 반드시 틀린다.
#
# -in: input_path = 검사할 원본 경로
# -in: ftype      = 이미 아는 감지 포맷(없으면 여기서 detect_format 으로 판별)
# -in: max_bytes  = 일반 상한 덮어쓰기(None 이면 config.MAX_FILE_BYTES)
# -in: max_bytes_text = 텍스트 계열 상한 덮어쓰기(None 이면 config.MAX_FILE_BYTES_TEXT)
#
# -out: 없음(통과하면 조용히 반환)
# -out: error = 상한 초과 시 SizeLimitError. 크기를 못 재면 예외 없이 통과
#------------------------------------------------------------------
def check_size_limit(input_path, ftype=None, max_bytes=None, max_bytes_text=None):
    from .. import config

    # (1) 크기를 못 재는 상황(권한·경쟁 삭제 등)에서 막아 버리면, 상한과 무관한
    #     이유로 정상 문서를 잃는다. 그런 판단은 뒤의 파서에게 맡긴다.
    try:
        size = os.path.getsize(input_path)
    except OSError:
        return

    # (2) 포맷을 모르면 여기서 판별한다(앞 512바이트만 읽어 값이 싸다).
    #     하이브리드는 이미 판별한 값을 넘겨 주므로 중복 판별이 없다.
    if ftype is None:
        try:
            from .detect import detect_format
            ftype = detect_format(input_path)
        except Exception:   # noqa: BLE001  판별 실패는 상한 판단을 막지 않는다
            ftype = None

    if ftype in ("text", "html"):
        limit = config.MAX_FILE_BYTES_TEXT if max_bytes_text is None else max_bytes_text
        which = "MAX_FILE_BYTES_TEXT"
    else:
        limit = config.MAX_FILE_BYTES if max_bytes is None else max_bytes
        which = "MAX_FILE_BYTES"

    # 0 이하는 '상한 없음'으로 읽는다(--no-size-limit 이 이 값을 0 으로 넘긴다).
    if not limit or limit <= 0 or size <= limit:
        return

    # (3) 사람이 바로 판단할 수 있게 실제 크기·상한·어느 상한인지를 함께 담는다.
    raise SizeLimitError(
        f"크기 상한 초과: {human_bytes(size)} > {human_bytes(limit)} ({which})")


#------------------------------------------------------------------
# 파서 시간 예산 (G5)
#=> "이 파일에 쓸 수 있는 시간"을 들고 다니며, 페이지·시트 루프가 한 바퀴 돌
#   때마다 expired() 로 물어보게 한다. 예산을 넘기면 루프를 멈추고 그때까지
#   모은 텍스트만 돌려준다 — 예외를 던지지 않는다.
#
#   [왜 예외가 아닌가] 3,000페이지 중 800페이지를 읽었다면 그 800페이지로도
#   등급 판정은 대개 정확하다. 예외로 버리면 그 문서는 '미분류'가 되어 사람이
#   손봐야 할 목록에 쌓인다. 부분 결과가 미분류보다 낫다.
#   [왜 필요한가] snf 는 subprocess 라 timeout= 한 줄로 막히지만, 기본 경로인
#   자체 파서는 in-process 호출이라 지금까지 아무 시간 방어가 없었다.
#
# -필드: seconds  = 허용 시간(초). None/0 이하면 무제한
# -필드: _started = 생성 시각(perf_counter 기준)
#------------------------------------------------------------------
class ParserDeadline:
    #------------------------------------------------------------------
    # 생성자 — 시계를 지금 시작한다
    #=> 파서가 실제 일을 시작하기 직전에 만들어야 측정이 정확하다.
    #
    # -in: seconds = 허용 시간(초). None 이면 config.PARSER_TIMEOUT 을 쓴다
    #
    # -out: 없음
    # -out: error = 없음
    #------------------------------------------------------------------
    def __init__(self, seconds=None):
        import time
        from .. import config
        self.seconds = config.PARSER_TIMEOUT if seconds is None else seconds
        self._started = time.perf_counter()

    #------------------------------------------------------------------
    # 예산을 다 썼나
    #=> 루프 안에서 매 반복 호출한다. 무제한 설정이면 항상 False.
    #
    # -in: 없음
    #
    # -out: bool = True 면 시간 초과(호출부가 루프를 멈춰야 한다)
    # -out: error = 없음
    #------------------------------------------------------------------
    def expired(self):
        # 0 이하는 '시간 제한 없음' — 조사·디버깅 때 상한을 끄는 통로다.
        if not self.seconds or self.seconds <= 0:
            return False
        import time
        return (time.perf_counter() - self._started) >= self.seconds


#------------------------------------------------------------------
# 텍스트 추출기 추상 클래스
#=> 모든 추출기가 구현해야 할 메서드(extract)를 강제한다.
#
# -필드: 없음(순수 인터페이스)
#------------------------------------------------------------------
class TextExtractor(ABC):
    #------------------------------------------------------------------
    # 문서 → 원시 텍스트
    #=> 입력 문서 경로를 받아 추출된 UTF-8 텍스트(정제 전)를 반환한다.
    #   임시파일 보존 여부(save_dir)는 구현체가 해석한다.
    #
    # -in: input_path = 추출할 문서의 절대/상대 경로
    # -in: save_dir   = 추출 텍스트를 남길 폴더(None 이면 임시파일 자동삭제)
    #
    # -out: text = 추출된 원시 텍스트
    # -out: error = 실패 시 ExtractError
    #------------------------------------------------------------------
    @abstractmethod
    def extract(self, input_path, save_dir=None):
        raise NotImplementedError
