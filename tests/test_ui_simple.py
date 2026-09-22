#------------------------------------------------------------------
# ui/app.py — 연동용 --simple 축약본 단위 테스트
#=> 화면은 분류를 다시 돌리지 않고 '전체 결과에서 필요한 칸만 뽑아' 축약본을 만든다.
#   그렇게 해도 되는 이유는 엔진의 --simple 이 애초에 같은 '골라 담기'이기 때문이다.
#   그 전제가 깨지면(엔진이 칸을 더하거나 빼면) 두 결과가 갈라지므로, 여기서 못박는다.
#------------------------------------------------------------------

import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "ui"))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

import app                                    # noqa: E402
from csoclassify.cli import _simple_record     # noqa: E402  (엔진 쪽 구현)


#------------------------------------------------------------------
# 엔진이 만드는 --simple 한 줄과 화면이 뽑는 한 줄이 같다
#=> 화면(app.simple_record)과 엔진(cli._simple_record)이 다른 칸을 고르면
#   "화면에서 만든 파일"과 "CLI 로 만든 파일"이 달라진다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_엔진과_같은_칸을_고른다():
    rec = {
        "file": "d:/sample/계약서.docx", "grade": "C", "hash": "abc123",
        "confidence": 0.9, "method": "fusion", "signals": {"rule": {}},
        "labels": {"security": "C",
                   "doctype": {"values": [{"dc_id": "DC_006_001", "confidence": 0.9},
                                          {"dc_id": "DC_004_003", "confidence": 0.7}],
                               "status": "proposed"}},
    }
    mine = app.simple_record(rec)
    engine = _simple_record(rec)
    # 엔진에도 doctype 이 들어가므로 두 결과가 통째로 같아야 한다.
    assert mine == engine
    assert mine == {"file": "d:/sample/계약서.docx", "grade": "C", "hash": "abc123",
                    "doctype": ["DC_006_001", "DC_004_003"]}


#------------------------------------------------------------------
# 업무분류 축을 안 쓴 실행에서는 doctype 키가 아예 없다
#=> 빈 배열로 내보내면 "분류를 못 했다"와 "축을 안 썼다"가 구분되지 않는다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_축을_안_쓰면_키가_없다():
    rec = {"file": "a.doc", "grade": "O", "hash": "h", "labels": {"security": "O"}}
    out = app.simple_record(rec)
    assert "doctype" not in out
    assert "doc_id" not in out
    assert out == _simple_record(rec)


#------------------------------------------------------------------
# doc_id 도 화면과 엔진이 같은 규칙으로 고른다
#=> 화면이 만든 축약본에는 doc_id 가 있는데 CLI 축약본에는 없다면(또는 그 반대면)
#   "화면에서 만든 파일"과 "CLI 로 만든 파일"이 갈려 적재 결과가 달라진다.
#   폴백을 None 으로 두는 규칙까지 두 쪽이 같아야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_doc_id_도_같은_규칙으로_고른다():
    base = {"file": "a.doc", "grade": "O", "hash": "h", "labels": {"security": "O"}}

    got = dict(base, doc_id="SF_2026", doc_id_source="sfile_id")
    assert app.simple_record(got) == _simple_record(got)
    assert app.simple_record(got)["doc_id"] == "SF_2026"

    fallback = dict(base, doc_id="abc123", doc_id_source="content")
    assert app.simple_record(fallback) == _simple_record(fallback)
    assert app.simple_record(fallback)["doc_id"] is None


#------------------------------------------------------------------
# 축은 돌았는데 아무 분류도 못 붙었으면 빈 배열이 나간다
#=> 위와 반대 경우다. 키는 있고 값이 비어 있어야 "돌렸는데 못 찾았다"가 된다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_축은_돌았는데_못_찾았으면_빈_배열():
    rec = {"file": "a.doc", "grade": "O", "hash": "h",
           "labels": {"doctype": {"values": [], "status": "proposed"}}}
    out = app.simple_record(rec)
    assert out["doctype"] == []
    assert out == _simple_record(rec)


#------------------------------------------------------------------
# 파일로 쓰면 한 줄에 한 문서(JSON Lines)
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_파일로_쓰기(tmp_path):
    grades = str(tmp_path / "cso_result.jsonl")
    recs = [{"file": f"d:/s/{i}.doc", "grade": "C", "hash": f"h{i}",
             "labels": {"doctype": {"values": [{"dc_id": "DC_1"}]}}} for i in range(3)]
    path, n = app.write_simple_file(grades, recs)

    assert path == str(tmp_path / "cso_result.simple.jsonl")
    assert n == 3
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    assert len(rows) == 3
    assert rows[0] == {"file": "d:/s/0.doc", "grade": "C", "hash": "h0",
                       "doctype": ["DC_1"]}


#------------------------------------------------------------------
# 축약본 경로는 결과 파일 옆에 나란히 만든다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_축약본_경로():
    assert app.simple_path("d:/x/cso_result.jsonl") == "d:/x/cso_result.simple.jsonl"
    # 확장자가 없어도 무언가는 만들어져야 한다(경로만 이상하게 만들고 끝내지 않는다).
    assert app.simple_path("d:/x/result").endswith(".simple.jsonl")


# ── 검토함 편입선(review_threshold) — 코드에서 정책 파일로 (2026-09-21) ──────

#------------------------------------------------------------------
# 규칙셋이 검토함 편입선을 정한다
#=> 예전에는 화면 코드(LOW_CONF)에 박혀 있어 고치려면 배포해야 했다. 고객마다
#   검토량이 달라 정책 파일로 옮겼다. 값이 없는 옛 규칙셋에서는 종전과 같은
#   0.6 이어야 한다 — 업그레이드만으로 검토량이 달라지면 안 된다.
#
# -in: tmp_path = pytest 임시 폴더
#
# -out: 없음(단언)
# -out: error = 어긋나면 AssertionError
#------------------------------------------------------------------
def test_검토함_편입선은_규칙셋이_정한다(tmp_path):
    import yaml
    from csoclassify.classify import rules as R

    base = yaml.safe_load(open(os.path.join(_HERE, "..", "resources", "policy",
                                            "cso_rule.yaml"), encoding="utf-8"))
    # ① 값이 있으면 그것을 쓴다.
    base["defaults"]["review_threshold"] = 0.75
    p = tmp_path / "cso_rule.yaml"
    p.write_text(yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")
    assert R.load_rules(str(p)).defaults.review_threshold == 0.75

    # ② 값이 없으면 종전과 같은 0.6.
    base["defaults"].pop("review_threshold")
    p.write_text(yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")
    assert R.load_rules(str(p)).defaults.review_threshold == 0.6

    # ③ 이상한 값은 조용히 넘기지 않고 막는다 — 0 이면 검토 큐가 통째로 비고,
    #    1 을 넘으면 모든 문서가 들어온다. 둘 다 화면을 못 쓰게 만든다.
    for bad in (0, 1.5, "높게"):
        base["defaults"]["review_threshold"] = bad
        p.write_text(yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")
        with pytest.raises(R.RuleSetValidationError):
            R.load_rules(str(p))



# ── PII 등급 상한 — 화면 저장 (2026-09-21) ────────────────────────────────

#------------------------------------------------------------------
# 화면의 상한 선택이 규칙 파일 칸으로 옮겨진다
#=> 'S 까지'를 고르면 defaults.pii_grade_cap: S 가 적히고, '제한 없음'을 고르면
#   칸이 지워져야 한다(칸이 없는 것이 곧 종전 동작). 상한 인자를 안 주면
#   (다른 편집만 저장) 파일에 있던 값을 건드리지 않아야 한다.
#
# -in: 없음
#
# -out: 없음(단언)
# -out: error = 어긋나면 AssertionError
#------------------------------------------------------------------
def test_PII_상한은_화면에서_저장된다():
    import rulesedit

    doc = {"defaults": {"bulk_threshold": 5}, "keywords": []}
    rulesedit.apply_all(doc, 5, [], pii_cap="S")
    assert doc["defaults"]["pii_grade_cap"] == "S"
    # 상한을 주지 않은 저장은 기존 값을 그대로 둔다.
    rulesedit.apply_all(doc, 7, [])
    assert doc["defaults"]["pii_grade_cap"] == "S"
    # '제한 없음'(None)은 칸을 지운다.
    rulesedit.apply_all(doc, 5, [], pii_cap=None)
    assert "pii_grade_cap" not in doc["defaults"]
