#------------------------------------------------------------------
# csoclassify 실행 러너 — 업로드 파일 1개 → 벡터 포함 분류 레코드
#=> seed 직접 추가(업로드) 시, 관리자가 올린 파일을 csoclassify 로 돌려(추출→임베딩)
#   벡터가 든 분류 레코드를 얻는다. subprocess 로 호출해 UI 프로세스와 격리한다.
#------------------------------------------------------------------

import json
import os
import re
import subprocess
import threading
from types import SimpleNamespace


#------------------------------------------------------------------
# 실행 명령 문자열 → 인자 리스트
#=> "csoclassify.exe" 또는 "python -m csoclassify" 같은 명령을 인자 리스트로 나눈다.
#   윈도 경로를 위해 백슬래시는 이스케이프로 보지 않고, 큰따옴표로 묶인 토큰(공백 포함
#   경로)은 통째로 한 인자로 처리한다. 예)
#     '"D:\\Program Files\\a b\\csoclassify.exe"' → ['D:\\Program Files\\a b\\csoclassify.exe']
#     'python -m csoclassify'                     → ['python','-m','csoclassify']
#   (사용자가 실수로 경로를 따옴표로 감싸도, 공백이 있어도 안전하게 동작)
#
# -in: cmd_str = 명령 문자열
# -out: list = 인자 리스트(빈 문자열이면 빈 리스트)
# -out: error = 없음
#------------------------------------------------------------------
def parse_base_cmd(cmd_str):
    if not cmd_str:
        return []
    # 따옴표로 묶인 덩어리는 통째로, 나머지는 공백으로 분리. 감싼 따옴표는 벗겨낸다.
    tokens = re.findall(r'"[^"]*"|\S+', cmd_str)
    return [t[1:-1] if len(t) >= 2 and t.startswith('"') and t.endswith('"') else t
            for t in tokens]


#------------------------------------------------------------------
# glob 패턴 중괄호 확장 (UI·CLI 공용 규칙)
#=> 파이썬 표준 glob 은 셸과 달리 중괄호({a,b,c}) 확장을 지원하지 않는다. 확장자를
#   여러 개 넣고 싶을 때 쓰도록 "*.{hwp,docx,pdf}" 를 ["*.hwp","*.docx","*.pdf"] 로
#   직접 펼친다. 중괄호가 없으면 그대로 1개, 여러 그룹/중첩도 재귀로 모두 펼친다.
#   csoclassify CLI 의 collect_files 와 같은 규칙을 UI 가 공유하도록 여기에 둔다
#   (패키지는 UI 를 역참조할 수 없어 CLI 쪽은 자체 사본을 유지). 예)
#     "*.{hwp,docx}"        → ["*.hwp","*.docx"]
#     "report_*.{doc,docx}" → ["report_*.doc","report_*.docx"]
#
# -in: pattern = 원본 glob 패턴(중괄호 그룹 포함 가능)
#
# -out: list = 펼쳐진 패턴 리스트(중복 제거, 최소 1개)
# -out: error = 없음
#------------------------------------------------------------------
def expand_glob_braces(pattern):
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    pre, post = pattern[:m.start()], pattern[m.end():]
    result = []
    for opt in m.group(1).split(","):
        # 남은 뒷부분에 또 다른 그룹이 있을 수 있어 재귀로 마저 펼친다.
        for expanded in expand_glob_braces(pre + opt.strip() + post):
            if expanded not in result:
                result.append(expanded)
    return result


#------------------------------------------------------------------
# 폴더 안 파일 개수 세기 (화면 안내용)
#=> "이 폴더에 몇 개가 있나"를 화면에 보여 주려고 센다. 분류 대상 수를 미리
#   알려 주는 용도라, 정확한 목록이 필요한 실행 경로와 달리 '빠르게·대충'이
#   목적이다.
#    1) os.scandir 로 훑는다(glob 보다 빠르고 메모리를 안 쌓는다)
#    2) cap 을 넘어서면 세기를 멈추고 capped=True 로 알린다 — 파일이 수십만 개인
#       폴더를 가리켰을 때 화면이 몇 초씩 멈추는 것을 막는다
#    3) 권한 없는 하위 폴더는 조용히 건너뛴다(안내용 숫자 때문에 화면이
#       죽으면 안 된다)
#
# -in: folder    = 셀 폴더 경로
# -in: recursive = 하위 폴더까지 셀지(기본 True)
# -in: cap       = 여기까지만 세고 멈춤(기본 20000)
#
# -out: (count, capped) = 파일 수, cap 에 걸려 멈췄으면 True
# -out: error = 폴더가 없거나 못 읽으면 (0, False)
#------------------------------------------------------------------
def count_files(folder, recursive=True, cap=20000):
    if not folder or not os.path.isdir(folder):
        return 0, False
    count = 0
    stack = [folder]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_file():
                            count += 1
                            # cap 에 닿으면 더 세지 않고 즉시 빠져나온다.
                            if count >= cap:
                                return count, True
                        elif recursive and e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                    except OSError:
                        continue
        except OSError:
            continue
    return count, False


#------------------------------------------------------------------
# glob 패턴 목록 펼치기 (UI·CLI 공용 진입점)
#=> --glob 한 값에 패턴을 여러 개 넣는 두 표기를 모두 받는다.
#     1) 콤마로 나열   : "*.hwp,*.pdf"        (기본·권장 — 접두가 달라도 됨)
#     2) 중괄호로 나열 : "*.{hwp,pdf}"         (셸 관습, 공통 접두/접미 간결)
#   먼저 "중괄호 밖" 최상위 콤마로 패턴을 나눈 뒤(중괄호 안 콤마는 그룹 구분자라
#   건드리지 않음), 각 토큰의 중괄호를 펼쳐 합친다. 두 표기를 섞어도 된다. 예)
#     "*.hwp,*.pdf"          → ["*.hwp","*.pdf"]
#     "*.{hwp,pdf},draft_*"  → ["*.hwp","*.pdf","draft_*"]
#   주의: 파일명/패턴에 리터럴 콤마가 있으면 구분자로 오인될 수 있으나, 확장자
#   필터 용도에선 사실상 발생하지 않는다.
#
# -in: spec = --glob 원본 값(콤마·중괄호로 여러 패턴 가능)
#
# -out: list = 펼쳐진 개별 glob 패턴 리스트(중복 제거, 최소 1개)
# -out: error = 없음
#------------------------------------------------------------------
def expand_glob_patterns(spec):
    # 중괄호 깊이를 세며 최상위(깊이 0) 콤마에서만 자른다.
    tokens, buf, depth = [], [], 0
    for ch in spec:
        if ch == "{":
            depth += 1
            buf.append(ch)
        elif ch == "}":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            tokens.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    tokens.append("".join(buf))

    result = []
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        for pat in expand_glob_braces(tok):
            if pat not in result:
                result.append(pat)
    return result


#------------------------------------------------------------------
# csoclassify 로 파일 1개 처리(핵심)
#=> --with-vector 로 파일을 분류(기본)해 stdout 의 jsonl 첫 레코드를 파싱한다.
#   벡터·자동등급·근거가 든 dict 를 돌려준다.
#    1) 인자 조립 후 subprocess 실행(UTF-8 강제 캡처)
#    2) stdout 에서 '{' 로 시작하는 첫 줄을 JSON 으로 파싱
#
# -in: base_cmd   = 실행 인자 리스트(parse_base_cmd 결과)
# -in: file_path  = 처리할 파일 경로
# -in: pythonpath = 'python -m csoclassify' 형태일 때 넣을 PYTHONPATH(예: src)
# -in: timeout    = 최대 대기(초)
#
# -out: dict = 분류 레코드({file, grade, vector, signals, ...})
# -out: error = 출력 없거나 실패 시 RuntimeError
#------------------------------------------------------------------
def run_csoclassify_file(base_cmd, file_path, pythonpath=None, timeout=180):
    # 분류가 기본 동작이므로 --classify 는 붙이지 않는다(--with-vector 로 벡터도 산출).
    args = list(base_cmd) + ["--with-vector", "--file", file_path,
                             "--format", "jsonl", "--no-timing"]
    env = dict(os.environ)
    # 캡처 시 한글이 깨지지 않게 csoclassify 가 UTF-8 로 출력하도록.
    env["PYTHONUTF8"] = "1"
    if pythonpath:
        env["PYTHONPATH"] = pythonpath

    p = subprocess.run(args, capture_output=True, timeout=timeout, env=env)
    out = p.stdout.decode("utf-8", errors="replace")
    err = p.stderr.decode("utf-8", errors="replace")
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            rec = json.loads(line)
            # stderr 을 함께 실어 보낸다. 실행은 됐는데 vector 가 없을 때 "왜 없는지"는
            # 여기(예: "[warn] 알 수 없는 인자: --with-vector")에만 적혀 있다.
            rec["_stderr"] = err[-600:]
            return rec

    raise RuntimeError(f"csoclassify 결과를 얻지 못했습니다 (code={p.returncode}). {err[-400:]}")


#------------------------------------------------------------------
# 공통 subprocess 실행
#=> 인자 리스트로 csoclassify 를 돌리고 결과(코드/stderr/요약줄)를 담아 돌려준다.
#   결과 본문은 --out 파일로 나가므로 여기선 상태·요약만 본다.
#
# -in: args       = 전체 실행 인자 리스트
# -in: pythonpath = 모듈 실행 시 PYTHONPATH
# -in: timeout    = 최대 대기(초)
# -in: marker     = stderr 에서 뽑을 요약줄 표식(예: "[classify]")
#
# -out: SimpleNamespace(returncode, stderr, summary)
# -out: error = timeout 시 예외 전파
#------------------------------------------------------------------
def _run(args, pythonpath, timeout, marker):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"          # 한글 출력 UTF-8 고정
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    p = subprocess.run(args, capture_output=True, timeout=timeout, env=env)
    err = p.stderr.decode("utf-8", errors="replace")
    # 요약 한 줄(예: "[classify] files=.. C=.. ..")만 뽑아 표시용으로.
    summary = ""
    for line in err.splitlines():
        if marker in line:
            summary = line.strip()
    return SimpleNamespace(returncode=p.returncode, stderr=err, summary=summary)


#------------------------------------------------------------------
# 회사 분류 체계 가져오기 실행 (doc_taxonomy.yaml 생성)
#=> MpowerV11 에서 내보낸 JSON 을 읽어 화면이 쓰는 doc_taxonomy.yaml 을 만든다.
#   실제로 도는 명령은 아래 한 줄이고, 화면은 이 함수만 부른다.
#     csoclassify --export-taxonomy --export-input <원본.json> --taxonomy <만들 .yaml>
#   [왜 화면에서 실행하나] 예전에는 이 명령을 글로만 알려 주고 관리자가 직접
#   명령창에서 치게 했다. 분류 체계를 연결하지 않으면 업무분류 축이 통째로 꺼지는데,
#   그 첫 관문을 명령창에 맡기면 대부분 거기서 멈춘다.
#
# -in: base_cmd     = 실행 인자 리스트(parse_base_cmd 결과)
# -in: export_input = MpowerV11 이 내보낸 원본 JSON 경로
# -in: taxonomy_out = 만들어 낼 doc_taxonomy.yaml 경로
# -in: pythonpath   = 모듈 실행 시 PYTHONPATH
# -in: timeout      = 최대 대기(초). 파일 한 개 변환이라 짧아도 된다
#
# -out: SimpleNamespace(returncode, stderr, summary, args)
#        args = 실제로 실행한 명령(실패했을 때 화면에 그대로 보여 주려고 담는다)
# -out: error = timeout 시 예외 전파
#------------------------------------------------------------------
def run_export_taxonomy(base_cmd, export_input, taxonomy_out, pythonpath=None,
                        timeout=180):
    args = list(base_cmd) + ["--export-taxonomy",
                             "--export-input", export_input,
                             "--taxonomy", taxonomy_out]
    r = _run(args, pythonpath, timeout, "[csoclassify]")
    r.args = args
    return r


#------------------------------------------------------------------
# 폴더 분류 실행 (Phase 1 수집 / Phase 3 분류의 "실행")
#=> csoclassify 로 폴더를 분류(기본)해 out_path(jsonl)에 저장한다. 벡터도 함께
#   뽑아(--with-vector) 이후 seed 비교·승격에 쓸 수 있게 한다.
#
# -in: base_cmd    = 실행 인자 리스트(exe 또는 python -m csoclassify)
# -in: folder      = 대상 폴더
# -in: out_path    = 결과 jsonl 저장 경로
# -in: glob        = 파일 패턴(기본 *)
# -in: recursive   = (쓰이지 않음) 두 엔진 모두 --dir 은 항상 재귀다
# -in: with_vector = 벡터도 산출(기본 True)
# -in: pythonpath  = 모듈 실행 시 PYTHONPATH
# -in: timeout     = 최대 대기(초, 폴더가 크면 오래 걸림)
#
# -out: SimpleNamespace(returncode, stderr, summary)
# -out: error = timeout 시 예외 전파
#------------------------------------------------------------------
def run_csoclassify_dir(base_cmd, folder, out_path, glob="*", recursive=True,
                     with_vector=True, pythonpath=None, timeout=3600):
    # 분류가 기본 동작이므로 --classify 는 생략한다.
    args = list(base_cmd) + ["--dir", folder, "--glob", glob,
                             "--format", "jsonl", "--out", out_path, "--no-timing"]
    if with_vector:
        args.append("--with-vector")
    # -r 은 넘기지 않는다. 두 엔진 모두 --dir 이면 **언제나 하위 폴더까지** 훑는다
    # (Python cli.py 의 -r 은 help 에 '(무시됨)' 이라 적혀 있고, Rust 는 walkdir 로
    # 항상 재귀한다). Rust 판은 모르는 인자라며 경고까지 찍어 로그만 지저분해졌다.
    return _run(args, pythonpath, timeout, "[classify]")


#------------------------------------------------------------------
# 폴더 분류 실행(스트리밍) — 진행바/경과시간용
#=> run_csoclassify_dir 과 같은 분류를 하되, subprocess.run(블로킹) 대신 Popen 으로
#   자식의 stderr 를 "한 줄씩" 읽는다. csoclassify 가 --progress 로 흘리는
#   "[progress] 처리수/총수 경로" 를 만나면 on_progress 콜백을 불러 UI 가 진행바와
#   경과시간을 실시간으로 갱신할 수 있게 한다.
#    1) --progress 를 붙여 Popen 기동(결과 본문은 --out 파일로 나가 stdout 은 버림)
#    2) stderr 를 줄단위로 읽어 [progress] 파싱→콜백, [classify] 요약줄 보관
#    3) timeout 초과 시 threading.Timer 로 프로세스 강제 종료
#
# -in: base_cmd    = 실행 인자 리스트(exe 또는 python -m csoclassify)
# -in: folder      = 대상 폴더
# -in: out_path    = 결과 jsonl 저장 경로
# -in: glob        = 파일 패턴(기본 *)
# -in: recursive   = (쓰이지 않음) 두 엔진 모두 --dir 은 항상 재귀다
# -in: embed_mode  = 임베딩 정책. "needed"=보류·seed_eligible 만(기본, 빠름),
#                    "all"=전량, "none"=임베딩 안 함
# -in: pythonpath  = 모듈 실행 시 PYTHONPATH
# -in: timeout     = 최대 대기(초)
# -in: on_progress = 콜백 fn(done:int, total:int, path:str). None 이면 진행보고 생략
# -in: extra_args  = 그대로 덧붙일 인자 리스트(예: ["--taxonomy", "…", "--doc-rules", "…"]).
#                    업무분류 축을 켤 때 화면이 넘긴다. None 이면 아무것도 안 붙는다
#
# -out: SimpleNamespace(returncode, stderr, summary)
# -out: error = 시작 실패 등은 예외 전파(호출부에서 표시)
#------------------------------------------------------------------
def run_csoclassify_dir_stream(base_cmd, folder, out_path, glob="*", recursive=True,
                               embed_mode="needed", pythonpath=None, timeout=3600,
                               on_progress=None, extra_args=None):
    # 분류가 기본 동작이므로 --classify 는 생략한다(임베딩 정책만 아래에서 지정).
    args = list(base_cmd) + ["--dir", folder, "--glob", glob,
                             "--format", "jsonl", "--out", out_path, "--no-timing",
                             "--progress"]
    if embed_mode == "needed":
        args.append("--embed-needed")
    elif embed_mode == "all":
        args.append("--with-vector")
    # -r 은 넘기지 않는다. 두 엔진 모두 --dir 이면 **언제나 하위 폴더까지** 훑는다
    # (Python cli.py 의 -r 은 help 에 '(무시됨)' 이라 적혀 있고, Rust 는 walkdir 로
    # 항상 재귀한다). Rust 판은 모르는 인자라며 경고까지 찍어 로그만 지저분해졌다.
    # 업무분류(doctype) 축 관련 인자(--taxonomy/--doc-rules/--axis)를 화면에서 그대로
    # 넘길 수 있게 열어 둔다. 안 주면 지금까지와 똑같이 보안등급만 도는 배포가 된다.
    if extra_args:
        args += list(extra_args)

    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"              # 자식이 UTF-8 로 출력(한글 깨짐 방지)
    if pythonpath:
        env["PYTHONPATH"] = pythonpath

    # 결과는 out_path 파일로 가므로 stdout 은 필요 없다 → 버리고 stderr 만 스트리밍.
    proc = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env=env, text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    # 자식이 멈춰 무한대기하지 않도록 타임아웃이 지나면 강제 종료.
    timer = threading.Timer(timeout, proc.kill)
    timer.start()

    err_lines = []
    summary = ""
    try:
        for line in proc.stderr:
            s = line.rstrip("\n")
            err_lines.append(s)
            if s.startswith("[progress]"):
                # "[progress] 3/30 D:\경로 파일.hwp" → done, total, path(공백 허용)
                if on_progress is not None:
                    try:
                        parts = s.split(" ", 2)          # 경로에 공백이 있어도 안전
                        done, total = parts[1].split("/")
                        path = parts[2] if len(parts) > 2 else ""
                        on_progress(int(done), int(total), path)
                    except (ValueError, IndexError):
                        pass
            elif "[classify]" in s:
                summary = s.strip()
        proc.wait()
    finally:
        timer.cancel()

    return SimpleNamespace(returncode=proc.returncode,
                           stderr="\n".join(err_lines), summary=summary)


