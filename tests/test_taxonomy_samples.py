#------------------------------------------------------------------
# 예시 분류체계(resources/policy/samples/*.json) 회귀 테스트
#=> 고객사 실데이터는 저장소에 올리지 않으므로, 형식을 보여 주는 자리는 이
#   예시 파일들뿐이다. 예시가 깨지면 "형식이 어떻게 생겼는지"를 알려 줄 것이
#   아무것도 남지 않으므로, 실제 변환기·로더를 그대로 태워서 지킨다.
#------------------------------------------------------------------

import glob
import json
import os

import pytest

from csoclassify.classify import axes as A
from csoclassify.classify import doc_rules as DR

_SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "resources", "policy", "samples")


#------------------------------------------------------------------
# 예시 파일 목록
#=> 파일이 늘어나도 테스트를 고치지 않도록 폴더를 훑어서 모은다.
#
# -in: 없음
#
# -out: list = (이름표, 파일경로) 짝 목록. 정렬해서 실행 순서를 고정한다
# -out: error = 없음(파일이 하나도 없으면 빈 목록 → 아래에서 실패로 잡는다)
#------------------------------------------------------------------
def sample_files():
    pat = os.path.join(_SAMPLES, "doc_classification_export.*.json")
    return sorted((os.path.basename(p).split(".")[1], p) for p in glob.glob(pat))


#------------------------------------------------------------------
# 예시가 사라지지 않았는가
#=> 이름을 바꾸거나 폴더를 옮기면 아래 테스트들이 '0건 통과'로 조용히 넘어간다.
#   그 조용한 통과를 막는 것이 이 테스트의 유일한 목적이다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_예시_분류체계가_여러_개_있다():
    names = [n for n, _ in sample_files()]
    assert len(names) >= 5, f"예시가 너무 적습니다: {names}"
    # 도메인이 한쪽으로 쏠리면 '다양한 예시'라는 목적을 잃는다.
    assert "itsec" in names and "public" in names


#------------------------------------------------------------------
# 모든 예시는 변환·검증·로드를 통과한다
#=> 실제 파이프라인(JSON → doc_taxonomy.yaml → load_taxonomy)을 그대로 태운다.
#   json.load 만 해 보고 통과시키면, 정작 제품이 못 읽는 파일을 예시로 두게 된다.
#
# -in: name/path = sample_files() 가 준 이름표와 경로(파라미터라이즈 주입)
# -in: tmp_path  = pytest 임시 폴더(자동 주입)
#
# -out: 없음(단언)
# -out: error = 변환/검증 실패 시 예외 그대로 전파(그것이 곧 실패다)
#------------------------------------------------------------------
@pytest.mark.parametrize("name,path", sample_files())
def test_예시는_변환과_로드를_통과한다(name, path, tmp_path):
    out = tmp_path / f"{name}.yaml"
    A.export_from_mpower_json(path, str(out))
    tax = A.load_taxonomy(str(out))
    assert len(tax) >= 10

    raw = json.load(open(path, encoding="utf-8"))
    # node_count 가 실제 개수와 어긋난 파일은 로더가 막지만, 예시 쪽에서 먼저 잡는다.
    assert raw["node_count"] == len(raw["nodes"]) == len(tax)

    for n in raw["nodes"]:
        dc = n["dc_id"]
        # 전체경로 세 칸은 부모를 따라 계산한 값이라, 손으로 고치면 여기서 어긋난다.
        assert tax.path_ids(dc) == tuple(n["path_ids"])
        assert tax.path(dc) == n["path"] == " > ".join(n["path_titles"])
        # 부모 ID 는 자기 경로의 바로 앞 칸이어야 한다(부모가 없으면 최상위).
        parent = n["path_ids"][-2] if len(n["path_ids"]) > 1 else None
        assert n["parent_dc_id"] == parent


#------------------------------------------------------------------
# 예시로 규칙 골격을 만들 수 있다
#=> 예시를 주는 이유의 절반은 "이걸로 doc_rule.yaml 을 만들어 보라"는 것이다.
#   골격 생성까지 되어야 예시 노릇을 한다. 폐지된 분류(status 0)는 새로 제안될
#   일이 없으므로 규칙이 생기지 않는 것도 함께 확인한다.
#
# -in: tmp_path = pytest 임시 폴더(자동 주입)
#
# -out: 없음(단언)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_예시로_규칙_골격을_만들_수_있다(tmp_path):
    path = os.path.join(_SAMPLES, "doc_classification_export.itsec.json")
    out = tmp_path / "itsec.yaml"
    A.export_from_mpower_json(path, str(out))
    tax = A.load_taxonomy(str(out))

    scaffold = DR.build_scaffold(tax)
    live = [n.dc_id for n in tax if n.status == 1]
    dead = [n.dc_id for n in tax if n.status == 0]
    assert dead, "itsec 예시에는 폐지된 분류(status 0)가 하나 들어 있어야 합니다."

    nodes = {r["node"] for r in scaffold["doctype_rules"]}
    assert nodes == set(live)
    assert not (nodes & set(dead))
    # 골격은 말 그대로 골격이다 — terms 가 비어 있어야 "채워야 동작한다"가 성립한다.
    assert all(not (r.get("terms") or []) for r in scaffold["doctype_rules"])
