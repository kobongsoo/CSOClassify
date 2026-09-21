#------------------------------------------------------------------
# UI 규칙 생성기 — 어휘 배치 테스트
#=> "분류 불러오기"(sync_from_taxonomy)가 재설계 12-4 의 어휘 배치 원칙대로
#   네 칸을 채우는지, UI 표를 거쳐 저장해도 신규 필드가 살아남는지 확인한다.
#   이 두 가지가 깨지면 UI 를 한 번 쓰는 것만으로 1·2단계 구조가 조용히 사라진다.
#------------------------------------------------------------------

import os
import sys

import pytest

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")
if UI not in sys.path:
    sys.path.insert(0, UI)

import docruleedit as DRE   # noqa: E402


#------------------------------------------------------------------
# 테스트용 유의어 사전
#=> 실제 doc_synonyms.yaml 에 의존하지 않는다. 사전 내용이 바뀌어도 이 테스트가
#   깨지면 안 되고, 반대로 배치 규칙이 바뀌면 반드시 깨져야 한다.
#
# -in: 없음
#
# -out: dict = load_synonyms() 와 같은 형태
# -out: error = 없음
#------------------------------------------------------------------
def mk_syn():
    return {
        "aliases": {
            # 일부러 범용 접미어('지침')를 aliases 에도 섞어 둔다 — 실제 사전이
            # 그렇게 돼 있고, 그래도 표제부로 새면 안 된다.
            "사내규정": ["내규", "취업규칙", "운영규정", "지침"],
        },
        "filename_only": {"사내규정": ["규정", "지침", "세칙"]},
        "tails": {},
        "heads": {},
    }


#------------------------------------------------------------------
# 범용 접미어는 제목·파일명에만 들어간다
#=> 실측: 같은 '규정'이 제목 칸이면 정확하고 표제부 칸이면 오탐이 쏟아진다.
#------------------------------------------------------------------
def test_범용_접미어는_제목과_파일명에만_들어간다():
    v = DRE.rule_vocab("사내규정", syn=mk_syn())
    assert "규정" in v["title_terms"]
    assert "규정" in v["filename"]
    assert "규정" not in v["head_terms"]
    assert "규정" not in v["terms"]


#------------------------------------------------------------------
# aliases 에 섞여 있어도 범용 접미어면 표제부에서 뺀다
#=> 사전이 사람 손으로 관리되는 이상 같은 말이 두 칸에 적히는 일은 늘 있다.
#   'filename_only 에 있으면 범용' 이라는 한 가지 기준으로 판정해야 안정적이다.
#------------------------------------------------------------------
def test_aliases에_섞인_범용어도_표제부에서_빠진다():
    v = DRE.rule_vocab("사내규정", syn=mk_syn())
    assert "지침" not in v["head_terms"]
    assert "지침" not in v["terms"]
    assert "지침" in v["title_terms"]


#------------------------------------------------------------------
# 좁은 유의어는 네 칸에 모두 들어간다
#------------------------------------------------------------------
def test_좁은_유의어는_모든_칸에_들어간다():
    v = DRE.rule_vocab("사내규정", syn=mk_syn())
    for cell in ("title_terms", "head_terms", "terms", "filename"):
        assert "취업규칙" in v[cell], cell


#------------------------------------------------------------------
# 분류 이름 그대로가 언제나 첫 줄
#=> 개수 제한에 걸려 잘릴 때 가장 확실한 말이 남아야 한다.
#------------------------------------------------------------------
def test_분류이름이_언제나_맨_앞이다():
    v = DRE.rule_vocab("사내규정", syn=mk_syn())
    for cell in ("title_terms", "head_terms", "terms", "filename"):
        assert v[cell][0] == "사내규정", cell


#------------------------------------------------------------------
# 파일명 칸에는 구분자 표기까지 만든다
#=> "요구사항 정의서" 는 파일명에서 "요구사항_정의서" 로 적히는 일이 흔하다.
#------------------------------------------------------------------
def test_파일명_칸은_구분자_표기를_포함한다():
    v = DRE.rule_vocab("사업 계획서", syn=None)
    assert "사업_계획서" in v["filename"]
    assert "사업-계획서" in v["filename"]
    # 본문 칸에는 구분자 표기를 넣지 않는다 — 문서 본문에 밑줄 표기는 안 쓴다.
    assert "사업_계획서" not in v["terms"]


#------------------------------------------------------------------
# '메뉴얼' 표기로 끝나는 이름도 띄어쓰기·구분자 표기를 받는다 (2026-09-15)
#=> 끝말 목록에 '매뉴얼' 만 있을 때는 "설치메뉴얼" 이 끊기지 않아 파일명
#   "설치_메뉴얼_v2.pdf" 를 놓쳤다. 파일명 비교는 공백만 지우고 밑줄은 그대로라서다.
#
# -in: 없음
#
# -out: 없음
# -out: error = 기대와 다르면 AssertionError
#------------------------------------------------------------------
def test_메뉴얼_표기도_구분자_표기를_받는다():
    v = DRE.rule_vocab("설치메뉴얼", syn=None)
    assert "설치 메뉴얼" in v["title_terms"]
    assert "설치_메뉴얼" in v["filename"]
    assert "설치-메뉴얼" in v["filename"]


#------------------------------------------------------------------
# 사전이 없어도 띄어쓰기 표기는 만든다
#=> doc_synonyms.yaml 이 없는 배포에서도 규칙 생성이 동작해야 한다.
#------------------------------------------------------------------
def test_사전이_없어도_동작한다():
    v = DRE.rule_vocab("요구사항정의서", syn=None)
    assert v["title_terms"] and v["head_terms"] and v["filename"]
    assert v["title_terms"][0] == "요구사항정의서"


#------------------------------------------------------------------
# 빈 이름은 빈 결과
#------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["", "   ", None])
def test_빈_이름은_빈_결과(bad):
    v = DRE.rule_vocab(bad, syn=mk_syn())
    assert v == {"title_terms": [], "head_terms": [], "terms": [],
                 "filename": [], "exclude": []}


# ── UI 표 왕복 ────────────────────────────────────────────────────

#------------------------------------------------------------------
# 표를 거쳐 저장해도 신규 필드가 살아남는다
#=> 이게 깨지면 관리자가 UI 에서 한 번 저장하는 것만으로 제목·표제부 규칙이
#   전부 사라진다 — 조용히 일어나서 알아채기 어려운 사고다.
#------------------------------------------------------------------
def test_표_왕복에서_신규필드가_보존된다():
    doc = {"doctype_rules": [{
        "id": "dt_x", "node": "DC_006_002", "weight": "high",
        "title_terms": ["규정", "취업규칙"],
        "head_terms": ["취업규칙"],
        "terms": ["취업규칙"],
        "filename": ["규정"],
        "exclude": ["규정에 따라"],
        # 표에 없는 필드도 그대로 있어야 한다.
        "head_chars": 300,
        "min_distinct": 2,
    }]}
    rows = DRE.to_rows(doc)
    assert rows[0]["제목에"] == "규정, 취업규칙"
    assert rows[0]["앞부분에"] == "취업규칙"

    DRE.apply_rows(doc, rows)
    r = doc["doctype_rules"][0]
    assert r["title_terms"] == ["규정", "취업규칙"]
    assert r["head_terms"] == ["취업규칙"]
    assert r["terms"] == ["취업규칙"]
    assert r["filename"] == ["규정"]
    assert r["weight"] == "high"
    assert r["head_chars"] == 300 and r["min_distinct"] == 2


#------------------------------------------------------------------
# 표에서 칸을 비우면 그 필드가 사라진다
#=> 사람이 일부러 지운 것을 되살리면 안 된다.
#------------------------------------------------------------------
def test_표에서_비우면_필드가_사라진다():
    doc = {"doctype_rules": [{"id": "dt_x", "node": "N", "title_terms": ["가"]}]}
    rows = DRE.to_rows(doc)
    rows[0]["제목에"] = ""
    DRE.apply_rows(doc, rows)
    assert "title_terms" not in doc["doctype_rules"][0]


#------------------------------------------------------------------
# 신규 필드만 있는 규칙도 '기준 있음'으로 센다
#=> nodes_without_rules 가 terms/filename 만 보면, 제목 규칙만 채운 분류가
#   "기준 없음"으로 잘못 경고된다.
#------------------------------------------------------------------
def test_제목규칙만_있어도_기준있음으로_센다():
    class Tax:
        def __init__(self):
            self.nodes = [{"dc_id": "A", "title": "가", "status": 1, "parent": "R"}]
    doc = {"doctype_rules": [{"id": "r", "node": "A", "title_terms": ["가"]}]}
    ids = {r.get("node") for r in doc["doctype_rules"]
           if (r.get("title_terms") or r.get("head_terms")
               or r.get("terms") or r.get("filename") or r.get("paths"))}
    assert "A" in ids


# ── 제외어 생성 ──────────────────────────────────────────────────

#------------------------------------------------------------------
# 제외어 사전을 읽어 규칙에 넣는다
#=> 한국어는 띄어쓰기가 없어 짧은 말이 더 긴 낱말에 파묻힌다
#   ('교육'이 '교육부'·'교육청' 안에서 걸린다). 실측에서 실제로 교육부
#   보도자료가 교육자료로 분류됐다.
#------------------------------------------------------------------
def test_제외어를_사전에서_가져온다():
    syn = mk_syn()
    syn["excludes"] = {"교육자료": ["교육부", "교육청"]}
    v = DRE.rule_vocab("교육자료", syn=syn)
    assert v["exclude"] == ["교육부", "교육청"]


#------------------------------------------------------------------
# 제외어는 개수 제한에 걸리지 않는다
#=> 어휘는 표가 길어지면 잘라도 되지만, 제외어가 잘리면 그대로 오탐이 된다.
#------------------------------------------------------------------
def test_제외어는_잘리지_않는다():
    syn = mk_syn()
    syn["excludes"] = {"교육자료": [f"제외{i}" for i in range(40)]}
    v = DRE.rule_vocab("교육자료", syn=syn, limit=3)
    assert len(v["exclude"]) == 40
    assert len(v["head_terms"]) <= 3


#------------------------------------------------------------------
# 제외어가 없는 분류는 exclude 키를 만들지 않는다
#=> 빈 목록을 적어 두면 규칙 파일만 길어지고 사람이 안 읽는다.
#------------------------------------------------------------------
def test_제외어_없으면_빈_리스트():
    v = DRE.rule_vocab("사내규정", syn=mk_syn())      # excludes 키 자체가 없는 사전
    assert v["exclude"] == []


#------------------------------------------------------------------
# 사전에 excludes 가 없어도 동작한다
#=> 구버전 doc_synonyms.yaml 을 쓰는 배포에서도 깨지면 안 된다.
#------------------------------------------------------------------
def test_구버전_사전에도_안전하다():
    v = DRE.rule_vocab("사내규정", syn={"aliases": {}, "filename_only": {},
                                     "tails": {}, "heads": {}})
    assert v["exclude"] == []
    assert v["title_terms"][0] == "사내규정"


#------------------------------------------------------------------
# 유의어가 아무리 많아도 범용 접미어는 잘려 나가지 않는다
#=> 실측에서 드러난 회귀다. 범용어를 목록 맨 뒤에 이어 붙여 두면 유의어가 많은
#   분류에서 개수 제한에 걸려 범용어부터 사라진다. 실제 사전에서 '매뉴얼'의
#   '가이드'·'manual'·'reference' 가 그렇게 규칙에서 빠져 있었고, 검증셋 150건
#   기준으로 매뉴얼 18건을 통째로 놓치고 있었다.
#   범용어는 파일명·제목에서 회수력이 가장 큰 말이므로 자리를 따로 가져야 한다.
#------------------------------------------------------------------
def test_유의어가_많아도_범용어는_안_잘린다():
    syn = {
        # 개수 제한(limit)을 훌쩍 넘기도록 유의어를 일부러 많이 넣는다.
        "aliases": {"매뉴얼": [f"유의어{i}" for i in range(30)]},
        "filename_only": {"매뉴얼": ["가이드", "manual", "reference"]},
        "tails": {}, "heads": {},
    }
    v = DRE.rule_vocab("매뉴얼", syn=syn, limit=5)

    # 범용어는 제목·파일명 두 칸에 반드시 남아 있어야 한다.
    for w in ("가이드", "manual", "reference"):
        assert w in v["filename"], f"파일명에서 {w} 가 잘렸다"
        assert w in v["title_terms"], f"제목에서 {w} 가 잘렸다"

    # 그러면서도 강한 말(분류 이름 그대로)이 밀려나면 안 된다.
    assert v["title_terms"][0] == "매뉴얼"
    assert v["filename"][0] == "매뉴얼"

    # 표제부·본문 칸에는 여전히 들어가지 않는다(12-4 배치 원칙).
    for w in ("가이드", "manual", "reference"):
        assert w not in v["head_terms"]
        assert w not in v["terms"]


#------------------------------------------------------------------
# 범용어가 강한 말의 자리를 빼앗지 않는다
#=> 자리를 나눠 쓰게 하면 둘 중 하나는 반드시 잘린다. 범용어에 제 몫을 따로
#   주었으므로, 유의어 쪽 개수는 범용어가 있든 없든 같아야 한다.
#------------------------------------------------------------------
def test_범용어가_유의어_자리를_빼앗지_않는다():
    aliases = {"매뉴얼": [f"유의어{i}" for i in range(30)]}
    without = DRE.rule_vocab("매뉴얼", limit=5, syn={
        "aliases": aliases, "filename_only": {}, "tails": {}, "heads": {}})
    with_gen = DRE.rule_vocab("매뉴얼", limit=5, syn={
        "aliases": aliases, "filename_only": {"매뉴얼": ["가이드", "manual"]},
        "tails": {}, "heads": {}})

    strong_only = [w for w in with_gen["title_terms"] if w not in ("가이드", "manual")]
    assert strong_only == without["title_terms"]


#------------------------------------------------------------------
# 치환이 만든 겹친 말은 버린다
#=> '대장 → 관리대장' 은 "자재대장" 에는 꼭 필요한 유의어라 사전에서 뺄 수 없다.
#   대신 이미 '관리' 를 품은 이름에 붙었을 때만 버려야 한다.
#------------------------------------------------------------------
def test_겹친_말은_버리고_멀쩡한_말은_남긴다():
    syn = {"aliases": {}, "filename_only": {}, "excludes": {},
           "tails": {"대장": ["관리대장", "명부"]}, "heads": {}}
    # 이미 '관리' 가 있는 이름 — "자재관리관리대장" 은 나오면 안 된다
    나온말 = DRE.synonyms_of("자재관리대장", syn)
    assert "자재관리관리대장" not in 나온말
    assert "자재관리명부" in 나온말          # 멀쩡한 말은 그대로 남는다
    # '관리' 가 없는 이름 — 같은 사전 항목이 여기서는 제 일을 해야 한다
    assert "자재관리대장" in DRE.synonyms_of("자재대장", syn)


#------------------------------------------------------------------
# 한 글자 반복은 겹침으로 보지 않는다
#=> "공공기관"·"각각" 같은 멀쩡한 말을 버리면 안 된다.
#------------------------------------------------------------------
def test_한글자_반복은_멀쩡한_말이다():
    assert not DRE._is_doubled("공공기관계획서")
    assert DRE._is_doubled("자재관리관리대장")


#------------------------------------------------------------------
# '서랍'의 정의 — 규칙을 만드는 곳과 경고하는 곳이 같은 기준을 쓴다
#=> 예전에는 세 곳이 제각각이었다(규칙 만들기=최상위 전부 제외, T12=자식 있으면
#   제외, 화면 경고=제외 없음). 그래서 분류체계에 "경영/관리" 하나만 있는 경우
#   규칙을 만들어 주지도 않으면서 경고만 나는, 관리자가 고칠 수 없는 상태가 됐다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_서랍은_최상위이면서_자식이_있는_노드():
    from csoclassify.classify.docvocab import is_drawer
    assert is_drawer(has_parent=False, has_children=True) is True    # 최상위 서랍
    assert is_drawer(has_parent=False, has_children=False) is False  # 자식 없는 최상위
    assert is_drawer(has_parent=True, has_children=True) is False    # 중간 노드
    assert is_drawer(has_parent=True, has_children=False) is False   # 잎


#------------------------------------------------------------------
# 자식 없는 최상위도 규칙 대상이다("경영/관리" 하나만 고른 분류체계)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_자식_없는_최상위도_규칙을_받는다():
    tax = {"by_id": {
        "R1": {"dc_id": "R1", "parent": None, "title": "경영/관리",
               "status": "1", "path": "경영/관리"},
        "R2": {"dc_id": "R2", "parent": None, "title": "법무/규정",
               "status": "1", "path": "법무/규정"},
        "R2A": {"dc_id": "R2A", "parent": "R2", "title": "계약서",
                "status": "1", "path": "법무/규정 > 계약서"},
    }}
    got = {n["dc_id"] for n in DRE.syncable_nodes(tax)}
    assert got == {"R1", "R2A"}, got     # R2 는 서랍이라 빠지고, R1 은 들어온다


# ── 분류 제목이 바뀌었을 때 알리기 (설계서 7장 ③) ──────────────────

#------------------------------------------------------------------
# 시험용 분류 체계 한 그루
#=> ui/taxonomy.load_taxonomy() 가 만드는 모양(by_id)만 흉내 낸다.
#
# -in: title = 잎 분류의 지금 제목
#
# -out: dict = {"by_id": {...}}
# -out: error = 없음
#------------------------------------------------------------------
def mk_tax(title):
    return {"by_id": {
        "ROOT": {"dc_id": "ROOT", "parent": None, "title": "기술/개발",
                 "status": "1", "path": "기술/개발"},
        "LEAF": {"dc_id": "LEAF", "parent": "ROOT", "title": title,
                 "status": "1", "path": f"기술/개발 > {title}"},
    }}


#------------------------------------------------------------------
# 불러오기가 '어느 제목에서 구웠는지' 적어 둔다
#=> 이 값이 없으면 나중에 제목이 바뀌었을 때 추측할 수밖에 없다.
#   새로 만든 규칙·빈 규칙을 채운 경우 모두 적혀야 한다.
#------------------------------------------------------------------
def test_불러오기가_구운_제목을_적어_둔다():
    nodes = [{"dc_id": "LEAF", "title": "매뉴얼", "path": "기술/개발 > 매뉴얼"}]
    doc = {"doctype_rules": []}
    DRE.sync_nodes(doc, nodes, syn=mk_syn())
    assert doc["doctype_rules"][0][DRE.FILLED_FROM] == "매뉴얼"

    # 이미 있는 빈 규칙을 채운 경우도 마찬가지다.
    doc2 = {"doctype_rules": [{"id": "dt_leaf", "node": "LEAF"}]}
    DRE.sync_nodes(doc2, nodes, fill_existing=True, syn=mk_syn())
    assert doc2["doctype_rules"][0][DRE.FILLED_FROM] == "매뉴얼"


#------------------------------------------------------------------
# 제목이 바뀌면 알린다 — 그러나 말은 지우지 않는다
#=> 적어 둔 값이 있으면 추측하지 않고 사실만 말한다.
#------------------------------------------------------------------
def test_제목이_바뀌면_표에_알림이_뜬다():
    rule = {"id": "dt_leaf", "node": "LEAF", DRE.FILLED_FROM: "매뉴얼",
            "title_terms": ["매뉴얼"], "filename": ["매뉴얼", "사용설명서"]}
    doc = {"doctype_rules": [rule]}

    # 제목이 그대로면 조용하다.
    rows = DRE.to_rows(doc, mk_tax("매뉴얼"), mk_syn())
    assert rows[0]["제목 확인"] == ""

    # 바뀌면 옛 제목을 알린다.
    rows = DRE.to_rows(doc, mk_tax("사용자가이드"), mk_syn())
    assert "매뉴얼" in rows[0]["제목 확인"]
    # 알리기만 한다 — 규칙의 말은 그대로다.
    assert doc["doctype_rules"][0]["filename"] == ["매뉴얼", "사용설명서"]


#------------------------------------------------------------------
# 그 칸이 없는 옛 규칙은 추측으로라도 알린다
#=> 이미 깔려 있는 고객 파일에는 filled_from_title 이 없다. 그때는 지금 제목에서
#   만들어질 말 목록에 없으면서 '문서종류 명사 꼴'인 말만 집는다 —
#   관리자가 적은 주제어까지 집으면 안내가 소음이 된다.
#------------------------------------------------------------------
def test_옛_규칙은_문서종류_꼴만_의심한다():
    syn = mk_syn()
    old = {"id": "dt_leaf", "node": "LEAF",
           "title_terms": ["매뉴얼"], "filename": ["매뉴얼"],
           "terms": ["설치", "환경설정"]}          # 주제어는 보지 않는다
    hint = DRE.stale_title_hint(old, "회의록", syn)
    assert hint["kind"] == "guess" and hint["words"] == ["매뉴얼"]

    # 지금 제목에서 나오는 말이면 조용하다.
    assert DRE.stale_title_hint(old, "매뉴얼", syn) is None

    # 주제형 제목(문서종류 명사가 아님)만 있는 규칙도 조용하다 — 헛경고 방지.
    topic = {"id": "dt_t", "node": "LEAF",
             "title_terms": ["복리후생"], "filename": ["복리후생"]}
    assert DRE.stale_title_hint(topic, "인사총무", syn) is None


#------------------------------------------------------------------
# 알림 칸은 표 왕복에서 규칙을 건드리지 않는다
#=> '제목 확인'은 값이 아니라 알림이다. 저장할 때 규칙에 섞여 들어가면 안 된다.
#------------------------------------------------------------------
def test_알림_칸은_저장에_섞이지_않는다():
    doc = {"doctype_rules": [{"id": "dt_leaf", "node": "LEAF",
                              DRE.FILLED_FROM: "매뉴얼", "title_terms": ["매뉴얼"]}]}
    rows = DRE.to_rows(doc, mk_tax("사용자가이드"), mk_syn())
    assert rows[0]["제목 확인"]          # 알림이 떠 있는 상태에서 저장한다
    DRE.apply_rows(doc, rows)
    r = doc["doctype_rules"][0]
    assert "제목 확인" not in r
    # 구운 제목 기록은 살아남는다 — 다음에도 같은 안내를 할 수 있어야 한다.
    assert r[DRE.FILLED_FROM] == "매뉴얼"


# ── 핵어로 쓰는 분류 표 (설계서 7장 ⓐ·ⓑ·ⓒ) ────────────────────────

#------------------------------------------------------------------
# 표는 판정 엔진과 같은 답을 보여 준다
#=> 화면이 따로 계산하면 "표에는 쓰는 중인데 실제로는 안 잡힌다"가 생긴다.
#   자동으로 걸러지는 이유도 줄마다 달라야 한다.
#------------------------------------------------------------------
def test_핵어_표는_이유까지_보여준다():
    tax = {"by_id": {
        "ROOT": {"dc_id": "ROOT", "parent": None, "title": "경영", "status": "1",
                 "path": "경영"},
        "A": {"dc_id": "A", "parent": "ROOT", "title": "회의록", "status": "1",
              "path": "경영 > 회의록"},
        "B": {"dc_id": "B", "parent": "ROOT", "title": "행사자료", "status": "1",
              "path": "경영 > 행사자료"},
        "C": {"dc_id": "C", "parent": "ROOT", "title": "복리후생", "status": "1",
              "path": "경영 > 복리후생"},
    }}
    rows, warns = DRE.head_rows({}, tax, mk_syn())
    by = {r["dc_id"]: (r["use"], r["reason"]) for r in rows}
    assert by == {"A": (True, "ok"), "B": (False, "container"),
                  "C": (False, "not_doctype")}
    assert warns == []
    # 서랍(자식이 있는 최상위)은 표에 나오지 않는다 — 규칙도 안 거는 자리다.
    assert "ROOT" not in by


#------------------------------------------------------------------
# 체크한 제목은 '말'로 저장된다 (ⓑ)
#=> 노드 id 로 적으면 제목이 바뀌어도 꺼진 채 남는다. 말로 적어야 제목이
#   바뀌었을 때 저절로 다시 쓰인다.
#------------------------------------------------------------------
def test_끄기는_노드가_아니라_말로_저장된다():
    tax = {"by_id": {
        "ROOT": {"dc_id": "ROOT", "parent": None, "title": "경영", "status": "1",
                 "path": "경영"},
        "A": {"dc_id": "A", "parent": "ROOT", "title": "회의록", "status": "1",
              "path": "경영 > 회의록"},
    }}
    doc = {}
    DRE.apply_head_off(doc, ["회의록"], ["회의록"])
    assert doc["taxonomy_title_exclude"] == ["회의록"]
    # 저장한 다음 표를 다시 그리면 '끔'으로 보인다.
    rows, _w = DRE.head_rows(doc, tax, mk_syn())
    assert (rows[0]["use"], rows[0]["reason"], rows[0]["by"]) == (False, "word_off", "word")

    # 체크를 풀면 목록에서 빠진다.
    DRE.apply_head_off(doc, [], ["회의록"])
    assert "taxonomy_title_exclude" not in doc

    # 지금 분류 체계에 없는 말은 건드리지 않는다 — 사람이 적어 둔 것일 수 있다.
    doc2 = {"taxonomy_title_exclude": ["옛분류자료"]}
    DRE.apply_head_off(doc2, ["회의록"], ["회의록"])
    assert doc2["taxonomy_title_exclude"] == ["옛분류자료", "회의록"]


#------------------------------------------------------------------
# 이 분류에서만 끄기는 제목이 바뀌면 무효가 된다 (ⓒ)
#=> 조용히 꺼진 채 남지도, 조용히 다시 켜지지도 않는다.
#------------------------------------------------------------------
def test_노드_끄기는_제목이_바뀌면_무효가_된다():
    def mk(title):
        return {"by_id": {
            "ROOT": {"dc_id": "ROOT", "parent": None, "title": "경영", "status": "1",
                     "path": "경영"},
            "A": {"dc_id": "A", "parent": "ROOT", "title": title, "status": "1",
                  "path": f"경영 > {title}"},
        }}
    doc = {}
    DRE.set_node_off(doc, "A", "회의록")
    assert doc["taxonomy_node_off"] == [{"node": "A", "title_at_decision": "회의록"}]

    rows, warns = DRE.head_rows(doc, mk("회의록"), mk_syn())
    assert (rows[0]["use"], rows[0]["by"]) == (False, "node") and warns == []

    # 제목이 바뀌면 끄기를 무시하고(=다시 쓰고) 재검토를 알린다.
    # 새 제목도 ⓐ 자동 판정은 통과해야 한다('…보고서'는 문서종류 끝말이다).
    rows2, warns2 = DRE.head_rows(doc, mk("협의결과보고서"), mk_syn())
    assert rows2[0]["use"] is True
    assert len(warns2) == 1 and "재검토" in warns2[0]

    # 되살리기는 목록에서 뺀다.
    DRE.set_node_off(doc, "A", None)
    assert "taxonomy_node_off" not in doc
