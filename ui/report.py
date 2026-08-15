#------------------------------------------------------------------
# 진단 리포트 로직 (STEP 5) — 순수 함수
#=> seed 저장소 균형/중복, 분류 결과의 신호 분포·신뢰도·중복 문서를 계산한다.
#   UI(Streamlit)와 분리해 단위테스트 가능하게 둔다.
#------------------------------------------------------------------

from collections import Counter

import numpy as np


#------------------------------------------------------------------
# seed 등급 구성·균형 진단
#=> C/S/O seed 개수를 세고, 특정 등급이 최다의 1/4 미만이거나 0이면 "편중"으로 보고
#   보강 권장을 만든다. 임베딩이 부족한 등급을 잘 못 구분하는 걸 예방.
#
# -in: seeds = seed 엔트리 리스트(각 grade 보유)
#
# -out: (counts, total, weak, msg) = 등급별수, 총계, 약한등급 리스트, 권장문구
# -out: error = 없음
#------------------------------------------------------------------
def seed_balance(seeds):
    counts = {"C": 0, "S": 0, "O": 0}
    for s in seeds:
        g = s.get("grade")
        if g in counts:
            counts[g] += 1
    total = sum(counts.values())
    weak = []
    if total:
        mx = max(counts.values())
        weak = [g for g, n in counts.items() if n == 0 or n < mx / 4]
    if not total:
        msg = "seed 가 없습니다. 먼저 seed 를 만드세요."
    elif weak:
        msg = f"{'/'.join(weak)} 등급 seed 가 부족합니다 — 보강을 권장합니다."
    else:
        msg = "등급 구성 균형이 양호합니다."
    return counts, total, weak, msg


#------------------------------------------------------------------
# near-duplicate 쌍 찾기
#=> 벡터가 있는 항목들 사이에서 코사인 유사도가 임계 이상인 쌍을 모두 찾는다.
#   seed 중복 정리, 코퍼스의 사본 문서 탐지에 공용으로 쓴다.
#    1) 벡터 정규화 → 코사인 행렬
#    2) 상삼각(자기자신·중복쌍 제외)에서 임계 이상만 추출
#   [성능] 항목이 cap 을 넘으면 앞에서 cap 개만 비교하고 truncated=True.
#
# -in: items     = [{"file":..., "vector":[...]}] 리스트
# -in: threshold = 코사인 임계(기본 0.97)
# -in: cap       = 최대 비교 개수(O(N^2) 방지)
#
# -out: (pairs, truncated) = [(fileA, fileB, sim)] 유사도 내림차순, 잘렸는지 여부
# -out: error = 없음
#------------------------------------------------------------------
def near_dups(items, threshold=0.97, cap=3000):
    vv = [(it.get("file"), it.get("vector")) for it in items if it.get("vector")]
    truncated = len(vv) > cap
    vv = vv[:cap]
    if len(vv) < 2:
        return [], truncated

    files = [f for f, _ in vv]
    arr = np.asarray([v for _, v in vv], dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    m = arr / norms
    sims = m @ m.T
    # 상삼각(k=1)에서만 임계 이상 위치를 뽑아 (i<j) 중복쌍만.
    mask = np.triu(sims >= threshold, k=1)
    pairs = [(files[a], files[b], float(sims[a, b])) for a, b in np.argwhere(mask)]
    pairs.sort(key=lambda x: -x[2])
    return pairs, truncated


#------------------------------------------------------------------
# 판정 신호 분포
#=> 각 문서가 어떤 신호(rule/path/name/embed)로 등급이 정해졌는지 집계한다.
#   어떤 신호가 분류를 이끄는지, 임베딩 의존도가 얼마나 되는지 보여준다.
#   (한 문서가 여러 신호로 결정되면 각각 카운트. 미분류/보류는 별도.)
#
# -in: records = 분류 레코드 리스트(decided_by/grade 보유)
#
# -out: dict = {신호명: 개수, "미분류/보류": n}
# -out: error = 없음
#------------------------------------------------------------------
def decided_by_counts(records):
    c = Counter()
    for r in records:
        db = r.get("decided_by") or []
        if not db or r.get("grade") is None:
            c["미분류/보류"] += 1
        else:
            for s in db:
                c[s] += 1
    return dict(c)


#------------------------------------------------------------------
# 신뢰도 분포(히스토그램)
#=> confidence 를 0.2 폭 구간으로 나눠 개수를 센다(분류 신뢰도 전반 파악).
#
# -in: records = 분류 레코드 리스트(confidence 보유)
#
# -out: dict = {"0.0–0.2":n, ... "0.8–1.0":n} (순서 유지)
# -out: error = 없음
#------------------------------------------------------------------
def confidence_bins(records):
    labels = ["0.0–0.2", "0.2–0.4", "0.4–0.6", "0.6–0.8", "0.8–1.0"]
    bins = {k: 0 for k in labels}
    for r in records:
        c = float(r.get("confidence", 0.0) or 0.0)
        idx = min(int(c / 0.2), 4)   # 1.0 은 마지막 구간에
        bins[labels[idx]] += 1
    return bins
