#------------------------------------------------------------------
# 모듈 실행 진입점 (python -m csoclassify / PyInstaller 엔트리)
#=> `python -m csoclassify ...` 또는 exe 로 실행될 때 cli.main 을 호출하고 그
#   반환값을 프로세스 종료코드로 넘긴다.
#
# -in: 없음(sys.argv 사용)
#
# -out: 없음(sys.exit 로 종료코드 반환)
# -out: error = 없음(예외는 cli.main 내부에서 코드로 환원)
#------------------------------------------------------------------

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
