#------------------------------------------------------------------
# 분류 레코드 축 단위 테스트 — 설계서 §7-1·7-2
#=> build_record 가 security 축을 항상 담고, doc_rules+taxonomy 를 둘 다
#   줬을 때만 doctype 축을 더하는지(4-6 "생략" 규칙 포함) 검증한다.
#
#   [2026-09-10] 레코드 모양이 두 번 바뀌었다. 아침에 labels 껍데기를 없애
#   두 축을 최상위로 올렸고(같은 값이 두 군데 있던 것을 한 벌로), 오후에
#   판정을 맨 앞으로 올리고 근거를 why 한 덩어리로 모았다.
#     {file, hash, doc_id, doc_id_source, grade, doctype[…], why:{…}, meta:{ts}}
#   여기 검사들은 그 '한 벌' 원칙을 지킨다 — 등급값은 rec["grade"] 하나뿐이고
#   why.security 에는 근거만 있다.
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
# security 축은 판정값을 '한 벌만' 담는다
#=> 예전에는 같은 값이 최상위와 labels.security 양쪽에 있었다. 두 벌이면
#   한쪽만 갱신되는 사고가 가능해진다 — 그 자리를 없앤 것이 이 검사의 뜻이다.
#
# -in: rs = 규칙셋
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_security축은_판정값을_한_벌만_담는다(rs):
    rec = build_record("D:/x/보고서.hwp", "본 문서는 대외비입니다.", rs, ts=FIXED_TS)
    # 판정은 맨 앞에 한 벌.
    assert rec["grade"] == "C"
    sec = rec["why"]["security"]
    # 근거 묶음에는 등급값이 없다 — 두 군데 있으면 한쪽만 갱신되는 사고가 난다.
    assert "grade" not in sec
    # 옛 세대의 자리에도 아무것도 남기지 않는다.
    for k in ("confidence", "method", "decided_by", "seed_eligible",
              "signals", "labels", "security"):
        assert k not in rec, k
    # strategy 도 뺐다 — 등급 축은 늘 max 고정이라 문서마다 적어도 새 정보가 없다.
    assert "strategy" not in sec
    # 어느 신호가 C 를 주장했는지는 근거 묶음 안에 그대로 남는다.
    assert any(v.get("grade") == "C" for v in sec["signals"].values())


#------------------------------------------------------------------
# 아무 신호도 없는 문서는 grade 가 None 이고 signals 에 등급 주장이 하나도 없다
#
# -in: rs = 규칙셋
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_security_무신호는_None(rs):
    rec = build_record("D:/x/평범한글.txt", "오늘 날씨가 좋습니다.", rs, ts=FIXED_TS)
    assert rec["grade"] is None
    sec = rec["why"]["security"]
    assert "candidates" not in sec           # 2026-09-10 제거된 칸
    # 아무것도 못 찾은 신호는 아예 적지 않으므로, 등급을 주장한 신호가 없다
    assert not [v for v in sec["signals"].values() if v.get("grade") is not None]


#------------------------------------------------------------------
# 벡터-only(rules_enabled=False) 모드도 security 축을 담는다(등급은 None)
#=> 칸 목록 전체를 고정한다 — 없앤 칸(candidates·strategy)이 슬그머니 되살아나면
#   여기서 실패한다.
#
# -in: rs = 규칙셋
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_벡터only_모드도_security축_보유(rs):
    rec = build_record("D:/x/문서.hwp", "아무 텍스트", rs, ts=FIXED_TS, rules_enabled=False)
    assert rec["grade"] is None
    assert rec["why"]["security"] == {
        "confidence": 0.0, "method": "unclassified",
        "decided_by": [], "seed_eligible": False, "signals": {},
    }
    assert "doctype" not in rec["why"]


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
    assert "doctype" not in rec["why"]
    # 버전은 레코드에 없다 — 결과 파일 맨 앞 실행 헤더가 한 번만 적는다.
    assert list(rec["meta"]) == ["ts"], rec["meta"]


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
    assert "doctype" not in rec["why"]


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
    dt = rec["why"]["doctype"]
    assert dt["values"][0]["dc_id"] == "CONTRACT"
    # [2026-09-10] status 를 뺐다 — 엔진은 "proposed" 말고 다른 값을 낼 수 없었고
    # (확정은 cso_override.jsonl 에 쌓인다) 읽는 코드도 없었다.
    # 칸 목록 전체를 고정해, 값 하나뿐인 칸이 슬그머니 되살아나면 여기서 걸리게 한다.
    assert list(dt) == ["values"], list(dt)
    # 규칙셋 버전 세 칸은 레코드에 없다(실행 헤더가 한 번만 적는다).
    assert list(rec["meta"]) == ["ts"], rec["meta"]


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
# 옛 모양(labels) 레코드를 전파하면 새 모양 한 벌만 남는다
#=> 예전 결과 파일을 다시 읽어 전파하는 경로다. 새 칸만 채우고 옛 칸을 남기면
#   한 레코드 안에 두 판의 답이 공존해, 읽는 쪽이 어느 쪽을 믿을지 알 수 없다.
#   그 어긋남은 조용히 생기므로 여기서 못 박는다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_옛_labels_레코드를_전파하면_새_모양_한_벌만_남는다():
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
    assert pending["why"]["security"]["method"] == "fusion"
    assert "embed" in pending["why"]["security"]["decided_by"]
    # 옛 자리는 전부 걷어낸다 — 두 모양이 한 레코드에 섞이지 않게.
    # (grade 는 3세대에서 다시 최상위 칸이라 여기 없다.)
    for k in ("confidence", "method", "decided_by", "seed_eligible",
              "signals", "labels", "security"):
        assert k not in pending, k


#------------------------------------------------------------------
# 최상위 grade 만 있던(더 옛) 입력 레코드도 propagate 후 security 축이 생긴다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_최상위_grade만_있던_레코드도_propagate_후_security축_생김():
    records = [
        {"file": "seed1", "grade": "S", "seed_eligible": True,
         "vector": [1.0, 0.0], "signals": {"rule": _sig("S")}},
        {"file": "pending", "grade": None, "seed_eligible": False,
         "vector": [0.99, 0.01], "signals": {}},
    ]
    updated, _ = propagate_records(records)
    pending = next(r for r in updated if r["file"] == "pending")
    assert pending["grade"] == "S"
