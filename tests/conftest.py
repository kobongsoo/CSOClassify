#------------------------------------------------------------------
# 테스트 공통 설정
#=> 테스트가 설치 없이도 src/ 의 csoclassify 패키지를 import 할 수 있게 경로를 추가한다.
#
# -in: 없음
#
# -out: 없음(sys.path 부작용)
# -out: error = 없음
#------------------------------------------------------------------

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
