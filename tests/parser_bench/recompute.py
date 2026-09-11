# -*- coding: utf-8 -*-
"""저장해 둔 본문으로 일치도만 다시 계산한다(시간 측정은 건드리지 않는다).

비교 기준(norm/toks)을 고칠 때마다 몇 분짜리 측정을 다시 돌릴 이유가 없다.
raw_pure/snftext · raw_pure/rstext 에 두 파서의 본문이 그대로 남아 있으므로
그것만 다시 읽어 재현율·정밀도를 새로 낸다.
"""

import json
import re
from pathlib import Path

import bench_pure as B

OUT = Path(__file__).resolve().parent / "raw_pure"


#------------------------------------------------------------------
# 일치도만 다시 계산해 pure.jsonl 을 갱신
#=> 줄마다 두 본문을 다시 읽어 글자수·재현율·정밀도·자카드를 새로 적는다.
#   시간(snf_sec·rs_sec)과 상태는 그대로 둔다 — 다시 잰 것이 아니기 때문이다.
#    1) 자체판 출력의 '===== 경로 =====' 구분선은 본문이 아니라 뗀다
#    2) 두 본문 다 50자 미만이면 비교값을 None 으로 둔다(비교가 뜻이 없다)
#
# -in: 없음
#
# -out: 없음 (raw_pure/pure.jsonl 덮어씀)
# -out: error = 본문 파일이 없으면 그 줄은 손대지 않는다
#------------------------------------------------------------------
def main():
    rows = [json.loads(l) for l in (OUT / "pure.jsonl").read_text("utf-8").splitlines() if l.strip()]
    changed = 0
    for r in rows:
        tag = f"{r['i']:04d}_{r['ext']}"
        sp, rp = OUT / "snftext" / f"{tag}.txt", OUT / "rstext" / f"{tag}.txt"
        if not sp.exists() or not rp.exists():
            continue
        stxt = sp.read_bytes().decode("utf-8", "replace")
        rtxt = rp.read_bytes().decode("utf-8", "replace")
        rtxt = re.sub(r"(?m)^=====.*=====$", "", rtxt)
        sn, rn = B.norm(stxt), B.norm(rtxt)
        st, rt = B.toks(sn), B.toks(rn)
        inter = len(st & rt)
        before = r.get("recall")
        r["snf_chars"], r["rs_chars"] = len(sn), len(rn)
        r["recall"] = round(inter / len(st), 4) if st else None
        r["precision"] = round(inter / len(rt), 4) if rt else None
        r["jaccard"] = round(inter / len(st | rt), 4) if (st or rt) else None
        if before != r["recall"]:
            changed += 1
    (OUT / "pure.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", "utf-8")
    print("갱신", changed, "/", len(rows))


if __name__ == "__main__":
    main()
