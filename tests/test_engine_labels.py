#------------------------------------------------------------------
# 분류 레코드 축 통합(labels) 단위 테스트 — 설계서 §7-1·7-2
#=> build_record 가 labels.security 를 항상 담고, doc_rules+taxonomy 를 둘 다
#   줬을 때만 labels.doctype 을 더하는지(4-6 "생략" 규칙 포함), 그리고 기존
#   최상위 grade/confidence/method/decided_by/seed_eligible 하위호환 필드가
#   labels.security 와 정확히 같은 값을 유지하는지 검증한다(로드맵 D5).
#------------------------------------------------------------------

import pytest

from csoclassify.classify import load_rules, build_record, propagate_records
from csoclassify.classify import axes as A
from csoclassify.classify import doc_rules as D

FIXED_TS = "2026-08-24T10:30:00+09:00"


#------------------------------------------------------------------
# 기본 규칙셋 픽스처
#
# -in: 없음
# -out: RuleSet
# -out: error = 없음
#------------------------------------------------------------------
@pytest.fixture(scope="module")
def rs():
    return load_rules()


#------------------------------------------------------------------
# 작은 taxonomy + 계약서 규칙 1개
#
# -in: 없음
# -out: (axes.Taxonomy, doc_rules.DocRuleSet)
# -out: error = 없음
#------------------------------------------------------------------
def mk_doctype_fixtures():
    taxonomy = A.Taxonomy("test", "20260824000000", 1, [
        A.TaxonomyNode(dc_id="CONTRACT", parent=None, order=1, title="계약서", status=1),
    ])
    drs = D.DocRuleSet(
        conflict=D.ConflictSpec(strategy="all"), version="doc-test-1",
        rules=(D.DoctypeRule(id="dt_contract", node="CONTRACT", weight="high",
                             terms=("계약서",)),),
    )
    return taxonomy, drs


# ── labels.security — 항상 존재, 하위호환 필드와 일치 ────────────────

#------------------------------------------------------------------
# labels.security 는 최상위 하위호환 필드와 값이 정확히 같다
#
# -in: rs = 규칙셋
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_labels_security는_하위호환_필드와_일치(rs):
    rec = build_record("D:/x/보고서.hwp", "본 문서는 대외비입니다.", rs, ts=FIXED_TS)
    sec = rec["labels"]["security"]
    assert sec["value"] == rec["grade"] == "C"
    assert sec["confidence"] == rec["confidence"]
    assert sec["method"] == rec["method"]
    assert sec["decided_by"] == rec["decided_by"]
    assert sec["strategy"] == "max"
    assert any(c["value"] == "C" for c in sec["candidates"])


#------------------------------------------------------------------
# 아무 신호도 없는 문서는 labels.security.value 가 None 이고 candidates 는 빈 목록
#
# -in: rs = 규칙셋
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_labels_security_무신호는_None(rs):
    rec = build_record("D:/x/평범한글.txt", "오늘 날씨가 좋습니다.", rs, ts=FIXED_TS)
    sec = rec["labels"]["security"]
    assert sec["value"] is None
    assert sec["candidates"] == []


#------------------------------------------------------------------
# 벡터-only(rules_enabled=False) 모드도 labels.security 를 담는다(값은 None)
#
# -in: rs = 규칙셋
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_벡터only_모드도_labels_security_보유(rs):
    rec = build_record("D:/x/문서.hwp", "아무 텍스트", rs, ts=FIXED_TS, rules_enabled=False)
    assert rec["labels"]["security"] == {
        "value": None, "confidence": 0.0, "strategy": "max",
        "method": "unclassified", "decided_by": [], "candidates": [],
    }
    assert "doctype" not in rec["labels"]


# ── labels.doctype — 4-6 "생략" 규칙 ─────────────────────────────────

#------------------------------------------------------------------
# doc_rules·taxonomy 를 안 주면 labels.doctype 키 자체가 없다(빈 목록이 아님)
#
# -in: rs = 규칙셋
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_doc_rules_taxonomy_없으면_doctype_키가_없다(rs):
    rec = build_record("D:/x/계약서.hwp", "본 계약서는 유효하다.", rs, ts=FIXED_TS)
    assert "doctype" not in rec["labels"]
    assert "doctype_rule_version" not in rec
    assert "taxonomy_version" not in rec


#------------------------------------------------------------------
# 하나만 주면(taxonomy 없이 doc_rules 만) 여전히 doctype 을 생략한다
#
# -in: rs = 규칙셋
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_doc_rules만_있으면_doctype_생략(rs):
    _, drs = mk_doctype_fixtures()
    rec = build_record("D:/x/계약서.hwp", "본 계약서는 유효하다.", rs, ts=FIXED_TS,
                       doc_rules=drs, taxonomy=None)
    assert "doctype" not in rec["labels"]


#------------------------------------------------------------------
# 둘 다 주면 labels.doctype 이 채워지고 버전 필드도 함께 붙는다
#
# -in: rs = 규칙셋
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_doc_rules_taxonomy_둘다_있으면_doctype_채워진다(rs):
    taxonomy, drs = mk_doctype_fixtures()
    rec = build_record("D:/x/계약서.hwp", "본 계약서는 유효하다.", rs, ts=FIXED_TS,
                       doc_rules=drs, taxonomy=taxonomy)
    dt = rec["labels"]["doctype"]
    assert dt["values"][0]["dc_id"] == "CONTRACT"
    assert dt["status"] == "proposed"
    assert rec["doctype_rule_version"] == "doc-test-1"
    assert rec["taxonomy_version"] == "20260824000000"
    assert rec["rule_version"] == rs.version   # security 버전은 그대로


# ── propagate_records 와 labels.security 동기화 ─────────────────────

#------------------------------------------------------------------
# rule 신호 dict 헬퍼(재융합 입력용)
#
# -in: grade = 등급/None
# -out: dict
# -out: error = 없음
#------------------------------------------------------------------
def _sig(grade):
    return {"grade": grade, "confidence": 0.85 if grade else 0.0,
            "seed_eligible": bool(grade), "hits": []}


#------------------------------------------------------------------
# propagate_records 로 등급이 바뀌면 labels.security 도 같이 갱신된다
#=> 재융합 후 rec["grade"] 만 바뀌고 labels.security 가 stale 로 남으면 두
#   표현이 어긋난다 — 그 회귀를 잡는다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_propagate_records는_labels_security도_갱신():
    records = [
        {"file": "seed1", "grade": "C", "seed_eligible": True,
         "vector": [1.0, 0.0], "signals": {"rule": _sig("C")},
         "labels": {"security": {"value": "C", "confidence": 0.85, "strategy": "max",
                                 "method": "fusion", "decided_by": ["rule"], "candidates": []}}},
        {"file": "pending", "grade": None, "seed_eligible": False,
         "vector": [0.99, 0.01], "signals": {}},
    ]
    updated, stats = propagate_records(records)
    pending = next(r for r in updated if r["file"] == "pending")
    assert pending["grade"] == "C"
    assert pending["labels"]["security"]["value"] == "C"
    assert pending["labels"]["security"]["method"] == "fusion"
    assert "embed" in pending["labels"]["security"]["decided_by"]


#------------------------------------------------------------------
# labels 가 아예 없던(구버전) 입력 레코드도 propagate 후 labels.security 가 생긴다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_labels_없는_구버전_레코드도_propagate_후_labels_생김():
    records = [
        {"file": "seed1", "grade": "S", "seed_eligible": True,
         "vector": [1.0, 0.0], "signals": {"rule": _sig("S")}},
        {"file": "pending", "grade": None, "seed_eligible": False,
         "vector": [0.99, 0.01], "signals": {}},
    ]
    updated, _ = propagate_records(records)
    pending = next(r for r in updated if r["file"] == "pending")
    assert pending["labels"]["security"]["value"] == "S"
