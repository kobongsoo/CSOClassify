# -*- coding: utf-8 -*-
"""동작이 바뀐 문서만 다시 재서 pure.jsonl 의 그 줄만 갈아 끼운다.

표본 305건을 통째로 다시 돌리면 40분이 넘게 걸린다. 상한을 올린 것처럼
**일부 문서의 판정만 달라지는 변경**에서는 달라진 문서만 다시 재는 편이
정확하고 빠르다 — 나머지 줄의 시간은 같은 exe·같은 문서라 그대로 유효하다.

쓰는 법:
    python remeasure.py --size-capped     # 20MB 상한에 막혔던 문서만
    python remeasure.py --ext tsv,json    # 해당 확장자 전체
"""

import argparse
import json
from pathlib import Path

import bench_pure as B


#------------------------------------------------------------------
# 문서 한 건을 다시 재서 pure.jsonl 한 줄 만들기
#=> bench_pure 와 똑같은 방식으로 두 파서를 돌려 같은 모양의 기록을 만든다.
#   측정 방식이 어긋나면 갈아 끼운 줄만 다른 잣대가 되어 표가 거짓말을 한다.
#
# -in: rec = 기존 pure.jsonl 한 줄(dict) — i/ext/path 를 재사용한다
#
# -out: 새로 잰 기록 dict (기존 줄과 같은 열 구성)
# -out: error = 개별 실패는 status 문자열로 남기고 예외를 던지지 않는다
#------------------------------------------------------------------
def remeasure(rec):
    src = Path(rec["path"])
    tag = f"{rec['i']:04d}_{rec['ext']}"
    se, stxt, sst = B.run_snf(src, B.OUT / "snftext" / f"{tag}.txt")
    re_, rtxt, rst = B.run_rs(src, B.OUT / "rstext" / f"{tag}.txt")
    sn, rn = B.norm(stxt), B.norm(rtxt)
    st, rt = B.toks(sn), B.toks(rn)
    inter = len(st & rt)
    return {
        "i": rec["i"], "ext": rec["ext"], "path": rec["path"],
        "size": src.stat().st_size if src.exists() else 0,
        "snf_sec": round(se, 4), "rs_sec": round(re_, 4),
        "snf_status": sst, "rs_status": rst,
        "snf_chars": len(sn), "rs_chars": len(rn),
        "recall": round(inter / len(st), 4) if st else None,
        "precision": round(inter / len(rt), 4) if rt else None,
        "jaccard": round(inter / len(st | rt), 4) if (st or rt) else None,
    }


#------------------------------------------------------------------
# 다시 잴 대상 고르기
#=> --size-capped 는 '자체판만 실패했고 20MB 를 넘으며 사이냅은 읽어낸' 문서다.
#   곧 크기 상한 때문에 빠졌던 문서만 정확히 집어낸다.
#
# -in: rows = pure.jsonl 전체
# -in: args = 명령줄 인자(size_capped / ext)
#
# -out: 다시 잴 기록 리스트
# -out: error = 없음
#------------------------------------------------------------------
def pick(rows, args):
    if args.size_capped:
        return [r for r in rows if r["rs_status"] != "ok"
                and r["size"] > 20_000_000 and r["snf_chars"] >= 50]
    exts = {e.strip() for e in args.ext.split(",") if e.strip()}
    return [r for r in rows if r["ext"] in exts]


#------------------------------------------------------------------
# 다시 재고 pure.jsonl 갈아 끼우기
#=> 대상만 새로 재고 나머지 줄은 원래 순서 그대로 다시 쓴다.
#
# -in: 없음(명령줄 인자를 읽는다)
#
# -out: 없음 (raw_pure/pure.jsonl 갱신)
# -out: error = 대상이 없으면 아무것도 바꾸지 않고 알린다
#------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size-capped", action="store_true")
    ap.add_argument("--ext", default="")
    args = ap.parse_args()

    path = B.OUT / "pure.jsonl"
    rows = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
    targets = pick(rows, args)
    if not targets:
        print("다시 잴 문서가 없습니다.")
        return

    print(f"대상 {len(targets)}건 · 각 {B.REPEAT}회 측정", flush=True)
    # i(표본 번호)로 갈아 끼운다 — 경로는 같은 파일이 두 번 뽑힐 수 있어 키로 약하다.
    new = {}
    for k, rec in enumerate(targets, 1):
        r = remeasure(rec)
        new[r["i"]] = r
        print(f"  {k}/{len(targets)} {r['ext']:5s} {r['rs_status']:6s} "
              f"rs={r['rs_chars']:>10,}자 snf={r['snf_chars']:>10,}자 "
              f"recall={r['recall']}", flush=True)

    with open(path, "w", encoding="utf-8") as fp:
        for r in rows:
            fp.write(json.dumps(new.get(r["i"], r), ensure_ascii=False) + "\n")
    print(f"pure.jsonl {len(new)}줄 갱신")


if __name__ == "__main__":
    main()
