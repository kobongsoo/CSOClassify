#------------------------------------------------------------------
# 임베딩 라벨 전파(Signal B) 단위 테스트
#=> propagate/ SeedIndex/ propagate_records 가 near-dup 상속·k-NN 다수결·보류
#   (too_far/mixed)·상향전용 융합을 올바로 수행하는지 검증한다.
#   모델 없이 합성 4차원 벡터로만 돈다(임베딩 실행 불필요).
#------------------------------------------------------------------

import json

import pytest

from csoclassify.classify import SeedIndex, propagate, propagate_records


#------------------------------------------------------------------
# rule 신호 dict 헬퍼
#=> 재융합 테스트용으로 저장 신호 형태의 dict 를 만든다.
#
# -in: grade = 등급/None, seed = seed_eligible
# -out: dict
# -out: error = 없음
#------------------------------------------------------------------
def _rule(grade, seed=False):
    return {"grade": grade, "confidence": 0.85 if grade else 0.0,
            "seed_eligible": seed, "hits": []}


#------------------------------------------------------------------
# 등급별 seed 레코드 묶음
#=> C/S/O 군집 seed 를 4차원 축에 배치한 레코드 리스트를 만든다.
#
# -in: 없음
# -out: list[dict]
# -out: error = 없음
#------------------------------------------------------------------
def _seed_records():
    return [
        {"file": "c1", "grade": "C", "seed_eligible": True, "vector": [1.0, 0.0, 0.0, 0.0]},
        {"file": "c2", "grade": "C", "seed_eligible": True, "vector": [0.9, 0.1, 0.0, 0.0]},
        {"file": "s1", "grade": "S", "seed_eligible": True, "vector": [0.0, 1.0, 0.0, 0.0]},
        {"file": "s2", "grade": "S", "seed_eligible": True, "vector": [0.1, 0.9, 0.0, 0.0]},
        {"file": "o1", "grade": "O", "seed_eligible": True, "vector": [0.0, 0.0, 1.0, 0.0]},
    ]


#------------------------------------------------------------------
# seed 인덱스 구성 필터
#=> seed_eligible=False 나 등급 없음/벡터 없음은 seed 에서 빠져야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_seed_index_filter():
    recs = _seed_records() + [
        {"file": "x", "grade": "C", "seed_eligible": False, "vector": [1, 0, 0, 0]},  # 저신뢰
        {"file": "y", "grade": None, "seed_eligible": True, "vector": [1, 0, 0, 0]},   # 등급없음
        {"file": "z", "grade": "S", "seed_eligible": True},                            # 벡터없음
    ]
    idx = SeedIndex.from_records(recs)
    assert idx.size == 5   # 원래 5개만


#------------------------------------------------------------------
# near-duplicate 등급 상속
#=> seed 와 거의 동일한 벡터는 그 등급을 그대로 상속한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_dup_inherit():
    idx = SeedIndex.from_records(_seed_records())
    sig = propagate([1.0, 0.0, 0.0, 0.0], idx)
    assert sig.grade == "C"
    assert sig.method == "dup_inherit"
    assert sig.seed_eligible is False   # 전파 등급은 새 seed 로 안 씀


#------------------------------------------------------------------
# k-NN 다수결
#=> C 군집 근처 벡터는 C 후보를 받는다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_knn_vote():
    idx = SeedIndex.from_records(_seed_records())
    sig = propagate([0.8, 0.2, 0.0, 0.0], idx)
    assert sig.grade == "C"
    assert sig.method in ("knn_vote", "dup_inherit")


#------------------------------------------------------------------
# 너무 먼 이웃 → 보류
#=> 모든 seed 와 직교(유사도 0)인 벡터는 등급을 못 받고 보류된다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_too_far():
    idx = SeedIndex.from_records(_seed_records())
    sig = propagate([0.0, 0.0, 0.0, 1.0], idx)
    assert sig.grade is None
    assert sig.method == "too_far"


#------------------------------------------------------------------
# 이웃이 섞이면 → 보류
#=> C·S 사이 중간 벡터는 승자 득표율이 낮아 보류된다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_mixed():
    idx = SeedIndex.from_records(_seed_records())
    sig = propagate([0.707, 0.707, 0.0, 0.0], idx)
    assert sig.grade is None
    assert sig.method == "mixed"


#------------------------------------------------------------------
# seed 가 없으면 → no_seeds
#=> seed 인덱스가 비면 전파는 항상 None.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_no_seeds():
    idx = SeedIndex.from_records([])
    sig = propagate([1, 0, 0, 0], idx)
    assert sig.grade is None and sig.method == "no_seeds"


#------------------------------------------------------------------
# 이웃에 seed 문서 경로가 실린다(감사용)
#=> as_dict().neighbors 각 항목에 file/grade/sim 이 있어, 어느 문서와 비슷해
#   이 등급이 됐는지 추적할 수 있어야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_neighbors_include_file():
    idx = SeedIndex.from_records(_seed_records())
    sig = propagate([0.8, 0.2, 0.0, 0.0], idx)
    d = sig.as_dict()
    assert d["neighbors"], "이웃 목록이 비어있으면 안 됨"
    n0 = d["neighbors"][0]
    assert set(n0) == {"file", "grade", "sim"}
    assert n0["file"] in {"c1", "c2", "s1", "s2", "o1"}


#------------------------------------------------------------------
# 배치 전파 — 미분류 문서 구제
#=> C seed 근처의 미분류(grade None) 문서가 전파로 C 가 되고, decided_by 에 embed.
#   전파 등급은 seed_eligible=False 여야 한다(재seed 방지).
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_propagate_records_rescue():
    recs = _seed_records() + [{
        "file": "u1", "grade": None, "seed_eligible": False,
        "vector": [0.95, 0.05, 0.0, 0.0],
        "signals": {"rule": _rule(None), "path": {"grade": None, "acl_restricted": False},
                    "name": {"grade": None}},
    }]
    records, stats = propagate_records(recs)
    u1 = next(r for r in records if r["file"] == "u1")
    assert u1["grade"] == "C"
    assert "embed" in u1["decided_by"]
    assert u1["seed_eligible"] is False
    assert stats["seeds"] == 5
    assert stats["embed_decided"] >= 1


#------------------------------------------------------------------
# 배치 전파 — 이미 등급 있는 문서는 건드리지 않음(보류만 대상)
#=> 규칙이 S 로 확정한 문서는, 벡터가 C·O seed 근처여도 전파 대상에서 제외되어
#   등급이 그대로 유지되고 embed 신호도 붙지 않는다(과분류·자기매칭 방지).
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_propagate_skips_already_graded():
    recs = _seed_records() + [{
        "file": "d1", "grade": "S", "seed_eligible": False,
        "vector": [1.0, 0.0, 0.0, 0.0],   # C seed 와 동일 방향이지만 이미 등급 있음
        "signals": {"rule": _rule("S"), "path": {"grade": None, "acl_restricted": False},
                    "name": {"grade": None}},
    }]
    records, stats = propagate_records(recs)
    d1 = next(r for r in records if r["file"] == "d1")
    assert d1["grade"] == "S"               # 이미 등급 있어 임베딩이 못 올림(그대로)
    assert "embed" not in d1["signals"]     # 전파 대상 아님 → embed 신호 없음
    assert stats["already_graded"] >= 1


#------------------------------------------------------------------
# 보류 문서 픽스처(외부 seed 테스트용)
#=> C 씨앗 근처에 있는, 아직 미분류(None)인 레코드 하나를 만든다.
#
# -in: 없음
# -out: dict = 보류 레코드
# -out: error = 없음
#------------------------------------------------------------------
def _boru_rec():
    return {"file": "u", "grade": None, "seed_eligible": False,
            "vector": [0.98, 0.02, 0.0, 0.0],
            "signals": {"rule": _rule(None),
                        "path": {"grade": None, "acl_restricted": False},
                        "name": {"grade": None}}}


#------------------------------------------------------------------
# 외부 seed 파일 로드(STEP 1)
#=> class_seed.jsonl 을 읽어, 등급+벡터만 있으면(seed_eligible 불필요) 씨앗으로 담고
#   벡터/등급 없는 줄은 제외하는지 확인한다.
#
# -in: tmp_path = pytest 임시폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_from_seed_file(tmp_path):
    p = tmp_path / "class_seed.jsonl"
    lines = [
        {"file": "s_c", "grade": "C", "vector": [1, 0, 0, 0]},  # seed_eligible 없어도 포함
        {"file": "s_o", "grade": "O", "vector": [0, 0, 1, 0]},
        {"file": "nov", "grade": "C"},                          # 벡터 없음 → 제외
        {"file": "nog", "vector": [0, 1, 0, 0]},                # 등급 없음 → 제외
    ]
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    idx = SeedIndex.from_seed_file(str(p))
    assert idx.size == 2
    assert set(idx.grades) == {"C", "O"}


#------------------------------------------------------------------
# 외부 seed_index 로 보류 문서 구제(STEP 1 핵심)
#=> 코퍼스 내부엔 씨앗이 없어도, 외부 seed_index 를 주면 그 기준으로 전파된다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_propagate_external_seed_index():
    ext = SeedIndex.from_records([
        {"file": "seed_c", "grade": "C", "seed_eligible": True, "vector": [1.0, 0.0, 0.0, 0.0]}])
    records, stats = propagate_records([_boru_rec()], seed_index=ext)
    assert records[0]["grade"] == "C"     # 외부 seed 로 구제됨
    assert stats["seeds"] == 1


#------------------------------------------------------------------
# 외부 seed + 내부 seed 병합 전파(사용자 정책)
#=> 외부 seed_index 를 줘도 코퍼스 내부 seed(seed_eligible 문서)와 "합쳐서" 비교한다.
#   내부 C seed 근처 보류는 내부로, 외부 S seed 근처 보류는 외부로 각각 구제된다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_propagate_merges_external_and_internal():
    ext = SeedIndex.from_records([
        {"file": "ext_s", "grade": "S", "seed_eligible": True, "vector": [0.0, 1.0, 0.0, 0.0]}])
    records = [
        {"file": "in_c", "grade": "C", "seed_eligible": True, "vector": [1.0, 0.0, 0.0, 0.0]},
        {"file": "b_c", "grade": None, "seed_eligible": False, "vector": [0.98, 0.02, 0.0, 0.0],
         "signals": {"rule": _rule(None), "path": {"grade": None, "acl_restricted": False},
                     "name": {"grade": None}}},
        {"file": "b_s", "grade": None, "seed_eligible": False, "vector": [0.02, 0.98, 0.0, 0.0],
         "signals": {"rule": _rule(None), "path": {"grade": None, "acl_restricted": False},
                     "name": {"grade": None}}},
    ]
    out, stats = propagate_records(records, seed_index=ext)
    g = {o["file"]: o["grade"] for o in out}
    assert stats["seeds"] == 2          # 외부1 + 내부1 을 합침
    assert g["b_c"] == "C"              # 내부 seed 로 구제
    assert g["b_s"] == "S"              # 외부 seed 로 구제


#------------------------------------------------------------------
# 외부 seed 없고 코퍼스에도 씨앗 없으면 → 구제 안 됨(대조)
#=> seed_index 미지정 + 코퍼스 내부 seed 0 → 보류 그대로.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_propagate_without_seeds_not_rescued():
    records, stats = propagate_records([_boru_rec()])
    assert records[0]["grade"] is None
    assert stats["seeds"] == 0
