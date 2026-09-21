#------------------------------------------------------------------
# 분류체계 제목에서 '핵어' 유도하기 (설계서 7장 · 13장 D1)
#=> 한국어 복합명사는 마지막 명사가 그 말의 정체다 — "보안전송관리시스템_작업
#   계획서" 는 계획서이지 시스템이 아니다. 그래서 파일 이름의 '끝자리'에 어떤
#   분류의 제목(=핵어)이 오면, 그 분류의 약한 후보로 삼는다.
#
#   [핵어는 어디서 오나] 새 파일을 만들지 않는다. 고객 분류체계 파일
#   (doc_taxonomy.yaml)의 잎 제목이 곧 핵어이고, 거기에 core 사전의 유의어를
#   얹는다. 판정할 때마다 다시 계산하므로 고객이 제목을 바꾸면 저절로 따라간다
#   — 규칙 파일에 구워 넣지 않는다(7장 "구워 넣기는 조용히 썩는다").
#
#   [제목이 다 핵어는 아니다] 세 겹으로 거르고 기본값은 "쓴다"로 둔다.
#     ⓐ 자동 판정   — 이 파일의 judge_title(). 대부분 여기서 끝난다
#     ⓑ 말 단위 제외 — doc_rule.yaml 의 taxonomy_title_exclude(노드 id 를 적지 않는다)
#     ⓒ 노드 단위 끄기 — doc_rule.yaml 의 taxonomy_node_off. 끌 당시 제목을 함께
#                      적게 해서, 제목이 달라지면 끄기가 저절로 무효가 된다
#
#   [시험 대장] tests/evalset/taxonomy_title_check.py 가 같은 ⓐ 규칙으로 고객
#   분류체계를 채점했다(2026-09-18, 22/22 통과). 이 모듈은 그 판정기를 엔진으로
#   옮긴 것이고, 목록은 코드가 아니라 core 사전에서 온다.
#------------------------------------------------------------------

import os
import re

from . import docvocab


# 잡음 꼬리 중 '말'이 아닌 것들 — 버전·날짜·괄호 묶음·일련번호. 사전에 적을 수
# 없는 모양이라(정규식) 코드가 들고 있다. 오른쪽부터 한 겹씩 걷는다.
_TAIL_PATTERNS = (
    re.compile(r"[\s_\-.~,+]+$"),                              # 구분자
    re.compile(r"\([^()]*\)$"),                                # (안)·(웹버전)
    re.compile(r"\[[^\[\]]*\]$"),                              # [2023]
    re.compile(r"(?:ver(?:sion)?|rev|v|r)\.?\s*\d+(?:\.\d+)*$", re.IGNORECASE),  # v2·ver 1.1
    re.compile(r"#\s*\d+$"),                                   # #1
    re.compile(r"\d+(?:[.\-]\d+)*$"),                          # 날짜·숫자
)


#------------------------------------------------------------------
# 사전에서 목록 하나 꺼내기
#=> core 사전에 그 칸이 있으면 그것을, 없으면(사전 없음·옛 core) 코드 내장
#   기본값을 쓴다. 사전이 깔리지 않은 배포에서 판정이 조용히 꺼지지 않게 한다.
#
# -in: syn = docvocab.load_synonyms() 결과(None 이면 내장값)
# -in: sec = 칸 이름("broad_words"|"container_tails"|"common_endings"|"noise_tails")
#
# -out: list = 말 목록
# -out: error = 없음
#------------------------------------------------------------------
def _list_of(syn, sec):
    got = list((syn or {}).get(sec) or [])
    return got or list(docvocab.SYN_HEAD_LISTS[sec])


#------------------------------------------------------------------
# 잡음 꼬리 말 목록
#=> core 사전의 noise_tails 칸(없으면 내장 기본값). 엔진이 로드 때 한 번 꺼내
#   규칙셋에 들고 다니게 하려고 공개 함수로 둔다 — 문서마다 사전을 다시 읽으면
#   느리다.
#
# -in: syn = docvocab.load_synonyms() 결과
#
# -out: list = 잡음 꼬리 말(긴 것부터)
# -out: error = 없음
#------------------------------------------------------------------
def noise_tails(syn):
    return _list_of(syn, "noise_tails")


#------------------------------------------------------------------
# 문서종류 끝말 모으기
#=> 제목이 문서종류를 말하는지는 끝말로 가린다. core 사전의 꼬리말(tails) 열쇠와
#   띄어쓰기 끝말(suffixes)이 이미 "문서종류 명사" 목록이라 그대로 합쳐 쓴다.
#   긴 끝말이 먼저 걸려야 "규정집" 이 "규정" 보다 우선한다.
#
# -in: syn = docvocab.load_synonyms() 결과
#
# -out: list = 문서종류 끝말(긴 것부터)
# -out: error = 없음
#------------------------------------------------------------------
def doctype_endings(syn):
    words = set((syn or {}).get("tails") or {})
    words |= set((syn or {}).get(docvocab.SYN_SUFFIXES) or docvocab.DOC_SUFFIXES)
    return sorted(words, key=len, reverse=True)


#------------------------------------------------------------------
# 잡음 꼬리 걷어내기
#=> 파일명 끝에 붙는 관리용 꼬리를 오른쪽부터 더 걷을 게 없을 때까지 걷는다.
#   "제안서_최종_v2(수정본)" → "제안서". 남은 끝이 핵어 자리다.
#    1) 구분자·괄호 묶음·버전·날짜 같은 '모양' 꼬리를 정규식으로 걷는다
#    2) 사전의 잡음 꼬리 말(최종·복사본·회람용…)을 걷는다
#    3) 더 안 걷히면 멈춘다. 다 걷혀 빈 문자열이 되면 직전 값을 돌려준다
#
# -in: s     = 파일명 줄기(확장자 뺀 것) 또는 제목 줄
# -in: tails = 잡음 꼬리 말 목록(None 이면 내장 기본값)
#
# -out: str = 꼬리를 걷은 문자열
# -out: error = 없음
#------------------------------------------------------------------
def strip_noise(s, tails=None):
    s = str(s or "").strip()
    if not s:
        return ""
    words = list(tails if tails is not None else docvocab.NOISE_TAILS)
    while True:
        before = s
        for p in _TAIL_PATTERNS:
            s = p.sub("", s).strip()
        low = s.lower()
        for w in words:
            w = str(w).lower()
            # 꼬리만 걷는다 — 제목 전체가 그 말이면 걷지 않는다("초안" 자체는 남긴다)
            if w and len(low) > len(w) and low.endswith(w):
                s = s[: len(s) - len(w)].strip()
                break
        if s == before or not s:
            return s or before


#------------------------------------------------------------------
# 어떤 말이 '끝자리'에 오는가
#=> 공백을 뺀 소문자 문자열이 그 말로 끝나는지 본다. 영문 말은 앞 글자가
#   영숫자면 인정하지 않는다("preport" 가 "report" 로 걸리지 않게).
#
# -in: s     = 잡음 걷은 문자열
# -in: terms = 찾을 말 목록
#
# -out: str|None = 끝자리에 온 말(없으면 None)
# -out: error = 없음
#------------------------------------------------------------------
def head_hit(s, terms):
    hay = re.sub(r"\s+", "", str(s or "")).lower()
    if not hay:
        return None
    for t in terms or ():
        n = re.sub(r"\s+", "", str(t)).lower()
        if not n or not hay.endswith(n):
            continue
        if (n.isascii() and len(hay) > len(n)
                and hay[-len(n) - 1].isalnum() and hay[-len(n) - 1].isascii()):
            continue
        return t
    return None


#------------------------------------------------------------------
# 제목 하나를 핵어로 써도 되는지 자동 판정 (ⓐ)
#=> 위에서부터 처음 걸린 이유 하나로 거른다. 기본값은 "쓴다" 다 —
#   1차 분류 실패의 대부분은 못 잡는 것(재현율)이라, 제목을 괜히 끄는 손해가 더 크다.
#    1) 제목이 통째로 넓은 말이면 안 쓴다               (broad_exact)
#    2) 끝이 묶음 말(○○자료·○○서류)이면 안 쓴다       (container)
#    3) 끝이 문서종류 명사가 아니면 — 주제형 제목 — 안 쓴다 (not_doctype)
#    4) 가장 긴 끝말이 흔한 말(규정·지침…)이면 안 쓴다   (common_head)
#    5) 다른 분류 제목이 이 제목으로 끝나거나 같으면 모호해서 안 쓴다 (ambiguous)
#
# -in: title   = 분류 제목(띄어쓰기는 무시한다)
# -in: others  = 다른 분류들의 제목(공백 뺀 것) 목록
# -in: endings = doctype_endings() 결과
# -in: syn     = docvocab.load_synonyms() 결과(목록 출처)
#
# -out: (use, reason) = (쓸 수 있으면 True, 걸린 이유 코드 — 쓰면 "ok")
# -out: error = 없음
#------------------------------------------------------------------
def judge_title(title, others, endings, syn=None):
    t = str(title or "").replace(" ", "").strip()
    if not t:
        return False, "empty"
    if t in set(_list_of(syn, "broad_words")):
        return False, "broad_exact"
    # 앞부분이 한 글자뿐이면 묶음 판정을 하지 않는다("서류" 자체는 1)에서 걸린다)
    if any(t.endswith(c) and len(t) > len(c) for c in _list_of(syn, "container_tails")):
        return False, "container"
    # endings 는 긴 것부터라 처음 걸린 것이 가장 긴 끝말이다
    ending = next((e for e in endings if t.endswith(e)), None)
    if ending is None:
        return False, "not_doctype"
    if ending in set(_list_of(syn, "common_endings")):
        return False, "common_head"
    # 같은 핵어가 여러 잎에 걸리면 어디로 보낼지 정할 수 없다
    if any(o == t or (o.endswith(t) and o != t) for o in others):
        return False, "ambiguous"
    return True, "ok"


#------------------------------------------------------------------
# 핵어를 끌어다 쓸 분류 고르기
#=> docvocab.syncable_nodes 와 같은 기준을 axes.Taxonomy 위에서 되풀이한다 —
#   꺼 둔 분류(status≠1)와 서랍(자식이 있는 뿌리)은 뺀다. 화면의 [분류 불러오기]
#   와 판정이 같은 범위를 보게 하려는 것이다.
#
# -in: taxonomy = axes.Taxonomy(None 이면 빈 목록)
#
# -out: list = TaxonomyNode 리스트(dc_id 순)
# -out: error = 없음
#------------------------------------------------------------------
def headable_nodes(taxonomy):
    if taxonomy is None:
        return []
    active = list(taxonomy.active_nodes)
    has_kids = {n.parent for n in active if n.parent}
    return sorted((n for n in active
                   if not docvocab.is_drawer(bool(n.parent), n.dc_id in has_kids)),
                  key=lambda n: n.dc_id)


#------------------------------------------------------------------
# 분류체계 → 분류별 핵어 사전 만들기 (ⓐ+ⓑ+ⓒ)
#=> 잎 제목을 ⓐ 로 거르고, 통과한 제목과 core 사전의 유의어를 그 분류의 핵어로
#   삼는다. 파일명 전용 말(filename_only — 규정·지침 계열)은 넣지 않는다.
#   그 말들이야말로 파일명에 흔해서, 끝자리에 와도 한 분류로 보낼 수 없다.
#    1) 대상 잎을 고른다(headable_nodes)
#    2) ⓑ 말 단위 제외 목록에 있는 제목은 건너뛴다
#    3) ⓒ 노드 끄기는 '끌 당시 제목'이 지금 제목과 같을 때만 듣는다.
#       달라졌으면 끄기를 무시하고 "재검토" 경고를 남긴다
#    4) ⓐ 자동 판정을 통과한 제목만 사전에 담는다
#
# -in: taxonomy = axes.Taxonomy
# -in: syn      = docvocab.load_synonyms() 결과
# -in: excludes = ⓑ 말 단위 제외 목록(doc_rule.yaml 의 taxonomy_title_exclude)
# -in: node_off = ⓒ 노드 끄기 목록 [{"node":dc_id,"title_at_decision":제목}]
#
# -out: (lexicon, warnings) = {dc_id: {"title":제목, "heads":[말…]}}, 경고 문자열 목록
# -out: error = 없음
#------------------------------------------------------------------
def build_head_lexicon(taxonomy, syn, excludes=(), node_off=()):
    nodes = headable_nodes(taxonomy)
    if not nodes:
        return {}, []
    endings = doctype_endings(syn)
    tights = {n.dc_id: str(n.title or "").replace(" ", "").strip() for n in nodes}
    skip_words = {str(w).replace(" ", "").strip() for w in (excludes or ()) if str(w).strip()}

    warnings = []
    off = set()
    for item in (node_off or ()):
        if not isinstance(item, dict):
            continue
        dc_id = str(item.get("node") or "")
        was = str(item.get("title_at_decision") or "").replace(" ", "").strip()
        now = tights.get(dc_id)
        if now is None:
            continue   # 분류체계에 없는 노드 — 이미 다른 검증이 알린다
        if was and was != now:
            # 끄기가 저절로 무효가 되는 자리다. 조용히 계속 꺼져 있지도,
            # 조용히 다시 켜지지도 않게 사람에게 알린다.
            warnings.append(
                f"[taxonomy_node_off] {dc_id}: 끌 당시 제목과 지금 제목이 다릅니다 "
                f"— 이 끄기는 무효로 두고 핵어를 씁니다. 재검토하세요")
            continue
        off.add(dc_id)

    lexicon = {}
    for n in nodes:
        dc_id = n.dc_id
        tight = tights[dc_id]
        if not tight or tight in skip_words or dc_id in off:
            continue
        others = [t for k, t in tights.items() if k != dc_id]
        use, _why = judge_title(n.title, others, endings, syn)
        if not use:
            continue
        heads = [tight] + [w for w in docvocab.synonyms_of(tight, syn, for_filename=False)
                           if w not in skip_words]
        lexicon[dc_id] = {"title": tight, "heads": heads}
    return lexicon, warnings


#------------------------------------------------------------------
# 파일 이름의 끝자리가 어느 분류의 핵어인가
#=> 폴더를 뺀 파일 이름에서 확장자를 떼고, 잡음 꼬리를 걷은 뒤 끝자리를 본다.
#   여러 분류에 걸리면 가장 긴 말이 걸린 쪽 하나만 돌려준다 — 짧은 말이
#   긴 말을 이기면 "결과보고서" 가 "보고서" 분류로 가 버린다.
#
# -in: file    = 파일 경로
# -in: lexicon = build_head_lexicon() 결과
# -in: tails   = 잡음 꼬리 말 목록(None·빈 목록이면 내장 기본값)
#
# -out: (dc_id, 걸린 말, 분류 제목) 또는 None
# -out: error = 없음
#------------------------------------------------------------------
def match_filename_head(file, lexicon, tails=None):
    if not lexicon:
        return None
    stem = os.path.splitext(os.path.basename(str(file or "")))[0]
    stem = strip_noise(stem, list(tails) if tails else None)
    if not stem:
        return None
    best = None
    for dc_id, item in sorted(lexicon.items()):
        hit = head_hit(stem, item["heads"])
        if hit and (best is None or len(str(hit)) > len(str(best[1]))):
            best = (dc_id, hit, item["title"])
    return best
