"""분류체계 제목을 핵어로 써도 되는지 자동 판정하고, 사람이 단 정답과 대조한다.

설계서 plan/업무분류-1차분류-원점재검토-20260916.html 12장 ⓪-b 시험.
문서는 한 건도 읽지 않는다 — 고객 분류체계 파일(doc_taxonomy.yaml)만 본다.

    # 1) 정답표 틀 만들기 (사람이 '정답' 칸에 O/X 를 채운다)
    python tests/evalset/taxonomy_title_check.py --taxonomy ui/policy/doc_taxonomy.yaml \
        --make-sheet report/0b-넓은제목-정답표.csv
    # 2) 정답이 다 달리면 자동 판정과 대조
    python tests/evalset/taxonomy_title_check.py --taxonomy ui/policy/doc_taxonomy.yaml \
        --labels report/0b-넓은제목-정답표.csv --out report/0b-넓은제목-결과.csv

고객 분류체계 제목은 고객 데이터다. 정답표·결과는 gitignore 된 report/ 에만 둔다.
"""
import argparse
import csv
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "ui")]

import taxonomy as taxlib  # noqa: E402  (ui/taxonomy.py — 화면이 쓰는 dict 로더)
from csoclassify.classify import docvocab  # noqa: E402

CORE_SYN = os.path.join(ROOT, "resources", "policy", "synonyms", "doc_synonyms.core.yaml")

# ── 넓은 말 목록 초안 (시험을 통과하면 core 사전으로 옮길 후보) ──────────
# 제목이 통째로 이 말이면, 문서종류이긴 해도 너무 넓어 한 분류로 보낼 수 없다.
# 2026-09-18 '보고서' 는 뺐다 — 사람 정답이 "써도 된다"였다. 더 좁은 'OO보고서' 분류가
#   함께 있으면 5) 모호 규칙이 따로 거른다.
BROAD_EXACT = {
    "자료", "서류", "문서", "기록", "목록", "현황", "양식", "서식",
    "파일", "기타", "일반", "공통", "참고", "첨부",
}
# 제목이 이 말로 끝나면 앞은 주제이고 끝은 '묶음'이다 — 문서종류를 말하지 않는다.
CONTAINER_TAILS = ("자료", "서류", "문서", "파일", "기타", "관련", "일반")
# 문서종류 명사이긴 하나 본문·파일명에 너무 흔해, 끝말이 이것이면 한 분류로 보낼 수 없다.
# core 사전 filename_only 가 이미 "본문에 너무 흔한 말"로 묶어 둔 규정 계열이다.
# (2026-09-18 ⓪-b 에서 사람이 '사내규정'을 X 로 단 이유 — "규정은 너무 흔한 말")
COMMON_ENDINGS = {"규정", "지침", "세칙", "준칙", "규약", "규칙"}

PASS_OVER_RATIO = 0.10  # 과잉(써도 되는데 걸러짐) 허용 비율


#------------------------------------------------------------------
# core 사전에서 '문서종류 명사 끝말' 모으기
#=> 제목이 문서종류를 말하는지는 끝말로 가린다. core 사전의 꼬리말(tails) 키와
#   띄어쓰기 끝말(suffixes)이 이미 "문서종류 명사" 목록이라 그것을 그대로 쓴다.
#    1) core yaml 을 읽는다
#    2) tails 키 + suffixes 를 합쳐 긴 것부터 정렬한다
#
# -in: path = core 사전 경로(기본 CORE_SYN)
#
# -out: list = 문서종류 끝말 리스트(긴 것부터)
# -out: error = 파일 없음·파싱 실패 시 예외 전파
#------------------------------------------------------------------
def load_doctype_endings(path=CORE_SYN):
    with open(path, encoding="utf-8") as f:
        core = yaml.safe_load(f) or {}
    words = set((core.get("tails") or {}).keys()) | set(core.get("suffixes") or [])
    # 긴 끝말이 먼저 걸려야 "결과보고서" 가 "보고서" 보다 우선한다
    return sorted(words, key=len, reverse=True)


#------------------------------------------------------------------
# 제목 하나를 핵어로 써도 되는지 자동 판정
#=> 설계서 7장 ⓐ 자동 판정을 그대로 옮긴 것. 위에서부터 처음 걸린 이유 하나로 거른다.
#    1) 제목이 통째로 넓은 말이면 거른다          (broad_exact)
#    2) 끝이 '묶음' 말(자료·서류…)이면 거른다     (container)
#    3) 끝이 문서종류 명사가 아니면 거른다(주제형) (not_doctype)
#    4) 가장 긴 문서종류 끝말이 흔한 말(규정·지침…)이면 거른다 (common_head)
#    5) 다른 분류 제목이 이 제목으로 끝나거나 같으면 모호해서 거른다 (ambiguous)
#    6) 다 통과하면 쓴다
#
# -in: title     = 분류 제목(띄어쓰기 무시)
# -in: others    = 다른 분류들의 제목 리스트(띄어쓰기 뺀 것)
# -in: endings   = load_doctype_endings() 결과
#
# -out: (use, reason) = (True/False, 걸린 이유 코드 — 쓰면 "ok")
# -out: error = 없음
#------------------------------------------------------------------
def judge_title(title, others, endings):
    t = str(title or "").replace(" ", "").strip()
    if not t:
        return False, "empty"
    if t in BROAD_EXACT:
        return False, "broad_exact"
    # 앞부분이 한 글자뿐이면 묶음 판정을 하지 않는다("서류" 자체는 1)에서 걸린다)
    if any(t.endswith(c) and len(t) > len(c) for c in CONTAINER_TAILS):
        return False, "container"
    # endings 는 긴 것부터라 처음 걸린 것이 가장 긴 끝말이다("규정집" 이 "규정" 보다 먼저)
    ending = next((e for e in endings if t.endswith(e)), None)
    if ending is None:
        return False, "not_doctype"
    if ending in COMMON_ENDINGS:
        return False, "common_head"
    # 같은 핵어가 여러 분류에 걸리면 어디로 보낼지 정할 수 없다
    if any(o == t or (o.endswith(t) and o != t) for o in others):
        return False, "ambiguous"
    return True, "ok"


#------------------------------------------------------------------
# 분류체계에서 시험 대상 노드 고르기
#=> 판정할 때 실제로 제목을 끌어다 쓸 범위와 똑같이 맞춘다 — 꺼 둔 분류와
#   서랍(자식 있는 뿌리)은 뺀다(docvocab.syncable_nodes 그대로).
#
# -in: path = doc_taxonomy.yaml 경로
#
# -out: list = 노드 dict 리스트(dc_id·title·path)
# -out: error = 파일을 못 읽으면 빈 리스트
#------------------------------------------------------------------
def target_nodes(path):
    tax = taxlib.load_taxonomy(path)
    return docvocab.syncable_nodes(tax)


#------------------------------------------------------------------
# 정답표 틀 쓰기
#=> 사람이 채울 CSV 를 만든다. 자동 판정 결과는 일부러 넣지 않는다 —
#   보고 적으면 사람 판단이 자동 판정에 끌려가 시험이 무의미해진다.
#
# -in: nodes = target_nodes() 결과
# -in: out   = 쓸 CSV 경로(엑셀에서 열리게 utf-8-sig)
#
# -out: 없음
# -out: error = 쓰기 실패 시 예외 전파
#------------------------------------------------------------------
def make_sheet(nodes, out):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["dc_id", "경로", "제목", "정답(O/X)", "메모"])
        for n in nodes:
            w.writerow([n.get("dc_id"), n.get("path"), n.get("title"), "", ""])


#------------------------------------------------------------------
# 정답표 읽기
#=> 사람이 채운 CSV 에서 dc_id → O/X 를 꺼낸다. 비어 있으면 아직 안 단 것이다.
#
# -in: path = 정답표 CSV 경로
#
# -out: dict = {dc_id: True(O)/False(X)} — 빈 칸은 빠진다
# -out: error = O/X 외의 값이 있으면 ValueError
#------------------------------------------------------------------
def read_labels(path):
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            v = (row.get("정답(O/X)") or "").strip().upper()
            if not v:
                continue
            if v not in ("O", "X"):
                raise ValueError(f"{row.get('dc_id')}: 정답은 O 또는 X — '{v}'")
            out[row["dc_id"]] = v == "O"
    return out


#------------------------------------------------------------------
# 자동 판정과 정답 대조
#=> 놓친 것(써선 안 되는데 통과)과 과잉(써도 되는데 걸러짐)을 따로 센다.
#   놓친 것은 오분류로 이어지고, 과잉은 재현율 손실이라 무게가 다르다.
#   통과 기준 — 놓친 것 0건, 과잉은 대상의 10% 이내(설계서 12장).
#
# -in: nodes  = target_nodes() 결과
# -in: labels = read_labels() 결과
# -in: out    = 노드별 결과 CSV 경로(None 이면 안 쓴다)
#
# -out: dict = {"n","labeled","missed","over","passed","rows"}
# -out: error = 정답이 빠진 노드가 있으면 ValueError(부분 채점은 하지 않는다)
#------------------------------------------------------------------
def compare(nodes, labels, out=None):
    endings = load_doctype_endings()
    tights = {n["dc_id"]: str(n.get("title") or "").replace(" ", "") for n in nodes}
    missing = [d for d in tights if d not in labels]
    if missing:
        raise ValueError(f"정답이 안 달린 분류 {len(missing)}개: {', '.join(missing)}")
    rows, missed, over = [], [], []
    for n in nodes:
        d = n["dc_id"]
        others = [t for k, t in tights.items() if k != d]
        use, reason = judge_title(n.get("title"), others, endings)
        truth = labels[d]
        kind = "일치" if use == truth else ("놓침" if use else "과잉")
        if kind == "놓침":
            missed.append(d)
        elif kind == "과잉":
            over.append(d)
        rows.append([d, n.get("path"), n.get("title"), "O" if truth else "X",
                     "O" if use else "X", reason, kind])
    if out:
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["dc_id", "경로", "제목", "정답", "자동", "이유", "판정"])
            w.writerows(rows)
    n = len(nodes)
    passed = not missed and len(over) <= PASS_OVER_RATIO * n
    return {"n": n, "labeled": len(labels), "missed": missed, "over": over,
            "passed": passed, "rows": rows}


#------------------------------------------------------------------
# 명령줄 진입점
#=> --make-sheet 면 정답표 틀만 만들고, --labels 면 대조해 요약을 찍는다.
#
# -in: argv = 명령줄 인자(None 이면 sys.argv)
#
# -out: int = 종료 코드(0 통과·틀 생성, 1 불통과)
# -out: error = 정답 누락·잘못된 값이면 ValueError 전파
#------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--taxonomy", required=True)
    ap.add_argument("--make-sheet")
    ap.add_argument("--labels")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    nodes = target_nodes(a.taxonomy)
    if a.make_sheet:
        make_sheet(nodes, a.make_sheet)
        print(f"정답표 틀 {len(nodes)}개 → {a.make_sheet}")
        return 0
    if not a.labels:
        ap.error("--make-sheet 또는 --labels 중 하나가 필요하다")
    r = compare(nodes, read_labels(a.labels), a.out)
    print(f"대상 {r['n']} · 놓침 {len(r['missed'])} · 과잉 {len(r['over'])}"
          f" (허용 {int(PASS_OVER_RATIO * r['n'])}) → {'통과' if r['passed'] else '불통과'}")
    for kind, ids in (("놓침", r["missed"]), ("과잉", r["over"])):
        for d in ids:
            row = next(x for x in r["rows"] if x[0] == d)
            print(f"  {kind}  {row[2]}  (자동 이유: {row[5]})")
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
