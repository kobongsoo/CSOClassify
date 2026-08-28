#------------------------------------------------------------------
# 검토 UI — 보안등급 수정 기록 로드 회귀 테스트
#=> cso_override.jsonl 한 파일에 두 축(security · doctype)의 결정이 함께 쌓인다.
#   app.load_overrides() 가 축을 가리지 않고 모두 읽으면, 업무분류를 확정했을 뿐인
#   문서가 "사람이 등급을 고친 것"으로 오해되고 new_grade 가 없어 등급이 판단 못 함
#   으로 뒤바뀐다. 이 파일은 그 사고가 다시 나지 않는지 지킨다.
#------------------------------------------------------------------

import json
import os
import sys

import pytest

# ui/ 는 패키지가 아니라 스크립트 폴더라 경로를 직접 추가해 import 한다.
_UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")
if _UI not in sys.path:
    sys.path.insert(0, _UI)

app = pytest.importorskip("app", reason="streamlit 이 설치돼 있어야 UI 모듈을 읽을 수 있다")


#------------------------------------------------------------------
# 테스트용 오버라이드 파일 만들기
#=> 줄 목록을 jsonl 로 써서 경로를 돌려준다.
#
# -in: tmp_path = pytest 임시 폴더
# -in: rows     = 파일에 쓸 dict 목록
#
# -out: str = 만들어진 jsonl 경로
# -out: error = 없음
#------------------------------------------------------------------
def _write(tmp_path, rows):
    p = tmp_path / "cso_override.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")
    return str(p)


#------------------------------------------------------------------
# 업무분류 결정 줄은 보안등급 기록으로 읽지 않는다
#=> axis:"doctype" 줄만 있는 문서는 "보안등급을 고친 적 없는" 상태여야 한다.
#
# -in: tmp_path = pytest 임시 폴더(자동 주입)
#
# -out: 없음(단언)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_doctype_line_is_not_a_security_override(tmp_path):
    path = _write(tmp_path, [
        {"file": "D:/a.docx", "axis": "doctype", "confirmed": ["DC_001"],
         "rejected": [], "reviewer": "고봉수", "ts": "t"},
    ])
    latest, history = app.load_overrides(path)
    assert latest == {}
    assert "D:/a.docx" not in history

    # 등급도 자동 판정 그대로 남아야 한다(판단 못 함으로 뒤바뀌면 안 된다).
    rec = {"file": "D:/a.docx", "grade": "S"}
    grade, overridden = app.effective_grade(rec, latest)
    assert (grade, overridden) == ("S", False)


#------------------------------------------------------------------
# axis 가 없는 옛 기록은 보안등급 수정으로 읽는다
#=> 두 축이 나뉘기 전에 쌓인 줄에는 axis 가 없다. 이 줄들을 버리면 과거에 사람이
#   고친 등급이 통째로 사라지므로, 반드시 security 로 읽어야 한다.
#
# -in: tmp_path = pytest 임시 폴더(자동 주입)
#
# -out: 없음(단언)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_legacy_line_without_axis_is_security(tmp_path):
    path = _write(tmp_path, [
        {"file": "D:/b.docx", "old_grade": "O", "new_grade": "C",
         "reason": "대외비", "reviewer": "고봉수", "ts": "t"},
    ])
    latest, history = app.load_overrides(path)
    assert latest["D:/b.docx"]["new_grade"] == "C"
    assert len(history["D:/b.docx"]) == 1
    assert app.effective_grade({"file": "D:/b.docx", "grade": "O"}, latest) == ("C", True)


#------------------------------------------------------------------
# 두 축이 섞인 파일에서 각자 자기 줄만 읽는다
#=> 같은 문서에 security 수정과 doctype 확정이 모두 쌓인 흔한 상황이다.
#   보안등급 쪽은 마지막 security 줄을, 업무분류 쪽은 마지막 doctype 줄을 본다.
#
# -in: tmp_path = pytest 임시 폴더(자동 주입)
#
# -out: 없음(단언)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_two_axes_do_not_mix(tmp_path):
    import doctype_review
    f = "D:/c.docx"
    path = _write(tmp_path, [
        {"file": f, "axis": "security", "old_grade": "S", "new_grade": "C",
         "reason": "도장", "reviewer": "고봉수", "ts": "t1"},
        {"file": f, "axis": "doctype", "confirmed": ["DC_006_001"],
         "rejected": ["DC_003_001"], "reviewer": "고봉수", "ts": "t2"},
    ])
    latest, history = app.load_overrides(path)
    assert latest[f]["new_grade"] == "C"
    assert len(history[f]) == 1                      # doctype 줄은 이력에 섞이지 않는다

    latest_dt, _ = doctype_review.load_doctype_overrides(path)
    assert latest_dt[f]["confirmed"] == ["DC_006_001"]

    rec = {"file": f, "grade": "S",
           "labels": {"doctype": {"values": [
               {"dc_id": "DC_006_001", "path": "법무/규정 > 계약서", "confidence": 0.85},
               {"dc_id": "DC_003_001", "path": "영업/마케팅 > 제안서", "confidence": 0.7}]}}}
    assert app.effective_grade(rec, latest) == ("C", True)
    cands, reviewed = doctype_review.effective_doctype(rec, latest_dt)
    assert reviewed is True
    assert [c["status"] for c in cands] == ["confirmed", "rejected"]


#------------------------------------------------------------------
# 표 만들기 — 두 축의 '할 일'이 각각 맞게 계산된다
#=> records_to_df 가 문서 한 줄에 두 축을 함께 담고, need_sec/need_doc 을 축마다
#   따로 판단하는지 확인한다. 화면의 검토함·상태 열이 전부 이 두 값에 달려 있다.
#   [need_doc 의 뜻] '업무분류가 하나도 안 붙은 문서'(사람이 직접 정해야 함)다.
#   기계가 후보를 낸 문서는 이미 분류된 것으로 보고 할 일에서 뺀다.
#   [축 미사용과의 구분] labels 에 "doctype" 키가 아예 없으면 그 배포는 이 축을
#   안 쓴 것이라 할 일도 없다 — 이게 없으면 보안등급만 쓰는 배포에서 모든 문서가
#   "업무분류 확인 필요"로 잡힌다.
#
# -in: 없음
#
# -out: 없음(단언)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_records_to_df_marks_todo_per_axis():
    recs = [
        # 등급도 확실하고 업무분류 후보도 붙은 문서 → 할 일 없음
        {"file": "D:/x.docx", "grade": "C", "confidence": 0.95,
         "labels": {"doctype": {"values": [
             {"dc_id": "DC_1", "path": "법무/규정 > 계약서", "confidence": 0.8}]}}},
        # 축을 안 쓰는 배포(doctype 키 없음)에서 등급만 못 정한 문서 → 보안등급만 할 일
        {"file": "D:/y.docx", "grade": None, "confidence": 0.0},
        # 축은 돌았는데 아무 분류도 못 붙은 문서 → 업무분류만 할 일
        {"file": "D:/z.docx", "grade": "S", "confidence": 0.9,
         "labels": {"doctype": {"values": []}}},
    ]
    df = app.records_to_df(recs, {}, {})
    x = df[df["file"] == "D:/x.docx"].iloc[0]
    y = df[df["file"] == "D:/y.docx"].iloc[0]
    z = df[df["file"] == "D:/z.docx"].iloc[0]

    assert (bool(x["need_sec"]), bool(x["need_doc"])) == (False, False)
    assert (bool(y["need_sec"]), bool(y["need_doc"])) == (True, False)
    assert (bool(z["need_sec"]), bool(z["need_doc"])) == (False, True)
    # 자동으로 찾은 분류도 확정으로 보므로 물음표를 붙이지 않는다.
    assert x["doctype"] == "법무/규정 > 계약서"
    assert y["doctype"] == "— 없음"
    assert z["doctype"] == "— 없음"


#------------------------------------------------------------------
# '이대로 확정'한 문서는 검토함에서 내려간다 (같은 등급 확정)
#=> 실제로 났던 버그다. 확신이 낮아 검토함에 올라온 문서를 담당자가 보고
#   "자동 판정이 맞다"고 판단하면 끝낼 방법이 없었다 — 같은 등급으로 저장하는
#   것을 화면이 거절했기 때문에 그 문서가 검토함에서 영영 내려가지 않았다.
#   이제는 같은 등급도 kind="confirm" 한 줄로 남고, need_sec 가 꺼진다.
#   다만 '고침'(overridden 열)으로 세지는 않는다 — 값이 안 바뀌었기 때문이다.
#
# -in: tmp_path = pytest 임시 폴더(자동 주입)
#
# -out: 없음(단언)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_confirm_same_grade_clears_review_queue(tmp_path):
    f = "D:/sample1/정치.pdf"
    # 확신 50% → 저확신이라 검토함에 올라오는 문서
    rec = {"file": f, "grade": "S", "confidence": 0.5,
           "labels": {"security": {}, "doctype": {"values": []}}}

    latest, _ = app.load_overrides(str(tmp_path / "none.jsonl"))
    df = app.records_to_df([rec], latest)
    assert bool(df.iloc[0]["need_sec"]) is True      # 확정 전에는 할 일이 남아 있다

    # 등급을 바꾸지 않고 '이대로 확정'만 한다.
    path = tmp_path / "cso_override.jsonl"
    app.append_override(str(path), f, "S", "S", "", "고봉수", kind="confirm")

    latest, history = app.load_overrides(str(path))
    assert latest[f]["kind"] == "confirm"
    assert app.effective_grade(rec, latest) == ("S", True)
    assert app.grade_changed(rec, latest) is False   # 값은 안 바뀌었다

    df = app.records_to_df([rec], latest)
    assert bool(df.iloc[0]["need_sec"]) is False     # 검토함에서 내려간다
    assert bool(df.iloc[0]["overridden"]) is False   # '고침' 건수에는 안 들어간다
    assert df.iloc[0]["final"] == "S"


#------------------------------------------------------------------
# 등급을 실제로 바꾼 문서는 '고침'으로 센다
#=> 위 테스트의 짝. confirm 과 change 가 검토함에서는 똑같이 취급되지만
#   '관리자가 고친 문서' 집계에서는 갈라져야 한다.
#
# -in: tmp_path = pytest 임시 폴더(자동 주입)
#
# -out: 없음(단언)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_changed_grade_counts_as_override(tmp_path):
    f = "D:/sample1/계약서.pdf"
    rec = {"file": f, "grade": "S", "confidence": 0.5,
           "labels": {"security": {}, "doctype": {"values": []}}}
    path = tmp_path / "cso_override.jsonl"
    app.append_override(str(path), f, "S", "C", "도장 있음", "고봉수", kind="change")

    latest, _ = app.load_overrides(str(path))
    assert app.grade_changed(rec, latest) is True
    df = app.records_to_df([rec], latest)
    assert bool(df.iloc[0]["need_sec"]) is False
    assert bool(df.iloc[0]["overridden"]) is True
    assert df.iloc[0]["final"] == "C"
