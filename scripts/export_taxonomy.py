#------------------------------------------------------------------
# DOC_CLASSIFICATION JSON 내보내기 → doc_taxonomy.yaml 스냅샷 (개발용 래퍼)
#=> 실제 변환 로직은 csoclassify.classify.axes/doc_rules 에 있다(패키지에 있어야
#   csoclassify.exe --export-taxonomy 로도 같은 동작을 낼 수 있다 — PyInstaller
#   번들에는 scripts/ 가 들어가지 않는다). 이 스크립트는 소스 체크아웃에서
#   빠르게 돌리기 위한 얇은 CLI 래퍼일 뿐이다.
#
#   사용:  python scripts/export_taxonomy.py
#          → resources/policy/doc_taxonomy.yaml 생성(기본 입출력 경로 사용)
#          python scripts/export_taxonomy.py --input <json> --output <yaml>
#          python scripts/export_taxonomy.py --scaffold-doc-rule <yaml>
#          → doc_rule.yaml 골격(빈 terms)도 함께 생성(설계서 3-2)
#          (exe 로 배포된 환경에서는 csoclassify --export-taxonomy 를 대신 쓴다)
#------------------------------------------------------------------

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from csoclassify.classify import axes                  # noqa: E402
from csoclassify.classify import doc_rules as doc_rules_mod  # noqa: E402

# 하위호환 별칭 — 이 이름으로 쓰던 기존 테스트/스크립트가 그대로 동작하도록 유지.
default_input_path = axes.default_taxonomy_export_input_path
convert = axes.convert_mpower_json
write_taxonomy_yaml = axes.write_taxonomy_yaml
build_doc_rule_scaffold = doc_rules_mod.build_scaffold


#------------------------------------------------------------------
# 스크립트 진입점
#=> 입력 JSON 을 읽어 doc_taxonomy.yaml 을 쓰고, 곧바로 axes.load_taxonomy() 로
#   다시 읽어 검증한다(round-trip validate) — 방금 만든 파일이 실제로 CSOClassify
#   가 문제없이 로드할 수 있는지 여기서 확인하지 않으면, 문제를 다음 실행(실제
#   분류) 때에야 알게 된다. 핵심 로직은 axes.export_from_mpower_json() 하나에
#   있고, 이 함수는 그 결과를 사람이 읽는 콘솔 출력으로 바꾸는 것만 한다.
#
# -in: 없음(argparse 로 --input/--output/--scaffold-doc-rule 수집)
#
# -out: 없음(콘솔 출력)
# -out: error = 변환·검증 실패 시 SystemExit(2)
#------------------------------------------------------------------
def main():
    # 한국어 Windows(cp949) 콘솔에서 이모지/특수문자 없는 한글도 안전히 나가도록
    # cli.py 의 main() 과 같은 방식으로 출력 인코딩을 UTF-8 로 고정한다.
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    p = argparse.ArgumentParser(
        description="DOC_CLASSIFICATION JSON 내보내기 → doc_taxonomy.yaml 스냅샷 변환")
    p.add_argument("--input", default=default_input_path(),
                    help="원본 JSON 경로(기본: resources/policy/doc_classification_export.json)")
    p.add_argument("--output", default=axes.default_taxonomy_path(),
                    help="doc_taxonomy.yaml 출력 경로(기본: 런타임이 읽는 기본 경로와 동일)")
    p.add_argument("--scaffold-doc-rule", metavar="PATH", default=None,
                    help="doc_rule.yaml 골격도 함께 생성(이미 있으면 덮어쓰지 않음)")
    args = p.parse_args()

    if not os.path.isfile(args.input):
        print(f"[export_taxonomy] 원본 JSON 을 찾을 수 없습니다: {args.input}", file=sys.stderr)
        raise SystemExit(2)

    try:
        taxonomy, warnings = axes.export_from_mpower_json(args.input, args.output)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[export_taxonomy] {e}", file=sys.stderr)
        raise SystemExit(2)
    except axes.TaxonomyValidationError as e:
        print(f"[export_taxonomy] 변환 결과가 검증을 통과하지 못했습니다:\n{e}", file=sys.stderr)
        raise SystemExit(2)

    for w in warnings:
        print(f"[export_taxonomy] 경고: {w}", file=sys.stderr)

    print(f"[export_taxonomy] {args.output} 생성 완료 — "
          f"노드 {len(taxonomy)}개, 최상위 {len(taxonomy.roots)}개, "
          f"exported_at={taxonomy.exported_at}")
    for root in taxonomy.roots[:3]:
        for leaf in taxonomy.children_of(root.dc_id)[:1]:
            print(f"  예: {taxonomy.path(leaf.dc_id)}")

    if args.scaffold_doc_rule:
        scaffold = doc_rules_mod.write_scaffold(taxonomy, args.scaffold_doc_rule)
        if scaffold is None:
            print(f"[export_taxonomy] {args.scaffold_doc_rule} 이 이미 있어 "
                  f"골격 생성을 건너뜁니다(사람이 채운 내용을 덮어쓰지 않기 위함).",
                  file=sys.stderr)
        else:
            print(f"[export_taxonomy] {args.scaffold_doc_rule} 골격 생성 완료 — "
                  f"규칙 {len(scaffold['doctype_rules'])}건(terms 는 비어 있음, 채워야 동작).")


if __name__ == "__main__":
    main()
