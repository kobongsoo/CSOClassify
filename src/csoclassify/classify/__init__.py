#------------------------------------------------------------------
# classify 서브패키지 — 수집 문서 C/S/O 자동 분류
#=> CSOClassify가 뽑아 놓은 "정제 텍스트"와 "임베딩 벡터"를 소비해 문서를
#   기밀(C)/민감(S)/공개(O) 로 등급화하는 정책층이다. 추출·임베딩·데몬 코어는
#   건드리지 않고, 그 결과만 소비하는 소비자(consumer)로만 존재한다.
#
#   현재 구현 범위: L1 정규식 PII + L2 기밀사전 규칙 스캔(Signal A).
#   (Signal B 임베딩 라벨 전파, C 경로/ACL 은 후속 단계에서 추가)
#------------------------------------------------------------------

from .rules import (
    RuleSet,
    RuleHit,
    GradeSignal,
    StampSignal,
    SensitiveSignal,
    load_rules,
    default_rules_path,
    default_seed_path,
    scan_text,
    scan_stamp,
    scan_sensitive,
    max_grade,
)
from .context import PathSignal, NameSignal, scan_path, scan_filename
from .fuse import FusionResult, fuse_signals
from .propagate import EmbedSignal, SeedIndex, propagate
from .engine import build_record, now_iso, propagate_records

__all__ = [
    "RuleSet",
    "RuleHit",
    "GradeSignal",
    "StampSignal",
    "SensitiveSignal",
    "load_rules",
    "default_rules_path",
    "default_seed_path",
    "scan_text",
    "scan_stamp",
    "scan_sensitive",
    "max_grade",
    "PathSignal",
    "NameSignal",
    "scan_path",
    "scan_filename",
    "FusionResult",
    "fuse_signals",
    "EmbedSignal",
    "SeedIndex",
    "propagate",
    "build_record",
    "now_iso",
    "propagate_records",
]
