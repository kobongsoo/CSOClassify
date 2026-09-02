"""오류 전용 로그(log/class_err_YYYYMMDD.log) 회귀 테스트.

배경: exe 로 배포하면 UI·배치·스케줄러가 실행해 화면이 없다. 예전에는 오류를
stderr 로만 알려서, 그런 환경에서 실패하면 원인이 통째로 사라졌다. 이제 오류만
따로 파일로 모은다. 여기서 지키는 약속은 세 가지다.

  1) 오류가 **실제로 났을 때만** 파일이 생긴다(정상 실행마다 빈 파일이 쌓이면
     "파일이 있다 = 오류가 있었다"라는 판단 자체가 불가능해진다)
  2) 잡히지 않은 예외도 **스택까지** 남는다(한 줄 메시지로는 원인을 못 찾는다)
  3) Ctrl+C 는 오류가 아니다(로그를 더럽히지 않는다)
"""

import logging
import os

import pytest

from csoclassify import cli, logsetup


#------------------------------------------------------------------
# 각 테스트 뒤 로거 핸들러 정리
#=> setup_logging 은 'csoclassify' 로거에 파일 핸들러를 붙인다. 정리하지 않으면
#   다음 테스트가 이전 테스트의 임시파일에 계속 쓰려다 tmp_path 정리와 충돌한다.
#
# -in: 없음(pytest fixture)
#
# -out: 없음
# -out: error = 없음
#------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_handlers():
    yield
    lg = logging.getLogger("csoclassify")
    for h in list(lg.handlers):
        h.close()
        lg.removeHandler(h)


#------------------------------------------------------------------
# 환경변수로 오류 로그 위치를 바꿀 수 있다
#=> 읽기전용 폴더에 설치했거나 여러 대의 로그를 한곳에 모을 때 쓴다.
#   Rust 판도 같은 변수 이름을 본다.
#------------------------------------------------------------------
def test_err_log_path_honors_env(tmp_path, monkeypatch):
    target = tmp_path / "somewhere" / "my_err.log"
    monkeypatch.setenv("CSOCLASSIFY_ERRLOG", str(target))
    assert logsetup.default_err_log_path() == os.path.abspath(str(target))


#------------------------------------------------------------------
# 환경변수가 없으면 exe 옆 날짜별 파일
#------------------------------------------------------------------
def test_err_log_path_default_is_dated(monkeypatch):
    monkeypatch.delenv("CSOCLASSIFY_ERRLOG", raising=False)
    p = logsetup.default_err_log_path()
    name = os.path.basename(p)
    assert name.startswith("class_err_")
    assert name.endswith(".log")
    day = name[len("class_err_"):-len(".log")]
    assert len(day) == 8 and day.isdigit(), name


#------------------------------------------------------------------
# 오류가 없으면 파일도 없다 (약속 ①)
#=> delay=True 로 핸들러를 달았기 때문에, ERROR 가 한 번도 안 나면 파일 자체가
#   만들어지지 않는다. INFO/WARNING 만으로는 생기지 않아야 한다.
#------------------------------------------------------------------
def test_no_file_when_no_error(tmp_path):
    err = tmp_path / "err.log"
    logsetup.setup_logging(log_path=None, verbose=False, err_log_path=str(err))
    log = logsetup.get_logger("csoclassify.test")
    log.info("정상 처리 중")
    log.warning("주의할 점이 있지만 오류는 아님")
    assert not err.exists(), "오류가 없는데 로그 파일이 생겼다"


#------------------------------------------------------------------
# 오류가 나면 그때 파일이 생기고 내용이 들어간다 (약속 ①)
#------------------------------------------------------------------
def test_file_created_on_error(tmp_path):
    err = tmp_path / "err.log"
    logsetup.setup_logging(log_path=None, verbose=False, err_log_path=str(err))
    log = logsetup.get_logger("csoclassify.test")
    log.info("이건 안 들어가야 한다")
    log.error("규칙셋을 찾을 수 없습니다")
    assert err.exists()
    body = err.read_text(encoding="utf-8")
    assert "규칙셋을 찾을 수 없습니다" in body
    assert "ERROR" in body
    # INFO 는 오류 파일에 섞이지 않아야 한다 — 섞이면 '오류만 보는' 목적이 깨진다.
    assert "이건 안 들어가야 한다" not in body


#------------------------------------------------------------------
# fail_code() 는 화면과 오류 로그 양쪽에 남기고 종료코드를 돌려준다
#=> 이름 그대로 '코드를 돌려줄 뿐' 프로세스를 끝내지 않는다. 호출부는 반드시
#   return 과 함께 써야 하므로, 여기서도 반환값이 그대로 나오는지 확인한다.
#------------------------------------------------------------------
def test_fail_helper_logs_and_returns_code(tmp_path, capsys):
    err = tmp_path / "err.log"
    logsetup.setup_logging(log_path=None, verbose=False, err_log_path=str(err))
    code = cli.fail_code("[csoclassify] 처리할 파일이 없습니다.\n  --dir 을 지정하세요", 3)
    assert code == 3
    # 화면(stderr)에는 여러 줄 그대로.
    assert "처리할 파일이 없습니다" in capsys.readouterr().err
    # 로그에는 한 줄로 눌러 담는다(한 사건이 여러 건처럼 보이지 않게).
    body = err.read_text(encoding="utf-8")
    assert "처리할 파일이 없습니다. /   --dir 을 지정하세요" in body
    assert len([ln for ln in body.splitlines() if ln.strip()]) == 1


#------------------------------------------------------------------
# 잡히지 않은 예외도 스택까지 남고, 코드 1 로 환원된다 (약속 ②)
#=> exe 환경에서는 트레이스백이 화면과 함께 사라진다. main() 바깥 껍데기가
#   그것을 받아 로그에 남기는지 확인한다.
#------------------------------------------------------------------
def test_unexpected_exception_is_logged_with_traceback(tmp_path, monkeypatch, capsys):
    err = tmp_path / "err.log"
    monkeypatch.setenv("CSOCLASSIFY_ERRLOG", str(err))
    logsetup.setup_logging(log_path=None, verbose=False, err_log_path=str(err))

    def boom(argv=None):
        raise RuntimeError("일부러 낸 내부 오류")

    monkeypatch.setattr(cli, "_main", boom)
    assert cli.main([]) == 1
    body = err.read_text(encoding="utf-8")
    assert "일부러 낸 내부 오류" in body
    assert "RuntimeError" in body
    # 스택이 있어야 어디서 터졌는지 알 수 있다.
    assert "Traceback" in body
    # 사용자에게는 로그 위치를 알려 준다.
    assert "오류 로그" in capsys.readouterr().err


#------------------------------------------------------------------
# Ctrl+C 는 오류가 아니다 (약속 ③)
#=> 사용자가 일부러 멈춘 것을 오류로 쌓으면, 진짜 오류를 찾을 때 방해가 된다.
#------------------------------------------------------------------
def test_keyboard_interrupt_is_not_an_error(tmp_path, monkeypatch):
    err = tmp_path / "err.log"
    logsetup.setup_logging(log_path=None, verbose=False, err_log_path=str(err))

    def stop(argv=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_main", stop)
    assert cli.main([]) == 130
    assert not err.exists(), "Ctrl+C 가 오류 로그에 쌓였다"


#------------------------------------------------------------------
# sys.exit() 는 정상 흐름이라 껍데기가 가로채지 않는다
#=> argparse 가 인자 오류로 SystemExit(2) 를 던지는데, 이걸 삼켜 1 로 바꿔 버리면
#   배치 스크립트가 보던 종료코드가 달라진다.
#------------------------------------------------------------------
def test_system_exit_passes_through(monkeypatch):
    def bye(argv=None):
        raise SystemExit(2)

    monkeypatch.setattr(cli, "_main", bye)
    with pytest.raises(SystemExit) as ei:
        cli.main([])
    assert ei.value.code == 2
