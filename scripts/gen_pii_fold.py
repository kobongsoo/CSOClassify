# -*- coding: utf-8 -*-
#------------------------------------------------------------------
# ko-pii 문자 정규화표 → Rust 소스 생성기
#=> ko-pii 는 검출 전에 글자를 '펴서' 회피를 막는다. 전각 １ 을 1 로, 아랍 숫자
#   ٠ 을 0 으로, 워드가 바꿔 놓은 – 을 - 로, 안 보이는 문자는 아예 지운다.
#   Rust 판은 ko-pii 를 안 쓰고 옮겨 적은 사본이라 이 표도 직접 들고 있어야 한다.
#
#   왜 ko-pii 의 _CHAR_FOLD 를 그대로 베끼지 않나:
#     · 그 표는 NFKC '뒤에' 적용된다. 그래서 ① 처럼 NFKC 가 먼저 1 로 바꿔 버리는
#       항목은 표에 적혀 있어도 실행되지 않는다(죽은 항목).
#     · 반대로 표에 없는데 NFKC 때문에 바뀌는 글자(전각 Ａ→A)도 있다.
#     · ㆍ(U+318D)는 표에 '-' 로 적혀 있지만 실제로는 NFKC 가 먼저 ᆞ(U+119E)로 바꾼다.
#   그래서 표를 읽지 않고, **글자를 하나씩 ko-pii 에 넣어 '실제로 무엇이 되는지'**
#   물어서 뽑는다. 손으로 옮겨 적을 일이 없으니 오타가 원천적으로 안 생긴다.
#
# 쓰는 법:
#   python scripts/gen_pii_fold.py            # Rust/src/pii_fold.rs 새로 만들기
#   python scripts/gen_pii_fold.py --check    # 최신인지 확인만(다르면 종료코드 1)
#------------------------------------------------------------------
"""ko-pii 에 글자를 하나씩 물어 Rust 용 정규화표(pii_fold.rs)를 만든다."""

import argparse
import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

try:
    import ko_pii
    from ko_pii.core.unicode_norm import normalize_unicode
except ImportError as _e:      # pragma: no cover - 미설치 환경에서만
    print("[오류] ko-pii 를 못 불러왔다: %s\n"
          "       pip install -r requirements.txt 후 다시 실행하라." % _e, file=sys.stderr)
    raise SystemExit(2)

# 훑을 코드포인트 범위. ko-pii 표의 최대값이 U+1FBF9 라 U+2FFFF 까지면 넉넉하다.
SCAN_END = 0x30000
# 탐침을 감쌀 글자. 숫자로 감싸면 안 된다 — ko-pii 가 '숫자열 안의 흉내 글자'를
# 먼저 펴 버려서(O→0) 그 효과가 이 표에 섞여 들어온다. 그건 Rust 쪽에서 별도
# 함수(fold_digit_homoglyphs)가 이미 담당하므로 여기서는 격리해야 한다.
LEFT, RIGHT = "x", "y"


#------------------------------------------------------------------
# 글자 하나가 ko-pii 정규화를 지나면 무엇이 되는지 물어본다
#=> 앞뒤에 표식을 붙여 통째로 정규화한 뒤, 표식 사이에 무엇이 남았는지 본다.
#   표식이 살아남지 못한 결과(글자가 앞뒤와 엉킨 경우)는 판단이 어려우니 버린다.
#
# -in: ch = 검사할 글자
#
# -out: None       = 아무 변화 없음(표에 넣을 필요 없음)
# -out: ""         = 이 글자는 지워진다(안 보이는 문자)
# -out: "<문자열>" = 이 글자가 그 문자열로 바뀐다(1글자일 수도, 여러 글자일 수도)
# -out: error = 예외 없음(판단 불가면 None 으로 본다)
#------------------------------------------------------------------
def probe(ch):
    try:
        norm, _ = normalize_unicode(LEFT + ch + RIGHT)
    except Exception:
        # 정규화가 못 다루는 글자는 건드리지 않는 쪽이 안전하다.
        return None
    if not (norm.startswith(LEFT) and norm.endswith(RIGHT)):
        return None
    mid = norm[len(LEFT):len(norm) - len(RIGHT)]
    return None if mid == ch else mid


#------------------------------------------------------------------
# 전 범위를 훑어 '바뀌는 글자'만 세 갈래로 모은다
#=> 1) 지워지는 글자(안 보이는 문자) 2) 한 글자로 바뀌는 것 3) 여러 글자로
#   늘어나는 것(㈜ → (주) 처럼). 세 갈래를 Rust 가 각각 다르게 처리해야 한다.
#
# -in: 없음
#
# -out: (remove, one, many) = (지울 코드포인트 목록, {코드포인트: 글자}, {코드포인트: 문자열})
# -out: error = 예외 없음
#------------------------------------------------------------------
def collect():
    remove, one, many = [], {}, {}
    for cp in range(0, SCAN_END):
        # 서로게이트는 단독으로 존재할 수 없는 자리라 건너뛴다.
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        got = probe(ch)
        if got is None:
            continue
        if got == "":
            remove.append(cp)
        elif len(got) == 1:
            one[cp] = got
        else:
            many[cp] = got
    return remove, one, many


#------------------------------------------------------------------
# 코드포인트 상위 바이트별 '표에 항목이 있나' 비트맵을 만든다
#=> 표는 U+0000~U+2FFFF 에 드문드문 흩어져 있다. 글자마다 이진탐색을 돌리면
#   한글 본문에서 헛품이 크다. 상위 8비트(페이지)로 먼저 O(1) 로 걸러내면
#   한글·ASCII 는 탐색을 아예 안 탄다(실측: 표를 811개로 늘려도 현행보다 빠름).
#
# -in: keys = 표에 든 코드포인트 전부
#
# -out: pages = [bool] 512칸(= U+0000~U+2FFFF 를 256개씩 나눈 페이지)
# -out: error = 예외 없음
#------------------------------------------------------------------
def page_bitmap(keys):
    pages = [False] * (SCAN_END >> 8)
    for cp in keys:
        pages[cp >> 8] = True
    return pages


#------------------------------------------------------------------
# '덧붙는 표시' 글자의 코드포인트 범위를 모은다
#=> 결합표시(´ ˆ 같은 덧쓰기 기호)와 조합용 한글 자모는 표로 못 다룬다. 같은
#   글자라도 앞에 무엇이 오느냐로 처리가 갈리기 때문이다 — ko-pii 는 숫자 뒤에
#   붙은 표시는 지우고(9̈51006 → 951006, 회피 차단), 글자 뒤는 살린다(é 보존).
#   그래서 '어떤 글자가 표시인가'만 범위로 넘겨주고 판단은 Rust 가 한다.
#
# -in: 없음
#
# -out: ranges = [(시작, 끝)] 오름차순 범위 목록(연속 구간을 합쳐 짧게 만든다)
# -out: error = 예외 없음
#------------------------------------------------------------------
def mark_ranges():
    import unicodedata
    def is_mark(cp):
        ch = chr(cp)
        if unicodedata.combining(ch):
            return True
        # 조합용 한글 자모(ko-pii _is_conjoining_jamo 와 같은 범위).
        return (0x1100 <= cp <= 0x11FF or 0xA960 <= cp <= 0xA97F
                or 0xD7B0 <= cp <= 0xD7FF)

    out, start = [], None
    for cp in range(0, SCAN_END):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        if is_mark(cp):
            if start is None:
                start = cp
        elif start is not None:
            out.append((start, cp - 1))
            start = None
    if start is not None:
        out.append((start, SCAN_END - 1))
    return out


#------------------------------------------------------------------
# '기준 글자 + 덧붙는 표시 → 한 글자' 합성표를 모은다
#=> ko-pii 는 덩어리를 통째로 NFKC 하므로 "c"+"◌́" 가 "ć" 한 글자로 합쳐진다.
#   표만 있고 이 합성이 없으면 우리 쪽에는 두 글자로 남아, 이메일 도메인 같은
#   곳에서 "…mail.c" 까지만 잡는 오탐이 난다(실측된 갈라짐).
#   유니코드가 정한 정준 합성(canonical composition)을 그대로 뽑는다.
#
# -in: 없음
#
# -out: pairs = {(기준코드, 표시코드): 합쳐진코드}
# -out: error = 예외 없음
#------------------------------------------------------------------
def compose_pairs():
    import unicodedata
    out = {}
    for cp in range(0, SCAN_END):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        d = unicodedata.decomposition(ch)
        # 호환분해(<font> 등)는 정준 합성이 아니므로 건너뛴다.
        if not d or d.startswith("<"):
            continue
        parts = d.split()
        if len(parts) != 2:
            continue
        a, b = int(parts[0], 16), int(parts[1], 16)
        # 합성 제외(composition exclusion)까지 반영하려면 실제로 NFC 를 돌려 확인한다.
        if unicodedata.normalize("NFC", chr(a) + chr(b)) == ch:
            out[(a, b)] = cp
    return out


#------------------------------------------------------------------
# Rust 소스 문자열을 만든다
#=> 표 세 개(지움/1:1/1:N)와 페이지 비트맵을 정적 배열로 낸다. 1:1 과 1:N 은
#   코드포인트 오름차순이라 Rust 쪽에서 이진탐색이 가능하다.
#
# -in: remove = 지울 코드포인트 목록
# -in: one    = {코드포인트: 바뀔 글자}
# -in: many   = {코드포인트: 바뀔 문자열}
#
# -out: src = pii_fold.rs 에 쓸 전체 소스 문자열
# -out: error = 예외 없음
#------------------------------------------------------------------
def render(remove, one, many):
    def esc(s):
        return "".join("\\u{%X}" % ord(c) for c in s)

    keys = sorted(set(remove) | set(one) | set(many))
    pages = page_bitmap(keys)

    L = []
    L.append("//! ko-pii 문자 정규화표 — **자동 생성 파일이니 손으로 고치지 말 것.**\n")
    L.append("//!\n")
    L.append("//! scripts/gen_pii_fold.py 가 ko-pii 에 글자를 하나씩 넣어 '실제로 무엇이 되는지'\n")
    L.append("//! 물어서 뽑았다. ko-pii 의 _CHAR_FOLD 를 베끼지 않은 이유는 그 표가 NFKC 뒤에\n")
    L.append("//! 적용돼 실제 효과가 표와 다른 항목이 있기 때문이다(자세한 사정은 생성기 헤더).\n")
    L.append("//!\n")
    L.append("//! 갱신: ko-pii 버전을 올렸다면 `python scripts/gen_pii_fold.py` 로 다시 뽑는다.\n")
    L.append("//! `--check` 로 최신인지 확인할 수 있고, 어긋나면 골든 테스트가 먼저 깨진다.\n")
    L.append("//!\n")
    L.append("//! 생성 기준: ko-pii %s / 훑은 범위 U+0000~U+%X\n" % (
        getattr(ko_pii, "__version__", "unknown"), SCAN_END - 1))
    L.append("//! 항목 수: 지움 %d · 1:1 %d · 1:N %d · 표시범위 %d\n\n"
             % (len(remove), len(one), len(many), len(mark_ranges())))

    L.append("/// 이 표가 손댈 글자가 있는 페이지(코드포인트>>8)인가 — 이진탐색 전 O(1) 사전거름.\n")
    L.append("/// 한글 음절 페이지는 여기 없어서, 한국어 본문은 탐색을 아예 타지 않는다.\n")
    L.append("pub static PAGE_HAS: [bool; %d] = [\n" % len(pages))
    for i in range(0, len(pages), 16):
        L.append("    " + ", ".join("true" if b else "false" for b in pages[i:i + 16]) + ",\n")
    L.append("];\n\n")

    L.append("/// 지워지는 글자(제로폭·소프트하이픈 등 안 보이는 문자). 오름차순.\n")
    L.append("pub static REMOVE: &[u32] = &[\n")
    for i in range(0, len(remove), 8):
        L.append("    " + " ".join("0x%X," % cp for cp in sorted(remove)[i:i + 8]) + "\n")
    L.append("];\n\n")

    L.append("/// 한 글자로 바뀌는 것 — (코드포인트, 바뀔 글자). 오름차순(이진탐색 전제).\n")
    L.append("pub static FOLD_ONE: &[(u32, char)] = &[\n")
    for cp in sorted(one):
        L.append("    (0x%X, '%s'),\n" % (cp, esc(one[cp])))
    L.append("];\n\n")

    L.append("/// 여러 글자로 늘어나는 것 — (코드포인트, 바뀔 문자열). 오름차순.\n")
    L.append("pub static FOLD_MANY: &[(u32, &str)] = &[\n")
    for cp in sorted(many):
        L.append('    (0x%X, "%s"),\n' % (cp, esc(many[cp])))
    L.append("];\n\n")

    L.append("/// 덧붙는 표시 글자(결합표시 · 조합용 한글 자모)의 범위 — (시작, 끝) 둘 다 포함.\n")
    L.append("/// 앞선 글자가 낱말이 아니면(숫자 등) 지우고, 낱말이면 살린다 — 판단은 pii.rs 가 한다.\n")
    L.append("pub static MARK_RANGES: &[(u32, u32)] = &[\n")
    for a, b in mark_ranges():
        L.append("    (0x%X, 0x%X),\n" % (a, b))
    L.append("];\n\n")

    cp_pairs = compose_pairs()
    L.append("/// 기준 글자 + 덧붙는 표시 → 한 글자 (유니코드 정준 합성).\n")
    L.append("/// ((기준코드, 표시코드), 합쳐진코드) 를 (기준,표시) 오름차순으로 담는다.\n")
    L.append("pub static COMPOSE: &[((u32, u32), u32)] = &[\n")
    for (a, b) in sorted(cp_pairs):
        L.append("    ((0x%X, 0x%X), 0x%X),\n" % (a, b, cp_pairs[(a, b)]))
    L.append("];\n")
    return "".join(L)


#------------------------------------------------------------------
# 표를 뽑아 Rust 파일로 쓴다(또는 --check 로 최신인지만 본다)
#=> ko-pii 를 올린 뒤 이 파일 갱신을 잊으면, Rust 만 옛 규칙으로 글자를 펴게 된다.
#   --check 는 그 '잊음'을 잡으려고 있다.
#
# -in: argv = 명령행 인자(None 이면 sys.argv)
#
# -out: 종료코드 = 0 정상 / 1 --check 에서 차이 발견
# -out: error = 파일 쓰기 실패 시 OSError 전파
#------------------------------------------------------------------
def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.join(here, "..", "Rust", "src", "pii_fold.rs")

    ap = argparse.ArgumentParser(description="ko-pii 문자 정규화표 → Rust 소스 생성기")
    ap.add_argument("--out", default=os.path.normpath(default_out),
                    help="출력 경로(기본: Rust/src/pii_fold.rs)")
    ap.add_argument("--check", action="store_true",
                    help="덮어쓰지 않고 최신인지만 확인(다르면 종료코드 1)")
    args = ap.parse_args(argv)

    print("[진행] U+0000~U+%X 를 훑는 중... (수십 초 걸린다)" % (SCAN_END - 1))
    remove, one, many = collect()
    src = render(remove, one, many)

    if args.check:
        try:
            with open(args.out, "r", encoding="utf-8") as f:
                old = f.read()
        except OSError:
            print("[다름] 기존 표가 없다: %s" % args.out, file=sys.stderr)
            return 1
        if old != src:
            print("[다름] 표가 현재 ko-pii 결과와 다르다.\n"
                  "       python scripts/gen_pii_fold.py 로 갱신하라.", file=sys.stderr)
            return 1
        print("[확인] 표가 최신이다(지움 %d · 1:1 %d · 1:N %d)."
              % (len(remove), len(one), len(many)))
        return 0

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(src)
    print("[생성] %s\n       지움 %d · 1:1 %d · 1:N %d · ko-pii %s"
          % (args.out, len(remove), len(one), len(many),
             getattr(ko_pii, "__version__", "unknown")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
