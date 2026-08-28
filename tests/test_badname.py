"""파일 이름이 UTF-8 이 아닐 때도 배치가 죽지 않는지.

리눅스에서 파일 이름은 그냥 바이트열이라 UTF-8 이 아닐 수 있다(윈도우에서 만든
CP949 이름을 그대로 복사한 경우 등). 파이썬은 그런 이름을 읽을 때 surrogateescape
로 '짝 없는 대리 문자'(\\udcXX)를 끼워 넣는데, 그 문자열을 UTF-8 파일에 쓰면
UnicodeEncodeError 가 난다.

예전에는 그 예외가 그대로 올라와 **배치 전체가 중단**되고, 그때까지 분류한 결과까지
통째로 사라졌다. 파일 하나의 이름 문제로 잃을 만한 것이 아니므로, 이름만 U+FFFD 로
바꿔 표시하고 나머지는 정상 처리하도록 고쳤다. 이 파일은 그 회귀를 막는다.
"""

import io
import json

from csoclassify.cli import safe_text
from csoclassify.output import RecordWriter, dumps_safe


#------------------------------------------------------------------
# 테스트용 '깨진 이름' 만들기
#=> CP949 로 인코딩된 한글 바이트를 UTF-8 로 읽으려다 실패한 상황을 그대로 재현한다.
#   surrogateescape 는 파이썬이 파일명을 읽을 때 실제로 쓰는 방식이라, 여기서
#   만든 문자열은 리눅스에서 os.listdir 이 돌려주는 것과 같은 모양이다.
#
# -in: 없음
#
# -out: str = 짝 없는 대리 문자가 섞인 경로 문자열
# -out: error = 없음
#------------------------------------------------------------------
def _bad_name():
    return "/data/" + b"\xbf\xeb\xbf\xaa".decode("utf-8", "surrogateescape") + "_계약서.txt"


#------------------------------------------------------------------
# 전제 확인 — 이 문자열이 정말 UTF-8 로 인코딩 불가한지
#=> 이 전제가 깨지면(파이썬이 동작을 바꾸는 등) 아래 테스트들이 아무것도 검증하지
#   못한 채 통과해 버린다. 그래서 전제부터 못박아 둔다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_전제_깨진이름은_utf8로_인코딩_불가():
    import pytest
    with pytest.raises(UnicodeEncodeError):
        _bad_name().encode("utf-8")


#------------------------------------------------------------------
# safe_text — 깨진 자리만 바꾸고 나머지 글자는 살린다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_safe_text는_인코딩가능하게_만든다():
    s = safe_text(_bad_name())
    s.encode("utf-8")                 # 예외가 나면 실패
    # 멀쩡한 부분은 그대로 남아야 어느 파일인지 사람이 알아볼 수 있다.
    assert s.startswith("/data/") and s.endswith("_계약서.txt")


#------------------------------------------------------------------
# safe_text — 정상 문자열은 손대지 않는다(대부분의 경우)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_safe_text는_정상문자열을_바꾸지_않는다():
    for ok in ["용역계약서.txt", "D:/collected/a b/c.hwp", "", "ascii_only.pdf"]:
        assert safe_text(ok) == ok
    # 문자열이 아니면 그대로 돌려준다(None 경로 등).
    assert safe_text(None) is None


#------------------------------------------------------------------
# dumps_safe — 깨진 이름이 어디에 있어도(값·키·리스트) 쓸 수 있는 JSON 을 만든다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_dumps_safe는_어디에_있든_인코딩가능하게_만든다():
    bad = _bad_name()
    rec = {"file": bad, "grade": "S", "by_root": {bad: 1}, "list": [bad]}
    s = dumps_safe(rec, separators=(",", ":"))
    s.encode("utf-8")                 # 예외가 나면 실패
    # 구조가 깨지지 않아 다시 읽을 수 있어야 한다.
    assert json.loads(s)["grade"] == "S"


#------------------------------------------------------------------
# 회귀 핵심 — 깨진 이름 한 건이 있어도 나머지 레코드가 살아남는다
#=> 예전에는 첫 레코드에서 예외가 터져 파일에 아무것도 안 남았다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_깨진이름_하나가_배치를_중단시키지_않는다(tmp_path):
    out = tmp_path / "result.jsonl"
    with io.open(out, "w", encoding="utf-8") as f:
        w = RecordWriter(f, "jsonl", multi=True)
        w.write_record({"file": _bad_name(), "grade": "S"})
        w.write_record({"file": "정상문서.txt", "grade": "O"})
        w.close()

    lines = [json.loads(l) for l in io.open(out, encoding="utf-8") if l.strip()]
    # 두 건 모두 남아야 한다 — 하나가 깨졌다고 나머지를 버리지 않는다.
    assert len(lines) == 2
    assert lines[1]["file"] == "정상문서.txt"
    assert lines[1]["grade"] == "O"


#------------------------------------------------------------------
# json 배열 모드에서도 유효한 JSON 이 나온다(뷰어가 그대로 열 수 있어야 한다)
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_json배열_모드도_유효하다(tmp_path):
    out = tmp_path / "result.json"
    with io.open(out, "w", encoding="utf-8") as f:
        w = RecordWriter(f, "json", multi=True)
        w.write_record({"file": _bad_name(), "grade": "S"})
        w.write_record({"file": "정상문서.txt", "grade": "O"})
        w.close()

    data = json.loads(io.open(out, encoding="utf-8").read())
    assert isinstance(data, list) and len(data) == 2
