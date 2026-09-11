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

# 본보기가 없을 때 쓸 새 규칙 기본값(본보기의 new_rule 이 이긴다).
NEW_RULE_FALLBACK = {"weight": "medium"}


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
# 규칙 목록 → 화면 표 행
#=> YAML 의 목록 필드(terms/filename/paths/exclude)를 사람이 고치기 쉬운
#   "콤마로 이어 붙인 한 줄"로 바꾼다. 분류는 DC_ID 대신 전체경로로 보여준다 —
#   화면에는 언제나 경로를, 파일에는 언제나 ID 를 쓴다(상위 설계 4-3).
#
# -in: doc = load_doc() 결과
# -in: tax = taxonomy.load_taxonomy() 결과(None 이면 경로 대신 DC_ID 를 보여준다)
#
# -out: list = [{"분류","이 말이 나오면","파일 이름에","이 폴더 안이면","제외할 말",
#                "_id","_node"}, …]
# -out: error = 없음
#------------------------------------------------------------------
def to_rows(doc, tax=None):
    import taxonomy as taxlib
    rows = []
    for r in doc.get("doctype_rules") or []:
        node = r.get("node") or ""
        rows.append({
            "분류": taxlib.path_of(tax, node) if tax else node,
            # 제목·표제부 칸을 앞에 둔다 — 판정력이 가장 센 칸부터 보이게.
            "제목에": ", ".join(r.get("title_terms") or []),
            "앞부분에": ", ".join(r.get("head_terms") or []),
            "이 말이 나오면": ", ".join(r.get("terms") or []),
            "파일 이름에": ", ".join(r.get("filename") or []),
            "이 폴더 안이면": ", ".join(r.get("paths") or []),
            "제외할 말": ", ".join(r.get("exclude") or []),
            "_id": r.get("id") or "",
            "_node": node,
        })
    return rows


#------------------------------------------------------------------
# 콤마로 이어진 한 줄 → 목록
#=> "계약서, 용역계약 , 도급계약" 을 ["계약서","용역계약","도급계약"] 로 만든다.
#   빈 칸과 중복은 버린다 — 사람이 친 값을 그대로 저장하면 규칙이 지저분해진다.
#
# -in: s = 콤마로 구분된 문자열(None 이나 빈 값 가능)
#
# -out: list = 문자열 리스트(입력이 비면 빈 리스트)
# -out: error = 없음
#------------------------------------------------------------------
def split_terms(s):
    out = []
    for part in str(s or "").split(","):
        v = part.strip()
        if v and v not in out:
            out.append(v)
    return out


#------------------------------------------------------------------
# 화면 표 행 → 규칙 목록으로 되돌리기(원본 dict 갱신)
#=> 표에서 고친 값을 doc 에 반영한다. 표에 없는 필드(weight 등)는 원래 규칙에서
#   그대로 옮겨 온다 — 화면이 모르는 값을 지우지 않기 위해서다.
#   분류(_node)가 비어 있는 줄은 버린다: 어느 분류에 대한 규칙인지 모르면 규칙이
#   될 수 없다(상위 설계 5-1-1 — node 는 규칙과 분류를 잇는 유일한 링크).
#
# -in: doc  = load_doc() 결과(이 dict 를 제자리에서 고친다)
# -in: rows = 화면 표의 행 리스트(to_rows 와 같은 열 이름 + _id/_node)
#
# -out: int = 반영된 규칙 개수
# -out: error = 없음
#------------------------------------------------------------------
def apply_rows(doc, rows):
    old_by_id = {r.get("id"): r for r in (doc.get("doctype_rules") or [])}
    new_rules = []
    for row in rows:
        node = (row.get("_node") or "").strip()
        if not node:
            continue
        rid = (row.get("_id") or "").strip() or f"dt_{node.lower()}"
        base = dict(old_by_id.get(rid) or {})     # weight 등 표에 없는 필드 보존
        base["id"] = rid
        base["node"] = node
        base["title_terms"] = split_terms(row.get("제목에"))
        base["head_terms"] = split_terms(row.get("앞부분에"))
        base["terms"] = split_terms(row.get("이 말이 나오면"))
        base["filename"] = split_terms(row.get("파일 이름에"))
        base["paths"] = split_terms(row.get("이 폴더 안이면"))
        base["exclude"] = split_terms(row.get("제외할 말"))
        # 빈 목록은 아예 적지 않는다 — 파일이 짧아야 사람이 읽는다.
        for k in ("title_terms", "head_terms", "terms", "filename", "paths", "exclude"):
            if not base[k]:
                base.pop(k)
        new_rules.append(base)
    doc["doctype_rules"] = new_rules
    return len(new_rules)


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
# 기준이 없는 분류 세기
#=> 회사 분류 체계에는 있는데 규칙이 하나도 없는 분류를 찾는다. 그런 분류로는
#   어떤 문서도 자동 제안되지 않으므로 관리자가 알아야 한다(상위 설계 4-5).
#
# -in: doc = load_doc() 결과
# -in: tax = taxonomy.load_taxonomy() 결과(None 이면 빈 목록)
#
# -out: list = 규칙이 없는 분류의 전체경로 문자열 리스트
# -out: error = 없음
#------------------------------------------------------------------
def nodes_without_rules(doc, tax):
    if not tax:
        return []
    have = {r.get("node") for r in (doc.get("doctype_rules") or [])
            if (r.get("title_terms") or r.get("head_terms")
                or r.get("terms") or r.get("filename") or r.get("paths"))}
    # 규칙을 만드는 대상만 센다 — 서랍(최상위이면서 자식이 있는 분류)은 애초에
    # 규칙을 걸지 않으므로 "기준이 없다"고 알릴 이유가 없다. 예전에는 여기서
    # 서랍까지 세어, 아무리 규칙을 채워도 "6개 분류에는 기준이 없습니다"가
    # 사라지지 않았다(= 관리자가 손쓸 수 없는 경고).
    missing = []
    for node in syncable_nodes(tax):
        if node.get("dc_id") not in have:
            missing.append(node.get("path") or node.get("dc_id"))
    return sorted(missing)


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
    NEW_RULE_FALLBACK, SAVE_HEADER, TEMPLATE_NAME, UI_ONLY_KEYS,
    _is_doubled, _spacing_forms,      # 사전 점검 테스트가 직접 쓴다
    industry_of, load_doc, load_synonyms, load_template, rule_vocab, save_doc,
    split_synonyms, synonyms_of, sync_from_taxonomy, syncable_nodes,
    title_variants,
)
