# -*- coding: utf-8 -*-
#------------------------------------------------------------------
# 평가셋 채점기(tests/evalset/score_evalsets.py) 단위 시험
#=> 채점 규칙 자체가 조용히 바뀌면 지난 측정치와 견줄 수 없게 된다. 평가셋 xlsx 는
#   고객사 실데이터라 저장소에 없으므로, 여기서는 xlsx 없이 돌릴 수 있는
#   '판정 가르기'와 '지표 계산'만 시험한다.
#------------------------------------------------------------------

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tests", "evalset"))

# openpyxl 이 없는 자리에서는 이 시험을 건너뛴다(채점기는 xlsx 를 읽어야 해서 필수다).
score = pytest.importorskip("score_evalsets")


#------------------------------------------------------------------
# 업무분류 행 하나 만들기 (시험용 도우미)
#=> 채점 함수가 보는 칸만 채운 dict 를 만든다. 시험마다 긴 dict 를 적지 않게 한다.
#
# -in: verdict = 판정 칸("정답있음"·"분류대상아님"·"체계에없음"·"판단불가")
# -in: ans1    = 정답1 dc_id
# -in: ans2    = 정답2 dc_id(없으면 None)
# -in: eng     = 엔진이 단 dc_id 리스트
# -in: stratum = 층 이름
#
# -out: dict = doc_grade/doc_metrics 에 넣을 행
# -out: error = 없음
#------------------------------------------------------------------
def drow(verdict="정답있음", ans1=None, ans2=None, eng=(), stratum="D1"):
    return {"verdict": verdict, "ans1": ans1, "ans2": ans2, "eng": list(eng),
            "stratum": stratum, "prev": []}


#------------------------------------------------------------------
# 보안등급 행 하나 만들기 (시험용 도우미)
#
# -in: answer  = 정답 등급("C"·"S"·"O"·"판단불가")
# -in: eng     = 엔진 등급(미분류는 빈 문자열)
# -in: stratum = 층 이름
#
# -out: dict = sec_grade/sec_metrics 에 넣을 행
# -out: error = 없음
#------------------------------------------------------------------
def srow(answer, eng, stratum="G1"):
    return {"answer": answer, "eng": eng, "stratum": stratum, "prev": ""}


#------------------------------------------------------------------
# 업무분류 다섯 갈래가 제대로 갈리는가
#=> 적중·오라벨·놓침·헛라벨·정상무라벨의 정의가 바뀌면 지난 수치와 못 견준다.
#------------------------------------------------------------------
@pytest.mark.parametrize("row,want", [
    (drow(ans1="DC_1", eng=["DC_1"]), "적중"),
    (drow(ans1="DC_1", eng=["DC_2"]), "오라벨"),
    (drow(ans1="DC_1", eng=[]), "놓침"),
    # 정답2 만 맞혀도 적중이다 — 사람이 둘 다 맞다고 판단한 문서라 그렇다.
    (drow(ans1="DC_1", ans2="DC_2", eng=["DC_2"]), "적중"),
    # 정답1 을 맞혔으면 곁다리 라벨이 더 붙어도 적중이다.
    (drow(ans1="DC_1", eng=["DC_1", "DC_9"]), "적중"),
    (drow(verdict="체계에없음", eng=["DC_1"]), "헛라벨"),
    (drow(verdict="체계에없음", eng=[]), "정상무라벨"),
    (drow(verdict="분류대상아님", eng=[]), "정상무라벨"),
    (drow(verdict="판단불가"), "판단불가"),
])
def test_업무분류_판정_가르기(row, want):
    assert score.doc_grade(row) == want


#------------------------------------------------------------------
# 업무분류 정밀도·재현율 셈
#=> 오라벨이 정밀도·재현율 양쪽 분모에 다 들어가는지 확인한다. 이 한 가지가
#   틀리면 모든 수치가 조용히 어긋난다.
#------------------------------------------------------------------
def test_업무분류_지표():
    rows = [drow(ans1="DC_1", eng=["DC_1"]),          # 적중
            drow(ans1="DC_1", eng=["DC_1"]),          # 적중
            drow(ans1="DC_1", eng=["DC_2"]),          # 오라벨
            drow(ans1="DC_1", eng=[]),                # 놓침
            drow(verdict="체계에없음", eng=["DC_3"])]  # 헛라벨
    m = score.doc_metrics(rows, "eng", False, {"D1": 5}, {"D1": 5})
    assert (m["적중"], m["오라벨"], m["헛라벨"], m["놓침"]) == (2, 1, 1, 1)
    assert m["precision"] == pytest.approx(2 / 4)   # 적중 / (적중+오라벨+헛라벨)
    assert m["recall"] == pytest.approx(2 / 4)      # 적중 / (적중+오라벨+놓침)


#------------------------------------------------------------------
# 층별 가중(모집단 추정)이 도는가
#=> 표본을 적게 뽑은 층일수록 한 건이 더 무겁게 세어져야 한다.
#------------------------------------------------------------------
def test_모집단_가중():
    rows = [drow(ans1="DC_1", eng=["DC_1"], stratum="D1"),   # 층 D1: 표본 1 → 모집단 10
            drow(ans1="DC_1", eng=[], stratum="D2")]         # 층 D2: 표본 1 → 모집단 100
    pop, n_by = {"D1": 10, "D2": 100}, {"D1": 1, "D2": 1}
    m = score.doc_metrics(rows, "eng", True, pop, n_by)
    assert (m["적중"], m["놓침"]) == (10, 100)
    # 표본으로는 반타작이지만, 모집단에서는 놓침이 압도적이다.
    assert m["recall"] == pytest.approx(10 / 110, abs=1e-4)


#------------------------------------------------------------------
# 보안등급 네 갈래 + 미분류 취급
#=> 미분류(빈 문자열)는 C 가 아니므로 정답 C 면 'C놓침' 이어야 한다.
#------------------------------------------------------------------
@pytest.mark.parametrize("row,want", [
    (srow("C", "C"), "C적중"),
    (srow("C", "S"), "C놓침"),
    (srow("C", ""), "C놓침"),
    (srow("O", "C"), "C오탐"),
    (srow("S", "S"), "비C정상"),
    (srow("판단불가", "C"), "판단불가"),
])
def test_보안등급_판정_가르기(row, want):
    assert score.sec_grade(row) == want


#------------------------------------------------------------------
# 보안등급 지표 — C 재현율·정밀도와 과대/과소 등급
#=> 과대등급은 C 정밀도에 안 잡히는 오류라 따로 세어야 한다(O 를 S 로 올린 경우).
#------------------------------------------------------------------
def test_보안등급_지표():
    rows = [srow("C", "C"),    # C적중 · 일치
            srow("C", ""),     # C놓침 · 과소(미분류는 가장 낮게 본다)
            srow("O", "C"),    # C오탐 · 과대
            srow("O", "S"),    # 비C정상 · 과대(C 정밀도에는 안 잡힌다)
            srow("S", "S")]    # 비C정상 · 일치
    m = score.sec_metrics(rows, "eng", False, {"G1": 5}, {"G1": 5})
    assert (m["C적중"], m["C놓침"], m["C오탐"]) == (1, 1, 1)
    assert m["C_recall"] == pytest.approx(0.5)
    assert m["C_precision"] == pytest.approx(0.5)
    assert m["3등급일치"] == pytest.approx(2 / 5)
    assert m["과대등급"] == pytest.approx(2 / 5)
    assert m["과소등급"] == pytest.approx(1 / 5)


#------------------------------------------------------------------
# 정답 출처 구분
#=> 'AI 가 단 정답'을 사람 것과 섞으면 성적이 낙관적으로 나온다. 표식 이름이
#   축마다 달라(AI라벨·AI추가C·AI검토) 한꺼번에 잡히는지 본다.
#------------------------------------------------------------------
@pytest.mark.parametrize("note,want", [
    ("", "사람"),
    (None, "사람"),
    ("[정리]", "사람"),
    ("[AI라벨]", "AI"),
    ("[AI추가C]", "AI"),
    ("[AI라벨·DC_007]", "AI"),
    ("[AI라벨·검토권장]", "검토권장"),
    ("[라벨정리·검토권장]", "검토권장"),
])
def test_정답_출처_구분(note, want):
    assert score.tag_of(note) == want


#------------------------------------------------------------------
# 엔진 결과 jsonl 읽기 — 머리글·깨진 줄 처리
#=> 첫 줄의 {"run": ...} 은 문서가 아니고, 깨진 줄 하나 때문에 채점이 멈추면 안 된다.
#------------------------------------------------------------------
def test_엔진결과_읽기(tmp_path):
    p = tmp_path / "run.jsonl"
    p.write_text(
        '{"run":{"rule_version":"cso-테스트"}}\n'
        '{"file":"D:/a/b.txt","grade":"C","doctype":["DC_1"]}\n'
        '{깨진 줄}\n'
        '{"summary":{"total":1}}\n',
        encoding="utf-8")
    recs, meta = score.load_run(str(p))
    assert meta["rule_version"] == "cso-테스트"
    assert len(recs) == 1                       # 머리글·요약·깨진 줄은 안 센다
    assert recs[score.norm_path("D:/a/b.txt")]["grade"] == "C"


#------------------------------------------------------------------
# 없는 파일을 주면 사람이 읽는 문장으로 끝나는가
#=> 스택 트레이스만 남기면 자동화에서 무엇이 잘못됐는지 알 수 없다.
#------------------------------------------------------------------
def test_없는_결과파일():
    with pytest.raises(SystemExit) as e:
        score.load_run("없는파일.jsonl")
    assert "없는파일.jsonl" in str(e.value)
