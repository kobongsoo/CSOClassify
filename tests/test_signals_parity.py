#------------------------------------------------------------------
# 두 판(Python·Rust)의 signals 블록이 완전히 같은지 — 회귀 시험
#=> 이 프로젝트는 두 구현이 "같은 답"을 내야 한다. 그런데 등급이 같으면
#   신호 속 숫자가 갈려도 아무도 모른다 — 오류도 경고도 안 난다.
#   실제로 2026-09-10 까지 그렇게 갈려 있었다:
#
#     · Rust 의 HTML 추출이 표 셀(td·th)을 블록 경계로 안 봐서 같은 행의
#       값이 이어붙었다 → PII 검출 건수가 달랐다
#       (ip_address 7↔5 · biz_reg_no 8↔6 · phone_mobile 7↔8)
#     · Rust 의 stamp·sensitive 히트에 term·confidence·seed_eligible 이 없었다
#     · L1 히트의 차례가 달랐다(Python 은 ko-pii 검출 차례, Rust 는 규칙 차례)
#
#   등급까지 갈리지는 않았지만, 임계값 근처에서는 같은 문서가 판마다 다른
#   등급을 받게 되는 자리였다. 그 조용한 어긋남을 여기서 못 박는다.
#
#   [왜 표가 있는 문서를 쓰나] 위 세 가지가 모두 표에서 터졌다. 표 없는
#   문서로는 이 시험이 통과해도 아무것도 지켜 주지 못한다.
#------------------------------------------------------------------

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS_EXE = os.path.join(ROOT, "Rust", "target", "release", "MpowerClassify-rs.exe")
POLICY = os.path.join(ROOT, "resources", "policy", "cso_rule.yaml")

# 표 안에 PII 를 촘촘히 넣은 문서. 셀 경계를 안 끊으면 값들이 이어붙어
# 검출이 통째로 사라진다 — 그게 실제로 났던 사고다.
#
# [번호는 체크섬을 통과하는 값이어야 한다] 주민번호·사업자등록번호는 검출기가
# 검증자리까지 계산한다. 아무 숫자나 넣으면 "엔진이 못 잡았다"가 아니라 "픽스처가
# 틀렸다"인데, 실패 메시지만 봐서는 구분이 안 된다. 아래 값은 모두 계산해 맞춘
# 가짜 번호다(실존 인물·사업자와 무관).
FIXTURE_HTML = """<!doctype html>
<html><head><title>연락처 대장</title><style>td{border:1px solid}</style></head>
<body>
<h1>대외비 — 관계자 외 열람금지</h1>
<p>본 문서는 기밀이며 외부 반출을 금합니다.</p>
<table>
  <tr><th>이름</th><th>주민등록번호</th><th>휴대전화</th><th>사업자등록번호</th></tr>
  <tr><td>홍길동</td><td>900101-1234568</td><td>010-1234-5678</td><td>220-81-62517</td></tr>
  <tr><td>김철수</td><td>880202-2345671</td><td>010-9876-5432</td><td>106-81-11522</td></tr>
</table>
<table>
  <tr><th>메일</th><th>아이피</th></tr>
  <tr><td>hong@example.co.kr</td><td>192.168.10.11</td></tr>
  <tr><td>kim@example.co.kr</td><td>10.0.0.7</td></tr>
</table>
<p>진단서와 병력 등 건강정보가 포함되어 있습니다.</p>
</body></html>
"""


#------------------------------------------------------------------
# 한 엔진을 돌려 첫 레코드를 돌려준다
#=> 두 판을 같은 인자·같은 규칙셋으로 돌려야 비교에 뜻이 있다.
#
# -in: engine   = "python" | "rust"
# -in: doc_dir  = 분류할 폴더
#
# -out: dict = 첫 결과 레코드
# -out: error = 실행 실패 시 AssertionError(표준오류를 함께 보여 준다)
#------------------------------------------------------------------
def _run(engine, doc_dir):
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8")
    r = subprocess.run(
        cmd + ["--dir", str(doc_dir), "--rule-only", "--rules", POLICY,
               "--format", "jsonl", "--nosummary"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    recs = [json.loads(l) for l in r.stdout.splitlines()
            if l.strip().startswith("{") and '"file"' in l]
    assert recs, "%s: 레코드가 없다 :: %s" % (engine, r.stderr[-400:])
    return recs[0]


#------------------------------------------------------------------
# 표가 있는 HTML 에서 두 판의 signals 가 완전히 같다
#=> 값만이 아니라 **칸 이름과 차례까지** 같아야 한다. 두 판의 결과 파일을
#   그대로 견줄 수 있어야 "같은 답을 낸다"고 말할 수 있다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
def test_두_판의_signals가_완전히_같다(tmp_path):
    (tmp_path / "연락처대장.html").write_text(FIXTURE_HTML, encoding="utf-8")
    py = _run("python", tmp_path)["why"]["security"]["signals"]
    rs = _run("rust", tmp_path)["why"]["security"]["signals"]

    assert list(py) == list(rs), ("신호 차례가 다르다", list(py), list(rs))
    for name in py:
        a, b = py[name], rs[name]
        assert list(a) == list(b), (name, "신호 칸 차례", list(a), list(b))
        ha, hb = a.get("hits") or [], b.get("hits") or []
        assert [h["id"] for h in ha] == [h["id"] for h in hb], \
            (name, "히트 차례", [h["id"] for h in ha], [h["id"] for h in hb])
        for x, y in zip(ha, hb):
            assert list(x) == list(y), (name, x.get("id"), "히트 칸 차례", list(x), list(y))
            assert x == y, (name, x.get("id"), "히트 값", x, y)
        assert a == b, (name, a, b)


#------------------------------------------------------------------
# 표 셀이 이어붙지 않는다 — 이 시험이 잡으려는 원래 사고
#=> <td>주민번호</td><td>전화</td> 를 구분자 없이 이으면 "9001011234568010…"
#   이 되어 검출기의 경계 검사에 걸려 **두 값이 함께 사라진다**.
#   건수를 직접 못 박아, 한쪽 판만 조용히 뒤로 밀리는 일을 막는다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_표_안의_PII가_이어붙어_사라지지_않는다(engine, tmp_path):
    (tmp_path / "연락처대장.html").write_text(FIXTURE_HTML, encoding="utf-8")
    sig = _run(engine, tmp_path)["why"]["security"]["signals"]
    counts = {h["id"]: h["count"] for h in (sig.get("rule") or {}).get("hits") or []}
    # 표에 2건씩 넣었다. 셀이 이어붙으면 0건이 된다.
    for rule_id in ("rrn", "phone_mobile", "biz_reg_no", "email", "ip_address"):
        assert counts.get(rule_id, 0) >= 2, \
            "%s: %s 가 %d건 — 표 셀이 이어붙었을 수 있다 (전체: %r)" % (
                engine, rule_id, counts.get(rule_id, 0), counts)


#------------------------------------------------------------------
# 스탬프 히트가 '어느 문구였나'를 남긴다
#=> term 은 다른 칸에서 얻을 수 없는 근거다. Rust 판에 이 칸이 없어
#   전파를 한 번 거친 문서가 "왜 스탬프로 인정됐는지"를 설명 못 했다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_스탬프_히트가_근거_문구를_남긴다(engine, tmp_path):
    (tmp_path / "연락처대장.html").write_text(FIXTURE_HTML, encoding="utf-8")
    sig = _run(engine, tmp_path)["why"]["security"]["signals"]
    stamp = sig.get("stamp") or {}
    hits = stamp.get("hits") or []
    assert hits, "%s: 스탬프가 안 잡혔다 (문서 머리에 '대외비'가 있다)" % engine
    for h in hits:
        assert h.get("term"), ("%s: 어느 문구가 스탬프였는지가 없다" % engine, h)
        # 규칙별 값이라 신호 전체값(max/any)과 다를 수 있다 — 파생값이 아니다.
        assert isinstance(h.get("confidence"), float), (engine, h)
        assert isinstance(h.get("seed_eligible"), bool), (engine, h)
