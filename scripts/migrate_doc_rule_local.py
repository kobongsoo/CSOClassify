#------------------------------------------------------------------
# 옛 doc_rule.yaml → doc_rule.local.yaml 초안 뽑기 (옮겨 가기 도구, 설계서 13장)
#=> 지금까지 사람이 고쳐 온 doc_rule.yaml 에는 자동값과 사람의 조정이 섞여 있다.
#   같은 입력으로 새로 만든 규칙과 견줘, 다른 부분만 조정(clear·add·remove·weight·
#   숫자 칸)으로 적은 doc_rule.local.yaml 초안을 만든다. 그 초안으로 다시 만들면
#   옛 규칙과 말 목록이 같아져야 한다(끝에서 확인한다).
#   실제 로직은 엔진의 docbuild.migrate_from_old() 다 — 화면의 [새 방식으로 바꾸기]와
#   같은 코드를 쓴다.
#
#   사용:
#     python scripts/migrate_doc_rule_local.py --old <옛 doc_rule.yaml> \
#         --policy <정책 폴더> [--export <체계 JSON>] [--out <초안 경로>]
#   · --out 을 안 주면 정책 폴더의 doc_rule.local.yaml.draft 에 쓴다(바로 덮지 않는다)
#   · 초안의 why 칸은 "TODO" 로 둔다 — 이유를 댈 수 없는 조정은 옮기지 말 것
#------------------------------------------------------------------

import argparse
import os
import shutil
import sys
import tempfile

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from csoclassify.classify import docbuild  # noqa: E402


#------------------------------------------------------------------
# 스크립트 진입점
#=> 정책 폴더 사본(지금 조정 파일은 뺀다)에서 초안을 뽑고, 그 초안으로 옛 규칙이
#   재현되는지 확인한 뒤 초안 파일을 쓴다. 정책 폴더의 규칙·조정 파일은 건드리지 않는다.
#
# -in: argv = 명령행 인자
#
# -out: int = 0(초안으로 옛 규칙 재현) · 1(재현 안 됨 — 차이를 찍는다)
# -out: error = 입력 파일 문제는 예외 전파
#------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="옛 doc_rule.yaml → doc_rule.local.yaml 초안")
    ap.add_argument("--old", required=True, help="옛 doc_rule.yaml")
    ap.add_argument("--policy", required=True, help="정책 폴더(사전·본보기가 있는 곳)")
    ap.add_argument("--export", help="체계 JSON(기본: 정책 폴더의 doc_classification_export.json)")
    ap.add_argument("--out", help="초안 경로(기본: 정책 폴더/doc_rule.local.yaml.draft)")
    args = ap.parse_args(argv)

    export = args.export or os.path.join(args.policy, "doc_classification_export.json")
    with open(args.old, encoding="utf-8") as f:
        old_doc = yaml.safe_load(f) or {}

    with tempfile.TemporaryDirectory() as tmp:
        # 지금 조정 파일이 있으면 자동값이 흐려지므로 사본에서 뺀다.
        shutil.copytree(args.policy, tmp, dirs_exist_ok=True)
        lp = os.path.join(tmp, docbuild.RULE_LOCAL)
        if os.path.exists(lp):
            os.remove(lp)
        local, warns, gaps = docbuild.migrate_from_old(
            old_doc, export, tmp, why="TODO — 이 조정의 이유를 적으세요(이유가 없으면 지우세요)")

    out = args.out or os.path.join(args.policy, docbuild.RULE_LOCAL + ".draft")
    with open(out, "w", encoding="utf-8") as f:
        f.write("# doc_rule.local.yaml 초안 — migrate_doc_rule_local.py 가 옛 규칙과의 차이로 만들었다.\n"
                "# why 칸을 채우고, 이유를 댈 수 없는 조정은 지운 뒤 doc_rule.local.yaml 로 이름을 바꾸세요.\n")
        yaml.safe_dump(local, f, allow_unicode=True, sort_keys=False)
    for w in warns:
        print(f"[경고] {w}")
    n = len(local.get("rules") or {})
    print(f"초안: {out} — 분류별 조정 {n}건"
          + (f", 설정 {', '.join(local['settings'])}" if local.get("settings") else ""))
    if gaps:
        print("초안으로 다시 만들어도 옛 규칙과 다른 칸: " + ", ".join(gaps))
        return 1
    print("초안으로 다시 만들면 옛 규칙과 말 목록이 같습니다(차례 무시).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
