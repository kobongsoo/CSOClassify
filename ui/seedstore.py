#------------------------------------------------------------------
# seed 저장소 관리 (cso_seed.jsonl) — 순수 로직
#=> 분류 검토 UI 가 쓰는 "큐레이션된 씨앗 저장소"의 입출력·편집·감사를 담당한다.
#   UI(Streamlit) 위젯과 분리해, 이 모듈만 단위테스트할 수 있게 한다.
#    - cso_seed.jsonl      : 현재 seed 상태(추가/수정/삭제로 갱신, 통째 저장)
#    - cso_seed_audit.jsonl: 변경 이력(append-only, 누가·언제·무엇을)
#   seed 엔트리: {file, grade, vector, source, approved_by, ts, note}
#------------------------------------------------------------------

import json
import os
import datetime

import numpy as np


#------------------------------------------------------------------
# 현재 시각 ISO 문자열
#=> seed 엔트리·감사 로그에 찍을 지역시간 ISO8601(초) 문자열을 만든다.
#
# -in: 없음
# -out: str = 예 "2026-08-13T10:00:00+09:00"
# -out: error = 없음
#------------------------------------------------------------------
def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


#------------------------------------------------------------------
# seed 저장소 로드
#=> cso_seed.jsonl 을 읽어 seed 엔트리 리스트로 만든다. 없으면 빈 리스트.
#
# -in: path = cso_seed.jsonl 경로
#
# -out: list = seed 엔트리 dict 리스트
# -out: error = 깨진 줄은 건너뜀(예외 없음)
#------------------------------------------------------------------
def load_seeds(path):
    seeds = []
    if not path or not os.path.isfile(path):
        return seeds
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                seeds.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return seeds


#------------------------------------------------------------------
# seed 저장소 저장(통째 덮어쓰기)
#=> 편집된 seed 리스트 전체를 cso_seed.jsonl 에 다시 쓴다(현재 상태 = 이 파일).
#   변경 감사는 append_seed_audit 로 별도로 남긴다.
#
# -in: path  = cso_seed.jsonl 경로
# -in: seeds = seed 엔트리 리스트
#
# -out: 없음(파일 기록)
# -out: error = 쓰기 실패 시 예외 전파
#------------------------------------------------------------------
def save_seeds(path, seeds):
    with open(path, "w", encoding="utf-8") as f:
        for s in seeds:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")


#------------------------------------------------------------------
# seed 변경 감사 기록(append-only)
#=> seed 추가/수정/삭제를 누가·언제·무엇을 했는지 cso_seed_audit.jsonl 에 남긴다.
#
# -in: path     = cso_seed_audit.jsonl 경로
# -in: action   = "add" | "update" | "delete"
# -in: file     = 대상 seed 문서 경로
# -in: detail   = 변경 상세(예: "grade S→C", "source=upload")
# -in: reviewer = 작업자 이름
#
# -out: 없음(append)
# -out: error = 쓰기 실패 시 예외 전파
#------------------------------------------------------------------
def append_seed_audit(path, action, file, detail, reviewer):
    entry = {"action": action, "file": file, "detail": detail,
             "reviewer": reviewer, "ts": now_iso()}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


#------------------------------------------------------------------
# 등급별 seed 개수
#=> seed 구성(C/S/O)을 세어 균형 점검에 쓴다.
#
# -in: seeds = seed 리스트
#
# -out: dict = {"C":n, "S":n, "O":n}
# -out: error = 없음
#------------------------------------------------------------------
def grade_counts(seeds):
    counts = {"C": 0, "S": 0, "O": 0}
    for s in seeds:
        g = s.get("grade")
        if g in counts:
            counts[g] += 1
    return counts


#------------------------------------------------------------------
# 최근접 seed 유사도(near-dup 판정용)
#=> 주어진 벡터와 기존 seed 들 사이 최대 코사인 유사도와 그 문서를 찾는다.
#   중복(정보 없는) seed 추가를 막기 위한 경고 근거.
#
# -in: vector       = 후보 문서 벡터(list/ndarray)
# -in: seeds        = 기존 seed 리스트(각 벡터 보유)
# -in: exclude_file = 비교에서 뺄 문서 경로. 후보가 이미 seed 저장소에 있으면
#                     자기 자신과 sim=1.000 이 나와 "무조건 중복"으로 오판되므로,
#                     승격 시 후보 자신을 넣어 자기비교를 배제한다.
#
# -out: (max_sim, file) = 최대 유사도, 그 seed 파일(없으면 (0.0, None))
# -out: error = 없음
#------------------------------------------------------------------
def nearest_seed_sim(vector, seeds, exclude_file=None):
    # 벡터가 있고, 자기 자신(같은 경로)이 아닌 seed 만 비교 대상으로.
    pairs = [(s.get("vector"), s.get("file")) for s in seeds
             if s.get("vector") and s.get("file") != exclude_file]
    if vector is None or not pairs:
        return 0.0, None
    vecs = [p[0] for p in pairs]
    files = [p[1] for p in pairs]
    v = np.asarray(vector, dtype=np.float32)
    n = np.linalg.norm(v)
    v = v / (n if n else 1.0)
    arr = np.asarray(vecs, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    sims = (arr / norms) @ v
    i = int(np.argmax(sims))
    return float(sims[i]), files[i]


#------------------------------------------------------------------
# seed 추가/갱신
#=> 파일 경로를 키로, 이미 있으면 교체(중복 파일 방지)하고 없으면 추가한다.
#
# -in: seeds       = 기존 seed 리스트(변형하지 않고 새 리스트 반환)
# -in: file        = 문서 경로
# -in: grade       = 등급("C"/"S"/"O")
# -in: vector      = 문서 벡터
# -in: reviewer    = 승인자
# -in: source      = 출처("phase2"/"phase4"/"upload")
# -in: note        = 메모
#
# -out: list = 갱신된 seed 리스트
# -out: error = 없음
#------------------------------------------------------------------
def add_seed(seeds, file, grade, vector, reviewer, source="phase4", note=""):
    entry = {"file": file, "grade": grade, "vector": vector,
             "source": source, "approved_by": reviewer, "ts": now_iso(), "note": note}
    # 같은 파일이 이미 있으면 교체, 아니면 뒤에 추가.
    out = [s for s in seeds if s.get("file") != file]
    out.append(entry)
    return out


#------------------------------------------------------------------
# 고신뢰 후보 일괄 seed 승격(중복 자동 제외) — 순수 로직
#=> 이번 분류에서 뽑은 "고신뢰 C/S 후보"들을 한 번에 seed 저장소에 넣되, 정보량이
#   없는 사본(near-dup)은 자동으로 건너뛴다. 관리자가 하나씩 승격하는 수고를 덜면서도
#   저장소가 중복으로 비대해지지 않게 한다.
#    1) 후보를 순회하며: 벡터 없음/이미 등록됨/기존·직전추가와 near-dup 이면 제외
#    2) 통과분만 add_seed 로 누적(뒤 후보는 앞서 추가된 것과도 중복 비교됨)
#
# -in: seeds      = 기존 seed 리스트(변형하지 않고 새 리스트 반환)
# -in: candidates = [{file, grade, vector}] 후보들(등급은 C/S 등 최종등급)
# -in: reviewer   = 승인자(감사 기록용)
# -in: dup_thresh = near-dup 판정 코사인 임계(≥ 이면 사본으로 보고 제외, 기본 0.985)
# -in: source     = seed 출처 태그(기본 "phase2" = 배치 승격)
# -in: note       = 각 seed 메모
#
# -out: (new_seeds, added, stats)
#        new_seeds = 갱신된 seed 리스트
#        added     = 실제 추가된 [{file, grade}] (UI 가 감사로그를 남길 때 사용)
#        stats     = {"added","dup","exist","novec"} 각 사유별 건수
# -out: error = 없음
#------------------------------------------------------------------
def bulk_add_confident(seeds, candidates, reviewer, dup_thresh=0.985,
                       source="phase2", note="일괄 승격"):
    out = list(seeds)
    existing = {s.get("file") for s in out}     # 이미 등록된 파일(중복 추가 방지)
    added = []
    dup = exist = novec = 0
    for c in candidates:
        f, g, v = c.get("file"), c.get("grade"), c.get("vector")
        if not v:                                # 벡터 없으면 seed 로 못 씀
            novec += 1
            continue
        if f in existing:                        # 이미 저장소에 있음 → 건너뜀
            exist += 1
            continue
        # 자기 자신 제외하고, 기존+직전 추가분과의 최대 유사도로 사본 여부 판단.
        sim, _ = nearest_seed_sim(v, out, exclude_file=f)
        if sim >= dup_thresh:
            dup += 1
            continue
        out = add_seed(out, f, g, v, reviewer, source=source, note=note)
        existing.add(f)
        added.append({"file": f, "grade": g})
    stats = {"added": len(added), "dup": dup, "exist": exist, "novec": novec}
    return out, added, stats


#------------------------------------------------------------------
# seed 삭제
#=> 지정 파일의 seed 를 제거한다.
#
# -in: seeds = 기존 리스트
# -in: file  = 삭제할 문서 경로
#
# -out: list = 제거된 새 리스트
# -out: error = 없음
#------------------------------------------------------------------
def remove_seed(seeds, file):
    return [s for s in seeds if s.get("file") != file]


#------------------------------------------------------------------
# seed 등급 변경
#=> 지정 파일 seed 의 등급을 바꾼다(있을 때만).
#
# -in: seeds = 기존 리스트
# -in: file  = 대상 문서
# -in: grade = 새 등급
#
# -out: list = 갱신된 리스트
# -out: error = 없음
#------------------------------------------------------------------
def set_grade(seeds, file, grade):
    out = []
    for s in seeds:
        if s.get("file") == file:
            s = dict(s)
            s["grade"] = grade
        out.append(s)
    return out
