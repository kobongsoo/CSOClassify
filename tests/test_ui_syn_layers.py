#------------------------------------------------------------------
# 유의어 사전 겹치기(core → 업종 → local) 테스트
#=> 업종마다 쓰는 문서 이름이 달라서 사전을 여러 장으로 갈라 두었다. 여기서
#   확인하는 것은 "어느 장이 이기는가" 와 "합친 뒤에도 그물 순서가 살아 있는가"
#   두 가지다. 둘 중 하나만 어긋나도 증상이 조용해서(유의어가 조금 적을 뿐)
#   사람 눈으로는 못 잡는다.
#------------------------------------------------------------------

import os
import sys

import yaml

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")
if UI not in sys.path:
    sys.path.insert(0, UI)

import docruleedit as DRE   # noqa: E402


#------------------------------------------------------------------
# 임시 폴더에 사전 겹들을 깔아 준다
#=> 진짜 배포 사전에 기대면 사전 내용이 바뀔 때마다 이 테스트가 깨진다.
#   여기서 보는 것은 내용이 아니라 겹치는 방식이므로 최소한만 만든다.
#
# -in: tmp = pytest tmp_path
# -in: layers = {파일이름: 사전 내용 dict}
# -in: rule = doc_rule.yaml 에 넣을 내용(업종 지정용)
#
# -out: str = 만들어진 doc_rule.yaml 경로(load_synonyms 에 넘길 값)
# -out: error = 없음
#------------------------------------------------------------------
def 사전깔기(tmp, layers, rule=None):
    for name, body in layers.items():
        (tmp / name).write_text(
            yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    rule_path = tmp / "doc_rule.yaml"
    rule_path.write_text(
        yaml.safe_dump(rule or {"doctype_rules": []}, allow_unicode=True),
        encoding="utf-8")
    return str(rule_path)


#------------------------------------------------------------------
# 업종 사전이 공통 사전 위에 얹힌다
#=> 같은 분류 이름이 두 겹에 다 있으면 지우지 않고 이어 붙이되, 센 겹(업종)의
#   말이 앞에 와야 한다. 순서가 곧 우선순위라 뒤는 잘려 나가기 때문이다.
#------------------------------------------------------------------
def test_업종_사전이_공통_사전_위에_얹힌다(tmp_path):
    path = 사전깔기(tmp_path, {
        "doc_synonyms.core.yaml": {"aliases": {"보고서": ["결과보고", "리포트"]}},
        "doc_synonyms.finance.yaml": {"aliases": {"보고서": ["여신보고"],
                                                  "여신심사서": ["심사평정표"]}},
    }, rule={"industry": "finance", "doctype_rules": []})
    syn = DRE.load_synonyms(path)
    # 업종 말이 앞, 공통 말이 뒤
    assert syn["aliases"]["보고서"] == ["여신보고", "결과보고", "리포트"]
    # 업종에만 있는 이름도 살아 있다
    assert syn["aliases"]["여신심사서"] == ["심사평정표"]


#------------------------------------------------------------------
# 사람이 고치는 local 이 언제나 이긴다
#=> 관리자가 적은 말이 제품 사전보다 뒤로 밀리면, 고쳐도 안 듣는 것처럼 보인다.
#------------------------------------------------------------------
def test_local_이_가장_세다(tmp_path):
    path = 사전깔기(tmp_path, {
        "doc_synonyms.core.yaml": {"aliases": {"보고서": ["리포트"]}},
        "doc_synonyms.finance.yaml": {"aliases": {"보고서": ["여신보고"]}},
        "doc_synonyms.local.yaml": {"aliases": {"보고서": ["우리회사보고"]}},
    }, rule={"industry": "finance", "doctype_rules": []})
    syn = DRE.load_synonyms(path)
    assert syn["aliases"]["보고서"] == ["우리회사보고", "여신보고", "리포트"]
    assert os.path.basename(syn["path"]) == "doc_synonyms.local.yaml"
    assert len(syn["layers"]) == 3


#------------------------------------------------------------------
# 업종을 안 적으면 업종 사전은 얹히지 않는다
#=> 폴더에 여러 업종 사전이 함께 놓여 있어도, 지정한 것만 써야 한다.
#   전부 얹으면 의료 문서명이 금융 고객사 규칙에 섞여 오탐이 된다.
#------------------------------------------------------------------
def test_업종을_안_적으면_업종_사전은_안_얹힌다(tmp_path):
    path = 사전깔기(tmp_path, {
        "doc_synonyms.core.yaml": {"aliases": {"보고서": ["리포트"]}},
        "doc_synonyms.finance.yaml": {"aliases": {"보고서": ["여신보고"]}},
        "doc_synonyms.medical.yaml": {"aliases": {"보고서": ["판독소견서"]}},
    })
    syn = DRE.load_synonyms(path)
    assert syn["aliases"]["보고서"] == ["리포트"]
    assert len(syn["layers"]) == 1


#------------------------------------------------------------------
# 업종을 여러 개 적을 수 있다 — 앞에 적은 것이 더 세다
#=> 병원의 총무팀처럼 한 회사가 두 업종에 걸치는 일이 실제로 있다.
#------------------------------------------------------------------
def test_업종을_여러개_적으면_앞엣것이_더_세다(tmp_path):
    path = 사전깔기(tmp_path, {
        "doc_synonyms.core.yaml": {"aliases": {"보고서": ["리포트"]}},
        "doc_synonyms.medical.yaml": {"aliases": {"보고서": ["판독소견서"]}},
        "doc_synonyms.public.yaml": {"aliases": {"보고서": ["행정보고"]}},
    }, rule={"industry": ["medical", "public"], "doctype_rules": []})
    syn = DRE.load_synonyms(path)
    assert syn["aliases"]["보고서"] == ["판독소견서", "행정보고", "리포트"]


#------------------------------------------------------------------
# 합친 뒤에는 긴 꼬리말이 앞으로 다시 세워진다
#=> 이 재정렬이 겹치기의 핵심이다. 업종 사전이 짧은 꼬리말을 들고 오면 파일
#   순서만으로는 core 의 긴 꼬리말이 뒤로 밀려 죽는다.
#------------------------------------------------------------------
def test_합친_뒤_긴_꼬리말이_앞으로_온다(tmp_path):
    path = 사전깔기(tmp_path, {
        "doc_synonyms.core.yaml": {"tails": {"결과보고서": ["결과보고"],
                                             "보고서": ["리포트"]}},
        # 업종 사전이 짧은 꼬리말만 들고 온다 — 그냥 합치면 순서가 뒤집힌다
        "doc_synonyms.finance.yaml": {"tails": {"보고서": ["여신보고"]}},
    }, rule={"industry": "finance", "doctype_rules": []})
    syn = DRE.load_synonyms(path)
    keys = list(syn["tails"])
    assert keys.index("결과보고서") < keys.index("보고서")
    # 실제로 긴 쪽이 걸리는지까지 본다
    assert "심사결과보고" in DRE.synonyms_of("심사결과보고서", syn)


#------------------------------------------------------------------
# 이름 없는 doc_synonyms.yaml 은 읽지 않는다
#=> 겹으로 가르기 전에 쓰던 단일 파일이다. 쓰는 고객사가 없어 겹에서 뺐다
#   (2026-08-26 결정). 폴더에 남아 있어도 무시해야 한다 — 어중간하게 읽으면
#   "core 를 고쳤는데 안 듣는다" 는 증상이 생기고, 원인이 옛날 파일이라는 것을
#   아무도 떠올리지 못한다. 되살리려면 core 나 local 로 이름을 바꿔야 한다.
#------------------------------------------------------------------
def test_예전_단일_파일은_무시한다(tmp_path):
    path = 사전깔기(tmp_path, {
        "doc_synonyms.core.yaml": {"aliases": {"보고서": ["리포트"]}},
        "doc_synonyms.yaml": {"aliases": {"보고서": ["예전에적어둔말"]}},
    })
    syn = DRE.load_synonyms(path)
    assert syn["aliases"]["보고서"] == ["리포트"]
    assert len(syn["layers"]) == 1
    assert all("doc_synonyms.yaml" not in os.path.basename(p)
               for p in syn["layers"])


#------------------------------------------------------------------
# 한 겹이 깨져도 나머지 겹은 살아남는다
#=> 사전은 사람이 손으로 고치는 파일이라 YAML 이 깨지는 일이 실제로 있다.
#   그때 전체 사전이 통째로 사라지면 규칙 생성이 조용히 빈약해진다.
#------------------------------------------------------------------
def test_한_겹이_깨져도_나머지는_살아남는다(tmp_path):
    path = 사전깔기(tmp_path, {
        "doc_synonyms.core.yaml": {"aliases": {"보고서": ["리포트"]}},
    })
    (tmp_path / "doc_synonyms.local.yaml").write_text(
        "aliases:\n  보고서:\n  - [깨진 YAML\n", encoding="utf-8")
    syn = DRE.load_synonyms(path)
    assert syn["aliases"]["보고서"] == ["리포트"]


#------------------------------------------------------------------
# 업종 이름으로 다른 폴더 파일을 읽을 수 없다
#=> industry 는 고객사가 고치는 doc_rule.yaml 에서 온다. 그 값이 그대로 파일
#   이름이 되므로 "../" 같은 값이 들어오면 엉뚱한 파일을 읽게 된다.
#------------------------------------------------------------------
def test_업종_이름에_경로를_넣을_수_없다(tmp_path):
    assert DRE.industry_of(str(사전깔기(
        tmp_path, {}, rule={"industry": "../../etc/passwd"}))) == []
    assert DRE.industry_of(str(사전깔기(
        tmp_path, {}, rule={"industry": ["finance", "a/b"]}))) == ["finance"]


#------------------------------------------------------------------
# 사전이 하나도 없으면 빈 값이다
#=> 사전은 없어도 되는 파일이다. 없다고 예외를 올리면 규칙 생성 자체가 멈춘다.
#------------------------------------------------------------------
def test_사전이_없으면_빈값이다(tmp_path):
    assert DRE.load_synonyms(사전깔기(tmp_path, {})) == {}
    assert DRE.load_synonyms(None) == {}
