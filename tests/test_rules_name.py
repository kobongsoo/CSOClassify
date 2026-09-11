"""규칙셋 파일 이름 — cso_rule.yaml (2026-09-08, 옛 이름 cso_rules.yaml)

업무분류가 `doc_rule.yaml` 이므로 보안등급도 단수형으로 맞췄다.
여기서 지키는 것은 하나다 — **이미 배포된 폴더가 그대로 돌아야 한다.**
exe 옆에 옛 이름 파일이 있는 설치가 "규칙셋을 찾을 수 없습니다"로 죽으면,
이름 통일이라는 사소한 이득이 현장 중단이라는 큰 손실로 바뀐다.
"""

import io
import os
import subprocess
import sys

import pytest

from csoclassify.classify import rules

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS_EXE = os.path.join(ROOT, "Rust", "target", "release", "MpowerClassify-rs.exe")
POLICY = os.path.join(ROOT, "resources", "policy")


#------------------------------------------------------------------
# 새 이름이 정본이다
#=> 상수 두 개가 서로 바뀌면 "옛 이름을 쓰라"고 안내하면서 새 이름을 옛 것으로
#   부르는 뒤집힌 상태가 된다. 값 자체를 못박아 둔다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_이름_상수():
    assert rules.RULES_NAME == "cso_rule.yaml"
    assert rules.RULES_NAME_OLD == "cso_rules.yaml"


#------------------------------------------------------------------
# 저장소의 정책 파일이 새 이름으로 있다
#=> 코드가 새 이름을 찾는데 저장소에는 옛 이름만 있으면, 소스 실행이 곧바로
#   깨진다. 파일 이름 변경을 빠뜨리지 않게 시험으로 잡는다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_저장소_파일이_새_이름이다():
    assert os.path.isfile(os.path.join(POLICY, "cso_rule.yaml"))
    assert not os.path.isfile(os.path.join(POLICY, "cso_rules.yaml"))


#------------------------------------------------------------------
# 새 이름을 먼저 쓴다 — 둘 다 있으면 새 이름이 이긴다
#=> 옮기는 중에 두 파일이 함께 있을 수 있다. 그때 옛 것을 읽으면 "새로 만든
#   규칙이 왜 안 먹지"가 되는데, 원인을 찾기 매우 어렵다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_둘_다_있으면_새_이름이_이긴다(tmp_path, monkeypatch):
    (tmp_path / "cso_rule.yaml").write_text("new", encoding="utf-8")
    (tmp_path / "cso_rules.yaml").write_text("old", encoding="utf-8")
    monkeypatch.setenv("CSOCLASSIFY_POLICY_DIR", str(tmp_path))
    got = rules.default_rules_path()
    assert os.path.basename(got) == "cso_rule.yaml", got


#------------------------------------------------------------------
# 옛 이름만 있으면 그것을 쓰되, 조용히 쓰지 않는다
#=> 이미 배포된 폴더가 이 모습이다. 그대로 돌아야 하지만, 아무 말도 없으면
#   "언제까지 이대로 두어도 되나"를 아무도 모른 채 어느 날 갑자기 멈춘다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_옛_이름만_있으면_쓰고_알린다(tmp_path, monkeypatch, capsys):
    (tmp_path / "cso_rules.yaml").write_text("old", encoding="utf-8")
    monkeypatch.setenv("CSOCLASSIFY_POLICY_DIR", str(tmp_path))
    got = rules.default_rules_path()
    assert os.path.basename(got) == "cso_rules.yaml", got
    assert "옛 이름" in capsys.readouterr().err


#------------------------------------------------------------------
# 둘 다 없으면 '새 이름' 경로를 돌려준다
#=> 없을 때 나오는 오류 메시지가 옛 이름을 가리키면, 사람은 옛 이름으로 파일을
#   만들어 두고 또 안내를 보게 된다. 처음부터 새 이름을 가리켜야 한다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_둘_다_없으면_새_이름을_가리킨다(tmp_path, monkeypatch):
    monkeypatch.setenv("CSOCLASSIFY_POLICY_DIR", str(tmp_path))
    assert os.path.basename(rules.default_rules_path()) == "cso_rule.yaml"


#------------------------------------------------------------------
# 두 판이 같은 규약으로 찾는다
#=> 한쪽만 폴백을 갖고 있으면, 옛 이름 폴더에서 한 판은 돌고 한 판은 죽는다.
#   같은 배포본을 두 엔진으로 쓰는 이 프로젝트에서는 그게 곧 사고다.
#
# -in: name = 폴더에 둘 파일 이름
# -out: 없음(assert)
# -out: error = Rust exe 가 없으면 skip
#------------------------------------------------------------------
@pytest.mark.parametrize("name", ["cso_rule.yaml", "cso_rules.yaml"])
def test_두_판이_같은_이름_규약을_쓴다(name, tmp_path):
    if not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    src = io.open(os.path.join(POLICY, "cso_rule.yaml"), encoding="utf-8").read()
    (tmp_path / name).write_text(src, encoding="utf-8")

    for label, cmd in (("python", [sys.executable, "-m", "csoclassify"]), ("rust", [RS_EXE])):
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
                   PYTHONIOENCODING="utf-8",
                   CSOCLASSIFY_POLICY_DIR=str(tmp_path))
        r = subprocess.run(cmd + ["--check-rules"], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8")
        # 안내가 stdout 으로 나가는 것도 있어 둘을 합쳐 본다(두 판의 갈래가 다르다).
        out = (r.stdout or "") + (r.stderr or "")
        assert r.returncode == 0, f"{label}/{name}: 종료코드 {r.returncode} :: {out[-300:]}"
        assert "규칙셋 정상" in out, f"{label}/{name}: {out[-300:]}"
        # 옛 이름을 썼으면 두 판 모두 안내가 나와야 한다.
        if name == "cso_rules.yaml":
            assert "옛 이름" in out, f"{label}: 안내가 없다"
