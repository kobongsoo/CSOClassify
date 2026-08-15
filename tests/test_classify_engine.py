#------------------------------------------------------------------
# 분류 레코드 조립(engine) 단위 테스트
#=> build_record 가 규칙 신호를 설계서 스키마의 레코드로 올바로 바꾸는지,
#   fail-safe 분기와 프라이버시 불변식(원문 미포함)이 지켜지는지 검증한다.
#   추출기·모델·네트워크가 필요 없도록 텍스트를 직접 넣는다.
#------------------------------------------------------------------

import pytest

from csoclassify.classify import load_rules, build_record

VALID_RRN = "900101-1234568"   # 체크섬 유효한 가짜 예시(실제 개인정보 아님)
FIXED_TS = "2026-08-12T10:30:00+09:00"


#------------------------------------------------------------------
# 규칙셋 픽스처
#=> 기본 규칙셋을 로드해 공유한다.
#
# -in: 없음
# -out: RuleSet
# -out: error = 로드 실패 시 테스트 에러
#------------------------------------------------------------------
@pytest.fixture(scope="module")
def rs():
    return load_rules()


#------------------------------------------------------------------
# 기밀 문서 레코드
#=> "대외비" 문서는 grade=C, method="rule_scan", signals.rule 이 채워지고
#   ts 는 넣어준 값 그대로여야 한다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_record_confidential(rs):
    rec = build_record("D:/x/보고서.hwp", "본 문서는 대외비입니다.", rs, ts=FIXED_TS)
    assert rec["grade"] == "C"
    assert rec["method"] == "fusion"
    assert "rule" in rec["decided_by"]
    assert rec["seed_eligible"] is True
    assert rec["signals"]["rule"]["grade"] == "C"
    assert rec["rule_version"].startswith("cso-")
    assert rec["ts"] == FIXED_TS
    assert rec["file"] == "D:/x/보고서.hwp"


#------------------------------------------------------------------
# 특이사항 없는 문서 → 등급 보류
#=> 규칙 미검출이면 grade=None, method="no_rule_hit" 이어야 한다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_record_no_hit(rs):
    rec = build_record("a.txt", "다음 주 회식 장소 안내드립니다.", rs, ts=FIXED_TS)
    assert rec["grade"] is None
    assert rec["method"] == "unclassified"
    assert rec["seed_eligible"] is False


#------------------------------------------------------------------
# fail-safe 기본등급
#=> 규칙 미검출이라도 failsafe="S" 를 주면 grade=S, method="failsafe_default".
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_record_failsafe(rs):
    rec = build_record("a.txt", "특이사항 없는 일반 안내문.", rs, ts=FIXED_TS, failsafe="S")
    assert rec["grade"] == "S"
    assert rec["method"] == "failsafe_default"


#------------------------------------------------------------------
# 레코드에 원문 값이 없다(프라이버시 불변식)
#=> 주민번호가 든 문서라도 레코드 전체를 문자열화했을 때 원문 숫자가 없어야 한다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_record_no_raw_value(rs):
    rec = build_record("b.hwp", f"주민등록번호 {VALID_RRN} 포함", rs, ts=FIXED_TS)
    blob = str(rec)
    assert VALID_RRN not in blob
    assert VALID_RRN.replace("-", "") not in blob
    assert rec["grade"] == "S"


#------------------------------------------------------------------
# 규칙 비활성(rules_enabled=False) — 벡터-only 분류의 '보류 레코드'
#=> 규칙에 걸릴 내용(대외비·주민번호)이어도 rules_enabled=False 면 규칙을 아예 안
#   돌리고 grade=None 보류 레코드만 만든다(등급은 이후 전파가 정함). 벡터는 실린다.
#------------------------------------------------------------------
def test_build_record_rules_disabled(rs):
    rec = build_record("D:/x/비밀.hwp", f"대외비 주민등록번호 {VALID_RRN}", rs,
                       ts=FIXED_TS, rules_enabled=False, vector=[0.1, 0.2, 0.3])
    assert rec["grade"] is None
    assert rec["method"] == "unclassified"
    assert rec["decided_by"] == []
    assert rec["signals"] == {}           # 규칙 신호를 하나도 안 담는다
    assert rec["vector"] == [0.1, 0.2, 0.3]
    assert rec["ts"] == FIXED_TS


#------------------------------------------------------------------
# 벡터-only 흐름: 보류 레코드가 seed 비교로 등급을 받는다
#=> rules_enabled=False 로 만든 보류 문서를, 동일 벡터의 seed(등급 C)와 전파 비교하면
#   near-dup 상속으로 grade=C 가 되고 decided_by 에 'embed' 가 들어간다.
#------------------------------------------------------------------
def test_vector_only_propagate_assigns_grade(rs):
    from csoclassify.classify import propagate_records
    from csoclassify.classify.propagate import SeedIndex
    vec = [1.0, 0.0, 0.0]                 # 단위벡터(정규화 불필요)
    seed = SeedIndex.from_records(
        [{"grade": "C", "seed_eligible": True, "vector": vec, "file": "seed"}])
    pending = build_record("D:/x/문서.hwp", "규칙에 안 걸리는 서술문.", rs,
                           ts=FIXED_TS, rules_enabled=False, vector=vec)
    assert pending["grade"] is None
    recs, stats = propagate_records([pending], seed_index=seed)
    assert recs[0]["grade"] == "C"
    assert "embed" in recs[0]["decided_by"]
    assert stats["embed_decided"] == 1
