# -*- coding: utf-8 -*-
"""벤치 결과 보강: 자체파서 실행 JSON 에서 등급·PII·내부시간을 꺼내고,
사이냅 본문으로도 같은 분류를 돌려 '본문 차이가 판정을 바꾸는지' 본다."""

import json
import subprocess
import sys
from pathlib import Path

WORK = Path(__file__).resolve().parent
OUT = WORK / "raw"
RS = Path(r"D:\Project\MpowerClassify\Rust\dist-onedir\windows\MpowerClassify-rs.exe")


#------------------------------------------------------------------
# 분류 결과 레코드에서 필요한 값만 뽑기
#=> 결과 JSON 한 건(파일 레코드)에서 등급·PII 유형·잘림 여부를 간추린다.
#    1) grade 는 그대로
#    2) why.security.signals.rule.hits 안의 유형 id 를 모아 집합으로
#
# -in: rec = 파일 레코드 dict
#
# -out: (등급, PII유형집합, 잘림여부)
# -out: error = 구조가 달라도 예외 없이 빈 값으로 답한다
#------------------------------------------------------------------
def pick(rec):
    grade = rec.get("grade")
    trunc = bool(rec.get("text_truncated"))
    ids = set()
    try:
        hits = rec["why"]["security"]["signals"]["rule"]["hits"]
        for h in hits:
            ids.add(h.get("id"))
    except Exception:
        pass
    return grade, ids, trunc


#------------------------------------------------------------------
# 결과 JSON 파일에서 레코드·요약 찾아내기
#=> 출력은 [실행헤더, 파일레코드…, 요약] 배열이라 가운데만 골라야 한다.
#
# -in: p = r.json 경로
#
# -out: (레코드리스트, 요약dict)
# -out: error = 파일이 없거나 깨졌으면 ([], {})
#------------------------------------------------------------------
def load(p):
    try:
        d = json.loads(Path(p).read_text("utf-8", "replace"))
    except Exception:
        return [], {}
    if isinstance(d, dict):
        d = [d]
    recs = [r for r in d if isinstance(r, dict) and "file" in r]
    summ = {}
    for r in d:
        if isinstance(r, dict) and "summary" in r:
            summ = r["summary"]
    return recs, summ


#------------------------------------------------------------------
# 사이냅 본문만 모아 한 번에 재분류
#=> raw/snftext/*.txt(사이냅이 뽑은 본문)를 자체 분류기에 통째로 넣는다.
#   같은 규칙·같은 코드로 돌리므로 차이는 오직 '본문'에서만 온다.
#
# -in: 없음
#
# -out: {태그: (등급, PII집합)} 사전
# -out: error = 실행 실패 시 빈 사전
#------------------------------------------------------------------
def grade_snf_texts():
    res = OUT / "snf_grades.json"
    subprocess.run(
        [str(RS), "--dir", str(OUT / "snftext"), "--rule-only", "--no-timing",
         "--format", "json", "--out", str(res)],
        capture_output=True, timeout=3600,
    )
    recs, _ = load(res)
    out = {}
    for r in recs:
        tag = Path(r["file"]).stem  # 0001_pdf 형태
        g, ids, _ = pick(r)
        out[tag] = (g, ids)
    return out


#------------------------------------------------------------------
# 모든 표본 줄에 등급·PII 비교 결과 붙이기
#=> results.jsonl 을 읽어 자체파서 쪽 판정과 사이냅본문 쪽 판정을 나란히 적는다.
#    1) tmp/<태그>/r.json 에서 자체파서 판정 회수
#    2) 사이냅 본문 일괄 분류 결과와 맞춰 등급 일치 여부 계산
#
# -in: 없음
#
# -out: 없음 (raw/merged.jsonl 생성)
# -out: error = 개별 항목 누락은 None 으로 남기고 계속
#------------------------------------------------------------------
def main():
    snfg = grade_snf_texts()
    print("사이냅 본문 분류:", len(snfg), flush=True)
    rows = [json.loads(l) for l in (OUT / "results.jsonl").read_text("utf-8").splitlines() if l.strip()]
    with open(OUT / "merged.jsonl", "w", encoding="utf-8") as fp:
        for row in rows:
            tag = f"{row['i']:04d}_{row['ext']}"
            recs, _ = load(OUT / "tmp" / tag / "r.json")
            if recs:
                g, ids, trunc = pick(recs[0])
            else:
                g, ids, trunc = None, set(), False
            sg, sids = snfg.get(tag, (None, set()))
            row["rs_grade"] = g
            row["rs_trunc"] = trunc
            row["snf_grade"] = sg
            row["grade_match"] = (g == sg)
            row["rs_pii"] = sorted(ids)
            row["snf_pii"] = sorted(sids)
            row["pii_match"] = (ids == sids)
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("merged", len(rows))


if __name__ == "__main__":
    main()
