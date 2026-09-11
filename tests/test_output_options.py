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
#   doc_id 축을 안 쓴 레코드(doc_id_source 없음)라 doc_id 키도 붙지 않는다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_simple_record_keeps_three_fields():
    rec = {"file": "a.hwp", "hash": "deadbeef",
           "security": {"grade": "C", "confidence": 0.9, "signals": {"rule": {}}},
           "meta": {"ts": "..."}}
    out = _simple_record(rec)
    assert out == {"file": "a.hwp", "grade": "C", "hash": "deadbeef"}


#------------------------------------------------------------------
# --simple 에 doc_id 가 실린다 — sfile_id 로 얻은 것만
#=> 축약본만 받는 쪽(MpowerV11)이 결과를 자기 문서와 이으려면 doc_id 가 있어야
#   한다. 다만 폴백(content·path)은 우리가 만들어 낸 값이라 적재하면 같은 문서가
#   두 건으로 들어간다. 게다가 content 폴백은 hash[:40] 이라 새 정보도 아니다.
#   그래서 sfile_id 일 때만 값을 싣고 나머지는 None 으로 둔다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_simple_record_carries_sfile_id_only():
    base = {"file": "a.hwp", "security": {"grade": "C"}, "hash": "deadbeef"}

    # ① 목록에서 얻은 값 — 그대로 싣는다(적재 대상).
    out = _simple_record(dict(base, doc_id="SF_2026", doc_id_source="sfile_id"))
    assert out["doc_id"] == "SF_2026"

    # ② 내용 해시 폴백 — None. 받는 쪽은 hash 로 문서를 가린다.
    out = _simple_record(dict(base, doc_id="deadbeef" * 5, doc_id_source="content"))
    assert "doc_id" in out and out["doc_id"] is None

    # ③ 경로 해시 폴백 — 마찬가지로 None.
    out = _simple_record(dict(base, doc_id="0123456789" * 4, doc_id_source="path"))
    assert out["doc_id"] is None

    # ④ --no-doc-id 로 축을 아예 안 쓴 실행 — 키가 없다("못 얻음"과 갈라야 한다).
    assert "doc_id" not in _simple_record(base)


#------------------------------------------------------------------
# 축약본의 칸 차례는 전체 레코드의 앞부분과 같다
#=> file · hash · doc_id — 신원 세 칸이 두 모양 모두에서 맨 앞에 온다. 그 뒤는
#   같아야 "축약본은 전체의 앞부분만 떼어낸 것"이라는 약속이 유지된다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_simple_record_field_order_matches_full():
    from csoclassify.cli import _REC_ORDER
    rec = {"file": "a.hwp", "security": {"grade": "C"}, "hash": "deadbeef",
           "doc_id": "SF_1", "doc_id_source": "sfile_id",
           "labels": {"doctype": {"values": [{"dc_id": "DC_001"}]}}}
    keys = list(_simple_record(rec))
    # --simple 은 받는 쪽(문서중앙화)과의 계약이라 모양을 바꾸지 않았다.
    assert keys == ["file", "hash", "doc_id", "grade", "doctype"], keys
    # [2026-09-10] 전체 레코드는 축이 최상위로 올라가면서 grade·doctype 이
    # security·doctype 두 덩어리 안으로 들어갔다. 그래서 "축약본 = 전체의 앞
    # 다섯 칸"이라는 예전 약속은 더 이상 성립하지 않는다. 대신 두 모양 모두
    # 신원 세 칸으로 시작한다는 것은 그대로 지킨다 — 사람이 어느 쪽을 보든
    # "이게 어느 문서인가"를 같은 자리에서 읽는다.
    assert list(_REC_ORDER[:3]) == keys[:3], _REC_ORDER[:3]


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
