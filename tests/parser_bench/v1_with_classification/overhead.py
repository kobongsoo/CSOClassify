# -*- coding: utf-8 -*-
"""자체판의 '분류에만 드는 시간'을 재서, 추출시간만 따로 떼어 볼 수 있게 한다."""

import json
import subprocess
import time
from pathlib import Path

WORK = Path(__file__).resolve().parent
OUT = WORK / "raw"
RS = Path(r"D:\Project\MpowerClassify\Rust\dist-onedir\windows\MpowerClassify-rs.exe")


#------------------------------------------------------------------
# 평문 txt 한 건을 분류만 시켜 시간 재기
#=> 이미 뽑혀 있는 본문(.txt)을 넣으면 '추출'은 거의 0 이고 규칙·PII 검사 시간만 남는다.
#   이 값을 원본 처리시간에서 빼면 순수 추출시간을 어림할 수 있다.
#
# -in: p = 사이냅이 뽑아 둔 본문 txt 경로
#
# -out: 걸린 초(float). 실패하면 None
# -out: error = 시간초과·실행실패는 None
#------------------------------------------------------------------
def clock(p):
    t0 = time.perf_counter()
    try:
        subprocess.run(
            [str(RS), "--file", str(p), "--rule-only", "--no-timing",
             "--format", "json", "--out", str(OUT / "_o.json")],
            capture_output=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return None
    return time.perf_counter() - t0


#------------------------------------------------------------------
# 분류시간을 빼서 '추출만' 값 붙이기
#=> merged.jsonl 의 각 줄에 rs_class(분류시간)·rs_extract(추출 추정시간)를 더해
#   merged2.jsonl 로 다시 쓴다. 보고서의 '추출만(추정)' 칸이 이 값이다.
#    1) 가장 짧았던 분류시간을 프로세스 기동시간 근사치로 본다
#    2) 전체시간에서 같은 본문의 분류시간을 빼면 남는 것이 추출시간이다
#
# -in: 없음
#
# -out: 없음 (raw/merged2.jsonl 생성)
# -out: error = overhead.json·merged.jsonl 이 없으면 예외 전파
#------------------------------------------------------------------
def attach():
    ov = json.loads((OUT / "overhead.json").read_text("utf-8"))
    base = min(v for v in ov.values() if v)
    rows = [json.loads(l) for l in (OUT / "merged.jsonl").read_text("utf-8").splitlines() if l.strip()]
    for r in rows:
        o = ov.get(f"{r['i']:04d}_{r['ext']}")
        r["rs_class"] = o
        # 추출 추정치가 음수가 되는 일(측정 흔들림)은 0 으로 눌러 둔다
        r["rs_extract"] = max(r["rs_sec"] - (o or base), 0.0) if r["rs_status"] == "ok" else None
    (OUT / "merged2.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), "utf-8")
    print("merged2", len(rows))


#------------------------------------------------------------------
# 표본 전체의 분류시간 측정
#=> snftext 폴더의 모든 본문에 대해 분류시간을 재서 태그별로 저장한다.
#
# -in: 없음
#
# -out: 없음 (raw/overhead.json 생성)
# -out: error = 개별 실패는 null 로 남긴다
#------------------------------------------------------------------
def main():
    d = {}
    files = sorted((OUT / "snftext").glob("*.txt"))
    for i, f in enumerate(files, 1):
        d[f.stem] = clock(f)
        if i % 50 == 0:
            print(i, len(files), flush=True)
    (OUT / "overhead.json").write_text(json.dumps(d, indent=1), "utf-8")
    print("ok", len(d))
    attach()


if __name__ == "__main__":
    main()
