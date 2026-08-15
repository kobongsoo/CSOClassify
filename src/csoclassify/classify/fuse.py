#------------------------------------------------------------------
# 등급 융합 (Signal 결합 → 최종 등급) — 설계서 §07
#=> 여러 신호(규칙·경로·파일명)의 등급 후보를 하나로 합친다. 원칙은 "보수적
#   최댓값 + fail-safe": 충돌 시 항상 높은 등급을 택하고, 아무 신호도 없으면
#   안전측 기본값으로 떨어뜨린다. (임베딩 전파 Signal B 는 이후 여기에 후보로
#   추가되며, '상향에만' 기여하도록 같은 max 구조에 얹으면 된다.)
#------------------------------------------------------------------

from dataclasses import dataclass, field

from .rules import max_grade


#------------------------------------------------------------------
# 융합 결과
#=> 최종 등급과 그 근거(어떤 신호가 결정했는지)를 담는다.
#
# -필드: grade         = 최종 등급(C/S/O) 또는 None(미분류)
# -필드: confidence    = 최종 등급을 만든 신호들의 최고 신뢰도
# -필드: seed_eligible = 이 문서를 전파 seed 로 승격해도 되는가
# -필드: method        = 결정 방식("fusion"|"failsafe_acl"|"failsafe_default"|"unclassified")
# -필드: decided_by    = 최종 등급을 만든 신호 이름들(예: ["rule"], ["path","name"])
#------------------------------------------------------------------
@dataclass(frozen=True)
class FusionResult:
    grade: str
    confidence: float
    seed_eligible: bool
    method: str
    decided_by: list = field(default_factory=list)


#------------------------------------------------------------------
# 신호 융합(핵심)
#=> 이름표가 붙은 신호들을 모아 최종 등급을 정한다.
#    1) 등급이 있는 신호만 추린다
#    2) 있으면 max_grade 로 최종등급 → 그 등급을 만든 신호들의 최고 신뢰도·seed 채택
#    3) 없고 acl_restricted 면 fail-safe 로 C(강한 제한은 내용 몰라도 기밀 취급)
#    4) 그것도 아니고 failsafe 지정 시 그 등급(예: S), 아니면 미분류(None)
#   [원칙] 등급은 올릴 수 있어도 임의로 내리지 않는다 → 항상 최댓값을 쓴다.
#
# -in: signals  = [(이름, 신호객체)] 리스트. 각 신호는 .grade/.confidence/.seed_eligible
#                 속성을 가진다(경로 신호는 .acl_restricted 도).
# -in: failsafe = 신호가 하나도 없을 때 부여할 기본등급("S"/"C" 등, 없으면 None)
#
# -out: FusionResult
# -out: error = 없음
#------------------------------------------------------------------
def fuse_signals(signals, failsafe=None):
    # 등급을 실제로 낸 신호만 후보로. (grade=None 인 신호는 판단에서 제외)
    present = [(name, s) for name, s in signals if getattr(s, "grade", None)]
    # 경로 신호 중 강한 제한 표식이 하나라도 있는지(내용 없이도 C 근거).
    acl = any(getattr(s, "acl_restricted", False) for _, s in signals)

    if present:
        final = max_grade(s.grade for _, s in present)
        # 최종 등급을 실제로 만든(=같은 등급) 신호만 신뢰도·seed·근거로 삼는다.
        deciding = [(name, s) for name, s in present if s.grade == final]
        confidence = max(s.confidence for _, s in deciding)
        seed_eligible = any(getattr(s, "seed_eligible", False) for _, s in deciding)
        decided_by = [name for name, _ in deciding]
        return FusionResult(final, confidence, seed_eligible, "fusion", decided_by)

    # 신호는 없지만 강한 제한 경로 → 보수적으로 C.
    if acl:
        return FusionResult("C", 0.70, True, "failsafe_acl", ["path"])

    # 아무 근거도 없음 → 지정된 안전측 기본값 또는 미분류.
    if failsafe:
        return FusionResult(failsafe, 0.0, False, "failsafe_default", [])
    return FusionResult(None, 0.0, False, "unclassified", [])
