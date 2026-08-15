#------------------------------------------------------------------
# PyInstaller/실행 진입 스크립트 (패키지 밖 엔트리)
#=> PyInstaller 는 엔트리 스크립트를 최상위(__main__)로 실행하므로, 패키지
#   내부의 상대 import(`from .cli ...`)를 쓰는 __main__.py 는 엔트리로 못 쓴다.
#   이 파일은 패키지 밖에서 **절대 import** 로 cli.main 을 불러 그 문제를 피한다.
#   (`python -m csoclassify` 는 여전히 csoclassify/__main__.py 를 쓴다)
#
# -in: 없음(sys.argv 사용)
#
# -out: 없음(sys.exit 로 종료코드 반환)
# -out: error = 없음(예외는 cli.main 내부에서 코드로 환원)
#------------------------------------------------------------------

import sys

from csoclassify.cli import main

if __name__ == "__main__":
    sys.exit(main())
