#------------------------------------------------------------------
# 업무분류 seed 부트스트랩 테스트 (재설계 10장)
#=> 씨앗 선별은 조용히 틀리기 쉬운 종류다. 잘못 뽑아도 에러가 나지 않고,
#   몇 주 뒤에 "전파가 이상한 라벨을 붙인다"로 나타난다. 특히 10-4 의
#   drift 방지(전파 라벨은 씨앗이 될 수 없다)는 테스트가 없으면 지켜지는지
#   확인할 방법이 없다.
#------------------------------------------------------------------

import json
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from csoclassify.classify import seedgen                      # noqa: E402
from csoclassify.classify import axes as A                    # noqa: E402


#------------------------------------------------------------------
# 테스트용 분류체계
#=> 1계층 2개, 그 아래 leaf 3개. leaf 판정과 중간노드 배제를 함께 보려면
#   최소한 이 모양이 필요하다.
#
# -in: 없음
#
# -out: Taxonomy
# -out: error = 없음
#------------------------------------------------------------------
def mk_tax():
    N = A.TaxonomyNode
    return A.Taxonomy("test", "20260826000000", 2, [
        N(dc_id="DC_001", parent=None, order=1, title="경영/관리", status=1),
        N(dc_id="DC_001_001", parent="DC_001", order=1, title="보고서", status=1),
        N(dc_id="DC_001_002", parent="DC_001", order=2, title="회의록", status=1),
        N(dc_id="DC_002", parent=None, order=2, title="기술/개발", status=1),
        N(dc_id="DC_002_001", parent="DC_002", order=1, title="매뉴얼", status=1),
    ])


#------------------------------------------------------------------
# 레코드 한 건 만들기
#=> 씨앗 자격 판정에 필요한 최소 필드만 채운다.
#
# -in: f     = 파일 경로
# -in: dc    = 라벨 dc_id
# -in: conf  = 신뢰도
# -in: frm   = 신호원 목록
# -in: extra = labels.doctype.values 에 후보를 더 넣고 싶을 때
#
# -out: dict = 레코드
# -out: error = 없음
#------------------------------------------------------------------
def rec(f, dc="DC_001_001", conf=0.91, frm=("title", "name"), extra=(), vector=(0.1, 0.2)):
    vals = [{"dc_id": dc, "confidence": conf, "from": list(frm)}]
    vals += [dict(e) for e in extra]
    return {"file": f, "vector": list(vector), "text_len": 5000,
            "labels": {"doctype": {"values": vals}}}


#------------------------------------------------------------------
# 규칙 고신뢰 문서는 씨앗이 된다
#=> 기본 경로. 이게 안 되면 나머지를 볼 것도 없다.
#------------------------------------------------------------------
def test_고신뢰_단일라벨_문서는_씨앗이_된다():
    seeds, st = seedgen.select_doctype_seeds([rec(r"D:\a\보고서.hwp")], mk_tax())
    assert len(seeds) == 1
    assert seeds[0]["labels"]["doctype"] == ["DC_001_001"]
    assert seeds[0]["vector"] == [0.1, 0.2]
    assert st["채택"] == 1


#------------------------------------------------------------------
# 전파로 붙은 라벨은 절대 씨앗이 되지 않는다 <가장 중요>
#=> 재설계 10-4. 이 원칙이 깨지면 초기 오분류가 스스로를 강화하며 코퍼스
#   전체로 번진다. 신뢰도가 아무리 높아도 벡터에서 온 후보는 탈락해야 한다.
#------------------------------------------------------------------
def test_전파로_붙은_라벨은_씨앗이_되지_않는다():
    r = rec(r"D:\a\x.hwp", conf=0.99, frm=("embed",))
    seeds, st = seedgen.select_doctype_seeds([r], mk_tax())
    assert seeds == []
    assert st["탈락사유"]["벡터후보"] == 1

    # 규칙 신호가 섞여 있어도, embed 가 함께 있으면 씨앗으로 쓰지 않는다.
    r2 = rec(r"D:\a\y.hwp", conf=0.99, frm=("title", "name", "embed"))
    seeds2, _ = seedgen.select_doctype_seeds([r2], mk_tax())
    assert seeds2 == []


#------------------------------------------------------------------
# 신뢰도가 낮으면 탈락한다
#=> t_seed 는 '아주 자신 있는 것만' 이라는 뜻이다. 여기를 낮추면 씨앗 품질이
#   그대로 전파 품질이 된다.
#------------------------------------------------------------------
def test_신뢰도가_낮으면_탈락한다():
    seeds, st = seedgen.select_doctype_seeds(
        [rec(r"D:\a\x.hwp", conf=0.70)], mk_tax(), t_seed=0.85)
    assert seeds == [] and st["탈락사유"]["신뢰도미달"] == 1
    # 임계를 낮추면 통과한다 — 정책에서 조절할 수 있어야 한다.
    seeds2, _ = seedgen.select_doctype_seeds(
        [rec(r"D:\a\x.hwp", conf=0.70)], mk_tax(), t_seed=0.65)
    assert len(seeds2) == 1


#------------------------------------------------------------------
# 신호원이 하나뿐이면 탈락한다
#=> 재설계 10-1 "독립된 두 신호원이 같은 노드를 가리킬 것".
#   파일 이름 하나만 보고 씨앗을 만들면, 파일 이름의 오탐이 그대로 씨앗이 된다.
#------------------------------------------------------------------
def test_신호원이_하나면_탈락한다():
    seeds, st = seedgen.select_doctype_seeds(
        [rec(r"D:\a\x.hwp", frm=("name",))], mk_tax())
    assert seeds == [] and st["탈락사유"]["신호원부족"] == 1


#------------------------------------------------------------------
# 약한 신호는 신호원 수에 세지 않는다
#=> 본문·짜임새는 단독으로 후보를 못 만드는 약한 증거다(재설계 8-4).
#   '파일이름 + 본문' 두 개라고 통과시키면 사실상 신호원 하나짜리 씨앗이 된다.
#------------------------------------------------------------------
def test_본문_신호는_신호원으로_세지_않는다():
    seeds, st = seedgen.select_doctype_seeds(
        [rec(r"D:\a\x.hwp", frm=("name", "body"))], mk_tax())
    assert seeds == [] and st["탈락사유"]["신호원부족"] == 1

    # 강한 신호가 둘이면 통과한다.
    ok, _ = seedgen.select_doctype_seeds(
        [rec(r"D:\a\x.hwp", frm=("title", "name", "body"))], mk_tax())
    assert len(ok) == 1


#------------------------------------------------------------------
# 라벨이 2개 이상인 문서는 씨앗으로 쓰지 않는다
#=> 그 문서의 벡터가 어느 분류를 대표하는지 정할 수 없다(재설계 10-1).
#------------------------------------------------------------------
def test_다중라벨_문서는_탈락한다():
    r = rec(r"D:\a\x.hwp",
            extra=[{"dc_id": "DC_002_001", "confidence": 0.9, "from": ["title"]}])
    seeds, st = seedgen.select_doctype_seeds([r], mk_tax())
    assert seeds == [] and st["탈락사유"]["다중라벨"] == 1


#------------------------------------------------------------------
# 중간 노드는 씨앗이 되지 않는다
#=> 자손이 함께 걸리면 조상 흡수로 사라지는 노드다. 씨앗에 넣으면 kNN 투표만
#   흐려 놓는다.
#------------------------------------------------------------------
def test_중간노드는_탈락한다():
    seeds, st = seedgen.select_doctype_seeds(
        [rec(r"D:\a\x.hwp", dc="DC_001")], mk_tax())
    assert seeds == [] and st["탈락사유"]["중간노드"] == 1


#------------------------------------------------------------------
# 벡터가 없거나 본문이 너무 짧으면 탈락한다
#=> 빈 스캔 PDF 의 벡터는 '아무거나와 비슷한' 벡터가 되어 전파를 망친다.
#   본문 길이를 모르는 경우(None)는 짧다고 단정하지 않는다.
#------------------------------------------------------------------
def test_벡터없음_본문짧음은_탈락한다():
    no_vec = rec(r"D:\a\x.hwp"); no_vec["vector"] = None
    short = rec(r"D:\a\y.hwp"); short["text_len"] = 10
    unknown = rec(r"D:\a\z.hwp"); del unknown["text_len"]

    seeds, st = seedgen.select_doctype_seeds([no_vec, short, unknown], mk_tax())
    assert st["탈락사유"]["벡터없음"] == 1
    assert st["탈락사유"]["본문짧음"] == 1
    # 길이를 모르는 문서는 통과해야 한다.
    assert [s["file"] for s in seeds] == [r"D:\a\z.hwp"]


#------------------------------------------------------------------
# 한 폴더에서 너무 많이 뽑지 않는다
#=> 재설계 10-3 완화 1. 한 폴더에서 수십 건을 뽑으면 그 폴더의 문서 모양이
#   곧 그 분류의 정의가 되어 버린다(편향).
#------------------------------------------------------------------
def test_폴더당_상한이_걸린다():
    recs = [rec(os.path.join(r"D:\같은폴더", f"보고서{i}.hwp"), conf=0.9 + i / 1000)
            for i in range(10)]
    seeds, st = seedgen.select_doctype_seeds(recs, mk_tax(), per_dir=3)
    assert len(seeds) == 3
    assert st["탈락사유"]["폴더상한"] == 7
    # 확실한 것부터 남아야 한다 — 신뢰도가 가장 높은 세 건.
    assert seeds[0]["file"].endswith("보고서9.hwp")


#------------------------------------------------------------------
# 한 분류가 씨앗을 독식하지 못한다
#=> 재설계 10-3 완화 2. 특정 노드가 씨앗을 독식하면 kNN 투표가 그 노드로 쏠린다.
#------------------------------------------------------------------
def test_노드당_상한이_걸린다():
    recs = [rec(os.path.join(rf"D:\폴더{i}", "보고서.hwp")) for i in range(10)]
    seeds, st = seedgen.select_doctype_seeds(recs, mk_tax(), per_node=4)
    assert len(seeds) == 4 and st["탈락사유"]["노드상한"] == 6


#------------------------------------------------------------------
# 저장 형식이 DoctypeSeedIndex 가 읽는 그대로다
#=> 재설계 10-1 단계 2 "새 저장 포맷이 필요 없다". 형식이 어긋나면 씨앗을
#   만들어도 전파가 0건이 되는데, 에러가 안 나서 알아채기 어렵다.
#------------------------------------------------------------------
def test_저장한_씨앗을_전파_인덱스가_읽는다(tmp_path):
    from csoclassify.classify.propagate import DoctypeSeedIndex
    seeds, _ = seedgen.select_doctype_seeds(
        [rec(r"D:\a\보고서.hwp", vector=(0.6, 0.8))], mk_tax())
    p = str(tmp_path / "class_seed.jsonl")
    assert seedgen.write_seed_file(seeds, p) == 1

    idx = DoctypeSeedIndex.from_seed_file(p)
    assert idx.size == 1
    assert idx.label_sets[0] == ("DC_001_001",)


#------------------------------------------------------------------
# 보안등급 씨앗을 지우지 않는다 <중요>
#=> class_seed.jsonl 은 두 축이 나눠 쓰는 파일이다(설계서 5-4). 통째로 덮어쓰면
#   보안등급 전파가 조용히 죽는다 — 에러 없이 등급만 안 붙게 된다.
#------------------------------------------------------------------
def test_보안등급_씨앗은_보존된다(tmp_path):
    p = str(tmp_path / "class_seed.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"file": "a.doc", "grade": "S", "vector": [0.1]}) + "\n")
        f.write(json.dumps({"file": "b.doc", "grade": "C", "vector": [0.2]}) + "\n")
        # 이전 회차의 업무분류 씨앗 — 새 것으로 갈아 끼워져야 한다.
        f.write(json.dumps({"file": "c.doc", "labels": {"doctype": ["DC_001_002"]},
                            "vector": [0.3]}) + "\n")

    seeds, _ = seedgen.select_doctype_seeds([rec(r"D:\a\보고서.hwp")], mk_tax())
    out = seedgen.merge_into(seeds, p)
    assert out == {"기존유지": 2, "이전doctype제거": 1, "신규": 1}

    lines = [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]
    assert [x.get("grade") for x in lines[:2]] == ["S", "C"]
    assert len(lines) == 3
    assert lines[2]["labels"]["doctype"] == ["DC_001_001"]


#------------------------------------------------------------------
# 씨앗 파일이 없던 경우에도 동작한다
#=> 처음 만들 때가 가장 흔한 경우다.
#------------------------------------------------------------------
def test_씨앗파일이_없어도_새로_만든다(tmp_path):
    p = str(tmp_path / "새폴더" / "class_seed.jsonl")
    seeds, _ = seedgen.select_doctype_seeds([rec(r"D:\a\보고서.hwp")], mk_tax())
    out = seedgen.merge_into(seeds, p)
    assert out["기존유지"] == 0 and out["신규"] == 1
    assert os.path.isfile(p)
