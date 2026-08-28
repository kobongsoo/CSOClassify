"""화면(UI) 오류 로그 회귀 테스트.

화면에서 문제가 생기면 예전에는 st.error 빨간 상자만 뜨고 끝이라, 사용자가 창을
닫으면 아무 흔적도 남지 않았다. 이제 exe 판과 같은 파일
(csoclassify_err_YYYYMMDD.log)에 남긴다. 여기서 지키는 약속:

  1) 오류가 실제로 났을 때만 파일이 생긴다
  2) 예외를 주면 **스택까지** 남는다(한 줄 메시지로는 원인을 못 찾는다)
  3) 로그를 못 써도 화면은 죽지 않는다
  4) exe 판과 **같은 파일 이름·같은 환경변수**를 쓴다(한 파일만 보면 되도록)
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui"))

import uierrlog  # noqa: E402


#------------------------------------------------------------------
# 파일 이름 규칙이 exe 판과 같은가 (약속 ④)
#=> 이름이 어긋나면 "오류 로그가 어디 있나"를 두 곳에서 찾아야 한다.
#------------------------------------------------------------------
def test_log_path_matches_exe_convention(monkeypatch):
    monkeypatch.delenv("CSOCLASSIFY_ERRLOG", raising=False)
    name = os.path.basename(uierrlog.log_path())
    assert name.startswith("csoclassify_err_")
    assert name.endswith(".log")
    day = name[len("csoclassify_err_"):-len(".log")]
    assert len(day) == 8 and day.isdigit(), name


#------------------------------------------------------------------
# 환경변수로 위치를 바꿀 수 있다 (약속 ④ — exe 판과 같은 변수)
#------------------------------------------------------------------
def test_log_path_honors_env(tmp_path, monkeypatch):
    target = tmp_path / "shared" / "err.log"
    monkeypatch.setenv("CSOCLASSIFY_ERRLOG", str(target))
    assert uierrlog.log_path() == os.path.abspath(str(target))


#------------------------------------------------------------------
# 오류가 없으면 파일도 없다 (약속 ①)
#=> uierrlog 는 '쓸 때 만드는' 방식이라, 아무것도 안 부르면 파일이 없어야 한다.
#------------------------------------------------------------------
def test_no_file_until_error(tmp_path, monkeypatch):
    err = tmp_path / "err.log"
    monkeypatch.setenv("CSOCLASSIFY_ERRLOG", str(err))
    assert not err.exists()
    assert uierrlog.log_path() == os.path.abspath(str(err))
    assert not err.exists(), "경로만 물었는데 파일이 생겼다"


#------------------------------------------------------------------
# 메시지만 남기기 — 여러 줄은 한 줄로 눌러 담는다
#=> 한 사건이 여러 건처럼 보이면 "오류가 몇 건 났나"를 셀 수 없다.
#------------------------------------------------------------------
def test_log_error_message_only(tmp_path, monkeypatch):
    err = tmp_path / "err.log"
    monkeypatch.setenv("CSOCLASSIFY_ERRLOG", str(err))
    uierrlog.log_error("분류 결과 파일이 생성되지 않았습니다.\n원인은 여기", where="분류 실행")
    body = err.read_text(encoding="utf-8")
    assert "분류 실행" in body
    assert "분류 결과 파일이 생성되지 않았습니다. / 원인은 여기" in body
    assert len([ln for ln in body.splitlines() if ln.strip()]) == 1


#------------------------------------------------------------------
# 예외를 주면 스택까지 남는다 (약속 ②)
#------------------------------------------------------------------
def test_log_error_includes_traceback(tmp_path, monkeypatch):
    err = tmp_path / "err.log"
    monkeypatch.setenv("CSOCLASSIFY_ERRLOG", str(err))
    try:
        raise ValueError("일부러 낸 오류")
    except ValueError as e:
        uierrlog.log_error(f"임베딩 실패: {e}", exc=e, where="임베딩")
    body = err.read_text(encoding="utf-8")
    assert "임베딩 실패: 일부러 낸 오류" in body
    assert "Traceback" in body
    assert "ValueError" in body
    # 어느 파일 어느 줄에서 터졌는지가 있어야 원인을 찾는다.
    assert "test_ui_errlog.py" in body


#------------------------------------------------------------------
# 로그를 못 써도 화면은 죽지 않는다 (약속 ③)
#=> 읽기전용 폴더·권한 없음 등으로 못 쓸 수 있다. 그때 예외가 올라오면
#   오류를 남기려다 화면을 멈추는 꼴이 된다.
#------------------------------------------------------------------
def test_write_failure_is_swallowed(tmp_path, monkeypatch):
    # 존재하는 '파일'을 폴더처럼 쓰게 만들어 쓰기를 실패시킨다.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("CSOCLASSIFY_ERRLOG", str(blocker / "err.log"))
    assert uierrlog.log_error("쓰기 실패해도 예외는 안 난다") is None


#------------------------------------------------------------------
# show_error 는 streamlit 이 없어도 동작한다
#=> 테스트·배치에서 import 되어도 죽지 않아야 한다(화면 밖 호출).
#------------------------------------------------------------------
def test_show_error_without_streamlit(tmp_path, monkeypatch, capsys):
    err = tmp_path / "err.log"
    monkeypatch.setenv("CSOCLASSIFY_ERRLOG", str(err))
    monkeypatch.setitem(sys.modules, "streamlit", None)   # import 는 되지만 st.error 가 없음
    uierrlog.show_error("화면 밖에서 부른 오류", where="테스트")
    assert "화면 밖에서 부른 오류" in err.read_text(encoding="utf-8")


#------------------------------------------------------------------
# run_guarded 는 기록한 뒤 예외를 그대로 다시 올린다
#=> 삼켜 버리면 Streamlit 이 평소 보여 주던 빨간 트레이스백 화면이 사라져
#   개발 중에 오히려 불편해진다. 기록만 하고 흐름은 그대로 둔다.
#------------------------------------------------------------------
def test_run_guarded_logs_and_reraises(tmp_path, monkeypatch):
    err = tmp_path / "err.log"
    monkeypatch.setenv("CSOCLASSIFY_ERRLOG", str(err))
    monkeypatch.setitem(sys.modules, "streamlit", None)

    def boom():
        raise RuntimeError("화면이 터졌다")

    with pytest.raises(RuntimeError):
        uierrlog.run_guarded(boom)
    body = err.read_text(encoding="utf-8")
    assert "화면 실행 중 오류: RuntimeError: 화면이 터졌다" in body
    assert "Traceback" in body


#------------------------------------------------------------------
# 정상 실행이면 run_guarded 는 아무것도 안 남긴다 (약속 ①)
#------------------------------------------------------------------
def test_run_guarded_quiet_on_success(tmp_path, monkeypatch):
    err = tmp_path / "err.log"
    monkeypatch.setenv("CSOCLASSIFY_ERRLOG", str(err))
    assert uierrlog.run_guarded(lambda: "정상") == "정상"
    assert not err.exists()
