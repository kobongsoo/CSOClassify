#------------------------------------------------------------------
# 업무분류 판단 기준(doc_rule.yaml) 읽기·쓰기 — 순수 로직
#=> 화면(설정 ③ 판단 기준)이 YAML 을 직접 다루지 않도록, "규칙 한 개 = 표 한 줄"로
#   바꿔 주고 되돌려 주는 일만 한다. 실무 관리자에게 YAML 을 보여주지 않는 것이
#   이 모듈의 존재 이유다(UI/UX 설계서 10-1).
#
#   [표 한 줄의 뜻]
#     이 분류로(node) ← 이 말이 나오면(terms) · 파일 이름에 이 말이 있으면(filename)
#                      · 이 폴더 안이면(paths) · 단, 이 말이 있으면 제외(exclude)
#
#   [손대지 않는 것] conflict 같은 최상위 값과, 표에 없는 필드(weight 등)는
#   읽은 그대로 보존한다. 화면이 모르는 값을 조용히 지우면 안 되기 때문이다.
#------------------------------------------------------------------

import os
import sys
import shutil

import yaml


# doc_rule.yaml 맨 위에 남길 안내 두 줄. 이 파일은 화면이 저장할 때마다 통째로
# 다시 쓰여 주석이 사라지므로, 설명 본문은 본보기 파일에 두고 여기서는 '어디를
# 보면 되는지'만 가리킨다.


# 본보기에는 있지만 규칙 파일(doc_rule.yaml)에는 넣지 않는 섹션 —
# 규칙을 "만들 때" 쓰는 값이라 만들어진 결과물에 남길 이유가 없다.



#------------------------------------------------------------------
# 판단 기준 본보기(doc_rule_template.yaml) 읽기
#=> doc_rule.yaml 의 '머리 부분'(version·industry·conflict·defaults·embed)을
#   어떤 값으로 시작할지 적어 둔 파일이다. 규칙 파일 옆에 두는 것이 규약이라
#   따로 경로를 받지 않고 형제 파일을 찾는다(doc_synonyms.yaml 과 같은 방식).
#
#   [왜 파일로 두나] 이 값들을 코드에 박아 두면, 회사마다 다른 기본값을 주려고
#   할 때마다 프로그램을 고쳐야 한다. 또 doc_rule.yaml 은 화면이 저장할 때마다
#   통째로 다시 쓰여 주석이 사라지는데, 값의 뜻과 고른 이유는 남아 있어야 한다.
#
# -in: doc_rules_path = doc_rule.yaml 경로(같은 폴더에서 본보기를 찾는다)
#
# -out: dict = 본보기의 최상위 값들(doctype_rules 는 뺀다) · 없으면 빈 dict
# -out: error = 파일 없음·파싱 실패 시 {} (본보기가 없어도 화면은 동작해야 한다)
#------------------------------------------------------------------
# doc_rule.yaml 로드
#=> 파일을 통째로 읽어 dict 로 돌려준다. 화면은 이 dict 를 그대로 들고 있다가
#   저장할 때 되돌려 준다 — 그래야 표에 없는 필드가 살아남는다.
#   파일이 없으면 본보기(doc_rule_template.yaml)로 시작하고, 파일은 있는데
#   defaults·embed 같은 섹션(또는 그 안의 항목 하나)이 빠져 있으면 본보기 값으로
#   채운다 — 빠진 채로
#   두면 엔진 기본값(legacy 채점)으로 조용히 돌아가, 화면에서 본 것과 실제
#   판정이 달라진다. 이미 적힌 값은 절대 덮어쓰지 않는다.
#
# -in: path = doc_rule.yaml 경로
#
# -out: dict = YAML 전체(+본보기로 채운 최상위 값)
# -out: error = 파싱 실패 시 예외 전파(화면이 받아 메시지로 보여준다)
#------------------------------------------------------------------
# doc_rule.yaml 저장(.bak 백업 후 덮어쓰기)
#=> 규칙을 잘못 고쳐 자동분류가 망가졌을 때 되돌릴 수 있게, 저장 전에 원본을
#   .bak 으로 복사한다(cso_rule.yaml 을 다루는 rulesedit 와 같은 방식).
#
# -in: path = doc_rule.yaml 경로
# -in: doc  = 저장할 dict
#
# -out: 없음(파일 기록)
# -out: error = 쓰기 실패 시 예외 전파
#------------------------------------------------------------------
# '말 만들기'(어휘)는 엔진 패키지가 한 벌만 갖는다
#=> 예전에는 유의어 사전 읽기·표기 만들기·분류 체계 동기화가 이 파일에만 있었다.
#   그래서 화면의 [분류 불러오기] 와 CLI 의 골격 생성이 서로 다른 결과를 냈다
#   (화면은 유의어까지 채우고 CLI 는 빈 칸만 만들었다). 구현을 엔진 쪽으로 모으고
#   화면은 그것을 그대로 가져다 쓴다 — 두 곳이 갈라질 수 없게 하려는 것이다.
#
#   [경로] 화면은 ui/ 에서 돌고 패키지는 ../src 에 있다. 규칙 미리보기
#   (rulesedit.preview_scan)가 이미 같은 방법으로 패키지를 부르고 있어, 같은
#   규약을 따른다.
#------------------------------------------------------------------
_SRC = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from csoclassify.classify.docvocab import (      # noqa: E402,F401
    DOC_SUFFIXES, GENERIC_SLOTS, SYN_CORE, SYN_LOCAL, SYN_DIR, SYN_SECTIONS,
    SYN_SUFFIXES, SYN_EXTRA, EXTRA_CELLS, SYN_HEAD_LISTS,
    SAVE_HEADER, TEMPLATE_NAME, UI_ONLY_KEYS,
    _is_doubled, _spacing_forms, _clean_suffixes, _clean_extra_terms, _clean_endings,  # 사전 점검 테스트가 직접 쓴다
    industry_of, load_doc, load_synonyms, load_template, rule_vocab, save_doc,
    split_synonyms, synonyms_of, syncable_nodes,
    title_variants,
)


#------------------------------------------------------------------
# 핵어로 쓰는 분류 표 (설계서 7장 ⓐ·ⓑ·ⓒ)
#=> "지금 어떤 분류 제목이 파일 이름 끝자리 판정에 쓰이는가"를 화면이 보여 줄 수
#   있게, 판정 엔진과 <b>같은 함수</b>로 분류마다 한 줄을 만든다. 화면이 따로
#   계산하면 표와 실제 판정이 갈린다.
#
# -in: doc = load_doc() 결과(제외 목록·노드 끄기를 여기서 읽는다)
# -in: tax = taxonomy.load_from_export() 결과(화면) 또는 Taxonomy
# -in: syn = load_synonyms() 결과
#
# -out: (rows, warnings) = rows 는 [{dc_id,path,title,use,reason,by}],
#        warnings 는 ⓒ 끄기가 무효가 된 경우의 안내
# -out: error = 없음(분류 체계가 없으면 빈 목록)
#------------------------------------------------------------------
def head_rows(doc, tax, syn=None):
    from csoclassify.classify import taxhead
    nodes = syncable_nodes(tax)
    if not nodes:
        return [], []
    return taxhead.head_status(nodes, syn,
                               (doc or {}).get("taxonomy_title_exclude") or (),
                               (doc or {}).get("taxonomy_node_off") or ())


#------------------------------------------------------------------
# 체크된 제목을 '핵어로 쓰지 않을 말'로 저장 (ⓑ)
#=> 노드 id 가 아니라 <b>말</b>로 적는다. 문제는 말에 있지 노드에 있지 않아서,
#   나중에 고객이 그 분류 이름을 바꾸면 목록에 없는 말이라 저절로 다시 쓰인다.
#   [지우지 않는 것] 지금 분류 체계에 없는 말은 그대로 둔다 — 분류가 잠시
#   빠졌다가 돌아올 수 있고, 관리자가 미리 적어 둔 말일 수도 있다.
#
# -in: doc    = load_doc() 결과(이 dict 를 제자리에서 고친다)
# -in: off    = 끄기로 체크된 제목들(띄어쓰기는 무시한다)
# -in: titles = 지금 분류 체계에 있는 제목 전부(체크가 풀린 말을 지울 판단 근거)
#
# -out: list = 저장된 제외 목록
# -out: error = 없음
#------------------------------------------------------------------
def apply_head_off(doc, off, titles):
    tight = lambda s: str(s or "").replace(" ", "").strip()          # noqa: E731
    want = [tight(w) for w in (off or []) if tight(w)]
    now = {tight(t) for t in (titles or []) if tight(t)}
    out = []
    # ① 지금 분류 체계에 없는 말은 건드리지 않는다(사람이 적어 둔 것일 수 있다).
    for w in (doc.get("taxonomy_title_exclude") or []):
        if tight(w) not in now and tight(w) not in want and tight(w) not in out:
            out.append(tight(w))
    # ② 체크된 제목을 더한다.
    for w in want:
        if w not in out:
            out.append(w)
    if out:
        doc["taxonomy_title_exclude"] = out
    else:
        doc.pop("taxonomy_title_exclude", None)
    return out


#------------------------------------------------------------------
# 이 분류에서만 끄기·되살리기 (ⓒ)
#=> 말은 괜찮은데 그 분류에서만 끄고 싶을 때 쓴다(드물다). 끌 당시 제목을 함께
#   적어 둔다 — 제목이 달라지면 이 끄기는 저절로 무효가 되고 화면이 재검토를
#   알린다. 그래야 조용히 꺼진 채로 남지 않는다.
#
# -in: doc   = load_doc() 결과(제자리에서 고친다)
# -in: dc_id = 분류 id
# -in: title = 지금 제목(끄기일 때만 쓴다). None 이면 되살리기
#
# -out: list = 저장된 노드 끄기 목록
# -out: error = 없음
#------------------------------------------------------------------
def set_node_off(doc, dc_id, title=None):
    items = [x for x in (doc.get("taxonomy_node_off") or [])
             if isinstance(x, dict) and x.get("node") != dc_id]
    if title is not None:
        items.append({"node": dc_id, "title_at_decision": str(title)})
    if items:
        doc["taxonomy_node_off"] = items
    else:
        doc.pop("taxonomy_node_off", None)
    return items
