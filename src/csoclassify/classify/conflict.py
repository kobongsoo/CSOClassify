#------------------------------------------------------------------
# 축별 충돌 해소 전략 — 여러 신호/후보가 겹칠 때 무엇을 낼지
#=> "여러 신호가 충돌하면 어떻게 할 것인가"는 축의 성격에서 자동으로 따라
#   나온다(설계서 6-2). 서열이 있는 축(security)은 더 위험한 쪽 하나만
#   남기고(max), 서열이 없는 축(doctype)은 해당하는 것을 전부(all) 또는
#   상위 N개(top_n) 낸다. 이 모듈은 그 판단 자체를 "후보 목록 → 선택 결과"의
#   순수 함수로 떼어내, fuse.py(security)와 향후 doctype 스캔 엔진이 같은
#   전략 구현을 공유하게 한다. 후보/신호 객체의 구체적인 모양(GradeSignal 등)
#   은 몰라도 되도록, 여기서는 {value, confidence, from, ...} 형태의 평범한
#   dict 만 다룬다(설계서 7-1 결과 레코드의 candidates 필드와 같은 모양).
#   (설계: 문서분류체계 연동 설계서 §6, 로드맵 D3)
#------------------------------------------------------------------

from dataclasses import dataclass


#------------------------------------------------------------------
# max 전략 결과
#=> 서열이 있는 축(현재 security)에서, 후보 중 가장 서열이 높은 값 하나를
#   고른 결과. cso_rules.yaml 기반 fuse.py 의 FusionResult 가 이 결과를
#   감싸 최종 레코드를 만든다.
#
# -필드: value      = 채택된 값(예: "C"). 후보가 하나도 없으면 None
# -필드: confidence = 채택 값을 만든 후보들의 최고 신뢰도(후보 없으면 0.0)
# -필드: decided_by = 채택 값을 만든 후보들의 출처("from") 튜플(설계서
#                      7-1 의 decided_by 와 같은 목적)
# -필드: candidates = 입력받은 후보 전체(값이 있는 것만, 원래 순서 그대로) —
#                      채택되지 않은 후보도 감사를 위해 보존한다(설계서
#                      7-1 note: "max 로 하나를 고르는 순간 나머지 근거가
#                      사라진다"를 막기 위한 필드)
#------------------------------------------------------------------
@dataclass(frozen=True)
class MaxResolution:
    value: str
    confidence: float
    decided_by: tuple = ()
    candidates: tuple = ()


#------------------------------------------------------------------
# all/top_n 전략 결과
#=> 서열이 없는 축(현재 doctype)에서, 임계값을 넘는 후보를 신뢰도 내림차순
#   으로 고른 결과. top_n 이 아니면(=all) truncated 는 항상 0이다.
#
# -필드: values    = 채택된 후보들(신뢰도 내림차순, 원본 dict 그대로 보존)
# -필드: truncated = top_n 으로 잘려나간 후보 수(all 이면 항상 0). 설계서
#                     6-3 note — "잘라낸 것은 반드시 알린다"를 위한 필드
#------------------------------------------------------------------
@dataclass(frozen=True)
class MultiResolution:
    values: tuple = ()
    truncated: int = 0


#------------------------------------------------------------------
# max 전략 — 서열 최댓값 1개 채택
#=> "어느 쪽이 더 위험한가"에 정당한 답이 있는 축 전용이다(설계서 6-3).
#   서열·정의되지 않은 값에 대한 fail-closed 판단은 이 함수가 직접 하지
#   않고 max_fn 에 맡긴다 — security 축은 rules.max_grade 를 그대로 넘기면
#   되고, 그 함수가 오타 등급에 UnknownGradeError 를 던지는 기존 계약이
#   토씨 하나 안 바뀌고 유지된다(로드맵 D3 완료 판정: "max 결과가 이전과
#   완전히 같다"). 이 함수가 새로 하는 일은 "후보 목록 → 채택 결과" 조립뿐이다.
#    1) 값이 있는 후보만 추린다(값 없는 후보=이 신호는 판단하지 않았다는 뜻)
#    2) 후보가 하나도 없으면 채택 없음(value=None)으로 끝낸다
#    3) max_fn 으로 최종값을 정한다(정의되지 않은 값이면 max_fn 이 예외를 던짐)
#    4) 최종값과 같은 값을 낸 후보들만 다시 골라 신뢰도 최댓값·출처를 모은다
#
# -in: candidates = [{"value":..., "confidence":..., "from":...}, ...] 후보 목록.
#                    confidence·from 은 없으면 각각 0.0/None 으로 취급한다
# -in: max_fn     = 값들의 반복가능 객체를 받아 "가장 서열 높은 값"을 돌려주는
#                    함수. 정의되지 않은 값을 만나면 예외를 던져야 한다
#                    (fail-closed) — security 축은 rules.max_grade 를 쓴다
#
# -out: MaxResolution
# -out: error = max_fn 이 던지는 예외를 그대로 전파한다(값 검증은 호출자 몫)
#------------------------------------------------------------------
def resolve_max(candidates, max_fn):
    present = tuple(c for c in candidates if c.get("value") is not None)
    if not present:
        return MaxResolution(value=None, confidence=0.0, decided_by=(), candidates=())

    final = max_fn(c["value"] for c in present)
    deciding = [c for c in present if c["value"] == final]
    confidence = max(c.get("confidence", 0.0) for c in deciding)
    decided_by = tuple(c["from"] for c in deciding if c.get("from") is not None)
    return MaxResolution(value=final, confidence=confidence, decided_by=decided_by,
                         candidates=present)


#------------------------------------------------------------------
# all/top_n 전략 — 임계값을 넘는 후보를 신뢰도 순으로(선택 개수 제한 가능)
#=> "이 문서가 A 인지 B 인지, 혹은 둘 다인지"에 답하는 축 전용이다(설계서
#   6-3). n=None(기본)이면 all — 개수를 자르지 않는다. n 을 주면 top_n —
#   그 개수만큼만 남기고 잘려나간 수를 truncated 로 알린다.
#    1) min_confidence 미만인 후보는 뺀다
#    2) 신뢰도 내림차순 정렬. 동점이면 dc_id 오름차순으로 확정한다
#       [재설계 8-5] 예전에는 입력 순서에 맡겼는데, doctype 후보는 set 순회로
#       만들어져 입력 순서 자체가 실행마다 달라졌다. 신뢰도가 이산값이라 동점이
#       흔해, top_n 을 켜면 "같은 문서를 두 번 돌리면 결과가 다름"이 됐다.
#       재현성은 우연에 맡길 수 없으므로 2차 키를 명시한다.
#    3) n 이 없으면 전부 채택, truncated=0
#    4) n 이 있으면 상위 n 개만 남기고 나머지 수를 truncated 로 기록
#
# -in: candidates     = [{"confidence":..., ...}, ...] 후보 목록. confidence
#                        없으면 0.0 으로 취급. dc_id·path 등 다른 필드는
#                        건드리지 않고 그대로 통과시킨다
# -in: n              = 상위 몇 개를 남길지(None=전부, 기본). top_n 전략일 때만 지정
# -in: min_confidence = 이 값 미만인 후보는 채택하지 않는다(기본 0.0=전부 통과)
#
# -out: MultiResolution
# -out: error = 없음
#------------------------------------------------------------------
def resolve_multi(candidates, n=None, min_confidence=0.0):
    kept = [c for c in candidates if c.get("confidence", 0.0) >= min_confidence]
    # 신뢰도는 내림차순, 동점이면 dc_id 오름차순. dc_id 가 없는 후보(security 축
    # 등 다른 호출자)는 빈 문자열로 취급해 종전처럼 안정 정렬로 남는다.
    kept.sort(key=lambda c: (-c.get("confidence", 0.0), str(c.get("dc_id", ""))))

    if n is None:
        return MultiResolution(values=tuple(kept), truncated=0)

    truncated = max(0, len(kept) - n)
    return MultiResolution(values=tuple(kept[:n]), truncated=truncated)


#------------------------------------------------------------------
# doc_rule.yaml 의 conflict: 설정으로 all/top_n 실행
#=> doc_rules.ConflictSpec(D2 의 로더 산출물)을 그대로 받아 resolve_multi 를
#   호출하는 얇은 다리 역할. doctype 축 스캔 엔진(D4)은 이 함수 하나만
#   부르면 conflict.yaml 문법을 몰라도 된다.
#
# -in: candidates = resolve_multi 와 동일
# -in: conflict    = doc_rules.ConflictSpec(strategy="all"|"top_n", n, min_confidence)
#
# -out: MultiResolution
# -out: error = 없음(conflict.strategy 가 "all"/"top_n" 이외의 값이면 all 로
#                취급 — doc_rules.validate_doc_rule_data(T2)가 로드 시점에
#                이미 걸러내므로 정상 경로에서는 일어나지 않는다)
#------------------------------------------------------------------
def resolve_doctype(candidates, conflict):
    min_confidence = conflict.min_confidence if conflict.min_confidence is not None else 0.0
    n = conflict.n if conflict.strategy == "top_n" else None
    return resolve_multi(candidates, n=n, min_confidence=min_confidence)


# 축마다 전략을 실행 시점에 덮어쓸 수 있는지(설계서 6-5). security 는 서열이
# 있어 "더 안전한 쪽"이 이미 정해져 있으므로, 실행 옵션으로도 못 바꾼다 —
# 바꿀 수 있게 열어두면 "실수로 등급이 약해지는" 사고 경로가 생긴다(R4).
FIXED_STRATEGY_AXES = ("security",)


#------------------------------------------------------------------
# 축 전략 덮어쓰기 금지 예외 (T11)
#=> --conflict 로 고정 전략 축(security)을 덮어쓰려는 시도에 던진다.
#
# -필드: axis = 덮어쓰려 한 축 id
#------------------------------------------------------------------
class AxisNotOverridableError(ValueError):

    #------------------------------------------------------------------
    # 예외 생성
    #
    # -in: axis = 덮어쓰려 한 축 id(예: "security")
    #
    # -out: 없음(생성자)
    # -out: error = 없음
    #------------------------------------------------------------------
    def __init__(self, axis):
        self.axis = axis
        super().__init__(
            f"'{axis}' 축은 전략을 실행 시에도 바꿀 수 없습니다(설계 T11) — "
            f"이 축은 서열이 있어 안전측(max)이 유일하게 정당한 선택입니다."
        )


#------------------------------------------------------------------
# 축 전략 덮어쓰기 가능 여부 확인 (T11)
#=> --conflict <axis>=<strategy> 형태의 실행 옵션(cli.py, 향후 D4)이 실제로
#   전략을 바꾸기 전에 이 함수를 불러 확인한다. FIXED_STRATEGY_AXES 에 있는
#   축이면 조용히 무시하지 않고 예외로 막는다 — "보안 등급이 실수로 약해지는"
#   경로를 검증 단계에서 원천 차단하기 위해서다(설계서 R4).
#
# -in: axis = 덮어쓰려는 축 id
#
# -out: 없음(문제 없으면 통과)
# -out: error = axis 가 FIXED_STRATEGY_AXES 에 있으면 AxisNotOverridableError
#------------------------------------------------------------------
def ensure_overridable_axis(axis):
    if axis in FIXED_STRATEGY_AXES:
        raise AxisNotOverridableError(axis)
