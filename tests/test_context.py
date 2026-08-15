#------------------------------------------------------------------
# 경로/파일명 신호(Signal C·D)와 융합(fuse) 단위 테스트
#=> scan_path/scan_filename 이 경로·파일명만으로 등급 후보를 올바로 내는지,
#   fuse_signals 가 보수적 최댓값 + fail-safe 로 합치는지 검증한다.
#   추출기·모델 없이 경로 문자열과 합성 신호로만 돈다.
#------------------------------------------------------------------

from types import SimpleNamespace

import pytest

from csoclassify.classify import (
    load_rules, scan_path, scan_filename, fuse_signals, build_record,
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
# 경로 규칙 로드 확인
#=> paths 섹션이 실제로 로드됐는지 본다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_path_rules_loaded(rs):
    assert len(rs.path_rules) >= 3
    assert "secure_server" in {r.id for r in rs.path_rules}


#------------------------------------------------------------------
# 경로 신호(Signal C)
#=> 기밀 서버=C(+acl), 인사폴더=S, 공개폴더=O, 무매칭=None 을 확인한다.
#   역슬래시/UNC 도 정규화되어 매칭돼야 한다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_scan_path(rs):
    c = scan_path(r"\\hr-server\share\뭔가.hwp", rs)
    # 경로규칙의 seed_eligible 은 정책 튜닝값(위치만으로 C인 문서를 내부 seed 로
    # 쓸지 여부)이라 값 자체는 단정하지 않는다. 등급 C·강한제한(acl)만 검증한다.
    assert c.grade == "C" and c.acl_restricted is True

    s = scan_path(r"D:\collected\인사\평가.hwp", rs)
    assert s.grade == "S"

    o = scan_path(r"D:\collected\public\안내.pdf", rs)
    assert o.grade == "O"

    none = scan_path(r"D:\collected\기타\메모.txt", rs)
    assert none.grade is None and none.acl_restricted is False


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

    codename = scan_filename("D:/x/엠파워_설치안내.pptx", rs)
    assert codename.grade == "C"

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
    path = SimpleNamespace(grade="O", confidence=0.9, seed_eligible=True, acl_restricted=False)
    r = fuse_signals([("rule", rule), ("path", path)])
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
    path = SimpleNamespace(grade="O", confidence=0.9, seed_eligible=True, acl_restricted=False)
    r = fuse_signals([("rule", rule), ("path", path)])
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
    acl = SimpleNamespace(grade=None, confidence=0.0, seed_eligible=False, acl_restricted=True)

    r_acl = fuse_signals([("rule", empty), ("path", acl)])
    assert r_acl.grade == "C" and r_acl.method == "failsafe_acl"

    r_def = fuse_signals([("rule", empty)], failsafe="S")
    assert r_def.grade == "S" and r_def.method == "failsafe_default"

    r_none = fuse_signals([("rule", empty)])
    assert r_none.grade is None and r_none.method == "unclassified"


#------------------------------------------------------------------
# 통합 — 경로만으로 등급(내용 무관)
#=> 본문에 아무 규칙이 안 걸려도 경로가 기밀 서버면 레코드 등급이 C 가 되고,
#   decided_by 에 path 가 남아야 한다(Signal C 가 단독으로 구제).
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_build_record_path_only(rs):
    rec = build_record(r"\\hr-server\share\일반메모.txt", "특이사항 없는 내용.", rs, ts=FIXED_TS)
    assert rec["grade"] == "C"
    assert "path" in rec["decided_by"]
    assert rec["signals"]["path"]["acl_restricted"] is True
