#------------------------------------------------------------------
# 정제 로직 단위 테스트
#=> clean_text 가 제어문자/페이지마커/과다공백/개행을 규칙대로 다듬는지 검증한다.
#------------------------------------------------------------------

from csoclassify.clean import clean_text


#------------------------------------------------------------------
# 제어문자 제거
#=> NUL 등 눈에 안 보이는 제어문자는 사라져야 한다(탭/개행 제외).
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_remove_control_chars():
    assert clean_text("A\x00B") == "AB"


#------------------------------------------------------------------
# 페이지 마커 제거
#=> '..PAGE:N' 라인은 기본적으로 제거되어야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_remove_page_marker():
    out = clean_text("본문1\n..PAGE:2\n본문2")
    assert "..PAGE" not in out
    assert "본문1" in out and "본문2" in out


#------------------------------------------------------------------
# 페이지 마커 보존 옵션
#=> remove_page_markers=False 면 마커가 남아야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_keep_page_marker():
    out = clean_text("본문\n..PAGE:2\n끝", remove_page_markers=False)
    assert "..PAGE:2" in out


#------------------------------------------------------------------
# 공백/개행 정규화
#=> 연속 공백은 1개로, 3줄 이상 빈 줄은 2줄로 축약되고 양끝 공백은 제거된다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_whitespace_normalize():
    assert clean_text("A   B") == "A B"
    assert clean_text("A\n\n\n\n\nB") == "A\n\nB"
    assert clean_text("  hi  \n") == "hi"


#------------------------------------------------------------------
# 빈 입력 방어
#=> None/빈 문자열은 빈 문자열을 돌려줘야 한다(예외 없음).
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_empty_input():
    assert clean_text(None) == ""
    assert clean_text("") == ""
