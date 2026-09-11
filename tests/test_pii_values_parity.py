#------------------------------------------------------------------
# --with-pii 원문 값 목록 — 회귀 시험
#=> 2026-09-11 까지 파이썬 판의 `pii` 배열이 **자기 판정과 어긋나** 있었다.
#   레코드가 "휴대전화 7건"이라고 적어 놓고 전화번호 값을 9개 실었고,
#   히트가 하나도 없는 ACCOUNT 값을 하나 더 실었다.
#
#   [원인] 건수(_kopii_counts)와 원문 값(collect_pii)이 같은 스캔을 **따로**
#   구현하고 있었고, 교차라벨 겹침 해소가 건수 쪽에만 들어가 있었다. 그래서
#   사업자등록번호 "022-00-76422" 한 자리를 전화번호로도 함께 실었다.
#   지금은 둘 다 _kopii_scan 이 낸 한 목록에서 나온다.
#
#   [왜 1만 자를 넘겨야 하나] 그 갈래는 문서가 _KOPII_MAX_CHARS(기본 10,000자)를
#   넘을 때만 탄다. 짧은 픽스처로는 이 시험이 통과해도 아무것도 지켜 주지 못한다.
#
#   [무엇을 지키나]
#    1) 자기 일관성 — L1 히트 건수 == pii 배열의 그 라벨 건수
#    2) 겹침 없음   — 같은 자리를 두 라벨이 물지 않는다
#    3) 두 판 일치  — (라벨, 값) 목록이 같다
#    4) 좌표 검산    — start/end 가 '추출된 본문'을 문자 단위로 가리킨다
#    5) 두 판 완전   — 좌표와 나오는 차례까지 같다(.txt 픽스처 기준)
#       HTML 은 두 판의 추출 공백이 미세하게 달라 좌표가 어긋난다 —
#       추출기 쪽 별건이라 여기서 섞지 않는다.
#------------------------------------------------------------------

import json
import os
import re
import subprocess
import sys
from collections import Counter

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS_EXE = os.path.join(ROOT, "Rust", "target", "release", "MpowerClassify-rs.exe")
POLICY = os.path.join(ROOT, "resources", "policy", "cso_rule.yaml")

# 사업자등록번호이면서 전화번호 모양이기도 한 값(국세청 체크섬 통과).
# 겹침 해소가 없으면 이 한 자리가 BUSINESS_REG 와 PHONE 으로 두 번 실린다.
#
# [하이픈이 없어야 한다] "022-00-76422" 처럼 구분자를 넣으면 사업자등록번호로만
# 읽혀 겹침이 일어나지 않는다. 실제 사고도 붙여 쓴 "0220076422" 에서 났다.
# (고치기 전 코드로 돌리면 이 한 자리가 BUSINESS_REG·PHONE 둘 다로 나오는 것을
#  확인했다 — 그래야 이 시험이 회귀를 잡는다.)
AMBIGUOUS = "0220076422"

# 1만 자를 넘겨 청킹 갈래를 타게 한다. 채우는 글자는 검출기에 안 걸리는 것으로.
_PAD = ("본 문서는 사내 절차를 설명하는 일반 안내문이며 별도의 첨부는 없습니다. " * 400)

# IPv4 를 품은 IPv6 표기. Rust 판이 뒤쪽 IPv4 만 잡아 값이 갈렸던 자리다
# (2026-09-11). 건수는 양쪽 다 1건이라 **값까지** 견줘야 회귀를 잡는다.
IPV6_MAPPED = "::ffff:10.1.100.25"

FIXTURE = (
    "거래처 안내\n"
    "사업자등록번호 %s 로 등록되어 있습니다.\n"
    "서버 주소는 %s 입니다.\n"
    "%s\n"
    "문의: 010-1234-5678\n" % (AMBIGUOUS, IPV6_MAPPED, _PAD)
)


#------------------------------------------------------------------
# cso_rule.yaml 에서 규칙 id → ko-pii 라벨 매핑을 읽는다
#=> 히트(규칙 id)와 pii 배열(ko-pii 라벨)을 견주려면 이 다리가 필요하다.
#   yaml 파서를 끌어오지 않는다 — 이 시험이 보려는 것은 두 줄뿐이다.
#
# -in: 없음(POLICY 경로 고정)
#
# -out: dict = {규칙 id: 라벨}
# -out: error = 없음
#------------------------------------------------------------------
def _label_map():
    out, cur = {}, None
    with open(POLICY, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"- id:\s*(\S+)", line)
            if m:
                cur = m.group(1)
            m = re.match(r"\s+label:\s*([A-Z_]+)", line)
            if m and cur:
                out[cur] = m.group(1)
    return out


#------------------------------------------------------------------
# 한 엔진을 --with-pii 로 돌려 첫 레코드를 돌려준다
#
# -in: engine  = "python" | "rust"
# -in: doc_dir = 분류할 폴더
#
# -out: dict = 첫 결과 레코드
# -out: error = 레코드가 없으면 AssertionError(표준오류를 함께 보여 준다)
#------------------------------------------------------------------
def _run(engine, doc_dir, textsave=None):
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8")
    extra = ["--textsave", str(textsave)] if textsave else []
    r = subprocess.run(
        cmd + ["--dir", str(doc_dir), "--rule-only", "--rules", POLICY,
               "--with-pii", "--format", "jsonl", "--nosummary"] + extra,
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    recs = [json.loads(l) for l in r.stdout.splitlines()
            if l.strip().startswith("{") and '"file"' in l]
    assert recs, "%s: 레코드가 없다 :: %s" % (engine, r.stderr[-400:])
    return recs[0]


#------------------------------------------------------------------
# 픽스처가 실제로 문제 갈래를 타는지 먼저 확인한다
#=> 이 시험이 통과해도 갈래를 안 타면 아무것도 지켜 주지 못한다.
#------------------------------------------------------------------
def test_픽스처가_청킹_갈래를_탄다():
    from csoclassify.classify import rules as R
    assert len(FIXTURE) > R._KOPII_MAX_CHARS, \
        "픽스처가 %d자뿐이라 청킹 갈래를 안 탄다(_KOPII_MAX_CHARS=%d)" % (
            len(FIXTURE), R._KOPII_MAX_CHARS)
    # 겹침이 실제로 일어나는 값인지도 본다 — 안 겹치면 회귀를 못 잡는다.
    assert AMBIGUOUS in FIXTURE


#------------------------------------------------------------------
# pii 배열이 그 레코드의 히트 건수와 맞는다 (자기 일관성)
#=> "전화 7건"이라고 적어 놓고 값 9개를 싣던 것이 이 검사에 걸린다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_pii_배열이_히트_건수와_맞는다(engine, tmp_path):
    (tmp_path / "거래처.txt").write_text(FIXTURE, encoding="utf-8")
    rec = _run(engine, tmp_path)
    lab = _label_map()

    hits = {h["id"]: h["count"]
            for h in (rec["why"]["security"]["signals"].get("rule") or {}).get("hits") or []
            if h.get("layer") == "L1"}
    arr = Counter(x["label"] for x in (rec.get("pii") or []))
    assert hits, (engine, "L1 히트가 없다 — 픽스처가 검출기에 안 걸렸다")

    for rid, n in sorted(hits.items()):
        assert arr.get(lab.get(rid), 0) == n, \
            "%s: %s(%s) 히트는 %d건인데 pii 배열에는 %d건" % (
                engine, rid, lab.get(rid), n, arr.get(lab.get(rid), 0))
    # 히트가 없는 라벨의 값이 실리는 일도 없어야 한다(ACCOUNT 가 그랬다).
    known = {lab.get(rid) for rid in hits}
    assert not (set(arr) - known), \
        "%s: 히트 없는 라벨이 pii 에 실렸다: %s" % (engine, sorted(set(arr) - known))


#------------------------------------------------------------------
# 같은 자리를 두 라벨이 물지 않는다
#=> 겹침 해소가 빠지면 사업자등록번호가 전화번호로도 실린다. 이 배열은
#   마스킹·비식별에 쓰라고 있는 값이라, 겹치면 엉뚱한 유형으로 지우게 된다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_pii_배열에_겹치는_자리가_없다(engine, tmp_path):
    (tmp_path / "거래처.txt").write_text(FIXTURE, encoding="utf-8")
    rec = _run(engine, tmp_path)
    items = sorted(rec.get("pii") or [], key=lambda x: (x["start"], x["end"]))
    assert items, (engine, "pii 가 비었다")
    for i, x in enumerate(items):
        for y in items[i + 1:]:
            if y["start"] >= x["end"]:
                break
            pytest.fail("%s: 같은 자리를 두 라벨이 물었다 — %s %d-%d ↔ %s %d-%d" % (
                engine, x["label"], x["start"], x["end"],
                y["label"], y["start"], y["end"]))


#------------------------------------------------------------------
# 두 판이 같은 (라벨, 값) 목록을 낸다
#=> start/end 는 견주지 않는다 — 두 판의 추출 텍스트 공백이 미세하게 달라
#   오프셋 기준이 서로 다르다(별개 사안). 무엇을 찾았는가는 같아야 한다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
def test_두_판이_같은_pii_값을_낸다(tmp_path):
    (tmp_path / "거래처.txt").write_text(FIXTURE, encoding="utf-8")
    py = sorted((x["label"], x["value"]) for x in (_run("python", tmp_path).get("pii") or []))
    rs = sorted((x["label"], x["value"]) for x in (_run("rust", tmp_path).get("pii") or []))
    assert py == rs, ("두 판의 pii 값이 다르다", py, rs)
    # IPv6 에 박힌 IPv4 는 **통째로** 잡혀야 한다 — 뒤쪽 IPv4 만 잡아도 건수는
    # 같아서, 값을 직접 확인하지 않으면 회귀를 놓친다.
    assert ("IP", IPV6_MAPPED) in py, ("IPv6 매핑 표기가 통째로 안 잡혔다", py)


#------------------------------------------------------------------
# start/end 가 '추출된 본문'을 문자 단위로 정확히 가리킨다
#=> 이 좌표는 원본 파일이 아니라 **추출·정제가 끝난 본문** 기준이다.
#   그 본문을 --textsave 로 받아 직접 잘라 본다 — 좌표가 맞다면 잘라낸 글자가
#   value 와 같아야 한다. 단위가 틀리면(바이트 vs 문자) 바로 어긋난다.
#
#   [왜 이게 필요했나] Rust 판은 바이트로 세고 있었다. 한글이 3바이트라
#   앞에 한글이 많을수록 벌어져, 같은 값이 파이썬 13 · Rust 31 이었다
#   (2026-09-11). 오류도 안 나고 받는 쪽이 **엉뚱한 자리를 지우게** 되는
#   종류라, 값이 아니라 좌표를 직접 검산한다.
#
#   [픽스처에 한글이 있어야 한다] 전부 ASCII 면 바이트와 문자가 같아
#   이 시험이 통과해도 아무것도 지켜 주지 못한다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_좌표가_추출본문을_문자단위로_가리킨다(engine, tmp_path):
    doc = tmp_path / "docs"
    doc.mkdir()
    (doc / "거래처.txt").write_text(FIXTURE, encoding="utf-8")
    save = tmp_path / "text"
    rec = _run(engine, doc, textsave=save)

    saved = sorted(save.glob("*.txt"))
    assert saved, "%s: --textsave 가 본문을 안 남겼다" % engine
    body = saved[0].read_text(encoding="utf-8")
    # 한글이 없으면 바이트=문자라 이 시험이 무의미해진다.
    assert not body.isascii(), "픽스처에 한글이 없어 단위 차이를 잡지 못한다"

    items = rec.get("pii") or []
    assert items, "%s: pii 가 비었다" % engine
    for x in items:
        assert body[x["start"]:x["end"]] == x["value"], (
            "%s: 좌표가 본문을 안 가리킨다 — %s %d-%d 기대=%r 실제=%r" % (
                engine, x["label"], x["start"], x["end"],
                x["value"], body[x["start"]:x["end"]]))


#------------------------------------------------------------------
# 두 판의 pii 배열이 좌표와 차례까지 똑같다
#=> 위 test_두_판이_같은_pii_값을_낸다 는 정렬해서 (라벨,값)만 본다.
#   여기서는 **원본 그대로** 견준다 — 좌표 단위도, 나오는 차례도 같아야
#   두 판의 결과 파일을 통째로 diff 할 수 있다.
#   (ko-pii 는 겹침 해소 뒤 문서 순서로 정렬해 돌려준다. Rust 는 그 정렬이
#    빠져 검출기 순서로 나갔다 — 2026-09-11 에 맞췄다.)
#   [.txt 를 쓰는 이유] HTML 은 두 판의 추출 공백이 미세하게 달라 좌표가
#   어긋난다. 그건 추출기 쪽 별건이라 여기서 섞지 않는다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
def test_두_판이_좌표와_차례까지_같다(tmp_path):
    (tmp_path / "거래처.txt").write_text(FIXTURE, encoding="utf-8")
    def rows(engine):
        return [(x["label"], x["value"], x["start"], x["end"])
                for x in (_run(engine, tmp_path).get("pii") or [])]
    py, rs = rows("python"), rows("rust")
    assert py == rs, ("두 판의 pii 가 좌표·차례까지 같지 않다", py, rs)
    # 문서 순서로 나와야 한다(받는 쪽이 앞에서부터 훑어 마스킹한다).
    assert py == sorted(py, key=lambda r: (r[2], r[3])), ("문서 순서가 아니다", py)
