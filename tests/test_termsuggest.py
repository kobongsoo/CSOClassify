# -*- coding: utf-8 -*-
#------------------------------------------------------------------
# 단어 제안(termsuggest) 시험 — 설계서 13-1 의 정답표 T1~T10
#=> 이 기능의 유일한 정답표는 "사람이 이미 내린 결정을 재현하는가"이다.
#   2026-09-14 에 사람이 손으로 넣은 말이 후보로 나오고, 오탐 때문에 뺀 말이
#   안 나와야 한다. 그러지 못하는 제안기는 쓸 값이 없다.
#
#   [합성 문서를 쓰는 이유] T1~T5 가 가리키는 실제 문서는 D:\분류함 에 있고
#   저장소에는 없다(고객사 실데이터). 그래서 그때의 '상황'을 가짜 문서로 다시
#   만든다 — 예컨대 T1 은 "'품목보고서'가 보고서 확정문서 7건 중 6건에 있고
#   다른 분류에는 없다", T4 는 "'인증'이 인증서류에도 나오지만 다른 분류
#   문서에도 흔하다" 는 상황이다. 실제 코퍼스로 확인하는 것은 2단계(CLI)의 몫이다.
#------------------------------------------------------------------

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from csoclassify.classify import termsuggest as ts


#------------------------------------------------------------------
# 시험용 문서 한 건 만들기
#=> make_doc 을 감싸, 시험마다 경로·본문을 짧게 적을 수 있게 한다.
#
# -in: folder = 폴더 이름(폴더 보정을 시험하려고 나눈다)
# -in: name   = 파일 이름
# -in: labels = 이 문서로 확정된 dc_id 들
# -in: text   = 본문(첫 줄이 제목이 된다). None 이면 '본문 없음' 문서
#
# -out: dict = termsuggest.make_doc 결과
# -out: error = 없음
#------------------------------------------------------------------
def doc(folder, name, labels=(), text=None, rejected=()):
    return ts.make_doc(os.path.join("D:\\분류함", folder, name),
                       labels=labels, rejected=rejected, text=text)


#------------------------------------------------------------------
# 후보 목록에서 말만 뽑기 (시험 도우미)
#
# -in: result = suggest() 결과
# -in: key    = "candidates" 또는 "cluster_only"
#
# -out: list = 말 목록(점수 차례 그대로)
# -out: error = 없음
#------------------------------------------------------------------
def terms(result, key="candidates"):
    return [c["term"] for c in result[key]]


#------------------------------------------------------------------
# 대조군 문서 만들기 (시험 도우미)
#=> 점수는 '다른 분류에 얼마나 나오나'로 갈린다. 그래서 어느 시험이든 대조군이
#   있어야 한다. 서로 다른 폴더에 두어 폴더 보정에 걸리지 않게 한다.
#
# -in: n     = 만들 문서 수
# -in: text  = 문서마다 넣을 본문(같은 글)
# -in: label = 이 문서들에 달 dc_id
#
# -out: list = 문서 목록
# -out: error = 없음
#------------------------------------------------------------------
def others(n, text, label="DC_OTHER"):
    return [doc(f"기타{i}", f"문서{i}.docx", labels=[label], text=text)
            for i in range(n)]


# ──────────────────────────────────────────────────────────────────
# T1 · 보고서 — 사람이 V2b 로 넣은 말이 후보로 나오는가
# ──────────────────────────────────────────────────────────────────
def test_T1_보고서_V2b_단어가_후보에_나온다():
    # 2026-09-14 에 사람이 title 칸에 손으로 넣은 말들. 폴더를 갈라 두어
    # 폴더 보정(한 폴더 = 1건)에 걸리지 않게 한다.
    pos = [
        doc("11_정부_공공사업", "3_4_지능형_사이버_위협_품목보고서.pdf", ["DC_001_003"],
            "품목보고서\n시장 현황분석 결과를 담는다."),
        doc("11_정부_공공사업", "4-3_디지털_휴먼_품목보고서.pdf", ["DC_001_003"],
            "품목보고서\n현황분석 내용."),
        doc("12_시장조사", "2026 실태조사 이슈페이퍼.hwp", ["DC_001_003"],
            "실태조사 이슈페이퍼\n현황분석 중심으로 정리."),
        doc("12_시장조사", "국내 실태조사 보고.docx", ["DC_001_003"],
            "실태조사 이슈페이퍼\n현황분석 요약."),
        doc("13_기획", "품목보고서 요약.pptx", ["DC_001_003"],
            "품목보고서\n현황분석 표."),
        doc("13_기획", "이슈페이퍼 초안.docx", ["DC_001_003"],
            "이슈페이퍼\n현황분석 초안."),
        # 실태조사도 한 폴더에만 있으면 '한 폴더 전용'으로 빠진다 — 실제로도
        # 이런 말은 여러 폴더에 흩어져 있다(시장조사·대외 양쪽).
        doc("14_대외", "해외 실태조사 요약.docx", ["DC_001_003"],
            "실태조사 요약\n현황분석 중심."),
        doc("14_대외", "참고 자료.docx", ["DC_001_003"], "참고 자료\n일반 설명."),
    ]
    docs = pos + others(8, "업무 처리 절차와 일반 설명을 담은 문서.")
    got = terms(ts.suggest("DC_001_003", docs))
    assert "품목보고서" in got
    assert "이슈페이퍼" in got
    assert "실태조사" in got
    assert "현황분석" in got


# ──────────────────────────────────────────────────────────────────
# T2 · 교육자료 — '입문교육'·'예방교육'
# ──────────────────────────────────────────────────────────────────
def test_T2_교육자료_단어가_후보에_나온다():
    pos = [
        doc("07_교육", "신입 입문교육 과정.pptx", ["DC_004_002"], "입문교육 과정 안내"),
        doc("07_교육", "개인정보 예방교육.pptx", ["DC_004_002"], "예방교육 자료"),
        doc("08_사내", "정보보호 예방교육 자료.pdf", ["DC_004_002"], "예방교육 교재"),
        doc("08_사내", "입문교육 교재.docx", ["DC_004_002"], "입문교육 교재"),
        doc("09_연수", "입문교육 일정.xlsx", ["DC_004_002"], "입문교육 일정표"),
        doc("09_연수", "예방교육 결과.docx", ["DC_004_002"], "예방교육 결과 정리"),
    ]
    docs = pos + others(8, "회사 업무 절차 설명 문서.")
    got = terms(ts.suggest("DC_004_002", docs))
    assert "입문교육" in got
    assert "예방교육" in got


# ──────────────────────────────────────────────────────────────────
# T3 · 매뉴얼 — 한 폴더에 몰린 '도움말'
#      설계 4-2(폴더 보정)와 부딪히는 자리. 기본 후보에서는 빠지고
#      '한 폴더 전용' 묶음에 담겨야 한다(기본 체크 해제).
# ──────────────────────────────────────────────────────────────────
def test_T3_한폴더_전용_어휘는_따로_담긴다():
    # decm 관리자 도움말 html 조각이 한 폴더에 몰려 있던 상황.
    decm = [doc("06_사이버다임\\decm", f"page{i}.html", ["DC_002_002"],
                "도움말\n화면 사용 도움말 설명.") for i in range(8)]
    other_folders = [
        doc("01_제품매뉴얼", "사용자 매뉴얼.doc", ["DC_002_002"], "사용자 매뉴얼"),
        doc("02_기술", "설치 매뉴얼.pdf", ["DC_002_002"], "설치 매뉴얼"),
    ]
    docs = decm + other_folders + others(8, "일반 업무 문서 본문.")
    got = ts.suggest("DC_002_002", docs)

    # 폴더 보정 때문에 기본 후보에는 못 든다 — 여덟 건이 합쳐 한 건이라 출현율이 낮다.
    assert "도움말" not in terms(got)
    # 그러나 버리지 않는다. '한 폴더 전용' 묶음에서 사람이 볼 수 있어야 한다.
    assert "도움말" in terms(got, "cluster_only")
    only = [c for c in got["cluster_only"] if c["term"] == "도움말"][0]
    assert only["checked"] is False          # 자동 채택은 하지 않는다
    assert "한 폴더 전용" in only["flags"]
    assert only["clusters"] == 1


# ──────────────────────────────────────────────────────────────────
# T4 · 인증서류 — '인증'은 안 나온다
#      2026-09-14 에 오탐 때문에 규칙·core 사전에서 뺀 말이다.
# ──────────────────────────────────────────────────────────────────
def test_T4_흔한말_인증은_후보에_없다():
    pos = [
        doc("05_인증", "ISO 인증서 사본.pdf", ["DC_006_003"], "인증 관련 서류"),
        doc("05_인증", "인증 심사 결과.docx", ["DC_006_003"], "인증 심사 자료"),
        doc("06_품질", "인증 신청 서류.hwp", ["DC_006_003"], "인증 신청 자료"),
        doc("06_품질", "인증 갱신 안내.docx", ["DC_006_003"], "인증 갱신 안내"),
        doc("07_대외", "인증 현황.xlsx", ["DC_006_003"], "인증 현황 정리"),
    ]
    # '인증'은 다른 분류 문서에도 흔하다 — 이것이 df_neg 문턱에 걸리는 상황이다.
    docs = pos + [doc(f"기타{i}", f"문서{i}.docx", labels=["DC_OTHER"],
                      text="제품 인증 절차와 인증 범위를 설명한다.") for i in range(8)]
    got = terms(ts.suggest("DC_006_003", docs))
    assert "인증" not in got


# ──────────────────────────────────────────────────────────────────
# T5 · 고객자료·계약서·복리후생 — '고객'·'계약'·'휴가' 가 안 나온다
#      T4 와 같은 이유로 뺀 말들이다. 분류를 바꿔도 같은 판단이 나와야 한다.
# ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("node,word,pos_text,neg_text", [
    ("DC_003_003", "고객", "고객 요청 정리", "고객 문의와 고객 응대 절차를 설명한다."),
    ("DC_006_001", "계약", "계약 조건 정리", "계약 기간과 계약 해지 조건을 설명한다."),
    ("DC_004_003", "휴가", "휴가 사용 안내", "연차 휴가 신청과 휴가 일수 규정."),
])
def test_T5_흔한말은_분류를_바꿔도_안나온다(node, word, pos_text, neg_text):
    pos = [doc(f"확정{i}", f"문서{i}.docx", labels=[node], text=pos_text)
           for i in range(5)]
    docs = pos + [doc(f"기타{i}", f"타{i}.docx", labels=["DC_OTHER"], text=neg_text)
                  for i in range(8)]
    assert word not in terms(ts.suggest(node, docs))


# ──────────────────────────────────────────────────────────────────
# T6 · 같은 폴더 35건은 근거 1건으로 세어진다
# ──────────────────────────────────────────────────────────────────
def test_T6_같은폴더는_한건으로_센다():
    same = [doc("한폴더", f"문서{i}.html", ["DC_X"], "동일한 본문 내용.")
            for i in range(35)]
    weights, clusters = ts.cluster_weights(same)
    assert clusters == 1
    # 35건이 합쳐서 1건이다.
    assert sum(weights.values()) == pytest.approx(1.0)


# ──────────────────────────────────────────────────────────────────
# T7 · 본문에만 나오는 말은 terms + min_count 2, 기본 체크 해제
# ──────────────────────────────────────────────────────────────────
def test_T7_본문전용_말은_체크해제로_제안된다():
    # 제목·파일 이름에는 없고 본문 뒤쪽에만 나오는 말을 만든다.
    filler = "가" * 500      # 앞부분(400자)을 넘겨 본문에만 남게 한다
    pos = [doc(f"확정{i}", f"문서{i}.docx", labels=["DC_Y"],
               text=f"일반 제목\n{filler}\n분기실적 정리") for i in range(6)]
    docs = pos + others(8, "다른 분류의 평범한 본문.")
    got = ts.suggest("DC_Y", docs)
    cand = [c for c in got["candidates"] if c["term"] == "분기실적"]
    assert cand, f"본문 전용 말이 후보에 없다: {terms(got)}"
    cand = cand[0]
    assert cand["where"] == "body"
    assert cand["fields"] == ["terms"]
    assert cand["extra"] == {"min_count": 2}
    assert cand["checked"] is False


# ──────────────────────────────────────────────────────────────────
# T8 · 기각 이력이 있는 말은 후보에서 빠진다
# ──────────────────────────────────────────────────────────────────
def test_T8_금지목록의_말은_빠진다():
    pos = [doc(f"확정{i}", f"품목보고서{i}.pdf", labels=["DC_Z"], text="품목보고서 내용")
           for i in range(6)]
    docs = pos + others(8, "다른 분류 본문.")
    assert "품목보고서" in terms(ts.suggest("DC_Z", docs))

    # 전체 금지 목록에 넣으면 빠진다.
    stop = {"global": {"품목보고서"}, "by_node": {}}
    assert "품목보고서" not in terms(ts.suggest("DC_Z", docs, stopwords=stop))

    # 그 분류에만 금지해도 빠진다.
    stop = {"global": set(), "by_node": {"DC_Z": {"품목보고서"}}}
    assert "품목보고서" not in terms(ts.suggest("DC_Z", docs, stopwords=stop))


# ──────────────────────────────────────────────────────────────────
# T9 · 확정 문서 2건이면 제안하지 않고, 왜 비었는지 말한다
# ──────────────────────────────────────────────────────────────────
def test_T9_재료가_모자라면_까닭을_말한다():
    pos = [doc("확정0", "품목보고서.pdf", ["DC_W"], "품목보고서 내용"),
           doc("확정1", "품목보고서 2.pdf", ["DC_W"], "품목보고서 내용")]
    got = ts.suggest("DC_W", pos + others(8, "다른 본문."))
    assert got["candidates"] == []
    assert got["docs"] == 2
    assert "3건부터" in got["reason"]


# ──────────────────────────────────────────────────────────────────
# T10 · 저장된 본문이 없으면 제목·파일 이름만으로 돌고, 몇 건인지 알린다
# ──────────────────────────────────────────────────────────────────
def test_T10_본문이_없으면_파일이름만으로_돈다():
    # text=None → has_text False. 파일 이름에만 단서가 있다.
    pos = [doc(f"확정{i}", f"품목보고서 {i}분기.pdf", labels=["DC_V"]) for i in range(6)]
    docs = pos + others(8, "다른 분류 본문.")
    got = ts.suggest("DC_V", docs)
    assert got["no_text"] == 6
    cand = [c for c in got["candidates"] if c["term"] == "품목보고서"]
    assert cand, f"파일 이름에서 못 뽑았다: {terms(got)}"
    assert cand[0]["where"] == "name"
    assert cand[0]["fields"] == ["title_terms", "filename"]


# ──────────────────────────────────────────────────────────────────
# 곁가지 — 거르기·자리 정하기의 잔 규칙들
# ──────────────────────────────────────────────────────────────────

#------------------------------------------------------------------
# 이미 규칙에 있는 말은 다시 제안하지 않는다(대소문자만 달라도 같은 말)
#=> DC_007 규칙에 Mpower/mpower 가 함께 들어가 본문 건수가 두 배로 세어지던
#   실제 사고가 이 규칙의 근거다.
#------------------------------------------------------------------
def test_이미_있는_말은_제안하지_않는다():
    pos = [doc(f"확정{i}", f"문서{i}.docx", labels=["DC_A"], text="Mpower 제품 설명")
           for i in range(6)]
    docs = pos + others(8, "다른 분류 본문.")
    assert "Mpower" in terms(ts.suggest("DC_A", docs))

    rule = {"doctype_rules": [{"node": "DC_A", "title_terms": ["mpower"]}]}
    assert "Mpower" not in terms(ts.suggest("DC_A", docs, rule_doc=rule))


#------------------------------------------------------------------
# 다른 분류 규칙에 있는 말은 '충돌' 표식이 붙는다(막지는 않는다)
#------------------------------------------------------------------
def test_다른_규칙과_겹치면_충돌_표식():
    pos = [doc(f"확정{i}", f"품목보고서{i}.pdf", labels=["DC_B"], text="품목보고서 내용")
           for i in range(6)]
    docs = pos + others(8, "다른 분류 본문.")
    rule = {"doctype_rules": [{"node": "DC_OTHER", "title_terms": ["품목보고서"]}]}
    got = ts.suggest("DC_B", docs, rule_doc=rule)
    cand = [c for c in got["candidates"] if c["term"] == "품목보고서"][0]
    assert any("충돌" in f for f in cand["flags"])
    assert cand["checked"] is False      # 충돌이 있으면 자동 채택하지 않는다


#------------------------------------------------------------------
# 다른 분류의 이름은 후보에서 뺀다
#=> 계약서 문서에서 '회의록'이 뽑히면 두 규칙이 서로를 먹는다.
#------------------------------------------------------------------
def test_다른_분류_이름은_빠진다():
    pos = [doc(f"확정{i}", f"문서{i}.docx", labels=["DC_C"], text="회의록 형식으로 정리")
           for i in range(6)]
    docs = pos + others(8, "다른 분류 본문.")
    assert "회의록" in terms(ts.suggest("DC_C", docs))

    tax = {"by_id": {"DC_C": {"title": "고객자료"}, "DC_001_002": {"title": "회의록"}}}
    assert "회의록" not in terms(ts.suggest("DC_C", docs, tax=tax))


#------------------------------------------------------------------
# 사람이 '이 분류 아님'이라고 거절한 문서는 대조군에서 무겁게 센다
#=> 그냥 다른 분류인 문서보다 강한 반례다.
#------------------------------------------------------------------
def test_거절한_문서는_대조군을_강하게_만든다():
    pos = [doc(f"확정{i}", f"문서{i}.docx", labels=["DC_D"], text="특수용어 내용")
           for i in range(6)]
    plain = others(8, "관계없는 본문.")
    without = ts.suggest("DC_D", pos + plain)
    # 같은 말이 들어 있는 문서를 '이 분류 아님'으로 거절하면 점수가 내려간다.
    rej = [doc(f"거절{i}", f"거절{i}.docx", labels=["DC_OTHER"], rejected=["DC_D"],
               text="특수용어 내용") for i in range(3)]
    with_rej = ts.suggest("DC_D", pos + plain + rej)

    s1 = [c for c in without["candidates"] if c["term"] == "특수용어"][0]["score"]
    got = [c for c in with_rej["candidates"] if c["term"] == "특수용어"]
    assert not got or got[0]["score"] < s1


#------------------------------------------------------------------
# 숫자·날짜·판번호·확장자는 후보가 되지 못한다
#------------------------------------------------------------------
@pytest.mark.parametrize("term,ok", [
    ("품목보고서", True), ("Mpower", True), ("SCPI", True),
    ("2026", False), ("2026년", False), ("3분기", False), ("v1.2", False),
    ("pdf", False), ("서", False), ("AI", False), ("", False),
])
def test_쓸수_있는_말_거르기(term, ok):
    assert ts.is_usable(term) is ok


#------------------------------------------------------------------
# 조사 떼기 — 끝말이 드러나면 거기서 멈춘다
#------------------------------------------------------------------
@pytest.mark.parametrize("word,want", [
    ("품목보고서를", "품목보고서"),
    ("매뉴얼은", "매뉴얼"),
    ("계약서", "계약서"),       # 이미 끝말로 끝나면 건드리지 않는다
    ("규정에서", "규정"),
    ("가", "가"),               # 너무 짧으면 떼지 않는다
])
def test_조사_떼기(word, want):
    assert ts.strip_particle(word) == want


#------------------------------------------------------------------
# 고유명사 의심 — 막지 않고 표식만 단다
#------------------------------------------------------------------
def test_고유명사_의심_표식():
    # 폴더를 갈라 둔다 — 한 폴더에만 있으면 '한 폴더 전용' 묶음으로 가서
    # 이 시험이 보려는 '고유명사 표식'을 확인할 수 없다.
    pos = [doc(f"0{6 + i % 2}_사이버다임", f"문서{i}.docx", labels=["DC_E"],
               text="사이버다임 제품 설명") for i in range(6)]
    docs = pos + others(8, "관계없는 본문.")
    got = ts.suggest("DC_E", docs)
    cand = [c for c in got["candidates"] if c["term"] == "사이버다임"]
    assert cand, "고유명사도 후보에서 막지는 않는다"
    assert "고유명사 의심" in cand[0]["flags"]
    assert cand[0]["checked"] is False


# ──────────────────────────────────────────────────────────────────
# 재료 모으기 — 파일에서 읽는 부분
# ──────────────────────────────────────────────────────────────────

#------------------------------------------------------------------
# seed 는 사람이 승인한 줄만 재료로 쓴다
#=> 자동 선별분(t_seed 통과분)까지 쓰면 규칙→seed→규칙 자기강화가 생긴다.
#------------------------------------------------------------------
def test_seed는_사람이_승인한_줄만(tmp_path):
    p = tmp_path / "class_seed.jsonl"
    p.write_text(
        '{"file":"D:/a/사람.docx","hash":"h1","doctype":["DC_1"],"approved_by":"kim"}\n'
        '{"file":"D:/a/자동.docx","hash":"h2","doctype":["DC_1"]}\n'
        '{"file":"D:/a/라벨없음.docx","hash":"h3","doctype":[],"approved_by":"kim"}\n'
        '{깨진 줄}\n', encoding="utf-8")
    got = ts.load_seed_labels(str(p))
    assert len(got) == 1
    assert got[ts.norm_key("D:/a/사람.docx")]["labels"] == {"DC_1"}
    # 승인 여부를 안 가리면 자동 선별분도 들어온다.
    assert len(ts.load_seed_labels(str(p), approved_only=False)) == 2


#------------------------------------------------------------------
# 검토 이력은 마지막 결정만 쓰고, doctype 축만 본다
#------------------------------------------------------------------
def test_검토이력은_마지막_결정만(tmp_path):
    p = tmp_path / "cso_override.jsonl"
    p.write_text(
        '{"file":"D:/a/x.docx","axis":"doctype","confirmed":["DC_1"],"rejected":[]}\n'
        '{"file":"D:/a/x.docx","axis":"doctype","confirmed":["DC_2"],"rejected":["DC_1"]}\n'
        '{"file":"D:/a/y.docx","axis":"security","confirmed":["C"],"rejected":[]}\n',
        encoding="utf-8")
    got = ts.load_override_labels(str(p))
    assert len(got) == 1                      # 보안등급 줄은 안 본다
    row = got[ts.norm_key("D:/a/x.docx")]
    assert row["confirmed"] == {"DC_2"}
    assert row["rejected"] == {"DC_1"}


#------------------------------------------------------------------
# seed 와 검토 이력을 합치고 저장된 본문을 붙인다
#=> 확정했다가 나중에 거절한 축은 확정에서 빠져야 한다.
#------------------------------------------------------------------
def test_재료_모으기(tmp_path):
    seed = tmp_path / "class_seed.jsonl"
    seed.write_text(
        '{"file":"D:/a/x.docx","hash":"abc","doctype":["DC_1"],"approved_by":"kim"}\n',
        encoding="utf-8")
    ov = tmp_path / "cso_override.jsonl"
    ov.write_text(
        '{"file":"D:/a/x.docx","axis":"doctype","confirmed":["DC_2"],"rejected":["DC_1"]}\n',
        encoding="utf-8")
    textdir = tmp_path / "text"
    textdir.mkdir()
    (textdir / "abc.txt").write_text("품목보고서\n본문 내용", encoding="utf-8")

    docs = ts.load_documents(str(seed), str(ov), str(textdir))
    assert len(docs) == 1
    d = docs[0]
    assert d["labels"] == {"DC_2"}            # DC_1 은 거절되어 빠졌다
    assert d["rejected"] == {"DC_1"}
    assert d["has_text"] is True
    assert d["title"] == "품목보고서"


#------------------------------------------------------------------
# 저장된 본문이 없어도 조용히 넘어간다
#=> 원본을 다시 열지 않는 설계라, 없으면 없는 대로 알리고 계속한다.
#------------------------------------------------------------------
def test_본문이_없어도_계속한다(tmp_path):
    seed = tmp_path / "class_seed.jsonl"
    seed.write_text(
        '{"file":"D:/a/x.docx","hash":"없는해시","doctype":["DC_1"],"approved_by":"kim"}\n',
        encoding="utf-8")
    docs = ts.load_documents(str(seed), None, str(tmp_path / "없는폴더"))
    assert len(docs) == 1
    assert docs[0]["has_text"] is False
    assert docs[0]["body"] == ""


#------------------------------------------------------------------
# 금지 목록 파일이 없거나 깨져 있어도 제안은 돈다
#------------------------------------------------------------------
def test_금지목록_없어도_돈다(tmp_path):
    assert ts.load_stopwords(None) == {"global": set(), "by_node": {}}
    assert ts.load_stopwords(str(tmp_path / "없음.yaml")) == {"global": set(), "by_node": {}}
    p = tmp_path / "doc_rule_stopwords.yaml"
    p.write_text("global:\n  - 인증\n  - 고객\nby_node:\n  DC_1:\n    - 보고\n",
                 encoding="utf-8")
    got = ts.load_stopwords(str(p))
    assert got["global"] == {"인증", "고객"}
    assert got["by_node"]["DC_1"] == {"보고"}


#------------------------------------------------------------------
# '한 폴더 전용' 묶음에는 두 글자 흔한 말을 넣지 않는다
#=> 폴더가 하나뿐이라 "다른 분류에 안 나온다"는 근거가 그 폴더의 우연일 수 있다.
#   '상황'·'업무' 같은 말은 코퍼스가 커지면 어디에나 나온다.
#------------------------------------------------------------------
def test_한폴더_묶음은_두글자_말을_거른다():
    pos = [doc("한폴더", f"문서{i}.html", labels=["DC_F"],
               text="도움말\n업무 진행 상황 도움말 설명.") for i in range(8)]
    pos += [doc("다른폴더", "사용자 매뉴얼.doc", labels=["DC_F"], text="사용자 매뉴얼")]
    got = terms(ts.suggest("DC_F", pos + others(8, "관계없는 본문.")), "cluster_only")
    assert "도움말" in got            # 세 글자는 남는다
    for word in ("업무", "진행", "상황"):
        assert word not in got


# ──────────────────────────────────────────────────────────────────
# 검토 화면용 — 문서 한 건을 놓고 후보 고르기
# ──────────────────────────────────────────────────────────────────

#------------------------------------------------------------------
# 이 문서에 있는 말로만 좁힌다
#=> 분류 전체의 후보를 다 보여 주면 지금 보고 있는 문서와 상관없는 말이 섞인다.
#------------------------------------------------------------------
def test_문서_한건으로_좁힌다():
    pos = [doc(f"확정{i}", f"품목보고서 {i}.pdf", labels=["DC_R"], text="품목보고서 내용")
           for i in range(5)]
    pos += [doc(f"별도{i}", f"실태조사 {i}.hwp", labels=["DC_R"], text="실태조사 내용")
            for i in range(5)]
    docs = pos + others(10, "관계없는 본문.")
    node = "DC_R"
    전체 = terms(ts.suggest(node, docs))
    assert "품목보고서" in 전체 and "실태조사" in 전체

    got = ts.suggest_for_doc(node, docs, pos[0]["file"])
    names = [c["term"] for c in got["focus_only"]]
    assert "품목보고서" in names
    assert "실태조사" not in names       # 이 문서에 없는 말은 빠진다


#------------------------------------------------------------------
# 문턱을 못 넘은 말도 까닭을 달아 함께 올린다
#=> 관리자가 그 문서를 눈앞에 두고 있어 판단이 가장 정확한 순간이다.
#   버리면 사람이 볼 기회가 아예 없어진다.
#------------------------------------------------------------------
def test_문턱을_못넘은_말도_까닭과_함께_올린다():
    pos = [doc(f"확정{i}", f"인증 서류 {i}.pdf", labels=["DC_C2"], text="인증 관련 서류")
           for i in range(5)]
    # '인증'은 다른 분류에도 흔하다 — 분류 단위 제안에서는 잘린다.
    docs = pos + [doc(f"기타{i}", f"타{i}.docx", labels=["DC_OTHER"],
                      text="제품 인증 절차 설명") for i in range(10)]
    assert "인증" not in terms(ts.suggest("DC_C2", docs))

    got = ts.suggest_for_doc("DC_C2", docs, pos[0]["file"])
    cand = [c for c in got["focus_only"] if c["term"] == "인증"]
    assert cand, "문서 화면에서는 까닭을 달아 보여 준다"
    assert cand[0]["checked"] is False
    assert "흔한 말" in cand[0]["flags"]


#------------------------------------------------------------------
# 아주 지우는 말은 문서 화면에도 안 올린다
#=> 이미 규칙에 있는 말·금지 목록·다른 분류 이름은 볼 이유가 없다.
#------------------------------------------------------------------
def test_아주_지우는_말은_문서화면에도_없다():
    pos = [doc(f"확정{i}", f"품목보고서 {i}.pdf", labels=["DC_R2"], text="품목보고서 내용")
           for i in range(5)]
    docs = pos + others(10, "관계없는 본문.")
    rule = {"doctype_rules": [{"node": "DC_R2", "title_terms": ["품목보고서"]}]}
    got = ts.suggest_for_doc("DC_R2", docs, pos[0]["file"], rule_doc=rule)
    assert "품목보고서" not in [c["term"] for c in got["focus_only"]]

    stop = {"global": {"품목보고서"}, "by_node": {}}
    got = ts.suggest_for_doc("DC_R2", docs, pos[0]["file"], stopwords=stop)
    assert "품목보고서" not in [c["term"] for c in got["focus_only"]]


#------------------------------------------------------------------
# 재료에 없는 문서를 가리키면 빈 목록으로 끝난다
#------------------------------------------------------------------
def test_없는_문서를_가리키면_빈목록():
    pos = [doc(f"확정{i}", f"문서{i}.docx", labels=["DC_R3"], text="품목보고서 내용")
           for i in range(5)]
    got = ts.suggest_for_doc("DC_R3", pos + others(10, "다른 본문."), "D:/없는/문서.docx")
    assert got["focus_only"] == []
    assert got["candidates"]           # 분류 단위 후보는 그대로 있다


# ──────────────────────────────────────────────────────────────────
# 고른 말을 규칙에 덧붙이기
# ──────────────────────────────────────────────────────────────────

#------------------------------------------------------------------
# 있는 말은 순서까지 그대로 두고 뒤에 붙인다
#------------------------------------------------------------------
def test_규칙에_덧붙인다():
    rule = {"doctype_rules": [
        {"id": "dt_a", "node": "DC_A", "title_terms": ["보고서", "리포트"],
         "filename": ["보고서"]}]}
    picks = [{"term": "품목보고서", "fields": ["title_terms", "filename"], "extra": {}}]
    added, skipped, missing = ts.apply_terms(rule, "DC_A", picks)
    assert missing is False
    assert skipped == []
    assert added == [("title_terms", "품목보고서"), ("filename", "품목보고서")]
    got = rule["doctype_rules"][0]
    # 사람이 적어 둔 말이 순서까지 그대로고, 새 말은 뒤에 붙는다.
    assert got["title_terms"] == ["보고서", "리포트", "품목보고서"]
    assert got["filename"] == ["보고서", "품목보고서"]


#------------------------------------------------------------------
# 이미 있는 말은 건너뛴다(대소문자만 달라도 같은 말)
#------------------------------------------------------------------
def test_이미_있는_말은_건너뛴다():
    rule = {"doctype_rules": [{"node": "DC_B", "title_terms": ["mpower"]}]}
    added, skipped, _ = ts.apply_terms(
        rule, "DC_B", [{"term": "Mpower", "fields": ["title_terms"], "extra": {}}])
    assert added == []
    assert skipped == [("title_terms", "Mpower")]
    assert rule["doctype_rules"][0]["title_terms"] == ["mpower"]


#------------------------------------------------------------------
# 본문 칸에 넣을 때 min_count 가 따라붙되, 사람이 정한 값은 안 덮는다
#------------------------------------------------------------------
def test_본문칸은_min_count_가_따라붙는다():
    rule = {"doctype_rules": [{"node": "DC_C", "terms": []}]}
    ts.apply_terms(rule, "DC_C",
                   [{"term": "분기실적", "fields": ["terms"], "extra": {"min_count": 2}}])
    assert rule["doctype_rules"][0]["min_count"] == 2

    rule2 = {"doctype_rules": [{"node": "DC_C", "terms": [], "min_count": 3}]}
    ts.apply_terms(rule2, "DC_C",
                   [{"term": "분기실적", "fields": ["terms"], "extra": {"min_count": 2}}])
    assert rule2["doctype_rules"][0]["min_count"] == 3     # 사람이 정한 값이 이긴다


#------------------------------------------------------------------
# 규칙이 없는 분류에는 규칙을 만들지 않는다
#=> 규칙 생성은 '분류 불러오기'의 일이다. 두 곳에서 만들면 결과가 갈라진다.
#------------------------------------------------------------------
def test_규칙이_없으면_만들지_않는다():
    rule = {"doctype_rules": [{"node": "DC_A", "title_terms": []}]}
    added, skipped, missing = ts.apply_terms(
        rule, "DC_없음", [{"term": "품목보고서", "fields": ["title_terms"], "extra": {}}])
    assert missing is True
    assert added == [] and skipped == []
    assert len(rule["doctype_rules"]) == 1       # 규칙을 새로 만들지 않았다


#------------------------------------------------------------------
# 넣은 뒤에도 규칙 파일이 유효하다 (덧붙이기 → 저장 → 검증 한 바퀴)
#=> 이 기능이 파일을 깨뜨리면 분류가 통째로 멈춘다(규칙 검증에 걸리면 엔진이
#   그 파일을 아예 안 읽는다). 화면이 실제로 지나는 경로 그대로 돌려 본다.
#------------------------------------------------------------------
def test_넣은_뒤에도_규칙파일이_유효하다(tmp_path):
    from csoclassify.classify import docvocab
    from csoclassify.classify import doc_rules as DR

    path = tmp_path / "doc_rule.yaml"
    path.write_text(
        "version: doctype-test.1\n"
        "conflict: all\n"
        "defaults: {scoring: staged, head_chars: 400, t_low: 0.35,\n"
        "  t_seed: 0.85, embed_cap: 0.65}\n"
        "doctype_rules:\n"
        "- id: dt_a\n  node: DC_001_003\n  weight: medium\n"
        "  title_terms: [보고서]\n  filename: [보고서]\n  terms: []\n",
        encoding="utf-8")

    doc = docvocab.load_doc(str(path))
    added, skipped, missing = ts.apply_terms(doc, "DC_001_003", [
        {"term": "품목보고서", "fields": ["title_terms", "filename"], "extra": {}},
        {"term": "분기실적", "fields": ["terms"], "extra": {"min_count": 2}},
    ])
    assert missing is False and skipped == [] and len(added) == 3
    docvocab.save_doc(str(path), doc)

    # 저장한 파일이 규칙 검증을 통과해야 한다 — 여기서 걸리면 엔진이 파일을 안 읽는다.
    again = docvocab.load_doc(str(path))
    violations = DR.validate_doc_rule_data(again)
    assert violations == [], violations
    rule = again["doctype_rules"][0]
    assert rule["title_terms"] == ["보고서", "품목보고서"]
    assert rule["filename"] == ["보고서", "품목보고서"]
    assert rule["terms"] == ["분기실적"]
    assert rule["min_count"] == 2


# ──────────────────────────────────────────────────────────────────
# 금지 목록과 이력 (설계 11장)
# ──────────────────────────────────────────────────────────────────

#------------------------------------------------------------------
# 기각한 말이 다음 제안에서 자동으로 빠진다 (고리를 닫는 부분)
#=> 이 기능의 값어치는 '쓸수록 조용해지는 것'이다. 기각 → 목록 → 다음 제안까지
#   이어지지 않으면 관리자는 같은 말을 영원히 다시 본다.
#------------------------------------------------------------------
def test_기각한_말은_다음_제안에서_빠진다(tmp_path):
    path = str(tmp_path / "doc_rule_stopwords.yaml")
    pos = [doc(f"확정{i}", f"품목보고서 {i}.pdf", labels=["DC_S"], text="품목보고서 내용")
           for i in range(5)]
    docs = pos + others(10, "관계없는 본문.")
    assert "품목보고서" in terms(ts.suggest("DC_S", docs))

    added = ts.add_stopwords(path, ["품목보고서"], node="DC_S")
    assert added == ["품목보고서"]
    stop = ts.load_stopwords(path)
    assert "품목보고서" not in terms(ts.suggest("DC_S", docs, stopwords=stop))
    # 그 분류에만 막았으므로 다른 분류에서는 그대로 나온다.
    assert stop["global"] == set()


#------------------------------------------------------------------
# 금지 목록은 더하기만 한다 — 있던 말을 지우지 않는다
#------------------------------------------------------------------
def test_금지목록은_더하기만_한다(tmp_path):
    path = str(tmp_path / "doc_rule_stopwords.yaml")
    ts.add_stopwords(path, ["인증"])
    ts.add_stopwords(path, ["고객"], node="DC_A")
    ts.add_stopwords(path, ["계약"])
    got = ts.load_stopwords(path)
    assert got["global"] == {"인증", "계약"}
    assert got["by_node"]["DC_A"] == {"고객"}

    # 이미 있는 말을 다시 넣으면 아무것도 더해지지 않는다.
    assert ts.add_stopwords(path, ["인증"]) == []
    assert ts.load_stopwords(path)["global"] == {"인증", "계약"}


#------------------------------------------------------------------
# 원본은 .bak 으로 남긴다
#=> 손으로 적어 둔 목록을 화면이 통째로 다시 쓰므로, 되돌릴 길을 남긴다.
#------------------------------------------------------------------
def test_금지목록_원본을_남긴다(tmp_path):
    path = tmp_path / "doc_rule_stopwords.yaml"
    ts.add_stopwords(str(path), ["인증"])
    before = path.read_text(encoding="utf-8")
    ts.add_stopwords(str(path), ["고객"])
    assert (tmp_path / "doc_rule_stopwords.yaml.bak").read_text(encoding="utf-8") == before


#------------------------------------------------------------------
# 빈 목록을 넣으면 파일을 건드리지 않는다
#------------------------------------------------------------------
def test_빈_금지목록은_파일을_안_건드린다(tmp_path):
    path = tmp_path / "doc_rule_stopwords.yaml"
    assert ts.add_stopwords(str(path), []) == []
    assert ts.add_stopwords(str(path), ["  "]) == []
    assert not path.exists()


#------------------------------------------------------------------
# 이력은 덧붙이기만 한다(append-only)
#=> doc_rule.yaml 에는 주석이 남지 않는다. "이 단어가 언제·어느 근거로 들어왔나"를
#   되짚을 기록은 이 파일뿐이라, 앞 줄을 고치는 일이 있으면 안 된다.
#------------------------------------------------------------------
def test_이력은_덧붙이기만_한다(tmp_path):
    path = str(tmp_path / "doc_rule_suggest_audit.jsonl")
    cand = {"term": "품목보고서", "fields": ["title_terms", "filename"],
            "df_pos": 0.5, "df_neg": 0.0, "docs": 6, "clusters": 2, "flags": []}
    assert ts.append_audit(path, "DC_A", "accept", [cand], "kim") == 1
    assert ts.append_audit(path, "DC_A", "reject",
                           [dict(cand, term="품목")], "kim", reason="너무 넓음") == 1

    rows = ts.load_audit(path)
    assert len(rows) == 2
    assert rows[0]["action"] == "accept" and rows[0]["term"] == "품목보고서"
    assert rows[0]["fields"] == ["title_terms", "filename"]
    assert rows[0]["reviewer"] == "kim"
    assert rows[1]["action"] == "reject" and rows[1]["reason"] == "너무 넓음"
    # 근거 숫자를 함께 남긴다 — 나중에 "그때 왜 넣었지"를 답할 수 있어야 한다.
    assert rows[0]["docs"] == 6 and rows[0]["clusters"] == 2


#------------------------------------------------------------------
# 이력은 분류별로 골라 읽는다
#------------------------------------------------------------------
def test_이력을_분류별로_읽는다(tmp_path):
    path = str(tmp_path / "audit.jsonl")
    one = {"term": "가", "fields": [], "flags": []}
    ts.append_audit(path, "DC_A", "accept", [one], "kim")
    ts.append_audit(path, "DC_B", "accept", [one], "kim")
    assert len(ts.load_audit(path)) == 2
    assert len(ts.load_audit(path, "DC_A")) == 1
    assert ts.load_audit(path, "DC_없음") == []


#------------------------------------------------------------------
# 빈 후보를 주면 이력을 남기지 않는다
#=> 아무 결정도 없는 줄이 쌓이면 이력이 못 읽을 만큼 지저분해진다.
#------------------------------------------------------------------
def test_빈_결정은_이력을_안_남긴다(tmp_path):
    path = tmp_path / "audit.jsonl"
    assert ts.append_audit(str(path), "DC_A", "accept", [], "kim") == 0
    assert not path.exists()


#------------------------------------------------------------------
# 이력 파일이 없거나 깨져 있어도 화면은 떠야 한다
#------------------------------------------------------------------
def test_이력이_없거나_깨져도_읽는다(tmp_path):
    assert ts.load_audit(None) == []
    assert ts.load_audit(str(tmp_path / "없음.jsonl")) == []
    path = tmp_path / "audit.jsonl"
    path.write_text('{"node":"DC_A","term":"가"}\n{깨진 줄}\n'
                    '{"node":"DC_A","term":"나"}\n', encoding="utf-8")
    assert [r["term"] for r in ts.load_audit(str(path))] == ["가", "나"]


#------------------------------------------------------------------
# 끝말 빠른 판정이 느린 판정과 같은 답을 낸다
#=> 실데이터 문서가 평균 12만 자라 '끝말로 끝나는가'를 수만 번 묻게 된다.
#   목록을 처음부터 훑는 대신 길이별 집합으로 찾는데, 답이 달라지면 안 된다.
#------------------------------------------------------------------
@pytest.mark.parametrize("word", [
    "품목보고서", "매뉴얼", "사내규정", "도움말", "이슈페이퍼", "계약서",
    "규정", "자료", "가", "", "Mpower", "현황분석", "보고서의",
])
def test_끝말_빠른판정이_느린판정과_같다(word):
    from csoclassify.classify import docvocab
    slow = any(word.endswith(suf) for suf in docvocab.DOC_SUFFIXES) if word else False
    assert ts.ends_with_suffix(word) is slow


#------------------------------------------------------------------
# 사전이 준 끝말 목록으로도 같은 답을 낸다(내장 목록이 아닐 때)
#=> core 사전에 suffixes 칸이 있으면 그 목록이 기준이 된다.
#------------------------------------------------------------------
def test_끝말_빠른판정은_받은_목록을_쓴다():
    mine = ["정산서", "회람"]
    assert ts.ends_with_suffix("월말정산서", mine) is True
    assert ts.ends_with_suffix("사내회람", mine) is True
    assert ts.ends_with_suffix("품목보고서", mine) is False   # 내장 목록에는 있지만
    assert ts.ends_with_suffix("회람", mine) is True          # 말 자체가 끝말이어도 참


#------------------------------------------------------------------
# 같은 말이 여러 번 나와도 한 번만 센다(중복 제거가 결과를 바꾸지 않는다)
#=> 조사 떼기 전에 set 으로 줄이는 최적화를 넣었다. 집합을 돌려주므로 값은
#   같아야 한다 — 이것이 깨지면 제안이 조용히 달라진다.
#------------------------------------------------------------------
def test_같은_말이_여러번_나와도_결과가_같다():
    one = ts.scan_words("품목보고서 내용 정리")
    many = ts.scan_words("품목보고서 품목보고서 내용 내용 내용 정리 품목보고서")
    assert one == many


#------------------------------------------------------------------
# 용언 활용형은 후보가 되지 못한다
#=> 2026-09-16 실코퍼스 첫 측정에서 매뉴얼 후보 9개 중 6개가 활용형이었다
#   ('클릭하면'·'선택하고'·'가능하며'·'통하여'). 빈도가 아니라 문법 문제라,
#   코퍼스를 보지 않고 모양만으로 버린다.
#------------------------------------------------------------------
@pytest.mark.parametrize("word", [
    "클릭하면", "선택하고", "선택하면", "가능하며", "통하여", "포함되어야",
    "정한다", "감사합니다", "적용하는", "해당되지", "제출해야", "관련된다",
])
def test_활용형은_후보가_아니다(word):
    assert ts.is_usable(word) is False


#------------------------------------------------------------------
# 짧은 꼬리('한'·'된')는 일부러 막지 않는다
#=> 그 한 글자를 막으면 기한·제한·권한·시한 같은 멀쩡한 명사가 함께 죽는다.
#   '선택한' 하나를 잡으려고 넷을 잃을 수는 없다 — 이 판단을 시험으로 굳힌다.
#------------------------------------------------------------------
@pytest.mark.parametrize("word", [
    "기한", "제한", "권한", "시한",              # '한'으로 끝나는 명사
    # ('관한'·'대한'·'위한' 은 명사가 아니라 용언이라 걸러지는 것이 맞다)
    "품목보고서", "동호회규정", "회원명부", "활동일지", "등록신청서",
    "비품", "구매", "적용범위", "매뉴얼", "지침",
])
def test_멀쩡한_명사는_살아남는다(word):
    assert ts.is_usable(word) is True


#------------------------------------------------------------------
# 활용형 어미가 현행 자산의 낱말을 죽이지 않는다
#=> 규칙 단어·유의어 사전·분류 이름에 실제로 대 본 검산을 시험으로 남긴다.
#   사전이 늘어나 멀쩡한 말이 걸리기 시작하면 여기서 먼저 걸린다.
#------------------------------------------------------------------
def test_활용형_어미가_현행_규칙단어를_죽이지_않는다():
    import glob
    import os
    import yaml

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rule_path = os.path.join(root, "resources", "policy", "doc_rule.yaml")
    if not os.path.isfile(rule_path):
        pytest.skip("배포 규칙 파일이 없는 자리(고객사 실데이터는 저장소에 없다)")

    words = set()
    doc = yaml.safe_load(open(rule_path, encoding="utf-8")) or {}
    for rule in doc.get("doctype_rules") or []:
        for cell in ("title_terms", "head_terms", "terms", "filename"):
            words.update(str(w) for w in (rule.get(cell) or []))
    for path in glob.glob(os.path.join(root, "resources", "policy",
                                       "synonyms", "*.yaml")):
        data = yaml.safe_load(open(path, encoding="utf-8")) or {}

        def walk(node):
            if isinstance(node, str):
                words.add(node)
            elif isinstance(node, list):
                for item in node:
                    walk(item)
            elif isinstance(node, dict):
                for key, val in node.items():
                    walk(key)
                    walk(val)
        walk(data)

    # 구절은 공백으로 잘려 낱말 단위로만 후보가 되므로 낱말만 본다.
    solo = {w for w in words if " " not in w and ts.HANGUL_RE.search(w)}
    # 꼬리 목록도 core 사전에 적혀 있어 '낱말'로 딸려 온다 — 자기 자신은 뺀다.
    solo -= set(ts.VERB_ENDINGS)
    killed = sorted(w for w in solo if w.endswith(ts.VERB_ENDINGS))
    # 걸리는 것이 있다면 그 자체가 활용형이어야 한다('감사합니다' 같은 것).
    assert len(killed) <= 5, f"멀쩡한 말이 죽는다: {killed}"


# ──────────────────────────────────────────────────────────────────
# 활용형 꼬리는 사전에서 온다 (끝말 목록과 같은 규약)
# ──────────────────────────────────────────────────────────────────

#------------------------------------------------------------------
# 사전이 준 꼬리 목록을 쓴다
#=> 목록이 코드에만 있으면 꼬리 하나 늘리는 데 재빌드가 필요하고, 회사마다
#   다른 말버릇을 담을 수 없다(끝말 목록을 2026-09-15 에 사전으로 옮긴 것과 같은 이유).
#------------------------------------------------------------------
def test_활용형_꼬리를_사전에서_받는다():
    mine = ["드리오니", "하옵신"]
    # 사전 목록을 주면 그것만 본다 — 내장 목록은 쓰지 않는다.
    assert ts.is_usable("보고드리오니", mine) is False
    assert ts.is_usable("클릭하면", mine) is True      # 내장 목록에는 있지만 사전에 없다
    # 목록을 안 주면 내장 목록을 쓴다(옛 사전이 깔린 곳에서 필터가 꺼지지 않게).
    assert ts.is_usable("클릭하면") is False


#------------------------------------------------------------------
# 한 글자 꼬리도 받는다
#=> 끝말(suffixes)은 한 글자를 막지만('서'가 모든 '…서'를 끊는다), 활용형 꼬리는
#   '된'·'는' 처럼 한 글자가 곧 뜻이라 막으면 쓸 수 없다.
#------------------------------------------------------------------
def test_한글자_꼬리도_받는다():
    from csoclassify.classify import docvocab
    assert docvocab._clean_endings(["된", "는", "하면"]) == ["된", "는", "하면"]
    # 끝말 쪽은 종전대로 한 글자를 버린다 — 두 규칙이 섞이면 안 된다.
    assert docvocab._clean_suffixes(["서", "보고서"]) == ["보고서"]


#------------------------------------------------------------------
# core 사전의 꼬리 목록이 멀쩡한 말을 죽이지 않는다
#=> 사전에 꼬리를 더할 때마다 여기서 먼저 걸리게 한다. '인'을 넣으면
#   승인·확인·가이드라인이, '한'을 넣으면 경고서한이 함께 죽는다.
#------------------------------------------------------------------
def test_사전의_꼬리가_현행_낱말을_죽이지_않는다():
    import glob
    import os
    import yaml
    from csoclassify.classify import docvocab

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rule_path = os.path.join(root, "resources", "policy", "doc_rule.yaml")
    if not os.path.isfile(rule_path):
        pytest.skip("배포 규칙 파일이 없는 자리(고객사 실데이터는 저장소에 없다)")
    endings = (docvocab.load_synonyms(rule_path) or {}).get("verb_endings") or []
    assert endings, "core 사전에 verb_endings 칸이 있어야 한다"

    words = set()
    doc = yaml.safe_load(open(rule_path, encoding="utf-8")) or {}
    for rule in doc.get("doctype_rules") or []:
        for cell in ("title_terms", "head_terms", "terms", "filename"):
            words.update(str(w) for w in (rule.get(cell) or []))
    for path in glob.glob(os.path.join(root, "resources", "policy",
                                       "synonyms", "*.yaml")):
        data = yaml.safe_load(open(path, encoding="utf-8")) or {}

        def walk(node):
            if isinstance(node, str):
                words.add(node)
            elif isinstance(node, list):
                for item in node:
                    walk(item)
            elif isinstance(node, dict):
                for key, val in node.items():
                    walk(key)
                    walk(val)
        walk(data)

    solo = {w for w in words if " " not in w and ts.HANGUL_RE.search(w)}
    # 꼬리 목록 자체는 사전에 적힌 말이라 검산에서 뺀다.
    solo -= set(endings)
    killed = sorted(w for w in solo if w.endswith(tuple(endings)))
    assert len(killed) <= 5, f"멀쩡한 말이 죽는다: {killed}"


#------------------------------------------------------------------
# 문서 화면의 '까닭 단 후보'에는 상한이 있다
#=> 문턱을 못 넘은 말도 올리는 자리라 상한이 없으면 문서 하나의 낱말이 통째로
#   올라온다 — 실측에서 사내규정 문서 한 건이 후보 126개를 냈다. 아무도 못 읽는다.
#   문턱을 넘은 후보는 상한에 걸리지 않는다(제대로 걸러진 말을 잃으면 안 된다).
#------------------------------------------------------------------
def test_문서화면_후보에_상한이_있다():
    # 숫자가 든 말은 미리 걸러지므로 한글만으로 서로 다른 말을 만든다.
    자 = "가나다라마바사아자차카타파하거너더러머버서어저처커터"
    filler = " ".join(f"{a}{b}잡말" for a in 자[:8] for b in 자[:8])
    # 이 말들은 두 문서에만 있다 — 후보 문턱(3건)은 못 넘고 '까닭 단 후보'가 된다.
    pos = [doc(f"확정{i}", f"품목보고서 {i}.pdf", labels=["DC_N"],
               text="품목보고서\n" + (filler if i < 2 else ""))
           for i in range(6)]
    docs = pos + others(10, "관계없는 본문.")
    got = ts.suggest_for_doc("DC_N", docs, pos[0]["file"])
    near = [c for c in got["focus_only"] if c["group"] == "near"]
    assert len(near) == ts.NEAR_MISS_MAX
    assert got["near_cut"] > 0          # 접어 둔 수를 알려 준다
    # 문턱을 넘은 말은 상한과 무관하게 남는다.
    assert "품목보고서" in [c["term"] for c in got["focus_only"]]


#------------------------------------------------------------------
# 그 문서에만 있는 말은 문서 화면에도 안 올린다
#=> 한 문서의 낱말은 규칙이 아니라 그 문서의 지문이다.
#------------------------------------------------------------------
def test_그_문서에만_있는_말은_안_올린다():
    pos = [doc(f"확정{i}", f"문서{i}.docx", labels=["DC_O"], text="품목보고서 내용")
           for i in range(6)]
    pos[0] = doc("확정0", "문서0.docx", labels=["DC_O"],
                 text="품목보고서 내용 오직여기에만있는말")
    docs = pos + others(10, "관계없는 본문.")
    got = ts.suggest_for_doc("DC_O", docs, pos[0]["file"])
    assert "오직여기에만있는말" not in [c["term"] for c in got["focus_only"]]


#------------------------------------------------------------------
# 한글 따옴표·괄호도 구분자로 자른다
#=> 실데이터에서 '“동호회”라'·'「지원금」' 이 한 낱말로 올라왔다(2026-09-16).
#------------------------------------------------------------------
def test_한글_따옴표도_자른다():
    got = ts.split_words("“동호회”라 하고 「지원금」 및 《회원》 자격")
    assert got == {"동호회", "지원금", "회원", "자격"}


#------------------------------------------------------------------
# 인용 조사 '라'를 떼되 짧은 명사는 건드리지 않는다
#=> 규정 문투('“동호회”라 한다')에서 나온다. 뗀 뒤 두 글자가 안 남으면 떼지 않으므로
#   '나라' 같은 말은 그대로 살아남는다.
#------------------------------------------------------------------
@pytest.mark.parametrize("word,want", [
    ("동호회라", "동호회"), ("지원금이라", "지원금"), ("나라", "나라"), ("우라", "우라"),
])
def test_인용조사_라_떼기(word, want):
    assert ts.strip_particle(word) == want
