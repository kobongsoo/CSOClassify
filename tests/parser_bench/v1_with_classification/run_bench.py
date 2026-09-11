# -*- coding: utf-8 -*-
"""자체 파서(Rust) vs 사이냅 문서필터(snf) 추출 성능 비교 실행기."""

import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\분류함")
SNF = Path(r"D:\Project\MpowerClassify\resources\synap\windows\snf_exe.exe")
RS = Path(r"D:\Project\MpowerClassify\Rust\dist-onedir\windows\MpowerClassify-rs.exe")
WORK = Path(__file__).resolve().parent
OUT = WORK / "raw"
OUT.mkdir(exist_ok=True)

# 확장자별로 몇 개까지 뽑아 볼지 — 너무 많으면 몇 시간씩 걸리므로 상한을 둔다
CAPS = {
    "pdf": 45, "hwp": 30, "hwpx": 10, "doc": 25, "docx": 25,
    "ppt": 20, "pptx": 25, "xls": 15, "xlsx": 20,
    "html": 20, "txt": 20, "csv": 15, "md": 15, "json": 10, "tsv": 10,
}
TIMEOUT = 180


#------------------------------------------------------------------
# 비교 대상 문서 고르기
#=> 'D:\분류함' 을 통째로 훑어 확장자별로 정해진 개수만 무작위로 뽑는다.
#    1) 재귀로 모든 파일을 모아 확장자별 통에 나눠 담는다
#    2) 통마다 섞은 뒤 CAPS 만큼 앞에서 자른다 (씨앗 고정 → 매번 같은 표본)
#
# -in: 없음
#
# -out: samples = [(확장자, 파일경로Path), ...] 목록
# -out: error = 없음 (읽을 수 없는 폴더는 조용히 건너뜀)
#------------------------------------------------------------------
def pick_samples():
    buckets = {}
    for dirpath, _dirnames, filenames in os.walk(ROOT):
        for fn in filenames:
            ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
            if ext in CAPS:
                buckets.setdefault(ext, []).append(Path(dirpath) / fn)
    rnd = random.Random(20260911)  # 표본을 재현 가능하게 고정
    out = []
    for ext, files in sorted(buckets.items()):
        rnd.shuffle(files)
        for f in files[: CAPS[ext]]:
            out.append((ext, f))
    return out


#------------------------------------------------------------------
# 본문을 비교하기 좋게 다듬기
#=> 공백·줄바꿈·페이지 마커처럼 파서마다 다를 수밖에 없는 군더더기를 없앤다.
#    두 파서의 '내용'만 놓고 견주기 위한 전처리다.
#
# -in: s = 원본 추출 문자열
#
# -out: 정규화된 문자열(공백 1칸으로 접힘, 앞뒤 공백 제거)
# -out: error = 없음
#------------------------------------------------------------------
def norm(s):
    s = s.replace("\x00", " ")
    # 사이냅이 넣는 페이지 구분 표식은 내용이 아니므로 제거
    s = re.sub(r"\[?page\s*\d+\]?", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


#------------------------------------------------------------------
# 비교용 토큰 뽑기
#=> 한글·영문·숫자 덩어리만 골라 '단어 집합'을 만든다.
#    줄 순서나 표 구조가 달라도 같은 낱말을 얼마나 건졌는지 볼 수 있다.
#
# -in: s = 정규화된 문자열
#
# -out: 토큰 집합(set)
# -out: error = 없음
#------------------------------------------------------------------
def toks(s):
    return set(re.findall(r"[0-9A-Za-z가-힣]+", s))


#------------------------------------------------------------------
# 사이냅으로 텍스트 뽑기
#=> snf_exe 를 파일 하나에 대해 돌리고 걸린 시간과 본문을 돌려준다.
#    1) -U8(UTF-8) 로 결과 파일을 만들게 한다
#    2) 벽시계 시간을 재고 결과 파일을 읽는다
#
# -in: src = 원본 문서 경로
# -in: dst = 결과 텍스트를 쓸 경로
#
# -out: (걸린초, 본문, 상태) — 상태는 ok/timeout/error/empty
# -out: error = 예외를 밖으로 던지지 않고 상태 문자열로 알린다
#------------------------------------------------------------------
def run_snf(src, dst):
    t0 = time.perf_counter()
    try:
        p = subprocess.run(
            [str(SNF), "-U8", str(src), str(dst)],
            capture_output=True, timeout=TIMEOUT,
        )
        el = time.perf_counter() - t0
    except subprocess.TimeoutExpired:
        return TIMEOUT, "", "timeout"
    txt = ""
    if dst.exists():
        txt = dst.read_bytes().decode("utf-8", "replace")
    if not txt.strip():
        return el, "", ("error" if p.returncode != 0 else "empty")
    return el, txt, "ok"


#------------------------------------------------------------------
# 자체 파서(Rust)로 텍스트 뽑기 + 등급 받기
#=> MpowerClassify-rs 를 규칙만 모드로 돌려 본문을 파일로 남기고 결과 JSON 도 받는다.
#    1) --textsave 로 <sha256>.txt 를 남기게 한다
#    2) --rule-only 라 임베딩 모델을 안 올려 추출 시간에 가깝다
#    3) 남은 txt 중 _index.jsonl 에 적힌 것을 골라 읽는다
#
# -in: src = 원본 문서 경로
# -in: tdir = 본문을 남길 빈 폴더 경로
#
# -out: (걸린초, 본문, 상태, 등급) — 등급은 C/S/O/None
# -out: error = 예외 대신 상태 문자열(timeout/error/empty)로 알린다
#------------------------------------------------------------------
def run_rs(src, tdir):
    tdir.mkdir(parents=True, exist_ok=True)
    res = tdir / "r.json"
    t0 = time.perf_counter()
    try:
        subprocess.run(
            [str(RS), "--file", str(src), "--rule-only", "--no-timing",
             "--textsave", str(tdir), "--format", "json", "--out", str(res)],
            capture_output=True, timeout=TIMEOUT,
        )
        el = time.perf_counter() - t0
    except subprocess.TimeoutExpired:
        return TIMEOUT, "", "timeout", None
    txt, grade = "", None
    # 결과 JSON 에서 최종 등급만 꺼낸다(본문 차이가 등급까지 바꾸는지 보려고)
    if res.exists():
        try:
            d = json.loads(res.read_text("utf-8", "replace"))
            rec = d[0] if isinstance(d, list) and d else d
            if isinstance(rec, dict):
                grade = rec.get("grade")
        except Exception:
            pass
    for f in tdir.glob("*.txt"):
        if f.name.startswith("_"):
            continue
        txt = f.read_text("utf-8", "replace")
        break
    if not txt.strip():
        return el, "", "empty", grade
    return el, txt, "ok", grade


#------------------------------------------------------------------
# 두 파서를 모든 표본에 대해 돌려 기록 남기기
#=> 표본 하나마다 snf·자체파서를 차례로 돌리고, 시간·글자수·낱말 일치도를 계산해
#   results.jsonl 한 줄로 적는다. 사이냅 본문은 뒤에서 등급 비교에 쓰려고 따로 모아 둔다.
#    1) 표본 선정 → 진행하며 한 줄씩 기록(중간에 끊겨도 남게)
#    2) 두 본문의 낱말 집합으로 재현율·자카드를 낸다
#
# -in: 없음
#
# -out: 없음 (raw/results.jsonl · raw/snftext/ 생성)
# -out: error = 개별 파일 실패는 상태값으로 남기고 계속 진행
#------------------------------------------------------------------
def main():
    samples = pick_samples()
    snfdir = OUT / "snftext"
    snfdir.mkdir(exist_ok=True)
    tmp = OUT / "tmp"
    rows = []
    total = len(samples)
    print(f"표본 {total}건", flush=True)
    for i, (ext, src) in enumerate(samples, 1):
        tag = f"{i:04d}_{ext}"
        sdst = snfdir / f"{tag}.txt"
        se, stxt, sst = run_snf(src, sdst)
        # 자체 파서는 실행마다 폴더를 비워야 이전 본문과 섞이지 않는다
        tdir = tmp / tag
        if tdir.exists():
            for f in tdir.iterdir():
                f.unlink()
        re_, rtxt, rst, grade = run_rs(src, tdir)
        sn, rn = norm(stxt), norm(rtxt)
        st, rt = toks(sn), toks(rn)
        inter = len(st & rt)
        row = {
            "i": i, "ext": ext, "path": str(src),
            "size": src.stat().st_size if src.exists() else 0,
            "snf_sec": round(se, 4), "rs_sec": round(re_, 4),
            "snf_status": sst, "rs_status": rst,
            "snf_chars": len(sn), "rs_chars": len(rn),
            "snf_toks": len(st), "rs_toks": len(rt),
            "recall": round(inter / len(st), 4) if st else None,
            "precision": round(inter / len(rt), 4) if rt else None,
            "jaccard": round(inter / len(st | rt), 4) if (st or rt) else None,
            "rs_grade": grade,
        }
        rows.append(row)
        with open(OUT / "results.jsonl", "a", encoding="utf-8") as fp:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")
        if i % 10 == 0 or i == total:
            print(f"  {i}/{total} {ext}", flush=True)
    print("done", len(rows))


if __name__ == "__main__":
    main()
