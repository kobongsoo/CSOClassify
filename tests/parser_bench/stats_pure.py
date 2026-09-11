# -*- coding: utf-8 -*-
"""pure.jsonl 을 확장자별로 집계해 pure_stats.json 으로 떨어뜨린다."""

import json
import statistics as st
from pathlib import Path

OUT = Path(__file__).resolve().parent / "raw_pure"


#------------------------------------------------------------------
# 중앙값(빈 목록 안전)
#=> 값이 없으면 0 을 돌려 표가 깨지지 않게 한다.
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
#=> 건수·크기·추출시간·성공률·본문일치도를 계산한다. 분류·PII 는 쓰지 않는다.
#    1) 둘 다 50자 이상 본문을 얻은 건만 일치도 계산에 쓴다
#    2) 실패는 '스캔본(사이냅도 못 읽음)'과 '크기 상한'으로 나눠 센다
#
# -in: rs = 같은 확장자의 표본 dict 리스트
#
# -out: 집계 dict
# -out: error = 없음
#------------------------------------------------------------------
def row(rs):
    n = len(rs)
    cmpable = [r for r in rs if r["snf_status"] == "ok" and r["rs_status"] == "ok"
               and r["snf_chars"] >= 50]
    fail = [r for r in rs if r["rs_status"] != "ok"]
    scan = [r for r in fail if r["snf_chars"] < 50]
    size = [r for r in fail if r["size"] > 20_000_000 and r["snf_chars"] >= 50]
    mb = sum(r["size"] for r in rs) / 1e6
    snf_t = sum(r["snf_sec"] for r in rs)
    rs_t = sum(r["rs_sec"] for r in rs)
    return {
        "n": n, "mb": mb / n,
        "snf_med": med([r["snf_sec"] for r in rs]),
        "rs_med": med([r["rs_sec"] for r in rs]),
        "snf_mbs": mb / snf_t if snf_t else None,
        "rs_mbs": mb / rs_t if rs_t else None,
        "snf_ok": sum(1 for r in rs if r["snf_status"] == "ok" and r["snf_chars"] >= 50),
        "rs_ok": sum(1 for r in rs if r["rs_status"] == "ok"),
        "fail": len(fail), "fail_scan": len(scan), "fail_size": len(size),
        "fail_other": len(fail) - len(scan) - len(size),
        "n_cmp": len(cmpable),
        "recall": avg([r["recall"] for r in cmpable]),
        "prec": avg([r["precision"] for r in cmpable]),
        "jac": avg([r["jaccard"] for r in cmpable]),
        "charratio": avg([r["rs_chars"] / r["snf_chars"] for r in cmpable if r["snf_chars"]]),
    }


#------------------------------------------------------------------
# 집계 실행
#=> pure.jsonl 을 읽어 pure_stats.json 을 쓰고 화면에 요약을 찍는다.
#
# -in: 없음
#
# -out: 없음 (raw_pure/pure_stats.json 생성)
# -out: error = 입력 파일이 없으면 예외 전파
#------------------------------------------------------------------
def main():
    rows = [json.loads(l) for l in (OUT / "pure.jsonl").read_text("utf-8").splitlines() if l.strip()]
    by = {}
    for r in rows:
        by.setdefault(r["ext"], []).append(r)
    res = {e: row(rs) for e, rs in sorted(by.items())}
    allr = row(rows)
    allr["bytes"] = sum(r["size"] for r in rows)
    allr["snf_total"] = sum(r["snf_sec"] for r in rows)
    allr["rs_total"] = sum(r["rs_sec"] for r in rows)
    (OUT / "pure_stats.json").write_text(
        json.dumps({"ext": res, "all": allr}, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(allr, ensure_ascii=False, indent=1))
    for e, v in res.items():
        print(f"{e:5s} n={v['n']:3d} rsOK={v['rs_ok']:3d} snf={v['snf_med']*1000:6.1f}ms "
              f"rs={v['rs_med']*1000:6.1f}ms x{(v['rs_med']/v['snf_med'] if v['snf_med'] else 0):.2f} "
              f"rec={v['recall'] if v['recall'] is None else round(v['recall'],3)} "
              f"prec={v['prec'] if v['prec'] is None else round(v['prec'],3)} "
              f"cr={v['charratio'] if v['charratio'] is None else round(v['charratio'],2)}")


if __name__ == "__main__":
    main()
