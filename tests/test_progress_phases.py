#------------------------------------------------------------------
# --progress 가 2차 패스(전파)도 알리는지 — 회귀 시험
#=> 예전에는 추출·1차 분류만 [progress] 를 냈고, 그 뒤 전파 단계는 아무 말이
#   없었다. 문서가 수천 건이면 마지막 줄이 찍힌 뒤로 화면이 조용해져, 쓰는
#   사람은 "멈춘 건가 도는 건가"를 알 수 없었다.
#
#   [모양을 지킨다] 화면 진행바(ui/gerunner.py)는 '[progress] ' 로 시작하는 줄을
#   공백으로 세 토막 내어 두 번째를 '한수/전체' 로 읽는다. 단계 이름을 붙이더라도
#   그 모양이 깨지면 진행바가 조용히 멈춘다 — 그래서 자리 수까지 못박는다.
#
#   [두 판을 함께 본다] 배치·화면이 두 판을 같은 방법으로 다뤄야 하므로
#   단계 이름과 줄 모양이 같아야 한다.
#------------------------------------------------------------------

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS_EXE = os.path.join(ROOT, "Rust", "target", "release", "MpowerClassify-rs.exe")
POLICY = os.path.join(ROOT, "resources", "policy")

# 전파는 seed 가 있어야 돈다. 저장소 정책 폴더의 기준 문서를 그대로 쓴다.
SEEDS = os.path.join(POLICY, "class_seed.jsonl")
DOC_RULES = os.path.join(POLICY, "doc_rule.yaml")

PHASES = ["전파·보안등급", "전파·업무분류"]


#------------------------------------------------------------------
# 한 엔진을 --progress 로 돌려 stderr 를 돌려준다
#
# -in: engine  = "python" | "rust"
# -in: doc_dir = 분류할 폴더
#
# -out: str = 표준오류 전체
# -out: error = 없음(실패해도 그대로 돌려준다 — 부르는 쪽이 판단한다)
#------------------------------------------------------------------
def _run(engine, doc_dir):
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8")
    r = subprocess.run(
        cmd + ["--dir", str(doc_dir), "--cso-rules", os.path.join(POLICY, "cso_rule.yaml"),
               "--doc-rules", DOC_RULES, "--seeds", SEEDS, "--progress",
               "--format", "jsonl", "--nosummary",
               "--out", os.path.join(str(doc_dir), "out.jsonl")],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    return r.stderr


#------------------------------------------------------------------
# 화면 진행바와 같은 방법으로 줄을 읽는다
#=> ui/gerunner.py 의 파싱을 그대로 흉내 낸다. 여기서 깨지면 화면도 깨진다.
#
# -in: line = '[progress]…' 로 시작하는 한 줄
#
# -out: (done, total) = 정수 두 개
# -out: error = 모양이 다르면 ValueError/IndexError
#------------------------------------------------------------------
def _parse_like_ui(line):
    parts = line.split(" ", 2)
    done, total = parts[1].split("/")
    return int(done), int(total)


#------------------------------------------------------------------
# 전파 단계도 진행을 알린다
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.skipif(not os.path.isfile(SEEDS), reason="기준 문서 없음")
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_전파_단계도_진행을_알린다(engine, tmp_path):
    (tmp_path / "규정.txt").write_text(
        "연구소안전관리규정\n제1조 이 규정은 안전관리에 관한 사항을 정한다.\n",
        encoding="utf-8")
    (tmp_path / "계약.txt").write_text(
        "용역계약서\n갑과 을은 다음과 같이 계약한다.\n", encoding="utf-8")
    err = _run(engine, tmp_path)

    for phase in PHASES:
        tag = f"[progress][{phase}]"
        assert tag in err, (engine, f"{phase} 단계가 조용하다", err[-600:])


#------------------------------------------------------------------
# 단계 줄도 진행바가 읽을 수 있는 모양이다
#=> 단계 이름을 붙이느라 '한수/전체' 자리가 밀리면 진행바가 조용히 멈춘다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.skipif(not os.path.isfile(SEEDS), reason="기준 문서 없음")
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_단계_줄도_진행바가_읽는_모양이다(engine, tmp_path):
    (tmp_path / "규정.txt").write_text("연구소안전관리규정\n제1조\n", encoding="utf-8")
    err = _run(engine, tmp_path)

    lines = [l for l in err.splitlines() if l.startswith("[progress][")]
    assert lines, (engine, "단계 줄이 없다", err[-600:])
    for l in lines:
        done, total = _parse_like_ui(l)          # 모양이 다르면 여기서 터진다
        assert 1 <= done <= total, (engine, l)


#------------------------------------------------------------------
# 단계 줄도 --json-errors 의 기계용 줄로 남는다
#=> stderr 를 기계가 읽는 호출에서 [progress]·[summary] 만 남긴다는 약속이
#   있다. 단계 줄이 그 그물에 안 걸리면 진행 보고가 통째로 사라진다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.skipif(not os.path.isfile(SEEDS), reason="기준 문서 없음")
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_단계_줄은_json_errors_에서도_남는다(engine, tmp_path):
    (tmp_path / "규정.txt").write_text("연구소안전관리규정\n제1조\n", encoding="utf-8")
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8")
    r = subprocess.run(
        cmd + ["--dir", str(tmp_path), "--cso-rules", os.path.join(POLICY, "cso_rule.yaml"),
               "--doc-rules", DOC_RULES, "--seeds", SEEDS, "--progress", "--json-errors",
               "--format", "jsonl", "--nosummary",
               "--out", os.path.join(str(tmp_path), "out.jsonl")],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")

    kept = [l for l in r.stderr.splitlines() if l.strip()]
    assert any(l.startswith("[progress][") for l in kept), (engine, kept[-8:])
    # 사람용 알림은 걸러진 채여야 한다(기존 약속).
    bad = [l for l in kept if not l.startswith(("[progress]", "[summary]"))]
    assert not bad, (engine, bad)
