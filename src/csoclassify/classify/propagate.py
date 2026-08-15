#------------------------------------------------------------------
# 임베딩 라벨 전파 (Signal B) — 벡터 이웃으로 등급 후보 만들기
#=> 이미 등급이 붙은 고신뢰 문서(seed)의 라벨을, 벡터 공간에서 가까운 미분류
#   문서로 옮긴다. 출처 흔적이 약한 수집 코퍼스의 주 엔진이다.
#    - near-duplicate(코사인≈1.0): seed 등급을 그대로 상속(사본 대량 정리)
#    - 그 외: k 최근접 seed 의 유사도 가중 다수결로 "후보 등급"
#    - 이웃이 멀거나(신뢰 부족) 흩어지면: 등급 None → 검토 큐로
#   [원칙] 전파 결과는 "후보"일 뿐 상향·확인에만 쓰고, 임베딩만으로 하향하지 않는다
#   (융합이 max 라 자동 보장). 전파로 붙은 등급은 다시 seed 로 쓰지 않는다(drift 방지).
#------------------------------------------------------------------

import json
import os
from dataclasses import dataclass, field

import numpy as np

# ============================================================================
# 전파 임계값 — "얼마나 비슷해야 등급을 옮길지" 기준 (운영 중 튜닝 대상)
# ----------------------------------------------------------------------------
# 유사도(sim)는 코사인 값 0.0~1.0. 1.0=사실상 같은 문서, 0.6쯤=주제 비슷, 0.0=무관.
#
#   DEFAULT_K     : 가장 비슷한 seed(이미 등급 붙은 문서) 몇 개를 "투표인단"으로 볼지.
#   DUP_THRESHOLD : 1등이 이 값 이상이면 "거의 사본"으로 보고 투표 없이 등급 즉시 상속.
#                     └ 올리면 더 엄격(웬만해선 사본으로 안 봄)
#   MIN_SIM       : 1등조차 이 값보다 낮으면 "쓸만한 이웃 없음" → 보류(등급 안 붙임).
#                     └ 올리면 더 보수적(어지간히 비슷해야 등급을 줌)
#   MIN_SHARE     : 투표에서 1등 등급의 "득표율"이 이 값 미만이면 "이웃 의견 갈림"
#                   → 보류. (평균/절대유사도가 아니라 '비율'임에 주의)
#                     · 각 등급의 표 = 그 등급 이웃들의 유사도 합(개수 아님, 가까울수록 큰 표)
#                     · 득표율 = (1등 등급 이웃들의 sim 합) ÷ (전체 이웃 sim 합)
#                     예) 10개 중 C합=3.83, S합=1.81, O합=0.57 → 전체 6.21
#                         → C 득표율 = 3.83/6.21 = 0.617 ≥ 0.60 → C 채택
#                     └ 올리면 더 보수적(이웃들이 확실히 한 등급으로 몰려야 인정)
#
# 보류(None)로 끝난 문서는 다른 신호(규칙/경로)가 없으면 "검토 큐" 대상이 된다.
# ============================================================================
DEFAULT_K = 10           # 참고할 최근접 seed 개수(투표인단 크기)
DUP_THRESHOLD = 0.950    # 1등 sim ≥ 0.950 → 사본 수준 → 그 등급 즉시 상속
MIN_SIM = 0.55           # 1등 sim < 0.55  → 이웃이 너무 멂 → 보류
MIN_SHARE = 0.70         # 1등 득표율 < 0.70 → 이웃 의견 갈림 → 보류


#------------------------------------------------------------------
# 전파 신호(Signal B) 결과
#=> 벡터 전파로 얻은 후보 등급과 그 근거(방식·유사도·이웃)를 담는다.
#
# -필드: grade         = 후보 등급(C/S/O) 또는 None(보류/검토 큐)
# -필드: confidence    = 신뢰도(near-dup=유사도, 다수결=승자 득표율)
# -필드: seed_eligible = 항상 False (전파 등급은 새 seed 로 쓰지 않음)
# -필드: method        = "dup_inherit"|"knn_vote"|"too_far"|"mixed"|"no_seeds"
# -필드: top_sim       = 최근접 seed 와의 코사인 유사도
# -필드: neighbors     = 상위 이웃 [(file, grade, sim)] — 어느 seed 문서와 비슷해
#                        이 등급이 됐는지 감사할 수 있게 seed 파일 경로를 남긴다.
#------------------------------------------------------------------
@dataclass(frozen=True)
class EmbedSignal:
    grade: str
    confidence: float
    seed_eligible: bool
    method: str
    top_sim: float
    neighbors: list = field(default_factory=list)

    #------------------------------------------------------------------
    # 직렬화용 dict
    #=> 분류 레코드의 signals.embed 칸에 넣을 순수 dict. neighbors 에 seed 문서
    #   경로를 실어, "이 문서가 왜 이 등급인지"를 근거 문서까지 추적할 수 있게 한다.
    #
    # -in: 없음
    # -out: dict = {grade, confidence, seed_eligible, method, top_sim,
    #               neighbors:[{file, grade, sim}]}
    # -out: error = 없음
    #------------------------------------------------------------------
    def as_dict(self):
        return {
            "grade": self.grade,
            "confidence": round(self.confidence, 3),
            "seed_eligible": self.seed_eligible,
            "method": self.method,
            "top_sim": round(self.top_sim, 3),
            "neighbors": [
                {"file": f, "grade": g, "sim": round(s, 3)}
                for f, g, s in self.neighbors
            ],
        }


#------------------------------------------------------------------
# seed 인덱스(라벨 씨앗 모음)
#=> 등급이 붙은 seed 문서들의 (정규화 벡터, 등급)을 담아 코사인 검색을 제공한다.
#
# -필드: vectors = (n, d) L2 정규화된 seed 벡터 행렬
# -필드: grades  = 각 seed 의 등급 리스트(vectors 행과 1:1)
# -필드: files   = 각 seed 의 파일 경로 리스트(감사·이웃 추적용)
# -필드: size    = seed 개수
#------------------------------------------------------------------
class SeedIndex:

    #------------------------------------------------------------------
    # 생성자
    #=> 정규화된 벡터 행렬·등급·파일 경로를 받아 보관한다.
    #
    # -in: vectors = numpy (n,d) 정규화 벡터
    # -in: grades  = 등급 문자열 리스트(len=n)
    # -in: files   = seed 파일 경로 리스트(len=n, 없으면 빈 리스트)
    #
    # -out: 없음
    # -out: error = 없음
    #------------------------------------------------------------------
    def __init__(self, vectors, grades, files=None):
        self.vectors = vectors
        self.grades = grades
        self.files = files if files is not None else []
        self.size = len(grades)

    #------------------------------------------------------------------
    # 레코드들로부터 seed 인덱스 구성
    #=> 분류 레코드 중 "벡터가 있고 + 등급이 C/S/O + seed_eligible=True" 인 것만
    #   골라 seed 로 삼는다(고신뢰만 씨앗). 벡터는 행별 L2 정규화하고, 감사에 쓰도록
    #   각 seed 의 파일 경로도 함께 보관한다.
    #
    # -in: records = 분류 레코드 dict 리스트(각 rec 는 'file','vector','grade','seed_eligible')
    #
    # -out: SeedIndex (해당 없으면 size=0)
    # -out: error = 없음
    #------------------------------------------------------------------
    #------------------------------------------------------------------
    # 리스트들로 인덱스 구성(공통 내부)
    #=> 벡터/등급/경로 리스트를 받아 행별 L2 정규화한 SeedIndex 를 만든다.
    #   from_records/from_seed_file 가 공유한다.
    #
    # -in: vecs/grades/files = 각 seed 의 벡터·등급·경로 리스트(길이 동일)
    #
    # -out: SeedIndex (비었으면 size=0)
    # -out: error = 없음
    #------------------------------------------------------------------
    @classmethod
    def _from_lists(cls, vecs, grades, files):
        if not vecs:
            return cls(np.zeros((0, 0), dtype=np.float32), [], [])
        arr = np.asarray(vecs, dtype=np.float32)
        # 코사인 계산을 위해 각 seed 벡터를 단위벡터로 만든다(0 벡터는 그대로 둠).
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return cls(arr / norms, grades, files)

    #------------------------------------------------------------------
    # 분류 레코드들로부터 seed 인덱스 구성(코퍼스 내부 seed)
    #=> grades 결과 안에서 "고신뢰(seed_eligible)" 로 판정된 것만 씨앗으로 쓴다.
    #   외부 큐레이션 seed 가 없을 때의 폴백 경로.
    #
    # -in: records = 분류 레코드 dict 리스트
    #
    # -out: SeedIndex (해당 없으면 size=0)
    # -out: error = 없음
    #------------------------------------------------------------------
    @classmethod
    def from_records(cls, records):
        vecs, grades, files = [], [], []
        for r in records:
            v = r.get("vector")
            g = r.get("grade")
            # 고신뢰(seed_eligible) + 유효 등급 + 벡터 존재만 씨앗으로.
            if v and g in ("C", "S", "O") and r.get("seed_eligible"):
                vecs.append(v)
                grades.append(g)
                files.append(r.get("file"))
        return cls._from_lists(vecs, grades, files)

    #------------------------------------------------------------------
    # 외부 seed 저장소(cso_seed.jsonl)로부터 인덱스 구성 — STEP 1 핵심
    #=> 관리자가 큐레이션한 seed 파일을 읽어 인덱스로 만든다. 이미 사람이 승인한
    #   씨앗이므로 seed_eligible 조건을 요구하지 않고, 등급+벡터만 있으면 담는다.
    #   Phase 3 분류가 "검증된 기준"과 비교하도록 하는 진입점.
    #
    # -in: path = cso_seed.jsonl 경로(각 줄: {file, grade, vector, ...})
    #
    # -out: SeedIndex (파일 없음/빈 파일이면 size=0)
    # -out: error = 깨진 줄은 건너뜀(예외 없음)
    #------------------------------------------------------------------
    @classmethod
    def from_seed_file(cls, path):
        vecs, grades, files = [], [], []
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    v = e.get("vector")
                    g = e.get("grade")
                    # 큐레이션된 seed → seed_eligible 불필요. 등급+벡터만 확인.
                    if v and g in ("C", "S", "O"):
                        vecs.append(v)
                        grades.append(g)
                        files.append(e.get("file"))
        return cls._from_lists(vecs, grades, files)

    #------------------------------------------------------------------
    # 두 seed 인덱스 병합
    #=> 외부 큐레이션 seed(cso_seed.jsonl)와 코퍼스 내부 seed(seed_eligible 문서)를
    #   하나의 비교 기준으로 합친다. 보류 문서를 "외부 기준 + 내부 기준" 양쪽과
    #   비교해 전파하기 위함. 두 인덱스의 벡터는 이미 행별 정규화돼 있어 그대로 이어붙임.
    #
    # -in: a, b = SeedIndex (한쪽이 비어(size=0)있으면 나머지를 그대로 반환)
    #
    # -out: SeedIndex = 합쳐진 인덱스(벡터/등급/경로 연결)
    # -out: error = 없음
    #------------------------------------------------------------------
    @classmethod
    def merge(cls, a, b):
        if a is None or a.size == 0:
            return b
        if b is None or b.size == 0:
            return a
        vecs = np.vstack([a.vectors, b.vectors])
        return cls(vecs, list(a.grades) + list(b.grades), list(a.files) + list(b.files))

    #------------------------------------------------------------------
    # 질의 벡터와 모든 seed 의 코사인 유사도
    #=> 입력 벡터를 정규화한 뒤 seed 행렬과 내적해 (n,) 유사도를 낸다.
    #
    # -in: vec = 질의 문서 벡터(리스트/ndarray)
    #
    # -out: ndarray = 각 seed 와의 코사인 유사도(seed 없으면 빈 배열)
    # -out: error = 없음
    #------------------------------------------------------------------
    def cosine(self, vec):
        if self.size == 0:
            return np.zeros((0,), dtype=np.float32)
        v = np.asarray(vec, dtype=np.float32)
        n = np.linalg.norm(v)
        n = n if n else 1.0
        return self.vectors @ (v / n)


#------------------------------------------------------------------
# 벡터 1개 전파(핵심) — "이 문서와 비슷한 seed 로 등급 후보 만들기"
#=> 질의 문서의 벡터를 이미 등급이 붙은 seed 들과 비교해 등급 "후보"를 만든다.
#
#   [판정 순서 — 위에서부터 먼저 걸리는 것 하나만 적용]
#     ① seed 없음 / 벡터 없음                         → 보류(no_seeds)   · 등급 없음
#     ② 최근접 sim ≥ DUP_THRESHOLD(0.985)             → 상속(dup_inherit) · 그 등급
#     ③ 최근접 sim <  MIN_SIM(0.55)                   → 보류(too_far)    · 등급 없음
#     ④ 그 외(0.55~0.985): 상위 K 유사도 가중 다수결
#          · 1등 득표율 ≥ MIN_SHARE(0.60)             → 채택(knn_vote)   · 1등 등급
#          · 1등 득표율 <  MIN_SHARE                  → 보류(mixed)      · 등급 없음
#
#   ※ 여기서 나온 등급은 어디까지나 "후보"다. 실제 상향 여부는 융합(fuse)에서
#     max(규칙, 경로, 파일명, 임베딩) 로 정해진다 → 기존 등급보다 "높을 때만" 상향되고
#     낮으면 무시된다(임베딩만으로는 절대 하향 안 함). 보류(None)면 다른 신호가
#     없을 때 검토 큐로 간다.
#     예) 규칙·경로·파일명이 모두 없고(=null) 임베딩 후보가 C → 최종 C (null→C 상향)
#
# -in: vec   = 질의 문서 벡터
# -in: seeds = SeedIndex(등급 붙은 seed 문서들의 벡터/등급/경로)
# -in: k/dup_threshold/min_sim/min_share = 임계값(기본은 위 모듈 상수)
#
# -out: EmbedSignal (grade=None 이면 보류=검토 큐 대상)
# -out: error = 없음
#------------------------------------------------------------------
def propagate(vec, seeds, k=DEFAULT_K, dup_threshold=DUP_THRESHOLD,
              min_sim=MIN_SIM, min_share=MIN_SHARE):
    # ① 씨앗이 없거나 이 문서 벡터가 없으면 비교 자체가 불가 → 보류.
    if vec is None or seeds is None or seeds.size == 0:
        return EmbedSignal(None, 0.0, False, "no_seeds", 0.0, [])

    sims = seeds.cosine(vec)
    # 유사도 내림차순 상위 k 개 인덱스.
    order = np.argsort(-sims)[:k]
    # 이웃마다 (어느 seed 문서, 등급, 유사도)를 함께 담아 감사에 쓴다.
    files = seeds.files
    top = [
        (files[i] if i < len(files) else None, seeds.grades[i], float(sims[i]))
        for i in order
    ]
    top1_file, top1_grade, top1_sim = top[0]
    neigh = top[:5]

    # ② 1등이 사본 수준(≥DUP_THRESHOLD)이면 투표 없이 그 등급을 그대로 상속.
    if top1_sim >= dup_threshold:
        return EmbedSignal(top1_grade, top1_sim, False, "dup_inherit", top1_sim, neigh)

    # ③ 1등조차 너무 멀면(<MIN_SIM) 믿을 이웃이 없다 → 보류.
    if top1_sim < min_sim:
        return EmbedSignal(None, top1_sim, False, "too_far", top1_sim, neigh)

    # ④ 상위 K 유사도 가중 다수결: 각 등급 버킷에 "유사도 만큼" 표를 준다
    #    (가까운 이웃일수록 더 큰 표). 그래서 단순 개수가 아니라 가중 득표다.
    buckets = {}
    for _f, g, s in top:
        buckets[g] = buckets.get(g, 0.0) + max(s, 0.0)
    total = sum(buckets.values()) or 1.0
    winner = max(buckets, key=buckets.get)        # 표를 가장 많이 받은 등급
    share = buckets[winner] / total               # 그 등급의 득표율(0~1)

    # ④-a 1등 득표율이 낮으면 이웃 의견이 갈린 것(예: C반 S반) → 보류.
    if share < min_share:
        return EmbedSignal(None, share, False, "mixed", top1_sim, neigh)

    # ④-b 이웃들이 한 등급으로 충분히 몰림 → 그 등급을 후보로 채택.
    return EmbedSignal(winner, share, False, "knn_vote", top1_sim, neigh)
