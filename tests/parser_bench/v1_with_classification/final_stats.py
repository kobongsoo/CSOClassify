# -*- coding: utf-8 -*-
"""보고서에 실을 최종 수치를 한 파일(final.json)로 모은다."""

import json
import statistics as st
from pathlib import Path

OUT = Path(__file__).resolve().parent / "raw"


#------------------------------------------------------------------
# 값이 없을 수 있는 목록의 중앙값
#=> 빈 목록이면 0 을 돌려 표가 깨지지 않게 한다.
#
# -in: xs = 숫자 리스트
#
# -out: 중앙값(float)
# -out: error = 없음
#------------------------------------------------------------------
def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else 0.0


#------------------------------------------------------------------
# 평균(빈 목록 안전)
#=> 나눗셈 0 을 피한다.
#
# -in: xs = 숫자 리스트
#
# -out: 평균(float) 또는 None
# -out: error = 없음
#------------------------------------------------------------------
def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


#------------------------------------------------------------------
# 확장자 한 묶음을 보고서 한 줄로 줄이기
#=> 건수·크기·시간·성공·본문일치·판정일치를 한 번에 계산한다.
#    1) '둘 다 본문을 얻었고 잘리지 않은' 건만 본문 일치도에 쓴다
#    2) 실패는 이유별(스캔본/크기상한/기타)로 나눠 센다 — 파서 탓이 아닌 걸 구분하려고
#
# -in: rs = 같은 확장자의 표본 dict 리스트
#
# -out: 집계 dict
# -out: error = 없음
#------------------------------------------------------------------
def row(rs):
    n = len(rs)
    cmpable = [r for r in rs if r["snf_status"] == "ok" and r["rs_status"] == "ok"
               and not r.get("rs_trunc") and r["snf_chars"] >= 50]
    fail = [r for r in rs if r["rs_status"] != "ok"]
    scan = [r for r in fail if r["snf_chars"] < 50]
    size = [r for r in fail if r["size"] > 20_000_000]
    return {
        "n": n,
        "mb": sum(r["size"] for r in rs) / n / 1e6,
        "snf_med": med([r["snf_sec"] for r in rs]),
        "rs_med": med([r["rs_sec"] for r in rs]),
        "rs_ext_med": med([r.get("rs_extract") for r in rs]),
        "snf_ok": sum(1 for r in rs if r["snf_status"] == "ok" and r["snf_chars"] >= 50),
        "rs_ok": sum(1 for r in rs if r["rs_status"] == "ok"),
        "fail": len(fail), "fail_scan": len(scan), "fail_size": len(size),
        "fail_other": len(fail) - len(scan) - len(size),
        "n_cmp": len(cmpable),
        "recall": avg([r["recall"] for r in cmpable]),
        "prec": avg([r["precision"] for r in cmpable]),
        "charratio": avg([r["rs_chars"] / r["snf_chars"] for r in cmpable if r["snf_chars"]]),
        "grade_match": sum(1 for r in rs if r["grade_match"]) / n,
        "grade_diff": [r for r in rs if not r["grade_match"]],
        "pii_match": avg([1.0 if r.get("pii_match") else 0.0 for r in cmpable]),
    }


#------------------------------------------------------------------
# 최종 수치 모으기
#=> merged2.jsonl 을 확장자별·전체로 집계해 final.json 에 쓴다.
#
# -in: 없음
#
# -out: 없음 (raw/final.json 생성)
# -out: error = 입력 없으면 예외 전파
#------------------------------------------------------------------
def main():
    rows = [json.loads(l) for l in (OUT / "merged2.jsonl").read_text("utf-8").splitlines() if l.strip()]
    by = {}
    for r in rows:
        by.setdefault(r["ext"], []).append(r)
    res = {e: row(rs) for e, rs in sorted(by.items())}
    allr = row(rows)
    allr["bytes"] = sum(r["size"] for r in rows)
    allr["snf_total"] = sum(r["snf_sec"] for r in rows)
    allr["rs_total"] = sum(r["rs_sec"] for r in rows)
    (OUT / "final.json").write_text(
        json.dumps({"ext": res, "all": allr}, ensure_ascii=False, default=str, indent=1), "utf-8")
    print(json.dumps({k: v for k, v in allr.items() if k != "grade_diff"},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
