# -*- coding: utf-8 -*-
#------------------------------------------------------------------
# Rust PII 포팅 검증용 '정답표(골든 픽스처)' 생성기
#=> Rust 의 src/pii.rs 는 ko-pii 라이브러리를 쓰지 않고 알고리즘을 그대로 옮겨
#   적은 것이라, ko-pii 를 올리면 둘이 소리 없이 어긋날 수 있다. 그래서 '진짜
#   ko-pii 가 낸 답'을 파일로 굳혀 두고, Rust 는 cargo test 로 그 파일과 자기
#   결과를 맞춰 본다. 이 스크립트가 그 파일(Rust/tests/pii_golden.json)을 만든다.
#
#   왜 exe 대조 대신 정답표인가: exe 대조는 pdfium·사이냅·ONNX·정책 yaml 까지
#   다 세워야 한 번 돌릴 수 있다. 정답표는 '텍스트 → 라벨별 건수'만 담으므로
#   문서 추출이 아예 끼어들지 않고, Rust 쪽은 cargo test 한 방이면 끝난다.
#
# 쓰는 법:
#   python scripts/gen_pii_golden.py                            # 기본 코퍼스로 생성
#   python scripts/gen_pii_golden.py --corpus-dir D:\some\txt   # 실문서 추가
#   python scripts/gen_pii_golden.py --check                    # 덮어쓰지 않고 차이만 확인
#------------------------------------------------------------------
"""ko-pii 로 PII 정답표를 만들어 Rust/tests/pii_golden.json 에 쓴다."""

import argparse
import io
import json
import os
import sys

# 콘솔이 cp949 여도 한글이 깨지지 않게 표준출력을 UTF-8 로 바꾼다.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ko-pii 는 이 스크립트의 '유일한 정답 근거'다. 없으면 정답표를 만들 수 없으니
# 애매하게 넘어가지 말고 설치 안내와 함께 바로 멈춘다.
try:
    import ko_pii
    from ko_pii import detect_all
    from ko_pii.core.unicode_norm import normalize_unicode
except ImportError as _e:      # pragma: no cover - 미설치 환경에서만
    print("[오류] ko-pii 를 못 불러왔다: %s\n"
          "       pip install ko-pii==1.15.2 후 다시 실행하라." % _e, file=sys.stderr)
    raise SystemExit(2)


# Rust src/pii.rs 의 kept_dets() 가 실제로 구현한 20라벨. 이 목록이 곧 검증 범위다.
# (NAME/PERSON·BIRTH·HEALTH 등은 Rust 미포팅이고 cso_rule.yaml 도 안 쓰므로 제외.)
LABELS = [
    "RRN", "FRN", "CARD", "BUSINESS_REG", "CORP_REG", "PHONE", "EMAIL", "ACCOUNT",
    "PASSPORT", "IP", "URL", "VEHICLE", "NATIONALITY", "ADDRESS", "DRIVER_LICENSE",
    "MEDICAL_INSURANCE", "PRESCRIPTION_ID", "EDI_DRUG", "COURT_CASE", "PNU",
]

#------------------------------------------------------------------
# 검사 문장 목록(코퍼스)을 YAML 에서 읽는다
#=> 코퍼스는 '코드'가 아니라 '데이터'다. 사례를 넣고 빼는 일은 파이썬을 모르는
#   사람도 해야 하므로, 스크립트 안에 박아 두지 않고 밖의 YAML 로 뺐다
#   (이 프로젝트가 cso_rule.yaml·doc_rule.yaml 을 외장으로 두는 것과 같은 결).
#    1) Rust/tests/pii_corpus.yaml 을 읽는다 — 정답표와 짝이므로 같은 폴더에 둔다
#    2) name/text 두 칸만 쓴다(note 는 사람이 읽는 설명이라 검출에 안 쓴다)
#    3) 파일이 없거나 모양이 틀리면 '무엇을 고치라'까지 알리고 멈춘다
#
# -in: path = 코퍼스 YAML 경로
#
# -out: cases = [(이름, 본문)] 목록. 파일에 적힌 차례를 그대로 지킨다
#               (차례가 바뀌면 정답표도 바뀌어 쓸데없는 diff 가 난다)
# -out: error = 파일 없음/YAML 깨짐/필수 칸 누락이면 안내 후 SystemExit(2)
#------------------------------------------------------------------
def load_corpus(path):
    try:
        import yaml
    except ImportError:      # pragma: no cover - PyYAML 미설치 환경에서만
        print("[오류] PyYAML 이 없다: pip install -r requirements.txt", file=sys.stderr)
        raise SystemExit(2)
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except OSError as e:
        print("[오류] 코퍼스를 못 읽었다 %s: %s\n"
              "       이 파일이 검사 문장 목록이다. 지웠다면 git 에서 되살려라." % (path, e),
              file=sys.stderr)
        raise SystemExit(2)
    except Exception as e:   # yaml 문법 오류
        print("[오류] 코퍼스 YAML 이 깨졌다 %s: %s" % (path, e), file=sys.stderr)
        raise SystemExit(2)

    cases = (doc or {}).get("cases")
    if not isinstance(cases, list) or not cases:
        print("[오류] 코퍼스에 cases 목록이 없다: %s" % path, file=sys.stderr)
        raise SystemExit(2)

    out, seen = [], set()
    for i, c in enumerate(cases, 1):
        if not isinstance(c, dict) or "name" not in c or "text" not in c:
            print("[오류] %d 번째 사례에 name/text 가 없다: %r" % (i, c), file=sys.stderr)
            raise SystemExit(2)
        name = str(c["name"])
        # 이름이 겹치면 실패 메시지에서 어느 사례인지 못 가린다.
        if name in seen:
            print("[오류] 사례 이름이 겹친다: %s" % name, file=sys.stderr)
            raise SystemExit(2)
        seen.add(name)
        # text 가 비어도(빈 문자열 사례) 정상이므로 존재 여부만 본다.
        out.append((name, "" if c["text"] is None else str(c["text"])))
    return out


# 코퍼스 기본 위치 — 정답표와 짝이라 같은 폴더에 둔다.
DEFAULT_CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "Rust", "tests", "pii_corpus.yaml")


#------------------------------------------------------------------
# 텍스트 한 건을 ko-pii 로 검사해 라벨별 건수·값을 뽑는다
#=> README 가 정한 검증 기준(detect_all(text, include=<20라벨>))을 그대로 쓴다.
#   운영 코드(rules.py)의 부분검출·청킹 최적화는 일부러 쓰지 않는다 — 그쪽은
#   '빠르게 만든 우회로'이고, 정답은 어디까지나 원본 엔진이기 때문이다.
#    1) detect_all 로 20라벨만 켜고 검출한다
#    2) 라벨별로 건수를 세고 원문 값도 함께 모은다(어긋났을 때 원인 보라고)
#    3) 값 순서는 엔진 구현 세부라 정렬해 '무엇을 잡았나'만 남긴다
#
# -in: text = 검사할 본문 문자열
#
# -out: (counts, values) = ({라벨: 건수}, {라벨: [검출값...]}) — 0건 라벨은 아예 뺀다
# -out: error = 예외 없음(ko-pii 내부 예외는 그대로 전파)
#------------------------------------------------------------------
def scan(text):
    results = detect_all(text, include=LABELS)
    counts = {}
    values = {}
    for r in results:
        # Rust pii_counts() 도 0건 라벨은 map 에 안 넣으므로 여기서도 넣지 않는다.
        counts[r.label] = counts.get(r.label, 0) + 1
        values.setdefault(r.label, []).append(r.text)
    for lab in values:
        values[lab].sort()
    return counts, values


#------------------------------------------------------------------
# 코퍼스 한 건을 정답표 레코드로 만든다
#=> 건수·값에 더해 '값까지 비교해도 되는가'를 함께 기록한다.
#   ko-pii 는 정규화한 뒤 찾은 값을 '원본 좌표'로 되돌려 주고(remap_to_source),
#   Rust pii_records 는 '정규화 좌표' 값을 준다. 전각·제로폭이 있어 정규화가
#   실제로 글자를 바꾼 문서는 두 값이 다를 수밖에 없으므로, 그런 건은 건수만
#   비교하도록 표시해 둔다(억지로 맞추면 진짜 버그를 가리게 된다).
#
#   판정에 needs_normalization() 을 쓰면 안 된다 — 그건 '검사해 볼 값어치가
#   있나'만 보는 싼 사전필터라 한글만 있어도 True 다(그러면 전 사례가 값 비교
#   대상에서 빠져 테스트가 껍데기가 된다). 정규화를 실제로 돌려 보고 글자가
#   '바뀌었는가'로 판정한다.
#
# -in: name = 사례 이름(테스트 실패 메시지에 그대로 찍힌다)
# -in: text = 사례 본문
#
# -out: rec = {"name","text","values_comparable","counts","values"} 딕셔너리
# -out: error = 예외 없음
#------------------------------------------------------------------
def make_case(name, text):
    counts, values = scan(text)
    # 정규화가 글자를 바꾸지 않는 문서만 값 비교 대상으로 삼는다.
    # normalize_unicode 는 (정규화문자열, 매핑) 튜플을 주므로 앞칸만 쓴다.
    normed = normalize_unicode(text)
    if isinstance(normed, tuple):
        normed = normed[0]
    comparable = (normed == text)
    return {
        "name": name,
        "text": text,
        "values_comparable": comparable,
        "counts": counts,
        "values": values,
    }


#------------------------------------------------------------------
# 폴더에서 실문서 텍스트를 추가로 읽어 코퍼스에 붙인다
#=> 기본 코퍼스는 라벨을 '한 번씩' 밟는 최소 표본이라, 실제 문서에서만 나오는
#   조합(표·머리말·반복 서식)은 못 본다. 현장 텍스트를 넣고 싶을 때 쓴다.
#   추출기 차이가 끼어들면 안 되므로 .txt/.md 만 받는다(pdf·hwp 는 대상 아님).
#
# -in: dirpath = 훑을 폴더 경로. None 이면 아무 것도 안 한다
# -in: limit   = 최대 파일 수(기본 200) — 정답표가 너무 커지지 않게 막는다
#
# -out: cases = [(이름, 본문)] 목록. 폴더가 없거나 비면 빈 목록
# -out: error = 읽기 실패한 파일은 경고만 찍고 건너뛴다(전체를 멈추지 않는다)
#------------------------------------------------------------------
def load_corpus_dir(dirpath, limit=200):
    if not dirpath:
        return []
    if not os.path.isdir(dirpath):
        print("[경고] 코퍼스 폴더가 없다: %s" % dirpath, file=sys.stderr)
        return []
    out = []
    for root, _dirs, files in os.walk(dirpath):
        for fn in sorted(files):
            if not fn.lower().endswith((".txt", ".md")):
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError as e:
                print("[경고] 못 읽어 건너뛴다 %s: %s" % (path, e), file=sys.stderr)
                continue
            # 빈 파일은 검증 가치가 없다.
            if not text.strip():
                continue
            rel = os.path.relpath(path, dirpath).replace(os.sep, "_")
            out.append(("file_" + rel, text))
            if len(out) >= limit:
                print("[알림] 파일 상한 %d 에 도달해 그만 읽는다." % limit, file=sys.stderr)
                return out
    return out


#------------------------------------------------------------------
# 20라벨이 정답표에서 한 번이라도 검출되는지 확인한다
#=> 라벨 하나가 통째로 0건이면 그 라벨은 '검증되지 않은 채' 통과한다.
#   Rust 가 그 라벨을 아예 안 잡아도 테스트가 초록불이 되는 상황을 막으려고,
#   생성 시점에 빈 라벨을 경고로 알린다.
#
# -in: cases = make_case() 로 만든 레코드 목록
#
# -out: missing = 한 번도 안 잡힌 라벨 목록(정상이면 빈 목록)
# -out: error = 예외 없음
#------------------------------------------------------------------
def find_uncovered_labels(cases):
    seen = set()
    for c in cases:
        seen.update(c["counts"].keys())
    return [lab for lab in LABELS if lab not in seen]


#------------------------------------------------------------------
# 정답표를 만들어 파일로 쓴다(또는 --check 로 차이만 본다)
#=> 1) 기본 코퍼스 + (옵션)실문서 폴더로 사례를 모은다
#    2) 사례마다 ko-pii 로 정답을 뽑는다
#    3) 20라벨 커버리지를 점검해 빈 라벨은 경고한다
#    4) --check 면 기존 파일과 비교만 하고, 아니면 덮어쓴다
#
# -in: argv = 명령행 인자 목록(None 이면 sys.argv 사용)
#
# -out: 종료코드 = 0 정상 / 1 --check 에서 차이 발견
# -out: error = 파일 쓰기 실패 시 OSError 전파 (ko-pii 미설치는 import 시점에 종료코드 2)
#------------------------------------------------------------------
def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.join(here, "..", "Rust", "tests", "pii_golden.json")

    ap = argparse.ArgumentParser(description="Rust PII 포팅 검증용 정답표 생성기")
    ap.add_argument("--out", default=os.path.normpath(default_out),
                    help="정답표 출력 경로(기본: Rust/tests/pii_golden.json)")
    ap.add_argument("--corpus", default=os.path.normpath(DEFAULT_CORPUS),
                    help="검사 문장 목록 YAML(기본: Rust/tests/pii_corpus.yaml)")
    ap.add_argument("--corpus-dir", default=None,
                    help="실문서 텍스트(.txt/.md) 폴더를 코퍼스에 추가")
    ap.add_argument("--check", action="store_true",
                    help="덮어쓰지 않고 기존 정답표와 다른지만 확인(다르면 종료코드 1)")
    args = ap.parse_args(argv)

    pairs = load_corpus(args.corpus) + load_corpus_dir(args.corpus_dir)
    cases = [make_case(name, text) for name, text in pairs]

    # 빈 라벨은 '조용한 미검증'이라 반드시 사람 눈에 띄게 알린다.
    missing = find_uncovered_labels(cases)
    if missing:
        print("[경고] 정답표에서 한 번도 안 잡힌 라벨: %s\n"
              "       해당 라벨은 Rust 가 0 을 반환해도 테스트가 통과한다."
              % ", ".join(missing), file=sys.stderr)

    doc = {
        "note": ("ko-pii detect_all(include=<20라벨>) 이 낸 정답표. Rust src/pii.rs 포팅이 "
                 "이것과 같은지 확인한다. 만든이: scripts/gen_pii_golden.py — 손으로 고치지 "
                 "말고 스크립트를 다시 돌려 갱신할 것."),
        "generator": "scripts/gen_pii_golden.py",
        "kopii_version": getattr(ko_pii, "__version__", "unknown"),
        "labels": LABELS,
        "cases": cases,
    }
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"

    if args.check:
        # 정답표가 최신인지 확인만 한다 — ko-pii 를 올린 뒤 갱신을 잊는 걸 잡는다.
        try:
            with open(args.out, "r", encoding="utf-8") as f:
                old = f.read()
        except OSError:
            print("[다름] 기존 정답표가 없다: %s" % args.out, file=sys.stderr)
            return 1
        if old != text:
            print("[다름] 정답표가 현재 ko-pii(%s) 결과와 다르다.\n"
                  "       python scripts/gen_pii_golden.py 로 갱신하고, Rust 테스트가 "
                  "깨지면 그 차이가 곧 포팅이 따라가야 할 변경분이다." % doc["kopii_version"],
                  file=sys.stderr)
            return 1
        print("[확인] 정답표가 최신이다(%d건)." % len(cases))
        return 0

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    total = sum(sum(c["counts"].values()) for c in cases)
    print("[생성] %s\n"
          "       사례 %d건 / 검출 %d건 / ko-pii %s"
          % (args.out, len(cases), total, doc["kopii_version"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
