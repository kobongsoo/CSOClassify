#------------------------------------------------------------------
# 임베딩 서브패키지 초기화
#=> 텍스트 → 벡터 변환기(Embedder)들을 모은 패키지. 기본 구현은 ONNX 기반.
#
# -필드: 없음(재노출만)
#------------------------------------------------------------------

from .base import Embedder, EmbedError
from .onnx_embedder import OnnxEmbedder

__all__ = ["Embedder", "EmbedError", "OnnxEmbedder"]
