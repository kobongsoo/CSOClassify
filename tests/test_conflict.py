#------------------------------------------------------------------
# 축별 충돌 해소 전략(conflict.py) 단위 테스트
#=> max(security)·all/top_n(doctype) 전략이 설계서 6장대로 동작하는지,
#   그리고 max 전략이 기존 rules.max_grade 의 fail-closed 계약(정의되지 않은
#   값은 예외)을 그대로 물려받는지 검증한다(로드맵 D3 완료 판정).
#------------------------------------------------------------------

import pytest

from csoclassify.classify import conflict as C
from csoclassify.classify import doc_rules as D
from csoclassify.classify.rules import max_grade, UnknownGradeError, GRADES


# ── resolve_max ────────────────────────────────────────────────────

#------------------------------------------------------------------
# 후보가 없으면 채택 없음
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_resolve_max_후보없음():
    res = C.resolve_max([], max_fn=max_grade)
    assert res.value is None
    assert res.confidence == 0.0
    assert res.decided_by == ()
    assert res.candidates == ()


#------------------------------------------------------------------
# value=None 인 후보는 경쟁에서 제외된다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_resolve_max_value_None인_후보는_제외():
    candidates = [{"value": None, "confidence": 0.9, "from": "rule"}]
    res = C.resolve_max(candidates, max_fn=max_grade)
    assert res.value is None


#------------------------------------------------------------------
# 서열이 높은 쪽이 채택된다(설계서 6-3 예시: S·C·O 중 C)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_resolve_max_서열_최댓값_채택():
    candidates = [
        {"value": "S", "confidence": 0.90, "from": "rule"},
        {"value": "C", "confidence": 0.95, "from": "stamp"},
        {"value": "O", "confidence": 0.90, "from": "path"},
    ]
    res = C.resolve_max(candidates, max_fn=max_grade)
    assert res.value == "C"
    assert res.confidence == 0.95
    assert res.decided_by == ("stamp",)
    assert len(res.candidates) == 3   # 채택 안 된 후보도 감사용으로 보존


#------------------------------------------------------------------
# 같은 최고 등급을 낸 후보가 여럿이면 전부 decided_by 에 담기고, 신뢰도는
# 그중 최댓값이다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_resolve_max_동점_후보는_모두_decided_by():
    candidates = [
        {"value": "C", "confidence": 0.80, "from": "rule"},
        {"value": "C", "confidence": 0.95, "from": "stamp"},
        {"value": "S", "confidence": 0.99, "from": "path"},
    ]
    res = C.resolve_max(candidates, max_fn=max_grade)
    assert res.value == "C"
    assert res.confidence == 0.95
    assert set(res.decided_by) == {"rule", "stamp"}


#------------------------------------------------------------------
# 정의되지 않은 값은 max_fn 의 예외가 그대로 전파된다(fail-closed 계약 유지)
#=> fuse.py 를 conflict.resolve_max 호출로 바꾼 뒤에도 cli.py 가 잡는
#   UnknownGradeError 계약이 깨지지 않아야 한다(로드맵 D3 완료 판정).
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_resolve_max_정의되지_않은_값은_예외_전파():
    candidates = [{"value": "정의안됨", "confidence": 0.9, "from": "rule"}]
    with pytest.raises(UnknownGradeError):
        C.resolve_max(candidates, max_fn=max_grade)


#------------------------------------------------------------------
# max_fn 은 축마다 다른 서열/검증 함수를 끼워 넣을 수 있어야 한다(범용성 확인)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_resolve_max_다른_max_fn도_동작():
    def custom_max(values):
        order = {"low": 0, "high": 1}
        return max(values, key=lambda v: order[v])

    candidates = [
        {"value": "low", "confidence": 0.5, "from": "a"},
        {"value": "high", "confidence": 0.6, "from": "b"},
    ]
    res = C.resolve_max(candidates, max_fn=custom_max)
    assert res.value == "high"
    assert res.decided_by == ("b",)


# ── resolve_multi ──────────────────────────────────────────────────

#------------------------------------------------------------------
# n=None(all)이면 임계값 넘는 후보를 신뢰도 내림차순으로 전부 낸다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_resolve_multi_all은_전부_내림차순():
    candidates = [
        {"dc_id": "A", "confidence": 0.70},
        {"dc_id": "B", "confidence": 0.85},
        {"dc_id": "C", "confidence": 0.60},
    ]
    res = C.resolve_multi(candidates, n=None)
    assert [c["dc_id"] for c in res.values] == ["B", "A", "C"]
    assert res.truncated == 0


#------------------------------------------------------------------
# min_confidence 미만은 제외된다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_resolve_multi_min_confidence_미만은_제외():
    candidates = [
        {"dc_id": "A", "confidence": 0.70},
        {"dc_id": "B", "confidence": 0.30},
    ]
    res = C.resolve_multi(candidates, min_confidence=0.5)
    assert [c["dc_id"] for c in res.values] == ["A"]


#------------------------------------------------------------------
# top_n 은 개수를 자르고 truncated 로 알린다(설계서 6-3 note)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_resolve_multi_top_n은_잘라내고_truncated_보고():
    candidates = [{"dc_id": str(i), "confidence": i / 10} for i in range(7)]
    res = C.resolve_multi(candidates, n=3)
    assert len(res.values) == 3
    assert res.truncated == 4   # 7건 중 3건만 채택 → 4건 잘림
    assert [c["dc_id"] for c in res.values] == ["6", "5", "4"]


#------------------------------------------------------------------
# 자를 후보가 없으면(전체가 n 이하) truncated=0
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_resolve_multi_n이_충분히_크면_truncated_0():
    candidates = [{"dc_id": "A", "confidence": 0.9}]
    res = C.resolve_multi(candidates, n=5)
    assert res.truncated == 0
    assert len(res.values) == 1


#------------------------------------------------------------------
# confidence 필드가 없는 후보는 0.0 으로 취급된다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_resolve_multi_confidence_없으면_0으로_취급():
    candidates = [{"dc_id": "A"}, {"dc_id": "B", "confidence": 0.1}]
    res = C.resolve_multi(candidates, min_confidence=0.0)
    assert [c["dc_id"] for c in res.values] == ["B", "A"]


# ── resolve_doctype (ConflictSpec 다리) ────────────────────────────

#------------------------------------------------------------------
# strategy=all 이면 n 없이 전부 낸다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_resolve_doctype_all():
    candidates = [{"dc_id": "A", "confidence": 0.6}, {"dc_id": "B", "confidence": 0.9}]
    spec = D.ConflictSpec(strategy="all")
    res = C.resolve_doctype(candidates, spec)
    assert len(res.values) == 2
    assert res.truncated == 0


#------------------------------------------------------------------
# strategy=top_n 이면 n·min_confidence 를 그대로 전달한다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_resolve_doctype_top_n():
    candidates = [{"dc_id": str(i), "confidence": i / 10} for i in range(5)]
    spec = D.ConflictSpec(strategy="top_n", n=2, min_confidence=0.25)
    res = C.resolve_doctype(candidates, spec)
    # confidence>=0.25 → dc_id 3,4(각 0.3,0.4)... 실제로 0,1,2,3,4 중 0.3 이상은 3,4 뿐
    assert [c["dc_id"] for c in res.values] == ["4", "3"]
    assert res.truncated == 0


# ── ensure_overridable_axis (T11) ──────────────────────────────────

#------------------------------------------------------------------
# security 축은 실행 시에도 전략을 바꿀 수 없다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_security_축은_덮어쓰기_금지():
    with pytest.raises(C.AxisNotOverridableError):
        C.ensure_overridable_axis("security")


#------------------------------------------------------------------
# doctype 축은 덮어쓸 수 있다(예외 없이 통과)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_doctype_축은_덮어쓰기_허용():
    C.ensure_overridable_axis("doctype")   # 예외 없이 통과하면 성공
