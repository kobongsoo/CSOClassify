#------------------------------------------------------------------
# 업무 분류(doctype) 축 — 실제 매칭 엔진
#=> doc_rule.yaml 의 규칙을 문서 하나에 걸어 "이 문서가 어떤 분류체계 노드에
#   해당하는가" 후보를 만들고(설계서 5장), 조상 흡수(5-2)와 축 전략(6장,
#   conflict.py)을 적용해 최종 labels.doctype 을 만든다.
#   security 축의 스캔 엔진(키워드 매칭·제외어·경로 정규화)을 그대로 재사용
#   하고(설계서 5-1 note), "여러 신호 → 등급 하나"였던 security 와 달리
#   "여러 신호 → 노드 하나"를 노드마다 반복한다는 점만 다르다.
#
#   [재설계 1단계 반영] 내용 신호를 위치별로 쪼개고 점수를 누적한다.
#     · title  : 첫 비어있지 않은 줄       — 가장 강한 증거
#     · head   : 앞 head_chars 자(표제부)  — 강한 증거
#     · form   : 서식 필드어 세트          — 강한 증거
#     · structure : 구조 정규식            — 보조(단독 채택 불가)
#     · body   : 문서 전체 terms           — 약한 증거(단독 채택 불가)
#     · name/path : 파일명·경로            — 중간 증거
#   결합은 noisy-OR(1 - Π(1-c))라 근거가 쌓일수록 점수가 오른다.
#
#   [scoring 모드] defaults.scoring 이
#     · "legacy"(기본) — 종전과 동일. body 1건이면 히트, 신호 결합은 max.
#                        재설계 14-2 의 0단계 baseline 측정을 위해 남겨 둔다
#     · "staged"       — 위 재설계 방식
#   (설계: 문서분류체계 연동 설계서 §5·6, 업무분류-내용기반-2단계-재설계 §6~8)
#------------------------------------------------------------------

import os
import re
from dataclasses import dataclass

from . import rules as R
from .conflict import resolve_doctype

# ── legacy 모드 신뢰도 (종전 값 그대로) ─────────────────────────────
# security 축의 같은 이름 상수와 값을 맞춘다. 두 축이 "같은 확신 수준은 같은
# 숫자"를 쓰게 해, 결과를 함께 볼 때 신뢰도가 서로 다른 잣대로 보이지 않게 한다.
_DT_KEYWORD_CONF = {"high": 0.85, "medium": 0.70, "low": 0.50}
_DT_PATH_CONF = {"high": 0.90, "medium": 0.70, "low": 0.50}
_DT_NAME_CONF = 0.60

# ── staged 모드 신호별 신뢰도 (재설계 8-2, 실측 전 잠정치) ───────────
# 상대 순서(title ≳ head ≳ form > path ≳ name > body ≳ structure)가 핵심이며,
# 절대값은 16장 검증셋 측정 후 재보정 대상이다.
#
# => 계산법 : 예) title(0.65) + name(0.30) → 1 - 0.35×0.70 = 0.755
# 성질: 항상 [0,1) 안에 머물고, 근거가 늘수록 오르되 포화합니다. 
# 약한 증거를 아무리 모아도 강한 증거 하나를 못 넘습니다. 
# 종전 max() 의 "근거가 쌓여도 점수 제자리" 문제를 푸는 게 목적입니다.
#
# [표 읽는 법]
#   세로줄(title/head/…) = "문서의 어디를 보고 맞혔나" — 신호원 7가지.
#   가로줄(high/medium/low) = 그 규칙에 적힌 weight. 규칙을 만든 사람이
#     "이 규칙은 얼마나 믿을 만한가"를 3단계로 고른 값이다(기본 medium).
#   칸의 숫자 = 그 신호 하나가 갖는 확신. 이 값들을 위 noisy-OR 로 합친다.
#
# [7가지 신호원 — 무엇을 어디서 보는가]
#   아래 설명의 예시로 쓸 규칙 하나(weight: medium 이라 가운데 칸이 쓰인다):
#     node: DC_계약서
#     title_terms: [계약서]        head_terms: [계약기간]   terms: [갑, 을]
#     filename: [계약]             paths: ["/계약/"]
#     form: {all_of: [갑, 을, 계약기간]}   structure: ["제\\s*\\d+\\s*조"]
#
#   title (0.65) — 문서의 '첫 비어있지 않은 줄'에 title_terms 가 있나.
#       예) 첫 줄이 "용역 계약서" → 걸림. 사람이 문서 맨 위에 일부러 적어 둔
#           이름이라 판별력이 가장 세다. "출 장 여 비 규 정" 처럼 자간을 벌려
#           놓아도 잡는다(_despace).
#       ※ .ppt/.xls/.xlsx 는 첫 줄이 제목이 아니어서 이 신호를 건너뛴다.
#
#   head (0.55) — 앞 head_chars(기본 400)자 안에 head_terms 가 있나.
#       예) 제목 줄에는 없어도 "… 계약기간: 2026-01-01 …" 이 문서 앞머리에
#           나오면 걸림. 제목 다음으로 문서 성격이 잘 드러나는 구간이다.
#
#   form (0.50) — '그 양식에만 있는 항목명'이 세트로 나오나(all_of / any_of+min_types).
#       예) 갑·을·계약기간이 모두 나오면 걸림. 회의록 본문에 "계약서"라는 말이
#           언급될 수는 있어도 이 세 항목이 함께 나오지는 않는다 — 그것이
#           종류명보다 양식이 세다고 보는 근거다.
#
#   path (0.30) — 문서가 들어 있는 '폴더 경로'에 paths 조각이 있나.
#       예) D:\사업\계약\2026\x.hwp → '/계약/' 포함 → 걸림. 폴더는 담당자가
#           옮기기도 하고 하위 문서가 통째로 물려받기도 해서 파일명만큼 믿지 않는다.
#
#   name (0.30) — 폴더를 뺀 '파일 이름'에 filename 조각이 있나.
#       예) "2026_계약_최종.hwp" → 걸림. 짧고 일부러 붙인 이름이라 우연 일치는
#           드물다. 다만 이 신호 하나만으로는 t_low(0.35)를 못 넘어 후보가 되지
#           못한다 — 파일 이름만 보고 붙인 라벨은 오탐이 잦기 때문이다.
#
#   body (0.15) — 문서 '전체'에서 terms 를 센다(min_distinct·min_count 를 넘겼을 때만).
#       예) 본문 어딘가에 '갑'·'을'이 나옴. 어느 문서에나 나올 수 있는 말이라
#           가장 약하고, 단독으로는 후보를 만들지 못한다(_WEAK_ONLY).
#
#   structure (0.20) — 어휘가 아니라 '문서 골격'을 정규식으로 잡는다.
#       예) "제1조"·"제 2 조" 같은 조문 번호가 있나. 한국어 어휘와 겹치지 않는
#           독립 축이라 form 과 잘 붙지만, 단독 판별력은 약해 보조로만 쓴다.
#
# [name·structure 의 세 칸이 같은 값인 이유]
#   표가 우연히 같은 게 아니라, 코드가 이 둘만 weight 를 보지 않고 "medium"
#   칸을 직접 집어 쓴다(_scan_rule 의 name·structure 분기). 파일명은 '이름에
#   그 말이 있다', 정규식은 '맞았다' 라는 사실 하나뿐이라 규칙의 자신감으로
#   등급을 나눌 근거가 없다. 세 칸을 같은 값으로 채워 둔 것은, 나중에 등급을
#   주기로 하면 코드 한 줄만 고치면 되도록 자리를 비워 둔 것이다.
#
# [지금 실제로 도는 신호] 현재 doc_rule.yaml 규칙들은 form·structure·paths 를
#   채우지 않아 title·head·body·name 4개만 동작한다. 나머지 셋은 규칙에 그
#   필드를 넣는 순간 이 표의 값 그대로 자동으로 살아난다.
_SIG_CONF = {
    "title":     {"high": 0.80, "medium": 0.65, "low": 0.50},
    "head":      {"high": 0.70, "medium": 0.55, "low": 0.40},
    "form":      {"high": 0.65, "medium": 0.50, "low": 0.35},
    "path":      {"high": 0.40, "medium": 0.30, "low": 0.20},
    "name":      {"high": 0.30, "medium": 0.30, "low": 0.30},
    "body":      {"high": 0.25, "medium": 0.15, "low": 0.10},
    "structure": {"high": 0.20, "medium": 0.20, "low": 0.20},
}

# 이 신호들은 단독으로 후보를 만들지 못한다(재설계 8-4). 반드시 다른 신호와
# 결합해야 한다 — 이 규칙이 없으면 신뢰도를 아무리 낮춰도 conflict: all 에서
# 약한 후보가 그대로 남아 P1(본문 단어 1건 = 확정)이 재발한다.
_WEAK_ONLY = frozenset({"structure", "body"})

# 첫 줄이 제목이 아닌 포맷 — title 신호를 건너뛴다(재설계 6-3, 실측 근거).
#   .ppt   : 첫 줄이 제목인 비율 16.7%(도형 순서가 시각 순서와 다름)
#   .xls/.xlsx : 50%(첫 행이 데이터 행이라 제목 개념이 없음)
_TITLE_UNSAFE_EXTS = frozenset({".ppt", ".xls", ".xlsx"})


#------------------------------------------------------------------
# 경로 정규화
#=> context.py 의 _norm 과 동일한 규약(역슬래시→'/', 소문자). doc_rule.yaml 의
#   paths 는 이미 load_doc_rules 가 정규화해 뒀으므로, 여기서는 매칭 대상인
#   "문서의 실제 경로"만 같은 규약으로 맞추면 된다.
#
# -in: s = 경로 문자열
#
# -out: str = 정규화된 경로
# -out: error = 없음
#------------------------------------------------------------------
def _norm_path(s):
    return (s or "").replace("\\", "/").lower()


#------------------------------------------------------------------
# 첫 비어있지 않은 줄 뽑기
#=> 재설계 6-3 의 "첫 줄 = 제목" 가설에 쓰는 값. 실측(354건)에서 개행이 없는
#   문서는 0건이었고 첫 줄 길이 중앙값은 13자, 60자 이하가 91% 였다.
#   공백만 있는 줄은 건너뛴다 — 추출기가 머리말 자리에 빈 줄을 남기는 일이 잦다.
#
# -in: text = 정제된 문서 텍스트
#
# -out: str = 첫 비어있지 않은 줄(없으면 빈 문자열)
# -out: error = 없음
#------------------------------------------------------------------
def _first_line(text):
    for line in (text or "").split("\n"):
        if line.strip():
            return line
    return ""


#------------------------------------------------------------------
# 공백 제거 사본 만들기 (제목의 '자간 벌리기' 대응)
#=> 한국어 공문·규정은 제목을 "출 장 여 비 규 정"처럼 한 글자씩 띄어 쓰는
#   관행이 있다. 이대로면 '규정' 이라는 단어가 절대 걸리지 않는다.
#   실측(354건)에서 첫 줄에 이 관행을 쓴 문서가 10건이었고, 전부 회수 대상인
#   규정·제안요청서 문서였다.
#   [적용 범위] 제목·표제부처럼 '짧은 구간'에만 쓴다. 문서 전체에 적용하면
#   줄바꿈까지 붙어 단어 경계를 넘는 오탐이 생긴다.
#
# -in: s = 원본 문자열
#
# -out: str = 모든 공백류를 제거한 문자열
# -out: error = 없음
#------------------------------------------------------------------
def _despace(s):
    return re.sub(r"\s+", "", s or "")


# 순수 ASCII 영숫자(+공백·하이픈·점)로만 이루어진 단어인지. 한글이 섞이면 False.
_ASCII_TERM = re.compile(r"^[0-9A-Za-z][0-9A-Za-z .\-_]*$")


#------------------------------------------------------------------
# 영문 약어를 단어 경계로 세기
#=> 한국어는 띄어쓰기가 없어 부분문자열 매칭이 맞지만, 영문은 정반대다.
#   'erd'(ERD, 개체관계도)를 부분문자열로 찾으면 "GERD"·"ordered"·"powered"
#   안에서 걸린다. 실측에서 실제로 의학 논문이 'DB설계서'로 분류됐다.
#   그래서 순수 ASCII 단어만 \b 경계를 붙여 센다 — 한글 단어는 종전 그대로다.
#
# -in: hay      = 검색 대상(소문자)
# -in: needle   = 찾을 ASCII 단어(소문자)
# -in: ex_spans = 제외 구간 [(start, end), ...]
#
# -out: int = 제외 구간 밖에서 단어 경계로 맞은 횟수
# -out: error = 없음 (정규식 컴파일 실패는 일어나지 않는다 — escape 하므로)
#------------------------------------------------------------------
def _count_word(hay, needle, ex_spans):
    total = 0
    for m in re.finditer(r"\b" + re.escape(needle) + r"\b", hay):
        i, j = m.span()
        if not any(s <= i and j <= e for s, e in ex_spans):
            total += 1
    return total


#------------------------------------------------------------------
# 단어 1개 세기 — 언어에 맞는 방식 고르기
#=> ASCII 단어면 경계 기반, 그 밖(한글 포함)이면 종전 부분문자열 기반.
#   security 축과 동작을 맞춰야 하는 곳은 여전히 R._count_outside 를 쓴다.
#
# -in: hay      = 검색 대상(소문자)
# -in: needle   = 찾을 단어(소문자)
# -in: ex_spans = 제외 구간
#
# -out: int = 등장 횟수
# -out: error = 없음
#------------------------------------------------------------------
def _count_term(hay, needle, ex_spans):
    if _ASCII_TERM.match(needle):
        return _count_word(hay, needle, ex_spans)
    return R._count_outside(hay, needle, ex_spans)


#------------------------------------------------------------------
# 지정 구간에서 단어 찾기 (title/head/body 공통)
#=> rules.py 의 _exclude_spans/_count_outside 를 그대로 재사용해, 제외어 구간
#   안에 든 매치는 세지 않는다(한국어는 띄어쓰기가 없어 부분문자열 오탐이 잦다).
#   "어디를 볼 것인가"만 호출자가 정하고, 세는 방식은 security 축과 완전히 같다.
#
# -in: scope   = 검색 대상 문자열(이미 잘라 낸 구간)
# -in: terms   = 찾을 단어 목록
# -in: exclude = 제외어 목록
# -in: compact = True 면 공백을 지운 사본에서도 찾아 본다(제목의 자간 벌리기 대응).
#                건수는 두 결과의 '최댓값'을 쓴다 — 합치면 같은 매치를 두 번 세게 된다
#
# -out: list[(단어, 건수)] = 걸린 단어와 건수. 없으면 빈 리스트
# -out: error = 없음
#------------------------------------------------------------------
def _find_terms(scope, terms, exclude, compact=False):
    if not terms or not scope:
        return []
    hay = scope.lower()
    ex_spans = R._exclude_spans(hay, exclude, True)

    hay_c = ex_c = None
    if compact:
        hay_c = _despace(hay)
        # 제외어도 같은 규약으로 눕혀야 "가이드라인" 같은 제외가 공백 제거
        # 사본에서 그대로 살아남는다.
        ex_c = R._exclude_spans(hay_c, [_despace(str(e)) for e in (exclude or ())], True)

    hits = []
    for term in terms:
        if not term:
            continue
        needle = str(term).lower()
        c = _count_term(hay, needle, ex_spans)
        if compact:
            # 공백 제거 사본에서는 ASCII 단어의 경계가 무너지므로(예: "user guide"
            # → "userguide") 한글 자간 벌리기 대응이 목적인 이 경로는 한글 단어에만
            # 의미가 있다. ASCII 단어는 원본 결과를 그대로 쓴다.
            if not _ASCII_TERM.match(needle):
                c = max(c, R._count_outside(hay_c, _despace(needle), ex_c))
        if c:
            hits.append((term, c))
    return hits


#------------------------------------------------------------------
# 서식 필드어 세트 판정 — DoctypeRule.form
#=> 문서종류명이 아니라 '그 양식에만 있는 항목명'의 조합을 본다(재설계 7장).
#   회의록 본문에 "계약서"가 언급될 수는 있어도 갑·을·제O조·계약기간이 함께
#   나오지는 않는다 — 이것이 양식 판별력의 근거다.
#    1) all_of 가 있으면 그 항목이 전부 있어야 한다
#    2) any_of + min_types 가 있으면 '서로 다른' 항목이 그 종수 이상이어야 한다
#    3) 둘 다 적혀 있으면 둘 다 만족해야 한다(AND)
#   탐색 범위는 문서 전체다 — 필드어는 종류명과 달리 다른 문서에 우연히
#   함께 등장하지 않으므로 범위를 좁힐 이유가 없다.
#
# -in: text    = 정제된 문서 텍스트
# -in: form    = FormSpec | None
# -in: exclude = 제외어 목록
#
# -out: (bool, list[str]) = (성립 여부, 걸린 항목 목록)
# -out: error = 없음
#------------------------------------------------------------------
def _match_form(text, form, exclude):
    if form is None or not form.usable or not text:
        return False, []

    hay = text.lower()
    ex_spans = R._exclude_spans(hay, exclude, True)

    def present(item):
        return bool(item) and _count_term(hay, str(item).lower(), ex_spans) > 0

    matched = []
    if form.all_of:
        missing = [x for x in form.all_of if not present(x)]
        if missing:
            return False, []
        matched += list(form.all_of)

    if form.any_of and form.min_types > 0:
        # '건수'가 아니라 '서로 다른 항목의 종수'를 센다 — 같은 단어가 여러 번
        # 나오는 것은 서식의 증거가 아니기 때문이다.
        found = [x for x in form.any_of if present(x)]
        if len(found) < form.min_types:
            return False, []
        matched += found

    return True, matched


#------------------------------------------------------------------
# 구조 신호 판정 — DoctypeRule.structure
#=> "제N조"·금액표·서명란처럼 어휘가 아닌 문서 골격을 정규식으로 잡는다.
#   한국어 어휘에 걸리지 않는 독립 축이라 form 과 잘 붙지만, 단독 판별력은
#   약해 보조 증거로만 쓴다(_WEAK_ONLY).
#
# -in: text     = 정제된 문서 텍스트
# -in: patterns = 정규식 문자열 목록(로드 시 T22 로 컴파일 가능함을 확인해 둠)
#
# -out: list[str] = 실제로 걸린 패턴 목록
# -out: error = 없음 (컴파일 실패 패턴은 조용히 건너뛴다 — 로드 단계에서 이미 막았다)
#------------------------------------------------------------------
def _match_structure(text, patterns):
    if not patterns or not text:
        return []
    hits = []
    for pat in patterns:
        try:
            if re.search(pat, text, re.IGNORECASE):
                hits.append(pat)
        except re.error:
            continue
    return hits


#------------------------------------------------------------------
# 규칙별 임계값 고르기
#=> 규칙에 값이 적혀 있으면 그것을, 없으면 전역 defaults 값을 쓴다.
#   "규칙이 전역을 이긴다"는 한 줄 규약을 한 곳에 모아 둔다.
#
# -in: rule     = DoctypeRule
# -in: defaults = doc_rules.Defaults
# -in: name     = 필드 이름("head_chars"|"min_distinct"|"min_count")
#
# -out: int = 적용할 값
# -out: error = 없음
#------------------------------------------------------------------
def _thr(rule, defaults, name):
    v = getattr(rule, name, None)
    return getattr(defaults, name) if v is None else v


#------------------------------------------------------------------
# noisy-OR 결합 (재설계 8-1)
#=> 독립 증거들을 합쳐 하나의 점수로 만든다. score = 1 - Π(1 - cᵢ).
#   단순 덧셈과 달리 항상 [0,1) 안에 머물고, 근거가 늘수록 단조 증가하되
#   포화한다 — 약한 증거를 아무리 모아도 강한 증거 하나를 넘지 못한다.
#   이것이 종전 max() 의 문제(근거가 쌓여도 점수가 그대로)를 푼다.
#
# -in: parts = [{"signal": 이름, "c": 신뢰도}, ...]
#
# -out: float = 결합 점수(parts 가 비면 0.0)
# -out: error = 없음
#
# => 계산법 : 예) title(0.65) + name(0.30) → 1 - 0.35×0.70 = 0.755
# 성질: 항상 [0,1) 안에 머물고, 근거가 늘수록 오르되 포화합니다. 
# 약한 증거를 아무리 모아도 강한 증거 하나를 못 넘습니다. 
# 종전 max() 의 "근거가 쌓여도 점수 제자리" 문제를 푸는 게 목적입니다.
#------------------------------------------------------------------
def _noisy_or(parts):
    remain = 1.0
    for p in parts:
        remain *= (1.0 - float(p["c"]))
    return 1.0 - remain


#------------------------------------------------------------------
# 파일명 매칭 — DoctypeRule.filename
#=> context.py 의 scan_filename 과 같은 방식(폴더를 뺀 파일명만, exclude 도
#   동일하게 적용). 파일명은 짧고 의도적으로 붙인 이름이라 우연 일치가 드물어,
#   재설계에서도 매칭 로직을 그대로 유지한다(비중만 재조정).
#
# -in: file = 파일 경로(폴더 포함)
# -in: rule = DoctypeRule
#
# -out: list[(단어, 건수)] = 파일명에서 걸린 단어들
# -out: error = 없음
#------------------------------------------------------------------
def _match_filename(file, rule):
    base = os.path.basename(file or "")
    if not base or not rule.filename:
        return []
    return _find_terms(base, rule.filename, rule.exclude, compact=True)


#------------------------------------------------------------------
# 경로 매칭 — DoctypeRule.paths
#=> context.py 의 scan_path 와 같은 부분일치 방식. security 의 path_rules 와
#   달리 "첫 매칭에서 멈추는" 정책은 여기 없다 — doctype 은 노드마다 독립적으로
#   판단하므로 이 규칙 하나가 걸리는지만 보면 된다.
#
# -in: norm_path = _norm_path() 로 정규화된 문서 경로
# -in: rule      = DoctypeRule(paths 는 load_doc_rules 가 이미 정규화해 둠)
#
# -out: list[str] = 걸린 경로 조각들
# -out: error = 없음
#------------------------------------------------------------------
def _match_paths(norm_path, rule):
    if not rule.paths:
        return []
    return [p for p in rule.paths if p in norm_path]


#------------------------------------------------------------------
# 규칙 1개 스캔 — 신호원 수집 + 점수화 (핵심)
#=> 한 규칙(=노드)에 대해 모든 신호원을 확인하고, 점수·근거·구성요소를 만든다.
#   scoring 모드에 따라 결합 방식이 달라진다.
#    1) 신호원별 매칭 — title/head/form/structure/body/name/path
#    2) legacy: 종전과 동일하게 max 결합, body 1건이면 히트
#       staged: noisy-OR 결합 + 단독 채택 금지(8-4) + t_low 미만 탈락(8-3)
#    3) 근거(evidence)는 두 모드 모두 남긴다 — P6(근거 미보존) 해소는
#       계측의 전제조건이라 모드와 무관하게 필요하다
#
# -in: text      = 정제된 문서 텍스트
# -in: file      = 파일 경로
# -in: norm_path = _norm_path(file) (반복 계산을 피하려 미리 넘긴다)
# -in: rule      = DoctypeRule(active=True 인 것만 호출자가 넘겨야 함)
# -in: defaults  = doc_rules.Defaults
# -in: allow_title = False 면 title 신호를 건너뛴다(ppt/xls/xlsx, 6-3)
#
# -out: dict | None = {score, sources, evidence, parts} — 후보가 아니면 None
# -out: error = 없음
#------------------------------------------------------------------
def _scan_rule(text, file, norm_path, rule, defaults, allow_title=True):
    staged = getattr(defaults, "scoring", "legacy") == "staged"
    evidence = {}
    parts = []

    def add(signal, conf, ev):
        evidence[signal] = ev
        parts.append({"signal": signal, "c": conf})

    # ── title: 첫 비어있지 않은 줄 ──────────────────────────────
    if allow_title and rule.title_terms:
        hits = _find_terms(_first_line(text), rule.title_terms, rule.exclude, compact=True)
        if hits:
            add("title", _SIG_CONF["title"].get(rule.weight, 0.65),
                {"terms": [{"term": t, "count": c} for t, c in hits]})

    # ── head: 앞 head_chars 자 ─────────────────────────────────
    if rule.head_terms:
        n = _thr(rule, defaults, "head_chars")
        hits = _find_terms((text or "")[:n], rule.head_terms, rule.exclude, compact=True)
        if hits:
            add("head", _SIG_CONF["head"].get(rule.weight, 0.55),
                {"terms": [{"term": t, "count": c} for t, c in hits], "window": n})

    # ── form: 서식 필드어 세트 ─────────────────────────────────
    # [설계 단계 구현] 계약서(갑·을·제O조·계약기간)처럼 서식 필드어를 판정하는
    # 신호다. 재설계 7장의 설계에는 포함되지만, 현재 doc_rule.yaml 의 규칙에서
    # form 필드를 채우지 않았으므로 rule.form = None → 신호 생성 안 함.
    # 향후 규칙에 form 을 추가하면 이 코드가 자동 활용된다.
    ok, matched = _match_form(text, rule.form, rule.exclude)
    if ok:
        add("form", _SIG_CONF["form"].get(rule.weight, 0.50),
            {"matched": matched,
             "all_of": list(rule.form.all_of),
             "min_types": rule.form.min_types})

    # ── structure: 구조 정규식 ─────────────────────────────────
    # [설계 단계 구현] 제N조·금액표·서명란처럼 어휘가 아닌 문서 골격을 정규식
    # 으로 판정하는 보조 신호다(재설계 7장). 현재 doc_rule.yaml 의 규칙에서
    # structure 필드를 채우지 않았으므로 rule.structure = () → 신호 생성 안 함.
    # 향후 규칙에 structure 를 추가하면 이 코드가 자동 활용된다.
    st = _match_structure(text, rule.structure)
    if st:
        add("structure", _SIG_CONF["structure"]["medium"], {"matched": st})

    # ── body: 문서 전체 terms ──────────────────────────────────
    if rule.terms:
        hits = _find_terms(text, rule.terms, rule.exclude)
        total = sum(c for _t, c in hits)
        distinct = len(hits)
        need_d = _thr(rule, defaults, "min_distinct")
        need_c = _thr(rule, defaults, "min_count")
        if hits and distinct >= need_d and total >= need_c:
            conf = (_SIG_CONF["body"].get(rule.weight, 0.15) if staged
                    else _DT_KEYWORD_CONF.get(rule.weight, 0.6))
            add("body", conf,
                {"terms": [{"term": t, "count": c} for t, c in hits],
                 "distinct": distinct, "total": total,
                 "min_distinct": need_d, "min_count": need_c})

    # ── name: 파일명 ───────────────────────────────────────────
    nm = _match_filename(file, rule)
    if nm:
        conf = _SIG_CONF["name"]["medium"] if staged else _DT_NAME_CONF
        add("name", conf, {"terms": [t for t, _c in nm]})

    # ── path: 폴더 경로 ────────────────────────────────────────
    # [설계 단계 구현] /contract/, /board/notice/ 처럼 폴더 경로로 문서 종류를
    # 판정하는 신호다(재설계 7장). 현재 doc_rule.yaml 의 규칙에서 paths 필드를
    # 채우지 않았으므로 rule.paths = () → 신호 생성 안 함.
    # 향후 규칙에 paths 를 추가하면 이 코드가 자동 활용된다.
    ph = _match_paths(norm_path, rule)
    if ph:
        conf = (_SIG_CONF["path"].get(rule.weight, 0.30) if staged
                else _DT_PATH_CONF.get(rule.weight, 0.7))
        add("path", conf, {"matched": ph})

    if not parts:
        return None

    sources = tuple(p["signal"] for p in parts)

    if not staged:
        # legacy: 종전과 완전히 같은 계산 — 걸린 신호 중 가장 높은 값 하나.
        return {"score": max(p["c"] for p in parts), "sources": sources,
                "evidence": evidence, "parts": parts}

    # staged: 보조 신호만으로는 후보를 만들지 않는다(8-4).
    if set(sources) <= _WEAK_ONLY:
        return None

    score = _noisy_or(parts)
    # 약한 후보 임계 미만은 후보에서 뺀다(8-3). 이 컷이 없으면 conflict: all
    # 에서 약한 증거가 그대로 라벨로 남아 다중 라벨이 다시 늘어난다.
    if score < defaults.t_low:
        return None

    return {"score": score, "sources": sources, "evidence": evidence, "parts": parts}


#------------------------------------------------------------------
# 조상 흡수(설계서 5-2)
#=> 같은 가지의 조상-자손이 함께 걸리면 조상은 버리고 자손만 남긴다 — 자손의
#   path_ids 에 조상이 이미 들어 있으므로 별도 라벨로 중복 낼 이유가 없다.
#   서로 다른 가지끼리 걸린 경우(계약서+제안서)는 조상 관계가 아니므로 그대로
#   둘 다 남는다(이게 진짜 충돌이고, conflict 전략이 다루는 대상이다).
#
# -in: dc_ids    = 매칭된 dc_id 반복가능 객체
# -in: taxonomy  = axes.Taxonomy
#
# -out: set[str] = 조상이 제거된 dc_id 집합
# -out: error = taxonomy 에 없는 dc_id 가 섞이면 KeyError(path_ids 가 던짐 —
#                호출자가 taxonomy.get() 으로 이미 걸러진 dc_id만 넘겨야 함)
#------------------------------------------------------------------
def _absorb_ancestors(dc_ids, taxonomy):
    matched = set(dc_ids)
    survivors = set(matched)
    for dc_id in matched:
        ancestors = set(taxonomy.path_ids(dc_id)[:-1])   # 자기 자신 제외
        survivors -= (ancestors & matched)
    return survivors


#------------------------------------------------------------------
# doctype 축 결과(labels.doctype)
#=> 설계서 7-1 의 labels.doctype 레코드를 그대로 담는다.
#
# -필드: values    = 채택된 후보 dict 튜플. 각 항목
#                     {dc_id, path, path_ids, confidence, from, stage,
#                      evidence, score_parts}
# -필드: strategy  = 실제로 적용된 전략("all"|"top_n")
# -필드: status    = 항상 "proposed"(자동 판정은 확정하지 않는다, 설계서 7-4-1).
#                     사람이 검토 화면에서 확정하면 "confirmed"가 되지만, 그
#                     상태 전이는 이 모듈이 아니라 검토 UI(D6)의 몫이다
# -필드: truncated = top_n 으로 잘려나간 후보 수(all 이면 항상 0)
# -필드: conflicts = 계층 정합성 경고 목록(재설계 11-3). 자동으로 후보를 버리지
#                     않고 검토 큐 우선순위 신호로만 쓴다
#------------------------------------------------------------------
@dataclass(frozen=True)
class DoctypeSignal:
    values: tuple = ()
    strategy: str = "all"
    status: str = "proposed"
    truncated: int = 0
    conflicts: tuple = ()

    #------------------------------------------------------------------
    # 직렬화용 dict
    #=> 분류 레코드의 labels.doctype 칸에 넣을 순수 dict.
    #   [프라이버시] evidence 에는 규칙에 정의된 단어와 그 건수만 담는다 —
    #   문서 원문 조각(스니펫)은 절대 넣지 않는다.
    #
    # -in: 없음
    #
    # -out: dict = {values:[{dc_id,path,path_ids,confidence,from,stage,
    #               evidence,score_parts}], strategy, status, truncated, conflicts}
    # -out: error = 없음
    #------------------------------------------------------------------
    def as_dict(self):
        out = []
        for v in self.values:
            item = {
                "dc_id": v["dc_id"],
                "path": v["path"],
                "path_ids": list(v["path_ids"]),
                "confidence": round(v["confidence"], 3),
                "from": list(v["from"]),
            }
            # 아래 세 칸은 재설계 13-2 의 근거 블록. 없으면(구버전 경로로 만든
            # 후보) 키 자체를 만들지 않아, 소비자가 "안 씀"과 "비었음"을 구분할 수 있다.
            if v.get("stage"):
                item["stage"] = v["stage"]
            if v.get("evidence"):
                item["evidence"] = v["evidence"]
            if v.get("score_parts"):
                item["score_parts"] = [
                    {"signal": p["signal"], "c": round(float(p["c"]), 3)}
                    for p in v["score_parts"]
                ]
            out.append(item)
        return {
            "values": out,
            "strategy": self.strategy,
            "status": self.status,
            "truncated": self.truncated,
            "conflicts": list(self.conflicts),
        }


#------------------------------------------------------------------
# title 신호를 쓸 수 있는 포맷인지
#=> 실측(재설계 3-B-3)에서 .ppt 는 첫 줄이 제목인 비율이 16.7%,
#   .xls/.xlsx 는 50% 였다. 이 포맷들은 첫 줄 신호를 끄고 표제부(head)로
#   폴백해야 오탐을 만들지 않는다.
#
# -in: file = 파일 경로
#
# -out: bool = title 신호를 써도 되면 True
# -out: error = 없음
#------------------------------------------------------------------
def _title_allowed(file):
    return os.path.splitext(file or "")[1].lower() not in _TITLE_UNSAFE_EXTS


#------------------------------------------------------------------
# doctype 축 스캔(핵심 진입점)
#=> 문서 하나(텍스트+경로)를 doc_rule.yaml 규칙 전체에 걸어 최종 DoctypeSignal
#   을 만든다.
#    1) 활성 규칙마다 신호원을 합쳐 노드별 (점수, from, 근거) 후보를 모은다
#    2) 같은 노드를 두 규칙이 참조하면 점수가 더 높은 쪽을 남기고 from 은 합집합
#    3) 조상 흡수(5-2) — 같은 가지의 상위 노드는 자손이 있으면 버린다
#    4) conflict.resolve_doctype 으로 축 전략(all/top_n) 적용
#    5) DoctypeSignal 로 포장(status 는 항상 "proposed")
#
# -in: text          = 정제된 문서 텍스트(없으면 파일명·경로 신호만으로 판단)
# -in: file          = 파일 경로(파일명·경로 신호에 씀)
# -in: doc_rule_set  = doc_rules.load_doc_rules() 결과(DocRuleSet)
# -in: taxonomy      = axes.load_taxonomy() 결과(axes.Taxonomy) — 경로·조상 계산에 필수
#
# -out: DoctypeSignal = 매칭 결과(후보가 없으면 values=())
# -out: error = 없음 (문서에 아무 신호도 없으면 그냥 빈 결과)
#------------------------------------------------------------------
def scan_doctype(text, file, doc_rule_set, taxonomy):
    norm_path = _norm_path(file)
    defaults = getattr(doc_rule_set, "defaults", None)
    if defaults is None:
        from .doc_rules import Defaults
        defaults = Defaults()
    allow_title = _title_allowed(file)

    merged = {}   # dc_id -> {"confidence","from","evidence","parts"}

    for rule in doc_rule_set.active_rules:
        hit = _scan_rule(text, file, norm_path, rule, defaults, allow_title)
        if hit is None:
            continue
        node = taxonomy.get(rule.node)
        if node is None:
            # T5 가 로드 시점에 이미 이런 규칙을 막지만, taxonomy 없이 로드된
            # doc_rule_set(교차검증 생략)이 넘어올 수도 있으니 여기서도 방어한다.
            continue

        entry = merged.get(rule.node)
        if entry is None:
            merged[rule.node] = {
                "confidence": hit["score"], "from": set(hit["sources"]),
                "evidence": hit["evidence"], "parts": hit["parts"],
            }
        else:
            entry["from"].update(hit["sources"])
            # 같은 노드를 두 규칙이 맞혔다면 더 강하게 맞힌 쪽의 근거를 남긴다 —
            # 근거와 점수가 서로 다른 규칙에서 나오면 감사 때 설명이 안 된다.
            if hit["score"] > entry["confidence"]:
                entry["confidence"] = hit["score"]
                entry["evidence"] = hit["evidence"]
                entry["parts"] = hit["parts"]

    return _finalize(merged, taxonomy, doc_rule_set.conflict)


#------------------------------------------------------------------
# 계층 정합성 검사 (재설계 11-3)
#=> 1계층(주제)과 2계층(양식)은 서로 다른 엔진이 결정하므로 모순이 생길 수 있다.
#   조상-자손 관계가 아닌 노드가 서로 다른 1계층 가지에 걸리면 모순으로 보고
#   플래그만 남긴다 — 자동으로 하나를 버리지 않는다(자동 판정은 확정하지 않는다).
#
# -in: dc_ids   = 조상 흡수 후 살아남은 dc_id 집합
# -in: taxonomy = axes.Taxonomy
#
# -out: tuple[dict] = [{"type","dc_ids","detail"}] — 모순이 없으면 빈 튜플
# -out: error = 없음
#------------------------------------------------------------------
def _check_hierarchy(dc_ids, taxonomy):
    ids = sorted(dc_ids)
    if len(ids) < 2:
        return ()
    # 각 후보의 최상위 조상(1계층)을 모은다. 두 종류 이상이면 서로 다른
    # 업무 영역이 동시에 제안된 것 — 사람이 먼저 봐야 할 문서다.
    roots = {i: taxonomy.path_ids(i)[0] for i in ids}
    if len(set(roots.values())) < 2:
        return ()
    return ({
        "type": "cross_branch",
        "dc_ids": ids,
        "detail": "서로 다른 1계층 가지의 후보가 동시에 제안됐습니다 — 검토 우선순위 상향",
    },)


#------------------------------------------------------------------
# 노드별 병합 상태 → 최종 DoctypeSignal (scan_doctype·merge_embed_candidates 공유)
#=> "노드별 (점수, from, 근거)" 중간 상태에서 조상 흡수(5-2)와 축 전략(6장)을
#   적용해 최종 결과를 만드는 뒷단 절반이다. 규칙 스캔이든 임베딩 전파와의
#   병합이든 같은 마무리를 거치게 해, 두 곳의 로직이 어긋날 위험을 없앤다.
#
# -in: merged   = {dc_id: {"confidence","from","evidence","parts"}}
# -in: taxonomy = axes.Taxonomy
# -in: conflict = doc_rules.ConflictSpec
#
# -out: DoctypeSignal
# -out: error = 없음
#------------------------------------------------------------------
def _finalize(merged, taxonomy, conflict):
    survivors = _absorb_ancestors(merged.keys(), taxonomy)

    candidates = []
    for dc_id in survivors:
        m = merged[dc_id]
        srcs = set(m["from"])
        # stage: 이 후보가 규칙에서 왔는지, 벡터에서 왔는지, 둘 다인지.
        # 검토 화면이 "근거를 제시할 수 있는 후보"를 구분하는 데 쓴다.
        if srcs == {"embed"}:
            stage = "embed"
        elif "embed" in srcs:
            stage = "both"
        else:
            stage = "rule"
        candidates.append({
            "dc_id": dc_id,
            "path": taxonomy.path(dc_id),
            "path_ids": taxonomy.path_ids(dc_id),
            "confidence": m["confidence"],
            "from": tuple(sorted(srcs)),
            "stage": stage,
            "evidence": m.get("evidence") or {},
            "score_parts": m.get("parts") or [],
        })

    res = resolve_doctype(candidates, conflict)
    return DoctypeSignal(values=res.values, strategy=conflict.strategy,
                         status="proposed", truncated=res.truncated,
                         conflicts=_check_hierarchy(survivors, taxonomy))


#------------------------------------------------------------------
# 임베딩 전파 후보를 규칙 후보와 병합 (D7, 설계서 5-4·재설계 11-1)
#=> 1차(규칙) 스캔이 이미 만든 최종 후보(values)에, doctype 축 전파가 찾아낸
#   후보를 더한 뒤 다시 조상 흡수·축 전략을 적용한다.
#    1) 기존 values 를 노드별 상태로 되돌린다(근거 블록도 함께 살린다)
#    2) embed 후보를 더한다 — 같은 노드면 점수는 더 높은 쪽, from 에 "embed" 추가
#    3) taxonomy 에 없는 dc_id(삭제됐거나 seed 가 낡음)는 조용히 무시한다
#    4) _finalize 로 조상 흡수 + 축 전략을 다시 적용
#   [원칙] embed 는 후보를 "추가"만 한다 — 이미 있던 규칙 기반 후보를 빼앗지
#   않는다(security 축이 embed 를 상향 전용으로만 쓰는 것과 같은 정신).
#   [상한] 벡터 '단독' 후보는 embed_cap 을 넘지 못한다(재설계 9-3a) — knn_vote
#   의 득표율은 "이웃 중 몇 %인가"이지 "이 문서가 그 라벨일 확률"이 아니라서,
#   근거를 제시할 수 있는 규칙 히트보다 세지면 안 된다.
#
# -in: values       = 병합 전 최종 후보 튜플(labels.doctype.values, 없으면 빈 튜플)
# -in: embed_signal = propagate.DoctypeEmbedSignal(propagate_doctype() 반환값)
# -in: taxonomy     = axes.Taxonomy
# -in: conflict     = doc_rules.ConflictSpec
# -in: embed_cap    = 벡터 단독 후보 신뢰도 상한(None 이면 상한 없음 — 종전 동작)
#
# -out: DoctypeSignal = embed 후보까지 반영된 최종 결과(둘 다 비었으면 values=())
# -out: error = 없음
#------------------------------------------------------------------
def merge_embed_candidates(values, embed_signal, taxonomy, conflict, embed_cap=None):
    merged = {}
    for v in values or ():
        merged[v["dc_id"]] = {
            "confidence": v["confidence"],
            "from": set(v.get("from") or ()),
            "evidence": dict(v.get("evidence") or {}),
            "parts": list(v.get("score_parts") or []),
        }

    for ev in embed_signal.values:
        dc_id = ev["dc_id"]
        if taxonomy.get(dc_id) is None:
            continue   # 삭제된 노드를 가리키는 낡은 seed — 조용히 무시
        ev_block = {
            "method": embed_signal.method,
            "top_sim": round(float(embed_signal.top_sim), 3),
            "share": round(float(ev["confidence"]), 3),
        }
        entry = merged.get(dc_id)
        if entry is None:
            # 벡터 단독 후보 — 상한을 건다.
            conf = float(ev["confidence"])
            if embed_cap is not None:
                conf = min(conf, embed_cap)
            merged[dc_id] = {"confidence": conf, "from": {"embed"},
                             "evidence": {"embed": ev_block},
                             "parts": [{"signal": "embed", "c": conf}]}
        else:
            entry["from"].add("embed")
            entry["evidence"]["embed"] = ev_block
            if ev["confidence"] > entry["confidence"]:
                entry["confidence"] = float(ev["confidence"])

    if not merged:
        return DoctypeSignal(values=(), strategy=conflict.strategy,
                             status="proposed", truncated=0)
    return _finalize(merged, taxonomy, conflict)
