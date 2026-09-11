#------------------------------------------------------------------
# 등급 융합 (Signal 결합 → 최종 등급) — 설계서 §07
#=> 여러 신호(규칙·경로·파일명)의 등급 후보를 하나로 합친다. 원칙은 "보수적
#   최댓값 + fail-safe": 충돌 시 항상 높은 등급을 택하고, 아무 신호도 없으면
#   안전측 기본값으로 떨어뜨린다. (임베딩 전파 Signal B 는 이후 여기에 후보로
#   추가되며, '상향에만' 기여하도록 같은 max 구조에 얹으면 된다.)
#
#   "여러 후보 중 무엇을 채택할지"의 핵심 판단(=max 전략)은 conflict.py 로
#   빼냈다(설계서 6장, 로드맵 D3) — security 축은 서열이 있어 max 만 정당한
#   선택이라 이 함수는 여전히 conflict.resolve_max 를 호출할 뿐이지만, 그
#   전략 자체가 이제 축(taxonomy 등)마다 다른 함수를 쓸 수 있는 일급 개념이
#   됐다. acl fail-safe·failsafe_default 처럼 "후보가 아예 없을 때" 안전측
#   등급을 매기는 판단은 security 축 고유의 정책이라 이 파일에 그대로 둔다.
#------------------------------------------------------------------

from dataclasses import dataclass, field

from .rules import max_grade
from .conflict import resolve_max


#------------------------------------------------------------------
# 융합 결과
#=> 최종 등급과 그 근거(어떤 신호가 결정했는지)를 담는다.
#
# -필드: grade         = 최종 등급(C/S/O) 또는 None(미분류)
# -필드: confidence    = 최종 등급을 만든 신호들의 최고 신뢰도
# -필드: seed_eligible = 이 문서를 전파 seed 로 승격해도 되는가
# -필드: method        = 결정 방식("fusion"|"failsafe_default"|"unclassified")
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
#    1) 등급이 있는 신호만 후보 dict 로 바꾼다({value, confidence, from})
#    2) 있으면 conflict.resolve_max(max_grade) 로 최종등급 채택 → 그 등급을
#       만든 신호(decided_by)들 중에서만 seed_eligible 을 다시 찾는다
#       ↳ 이 분기는 '등급을 내지 않는 경로 규칙'(grade 생략 + acl_restricted: true)이
#         있어야 도달한다. 그런 규칙을 쓰지 않으면 예전처럼 실행되지 않는다.
#    4) 그것도 아니고 failsafe 지정 시 그 등급(예: S), 아니면 미분류(None)
#   [원칙] 등급은 올릴 수 있어도 임의로 내리지 않는다 → 항상 최댓값을 쓴다.
#   [정의되지 않은 등급] resolve_max 에 넘기는 max_fn 이 rules.max_grade 그대로라,
#   오타 등급을 만나면 예전과 똑같이 UnknownGradeError 가 그대로 전파된다
#   (cli.py 가 이 예외 타입으로 잡아 처리하므로 바뀌면 안 된다).
#
# -in: signals  = [(이름, 신호객체)] 리스트. 각 신호는 .grade/.confidence/.seed_eligible
#                 속성을 가진다(경로 신호는 .acl_restricted 도).
# -in: failsafe = 신호가 하나도 없을 때 부여할 기본등급("S"/"C" 등, 없으면 None)
#
# -out: FusionResult
# -out: error = 없음(등급 값이 정의되지 않았으면 UnknownGradeError 전파)
#------------------------------------------------------------------
def fuse_signals(signals, failsafe=None):
    # 등급을 실제로 낸 신호만 후보로. (grade=None 인 신호는 판단에서 제외)
    candidates = [
        {"value": s.grade, "confidence": s.confidence, "from": name}
        for name, s in signals if getattr(s, "grade", None)
    ]
    if candidates:
        res = resolve_max(candidates, max_fn=max_grade)
        # 최종 등급을 실제로 만든(=decided_by) 신호만 다시 찾아 seed 자격을 본다.
        by_name = dict(signals)
        seed_eligible = any(getattr(by_name[name], "seed_eligible", False)
                            for name in res.decided_by)
        return FusionResult(res.value, res.confidence, seed_eligible, "fusion",
                            list(res.decided_by))

    # [2026-09-08] 경로 신호(Signal C)와 그 fail-safe(failsafe_acl)를 걷어냈다.
    #   경로가 등급을 내던 자리는 실행할 때 --failsafe 로 대신한다.
    #   설계: plan/보안등급-신호정리-paths제거-20260908.html 6장
    #   (그 안의 빈틈 — '못 읽은 문서'에는 --failsafe 가 적용되지 않는다 — 도 거기 적혀 있다.)

    # 아무 근거도 없음 → 지정된 안전측 기본값 또는 미분류.
    if failsafe:
        return FusionResult(failsafe, 0.0, False, "failsafe_default", [])
    return FusionResult(None, 0.0, False, "unclassified", [])
