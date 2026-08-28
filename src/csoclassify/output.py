#------------------------------------------------------------------
# 결과 출력 포맷터 (설계서 §7)
#=> 파이프라인이 만든 결과 dict 를 text/json/jsonl 형식의 문자열로 바꾼다.
#   벡터는 stdout, 시간 요약은 stderr 로 분리해 파이프(stdout 파싱)를 안전하게 둔다.
#------------------------------------------------------------------

import json


#------------------------------------------------------------------
# 안전한 JSON 직렬화 (마지막 방어선)
#=> json.dumps 를 하되, 결과에 'UTF-8 로 쓸 수 없는 글자'가 섞여 있으면 그 자리를
#   U+FFFD 로 바꿔 돌려준다. 리눅스에서 파일 이름은 그냥 바이트열이라 UTF-8 이
#   아닐 수 있고(윈도우에서 만든 CP949 이름을 복사한 경우 등), 파이썬은 그런
#   이름을 읽을 때 짝 없는 대리 문자(\udcXX)를 끼워 넣는다.
#   [왜 dumps 를 try 로 감싸지 않나] json.dumps 는 str 을 만들 뿐 인코딩을 하지
#   않아서 여기서는 예외가 나지 않는다. 실제 예외는 그 문자열을 파일에 write 할
#   때(파일 객체의 UTF-8 인코더에서) 터진다 — 그래서 '만든 문자열'을 검사한다.
#    1) 만든 문자열을 한 번 encode 해 본다(정상이면 그대로 반환)
#    2) 실패하면 surrogateescape 로 원래 바이트를 되살린 뒤 replace 로 다시 읽어,
#       못 읽는 자리만 U+FFFD 로 바꾼다(JSON 구조는 그대로 유지된다)
#   보통은 cli.safe_text 가 레코드에 들어가기 전에 걸러 주지만, 앞으로 어떤 경로가
#   추가되더라도 '파일 하나 때문에 배치 전체가 죽는' 일만은 없게 하려고 여기서도 막는다.
#
# -in: rec = 직렬화할 값(dict 등)
# -in: kw  = json.dumps 에 그대로 넘길 인자(indent·separators 등)
#
# -out: str = 파일에 그대로 써도 안전한 JSON 문자열
# -out: error = 없음(인코딩 불가 문자는 U+FFFD 로 대체)
#------------------------------------------------------------------
def dumps_safe(rec, **kw):
    s = json.dumps(rec, ensure_ascii=False, **kw)
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return s.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


#------------------------------------------------------------------
# 벡터를 공백구분 문자열로
#=> [0.01, -0.02, ...] 를 "0.01 -0.02 ..." 로 바꾼다(text 모드용).
#
# -in: vec = float 리스트
#
# -out: str = 공백으로 이은 숫자 문자열
# -out: error = 없음
#------------------------------------------------------------------
def _vec_to_str(vec):
    # repr 대신 짧은 표현을 위해 각 원소를 그대로 문자열화(정밀도는 이미 상위에서 조정).
    return " ".join(str(x) for x in vec)


#------------------------------------------------------------------
# 결과 dict → 출력 문자열(stdout/stderr 분리) — 핵심
#=> 형식(fmt)에 맞춰 stdout 본문과 stderr 시간요약을 각각 만든다.
#    1) text  : "<파일>\t<벡터>" (per_chunk 면 청크마다 한 줄)
#    2) json  : 사람이 보기 좋은 들여쓰기 JSON
#    3) jsonl : 한 줄 JSON(배치에서 파일당 1줄)
#    4) timing 이 켜져 있고 text 모드면 stderr 에 "[timing] ... total=.. ms"
#
# -in: result   = 파이프라인 결과 dict (file/model/dim/chunks/vector|vectors/elapsed_ms)
# -in: fmt      = "text" | "json" | "jsonl"
# -in: timing   = True 면 시간요약 출력(text 모드는 stderr, json 계열은 본문 포함)
#
# -out: (stdout_str, stderr_str) = 표준출력 본문, 표준에러 문자열(없으면 "")
# -out: error = 알 수 없는 fmt 면 ValueError
#------------------------------------------------------------------
def format_result(result, fmt, timing=True):
    stderr_str = ""

    # json 계열은 timing 이 꺼져 있으면 elapsed_ms 를 빼고 낸다(순수 결과).
    if fmt in ("json", "jsonl"):
        payload = dict(result)
        if not timing:
            payload.pop("elapsed_ms", None)
        if fmt == "json":
            return dumps_safe(payload, indent=2), stderr_str
        return dumps_safe(payload, separators=(",", ":")), stderr_str

    if fmt == "text":
        file = result.get("file", "")
        # per_chunk 결과면 'vectors' 가, 문서벡터면 'vector' 가 들어 있다.
        if "vectors" in result:
            lines = []
            for i, v in enumerate(result["vectors"]):
                lines.append(f"{file}#chunk{i}\t{_vec_to_str(v)}")
            stdout_str = "\n".join(lines)
        else:
            stdout_str = f"{file}\t{_vec_to_str(result.get('vector', []))}"

        # 시간요약은 stdout 벡터 라인을 건드리지 않도록 stderr 로 보낸다.
        if timing and "elapsed_ms" in result:
            stderr_str = _timing_line(file, result["elapsed_ms"])
        return stdout_str, stderr_str

    raise ValueError(f"알 수 없는 출력 형식: {fmt}")


#------------------------------------------------------------------
# 시간요약 한 줄 생성
#=> "[timing] <파일> extract=.. embed=.. model_load=.. total=.. ms" 문자열을 만든다.
#
# -in: file       = 파일 경로(표시용)
# -in: elapsed_ms = {단계: ms} 딕셔너리
#
# -out: str = 사람이 읽는 시간요약 한 줄
# -out: error = 없음(없는 키는 0 으로 표기)
#------------------------------------------------------------------
def _timing_line(file, elapsed_ms):
    def g(k):
        return elapsed_ms.get(k, 0.0)
    return (
        f"[timing] {file}  "
        f"extract={g('extract')}  embed={g('embed')}  "
        f"model_load={g('model_load')}  total={g('total')} ms"
    )


#------------------------------------------------------------------
# 배치 시간 요약 한 줄
#=> 여러 파일 처리 후 총합/평균/최댓값을 stderr 로 요약한다(데몬 상주 효과 확인용).
#
# -in: totals = 파일별 total(ms) 리스트
#
# -out: str = 요약 문자열(빈 리스트면 빈 문자열)
# -out: error = 없음
#------------------------------------------------------------------
def batch_timing_line(totals):
    if not totals:
        return ""
    n = len(totals)
    s = sum(totals)
    return (
        f"[timing-batch] files={n}  sum={round(s, 1)}  "
        f"avg={round(s / n, 1)}  max={round(max(totals), 1)} ms"
    )


#------------------------------------------------------------------
# 레코드 스트리밍 출력기 (JSON 배열 유효성 보장)
#=> 여러 레코드를 형식에 맞게 순차로 파일/화면에 쓴다. 핵심은 json(들여쓰기) 모드에서
#   레코드가 '여러 개'일 때 유효한 JSON 배열([ ... ])로 감싸는 것이다 — 들여쓴 JSON
#   객체를 그냥 이어붙이면 하나의 JSON 파일로 못 읽는다(뷰어가 '두 번째 객체'를 만나
#   "Unexpected non-whitespace character after JSON" 로 실패). 배치(--dir)는 배열,
#   단건(--file)은 객체 하나가 되도록 호출부가 multi 로 지정한다.
#    - json + multi : 첫 원소 앞 '[' · 원소 사이 ',' · 끝에 ']' (close 에서)
#    - json + 단건  : 객체 하나 그대로
#    - jsonl        : 레코드마다 1줄(항상 유효)
#    - text         : 미리 만든 문자열을 그대로 이어 씀
#
# -필드: fp    = 출력 파일객체(--out 파일 또는 sys.stdout)
# -필드: fmt   = "text"|"json"|"jsonl"
# -필드: array = json 배열로 감쌀지(= fmt=='json' 이고 multi=True)
#------------------------------------------------------------------
class RecordWriter:

    #------------------------------------------------------------------
    # 생성자
    #=> 출력 대상·형식과 '여러 건 여부'를 받아 배열 감싸기 여부를 정한다.
    #
    # -in: fp    = 출력 파일객체(None 불가 — 호출부가 sys.stdout 을 넘김)
    # -in: fmt   = 출력 형식 문자열
    # -in: multi = True 면 여러 레코드(배치) → json 은 배열로 감싼다
    #
    # -out: 없음
    # -out: error = 없음
    #------------------------------------------------------------------
    def __init__(self, fp, fmt, multi):
        self.fp = fp
        self.fmt = fmt
        self.array = (fmt == "json" and multi)
        self._started = False

    #------------------------------------------------------------------
    # 배열 경계 쓰기(내부)
    #=> 첫 원소면 '['+개행, 아니면 ','+개행 을 써서 원소 사이를 잇는다.
    #
    # -in: 없음
    # -out: 없음
    # -out: error = 없음
    #------------------------------------------------------------------
    def _open_or_sep(self):
        self.fp.write("[\n" if not self._started else ",\n")
        self._started = True

    #------------------------------------------------------------------
    # 레코드(dict) 1개 출력
    #=> 형식에 맞춰 rec 을 직렬화해 쓴다. json 배열 모드면 경계(',' 등)를 함께 관리한다.
    #
    # -in: rec = 결과 레코드 dict
    # -out: 없음
    # -out: error = 없음
    #------------------------------------------------------------------
    def write_record(self, rec):
        if self.fmt == "json":
            block = dumps_safe(rec, indent=2)
            if self.array:
                self._open_or_sep()
                self.fp.write(block)
            else:
                self.fp.write(block + "\n")
        else:  # jsonl (compact 1줄)
            self.fp.write(dumps_safe(rec, separators=(",", ":")) + "\n")

    #------------------------------------------------------------------
    # 미리 만든 본문 문자열 1개 출력(run_embed 의 text/json 혼용용)
    #=> format_result 가 돌려준 stdout 문자열을 쓴다. json 배열 모드면 경계를 관리하고,
    #   그 외(text/jsonl/단건 json)면 문자열 뒤에 개행만 붙여 종전과 동일하게 낸다.
    #
    # -in: s = 이미 형식화된 본문 문자열
    # -out: 없음
    # -out: error = 없음
    #------------------------------------------------------------------
    def write_block(self, s):
        if self.array:
            self._open_or_sep()
            self.fp.write(s)
        else:
            self.fp.write(s + "\n")

    #------------------------------------------------------------------
    # 마무리(배열 닫기)
    #=> 배열 모드면 마지막에 ']' 를 써서 JSON 을 완성한다. 한 건도 안 나왔으면 빈 배열
    #   '[]' 로 유효성을 유지한다. 반드시 출력 루프 뒤 1회 호출한다.
    #
    # -in: 없음
    # -out: 없음
    # -out: error = 없음
    #------------------------------------------------------------------
    def close(self):
        if self.array:
            self.fp.write("\n]\n" if self._started else "[]\n")
