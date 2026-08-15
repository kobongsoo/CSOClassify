#------------------------------------------------------------------
# 추출 서브패키지 초기화
#=> 문서 → 텍스트 추출기들을 모은 패키지. 기본 구현(SynapExeExtractor)만
#   편의상 최상위로 끌어올려 import 를 짧게 한다.
#
# -필드: 없음(재노출만)
#------------------------------------------------------------------

from .base import TextExtractor, ExtractError
from .synap_exe import SynapExeExtractor

__all__ = ["TextExtractor", "ExtractError", "SynapExeExtractor"]
