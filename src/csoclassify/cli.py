#------------------------------------------------------------------
# CLI 진입점 (설계서 §6)
#=> csoclassify.exe 의 명령행을 해석하고 모드를 분기한다.
#    - --serve/--status/--stop : 데몬 제어
#    - 일반 : 파일/폴더를 처리해 파일명+벡터(+시간)를 출력
#   기본은 데몬 on. 데몬을 못 쓰면 in-process 로 자동 폴백해 항상 동작하게 한다.
#------------------------------------------------------------------

import argparse
import glob
import os
import re
import sys

from . import __version__
from . import config
from . import logsetup
from . import output
from .timing import Timing


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
    p = argparse.ArgumentParser(
        prog="csoclassify",
        description="한국어 문서 → C/S/O 자동 분류(기본) · 임베딩 벡터(--embed) · 텍스트 추출(--text-only)",
    )
    # 입력 대상
    # 표준(GNU) 관례에 맞춰 긴 이름은 '--' 로 통일한다(--file/--dir). 기존 스크립트·
    # UI 가 쓰던 단일대시 '-file'/'-dir' 도 하위호환 별칭으로 계속 받는다.
    p.add_argument("--file", "-file", dest="file", help="대상 문서 1개 경로")
    p.add_argument("--dir", "-dir", dest="dir", help="대상 폴더(배치)")
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
                   help="파일별로 문서명·등급(C/S/O)·해시 3가지만 JSON 으로 출력")

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
                   help="로그 파일 경로(미지정 시 <exe폴더>/log/csoclassify-날짜.log)")
    p.add_argument("--version", action="version", version=f"csoclassify {__version__}")
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
    if args.file:
        return [args.file]
    if args.dir:
        found = []
        # 콤마/중괄호로 패턴 여러 개를 준 경우 패턴별로 각각 매칭해 합친다.
        for gp in expand_glob_patterns(args.glob):
            # --dir 은 항상 '**' 로 하위 폴더까지 재귀 탐색한다(-r 여부와 무관).
            found.extend(glob.glob(os.path.join(args.dir, "**", gp), recursive=True))
        # 매칭 결과 중 실제 파일만(폴더 제외) 대상으로, 중복 제거해 정렬한다
        # (여러 패턴에 동시에 걸린 파일이 있을 수 있음).
        return sorted({f for f in found if os.path.isfile(f)})
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
    if not isinstance(s, str):
        return s
    try:
        s.encode("utf-8")
        return s          # 정상 문자열이 대부분이므로 이 경로가 가장 빠르다
    except UnicodeEncodeError:
        # surrogateescape 로 되돌린 '원래 바이트'를 UTF-8 로 다시 읽으며,
        # 해독 안 되는 자리만 U+FFFD 로 바꾼다(나머지 글자는 최대한 살린다).
        return s.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


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
    # now_iso 는 run_classify 안에서 지역 import 하는 이름이라 모듈 전역에는 없다.
    # 이 함수도 같은 방식으로 그때 가져온다(무거운 모듈을 import 시점에 끌어오지
    # 않으려는 이 파일의 관례를 따른다).
    from .classify import now_iso
    from .classify.engine import _attach_doctype
    rec = {
        "file": path,
        "grade": None,
        "confidence": 0.0,
        "method": "extract_failed",
        "decided_by": [],
        "seed_eligible": False,
        "signals": {},
        "error": {"stage": "extract", "reason": str(err)},
        "rule_version": getattr(ruleset, "version", ""),
        "ts": now_iso(),
        "labels": {
            "security": {"value": None, "confidence": 0.0, "strategy": "max",
                         "method": "extract_failed", "decided_by": [], "candidates": []},
        },
    }
    # 본문이 없어도 축이 켜져 있으면 '축은 돌았다'를 남긴다 — 그래야 화면이
    # "분류 안 됨(사람이 봐야 함)"으로 셀 수 있다(키 부재 = 축 미사용과 구분).
    _attach_doctype(rec, "", path, doc_rules, taxonomy)
    return rec


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
# --simple 용 레코드 축약(문서명·등급·해시 3필드)
#=> 결과 레코드에서 사용자가 요청한 3가지(file·grade·hash)만 남긴 새 dict 를 만든다.
#   원본 레코드는 건드리지 않는다.
#
# -in: rec = 전체 결과 레코드(hash 필드가 이미 채워져 있어야 함)
#
# -out: dict = {"file":..., "grade":..., "hash":...}
# -out: error = 없음
#------------------------------------------------------------------
def _simple_record(rec):
    return {"file": rec.get("file"), "grade": rec.get("grade"), "hash": rec.get("hash")}


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
        taxonomy = AX.load_taxonomy(taxonomy_path)
    except FileNotFoundError as e:
        if axis == "doctype":
            print(f"[csoclassify] {e}", file=sys.stderr)
            return None, None, config.EXIT_RULES_INVALID
        print(f"[csoclassify] doc_taxonomy.yaml 이 없어 업무분류(doctype) 축을 건너뜁니다.\n"
              f"              보안등급(security)만 판정합니다. 분류체계를 쓰려면\n"
              f"              scripts/export_taxonomy.py 로 내보낸 뒤 exe 옆에 두세요.",
              file=sys.stderr)
        return None, None, None
    except AX.TaxonomyValidationError as e:
        print(f"[csoclassify] {e}", file=sys.stderr)
        log.error("분류체계 스냅샷 검증 실패 count=%d path=%s", len(e.violations), e.path)
        return None, None, config.EXIT_RULES_INVALID

    stale = _check_stale_taxonomy(taxonomy)
    if stale:
        print(stale, file=sys.stderr)
        log.warning("분류체계 스냅샷 노후 :: exported_at=%s", taxonomy.exported_at)

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
        print(f"[csoclassify] {e}", file=sys.stderr)
        log.error("업무분류 규칙셋 검증 실패 count=%d path=%s", len(e.violations), e.path)
        return None, None, config.EXIT_RULES_INVALID

    for w in doc_rules_set.warnings:
        print(f"[csoclassify] {w}", file=sys.stderr)
        log.warning("doctype 규칙 경고 :: %s", w)

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
# C/S/O 분류 처리(기본 모드 · 예전 --classify)
#=> 임베딩 없이, 추출→정제한 텍스트에 규칙 스캔(Signal A)을 걸어 문서마다 등급
#   레코드를 출력한다. 모델이 필요 없어 항상 in-process 로 돈다.
#    1) 규칙셋 1회 로드(--rules 로 교체 가능)
#    2) 파일마다: 추출→정제→build_record(규칙/경로/파일명으로 등급 산출) → jsonl/json
#    3) 마지막에 등급 분포 요약을 stderr 로(오탐률 실측용)
#   [프라이버시] 추출 텍스트는 기본적으로 디스크에 남기지 않는다(save_dir=None → 자동삭제).
#     단 --with-text 를 주면 결과 레코드에 정제 텍스트를 rec["text"] 로 함께 저장한다.
#   [진행표시] args.progress 면 파일마다 "[progress] 처리수/총수 경로" 를 stderr 로
#     흘려 보낸다(성공/실패 무관). UI 가 이 줄을 읽어 진행바·경과시간을 그린다.
#   [임베딩 정책] 임베딩은 등급 결정엔 안 쓰이고(1차 분류는 규칙/경로/파일명만),
#     전파(2차)의 '보류 구제 대상'과 '내부 seed 기준'에만 필요하다. 그래서:
#       · --with-vector : 모든 문서 임베딩(구 동작, RAG 등 전량 벡터가 필요할 때)
#       · --embed-needed: 보류(grade=None)이거나 seed_eligible 인 문서만 임베딩
#                         → 규칙으로 확정된 대다수 문서의 임베딩을 건너뛰어 대폭 빨라짐
#       · 둘 다 없음     : 임베딩 안 함(가장 빠른 규칙-only 분류)
#   [전파(자동)] 기본 분류는 보류(none) 문서를 seed 와 임베딩 비교해 자동 전파한다:
#     seed 파일(--seeds 또는 exe 옆 class_seed.jsonl)이 '있으면' auto_prop 을 자동으로 켜고,
#     보류 문서 벡터가 필요하므로 임베딩(needed)도 자동 활성화한다. seed 파일이 없으면
#     전파 없이 규칙만으로 끝낸다(레코드 스트리밍). --auto-propagate 로 명시할 수도 있다.
#     전파는 '외부 seed(class_seed.jsonl) + 내부 seed_eligible'을 함께 기준으로 쓴다.
#   [분류방식 배타옵션]
#       · --rule-only  : 규칙(cso_rules.yaml)만으로 분류. seed 가 옆에 있어도 임베딩·전파를
#                        아예 안 한다(순수 규칙 분류를 보장하는 명시적 차단, 가장 빠름).
#       · --vector-only: 규칙 검사를 하지 않고(rules_enabled=False → 전부 보류 레코드),
#                        전량 임베딩 후 seed 와 벡터 비교(전파)로만 등급을 정한다.
#                        비교 기준 seed 파일(--seeds)이 반드시 있어야 한다(없으면 인자 오류).
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
    # 규칙셋은 파일마다 다시 읽지 않도록 한 번만 로드해 재사용한다.
    # 규칙셋은 외장 파일(exe 옆)이라 누락이 흔하다 → 원시 스택 대신 안내 후 정상 종료.
    try:
        ruleset = load_rules(args.rules)
    except FileNotFoundError as e:
        print(f"[csoclassify] {e}", file=sys.stderr)
        log.error("규칙셋 로드 실패 :: %s", e)
        return config.EXIT_ARG_ERROR
    except RuleSetValidationError as e:
        # 파일은 있지만 내용이 틀림 → 스캔을 시작하지 않고 위반 전체를 보여 준다.
        # "파일 없음(3)"과 구분되는 코드 4 로 나가 배치가 대응을 나눌 수 있게 한다.
        print(f"[csoclassify] {e}", file=sys.stderr)
        log.error("규칙셋 검증 실패 count=%d path=%s", len(e.violations), e.path)
        return config.EXIT_RULES_INVALID

    # 업무분류(doctype) 축 — 있으면 켜고 없으면 security 만(4-6). --axis doctype 이면 필수.
    taxonomy, doc_rules_set, dt_code = _load_doctype_axis(args, log)
    # not None => 규칙파일이 잘못된 경우임. 이때는 에러띄우고 종료.
    if dt_code is not None: 
        return dt_code

    # --conflict 로 doctype 전략을 실행 시 덮어쓰기(6-5). security 는 서열 축이라 금지(T11).
    doctype_strategy_override = None   # 있으면 rec["labels"]["doctype"]["strategy"] 에 각인
    if getattr(args, "conflict", None):
        from .classify import AxisNotOverridableError, ensure_overridable_axis
        try:
            override_axis, override_spec = _parse_conflict_override(args.conflict)
            ensure_overridable_axis(override_axis)
        except (ValueError, AxisNotOverridableError) as e:
            return fail(f"[csoclassify] {e}", config.EXIT_RULES_INVALID)
        if doc_rules_set is not None:
            import dataclasses
            doc_rules_set = dataclasses.replace(doc_rules_set, conflict=override_spec)
        # 재현성을 위해 실제 적용된 문자열을 그대로 결과에 남긴다(설계서 6-5 warn).
        doctype_strategy_override = args.conflict.split("=", 1)[1]

    axis = getattr(args, "axis", None)
    # --hybridparse 여부에 따라 사이냅 단독 또는 하이브리드 추출기를 만든다(분류는 추출이 클라이언트측).
    extractor = build_extractor(hybrid=getattr(args, "hybridparse", False))

    with_text = getattr(args, "with_text", False)         # 결과 레코드에 추출 텍스트 포함
    auto_prop = getattr(args, "auto_propagate", False)    # 분류 직후 보류 문서를 seed 로 전파
    rule_only = getattr(args, "rule_only", False)         # 규칙만(임베딩·전파 없음)
    vector_only = getattr(args, "vector_only", False)     # 규칙 없이 벡터-seed 비교만
    # --axis doctype 이면 security 신호 자체를 계산하지 않는다(6-4 — PII 검출까지 포함해
    # 통째로 생략, 가장 비싼 단계를 건너뛰어 대량 업무분류 스캔이 크게 빨라진다).
    rules_enabled = not vector_only and axis != "doctype"

    # 전파 비교 기준 seed 경로: --seeds 우선, 없으면 exe 옆 class_seed.jsonl(기본 규약).
    seeds_path = args.seeds or default_seed_path()

    # 분류 방식 결정 — 배타 3분기: 벡터만 / 규칙만 / 기본(규칙+자동전파).
    if vector_only:
        # 규칙 미사용: 전량 임베딩 후 seed 와 비교해 등급을 정한다 → 비교 기준 seed 파일 필수.
        if not seeds_path or not os.path.isfile(seeds_path):
            return fail("[csoclassify] --vector-only 는 비교 기준 seed 파일이 필요합니다: "
                        "--seeds <class_seed.jsonl>(또는 exe 옆 class_seed.jsonl)",
                        config.EXIT_ARG_ERROR)
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

    # 'seed 전파 전용'(doc_rule.yaml 없음)인데 전파까지 못 하는 상황이면 미리 알린다.
    #   조용히 빈 결과를 내면 "분류할 게 없었다"로 오해되는데, 실제로는 "분류할 수단이
    #   없었다"라서 대응이 완전히 다르다(규칙을 쓰거나 seed 를 채워야 한다).
    #   전파를 못 하는 경우는 두 가지 — 임베딩을 아예 안 하거나(--rule-only 등),
    #   임베딩은 하는데 비교할 doctype seed 가 없거나.
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

    # 임베딩 수단 준비(임베딩이 필요한 정책일 때만). 데몬(웜 모델 재사용) 우선, 실패 시
    # in-process 폴백. 데몬을 쓰면 '따로따로 반복 실행'해도 model_load=0 으로 빨라진다.
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
        emb = _get_ip_embedder()
        timing.set("model_load", emb.ensure_loaded())
        with timing.measure("embed"):
            result, _ = emb.embed_document(text, args.max_tokens, args.overlap,
                                           normalize=True, per_chunk=False)
        return list(result)

    # 등급 분포 집계(요약용). none = 규칙 미검출(+failsafe 미사용).
    counts = {"C": 0, "S": 0, "O": 0, "none": 0}
    embedded = 0            # 실제 임베딩한 문서 수(요약·검증용)
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
    simple = getattr(args, "simple", False)               # 파일별 문서명·등급·해시 3필드만
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

    def _emit(rec):
        # --summary 면 파일별/집계 레코드는 아예 출력하지 않는다(맨 끝 요약만 낸다).
        if summary_only:
            return
        # --simple 이면 문서명·등급·해시 3가지만 남긴다.
        out = _simple_record(rec) if simple else rec
        writer.write_record(out)
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

    for i, item in enumerate(files, 1):
        # src=실제 읽을 경로(압축 내부면 임시파일), path(label)=표시·규칙신호용(압축경로/내부경로),
        # origin=이 파일이 나온 최상위 압축경로(일반 파일이면 None).
        src, path, origin = _as_job(item)
        # 파일별 스톱워치 — 추출/정제/규칙/(임베딩) 단계별 소요시간을 잰다(--embed 와 동일 형식).
        timing = Timing()
        try:
            # 분류에서는 원문 텍스트를 (기본은) 디스크에 남기지 않는다(민감정보 잔존 방지).
            with timing.measure("extract"):
                raw = extractor.extract(src, save_dir=None)
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
            counts["none"] = counts.get("none", 0) + 1
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

        # 1) 규칙(내용·민감정보·스탬프·경로·파일명)으로 먼저 등급을 낸다(임베딩은 관여 안 함).
        #    단 --vector-only(rules_enabled=False)면 규칙을 건너뛰고 '보류' 레코드만 만든다
        #    → 등급은 아래 전파 단계가 seed 비교로 정한다.
        #    --with-pii 면 검출된 원문 PII 값도 레코드에 싣는다(기본 off=미저장).
        with timing.measure("rule"):
            rec = build_record(path, text, ruleset, ts=now_iso(),
                               failsafe=args.failsafe, vector=None,
                               with_pii=getattr(args, "with_pii", False),
                               rules_enabled=rules_enabled,
                               doc_rules=doc_rules_set, taxonomy=taxonomy)
        # 업무분류 집계는 여기서 하지 않는다 — 전파(auto_prop)가 라벨을 더 붙일 수
        # 있어서, 지금 세면 전파 전 숫자가 요약에 박힌다. 레코드가 '최종'이 되는
        # 출력 직전에 _finalize_doctype() 으로 센다.

        # --hash/--simple 이면 문서 '내용'의 해시를 레코드에 싣는다(읽기 실경로 src 기준).
        if need_hash:
            rec["hash"] = _file_hash(src)

        # 2) 이 문서에 벡터가 필요한지 판단 → 필요할 때만 임베딩(느린 단계 절약).
        #    all=무조건 / needed=아직 못 정한 축이 하나라도 있을 때.
        #    'needed' 의 뜻은 축별로 이렇다:
        #      · security — 보류(grade=None)이거나 seed_eligible(내부 seed 후보)
        #      · doctype  — 축은 켜졌는데 라벨이 하나도 안 붙음(values 가 빔)
        #    doctype 조건이 꼭 필요한 이유: doc_rule.yaml 이 없어 'seed 전파 전용'으로
        #    도는 배포에서는 1차 스캔이 언제나 빈손이다. 그런데 security 등급은
        #    멀쩡히 나올 수 있어서, security 기준만 보면 벡터를 안 만들고 → 전파도
        #    못 하고 → 업무분류가 영영 미분류로 남는다.
        dt_label = rec.get("labels", {}).get("doctype")
        dt_undecided = dt_label is not None and not dt_label.get("values")
        need_vec = embed_mode == "all" or (
            embed_mode == "needed"
            and (rec["grade"] is None or rec.get("seed_eligible") or dt_undecided))
        if need_vec and embed_mode != "none":
            try:
                # 데몬(웜) 우선, 실패 시 in-process 폴백. build_record(vector=...) 과 동일 형식.
                rec["vector"] = _embed_text(text, timing)
                embedded += 1
            except Exception as e:  # 모델 없음 등 → 벡터 없이 진행
                log.warning("분류 임베딩 실패 file=%s :: %s", path, e)

        # 옵션: 추출(정제) 텍스트를 결과 레코드에 함께 저장(기본 off — 프라이버시).
        if with_text:
            rec["text"] = text

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

    # 옵션: 업무분류 seed 만들기(재설계 10장). 전파보다 '먼저' 한다 —
    # 전파가 라벨을 더하고 나면 어느 라벨이 규칙에서 온 것인지 흐려진다.
    if getattr(args, "make_doctype_seeds", None):
        from .classify import seedgen
        out_path = args.make_doctype_seeds
        if taxonomy is None or doc_rules_set is None:
            print("[csoclassify] 업무분류 축이 꺼져 있어 seed 를 만들 수 없습니다 "
                  "(--taxonomy·--doc-rules 확인)", file=sys.stderr)
        else:
            seeds, sstats = seedgen.select_doctype_seeds(
                seed_pool, taxonomy,
                t_seed=doc_rules_set.defaults.t_seed,
                per_dir=getattr(args, "seed_per_dir", 3),
                per_node=getattr(args, "seed_per_node", 50))
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

    # 옵션: 분류 직후 보류 문서를 seed 로 전파해 구제(단일 문서 확인에도 유용).
    if auto_prop:
        from .classify.propagate import SeedIndex
        from .classify.engine import propagate_records
        seed_index = None
        if seeds_path and os.path.isfile(seeds_path):
            seed_index = SeedIndex.from_seed_file(seeds_path)
            print(f"[csoclassify] 외부 seed {seed_index.size}건 로드: {seeds_path}", file=sys.stderr)
        elif args.seeds:
            # 사용자가 --seeds 로 명시했는데 파일이 없을 때만 알린다(내부 seed 로 진행).
            print(f"[csoclassify] seed 파일 없음(내부 seed 만 사용): {args.seeds}", file=sys.stderr)
        records, pstats = propagate_records(records, seed_index=seed_index, failsafe=args.failsafe)

        # 업무분류 축도 같은 seed 저장소로 전파한다. security 전파와 두 가지가 다르다:
        #   · 대상 — security 는 '보류 문서만' 구제하지만, doctype 은 이미 라벨이
        #     있는 문서에도 후보를 '더한다'(한 문서가 여러 분류에 동시에 맞을 수 있다).
        #   · 필요성 — doc_rule.yaml 이 없는 배포에서는 이 단계가 유일한 분류 수단이다.
        if doc_rules_set is not None and taxonomy is not None:
            from .classify.propagate import DoctypeSeedIndex
            from .classify.engine import propagate_doctype_records
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
    summary = {
        "total": total_files, "detected": detected,
        "C": c_cnt, "S": s_cnt, "O": o_cnt, "unclassified": none_cnt,
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
    # json 배열 모드면 마지막에 ']' 로 닫아 유효한 JSON 파일을 완성한다.
    writer.close()

    # (2) 화면(stderr) 최종 요약 — 사용자가 요청한 형식(총수/검출/등급별/미분류/총시간).
    #     --nosummary 면 이 화면 요약 줄도 내지 않는다(summary 를 마지막 출력에서 완전 제거).
    if not no_summary:
        arch_note = f", 압축 {len(arch_recs)}건" if arch_recs else ""
        print(
            f"[summary] 총 {total_files}개 / 검출 {detected}, "
            f"C={c_cnt} S={s_cnt} O={o_cnt} 미분류={none_cnt}{arch_note}, "
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
        return fail(f"[csoclassify] 전파 입력 파일이 없습니다: {args.propagate}",
                    config.EXIT_ARG_ERROR)

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
            return fail(f"[csoclassify] seed 파일이 없습니다: {args.seeds}",
                        config.EXIT_ARG_ERROR)
        seed_index = SeedIndex.from_seed_file(args.seeds)
        print(f"[csoclassify] 외부 seed {seed_index.size}건 로드: {args.seeds}", file=sys.stderr)

    # 전파 + 재융합(상향 전용).
    # 입력 레코드/seed 는 외부 파일이라 손으로 고쳐졌을 수 있다. 등급 값이 정의된
    # 서열에 없으면 max_grade 가 예외를 던지므로(fail-closed), 원시 스택 대신
    # "어느 파일이 문제인지" 알려 주고 규칙셋 오류와 같은 코드 4 로 나간다.
    try:
        records, stats = propagate_records(recs, seed_index=seed_index, failsafe=args.failsafe)
    except UnknownGradeError as e:
        print(f"[csoclassify] 전파 입력의 등급 값이 올바르지 않습니다: {args.propagate}\n"
              f"  {e}", file=sys.stderr)
        log.error("전파 입력 등급 오류 :: %s", e)
        return config.EXIT_RULES_INVALID

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
    out_fp = open(args.out, "w", encoding="utf-8") if args.out else None
    writer = output.RecordWriter(out_fp or sys.stdout, args.fmt, multi=len(records) > 1)
    try:
        for rec in records:
            writer.write_record(rec)
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
        print(f"[csoclassify] 원본 JSON을 찾을 수 없습니다: {input_path}\n"
              f"  · --export-input <파일경로> 로 지정하거나,\n"
              f"  · resources/policy/doc_classification_export.json 에 두세요.",
              file=sys.stderr)
        return config.EXIT_ARG_ERROR

    try:
        taxonomy, warnings = AX.export_from_mpower_json(input_path, output_path)
    except (ValueError, _json.JSONDecodeError) as e:
        # 원본 JSON 이 매핑이 아니거나 nodes 가 없거나 문법이 틀림 — 입력 문제.
        print(f"[csoclassify] {e}", file=sys.stderr)
        log.error("분류체계 내보내기 실패(원본 문제) :: %s", e)
        return config.EXIT_ARG_ERROR
    except AX.TaxonomyValidationError as e:
        # 변환은 됐지만 결과 트리가 깨짐(순환·고아 등) — 원본 데이터 자체의 문제.
        print(f"[csoclassify] 변환 결과가 검증을 통과하지 못했습니다:\n{e}", file=sys.stderr)
        log.error("분류체계 내보내기 검증 실패 count=%d", len(e.violations))
        return config.EXIT_RULES_INVALID

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
        print(f"[csoclassify] {e}", file=sys.stderr)
        return config.EXIT_ARG_ERROR
    except RuleSetValidationError as e:
        print(f"[csoclassify] {e}", file=sys.stderr)
        log.error("규칙셋 검증 실패 count=%d path=%s", len(e.violations), e.path)
        return config.EXIT_RULES_INVALID

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
        print(f"[csoclassify] {e}", file=sys.stderr)
        log.error("분류체계 스냅샷 검증 실패 count=%d path=%s", len(e.violations), e.path)
        return config.EXIT_RULES_INVALID
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
        print(f"[csoclassify] {e}", file=sys.stderr)
        log.error("업무분류 규칙셋 검증 실패 count=%d path=%s", len(e.violations), e.path)
        return config.EXIT_RULES_INVALID
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
# 오류 안내 + 오류 로그 + 종료코드 (한 번에)
#=> "화면에 안내하고 코드로 끝낸다"를 한 함수로 묶는다. 예전에는 print 만 하고
#   끝내는 자리가 많아, exe 를 UI·배치로 돌리면 그 안내가 사라져 아무 흔적도
#   남지 않았다. 이 함수를 쓰면 화면 동작은 그대로면서 오류 로그
#   (csoclassify_err_YYYYMMDD.log)에도 같은 내용이 남는다.
#
# -in: msg  = 사용자에게 보일 안내(여러 줄 가능)
# -in: code = 돌려줄 종료코드
#
# -out: code = 받은 종료코드 그대로 (호출부에서 return fail(...) 로 쓴다)
# -out: error = 없음
#------------------------------------------------------------------
def fail(msg, code):
    print(msg, file=sys.stderr)
    # 로그는 한 줄로 눌러 담는다 — 여러 줄이면 로그 파일에서 한 사건이 여러 건처럼 보인다.
    logsetup.get_logger("csoclassify.cli").error("%s", " / ".join(msg.splitlines()))
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
    try:
        return _main(argv)
    except SystemExit:
        raise                       # sys.exit() 는 정상 흐름이므로 그대로 통과
    except KeyboardInterrupt:
        # 사용자가 Ctrl+C 로 멈춘 것은 '오류'가 아니다 — 로그를 더럽히지 않는다.
        print("\n[csoclassify] 사용자가 중단했습니다.", file=sys.stderr)
        return 130
    except BaseException as e:      # noqa: BLE001  (여기서 놓치면 흔적이 안 남는다)
        log = logsetup.get_logger("csoclassify.cli")
        # exc_info=True 로 스택까지 남긴다 — 한 줄 메시지만으로는 원인을 못 찾는다.
        log.error("예상하지 못한 오류로 중단: %s: %s", type(e).__name__, e, exc_info=True)
        print(f"[csoclassify] 예상하지 못한 오류: {type(e).__name__}: {e}", file=sys.stderr)
        print(f"[csoclassify] 자세한 내용은 오류 로그를 보세요: {logsetup.default_err_log_path()}",
              file=sys.stderr)
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

    parser = build_parser()
    args = parser.parse_args(argv)

    # (0) 로깅 먼저 구성: --log 없으면 exe 옆 log/ 폴더 기본 경로를 쓴다.
    #     여기서 확정한 절대경로를 데몬에도 그대로 넘겨(같은 파일에 기록) 한다.
    args._log_path = os.path.abspath(args.log) if args.log else logsetup.default_log_path()
    # 오류(ERROR 이상)는 exe 옆 csoclassify_err_YYYYMMDD.log 에도 따로 쌓는다.
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
        return dc

    # (1.3) --failsafe 값 검증. 이 값은 규칙셋을 거치지 않고 곧바로 최종 등급이 되므로,
    #   오타가 있으면 정의되지 않은 등급이 그대로 결과에 박힌다(규칙셋 오타와 같은 종류의
    #   fail-open). 스캔 시작 전에 막는다.
    #   => 어느 신호로도 못 정한 문서에 부여할 기본등급. C/S/O 만 허용(그 외는 종료코드 4)
    #   => 지정하지 않으면 기본등급: S 
    if args.failsafe is not None:
        from .classify import GRADES
        if args.failsafe not in GRADES:
            return fail(f"[csoclassify] --failsafe 값이 올바르지 않습니다: {args.failsafe!r}\n"
                        f"  정의된 등급: {' < '.join(GRADES)}",
                        config.EXIT_RULES_INVALID)

    # (1.35) 분류체계 스냅샷 내보내기 모드: 문서도 모델도 필요 없다 → 가장 먼저 처리.
    # => 엠파워에 문서분류체계 doc_classification_export.json -> doc_taxonomy.yaml 파일로 만듬
    # => doc_taxonomy.yaml 은 1차분류시 node 값(dc_id : 문서분류id) 만 필요.
    if getattr(args, "export_taxonomy", False):
        return run_export_taxonomy(args)

    # (1.4) 규칙셋 검사 모드: 문서도 모델도 필요 없다 → 파일 수집 전에 먼저 끝낸다.
    # => cso_rules.yaml, doc_rules.yaml, doc_taxonomy.yaml 파일 유효성 검사.
    if getattr(args, "check_rules", False):
        return run_check_rules(args)

    # (1.5) 전파 모드: 입력이 레코드 파일이라 --file/--dir·모델이 필요 없다 → 먼저 처리.
    if args.propagate:
        return run_propagate(args)

    # (2) 모델 별칭이 유효한지 먼저 확인(빠른 실패).
    try:
        config.get_model_spec(args.model)
    except KeyError as e:
        print(f"[csoclassify] {e}", file=sys.stderr)
        return config.EXIT_ARG_ERROR

    # (3) 대상 파일 수집.
    # => --file 혹은 --dir 처리
    # => --dir 인 경우에는 --glob 로 확장자도 지정할수 있음.
    files = collect_files(args)
    if not files:
        return fail("[csoclassify] 처리할 파일이 없습니다. --file <경로> 또는 --dir <폴더> 를 지정하세요.",
                    config.EXIT_ARG_ERROR)

    # (3-1) 압축파일(zip) 확장: zip 이 섞여 있으면 임시폴더에 풀어 내부 '파일별'로 나눈다.
    #   → 압축 1개가 여러 건으로 분류돼 어느 내부 파일이 C/S/O 인지 알 수 있다.
    #   임시폴더는 처리 후 반드시 지운다(민감정보 잔존 방지). 일반 파일만 있으면 그대로.
    import shutil
    import tempfile
    from .extract.archive import expand_paths
    arch_tmp = tempfile.mkdtemp(prefix="cso_zip_")

    opts = make_opts(args)

    # --out 이 있으면 결과 본문을 파일로 쓴다(시간요약은 여전히 stderr).
    out_fp = None
    if args.out:
        out_fp = open(args.out, "w", encoding="utf-8")
    try:
        jobs = expand_paths(files, arch_tmp)
        # 명시 모드가 있으면 그쪽으로, 없으면 기본 = C/S/O 분류.
        # => --text-only 일때 문서에서 text만 추출(*python 모드 exe 일때만)
        if args.text_only: 
            return run_text_only(jobs, opts, out_fp)
        # => --embed 일때는 문서에서 벡터만 추출(*python 모드 exe 일때만)
        if args.embed:
            return run_embed(jobs, args, opts, out_fp)

        # => 그외는 분류진행
        return run_classify(jobs, args, out_fp)
    finally:
        if out_fp is not None:
            out_fp.close()
        # 압축 확장에 쓴 임시폴더(내부 원문 포함)를 통째로 정리한다.
        shutil.rmtree(arch_tmp, ignore_errors=True)
