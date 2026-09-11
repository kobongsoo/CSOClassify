#------------------------------------------------------------------
# 업무 분류(doctype) 축 — 매칭 엔진(doctype.py) 단위 테스트
#=> terms/filename/paths 신호가 후보를 만들고, 조상 흡수(5-2)·축 전략(6장)이
#   적용되는지 검증한다. 로드맵 D4 완료 판정("계약서 샘플이 법무/규정 > 계약서
#   로 분류되고, 계약+제안 혼합 문서에서 둘 다 나온다")을 실제 taxonomy 로
#   회귀 고정하는 테스트를 포함한다.
#------------------------------------------------------------------

import pytest

from csoclassify.classify import axes as A
from csoclassify.classify import doc_rules as D
from csoclassify.classify import doctype as DT


#------------------------------------------------------------------
# 작은 Taxonomy — 법무/규정 > 계약서, 영업/마케팅 > 제안서, 기술/개발 > 설계문서 > 요구사항정의서
#=> 조상 흡수·서로 다른 가지 동시 매칭을 함께 시험할 수 있도록 3계층까지 만든다.
#
# -in: 없음
#
# -out: axes.Taxonomy
# -out: error = 없음
#------------------------------------------------------------------
def mk_taxonomy():
    return A.Taxonomy("test", "20260824000000", 6, [
        A.TaxonomyNode(dc_id="LEGAL", parent=None, order=1, title="법무/규정", status=1),
        A.TaxonomyNode(dc_id="CONTRACT", parent="LEGAL", order=1, title="계약서", status=1),
        A.TaxonomyNode(dc_id="SALES", parent=None, order=2, title="영업/마케팅", status=1),
        A.TaxonomyNode(dc_id="PROPOSAL", parent="SALES", order=1, title="제안서", status=1),
        A.TaxonomyNode(dc_id="TECH", parent=None, order=3, title="기술/개발", status=1),
        A.TaxonomyNode(dc_id="DESIGN", parent="TECH", order=1, title="설계문서", status=1),
        A.TaxonomyNode(dc_id="REQSPEC", parent="DESIGN", order=1, title="요구사항정의서", status=1),
    ])


#------------------------------------------------------------------
# 작은 DocRuleSet — 계약서·제안서·요구사항정의서·설계문서 규칙
#
# -in: conflict = doc_rules.ConflictSpec(기본 all)
#
# -out: doc_rules.DocRuleSet
# -out: error = 없음
#------------------------------------------------------------------
def mk_ruleset(conflict=None):
    rules = (
        D.DoctypeRule(id="dt_contract", node="CONTRACT", weight="high",
                     terms=("계약서", "용역계약", "갑과 을"), exclude=("계약서 양식",),
                     filename=("계약서",)),
        D.DoctypeRule(id="dt_proposal", node="PROPOSAL", weight="high",
                     terms=("제안서", "제안 내용"), filename=("제안서",)),
        D.DoctypeRule(id="dt_reqspec", node="REQSPEC", weight="high",
                     terms=("요구사항정의서", "요구사항 명세")),
        D.DoctypeRule(id="dt_design", node="DESIGN", weight="medium",
                     terms=("설계문서",)),
        D.DoctypeRule(id="dt_legal_root", node="LEGAL", weight="low",
                     terms=("법무",)),
    )
    return D.DocRuleSet(conflict=conflict or D.ConflictSpec(strategy="all"), rules=rules)


# ── 설계서 D4 완료 판정 — 실제 taxonomy 회귀 ────────────────────────

#------------------------------------------------------------------
# 계약서 샘플은 "법무/규정 > 계약서" 로 분류된다(실제 배포 taxonomy 사용)
#=> 저장소의 doc_taxonomy.yaml(D1) 은 실제 dc_id(DC_006_001 등)를 쓰므로,
#   합성 taxonomy 가 아니라 이걸로 확인해야 로드맵 D4 완료 판정이 실제
#   운영 값으로 성립함을 보여준다. doc_rule.yaml 배포본은 아직 스캐폴드
#   (terms 비어 있음)라 여기서는 계약서/제안서 노드에 대해서만 손으로 채운
#   규칙을 만들어 taxonomy 와 짝지어 쓴다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_계약서_샘플은_법무규정_계약서로_분류된다():
    taxonomy = A.load_taxonomy()
    contract_node = taxonomy.get("DC_006_001")
    assert contract_node is not None and contract_node.title == "계약서"

    rules = (D.DoctypeRule(id="dt_contract", node="DC_006_001", weight="high",
                          terms=("계약서", "용역계약", "도급계약", "갑과 을"),
                          exclude=("계약서 양식",)),)
    drs = D.DocRuleSet(conflict=D.ConflictSpec(strategy="all"), rules=rules)

    text = "본 용역계약서는 갑과 을 사이에 체결된 도급계약의 조건을 정한다."
    sig = DT.scan_doctype(text, "D:/collected/법무/용역계약서_한빛테크.docx", drs, taxonomy)

    assert len(sig.values) == 1
    assert sig.values[0]["dc_id"] == "DC_006_001"
    assert sig.values[0]["path"] == "법무/규정 > 계약서"
    # 재설계 13-2 로 from 어휘가 신호원별로 세분화됐다: 본문 terms 는 "body".
    assert "body" in sig.values[0]["from"]


#------------------------------------------------------------------
# 계약+제안 혼합 문서는 계약서·제안서 둘 다 나온다(실제 배포 taxonomy 사용)
#=> 설계서 2장의 핵심 사례("영업/마케팅 > 제안서" 가 첨부로 함께 걸리는 문서)를
#   그대로 재현한다 — all 전략에서는 어느 한쪽을 버리지 않는다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_계약_제안_혼합_문서는_둘_다_나온다():
    taxonomy = A.load_taxonomy()
    rules = (
        D.DoctypeRule(id="dt_contract", node="DC_006_001", weight="high",
                     terms=("계약서", "용역계약")),
        D.DoctypeRule(id="dt_proposal", node="DC_003_001", weight="high",
                     terms=("제안서", "제안 내용")),
    )
    drs = D.DocRuleSet(conflict=D.ConflictSpec(strategy="all"), rules=rules)

    text = "본 용역계약서에는 최초 제안서에 담긴 제안 내용을 별첨한다."
    sig = DT.scan_doctype(text, "D:/collected/법무/계약_제안_혼합.docx", drs, taxonomy)

    dc_ids = {v["dc_id"] for v in sig.values}
    assert dc_ids == {"DC_006_001", "DC_003_001"}
    assert sig.strategy == "all"
    assert sig.status == "proposed"


# ── 신호원(terms/filename/paths) ────────────────────────────────────

#------------------------------------------------------------------
# terms 매칭이 없으면 후보가 없다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_매칭없으면_빈_결과():
    taxonomy = mk_taxonomy()
    drs = mk_ruleset()
    sig = DT.scan_doctype("아무 관련 없는 회의록입니다.", "D:/x/회의록.hwp", drs, taxonomy)
    assert sig.values == ()


#------------------------------------------------------------------
# exclude 에 걸린 매치는 세지 않는다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_exclude된_매치는_제외된다():
    taxonomy = mk_taxonomy()
    drs = mk_ruleset()
    sig = DT.scan_doctype("이 파일은 계약서 양식일 뿐이다.", "D:/x/서식.hwp", drs, taxonomy)
    assert sig.values == ()


#------------------------------------------------------------------
# 파일명만으로도(terms 없이) filename 신호로 매칭될 수 있다(스캐폴드 동작 확인)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_filename만으로도_매칭된다():
    taxonomy = mk_taxonomy()
    drs = mk_ruleset()
    sig = DT.scan_doctype("", "D:/x/계약서_초안.hwp", drs, taxonomy)
    dc_ids = {v["dc_id"] for v in sig.values}
    assert "CONTRACT" in dc_ids
    hit = next(v for v in sig.values if v["dc_id"] == "CONTRACT")
    assert hit["from"] == ("name",)


#------------------------------------------------------------------
# terms 와 filename 이 함께 걸리면 from 이 합쳐지고 신뢰도는 최댓값
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_terms와_filename이_함께_걸리면_from_합쳐진다():
    taxonomy = mk_taxonomy()
    drs = mk_ruleset()
    sig = DT.scan_doctype("본 계약서는 용역계약을 다룬다.", "D:/x/계약서_2026.hwp", drs, taxonomy)
    hit = next(v for v in sig.values if v["dc_id"] == "CONTRACT")
    assert set(hit["from"]) == {"body", "name"}


#------------------------------------------------------------------
# 조상-자손이 함께 걸리면 자손만 남는다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_조상_자손_동시매칭시_자손만_남는다():
    taxonomy = mk_taxonomy()
    drs = mk_ruleset()
    text = "이 문서는 설계문서 중 요구사항정의서에 해당한다."
    sig = DT.scan_doctype(text, "D:/x/문서.hwp", drs, taxonomy)
    dc_ids = {v["dc_id"] for v in sig.values}
    assert dc_ids == {"REQSPEC"}   # DESIGN(조상)은 흡수되어 빠진다


#------------------------------------------------------------------
# 서로 다른 가지끼리 걸리면 둘 다 남는다(조상 관계 아님)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_서로_다른_가지는_둘_다_남는다():
    taxonomy = mk_taxonomy()
    drs = mk_ruleset()
    text = "이 계약서에는 제안 내용이 별첨돼 있다."
    sig = DT.scan_doctype(text, "D:/x/문서.hwp", drs, taxonomy)
    dc_ids = {v["dc_id"] for v in sig.values}
    assert dc_ids == {"CONTRACT", "PROPOSAL"}


# ── 축 전략(conflict) ────────────────────────────────────────────

#------------------------------------------------------------------
# top_n 전략이면 개수를 자르고 truncated 로 알린다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_top_n_전략은_잘라내고_truncated_보고():
    taxonomy = mk_taxonomy()
    drs = mk_ruleset(conflict=D.ConflictSpec(strategy="top_n", n=1))
    text = "이 계약서에는 제안 내용이 별첨돼 있다."
    sig = DT.scan_doctype(text, "D:/x/문서.hwp", drs, taxonomy)
    assert len(sig.values) == 1
    assert sig.truncated == 1
    assert sig.strategy == "top_n"


# ── 비활성 규칙(T6) 연동 ─────────────────────────────────────────

#------------------------------------------------------------------
# 미사용(status=0) 노드를 참조하는 규칙은 애초에 active_rules 에서 빠지므로
# scan_doctype 도 매칭을 시도하지 않는다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_비활성_규칙은_매칭_대상이_아니다(tmp_path):
    import yaml as _yaml
    taxonomy = A.Taxonomy("test", "20260824000000", 2, [
        A.TaxonomyNode(dc_id="A", parent=None, order=1, title="루트A", status=1),
        A.TaxonomyNode(dc_id="A1", parent="A", order=1, title="자식A1(미사용)", status=0),
    ])
    p = tmp_path / "doc_rule.yaml"
    data = {"doctype_rules": [{"id": "dt_a1", "node": "A1", "terms": ["자식A1"]}]}
    p.write_text(_yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    drs = D.load_doc_rules(str(p), taxonomy=taxonomy)

    sig = DT.scan_doctype("이 문서는 자식A1 에 관한 것이다.", "D:/x/문서.hwp", drs, taxonomy)
    assert sig.values == ()
