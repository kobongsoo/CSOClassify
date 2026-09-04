#------------------------------------------------------------------
# CLI 진입점 (설계서 §6)
#=> csoclassify.exe 의 명령행을 해석하고 모드를 분기한다.
#    - --serve/--status/--stop : 데몬 제어
#    - 일반 : 파일/폴더를 처리해 파일명+벡터(+시간)를 출력
#   기본은 데몬 on. 데몬을 못 쓰면 in-process 로 자동 폴백해 항상 동작하게 한다.
#------------------------------------------------------------------

import argparse
import dataclasses
import glob
import os
import re
import sys

from . import __version__
from . import config
from . import errcodes
from . import logsetup
from . import output
from .timing import Timing


#------------------------------------------------------------------
# 인자 해석 실패도 오류 계약으로 내보내는 파서
#=> argparse 는 인자가 틀리면 제 손으로 usage 를 찍고 종료코드 2 로 죽는다.
#   그 2 는 우리 종료코드 표(2=임베딩 실패)와 뜻이 겹쳐, 받는 쪽이 "모델이
#   없구나" 하고 엉뚱한 안내를 하게 된다. 그래서 error() 를 가로채
#   '인자 오류(3)'로 바꾸고, --json-errors 면 JSON 도 함께 낸다.
#   메시지를 보고 갈래를 나누는 이유: argparse 는 어떤 규칙에 걸렸는지를
#   예외 종류가 아니라 문장으로만 알려 준다.
#
# -필드: 없음(argparse.ArgumentParser 를 그대로 쓰고 error() 만 바꾼다)
#------------------------------------------------------------------
class _ContractParser(argparse.ArgumentParser):
    #--------------------------------------------------------------
    # 인자 오류 처리 — usage 는 그대로, 종료코드만 계약대로
    #=> argparse 기본 동작(usage 출력)은 사람에게 유용하므로 남기고,
    #   종료코드와 JSON 만 계약에 맞춘다.
    #
    # -in: message = argparse 가 만든 영어 오류 문장
    #
    # -out: 없음(프로세스 종료)
    # -out: error = 언제나 SystemExit(3)
    #--------------------------------------------------------------
    def error(self, message):
        # --axis 오타는 '축 전체 무시'로 조용히 이어질 수 있어 따로 구분한다.
        if "--axis" in message and "invalid choice" in message:
            kind = "bad_axis"
        # 배타 옵션(--rule-only 와 --vector-only)을 함께 준 경우.
        elif "not allowed with argument" in message:
            kind = "mode_conflict"
        else:
            kind = "bad_args"
        errcodes.emit(kind, message)
        # --json-errors 면 usage 도 내지 않는다 — 그것도 stderr 로 나가는 사람용 글이다.
        if not errcodes.is_json():
            self.print_usage(sys.stderr)
            print(f"[csoclassify] {message}", file=sys.stderr)
        sys.exit(errcodes.exit_of(kind))


#------------------------------------------------------------------
# 버전 문자열 만들기 (앱 버전 + 번들된 ko-pii 버전)
#=> ko-pii 는 PyInstaller 가 '빌드 시점에' exe 안으로 복사해 넣는다. 그래서
#   개발 PC 에서 pip 로 ko-pii 를 올려도 이미 만들어진 exe 는 옛 버전을 계속
#   쓴다. 그런데 그 '박혀 있는 버전'을 밖에서 알아낼 방법이 없어서, 배포본이
#   실제로 어떤 검출기를 쓰는지 확인할 수가 없었다(번들에는 dist-info 가
#   남지 않는다). 그래서 --version 이 직접 물어보고 찍어 준다.
#    1) ko_pii 를 그 자리에서 import 해 __version__ 을 읽는다
#    2) 못 읽어도 --version 은 성공해야 하므로, 사유만 괄호 안에 적는다
#
# -in: 없음
#
# -out: text = "csoclassify <앱버전> (ko-pii <버전>)" 형태의 한 줄
# -out: error = 예외 없음 — ko-pii 가 없거나 버전이 없으면 "없음"/"버전미상"으로 적는다
#------------------------------------------------------------------
def version_text():
    try:
        import ko_pii
        # 아주 옛 버전은 __version__ 이 없을 수 있어 기본값을 둔다.
        kopii = getattr(ko_pii, "__version__", None) or "버전미상"
    except Exception:
        # PII 검출은 ko-pii 없이는 못 하지만, 그 진단은 실제 스캔 때 하면 된다.
        # 여기서 죽으면 "왜 안 되는지" 물어보려던 사람이 답을 못 얻는다.
        kopii = "없음"
    return f"csoclassify {__version__} (ko-pii {kopii})"


#------------------------------------------------------------------
# --version 전용 동작 — 쓸 때만 ko-pii 를 읽는다
#=> argparse 의 기본 version 액션은 문자열을 '파서를 만드는 시점'에 받는다.
#   거기에 ko-pii 버전을 끼워 넣으면 --version 을 안 쓰는 평범한 실행에서도
#   매번 ko_pii 를 import 하게 된다(실측 약 0.28초). 실시간 UI 경로라 그
#   비용을 늘 물 수는 없어서, 실제로 --version 이 들어왔을 때만 읽도록
#   액션을 따로 만든다.
#
# -필드: 없음(argparse.Action 을 그대로 쓰고 __call__ 만 바꾼다)
#------------------------------------------------------------------
class _VersionAction(argparse.Action):
    #--------------------------------------------------------------
    # --version 이 실제로 주어졌을 때 호출된다
    #=> 버전 한 줄을 찍고 정상 종료(0)한다. argparse 의 version 액션과
    #   같은 동작이라, 쓰는 쪽에서는 달라진 게 없다.
    #
    # -in: parser = 이 액션을 가진 파서(종료 처리에 쓴다)
    # -in: namespace = 파싱 중인 결과(쓰지 않음)
    # -in: values = 이 옵션의 값(nargs=0 이라 항상 None)
    # -in: option_string = 실제로 쓰인 옵션 이름(쓰지 않음)
    #
    # -out: 없음(프로세스 종료)
    # -out: error = 언제나 SystemExit(0)
    #--------------------------------------------------------------
    def __call__(self, parser, namespace, values, option_string=None):
        parser.exit(message=version_text() + "\n")


#------------------------------------------------------------------
# 인자 파서 구성
#=> 설계서 §6 의 옵션들을 argparse 로 정의한다. 긴 이름은 '--'(GNU 관례)로 통일하고
#   '-file'/'-dir' 은 하위호환 별칭으로만 유지한다.
#
# -in: 없음
#
# -out: parser = 구성된 ArgumentParser
# -out: error = 없음
#------------------------------------------------------------------
def build_parser():
    p = _ContractParser(
        prog="csoclassify",
        description="한국어 문서 → C/S/O 및 분류체계 자동분류(기본) · 임베딩 벡터(--embed) · 텍스트 추출(--text-only)",
    )
    # 입력 대상
    # 표준(GNU) 관례에 맞춰 긴 이름은 '--' 로 통일한다(--file/--dir). 기존 스크립트·
    # UI 가 쓰던 단일대시 '-file'/'-dir' 도 하위호환 별칭으로 계속 받는다.
    p.add_argument("--file", "-file", dest="file", help="대상 문서 1개 경로")
    p.add_argument("--dir", "-dir", dest="dir", help="대상 폴더(배치)")
    p.add_argument("--files-from", dest="files_from", default=None,
                   help="처리할 파일 경로 목록(한 줄에 하나, '-' 면 표준입력). "
                        "여러 폴더에 흩어진 파일을 한 프로세스로 처리 — --file/--dir 과 배타")
    # 문서 식별자(doc_id) — 설계 §7-5 · D0/D0b.
    p.add_argument("--filelist", dest="filelist", default=None,
                   help="MpowerV11 이 뽑아 준 {path, sfile_id} 목록(jsonl 또는 csv). "
                        "이 목록으로 결과 레코드의 doc_id 를 채운다. "
                        "--file/--dir 이 있으면 그쪽이 대상을 정하고 목록은 'ID 사전' "
                        "역할만 하며, 없으면 목록에 적힌 파일이 대상이 된다")
    p.add_argument("--no-doc-id", dest="no_doc_id", action="store_true",
                   help="결과 레코드에 doc_id/key 를 넣지 않는다(보안등급만 볼 때). "
                        "기본은 넣는다 — 계산 비용이 사실상 0 이고, 사람이 고친 등급을 "
                        "결과와 잇는 데도 쓰인다")
    p.add_argument("--report-missing-id", dest="report_missing_id", default=None,
                   metavar="경로",
                   help="sfile_id 를 못 얻어 폴백으로 채운 문서 목록을 이 파일에 쓴다"
                        "(jsonl). 그 문서들은 매핑 테이블 적재 대상이 아니다")
    p.add_argument("--glob", dest="glob", default="*",
                   help="배치 필터 패턴(기본 *). 여러 개는 콤마로 나열: "
                        "예) \"*.hwp,*.docx,*.pdf\" (중괄호 \"*.{hwp,docx,pdf}\" 도 가능)")
    # --dir 은 이제 '항상 하위폴더까지 재귀'가 기본이다. -r/--recursive 는 하위호환용으로
    # 받아만 주고 무시한다(줘도 안 줘도 재귀). 기존 스크립트가 깨지지 않게 유지.
    p.add_argument("-r", "--recursive", action="store_true",
                   help="(무시됨) --dir 은 항상 하위 폴더까지 재귀 탐색합니다")

    # 모델/청킹/출력
    p.add_argument("--model", default=config.DEFAULT_MODEL, help="임베딩 모델 별칭")
    # default 를 None 으로 두는 이유: "사용자가 --format 을 줬는가"를 알아야
    # --out 확장자로 추론할지 말지를 정할 수 있다. 기본값을 여기서 박아 두면
    # 명시한 "json" 과 기본값 "json" 을 구분할 수 없다. 실제 기본값은
    # resolve_format() 이 넣는다.
    p.add_argument("--format", dest="fmt", choices=["text", "json", "jsonl"],
                   default=None,
                   help=f"출력 형식(미지정 시 --out 확장자로 판단, 그것도 없으면 {config.DEFAULT_FORMAT})")
    p.add_argument("--out", dest="out", help="결과 저장 파일(미지정 시 stdout)")
    p.add_argument("--per-chunk", action="store_true", help="청크별 벡터 출력")
    p.add_argument("--max-tokens", type=int, default=config.DEFAULT_MAX_TOKENS, help="청크 최대 토큰")
    p.add_argument("--overlap", type=int, default=config.DEFAULT_OVERLAP, help="청크 겹침 토큰")
    p.add_argument("--normalize", dest="normalize", action="store_true", default=config.DEFAULT_NORMALIZE)
    p.add_argument("--no-normalize", dest="normalize", action="store_false", help="L2 정규화 끄기")
    p.add_argument("--precision", choices=["f32", "f16"], default=config.DEFAULT_PRECISION, help="출력 벡터 정밀도")

    # 시간/텍스트 보존/디버그
    p.add_argument("--timing", dest="timing", action="store_true", default=True)
    p.add_argument("--no-timing", dest="timing", action="store_false", help="처리 시간 출력 끄기")
    p.add_argument("--save-text", dest="save_text", nargs="?", const="__DEFAULT__", default=None,
                   help="추출 텍스트 보존(경로 생략 시 ./_extracted)")
    p.add_argument("--text-only", action="store_true", help="벡터 없이 추출 텍스트만 출력")
    # 추출 방식 — 기본은 '자체 파서'(하이브리드). Rust 포트와 추출 텍스트를 맞추기
    # 위한 기본값이다(config.DEFAULT_HYBRID_PARSE 주석 참고).
    p.add_argument("--hybridparse", dest="hybridparse", action="store_true",
                   default=config.DEFAULT_HYBRID_PARSE,
                   help="내용 감지로 포맷별 전용 파서 추출: pdf→pypdfium2, hwp→HWP5, "
                        "hwpx→zip/OWPML, doc/ppt→자체파서, xls→xlrd, docx/xlsx/pptx→python-*, text→직접읽기. "
                        "전용 파서 실패 시 snf 폴백(snf 없으면 그 파일만 미분류). "
                        "[기본값이라 따로 줄 필요 없다 — 되돌리려면 --synap-only] "
                        "설계: plan/CSO_HybridParse.html")
    p.add_argument("--synap-only", dest="hybridparse", action="store_false",
                   help="종전 방식 — 모든 포맷을 사이냅(snf_exe) 하나로만 추출한다. "
                        "자체 파서가 특정 문서에서 이상할 때 비교용으로 쓴다.")

    # 처리 모드 — 기본은 C/S/O 자동 분류다. 임베딩 벡터만 필요하면 --embed,
    # 추출 텍스트만 필요하면 --text-only 를 준다.
    p.add_argument("--embed", dest="embed", action="store_true",
                   help="임베딩 벡터만 출력(분류 없이). 미지정 시 기본은 C/S/O 분류")
    # (하위호환) 예전의 --classify 는 이제 '기본 동작'이라 붙일 필요가 없다. 기존
    # 실패를 기계가 읽을 수 있게 낸다(설계: plan/CLI-오류출력-설계.html).
    # 옵션을 안 주면 오늘과 똑같이 동작한다 — 지금 이 CLI 를 쓰는 쪽의 stdout 파싱이
    # 깨지지 않도록 '선택'으로 둔다.
    # 축약본만 받으면 "이 문서가 왜 C 인가"에 답할 수 없다. 판정에 쓰인 신호와
    # 규칙 id·건수만 요약해 얹는다(원문 값은 애초에 저장하지 않으므로 새는 것이 없다).
    p.add_argument("--simple-why", dest="simple_why", action="store_true",
                   help="--simple 에 판정 근거 요약(why)을 더한다"
                        "(어느 신호가 정했는지 · 어느 규칙이 몇 건). --simple 을 포함한다")
    p.add_argument("--json-errors", dest="json_errors", action="store_true",
                   help="실패할 때 stdout 에 오류 JSON 한 줄을 낸다"
                        "({\"error\":{code,kind,message,path}})")
    # 스크립트·UI 가 계속 넘겨도 오류 없이 받아들이되(무시), 도움말/문서에는 노출하지 않는다.
    p.add_argument("--classify", dest="classify", action="store_true",
                   help=argparse.SUPPRESS)
    # C/S/O 자동 분류(규칙 스캔 Signal A)
    p.add_argument("--rules", dest="rules", default=None,
                   help="분류 규칙셋 경로(기본 resources/policy/cso_rules.yaml)")
    p.add_argument("--failsafe", dest="failsafe", nargs="?", const="S", default=None,
                   help="규칙 미검출 시 부여할 기본등급(값 생략 시 S)")
    p.add_argument("--check-rules", dest="check_rules", action="store_true",
                   help="규칙셋(cso_rules.yaml)의 등급 값만 검사하고 종료(문서는 읽지 않음). "
                        "정상 0, 검증 실패 4")
    # 업무 분류(doctype) 축 — 문서분류체계 연동(설계서 §3~6).
    p.add_argument("--taxonomy", dest="taxonomy", default=None,
                   help="분류체계 스냅샷 경로(기본 resources/policy/doc_taxonomy.yaml). "
                        "--export-taxonomy 로 생성(저장 경로로도 쓰임)")
    p.add_argument("--doc-rules", dest="doc_rules", default=None,
                   help="업무분류 규칙셋 경로(기본 resources/policy/doc_rule.yaml). "
                        "없으면 doctype 축을 건너뛰고 security 만 처리(경고 후 계속)")
    p.add_argument("--axis", dest="axis", choices=["security", "doctype"], default=None,
                   help="처리할 축을 하나로 제한(미지정 시 가능한 축 전부). "
                        "doctype 은 PII 검출을 생략해 더 빠르다")
    p.add_argument("--conflict", dest="conflict", default=None,
                   help="doctype 축의 충돌 전략을 실행 시 덮어쓰기: 예) --conflict doctype=top_n:3 "
                        "(security 축은 서열이 있어 덮어쓸 수 없음 — 주면 오류)")
    # 분류체계 스냅샷 내보내기(문서 처리 없음, --export-taxonomy 단독 모드).
    p.add_argument("--export-taxonomy", dest="export_taxonomy", action="store_true",
                   help="DOC_CLASSIFICATION JSON(MpowerV11 내보내기)을 --taxonomy 경로에 "
                        "doc_taxonomy.yaml 스냅샷으로 변환하고 종료(문서는 읽지 않음). "
                        "--file/--dir 불필요")
    p.add_argument("--export-input", dest="export_input", default=None,
                   help="--export-taxonomy 의 원본 JSON 경로(기본 "
                        "resources/policy/doc_classification_export.json)")
    p.add_argument("--scaffold-doc-rule", dest="scaffold_doc_rule", action="store_true",
                   help="--export-taxonomy 와 함께 쓰면 --doc-rules 경로에 doc_rule.yaml "
                        "골격(빈 terms, filename 만 title 로 채움)도 생성. 이미 있으면 건너뜀")
    p.add_argument("--sync-doc-rule", dest="sync_doc_rule", action="store_true",
                   help="분류 체계(doc_taxonomy.yaml)를 훑어 --doc-rules 파일에 규칙을 "
                        "채운다. 빠진 분류는 새로 만들고, 이름·띄어쓰기 변형·유의어 "
                        "사전(synonyms/)의 같은 뜻 다른 말까지 넣는다. 이미 있는 파일에도 "
                        "덧붙이므로 --scaffold-doc-rule 과 달리 건너뛰지 않는다")
    p.add_argument("--no-fill-blank", dest="sync_fill_blank", action="store_false",
                   default=True,
                   help="--sync-doc-rule 에서 '단어가 하나도 없는 기존 규칙'을 "
                        "채우지 않는다(기본은 채운다)")
    p.add_argument("--sync-enrich", dest="sync_enrich", action="store_true",
                   help="--sync-doc-rule 에서 '이미 단어가 있는 규칙'에도 빠진 유의어만 "
                        "덧붙인다(사람이 적어 둔 말은 지우지 않는다). 기본은 하지 않음")
    p.add_argument("--with-vector", dest="with_vector", action="store_true",
                   help="분류 시 모든 문서에 임베딩 벡터 산출(구 동작, 모델 필요)")
    p.add_argument("--embed-needed", dest="embed_needed", action="store_true",
                   help="임베딩을 '보류(미확정)'와 'seed_eligible' 문서에만 수행(확정문서 임베딩 생략, 속도↑)")
    p.add_argument("--propagate", dest="propagate", default=None,
                   help="1차 레코드(jsonl)에 임베딩 라벨 전파를 적용해 재분류")
    p.add_argument("--seeds", dest="seeds", default=None,
                   help="전파 비교 기준 seed 저장소(class_seed.jsonl). 없으면 코퍼스 내부 seed 사용")
    p.add_argument("--with-text", dest="with_text", action="store_true",
                   help="분류 결과 레코드에 추출(정제) 텍스트를 함께 저장(기본 미포함=프라이버시)")
    p.add_argument("--with-pii", dest="with_pii", action="store_true",
                   help="[프라이버시 주의] 검출된 원문 PII 값(주민번호·카드번호 등 실제 값)을 "
                        "결과의 pii 필드에 함께 저장(기본 미포함). 결과 파일 취급에 주의")
    p.add_argument("--auto-propagate", dest="auto_propagate", action="store_true",
                   help="분류 직후 보류 문서를 seed(--seeds 또는 내부)로 전파까지 수행(보류 임베딩 자동 활성화)")
    # 업무분류 2단계(벡터 비교)를 켜려면 seed 가 있어야 하는데, 사람이 라벨링할
    # 수는 없다. 규칙이 아주 자신 있게 분류한 문서를 씨앗으로 재활용한다(재설계 10장).
    p.add_argument("--make-doctype-seeds", dest="make_doctype_seeds", default=None,
                   metavar="경로",
                   help="업무분류 seed 를 만들어 지정한 class_seed.jsonl 에 기록한다"
                        "(규칙 고신뢰 문서만 · 전량 임베딩 자동 활성화). "
                        "기존 보안등급 seed 는 보존하고 업무분류 seed 만 갈아 끼운다")
    p.add_argument("--seed-per-dir", dest="seed_per_dir", type=int, default=6,
                   help="--make-doctype-seeds: 한 폴더에서 뽑을 최대 seed 수(기본 6). "
                        "편향 완화용 — 한 폴더 문서 모양이 그 분류의 정의가 되는 것을 막는다. "
                        "실측(검증셋 150건)에서 3 은 너무 좁아 씨앗이 33건뿐이었고, "
                        "6 으로 풀면 48건이 되면서 정밀도·재현율이 둘 다 올랐다")
    p.add_argument("--seed-per-node", dest="seed_per_node", type=int, default=50,
                   help="--make-doctype-seeds: 한 분류에서 뽑을 최대 seed 수(기본 50)")
    # 분류 방식 배타 선택: 규칙만(rule-only) vs 벡터만(vector-only). 둘 다 안 주면 기존 동작.
    _clsmode = p.add_mutually_exclusive_group()
    _clsmode.add_argument("--rule-only", dest="rule_only", action="store_true",
                   help="규칙(cso_rules.yaml)만으로 분류 — 임베딩·전파를 하지 않음(가장 빠름). --vector-only 와 배타")
    _clsmode.add_argument("--vector-only", dest="vector_only", action="store_true",
                   help="규칙 검사 없이 임베딩 벡터를 seed 와 비교해서만 분류(--seeds 필수). --rule-only 와 배타")
    p.add_argument("--doctype-vector-only", dest="doctype_vector_only",
                   action="store_true",
                   help="업무분류(doctype)를 규칙 없이 기준 문서(class_seed.jsonl) 비교로만 "
                        "분류한다. 규칙 파일의 embed·conflict 설정은 그대로 쓴다. "
                        "security 축은 영향을 받지 않는다")
    p.add_argument("--progress", action="store_true",
                   help="파일별 진행 상황을 stderr 로 출력(형식: '[progress] 처리수/총수 경로'). UI 진행바용")

    # 출력 축약 옵션(C/S/O 분류 모드 전용). 모두 JSON 으로 출력한다.
    p.add_argument("--hash", dest="hash", action="store_true",
                   help="각 문서의 해시(SHA-256)를 결과 레코드에 hash 필드로 포함")
    # 요약(summary) 제어 — 배타: '요약만' vs '요약 제거'.
    _sumgrp = p.add_mutually_exclusive_group()
    _sumgrp.add_argument("--summary", dest="summary_only", action="store_true",
                   help="파일별 레코드 없이 '맨 끝 요약(summary)'만 JSON 으로 출력(--file/--dir)")
    _sumgrp.add_argument("--nosummary", dest="no_summary", action="store_true",
                   help="맨 끝 요약(summary) 레코드를 결과 출력에서 제거(파일별 레코드만)")
    p.add_argument("--simple", dest="simple", action="store_true",
                   help="파일별 결과를 문서명·등급·해시(+업무분류 dc_id)로 줄여서 출력")

    # 데몬 제어
    p.add_argument("--daemon", dest="daemon", action="store_true", default=config.DEFAULT_DAEMON)
    p.add_argument("--no-daemon", dest="daemon", action="store_false", help="데몬 없이 in-process 처리")
    p.add_argument("--serve", action="store_true", help="데몬 프로세스로 기동")
    p.add_argument("--status", action="store_true", help="데몬 상태 출력")
    p.add_argument("--stop", action="store_true", help="데몬 정지")
    p.add_argument("--idle-timeout", type=int, default=config.DEFAULT_IDLE_TIMEOUT,
                   help="데몬 유휴 자동종료(초). 미지정/0 = 무한(자동종료 없음). 예: --idle-timeout 600")
    p.add_argument("--num-threads", type=int, default=None, help="onnxruntime 스레드 수")

    p.add_argument("-v", "--verbose", action="store_true", help="상세 로그(화면 출력 + DEBUG)")
    p.add_argument("--log", dest="log", default=None,
                   help="로그 파일 경로(미지정 시 <exe폴더>/log/class_날짜.log)")
    # 번들된 ko-pii 버전까지 찍는다. 문자열을 미리 만들지 않고 액션으로 미루는
    # 이유는 _VersionAction 헤더 참고(평상시 실행에 ko_pii import 비용을 안 물린다).
    p.add_argument("--version", action=_VersionAction, nargs=0,
                   help="버전 출력(앱 버전 + 번들된 ko-pii 버전)")
    return p


#------------------------------------------------------------------
# 옵션 dict 구성
#=> 파싱된 args 에서 파이프라인/데몬이 쓰는 처리 옵션만 뽑아 dict 로 만든다(데몬에
#   IPC 로 넘기기 좋게 순수 값들로 구성). save-text 기본경로도 여기서 확정한다.
#
# -in: args = argparse 결과 네임스페이스
#
# -out: opts = {max_tokens, overlap, normalize, per_chunk, precision, save_dir, ...}
# -out: error = 없음
#------------------------------------------------------------------
def make_opts(args):
    # --save-text 를 값 없이 준 경우 기본 폴더로 해석.
    save_dir = args.save_text
    if save_dir == "__DEFAULT__":
        save_dir = os.path.join(os.getcwd(), "_extracted")

    return {
        "max_tokens": args.max_tokens,
        "overlap": args.overlap,
        "normalize": args.normalize,
        "per_chunk": args.per_chunk,
        "precision": args.precision,
        "save_dir": save_dir,
        "keep_page_markers": False,
        # 추출기 선택용(--hybridparse). run_text_only 처럼 args 를 직접 안 받는 경로가 읽는다.
        "hybridparse": getattr(args, "hybridparse", False),
    }


#------------------------------------------------------------------
# glob 패턴의 중괄호 확장
#=> 파이썬 표준 glob 은 셸과 달리 중괄호({a,b,c}) 확장을 지원하지 않는다. 그래서
#   확장자를 여러 개 넣고 싶을 때 쓰도록, "*.{hwp,docx,pdf}" 같은 패턴을
#   ["*.hwp","*.docx","*.pdf"] 로 직접 펼쳐 준다. 중괄호가 없으면 그대로 1개.
#   여러 그룹/중첩도 재귀로 모두 펼친다. 예)
#     "*.{hwp,docx}"        → ["*.hwp","*.docx"]
#     "report_*.{doc,docx}" → ["report_*.doc","report_*.docx"]
#
# -in: pattern = 원본 glob 패턴(중괄호 그룹 포함 가능)
#
# -out: list = 펼쳐진 패턴 리스트(중복 제거, 최소 1개)
# -out: error = 없음
#------------------------------------------------------------------
def expand_glob_braces(pattern):
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    pre, post = pattern[:m.start()], pattern[m.end():]
    result = []
    for opt in m.group(1).split(","):
        # 남은 뒷부분에 또 다른 그룹이 있을 수 있어 재귀로 마저 펼친다.
        for expanded in expand_glob_braces(pre + opt.strip() + post):
            if expanded not in result:
                result.append(expanded)
    return result


#------------------------------------------------------------------
# glob 패턴 목록 펼치기 (--glob 진입점)
#=> --glob 한 값에 패턴을 여러 개 넣는 두 표기를 모두 받는다.
#     1) 콤마로 나열   : "*.hwp,*.pdf"   (기본·권장 — 접두가 달라도 됨)
#     2) 중괄호로 나열 : "*.{hwp,pdf}"    (셸 관습, 공통 접두/접미 간결)
#   먼저 "중괄호 밖" 최상위 콤마로 패턴을 나눈 뒤(중괄호 안 콤마는 그룹 구분자라
#   건드리지 않음), 각 토큰의 중괄호를 펼쳐 합친다. 두 표기를 섞어도 된다. 예)
#     "*.hwp,*.pdf"          → ["*.hwp","*.pdf"]
#     "*.{hwp,pdf},draft_*"  → ["*.hwp","*.pdf","draft_*"]
#
# -in: spec = --glob 원본 값(콤마·중괄호로 여러 패턴 가능)
#
# -out: list = 펼쳐진 개별 glob 패턴 리스트(중복 제거, 최소 1개)
# -out: error = 없음
#------------------------------------------------------------------
def expand_glob_patterns(spec):
    # 중괄호 깊이를 세며 최상위(깊이 0) 콤마에서만 자른다.
    tokens, buf, depth = [], [], 0
    for ch in spec:
        if ch == "{":
            depth += 1
            buf.append(ch)
        elif ch == "}":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            tokens.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    tokens.append("".join(buf))

    result = []
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        for pat in expand_glob_braces(tok):
            if pat not in result:
                result.append(pat)
    return result


#------------------------------------------------------------------
# 파일 경로 목록 읽기 (--files-from)
#=> 여러 폴더에 흩어진 문서를 "한 프로세스"로 처리하려고 만든 입력 방식이다.
#   --dir 은 폴더 하나 아래만 훑을 수 있어서, 경로가 흩어져 있으면 파일마다
#   프로세스를 새로 띄우게 되고 그때마다 임베딩 모델을 다시 읽어 느려진다.
#   목록을 통째로 받으면 모델을 한 번만 읽고 전부 처리한다.
#    1) "-" 이면 표준입력, 아니면 그 파일을 UTF-8(BOM 허용)로 읽는다
#    2) 줄 앞뒤 공백을 떼고, 빈 줄과 "#" 으로 시작하는 주석 줄은 버린다
#    3) 실제 "파일"인 것만 남긴다(없는 경로·폴더는 건너뛰고 건수를 알린다)
#    4) 같은 경로가 여러 번 나오면 처음 것만 남긴다(입력 순서는 그대로 보존)
#
# -in: src = 목록 파일 경로. "-" 이면 표준입력에서 읽는다
#
# -out: files = 처리할 파일 경로 리스트(입력 순서 유지, 중복 제거)
# -out: error = 목록 파일 자체를 못 열면 OSError 를 그대로 올린다(상위에서 처리).
#               목록 "안"의 잘못된 경로는 예외 없이 건너뛰고 stderr 로 건수만 알린다
#------------------------------------------------------------------
def read_files_from(src):
    if src == "-":
        raw = sys.stdin.read()
    else:
        # 윈도우 메모장으로 저장한 목록에는 BOM 이 붙는다 — utf-8-sig 로 흡수한다.
        with open(src, "r", encoding="utf-8-sig", errors="replace") as fh:
            raw = fh.read()
    files, seen, skipped = [], set(), 0
    for line in raw.splitlines():
        # 경로에 공백이 있어 따옴표로 감싼 목록(dir /b 결과 등)도 받아 준다.
        path = line.strip().strip('"')
        if not path or path.startswith("#"):
            continue
        # 같은 문서를 두 번 임베딩하지 않도록 여기서 미리 중복을 접는다.
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(path):
            files.append(path)
        else:
            skipped += 1
    # 조용히 버리면 "왜 결과 건수가 모자라지?" 로 이어진다 — 건수만이라도 남긴다.
    if skipped:
        print(f"[csoclassify] --files-from: 파일이 아니어서 건너뜀 {skipped}건",
              file=sys.stderr)
    return files


#------------------------------------------------------------------
# 처리 대상 파일 목록 수집
#=> --file 이면 그 파일 하나, --dir 이면 폴더에서 glob 패턴에 맞는 "파일"들을 모은다.
#   --dir 은 '항상 하위 폴더까지 재귀'가 기본이다(-r/--recursive 는 무시됨).
#    1) --glob 의 콤마·중괄호를 여러 패턴으로 펼친다(다중 확장자 지원)
#    2) <dir>/**/<glob> 을 recursive=True 로 확장(항상 하위폴더 포함)
#
# -in: args = argparse 결과(dir/glob)
#
# -out: files = 처리할 파일 경로 리스트(정렬·중복제거, 디렉터리는 제외)
# -out: error = --file/--dir 둘 다 없거나 대상이 없으면 빈 리스트(상위에서 처리)
#------------------------------------------------------------------
def collect_files(args):
    # 목록 입력이 있으면 그것이 대상이다(--file/--dir 과의 동시 사용은 호출부에서 막는다).
    if getattr(args, "files_from", None):
        return read_files_from(args.files_from)
    if args.file:
        # 실제 파일일 때만 대상으로 삼는다. 없는 경로·폴더를 그대로 넘기면
        # '추출 실패' 레코드가 만들어져 결과처럼 나간다 — 아래 no_input 으로
        # 걸러 부른 쪽에 "대상이 없다"고 알린다.
        return [args.file] if os.path.isfile(args.file) else []
    if args.dir:
        found = []
        # 콤마/중괄호로 패턴 여러 개를 준 경우 패턴별로 각각 매칭해 합친다.
        # *.hwp,*.pdf 식으로 추가.
        for gp in expand_glob_patterns(args.glob):
            # --dir 은 항상 '**' 로 하위 폴더까지 재귀 탐색한다(-r 여부와 무관).
            found.extend(glob.glob(os.path.join(args.dir, "**", gp), recursive=True))
        # 매칭 결과 중 실제 파일만(폴더 제외) 대상으로, 중복 제거해 정렬한다
        # (여러 패턴에 동시에 걸린 파일이 있을 수 있음).
        return sorted({f for f in found if os.path.isfile(f)})
    # --file/--dir/--files-from 이 하나도 없고 목록만 준 경우 — 목록이 대상을 정한다
    # (설계 §7-5-2-1 "정식 운영": MpowerV11 이 '이번에 분류할 문서'를 고른다).
    # 반대로 --dir 과 함께 주면 위에서 이미 돌아갔다 — 그때 목록은 'ID 사전' 역할만 한다.
    flist = getattr(args, "_filelist", None)
    if flist is not None:
        return flist.target_paths()
    return []


#------------------------------------------------------------------
# 처리 작업 정규화 — 경로 or 튜플 → (src, label, origin)
#=> 파일 목록의 각 원소는 '읽을 경로 문자열'이거나, 압축 확장으로 생긴
#   (읽기용 실제경로 src, 표시·신호용 가상경로 label, 최상위압축 origin) 튜플이다.
#   어느 쪽이든 (src, label, origin) 로 통일해 준다.
#   (일반 파일은 src==label, origin=None 이라 예전 동작과 100% 동일하다.)
#
# -in: item = 경로 문자열 또는 (src,label) / (src,label,origin) 튜플
#
# -out: (src, label, origin) = 읽기용 경로 / 표시·신호용 경로 / 최상위 압축경로(없으면 None)
# -out: error = 없음
#------------------------------------------------------------------
def _as_job(item):
    if isinstance(item, tuple):
        if len(item) == 3:
            src, label, origin = item
            return (src, safe_text(label), safe_text(origin))
        # (src, label) 형태는 origin 없음으로 채운다.
        return (item[0], safe_text(item[1]), None)
    # 읽기용(src)은 원본 그대로 둔다 — 손대면 파일을 못 연다. 표시·기록용만 고친다.
    return (item, safe_text(item), None)


#------------------------------------------------------------------
# 결과에 실을 문자열을 '안전하게' 만들기 (파일명이 UTF-8 이 아닐 때)
#=> 리눅스에서 파일 이름은 그냥 바이트열이라 UTF-8 이 아닐 수 있다(윈도우에서
#   만든 CP949 이름을 그대로 복사한 경우 등). 파이썬은 그런 이름을 읽을 때
#   surrogateescape 로 '짝 없는 대리 문자'(\udcXX)를 끼워 넣는데, 이 문자는
#   UTF-8 로 다시 인코딩할 수 없어서 json.dumps 가 예외를 던진다.
#   그대로 두면 그런 파일 하나 때문에 배치 전체가 중단되고, 그때까지 분류한
#   결과까지 통째로 날아간다 — 한 파일의 이름 문제로 잃을 만한 것이 아니다.
#   그래서 결과·로그에 실을 때만 그 자리를 U+FFFD 로 바꾼다.
#   [주의] 파일을 여는 데 쓰는 경로에는 절대 쓰지 말 것 — 바꾼 이름으로는
#   파일을 못 연다. '읽기용 경로'와 '표시·기록용 경로'를 나눠 쓰는 이유다.
#
# -in: s = 임의 문자열(경로 등). None 이면 그대로 None 을 돌려준다
#
# -out: str|None = UTF-8 로 인코딩 가능한 문자열(문제 없으면 원본 그대로)
# -out: error = 없음
#------------------------------------------------------------------
def safe_text(s):
    # 실제 처리는 output.scrub_surrogates 한 곳에 모아 뒀다.
    #=> 예전에는 여기서 surrogateescape 만 썼는데, 그 방법은 U+DC80~U+DCFF 밖의
    #   대리 문자(깨진 문서에서 반쪽만 남은 UTF-16 대리쌍 등)를 만나면 **다시 예외를
    #   낸다.** 파일 이름뿐 아니라 본문에도 그런 글자가 들어오면서 실제로 배치가
    #   통째로 죽었다. 두 경우를 모두 막는 판이 output 쪽에 있으므로 그것을 쓴다.
    return output.scrub_surrogates(s)


# 등급 심각도(집계용): C > S > O > 미분류. 압축파일 '자체' 등급은 내부 최고 위험으로 정한다.
_GRADE_SEVERITY = {"C": 3, "S": 2, "O": 1, "none": 0, None: 0}


#------------------------------------------------------------------
# 여러 등급 중 최고 위험 등급 고르기
#=> 압축파일 안 파일들의 등급 목록에서 가장 위험한 등급(C>S>O>미분류)을 고른다.
#
# -in: grades = 등급 문자열/None 의 리스트
# -out: str|None = 최고 위험 등급(전부 미분류면 None)
# -out: error = 없음
#------------------------------------------------------------------
def _worst_grade(grades):
    best, best_sev = None, -1
    for g in grades:
        sev = _GRADE_SEVERITY.get(g, 0)
        if sev > best_sev:
            best, best_sev = g, sev
    return best


#------------------------------------------------------------------
# 압축파일 '자체'의 집계 등급 레코드 만들기
#=> 내부 파일들의 등급을 모아, 압축파일 자체에 '최고 위험 등급'을 부여한 요약 레코드를
#   만든다. contains 에 내부 등급 분포를 담아 어느 위험이 몇 건인지 보이게 한다.
#
# -in: origin = 최상위 압축파일 경로
# -in: grades = 그 압축에 속한 내부 파일들의 최종 등급 리스트
# -in: ts     = 타임스탬프 문자열
#
# -out: dict = 압축 집계 레코드(archive=True 로 표시)
# -out: error = 없음
#------------------------------------------------------------------
def _archive_record(origin, grades, ts):
    from collections import Counter
    dist = Counter(g if g in ("C", "S", "O") else "none" for g in grades)
    worst = _worst_grade(grades)
    return {
        "file": origin,
        "grade": worst,
        "archive": True,                 # 이 레코드는 '압축파일 자체'의 집계임을 표시
        "method": "archive_rollup",
        "decided_by": ["archive"],
        # 내부 등급 분포(어느 위험이 몇 건인지) — 압축만 봐도 위험도를 파악.
        "contains": {"total": len(grades), "C": dist["C"], "S": dist["S"],
                     "O": dist["O"], "unclassified": dist["none"]},
        "ts": ts,
    }


#------------------------------------------------------------------
# 추출 실패 문서의 결과 레코드
#=> 텍스트를 못 뽑은 문서를 '없던 일'로 넘기지 않고, 등급 없는(=보류) 레코드로
#   남긴다. 그래야 화면·집계에서 그 문서가 보이고 사람이 처리할 수 있다.
#    1) 등급은 매기지 않는다 — 내용을 못 봤으므로 어떤 등급도 근거가 없다.
#       (failsafe 도 적용하지 않는다. '못 읽었다'와 '읽었는데 신호가 없다'는
#        다른 상태이고, 전자를 자동으로 등급 매기면 사람이 확인할 기회를 잃는다.)
#    2) 왜 실패했는지 error 필드에 남긴다 — 사람이 다음에 뭘 해야 할지 알아야 한다
#    3) 업무분류 축이 켜져 있으면 본문 없이(파일명·경로만으로) 한 번 훑는다.
#       이름만으로 잡히는 것이 있으면 건지고, 없으면 '미분류'로 남는다.
#
# -in: path       = 문서 경로(표시용)
# -in: err        = ExtractError 등 실패 사유
# -in: ruleset    = 규칙셋(rule_version 표기용)
# -in: doc_rules  = doc_rules.DocRuleSet | None
# -in: taxonomy   = axes.Taxonomy | None
#
# -out: dict = 결과 레코드(grade=None, method="extract_failed")
# -out: error = 없음
#------------------------------------------------------------------
def _extract_failed_record(path, err, ruleset, doc_rules=None, taxonomy=None):
    # now_iso·build_record 는 무거운 모듈을 import 시점에 끌어오지 않으려는 이 파일의
    # 관례를 따라 그때 가져온다.
    from .classify import now_iso
    from .classify.engine import build_record

    # 빈 본문으로 정상 경로를 그대로 태운다.
    #=> 본문을 못 읽었어도 경로(Signal C)·파일명(Signal D)은 본문과 무관하게 유효하다.
    #   내용 기반 신호(rule·sensitive·stamp)는 빈 텍스트라 자연히 아무것도 못 찾는다.
    #   [왜 바꿨나] 예전에는 여기서 signals 를 통째로 비워, 보안서버 경로의 읽을 수
    #   없는 문서가 아무 등급 없이 나갔다. 그런데 같은 파일 안에서도 처리가 갈렸다 —
    #   본문이 '거의' 없으면(_mark_no_body) 경로 신호를 살리고, '완전히' 실패하면
    #   버렸다. 둘 다 "본문을 못 읽었다"인데 대응이 달랐던 것이다. 업무분류 축은
    #   이미 파일명·경로로 돌고 있었으니 보안 축만 유독 버려지던 셈이기도 하다.
    #   (실측: D:\분류함 1,492건 비교에서 보안서버 경로의 스캔 PDF 4건이 Python 만
    #    미분류로 나갔다. Rust 판은 같은 문서를 경로 신호로 C 로 잡는다.)
    #   [failsafe 는 주지 않는다] '무신호 기본등급'은 "읽었는데 신호가 없다"에 쓰는
    #   것이지 "못 읽었다"에 쓰는 것이 아니다. 못 읽은 문서를 자동으로 등급 매기면
    #   사람이 확인할 기회를 잃는다.
    rec = build_record(path, "", ruleset, ts=now_iso(), failsafe=None, vector=None,
                       rules_enabled=True, doc_rules=doc_rules, taxonomy=taxonomy)

    rec["error"] = {"stage": "extract", "reason": str(err)}

    # 못 읽은 문서는 전파 seed 가 될 수 없다 — 근거를 못 본 문서를 다른 문서의
    # 기준으로 삼으면 오분류가 스스로를 강화한다.
    rec["seed_eligible"] = False

    # 아무것도 못 정했으면 '봤는데 없더라'(unclassified)가 아니라 '못 읽었다'로 적는다.
    # 조건과 표기를 _mark_no_body 와 같게 맞춰, 두 실패 경로가 같은 모양을 내게 한다.
    if not rec.get("grade") and rec.get("method") in (None, "", "unclassified"):
        rec["method"] = "extract_failed"
        sec = (rec.get("labels") or {}).get("security")
        if isinstance(sec, dict):
            sec["method"] = "extract_failed"
    return rec


#------------------------------------------------------------------
# 결과 레코드에 문서 식별자 달기 (설계 §7-5)
#=> 지금까지 결과와 사람의 수정을 잇는 유일한 키가 '경로 문자열'이었다. 경로는
#   문서의 주소이지 신분증이 아니라서, 폴더를 옮기거나 드라이브 문자 대소문자만
#   달라져도 그 문서에 쌓아 둔 판단 이력이 끊긴다(실측 4건 유실).
#   그래서 변하지 않는 이름표(doc_id)와 정규화 경로(key)를 함께 심는다.
#    1) 목록(--filelist)에 있으면 그 sfile_id 를 쓴다 — 시스템이 이미 정한 정체성
#    2) 없으면 내용 해시 → 그것도 안 되면 경로 해시로 폴백하고, 어느 것을 썼는지 남긴다
#    3) 목록의 hash 와 실제 파일이 다르면 경고만 세어 둔다(F6 — 수정됐어도 같은 문서다)
#
#   [왜 stats 를 따로 세나] 폴백으로 채워진 문서는 매핑 테이블에 넣으면 안 된다
#   (그 값은 MpowerV11 문서와 이어지지 않아 같은 문서가 두 건으로 들어간다 — R14).
#   그런데 이건 아무 오류도 내지 않는 조용한 누적이라, 건수를 요약 첫 줄에 띄운다.
#
# -in: rec   = 완성된 결과 레코드(제자리에서 고친다)
# -in: path  = 표시용 경로(rec["file"] 과 같은 값)
# -in: src   = 실제로 읽은 경로(압축 내부 파일이면 임시 실경로). None 이면 path
# -in: flist = filelist.FileList 또는 None
# -in: stats = 집계 dict(sfile_id/content/path/case/hash_mismatch 키를 늘린다)
#
# -out: 없음(rec 을 제자리에서 고친다)
# -out: error = 없음
#------------------------------------------------------------------
def _attach_doc_id(rec, path, src, flist, stats):
    from . import filelist as filelist_mod
    doc_id, source, key, matched = filelist_mod.resolve_doc_id(path, src, flist)
    rec["doc_id"] = doc_id
    rec["doc_id_source"] = source
    rec["key"] = key
    stats[source] = stats.get(source, 0) + 1
    # 대소문자만 달라 구제된 경우는 조용히 넘기지 않고 그 사실을 레코드에 남긴다.
    if matched == "case":
        rec["rematched_by"] = "case"
        stats["case"] = stats.get("case", 0) + 1
    # F6 — 목록이 준 해시와 실제 파일이 다르면 문서가 수정된 것이다. 같은 문서이므로
    # 처리는 그대로 계속하고 건수만 센다.
    if flist is not None and source == "sfile_id":
        entry, _ = flist.lookup(path)
        if entry is not None and entry.hash:
            actual = _file_hash(src or path)
            if actual and actual != entry.hash:
                stats["hash_mismatch"] = stats.get("hash_mismatch", 0) + 1


#------------------------------------------------------------------
# 본문에 쓸 만한 글자가 있었나
#=> 스캔본(이미지) PDF 는 추출기가 예외를 던지지 않는다. 장식기호 몇 개를
#   돌려주고 성공한 척한다. 길이로 가려내지 않으면 '미분류'와 섞여 버린다.
#    1) 공백류를 모두 지운 뒤 글자 수를 센다(줄바꿈만 잔뜩인 파일을 거르려고)
#    2) 임계값(config.MIN_TEXT_LEN)보다 적으면 '읽을 글자가 없었다'로 본다
#
# -in: text = 정제된 추출 본문
#
# -out: (부족한가, 글자수) = (True/False, 공백 제외 글자 수)
# -out: error = 없음
#------------------------------------------------------------------
def _body_too_short(text):
    n = len(re.sub(r"\s", "", text or ""))
    return n < config.MIN_TEXT_LEN, n


#------------------------------------------------------------------
# '본문을 못 읽었다' 표식 달기
#=> 분류 결과는 그대로 두고 표식만 더한다 — 파일명·경로 신호는 본문과
#   무관하고, 몇 글자라도 규칙에 걸렸으면 그 등급이 맞다. 다만 아무것도
#   못 정했다면 method 를 'unclassified' 로 두지 않는다. 그 말은 "봤는데
#   없더라"라는 뜻이라 사실과 다르다.
#
# -in: rec = 완성된 레코드(제자리에서 고친다)
# -in: n   = 실제로 얻은 글자 수(공백 제외)
#
# -out: 없음(rec 을 제자리에서 고친다)
# -out: error = 없음
#------------------------------------------------------------------
def _mark_no_body(rec, n):
    rec["error"] = {"stage": "extract", "text_len": n,
                    "reason": "본문 텍스트가 거의 없습니다 — 스캔본(이미지)이거나 "
                              "빈 문서일 수 있습니다. OCR 이나 사람 확인이 필요합니다."}
    if not rec.get("grade") and rec.get("method") in (None, "", "unclassified"):
        rec["method"] = "extract_failed"
        sec = (rec.get("labels") or {}).get("security")
        if isinstance(sec, dict):
            sec["method"] = "extract_failed"


#------------------------------------------------------------------
# 전체 레코드를 '읽는 차례'로 다시 담기
#=> 만들어진 순서 그대로 내보내면 눈에 안 들어온다 — 가장 궁금한 업무분류가
#   labels 안에 묻혀 열 번째에 있고, 판정 근거(signals)가 결과보다 먼저 나오며,
#   버전 칸이 여기저기 흩어져 있었다.
#    ① 무엇을      file · hash
#    ② 어떻게 됐나  grade · doctype · confidence · method · decided_by · error
#    ③ 왜          labels(축별 상세) · signals(근거)
#    ④ 부속        seed_eligible · 버전 3개 · ts · elapsed_ms
#   앞 네 칸이 --simple 과 같아, 축약본이 전체의 '앞부분만 떼어낸 것'이 된다.
#
#   [doctype 을 최상위로] labels.doctype.values 에서 뽑은 같은 값이라 새로 만드는
#   정보가 아니다. 축이 돌았을 때만 넣는다 — 빈 배열이 나가면 "분류 못 함"과
#   "축 안 씀"이 구분되지 않는다(--simple 과 같은 규약).
#
#   [모르는 칸도 잃지 않는다] 표에 없는 칸은 뒤에 그대로 붙인다. 나중에 새 칸이
#   생겼을 때 이 함수가 조용히 지워 버리면 가장 찾기 어려운 사고가 된다.
#
# -in: rec = 결과 레코드
#
# -out: dict = 같은 내용, 차례만 바뀐 새 dict
# -out: error = 없음
#------------------------------------------------------------------
# doc_id·key 는 --simple 네 칸 바로 뒤에 둔다 — 문서를 잇는 키라 눈에 띄어야 하고,
# 앞 네 칸(=--simple)의 차례를 건드리면 "축약본은 전체의 앞부분"이라는 약속이 깨진다.
_REC_ORDER = ("file", "hash", "grade", "doctype",
              "doc_id", "doc_id_source", "key", "rematched_by",
              "confidence", "method",
              "decided_by", "error", "labels", "signals", "pii", "vector",
              "seed_eligible", "rule_version", "taxonomy_version",
              "doctype_rule_version", "ts", "elapsed_ms")


def _order_record(rec):
    if not isinstance(rec, dict):
        return rec
    out = {}
    dt = (rec.get("labels") or {}).get("doctype")
    for key in _REC_ORDER:
        if key == "doctype":
            # 축이 돌았을 때만 — labels.doctype 이 있을 때가 그때다.
            if isinstance(dt, dict):
                out["doctype"] = [v.get("dc_id") for v in (dt.get("values") or [])
                                  if v.get("dc_id")]
            continue
        if key in rec:
            out[key] = rec[key]
    # 표에 없는 칸은 잃지 않고 뒤에 붙인다.
    for key, val in rec.items():
        if key not in out:
            out[key] = val
    return out


#------------------------------------------------------------------
# 판정 근거 요약 만들기 (--simple-why)
#=> 축약본 4칸으로는 "왜 이 등급인가"를 댈 수 없다. 그렇다고 전체 레코드를
#   주면 26배로 커진다. 판정에 실제로 쓰인 것만 추린다 — 전체의 15% 쯤이다.
#
#   [축별로 묶는다] 레코드 자신의 labels.security / labels.doctype 갈래를 그대로
#   따른다. 평평하게 늘어놓으면 보안등급의 conf 를 업무분류의 값으로 오해한다
#   (Rust 는 키를 사전순으로 내보내 dt 가 conf 와 hits 사이에 끼기까지 했다).
#    1) security.by   : 어느 신호가 등급을 정했는가(rule·stamp·path·name·sensitive)
#    2) security.conf : 그 판정의 확신(= labels.security.confidence)
#    3) security.hits : 걸린 규칙의 id 와 건수만 (이름·용어·원문은 넣지 않는다)
#    4) doctype       : 업무분류 후보의 dc_id·확신과 **판정 경로(by)**
#
#   [doctype 의 by 가 왜 중요한가] 같은 0.91 이라도 뜻이 다르다.
#     rule  = 우리가 정한 낱말이 제목·머리·파일명 여러 군데서 나왔다
#     embed = 이미 분류해 둔 기준 문서와 닮았다
#   검토자가 다르게 봐야 하는 값이라 갈라 준다.
#
#   [원문을 넣지 않는 이유] 이 도구의 불변식이다 — 매칭된 원문 값은 결과에
#   저장하지 않는다(주민번호 12건 검출, 이 아니라 그 번호 자체는 안 남긴다).
#   근거 요약도 그 규칙을 그대로 따른다.
#
# -in: rec = 완성된 결과 레코드
#
# -out: dict = {"security": {"by","conf","hits"}, ["doctype": [{"dc","c","by"}…]]}
# -out: error = 없음(신호가 없으면 hits 가 빈 목록)
#------------------------------------------------------------------
def _why_record(rec):
    sec = {"by": rec.get("decided_by") or [], "conf": rec.get("confidence")}
    hits = []
    for sig, val in (rec.get("signals") or {}).items():
        if not isinstance(val, dict) or not val.get("grade"):
            continue
        got = val.get("hits") or []
        if got:
            for h in got:
                if isinstance(h, dict):
                    hits.append({"sig": sig, "id": h.get("id"), "n": h.get("count")})
        else:
            # path·embed 처럼 '걸린 규칙 목록'이 없는 신호는 출처만 남긴다.
            hits.append({"sig": sig, "src": val.get("source") or val.get("seed")})
    sec["hits"] = hits
    why = {"security": sec}
    dt = (rec.get("labels") or {}).get("doctype")
    if isinstance(dt, dict):
        # stage 는 이 후보가 규칙 스캔에서 나왔는지(rule) 기준 문서 비교에서
        # 나왔는지(embed) 말해 준다 — 같은 숫자라도 뜻이 달라 반드시 함께 낸다.
        vals = [{"dc": v.get("dc_id"), "c": round(v.get("confidence") or 0, 2),
                 "by": v.get("stage") or ("embed" if "embed" in (v.get("from") or [])
                                          else "rule")}
                for v in (dt.get("values") or []) if v.get("dc_id")]
        if vals:
            why["doctype"] = vals
    return why


#------------------------------------------------------------------
# 파일 해시(SHA-256) 계산
#=> 문서 '내용'의 지문을 만든다. 같은 문서인지·변조됐는지 판별, 중복 제거 등에 쓴다.
#   큰 파일도 메모리 폭발 없이 1MB 씩 나눠 읽어 갱신한다.
#
# -in: path = 해시를 구할 파일 경로(압축 내부 파일이면 그 임시 실경로)
# -in: algo = 해시 알고리즘 이름(기본 sha256)
#
# -out: str = 소문자 16진수 해시 문자열
# -out: error = 읽기 실패(OSError) 시 None
#------------------------------------------------------------------
def _file_hash(path, algo="sha256"):
    import hashlib
    h = hashlib.new(algo)
    try:
        with open(path, "rb") as f:
            # 1MB 단위로 읽어 대용량도 안전하게 누적 해시.
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


#------------------------------------------------------------------
# --simple 용 레코드 축약(문서명·등급·해시 + 업무분류)
#=> 결과 레코드에서 사용자가 요청한 3가지(file·grade·hash)만 남긴 새 dict 를 만든다.
#   원본 레코드는 건드리지 않는다.
#
# -in: rec = 전체 결과 레코드(hash 필드가 이미 채워져 있어야 함)
# -in: why = True 면 판정 근거 요약(why)을 함께 담는다(--simple-why)
#
# -out: dict = {"file":..., "grade":..., "hash":..., ["why":...]}
# -out: error = 없음
#------------------------------------------------------------------
def _simple_record(rec, why=False):
    # 읽는 차례로 넣는다 — 무엇을(file·hash) → 어떻게 됐나(grade·doctype) → 왜(why).
    # file 과 hash 는 둘 다 '이 문서가 무엇인가'라서 붙여 두고, 판정 결과와 섞지 않는다.
    out = {"file": rec.get("file"), "hash": rec.get("hash"), "grade": rec.get("grade")}
    # 업무분류 축이 돌았을 때만 doctype 키를 붙인다. 축을 안 쓰는 배포에서 빈 배열이
    # 나가면 "분류를 못 했다"와 "축을 안 썼다"가 구분되지 않는다(설계서 4-6과 같은 규약).
    dt = (rec.get("labels") or {}).get("doctype")
    if isinstance(dt, dict):
        # 확신 내림차순은 엔진이 이미 맞춰 놓았다. 여기서는 dc_id 만 뽑아 담는다 —
        # 받는 쪽(문서중앙화)은 분류체계를 이미 갖고 있으므로 이름은 필요 없다.
        out["doctype"] = [v.get("dc_id") for v in (dt.get("values") or []) if v.get("dc_id")]
    # 못 읽은 문서에는 그 사실을 함께 싣는다. 없으면 '읽었는데 미분류'와 글자 그대로
    # 같은 모습이라, --simple 만 받는 쪽은 스캔본을 영영 못 가려낸다.
    # 성공한 문서에는 이 칸이 아예 없다(기존 모양 그대로).
    err = rec.get("error")
    if isinstance(err, dict):
        out["error"] = err.get("reason") or "extract_failed"
    if why:
        out["why"] = _why_record(rec)
    return out


#------------------------------------------------------------------
# 텍스트 추출만 수행(--text-only)
#=> 임베딩 없이 추출→정제한 텍스트를 그대로 출력한다. 모델이 필요 없으므로 항상
#   in-process 로 처리한다.
#
# -in: files    = 대상 파일 리스트(경로 or (src,label))
# -in: opts     = 처리 옵션(save_dir 등)
# -in: out_fp   = 출력 파일객체(None 이면 stdout)
#
# -out: code = 종료코드(모두 성공이면 0, 하나라도 추출 실패면 EXIT_EXTRACT_FAIL)
# -out: error = 없음(개별 실패는 stderr 로 알림)
#------------------------------------------------------------------
def run_text_only(files, opts, out_fp):
    from .extract import build_extractor
    from .extract.base import ExtractError
    from .clean import clean_text

    # --hybridparse 여부에 따라 사이냅 단독 또는 하이브리드 추출기를 만든다.
    extractor = build_extractor(hybrid=opts.get("hybridparse", False))
    code = config.EXIT_OK
    for item in files:
        # src=실제 읽을 경로(압축 내부면 임시파일), label=표시용(압축경로/내부경로).
        src, label, _origin = _as_job(item)
        try:
            raw = extractor.extract(src, save_dir=opts.get("save_dir"))
            text = clean_text(raw)
            print(f"===== {label} =====", file=(out_fp or sys.stdout))
            print(text, file=(out_fp or sys.stdout))
        except ExtractError as e:
            # ExtractError 는 이제 사유만 담으므로 경로는 여기서 한 번 붙인다.
            print(f"[csoclassify] 추출 실패: {label} :: {e}", file=sys.stderr)
            code = config.EXIT_EXTRACT_FAIL
    return code


#------------------------------------------------------------------
# 분류체계 스냅샷 노후 경고 (T13)
#=> exported_at 이 max_age_days 보다 오래됐으면 경고 문구를 만든다. DB 를 다시
#   조회하지 않고 판단할 수 있는 유일한 근거가 이 타임스탬프뿐이라(설계서 3-3 —
#   CSOClassify 는 분류 중 DB 에 전혀 접속하지 않는다), 완벽히 막지는 못해도
#   "느슨하게 동기화되지만 조용히 낡지는 않는다"를 지키기 위한 안전망이다.
#
# -in: taxonomy     = axes.Taxonomy
# -in: max_age_days = 경고 기준 일수(기본 90)
#
# -out: str | None = 경고 문구(문제 없거나 exported_at 을 못 읽으면 None)
# -out: error = 없음
#------------------------------------------------------------------
def _check_stale_taxonomy(taxonomy, max_age_days=90):
    import datetime
    try:
        exported = datetime.datetime.strptime(taxonomy.exported_at, "%Y%m%d%H%M%S")
    except (ValueError, TypeError):
        return None   # 형식이 다르면 판단하지 않는다(오탐보다 조용히 넘기는 편이 안전)
    age_days = (datetime.datetime.now() - exported).days
    if age_days <= max_age_days:
        return None
    return (f"[csoclassify] 분류체계 스냅샷(doc_taxonomy.yaml)이 {age_days}일 전 것입니다"
           f"(exported_at={taxonomy.exported_at}) — DB 와 어긋났을 수 있습니다. "
           f"scripts/export_taxonomy.py 로 다시 내보내는 것을 권장합니다.")


#------------------------------------------------------------------
# 업무분류(doctype) 축 로드 — 있으면 켜고 없으면 끄는 외장 자산 (설계서 4-6·T14·T16)
#=> doc_taxonomy.yaml·doc_rule.yaml 을 함께 로드해 taxonomy/doc_rules 를 만든다.
#   security 축과 달리 이 두 파일은 필수가 아니다 — 아래 표(4-6)대로 동작한다.
#    1) --axis security 면 애초에 시도하지 않는다(파일이 깨져 있어도 상관없다 —
#       이번 실행이 쓰지 않을 축이므로 검증할 이유가 없다)
#    2) doc_taxonomy.yaml 이 없으면: --axis doctype 명시 시 종료(4), 아니면 경고 후
#       security 만(축 자체를 끈다)
#    3) 스냅샷 검증(T0·T7·T8) 실패는 항상 치명적 — 파일이 있는데 깨졌다는 것은
#       "안 쓴다"가 아니라 "고쳐야 한다"는 뜻이라 조용히 넘기지 않는다
#    4) 스냅샷이 오래됐으면(T13) 경고만
#    5) doc_rule.yaml 도 같은 방식으로 로드(taxonomy 를 넘겨 T5·T6·T12 교차검증까지)
#    6) T6·T12 경고는 화면에 그대로 보여준다(로드 자체는 막지 않음)
#   [doc_rule.yaml 이 없을 때 — 축을 끄지 않는다]
#     규칙 파일이 없어도 분류 수단이 하나 더 남아 있다: class_seed.jsonl 과의
#     임베딩 전파. 그래서 파일이 없으면 '빈 규칙셋'(seed_only_ruleset)으로 축을
#     켠 채로 둔다 — 1차 규칙 스캔은 아무것도 못 맞히지만 전파가 라벨을 채우고,
#     seed 마저 없으면 미분류로 남아 사람이 나중에 분류한다. 축을 통째로 끄면
#     labels.doctype 키가 아예 안 생겨 전파 단계까지 건너뛰게 되므로(그러면
#     "seed 로라도 분류" 자체가 불가능) 이 구분이 중요하다.
#     반면 doc_taxonomy.yaml 은 여전히 필수다 — 노드 경로·조상 관계를 모르면
#     seed 가 준 dc_id 를 사람이 읽는 분류로 풀 수도, 조상 흡수를 할 수도 없다.
#
# -in: args = argparse 결과(taxonomy/doc_rules/axis 필드 사용)
# -in: log  = 로거
#
# -out: (taxonomy, doc_rules_set, code) — code 가 None 이 아니면 호출자는 그 값을
#        즉시 반환해야 한다(치명적 오류). code 가 None 이면 taxonomy/doc_rules_set
#        은 (축이 꺼졌으면 둘 다 None, 켜졌으면 둘 다 값이 있음 — 규칙 파일이
#        없었으면 doc_rules_set 은 규칙 0건짜리 빈 규칙셋) 중 하나다
# -out: error = 없음(모든 실패는 code 로 환원)
#------------------------------------------------------------------
def _load_doctype_axis(args, log):
    axis = getattr(args, "axis", None)
    if axis == "security":
        return None, None, None   # 이번 실행은 doctype 을 아예 안 쓴다 — 시도조차 안 함

    from .classify import axes as AX
    from .classify import doc_rules as DR

    taxonomy_path = args.taxonomy or AX.default_taxonomy_path()
    try:
        # doc_taxonomy.yaml 불러오기
        # => doc_taxonomy.yaml 파일을 읽어옴.(엠파워 분류체게 설정한 dc_id 적용을 위해..)
        taxonomy = AX.load_taxonomy(taxonomy_path)
    except FileNotFoundError as e:
        if axis == "doctype":
            # '파일 없음'은 내용 오류(4)가 아니라 부른 쪽이 고칠 문제(3)다.
            return None, None, fail_err("taxonomy_missing", f"[csoclassify] {e}",
                                        taxonomy_path)
        print(f"[csoclassify] doc_taxonomy.yaml 이 없어 업무분류(doctype) 축을 건너뜁니다.\n"
              f"              보안등급(security)만 판정합니다. 분류체계를 쓰려면\n"
              f"              scripts/export_taxonomy.py 로 내보낸 뒤 exe 옆에 두세요.",
              file=sys.stderr)
        return None, None, None
    except AX.TaxonomyValidationError as e:
        log.error("분류체계 스냅샷 검증 실패 count=%d path=%s", len(e.violations), e.path)
        return None, None, fail_err("taxonomy_invalid", f"[csoclassify] {e}", e.path)

    # doc_taxonomy.yaml 파일 점검
    # => exported_at 날짜가 현재기준 90일 이전꺼면 노후화된 분류체계 로그 남김.
    stale = _check_stale_taxonomy(taxonomy)
    if stale:
        print(stale, file=sys.stderr)
        log.warning("분류체계 스냅샷 노후 :: exported_at=%s", taxonomy.exported_at)

    # doc_rules.yaml 불러오기
    doc_rules_path = args.doc_rules or DR.default_doc_rules_path()
    try:
        doc_rules_set = DR.load_doc_rules(doc_rules_path, taxonomy=taxonomy)
    except FileNotFoundError:
        # 규칙 파일이 없다고 축을 끄지는 않는다 — 규칙이 없을 뿐 분류할 방법은
        # 아직 하나 더 있다(class_seed.jsonl 과의 임베딩 전파). 빈 규칙셋으로 축을
        # 켜 두면 1차 스캔은 아무것도 못 맞히지만 전파가 라벨을 채울 수 있고,
        # seed 마저 없으면 미분류로 남아 사람이 나중에 분류하게 된다.
        doc_rules_set = DR.seed_only_ruleset()
        print(f"[csoclassify] doc_rule.yaml 이 없어 업무분류(doctype)를 "
              f"'seed 전파 전용'으로 돌립니다.\n"
              f"              규칙 대신 class_seed.jsonl 과의 임베딩 유사도로만 분류합니다"
              f"(seed 도 없으면 전부 미분류).\n"
              f"              찾은 경로: {doc_rules_path}", file=sys.stderr)
        log.warning("doc_rule.yaml 없음 — seed 전파 전용 모드 :: path=%s", doc_rules_path)
        return taxonomy, doc_rules_set, None
    except DR.DocRuleValidationError as e:
        log.error("업무분류 규칙셋 검증 실패 count=%d path=%s", len(e.violations), e.path)
        return None, None, fail_err("doc_rules_invalid", f"[csoclassify] {e}", e.path)

    for w in doc_rules_set.warnings:
        print(f"[csoclassify] {w}", file=sys.stderr)
        log.warning("doctype 규칙 경고 :: %s", w)

    # --doctype-vector-only : 규칙 목록만 비우고 나머지(embed 임계값·conflict 전략·
    # defaults)는 파일에 적힌 그대로 쓴다. 규칙셋을 통째로 seed_only_ruleset() 으로
    # 갈아치우면 그 설정까지 기본값으로 되돌아가, 관리자가 정해 둔 임계값이 조용히
    # 무시된다. 1차 스캔이 빈손이 되므로 그 뒤 단계는 '규칙 파일이 없는 배포'와
    # 똑같이 흘러간다 — 엔진에 새 분기를 만들지 않아도 되는 이유다.
    if getattr(args, "doctype_vector_only", False):
        doc_rules_set = dataclasses.replace(doc_rules_set, rules=())
        print("[csoclassify] 업무분류: 규칙을 쓰지 않고 기준 문서(class_seed) 비교로만 "
              "분류합니다(--doctype-vector-only).", file=sys.stderr)
        log.info("doctype 벡터 전용 모드 :: path=%s", doc_rules_path)

    # doc_taxonomy.yaml, doc_rules.yaml 파일 class 리턴.
    return taxonomy, doc_rules_set, None


#------------------------------------------------------------------
# --conflict 실행 시 덮어쓰기 파싱 (설계서 6-5·T2·T10·T11)
#=> "doctype=top_n:3" 또는 "doctype=all" 형태를 (축 id, ConflictSpec) 으로 바꾼다.
#   값 검증은 doc_rules.py 의 기존 검증기(_check_conflict)를 그대로 재사용해,
#   doc_rule.yaml 에 적을 때와 실행 시 덮어쓸 때가 항상 같은 기준으로 걸러지게 한다.
#
# -in: spec = --conflict 원본 문자열
#
# -out: (axis, ConflictSpec) = 덮어쓸 축 id 와 파싱된 전략
# -out: error = 형식·값이 틀리면 ValueError(사람이 읽는 메시지 포함)
#------------------------------------------------------------------
def _parse_conflict_override(spec):
    from .classify import doc_rules as DR

    if "=" not in spec:
        raise ValueError(f"--conflict 형식이 올바르지 않습니다: {spec!r} (예: doctype=top_n:3)")
    axis, value = (part.strip() for part in spec.split("=", 1))

    if ":" in value:
        strategy, n_str = value.split(":", 1)
        try:
            n = int(n_str)
        except ValueError:
            raise ValueError(f"--conflict 의 n 값이 정수가 아닙니다: {n_str!r}")
        raw = {"strategy": strategy, "n": n}
    else:
        raw = value

    violation = DR._check_conflict(raw)
    if violation:
        raise ValueError(f"--conflict {spec!r} — {violation['detail']}")
    return axis, DR._parse_conflict(raw)


#------------------------------------------------------------------
# labels.doctype → (뿌리, 노드) 쌍 목록 (요약 집계용)
#=> rec["labels"]["doctype"]["values"] 의 각 후보에서 path 문자열의 첫 조각(뿌리
#   카테고리)과 마지막 조각(실제 걸린 노드 이름)만 뽑는다. taxonomy 객체가 없어도
#   되도록 이미 레코드에 박힌 path 문자열만 쓴다(설계서 4-4의 "> " 구분자 그대로).
#
# -in: doctype_label = rec["labels"]["doctype"] dict
#
# -out: list[(str, str)] = (뿌리 title, 노드 title) 쌍(후보가 없으면 빈 목록)
# -out: error = 없음
#------------------------------------------------------------------
def _doctype_breakdown(doctype_label):
    pairs = []
    for v in (doctype_label.get("values") or []):
        segments = [s for s in (v.get("path") or "").split(" > ") if s]
        if not segments:
            segments = [v.get("dc_id", "?")]
        pairs.append((segments[0], segments[-1]))
    return pairs


#------------------------------------------------------------------
# C/S/O 보안등급 & 업무분류 분류 처리 (기본 모드 · 예전 --classify)
#=> 추출→정제한 텍스트에 규칙 스캔(Signal A)을 걸어 보안등급(C/S/O)과 업무분류(doctype)
#   를 동시에 수행하여 문서마다 분류 결과 레코드를 출력한다.
#    1) 규칙셋 1회 로드: CSO 보안등급(cso_rules.yaml) + 업무분류(doc_rule.yaml, 선택)
#    2) 파일마다: 추출→정제→build_record(규칙/경로/파일명/임베딩으로 등급·분류 산출) → jsonl/json
#    3) 마지막에 CSO 등급 분포 + 업무분류 롤업 요약을 stderr 로 출력(오탐률/분류현황 실측용)
#   [프라이버시] 추출 텍스트는 기본적으로 디스크에 남기지 않는다(save_dir=None → 자동삭제).
#     단 --with-text 를 주면 결과 레코드에 정제 텍스트를 rec["text"] 로 함께 저장한다.
#   [진행표시] args.progress 면 파일마다 "[progress] 처리수/총수 경로" 를 stderr 로
#     흘려 보낸다(성공/실패 무관). UI 가 이 줄을 읽어 진행바·경과시간을 그린다.
#   [보안등급(security)] 임베딩은 등급 결정엔 안 쓰이고(1차 분류는 규칙/경로/파일명만),
#     전파(2차)의 '보류 구제 대상'과 '내부 seed 기준'에만 필요하다. 그래서:
#       · --with-vector : 모든 문서 임베딩(구 동작, RAG 등 전량 벡터가 필요할 때)
#       · --embed-needed: 보류(grade=None)이거나 seed_eligible 인 문서만 임베딩
#                         → 규칙으로 확정된 대다수 문서의 임베딩을 건너뛰어 대폭 빨라짐
#       · 둘 다 없음     : 임베딩 안 함(가장 빠른 규칙-only 분류)
#   [업무분류(doctype)] doc_rule.yaml 이 있으면 활성화. 규칙(내용 기반) + 임베딩(벡터 비교)
#     으로 업무분류 후보를 산출하고, seed 전파로 미분류를 구제한다. 없으면 미활성화(축 자체 생략).
#   [전파(자동)] 기본 분류는 보류 문서를 seed 와 임베딩 비교해 자동 전파한다:
#     seed 파일(--seeds 또는 exe 옆 class_seed.jsonl)이 '있으면' auto_prop 을 자동으로 켜고,
#     보류 문서 벡터가 필요하므로 임베딩(needed)도 자동 활성화한다. seed 파일이 없으면
#     전파 없이 규칙만으로 끝낸다(레코드 스트리밍). --auto-propagate 로 명시할 수도 있다.
#     전파는 '외부 seed(class_seed.jsonl) + 내부 seed_eligible'을 함께 기준으로 쓴다.
#   [분류방식 배타옵션]
#       · --rule-only  : 규칙(cso_rules.yaml + doc_rule.yaml)만으로 분류. seed 가 옆에 있어도
#                        임베딩·전파를 아예 안 한다(순수 규칙 분류를 보장하는 명시적 차단, 가장 빠름).
#       · --vector-only: 규칙 검사를 하지 않고(rules_enabled=False → 전부 보류 레코드),
#                        전량 임베딩 후 seed 와 벡터 비교(전파)로만 등급을 정한다.
#                        비교 기준 seed 파일(--seeds)이 반드시 있어야 한다(없으면 인자 오류).
#   [--axis 축 선택]
#       · (기본)        : CSO 보안등급만 분류(업무분류 축 제외, 가장 빠름)
#       · --axis doctype: 업무분류만 수행(CSO 보안등급 계산 생략, PII 검출 포함)
#
# -in: files  = 대상 파일 리스트
# -in: args   = argparse 결과(fmt/rules/failsafe 등)
# -in: out_fp = 출력 파일객체(None 이면 stdout)
#
# -out: code = 종료코드(모두 성공 0, 하나라도 추출 실패면 EXIT_EXTRACT_FAIL)
# -out: error = 없음(개별 실패는 stderr + 로그로 알림)
#------------------------------------------------------------------
def run_classify(files, args, out_fp):
    import json
    import time

    from . import PROCESS_START_PERF   # 프로세스(파이썬 진입) 시작 시각 — startup 측정 기준
    from .extract import build_extractor
    from .extract.base import ExtractError
    from .clean import clean_text
    from .classify import (load_rules, build_record, now_iso, default_seed_path,
                           RuleSetValidationError)

    log = logsetup.get_logger("csoclassify.cli")

    #-----------------------------------------------------------
    # C/S/O 분류 규칙파일 로딩.
    #-----------------------------------------------------------
    # 규칙셋은 파일마다 다시 읽지 않도록 한 번만 로드해 재사용한다.
    # 규칙셋은 외장 파일(exe 옆)이라 누락이 흔하다 → 원시 스택 대신 안내 후 정상 종료.
    try:
        # => C/S/O 분류 cso_rules.yaml 파일을 불러온다.
        ruleset = load_rules(args.rules)
    except FileNotFoundError as e:
        log.error("규칙셋 로드 실패 :: %s", e)
        # 예외가 실제로 뒤진 경로를 알고 있으면 그것을 쓴다(--rules 를 안 준 경우 기본 경로).
        return fail_err("rules_missing", f"[csoclassify] {e}",
                        getattr(e, "filename", None) or args.rules)
    except RuleSetValidationError as e:
        # 파일은 있지만 내용이 틀림 → 스캔을 시작하지 않고 위반 전체를 보여 준다.
        # "파일 없음(3)"과 구분되는 코드 4 로 나가 배치가 대응을 나눌 수 있게 한다.
        log.error("규칙셋 검증 실패 count=%d path=%s", len(e.violations), e.path)
        return fail_err("rules_invalid", f"[csoclassify] {e}", e.path)

    #-----------------------------------------------------------
    # 업무분류 규칙파일 로딩.
    #-----------------------------------------------------------
    # 업무분류(doctype) 축 — 있으면 켜고 없으면 security 만(4-6). --axis doctype 이면 필수.
    # => doc_taxomomy.yaml 과 doc_rules.yaml 파일을 불러온다.
    taxonomy, doc_rules_set, dt_code = _load_doctype_axis(args, log)

    # not None => 규칙파일이 잘못된 경우임. 이때는 에러띄우고 종료.
    if dt_code is not None: 
        return dt_code

    #-----------------------------------------------------------
    # --conflict 옵션 : 업무분류시 분류체계를 몇개남길꺼나(기본=all)
    #-----------------------------------------------------------
    # --conflict 로 doctype 전략을 실행 시 덮어쓰기(6-5). security 는 서열 축이라 금지(T11).
    # => --conflit 는 한문서에 업무분류체계가 여러개일때 몇개를 남길껀지 설정값(기본=all)
    doctype_strategy_override = None   # 있으면 rec["labels"]["doctype"]["strategy"] 에 각인
    # --conflict 설정되어 있으면 설정한 숫자(1,2,3,..)를 설정한다.
    if getattr(args, "conflict", None):
        from .classify import AxisNotOverridableError, ensure_overridable_axis
        try:
            override_axis, override_spec = _parse_conflict_override(args.conflict)
            ensure_overridable_axis(override_axis)
        except (ValueError, AxisNotOverridableError) as e:
            return fail_err("bad_conflict_axis", f"[csoclassify] {e}")
        if doc_rules_set is not None:
            import dataclasses
            doc_rules_set = dataclasses.replace(doc_rules_set, conflict=override_spec)
        # 재현성을 위해 실제 적용된 문자열을 그대로 결과에 남긴다(설계서 6-5 warn).
        doctype_strategy_override = args.conflict.split("=", 1)[1]

    #-----------------------------------------------------------
    # --axis 옵션 : 등급분류만 할꺼냐, 업무분류만 할꺼냐, 아님 둘다(기본=둘다)
    #-----------------------------------------------------------
    # => --axis security|doctype   이번 실행에 쓸 축만 지정(doctype 이면 보안등급 계산 생략) 
    axis = getattr(args, "axis", None)

    #-----------------------------------------------------------
    # -- hybridparse 옵션 : 하이브리드 추출기 만듬.
    # --hybridparse 여부에 따라 사이냅 단독 또는 하이브리드 추출기를 만든다(분류는 추출이 클라이언트측).
    #-----------------------------------------------------------
    extractor = build_extractor(hybrid=getattr(args, "hybridparse", False))

    #-----------------------------------------------------------
    # 부가적인 옵션값 설정.
    #-----------------------------------------------------------
    with_text = getattr(args, "with_text", False)         # 결과 레코드에 추출 텍스트 포함
    auto_prop = getattr(args, "auto_propagate", False)    # 분류 직후 보류 문서를 seed 로 전파
    rule_only = getattr(args, "rule_only", False)         # 규칙만(임베딩·전파 없음)
    vector_only = getattr(args, "vector_only", False)     # 규칙 없이 벡터-seed 비교만

    #-----------------------------------------------------------
    # rules_enabled 설정.
    # --axis doctype 이면 security 신호 자체를 계산하지 않는다(6-4 — PII 검출까지 포함해
    # 통째로 생략, 가장 비싼 단계를 건너뛰어 대량 업무분류 스캔이 크게 빨라진다).
    #-----------------------------------------------------------
    rules_enabled = not vector_only and axis != "doctype"

    #-----------------------------------------------------------
    # 전파 비교 기준 seed 경로
    #  --seeds 우선, 없으면 exe 옆 class_seed.jsonl(기본 규약).
    #-----------------------------------------------------------
    seeds_path = args.seeds or default_seed_path()

    #-----------------------------------------------------------
    # 분류 방식 결정 — 배타 3분기: 벡터만 / 규칙만 / 기본(규칙+자동전파).
    #-----------------------------------------------------------
    if vector_only:
        # 규칙 미사용: 전량 임베딩 후 seed 와 비교해 등급을 정한다 → 비교 기준 seed 파일 필수.
        if not seeds_path or not os.path.isfile(seeds_path):
            return fail_err("seeds_missing",
                            "[csoclassify] --vector-only 는 비교 기준 seed 파일이 필요합니다: "
                            "--seeds <class_seed.jsonl>(또는 exe 옆 class_seed.jsonl)",
                            seeds_path)
        embed_mode = "all"      # 모든 문서를 임베딩해야 seed 와 비교 가능
        auto_prop = True        # 전파(=벡터 비교)로 등급을 정하므로 항상 켠다
    elif rule_only:
        # 규칙만: 임베딩·전파를 하지 않는다(가장 빠름). seed 가 옆에 있어도 무시(명시적 차단).
        embed_mode = "none"
        auto_prop = False
    else:
        # 기본: 규칙 분류 + '보류 문서 자동 전파'. seed 파일(exe 옆 class_seed.jsonl 또는 --seeds)이
        # 있으면 auto_prop 을 자동으로 켠다(명시적 --auto-propagate 없이도). 없으면 전파 없이 규칙만.
        if not auto_prop and seeds_path and os.path.isfile(seeds_path):
            auto_prop = True
        elif not auto_prop:
            # seed 가 없으면 전파(Signal B)가 통째로 꺼진다 — 규칙이 못 정한 문서는
            # 구제받지 못하고 등급 없이(null) 나간다. 예전에는 이걸 아무 말 없이
            # 넘어갔고, 배포 폴더에 파일이 빠진 것을 아무도 몰랐다(실측: 배포본에
            # class_seed.jsonl 이 없어 보류 21건이 미분류로 나갔다 — 부록 C-5).
            # 파일이 없는 것 자체는 정상 배포일 수도 있으므로(규칙만 쓰는 배포)
            # 오류로 막지는 않고, '무엇이 꺼졌는지'만 분명히 알린다.
            print(f"[csoclassify] 전파용 seed 파일이 없어 자동 전파를 건너뜁니다: "
                  f"{seeds_path}\n"
                  f"              규칙이 못 정한 문서는 미분류로 남습니다. "
                  f"의도한 것이면 --rule-only 로 명시하세요.", file=sys.stderr)
        # 임베딩 정책 needed(선택적) > all(전량) > none. embed_needed 가 우선.
        if getattr(args, "embed_needed", False):
            embed_mode = "needed"
        elif getattr(args, "with_vector", False):
            embed_mode = "all"
        else:
            embed_mode = "none"
        # seed 를 만들려면 '보류 문서'가 아니라 '규칙이 자신 있게 분류한 문서'의
        # 벡터가 필요하다. needed 모드는 확정 문서의 임베딩을 건너뛰므로 씨앗이
        # 하나도 안 나온다 — 전량 임베딩으로 올린다.
        if getattr(args, "make_doctype_seeds", None):
            embed_mode = "all"
        # 전파(auto_prop)는 보류 문서 벡터가 있어야 구제 가능 → 꺼져 있으면 needed 로 올린다.
        if auto_prop and embed_mode == "none":
            embed_mode = "needed"

    #-----------------------------------------------------------
    # seed 전파 가능한지 판단
    # 'seed 전파 전용'(doc_rule.yaml 없음)인데 전파까지 못 하는 상황이면 미리 알린다.
    #   조용히 빈 결과를 내면 "분류할 게 없었다"로 오해되는데, 실제로는 "분류할 수단이
    #   없었다"라서 대응이 완전히 다르다(규칙을 쓰거나 seed 를 채워야 한다).
    #   전파를 못 하는 경우는 두 가지 — 임베딩을 아예 안 하거나(--rule-only 등),
    #   임베딩은 하는데 비교할 doctype seed 가 없거나.
    #-----------------------------------------------------------
    if doc_rules_set is not None and doc_rules_set.version == "none":
        from .classify.propagate import DoctypeSeedIndex
        # seed 유무를 먼저 본다 — seed 가 없으면 embed_mode 도 덩달아 none 이 되므로
        # (전파할 대상이 없으니 임베딩을 켤 이유가 없다), 순서를 반대로 하면 진짜
        # 원인인 'seed 없음'을 '--rule-only 탓'으로 잘못 짚는다.
        n_dt_seed = DoctypeSeedIndex.from_seed_file(seeds_path).size
        why = None
        if not n_dt_seed:
            why = f"쓸 수 있는 seed(labels.doctype)가 없어({seeds_path})"
        elif embed_mode == "none":
            why = "임베딩을 하지 않아(--rule-only 등)"
        if why:
            print(f"[csoclassify] 업무분류: 규칙(doc_rule.yaml)도 없고 {why} 전파도 못 합니다 "
                  f"— 전부 미분류로 두니 관리자가 분류한 뒤 seed 로 승격하세요.", file=sys.stderr)
            log.warning("업무분류 분류수단 없음 :: embed_mode=%s dt_seed=%d seeds=%s",
                        embed_mode, n_dt_seed, seeds_path)

    #-----------------------------------------------------------
    # 임베딩 수단 준비(임베딩이 필요한 정책일 때만). 데몬(웜 모델 재사용) 우선, 실패 시
    # in-process 폴백. 데몬을 쓰면 '따로따로 반복 실행'해도 model_load=0 으로 빨라진다.
    #-----------------------------------------------------------
    daemon_client = None
    ip_embedder = {"v": None}    # in-process 임베더(폴백/‘--no-daemon’). 처음 필요할 때 지연 생성.
    if embed_mode != "none" and args.daemon:
        from .daemon.client import DaemonClient
        # 데몬이 같은 로그파일에 남기도록 경로/verbose 전달. 유휴시간(무한 기본)도 함께.
        daemon_client = DaemonClient(args.model, idle_timeout=args.idle_timeout,
                                     log_path=getattr(args, "_log_path", None), verbose=args.verbose)

    #--------------------------------------------------------------
    # in-process 임베더 확보(지연 생성)
    #=> 데몬 미사용/폴백 시 쓸 OnnxEmbedder 를 한 번만 만들어 재사용한다.
    # -in: 없음
    # -out: OnnxEmbedder
    # -out: error = 없음
    #--------------------------------------------------------------
    def _get_ip_embedder():
        if ip_embedder["v"] is None:
            from .embed.onnx_embedder import OnnxEmbedder
            ip_embedder["v"] = OnnxEmbedder(config.get_model_spec(args.model),
                                            num_threads=args.num_threads)
        return ip_embedder["v"]

    #--------------------------------------------------------------
    # 텍스트 → 벡터 (데몬 우선, in-process 폴백)
    #=> 이미 추출·정제된 텍스트를 임베딩한다. 데몬이 가능하면 웜 모델로(model_load=0),
    #   접속/통신 실패면 조용히 in-process 로 폴백한다. 단계 시간(model_load/embed)도 기록.
    # -in: text   = 정제 텍스트
    # -in: timing = 이 파일의 Timing(단계시간 기록용)
    # -out: list[float] = 문서 임베딩 벡터
    # -out: error = 임베딩 자체 실패는 예외 전파(호출부가 잡아 벡터 없이 진행)
    #--------------------------------------------------------------
    def _embed_text(text, timing):
        emb_opts = {"max_tokens": args.max_tokens, "overlap": args.overlap, "normalize": True}
        if daemon_client is not None:
            from .daemon.client import DaemonUnavailable
            try:
                with timing.measure("embed"):
                    vec = daemon_client.request_embed_text(text, emb_opts)
                timing.set("model_load", 0.0)      # 데몬은 이미 웜
                return list(vec)
            except DaemonUnavailable as e:
                # 데몬 불가 → in-process 폴백(원인은 로그로 남긴다).
                if args.verbose:
                    print(f"[csoclassify] 데몬 폴백(in-process): {e}", file=sys.stderr)
                log.warning("분류 임베딩 데몬 불가 → in-process 폴백 :: %s", e)

        #--------------------------------------------------------------
        # 임베딩모델 로딩
        #--------------------------------------------------------------
        emb = _get_ip_embedder()
        
        timing.set("model_load", emb.ensure_loaded())
        with timing.measure("embed"):
            result, _ = emb.embed_document(text, args.max_tokens, args.overlap,
                                           normalize=True, per_chunk=False)
        return list(result)

    # 등급 분포 집계(요약용). none = 규칙 미검출(+failsafe 미사용).
    counts = {"C": 0, "S": 0, "O": 0, "none": 0}
    embedded = 0            # 실제 임베딩한 문서 수(요약·검증용)
    # 본문을 못 읽은 문서 수 — 추출기가 예외를 던진 것과 '글자가 사실상 없던 것'을
    # 함께 센다. 부르는 쪽이 "이번 실행에 손봐야 할 문서가 몇 건인가"를 알아야 한다.
    n_extract_failed = 0
    # 문서 식별자 집계 — ID 를 어떤 출처로 채웠는지 센다(요약 첫 줄 · R14).
    docid_stats = {}
    docid_flist = getattr(args, "_filelist", None)
    docid_on = not getattr(args, "no_doc_id", False)
    missing_id_rows = []          # --report-missing-id 로 뽑을 폴백 문서들
    code = config.EXIT_OK

    # 업무분류(doctype) 축 집계(요약용, 설계서 7-3 — 뿌리 카테고리 롤업). doctype 축이
    # 이번 실행에서 한 건도 안 돌았으면 doctype_total_docs 가 0 으로 남아 요약에서
    # 이 섹션 자체를 뺀다(4-6 — "축을 안 씀"과 "미분류"를 구분).
    doctype_total_docs = 0
    doctype_unclassified = 0
    doctype_root_totals = {}     # {뿌리 title: 문서-분류 쌍 수}
    doctype_node_totals = {}     # {(뿌리 title, 노드 title): 건수}

    # 출력 축약 옵션 — 모두 C/S/O 분류 모드에서만 의미가 있다.
    summary_only = getattr(args, "summary_only", False)   # 요약만 출력(파일별 레코드 생략)
    no_summary = getattr(args, "no_summary", False)       # 맨 끝 요약 레코드를 출력에서 제거
    simple_why = getattr(args, "simple_why", False)       # 축약본에 판정 근거 요약까지
    simple = getattr(args, "simple", False) or simple_why  # 파일별 문서명·등급·해시 3필드만
    need_hash = getattr(args, "hash", False) or simple    # --hash 또는 --simple 이면 해시 계산

    #--------------------------------------------------------------
    # 레코드 1개 출력(+로그)
    #=> 형식(json=들여쓰기 / 그외=1줄 압축)을 한 곳에서 정해 출력하고, 같은 내용을
    #   로그에도 남긴다(벡터·텍스트는 축약). 일반/전파 두 경로가 공유한다.
    #   ※ --summary 면 파일별 레코드는 내보내지 않고(요약만), --simple 이면 3필드로 줄인다.
    # -in: rec = 결과 레코드
    # -out: 없음(파일/화면 출력 + 로그)
    # -out: error = 없음
    #--------------------------------------------------------------
    # json 은 '항상' 유효한 배열([ ... ])로 낸다. 분류는 맨 끝에 summary 레코드를 반드시
    # 덧붙이므로(단건이라도 레코드+summary = 2건), 배열로 감싸지 않으면 객체가 이어붙어
    # 깨진 JSON({..}{..})이 된다. 압축 확장으로 단건이 여러 건이 되는 경우도 배열이라야 맞다.
    # 단, --summary 는 요약 객체 하나만 내므로 배열로 감싸지 않는다(단일 JSON 객체).
    writer = output.RecordWriter(out_fp or sys.stdout, args.fmt, multi=not summary_only)

    # --simple + --out 이면 전체 레코드를 '<out>.full.<확장자>' 에 함께 남긴다(감사용).
    #   · --out 이 없으면(화면 출력) 두 갈래를 한 곳에 흘릴 수 없으므로 만들지 않는다
    #   · --out 이 가리키는 파일은 지금까지처럼 축약본이다 — 이미 그 파일을 읽고
    #     있는 쪽의 계약을 바꾸지 않는다
    #   · 분류를 다시 하는 것이 아니라 손에 든 레코드를 한 번 더 적을 뿐이다
    #     (실측 — 분류 2,320ms 대 축약본 쓰기 0.27ms = 0.011%)
    full_path = full_fp = full_writer = None
    if simple and args.out and not summary_only:
        _b, _e = os.path.splitext(args.out)
        full_path = f"{_b}.full{_e or '.jsonl'}"
        try:
            full_fp = open(full_path, "w", encoding="utf-8")
        except OSError as e:
            return fail_err("output_write_failed",
                            f"[csoclassify] 감사용 전체 결과 파일을 쓰지 못했습니다: "
                            f"{full_path} :: {e}", full_path)
        full_writer = output.RecordWriter(full_fp, args.fmt, multi=True)

    def _emit(rec):
        # --summary 면 파일별/집계 레코드는 아예 출력하지 않는다(맨 끝 요약만 낸다).
        if summary_only:
            return
        # --simple 이면 문서명·등급·해시 3가지만 남긴다.
        out = _simple_record(rec, why=simple_why) if simple else _order_record(rec)
        writer.write_record(out)
        # 축약본만으로는 "왜 이 등급인가"를 나중에 댈 수 없다 — 전체를 옆에 남긴다.
        if full_writer is not None:
            full_writer.write_record(_order_record(rec))
        log.info("결과 %s", output.dumps_safe(_loggable_record(rec), separators=(",", ":")))

    #--------------------------------------------------------------
    # 업무분류 라벨 마무리 + 요약 집계
    #=> 레코드가 '최종'이 된 시점(=출력 직전)에 딱 한 번 부른다. 전파가 라벨을
    #   더 붙일 수 있어서, 1차 스캔 직후에 세면 전파 전 숫자가 요약에 박힌다.
    #    1) --conflict 로 전략을 덮어썼으면 그 문자열을 레코드에 각인(6-5 재현성)
    #    2) 뿌리/말단 분류별로 문서 수를 센다(라벨이 없으면 미분류로 카운트)
    #
    # -in: rec = 결과 레코드(labels.doctype 이 있을 수도, 없을 수도)
    #
    # -out: 없음(rec 갱신 + 바깥 집계 변수 갱신)
    # -out: error = 없음
    #--------------------------------------------------------------
    def _finalize_doctype(rec):
        nonlocal doctype_total_docs, doctype_unclassified
        label = (rec.get("labels") or {}).get("doctype")
        # 키 자체가 없으면 이번 실행이 축을 안 쓴 것 — 집계 대상이 아니다(4-6).
        if label is None:
            return
        if doctype_strategy_override:
            # 재현 불가를 막는다(6-5 warn) — 같은 규칙셋인데 실행 시 덮어써서 결과가
            # 다르면, 그 사실이 레코드 자체에 남아야 나중에 왜 달랐는지 알 수 있다.
            label["strategy"] = doctype_strategy_override
        doctype_total_docs += 1
        pairs = _doctype_breakdown(label)
        if not pairs:
            doctype_unclassified += 1
        for root_title, node_title in pairs:
            doctype_root_totals[root_title] = doctype_root_totals.get(root_title, 0) + 1
            key = (root_title, node_title)
            doctype_node_totals[key] = doctype_node_totals.get(key, 0) + 1

    # 진행표시를 켰으면 총 개수를 먼저 알린다(UI 가 0/total 로 바를 초기화).
    total = len(files)
    show_progress = getattr(args, "progress", False)
    if show_progress:
        print(f"[progress] 0/{total} ", file=sys.stderr, flush=True)

    do_timing = getattr(args, "timing", True)   # 결과 레코드에 elapsed_ms 를 넣을지(--no-timing 이면 끔)
    totals = []       # 파일별 total ms(배치 요약용)
    records = []      # auto_prop 일 때만 모아 뒀다가 전파 후 한꺼번에 출력
    # 업무분류 seed 후보. 전파가 라벨을 더하기 '전' 상태로 담아 둔다 —
    # 전파로 붙은 라벨은 절대 씨앗이 되면 안 되기 때문이다(재설계 10-4 drift 방지).
    # 본문 전체가 아니라 길이만 담는다(문서 하나가 수 MB 인 경우가 있다).
    seed_pool = []

    #---------------------------------------------------------------------
    # 실제 문서 분류 시작
    # => 1.문서추출->2.1차 룰분류->3.벡터생성->4.업무분류 seed 생성->5.전파
    #---------------------------------------------------------------------
    # startup: 프로세스(파이썬 진입) 시작 → 여기(첫 추출 직전)까지의 일회성 준비 비용.
    #   무거운 import(ko-pii·numpy·onnxruntime)·규칙셋 로드·데몬 준비 등이 포함된다.
    #   (exe 부트로더의 _internal 로딩 등 파이썬 진입 이전은 측정 불가.) 로그로 남기고,
    #   맨 처음 출력되는 레코드의 elapsed_ms 앞에 'startup' 키로 실어 total 에도 더한다.
    startup_ms = (time.perf_counter() - PROCESS_START_PERF) * 1000.0
    log.info("startup %.1fms (프로세스 시작→분류 시작; import·규칙로드·데몬준비 포함)", startup_ms)
    startup_pending = do_timing   # 첫 레코드에만 startup 을 붙인다(일회성 비용)

    # 압축파일 '자체'의 집계 등급용: origin(최상위 압축) → 내부 파일 최종등급 리스트.
    arch_members = {}
    rec_origins = []   # auto_prop 경로에서 records 와 나란히 origin 을 보관

    # for문을 돌면서 문서처리..
    for i, item in enumerate(files, 1):
        # src=실제 읽을 경로(압축 내부면 임시파일), path(label)=표시·규칙신호용(압축경로/내부경로),
        # origin=이 파일이 나온 최상위 압축경로(일반 파일이면 None).
        src, path, origin = _as_job(item)
        # 파일별 스톱워치 — 추출/정제/규칙/(임베딩) 단계별 소요시간을 잰다(--embed 와 동일 형식).
        timing = Timing()
        try:
            #----------------------------------------------------------------------------
            # **(1) 문서text 추출**
            #----------------------------------------------------------------------------
            #=> 문서포멧감지(detected_format)->포멧문서파서로 text 추출
            #=> 분류에서는 원문 텍스트를 (기본은) 디스크에 남기지 않는다(민감정보 잔존 방지).
            with timing.measure("extract"):
                raw = extractor.extract(src, save_dir=None)

            #----------------------------------------------------------------------------
            # 추출된 text 정제
            #----------------------------------------------------------------------------
            with timing.measure("clean"):
                text = clean_text(raw)

        except ExtractError as e:
            print(f"[csoclassify] 추출 실패: {path} :: {e}", file=sys.stderr)
            log.error("분류 추출 실패 file=%s :: %s", path, e)
            code = config.EXIT_EXTRACT_FAIL
            # 못 읽은 문서도 '결과'다 — 레코드를 안 내면 그 문서는 결과에서 통째로
            # 사라져, 화면에는 "18개 중 11건"처럼 조용히 줄어든 숫자만 남는다.
            # 무엇이 왜 빠졌는지 알 수 없는 것은 거버넌스 도구에서 가장 나쁜 실패다.
            # 등급 없이(=보류) 실패 사유를 실어 내보내 사람이 처리하게 한다.
            rec = _extract_failed_record(path, e, ruleset, doc_rules_set, taxonomy)
            # 못 읽은 문서에도 식별자는 단다 — 그 문서도 사람이 손볼 '결과'라
            # 나중에 수정을 이으려면 키가 필요하다(내용을 못 읽으면 경로 해시로 내려간다).
            if docid_on:
                _attach_doc_id(rec, path, src, docid_flist, docid_stats)
                if rec.get("doc_id_source") != "sfile_id":
                    missing_id_rows.append(rec)
            counts["none"] = counts.get("none", 0) + 1
            n_extract_failed += 1
            if auto_prop:
                records.append(rec)
                rec_origins.append(origin)
            else:
                _finalize_doctype(rec)
                _emit(rec)
                if origin is not None:
                    arch_members.setdefault(origin, []).append(None)
            # 실패해도 진행수는 늘려 UI 바가 멈추지 않게 한다.
            if show_progress:
                print(f"[progress] {i}/{total} {path}", file=sys.stderr, flush=True)
            continue

        #----------------------------------------------------------------------------
        # **(2) CSO 보안등급 & 업무분류 1차 분류**
        #----------------------------------------------------------------------------
        # => build_record() 함수가 다섯 신호(규칙·민감정보·스탬프·경로·파일명)를 스캔·융합하여
        #    CSO 보안등급 1차 분류를 완성한다(cso_rules.yaml 기반).
        #    동시에 doc_rules 와 taxonomy 가 '둘 다' 있으면 _attach_doctype() 함수 내에서
        #    scan_doctype() 을 통해 업무분류 1차 분류도 수행한다(doc_rule.yaml 기반).
        #
        #   [CSO 보안등급 규칙 스캔]
        #    - 규칙(내용·민감정보·스탬프·경로·파일명)으로 등급을 낸다(임베딩은 관여 안 함)
        #    - --vector-only(rules_enabled=False)면 규칙을 건너뛰고 '보류' 레코드만 만든다
        #      → 등급은 아래 전파 단계가 seed 비교로 정한다
        #    - --with-pii 면 검출된 원문 PII 값도 레코드에 싣는다(기본 off=미저장)
        #
        #   [업무분류]
        #    - doc_rule.yaml 규칙으로 스캔하여 다중 라벨 후보를 산출
        #    - doc_rules_set 또는 taxonomy 중 하나라도 없으면 labels.doctype 키 자체를 생략
        with timing.measure("rule"):
            rec = build_record(path, text, ruleset, ts=now_iso(),
                               failsafe=args.failsafe, vector=None,
                               with_pii=getattr(args, "with_pii", False),
                               rules_enabled=rules_enabled,
                               doc_rules=doc_rules_set, taxonomy=taxonomy)
            
        # 본문을 사실상 못 읽었으면 표식을 단다(분류 결과 자체는 건드리지 않는다).
        # 종료코드도 추출 실패(1)로 올린다 — 배치가 "이 실행에 손봐야 할 문서가
        # 있다"를 종료코드만으로 알 수 있어야 한다.
        short, n_chars = _body_too_short(text)
        if short:
            _mark_no_body(rec, n_chars)
            n_extract_failed += 1
            code = config.EXIT_EXTRACT_FAIL
            print(f"[csoclassify] 본문 텍스트 없음({n_chars}자): {path}"
                  f" — 스캔본(이미지)일 수 있습니다", file=sys.stderr)

        # 업무분류 집계는 여기서 하지 않는다 — 전파(auto_prop)가 라벨을 더 붙일 수
        # 있어서, 지금 세면 전파 전 숫자가 요약에 박힌다. 레코드가 '최종'이 되는
        # 출력 직전에 _finalize_doctype() 으로 센다.

        #----------------------------------------------------------------------------
        # 문서해쉬값
        # =>인자가 --hash/--simple 인 경우에만 문서 '내용'의 해시를 레코드에 싣는다(읽기 실경로 src 기준).
        #----------------------------------------------------------------------------
        # 문서 식별자(doc_id·key) — 설계 §7-5. 해시 계산보다 먼저 달아 둔다.
        if docid_on:
            _attach_doc_id(rec, path, src, docid_flist, docid_stats)
            if rec.get("doc_id_source") != "sfile_id":
                missing_id_rows.append(rec)

        if need_hash:
            rec["hash"] = _file_hash(src)

        #----------------------------------------------------------------------------
        # ** (3) 벡터 생성 **        
        #----------------------------------------------------------------------------
        #  => 벡터 임베딩이 필요한지 판단 (비싼 작업이므로 필요할 때만 수행)
        #    기본 모드: --auto-propagate 켜짐 && 규칙 분류 사용 → "needed" 모드가 기본
        #
        #    · "all" 모드: 항상 임베딩 (--with-vector 또는 seed 생성 시)
        #    · "needed" 모드(**기본): 다음 중 하나 만족 시만 임베딩 (선택적 = 비용 절감)
        #      - 보안등급 미정(grade=None): 규칙이 등급을 못 정한 경우
        #      - seed 전파 후보(seed_eligible=True): cso_rule.yaml에서 marked
        #      - 문서타입 미정: doc_rule.yaml에서 라벨은 있지만 선택지가 비어있음
        #        예: "labels": {"doctype": {"values": []}} ← 규칙이 후보를 찾지 못함
        #
        #    · "none" 모드: 임베딩 안 함 (--rule-only 또는 규칙 분류만 사용할 때)
        #
        #  ⚠️ 중요: 벡터가 없으면 업무분류 seed도 생성 안 됨!
        #    · 업무분류 seed는 '벡터가 있는 문서'에만 들어간다
        #    · 세 조건을 모두 만족 못하면 → need_vec=False → 벡터 생성 안 함
        #    · 벡터 없음 → seed_pool에 추가 안 됨 → 업무분류 seed 최종 미포함
        #
        dt_label = rec.get("labels", {}).get("doctype")

        dt_undecided = dt_label is not None and not dt_label.get("values")
        need_vec = embed_mode == "all" or (
            embed_mode == "needed"
            and (rec["grade"] is None or rec.get("seed_eligible") or dt_undecided))

        if need_vec and embed_mode != "none":
            try:
                # 벡터 생성 (데몬 서버 우선, 실패 시 로컬 폴백)
                rec["vector"] = _embed_text(text, timing)
                embedded += 1
            except Exception as e:
                # 모델 없음 등의 오류: 벡터 없이 계속 진행
                log.warning("분류 임베딩 실패 file=%s :: %s", path, e)

        # 옵션: 추출(정제) 텍스트를 결과 레코드에 함께 저장(기본 off — 프라이버시).
        # safe_text 를 거치는 이유: 깨진 문서에서 '짝 없는 대리 문자'가 본문에 섞여
        # 들어오면 결과를 파일에 쓸 때 UnicodeEncodeError 로 배치 전체가 죽는다.
        # 레코드에 넣기 전에 걸러 두면 값 자체도 깨끗해진다(출력단 방어는 그대로 둔다).
        if with_text:
            rec["text"] = safe_text(text)

        # 처리시간(단계별 ms)을 레코드에 부착 — --embed 와 동일 형식(extract/clean/rule/
        # [model_load/embed]/total). --no-timing 이면 생략.
        if do_timing:
            timing.finalize_total()
            em = timing.as_dict()
            # 첫 레코드에는 startup 을 맨 앞 키로 넣고 total 에도 더한다(프로세스 시작~완료 = 실제 체감).
            if startup_pending:
                em = {"startup": round(startup_ms, 2), **em}
                em["total"] = round(em.get("total", 0.0) + startup_ms, 2)
                startup_pending = False
            rec["elapsed_ms"] = em
            totals.append(em.get("total", 0.0))

        counts[rec["grade"] or "none"] = counts.get(rec["grade"] or "none", 0) + 1

        # 씨앗 후보 적재 — 지금이 '규칙만으로 판정한 상태'라 씨앗 자격을 보기에 맞다.
        if getattr(args, "make_doctype_seeds", None) and rec.get("vector"):
            seed_pool.append({"file": rec.get("file"), "labels": rec.get("labels"),
                              "vector": rec["vector"], "text_len": len(text or "")})

        # auto_prop 면 전파 후 출력하려고 모아 두고, 아니면 바로 출력.
        if auto_prop:
            records.append(rec)
            rec_origins.append(origin)   # 압축 집계용 origin 을 레코드와 나란히 보관
        else:
            # 스트리밍 경로는 전파가 없으니 지금이 최종 — 여기서 업무분류를 센다.
            _finalize_doctype(rec)
            _emit(rec)
            # 스트리밍 경로는 지금 등급이 최종이라 바로 압축별로 모은다.
            if origin is not None:
                arch_members.setdefault(origin, []).append(rec["grade"])

        # 한 파일 처리 완료 → 진행수 갱신(UI 진행바/경과시간용).
        if show_progress:
            print(f"[progress] {i}/{total} {path}", file=sys.stderr, flush=True)

    #----------------------------------------------------------------------------
    # **(옵션) 업부분류를 seed에 추가 한다. **
    # --make_doctype_seeds 옵션된 경우에만 
    #  ⚠️ 중요: 벡터가 없으면 업무분류 seed도 생성 안 됨!
    #    · 업무분류 seed는 '벡터가 있는 문서'에만 들어간다
    #----------------------------------------------------------------------------
    # 옵션: 업무분류 seed 만들기(재설계 10장). 전파보다 '먼저' 한다 —
    # 전파가 라벨을 더하고 나면 어느 라벨이 규칙에서 온 것인지 흐려진다.
    if getattr(args, "make_doctype_seeds", None):
        from .classify import seedgen
        out_path = args.make_doctype_seeds
        if taxonomy is None or doc_rules_set is None:
            print("[csoclassify] 업무분류 축이 꺼져 있어 seed 를 만들 수 없습니다 "
                  "(--taxonomy·--doc-rules 확인)", file=sys.stderr)
        else:
            # 업무분류측 class_seed.jsonl 씨드파일을 만든다.
            seeds, sstats = seedgen.select_doctype_seeds(
                seed_pool, taxonomy,
                t_seed=doc_rules_set.defaults.t_seed,
                per_dir=getattr(args, "seed_per_dir", 3),
                per_node=getattr(args, "seed_per_node", 50))

            # class_seed.jsonl 파일 생성.
            merged = seedgen.merge_into(seeds, out_path)

            print(f"[seed][업무분류] 후보 {sstats['입력']} → 채택 {sstats['채택']} "
                  f"(분류 {len(sstats['노드별'])}종) · {out_path}", file=sys.stderr)
            print(f"[seed][업무분류] 기존 보안등급 seed {merged['기존유지']}건 유지 · "
                  f"이전 업무분류 seed {merged['이전doctype제거']}건 교체", file=sys.stderr)

            # 왜 안 뽑혔는지를 남긴다 — 씨앗이 0건일 때 이 줄이 없으면 원인을 못 찾는다.
            if sstats["탈락사유"]:
                why = " ".join(f"{k}={v}" for k, v in
                               sorted(sstats["탈락사유"].items(), key=lambda x: -x[1]))
                print(f"[seed][업무분류] 탈락 사유: {why}", file=sys.stderr)

    #----------------------------------------------------------------------------
    # **(4) 보류문서들 전파(seed 비교)
     #----------------------------------------------------------------------------
    # => 외부용 class_seed.json 파일과, cse_rule.yaml에 seed 문서들이 벡터값을 읽어와서 비교함.
    # 옵션: 분류 직후 보류 문서를 seed 로 전파해 구제(단일 문서 확인에도 유용).
    if auto_prop:
        from .classify.propagate import SeedIndex
        from .classify.engine import propagate_records
        seed_index = None

        # 외부 class_seed.jsonl 파일 읽어오기
        if seeds_path and os.path.isfile(seeds_path):
            seed_index = SeedIndex.from_seed_file(seeds_path)
            print(f"[csoclassify] 외부 seed {seed_index.size}건 로드: {seeds_path}", file=sys.stderr)
        elif args.seeds:
            # 사용자가 --seeds 로 명시했는데 파일이 없을 때만 알린다(내부 seed 로 진행).
            print(f"[csoclassify] seed 파일 없음(내부 seed 만 사용): {args.seeds}", file=sys.stderr)

        # 내부 seed 읽기(cso_rule.yaml)
        # => cso_rule.yaml에서 고신뢰(seed_eligible) 설정된 경우에 대해 읽어옴
        records, pstats = propagate_records(records, seed_index=seed_index, failsafe=args.failsafe)

        #-------------------------------------------------------------
        # **업무분류 전파**
        # => doc_rule.yaml, doc_taxonomy.yaml 설정된 경우에만 실행.
        #   · 필요성 — doc_rule.yaml 이 없는 배포에서는 이 단계가 유일한 분류 수단이다.
        # 업무분류 축도 같은 seed 저장소로 전파한다. security 전파와 두 가지가 다르다:
        #   · 대상 — security 는 '보류 문서만' 구제하지만, doctype 은 이미 라벨이
        #     있는 문서에도 후보를 '더한다'(한 문서가 여러 분류에 동시에 맞을 수 있다).
        #-------------------------------------------------------------
        if doc_rules_set is not None and taxonomy is not None:

            from .classify.propagate import DoctypeSeedIndex
            from .classify.engine import propagate_doctype_records

            # 외부 class_seed.jsonl 파일 읽어오기
            dt_seeds = DoctypeSeedIndex.from_seed_file(seeds_path)
            emb = doc_rules_set.embed

            if dt_seeds.size and not emb.enabled:
                # seed 는 있는데 스위치가 꺼져 있는 상태. 조용히 넘어가면
                # "왜 벡터가 안 도는지" 를 아무도 못 찾는다.
                print(f"[전파][업무분류] seed {dt_seeds.size}건이 있으나 "
                      f"doc_rule.yaml 의 embed.enabled 가 false 라 2단계를 건너뜁니다",
                      file=sys.stderr)
            elif dt_seeds.size:
                # 벡터 단독 후보 상한은 defaults.embed_cap, kNN 임계값은 embed 블록이
                # 정한다. 여태 이 값들은 파일에 적혀 있어도 쓰이지 않았다.
                records, dt_stats = propagate_doctype_records(
                    records, dt_seeds, taxonomy, doc_rules_set.conflict,
                    embed_cap=doc_rules_set.defaults.embed_cap, **emb.kwargs())
                print(f"[전파][업무분류] seed={dt_stats['seeds']} "
                      f"embed기여={dt_stats['embed_contributed']} "
                      f"벡터없음={dt_stats['no_vector']}", file=sys.stderr)
            # seed 가 없는 경우의 안내는 위(모드 결정 직후)에서 이미 냈다 — 그쪽은
            # auto_prop 이 아예 꺼진 경우까지 잡아 주므로 여기서 또 내지 않는다.

        # 전파로 등급이 바뀌었을 수 있으니 분포를 다시 집계한 뒤 출력.
        counts = {"C": 0, "S": 0, "O": 0, "none": 0}
        for rec, origin in zip(records, rec_origins):
            counts[rec.get("grade") or "none"] = counts.get(rec.get("grade") or "none", 0) + 1
            # 전파까지 끝난 지금이 최종 — 여기서 업무분류를 센다(전파로 붙은 라벨 포함).
            _finalize_doctype(rec)
            _emit(rec)
            # 전파 후가 최종 등급이므로 여기서 압축별로 모은다.
            if origin is not None:
                arch_members.setdefault(origin, []).append(rec.get("grade"))
        print(
            f"[propagate] seeds={pstats['seeds']} already_graded={pstats['already_graded']} "
            f"embed_decided={pstats['embed_decided']} "
            f"still_unclassified={pstats['still_unclassified']} no_vector={pstats['no_vector']}",
            file=sys.stderr,
        )

    # (0) 압축파일 '자체'의 집계 등급 레코드를 내부 파일들 뒤에 덧붙인다(요청 옵션).
    #     내부 파일 중 최고 위험(C>S>O)을 압축파일 등급으로 삼아, 압축만 봐도 위험도를 안다.
    #     이 집계 레코드는 아래 등급 카운트(내부 파일 기준)에는 넣지 않는다(중복 방지).
    arch_recs = []
    if arch_members:
        ts = now_iso()
        for origin, grades in arch_members.items():
            arec = _archive_record(origin, grades, ts)
            # --hash/--simple 이면 압축파일 자체의 해시도 함께 싣는다(origin=압축파일 실경로).
            if need_hash:
                arec["hash"] = _file_hash(origin)
            arch_recs.append(arec)
            _emit(arec)

    # 최종 요약 집계 — 총수/검출수(C+S+O)/등급별/미분류, 그리고 프로세스 시작~지금까지의
    #   '총 소요시간'(startup 포함 = exe 실행부터 여기까지의 실제 체감 시간).
    c_cnt = counts.get("C", 0)
    s_cnt = counts.get("S", 0)
    o_cnt = counts.get("O", 0)
    none_cnt = counts.get("none", 0)
    total_files = len(files)
    detected = c_cnt + s_cnt + o_cnt                      # 등급이 매겨진(=검출된) 문서 수
    total_ms = round((time.perf_counter() - PROCESS_START_PERF) * 1000.0, 1)
    # 상태 줄에 실을 건수를 남겨 둔다 — '16건 중 1건 실패'를 숫자로 알려 준다.
    errcodes.set_counts(total_files, n_extract_failed)
    # 정책 버전도 함께 — 결과만 있고 '어떤 규칙으로 판정했는지'가 없으면 나중에
    # 재현할 수 없다(--simple --nosummary 면 지금까지 어디에도 안 남았다).
    errcodes.set_versions(
        rule_version=ruleset.version,
        taxonomy_version=taxonomy.exported_at if taxonomy else None,
        doctype_rule_version=doc_rules_set.version if doc_rules_set else None)
    summary = {
        "total": total_files, "detected": detected,
        "C": c_cnt, "S": s_cnt, "O": o_cnt, "unclassified": none_cnt,
        # 본문을 못 읽은 문서 수. Rust 판 요약에는 있었는데 파이썬 판에는 빠져 있어,
        # 같은 실행인데 두 판의 요약 칸이 달랐다(2026-09-01 맞춤).
        "extract_failed": n_extract_failed,
        "elapsed_total_ms": total_ms, "rule_version": ruleset.version, "embedded": embedded,
    }
    # 압축파일 집계 레코드가 있으면 개수도 요약에 표기(내부 파일 total 과는 별개).
    if arch_recs:
        summary["archives"] = len(arch_recs)
    # doctype 축이 이번 실행에서 한 건이라도 돌았을 때만 요약에 싣는다(4-6).
    if doctype_total_docs:
        summary["doctype"] = {
            "total": doctype_total_docs,
            "unclassified": doctype_unclassified,
            "by_root": doctype_root_totals,
            "doctype_rule_version": doc_rules_set.version if doc_rules_set else None,
            "taxonomy_version": taxonomy.exported_at if taxonomy else None,
        }

    # (1) 결과 파일(json/jsonl)에도 맨 마지막에 요약을 JSON 으로 남긴다.
    #     json 배열이면 마지막 원소로, jsonl 이면 마지막 줄로 들어간다({"summary": {...}}).
    #     단 --nosummary 면 이 요약 레코드를 출력에서 뺀다(파일별 레코드만 남긴다).
    if args.fmt in ("json", "jsonl") and not no_summary:
        writer.write_record({"summary": summary})
    # 감사용 전체 파일에는 --nosummary 와 무관하게 요약을 남긴다 — 이 파일은
    # 연동용이 아니라 '나중에 되짚어 보는' 파일이라 정책 버전이 반드시 있어야 한다.
    if full_writer is not None and args.fmt in ("json", "jsonl"):
        full_writer.write_record({"summary": summary})
    # (1-2) --out 으로 저장할 때는 상태도 파일 안에 남긴다. 파일만 받아 나중에 읽는
    #     쪽은 stdout 을 이미 흘려보낸 뒤라, 파일 자체가 "이 결과가 온전한가"를
    #     말해 줘야 한다. summary 와 같은 자리(json 은 배열 마지막 원소, jsonl 은
    #     마지막 줄)라 새 규약이 아니다. --out 이 없으면 결과가 stdout 으로 나가고
    #     상태 줄도 거기 붙으므로 여기서 또 넣지 않는다(같은 줄이 두 번 나간다).
    if errcodes.is_json() and args.out and args.fmt in ("json", "jsonl"):
        writer.write_record(errcodes.status_object(code, args.file or args.dir))
    # json 배열 모드면 마지막에 ']' 로 닫아 유효한 JSON 파일을 완성한다.
    writer.close()
    if full_writer is not None:
        if errcodes.is_json():
            full_writer.write_record(errcodes.status_object(code, args.file or args.dir))
        full_writer.close()
        full_fp.close()
        print(f"[csoclassify] 감사용 전체 결과: {full_path}", file=sys.stderr)

    # (1-b) --report-missing-id — sfile_id 를 못 얻은 문서를 따로 뽑아 둔다.
    #       이 문서들은 매핑 테이블 적재 대상이 아니므로(R14), 왜 ID 를 못 얻었는지
    #       사람이 확인할 수 있게 목록으로 남긴다.
    if getattr(args, "report_missing_id", None) and missing_id_rows:
        try:
            with open(args.report_missing_id, "w", encoding="utf-8") as mf:
                for r in missing_id_rows:
                    mf.write(output.dumps_safe(
                        {"file": r.get("file"), "key": r.get("key"),
                         "doc_id": r.get("doc_id"),
                         "doc_id_source": r.get("doc_id_source")},
                        separators=(",", ":")) + "\n")
            print(f"[csoclassify] 문서 ID 미획득 목록: {args.report_missing_id} "
                  f"({len(missing_id_rows)}건)", file=sys.stderr)
        except OSError as e:
            # 본 작업은 끝난 뒤라, 리포트를 못 썼다고 결과까지 버릴 이유는 없다.
            print(f"[csoclassify] 미획득 목록을 쓰지 못했습니다: {e}", file=sys.stderr)

    # (2) 화면(stderr) 최종 요약 — 사용자가 요청한 형식(총수/검출/등급별/미분류/총시간).
    #     --nosummary 면 이 화면 요약 줄도 내지 않는다(summary 를 마지막 출력에서 완전 제거).
    if not no_summary:
        # 문서 ID 획득 현황 — 설계 §7-5-2-1. 폴백 건수를 등급 줄보다 먼저 보여 준다.
        # 이 수치가 조용히 커지면 매핑에 들어가지 못하는 문서가 쌓이는데, 아무 오류도
        # 나지 않아 알아채기 어려운 종류의 실패이기 때문이다(R14).
        if docid_on and docid_stats:
            n_sf = docid_stats.get("sfile_id", 0)
            n_content = docid_stats.get("content", 0)
            n_path = docid_stats.get("path", 0)
            print(f"[summary][문서 ID] sfile_id {n_sf} · 폴백(content) {n_content} "
                  f"· 폴백(path) {n_path}", file=sys.stderr)
            n_fallback = n_content + n_path
            if n_fallback:
                hint = ("" if getattr(args, "report_missing_id", None)
                        else " --report-missing-id 로 목록 확인")
                print(f"[summary][문서 ID] ※ 폴백 {n_fallback}건은 매핑 테이블 "
                      f"적재 대상이 아닙니다.{hint}", file=sys.stderr)
            if docid_stats.get("case"):
                print(f"[summary][문서 ID] 대소문자만 달라 목록과 이어진 문서 "
                      f"{docid_stats['case']}건(rematched_by=case)", file=sys.stderr)
            if docid_stats.get("hash_mismatch"):
                print(f"[summary][문서 ID] [F6] 목록의 hash 와 실제 파일이 다른 문서 "
                      f"{docid_stats['hash_mismatch']}건 — 수정된 것으로 보이며 "
                      f"같은 문서로 처리했습니다", file=sys.stderr)
        arch_note = f", 압축 {len(arch_recs)}건" if arch_recs else ""
        print(
            f"[summary] 총 {total_files}개 / 검출 {detected}, "
            f"C={c_cnt} S={s_cnt} O={o_cnt} 미분류={none_cnt} "
            f"추출실패={n_extract_failed}{arch_note}, "
            f"총시간={total_ms}ms",
            file=sys.stderr,
        )
        # doctype 축 롤업 — 뿌리 카테고리별 총계(괄호 안은 실제 걸린 노드별 내역) + 미분류.
        # 상세 문서별 내역이 필요하면 결과 파일을 classify.summarize_records() 로 다시 돌리면 된다.
        if doctype_total_docs:
            parts = []
            for root_title, total in sorted(doctype_root_totals.items(),
                                            key=lambda kv: kv[1], reverse=True):
                nodes = sorted(
                    ((node, cnt) for (root, node), cnt in doctype_node_totals.items()
                     if root == root_title),
                    key=lambda kv: kv[1], reverse=True,
                )
                breakdown = " · ".join(f"{n} {c}" for n, c in nodes)
                parts.append(f"{root_title} {total} ({breakdown})")
            # 한 건도 못 맞힌 경우 앞부분이 빈 문자열이 되어 "[summary][업무분류]  / 미분류 3"
            # 처럼 공백이 뜬다 — 값이 없다는 걸 말로 적어 준다.
            head = " / ".join(parts) if parts else "(분류된 문서 없음)"
            print(f"[summary][업무분류] {head} / 미분류 {doctype_unclassified}",
                  file=sys.stderr)
    return code


#------------------------------------------------------------------
# 임베딩 라벨 전파(--propagate, Signal B 2차 패스)
#=> --with-vector 로 만든 1차 레코드(jsonl, 벡터 포함)를 읽어, 고신뢰 등급을 seed
#   삼아 미분류·저신뢰 문서에 라벨을 전파하고 최종 등급을 재계산해 다시 출력한다.
#   임베딩 모델이 필요 없다(이미 뽑힌 벡터로 벡터연산만).
#    1) jsonl 레코드 로드
#    2) propagate_records 로 전파+재융합
#    3) 갱신 레코드 출력 + 통계 요약
#   [로그 라벨] 헷갈림 방지로 레코드마다 "무엇이 등급을 정했는지"를 태그로 구분한다:
#     · [전파결과][임베딩]          = 보류였고 임베딩 전파가 관여함(signals.embed 존재)
#     · [전파결과][통과(rule 확정)] = 1차에서 이미 확정돼 임베딩 안 거침(그대로 통과)
#     · [전파결과][보류(미확정)]    = 벡터 없음 등으로 끝내 등급 못 정함
#   [doctype 축, 로드맵 D7] security 전파 뒤에 이어서, labels.doctype 이 있는
#   레코드에 한해 별도로 doctype 축도 전파한다(같은 --seeds 파일의 labels.doctype
#   필드를 읽는다). security 와 달리 "이미 후보가 있어도" embed 후보를 추가한다
#   (다중 라벨은 상향이 아니라 추가가 원칙, 설계서 5-4).
#
# -in: args = argparse 결과(propagate=입력파일, out/fmt/failsafe, taxonomy/doc_rules/axis)
#
# -out: code = 종료코드(성공 0, 입력파일 문제면 EXIT_ARG_ERROR)
# -out: error = 없음(개별 파싱 오류는 건너뛰고 로그)
#------------------------------------------------------------------
def run_propagate(args):
    import json

    from .classify.engine import propagate_records, propagate_doctype_records
    from .classify.propagate import SeedIndex, DoctypeSeedIndex
    from .classify import UnknownGradeError

    log = logsetup.get_logger("csoclassify.cli")

    # 1차 레코드(jsonl) 로드.
    if not os.path.isfile(args.propagate):
        return fail_err("propagate_input_missing",
                        f"[csoclassify] 전파 입력 파일이 없습니다: {args.propagate}",
                        args.propagate)

    # 입력 레코드 로드 — classify 가 형식에 따라 'JSON 배열'(--dir 기본), '객체 1개'
    # (--file 기본), 'jsonl'(--format jsonl) 중 무엇이든 낼 수 있으므로 모두 받아들인다.
    #  1) 파일 전체를 JSON 으로 파싱 시도 → 배열이면 그대로, 객체면 1건으로.
    #  2) 실패하면 jsonl(한 줄=한 레코드)로 폴백 파싱.
    recs = []
    with open(args.propagate, encoding="utf-8") as f:
        raw = f.read()
    try:
        data = json.loads(raw)
        recs = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    except json.JSONDecodeError:
        for i, line in enumerate(raw.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError as e:
                log.warning("전파 입력 %d 행 파싱 실패: %s", i, e)

    # --seeds 가 있으면 외부 큐레이션 seed 저장소를 기준으로 비교(Phase 3 정석).
    seed_index = None
    if args.seeds:
        if not os.path.isfile(args.seeds):
            return fail_err("seeds_missing",
                            f"[csoclassify] seed 파일이 없습니다: {args.seeds}",
                            args.seeds)
        seed_index = SeedIndex.from_seed_file(args.seeds)
        print(f"[csoclassify] 외부 seed {seed_index.size}건 로드: {args.seeds}", file=sys.stderr)

    # 전파 + 재융합(상향 전용).
    # 입력 레코드/seed 는 외부 파일이라 손으로 고쳐졌을 수 있다. 등급 값이 정의된
    # 서열에 없으면 max_grade 가 예외를 던지므로(fail-closed), 원시 스택 대신
    # "어느 파일이 문제인지" 알려 주고 규칙셋 오류와 같은 코드 4 로 나간다.
    try:
        records, stats = propagate_records(recs, seed_index=seed_index, failsafe=args.failsafe)
    except UnknownGradeError as e:
        log.error("전파 입력 등급 오류 :: %s", e)
        return fail_err("rules_invalid",
                        f"[csoclassify] 전파 입력의 등급 값이 올바르지 않습니다: "
                        f"{args.propagate}\n  {e}", args.propagate)

    # doctype 축 전파(D7) — security 와 별개 축이라 별도 seed 저장소(같은 class_seed.jsonl
    # 파일의 labels.doctype 필드, 설계서 5-4 "한 파일에 두 축")와 별도 통계로 처리한다.
    # taxonomy/doc_rules 가 있어야(=이 배치가 doctype 축을 쓰고 있어야) 조상 흡수·전략
    # 재적용에 쓸 Taxonomy·ConflictSpec 을 알 수 있다 — 4-6 과 같은 "있으면 켜짐" 원칙.
    taxonomy, doc_rules_set, dt_code = _load_doctype_axis(args, log)
    if dt_code is not None:
        return dt_code
    if taxonomy is not None and doc_rules_set is not None:
        dt_seeds = DoctypeSeedIndex.from_seed_file(args.seeds)
        emb = doc_rules_set.embed
        if dt_seeds.size:
            print(f"[csoclassify] 업무분류 seed {dt_seeds.size}건 로드: {args.seeds}", file=sys.stderr)
        if dt_seeds.size and not emb.enabled:
            print("[csoclassify] doc_rule.yaml 의 embed.enabled 가 false 라 "
                  "업무분류 2단계를 건너뜁니다", file=sys.stderr)
            dt_seeds = DoctypeSeedIndex.from_seed_file(None)   # 빈 인덱스로 통과
        records, dt_stats = propagate_doctype_records(
            records, dt_seeds, taxonomy, doc_rules_set.conflict,
            embed_cap=doc_rules_set.defaults.embed_cap, **emb.kwargs())
        print(
            f"[propagate][업무분류] seeds={dt_stats['seeds']} "
            f"embed_contributed={dt_stats['embed_contributed']} "
            f"no_vector={dt_stats['no_vector']} axis_off={dt_stats['axis_off']}",
            file=sys.stderr,
        )

    # 결과 출력(--out 있으면 파일). 전파 입력은 '레코드 묶음'이라 json 은 항상 배열로
    # 감싸 유효한 JSON 파일이 되게 한다(다건 concatenation 무효화 방지).
    try:
        out_fp = open(args.out, "w", encoding="utf-8") if args.out else None
    except OSError as e:
        return fail_err("output_write_failed",
                        f"[csoclassify] 출력 파일을 쓰지 못했습니다: {args.out}\n  {e}",
                        args.out)
    writer = output.RecordWriter(out_fp or sys.stdout, args.fmt, multi=len(records) > 1)
    try:
        for rec in records:
            writer.write_record(_order_record(rec))
            # 전파 후 최종 결과 JSON 도 로그로 남긴다(벡터 축약).
            # 이 레코드의 등급이 임베딩 전파로 정해졌는지, 1차에서 이미 확정돼 그냥
            # 통과했는지를 태그로 구분해 로그만 봐도 바로 알 수 있게 한다.
            #  · signals.embed 존재 = propagate_records 가 보류 문서에만 붙임 → 임베딩 관여
            #  · 없고 grade 있음    = 1차(rule/path/name) 확정본이 그대로 통과
            #  · 없고 grade 없음    = 끝내 미확정(보류)
            if "embed" in (rec.get("signals") or {}):
                tag = "[임베딩]"
            elif rec.get("grade") is not None:
                # 실제로 무엇이 확정했는지(rule/path/name)를 그대로 보여 준다.
                who = "·".join(rec.get("decided_by") or []) or "1차"
                tag = f"[통과({who} 확정)]"
            else:
                tag = "[보류(미확정)]"
            log.info("[전파결과]%s %s", tag,
                     output.dumps_safe(_loggable_record(rec), separators=(",", ":")))
        # json 배열 모드면 마지막에 ']' 로 닫는다(파일 닫기 전에).
        writer.close()
    finally:
        if out_fp is not None:
            out_fp.close()

    print(
        f"[propagate] seeds={stats['seeds']} already_graded={stats['already_graded']} "
        f"embed_decided={stats['embed_decided']} "
        f"still_unclassified={stats['still_unclassified']} no_vector={stats['no_vector']}",
        file=sys.stderr,
    )
    return config.EXIT_OK


#------------------------------------------------------------------
# 일반 처리(임베딩) — 데몬 우선, 폴백 포함
#=> 파일들을 처리해 벡터를 출력한다. 데몬 on 이면 클라이언트로 위임하고, 데몬을
#   못 쓰면 in-process 로 폴백한다. in-process 임베더는 한 번만 만들어 재사용한다.
#    1) 데몬 클라이언트 준비(옵션)
#    2) 파일마다: 데몬 요청 → 실패 시 in-process
#    3) 결과 출력 + 배치 시간요약
#
# -in: files  = 대상 파일 리스트
# -in: args   = argparse 결과(모델/포맷/timing 등)
# -in: opts   = 처리 옵션 dict
# -in: out_fp = 출력 파일객체(None 이면 stdout)
#
# -out: code = 종료코드(성공 0, 추출/임베딩 실패 코드)
# -out: error = 없음(개별 실패는 stderr 로 알림 + 코드 반영)
#------------------------------------------------------------------
def run_embed(files, args, opts, out_fp):
    from .extract import build_extractor
    from .extract.base import ExtractError
    from .embed.onnx_embedder import OnnxEmbedder
    from .embed.base import EmbedError
    from . import pipeline

    log = logsetup.get_logger("csoclassify.cli")
    spec = config.get_model_spec(args.model)

    # 데몬 클라이언트(옵션). 실제 접속/기동은 첫 요청 때 일어난다.
    client = None
    if args.daemon:
        from .daemon.client import DaemonClient
        # 데몬이 같은 로그파일에 남기도록 경로/verbose 를 함께 넘긴다.
        client = DaemonClient(args.model, idle_timeout=args.idle_timeout,
                              log_path=getattr(args, "_log_path", None), verbose=args.verbose)

    # in-process 폴백용 자원은 처음 필요할 때 한 번만 만들어 재사용한다.
    ip_extractor = {"v": None}
    ip_embedder = {"v": None}

    #--------------------------------------------------------------
    # in-process 로 파일 1개 처리(내부 헬퍼)
    #=> 폴백/‘--no-daemon’ 경로. 추출기·임베더를 지연 생성해 재사용하고, 단일
    #   파일이면 추출과 모델 로딩을 병렬 예열한다.
    #
    # -in: path = 처리할 파일 경로
    #
    # -out: result = 파이프라인 결과 dict
    # -out: error = ExtractError/EmbedError 전파
    #--------------------------------------------------------------
    def process_inprocess(path):
        if ip_extractor["v"] is None:
            # --hybridparse 여부에 따라 사이냅 단독/하이브리드. (데몬 경로는 서버측 snf — 설계 §9 선택A)
            ip_extractor["v"] = build_extractor(hybrid=getattr(args, "hybridparse", False))
            ip_embedder["v"] = OnnxEmbedder(spec, num_threads=args.num_threads)
        timing = Timing()
        # 파일이 1개뿐이면 병렬 예열 이득이 크다(배치는 이미 로드돼 있어 불필요).
        parallel = (len(files) == 1)
        return pipeline.process_file(
            path, opts, ip_extractor["v"], ip_embedder["v"], timing, parallel_preload=parallel
        )

    code = config.EXIT_OK
    totals = []
    # json 은 처리 대상이 여러 건(배치 --dir 또는 압축 확장으로 다건)일 때 배열로 감싼다.
    # 진짜 단건이면 객체 하나(기존 동작 유지).
    writer = output.RecordWriter(out_fp or sys.stdout, args.fmt,
                                 multi=(len(files) > 1))
    for item in files:
        # src=실제 읽을 경로(압축 내부면 임시파일), label=표시용(압축경로/내부경로).
        src, label, _origin = _as_job(item)
        try:
            if client is not None:
                # 데몬 경로: 사용 불가 신호(DaemonUnavailable)면 조용히 폴백.
                from .daemon.client import DaemonUnavailable
                try:
                    result = client.request_embed(src, opts)
                except DaemonUnavailable as e:
                    if args.verbose:
                        print(f"[csoclassify] 데몬 폴백(in-process): {e}", file=sys.stderr)
                    # 폴백은 파일 로그에 항상 남겨 원인을 나중에 확인할 수 있게 한다.
                    log.warning("데몬 사용 불가 → in-process 폴백 file=%s :: %s", label, e)
                    result = process_inprocess(src)
            else:
                result = process_inprocess(src)

            # 결과의 file 필드를 표시용 경로(zip 내부 파일명)로 교체한다(임시경로 노출 방지).
            if isinstance(result, dict):
                result["file"] = label
            # 본문은 배열 경계를 관리하는 writer 로, 시간요약은 stderr 로 분리해 출력.
            stdout_str, stderr_str = output.format_result(result, args.fmt, timing=args.timing)
            writer.write_block(stdout_str)
            if stderr_str:
                print(stderr_str, file=sys.stderr)
            # 배치 시간요약에 쓸 total 수집.
            totals.append(result.get("elapsed_ms", {}).get("total", 0.0))

        except ExtractError as e:
            print(f"[csoclassify] 추출 실패: {label} :: {e}", file=sys.stderr)
            log.error("추출 실패 file=%s :: %s", label, e)
            code = config.EXIT_EXTRACT_FAIL
        except EmbedError as e:
            print(f"[csoclassify] 임베딩 실패: {label} :: {e}", file=sys.stderr)
            log.error("임베딩 실패 file=%s :: %s", label, e)
            code = config.EXIT_EMBED_FAIL
        except RuntimeError as e:
            # 데몬이 돌려준 처리 오류(추출/임베딩)를 코드로 환원.
            msg = str(e)
            print(f"[csoclassify] 처리 실패: {label} :: {msg}", file=sys.stderr)
            # 스택까지 파일 로그에 남겨 원인 추적을 돕는다.
            log.exception("처리 실패 file=%s :: %s", label, msg)
            code = config.EXIT_EMBED_FAIL if "embed" in msg else config.EXIT_EXTRACT_FAIL

    # json 배열 모드면 마지막에 ']' 로 닫는다.
    writer.close()

    # 배치이고 timing 이면 마지막에 합계/평균/최댓값 요약.
    if args.timing and len(files) > 1:
        line = output.batch_timing_line(totals)
        if line:
            print(line, file=sys.stderr)
    return code


#------------------------------------------------------------------
# 데몬 제어 서브커맨드 처리
#=> --serve/--status/--stop 중 하나가 있으면 그에 맞게 동작하고 종료코드를 준다.
#
# -in: args = argparse 결과
#
# -out: code 또는 None = 처리했으면 종료코드, 해당 없으면 None(일반 처리로 진행)
# -out: error = 없음
#------------------------------------------------------------------
def handle_daemon_commands(args):
    # 데몬 본체로 기동: 이 프로세스가 서버가 되어 블로킹 실행.
    # => --serve 인자인 경우.
    if args.serve:
        from .daemon.server import DaemonServer
        spec = config.get_model_spec(args.model)
        server = DaemonServer(spec, idle_timeout=args.idle_timeout, num_threads=args.num_threads)
        return server.run()

    # 상태 조회.
    if args.status:
        from .daemon.client import DaemonClient
        rec, ping = DaemonClient(args.model).status()
        if rec:
            # 유휴시간 표시: 0/없음 = 무한(자동종료 없음).
            _it = rec.get("idle_timeout")
            idle_disp = "무한(자동종료 없음)" if not _it else f"{_it}s"
            print(f"model={rec.get('model')} pid={rec.get('pid')} "
                  f"port={rec.get('port')} version={rec.get('version')} "
                  f"idle_timeout={idle_disp} ready={ping.get('model_ready')}")
        else:
            print(f"model={args.model}: 실행 중인 데몬 없음")
        return config.EXIT_OK

    # 정지.
    if args.stop:
        from .daemon.client import DaemonClient
        stopped = DaemonClient(args.model).stop()
        print("데몬 정지 요청 전송" if stopped else "정지할 데몬 없음")
        return config.EXIT_OK

    return None


#------------------------------------------------------------------
# 실제 실행 커맨드라인 문자열
#=> 로그에 "무엇을 어떻게 실행했는지"를 재현 가능하게 남기려고, 이 프로세스의 실행
#   커맨드라인을 풀경로로 조립한다.
#    - frozen(exe): sys.argv[0] 이 exe 풀경로라 그대로 사용 (예: C:\...\csoclassify.exe ...)
#    - 소스/모듈 실행: 앞에 파이썬 실행기를 붙여 실제 실행형태로 복원
#
# -in: 없음
# -out: cmd = 따옴표 처리된 전체 커맨드라인 문자열
# -out: error = 없음(조립 실패 시 공백 join 으로 폴백)
#------------------------------------------------------------------
def _full_command_line():
    import subprocess
    parts = list(sys.argv) if getattr(sys, "frozen", False) else [sys.executable] + list(sys.argv)
    try:
        return subprocess.list2cmdline(parts)
    except Exception:
        return " ".join(str(p) for p in parts)


#------------------------------------------------------------------
# 로그용 레코드 축약
#=> 결과 JSON 을 로그로 남길 때 벡터(384차원)와 추출 텍스트(--with-text 시)는 너무 길어
#   로그를 가리므로 길이/차원 요약으로 바꾼다. 등급/신호 등 사람이 볼 값은 그대로 둔다.
#
# -in: rec = 분류/전파 결과 dict
#
# -out: dict = vector→"[vec dim=N]", text→"[text len=N]" 로 축약한 얕은 복사본
#              (해당 필드가 없으면 원본 그대로)
# -out: error = 없음
#------------------------------------------------------------------
def _loggable_record(rec):
    # vector/text/pii 중 하나라도 있으면 로그용으로 축약·가림(원문 유출 방지).
    if not (isinstance(rec, dict) and (rec.get("vector") is not None
            or rec.get("text") is not None or rec.get("pii") is not None)):
        return rec
    r = dict(rec)
    if r.get("vector") is not None:
        try:
            r["vector"] = f"[vec dim={len(rec['vector'])}]"
        except TypeError:
            r["vector"] = "[vec]"
    if r.get("text") is not None:
        try:
            r["text"] = f"[text len={len(rec['text'])}]"
        except TypeError:
            r["text"] = "[text]"
    # [프라이버시] 원문 PII 는 로그에 절대 남기지 않는다 — 건수만 표기(값은 --out 파일에만).
    if r.get("pii") is not None:
        try:
            r["pii"] = f"[pii {len(rec['pii'])} detected]"
        except TypeError:
            r["pii"] = "[pii]"
    return r


#------------------------------------------------------------------
# 업무분류 규칙 채우기(--sync-doc-rule)
#=> 화면의 [분류 불러오기] 버튼과 **같은 코드**로 doc_rule.yaml 을 채운다.
#   예전에는 이 일이 화면에만 있어서, CLI 의 --scaffold-doc-rule 은 분류 이름
#   하나만 넣은 빈 뼈대를 만들었다(유의어 없음, 파일이 있으면 건너뜀). 같은 일을
#   두 곳이 다르게 하던 셈이라, 어휘를 만드는 층을 classify/docvocab.py 로 모으고
#   양쪽이 그것을 부르게 했다.
#    1) 분류 체계를 읽어 '규칙을 만들 분류'를 고른다(꺼 둔 분류·대분류는 뺀다)
#    2) 유의어 사전(synonyms/)을 규칙 파일 옆에서 찾아 얹는다
#    3) 빠진 분류는 새로 만들고, 옵션에 따라 빈 규칙을 채우거나 유의어를 덧붙인다
#    4) 화면과 같은 방식으로 저장한다(머리 주석 · 키 순서 · .bak 백업)
#
# -in: args = 파싱된 인자(taxonomy · doc_rules · sync_fill_blank · sync_enrich)
#
# -out: code = 0(정상) · 3(분류 체계 없음) · 4(규칙 파일을 읽거나 쓸 수 없음)
# -out: error = 없음(예외를 종료코드로 환원)
#------------------------------------------------------------------
def run_sync_doc_rule(args):
    from .classify import axes as AX
    from .classify import doc_rules as DR
    from .classify import docvocab as DV

    log = logsetup.get_logger("csoclassify.cli")
    tax_path = args.taxonomy or AX.default_taxonomy_path()
    rules_path = args.doc_rules or DR.default_doc_rules_path()

    if not os.path.isfile(tax_path):
        return fail_err("taxonomy_missing",
                        f"[csoclassify] 회사 분류 체계를 찾을 수 없습니다: {tax_path}\n"
                        f"  · --taxonomy <파일경로> 로 지정하거나,\n"
                        f"  · --export-taxonomy 로 먼저 만드세요.", tax_path)
    try:
        # doc_taxonomy.yaml 파일 로딩.
        taxonomy = AX.load_taxonomy(tax_path)
    except Exception as e:
        # 파일은 있는데 못 읽는다 = 내용 문제다(없음과 구분해 코드 4 로 나간다).
        return fail_err("taxonomy_invalid",
                        f"[csoclassify] 분류 체계를 읽지 못했습니다: {e}", tax_path)

    # doc_taxonomy.yaml 을 읽어오면서 doc_rule.yaml 에 분류체계노드를 만듬.
    nodes = DV.nodes_from_taxonomy(taxonomy)
    if not nodes:
        print("[csoclassify] 규칙을 만들 분류가 없습니다(꺼 둔 분류와 대분류는 "
              "가져오지 않습니다).", file=sys.stderr)
        return 0

    try:
        # 기존 doc_rule.yaml 에 doc_templete.yaml 을 합쳐서 dict 만듬.
        doc = DV.load_doc(rules_path)
    except Exception as e:
        return fail_err("doc_rules_invalid",
                        f"[csoclassify] 규칙 파일을 읽지 못했습니다: {rules_path}\n  {e}",
                        rules_path)

    # 업종별 분류체계 유의어 사전 파일을 로딩
    # => synomins 폴더에 있는 _core 및 업종별 유의어 사전파일(doc_synomins_legal.yaml 등) 로딩.
    syn = DV.load_synonyms(rules_path)
    # 새 규칙에 얹을 값(weight 등)은 본보기가 정한다 — 코드에 박아 두지 않는다.
    new_rule = (DR.load_scaffold_template(rules_path) or {}).get("new_rule")

    # doc_rule.yaml 만들 doc dict 에 유의어 규칙들을 추가.
    added, filled, enriched = DV.sync_nodes(
        doc, nodes, fill_existing=bool(args.sync_fill_blank),
        enrich_existing=bool(args.sync_enrich), syn=syn, new_rule=new_rule)

    if not (added or filled or enriched):
        print(f"[csoclassify] 바뀐 것이 없습니다 — 규칙 파일은 그대로 둡니다: {rules_path}",
              file=sys.stderr)
        return 0

    try:
        # 여기서 실제 doc_rule.yaml 파일 자체를 만듬.
        DV.save_doc(rules_path, doc)
    except OSError as e:
        return fail_err("doc_rules_write_failed",
                        f"[csoclassify] 규칙 파일을 쓰지 못했습니다: {rules_path}\n  {e}",
                        rules_path)

    layers = [os.path.basename(p) for p in (syn or {}).get("layers") or []]
    print(f"[csoclassify] {rules_path} 갱신 — 새 분류 {added}개 · 빈 규칙 채움 "
          f"{filled}개 · 유의어 더함 {enriched}개"
          + (f" (유의어 사전: {' → '.join(layers)})" if layers else " (유의어 사전 없음)"),
          file=sys.stderr)
    log.info("업무분류 규칙 동기화 :: added=%d filled=%d enriched=%d path=%s",
             added, filled, enriched, rules_path)
    return 0


#------------------------------------------------------------------
# 분류체계 스냅샷 내보내기 전용 모드 (--export-taxonomy)
#=> DOC_CLASSIFICATION JSON(MpowerV11 관리 화면/배치가 뽑은 원본)을 CSOClassify
#   가 읽는 doc_taxonomy.yaml 로 바꾼다. scripts/export_taxonomy.py(개발용
#   스크립트)와 완전히 같은 axes.export_from_mpower_json() 을 쓴다 — exe 로
#   얼려도(scripts/ 는 PyInstaller 번들에 안 들어간다) 이 변환을 할 수 있어야
#   현장에서 소스 체크아웃 없이 스냅샷을 갱신할 수 있다.
#    1) 원본 JSON → doc_taxonomy.yaml 변환 + round-trip 검증(축 로더가 그대로 읽는지)
#    2) --scaffold-doc-rule 이면 doc_rule.yaml 골격도 --doc-rules 경로에 생성
#       (이미 있으면 사람이 채운 내용을 덮어쓰지 않고 건너뜀)
#
# -in: args = 파싱된 인자(사용 필드: export_input·taxonomy·doc_rules·scaffold_doc_rule)
#
# -out: code = 0(정상) · 3(원본 JSON 없음/구조 오류) · 4(변환 결과 검증 실패)
# -out: error = 없음(예외를 종료코드로 환원)
#------------------------------------------------------------------
def run_export_taxonomy(args):
    import json as _json

    from .classify import axes as AX
    from .classify import doc_rules as DR

    log = logsetup.get_logger("csoclassify.cli")
    input_path = args.export_input or AX.default_taxonomy_export_input_path()
    output_path = args.taxonomy or AX.default_taxonomy_path()

    if not os.path.isfile(input_path):
        return fail_err("export_input_missing",
                        f"[csoclassify] 원본 JSON을 찾을 수 없습니다: {input_path}\n"
                        f"  · --export-input <파일경로> 로 지정하거나,\n"
                        f"  · resources/policy/doc_classification_export.json 에 두세요.",
                        input_path)

    try:
        taxonomy, warnings = AX.export_from_mpower_json(input_path, output_path)
    except (ValueError, _json.JSONDecodeError) as e:
        # 원본 JSON 이 매핑이 아니거나 nodes 가 없거나 문법이 틀림 — 입력 '내용' 문제다.
        # 파일이 없는 경우(2007)와 갈라 두면 부르는 쪽이 "경로를 다시 묻는다 / 원본
        # 데이터를 고친다"를 구분할 수 있다.
        log.error("분류체계 내보내기 실패(원본 문제) :: %s", e)
        return fail_err("export_input_invalid", f"[csoclassify] {e}", input_path)
    except AX.TaxonomyValidationError as e:
        # 변환은 됐지만 결과 트리가 깨짐(순환·고아 등) — 원본 데이터 자체의 문제.
        log.error("분류체계 내보내기 검증 실패 count=%d", len(e.violations))
        return fail_err("export_input_invalid",
                        f"[csoclassify] 변환 결과가 검증을 통과하지 못했습니다:\n{e}",
                        input_path)

    for w in warnings:
        print(f"[csoclassify] 경고: {w}", file=sys.stderr)

    print(f"[csoclassify] {output_path} 생성 완료 — "
          f"노드 {len(taxonomy)}개, 최상위 {len(taxonomy.roots)}개, "
          f"exported_at={taxonomy.exported_at}")
    for root in taxonomy.roots[:3]:
        for leaf in taxonomy.children_of(root.dc_id)[:1]:
            print(f"  예: {taxonomy.path(leaf.dc_id)}")

    if getattr(args, "scaffold_doc_rule", False):
        doc_rules_path = args.doc_rules or DR.default_doc_rules_path()
        scaffold = DR.write_scaffold(taxonomy, doc_rules_path)
        if scaffold is None:
            print(f"[csoclassify] {doc_rules_path} 이 이미 있어 골격 생성을 건너뜁니다"
                  f"(사람이 채운 내용을 덮어쓰지 않기 위함).", file=sys.stderr)
        else:
            print(f"[csoclassify] {doc_rules_path} 골격 생성 완료 — "
                  f"규칙 {len(scaffold['doctype_rules'])}건(terms 는 비어 있음, 채워야 동작).")

    return config.EXIT_OK


#------------------------------------------------------------------
# 규칙셋 검사 전용 모드 (--check-rules)
#=> 문서는 한 건도 읽지 않고 cso_rules.yaml 의 등급 값만 확인하고 끝낸다.
#   규칙셋을 고친 뒤 '실제 스캔을 돌리기 전에' 안전한지 확인하는 용도다.
#   특히 검증을 새로 도입한 직후, 기존 규칙셋에 이미 오타가 있는지 미리 보는 데 쓴다.
#    1) 규칙셋을 검증까지 포함해 읽어 본다
#    2) 문제가 없으면 규칙 건수를 요약해 보여 준다
#    3) doc_taxonomy.yaml·doc_rule.yaml 이 있으면 그것도 같은 자리에서 검사한다
#       (security 만 검사하고 doctype 은 실제 배치 때에야 오류를 만나면, "확인했는데
#       왜 또 실패하지" 하는 상황이 생긴다 — 있는 파일은 전부 미리 본다)
#
# -in: args = 파싱된 인자(사용 필드: rules·taxonomy·doc_rules)
#
# -out: code = 0(정상) · 3(파일 없음) · 4(검증 실패)
# -out: error = 없음(예외를 종료코드로 환원)
#------------------------------------------------------------------
def run_check_rules(args):
    from .classify import (load_rules, default_rules_path,
                           RuleSetValidationError, GRADES)

    log = logsetup.get_logger("csoclassify.cli")
    path = args.rules or default_rules_path()
    try:
        rs = load_rules(args.rules)
    except FileNotFoundError as e:
        return fail_err("rules_missing", f"[csoclassify] {e}", path)
    except RuleSetValidationError as e:
        log.error("규칙셋 검증 실패 count=%d path=%s", len(e.violations), e.path)
        return fail_err("rules_invalid", f"[csoclassify] {e}", e.path)

    # 통과했으면 "무엇을 검사했는지"를 건수로 보여 준다. 규칙이 0건이면 파일을
    # 잘못 지정했을 가능성이 크므로 눈에 띄게 알려 주는 편이 낫다.
    print(f"[csoclassify] 규칙셋 정상: {path}")
    print(f"  version={rs.version}  등급={'/'.join(GRADES)}")
    print(f"  regex_pii={len(rs.regex_rules)} · pii_combos={len(rs.pii_combos)} · "
          f"keywords={len(rs.keyword_rules)} · sensitive={len(rs.sensitive_rules)} · "
          f"stamps={len(rs.stamp_rules)} · paths={len(rs.path_rules)}")

    # doctype 축은 선택 자산이다(4-6) — 파일이 아예 없으면 "안 씀"으로 보고 건너뛴다.
    # (--axis security 로 좁히지 않는다 — --check-rules 는 배치 실행이 아니라 사람이
    # "지금 상태가 괜찮은지" 미리 보는 자리라, 있는 건 다 보여주는 편이 낫다.)
    from .classify import axes as AX
    from .classify import doc_rules as DR

    taxonomy_path = args.taxonomy or AX.default_taxonomy_path()
    if not os.path.isfile(taxonomy_path):
        print(f"[csoclassify] 분류체계 스냅샷 없음(doctype 축 미사용): {taxonomy_path}")
        return config.EXIT_OK
    try:
        taxonomy = AX.load_taxonomy(taxonomy_path)
    except AX.TaxonomyValidationError as e:
        log.error("분류체계 스냅샷 검증 실패 count=%d path=%s", len(e.violations), e.path)
        return fail_err("taxonomy_invalid", f"[csoclassify] {e}", e.path)
    stale = _check_stale_taxonomy(taxonomy)
    if stale:
        print(stale, file=sys.stderr)
    print(f"[csoclassify] 분류체계 스냅샷 정상: {taxonomy_path}")
    print(f"  exported_at={taxonomy.exported_at}  노드={len(taxonomy)}개  "
          f"최상위={len(taxonomy.roots)}개")

    doc_rules_path = args.doc_rules or DR.default_doc_rules_path()
    if not os.path.isfile(doc_rules_path):
        # 규칙이 없어도 축은 돈다(seed 전파 전용) — 그러니 "미사용"이 아니라 무엇으로
        # 분류하게 되는지를 알려 준다. seed 저장소가 실제로 쓸 만한지도 같이 본다.
        from .classify.propagate import DoctypeSeedIndex
        from .classify import default_seed_path
        seeds_path = args.seeds or default_seed_path()
        n_seed = DoctypeSeedIndex.from_seed_file(seeds_path).size
        print(f"[csoclassify] 업무분류 규칙셋 없음 → 'seed 전파 전용' 모드: {doc_rules_path}")
        if n_seed:
            print(f"  업무분류 seed {n_seed}건 사용 가능: {seeds_path}")
        else:
            print(f"  쓸 수 있는 업무분류 seed 가 없습니다({seeds_path}) — "
                  f"업무분류는 전부 미분류로 남습니다")
        return config.EXIT_OK
    try:
        drs = DR.load_doc_rules(doc_rules_path, taxonomy=taxonomy)
    except DR.DocRuleValidationError as e:
        log.error("업무분류 규칙셋 검증 실패 count=%d path=%s", len(e.violations), e.path)
        return fail_err("doc_rules_invalid", f"[csoclassify] {e}", e.path)
    for w in drs.warnings:
        print(f"[csoclassify] {w}")
    print(f"[csoclassify] 업무분류 규칙셋 정상: {doc_rules_path}")
    print(f"  version={drs.version}  conflict={drs.conflict.strategy}  "
          f"규칙={len(drs.rules)}건(활성 {len(drs.active_rules)}건)")
    return config.EXIT_OK


#------------------------------------------------------------------
# 메인 (핵심)
#=> 인자를 파싱하고 모드를 분기해 실행한다. 파일 대상이 없으면 사용법을 알린다.
#    1) 데몬 제어 명령이면 그쪽 처리
#    1.4) --check-rules 면 규칙셋만 검사하고 종료(문서 불필요)
#    2) 모델 별칭 검증
#    3) 대상 파일 수집 → text-only/일반 처리
#
# -in: argv = 명령행 인자 리스트(None 이면 sys.argv 사용)
#
# -out: code = 프로세스 종료코드
# -out: error = 없음(내부에서 예외를 코드로 환원)
#------------------------------------------------------------------
#------------------------------------------------------------------
# 오류 이름 하나로: JSON + 화면 + 로그 + 종료코드
#=> 실패 자리에서 종료코드를 손으로 고르지 않게 한다. 이름(kind)만 고르면
#   번호(code)와 종료코드는 errcodes 표가 정한다 — 같은 상황에서 값이
#   갈리던 원인을 없앤다.
#    1) --json-errors 면 stdout 에 오류 JSON 한 줄
#    2) 화면(stderr)과 오류 로그에는 지금까지와 똑같이
#    3) 표가 정한 종료코드를 돌려준다
#
#   [이름 주의] fail_code 와 마찬가지로 프로세스를 끝내지 않는다.
#   반드시 `return fail_err(...)` 로 써야 한다.
#
# -in: kind = 오류 이름(errcodes.ERRORS 의 키)
# -in: msg  = 사용자에게 보일 안내(여러 줄 가능)
# -in: path = 문제가 된 파일·폴더(없으면 JSON 에서 키 자체가 빠진다, 기본 None)
#
# -out: code = 표가 정한 종료코드
# -out: error = 표에 없는 이름이면 KeyError(개발 중 오타)
#------------------------------------------------------------------
#------------------------------------------------------------------
# 오류 안내 + 오류 로그 + 종료코드 (한 번에)
#=> "화면에 안내하고 코드로 끝낸다"를 한 함수로 묶는다. 예전에는 print 만 하고
#   끝내는 자리가 많아, exe 를 UI·배치로 돌리면 그 안내가 사라져 아무 흔적도
#   남지 않았다. 이 함수를 쓰면 화면 동작은 그대로면서 오류 로그
#   (log/class_err_YYYYMMDD.log)에도 같은 내용이 남는다.
#    1) 화면(stderr)에는 받은 그대로 — 여러 줄이면 여러 줄로 보여 준다
#    2) 로그에는 한 줄로 눌러 담는다(아래 이유 참고)
#    3) 받은 종료코드를 그대로 돌려준다
#
#   [이름 주의] 이 함수는 프로세스를 끝내지 않는다. 종료코드를 '돌려주기만' 한다.
#   그래서 이름이 fail 이 아니라 fail_code 다 — 반드시 `return fail_code(...)` 처럼
#   return 과 함께 써야 한다. return 을 빠뜨리면 메시지·로그만 남고 실행이 그대로
#   이어진다. (Rust 판 errlog::fail() 은 이름이 비슷하지만 정말로 exit 하고
#   돌아오지 않는다 — 두 구현을 오가며 읽을 때 혼동하지 않도록 이름을 구분했다.)
#
# -in: msg  = 사용자에게 보일 안내(여러 줄 가능)
# -in: code = 돌려줄 종료코드(config.EXIT_* 중 하나)
#
# -out: code = 받은 종료코드 그대로 (호출부에서 return fail_code(...) 로 쓴다)
# -out: error = 없음(예외를 던지지 않는다)
#------------------------------------------------------------------
def fail_err(kind, msg, path=None):
    errcodes.emit(kind, msg, path)
    if errcodes.is_json():
        # 같은 내용이 이미 stdout 으로 나갔다. stderr 로 한 번 더 내면, 부르는 쪽이
        # 두 갈래를 합쳐 받을 때(2>&1) JSON 뒤에 사람용 문장이 따라붙어 파싱이 깨진다.
        # 로그 파일에는 그대로 남긴다 — 화면이 없는 배치에서 원인을 찾을 흔적이다.
        logsetup.get_logger("csoclassify.cli").error(
            "%s", " / ".join(msg.splitlines()), stacklevel=2)
        return errcodes.exit_of(kind)
    return fail_code(msg, errcodes.exit_of(kind))


def fail_code(msg, code):
    print(msg, file=sys.stderr)
    # 로그는 한 줄로 눌러 담는다 — 여러 줄이면 로그 파일에서 한 사건이 여러 건처럼 보인다.
    # stacklevel=2 로 '이 함수'가 아니라 '실제로 오류를 낸 호출부'의 위치가 기록되게 한다
    # (포맷에 %(funcName)s/%(lineno)d 를 넣으면 6건 모두 fail_code 로 찍히는 것을 막는다).
    logsetup.get_logger("csoclassify.cli").error(
        "%s", " / ".join(msg.splitlines()), stacklevel=2)
    return code


#------------------------------------------------------------------
# 출력 형식 확정 — --format 이 없으면 --out 확장자로 판단
#=> "--out result.jsonl 로 저장했는데 안에는 JSON 배열이 들어 있어 뷰어가 못 연다"는
#   함정을 없앤다. --out 이름이 사실상 형식을 말하고 있으므로 그대로 따른다.
#    1) --format 을 명시했으면 그 값을 그대로 쓴다(사용자 의도가 최우선)
#    2) 없으면 --out 확장자로 정한다(.jsonl/.ndjson→jsonl, .json→json, .txt→text)
#    3) --out 도 없거나 모르는 확장자면 기본값(json)
#   추론이 일어나면 화면에 한 줄 알린다 — 조용히 형식이 바뀌면 그게 더 놀랍다.
#
# -in: fmt = --format 값(안 줬으면 None)
# -in: out = --out 경로(안 줬으면 None)
# -in: log = 로거(없으면 로그 생략)
#
# -out: str = "text" | "json" | "jsonl"
# -out: error = 없음(모르는 확장자는 기본값으로 환원)
#------------------------------------------------------------------
def resolve_format(fmt, out, log=None):
    # 명시했으면 그대로 — 확장자가 달라도 사용자가 정한 것이 이긴다.
    if fmt:
        return fmt
    if out:
        ext = os.path.splitext(out)[1].lower()
        guessed = {".jsonl": "jsonl", ".ndjson": "jsonl",
                   ".json": "json", ".txt": "text"}.get(ext)
        if guessed:
            if guessed != config.DEFAULT_FORMAT:
                print(f"[csoclassify] --out 확장자({ext})에 맞춰 --format {guessed} 로 저장합니다."
                      f" (다르게 하려면 --format 을 직접 지정하세요)", file=sys.stderr)
            if log is not None:
                log.info("출력 형식 추론: out=%s → format=%s", out, guessed)
            return guessed
    return config.DEFAULT_FORMAT


#------------------------------------------------------------------
# 기계가 읽는 줄만 통과시키는 stderr 거름망
#=> --json-errors 를 준 호출에서 사람용 알림([전파]·본문없음·경고 등)을 막는다.
#   자리를 하나씩 고치지 않는 이유: 그런 자리가 60곳이 넘어 빠뜨리기 쉽고,
#   나중에 새로 생기는 줄까지 자동으로 걸러야 하기 때문이다.
#    1) 줄 단위로 모은다(print 는 본문과 줄바꿈을 따로 쓴다)
#    2) [progress]·[summary] 로 시작하는 줄만 진짜 stderr 로 흘린다
#       — 화면 진행바와 완료 메시지가 그 두 줄을 읽는다
#
# -필드: raw  = 진짜 stderr(여기로만 통과시킨다)
# -필드: buf  = 아직 줄바꿈을 못 만난 조각
#------------------------------------------------------------------
class _QuietStderr:
    KEEP = ("[progress]", "[summary]")

    #--------------------------------------------------------------
    # 거름망 만들기
    #=> 원래 stderr 를 품고 있다가 통과시킬 줄만 넘긴다.
    #
    # -in: raw = 원래 sys.stderr
    #
    # -out: 없음
    # -out: error = 없음
    #--------------------------------------------------------------
    def __init__(self, raw):
        self.raw = raw
        self.buf = ""

    #--------------------------------------------------------------
    # 글자 받기 — 줄이 완성될 때마다 판단한다
    #=> print 는 "본문"과 "\n" 을 따로 쓴다. 조각만 보고 판단하면 같은 줄을
    #   반쯤 흘려보내게 되므로, 줄바꿈을 만날 때까지 모았다가 검사한다.
    #
    # -in: s = 쓰려는 글자
    #
    # -out: int = 받은 글자 수(파일 객체 흉내)
    # -out: error = 없음
    #--------------------------------------------------------------
    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.startswith(self.KEEP):
                self.raw.write(line + "\n")
        return len(s)

    #--------------------------------------------------------------
    # 남은 조각 내보내기
    #=> 줄바꿈 없이 끝난 마지막 조각도 규칙에 맞으면 흘려보낸다.
    #
    # -in: 없음
    #
    # -out: 없음
    # -out: error = 없음
    #--------------------------------------------------------------
    def flush(self):
        if self.buf.startswith(self.KEEP):
            self.raw.write(self.buf)
            self.buf = ""
        self.raw.flush()

    #--------------------------------------------------------------
    # 그 밖의 파일 객체 흉내
    #=> isatty() 처럼 라이브러리가 물어보는 것들을 원래 stderr 에 넘긴다.
    #
    # -in: name = 찾는 속성 이름
    #
    # -out: 원래 stderr 의 그 속성
    # -out: error = 없으면 AttributeError
    #--------------------------------------------------------------
    def __getattr__(self, name):
        return getattr(self.raw, name)


#------------------------------------------------------------------
# 상태 줄에 실을 '대상' 뽑기
#=> 부르는 쪽이 여러 폴더를 돌릴 때, 어느 실행의 결과인지 알아야 한다.
#   인자를 다시 해석하지 않고 날것의 argv 에서 --file/--dir 값만 집어 온다
#   (여기는 argparse 가 실패한 뒤에도 불릴 수 있는 자리다).
#
# -in: argv = 인자 리스트(None 이면 sys.argv[1:])
#
# -out: str = --file 또는 --dir 값 · 없으면 None
# -out: error = 없음
#------------------------------------------------------------------
def _target_of(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    for i, a in enumerate(argv):
        if a in ("--file", "-file", "--dir", "-dir") and i + 1 < len(argv):
            return argv[i + 1]
    return None


#------------------------------------------------------------------
# 진입점 — 예상 못 한 오류까지 파일에 남기는 바깥 껍데기
#=> 실제 처리는 _main() 이 한다. 여기서는 그 바깥을 try 로 감싸, 어디서든 잡히지
#   않은 예외가 올라오면 오류 로그에 스택까지 남기고 종료코드로 환원한다.
#   왜 필요한가: exe 로 배포하면 UI·배치·스케줄러가 실행해 화면이 없다. 그러면
#   파이썬이 찍는 트레이스백이 그대로 사라져 "왜 죽었는지" 알 방법이 없어진다.
#
# -in: argv = 인자 리스트(None 이면 sys.argv 사용)
#
# -out: code = 종료코드(정상 0, 그 밖은 각 모드가 정한 값, 예상 못 한 예외는 1)
# -out: error = 없음(모든 예외를 코드로 환원 — 여기서 예외를 올리면 로그가 안 남는다)
#------------------------------------------------------------------
def main(argv=None):
    # 인자 해석이 실패해도 JSON 으로 알려야 하므로, 파서를 만들기 전에 날것의
    # argv 를 훑어 옵션을 먼저 켠다.
    errcodes.enable_json(errcodes.wants_json(argv))
    # 기계가 읽는 호출에서는 사람용 알림을 stderr 로 내지 않는다(로그 파일에는 남는다).
    if errcodes.is_json():
        sys.stderr = _QuietStderr(sys.stderr)
    try:
        #------------------------
        # _main 호출
        #------------------------
        code = _main(argv)

        # 성공이든 부분 실패든 마지막에 상태 한 줄을 낸다(이미 실패 줄을 냈으면
        # emit_final 이 알아서 넘어간다).
        errcodes.emit_final(code, _target_of(argv))
        return code
    except SystemExit:
        raise                       # sys.exit() 는 정상 흐름이므로 그대로 통과
    except KeyboardInterrupt:
        # 사용자가 Ctrl+C 로 멈춘 것은 '오류'가 아니다 — 로그를 더럽히지 않는다.
        if not errcodes.is_json():
            print("\n[csoclassify] 사용자가 중단했습니다.", file=sys.stderr)
        return 130
    except BaseException as e:      # noqa: BLE001  (여기서 놓치면 흔적이 안 남는다)
        log = logsetup.get_logger("csoclassify.cli")
        # exc_info=True 로 스택까지 남긴다 — 한 줄 메시지만으로는 원인을 못 찾는다.
        log.error("예상하지 못한 오류로 중단: %s: %s", type(e).__name__, e, exc_info=True)
        errcodes.emit("internal_error", f"{type(e).__name__}: {e}")
        if not errcodes.is_json():
            print(f"[csoclassify] 예상하지 못한 오류: {type(e).__name__}: {e}", file=sys.stderr)
            print(f"[csoclassify] 자세한 내용은 오류 로그를 보세요: "
                  f"{logsetup.default_err_log_path()}", file=sys.stderr)
        return 1


def _main(argv=None):
    # 출력 인코딩을 UTF-8 로 고정한다. 얼려진(PyInstaller exe) 환경에서는 PYTHONUTF8 이
    # 무시되어 stdout/stderr 가 로케일(예: 한국어 Windows=cp949)로 나가는데, UI 가
    # 파이프를 UTF-8 로 읽으면 [progress] 줄의 한글 파일명이 깨진다. 코드로 강제 고정해
    # 소스/모듈/exe 어디서 실행하든 항상 UTF-8 로 내보낸다.
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass  # 스트림이 없거나(windowed) reconfigure 미지원이면 그냥 둔다

    #------------------------------------------------------------
    # 입력 인자 파싱
    # => --dir, --simple-why, --nosummary, --json-errors 등등...
    #------------------------------------------------------------
    parser = build_parser()
    args = parser.parse_args(argv)

    # (0) 로깅 먼저 구성: --log 없으면 exe 옆 log/ 폴더 기본 경로를 쓴다.
    #     여기서 확정한 절대경로를 데몬에도 그대로 넘겨(같은 파일에 기록) 한다.
    args._log_path = os.path.abspath(args.log) if args.log else logsetup.default_log_path()
    # 오류(ERROR 이상)는 log/class_err_YYYYMMDD.log 에도 따로 쌓는다.
    # 일반 로그는 정상 처리 기록까지 수천 줄이라, 문제만 빨리 보려면 별도 파일이 필요하다.
    args._err_log_path = logsetup.default_err_log_path()
    logsetup.setup_logging(args._log_path, args.verbose, err_log_path=args._err_log_path)
    log = logsetup.get_logger("csoclassify.cli")

    # 어떤 실행이었는지 남겨 재현/추적을 돕는다: (1) 실제 커맨드라인 풀경로, (2) 파싱된 argv.
    log.info("실행 cmd=%s", _full_command_line())
    log.info("실행 argv=%s", argv if argv is not None else sys.argv[1:])

    # (0-1) 출력 형식 확정 — 아래 모든 모드가 args.fmt 를 그대로 읽으므로 여기서 한 번만 정한다.
    args.fmt = resolve_format(args.fmt, args.out, log)

    # (1) 데몬 제어 명령 우선 처리.
    dc = handle_daemon_commands(args)
    if dc is not None:
        log.info("데몬실행(--serve/--status/--stop)")
        return dc

    # (1.3) --failsafe 값 검증. 이 값은 규칙셋을 거치지 않고 곧바로 최종 등급이 되므로,
    #   오타가 있으면 정의되지 않은 등급이 그대로 결과에 박힌다(규칙셋 오타와 같은 종류의
    #   fail-open). 스캔 시작 전에 막는다.
    #   => 어느 신호로도 못 정한 문서에 부여할 기본등급. C/S/O 만 허용(그 외는 종료코드 4)
    #   => 지정하지 않으면 기본등급: S 
    if args.failsafe is not None:
        from .classify import GRADES
        if args.failsafe not in GRADES:
            # 부른 쪽이 준 값이 틀린 것이므로 '인자 오류(3)'다. 예전에는 규칙셋
            # 내용 오류와 같은 4 로 나가 배치가 원인을 구분할 수 없었다.
            return fail_err("bad_failsafe",
                            f"[csoclassify] --failsafe 값이 올바르지 않습니다: {args.failsafe!r}\n"
                            f"  정의된 등급: {' < '.join(GRADES)}")

    # (1.35) 분류체계 스냅샷 내보내기 모드: 문서도 모델도 필요 없다 → 가장 먼저 처리.
    # => 엠파워에 문서분류체계 doc_classification_export.json -> doc_taxonomy.yaml 파일로 만듬
    # => doc_taxonomy.yaml 은 1차분류시 node 값(dc_id : 문서분류id) 만 필요.
    if getattr(args, "export_taxonomy", False):
        log.info("문서분류체계파일 doc_taxonomy.yaml 생성(--export_taxonomy)")
        return run_export_taxonomy(args)

    # (1.36) 업무분류 규칙 채우기 모드: 분류 체계와 규칙 파일만 있으면 된다.
    # => 화면의 [분류 불러오기] 버튼과 같은 일. 문서도 모델도 필요 없다.
    if getattr(args, "sync_doc_rule", False):
        log.info("문서분류규칙파일 doc_rule.yaml 생성(--sync_doc_rule)")
        return run_sync_doc_rule(args)

    # (1.4) 규칙셋 검사 모드: 문서도 모델도 필요 없다 → 파일 수집 전에 먼저 끝낸다.
    # => cso_rules.yaml, doc_rules.yaml, doc_taxonomy.yaml 파일 유효성 검사.
    if getattr(args, "check_rules", False):
        log.info("규칙파일 유효성 검사(--check_rules)")
        return run_check_rules(args)

    # (1.5) 전파 모드: 입력이 레코드 파일이라 --file/--dir·모델이 필요 없다 → 먼저 처리.
    # => cli.py --propagate / --auto-propagate 인자로 실행하면.
    if args.propagate:
        log.info("전파모드실행(--propagate, --auto-propagate)")
        return run_propagate(args)

    # (2) 모델 별칭이 유효한지 먼저 확인(빠른 실패).
    # => DEFAULT_MODEL = "e5-small-ko" 로 정의되어 있음.
    try:
        config.get_model_spec(args.model)
    except KeyError as e:
        return fail_err("bad_args", f"[csoclassify] {e}")

    # (3) 대상 파일 수집.
    # => --file 혹은 --dir 처리
    # => --dir 인 경우에는 --glob 로 확장자도 지정(# *.hwp,*.pdf)할수 있음.
    # 목록과 --file/--dir 을 같이 주면 어느 쪽이 진짜 대상인지 알 수 없다 —
    # 조용히 하나를 고르면 '왜 저 파일이 빠졌지?' 로 이어지므로 그 자리에서 막는다.
    if getattr(args, "files_from", None) and (args.file or args.dir):
        return fail_err("bad_args",
                        "[csoclassify] --files-from 은 --file/--dir 과 함께 쓸 수 없습니다.")

    # (3-a) 입력 목록(--filelist) 로드 — 대상 수집보다 먼저 한다.
    # 목록이 깨져 있으면(F1·F3) 문서를 한 건도 읽기 전에 멈추는 것이 맞다.
    # 절반쯤 잘못된 ID 가 붙은 결과가 나가는 것이 최악이기 때문이다(규칙셋 검증과 같은 원칙).
    args._filelist = None
    if getattr(args, "filelist", None):
        from . import filelist as filelist_mod
        try:
            args._filelist = filelist_mod.load(args.filelist)
        except filelist_mod.FileListError as e:
            # '파일이 없다'와 '내용이 잘못됐다'는 부르는 쪽의 대응이 다르므로 코드를 나눈다.
            kind = ("filelist_missing" if not os.path.isfile(args.filelist or "")
                    else "filelist_invalid")
            return fail_err(kind, f"[csoclassify] {e}")
        st = args._filelist.stats
        print(f"[csoclassify] 목록 {os.path.basename(args.filelist)}: "
              f"{st['loaded']}건 적재(전체 {st['lines']}줄)", file=sys.stderr)
        for w in args._filelist.warnings:
            print(f"[csoclassify] {w}", file=sys.stderr)

    #-------------------------------------------
    # 문서 수집
    # => --file, --dir, --filelist 등 인자값에 따라 분류할 문서수집
    #-------------------------------------------
    files = collect_files(args)
    log.info("대상수집파일목록(%d건): dir=%s, file=%s", 
             len(files), args.dir, args.file)
    
    # 수집대상목록 출력 해봄.
    #for f in files:
    #    print(f"[대상수집목록] {f}", file=sys.stderr)
    #    log.inof(f"[대상수집목록] {f}")

    if not files:
        # 두 상황을 갈라 준다 — 부르는 쪽의 대응이 다르다.
        #   · 대상을 아예 안 줌(1002)      → 명령 자체를 고쳐야 한다
        #   · 줬는데 0건(1001)             → 사용자에게 폴더를 다시 물으면 된다
        target = (args.file or args.dir or getattr(args, "files_from", None)
                  or getattr(args, "filelist", None))
        if not target:
            return fail_err("no_target_arg",
                            "[csoclassify] 처리할 파일이 없습니다. "
                            "--file <경로> · --dir <폴더> · --files-from <목록> · "
                            "--filelist <목록> 중 하나를 지정하세요.")
        # 왜 0건인지를 상황에 맞게 말해 준다 — "없다"만으로는 무엇을 고칠지 모른다.
        if args.file:
            why = ("폴더입니다 — 폴더는 --dir 로 지정하세요."
                   if os.path.isdir(args.file) else "그런 파일이 없습니다.")
        elif getattr(args, "files_from", None):
            why = "목록이 비었거나, 목록의 경로가 모두 실제 파일이 아닙니다."
        else:
            why = f"폴더가 없거나, --glob 패턴({args.glob})에 맞는 파일이 없습니다."
        return fail_err("no_input",
                        f"[csoclassify] 처리할 파일이 없습니다: {target}\n  {why}",
                        target)

    # (3-1) 압축파일(zip) 확장: zip 이 섞여 있으면 임시폴더에 풀어 내부 '파일별'로 나눈다.
    #   → 압축 1개가 여러 건으로 분류돼 어느 내부 파일이 C/S/O 인지 알 수 있다.
    #   임시폴더는 처리 후 반드시 지운다(민감정보 잔존 방지). 일반 파일만 있으면 그대로.
    #
    # => Windows 기준으로 이런 폴더를 만듬.(압축푸는건 아님. 폴더만 만듬)
    # C:\Users\bong9\AppData\Local\Temp\cso_zip_a7f3k1qz\
    #                              └────┬────┘└──┬──┘
    #                                prefix    자동 생성된 무작위 문자
    import shutil
    import tempfile
    from .extract.archive import expand_paths
    arch_tmp = tempfile.mkdtemp(prefix="cso_zip_")

    # 파싱된 args 에서 파이프라인/데몬이 쓰는 처리 옵션만 뽑아 dict 로 만든다
    opts = make_opts(args)

    # --out 이 있으면 결과 본문을 파일로 쓴다(시간요약은 여전히 stderr).
    out_fp = None
    if args.out:
        try:
            out_fp = open(args.out, "w", encoding="utf-8")
        except OSError as e:
            # 권한·디스크·다른 프로그램이 잡고 있음 — 결과를 만들어도 둘 데가 없다.
            shutil.rmtree(arch_tmp, ignore_errors=True)
            return fail_err("output_write_failed",
                            f"[csoclassify] 출력 파일을 쓰지 못했습니다: {args.out}\n  {e}",
                            args.out)
        
    #-----------------------------------------------------
    # 분류 시작
    #-----------------------------------------------------
    try:
        jobs = expand_paths(files, arch_tmp)

        # 명시 모드가 있으면 그쪽으로, 없으면 기본 = C/S/O 분류.
        # => --text-only 일때 문서에서 text만 추출(*python 모드 exe 일때만)
        if args.text_only: 
            return run_text_only(jobs, opts, out_fp)
        # => --embed 일때는 문서에서 벡터만 추출(*python 모드 exe 일때만)
        if args.embed:
            return run_embed(jobs, args, opts, out_fp)

        #--------------------------------------------------------------------------
        # 실제 분류 처리.
        # => --text-only, --embed 옵션 외는 분류진행.
        # => text 추출/정제 -> 1단계 rule 분류 -> 벡터생성 -> 씨드생성 -> 2차 전파(분류)
        #--------------------------------------------------------------------------
        return run_classify(jobs, args, out_fp)
    finally:
        if out_fp is not None:
            out_fp.close()
        # 압축 확장에 쓴 임시폴더(내부 원문 포함)를 통째로 정리한다.
        shutil.rmtree(arch_tmp, ignore_errors=True)
