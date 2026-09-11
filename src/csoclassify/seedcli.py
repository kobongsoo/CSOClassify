#------------------------------------------------------------------
# 기준 문서 CLI 등록 (--seed-add / --seed-add-from) — 설계서 3부
#=> 화면 없이 MpowerClassify 한 번으로 기준 문서(class_seed.jsonl)를 등록한다.
#   웹·배치가 UI 를 거치지 않고 부를 수 있게 하는 것이 목적이다.
#
#   [이 모듈이 하는 일과 안 하는 일]
#   문서를 읽고 벡터를 만드는 일은 이 모듈이 하지 않는다 — cli.py 의 분류
#   경로가 이미 하는 일이라, 그것을 그대로 태우고 결과 레코드만 받아 쓴다.
#   같은 경로를 타야 "화면으로 넣은 씨앗"과 "CLI 로 넣은 씨앗"이 같아진다.
#   저장도 직접 하지 않는다 — seedstore.save_seeds 한 곳만 쓴다(writer 는 하나).
#
#   [지문(hash)을 반드시 채우는 이유]
#   스키마 v3 는 '원본이 바뀌었나'를 hash 하나로만 판정한다(size·mtime 은
#   읽는 코드가 없어 뺐다). 지문 없이 등록된 줄은 점검에서 "모르면 넘어간다"
#   가지에 걸려, 원본이 아무리 바뀌어도 경고 없이 옛 벡터로 계속 전파한다.
#   그래서 --hash 를 안 줘도 이 경로에서는 항상 잰다.
#
#   [문서 ID(doc_id)를 지어내지 않는 이유]
#   폴백 doc_id 는 '파일 해시 앞 40자'라 이미 hash 칸에 있고, 그 값을 실으면
#   외부 문서 ID 처럼 생긴 값이 적재 쪽으로 흘러가 같은 문서가 두 건이 된다.
#   목록이 준 값이나 --filelist 의 sfile_id 만 쓰고, 없으면 칸을 안 만든다.
#------------------------------------------------------------------

import json
import os
import sys
# 주석 판정은 --filelist 와 같은 규칙을 써야 한다 — 복사하지 않고 그대로 쓴다.
from .filelist import _is_comment

from . import seedstore


# 이 경로로 들어온 씨앗의 출처 표시. 화면은 "upload"/"phase4", 문서함 선택은
# "phase4" 를 쓴다 — 어디서 들어온 씨앗인지 나중에 셀 수 있어야 한다.
SOURCE = "cli"

# 목록 파일(jsonl) 한 줄에서 받아들이는 칸. 모르는 칸이 오면 알려 준다 —
# 오타(doctypes·grades)를 조용히 무시하면 축이 통째로 빠진 채 등록된다.
LIST_KEYS = frozenset({"file", "doc_id", "grade", "doctype", "note", "reviewer"})


#------------------------------------------------------------------
# --seed-add 관련 옵션을 파서에 붙인다
#=> cli.py 의 build_parser 에서 한 번 부른다. 옵션이 한곳에 모여 있어야
#   도움말 분류(--help)와 실제 처리가 어긋나지 않는다.
#
# -in: p = argparse.ArgumentParser
#
# -out: 없음(파서를 제자리에서 고친다)
# -out: error = 없음
#------------------------------------------------------------------
def add_args(p):
    p.add_argument("--seed-add", dest="seed_add", nargs="+", metavar="문서",
                   help="이 문서들을 기준 문서로 등록한다(여러 개 가능). "
                        "--seed-grade/--seed-doctype 값이 모든 문서에 그대로 적용된다")
    p.add_argument("--seed-add-from", dest="seed_add_from", metavar="목록|-",
                   help="문서마다 다른 등급·분류를 줄 때 쓰는 목록(jsonl). "
                        "한 줄에 {file, doc_id?, grade?, doctype?, note?, reviewer?}. "
                        "'-' 면 표준입력. --seed-add 와 함께 못 쓴다")
    p.add_argument("--seed-grade", dest="seed_grade", metavar="C|S|O",
                   help="등록할 보안등급")
    p.add_argument("--seed-doctype", dest="seed_doctype", metavar="dc_id[,dc_id]",
                   help="등록할 업무분류(콤마로 여러 개)")
    p.add_argument("--seed-reviewer", dest="seed_reviewer", metavar="이름",
                   help="검토자 이름 — 필수. 출처 없는 기준 문서를 만들지 않는다")
    p.add_argument("--seed-note", dest="seed_note", default="", metavar="메모",
                   help="기준 문서에 남길 메모(선택)")
    p.add_argument("--seed-audit", dest="seed_audit", metavar="경로",
                   help="변경 기록 파일. 안 주면 --seeds 와 같은 폴더의 "
                        "class_seed_audit.jsonl")


#------------------------------------------------------------------
# 이 실행이 --seed-add 모드인가
#=> cli.py 가 여러 곳에서 물어보므로 한 줄로 모아 둔다.
#
# -in: args = 파싱된 인자
#
# -out: bool
# -out: error = 없음
#------------------------------------------------------------------
def is_seed_add(args):
    return bool(getattr(args, "seed_add", None) or getattr(args, "seed_add_from", None))


#------------------------------------------------------------------
# 업무분류 문자열을 dc_id 목록으로
#=> "DC_1, DC_2" 처럼 콤마·공백이 섞여 와도 같게 다룬다. 문자열 하나가 와도
#   배열로 만든다 — 엔진이 배열만 읽기 때문이다.
#
# -in: v = 문자열 또는 리스트 또는 None
#
# -out: list = dc_id 문자열 리스트(없으면 빈 리스트)
# -out: error = 없음
#------------------------------------------------------------------
def _dc_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        v = v.split(",")
    return [str(x).strip() for x in v if str(x).strip()]


#------------------------------------------------------------------
# 등록 계획 만들기 — 인자·목록을 문서별 한 줄로 편다
#=> 문서를 읽기 전에 전부 검사한다. 절반 읽고 나서 인자가 틀린 것을 알면
#   이미 쓴 것과 안 쓴 것이 섞여 되돌리기 어렵다.
#    1) 검토자·축·모드 충돌을 먼저 본다(P2 · 6장)
#    2) 목록이면 한 줄씩 읽어 칸을 정규화하고, 아니면 플래그 값을 모든 문서에 편다
#    3) 같은 doc_id 가 두 문서에 붙는지 본다 — 받는 쪽에서 한 문서가 다른 것을 덮는다
#
# -in: args = 파싱된 인자
# -in: tax  = 분류체계(axes.Taxonomy) 또는 None. 있으면 dc_id 존재를 검사한다
#
# -out: (plan, err) = plan 은 [{file, doc_id, grade, doctype[], note, reviewer}],
#                     err 는 (kind, message) 또는 None
# -out: error = 예외 없음(잘못은 err 로 돌려준다)
#------------------------------------------------------------------
def build_plan(args, tax=None):
    reviewer = (getattr(args, "seed_reviewer", None) or "").strip()
    if not reviewer:
        return None, ("bad_args",
                      "--seed-reviewer <이름> 이 필요합니다 — "
                      "누가 확정했는지 모르는 기준 문서는 만들지 않습니다")

    if getattr(args, "seed_add", None) and getattr(args, "seed_add_from", None):
        return None, ("mode_conflict",
                      "--seed-add 와 --seed-add-from 은 함께 쓸 수 없습니다 — "
                      "값이 다를 때 무엇이 이기는지 매번 되짚게 됩니다")

    if getattr(args, "file", None) or getattr(args, "dir", None):
        return None, ("mode_conflict",
                      "--seed-add 는 --file/--dir 과 함께 쓸 수 없습니다")

    note = getattr(args, "seed_note", "") or ""
    plan = []

    if getattr(args, "seed_add_from", None):
        rows, err = _read_list(args.seed_add_from)
        if err:
            return None, err
        for n, row in rows:
            f = str(row.get("file") or "").strip()
            if not f:
                return None, ("bad_args", "목록 %d번째 줄에 file 이 없습니다" % n)
            unknown = sorted(set(row) - LIST_KEYS)
            if unknown:
                # 조용히 무시하면 grade 오타 하나로 축이 통째로 빠진 채 등록된다.
                return None, ("bad_args",
                              "목록 %d번째 줄에 모르는 칸이 있습니다: %s "
                              "(쓸 수 있는 칸: %s)"
                              % (n, ", ".join(unknown), ", ".join(sorted(LIST_KEYS))))
            plan.append({
                "file": f,
                "doc_id": (str(row.get("doc_id") or "").strip() or None),
                "grade": (str(row.get("grade") or "").strip() or None),
                "doctype": _dc_list(row.get("doctype")),
                "note": row.get("note") if row.get("note") is not None else note,
                "reviewer": (str(row.get("reviewer") or "").strip() or reviewer),
            })
    else:
        grade = (getattr(args, "seed_grade", None) or "").strip() or None
        dcs = _dc_list(getattr(args, "seed_doctype", None))
        for f in args.seed_add:
            plan.append({"file": f, "doc_id": None, "grade": grade,
                         "doctype": list(dcs), "note": note, "reviewer": reviewer})

    if not plan:
        return None, ("bad_args", "등록할 문서가 없습니다")

    # 축을 하나도 안 정한 줄은 아무 뜻이 없다 — 저장해도 기준으로 쓰이지 않는다.
    for p in plan:
        if not p["grade"] and not p["doctype"]:
            return None, ("bad_args",
                          "%s: 보안등급도 업무분류도 없습니다 — "
                          "--seed-grade 나 --seed-doctype 중 하나는 필요합니다"
                          % p["file"])
        if p["grade"] and p["grade"] not in ("C", "S", "O"):
            return None, ("bad_args",
                          "%s: 보안등급은 C·S·O 중 하나여야 합니다(받은 값: %s)"
                          % (p["file"], p["grade"]))

    # 없는 분류로 씨앗을 만들면 전파가 조용히 헛돈다. 체계를 아는 경우에만 막는다.
    if tax is not None:
        for p in plan:
            gone = [d for d in p["doctype"] if tax.get(d) is None]
            if gone:
                return None, ("bad_args",
                              "%s: 분류체계에 없는 dc_id 입니다: %s"
                              % (p["file"], ", ".join(gone)))

    # 같은 신분증을 두 문서에 붙이면 받는 쪽에서 한 문서가 다른 문서를 덮는다.
    seen = {}
    for p in plan:
        if p["doc_id"]:
            if p["doc_id"] in seen:
                return None, ("bad_args",
                              "같은 doc_id 를 두 문서에 줬습니다: %s (%s · %s)"
                              % (p["doc_id"], seen[p["doc_id"]], p["file"]))
            seen[p["doc_id"]] = p["file"]

    return plan, None


#------------------------------------------------------------------
# 목록 파일(jsonl) 읽기
#=> '-' 면 표준입력을 읽는다 — 웹이 임시파일을 만들지 않아도 되게.
#   깨진 줄은 건너뛰지 않고 실패시킨다. 등록은 '몇 건을 넣었나'가 계약인데,
#   조용히 건너뛰면 부르는 쪽이 몇 건이 빠졌는지 알 수 없다.
#
# -in: path = 목록 파일 경로 또는 "-"
#
# -out: (rows, err) = rows 는 [(줄번호, dict)], err 는 (kind, message) 또는 None
# -out: error = 예외 없음(잘못은 err 로 돌려준다)
#------------------------------------------------------------------
def _read_list(path):
    try:
        if path == "-":
            text = sys.stdin.read()
        else:
            with open(path, encoding="utf-8") as f:
                text = f.read()
    except OSError as e:
        return None, ("bad_args", "목록을 읽지 못했습니다: %s :: %s" % (path, e))

    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        # '#' 로 시작하는 줄은 사람이 적어 둔 설명으로 보고 건너뛴다.
        #=> --filelist 와 같은 규칙을 쓰려고 그쪽 판정을 그대로 가져다 쓴다. 두 목록이
        #   같은 모양인데 한쪽만 주석이 되면 쓰는 사람이 매번 어느 쪽인지 되짚어야 한다.
        #   판정을 복사해 오면 나중에 한쪽만 고쳐져 갈리므로 함수를 재사용한다.
        #   [줄 번호는 그대로] 오류 메시지의 N 번째 줄은 파일에서 눈으로 세는 줄 번호여야
        #   하므로, 건너뛴 줄도 번호에는 포함된다(enumerate 를 앞에서 돌린 이유).
        if _is_comment(line):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            return None, ("bad_args", "목록 %d번째 줄이 JSON 이 아닙니다: %s" % (n, e))
        if not isinstance(row, dict):
            return None, ("bad_args", "목록 %d번째 줄이 객체가 아닙니다" % n)
        rows.append((n, row))
    return rows, None


#------------------------------------------------------------------
# 분류 결과 레코드를 기준 문서 줄로 바꾼다
#=> 문서를 읽고 벡터를 만드는 일은 이미 끝났다. 여기서는 그 결과에 사람이
#   정한 값(등급·분류·검토자·메모)을 얹어 저장소 모양으로 만든다.
#    1) 벡터가 없는 문서는 기준으로 쓸 수 없다 — 실패로 센다(설계 Q3)
#    2) 축마다 set_axis 를 부른다 — 화면과 같은 함수라 결과가 갈릴 수 없다
#    3) doc_id 는 목록이 준 값 > 레코드의 sfile_id 순으로 쓰고, 없으면 안 적는다
#
# -in: plan    = build_plan 결과
# -in: records = 분류가 만든 레코드 리스트(file·hash·vector·doc_id 를 본다)
# -in: seeds   = 바탕이 될 기존 기준 문서 리스트(stdout 모드면 빈 리스트)
#
# -out: (seeds, added, failed) = 갱신된 리스트, 등록된 [{file, grade, doctype}],
#                                실패한 [{file, why}]
# -out: error = 없음
#------------------------------------------------------------------
def build_rows(plan, records, seeds):
    by_file = {seedstore.norm_file(r.get("file")): r for r in (records or [])}
    out, added, failed = list(seeds), [], []

    for p in plan:
        rec = by_file.get(seedstore.norm_file(p["file"]))
        if rec is None:
            failed.append({"file": p["file"], "why": "문서를 읽지 못했습니다"})
            continue
        vec = rec.get("vector")
        if not vec:
            # 벡터 없는 기준 문서는 비교 대상이 못 된다 — 저장할 이유가 없다.
            why = (rec.get("error") or {}).get("kind") or "벡터를 얻지 못했습니다"
            failed.append({"file": p["file"], "why": why})
            continue

        doc = {"hash": rec.get("hash")} if rec.get("hash") else None
        for axis, value in (("security", p["grade"]), ("doctype", p["doctype"])):
            if not value:
                continue
            out = seedstore.set_axis(out, p["file"], axis, value, p["reviewer"],
                                     source=SOURCE, note=p["note"],
                                     vector=vec, doc=doc)

        # 문서 ID — 목록이 준 값이 먼저다(웹이 아는 진짜 신분증).
        doc_id = p["doc_id"]
        if not doc_id and rec.get("doc_id_source") == "sfile_id":
            doc_id = rec.get("doc_id")
        if doc_id:
            key = seedstore.norm_file(p["file"])
            out = [dict(x, doc_id=doc_id)
                   if seedstore.norm_file(x.get("file")) == key else x for x in out]

        added.append({"file": p["file"], "grade": p["grade"],
                      "doctype": p["doctype"]})

    return out, added, failed


#------------------------------------------------------------------
# 변경 기록 파일 경로 정하기
#=> --seed-audit 을 주면 그것, 아니면 --seeds 와 같은 폴더의
#   class_seed_audit.jsonl. 화면의 기본값과 다를 수 있어 실행 요약에 찍는다.
#
# -in: args = 파싱된 인자
#
# -out: str|None = 감사 파일 경로(--seeds 도 --seed-audit 도 없으면 None)
# -out: error = 없음
#------------------------------------------------------------------
def audit_path(args):
    p = getattr(args, "seed_audit", None)
    if p:
        return p
    seeds = getattr(args, "seeds", None)
    if not seeds:
        return None
    return os.path.join(os.path.dirname(os.path.abspath(seeds)),
                        "class_seed_audit.jsonl")
