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
    p.add_argument("--format", dest="fmt", choices=["text", "json", "jsonl"],
                   default=config.DEFAULT_FORMAT, help="출력 형식")
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
    # 추출 방식 — 기본은 사이냅(snf) 단독(현행). --hybridparse 면 확장자별 최적 엔진 라우팅.
    p.add_argument("--hybridparse", dest="hybridparse", action="store_true",
                   default=config.DEFAULT_HYBRID_PARSE,
                   help="[실험] 내용 감지로 포맷별 전용 파서 추출(사이냅 없이): pdf→pypdfium2, hwp→HWP5, "
                        "hwpx→zip/OWPML, doc/ppt→자체파서, xls→xlrd, docx/xlsx/pptx→python-*, text→직접읽기. "
                        "전용 파서 실패 시 snf 폴백(snf 없으면 그 파일만 미분류). 미지정 시 전부 사이냅(현행). "
                        "설계: plan/CSO_HybridParse.html")

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
    p.add_argument("--with-vector", dest="with_vector", action="store_true",
                   help="분류 시 모든 문서에 임베딩 벡터 산출(구 동작, 모델 필요)")
    p.add_argument("--embed-needed", dest="embed_needed", action="store_true",
                   help="임베딩을 '보류(미확정)'와 'seed_eligible' 문서에만 수행(확정문서 임베딩 생략, 속도↑)")
    p.add_argument("--propagate", dest="propagate", default=None,
                   help="1차 레코드(jsonl)에 임베딩 라벨 전파를 적용해 재분류")
    p.add_argument("--seeds", dest="seeds", default=None,
                   help="전파 비교 기준 seed 저장소(cso_seed.jsonl). 없으면 코퍼스 내부 seed 사용")
    p.add_argument("--with-text", dest="with_text", action="store_true",
                   help="분류 결과 레코드에 추출(정제) 텍스트를 함께 저장(기본 미포함=프라이버시)")
    p.add_argument("--with-pii", dest="with_pii", action="store_true",
                   help="[프라이버시 주의] 검출된 원문 PII 값(주민번호·카드번호 등 실제 값)을 "
                        "결과의 pii 필드에 함께 저장(기본 미포함). 결과 파일 취급에 주의")
    p.add_argument("--auto-propagate", dest="auto_propagate", action="store_true",
                   help="분류 직후 보류 문서를 seed(--seeds 또는 내부)로 전파까지 수행(보류 임베딩 자동 활성화)")
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
            return item
        # (src, label) 형태는 origin 없음으로 채운다.
        return (item[0], item[1], None)
    return (item, item, None)


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
#     seed 파일(--seeds 또는 exe 옆 cso_seed.jsonl)이 '있으면' auto_prop 을 자동으로 켜고,
#     보류 문서 벡터가 필요하므로 임베딩(needed)도 자동 활성화한다. seed 파일이 없으면
#     전파 없이 규칙만으로 끝낸다(레코드 스트리밍). --auto-propagate 로 명시할 수도 있다.
#     전파는 '외부 seed(cso_seed.jsonl) + 내부 seed_eligible'을 함께 기준으로 쓴다.
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
    from .classify import load_rules, build_record, now_iso, default_seed_path

    log = logsetup.get_logger("csoclassify.cli")
    # 규칙셋은 파일마다 다시 읽지 않도록 한 번만 로드해 재사용한다.
    # 규칙셋은 외장 파일(exe 옆)이라 누락이 흔하다 → 원시 스택 대신 안내 후 정상 종료.
    try:
        ruleset = load_rules(args.rules)
    except FileNotFoundError as e:
        print(f"[csoclassify] {e}", file=sys.stderr)
        log.error("규칙셋 로드 실패 :: %s", e)
        return config.EXIT_ARG_ERROR
    # --hybridparse 여부에 따라 사이냅 단독 또는 하이브리드 추출기를 만든다(분류는 추출이 클라이언트측).
    extractor = build_extractor(hybrid=getattr(args, "hybridparse", False))

    with_text = getattr(args, "with_text", False)         # 결과 레코드에 추출 텍스트 포함
    auto_prop = getattr(args, "auto_propagate", False)    # 분류 직후 보류 문서를 seed 로 전파
    rule_only = getattr(args, "rule_only", False)         # 규칙만(임베딩·전파 없음)
    vector_only = getattr(args, "vector_only", False)     # 규칙 없이 벡터-seed 비교만
    rules_enabled = not vector_only                       # 벡터-only 면 규칙 신호를 끈다

    # 전파 비교 기준 seed 경로: --seeds 우선, 없으면 exe 옆 cso_seed.jsonl(기본 규약).
    seeds_path = args.seeds or default_seed_path()

    # 분류 방식 결정 — 배타 3분기: 벡터만 / 규칙만 / 기본(규칙+자동전파).
    if vector_only:
        # 규칙 미사용: 전량 임베딩 후 seed 와 비교해 등급을 정한다 → 비교 기준 seed 파일 필수.
        if not seeds_path or not os.path.isfile(seeds_path):
            print("[csoclassify] --vector-only 는 비교 기준 seed 파일이 필요합니다: "
                  "--seeds <cso_seed.jsonl>(또는 exe 옆 cso_seed.jsonl)", file=sys.stderr)
            return config.EXIT_ARG_ERROR
        embed_mode = "all"      # 모든 문서를 임베딩해야 seed 와 비교 가능
        auto_prop = True        # 전파(=벡터 비교)로 등급을 정하므로 항상 켠다
    elif rule_only:
        # 규칙만: 임베딩·전파를 하지 않는다(가장 빠름). seed 가 옆에 있어도 무시(명시적 차단).
        embed_mode = "none"
        auto_prop = False
    else:
        # 기본: 규칙 분류 + '보류 문서 자동 전파'. seed 파일(exe 옆 cso_seed.jsonl 또는 --seeds)이
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
        # 전파(auto_prop)는 보류 문서 벡터가 있어야 구제 가능 → 꺼져 있으면 needed 로 올린다.
        if auto_prop and embed_mode == "none":
            embed_mode = "needed"

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
        log.info("결과 %s", json.dumps(_loggable_record(rec), ensure_ascii=False, separators=(",", ":")))

    # 진행표시를 켰으면 총 개수를 먼저 알린다(UI 가 0/total 로 바를 초기화).
    total = len(files)
    show_progress = getattr(args, "progress", False)
    if show_progress:
        print(f"[progress] 0/{total} ", file=sys.stderr, flush=True)

    do_timing = getattr(args, "timing", True)   # 결과 레코드에 elapsed_ms 를 넣을지(--no-timing 이면 끔)
    totals = []       # 파일별 total ms(배치 요약용)
    records = []      # auto_prop 일 때만 모아 뒀다가 전파 후 한꺼번에 출력

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
                               rules_enabled=rules_enabled)

        # --hash/--simple 이면 문서 '내용'의 해시를 레코드에 싣는다(읽기 실경로 src 기준).
        if need_hash:
            rec["hash"] = _file_hash(src)

        # 2) 이 문서에 벡터가 필요한지 판단 → 필요할 때만 임베딩(느린 단계 절약).
        #    all=무조건 / needed=보류(grade=None)이거나 seed_eligible(내부 seed 후보)일 때만.
        need_vec = embed_mode == "all" or (
            embed_mode == "needed" and (rec["grade"] is None or rec.get("seed_eligible")))
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

        # auto_prop 면 전파 후 출력하려고 모아 두고, 아니면 바로 출력.
        if auto_prop:
            records.append(rec)
            rec_origins.append(origin)   # 압축 집계용 origin 을 레코드와 나란히 보관
        else:
            _emit(rec)
            # 스트리밍 경로는 지금 등급이 최종이라 바로 압축별로 모은다.
            if origin is not None:
                arch_members.setdefault(origin, []).append(rec["grade"])

        # 한 파일 처리 완료 → 진행수 갱신(UI 진행바/경과시간용).
        if show_progress:
            print(f"[progress] {i}/{total} {path}", file=sys.stderr, flush=True)

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
        # 전파로 등급이 바뀌었을 수 있으니 분포를 다시 집계한 뒤 출력.
        counts = {"C": 0, "S": 0, "O": 0, "none": 0}
        for rec, origin in zip(records, rec_origins):
            counts[rec.get("grade") or "none"] = counts.get(rec.get("grade") or "none", 0) + 1
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
#
# -in: args = argparse 결과(propagate=입력파일, out/fmt/failsafe)
#
# -out: code = 종료코드(성공 0, 입력파일 문제면 EXIT_ARG_ERROR)
# -out: error = 없음(개별 파싱 오류는 건너뛰고 로그)
#------------------------------------------------------------------
def run_propagate(args):
    import json

    from .classify.engine import propagate_records
    from .classify.propagate import SeedIndex

    log = logsetup.get_logger("csoclassify.cli")

    # 1차 레코드(jsonl) 로드.
    if not os.path.isfile(args.propagate):
        print(f"[csoclassify] 전파 입력 파일이 없습니다: {args.propagate}", file=sys.stderr)
        return config.EXIT_ARG_ERROR

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
            print(f"[csoclassify] seed 파일이 없습니다: {args.seeds}", file=sys.stderr)
            return config.EXIT_ARG_ERROR
        seed_index = SeedIndex.from_seed_file(args.seeds)
        print(f"[csoclassify] 외부 seed {seed_index.size}건 로드: {args.seeds}", file=sys.stderr)

    # 전파 + 재융합(상향 전용).
    records, stats = propagate_records(recs, seed_index=seed_index, failsafe=args.failsafe)

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
                     json.dumps(_loggable_record(rec), ensure_ascii=False, separators=(",", ":")))
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
# 메인 (핵심)
#=> 인자를 파싱하고 모드를 분기해 실행한다. 파일 대상이 없으면 사용법을 알린다.
#    1) 데몬 제어 명령이면 그쪽 처리
#    2) 모델 별칭 검증
#    3) 대상 파일 수집 → text-only/일반 처리
#
# -in: argv = 명령행 인자 리스트(None 이면 sys.argv 사용)
#
# -out: code = 프로세스 종료코드
# -out: error = 없음(내부에서 예외를 코드로 환원)
#------------------------------------------------------------------
def main(argv=None):
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
    logsetup.setup_logging(args._log_path, args.verbose)
    log = logsetup.get_logger("csoclassify.cli")
    # 어떤 실행이었는지 남겨 재현/추적을 돕는다: (1) 실제 커맨드라인 풀경로, (2) 파싱된 argv.
    log.info("실행 cmd=%s", _full_command_line())
    log.info("실행 argv=%s", argv if argv is not None else sys.argv[1:])

    # (1) 데몬 제어 명령 우선 처리.
    dc = handle_daemon_commands(args)
    if dc is not None:
        return dc

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
    files = collect_files(args)
    if not files:
        print("[csoclassify] 처리할 파일이 없습니다. --file <경로> 또는 --dir <폴더> 를 지정하세요.",
              file=sys.stderr)
        return config.EXIT_ARG_ERROR

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
        if args.text_only:
            return run_text_only(jobs, opts, out_fp)
        if args.embed:
            return run_embed(jobs, args, opts, out_fp)
        return run_classify(jobs, args, out_fp)
    finally:
        if out_fp is not None:
            out_fp.close()
        # 압축 확장에 쓴 임시폴더(내부 원문 포함)를 통째로 정리한다.
        shutil.rmtree(arch_tmp, ignore_errors=True)
