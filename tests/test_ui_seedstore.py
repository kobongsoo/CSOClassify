#------------------------------------------------------------------
# ui/seedstore.py — 기준 문서 저장소 v2(두 축) 단위 테스트
#=> 한 문서 = 한 줄 = 벡터 하나를 유지한 채 보안등급·업무분류 두 축이 그 줄을
#   나눠 쓰는 구조를 검증한다. 특히 다음 세 가지가 이 저장소의 핵심 계약이다.
#    1) axes 가 진실이고 grade/labels 는 저장할 때 다시 만들어지는 파생값이다
#    2) 축 하나를 해제해도 다른 축은 살아 있다(둘 다 죽으면 줄이 사라진다)
#    3) 시스템은 표시(suspect)만 하고, 지우는 것은 사람이 한다
#------------------------------------------------------------------

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "ui"))

import seedstore as S   # noqa: E402


#------------------------------------------------------------------
# 기존 항목이 없으면 새로 만든다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_기존_seed_없으면_새로_만든다():
    seeds = S.add_doctype_seed([], "a.hwp", ["DC_006_001"], [0.1, 0.2], "고봉수")
    assert len(seeds) == 1
    assert seeds[0]["labels"]["doctype"] == ["DC_006_001"]
    assert seeds[0]["vector"] == [0.1, 0.2]
    assert seeds[0]["approved_by"] == "고봉수"


#------------------------------------------------------------------
# 보안등급으로 이미 등록된 줄에 업무분류만 얹힌다(등급은 그대로)
#=> 벡터는 같은 문서에서 나온 최신 값이므로 갱신한다 — 원본이 바뀌었을 때
#   [다시 읽어 갱신]이 옛 벡터를 남기지 않아야 하기 때문이다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_기존_security_seed에_doctype_추가():
    seeds = [{"file": "a.hwp", "grade": "S", "vector": [1.0, 0.0],
             "source": "phase4", "approved_by": "x", "ts": "t", "note": ""}]
    out = S.add_doctype_seed(seeds, "a.hwp", ["DC_006_001"], [9.9, 9.9], "고봉수")
    assert len(out) == 1
    e = out[0]
    assert e["grade"] == "S"                      # 보안축은 손대지 않는다
    assert e["vector"] == [9.9, 9.9]              # 새로 준 벡터로 갱신
    assert e["labels"]["doctype"] == ["DC_006_001"]
    assert e["labels"]["security"] == "S"         # 파생값은 axes 에서 다시 만들어진다
    assert S.axis_value(e, "security") == "S"     # 옛 grade 가 axes 로 승격됐다


#------------------------------------------------------------------
# 같은 문서를 다시 확정하면 라벨이 교체된다(누적이 아니라 최신 확정으로)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_다시_호출하면_라벨이_교체된다():
    seeds = S.add_doctype_seed([], "a.hwp", ["DC_006_001"], [1, 2], "x")
    seeds = S.add_doctype_seed(seeds, "a.hwp", ["DC_006_001", "DC_003_001"], [1, 2], "x")
    assert len(seeds) == 1
    assert seeds[0]["labels"]["doctype"] == ["DC_006_001", "DC_003_001"]


#------------------------------------------------------------------
# 다른 문서의 항목은 건드리지 않는다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_다른_파일은_건드리지_않는다():
    seeds = [{"file": "b.hwp", "grade": "C", "vector": [1, 1],
             "source": "phase4", "approved_by": "x", "ts": "t", "note": ""}]
    out = S.add_doctype_seed(seeds, "a.hwp", ["DC_001"], [0, 0], "x")
    assert len(out) == 2
    b = next(s for s in out if s["file"] == "b.hwp")
    assert "labels" not in b


#------------------------------------------------------------------
# 경로 표기만 다른 같은 문서는 한 줄로 모인다
#=> 예전에는 add_seed 만 정규화 비교를 해서, 지우기·중복검사는 표기가 다르면
#   실패하면서도 성공한 것처럼 보였다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_경로_표기가_달라도_같은_문서():
    win = "D:" + chr(92) + "sample" + chr(92) + "a.doc"
    seeds = S.add_seed([], win, "C", [1, 0], "x")
    seeds = S.add_doctype_seed(seeds, "d:/SAMPLE/a.doc", ["DC_1"], [1, 0], "x")
    assert len(seeds) == 1
    assert S.find_seed(seeds, "D:/Sample/A.DOC") is not None
    assert S.remove_seed(seeds, "d:/sample/a.doc") == []


#------------------------------------------------------------------
# 축 하나를 해제해도 다른 축은 살아 있다
#=> 값은 지우지 않는다(무엇을 뺐는지 남아야 하고, 되살릴 수도 있어야 한다).
#   다만 엔진이 읽는 파생값에서는 빠진다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_축_하나만_해제하면_다른_축은_남는다():
    seeds = S.add_seed([], "a.hwp", "C", [1, 0], "x")
    seeds = S.add_doctype_seed(seeds, "a.hwp", ["DC_1"], [1, 0], "x")

    seeds, removed = S.retire_axis(seeds, "a.hwp", "doctype")
    assert removed is False
    assert seeds[0]["grade"] == "C"                       # 보안축은 그대로
    assert "doctype" not in seeds[0].get("labels", {})    # 엔진에는 안 보인다
    assert S.axis_value(seeds[0], "doctype") == ["DC_1"]  # 값은 남아 있다
    assert S.active_axes(seeds[0]) == ("security",)

    seeds, removed = S.retire_axis(seeds, "a.hwp", "security")
    assert removed is True                                 # 살아 있는 축이 없다
    assert "grade" not in seeds[0]


#------------------------------------------------------------------
# 살아 있는 축이 없는 줄은 저장 시 파일에서 빠진다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_두_축_모두_해제된_줄은_저장되지_않는다(tmp_path):
    seeds = S.add_seed([], "a.hwp", "C", [1, 0], "x")
    seeds, _ = S.retire_axis(seeds, "a.hwp", "security")
    p = str(tmp_path / "class_seed.jsonl")
    assert S.save_seeds(p, seeds) == 0
    assert S.load_seeds(p) == []


#------------------------------------------------------------------
# 원본이 바뀌면 표시만 붙고, 되돌아오면 표시가 풀린다
#=> 시스템은 절대 지우지 않는다 — 값은 그대로 두고 상태만 바꾼다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_원본이_바뀌면_확인필요로_빠지고_되돌아오면_복귀(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("hello", encoding="utf-8")
    f = str(real)
    seeds = S.set_axis([], f, "security", "S", "x", vector=[0, 1], doc={"hash": "aa"})

    s2, ev = S.sync_with_records(seeds, [{"file": f, "hash": "bb"}])
    assert [e["reason"] for e in ev] == ["stale"]
    assert S.axis_state(s2[0], "security") == S.STATE_SUSPECT
    assert "grade" not in s2[0]                     # 전파에서 빠진다
    assert S.axis_value(s2[0], "security") == "S"   # 값은 살아 있다

    s3, ev2 = S.sync_with_records(s2, [{"file": f, "hash": "aa"}])
    assert [e["action"] for e in ev2] == ["restore"]
    assert s3[0]["grade"] == "S"


#------------------------------------------------------------------
# 사람이 해제한 축은 시스템이 건드리지 않는다
#=> 사람의 판단이 시스템의 자동 표시보다 우선한다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_사람이_해제한_축은_시스템이_안_건드린다(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("hello", encoding="utf-8")
    f = str(real)
    seeds = S.set_axis([], f, "doctype", ["DC_1"], "x", vector=[0, 1], doc={"hash": "aa"})
    seeds, _ = S.retire_axis(seeds, f, "doctype")
    out, ev = S.sync_with_records(seeds, [{"file": f, "hash": "zz"}])
    assert ev == []
    assert S.axis_state(out[0], "doctype") == S.STATE_RETIRED


#------------------------------------------------------------------
# 점검 — 없는 분류코드·벡터 없음·원본 없음을 센다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_점검이_이상한_기준문서를_찾아낸다(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("hello", encoding="utf-8")
    seeds = S.set_axis([], str(real), "doctype", ["DC_1", "DC_사라짐"], "x", vector=[1, 0])
    seeds = S.set_axis(seeds, str(tmp_path / "없는파일.doc"), "security", "C", "x")
    rep = S.check_seeds(seeds, records=[], tax={"by_id": {"DC_1": {}}})
    assert [x["dc_ids"] for x in rep["orphan"]] == [["DC_사라짐"]]
    assert len(rep["missing"]) == 1
    assert len(rep["novec"]) == 1


#------------------------------------------------------------------
# 사실상 같은 문서인데 등급이 다르면 잡아낸다
#=> 이 쌍이 있으면 그 근처 문서의 전파가 mixed 로 죽는다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_등급이_충돌하는_사본을_찾아낸다():
    seeds = S.set_axis([], "x1", "security", "C", "x", vector=[1, 0, 0])
    seeds = S.set_axis(seeds, "x2", "security", "O", "x", vector=[0.999, 0.01, 0])
    conf = S.check_seeds(seeds)["conflict"]
    assert len(conf) == 1
    assert {conf[0]["ga"], conf[0]["gb"]} == {"C", "O"}


#------------------------------------------------------------------
# 저장 → 로드 왕복에서 v2 모양이 유지된다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_저장하고_다시_읽어도_같다(tmp_path):
    p = str(tmp_path / "class_seed.jsonl")
    seeds = S.add_seed([], "a.hwp", "C", [1, 0], "고봉수", doc={"hash": "aa"})
    seeds = S.add_doctype_seed(seeds, "a.hwp", ["DC_1"], [1, 0], "고봉수")
    S.save_seeds(p, seeds)
    back = S.load_seeds(p)
    assert back[0]["v"] == S.SCHEMA_VERSION
    assert back[0]["grade"] == "C"
    assert back[0]["labels"] == {"security": "C", "doctype": ["DC_1"]}
    assert back[0]["doc"]["hash"] == "aa"
    assert S.axis_counts(back) == {"total": 1, "security": 1, "doctype": 1,
                                   "both": 1, "suspect": 0, "retired": 0}
