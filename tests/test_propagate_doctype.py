#------------------------------------------------------------------
# 업무분류(doctype) 축 임베딩 전파(Signal B) 단위 테스트 — 설계서 §5-4, 로드맵 D7
#=> DoctypeSeedIndex/propagate_doctype 이 near-dup 전체상속·라벨별 독립점수를
#   올바로 계산하는지, engine.propagate_doctype_records 가 규칙 기반 후보와
#   embed 후보를 "교체가 아니라 추가"로 병합하는지 검증한다. 모델 없이 합성
#   4차원 벡터로만 돈다(test_propagate.py 와 같은 방식).
#------------------------------------------------------------------

import json

from csoclassify.classify import DoctypeSeedIndex, propagate_doctype, propagate_doctype_records


#------------------------------------------------------------------
# doctype seed 레코드 묶음(4차원 축에 배치)
#=> A 라벨 군집(축0)·B 라벨 군집(축1)·A+B 동시 라벨(축0·1 중간)을 만든다.
#
# -in: 없음
# -out: list[dict] = {file, labels:{doctype:[...]}, vector}
# -out: error = 없음
#------------------------------------------------------------------
def _seed_entries():
    return [
        {"file": "a1", "labels": {"doctype": ["A"]}, "vector": [1.0, 0.0, 0.0, 0.0]},
        {"file": "a2", "labels": {"doctype": ["A"]}, "vector": [0.95, 0.05, 0.0, 0.0]},
        {"file": "b1", "labels": {"doctype": ["B"]}, "vector": [0.0, 1.0, 0.0, 0.0]},
        {"file": "b2", "labels": {"doctype": ["B"]}, "vector": [0.0, 0.95, 0.05, 0.0]},
        {"file": "ab1", "labels": {"doctype": ["A", "B"]}, "vector": [0.707, 0.707, 0.0, 0.0]},
    ]


#------------------------------------------------------------------
# 위 seed 를 파일로 써서 DoctypeSeedIndex 로 로드
#
# -in: tmp_path = pytest 임시폴더
# -out: DoctypeSeedIndex
# -out: error = 없음
#------------------------------------------------------------------
def _mk_index(tmp_path):
    p = tmp_path / "class_seed.jsonl"
    p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in _seed_entries()),
                 encoding="utf-8")
    return DoctypeSeedIndex.from_seed_file(str(p))


# ── DoctypeSeedIndex.from_seed_file ──────────────────────────────

#------------------------------------------------------------------
# labels.doctype 없는/grade 만 있는 줄은 제외된다
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_from_seed_file은_doctype_라벨만_담는다(tmp_path):
    p = tmp_path / "class_seed.jsonl"
    lines = [
        {"file": "a", "labels": {"doctype": ["DC1"]}, "vector": [1, 0, 0, 0]},
        {"file": "b", "grade": "S", "vector": [0, 1, 0, 0]},                  # security 전용, 제외
        {"file": "c", "labels": {"doctype": []}, "vector": [0, 0, 1, 0]},     # 빈 라벨, 제외
        {"file": "d", "labels": {"doctype": ["DC2"]}},                       # 벡터 없음, 제외
    ]
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    idx = DoctypeSeedIndex.from_seed_file(str(p))
    assert idx.size == 1
    assert idx.label_sets[0] == ("DC1",)


#------------------------------------------------------------------
# 파일이 없으면 빈 인덱스
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_파일없으면_빈인덱스():
    idx = DoctypeSeedIndex.from_seed_file("D:/no/such/file.jsonl")
    assert idx.size == 0


# ── propagate_doctype ─────────────────────────────────────────────

#------------------------------------------------------------------
# seed 없으면 no_seeds
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_seed없으면_no_seeds(tmp_path):
    idx = DoctypeSeedIndex.from_seed_file(str(tmp_path / "없음.jsonl"))
    sig = propagate_doctype([1, 0, 0, 0], idx)
    assert sig.values == () and sig.method == "no_seeds"


#------------------------------------------------------------------
# 너무 먼 이웃 → too_far
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_너무_멀면_too_far(tmp_path):
    idx = _mk_index(tmp_path)
    sig = propagate_doctype([0.0, 0.0, 0.0, 1.0], idx)
    assert sig.values == () and sig.method == "too_far"


#------------------------------------------------------------------
# near-dup 이면 그 seed 의 라벨 집합 전체를 상속한다(설계서 5-4 — "라벨 집합 전체 상속")
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_near_dup은_라벨_집합_전체_상속(tmp_path):
    idx = _mk_index(tmp_path)
    sig = propagate_doctype([0.707, 0.707, 0.0, 0.0], idx)   # ab1 과 거의 동일
    assert sig.method == "dup_inherit"
    dc_ids = {v["dc_id"] for v in sig.values}
    assert dc_ids == {"A", "B"}


#------------------------------------------------------------------
# 한쪽 라벨 근처면 그 라벨만 채택된다(단일 라벨 승리)
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_한쪽_라벨_근처는_그_라벨만():
    import csoclassify.classify as C
    idx_entries = [
        {"file": "a1", "labels": {"doctype": ["A"]}, "vector": [1.0, 0.0, 0.0, 0.0]},
        {"file": "a2", "labels": {"doctype": ["A"]}, "vector": [0.9, 0.1, 0.0, 0.0]},
        {"file": "b1", "labels": {"doctype": ["B"]}, "vector": [0.0, 0.0, 1.0, 0.0]},
    ]
    import numpy as np
    vecs = [e["vector"] for e in idx_entries]
    label_sets = [tuple(e["labels"]["doctype"]) for e in idx_entries]
    files = [e["file"] for e in idx_entries]
    idx = C.DoctypeSeedIndex._from_lists(vecs, label_sets, files)
    sig = propagate_doctype([0.8, 0.2, 0.0, 0.0], idx, min_share=0.5)
    dc_ids = {v["dc_id"] for v in sig.values}
    assert dc_ids == {"A"}


#------------------------------------------------------------------
# 라벨별 독립 점수 — 임계값을 넘는 라벨을 전부 채택한다(다중 라벨 본질)
#=> A·B 두 라벨이 이웃에 고르게 섞이면(예: ab1 근처) 둘 다 채택될 수 있다
#   (min_share 를 낮게 주면 둘 다 넘는다).
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_라벨별_독립점수_둘다_넘으면_둘다_채택(tmp_path):
    idx = _mk_index(tmp_path)
    # ab1 벡터 근처지만 dup_threshold 미만이 되도록 살짝 비틀어 knn_vote 경로를 타게 한다.
    sig = propagate_doctype([0.75, 0.65, 0.0, 0.0], idx, dup_threshold=0.999, min_share=0.15)
    dc_ids = {v["dc_id"] for v in sig.values}
    assert "A" in dc_ids and "B" in dc_ids
    assert sig.method == "knn_vote"


#------------------------------------------------------------------
# 신뢰도는 0~1 사이이고, 각 라벨의 confidence 는 실제로 min_share 이상이다
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_채택된_라벨의_confidence는_min_share_이상(tmp_path):
    idx = _mk_index(tmp_path)
    sig = propagate_doctype([0.75, 0.65, 0.0, 0.0], idx, dup_threshold=0.999, min_share=0.15)
    for v in sig.values:
        assert 0.15 <= v["confidence"] <= 1.0


# ── 근거 왕복(signals) ───────────────────────────────────────────

#------------------------------------------------------------------
# as_dict 로 적은 근거를 다시 읽어 전파를 돌려도 근거가 살아남는다
#=> 2026-09-10 부터 근거는 signals 한 칸으로 합쳐 적는다. 전파는 레코드를
#   다시 읽어 병합하므로, 되읽기가 깨지면 전파를 한 번 거친 문서만
#   "왜 이 라벨인지"를 통째로 잃는다 — 조용히 일어나서 더 나쁘다.
#------------------------------------------------------------------
def test_signals_로_적은_근거가_전파_왕복에서_살아남는다():
    from csoclassify.classify import doctype as DT
    from csoclassify.classify import doc_rules as D
    from csoclassify.classify import axes as A
    from csoclassify.classify.propagate import DoctypeEmbedSignal

    taxonomy = A.Taxonomy("t", "20260824000000", 1, [
        A.TaxonomyNode(dc_id="CONTRACT", parent=None, order=1, title="계약서", status=1)])
    drs = D.DocRuleSet(
        conflict=D.ConflictSpec(strategy="all"), version="doc-test-1",
        rules=(D.DoctypeRule(id="r", node="CONTRACT", weight="high",
                             title_terms=("계약서",), terms=("계약서",)),))
    sig = DT.scan_doctype("계약서\n본 계약서는 유효하다.", "D:/x/용역_계약서.hwp",
                          drs, taxonomy)
    written = sig.as_dict()["values"]
    assert "signals" in written[0] and "evidence" not in written[0]

    # 레코드에 적힌 그 모양 그대로 다시 읽혀 병합에 들어간다.
    result = DT.merge_embed_candidates(written, DoctypeEmbedSignal(),
                                       taxonomy, D.ConflictSpec(strategy="all"))
    again = result.as_dict()["values"][0]
    assert again["signals"] == written[0]["signals"]


#------------------------------------------------------------------
# 옛 결과 파일(evidence·score_parts 두 칸)도 그대로 읽힌다
#=> 화면과 전파가 옛 파일을 못 읽으면 그동안 쌓인 이력이 사라진다.
#------------------------------------------------------------------
def test_옛_두칸_근거도_읽힌다():
    from csoclassify.classify import doctype as DT

    ev, parts = DT._split_signals({
        "evidence": {"title": {"terms": [{"term": "계약서", "count": 1}]}},
        "score_parts": [{"signal": "title", "c": 0.8}]})
    assert ev["title"]["terms"][0]["term"] == "계약서"
    assert parts == [{"signal": "title", "c": 0.8}]


# ── merge_embed_candidates (doctype.py) ──────────────────────────

def _mk_taxonomy():
    from csoclassify.classify import axes as A
    return A.Taxonomy("t", "20260824000000", 3, [
        A.TaxonomyNode(dc_id="ROOT", parent=None, order=1, title="루트", status=1),
        A.TaxonomyNode(dc_id="LEAF1", parent="ROOT", order=1, title="잎1", status=1),
        A.TaxonomyNode(dc_id="LEAF2", parent="ROOT", order=2, title="잎2", status=1),
    ])


#------------------------------------------------------------------
# embed 후보가 새 노드를 추가한다(기존 규칙 후보를 빼앗지 않음)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_merge_embed는_새_후보를_추가한다():
    from csoclassify.classify import doctype as DT
    from csoclassify.classify import doc_rules as D
    from csoclassify.classify.propagate import DoctypeEmbedSignal

    taxonomy = _mk_taxonomy()
    existing = ({"dc_id": "LEAF1", "path": taxonomy.path("LEAF1"),
                "path_ids": taxonomy.path_ids("LEAF1"), "confidence": 0.8, "from": ("rule",)},)
    esig = DoctypeEmbedSignal(values=({"dc_id": "LEAF2", "confidence": 0.4},), method="knn_vote")

    result = DT.merge_embed_candidates(existing, esig, taxonomy, D.ConflictSpec(strategy="all"))
    dc_ids = {v["dc_id"] for v in result.values}
    assert dc_ids == {"LEAF1", "LEAF2"}
    leaf2 = next(v for v in result.values if v["dc_id"] == "LEAF2")
    assert leaf2["from"] == ("embed",)


#------------------------------------------------------------------
# 같은 노드를 규칙과 embed 가 둘 다 지목하면 confidence 최댓값 + from 합집합
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_merge_embed는_같은_노드를_강화한다():
    from csoclassify.classify import doctype as DT
    from csoclassify.classify import doc_rules as D
    from csoclassify.classify.propagate import DoctypeEmbedSignal

    taxonomy = _mk_taxonomy()
    existing = ({"dc_id": "LEAF1", "path": taxonomy.path("LEAF1"),
                "path_ids": taxonomy.path_ids("LEAF1"), "confidence": 0.5, "from": ("rule",)},)
    esig = DoctypeEmbedSignal(values=({"dc_id": "LEAF1", "confidence": 0.9},), method="knn_vote")

    result = DT.merge_embed_candidates(existing, esig, taxonomy, D.ConflictSpec(strategy="all"))
    assert len(result.values) == 1
    v = result.values[0]
    assert v["confidence"] == 0.9
    assert set(v["from"]) == {"rule", "embed"}


#------------------------------------------------------------------
# 조상-자손이 embed 로 함께 걸리면 조상 흡수가 재적용된다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_merge_embed_후에도_조상_흡수가_적용된다():
    from csoclassify.classify import doctype as DT
    from csoclassify.classify import doc_rules as D
    from csoclassify.classify.propagate import DoctypeEmbedSignal

    taxonomy = _mk_taxonomy()
    existing = ({"dc_id": "LEAF1", "path": taxonomy.path("LEAF1"),
                "path_ids": taxonomy.path_ids("LEAF1"), "confidence": 0.8, "from": ("rule",)},)
    esig = DoctypeEmbedSignal(values=({"dc_id": "ROOT", "confidence": 0.4},), method="knn_vote")

    result = DT.merge_embed_candidates(existing, esig, taxonomy, D.ConflictSpec(strategy="all"))
    dc_ids = {v["dc_id"] for v in result.values}
    assert dc_ids == {"LEAF1"}   # ROOT(조상)는 흡수되어 빠진다


#------------------------------------------------------------------
# taxonomy 에 없는(삭제된) dc_id 를 가리키는 embed 후보는 무시된다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_taxonomy에_없는_embed_후보는_무시():
    from csoclassify.classify import doctype as DT
    from csoclassify.classify import doc_rules as D
    from csoclassify.classify.propagate import DoctypeEmbedSignal

    taxonomy = _mk_taxonomy()
    esig = DoctypeEmbedSignal(values=({"dc_id": "삭제된노드", "confidence": 0.9},), method="knn_vote")
    result = DT.merge_embed_candidates((), esig, taxonomy, D.ConflictSpec(strategy="all"))
    assert result.values == ()


#------------------------------------------------------------------
# 규칙 후보도 embed 후보도 없으면 빈 결과
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_둘다_없으면_빈결과():
    from csoclassify.classify import doctype as DT
    from csoclassify.classify import doc_rules as D
    from csoclassify.classify.propagate import DoctypeEmbedSignal

    taxonomy = _mk_taxonomy()
    result = DT.merge_embed_candidates((), DoctypeEmbedSignal(), taxonomy, D.ConflictSpec(strategy="all"))
    assert result.values == ()


# ── engine.propagate_doctype_records ─────────────────────────────

#------------------------------------------------------------------
# labels.doctype 없는 레코드(축을 안 쓴 문서)는 건너뛴다
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_records_axis_없으면_건너뛴다(tmp_path):
    from csoclassify.classify import doc_rules as D

    taxonomy = _mk_taxonomy()
    idx = _mk_index(tmp_path)
    records = [{"file": "x", "labels": {"security": {"value": "S"}}}]
    out, stats = propagate_doctype_records(records, idx, taxonomy, D.ConflictSpec(strategy="all"))
    assert stats["axis_off"] == 1
    assert "doctype" not in out[0]["labels"]


#------------------------------------------------------------------
# 벡터 없으면 건너뛴다
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_records_벡터없으면_건너뛴다(tmp_path):
    from csoclassify.classify import doc_rules as D

    taxonomy = _mk_taxonomy()
    idx = _mk_index(tmp_path)
    records = [{"file": "x", "labels": {"doctype": {"values": [], "strategy": "all",
                                                    "status": "proposed", "truncated": 0}}}]
    out, stats = propagate_doctype_records(records, idx, taxonomy, D.ConflictSpec(strategy="all"))
    assert stats["no_vector"] == 1


#------------------------------------------------------------------
# 이미 규칙 후보가 있어도 embed 후보가 "추가"된다(교체 아님, security 와의 핵심 차이)
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_records_기존_후보에_embed가_추가된다(tmp_path):
    from csoclassify.classify import doc_rules as D

    taxonomy = _mk_taxonomy()
    p = tmp_path / "class_seed.jsonl"
    p.write_text(json.dumps({"file": "seed1", "labels": {"doctype": ["LEAF2"]},
                             "vector": [0.0, 1.0, 0.0, 0.0]}), encoding="utf-8")
    idx = DoctypeSeedIndex.from_seed_file(str(p))

    records = [{
        "file": "x",
        "vector": [0.02, 0.98, 0.0, 0.0],   # seed1 과 near-dup
        "labels": {"doctype": {
            "values": [{"dc_id": "LEAF1", "path": taxonomy.path("LEAF1"),
                       "path_ids": list(taxonomy.path_ids("LEAF1")),
                       "confidence": 0.7, "from": ["rule"]}],
            "strategy": "all", "status": "proposed", "truncated": 0,
        }},
    }]
    out, stats = propagate_doctype_records(records, idx, taxonomy, D.ConflictSpec(strategy="all"))
    # 입력은 옛 모양(labels.doctype)이다 — 옛 결과 파일을 다시 읽어 전파하는 경로.
    dc_ids = {v["dc_id"] for v in out[0]["why"]["doctype"]["values"]}
    assert dc_ids == {"LEAF1", "LEAF2"}     # 기존 LEAF1 유지 + embed 로 LEAF2 추가
    assert stats["embed_contributed"] == 1
    # [2026-09-10] 전파 신호는 그 축 안에 둔다 — 예전에는 등급 신호들과 같은
    # signals 상자에 doctype_embed 라는 이름으로 섞여 있어, 축이 둘이라는 사실이
    # 레코드 모양에서 드러나지 않았다.
    assert "embed" in out[0]["why"]["doctype"]
    assert "signals" not in out[0] and "labels" not in out[0]   # 옛 자리는 걷어낸다
