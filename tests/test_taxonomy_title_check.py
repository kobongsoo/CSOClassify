"""⓪-b 넓은 제목 자동 판정 규칙 — 고객 데이터 없이 일반 제목으로만 검사한다."""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "evalset"))

import taxonomy_title_check as ttc  # noqa: E402

ENDINGS = ttc.load_doctype_endings()


#------------------------------------------------------------------
# 판정 한 건 도우미
#=> 다른 제목 목록을 받아 judge_title 결과의 이유 코드만 돌려준다.
#
# -in: title  = 검사할 제목
# -in: others = 다른 분류 제목들(기본 없음)
#
# -out: str = 이유 코드("ok" 면 핵어로 쓴다)
# -out: error = 없음
#------------------------------------------------------------------
def _reason(title, others=()):
    return ttc.judge_title(title, list(others), ENDINGS)[1]


#------------------------------------------------------------------
# 좁은 문서종류 제목은 통과한다
#=> 회의록·계약서처럼 끝이 문서종류 명사이고 넓지 않으면 핵어로 쓴다.
#
# -in: 없음
#
# -out: 없음
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_narrow_doctype_passes():
    for t in ("회의록", "계약서", "견적서", "요구사항정의서", "테스트결과서", "세금계산서",
              "보고서", "예산서", "규정집"):
        assert _reason(t) == "ok", t


#------------------------------------------------------------------
# 넓은 말·묶음 말·주제형 제목은 걸러진다
#=> 설계서 7장 ⓐ 의 세 규칙이 각각 제 이유로 걸리는지 본다.
#
# -in: 없음
#
# -out: 없음
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_broad_container_topic_filtered():
    assert _reason("자료") == "broad_exact"
    assert _reason("자 료") == "broad_exact"          # 띄어쓰기 무시
    assert _reason("영업자료") == "container"
    assert _reason("인사서류") == "container"
    assert _reason("복리후생") == "not_doctype"
    assert _reason("자사제품") == "not_doctype"


#------------------------------------------------------------------
# 다른 분류 제목이 같은 끝말을 가지면 모호해서 걸러진다
#=> "설계서" 는 "DB설계서" 와 겹쳐 어느 쪽으로 보낼지 정할 수 없다.
#   긴 쪽("DB설계서")은 더 긴 제목이 없으니 통과한다.
#
# -in: 없음
#
# -out: 없음
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_ambiguous_suffix_filtered():
    assert _reason("설계서", ["DB설계서"]) == "ambiguous"
    assert _reason("DB설계서", ["설계서"]) == "ok"
    assert _reason("계약서", ["계약서"]) == "ambiguous"   # 같은 제목 두 개


#------------------------------------------------------------------
# 대조 — 놓침·과잉을 따로 세고 통과 기준을 적용한다
#=> 정답표를 직접 만들어 compare 가 놓침(정답 X·자동 O)과 과잉(정답 O·자동 X)을
#   구분하는지, 놓침이 하나라도 있으면 불통과인지 본다.
#
# -in: tmp_path = pytest 임시 폴더
#
# -out: 없음
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_compare_counts_missed_and_over(tmp_path):
    nodes = [{"dc_id": "A", "title": "회의록", "path": "p > 회의록"},
             {"dc_id": "B", "title": "영업자료", "path": "p > 영업자료"},
             {"dc_id": "C", "title": "견적서", "path": "p > 견적서"}]
    # A 는 일치, B 는 과잉(사람은 O, 자동은 X), C 는 놓침(사람은 X, 자동은 O)
    r = ttc.compare(nodes, {"A": True, "B": True, "C": False})
    assert r["missed"] == ["C"] and r["over"] == ["B"] and not r["passed"]

    sheet = tmp_path / "s.csv"
    ttc.make_sheet(nodes, str(sheet))
    with open(sheet, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    # 틀에는 자동 판정이 들어 있지 않아야 한다 — 사람 판단이 끌려가지 않게
    assert rows[0] == ["dc_id", "경로", "제목", "정답(O/X)", "메모"]
    assert all(r[3] == "" for r in rows[1:])


#------------------------------------------------------------------
# 끝말이 흔한 말(규정 계열)이면 걸러진다
#=> "OO규정"·"OO지침" 은 문서종류이긴 하나 너무 흔해 한 분류로 보낼 수 없다.
#   더 긴 끝말("규정집")이 먼저 걸리면 흔한 말 규칙에 해당하지 않는다.
#
# -in: 없음
#
# -out: 없음
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_common_ending_filtered():
    assert _reason("사내규정") == "common_head"
    assert _reason("보안지침") == "common_head"
    assert _reason("취업규칙") == "common_head"
    assert _reason("인사규정집") == "ok"
