"""화면(Streamlit) 쪽 오류 로그 — log/class_err_YYYYMMDD.log 에 남긴다.

왜 필요한가: 화면에서 문제가 생기면 st.error 로 빨간 상자만 뜬다. 사용자는 그걸
읽고 창을 닫아 버리고, 나중에 "아까 뭐라고 떴어요?" 라고 물으면 아무도 답을 못 한다.
Streamlit 이 잡아 주는 예외도 브라우저에만 트레이스백을 그리고 끝난다 — 원격에
띄워 둔 화면이면 그 트레이스백조차 볼 수 없다. 그래서 오류를 파일로 남긴다.

exe 판(MpowerClassify.exe · MpowerClassify-rs.exe)과 **같은 파일 이름·같은 환경변수**를
쓴다. 그래야 "무슨 일이 있었나" 를 볼 때 한 파일만 보면 된다.

규칙은 exe 판과 같다:
  · 오류가 실제로 났을 때만 파일을 만든다(정상 사용 중에는 안 생긴다)
  · 날짜별로 나눈다
  · 로그를 못 써도 화면은 그대로 동작한다(로그 때문에 화면이 죽으면 더 나쁘다)
"""

import datetime
import os
import sys
import threading
import traceback

# 파일 이름 규칙 — exe 판(logsetup.default_err_log_path · Rust errlog.rs)과 같다.
_PREFIX = "class_err_"


#------------------------------------------------------------------
# 오류 로그 파일 경로
#=> 어디에 남길지 정한다. exe 판(logsetup.log_dir)과 같은 규칙을 쓴다 —
#   화면과 분류기의 로그가 서로 다른 폴더로 흩어지면, 한 사건을 좇는 데
#   두 곳을 뒤져야 한다.
#    1) 환경변수 CSOCLASSIFY_ERRLOG 가 있으면 그 '파일 경로' 그대로
#       (한 대에서 화면과 exe 로그를 한 파일로 모을 때)
#    2) 환경변수 CSOCLASSIFY_LOGDIR 이 있으면 그 폴더의 class_err_YYYYMMDD.log
#    3) 없으면 <프로젝트 루트>/log/class_err_YYYYMMDD.log
#       — 이 파일은 ui/uierrlog.py 이므로 루트는 한 단계 위다.
#         화면은 언제나 소스로 돌아서 exe_dir() 과 같은 값이 된다.
#
# -in: 없음
#
# -out: path = 로그 파일 절대경로
# -out: error = 없음
#------------------------------------------------------------------
def log_path():
    env = os.environ.get("CSOCLASSIFY_ERRLOG")
    if env:
        return os.path.abspath(env)
    day = datetime.datetime.now().strftime("%Y%m%d")
    return os.path.join(log_dir(), f"{_PREFIX}{day}.log")


#------------------------------------------------------------------
# 화면 로그를 모아 둘 폴더
#=> 분류기(logsetup.log_dir)와 같은 자리를 가리키게 한다. 화면은 exe 로 굳지
#   않고 언제나 소스로 돌기 때문에, 분류기의 '소스 실행' 기준(프로젝트 루트)과
#   맞추면 두 로그가 한 폴더에 모인다.
#
# -in: 없음
#
# -out: dir = 로그 폴더 절대경로(만들지 않는다 — 쓰는 쪽이 만든다)
# -out: error = 없음
#------------------------------------------------------------------
def log_dir():
    env = os.environ.get("CSOCLASSIFY_LOGDIR")
    if env:
        return os.path.abspath(env)
    here = os.path.dirname(os.path.abspath(__file__))       # <루트>/ui
    return os.path.join(os.path.dirname(here), "log")


#------------------------------------------------------------------
# 로그 파일 열기(이어쓰기) — 새 파일이면 UTF-8 BOM 을 얹는다
#=> 파일은 항상 UTF-8 로 쓴다. 그런데 Windows 의 메모장·엑셀·PowerShell 은 BOM 이
#   없는 UTF-8 을 ANSI(cp949)로 읽어 한글이 통째로 깨진다("화면" → "?붾㈃").
#   그래서 파일을 처음 만들 때 한 번만 BOM 을 붙인다 — 이어쓸 때마다 붙이면
#   파일 중간에 BOM 이 박혀 오히려 지저분해진다.
#
# -in: p = 로그 파일 경로
#
# -out: file = 이어쓰기로 열린 파일 객체
# -out: error = 열기 실패 시 예외 전파(부르는 쪽이 통째로 무시한다)
#------------------------------------------------------------------
def _open_append(p):
    new_file = (not os.path.isfile(p)) or os.path.getsize(p) == 0
    return open(p, "a", encoding="utf-8-sig" if new_file else "utf-8")


#------------------------------------------------------------------
# 오류 한 건 기록
#=> 시각·PID 와 함께 한 줄(예외가 있으면 스택까지) 덧붙인다.
#   쓰기 실패는 전부 무시한다 — 오류를 남기려다 또 오류를 내면 화면이 멈춘다.
#
# -in: msg = 남길 내용(사용자에게 보인 문구를 그대로 넣으면 대조가 쉽다)
# -in: exc = 예외 객체(있으면 트레이스백까지 남긴다). 없으면 메시지만
# -in: where = 어느 화면·어느 동작이었는지 짧은 표시(예: "분류 실행")
#
# -out: path = 실제로 기록한 파일 경로(실패하면 None)
# -out: error = 없음(모든 실패를 None 으로 환원)
#------------------------------------------------------------------
def log_error(msg, exc=None, where=None):
    try:
        p = log_path()
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        head = f"{stamp} [PID {os.getpid()}] ERROR ui"
        if where:
            head += f"({where})"
        with _open_append(p) as f:
            # 여러 줄 메시지는 한 줄로 눌러 담는다 — 한 사건이 여러 건처럼 보이면
            # "오류가 몇 건이나 났나"를 셀 수 없게 된다.
            f.write(f"{head}: {' / '.join(str(msg).splitlines())}\n")
            if exc is not None:
                # 스택은 원인을 찾는 유일한 단서라 줄바꿈 그대로 남긴다.
                f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        return p
    except Exception:   # noqa: BLE001  (로그 실패가 화면을 멈추면 안 된다)
        return None


#------------------------------------------------------------------
# 화면 오류 표시 + 로그 (한 번에)
#=> st.error 로 빨간 상자를 띄우면서 같은 내용을 파일에도 남긴다. 화면 코드의
#   `st.error(...)` 를 이 함수로 바꾸면 동작은 그대로면서 흔적이 남는다.
#
# -in: msg   = 사용자에게 보일 문구
# -in: exc   = 예외 객체(있으면 로그에 스택까지)
# -in: where = 어느 동작이었는지 짧은 표시
#
# -out: 없음
# -out: error = 없음
#------------------------------------------------------------------
def show_error(msg, exc=None, where=None):
    log_error(msg, exc=exc, where=where)
    try:
        import streamlit as st
        st.error(msg)
    except Exception:   # noqa: BLE001  (테스트 등 streamlit 밖에서 불릴 수 있다)
        print(msg, file=sys.stderr)


#------------------------------------------------------------------
# 잡히지 않은 예외도 파일에 남기기
#=> Streamlit 은 스크립트 실행 중 예외를 자기가 잡아 브라우저에 그린다. 그래서
#   sys.excepthook 만으로는 부족하고, 화면 진입점을 try 로 감싸는 쪽이 확실하다
#   (run_guarded 참고). 이 함수는 그 바깥의 것들 — 백그라운드 스레드나
#   Streamlit 이 못 잡은 예외 — 을 받아 준다. 앱 시작 때 한 번 부른다.
#
# -in: 없음
#
# -out: 없음
# -out: error = 없음
#------------------------------------------------------------------
def install_hooks():
    prev = sys.excepthook

    def _hook(etype, value, tb):
        log_error(f"잡히지 않은 오류: {etype.__name__}: {value}", exc=value, where="excepthook")
        prev(etype, value, tb)

    sys.excepthook = _hook

    # 백그라운드 스레드에서 죽는 경우(파이썬 3.8+).
    prev_thread = getattr(threading, "excepthook", None)
    if prev_thread is not None:
        def _thook(args):
            log_error(f"스레드에서 잡히지 않은 오류: {args.exc_type.__name__}: {args.exc_value}",
                      exc=args.exc_value, where=f"thread:{getattr(args.thread, 'name', '?')}")
            prev_thread(args)

        threading.excepthook = _thook


#------------------------------------------------------------------
# 화면 진입점 감싸기
#=> main() 을 이 함수로 감싸면, 화면 어디서 예외가 터지든 파일에 스택까지 남기고
#   사용자에게는 "어디를 보면 되는지"를 알려 준다. Streamlit 이 예외를 자기가
#   잡아 브라우저에만 그리는 것을 막기 위해(=파일에도 남기기 위해) 필요하다.
#   기록한 뒤 예외를 그대로 다시 올려, Streamlit 의 평소 화면(빨간 트레이스백)은
#   그대로 보이게 둔다 — 개발 중에는 그게 더 편하다.
#
# -in: fn = 실행할 함수(보통 app.main)
#
# -out: fn 의 반환값
# -out: error = 예외를 기록한 뒤 그대로 다시 올림(raise)
#------------------------------------------------------------------
# Streamlit 이 '화면 흐름'을 제어하려고 일부러 던지는 예외들. st.rerun() 은
# RerunException 을, 화면 전환 요청은 StopException 을 올려서 스크립트를 끊는다 —
# 정상 동작이지 오류가 아니다. 이것까지 잡아 로그에 남기면 "분류가 잘 끝났는데
# 빨간 오류 상자가 뜬다"가 된다(실제로 그랬다).
_CONTROL_FLOW = ("RerunException", "StopException")


#------------------------------------------------------------------
# 이 예외가 Streamlit 의 정상적인 화면 흐름 제어인가
#=> 클래스 이름으로 판별한다. streamlit 버전마다 이 예외들이 사는 모듈 경로가
#   달라져 import 로 붙잡으면 버전이 바뀔 때 조용히 깨진다.
#
# -in: exc = 잡힌 예외 객체
#
# -out: bool = 제어용 예외면 True(로그·화면 표시 없이 그대로 흘려보낸다)
# -out: error = 없음
#------------------------------------------------------------------
def is_control_flow(exc):
    return type(exc).__name__ in _CONTROL_FLOW


def run_guarded(fn):
    try:
        return fn()
    except BaseException as e:      # noqa: BLE001  (여기서 놓치면 흔적이 안 남는다)
        if is_control_flow(e):
            # st.rerun() 같은 정상 흐름 — 손대지 않고 그대로 올린다.
            raise
        p = log_error(f"화면 실행 중 오류: {type(e).__name__}: {e}", exc=e, where="main")
        try:
            import streamlit as st
            st.error(f"오류가 발생했습니다: {type(e).__name__}: {e}")
            if p:
                st.caption(f"자세한 내용은 오류 로그를 보세요 — {p}")
        except Exception:   # noqa: BLE001
            pass
        raise
