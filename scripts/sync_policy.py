#------------------------------------------------------------------
# 정책 파일 사본 동기화·일치 검사 (개발용)
#=> 같은 정책 파일(cso_rule.yaml · doc_rule.yaml · 유의어 사전 …)이 원본 한 곳과
#   사본 여러 곳(Rust 판 · 화면 · 배포 폴더 4개)에 흩어져 있다. 지금까지는 규칙을
#   고칠 때마다 손으로 복사했고, 한 곳을 빠뜨려도 아무도 몰랐다
#   (2026-09-21 실측: Rust/resources/policy 의 doc_rule·doc_taxonomy·class_seed 가
#   화면 원본보다 뒤처져 있었다).
#
#   파일마다 '원본'이 다르다 — 누가 고치는 파일이냐에 따라 나눈다.
#    · 제품 파일(저장소가 원본, resources/policy): cso_rule.yaml · 유의어 core/업종 사전 · 본보기
#    · 고객 파일(화면이 원본, ui/policy): doc_rule.yaml · doc_rule.local.yaml(회사 조정) ·
#      doc_taxonomy.yaml(옛 형식 동안만) · class_seed.jsonl · 체계 JSON · 회사 겹 유의어(local)
#
#   사용:  python scripts/sync_policy.py            → 검사만(기본). 어긋나면 종료코드 1
#          python scripts/sync_policy.py --apply    → 원본을 사본으로 복사(덮기 전 백업)
#          python scripts/sync_policy.py --apply --force  → 사본에서 고친 것 같아도 덮는다
#          python scripts/sync_policy.py --only product|customer
#          python scripts/sync_policy.py --file cso_rule.yaml   → 그 파일만 본다(--apply·--force 와 함께)
#          python scripts/sync_policy.py --file cso_rule.yaml --diff  → 사본별 차이를 본다
#
#   [안전장치]
#    1) 기본은 검사만 한다 — 아무것도 쓰지 않는다
#    2) 사본이 '마지막 동기화 때 이 도구가 쓴 내용'과 다르면 덮지 않는다 — 화면에서 고친
#       규칙·배포 폴더에서 등록한 기준 문서·손으로 적은 설명일 수 있다. --force 로만 덮는다.
#       기록(_backup/policy-sync/ledger.json)이 없는 첫 실행에서는 다른 사본이 모두 여기
#       걸린다 — --diff 로 차이를 보고 사람이 정한다.
#       (수정 시각은 쓰지 않는다 — git checkout 이 원본 시각을 새로 찍어 방향이 뒤집힌다.
#        실제로 손으로 설명을 단 ui/policy 본보기가 '원본이 새것'으로 나왔다)
#    3) 덮기 전에 사본을 _backup/policy-sync/<시각>/ 아래에 같은 경로로 남긴다
#    4) 줄끝(CRLF/LF)만 다른 것은 같은 파일로 본다 — git autocrlf 때문에 저장소 쪽은
#       CRLF, 화면이 쓴 쪽은 LF 인 것이 정상이다. 복사할 때도 사본의 줄끝을 지킨다
#   [대상 아님] Rust/tests/fixtures(시험용 고정 입력) · ui/policy_org(최초 원본 보관본)
#------------------------------------------------------------------

import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import shutil
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# 제품 원본은 저장소, 고객 원본은 화면 폴더.
PRODUCT_SRC = "resources/policy"
CUSTOMER_SRC = "ui/policy"

# 배포 폴더 4개 — 없으면(다른 개발 PC 등) 건너뛴다.
DIST_DIRS = [
    "dist-onedir/windows",
    "dist-onedir/linux",
    "Rust/dist-onedir/windows",
    "Rust/dist-onedir/linux",
]

_INDUSTRIES = ["construction", "education", "finance", "legal",
               "manufacturing", "medical", "public"]

# (묶음, 파일 상대경로, 원본 폴더, 사본 폴더 목록)
#   · 회사 겹 유의어(local)는 제품 소스 폴더에 두지 않는다(dc3fbae — 고객 제품명이 들어가
#     공개 저장소로 나갈 수 있다). 그래서 사본 목록에서 resources/policy·Rust/resources/policy 를 뺀다
#   · 본보기(doc_rule_template)는 Rust 판이 읽지 않아 Rust/resources/policy 에는 두지 않는다
MANIFEST = (
    [("product", "cso_rule.yaml", PRODUCT_SRC,
      ["Rust/resources/policy", CUSTOMER_SRC] + DIST_DIRS),
     ("product", "doc_rule_template.yaml", PRODUCT_SRC, [CUSTOMER_SRC] + DIST_DIRS)]
    + [("product", f"synonyms/doc_synonyms.{k}.yaml", PRODUCT_SRC,
        ["Rust/resources/policy", CUSTOMER_SRC] + DIST_DIRS)
       for k in ["core"] + _INDUSTRIES]
    + [("customer", f, CUSTOMER_SRC, [PRODUCT_SRC, "Rust/resources/policy"] + DIST_DIRS)
       for f in ["doc_rule.yaml", "doc_rule.local.yaml", "doc_taxonomy.yaml",
                 "class_seed.jsonl", "doc_classification_export.json"]]
    + [("customer", "synonyms/doc_synonyms.local.yaml", CUSTOMER_SRC, list(DIST_DIRS))]
)

# 마지막 동기화 때 사본마다 쓴(또는 같다고 확인한) 내용의 지문. 이 PC 에만 의미가 있어
# git 밖(_backup 은 gitignore)에 둔다.
LEDGER_REL = os.path.join("_backup", "policy-sync", "ledger.json")

_VERSION_RE = re.compile(rb'^\s*version:\s*["\']?([^"\'\r\n#]+?)["\']?\s*(?:#.*)?$', re.M)


#------------------------------------------------------------------
# 줄끝을 무시한 파일 내용
#=> 비교용으로 CRLF 를 LF 로 바꾼 바이트를 돌려준다. 줄끝만 다른 두 파일은 같은 파일이다.
#
# -in: path = 파일 경로
#
# -out: bytes = 줄끝을 LF 로 맞춘 내용
# -out: error = 읽기 실패 시 OSError 전파
#------------------------------------------------------------------
def normalized(path):
    with open(path, "rb") as f:
        return f.read().replace(b"\r\n", b"\n")


#------------------------------------------------------------------
# 내용 지문
#=> 줄끝을 맞춘 내용의 sha256. 동기화 기록에 이 값만 남긴다(내용 자체는 남기지 않는다).
#
# -in: data = 줄끝을 맞춘 내용(bytes)
#
# -out: str = 16진 지문
# -out: error = 없음
#------------------------------------------------------------------
def digest(data):
    return hashlib.sha256(data).hexdigest()


#------------------------------------------------------------------
# 동기화 기록 읽기·쓰기
#=> {사본 상대경로: 지문}. 없거나 깨졌으면 빈 기록으로 본다 — 그러면 다른 사본은 전부
#   '사본에서 고쳤을 수 있음'으로 분류되어 덮이지 않는다(안전한 쪽).
#
# -in: root = 저장소 최상위 폴더
#
# -out: dict = 기록
# -out: error = 없음 (읽기 실패는 빈 기록)
#------------------------------------------------------------------
def load_ledger(root):
    try:
        with open(os.path.join(root, LEDGER_REL), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


#------------------------------------------------------------------
# 동기화 기록 저장
#=> apply 가 끝난 뒤 사본별 지문을 저장한다.
#
# -in: root   = 저장소 최상위 폴더
# -in: ledger = 기록 dict
#
# -out: 없음
# -out: error = 쓰기 실패 시 OSError 전파
#------------------------------------------------------------------
def save_ledger(root, ledger):
    path = os.path.join(root, LEDGER_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=1, sort_keys=True)


#------------------------------------------------------------------
# 정책 파일의 버전 표기 찾기
#=> YAML 맨 위쪽 'version: ...' 값을 뽑는다. 검사 표에서 어느 판이 어디 깔렸는지 보이게.
#
# -in: data = 파일 내용(bytes)
#
# -out: str = 버전 문자열, 없으면 "-"
# -out: error = 없음
#------------------------------------------------------------------
def version_of(data):
    m = _VERSION_RE.search(data)
    return m.group(1).decode("utf-8", "replace").strip() if m else "-"


#------------------------------------------------------------------
# 사본 한 개의 상태 판정
#=> 원본과 사본을 견줘 넷 중 하나로 가른다.
#    · same    : 줄끝을 빼면 내용이 같다
#    · missing : 사본이 없다(배포 폴더에 새로 생긴 파일 등)
#    · differ  : 내용이 다르지만 사본은 지난 동기화 그대로다 → 원본이 바뀐 것, 복사하면 된다
#    · edited  : 사본이 지난 동기화 이후 바뀌었거나 기록이 없다 → 사본에서 고쳤을 수 있다.
#                기본은 덮지 않는다
#
# -in: src      = 원본 파일 경로
# -in: dst      = 사본 파일 경로
# -in: recorded = 이 사본의 지난 동기화 지문(없으면 None)
#
# -out: str = "same" | "missing" | "differ" | "edited"
# -out: error = 읽기 실패 시 OSError 전파
#------------------------------------------------------------------
def compare(src, dst, recorded=None):
    if not os.path.isfile(dst):
        return "missing"
    cur = normalized(dst)
    if normalized(src) == cur:
        return "same"
    # 사본이 지난번에 이 도구가 쓴 그대로일 때만 '원본이 앞선 것'으로 믿는다.
    return "differ" if recorded and digest(cur) == recorded else "edited"


#------------------------------------------------------------------
# 원본 → 사본 복사(사본의 줄끝 유지)
#=> 사본이 CRLF 였으면 CRLF 로, LF 였으면 LF 로 쓴다. 저장소 쪽 사본의 줄끝이 바뀌면
#   git 이 파일 전체가 바뀐 것으로 보고, 화면 쪽은 제 줄끝으로 다시 쓰기 때문이다.
#    1) 덮을 사본이 있으면 백업 폴더에 같은 상대경로로 복사
#    2) 사본의 줄끝(없으면 원본의 줄끝)에 맞춰 내용을 쓴다
#
# -in: src        = 원본 파일 경로
# -in: dst        = 사본 파일 경로
# -in: backup_dir = 백업 최상위 폴더(None 이면 백업 안 함)
# -in: rel_dst    = 백업 폴더 아래에 쓸 사본의 상대경로
#
# -out: 없음
# -out: error = 읽기·쓰기 실패 시 OSError 전파
#------------------------------------------------------------------
def copy_keep_eol(src, dst, backup_dir, rel_dst):
    body = normalized(src)
    if os.path.isfile(dst):
        with open(dst, "rb") as f:
            old = f.read()
        crlf = b"\r\n" in old
        if backup_dir:
            bak = os.path.join(backup_dir, rel_dst)
            os.makedirs(os.path.dirname(bak), exist_ok=True)
            shutil.copy2(dst, bak)
    else:
        with open(src, "rb") as f:
            crlf = b"\r\n" in f.read()
    if crlf:
        body = body.replace(b"\n", b"\r\n")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(body)


#------------------------------------------------------------------
# 목록 전체 검사(와 반영)
#=> MANIFEST 의 원본·사본 짝을 모두 견주고, apply 면 복사까지 한다.
#    1) 원본이 없으면 그 파일은 건너뛴다(고객 파일은 개발 PC 에 없을 수 있다)
#    2) 사본 폴더 자체가 없으면 건너뛴다(배포 폴더를 안 만든 PC)
#    3) differ·missing 은 apply 때 복사, edited 는 force 일 때만 복사
#    4) apply 면 같거나 복사한 사본의 지문을 기록에 남긴다 — 다음 번에 사본이 손으로
#       바뀌었는지 가를 기준이 된다
#
# -in: root   = 저장소 최상위 폴더
# -in: apply  = True 면 복사한다(기본 검사만)
# -in: force  = True 면 사본이 더 새것이어도 덮는다
# -in: only   = "product" | "customer" | None(전부)
# -in: stamp  = 백업 폴더 이름에 쓸 시각 문자열(없으면 지금 시각)
# -in: file   = 이 상대경로(예: "cso_rule.yaml")만 본다. None 이면 전부
#
# -out: list[dict] = 줄마다 {group, file, src, dst, state, action, src_ver, dst_ver}
# -out: error = 파일 읽기·쓰기 실패 시 OSError 전파
#------------------------------------------------------------------
def run(root, apply=False, force=False, only=None, stamp=None, file=None):
    stamp = stamp or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(root, "_backup", "policy-sync", stamp)
    ledger = load_ledger(root)
    rows = []
    for group, rel, src_dir, dst_dirs in MANIFEST:
        if (only and group != only) or (file and rel != file):
            continue
        src = os.path.join(root, src_dir, rel)
        if not os.path.isfile(src):
            rows.append({"group": group, "file": rel, "src": src_dir, "dst": "-",
                         "state": "no-source", "action": "", "src_ver": "-", "dst_ver": "-"})
            continue
        src_ver = version_of(normalized(src))
        for dst_dir in dst_dirs:
            # 폴더째 없는 배포처는 이 PC 에 없는 것 — 새로 만들지 않는다.
            if not os.path.isdir(os.path.join(root, dst_dir)):
                continue
            dst = os.path.join(root, dst_dir, rel)
            key = f"{dst_dir}/{rel}"
            state = compare(src, dst, ledger.get(key))
            action = ""
            if apply and (state in ("differ", "missing") or (state == "edited" and force)):
                copy_keep_eol(src, dst, backup_dir, os.path.join(dst_dir, rel))
                action = "copied"
            elif apply and state == "edited":
                action = "skipped"
            # 원본과 같아진 사본만 기록한다 — 덮지 않은 사본은 다음에도 계속 걸려야 한다.
            if apply and (state == "same" or action == "copied"):
                ledger[key] = digest(normalized(dst))
            dst_ver = version_of(normalized(dst)) if os.path.isfile(dst) else "-"
            rows.append({"group": group, "file": rel, "src": src_dir, "dst": dst_dir,
                         "state": state, "action": action,
                         "src_ver": src_ver, "dst_ver": dst_ver})
    if apply:
        save_ledger(root, ledger)
    return rows


#------------------------------------------------------------------
# 원본과 어긋난 사본의 차이 보기
#=> --diff 용. 사본마다 원본 대비 unified diff 를 만든다(줄끝은 맞춘 뒤 비교).
#   '사본에서 고쳤을 수 있음'이 뜬 곳을 덮을지 사람이 정할 때 본다.
#
# -in: root = 저장소 최상위 폴더
# -in: rows = run() 결과(검사 모드)
#
# -out: str = 차이 전문(어긋난 사본이 없으면 빈 문자열)
# -out: error = 읽기 실패 시 OSError 전파
#------------------------------------------------------------------
def render_diff(root, rows):
    out = []
    for r in rows:
        if r["state"] not in ("differ", "edited"):
            continue
        a = normalized(os.path.join(root, r["src"], r["file"])).decode("utf-8", "replace")
        b = normalized(os.path.join(root, r["dst"], r["file"])).decode("utf-8", "replace")
        # '-' 는 원본에만 있는 줄, '+' 는 사본에만 있는 줄이다.
        out.extend(difflib.unified_diff(a.splitlines(), b.splitlines(),
                                        f"{r['src']}/{r['file']} (원본)",
                                        f"{r['dst']}/{r['file']} (사본)", lineterm=""))
    return "\n".join(out)


_STATE_TEXT = {
    "same": "같음",
    "differ": "다름(원본이 바뀜)",
    "missing": "사본 없음",
    "edited": "다름(사본에서 고쳤을 수 있음 — 지난 동기화 기록과 다름)",
    "no-source": "원본 없음(건너뜀)",
}


#------------------------------------------------------------------
# 검사 결과를 사람이 읽는 표로
#=> 같은 것은 파일별 한 줄로 묶고, 어긋난 것만 사본마다 적는다 — 전부 같을 때 화면이
#   수십 줄로 넘치지 않게.
#
# -in: rows    = run() 결과
# -in: verbose = True 면 같은 사본도 전부 적는다
#
# -out: str = 출력할 문자열
# -out: error = 없음
#------------------------------------------------------------------
def render(rows, verbose=False):
    out = []
    by_file = {}
    for r in rows:
        by_file.setdefault((r["group"], r["file"], r["src"]), []).append(r)
    for (group, rel, src_dir), rs in by_file.items():
        bad = [r for r in rs if r["state"] != "same"]
        ver = rs[0]["src_ver"]
        head = f"[{group}] {rel}  (원본 {src_dir}" + (f" · {ver}" if ver != "-" else "") + ")"
        if not bad:
            out.append(f"  OK  {head} — 사본 {len(rs)}곳 모두 같음")
            if not verbose:
                continue
        else:
            out.append(f"  !!  {head}")
        for r in (rs if verbose else bad):
            if r["state"] == "no-source":
                out.append(f"        {_STATE_TEXT['no-source']}")
                continue
            ver_note = f" · {r['dst_ver']}" if r["dst_ver"] not in ("-", r["src_ver"]) else ""
            act = {"copied": " → 복사함", "skipped": " → 덮지 않음(--force 필요)"}.get(r["action"], "")
            out.append(f"        {r['dst']:<26} {_STATE_TEXT[r['state']]}{ver_note}{act}")
    return "\n".join(out)


#------------------------------------------------------------------
# 스크립트 진입점
#=> 인자를 읽어 검사(또는 반영)하고 표를 찍는다.
#    1) 검사만: 어긋난 곳이 있으면 종료코드 1
#    2) --apply: 복사 후, 덮지 못한(사본에서 고친 것 같은) 곳이 남으면 종료코드 2
#    3) --diff: 표 대신 어긋난 사본의 차이를 찍는다(아무것도 쓰지 않는다)
#
# -in: argv = 명령행 인자(없으면 sys.argv)
#
# -out: int = 종료코드(0 모두 일치 · 1 어긋남 · 2 덮지 못한 사본 남음)
# -out: error = 파일 읽기·쓰기 실패 시 OSError 전파
#------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="정책 파일 사본 동기화·일치 검사")
    ap.add_argument("--apply", action="store_true", help="원본을 사본으로 복사한다(기본: 검사만)")
    ap.add_argument("--force", action="store_true", help="사본에서 고친 것 같아도 덮는다")
    ap.add_argument("--only", choices=["product", "customer"], help="한 묶음만 본다")
    ap.add_argument("--file", help="이 파일만 본다(원본 폴더 기준 상대경로, 예: cso_rule.yaml)")
    ap.add_argument("--diff", action="store_true", help="어긋난 사본의 차이를 보여 준다")
    ap.add_argument("--root", default=_ROOT, help="저장소 최상위 폴더")
    ap.add_argument("-v", "--verbose", action="store_true", help="같은 사본도 전부 적는다")
    args = ap.parse_args(argv)
    # 윈도우 콘솔(cp949)은 '—' 같은 글자를 못 찍어 표를 찍다 죽는다 — UTF-8 로 내보낸다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # 차이 보기는 검사 전용 — --apply 와 함께 줘도 쓰지 않는다.
    if args.diff:
        rows = run(args.root, only=args.only, file=args.file)
        print(render_diff(args.root, rows) or "어긋난 사본이 없습니다.")
        return 0

    rows = run(args.root, apply=args.apply, force=args.force, only=args.only, file=args.file)
    if not rows:
        print("대상 파일이 없습니다(--file 이름을 확인하세요).")
        return 1
    print(render(rows, verbose=args.verbose))

    copied = sum(r["action"] == "copied" for r in rows)
    skipped = sum(r["action"] == "skipped" for r in rows)
    drift = sum(r["state"] in ("differ", "missing", "edited") for r in rows)
    if args.apply:
        if copied:
            print(f"\n복사 {copied}곳 · 덮기 전 사본은 _backup/policy-sync/ 아래에 남겼습니다.")
        if skipped:
            print(f"덮지 않은 사본 {skipped}곳 — 사본에서 고쳤을 수 있습니다. --file 이름 --diff 로 "
                  "차이를 보고, 원본에 반영하거나 --file 이름 --apply --force 로 덮으세요.")
            return 2
        return 0
    if drift:
        print(f"\n어긋난 사본 {drift}곳 — --apply 로 맞춥니다"
              "('사본에서 고쳤을 수 있음'은 --diff 로 확인 후 --force).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
