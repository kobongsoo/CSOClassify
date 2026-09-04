# -*- coding: utf-8 -*-
"""HTML 본문 추출 — 표 셀이 이어붙지 않는가.

이 시험이 지키는 것은 하나다. 표는 `tr` 로만 끊으면 같은 행의 셀 값이 구분자 없이
이어붙는다.

    <td>900101-1234568</td><td>010-1234-5678</td>  →  900101-1234568010-1234-5678

그러면 검출기의 경계 검사(앞뒤가 숫자면 거부)에 걸려 **두 값이 함께 사라진다**.
실측에서 PII 가 표로 빼곡한 문서 하나가 사이냅 대비 검출이 37% 적었는데, 추출
글자수는 5.5% 차이뿐이었다 — '덜 뽑아서'가 아니라 '이어붙여서'였다(2026-09-04).

hybrid 를 거치지 않고 HtmlTextExtractor 만 직접 부른다. 추출기 하나의 동작을 보는
시험이라 사슬(hybrid)을 끼우면 무엇이 깨졌는지가 흐려진다.
"""
import re

from csoclassify.extract.html_text import HtmlTextExtractor


#------------------------------------------------------------------
# 파일 하나 만들어 추출한다
#=> 시험마다 반복되는 세 줄을 묶는다.
#
# -in: tmp_path = pytest 임시 폴더
# -in: html     = 파일에 쓸 HTML 문자열
#
# -out: str = 추출된 본문
# -out: error = 없음
#------------------------------------------------------------------
def _extract(tmp_path, html):
    p = tmp_path / "t.html"
    p.write_text(html, encoding="utf-8")
    return HtmlTextExtractor().extract(str(p))


#------------------------------------------------------------------
# 같은 행의 셀 값이 이어붙지 않는다 (핵심 회귀)
#=> 두 값 사이에 구분자가 있어야 검출기가 각각을 온전한 값으로 본다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 값이 이어붙으면 AssertionError
#------------------------------------------------------------------
def test_표_셀이_이어붙지_않는다(tmp_path):
    t = _extract(tmp_path,
                 "<table><tr><td>900101-1234568</td><td>010-1234-5678</td></tr></table>")
    assert "900101-1234568010-1234-5678" not in t, "셀 값이 이어붙었다"
    assert "900101-1234568" in t and "010-1234-5678" in t


#------------------------------------------------------------------
# th(머리칸)도 마찬가지다
#=> 머리행은 th 를 쓴다. td 만 고치면 머리행에서 같은 문제가 남는다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 값이 이어붙으면 AssertionError
#------------------------------------------------------------------
def test_머리칸도_이어붙지_않는다(tmp_path):
    t = _extract(tmp_path, "<table><tr><th>이름</th><th>연락처</th></tr></table>")
    assert "이름연락처" not in t
    assert "이름" in t and "연락처" in t


#------------------------------------------------------------------
# 여러 행·여러 칸에서도 값이 모두 살아남는다
#=> 실제 문서는 표가 크다. 한 칸만 되는 것이 아니라 전부 분리돼야 한다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 하나라도 사라지면 AssertionError
#------------------------------------------------------------------
def test_큰_표에서도_값이_모두_남는다(tmp_path):
    rows = "".join(
        f"<tr><td>91010{i}-123456{i}</td><td>010-1234-000{i}</td></tr>" for i in range(5))
    t = _extract(tmp_path, f"<table>{rows}</table>")
    for i in range(5):
        assert f"91010{i}-123456{i}" in t
        assert f"010-1234-000{i}" in t
    # 붙어 버린 흔적(숫자-숫자가 구분자 없이 이어진 자리)이 없어야 한다.
    assert not re.search(r"\d{6}-\d{7}0\d{2}-", t)


#------------------------------------------------------------------
# script·style 안 내용은 여전히 버린다 (기존 동작 회귀 방지)
#=> 셀 분리를 넣으면서 버릴 구역 처리가 흔들리면 코드·CSS 가 본문으로 새어 들어온다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 코드가 본문에 섞이면 AssertionError
#------------------------------------------------------------------
def test_script_style_는_그대로_버린다(tmp_path):
    t = _extract(tmp_path,
                 "<html><body><header>회사 소개</header>"
                 "<p>담당자 연락처입니다.</p>"
                 "<script>var x='숨은코드';</script>"
                 "<style>.a{color:red}</style></body></html>")
    assert "숨은코드" not in t and "color:red" not in t
    # <header> 는 버릴 태그가 아니다 — head 와 이름이 겹친다고 본문을 지우면 안 된다.
    assert "회사 소개" in t and "담당자 연락처입니다." in t
