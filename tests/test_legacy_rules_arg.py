#------------------------------------------------------------------
# 옛 이름 --rules / --save-text 를 없앤 자리 — 회귀 시험
#=> 보안등급 규칙셋 인자를 --rules 에서 --cso-rules 로 바꿨다(2026-09-28).
#   업무분류 쪽이 --doc-rules 라, 짝이 맞지 않아 어느 쪽을 주는지 헷갈렸다.
#   본문 저장의 옛 이름 --save-text 도 같이 없앴다.
#
#   [조용히 받아 주지 않는다] 옛 이름을 그대로 받아 주면 두 이름이 같은 자리를
#   가리키는 상태가 굳고, 아무도 새 이름으로 옮기지 않는다. 그렇다고 '모르는
#   인자'로 흘리면 왜 안 되는지 알 수 없다. 그래서 '없어진 옵션'이라고 사실대로
#   답하고 무엇을 쓰면 되는지 한 줄로 알린다(unsupported_option · 종료 3).
#
#   [두 판을 함께 본다] 한쪽만 바뀌면 배치가 두 판을 같은 방법으로 다루지
#   못한다. 이름·오류코드·안내 문구가 모두 같아야 한다.
#------------------------------------------------------------------

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS_EXE = os.path.join(ROOT, "Rust", "target", "release", "MpowerClassify-rs.exe")
POLICY = os.path.join(ROOT, "resources", "policy", "cso_rule.yaml")

FIXTURE = """사내 대외비 자료
이 문서는 내부 검토용입니다.
"""

# 없어진 이름 → 대신 쓸 이름. 두 판이 같은 짝을 알려 줘야 한다.
LEGACY = [("--rules", "--cso-rules"), ("--save-text", "--textsave")]


#------------------------------------------------------------------
# 한 엔진을 주어진 인자로 돌린다
#=> 두 판을 같은 자리에서 부르고, 종료코드까지 그대로 돌려준다.
#
# -in: engine = "python" | "rust"
# -in: args   = 덧붙일 인자 목록
#
# -out: CompletedProcess = 종료코드·stdout·stderr 를 담은 결과
# -out: error = 없음(실패해도 그대로 돌려준다 — 부르는 쪽이 판단한다)
#------------------------------------------------------------------
def _run(engine, args):
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8")
    return subprocess.run(cmd + args, cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8")


#------------------------------------------------------------------
# stdout 마지막 오류 JSON 에서 (code, kind) 를 꺼낸다
#=> --json-errors 를 줬을 때 두 판이 내는 계약을 본다.
#
# -in: out = 표준출력 전체
#
# -out: (code, kind) = 오류 번호와 이름
# -out: error = 오류 JSON 이 없으면 AssertionError
#------------------------------------------------------------------
def _err(out):
    lines = [l for l in out.splitlines() if l.strip().startswith("{")]
    assert lines, out[-500:]
    e = json.loads(lines[-1])["error"]
    return e["code"], e["kind"]


#------------------------------------------------------------------
# 새 이름 --cso-rules 가 동작한다
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_새_이름_cso_rules_로_분류된다(engine, tmp_path):
    (tmp_path / "문서.txt").write_text(FIXTURE, encoding="utf-8")
    r = _run(engine, ["--dir", str(tmp_path), "--rule-only", "--cso-rules", POLICY,
                      "--simple", "--format", "jsonl", "--nosummary"])

    assert r.returncode == 0, (engine, r.stderr[-500:])
    recs = [json.loads(l) for l in r.stdout.splitlines()
            if l.strip().startswith("{") and '"file"' in l]
    assert recs and recs[0].get("grade"), (engine, r.stderr[-500:])


#------------------------------------------------------------------
# 옛 이름을 주면 '없어졌습니다' 로 끝난다
#=> 조용히 무시하면 부른 쪽은 옛 명령이 먹힌 줄 안다. 그 자리에서 끝낸다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.parametrize("engine", ["python", "rust"])
@pytest.mark.parametrize("old,new", LEGACY)
def test_옛_이름은_없어진_옵션으로_끝난다(engine, old, new, tmp_path):
    (tmp_path / "문서.txt").write_text(FIXTURE, encoding="utf-8")
    r = _run(engine, ["--dir", str(tmp_path), "--rule-only", old, POLICY,
                      "--json-errors"])

    assert r.returncode == 3, (engine, old, r.returncode, r.stderr[-400:])
    assert _err(r.stdout) == (1012, "unsupported_option"), (engine, old)
    both = r.stdout + r.stderr
    assert f"{old} 는 없어졌습니다" in both, (engine, old, both[-400:])
    # 무엇으로 바꿔야 하는지 그 자리에서 알려 줘야 한다.
    assert new in both, (engine, old, "새 이름을 안 알려 준다", both[-400:])


#------------------------------------------------------------------
# 값 없이 옛 이름만 줘도 죽지 않는다
#=> 값을 받던 옵션이라, 값을 안 주면 '값이 없습니다'로 엉뚱하게 끝날 수 있다.
#   없어진 옵션이라는 사실이 먼저다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.parametrize("engine", ["python", "rust"])
@pytest.mark.parametrize("old,new", LEGACY)
def test_옛_이름은_값이_없어도_같은_안내로_끝난다(engine, old, new):
    r = _run(engine, [old, "--json-errors"])

    assert r.returncode == 3, (engine, old, r.returncode, r.stderr[-400:])
    assert _err(r.stdout) == (1012, "unsupported_option"), (engine, old)


#------------------------------------------------------------------
# 도움말에 옛 이름이 남아 있지 않다
#=> 없앤 이름이 도움말에 남아 있으면, 읽은 사람이 그대로 따라 쓴다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_도움말에_옛_이름이_없다(engine):
    r = _run(engine, ["--help"])
    text = r.stdout + r.stderr

    assert "--cso-rules" in text, (engine, "새 이름이 도움말에 없다")
    for old, _ in LEGACY:
        assert f"{old} " not in text and f"{old}\n" not in text, (engine, old)
