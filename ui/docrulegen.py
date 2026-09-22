#------------------------------------------------------------------
# 업무분류 규칙 — '자동 생성 방식' 화면 로직(순수 로직, Streamlit 없음)
#=> 새 방식에서는 화면이 doc_rule.yaml 을 직접 고치지 않는다. 사람이 고친 것은
#   doc_rule.local.yaml(회사 조정)에만 적고, 규칙은 --build-doc-rule 로 다시 만든다.
#   (설계: plan/업무분류-규칙파일-자동생성-설계-20260922.html 12장)
#
#   이 모듈이 하는 일
#    · 새 방식인지 가리기, 조정 파일 읽기·쓰기
#    · 표에 보여 줄 값 만들기 — '지금 규칙'(조정 반영)과 '자동값'(조정 없음)을 함께 계산
#    · 표·단어 제안에서 고친 칸을 조정(clear·add·remove)으로 되돌리기
#    · 핵어 끄기(ⓑ·ⓒ)를 조정 파일의 head 칸에 적기
#    · [규칙 다시 만들기] 전에 무엇이 바뀌는지 미리 보기
#   옛 방식(doc_rule.yaml 을 직접 고침) 화면 로직은 docruleedit.py 에 그대로 있다.
#------------------------------------------------------------------

import copy
import datetime
import os
import shutil
import sys

import yaml

_SRC = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from csoclassify.classify import docbuild   # noqa: E402

# 조정 파일 맨 위 안내. 화면이 저장할 때마다 통째로 다시 쓰므로 주석은 남지 않는다 —
# 조정마다 이유는 why 칸에 적는다.
LOCAL_HEADER = (
    "# 회사별 업무분류 조정 — 사람이 고치는 유일한 업무분류 파일.\n"
    "# doc_rule.yaml 은 이 파일·체계 JSON·유의어 사전으로 자동으로 만들어진다(--build-doc-rule).\n"
    "# 화면(설정 ② 판단 기준 › 업무분류)이 저장할 때마다 다시 쓰므로 주석은 남지 않는다.\n"
    "# 조정마다 이유는 why 칸에 적는다.\n"
)

# 표의 말 칸 — (조정 파일 칸 이름, 표 열 이름). 판정력이 센 칸부터.
TABLE_CELLS = (
    ("title_terms", "제목에"),
    ("head_terms", "앞부분에"),
    ("terms", "이 말이 나오면"),
    ("filename", "파일 이름에"),
    ("exclude", "제외할 말"),
)


#------------------------------------------------------------------
# 조정 파일 경로
#
# -in: rules_path = doc_rule.yaml 경로
#
# -out: str = 같은 폴더의 doc_rule.local.yaml 경로
# -out: error = 없음
#------------------------------------------------------------------
def local_path_of(rules_path):
    return os.path.join(os.path.dirname(os.path.abspath(rules_path)), docbuild.RULE_LOCAL)


#------------------------------------------------------------------
# 옛 모양 규칙 파일인가
#=> 규칙 파일이 있는데 자동 생성본이 아니고 조정 파일도 없으면 옛 모양이다(사람이 규칙
#   파일을 직접 고치고 분류체계를 doc_taxonomy.yaml 로 따로 읽던 방식). 2026-09-22 부터
#   엔진이 읽지 않으므로 화면은 [새 방식으로 바꾸기]만 보여 준다.
#   규칙 파일이 아직 없으면 옛 모양이 아니다 — 새로 만들면 된다.
#
# -in: rules_path = doc_rule.yaml 경로
#
# -out: bool
# -out: error = 없음(못 읽는 파일도 옛 모양으로 본다 — 엔진도 그 파일을 읽지 않는다)
#------------------------------------------------------------------
def is_legacy_rules(rules_path):
    if not rules_path or not os.path.isfile(rules_path):
        return False
    if os.path.isfile(local_path_of(rules_path)):
        return False
    try:
        with open(rules_path, encoding="utf-8") as f:
            return not docbuild.is_generated(yaml.safe_load(f))
    except (OSError, yaml.YAMLError):
        return True


#------------------------------------------------------------------
# 조정 파일 읽기
#
# -in: rules_path = doc_rule.yaml 경로
#
# -out: dict = 조정 내용(없으면 {})
# -out: error = 파일이 틀렸으면 docbuild.RuleLocalValidationError 전파(화면이 보여 준다)
#------------------------------------------------------------------
def load_local(rules_path):
    return docbuild.load_rule_local(local_path_of(rules_path))


#------------------------------------------------------------------
# 조정 파일 쓰기(.bak 백업)
#=> 쓰기 전에 검증한다 — 틀린 조정을 저장해 두면 다음 규칙 만들기가 통째로 멈춘다.
#   빈 칸(빈 rules·빈 head)은 적지 않아 파일을 짧게 둔다.
#
# -in: rules_path = doc_rule.yaml 경로
# -in: local      = 조정 dict
#
# -out: str = 쓴 파일 경로
# -out: error = 검증 위반 시 RuleLocalValidationError · 쓰기 실패 시 OSError
#------------------------------------------------------------------
def save_local(rules_path, local):
    data = {k: v for k, v in (local or {}).items() if v not in (None, {}, [])}
    violations = docbuild.validate_rule_local(data)
    if violations:
        raise docbuild.RuleLocalValidationError(local_path_of(rules_path), violations)
    path = local_path_of(rules_path)
    if os.path.isfile(path):
        shutil.copyfile(path, path + ".bak")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(LOCAL_HEADER)
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return path


#------------------------------------------------------------------
# 표·미리보기용 규칙 두 벌 계산
#=> 파일은 쓰지 않고 엔진의 생성 함수를 그대로 부른다.
#    · view : 지금 조정을 반영한 규칙. 단 '끈 분류'도 표에 보이도록 enabled 만 빼고 만든다
#    · base : 말 칸 조정(clear·add·remove)을 뺀 자동값 — 표에서 고친 칸을 조정으로
#             되돌릴 때의 기준
#    · real : 실제 조정(끈 분류 포함)으로 만든 규칙 — [규칙 다시 만들기] 미리 보기의 '새 규칙'
#    · warns: real 을 만들 때의 경고
#
# -in: rules_path  = doc_rule.yaml 경로(정책 폴더를 여기서 안다)
# -in: export_path = 체계 JSON 경로
# -in: local       = 조정 dict
#
# -out: (view, base, real, warns)
# -out: error = 체계 JSON·조정 오류는 엔진 예외 전파
#------------------------------------------------------------------
def views(rules_path, export_path, local):
    policy_dir = os.path.dirname(os.path.abspath(rules_path))
    local = local or {}
    shown = copy.deepcopy(local)
    for adj in (shown.get("rules") or {}).values():
        adj.pop("enabled", None)
    plain = copy.deepcopy(local)
    for adj in (plain.get("rules") or {}).values():
        for k in ("clear", "add", "remove", "enabled"):
            adj.pop(k, None)
    view, _ = docbuild.build_doc_rule(export_path, policy_dir, local=shown)
    base, _ = docbuild.build_doc_rule(export_path, policy_dir, local=plain)
    real, warns = docbuild.build_doc_rule(export_path, policy_dir, local=local)
    return view, base, real, warns


#------------------------------------------------------------------
# 규칙 목록 → 분류별 dict
#
# -in: doc = 규칙 dict
#
# -out: dict = {dc_id: 규칙}
# -out: error = 없음
#------------------------------------------------------------------
def _by_node(doc):
    return {r["node"]: r for r in (doc or {}).get("doctype_rules") or []}


#------------------------------------------------------------------
# 조정 표지 → 사람이 읽는 짧은 글
#
# -in: marks = 규칙의 local 칸(예: ["clear:terms", "add:title_terms"])
#
# -out: str = "비움: 본문 · 더함: 제목" 꼴(없으면 빈 문자열)
# -out: error = 없음
#------------------------------------------------------------------
def marks_text(marks):
    names = dict(TABLE_CELLS)
    names["terms"] = "본문"
    verbs = {"clear": "비움", "add": "더함", "remove": "뺌"}
    groups = {}
    other = []
    for m in marks or []:
        if ":" in m:
            op, cell = m.split(":", 1)
            groups.setdefault(verbs.get(op, op), []).append(names.get(cell, cell))
        else:
            other.append(m)
    parts = [f"{v}: {'·'.join(c)}" for v, c in groups.items()] + other
    return " · ".join(parts)


#------------------------------------------------------------------
# 조정 한 줄 → 사람이 읽는 짧은 글(옮기기 미리 보기용)
#=> clear·remove·add 는 marks_text 와 같은 말로, 그 밖의 칸(끄기·세기·숫자)은 이름대로.
#
# -in: adj = rules.<dc_id> 조정 dict
#
# -out: str = "비움: 앞부분에·본문 · 더함: 제목에" 꼴
# -out: error = 없음
#------------------------------------------------------------------
def adjust_text(adj):
    marks = [f"clear:{c}" for c in adj.get("clear") or []]
    marks += [f"remove:{c}" for c in (adj.get("remove") or {})]
    marks += [f"add:{c}" for c in (adj.get("add") or {})]
    if adj.get("enabled") is False:
        marks.append("끔")
    if "weight" in adj:
        marks.append(f"세기 {adj['weight']}")
    marks += [f"{k} {adj[k]}" for k in docbuild.NUM_FIELDS if k in adj]
    return marks_text(marks)


#------------------------------------------------------------------
# 표 행 만들기
#=> 분류 하나가 한 줄. 말 칸은 '지금 규칙'(조정 반영) 값을 보여 준다.
#   끈 분류도 줄은 보인다(사용 칸이 빈 상태) — 다시 켤 수 있어야 한다.
#
# -in: view  = views() 의 view
# -in: local = 조정 dict
#
# -out: list[dict] = 표 행({"사용","분류","회사 조정","이유", 말 칸…,"_node","_title"})
# -out: error = 없음
#------------------------------------------------------------------
def to_rows(view, local):
    adjust = (local or {}).get("rules") or {}
    rows = []
    for r in (view or {}).get("doctype_rules") or []:
        adj = adjust.get(r["node"]) or {}
        row = {
            "사용": adj.get("enabled") is not False,
            "분류": r.get("path") or r["node"],
            "회사 조정": marks_text(r.get("local")),
        }
        for cell, col in TABLE_CELLS:
            row[col] = ", ".join(r.get(cell) or [])
        row["이유"] = adj.get("why") or ""
        row["_node"] = r["node"]
        row["_title"] = r.get("title") or ""
        rows.append(row)
    return rows


#------------------------------------------------------------------
# 콤마로 이어진 한 줄 → 목록(빈 칸·중복 제거)
#
# -in: s = 콤마 구분 문자열
#
# -out: list = 말 목록
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
# 분류 하나의 조정 줄 고쳐 쓰기(공용)
#=> 말 칸 조정은 새로 계산한 값으로 통째로 바꾸고, 나머지(weight·숫자 칸·이유)는 둔다.
#    1) clear·add·remove 를 지우고 새 조정을 넣는다
#    2) 말 칸 조정이 바뀌었으면 '결정할 당시 이름'을 지금 이름으로 적는다
#    3) 이유가 비어 있고 조정이 있으면 날짜와 함께 기본 문구를 적는다(나중에 고칠 수 있게)
#    4) 조정도 설정도 남지 않으면 줄을 지운다(파일을 짧게)
#
# -in: local  = 조정 dict(제자리에서 고친다)
# -in: node   = dc_id
# -in: title  = 지금 분류 이름
# -in: adjust = diff_cells 결과(말 칸 조정)
# -in: today  = "YYYY-MM-DD"
# -in: why    = 사람이 적은 이유(None 이면 그대로 둔다)
# -in: enabled = True/False(None 이면 그대로 둔다)
# -in: nums   = {숫자 칸: 값} 덮을 값(없으면 그대로)
#
# -out: bool = 이 분류의 조정이 바뀌었으면 True
# -out: error = 없음
#------------------------------------------------------------------
def set_entry(local, node, title, adjust, today, why=None, enabled=None, nums=None):
    rules = local.setdefault("rules", {})
    before = copy.deepcopy(rules.get(node) or {})
    entry = dict(before)
    old_cells = {k: entry.pop(k) for k in ("clear", "add", "remove") if k in entry}
    entry.update(adjust or {})
    if (adjust or {}) != old_cells:
        entry["title_at_decision"] = title
    if enabled is True:
        entry.pop("enabled", None)
    elif enabled is False:
        entry["enabled"] = False
    for k, v in (nums or {}).items():
        entry[k] = v
    if why is not None:
        if why.strip():
            entry["why"] = why.strip()
        else:
            entry.pop("why", None)
    meaningful = [k for k in entry if k not in ("title_at_decision", "why")]
    if meaningful and not entry.get("why"):
        entry["why"] = f"화면에서 고침 {today}"
    if not meaningful:
        rules.pop(node, None)
    else:
        # 칸 차례를 사람이 읽기 좋게 — 이름·끄기·말 조정·설정·이유.
        order = ("title_at_decision", "enabled", "clear", "remove", "add", "weight") \
            + docbuild.NUM_FIELDS + ("why",)
        rules[node] = {k: entry[k] for k in order if k in entry}
    if not rules:
        local.pop("rules", None)
    return (rules.get(node) or {}) != before


#------------------------------------------------------------------
# 표에서 고친 값 → 조정 파일
#=> 행마다 말 칸을 자동값(base)과 견줘 조정으로 되돌리고, 사용·이유 칸도 옮긴다.
#
# -in: local = 조정 dict(제자리에서 고친다)
# -in: base  = views() 의 base
# -in: rows  = 표 행(to_rows 와 같은 열)
# -in: today = "YYYY-MM-DD"(없으면 오늘)
#
# -out: int = 조정이 바뀐 분류 수
# -out: error = 없음
#------------------------------------------------------------------
def apply_rows(local, base, rows, today=None):
    today = today or datetime.date.today().isoformat()
    base_by = _by_node(base)
    changed = 0
    for row in rows:
        node = row.get("_node")
        if node not in base_by:
            continue
        edited = {cell: split_terms(row.get(col)) for cell, col in TABLE_CELLS}
        adj = docbuild.diff_cells(base_by[node], edited)
        if set_entry(local, node, row.get("_title") or "", adj, today,
                     why=str(row.get("이유") or ""), enabled=bool(row.get("사용", True))):
            changed += 1
    return changed


#------------------------------------------------------------------
# 규칙 dict 에서 고친 분류 → 조정 파일(단어 제안용)
#=> 단어 제안은 '규칙 dict'에 말을 더하는 방식(termsuggest.apply_terms)이다. 새 방식에서는
#   그 결과를 조정으로 옮긴다. 본문 칸에 넣을 때 따라오는 숫자 칸(min_count 등)도 옮긴다.
#
# -in: local  = 조정 dict(제자리에서 고친다)
# -in: base   = views() 의 base
# -in: view   = 고치기 전 view(숫자 칸이 새로 생겼는지 가릴 기준)
# -in: edited = apply_terms 로 고친 규칙 dict(view 의 사본)
# -in: node   = 고친 분류 dc_id
# -in: today  = "YYYY-MM-DD"(없으면 오늘)
#
# -out: bool = 조정이 바뀌었으면 True
# -out: error = 없음
#------------------------------------------------------------------
def apply_rule_edit(local, base, view, edited, node, today=None):
    today = today or datetime.date.today().isoformat()
    b = _by_node(base).get(node)
    e = _by_node(edited).get(node)
    v = _by_node(view).get(node) or {}
    if b is None or e is None:
        return False
    adj = docbuild.diff_cells(b, e)
    nums = {k: e[k] for k in docbuild.NUM_FIELDS if k in e and e.get(k) != v.get(k)}
    return set_entry(local, node, e.get("title") or "", adj, today, nums=nums)


#------------------------------------------------------------------
# 핵어 끄기를 옛 모양 dict 로(표 계산용 어댑터)
#=> 핵어 표 계산(docruleedit.head_rows)과 끄기 저장(apply_head_off·set_node_off)은
#   옛 규칙 파일 칸 이름(taxonomy_title_exclude·taxonomy_node_off)으로 짜여 있다.
#   같은 함수를 쓰려고 조정 파일의 head 칸을 그 모양으로 바꿔 준다.
#
# -in: local = 조정 dict
#
# -out: dict = {"taxonomy_title_exclude": [...], "taxonomy_node_off": [...]}
# -out: error = 없음
#------------------------------------------------------------------
def head_as_doc(local):
    head = (local or {}).get("head") or {}
    out = {}
    if head.get("title_exclude"):
        out["taxonomy_title_exclude"] = list(head["title_exclude"])
    if head.get("node_off"):
        out["taxonomy_node_off"] = list(head["node_off"])
    return out


#------------------------------------------------------------------
# 옛 모양 dict → 조정 파일 head 칸
#
# -in: local = 조정 dict(제자리에서 고친다)
# -in: doc   = head_as_doc 모양 dict(끄기 저장 함수가 고친 뒤)
#
# -out: 없음
# -out: error = 없음
#------------------------------------------------------------------
def head_from_doc(local, doc):
    head = {}
    if doc.get("taxonomy_title_exclude"):
        head["title_exclude"] = list(doc["taxonomy_title_exclude"])
    if doc.get("taxonomy_node_off"):
        head["node_off"] = list(doc["taxonomy_node_off"])
    if head:
        local["head"] = head
    else:
        local.pop("head", None)


#------------------------------------------------------------------
# [규칙 다시 만들기] 미리 보기
#=> 지금 규칙 파일과 새로 만들 규칙을 분류별로 견줘, 사람이 확인할 만한 차이만 모은다.
#   지금 규칙 파일이 없으면 전부 '새 분류'다.
#
# -in: current = 지금 doc_rule.yaml 내용(dict, 없으면 None)
# -in: new     = 새로 만들 규칙 dict
#
# -out: list[dict] = [{"분류","무엇","더해질 말","빠질 말"}] (차이가 없으면 빈 목록)
# -out: error = 없음
#------------------------------------------------------------------
def preview_changes(current, new):
    cur = _by_node(current)
    nxt = _by_node(new)
    out = []
    for node, r in nxt.items():
        path = r.get("path") or node
        if node not in cur:
            out.append({"분류": path, "무엇": "새 분류", "더해질 말": "", "빠질 말": ""})
            continue
        adds, gone = [], []
        for cell, col in TABLE_CELLS:
            a = cur[node].get(cell) or []
            b = r.get(cell) or []
            adds += [f"{w}({col})" for w in b if w not in a]
            gone += [f"{w}({col})" for w in a if w not in b]
        if adds or gone:
            out.append({"분류": path, "무엇": "말 바뀜",
                        "더해질 말": ", ".join(adds[:12]) + (" …" if len(adds) > 12 else ""),
                        "빠질 말": ", ".join(gone[:12]) + (" …" if len(gone) > 12 else "")})
    for node, r in cur.items():
        if node not in nxt:
            out.append({"분류": r.get("path") or node, "무엇": "빠지는 분류",
                        "더해질 말": "", "빠질 말": ""})
    return out


#------------------------------------------------------------------
# 옛 방식 규칙 파일 → 새 방식 조정 초안(화면 [새 방식으로 바꾸기])
#=> 엔진의 migrate_from_old 를 부른다. 지금 조정 파일은 쓰지 않는다(아직 없다는 전제).
#
# -in: rules_path  = 옛 doc_rule.yaml 경로
# -in: export_path = 체계 JSON 경로
# -in: today       = "YYYY-MM-DD"(없으면 오늘)
#
# -out: (local, warns, gaps) = 초안, 경고, 재현 안 되는 칸(비어야 정상)
# -out: error = 규칙 파일·체계 JSON 을 못 읽으면 예외 전파
#------------------------------------------------------------------
def migrate(rules_path, export_path, today=None):
    today = today or datetime.date.today().isoformat()
    with open(rules_path, encoding="utf-8") as f:
        old_doc = yaml.safe_load(f) or {}
    policy_dir = os.path.dirname(os.path.abspath(rules_path))
    return docbuild.migrate_from_old(old_doc, export_path, policy_dir,
                                     why=f"옛 규칙에서 옮겨 옴 {today}")
