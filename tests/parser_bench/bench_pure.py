# -*- coding: utf-8 -*-
"""순수 문서파서 비교 — 자체 파서(--text-only) vs 사이냅(snf_exe).

분류·PII 검사를 전혀 하지 않는 조건에서 추출시간과 본문만 견준다.
표본 뽑는 방식은 run_bench.py 와 같다(같은 난수 씨앗 → 같은 문서).
"""

import json
import os
import random
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(r"D:\분류함")
SNF = Path(r"D:\Project\MpowerClassify\resources\synap\windows\snf_exe.exe")
RS = Path(r"D:\Project\MpowerClassify\Rust\dist-onedir\windows\MpowerClassify-rs.exe")
WORK = Path(__file__).resolve().parent
OUT = WORK / "raw_pure"
OUT.mkdir(exist_ok=True)

CAPS = {
    "pdf": 45, "hwp": 30, "hwpx": 10, "doc": 25, "docx": 25,
    "ppt": 20, "pptx": 25, "xls": 15, "xlsx": 20,
    "html": 20, "txt": 20, "csv": 15, "md": 15, "json": 10, "tsv": 10,
}
TIMEOUT = 300
REPEAT = 3          # 시간은 흔들린다 — 여러 번 재서 가장 빠른 값을 쓴다


#------------------------------------------------------------------
# 비교 대상 문서 고르기
#=> 'D:\분류함' 을 통째로 훑어 확장자별로 정해진 개수만 무작위로 뽑는다.
#   난수 씨앗이 고정돼 있어 1차 측정과 똑같은 문서가 뽑힌다(비교가 이어진다).
#
# -in: 없음
#
# -out: samples = [(확장자, 파일경로Path), ...]
# -out: error = 없음(읽을 수 없는 폴더는 건너뜀)
#------------------------------------------------------------------
def pick_samples():
    buckets = {}
    for dirpath, _dirnames, filenames in os.walk(ROOT):
        for fn in filenames:
            ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
            if ext in CAPS:
                buckets.setdefault(ext, []).append(Path(dirpath) / fn)
    rnd = random.Random(20260911)
    out = []
    for ext, files in sorted(buckets.items()):
        rnd.shuffle(files)
        for f in files[: CAPS[ext]]:
            out.append((ext, f))
    return out


#------------------------------------------------------------------
# 본문을 비교하기 좋게 다듬기
#=> 공백·줄바꿈·페이지 표식처럼 파서마다 다를 수밖에 없는 군더더기를 없앤다.
#
# -in: s = 원본 추출 문자열
#
# -out: 정규화된 문자열
# -out: error = 없음
#------------------------------------------------------------------
def norm(s):
    s = s.replace("\x00", " ")
    # 사이냅이 넣는 쪽·시트 구분 표식(..PAGE:3 · ..SHEET:1)은 내용이 아니다.
    # 빼지 않으면 '자체판이 놓친 낱말'로 잘못 세어 재현율이 실제보다 낮게 나온다.
    s = re.sub(r"\.\.(?:PAGE|SHEET):\d+", " ", s, flags=re.I)
    s = re.sub(r"\[?page\s*\d+\]?", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


#------------------------------------------------------------------
# 비교용 낱말 집합 뽑기
#=> 한글·영문·숫자 덩어리만 골라 집합으로 만든다. 줄 순서가 달라도 견줄 수 있다.
#
# -in: s = 정규화된 문자열
#
# -out: 낱말 집합(set)
# -out: error = 없음
#------------------------------------------------------------------
def toks(s):
    return set(re.findall(r"[0-9A-Za-z가-힣]+", s))


#------------------------------------------------------------------
# 명령 한 줄을 여러 번 재서 가장 빠른 시간 얻기
#=> 디스크 캐시·백그라운드 작업 때문에 한 번 측정은 들쭉날쭉하다.
#   같은 일을 REPEAT 번 시키고 **최솟값**을 쓴다 — 방해가 가장 적었던 판이
#   그 도구의 실력에 가장 가깝기 때문이다(평균은 잡음을 그대로 싣는다).
#
# -in: cmd = 실행할 명령 리스트
#
# -out: (가장 빠른 초, 종료코드) — 시간초과면 (TIMEOUT, None)
# -out: error = 예외를 던지지 않고 종료코드 None 으로 알린다
#------------------------------------------------------------------
def timed(cmd):
    best, rc = None, None
    for _ in range(REPEAT):
        t0 = time.perf_counter()
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return float(TIMEOUT), None
        el = time.perf_counter() - t0
        rc = p.returncode
        if best is None or el < best:
            best = el
    return best, rc


#------------------------------------------------------------------
# 사이냅으로 본문만 뽑기
#=> snf_exe 는 원래 추출 전용이라 옵션 그대로 쓴다(-U8 = UTF-8 출력).
#
# -in: src = 원본 문서 경로
# -in: dst = 결과 텍스트 경로
#
# -out: (가장 빠른 초, 본문, 상태)
# -out: error = 상태 문자열(timeout/error/empty)로 알린다
#------------------------------------------------------------------
def run_snf(src, dst):
    el, rc = timed([str(SNF), "-U8", str(src), str(dst)])
    if rc is None:
        return el, "", "timeout"
    txt = dst.read_bytes().decode("utf-8", "replace") if dst.exists() else ""
    if not txt.strip():
        return el, "", ("error" if rc != 0 else "empty")
    return el, txt, "ok"


#------------------------------------------------------------------
# 자체 파서로 본문만 뽑기
#=> --text-only 는 규칙셋을 아예 읽지 않고 PII 검사도 하지 않는다.
#   그래서 사이냅과 같은 일(=추출)만 하는 상태로 견줄 수 있다.
#    1) 출력 첫 줄 '===== 경로 =====' 는 구분자라 본문에서 뗀다
#
# -in: src = 원본 문서 경로
# -in: dst = 결과 텍스트 경로
#
# -out: (가장 빠른 초, 본문, 상태)
# -out: error = 상태 문자열(timeout/error/empty)로 알린다
#------------------------------------------------------------------
def run_rs(src, dst):
    el, rc = timed([str(RS), "--text-only", "--file", str(src), "--out", str(dst)])
    if rc is None:
        return el, "", "timeout"
    txt = dst.read_bytes().decode("utf-8", "replace") if dst.exists() else ""
    # 문서 구분선은 본문이 아니다 — 비교에서 빼지 않으면 없던 낱말이 생긴다.
    txt = re.sub(r"(?m)^=====.*=====$", "", txt)
    if not txt.strip():
        return el, "", ("error" if rc not in (0,) else "empty")
    return el, txt, "ok"


#------------------------------------------------------------------
# 표본 전체를 두 파서로 돌려 기록 남기기
#=> 문서마다 두 파서를 각각 REPEAT 번 돌려 최소 시간을 재고, 본문 낱말로
#   재현율·정밀도를 계산해 pure.jsonl 한 줄로 적는다.
#
# -in: 없음
#
# -out: 없음 (raw_pure/pure.jsonl 생성)
# -out: error = 개별 실패는 상태값으로 남기고 계속 진행
#------------------------------------------------------------------
def main():
    samples = pick_samples()
    sdir, rdir = OUT / "snftext", OUT / "rstext"
    sdir.mkdir(exist_ok=True)
    rdir.mkdir(exist_ok=True)
    total = len(samples)
    print(f"표본 {total}건 · 각 {REPEAT}회 측정", flush=True)
    with open(OUT / "pure.jsonl", "w", encoding="utf-8") as fp:
        for i, (ext, src) in enumerate(samples, 1):
            tag = f"{i:04d}_{ext}"
            se, stxt, sst = run_snf(src, sdir / f"{tag}.txt")
            re_, rtxt, rst = run_rs(src, rdir / f"{tag}.txt")
            sn, rn = norm(stxt), norm(rtxt)
            st, rt = toks(sn), toks(rn)
            inter = len(st & rt)
            fp.write(json.dumps({
                "i": i, "ext": ext, "path": str(src),
                "size": src.stat().st_size if src.exists() else 0,
                "snf_sec": round(se, 4), "rs_sec": round(re_, 4),
                "snf_status": sst, "rs_status": rst,
                "snf_chars": len(sn), "rs_chars": len(rn),
                "recall": round(inter / len(st), 4) if st else None,
                "precision": round(inter / len(rt), 4) if rt else None,
                "jaccard": round(inter / len(st | rt), 4) if (st or rt) else None,
            }, ensure_ascii=False) + "\n")
            fp.flush()
            if i % 20 == 0 or i == total:
                print(f"  {i}/{total} {ext}", flush=True)
    print("done", total)


if __name__ == "__main__":
    main()
