#------------------------------------------------------------------
# ui/app.py — 문서함 '여러 건 한꺼번에 기준 문서 등록' 단위 테스트
#=> 화면(Streamlit) 없이 판단(plan_bulk_seed)과 적용(apply_bulk_seed)만 검증한다.
#   이 두 함수가 같은 계산을 쓰기 때문에 "표에 보여 준 것과 실제 등록이 다르다"는
#   사고가 생기지 않는다 — 그 계약을 여기서 못박는다.
#------------------------------------------------------------------

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "ui"))

import seedstore as S     # noqa: E402
import app                # noqa: E402  (main 은 __main__ 가드 안에 있어 import 해도 안 돈다)


#------------------------------------------------------------------
# 테스트용 분류 레코드 만들기
#=> 업무분류 후보는 엔진이 내는 labels.doctype.values 모양을 그대로 흉내 낸다.
#
# -in: file  = 문서 경로
# -in: grade = 자동 판정 등급
# -in: dc    = 자동 제안된 dc_id 리스트
# -in: vec   = 벡터(없으면 None — 등록 시 임베딩 대상이 된다)
#
# -out: dict = 분류 레코드
# -out: error = 없음
#------------------------------------------------------------------
def rec(file, grade="C", dc=(), vec=(1.0, 0.0), hash_=None):
    r = {"file": file, "grade": grade, "hash": hash_ or "h_" + file,
         "labels": {"doctype": {"values": [{"dc_id": d, "confidence": 0.9} for d in dc]}}}
    if vec is not None:
        r["vector"] = list(vec)
    return r


#------------------------------------------------------------------
# 자동으로 찾은 분류도 올라가고, 관리자가 거절한 것만 빠진다
#=> 자동 제안을 '확정'으로 보기로 했으므로 기준 문서에도 그대로 올라간다.
#   사람이 아니라고 표시한 것만 제외된다 — 그 판단은 되돌리면 안 된다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_거절한_분류만_빠진다():
    r1 = rec("a.doc", "C", dc=["DC_1"])          # 아무 결정도 없음 → 자동 확정
    r2 = rec("b.doc", "S", dc=["DC_2", "DC_3"])
    latest_dt = {"b.doc": {"confirmed": ["DC_2"], "rejected": ["DC_3"]}}
    plan = app.plan_bulk_seed(["a.doc", "b.doc"], {"a.doc": r1, "b.doc": r2},
                              {}, latest_dt, [])
    by = {x["file"]: x for x in plan}
    assert by["a.doc"]["dc_ids"] == ["DC_1"]     # 자동 확정도 올라간다
    assert by["a.doc"]["grade"] == "C"
    assert by["b.doc"]["dc_ids"] == ["DC_2"]     # 거절한 DC_3 는 빠진다


#------------------------------------------------------------------
# 등급도 확정 분류도 없으면 이유와 함께 빠진다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_올릴_것이_없으면_빠진다():
    r = rec("c.doc", None, dc=[])                # 보류 + 붙은 분류도 없음
    plan = app.plan_bulk_seed(["c.doc"], {"c.doc": r}, {}, {}, [])
    assert plan[0]["how"] == app.BULK_SKIP
    out, stats = app.apply_bulk_seed(plan, [], "관리자")
    assert out == [] and stats == {"new": 0, "update": 0, "dup": 0, "fail": 0}


#------------------------------------------------------------------
# 사람이 고친 등급이 자동 판정보다 우선한다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_사람이_고친_등급이_우선():
    r = rec("d.doc", "O")
    plan = app.plan_bulk_seed(["d.doc"], {"d.doc": r},
                              {"d.doc": {"new_grade": "C"}}, {}, [])
    assert plan[0]["grade"] == "C"


#------------------------------------------------------------------
# 여러 건을 한 번에 올리면 두 축이 각각 얹히고 감사 기록도 축마다 남는다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_여러건_한꺼번에_등록(tmp_path):
    audit = str(tmp_path / "class_seed_audit.jsonl")
    recs = {"a.doc": rec("a.doc", "C", vec=(1.0, 0.0)),
            "b.doc": rec("b.doc", "S", dc=["DC_2"], vec=(0.0, 1.0))}
    latest_dt = {"b.doc": {"confirmed": ["DC_2"], "rejected": []}}
    plan = app.plan_bulk_seed(["a.doc", "b.doc"], recs, {}, latest_dt, [])
    assert [x["how"] for x in plan] == ["새로 등록", "새로 등록"]

    out, stats = app.apply_bulk_seed(plan, [], "관리자", audit_path=audit)
    assert stats["new"] == 2 and stats["dup"] == 0 and stats["fail"] == 0
    a = S.find_seed(out, "a.doc")
    b = S.find_seed(out, "b.doc")
    assert a["grade"] == "C" and "doctype" not in a.get("labels", {})
    assert b["labels"] == {"security": "S", "doctype": ["DC_2"]}
    assert b["doc"]["hash"] == "h_b.doc"          # 원본 지문이 함께 적힌다
    # 감사: 보안 2줄 + 업무분류 1줄
    lines = open(audit, encoding="utf-8").read().strip().splitlines()
    assert len(lines) == 3


#------------------------------------------------------------------
# 이번에 함께 고른 문서끼리 사본이면 뒤엣것이 걸러진다
#=> 사본이 쌓이면 그 문서가 전파에서 표를 두 번 던진다. force 면 그래도 등록한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_사본은_건너뛰고_force_면_등록():
    recs = {"a.doc": rec("a.doc", "C", vec=(1.0, 0.0)),
            "a_사본.doc": rec("a_사본.doc", "C", vec=(0.9999, 0.001))}
    files = ["a.doc", "a_사본.doc"]
    plan = app.plan_bulk_seed(files, recs, {}, {}, [])

    out, stats = app.apply_bulk_seed(plan, [], "관리자")
    assert (stats["new"], stats["dup"]) == (1, 1)
    assert len(out) == 1

    out2, stats2 = app.apply_bulk_seed(plan, [], "관리자", force=True)
    assert (stats2["new"], stats2["dup"]) == (2, 0)


#------------------------------------------------------------------
# 이미 등록된 문서는 '갱신'으로 세고, 예전 업무분류를 지우지 않는다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_이미_등록된_문서는_갱신되고_옛_분류를_지키다():
    seeds = S.set_axis([], "a.doc", "security", "O", "x", vector=[1.0, 0.0])
    seeds = S.set_axis(seeds, "a.doc", "doctype", ["DC_옛것"], "x")
    # 이번엔 등급만 C 로 바뀌고 확정 분류는 없다.
    plan = app.plan_bulk_seed(["a.doc"], {"a.doc": rec("a.doc", "C", vec=(1.0, 0.0))},
                              {}, {}, seeds)
    assert plan[0]["how"] == "갱신"
    assert plan[0]["dc_ids"] == ["DC_옛것"]        # 예전 분류를 지우지 않는다

    out, stats = app.apply_bulk_seed(plan, seeds, "관리자", force=True)
    assert stats["update"] == 1 and stats["new"] == 0
    e = S.find_seed(out, "a.doc")
    assert e["grade"] == "C"
    assert e["labels"]["doctype"] == ["DC_옛것"]


#------------------------------------------------------------------
# 벡터가 없는 문서는 임베딩해서 올리고, 임베딩이 실패하면 그 문서만 빠진다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_벡터가_없으면_임베딩하고_실패는_그_문서만_빠진다():
    recs = {"a.doc": rec("a.doc", "C", vec=None),
            "b.doc": rec("b.doc", "S", vec=None)}
    plan = app.plan_bulk_seed(["a.doc", "b.doc"], recs, {}, {}, [])
    called = []

    def embed(f):
        called.append(f)
        return [0.0, 1.0] if f == "a.doc" else None    # b 는 실패

    out, stats = app.apply_bulk_seed(plan, [], "관리자", embed=embed)
    assert called == ["a.doc", "b.doc"]
    assert (stats["new"], stats["fail"]) == (1, 1)
    assert [x["file"] for x in out] == ["a.doc"]


#------------------------------------------------------------------
# 진행 알림이 건마다 온다(진행바가 멈춘 것처럼 보이지 않게)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_진행_알림이_건마다_온다():
    recs = {"a.doc": rec("a.doc", "C", vec=(1.0, 0.0)),
            "b.doc": rec("b.doc", "S", vec=(0.0, 1.0))}
    plan = app.plan_bulk_seed(["a.doc", "b.doc"], recs, {}, {}, [])
    seen = []
    app.apply_bulk_seed(plan, [], "관리자",
                        on_progress=lambda d, t, f: seen.append((d, t)))
    assert seen == [(0, 2), (1, 2), (2, 2)]
