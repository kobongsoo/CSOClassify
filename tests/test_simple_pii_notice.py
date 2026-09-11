#------------------------------------------------------------------
# --simple 과 --with-pii 를 같이 줬을 때 — 회귀 시험
#=> 축약본에 pii 를 안 싣는 것은 **의도**다. --simple 은 연동용(문서중앙화)
#   형식이고, 이 도구의 불변식이 "매칭된 원문 PII 값은 결과에 저장하지 않는다"
#   이기 때문이다. 주민번호 원본이 연동 경로로 흘러가면 안 된다.
#
#   문제는 그 사실을 **말해 주지 않던 것**이었다(2026-09-11). 사용자가
#   --with-pii 를 명시해서 줬는데 아무 일도 안 일어나고 아무 말도 없으면
#   "줬으니 받았겠지" 라고 믿게 된다.
#
#   특히 --out 이 없으면 감사용 <out>.full 사이드카가 아예 안 만들어져
#   **원문 값이 어디에도 안 남는다**. 그 경로를 여기서 못 박는다.
#
#   [두 판을 함께 본다] 문구가 갈리면 한쪽 사용자만 안내를 못 받는다.
#------------------------------------------------------------------

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS_EXE = os.path.join(ROOT, "Rust", "target", "release", "MpowerClassify-rs.exe")
POLICY = os.path.join(ROOT, "resources", "policy", "cso_rule.yaml")

# 주민번호·전화번호가 든 문서. 검출이 0건이면 이 시험이 아무것도 지켜 주지 못한다.
FIXTURE = """연락처 대장
홍길동 900101-1234568 010-1234-5678
김철수 880202-2345671 010-9876-5432
"""


#------------------------------------------------------------------
# 한 엔진을 돌려 (표준출력, 표준오류) 를 돌려준다
#
# -in: engine  = "python" | "rust"
# -in: doc_dir = 분류할 폴더
# -in: extra   = 덧붙일 인자 목록
#
# -out: (stdout, stderr)
# -out: error = 없음(실패해도 그대로 돌려준다 — 부르는 쪽이 판단한다)
#------------------------------------------------------------------
def _run(engine, doc_dir, extra):
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8")
    r = subprocess.run(
        cmd + ["--dir", str(doc_dir), "--rule-only", "--rules", POLICY,
               "--format", "jsonl", "--nosummary"] + extra,
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    return r.stdout, r.stderr


#------------------------------------------------------------------
# --out 없이 --simple --with-pii 면 '아무 데도 안 남는다'고 알린다
#=> 가장 위험한 경로다. 사이드카가 없어 원문이 통째로 사라지는데,
#   예전에는 그 사실을 아무도 알려 주지 않았다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_out_없이_simple_with_pii_면_경고한다(engine, tmp_path):
    (tmp_path / "대장.txt").write_text(FIXTURE, encoding="utf-8")
    out, err = _run(engine, tmp_path, ["--simple", "--with-pii"])

    assert "아무 데도 남지 않습니다" in err, (engine, "경고가 없다", err[-500:])
    assert "--out" in err, (engine, "무엇을 하라는지 안 알려 준다", err[-500:])
    # 축약본에는 그대로 안 실린다(의도).
    recs = [json.loads(l) for l in out.splitlines()
            if l.strip().startswith("{") and '"file"' in l]
    assert recs, (engine, "레코드가 없다", err[-400:])
    assert "pii" not in recs[0], (engine, "축약본에 원문 PII 가 실렸다", list(recs[0]))


#------------------------------------------------------------------
# --out 이 있으면 '감사용 전체 파일에 있다'고 알리고, 실제로 거기 있다
#=> 알려 주기만 하고 실제로 없으면 더 나쁘다. 파일까지 열어 확인한다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_out_있으면_감사용_파일_위치를_알리고_거기_있다(engine, tmp_path):
    (tmp_path / "대장.txt").write_text(FIXTURE, encoding="utf-8")
    out_path = tmp_path / "r.jsonl"
    _out, err = _run(engine, tmp_path, ["--simple", "--with-pii", "--out", str(out_path)])

    assert "축약본에는 pii 를 싣지 않습니다" in err, (engine, "안내가 없다", err[-500:])
    full = tmp_path / "r.full.jsonl"
    assert full.is_file(), (engine, "감사용 전체 파일이 없다")

    # 축약본에는 없고, 감사용에는 있다.
    simple = [json.loads(l) for l in out_path.read_text(encoding="utf-8").splitlines()
              if l.strip().startswith("{") and '"file"' in l]
    whole = [json.loads(l) for l in full.read_text(encoding="utf-8").splitlines()
             if l.strip().startswith("{") and '"file"' in l]
    assert simple and "pii" not in simple[0], (engine, "축약본에 pii 가 실렸다")
    assert whole and whole[0].get("pii"), (engine, "감사용 파일에 pii 가 없다 — 안내가 거짓말이 된다")


#------------------------------------------------------------------
# --simple 없이 --with-pii 만 주면 안내하지 않는다
#=> 아무 문제가 없는 실행에 경고를 띄우면, 진짜 경고를 흘려보게 된다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_simple_없으면_안내하지_않는다(engine, tmp_path):
    (tmp_path / "대장.txt").write_text(FIXTURE, encoding="utf-8")
    out, err = _run(engine, tmp_path, ["--with-pii"])

    assert "축약본" not in err, (engine, "쓸데없는 경고가 나온다", err[-400:])
    recs = [json.loads(l) for l in out.splitlines()
            if l.strip().startswith("{") and '"file"' in l]
    assert recs and recs[0].get("pii"), (engine, "전체 출력에는 pii 가 있어야 한다")
