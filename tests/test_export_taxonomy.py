#------------------------------------------------------------------
# export_taxonomy.py 단위 테스트
#=> DOC_CLASSIFICATION JSON 원본을 doc_taxonomy.yaml 규약으로 바꾸는 순수
#   변환 로직(convert)과 doc_rule.yaml 골격 생성(build_doc_rule_scaffold)을
#   검증한다. 파일 I/O 는 건드리지 않고 dict 만 오간다.
#------------------------------------------------------------------

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))

import export_taxonomy as ET   # noqa: E402
from csoclassify.classify import axes as A   # noqa: E402


#------------------------------------------------------------------
# 최소 MPOWER JSON 원본 만들기
#=> convert() 입력으로 쓸 최소 원본을 만든다. 실제 doc_classification_export.json
#   의 필드명(parent_dc_id·order_num)을 그대로 쓴다.
#
# -in: nodes = {dc_id, parent_dc_id, order_num, title, status} dict 목록
#
# -out: dict = {source, node_count, nodes}
# -out: error = 없음
#------------------------------------------------------------------
def mk_raw(nodes, node_count=None):
    return {
        "source": "Mpower10U.DOC_CLASSIFICATION",
        "node_count": node_count if node_count is not None else len(nodes),
        "nodes": nodes,
    }


# ── convert() ─────────────────────────────────────────────────────

#------------------------------------------------------------------
# 필드명이 규약대로 바뀐다 (parent_dc_id→parent, order_num→order)
#=> path_ids/path_titles/path 처럼 원본 JSON 에 있어도 재계산 대상인 필드는
#   스냅샷에 옮기지 않는지도 함께 확인한다(설계서 3-2-1 — 경로는 axes.Taxonomy
#   가 로드 시점에만 계산한다).
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_필드명이_규약대로_바뀌고_경로필드는_버려진다():
    raw = mk_raw([
        {"dc_id": "DC_001", "parent_dc_id": None, "order_num": 1,
         "title": "경영/관리", "status": 1,
         "path_ids": ["DC_001"], "path_titles": ["경영/관리"], "path": "경영/관리"},
    ])
    snapshot, warnings = ET.convert(raw)
    assert warnings == []
    node = snapshot["taxonomy"]["nodes"][0]
    assert node == {"dc_id": "DC_001", "parent": None, "order": 1,
                     "title": "경영/관리", "status": 1}
    assert "path" not in node and "path_ids" not in node


#------------------------------------------------------------------
# 변환 결과는 axes.validate_taxonomy_data 를 통과한다
#=> export_taxonomy.py 가 만드는 dict 모양이 axes.py 가 실제로 읽는 스키마와
#   어긋나지 않는지, 두 모듈을 이어 확인한다(연동 지점 회귀 방지).
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_변환_결과는_axes_검증을_통과한다():
    raw = mk_raw([
        {"dc_id": "DC_002", "parent_dc_id": None, "order_num": 2, "title": "기술/개발", "status": 1},
        {"dc_id": "DC_002_001", "parent_dc_id": "DC_002", "order_num": 1,
         "title": "설계문서", "status": 1},
    ])
    snapshot, _ = ET.convert(raw)
    assert A.validate_taxonomy_data(snapshot) == []


#------------------------------------------------------------------
# node_count 가 실제 노드 수와 다르면 경고를 내되 변환은 계속한다
#=> 원본이 낡았을 수 있어도(설계서 R1), 변환 자체를 막을 이유는 없다 —
#   경고로만 알리고 진행한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_node_count_불일치는_경고만_내고_계속한다():
    raw = mk_raw([
        {"dc_id": "DC_001", "parent_dc_id": None, "order_num": 1, "title": "A", "status": 1},
    ], node_count=99)
    snapshot, warnings = ET.convert(raw)
    assert len(warnings) == 1
    assert "99" in warnings[0]
    assert snapshot["taxonomy"]["node_count"] == 1


#------------------------------------------------------------------
# dc_id 없는 항목은 건너뛰고 경고만 낸다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_dc_id_없는_노드는_건너뛴다():
    raw = mk_raw([
        {"parent_dc_id": None, "order_num": 1, "title": "dc_id 빠짐", "status": 1},
        {"dc_id": "DC_001", "parent_dc_id": None, "order_num": 1, "title": "정상", "status": 1},
    ], node_count=1)   # 실제 유효 노드 수(1)와 맞춰 node_count 경고는 따로 섞이지 않게
    snapshot, warnings = ET.convert(raw)
    assert len(warnings) == 1
    assert len(snapshot["taxonomy"]["nodes"]) == 1


#------------------------------------------------------------------
# 최상위가 매핑이 아니거나 nodes 가 없으면 ValueError
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_형식이_틀리면_ValueError():
    with pytest.raises(ValueError):
        ET.convert(["리스트인데_매핑이_아님"])
    with pytest.raises(ValueError):
        ET.convert({"source": "x"})   # nodes 없음


# ── build_doc_rule_scaffold() ────────────────────────────────────

#------------------------------------------------------------------
# 활성 노드마다 골격 1건, 비활성 노드는 제외된다
#=> filename 에 title 이 최초 제안값으로 복사되는지(설계서 5-1-1)도 함께 본다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_scaffold는_활성_노드만_담고_filename에_title을_제안한다():
    taxonomy = A.Taxonomy("test", "20260824000000", 2, [
        A.TaxonomyNode(dc_id="A", parent=None, order=1, title="루트A", status=1),
        A.TaxonomyNode(dc_id="A1", parent="A", order=1, title="자식A1", status=0),
    ])
    scaffold = ET.build_doc_rule_scaffold(taxonomy)
    assert scaffold["conflict"] == "all"
    ids = [r["node"] for r in scaffold["doctype_rules"]]
    assert ids == ["A"]   # status=0 인 A1 은 빠진다
    rule = scaffold["doctype_rules"][0]
    assert rule["terms"] == []
    assert rule["filename"] == ["루트A"]
