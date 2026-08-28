#------------------------------------------------------------------
# 재설계 1단계(구조 개선) 테스트
#=> 표제부/첫줄 제한 · 서식 필드어 · 구조 신호 · 본문 격하 · noisy-OR 가산점수 ·
#   단독 채택 금지 · 정렬 재현성 · 근거 블록을 검증한다.
#   legacy 모드가 종전과 완전히 같게 도는지(하위호환)도 함께 확인한다.
#------------------------------------------------------------------

import pytest

from csoclassify.classify import axes as A
from csoclassify.classify import doc_rules as D
from csoclassify.classify import doctype as DT


#------------------------------------------------------------------
# 테스트용 분류체계
#=> 실제 doc_taxonomy.yaml 에 의존하지 않고 최소 트리를 만든다. 1계층 두 개를
#   두어 계층 정합성(11-3) 검사까지 시험할 수 있게 한다.
#
# -in: 없음
#
# -out: A.Taxonomy
# -out: error = 없음
#------------------------------------------------------------------
def mk_taxonomy():
    return A.Taxonomy(
        source="test", exported_at="20260826", node_count=5,
        nodes=[
            A.TaxonomyNode(dc_id="BIZ", parent=None, order=1, title="경영관리", status=1),
            A.TaxonomyNode(dc_id="MINUTES", parent="BIZ", order=1, title="회의록", status=1),
            A.TaxonomyNode(dc_id="REPORT", parent="BIZ", order=2, title="보고서", status=1),
            A.TaxonomyNode(dc_id="LAW", parent=None, order=2, title="법무규정", status=1),
            A.TaxonomyNode(dc_id="CONTRACT", parent="LAW", order=1, title="계약서", status=1),
        ],
    )


#------------------------------------------------------------------
# 규칙셋 조립 헬퍼
#=> 테스트마다 defaults 만 바꿔 가며 같은 규칙을 재사용하기 위한 것.
#
# -in: rules    = DoctypeRule 튜플
# -in: defaults = D.Defaults (없으면 staged 모드 기본값)
#
# -out: D.DocRuleSet
# -out: error = 없음
#------------------------------------------------------------------
def mk_set(rules, defaults=None):
    return D.DocRuleSet(
        conflict=D.ConflictSpec(strategy="all"),
        rules=tuple(rules),
        defaults=defaults or D.Defaults(scoring="staged"),
    )


STAGED = D.Defaults(scoring="staged")


# ── 표제부(head) · 첫줄(title) 제한 ────────────────────────────────

#------------------------------------------------------------------
# 본문 중간 언급은 표제부 신호를 만들지 않는다 (P2)
#=> "회의록을 첨부합니다"는 그 문서가 회의록이라는 증거가 아니다. 표제부
#   범위를 벗어난 언급이 head 신호로 잡히지 않아야 한다.
#------------------------------------------------------------------
def test_표제부_밖의_언급은_head신호가_아니다():
    rule = D.DoctypeRule(id="r", node="MINUTES", head_terms=("회의록",), head_chars=20)
    text = "제목 없는 공문입니다.\n" + ("가" * 200) + "\n첨부: 회의록을 참조하십시오."
    sig = DT.scan_doctype(text, "D:/x/공문.hwp", mk_set([rule]), mk_taxonomy())
    assert sig.values == ()


#------------------------------------------------------------------
# 표제부 안이면 head 신호가 잡힌다
#------------------------------------------------------------------
def test_표제부_안이면_head신호가_잡힌다():
    rule = D.DoctypeRule(id="r", node="MINUTES", head_terms=("회의록",), head_chars=50)
    sig = DT.scan_doctype("2026년 3분기 영업 회의록\n일시: ...", "D:/x/a.hwp",
                          mk_set([rule]), mk_taxonomy())
    assert len(sig.values) == 1
    assert "head" in sig.values[0]["from"]


#------------------------------------------------------------------
# 첫 줄 신호는 첫 비어있지 않은 줄만 본다
#=> 빈 줄이 앞에 있어도 건너뛰고, 둘째 줄에 있는 단어는 잡지 않는다.
#------------------------------------------------------------------
def test_title신호는_첫_비어있지_않은_줄만_본다():
    rule = D.DoctypeRule(id="r", node="MINUTES", title_terms=("회의록",))
    tax, rs = mk_taxonomy(), mk_set([D.DoctypeRule(id="r", node="MINUTES",
                                                  title_terms=("회의록",))])
    ok = DT.scan_doctype("\n\n  3분기 회의록  \n본문", "D:/x/a.hwp", rs, tax)
    assert "title" in ok.values[0]["from"]

    ng = DT.scan_doctype("공문\n3분기 회의록", "D:/x/a.hwp", rs, tax)
    assert ng.values == ()
    assert rule.node == "MINUTES"


#------------------------------------------------------------------
# ppt/xls/xlsx 는 첫 줄이 제목이 아니므로 title 신호를 건너뛴다 (실측 3-B-3)
#=> .ppt 는 첫 줄이 제목인 비율이 16.7%, .xls/.xlsx 는 50% 였다.
#------------------------------------------------------------------
@pytest.mark.parametrize("ext,expect_title", [
    (".hwp", True), (".docx", True), (".pdf", True),
    (".ppt", False), (".xls", False), (".xlsx", False),
])
def test_첫줄이_제목이_아닌_포맷은_title신호를_건너뛴다(ext, expect_title):
    rule = D.DoctypeRule(id="r", node="MINUTES", title_terms=("회의록",))
    sig = DT.scan_doctype("회의록\n본문", f"D:/x/a{ext}", mk_set([rule]), mk_taxonomy())
    if expect_title:
        assert "title" in sig.values[0]["from"]
    else:
        assert sig.values == ()


# ── 서식 필드어(form) ────────────────────────────────────────────

#------------------------------------------------------------------
# any_of + min_types — 종수를 채워야 성립한다
#------------------------------------------------------------------
def test_form_any_of는_서로_다른_항목_종수를_센다():
    form = D.FormSpec(any_of=("일시", "장소", "참석", "안건"), min_types=3)
    rule = D.DoctypeRule(id="r", node="MINUTES", form=form,
                         head_terms=("회의",), head_chars=100)
    tax, rs = mk_taxonomy(), mk_set([rule])

    ok = DT.scan_doctype("회의 결과\n일시: 3/1\n장소: 본사\n참석: 홍길동", "D:/x/a.hwp", rs, tax)
    assert "form" in ok.values[0]["from"]

    # 같은 단어가 여러 번 나와도 '종수'는 1 이라 성립하지 않는다.
    ng = DT.scan_doctype("회의 결과\n일시: 3/1 일시 일시 일시", "D:/x/a.hwp", rs, tax)
    assert "form" not in ng.values[0]["from"]


#------------------------------------------------------------------
# all_of — 하나라도 빠지면 성립하지 않는다
#------------------------------------------------------------------
def test_form_all_of는_전부_있어야_성립한다():
    form = D.FormSpec(all_of=("갑", "을", "계약기간"))
    rule = D.DoctypeRule(id="r", node="CONTRACT", form=form, filename=("계약",))
    tax, rs = mk_taxonomy(), mk_set([rule])

    ok = DT.scan_doctype("갑과 을은 계약기간 동안", "D:/x/계약_a.hwp", rs, tax)
    assert "form" in ok.values[0]["from"]

    ng = DT.scan_doctype("갑과 을은 협의한다", "D:/x/계약_a.hwp", rs, tax)
    assert "form" not in ng.values[0]["from"]


#------------------------------------------------------------------
# 빈 form 블록은 "항상 성립"이 되면 안 된다
#=> 조건이 하나도 없으면 로더가 None 으로 접고, 스캔은 신호를 만들지 않는다.
#------------------------------------------------------------------
def test_빈_form은_신호를_만들지_않는다():
    assert D._parse_form({}) is None
    assert D._parse_form({"any_of": ["a", "b"]}) is None      # min_types 없음
    ok, matched = DT._match_form("무슨 글이든", None, ())
    assert ok is False and matched == []


# ── 본문 격하 · 단독 채택 금지 (8-4) ──────────────────────────────

#------------------------------------------------------------------
# staged 모드에서 본문 단어만으로는 후보가 되지 않는다 (P1 해소의 핵심)
#=> 이게 없으면 신뢰도를 아무리 낮춰도 conflict: all 에서 약한 후보가 남는다.
#------------------------------------------------------------------
def test_staged에서_본문만으로는_후보가_되지_않는다():
    rule = D.DoctypeRule(id="r", node="MINUTES", terms=("회의",))
    sig = DT.scan_doctype("올해 사업계획을 논의한 회의 내용을 정리한다.",
                          "D:/x/사업계획서.hwp", mk_set([rule]), mk_taxonomy())
    assert sig.values == ()


#------------------------------------------------------------------
# 구조 신호도 단독으로는 후보가 되지 않는다
#------------------------------------------------------------------
def test_structure만으로는_후보가_되지_않는다():
    rule = D.DoctypeRule(id="r", node="CONTRACT", structure=(r"제\d+조",))
    sig = DT.scan_doctype("제1조 목적 ... 제2조 범위", "D:/x/사내문서.hwp",
                          mk_set([rule]), mk_taxonomy())
    assert sig.values == ()


#------------------------------------------------------------------
# 본문이 다른 신호와 결합하면 후보가 된다(보강 증거로서의 역할)
#------------------------------------------------------------------
def test_본문은_다른_신호와_결합하면_후보가_된다():
    rule = D.DoctypeRule(id="r", node="MINUTES", terms=("회의",), filename=("회의록",))
    sig = DT.scan_doctype("회의 내용", "D:/x/3분기_회의록.hwp",
                          mk_set([rule]), mk_taxonomy())
    assert len(sig.values) == 1
    assert set(sig.values[0]["from"]) == {"body", "name"}


#------------------------------------------------------------------
# min_distinct / min_count 로 본문 신호를 조인다
#=> 기본값은 1(하위호환)이고, 정책 파일에서 2로 켜면 흔한 단어 단독 히트가 죽는다.
#------------------------------------------------------------------
def test_min_distinct와_min_count가_본문_신호를_조인다():
    tax = mk_taxonomy()
    rule = D.DoctypeRule(id="r", node="MINUTES", terms=("회의", "회의록"),
                         filename=("회의록",), min_distinct=2)
    sig = DT.scan_doctype("회의 한 번 언급", "D:/x/회의록.hwp", mk_set([rule]), tax)
    assert "body" not in sig.values[0]["from"]      # 1 종뿐이라 탈락

    sig2 = DT.scan_doctype("회의 및 회의록", "D:/x/회의록.hwp", mk_set([rule]), tax)
    assert "body" in sig2.values[0]["from"]         # 2 종 → 통과


#------------------------------------------------------------------
# 규칙별 값이 전역 defaults 를 이긴다
#------------------------------------------------------------------
def test_규칙별_임계값이_전역_defaults를_이긴다():
    defaults = D.Defaults(scoring="staged", min_count=5)
    rule = D.DoctypeRule(id="r", node="MINUTES", terms=("회의",),
                         filename=("회의록",), min_count=1)
    sig = DT.scan_doctype("회의", "D:/x/회의록.hwp", mk_set([rule], defaults), mk_taxonomy())
    assert "body" in sig.values[0]["from"]


# ── noisy-OR 가산 점수 (8-1) ─────────────────────────────────────

#------------------------------------------------------------------
# 근거가 쌓이면 점수가 오른다 (P4 해소)
#=> 종전 max() 에서는 신호가 3개든 1개든 같은 값이었다.
#------------------------------------------------------------------
def test_근거가_쌓이면_점수가_오른다():
    tax = mk_taxonomy()
    one = D.DoctypeRule(id="r", node="MINUTES", head_terms=("회의록",), head_chars=50)
    many = D.DoctypeRule(id="r", node="MINUTES", head_terms=("회의록",), head_chars=50,
                         filename=("회의록",), terms=("회의",))

    s1 = DT.scan_doctype("3분기 회의록", "D:/x/문서.hwp", mk_set([one]), tax)
    s2 = DT.scan_doctype("3분기 회의록 회의", "D:/x/회의록.hwp", mk_set([many]), tax)
    assert s2.values[0]["confidence"] > s1.values[0]["confidence"]


#------------------------------------------------------------------
# noisy-OR 는 1.0 을 넘지 않는다
#=> 상한 처리를 따로 하지 않아도 되는 것이 이 결합식을 고른 이유다.
#------------------------------------------------------------------
def test_noisy_or는_1을_넘지_않는다():
    parts = [{"signal": f"s{i}", "c": 0.9} for i in range(10)]
    score = DT._noisy_or(parts)
    assert 0.0 <= score < 1.0


#------------------------------------------------------------------
# noisy-OR 계산이 정의대로인지
#------------------------------------------------------------------
def test_noisy_or_계산값():
    got = DT._noisy_or([{"signal": "a", "c": 0.7},
                        {"signal": "b", "c": 0.5},
                        {"signal": "c", "c": 0.3}])
    assert got == pytest.approx(1 - (0.3 * 0.5 * 0.7))


#------------------------------------------------------------------
# t_low 미만 후보는 탈락한다 (8-3)
#------------------------------------------------------------------
def test_t_low_미만은_후보에서_빠진다():
    tax = mk_taxonomy()
    rule = D.DoctypeRule(id="r", node="MINUTES", terms=("회의",), filename=("회의록",))
    # body(0.15)+name(0.30) → 0.405. 임계를 그 위로 올리면 탈락해야 한다.
    high = D.Defaults(scoring="staged", t_low=0.5)
    assert DT.scan_doctype("회의", "D:/x/회의록.hwp", mk_set([rule], high), tax).values == ()
    low = D.Defaults(scoring="staged", t_low=0.30)
    assert DT.scan_doctype("회의", "D:/x/회의록.hwp", mk_set([rule], low), tax).values != ()


# ── 정렬 재현성 (8-5, P5) ────────────────────────────────────────

#------------------------------------------------------------------
# 동점 후보의 순서가 실행마다 같아야 한다
#=> 종전에는 set 순회 순서에 좌우돼 top_n 결과가 흔들렸다.
#------------------------------------------------------------------
def test_동점_후보_정렬은_재현가능하다():
    tax = mk_taxonomy()
    rules = [
        D.DoctypeRule(id="a", node="MINUTES", filename=("문서",)),
        D.DoctypeRule(id="b", node="REPORT", filename=("문서",)),
        D.DoctypeRule(id="c", node="CONTRACT", filename=("문서",)),
    ]
    rs = mk_set(rules)
    orders = {tuple(v["dc_id"] for v in DT.scan_doctype("본문", "D:/x/문서.hwp", rs, tax).values)
              for _ in range(20)}
    assert len(orders) == 1
    assert orders.pop() == ("CONTRACT", "MINUTES", "REPORT")   # 동점 → dc_id 오름차순


# ── 근거 블록 (13장, P6) ─────────────────────────────────────────

#------------------------------------------------------------------
# 근거 블록에 어느 신호가 왜 걸렸는지 남는다
#=> 이게 없으면 임계값을 실측으로 정할 수도, 검토자가 오분류 이유를 볼 수도 없다.
#------------------------------------------------------------------
def test_근거블록이_남는다():
    rule = D.DoctypeRule(id="r", node="MINUTES", weight="high",
                         title_terms=("회의록",), terms=("회의",), filename=("회의록",))
    sig = DT.scan_doctype("회의록\n회의 내용 회의", "D:/x/3분기_회의록.hwp",
                          mk_set([rule]), mk_taxonomy())
    d = sig.as_dict()["values"][0]

    assert d["stage"] == "rule"
    assert set(d["from"]) == {"title", "body", "name"}
    assert d["evidence"]["title"]["terms"] == [{"term": "회의록", "count": 1}]
    assert d["evidence"]["body"]["total"] == 3   # "회의록" 안의 "회의" 도 센다
    assert d["evidence"]["name"]["terms"] == ["회의록"]
    signals = {p["signal"] for p in d["score_parts"]}
    assert signals == {"title", "body", "name"}


#------------------------------------------------------------------
# 근거 블록에 원문 조각이 들어가지 않는다 (프라이버시)
#=> 규칙에 정의된 단어와 건수만 담아야 한다.
#------------------------------------------------------------------
def test_근거블록에_원문_조각이_없다():
    secret = "주민등록번호 900101-1234567 을 포함한 회의록"
    rule = D.DoctypeRule(id="r", node="MINUTES", title_terms=("회의록",))
    sig = DT.scan_doctype(secret, "D:/x/a.hwp", mk_set([rule]), mk_taxonomy())
    blob = repr(sig.as_dict())
    assert "900101" not in blob and "주민등록번호" not in blob


# ── 계층 정합성 (11-3) ───────────────────────────────────────────

#------------------------------------------------------------------
# 서로 다른 1계층 가지가 동시에 걸리면 모순 플래그가 붙는다
#=> 자동으로 하나를 버리지 않는다 — 검토 우선순위 신호로만 쓴다.
#------------------------------------------------------------------
def test_다른_가지가_동시에_걸리면_모순_플래그가_붙는다():
    rules = [D.DoctypeRule(id="a", node="MINUTES", filename=("문서",)),
             D.DoctypeRule(id="b", node="CONTRACT", filename=("문서",))]
    sig = DT.scan_doctype("본문", "D:/x/문서.hwp", mk_set(rules), mk_taxonomy())
    assert len(sig.values) == 2                      # 후보는 그대로 둔다
    assert sig.conflicts and sig.conflicts[0]["type"] == "cross_branch"


#------------------------------------------------------------------
# 같은 가지면 모순이 아니다
#------------------------------------------------------------------
def test_같은_가지는_모순이_아니다():
    rules = [D.DoctypeRule(id="a", node="MINUTES", filename=("문서",)),
             D.DoctypeRule(id="b", node="REPORT", filename=("문서",))]
    sig = DT.scan_doctype("본문", "D:/x/문서.hwp", mk_set(rules), mk_taxonomy())
    assert sig.conflicts == ()


# ── 하위호환: legacy 모드 (14-1) ─────────────────────────────────

#------------------------------------------------------------------
# legacy 는 종전과 같다 — 본문 1건이면 히트, 신뢰도도 종전 값
#=> 기존 doc_rule.yaml 을 한 글자도 안 고쳐도 그대로 돌아야 한다.
#------------------------------------------------------------------
def test_legacy는_본문_1건으로_히트하고_종전_신뢰도를_쓴다():
    rule = D.DoctypeRule(id="r", node="MINUTES", weight="medium", terms=("회의",))
    rs = D.DocRuleSet(conflict=D.ConflictSpec(strategy="all"), rules=(rule,))
    sig = DT.scan_doctype("회의 한 번", "D:/x/문서.hwp", rs, mk_taxonomy())
    assert len(sig.values) == 1
    assert sig.values[0]["confidence"] == pytest.approx(0.70)


#------------------------------------------------------------------
# legacy 의 신호 결합은 max 다
#------------------------------------------------------------------
def test_legacy의_결합은_max다():
    rule = D.DoctypeRule(id="r", node="MINUTES", weight="medium",
                         terms=("회의",), filename=("회의록",))
    rs = D.DocRuleSet(conflict=D.ConflictSpec(strategy="all"), rules=(rule,))
    sig = DT.scan_doctype("회의", "D:/x/회의록.hwp", rs, mk_taxonomy())
    # body(0.70) 와 name(0.60) 중 큰 값. 가산이면 0.70 을 넘었을 것이다.
    assert sig.values[0]["confidence"] == pytest.approx(0.70)


#------------------------------------------------------------------
# defaults 기본값은 legacy 이며 min_distinct/min_count 는 1이다
#=> "신규 기능은 전부 옵트인"이라는 하위호환 원칙의 실체.
#------------------------------------------------------------------
def test_defaults_기본값은_하위호환이다():
    d = D.Defaults()
    assert d.scoring == "legacy"
    assert d.min_distinct == 1 and d.min_count == 1
    assert d.head_chars == 400


# ── 영문 약어 단어 경계 (실측에서 발견한 오탐) ────────────────────

#------------------------------------------------------------------
# 영문 약어는 단어 경계로만 걸린다
#=> 실측에서 'erd'(ERD, 개체관계도)가 "GERD"(위식도역류질환) 안에 걸려
#   의학 논문이 DB설계서로 분류됐다. 한글은 부분문자열이 맞지만 영문은 반대다.
#------------------------------------------------------------------
def test_영문약어는_단어_경계로만_걸린다():
    rule = D.DoctypeRule(id="r", node="REPORT", title_terms=("erd",))
    tax, rs = mk_taxonomy(), mk_set([D.DoctypeRule(id="r", node="REPORT",
                                                  title_terms=("erd",))])

    ng = DT.scan_doctype("Gastroesophageal Reflux Disease (GERD)", "D:/x/a.pdf", rs, tax)
    assert ng.values == ()

    ok = DT.scan_doctype("ERD 및 테이블 정의", "D:/x/a.pdf", rs, tax)
    assert "title" in ok.values[0]["from"]
    assert rule.node == "REPORT"


#------------------------------------------------------------------
# 한글 단어는 종전대로 부분문자열로 걸린다
#=> 한국어는 띄어쓰기가 없어 경계를 강제하면 대부분 못 찾는다.
#------------------------------------------------------------------
def test_한글은_부분문자열로_걸린다():
    rule = D.DoctypeRule(id="r", node="MINUTES", title_terms=("회의",))
    sig = DT.scan_doctype("3분기영업회의결과", "D:/x/a.hwp", mk_set([rule]), mk_taxonomy())
    assert "title" in sig.values[0]["from"]


#------------------------------------------------------------------
# 제목의 '자간 벌리기'를 잡는다
#=> 한국어 규정·공문은 "출 장 여 비 규 정"처럼 한 글자씩 띄어 쓴다.
#   실측 354건 중 10건이 이 관행을 썼고 전부 회수 대상 문서였다.
#------------------------------------------------------------------
def test_자간_벌리기_제목을_잡는다():
    rule = D.DoctypeRule(id="r", node="MINUTES", title_terms=("회의록",))
    sig = DT.scan_doctype("회 의 록\n일시: ...", "D:/x/a.hwp", mk_set([rule]), mk_taxonomy())
    assert "title" in sig.values[0]["from"]


#------------------------------------------------------------------
# 본문(body)에는 자간 벌리기 보정을 적용하지 않는다
#=> 문서 전체의 공백을 지우면 줄바꿈까지 붙어 단어 경계를 넘는 오탐이 생긴다.
#------------------------------------------------------------------
def test_본문에는_공백제거_보정을_쓰지_않는다():
    rule = D.DoctypeRule(id="r", node="MINUTES", terms=("회의록",), filename=("문서",))
    sig = DT.scan_doctype("앞 문장 끝. 회 의 록 이라고 띄어 씀", "D:/x/문서.hwp",
                          mk_set([rule]), mk_taxonomy())
    assert "body" not in sig.values[0]["from"]
