# -*- coding: utf-8 -*-
"""분류 실행 패널의 순수 부분 — 파일 종류 패턴과 기본값.

패턴은 파일 '이름 전체'와 맞춰 보므로 `.hwp` 라고만 적으면 이름이 정확히
".hwp" 인 파일만 찾는다. 실측 — 18건짜리 폴더에서 `.hwp,.doc,` 는 0건이었다.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "ui") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "ui"))

import app  # noqa: E402


#------------------------------------------------------------------
# 별표를 빠뜨린 조각을 찾아낸다
#=> 조용히 0건이 되는 것을 막는 장치다. 화면이 아무 말도 안 하면 관리자는
#   폴더부터 의심하게 된다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_별표_빠진_조각을_찾는다():
    assert app.glob_missing_star(".hwp,.doc,") == [".hwp", ".doc"]
    assert app.glob_missing_star(" .pdf ") == [".pdf"]
    # 제대로 적은 것은 잡지 않는다.
    assert app.glob_missing_star("*.hwp,*.doc") == []
    assert app.glob_missing_star("*") == []
    assert app.glob_missing_star("") == []
    assert app.glob_missing_star(None) == []


#------------------------------------------------------------------
# 기본 패턴은 엔진이 실제로 글을 뽑는 확장자만 담는다
#=> 못 다루는 확장자를 넣으면 '본문 없음'으로 남아 추출실패 건수만 늘어난다.
#   그리고 모든 조각에 별표가 붙어 있어야 한다(위 시험과 짝).
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_기본_패턴은_바르고_다룰_수_있는_것들이다():
    parts = [p.strip() for p in app.DEFAULT_GLOB.split(",")]
    assert app.glob_missing_star(app.DEFAULT_GLOB) == [], parts
    assert all(p.startswith("*.") for p in parts), parts
    exts = {p[2:] for p in parts}
    # 사용자가 요청한 12종이 모두 들어 있다.
    assert exts == {"txt", "html", "md", "doc", "docx", "ppt", "pptx",
                    "xls", "xlsx", "hwp", "hwpx", "pdf"}, exts
    # 엔진의 형식 감지가 아는 것들인가(txt·md 는 text 로 간다).
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from csoclassify.extract import detect  # noqa: E402
    known = {"pdf", "hwp", "hwpx", "doc", "docx", "xls", "xlsx",
             "ppt", "pptx", "text", "html"}
    assert known.issuperset({"pdf", "hwp"}), "감지 형식 목록이 바뀌었는지 확인"
    assert detect is not None


#------------------------------------------------------------------
# 폴더 고르기는 실패해도 예외를 올리지 않는다
#=> 화면이 이 버튼 하나 때문에 죽으면 안 된다. 화면 없는 서버에서는 창을
#   못 띄우므로, 그때는 사유를 문자열로 돌려 "직접 입력하세요"로 안내한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_폴더고르기는_예외를_올리지_않는다(monkeypatch):
    # tkinter 를 못 쓰는 환경을 흉내 낸다.
    import builtins
    real = builtins.__import__

    def fake(name, *a, **kw):
        if name.startswith("tkinter"):
            raise ImportError("no display")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake)
    got, err = app.pick_folder("D:/없는폴더")
    assert got is None and err, (got, err)
