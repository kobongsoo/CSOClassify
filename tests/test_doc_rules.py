#------------------------------------------------------------------
# 업무 분류(doctype) 축 — doc_rule.yaml 로더 단위 테스트
#=> doc_rules.py 가 conflict:·doctype_rules 를 정확히 읽고, 구조 오류(T0)와
#   conflict 오류(T2·T10)를 조용히 넘기지 않고 확실히 실패하는지, 그리고
#   doc_taxonomy.yaml(Taxonomy)과의 교차검증(T5·T6·T12)이 설계대로 동작하는지
#   검증한다(로드맵 D2 완료 판정 — doc_rule.yaml 이 없어도 security-only 로
#   완전히 동일하게 도는 것 포함).
#------------------------------------------------------------------

import pytest

from csoclassify.classify import axes as A
from csoclassify.classify import doc_rules as D


#------------------------------------------------------------------
# 최소 doc_rule.yaml 원시 dict 만들기
#
# -in: rules     = doctype_rules 항목 dict 목록
# -in: conflict  = conflict: 원본 값(기본 생략)
#
# -out: dict = validate_doc_rule_data/load_doc_rules 에 넣을 수 있는 원시 데이터
# -out: error = 없음
#------------------------------------------------------------------
def mk(rules, conflict=None):
    data = {"doctype_rules": rules}
    if conflict is not None:
        data["conflict"] = conflict
    return data


#------------------------------------------------------------------
# 위반 코드만 뽑기
#
# -in: violations = validate_doc_rule_data 결과
#
# -out: list = ["T0", "T2", ...]
# -out: error = 없음
#------------------------------------------------------------------
def codes(violations):
    return [v["code"] for v in violations]


#------------------------------------------------------------------
# 작은 Taxonomy 하나 만들기 — 루트 A(사용) 아래 자식 A1(사용)·A2(미사용)
#
# -in: 없음
#
# -out: axes.Taxonomy
# -out: error = 없음
#------------------------------------------------------------------
def mk_taxonomy():
    return A.Taxonomy("test", "20260824000000", 3, [
        A.TaxonomyNode(dc_id="A", parent=None, order=1, title="루트A", status=1),
        A.TaxonomyNode(dc_id="A1", parent="A", order=1, title="자식A1", status=1),
        A.TaxonomyNode(dc_id="A2", parent="A", order=2, title="자식A2(미사용)", status=0),
    ])


# ── 실제 배포 규칙셋 회귀 ────────────────────────────────────────────

#------------------------------------------------------------------
# 저장소의 doc_rule.yaml 은 검증을 통과하고 taxonomy 와 어긋나지 않는다
#=> 배포되는 실제 파일을 실제 doc_taxonomy.yaml 과 함께 로드해 회귀로 묶는다.
#   예전에는 이 파일이 '빈 스캐폴드'(모든 활성 노드를 1:1 로 덮고 terms 는 빈)
#   였고 그 전제로 단언했다. 지금은 UI 생성기가 만든 실제 규칙셋이라
#   최하위(leaf) 노드만 덮는다 — 최상위 분류("경영/관리")는 문서에 그대로
#   적히는 말이 아니라 서랍 이름이라 규칙에서 일부러 뺀다(docruleedit.syncable_nodes).
#   그래서 "1:1 커버리지" 대신 아래 불변식으로 지킨다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_배포_규칙셋은_검증을_통과하고_taxonomy와_맞물린다():
    taxonomy = A.load_taxonomy()
    drs = D.load_doc_rules(taxonomy=taxonomy)

    assert drs.rules, "배포 규칙셋이 비어 있습니다"
    # 모든 규칙이 살아 있어야 한다 — 미사용(status=0) 노드를 가리키면 T6 로 죽는다.
    assert drs.active_rules == drs.rules
    # 규칙이 가리키는 노드는 전부 분류체계에 있어야 한다(T5 는 로드가 이미 막지만
    # 여기서 한 번 더 확인해, 스냅샷이 낡았을 때 이 테스트가 먼저 알려주게 한다).
    for r in drs.rules:
        assert taxonomy.get(r.node) is not None, r.id

    # 규칙마다 최소 한 개의 신호는 있어야 한다 — 아무 신호도 없는 규칙은
    # 파일만 길게 만들고 아무 문서도 못 맞힌다.
    for r in drs.rules:
        assert (r.title_terms or r.head_terms or r.terms
                or r.filename or r.paths or r.form), r.id

    # 남는 경고는 '규칙이 참조하지 않는 노드'(T12)뿐이어야 하고, 그 대상은
    # 최상위 분류로 한정된다. 하위 노드가 T12 로 뜨면 어휘가 빠진 것이다.
    tops = {n.dc_id for n in taxonomy.active_nodes if not n.parent}
    for w in drs.warnings:
        assert w.startswith("[T12]"), w
        assert any(t in w for t in tops), w


# ── doc_rule.yaml 부재 — D2 완료 판정 핵심 ─────────────────────────

#------------------------------------------------------------------
# 파일이 없으면 FileNotFoundError — security-only 배포와 동일한 취급
#=> "없으면 doctype 축 비활성"은 cli.py(D4)의 정책이지만, 그 정책이 성립하려면
#   로더가 이 예외를 명확히 던져야 한다(설계서 4-6·T16 원칙).
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_파일_없으면_FileNotFoundError(tmp_path):
    missing = tmp_path / "없는파일.yaml"
    with pytest.raises(FileNotFoundError):
        D.load_doc_rules(str(missing))


# ── 정상 케이스 ────────────────────────────────────────────────────

#------------------------------------------------------------------
# conflict 생략 시 기본값은 all
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_conflict_생략시_기본값_all():
    assert D.validate_doc_rule_data(mk([])) == []
    data = mk([{"id": "dt_a", "node": "A", "terms": ["계약서"]}])
    violations = D.validate_doc_rule_data(data)
    assert violations == []


#------------------------------------------------------------------
# doctype_rules 가 아예 없으면(생략) 빈 규칙셋으로 통과한다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_doctype_rules_생략은_위반이_아니다():
    assert D.validate_doc_rule_data({}) == []


# ── 구조 오류(T0) ──────────────────────────────────────────────────

#------------------------------------------------------------------
# doctype_rules 가 목록이 아니면 T0
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_doctype_rules가_목록이_아니면_T0():
    data = {"doctype_rules": {"id": "매핑인데_목록이_아님"}}
    assert codes(D.validate_doc_rule_data(data)) == ["T0"]


#------------------------------------------------------------------
# id 없는 항목은 T0
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_id_없으면_T0():
    data = mk([{"node": "A"}])
    assert codes(D.validate_doc_rule_data(data)) == ["T0"]


#------------------------------------------------------------------
# node 없는 항목은 T0
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_node_없으면_T0():
    data = mk([{"id": "dt_a"}])
    assert codes(D.validate_doc_rule_data(data)) == ["T0"]


#------------------------------------------------------------------
# id 중복은 T0
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_id_중복은_T0():
    data = mk([
        {"id": "dt_a", "node": "A"},
        {"id": "dt_a", "node": "A1"},
    ])
    assert codes(D.validate_doc_rule_data(data)) == ["T0"]


# ── conflict 오류(T2·T10) ─────────────────────────────────────────

#------------------------------------------------------------------
# max 는 taxonomy 축엔 쓸 수 없다(T2) — security 처럼 등급을 하나만 남기면
# "혹은 둘 다인지" 라는 이 축의 존재 이유가 사라진다(설계서 6-2)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_conflict_max는_T2():
    assert codes(D.validate_doc_rule_data(mk([], conflict="max"))) == ["T2"]
    assert codes(D.validate_doc_rule_data(mk([], conflict="min"))) == ["T2"]


#------------------------------------------------------------------
# top_n 을 n 없이 쓰면 T10
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_top_n_n없이_쓰면_T10():
    assert codes(D.validate_doc_rule_data(mk([], conflict="top_n"))) == ["T10"]
    assert codes(D.validate_doc_rule_data(mk([], conflict={"strategy": "top_n"}))) == ["T10"]


#------------------------------------------------------------------
# top_n 의 n 이 0 이하/정수 아님이면 T10
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_top_n_n이_잘못되면_T10():
    assert codes(D.validate_doc_rule_data(
        mk([], conflict={"strategy": "top_n", "n": 0}))) == ["T10"]
    assert codes(D.validate_doc_rule_data(
        mk([], conflict={"strategy": "top_n", "n": "3"}))) == ["T10"]


#------------------------------------------------------------------
# 유효한 top_n 은 통과하고 그대로 파싱된다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_유효한_top_n은_통과하고_파싱된다(tmp_path):
    import yaml as _yaml
    p = tmp_path / "doc_rule.yaml"
    data = mk([{"id": "dt_a", "node": "A", "terms": ["x"]}],
              conflict={"strategy": "top_n", "n": 3, "min_confidence": 0.6})
    p.write_text(_yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    drs = D.load_doc_rules(str(p))
    assert drs.conflict == D.ConflictSpec(strategy="top_n", n=3, min_confidence=0.6)


# ── taxonomy 교차검증 ─────────────────────────────────────────────

#------------------------------------------------------------------
# taxonomy 를 안 주면 node 값이 뭐든 그대로 믿고 로드한다(T5 건너뜀)
#=> D1 이 아직 안 붙었거나 doc_taxonomy.yaml 이 없는 배포에서도 doc_rule.yaml
#   자체는 문법대로 읽혀야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_taxonomy_없으면_존재하지_않는_node도_그대로_로드된다(tmp_path):
    import yaml as _yaml
    p = tmp_path / "doc_rule.yaml"
    data = mk([{"id": "dt_a", "node": "없는노드", "terms": ["x"]}])
    p.write_text(_yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    drs = D.load_doc_rules(str(p))   # taxonomy=None(기본)
    assert drs.rules[0].node == "없는노드"
    assert drs.rules[0].active is True


#------------------------------------------------------------------
# taxonomy 를 주면 스냅샷에 없는 node 는 T5 로 로드가 막힌다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_taxonomy_주면_없는_node는_T5(tmp_path):
    import yaml as _yaml
    p = tmp_path / "doc_rule.yaml"
    data = mk([{"id": "dt_a", "node": "없는노드", "terms": ["x"]}])
    p.write_text(_yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    taxonomy = mk_taxonomy()
    with pytest.raises(D.DocRuleValidationError) as exc_info:
        D.load_doc_rules(str(p), taxonomy=taxonomy)
    assert exc_info.value.violations[0]["code"] == "T5"


#------------------------------------------------------------------
# 미사용(status=0) 노드를 참조한 규칙은 T6 경고 + active=False
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_미사용_노드_참조는_T6_경고와_active_False(tmp_path):
    import yaml as _yaml
    p = tmp_path / "doc_rule.yaml"
    data = mk([
        {"id": "dt_a1", "node": "A1", "terms": ["x"]},
        {"id": "dt_a2", "node": "A2", "terms": ["y"]},   # A2 는 status=0
    ])
    p.write_text(_yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    taxonomy = mk_taxonomy()
    drs = D.load_doc_rules(str(p), taxonomy=taxonomy)

    by_id = {r.id: r for r in drs.rules}
    assert by_id["dt_a1"].active is True
    assert by_id["dt_a2"].active is False
    assert drs.active_rules == (by_id["dt_a1"],)
    assert any(w.startswith("[T6]") for w in drs.warnings)


#------------------------------------------------------------------
# 참조 안 된 '잎'만 T12 경고 — 대분류(자식 있는 노드)는 경고하지 않는다
#=> 규칙은 가장 아래 분류에만 거는 것이 설계다("경영/관리" 같은 서랍 이름을 규칙에
#   넣으면 아무 문서나 걸린다). 그래서 대분류에 규칙이 없는 것은 정상이고, 여기에
#   경고를 내면 실행할 때마다 고칠 수 없는 경고가 쌓여 진짜 경고를 묻는다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_참조되지_않는_잎만_T12_경고(tmp_path):
    import yaml as _yaml
    # 루트 A 아래 잎이 둘(A1·A3). 규칙은 A1 만 참조한다.
    taxonomy = A.Taxonomy("test", "20260824000000", 3, [
        A.TaxonomyNode(dc_id="A", parent=None, order=1, title="루트A", status=1),
        A.TaxonomyNode(dc_id="A1", parent="A", order=1, title="자식A1", status=1),
        A.TaxonomyNode(dc_id="A3", parent="A", order=3, title="자식A3", status=1),
    ])
    p = tmp_path / "doc_rule.yaml"
    data = mk([{"id": "dt_a1", "node": "A1", "terms": ["x"]}])
    p.write_text(_yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    drs = D.load_doc_rules(str(p), taxonomy=taxonomy)

    t12 = [w for w in drs.warnings if w.startswith("[T12]")]
    # 규칙이 없는 잎(A3)은 알려 준다.
    assert any("A3(" in w for w in t12), t12
    # 대분류 A 는 자식이 있으므로 경고하지 않는다.
    assert not any(w.startswith("[T12] A(") for w in t12), t12


#------------------------------------------------------------------
# doctype_rules 가 비어 있으면 안내 경고를 남긴다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_빈_doctype_rules는_경고를_남긴다(tmp_path):
    import yaml as _yaml
    p = tmp_path / "doc_rule.yaml"
    p.write_text(_yaml.safe_dump(mk([]), allow_unicode=True), encoding="utf-8")
    drs = D.load_doc_rules(str(p))
    assert drs.rules == ()
    assert len(drs.warnings) == 1


#------------------------------------------------------------------
# validate=False 면 위반이 있어도 그대로 로드된다(도구용 탈출구)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_validate_False면_conflict_오류도_로드된다(tmp_path):
    import yaml as _yaml
    p = tmp_path / "doc_rule.yaml"
    data = mk([{"id": "dt_a", "node": "A"}], conflict="max")
    p.write_text(_yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    drs = D.load_doc_rules(str(p), validate=False)
    assert drs.conflict.strategy == "max"
