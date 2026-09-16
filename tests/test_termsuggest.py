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
