# -*- coding: utf-8 -*-
"""merged.jsonl 을 확장자별로 집계해 stats.json 으로 떨어뜨린다."""

import json
import statistics as st
from pathlib import Path

WORK = Path(__file__).resolve().parent
OUT = WORK / "raw"


#------------------------------------------------------------------
# 숫자 목록의 평균/중앙값 안전하게 구하기
#=> 값이 하나도 없을 때 예외로 죽지 않고 None 을 돌려준다.
#
# -in: xs = 숫자 리스트
#
# -out: (평균, 중앙값) 또는 (None, None)
# -out: error = 없음
#------------------------------------------------------------------
def ms(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None, None
    return sum(xs) / len(xs), st.median(xs)


#------------------------------------------------------------------
# 확장자별 집계표 만들기
#=> 표본 줄들을 확장자로 묶어 시간·성공률·본문일치도·판정일치율을 한 줄로 줄인다.
#    1) 둘 다 본문을 얻은 건만 '일치도' 계산에 쓴다(실패끼리 비교는 무의미)
#    2) 시간은 평균이 큰 파일에 휘둘리므로 중앙값도 같이 낸다
#
# -in: rows = merged.jsonl 을 읽은 dict 리스트
#
# -out: {확장자: 집계dict} 와 전체 집계
# -out: error = 없음
#------------------------------------------------------------------
def agg(rows):
    by = {}
    for r in rows:
        by.setdefault(r["ext"], []).append(r)
    out = {}
    for ext, rs in sorted(by.items()):
        both = [r for r in rs if r["snf_status"] == "ok" and r["rs_status"] == "ok"]
        # 2M 글자 상한에 걸린 건은 파서 실력이 아니라 정책이므로 일치도에서 뺀다
        cmpable = [r for r in both if not r.get("rs_trunc")]
        sa, sm = ms([r["snf_sec"] for r in rs])
        ra, rm = ms([r["rs_sec"] for r in rs])
        gm = [r for r in rs if r.get("snf_grade") is not None or r.get("rs_grade") is not None]
        out[ext] = {
            "n": len(rs),
            "mb": sum(r["size"] for r in rs) / len(rs) / 1e6,
            "snf_ok": sum(1 for r in rs if r["snf_status"] == "ok"),
            "rs_ok": sum(1 for r in rs if r["rs_status"] == "ok"),
            "snf_avg": sa, "snf_med": sm, "rs_avg": ra, "rs_med": rm,
            "ratio": (ra / sa) if (sa and ra) else None,
            "n_cmp": len(cmpable),
            "recall": ms([r["recall"] for r in cmpable])[0],
            "prec": ms([r["precision"] for r in cmpable])[0],
            "jac": ms([r["jaccard"] for r in cmpable])[0],
            "charratio": ms([(r["rs_chars"] / r["snf_chars"]) for r in cmpable if r["snf_chars"]])[0],
            "grade_match": (sum(1 for r in gm if r["grade_match"]) / len(gm)) if gm else None,
            "n_grade": len(gm),
            "pii_match": (sum(1 for r in cmpable if r.get("pii_match")) / len(cmpable)) if cmpable else None,
            "trunc": sum(1 for r in rs if r.get("rs_trunc")),
        }
    return out


#------------------------------------------------------------------
# 집계 실행 진입점
#=> merged.jsonl 을 읽어 stats.json 으로 저장하고 화면에 요약을 찍는다.
#
# -in: 없음
#
# -out: 없음 (raw/stats.json 생성)
# -out: error = 입력 파일이 없으면 예외 전파
#------------------------------------------------------------------
def main():
    rows = [json.loads(l) for l in (OUT / "merged.jsonl").read_text("utf-8").splitlines() if l.strip()]
    a = agg(rows)
    tot = {
        "n": len(rows),
        "snf_ok": sum(1 for r in rows if r["snf_status"] == "ok"),
        "rs_ok": sum(1 for r in rows if r["rs_status"] == "ok"),
        "snf_sec": sum(r["snf_sec"] for r in rows),
        "rs_sec": sum(r["rs_sec"] for r in rows),
        "grade_match": sum(1 for r in rows if r["grade_match"]) / len(rows),
        "bytes": sum(r["size"] for r in rows),
    }
    (OUT / "stats.json").write_text(
        json.dumps({"by_ext": a, "total": tot}, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(tot, ensure_ascii=False, indent=1))
    for e, v in a.items():
        print(f"{e:6s} n={v['n']:3d} snfOK={v['snf_ok']:3d} rsOK={v['rs_ok']:3d} "
              f"snf={v['snf_med']:.3f} rs={v['rs_med']:.3f} "
              f"recall={v['recall'] if v['recall'] is None else round(v['recall'],3)} "
              f"grade={v['grade_match'] if v['grade_match'] is None else round(v['grade_match'],3)}")


if __name__ == "__main__":
    main()
