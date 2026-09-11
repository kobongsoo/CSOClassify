#------------------------------------------------------------------
# HTML 추출 본문이 두 판에서 같은지 — 회귀 시험
#=> 지금까지 파리티 시험들은 .txt 픽스처를 써서 **HTML 추출 경로를 안 탔다**.
#   그 사이 두 판의 본문이 조용히 갈려 있었다(2026-09-11 실측, doc/ 5건):
#
#    ① `<pre>` 가 블록 태그로 잘못 잡혔다 — Rust 의 정규식이 이름 뒤에 경계를
#       두지 않아 `p` + `re` 로 매칭됐다. 파이썬은 이름 집합으로 판정한다.
#       전수 확인: pre·param·picture·progress·track·thead·link 가 걸렸다.
#    ② 이름 엔티티를 안 풀었다 — `&mdash;`·`&middot;` 가 글자 그대로 남았다.
#       파이썬은 HTMLParser 라 HTML5 이름 엔티티를 전부 푼다.
#    ③ `&nbsp;` 를 일반 공백으로 풀었다 — 파이썬은 U+00A0 이다.
#
#   등급·신호까지 갈리지는 않았지만 pii 좌표가 어긋났고, 규칙 용어가 그 경계에
#   걸리면 판정까지 갈릴 수 있는 자리였다.
#
#   [무엇을 지키나] 값 몇 개가 아니라 **추출 본문 전체**를 견준다. 무엇이 갈릴지
#   미리 알 수 없으니, 결과물을 통째로 고정하는 편이 확실하다.
#------------------------------------------------------------------

import glob
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS_EXE = os.path.join(ROOT, "Rust", "target", "release", "MpowerClassify-rs.exe")
POLICY = os.path.join(ROOT, "resources", "policy", "cso_rule.yaml")

# 갈렸던 자리를 전부 담는다. 하나라도 빠지면 그만큼 안 지켜진다.
FIXTURE = """<!doctype html>
<html><head><title>표기 시험</title><style>p{margin:0}</style></head>
<body>
<p>앞 문단입니다.</p>
  <pre><code>010-1234-5678 들여쓴 줄</code></pre>
<p>대시 &mdash; 가운뎃점 &middot; 줄임 &hellip; 화살 &rarr; 따옴표 &ldquo;인용&rdquo;</p>
<p>공백&nbsp;붙임 · 부등호 &lt;태그&gt; · 앰퍼샌드 &amp; · 숫자 &#49;&#x32;</p>
<table>
  <thead><tr><th>이름</th><th>번호</th></tr></thead>
  <tbody><tr><td>홍길동</td><td>900101-1234568</td></tr></tbody>
</table>
<p>모르는 엔티티 &qqqq; 는 그대로 둔다</p>
<p>줄바꿈<br/>뒤<br>끝</p>
</body></html>
"""


#------------------------------------------------------------------
# 한 엔진을 돌려 추출 본문을 받아 온다
#=> --textsave 가 남기는 것이 '도구가 실제로 보고 판정한 그 텍스트'다.
#
# -in: engine  = "python" | "rust"
# -in: doc_dir = 분류할 폴더
# -in: save    = 본문을 남길 폴더
#
# -out: str = 추출·정제가 끝난 본문
# -out: error = 본문이 안 남으면 AssertionError(표준오류를 함께 보여 준다)
#------------------------------------------------------------------
def _extract(engine, doc_dir, save):
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8")
    r = subprocess.run(
        cmd + ["--dir", str(doc_dir), "--rule-only", "--rules", POLICY,
               "--format", "jsonl", "--nosummary", "--textsave", str(save)],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    files = sorted(glob.glob(os.path.join(str(save), "*.txt")))
    assert files, "%s: 추출 본문이 안 남았다 :: %s" % (engine, r.stderr[-400:])
    with open(files[0], encoding="utf-8") as f:
        return f.read()


#------------------------------------------------------------------
# 두 판의 추출 본문이 글자 하나까지 같다
#=> 본문이 다르면 그 위에서 도는 모든 것(검출·좌표·업무분류 낱말)이 흔들린다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
def test_두_판의_HTML_추출_본문이_같다(tmp_path):
    doc = tmp_path / "docs"
    doc.mkdir()
    (doc / "표기.html").write_text(FIXTURE, encoding="utf-8")
    py = _extract("python", doc, tmp_path / "p")
    rs = _extract("rust", doc, tmp_path / "r")
    assert py == rs, ("추출 본문이 다르다", repr(py[:300]), repr(rs[:300]))


#------------------------------------------------------------------
# 갈렸던 자리들이 실제로 제대로 나오는지 하나씩 못 박는다
#=> 위 전체 대조는 '두 판이 같다'만 보장한다. 둘 다 똑같이 틀리면 통과한다.
#   그래서 기대값을 직접 적는다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_엔티티와_태그_경계가_제대로_풀린다(engine, tmp_path):
    doc = tmp_path / "docs"
    doc.mkdir()
    (doc / "표기.html").write_text(FIXTURE, encoding="utf-8")
    t = _extract(engine, doc, tmp_path / "out")

    # ① 이름 엔티티가 글자로 풀린다.
    for raw, want in (("&mdash;", "—"), ("&middot;", "·"), ("&hellip;", "…"),
                      ("&rarr;", "→"), ("&ldquo;", "“"), ("&rdquo;", "”")):
        assert raw not in t, "%s: %s 가 안 풀렸다" % (engine, raw)
        assert want in t, "%s: %s 가 %s 로 안 바뀌었다" % (engine, raw, want)

    # ② &nbsp; 는 U+00A0 이다(일반 공백이 아니다).
    assert "공백 붙임" in t, "%s: &nbsp; 가 U+00A0 이 아니다" % engine

    # ③ 숫자 엔티티도 푼다.
    assert "숫자 12" in t, "%s: 숫자 엔티티가 안 풀렸다" % engine

    # ④ <pre> 는 블록이 아니다 — 앞 들여쓰기 공백이 살아 있어야 한다.
    #    블록으로 잘못 보면 그 공백이 혼자 남은 줄이 되어 지워진다.
    assert "\n 010-1234-5678" in t, (
        "%s: <pre> 를 블록으로 봤다(앞 공백이 사라졌다)" % engine, repr(t[:200]))

    # ⑤ 모르는 엔티티는 지우지 않고 그대로 둔다(지우면 본문이 조용히 사라진다).
    assert "&qqqq;" in t, "%s: 모르는 엔티티를 지웠다" % engine

    # ⑥ 표 셀은 끊어진다 — 안 끊으면 값이 이어붙어 검출이 통째로 사라진다.
    assert "홍길동900101" not in t, "%s: 표 셀이 이어붙었다" % engine
