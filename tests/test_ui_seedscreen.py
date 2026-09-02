#------------------------------------------------------------------
# ui/app.py — '기준문서' 화면의 편집 로직 단위 테스트
#=> 화면(Streamlit) 없이 '무엇이 바뀌는가'(seed_edit_ops)와 '어떻게 적용되는가'
#   (apply_seed_edit)만 검증한다. 이 화면에서 가장 헷갈리는 것은 **해제와 값 변경이
#   다른 일**이라는 점이다 — 해제는 값을 지우지 않고 잣대에서만 뺀다.
#------------------------------------------------------------------

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "ui"))

import seedstore as S     # noqa: E402
import app                # noqa: E402


#------------------------------------------------------------------
# 두 축이 모두 살아 있는 기준 문서 한 건 만들기
#
# -in: 없음
# -out: dict = 기준 문서 한 줄
# -out: error = 없음
#------------------------------------------------------------------
def mk():
    seeds = S.set_axis([], "d:/s/a.doc", "security", "C", "관리자", vector=[1.0, 0.0])
    seeds = S.set_axis(seeds, "d:/s/a.doc", "doctype", ["DC_1", "DC_2"], "관리자")
    return seeds[0]


#------------------------------------------------------------------
# 아무것도 안 고치면 할 일이 없다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_안_고치면_할_일이_없다():
    e = mk()
    assert app.seed_edit_ops(e, "C", ["DC_1", "DC_2"], "") == []


#------------------------------------------------------------------
# 등급만 바꾸면 보안축만 손댄다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_등급만_바꾸기():
    e = mk()
    ops = app.seed_edit_ops(e, "S", ["DC_1", "DC_2"], "")
    assert ops == [{"axis": "security", "action": "set", "before": "C", "after": "S"}]
    out = app.apply_seed_edit([e], "d:/s/a.doc", ops, "관리자")
    got = S.find_seed(out, "d:/s/a.doc")
    assert got["grade"] == "S"
    assert got["labels"]["doctype"] == ["DC_1", "DC_2"]   # 다른 축은 그대로


#------------------------------------------------------------------
# 보안축을 '해제'하면 값은 남고 잣대에서만 빠진다
#=> 업무분류축은 살아 있으므로 줄 자체는 남아야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_보안축_해제는_값을_지우지_않는다():
    e = mk()
    ops = app.seed_edit_ops(e, "해제", ["DC_1", "DC_2"], "")
    assert [o["action"] for o in ops] == ["retire"]
    out = app.apply_seed_edit([e], "d:/s/a.doc", ops, "관리자")
    got = S.find_seed(out, "d:/s/a.doc")
    assert "grade" not in got                       # 엔진에는 안 보인다
    assert S.axis_value(got, "security") == "C"     # 값은 남아 있다
    assert S.active_axes(got) == ("doctype",)


#------------------------------------------------------------------
# 업무분류를 비우면 그 축이 해제된다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_업무분류를_비우면_해제():
    e = mk()
    ops = app.seed_edit_ops(e, "C", [], "")
    assert ops == [{"axis": "doctype", "action": "retire",
                    "before": ["DC_1", "DC_2"], "after": None}]
    out = app.apply_seed_edit([e], "d:/s/a.doc", ops, "관리자")
    got = S.find_seed(out, "d:/s/a.doc")
    assert "doctype" not in got.get("labels", {})
    assert got["grade"] == "C"


#------------------------------------------------------------------
# 두 축을 모두 해제하면 저장 시 목록에서 빠진다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_두_축_모두_해제하면_사라진다(tmp_path):
    e = mk()
    ops = app.seed_edit_ops(e, "해제", [], "")
    assert len(ops) == 2
    out = app.apply_seed_edit([e], "d:/s/a.doc", ops, "관리자")
    p = str(tmp_path / "class_seed.jsonl")
    assert S.save_seeds(p, out) == 0
    assert S.load_seeds(p) == []


#------------------------------------------------------------------
# 업무분류를 다른 목록으로 바꾸면 교체된다(누적이 아니다)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_업무분류_교체():
    e = mk()
    ops = app.seed_edit_ops(e, "C", ["DC_9"], "")
    out = app.apply_seed_edit([e], "d:/s/a.doc", ops, "관리자")
    assert S.find_seed(out, "d:/s/a.doc")["labels"]["doctype"] == ["DC_9"]


#------------------------------------------------------------------
# 메모만 고치면 축은 건드리지 않는다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_메모만_고치기():
    e = mk()
    ops = app.seed_edit_ops(e, "C", ["DC_1", "DC_2"], "대표 문서")
    assert [o["action"] for o in ops] == ["note"]
    out = app.apply_seed_edit([e], "d:/s/a.doc", ops, "관리자")
    got = S.find_seed(out, "d:/s/a.doc")
    assert got["note"] == "대표 문서"
    assert got["grade"] == "C" and got["labels"]["doctype"] == ["DC_1", "DC_2"]


#------------------------------------------------------------------
# 축마다 따로 감사 기록이 남는다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_감사_기록이_축마다_남는다(tmp_path):
    e = mk()
    audit = str(tmp_path / "class_seed_audit.jsonl")
    ops = app.seed_edit_ops(e, "O", ["DC_9"], "메모")
    app.apply_seed_edit([e], "d:/s/a.doc", ops, "관리자", audit_path=audit)
    rows = [json.loads(l) for l in open(audit, encoding="utf-8") if l.strip()]
    assert [(r["axis"], r["action"]) for r in rows] == [
        ("security", "update"), ("doctype", "update"), ("-", "update")]
    assert rows[0]["before"] == "C" and rows[0]["after"] == "O"


#------------------------------------------------------------------
# 해제한 축을 다시 값으로 되돌릴 수 있다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_해제한_축을_되살리기():
    e = mk()
    out = app.apply_seed_edit([e], "d:/s/a.doc",
                              app.seed_edit_ops(e, "해제", ["DC_1", "DC_2"], ""), "관리자")
    e2 = S.find_seed(out, "d:/s/a.doc")
    ops = app.seed_edit_ops(e2, "S", ["DC_1", "DC_2"], "")
    assert ops == [{"axis": "security", "action": "set", "before": None, "after": "S"}]
    out2 = app.apply_seed_edit(out, "d:/s/a.doc", ops, "관리자")
    assert S.find_seed(out2, "d:/s/a.doc")["grade"] == "S"
