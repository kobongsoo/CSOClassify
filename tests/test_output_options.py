#------------------------------------------------------------------
# 출력 축약 옵션(--hash/--simple/--summary) 단위 테스트
#=> 해시 계산이 hashlib 과 일치하는지, --simple 레코드가 3필드만 남기는지,
#   최고위험 등급 선택이 맞는지 검증한다.
#------------------------------------------------------------------

import hashlib

from csoclassify.cli import _file_hash, _simple_record, _worst_grade


#------------------------------------------------------------------
# 파일 해시가 hashlib.sha256 과 일치
#=> _file_hash 가 표준 hashlib 로 계산한 값과 정확히 같아야 한다.
#
# -in: tmp_path = pytest 임시폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_file_hash_matches_hashlib(tmp_path):
    p = tmp_path / "x.bin"
    data = b"hello \x00\xff world" * 100000   # 대용량(청크 경계) 확인 겸
    p.write_bytes(data)
    assert _file_hash(str(p)) == hashlib.sha256(data).hexdigest()


#------------------------------------------------------------------
# 없는 파일은 None
#=> 읽기 실패 시 예외 대신 None 을 돌려줘 상위 흐름을 막지 않는다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_file_hash_missing_returns_none():
    assert _file_hash("D:/no/such/file/at/all.xyz") is None


#------------------------------------------------------------------
# --simple 레코드는 3필드만
#=> file/grade/hash 만 남고 나머지(signals 등)는 빠져야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_simple_record_keeps_three_fields():
    rec = {"file": "a.hwp", "grade": "C", "hash": "deadbeef",
           "signals": {"rule": {}}, "confidence": 0.9, "ts": "..."}
    out = _simple_record(rec)
    assert out == {"file": "a.hwp", "grade": "C", "hash": "deadbeef"}


#------------------------------------------------------------------
# 최고위험 등급 선택(C>S>O>None)
#=> 압축 집계에 쓰는 _worst_grade 가 위험 순서를 지키는지.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_worst_grade_order():
    assert _worst_grade(["O", "S", None]) == "S"
    assert _worst_grade(["O", "C", "S"]) == "C"
    assert _worst_grade([None, None]) is None
    assert _worst_grade(["O", "O"]) == "O"
    assert _worst_grade([]) is None
