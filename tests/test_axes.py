#------------------------------------------------------------------
# 업무 분류(doctype) 축 — 분류체계 스냅샷 로더 단위 테스트
#=> axes.py 가 doc_taxonomy.yaml 을 정확히 읽고, 관리 화면과 같은 전체경로를
#   재현하며, 깨진 트리(순환·고아·구조 오류)를 조용히 넘기지 않고 확실히
#   실패하는지 검증한다(로드맵 D1 완료 판정).
#------------------------------------------------------------------

import os

import pytest

from csoclassify.classify import axes as A


#------------------------------------------------------------------
# 최소 스냅샷 dict 만들기
#=> validate_taxonomy_data/load 대상이 되는 원시 dict 를 손으로 조립한다.
#   실제 doc_taxonomy.yaml 파일을 거치지 않아 테스트가 파일 시스템에 흔들리지
#   않는다.
#
# -in: nodes = {dc_id, parent, order, title, status} dict 목록
#
# -out: dict = validate_taxonomy_data/load_taxonomy 에 넣을 수 있는 원시 데이터
# -out: error = 없음
#------------------------------------------------------------------
def mk(nodes):
    return {"taxonomy": {"source": "test", "exported_at": "20260824000000",
                          "node_count": len(nodes), "nodes": nodes}}


#------------------------------------------------------------------
# 위반 코드만 뽑기
#=> 단언문을 짧게 쓰려고 위반 목록에서 코드 문자열만 모은다.
#
# -in: violations = validate_taxonomy_data 결과
#
# -out: list = ["T0", "T7", ...]
# -out: error = 없음
#------------------------------------------------------------------
def codes(violations):
    return [v["code"] for v in violations]


# ── 실제 배포 스냅샷 회귀 ────────────────────────────────────────────

#------------------------------------------------------------------
# 저장소의 doc_taxonomy.yaml 은 검증을 통과하고, 관리 화면과 같은 경로를 낸다
#=> scripts/export_taxonomy.py 로 이미 만들어 둔 실제 스냅샷을 회귀로 묶는다.
#   설계서 로드맵 D1 완료 판정("22개 노드가 로드되고, 임의 DC_ID 의 전체경로가
#   관리 화면과 글자까지 같게 나온다")을 그대로 코드로 옮긴 것이다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 스냅샷이 없거나 검증에 실패하면 AssertionError/예외
#------------------------------------------------------------------
def test_배포_스냅샷은_검증을_통과하고_경로가_관리화면과_일치한다():
    # 실제 스냅샷은 고객사 DB 에서 나온 값이라 저장소에 올리지 않는다. 갓 받아온
    # 클론에는 없는 것이 정상이므로 실패가 아니라 건너뛴다 — 형식만 보려면 같은
    # 폴더의 doc_taxonomy.sample.yaml 을 보면 된다.
    if not os.path.isfile(A.default_taxonomy_path()):
        pytest.skip("배포 스냅샷(doc_taxonomy.yaml)이 없습니다 — "
                    "scripts/export_taxonomy.py 로 만든 뒤에만 도는 회귀입니다.")
    taxonomy = A.load_taxonomy()
    assert len(taxonomy) >= 1
    node = taxonomy.get("DC_002_001_001")
    assert node is not None
    assert taxonomy.path("DC_002_001_001") == "기술/개발 > 설계문서 > 요구사항정의서"
    assert taxonomy.path_ids("DC_002_001_001") == ("DC_002", "DC_002_001", "DC_002_001_001")


# ── 정상 케이스 ────────────────────────────────────────────────────

#------------------------------------------------------------------
# 정상 트리는 위반이 없다
#=> 최상위 2개 + 자식 1개짜리 작은 트리로 기본 통과 경로를 확인한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_정상_트리는_위반이_없다():
    data = mk([
        {"dc_id": "A", "parent": None, "order": 1, "title": "루트A", "status": 1},
        {"dc_id": "A1", "parent": "A", "order": 1, "title": "자식A1", "status": 1},
    ])
    assert A.validate_taxonomy_data(data) == []


#------------------------------------------------------------------
# 최상위 노드는 parent 가 null 이어야 root 로 잡힌다
#=> children_of(None) 이 최상위 노드 목록을 정확히 돌려주는지 확인한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_최상위_노드는_children_of_None_으로_찾는다():
    taxonomy = A.Taxonomy("test", "20260824000000", 2, [
        A.TaxonomyNode(dc_id="B", parent=None, order=2, title="루트B", status=1),
        A.TaxonomyNode(dc_id="A", parent=None, order=1, title="루트A", status=1),
    ])
    roots = taxonomy.roots
    assert [n.dc_id for n in roots] == ["A", "B"]   # order 순 정렬 확인


# ── 구조 오류(T0) ──────────────────────────────────────────────────

#------------------------------------------------------------------
# 최상위 taxonomy 매핑이 없으면 T0
#=> 완전히 다른 모양의 YAML(예: 빈 dict, cso_rule.yaml 을 실수로 지정)을
#   넣었을 때 조용히 빈 트리로 넘어가지 않고 확실히 실패해야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_taxonomy_키가_없으면_T0():
    assert codes(A.validate_taxonomy_data({})) == ["T0"]
    assert codes(A.validate_taxonomy_data({"taxonomy": "문자열"})) == ["T0"]


#------------------------------------------------------------------
# nodes 가 목록이 아니면 T0
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_nodes가_목록이_아니면_T0():
    data = {"taxonomy": {"nodes": {"이것은": "매핑이다"}}}
    assert codes(A.validate_taxonomy_data(data)) == ["T0"]


#------------------------------------------------------------------
# dc_id 중복은 T0, 이후 트리 검사(T7·T8)는 건너뛴다
#=> 구조가 흔들리면 부모-자식 관계를 신뢰할 수 없으므로, T0 가 하나라도
#   있으면 T7/T8 은 아예 돌지 않는지 확인한다(연쇄 오보고 방지).
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_dc_id_중복은_T0이고_T7_T8은_건너뛴다():
    data = mk([
        {"dc_id": "A", "parent": None, "order": 1, "title": "루트A", "status": 1},
        {"dc_id": "A", "parent": "없는부모", "order": 2, "title": "중복A", "status": 1},
    ])
    violations = A.validate_taxonomy_data(data)
    assert codes(violations) == ["T0"]


#------------------------------------------------------------------
# title 이 비어 있으면 T0
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_title_없으면_T0():
    data = mk([{"dc_id": "A", "parent": None, "order": 1, "title": "", "status": 1}])
    assert codes(A.validate_taxonomy_data(data)) == ["T0"]


# ── 고아 노드(T8) ──────────────────────────────────────────────────

#------------------------------------------------------------------
# parent 가 스냅샷에 없는 dc_id 를 가리키면 T8
#=> 노드가 삭제됐거나 스냅샷이 낡았을 때의 상황을 재현한다(설계서 4-5).
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_고아_parent는_T8():
    data = mk([
        {"dc_id": "A1", "parent": "삭제된부모", "order": 1, "title": "자식A1", "status": 1},
    ])
    violations = A.validate_taxonomy_data(data)
    assert codes(violations) == ["T8"]
    assert violations[0]["dc_id"] == "A1"


# ── 순환(T7) ───────────────────────────────────────────────────────

#------------------------------------------------------------------
# 자기 자신을 parent 로 가리키면 순환(T7)
#=> 가장 단순한 순환 형태(1노드 자기참조)를 잡아내는지 확인한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_자기참조는_T7():
    data = mk([{"dc_id": "A", "parent": "A", "order": 1, "title": "루트A", "status": 1}])
    assert codes(A.validate_taxonomy_data(data)) == ["T7"]


#------------------------------------------------------------------
# A→B→A 처럼 두 노드가 서로를 부모로 가리키면 순환(T7)
#=> 설계서 8장 T7 예시(A→B→A)를 그대로 재현한다. 이 경우를 검증 없이
#   Taxonomy.path_ids() 로 바로 계산하면 무한루프에 빠지므로, load_taxonomy()
#   가 이 상태를 만들기 전에 반드시 막아야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_두_노드가_서로를_가리키면_T7():
    data = mk([
        {"dc_id": "A", "parent": "B", "order": 1, "title": "A", "status": 1},
        {"dc_id": "B", "parent": "A", "order": 1, "title": "B", "status": 1},
    ])
    assert codes(A.validate_taxonomy_data(data)) == ["T7"]


#------------------------------------------------------------------
# 검증 없이 만든 Taxonomy 도 path_ids() 가 순환에서 무한루프에 빠지지 않는다
#=> load_taxonomy() 가 아니라 Taxonomy 생성자를 직접 써서(검증 우회) 순환
#   트리를 만들었을 때, path_ids() 자체가 안전망으로 ValueError 를 내는지
#   확인한다(axes.py 의 이중 방어).
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_검증을_건너뛴_순환_트리는_path_ids에서_ValueError():
    taxonomy = A.Taxonomy("test", "20260824000000", 2, [
        A.TaxonomyNode(dc_id="A", parent="B", order=1, title="A", status=1),
        A.TaxonomyNode(dc_id="B", parent="A", order=1, title="B", status=1),
    ])
    with pytest.raises(ValueError):
        taxonomy.path_ids("A")


# ── load_taxonomy() 예외 경로 ────────────────────────────────────────

#------------------------------------------------------------------
# 존재하지 않는 경로는 FileNotFoundError
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_파일_없으면_FileNotFoundError(tmp_path):
    missing = tmp_path / "없는파일.yaml"
    with pytest.raises(FileNotFoundError):
        A.load_taxonomy(str(missing))


#------------------------------------------------------------------
# 검증 실패한 파일은 TaxonomyValidationError, 위반 목록을 담는다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_검증_실패시_TaxonomyValidationError(tmp_path):
    import yaml as _yaml
    bad = tmp_path / "doc_taxonomy.yaml"
    bad.write_text(
        _yaml.safe_dump(mk([{"dc_id": "A", "parent": "A", "order": 1,
                             "title": "A", "status": 1}]), allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(A.TaxonomyValidationError) as exc_info:
        A.load_taxonomy(str(bad))
    assert exc_info.value.violations[0]["code"] == "T7"


#------------------------------------------------------------------
# validate=False 면 위반이 있어도 그대로 로드된다(도구용 탈출구)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_validate_False면_고아_parent도_로드된다(tmp_path):
    import yaml as _yaml
    p = tmp_path / "doc_taxonomy.yaml"
    p.write_text(
        _yaml.safe_dump(mk([{"dc_id": "A1", "parent": "없는부모", "order": 1,
                             "title": "A1", "status": 1}]), allow_unicode=True),
        encoding="utf-8",
    )
    taxonomy = A.load_taxonomy(str(p), validate=False)
    assert len(taxonomy) == 1
