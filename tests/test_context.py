#------------------------------------------------------------------
# 경로/파일명 신호(Signal C·D)와 융합(fuse) 단위 테스트
#=> scan_path/scan_filename 이 경로·파일명만으로 등급 후보를 올바로 내는지,
#   fuse_signals 가 보수적 최댓값 + fail-safe 로 합치는지 검증한다.
#   추출기·모델 없이 경로 문자열과 합성 신호로만 돈다.
#------------------------------------------------------------------

from types import SimpleNamespace

import pytest

from csoclassify.classify import (
    load_rules, scan_filename, fuse_signals, build_record,
)

FIXED_TS = "2026-08-12T10:30:00+09:00"


#------------------------------------------------------------------
# 규칙셋 픽스처
#=> 기본 규칙셋(경로 규칙 포함)을 로드해 공유한다.
#
# -in: 없음
# -out: RuleSet
# -out: error = 로드 실패 시 테스트 에러
#------------------------------------------------------------------
@pytest.fixture(scope="module")
def rs():
    return load_rules()


#------------------------------------------------------------------
# 파일명 신호(Signal D)
#=> 파일명 키워드로 등급이 나오고, seed 로는 쓰지 않는다(항상 False).
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_scan_filename(rs):
    c = scan_filename("D:/x/대외비_보고서.hwp", rs)
    assert c.grade == "C" and c.seed_eligible is False

    # [2026-09-08] project_codenames 에는 filename 을 일부러 안 적었다 —
    # 제품 매뉴얼이 이름에 제품명이 있다는 이유만으로 C 가 되던 오탐의 근원이다.
    # terms 로 폴백하지 않으므로 이제 신호가 없어야 한다(설계 4장 P2).
    codename = scan_filename("D:/x/엠파워_설치안내.pptx", rs)
    assert codename.grade is None, codename.hits

    plain = scan_filename("D:/x/회의록_초안.hwp", rs)
    assert plain.grade is None


#------------------------------------------------------------------
# 융합 — 보수적 최댓값
#=> 신호 등급 중 가장 높은 등급을 택하고, 그 등급을 만든 신호가 decided_by 에 남는다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_fuse_max():
    rule = SimpleNamespace(grade="S", confidence=0.9, seed_eligible=True)
    name = SimpleNamespace(grade="O", confidence=0.9, seed_eligible=True)
    r = fuse_signals([("rule", rule), ("name", name)])
    assert r.grade == "S"
    assert r.decided_by == ["rule"]
    assert r.method == "fusion"


#------------------------------------------------------------------
# 융합 — 하향 금지(공개 경로라도 내용이 기밀이면 C)
#=> 경로가 O 라도 규칙이 C 면 최종은 C. 낮은 등급으로 끌어내리지 않는다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_fuse_no_downgrade():
    rule = SimpleNamespace(grade="C", confidence=0.85, seed_eligible=True)
    name = SimpleNamespace(grade="O", confidence=0.9, seed_eligible=True)
    r = fuse_signals([("rule", rule), ("name", name)])
    assert r.grade == "C"


#------------------------------------------------------------------
# 융합 — fail-safe 분기
#=> ①강한 제한(acl)만 있고 등급 신호가 없으면 C, ②아무것도 없고 failsafe 지정 시
#   그 등급, ③아무것도 없으면 None(미분류).
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_fuse_failsafe():
    empty = SimpleNamespace(grade=None, confidence=0.0, seed_eligible=False)

    # [2026-09-08] 경로 fail-safe(failsafe_acl)는 없어졌다. 신호가 하나도 없을 때
    # 등급을 줄지는 실행할 때 --failsafe 로 정한다(설계 6장).
    r_def = fuse_signals([("rule", empty)], failsafe="S")
    assert r_def.grade == "S" and r_def.method == "failsafe_default"

    r_none = fuse_signals([("rule", empty)])
    assert r_none.grade is None and r_none.method == "unclassified"


