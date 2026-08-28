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
import shutil

import yaml


# doc_rule.yaml 맨 위에 남길 안내 두 줄. 이 파일은 화면이 저장할 때마다 통째로
# 다시 쓰여 주석이 사라지므로, 설명 본문은 본보기 파일에 두고 여기서는 '어디를
# 보면 되는지'만 가리킨다.
SAVE_HEADER = (
    "# 업무분류 판단 기준 — 화면(설정 ③ 판단 기준)이 저장할 때마다 다시 쓰는 파일이라\n"
    "# 주석이 남지 않는다. 각 값의 뜻과 그 값을 고른 이유는 doc_rule_template.yaml 에 있다.\n"
)


# 본보기에는 있지만 규칙 파일(doc_rule.yaml)에는 넣지 않는 섹션 —
# 규칙을 "만들 때" 쓰는 값이라 만들어진 결과물에 남길 이유가 없다.
UI_ONLY_KEYS = ("new_rule",)

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
def load_template(doc_rules_path):
    if not doc_rules_path:
        return {}
    path = os.path.join(os.path.dirname(os.path.abspath(doc_rules_path)),
                        "doc_rule_template.yaml")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}
    data.pop("doctype_rules", None)     # 규칙은 분류 체계에서 만들어진다
    return data


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
def load_doc(path):
    tpl = load_template(path)
    # new_rule 은 '규칙을 만들 때' 쓰는 값이라 규칙 파일에 남길 것이 아니다.
    # 여기서 빼지 않으면 저장할 때마다 doc_rule.yaml 에 딸려 들어간다.
    tpl = {k: v for k, v in tpl.items() if k not in UI_ONLY_KEYS}
    if not path or not os.path.isfile(path):
        doc = dict(tpl)
        doc.setdefault("conflict", "all")
        doc["doctype_rules"] = []
        return doc
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    for k, v in tpl.items():
        if isinstance(v, dict) and isinstance(doc.get(k), dict):
            # defaults·embed 처럼 묶음인 값은 '통째로' 두지 않고 한 겹 안까지 본다.
            # 그래야 본보기에 새 항목(t_low 등)을 더했을 때, 이미 defaults 를 가진
            # 기존 파일에도 그 항목이 채워진다. 이미 적힌 값은 건드리지 않는다.
            for k2, v2 in v.items():
                doc[k].setdefault(k2, v2)
        else:
            doc.setdefault(k, v)
    doc.setdefault("conflict", "all")
    doc.setdefault("doctype_rules", [])
    return doc


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
#   .bak 으로 복사한다(cso_rules.yaml 을 다루는 rulesedit 와 같은 방식).
#
# -in: path = doc_rule.yaml 경로
# -in: doc  = 저장할 dict
#
# -out: 없음(파일 기록)
# -out: error = 쓰기 실패 시 예외 전파
#------------------------------------------------------------------
def save_doc(path, doc):
    if os.path.isfile(path):
        shutil.copyfile(path, path + ".bak")
    with open(path, "w", encoding="utf-8") as f:
        # 이 파일은 저장할 때마다 통째로 다시 쓰여 주석이 남지 않는다. 값의 뜻을
        # 어디서 봐야 하는지만 두 줄로 남긴다(설명 본문은 본보기 파일에 있다).
        f.write(SAVE_HEADER)
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)


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
    missing = []
    for dc_id, node in (tax.get("by_id") or {}).items():
        if str(node.get("status", "1")) != "1":
            continue
        if dc_id not in have:
            missing.append(node.get("path") or dc_id)
    return sorted(missing)


# 문서 종류를 뜻하는 꼬리말 모음.
#=> "요구사항정의서" 처럼 붙여 쓴 분류 이름을 "요구사항 / 정의서" 로 끊어 주는 데 쓴다.
#   실제 파일 이름은 "요구사항 정의서_v1.docx" 처럼 띄어 쓰는 일이 흔해서,
#   붙임형만 넣어 두면 매칭이 통째로 빗나간다. 긴 꼬리말을 먼저 보도록 정렬해 둔다
#   (예: "요구사항정의서" 에서 "정의서" 가 "서" 보다 먼저 걸려야 한다).
DOC_SUFFIXES = (
    "회의록", "계획서", "정의서", "설계서", "명세서", "제안서",
    "보고서", "결과서", "확인서", "신청서", "승인서", "의뢰서", "합의서", "계약서",
    "계산서", "견적서", "발주서", "검수서", "내역서", "산출물", "매뉴얼", "가이드",
    "지침서", "표준서", "규정집", "일지", "일보", "대장", "양식", "규정", "지침",
    "약관", "정관", "각서", "조서", "명부", "목록", "현황",
    # 'OO서류 · OO자료 · OO문서' 처럼 서류 뭉치를 가리키는 이름도 같은 대접을 한다
    # (인증서류 → "인증 서류", 결산자료 → "결산 자료").
    "서류", "자료", "문서", "기록",
)


#------------------------------------------------------------------
# 유의어 사전은 세 겹이다 — 파일 이름 규약
#=> 업종마다 쓰는 문서 이름이 다르다(금융의 '여신심사기준서', 의료의 '감염관리
#   지침서'). 그렇다고 고객사마다 사전을 통째로 새로 쓸 수는 없다. 그래서
#   바뀌지 않는 부분과 바뀌는 부분을 파일로 갈라 둔다.
#     ① doc_synonyms.core.yaml      — 업종과 무관한 그물(꼬리말·머리말). 제품 내장
#     ② doc_synonyms.<업종>.yaml    — 업종 고유 문서 이름. 업종별로 하나씩 납품
#     ③ doc_synonyms.local.yaml     — 그 회사에만 있는 말. 사람이 고치는 자리
#   아래로 갈수록 세다(③이 언제나 이긴다). 어느 업종을 얹을지는
#   doc_rule.yaml 의 industry 값이 정한다(없으면 ①③만 쓴다).
#
#   [이름 없는 doc_synonyms.yaml 은 읽지 않는다] 겹으로 가르기 전에 쓰던 단일
#   파일이다. 쓰는 고객사가 없어 겹에서 뺐다(2026-08-26). 폴더에 남아 있어도
#   무시하므로, 예전 파일을 되살리려면 core 나 local 로 이름을 바꿔야 한다.
#------------------------------------------------------------------
SYN_CORE = "doc_synonyms.core.yaml"
SYN_LOCAL = "doc_synonyms.local.yaml"
# 사전은 정책 폴더의 synonyms/ 하위에 모아 둔다. 규칙 파일 옆에 아홉 개가
# 흩어져 있으면 어느 것이 규칙이고 어느 것이 사전인지 눈으로 구분되지 않고,
# 배포할 때도 폴더 하나만 옮기면 되도록 하려는 것이다.
SYN_DIR = "synonyms"
SYN_SECTIONS = ("aliases", "filename_only", "excludes", "tails", "heads")
# 업종 이름은 파일 이름에 그대로 들어간다. 경로로 새어 나갈 수 있는 글자를 막는다.
PATH_CHARS = frozenset(["/", "\\", ".", ":"])


#------------------------------------------------------------------
# doc_rule.yaml 에 적힌 업종 읽기
#=> 어느 업종 사전을 얹을지는 규칙 파일이 정한다. 사전 쪽에 적으면 사전을
#   바꿔 끼울 때마다 같이 고쳐야 해서, 회사를 나타내는 규칙 파일에 둔다.
#   한 회사가 두 업종에 걸치는 일이 있어서(예: 병원의 의료 + 총무) 여러 개를
#   적을 수 있게 한다. 앞에 적은 업종이 더 세다.
#
# -in: doc_rules_path = doc_rule.yaml 경로
#
# -out: list = 업종 이름 리스트(예: ["medical", "public"]). 없으면 빈 리스트
# -out: error = 파일 없음·파싱 실패 시 [] (예외를 올리지 않는다 — 업종은 없어도 된다)
#------------------------------------------------------------------
def industry_of(doc_rules_path):
    if not doc_rules_path or not os.path.isfile(doc_rules_path):
        return []
    try:
        with open(doc_rules_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return []
    raw = data.get("industry")
    if not raw:
        return []
    # 한 개만 적었으면 문자열로 온다. 여러 개면 리스트다. 둘 다 받는다.
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    out = []
    for it in items:
        name = str(it or "").strip()
        # 파일 이름에 그대로 들어가는 값이라 경로 문자를 막는다(../ 방지).
        if name and name not in out and not set(name) & PATH_CHARS:
            out.append(name)
    return out


#------------------------------------------------------------------
# 사전 한 겹 읽기
#=> 파일 하나를 읽어 다섯 구획만 뽑아 준다. 없거나 깨졌으면 조용히 빈 값이다 —
#   사전은 없어도 되는 파일이고, 한 겹이 깨졌다고 나머지 겹까지 버릴 이유가 없다.
#
# -in: path = 사전 파일 경로
#
# -out: dict = 구획 이름 → {낱말: [유의어…]} (읽지 못했으면 빈 dict)
# -out: error = 파일 없음·파싱 실패 시 {} (예외를 올리지 않는다)
#------------------------------------------------------------------
#------------------------------------------------------------------
# 사전 파일 한 개의 경로 정하기
#=> synonyms/ 하위를 먼저 보고, 없으면 규칙 파일 옆(예전 자리)을 본다.
#   [왜 둘 다 보는가] 이미 나가 있는 배포는 사전이 규칙 파일 옆에 평평하게
#   깔려 있다. 새 자리만 보게 만들면 그 배포들은 업데이트하는 순간 사전이
#   통째로 사라진 것처럼 동작한다 — 오류도 안 나고 규칙만 조용히 나빠진다.
#
# -in: base_dir = 규칙 파일이 있는 폴더
# -in: name     = 사전 파일 이름(예: doc_synonyms.core.yaml)
#
# -out: str = 실제로 읽을 경로(둘 다 없으면 새 자리 경로를 돌려준다)
# -out: error = 없음
#------------------------------------------------------------------
def _syn_path(base_dir, name):
    new = os.path.join(base_dir, SYN_DIR, name)
    if os.path.isfile(new):
        return new
    old = os.path.join(base_dir, name)
    return old if os.path.isfile(old) else new


def _read_syn_layer(path):
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {sec: (data.get(sec) or {}) for sec in SYN_SECTIONS}


#------------------------------------------------------------------
# 사전 두 겹 합치기 — 센 쪽 말이 앞에 온다
#=> 같은 분류 이름이 두 겹에 다 있으면 지우지 않고 이어 붙인다. 순서가 곧
#   우선순위라서(앞이 먼저 쓰이고 뒤가 잘린다) 센 겹의 말을 앞에 둔다.
#   예: core 의 '보고서' 에 금융 사전의 '여신보고' 를 더하면
#       ["여신보고", …core 의 말…] 이 된다.
#
# -in: base = 약한 겹 {낱말: [유의어…]}
# -in: over = 센 겹 {낱말: [유의어…]}
#
# -out: dict = 합친 결과(중복은 앞의 것만 남긴다)
# -out: error = 없음
#------------------------------------------------------------------
def _merge_layer(base, over):
    out = dict(base or {})
    for key, words in (over or {}).items():
        merged = list(words or []) + list(out.get(key) or [])
        uniq = []
        for w in merged:
            w = str(w).strip()
            if w and w not in uniq:
                uniq.append(w)
        out[key] = uniq
    return out


#------------------------------------------------------------------
# 꼬리말·머리말을 긴 것부터 오게 다시 세우기
#=> synonyms_of() 는 위에서부터 훑다가 처음 걸린 하나만 쓰고 멈춘다. 그래서
#   "보고서" 가 "결과보고서" 보다 위에 있으면 "결과보고서" 항목은 영영 죽는다.
#   사전이 한 장일 때는 사람이 파일에서 순서를 지키면 됐지만, 여러 겹을 합치는
#   순간 파일 순서로는 보장할 수 없다(업종 사전이 core 뒤에 끼어든다).
#   그래서 합친 뒤 코드가 다시 세운다 — 긴 말이 위로.
#
#   [길이만 보면 되는 이유] 두 꼬리말이 같은 이름에 동시에 걸리려면 한쪽이
#   다른 쪽의 끝토막이어야 하고, 그러면 반드시 더 길다. 서로 상관없는 항목끼리는
#   순서가 바뀌어도 결과가 같다. 그래서 길이 내림차순 한 번이면 충분하다.
#   같은 길이끼리는 원래 순서를 지킨다(사람이 적어 둔 우선순위를 흔들지 않는다).
#
# -in: mapping = {꼬리말/머리말: [유의어…]}
#
# -out: dict = 긴 열쇠가 앞에 오도록 다시 세운 dict
# -out: error = 없음
#------------------------------------------------------------------
def _order_longest_first(mapping):
    keys = sorted((mapping or {}), key=lambda k: -len(str(k)))
    return {k: mapping[k] for k in keys}


#------------------------------------------------------------------
# 유의어 사전 읽기 — 세 겹을 찾아 합친다
#=> 분류 이름과 "같은 뜻 다른 말"(요구사항정의서 ↔ 요구사항명세서 ↔ SRS)을 적어
#   둔 사전들을 읽는다. 규칙 파일(doc_rule.yaml) 옆에 두는 것이 규약이라
#   따로 경로를 받지 않고 형제 파일들을 찾는다.
#     core → 업종 → local 순으로 읽어 뒤에 읽은 것이 이긴다.
#   한 겹도 없어도 괜찮다 — 그때는 띄어쓰기 표기만 만들고 조용히 넘어간다.
#   (없는 것이 정상 동작이지 오류가 아니다. 사전은 시작값을 넓혀 줄 뿐이다.)
#
# -in: doc_rules_path = doc_rule.yaml 경로(같은 폴더에서 사전들을 찾는다)
#
# -out: dict = {"aliases","filename_only","excludes","tails","heads"} 와
#              "path"(대표 경로 — 화면 표시용) · "layers"(실제로 읽은 파일 경로들)
#              한 겹도 못 읽었으면 빈 dict
# -out: error = 파일 없음·파싱 실패 시 그 겹만 건너뛴다(예외를 올리지 않는다)
#------------------------------------------------------------------
def load_synonyms(doc_rules_path):
    if not doc_rules_path:
        return {}
    base_dir = os.path.dirname(os.path.abspath(doc_rules_path))

    # 약한 것부터 센 것 순. 업종은 여러 개일 수 있고, 앞에 적은 업종이 더 세다.
    names = [SYN_CORE]
    names += [f"doc_synonyms.{ind}.yaml"
              for ind in reversed(industry_of(doc_rules_path))]
    names.append(SYN_LOCAL)

    merged = {sec: {} for sec in SYN_SECTIONS}
    layers = []
    for name in names:
        path = _syn_path(base_dir, name)
        layer = _read_syn_layer(path)
        if not layer:
            continue
        layers.append(path)
        for sec in SYN_SECTIONS:
            merged[sec] = _merge_layer(merged[sec], layer[sec])

    if not layers:
        return {}

    # 그물은 합친 뒤 반드시 다시 세운다 — 파일 순서로는 보장되지 않는다.
    merged["tails"] = _order_longest_first(merged["tails"])
    merged["heads"] = _order_longest_first(merged["heads"])
    # 화면에 한 줄로 보여 줄 대표 경로는 가장 센 겹(=사람이 고치는 자리)으로 둔다.
    merged["path"] = layers[-1]
    merged["layers"] = layers
    return merged


#------------------------------------------------------------------
# 한 낱말의 띄어쓰기 표기들
#=> "요구사항정의서" 하나에서 붙임형·띄어쓰기형을 만든다. 매칭이 글자 그대로의
#   부분일치라서(doctype.py) 띄어쓰기 하나만 달라도 못 찾기 때문이다.
#
# -in: word = 낱말 하나
#
# -out: list = [원래대로, (공백 뺀 형태), (꼬리말 앞에서 끊은 형태)]
# -out: error = 없음
#------------------------------------------------------------------
#------------------------------------------------------------------
# 같은 토막이 두 번 이어 붙은 말 걸러내기
#=> 꼬리말·머리말 치환은 "앞부분 + 다른 꼬리말" 로 새 말을 만든다. 그런데 이름이
#   이미 그 뜻을 품고 있으면 같은 토막이 두 번 나온다.
#     · "자재관리대장" + (대장 → 관리대장)  → "자재관리관리대장"
#     · "안전관리지침" + (안전 → 안전관리)  → "안전관리관리지침"
#   이런 말은 어떤 문서에도 없어서 규칙 한 자리만 잡아먹고, 무엇보다 관리자가
#   규칙 표에서 보면 도구를 못 믿게 된다. 사전에서 빼는 것으로는 못 막는다 —
#   '대장 → 관리대장' 은 "자재대장" 에는 꼭 필요한 올바른 유의어이기 때문이다.
#   그래서 사전이 아니라 만들어진 말 쪽에서 판정한다.
#
# -in: word = 치환으로 만들어진 낱말 하나
#
# -out: bool = True 면 같은 토막이 이어 붙은 말(버려야 한다)
# -out: error = 없음
#------------------------------------------------------------------
def _is_doubled(word):
    tight = str(word or "").replace(" ", "")
    # 두 글자짜리 토막부터 본다. 한 글자 반복("공공"·"각각")은 멀쩡한 말이라 뺀다.
    for n in range(2, len(tight) // 2 + 1):
        for i in range(len(tight) - 2 * n + 1):
            if tight[i:i + n] == tight[i + n:i + 2 * n]:
                return True
    return False


def _spacing_forms(word):
    out = [word]
    # 띄어쓰기가 이미 있으면 붙임형도 넣는다 — 반대 표기로 적힌 문서를 놓치지 않으려고.
    tight = word.replace(" ", "")
    if tight and tight != word:
        out.append(tight)
    # 붙여 쓴 이름은 꼬리말 앞에서 한 번만 끊는다. 여러 번 끊으면 "요 구 사 항"
    # 같은 이상한 말이 생기고, 그런 말은 어떤 문서에도 없어 규칙만 지저분해진다.
    for suf in DOC_SUFFIXES:
        if tight.endswith(suf) and len(tight) > len(suf) + 1:
            out.append(f"{tight[: -len(suf)]} {suf}")
            break
    return out


#------------------------------------------------------------------
# 분류 이름의 유의어 뽑기
#=> 사전을 세 가지 방법으로 훑는다. 위에서부터 확실한 순서다.
#    1) 통째 별칭 — 이름이 사전에 그대로 있으면 그 목록만 쓰고 끝낸다(가장 정확)
#    2) 꼬리말 치환 — "OO정의서" → "OO명세서"(사전에 없는 새 분류를 위한 그물)
#    3) 머리말 치환 — "요구사항OO" → "요건OO"
#    4) 파일 이름 칸에는 filename_only 의 말까지 얹는다(본문에는 너무 흔한 말)
#   2)·3)은 각각 처음 걸린 것 하나만 쓴다. 여러 개를 겹쳐 바꾸면
#   "요건명세서" 처럼 아무도 안 쓰는 말까지 만들어져 규칙만 지저분해진다.
#
# -in: title = 분류의 최하위 명칭
# -in: syn   = load_synonyms() 결과(빈 dict 면 유의어 없음)
# -in: for_filename = True 면 '파일 이름에서만 쓸 말'(filename_only)도 더한다.
#                     "규정"·"지침" 처럼 본문에는 너무 흔해서 terms 에 넣으면 아무
#                     문서나 걸리지만, 파일 이름에 있으면 꽤 확실한 말들이다
#
# -out: list = 유의어 낱말 리스트(없으면 빈 리스트)
# -out: error = 없음
#------------------------------------------------------------------
def synonyms_of(title, syn, for_filename=False):
    if not syn:
        return []
    tight = str(title or "").replace(" ", "").strip()
    if not tight:
        return []

    fname_only = (list((syn.get("filename_only") or {}).get(tight) or [])
                  if for_filename else [])

    alias = list((syn.get("aliases") or {}).get(tight) or [])
    if alias:
        # 사람이 손질해 둔 별칭이 있으면 거기서 끝낸다. 그 위에 꼬리말·머리말 치환을
        # 또 얹으면 "사내규정 → 사내내규" 같은 아무도 안 쓰는 말이 섞인다.
        return alias + fname_only

    out = []

    for tail, alts in (syn.get("tails") or {}).items():
        # 머리말이 한 글자만 남는 치환은 하지 않는다("서류" → "류서식" 같은 사고 방지).
        if tight.endswith(tail) and len(tight) > len(tail) + 1:
            head = tight[: -len(tail)]
            out += [f"{head}{a}" for a in (alts or [])]
            break

    for head, alts in (syn.get("heads") or {}).items():
        if tight.startswith(head) and len(tight) > len(head) + 1:
            rest = tight[len(head):]
            out += [f"{a}{rest}" for a in (alts or [])]
            break

    # 치환으로 만든 말만 검사한다 — filename_only 와 aliases 는 사람이 적은 말이라
    # 겹쳐 보여도 그렇게 쓰는 말일 수 있다(자동 생성물만 의심한다).
    out = [w for w in out if not _is_doubled(w)]

    return out + fname_only


#------------------------------------------------------------------
# 유의어를 '강한 말' 과 '파일명 전용 말' 로 갈라 돌려주기
#=> synonyms_of() 는 둘을 한 덩어리로 합쳐 준다. 규칙의 칸이 하나뿐이던 시절엔
#   그걸로 충분했지만, 지금은 칸이 넷(제목·표제부·본문·파일명)이라 어느 말을
#   어느 칸에 넣을지 갈라야 한다(재설계 12-4 어휘 배치 원칙).
#     · 강한 말(aliases 또는 꼬리말·머리말 치환) — 그 말만으로 문서 종류가 특정된다
#     · 파일명 전용 말(filename_only) — "규정"·"지침"처럼 본문·표제부에 흔해서
#       거기 넣으면 아무 문서나 걸린다. 제목과 파일 이름에서만 쓴다
#
# -in: title = 분류의 최하위 명칭
# -in: syn   = load_synonyms() 결과
#
# -out: (strong, name_only) = 강한 말 리스트, 파일명 전용 말 리스트
# -out: error = 없음
#------------------------------------------------------------------
def split_synonyms(title, syn):
    if not syn:
        return [], []
    tight = str(title or "").replace(" ", "").strip()
    if not tight:
        return [], []
    name_only = list((syn.get("filename_only") or {}).get(tight) or [])
    # synonyms_of(for_filename=False) 가 곧 '강한 말' 이다 — filename_only 를
    # 빼고 돌려주므로 여기서 다시 계산하지 않고 그대로 쓴다.
    return list(synonyms_of(title, syn, for_filename=False)), name_only


#------------------------------------------------------------------
# 분류 명칭 하나 → 규칙 네 칸의 시작값
#=> "요구사항정의서" 라는 분류 이름에서 제목·표제부·본문·파일명 칸에 넣을 말을
#   각각 만든다. 같은 말이라도 칸이 다르면 결과가 달라진다는 것이 이 함수의 존재
#   이유다(실측: 같은 '규정'이 제목 칸이면 회수, 표제부 칸이면 오탐).
#
#   [칸마다 다른 이유]
#     title_terms : 첫 줄만 본다 — 가장 좁고 가장 확실하다. 범용 접미어도 안전
#     head_terms  : 앞 400자를 본다 — 본문 첫 문단이 섞여 들어온다. 그래서
#                   범용 접미어("…규정에 따라")를 넣으면 오탐이 쏟아진다. 제외
#     terms       : 문서 전체를 본다 — 보조 증거라 단독으로는 라벨을 못 만든다
#     filename    : 파일 이름만 본다 — 우연 일치가 드물어 범용 접미어도 안전
#
# -in: title = 분류의 최하위 명칭
# -in: syn   = load_synonyms() 결과(None 이면 띄어쓰기 표기만)
# -in: limit = 칸 하나에 넣을 최대 개수(기본 10)
#
# -out: dict = {"title_terms","head_terms","terms","filename"} 각각 리스트
# -out: error = 없음
#------------------------------------------------------------------
# 범용 접미어(filename_only)에 칸마다 떼어 두는 자리 수.
# 6 인 이유 — 실제 사전에서 한 분류의 filename_only 가 가장 많은 경우가 6개다
# (매뉴얼: 가이드·manual·guide·reference·user's guide·users guide).
GENERIC_SLOTS = 6


def rule_vocab(title, syn=None, limit=10):
    base = str(title or "").strip()
    if not base:
        return {"title_terms": [], "head_terms": [], "terms": [],
                "filename": [], "exclude": []}

    strong_syn, name_only = split_synonyms(base, syn)
    # 이름 그대로 + 띄어쓰기 표기가 언제나 앞. 잘릴 때 확실한 것부터 남는다.
    strong = _spacing_forms(base) + strong_syn

    def cut(words, cap):
        uniq = []
        for w in words:
            w = str(w).strip()
            if w and w not in uniq:
                uniq.append(w)
        return uniq[:cap]

    #--------------------------------------------------------------
    # 강한 말 + 범용어를 한 칸에 담되, 범용어 자리를 먼저 떼어 둔다
    #=> 예전에는 범용어를 그냥 뒤에 붙였다. 그러면 유의어가 많은 분류에서
    #   개수 제한에 걸려 범용어가 통째로 잘려 나간다. 실측(검증셋 150건)에서
    #   '매뉴얼'의 '가이드'·'manual'·'reference' 가 그렇게 빠져 있었고, 그
    #   세 말을 되살리자 매뉴얼 회수가 18건 늘었다. 잘려야 할 것은 확실도가
    #   낮은 유의어 쪽이지 범용어가 아니다.
    #--------------------------------------------------------------
    def blend(strong_words, generic_words, cap):
        # 범용어는 강한 말과 자리를 다투지 않는다 — 제 몫을 따로 갖는다.
        # 한 칸에서 나눠 쓰게 하면 둘 중 하나는 반드시 잘리는데, 어느 쪽이
        # 잘려도 손해다(강한 말이 잘리면 '메뉴얼' 같은 표기 변형을 놓치고,
        # 범용어가 잘리면 '가이드'·'manual' 로 잡던 문서를 통째로 놓친다).
        return cut(cut(strong_words, cap) + cut(generic_words, GENERIC_SLOTS),
                   cap + GENERIC_SLOTS)

    fname = blend(strong, name_only, limit + 6)
    # 파일 이름은 공백 자리에 '_' 나 '-' 가 오는 일이 흔하다. 표기 변형이라
    # 새 어휘가 아니므로, 이미 뽑힌 말에서만 만들고 개수 제한 밖에 둔다.
    for v in list(fname):
        if " " in v:
            fname.append(v.replace(" ", "_"))
            fname.append(v.replace(" ", "-"))

    # filename_only 에 적힌 말은 '범용 접미어' 라는 뜻이다. 그런 말이 aliases 에도
    # 섞여 있을 수 있으므로(예: 사내규정의 '지침'·'준칙'), 어디에 적혔든 표제부·
    # 본문 칸에서는 빼낸다 — 이 두 칸은 본문 문장을 포함해서 오탐이 쏟아진다.
    generic = {str(w).replace(" ", "").lower() for w in name_only}
    narrow = [w for w in strong
              if str(w).replace(" ", "").lower() not in generic]

    # 제외어는 개수를 자르지 않는다 — 오탐을 막는 말이라 잘리면 그대로 오탐이 된다.
    tight = base.replace(" ", "")
    excl = list(((syn or {}).get("excludes") or {}).get(tight) or [])

    return {
        "title_terms": blend(strong, name_only, limit + 4),
        "head_terms": cut(narrow, limit),
        "terms": cut(narrow, limit),
        # 파일명 칸은 blend 로 이미 골라 놨고, 그 뒤에 붙은 것은 표기 변형뿐이다.
        # 변형은 새 어휘가 아니라 같은 말의 다른 적기라 개수 제한을 따로 둔다.
        "filename": cut(fname, 2 * (limit + 6 + GENERIC_SLOTS)),
        "exclude": cut(excl, 10 ** 6),
    }


#------------------------------------------------------------------
# 분류 최하위 명칭 → 비슷한 말 후보 만들기
#=> 관리자가 "요구사항정의서" 라는 분류를 만들었다고 해서 문서에 그 여섯 글자가
#   그대로 붙어 있으리라는 보장이 없다. 실제로는 "요구사항 정의서" 처럼 띄어
#   쓰는 일이 더 많다. 매칭은 글자 그대로의 부분일치라서(doctype.py) 띄어쓰기가
#   하나만 달라도 못 찾는다. 그래서 한 이름에서 쓸 만한 표기 몇 가지를 미리 만든다.
#    1) 이름 그대로 (예: "요구사항정의서")
#    2) 꼬리말 앞에서 한 번 끊은 형태 (예: "요구사항 정의서")
#    3) 이름에 이미 띄어쓰기가 있으면 그것을 붙인 형태 (예: "사업 계획서" → "사업계획서")
#
# -in: title = 분류의 최하위 명칭(전체경로가 아니라 마지막 마디)
#
# -in: for_filename = True 면 위의 표기(붙임형·띄어쓰기형)에 더해 파일 이름용
#                     구분자 표기('_', '-')까지 만든다. 기본 False.
#                     파일명은 "요구사항_정의서_v2.docx" 처럼 공백 자리에 밑줄을
#                     쓰는 일이 흔해서, 네 가지를 다 넣어야 놓치지 않는다
#
# -in: syn = load_synonyms() 결과(None/빈 값이면 유의어는 붙이지 않는다)
#
# -in: limit = 만들어 낼 표기의 최대 개수(기본 10). 표 한 칸에 스무 개가 들어가면
#              사람이 못 읽고, 못 읽는 규칙은 아무도 손보지 않는다.
#              잘릴 때는 뒤(=덜 확실한 유의어)부터 잘린다
#
# -out: list = 중복 없는 표기 후보 리스트(입력이 비면 빈 리스트)
# -out: error = 없음
#------------------------------------------------------------------
def title_variants(title, for_filename=False, syn=None, limit=10):
    base = str(title or "").strip()
    if not base:
        return []

    # 이름 그대로가 언제나 첫 줄. 그다음이 띄어쓰기만 다른 형태.
    out = _spacing_forms(base)

    # 유의어는 그 뒤에 붙인다 — 잘라야 할 때 확실한 것부터 남기기 위해서다.
    # 유의어 자체는 띄어쓰기 변형까지 만들지 않는다(개수만 불어난다).
    out += synonyms_of(base, syn, for_filename=for_filename)

    if for_filename:
        # 파일 이름에서는 공백 자리에 '_' 나 '-' 가 오는 일이 흔하다.
        for v in list(out):
            if " " in v:
                out.append(v.replace(" ", "_"))
                out.append(v.replace(" ", "-"))

    # 순서를 지키면서 중복만 걷어낸다 — 이름 그대로가 언제나 첫 줄에 오게 한다.
    uniq = []
    for v in out:
        v = str(v).strip()
        if v and v not in uniq:
            uniq.append(v)
    # 파일 이름 칸은 같은 말의 구분자 표기까지 담느라 길어지므로 여유를 더 준다.
    cap = limit + 6 if for_filename else limit
    return uniq[:cap]


#------------------------------------------------------------------
# 자동으로 가져올 분류 고르기
#=> 분류 체계에 있는 것을 전부 규칙으로 만들지는 않는다. 두 가지를 뺀다.
#     · 꺼 둔 분류(status=0) — 규칙을 걸어도 엔진이 비활성 처리한다(T6)
#     · 대분류(부모가 없는 뿌리) — "경영/관리" 같은 이름은 문서에 그대로 적히는
#       말이 아니라 서랍 이름이다. 그런 말을 규칙에 넣으면 아무 문서나 걸린다
#   화면에서 위에서 아래로 읽기 좋도록 전체경로 순으로 정렬해 돌려준다.
#
# -in: tax = taxonomy.load_taxonomy() 결과(None 이면 빈 목록)
#
# -out: list = 분류 노드 dict 리스트(전체경로 가나다순)
# -out: error = 없음
#------------------------------------------------------------------
def syncable_nodes(tax):
    if not tax:
        return []
    nodes = [n for n in (tax.get("by_id") or {}).values()
             if str(n.get("status", "1")) == "1" and n.get("parent")]
    nodes.sort(key=lambda n: n.get("path") or n.get("dc_id") or "")
    return nodes


#------------------------------------------------------------------
# 회사 분류 체계에서 규칙 자동으로 불러오기(동기화)
#=> 관리자가 분류를 하나씩 고르고 단어를 손으로 채우던 일을 없앤다.
#   doc_taxonomy.yaml 에 있는 사용 중인 분류를 전부 훑어, 규칙이 없는 분류에는
#   규칙을 만들어 주고, 그 분류의 최하위 명칭에서 뽑은 비슷한 말들을
#   '이 말이 나오면'·'파일 이름에' 칸의 시작값으로 넣어 준다.
#    1) 이미 있는 규칙은 순서·내용을 그대로 둔다 (사람이 채운 값이 진실이다)
#    2) 규칙은 있는데 두 칸이 다 비어 있으면 시작값만 채운다(fill_existing=True 일 때)
#       — 시작값에는 유의어 사전(doc_synonyms.core.yaml 등)의 '같은 뜻 다른 말'도 들어간다
#    3) 이미 단어가 있는 규칙에는, 원하면 '빠진 유의어만' 뒤에 덧붙인다
#       (enrich_existing=True). 사람이 적어 둔 말은 순서까지 그대로 둔다 —
#       사전을 나중에 늘렸을 때 기존 규칙도 따라올 수 있게 하는 유일한 통로다
#    4) 분류 체계에는 없는데 규칙만 남은 줄은 건드리지 않는다 —
#       지난 판정이 참조하던 분류일 수 있어서, 지우는 것은 사람이 결정할 일이다
#
# -in: doc = load_doc() 결과(이 dict 를 제자리에서 고친다)
# -in: tax = taxonomy.load_taxonomy() 결과(None 이면 아무 것도 하지 않는다)
# -in: fill_existing = True 면 '단어가 하나도 없는 기존 규칙'의 시작값도 채운다(기본 True)
# -in: enrich_existing = True 면 '이미 단어가 있는 규칙'에도 빠진 말만 덧붙인다(기본 False).
#                        지우는 일은 절대 없다
# -in: syn = load_synonyms() 결과(None 이면 유의어 없이 띄어쓰기 표기만 넣는다)
# -in: new_rule = 새로 만드는 규칙에 얹을 값(본보기의 new_rule 섹션 — weight 등).
#                 None 이면 NEW_RULE_FALLBACK(weight: medium)
#
# -out: (added, filled, enriched) = 새로 만든 규칙 수, 시작값을 채운 규칙 수,
#                                   유의어를 덧붙인 규칙 수
# -out: error = 없음
#------------------------------------------------------------------
def sync_from_taxonomy(doc, tax, fill_existing=True, enrich_existing=False, syn=None,
                       new_rule=None):
    if not tax:
        return (0, 0, 0)

    rules = doc.setdefault("doctype_rules", [])
    by_node = {r.get("node"): r for r in rules if r.get("node")}

    added = filled = enriched = 0
    for n in syncable_nodes(tax):
        dc_id = n.get("dc_id")
        title = n.get("title") or ""
        if not dc_id:
            continue
        # 네 칸(제목·표제부·본문·파일명)의 시작값을 한 번에 만든다.
        vocab = rule_vocab(title, syn=syn)
        cur = by_node.get(dc_id)
        if cur is None:
            # id·node 다음에 본보기 값(weight 등)이 오고, 그다음이 말 목록이다.
            # 사람이 규칙을 읽을 때 "어느 분류의, 얼마나 센 규칙인가"를 먼저 보고
            # 단어를 보게 되는 순서다.
            rule = {"id": f"dt_{dc_id.lower()}", "node": dc_id}
            rule.update(NEW_RULE_FALLBACK if new_rule is None else new_rule)
            rule.update({
                "title_terms": vocab["title_terms"],
                "head_terms": vocab["head_terms"],
                "terms": vocab["terms"],
                "filename": vocab["filename"],
                **({"exclude": vocab["exclude"]} if vocab["exclude"] else {}),
            })
            rules.append(rule)
            added += 1
        elif not (cur.get("title_terms") or cur.get("head_terms")
                  or cur.get("terms") or cur.get("filename")):
            # 빈 규칙 — 시작값을 그대로 넣는다.
            if fill_existing:
                for key in ("title_terms", "head_terms", "terms", "filename"):
                    cur[key] = vocab[key]
                if vocab["exclude"]:
                    cur["exclude"] = vocab["exclude"]
                filled += 1
        elif enrich_existing:
            # 이미 말이 들어 있는 규칙. 사람이 적은 것은 앞에 그대로 두고,
            # 사전이 아는 말 중 빠진 것만 뒤에 잇는다(빼는 일은 하지 않는다).
            grew = False
            for key in ("title_terms", "head_terms", "terms", "filename", "exclude"):
                have = list(cur.get(key) or [])
                more = [w for w in vocab[key] if w not in have]
                if more:
                    cur[key] = have + more
                    grew = True
            if grew:
                enriched += 1
    return (added, filled, enriched)
