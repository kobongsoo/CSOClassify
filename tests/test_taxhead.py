#------------------------------------------------------------------
# 분류체계 제목 → 핵어 유도(taxhead) 시험
#=> 설계서 plan/업무분류-1차분류-원점재검토-20260916.html 7장·13장 D1.
#   네 가지를 본다.
#     1) 잡음 꼬리를 오른쪽부터 걷는가
#     2) '끝자리'에 온 말만 잡는가(가방 매칭과 다른 점)
#     3) 넓은 제목·묶음 제목·주제형 제목·흔한 끝말을 거르는가(ⓐ)
#     4) 말 단위 제외(ⓑ)와 노드 단위 끄기(ⓒ)가 설계대로 듣는가
#   Rust 판(Rust/src/taxhead.rs)에 같은 뜻의 시험이 있다 — 둘이 갈리면
#   같은 문서에 다른 라벨이 붙는데 오류도 경고도 안 난다.
#------------------------------------------------------------------

import os
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from csoclassify.classify import axes, taxhead  # noqa: E402


TAXONOMY_YAML = """\
taxonomy:
  source: t
  exported_at: '20260921000000'
  node_count: 7
  nodes:
    - {dc_id: LEGAL, parent: null, order: 1, title: 법무/규정, status: 1}
    - {dc_id: CONTRACT, parent: LEGAL, order: 1, title: 계약서, status: 1}
    - {dc_id: SALES, parent: null, order: 2, title: 영업/마케팅, status: 1}
    - {dc_id: PROPOSAL, parent: SALES, order: 1, title: 제안서, status: 1}
    - {dc_id: EVENT, parent: SALES, order: 2, title: 행사자료, status: 1}
    - {dc_id: HR, parent: null, order: 3, title: 인사, status: 1}
    - {dc_id: WELFARE, parent: HR, order: 1, title: 복리후생, status: 1}
"""


#------------------------------------------------------------------
# 시험용 분류체계
#=> 시험용 스냅샷 dict 를 엔진의 트리 생성기로 읽는다 — 고객 파일을 시험에 쓰지 않는다.
#
# -in: 없음
#
# -out: axes.Taxonomy
# -out: error = 없음
#------------------------------------------------------------------
@pytest.fixture
def tax():
    return axes.taxonomy_from_snapshot(yaml.safe_load(TAXONOMY_YAML), "(시험)")


#------------------------------------------------------------------
# 잡음 꼬리 걷기
#=> 버전·날짜·괄호 묶음·관리용 말이 섞여 있어도 핵어 자리가 드러나야 한다.
#   제목 전체가 잡음 말이면 걷지 않는다 — 빈 문자열을 돌려주면 안 된다.
#
# -in: 없음
#
# -out: 없음
# -out: error = 어긋나면 AssertionError
#------------------------------------------------------------------
def test_잡음_꼬리를_오른쪽부터_걷는다():
    assert taxhead.strip_noise("제안서_최종_v2") == "제안서"
    assert taxhead.strip_noise("회의록(수정본)") == "회의록"
    assert taxhead.strip_noise("계약서 20260101") == "계약서"
    assert taxhead.strip_noise("최종") == "최종"


#------------------------------------------------------------------
# 끝자리 매칭
#=> 핵어는 '자리'를 보는 것이라, 같은 말이 앞에 있으면 잡지 않는다.
#   영문은 단어 경계를 본다("preport" 가 "report" 로 걸리면 안 된다).
#
# -in: 없음
#
# -out: 없음
# -out: error = 어긋나면 AssertionError
#------------------------------------------------------------------
def test_끝자리에_온_말만_잡는다():
    assert taxhead.head_hit("사업계획서", ["계획서"]) == "계획서"
    assert taxhead.head_hit("계획서검토회의록", ["계획서"]) is None
    assert taxhead.head_hit("preport", ["report"]) is None
    assert taxhead.head_hit("2026_report", ["report"]) == "report"


#------------------------------------------------------------------
# ⓐ 자동 판정
#=> 넓은 말·묶음 제목·주제형 제목·흔한 끝말·모호한 제목을 각각 다른 이유로 거른다.
#
# -in: 없음
#
# -out: 없음
# -out: error = 어긋나면 AssertionError
#------------------------------------------------------------------
def test_핵어로_쓸_수_없는_제목을_거른다():
    end = taxhead.doctype_endings(None)
    assert taxhead.judge_title("자료", [], end) == (False, "broad_exact")
    assert taxhead.judge_title("행사자료", [], end) == (False, "container")
    assert taxhead.judge_title("복리후생", [], end) == (False, "not_doctype")
    assert taxhead.judge_title("사내규정", [], end) == (False, "common_head")
    # 더 좁은 'OO보고서' 분류가 함께 있으면 '보고서' 는 어디로 보낼지 정할 수 없다.
    assert taxhead.judge_title("보고서", ["점검보고서"], end) == (False, "ambiguous")
    assert taxhead.judge_title("점검보고서", ["보고서"], end) == (True, "ok")


#------------------------------------------------------------------
# 사전 만들기 + 파일명 끝자리 매칭
#=> 서랍(뿌리)·거른 제목은 빠지고, 통과한 제목은 유의어까지 함께 실린다.
#
# -in: tax = 시험용 분류체계
#
# -out: 없음
# -out: error = 어긋나면 AssertionError
#------------------------------------------------------------------
def test_핵어_사전은_통과한_제목만_담는다(tax):
    lex, warns = taxhead.build_head_lexicon(tax, None)
    assert warns == []
    # 계약서·제안서는 통과. 행사자료(묶음)·복리후생(주제형)·서랍은 빠진다.
    assert sorted(lex) == ["CONTRACT", "PROPOSAL"]
    hit = taxhead.match_filename_head(r"D:\x\보안관제_제안서_최종_v2.hwp", lex)
    assert hit[:2] == ("PROPOSAL", "제안서")
    # 끝자리가 아니면 안 잡는다 — 가방 매칭이라면 여기서도 제안서가 됐을 것이다.
    assert taxhead.match_filename_head(r"D:\x\제안서_검토_회의록.hwp", lex) is None


#------------------------------------------------------------------
# ⓑ 말 단위 제외
#=> 노드 id 가 아니라 '말'로 끈다. 그래서 제목이 바뀌면 저절로 다시 쓰인다.
#
# -in: tax = 시험용 분류체계
#
# -out: 없음
# -out: error = 어긋나면 AssertionError
#------------------------------------------------------------------
def test_말_단위_제외는_그_말만_끈다(tax):
    lex, _w = taxhead.build_head_lexicon(tax, None, excludes=["제안서"])
    assert sorted(lex) == ["CONTRACT"]


#------------------------------------------------------------------
# ⓒ 노드 단위 끄기
#=> 끌 당시 제목과 지금 제목이 같을 때만 듣는다. 제목이 달라졌으면 끄기를
#   무시하고(=핵어를 쓰고) "재검토" 경고를 남긴다 — 조용히 꺼져 있지 않게 한다.
#
# -in: tax = 시험용 분류체계
#
# -out: 없음
# -out: error = 어긋나면 AssertionError
#------------------------------------------------------------------
def test_노드_끄기는_제목이_그대로일_때만_듣는다(tax):
    off = [{"node": "PROPOSAL", "title_at_decision": "제안서"}]
    lex, warns = taxhead.build_head_lexicon(tax, None, node_off=off)
    assert sorted(lex) == ["CONTRACT"] and warns == []

    stale = [{"node": "PROPOSAL", "title_at_decision": "제안 자료"}]
    lex2, warns2 = taxhead.build_head_lexicon(tax, None, node_off=stale)
    assert sorted(lex2) == ["CONTRACT", "PROPOSAL"]
    assert len(warns2) == 1 and "PROPOSAL" in warns2[0]
