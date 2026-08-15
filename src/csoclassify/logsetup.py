#------------------------------------------------------------------
# 로깅 설정 (--log / --verbose)
#=> 프로그램 전체가 공유하는 'csoclassify' 로거를 한 곳에서 구성한다. 파일 로그는
#   --log 경로가 있을 때, 화면(stderr) 로그는 --verbose 일 때만 켠다. 데몬도 같은
#   함수를 써서 자신이 받은 --log 경로에 처리 로그를 남긴다(데몬은 콘솔이 없으므로).
#   각 모듈은 get_logger(__name__) 으로 자식 로거를 얻어 쓰면 이름/PID 가 함께 찍힌다.
#------------------------------------------------------------------

import logging
import os
import sys
import time

from . import resources

# 모든 모듈 로거의 공통 부모 이름. 이 로거에만 핸들러를 단다.
_ROOT_NAME = "csoclassify"


#------------------------------------------------------------------
# 기본 로그 파일 경로
#=> --log 를 안 줬을 때 쓰는 경로. "실행된 csoclassify.exe 가 있는 폴더" 아래 log/
#   폴더에 날짜별 파일로 남긴다(<exe폴더>/log/csoclassify-YYYYMMDD.log). 소스 실행 때는
#   프로젝트 루트 아래 log/ 가 된다. 날짜별로 나눠 파일이 무한정 커지지 않게 한다.
#
# -in: 없음
#
# -out: path = 기본 로그 파일 절대경로(폴더는 setup_logging 이 만든다)
# -out: error = 없음
#------------------------------------------------------------------
def default_log_path():
    # 로컬시각 기준 날짜 태그(클라이언트/데몬이 같은 날이면 같은 파일을 공유).
    day = time.strftime("%Y%m%d")
    return os.path.join(resources.exe_dir(), "log", f"csoclassify-{day}.log")


#------------------------------------------------------------------
# 모듈용 로거 얻기
#=> get_logger("csoclassify.pipeline") 처럼 자식 로거를 돌려준다. 자식은 부모
#   ('csoclassify')의 핸들러/레벨을 물려받아, 설정을 한 곳에서만 관리하면 된다.
#
# -in: name = 보통 모듈의 __name__ (예: "csoclassify.embed.onnx_embedder")
#
# -out: logger = logging.Logger (부모 'csoclassify' 의 자식)
# -out: error = 없음
#------------------------------------------------------------------
def get_logger(name):
    return logging.getLogger(name)


#------------------------------------------------------------------
# 로깅 구성 (핵심)
#=> 'csoclassify' 로거에 파일/화면 핸들러를 붙이고 레벨을 정한다. 프로세스마다 시작 시
#   한 번 부른다(클라이언트/데몬/in-process 공통).
#    1) 레벨: verbose 면 DEBUG, 아니면 INFO
#    2) --log 있으면 파일 핸들러(append, UTF-8) — 상위 폴더 없으면 만든다
#       2-1) 파일 핸들러를 붙인 직후, 이번 실행 시작을 알리는 구분선("---")을 파일에
#            먼저 남겨 이전 실행 로그와 눈으로 구분되게 한다
#    3) verbose 면 stderr 핸들러도 추가(화면에서 실시간 확인)
#    4) 핸들러가 하나도 없으면 NullHandler 로 'No handlers' 경고 방지
#
# -in: log_path = 로그 파일 경로(None 이면 파일 로그 없음)
# -in: verbose  = True 면 DEBUG 레벨 + 화면(stderr) 출력
#
# -out: logger = 구성된 'csoclassify' 로거
# -out: error = 없음(파일 열기 실패는 조용히 무시하고 화면/Null 로 진행)
#------------------------------------------------------------------
def setup_logging(log_path=None, verbose=False):
    logger = logging.getLogger(_ROOT_NAME)
    # 자식→부모까지만 흐르게 하고 루트로는 전파하지 않아 중복 출력을 막는다.
    logger.propagate = False
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    # 재호출(테스트/재설정) 시 핸들러가 쌓이지 않도록 먼저 비운다.
    for h in list(logger.handlers):
        logger.removeHandler(h)

    # PID 를 넣어 클라이언트/데몬 로그를 한 파일에서도 구분할 수 있게 한다.
    fmt = logging.Formatter(
        "%(asctime)s [PID %(process)d] %(levelname)s %(name)s: %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    # (2) 파일 로그: --log 가 있을 때만. 데몬도 이 경로로 남긴다.
    if log_path:
        try:
            # 상대경로는 현재 작업폴더 기준. 상위 폴더가 없으면 만들어 준다.
            parent = os.path.dirname(os.path.abspath(log_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            fh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
            # 새 실행 시작을 이전 로그와 눈으로 구분하도록 구분선을 파일에 먼저 남긴다.
            # 직접 stream 에 써서 타임스탬프/PID 접두 없이 구분선만 남긴다. 파일은 text
            # 모드라 '\n' 이 Windows 에서 '\r\n' 으로 저장돼 기존 로그 줄과 개행이 일치한다.
            try:
                fh.stream.write("-------------\n")
                fh.stream.flush()
            except Exception:
                # 구분선은 부가 기능이라 실패해도 본 로깅은 계속되어야 한다.
                pass
        except OSError:
            # 로그 파일을 못 열어도 본 기능은 계속되어야 하므로 조용히 넘어간다.
            pass

    # (3) 화면 로그: verbose 일 때만(평소엔 stdout/stderr 를 깔끔하게 유지).
    if verbose:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    # (4) 아무 핸들러도 없으면 INFO/DEBUG 는 조용히 버려지도록 NullHandler 를 둔다.
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

    return logger
