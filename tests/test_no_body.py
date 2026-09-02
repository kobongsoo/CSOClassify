# -*- coding: utf-8 -*-
"""본문을 못 읽은 문서(스캔본 등)를 '미분류'와 구분해 알리는가.

스캔 PDF 는 추출기가 예외를 던지지 않는다 — 장식기호 몇 개를 돌려주고 성공한
척한다. 그러면 신호가 하나도 안 잡혀 '미분류'로 나가는데, 그건 "읽어 봤더니
민감한 게 없더라"와 겉모습이 같다. 둘은 대응이 다르다.
"""
import json
import os
import subprocess
import sys

import pytest

from csoclassify import cli
from csoclassify import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS_EXE = os.path.join(ROOT, "Rust", "target", "release", "csoclassify-rs.exe")


#------------------------------------------------------------------
# 공백은 글자로 세지 않는다
#=> 줄바꿈·탭만 잔뜩인 파일은 '내용이 있다'가 아니다. 페이지 구분만 남은
#   스캔본이 정확히 이 모양으로 나온다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_공백만_있으면_본문없음():
    assert cli._body_too_short("\n\n   \t\n")[0] is True
    assert cli._body_too_short("")[0] is True
    assert cli._body_too_short(None)[0] is True


#------------------------------------------------------------------
# 실측값 언저리에서 갈린다
#=> d:\sample1 15건 실측 — 스캔본 3자, 가장 짧은 정상 문서 311자.
#   임계값(기본 20)은 그 사이에서 정상 문서 쪽에 넉넉히 떨어져 있어야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_임계값_언저리():
    assert config.MIN_TEXT_LEN < 311, "정상 문서를 잘못 걸면 안 된다"
    assert cli._body_too_short("가" * (config.MIN_TEXT_LEN - 1)) == (True, config.MIN_TEXT_LEN - 1)
    assert cli._body_too_short("가" * config.MIN_TEXT_LEN)[0] is False


#------------------------------------------------------------------
# 표식은 달되 분류 결과는 건드리지 않는다
#=> 파일명·경로 신호는 본문과 무관하고, 몇 글자라도 규칙에 걸렸으면 그 등급이
#   맞다. 표식만 더하는 것이 이 수정의 핵심이다 — 검출을 잃으면 안 된다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_이미_등급이_있으면_그대로_둔다():
    rec = {"grade": "C", "method": "fusion",
           "labels": {"security": {"method": "fusion"}}}
    cli._mark_no_body(rec, 3)
    assert rec["grade"] == "C" and rec["method"] == "fusion"
    assert rec["labels"]["security"]["method"] == "fusion"
    assert rec["error"]["stage"] == "extract" and rec["error"]["text_len"] == 3


#------------------------------------------------------------------
# 아무것도 못 정했으면 '미분류'로 두지 않는다
#=> "unclassified" 는 "봤는데 없더라"라는 뜻이다. 읽을 글자가 없었던 문서에
#   그 말을 붙이면 사실과 다르고, 사람이 손볼 문서를 놓친다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_미분류였다면_추출실패로_바꾼다():
    rec = {"grade": None, "method": "unclassified",
           "labels": {"security": {"method": "unclassified"}}}
    cli._mark_no_body(rec, 3)
    assert rec["method"] == "extract_failed"
    assert rec["labels"]["security"]["method"] == "extract_failed"


#------------------------------------------------------------------
# 두 엔진이 같은 파일을 같게 다룬다 (실제 실행)
#=> 글자 3개짜리 파일을 만들어 돌린다. 스캔본과 같은 상황이다(추출은 되는데
#   쓸 글자가 없다). 두 판이 같은 표식·같은 종료코드를 내야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 rust 쪽은 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_두_엔진이_본문없음을_같게_알린다(engine, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    doc = tmp_path / "스캔본.txt"
    doc.write_text("〮 ∽ …", encoding="utf-8")     # 실제 스캔 PDF 에서 나온 3글자
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8",
               CSOCLASSIFY_POLICY_DIR=os.path.join(ROOT, "resources", "policy"))
    r = subprocess.run(cmd + ["--file", str(doc), "--format", "jsonl", "--rule-only"],
                       cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8")
    recs = [json.loads(l) for l in r.stdout.splitlines()
            if l.strip().startswith("{") and '"file"' in l]
    assert recs, f"레코드가 없다 :: {r.stderr[-400:]}"
    rec = recs[0]
    # method 까지 단언하지는 않는다 — 임시폴더 경로가 경로 규칙에 걸려 등급이
    # 정해지면 method 는 그 결정을 그대로 유지한다(그게 이 수정의 의도다).
    # "아무것도 못 정했으면 extract_failed" 규칙은 위 단위 테스트가 지킨다.
    assert rec["error"]["stage"] == "extract"
    assert rec["error"]["text_len"] == 3
    # 결과 파일은 정상적으로 만들어지되 종료코드로 "손볼 문서가 있다"를 알린다.
    assert r.returncode == 1


#------------------------------------------------------------------
# --simple 로 줄여도 '못 읽었다'는 남는다
#=> --simple 은 file/grade/hash/doctype 네 칸뿐이라, 못 읽은 문서가 '읽었는데
#   미분류'와 글자 그대로 같은 모습으로 나갔다. --simple 만 받는 쪽(문서중앙화)은
#   스캔본을 영영 못 가려낸다. 실패한 문서에만 error 칸을 더한다 — 성공한 문서의
#   모양은 그대로라 기존 호출부가 깨지지 않는다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 rust 쪽은 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_simple에도_실패가_남는다(engine, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    bad = tmp_path / "스캔본.txt"
    bad.write_text("〮 ∽ …", encoding="utf-8")
    good = tmp_path / "정상.txt"
    good.write_text("이 문서는 사내 규정에 따른 일반 안내문입니다. " * 5, encoding="utf-8")
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8",
               CSOCLASSIFY_POLICY_DIR=os.path.join(ROOT, "resources", "policy"))
    r = subprocess.run(cmd + ["--dir", str(tmp_path), "--simple", "--format", "jsonl",
                              "--rule-only"],
                       cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    recs = {os.path.basename(json.loads(l)["file"]): json.loads(l)
            for l in r.stdout.splitlines()
            if l.strip().startswith("{") and '"file"' in l}
    assert set(recs) == {"스캔본.txt", "정상.txt"}, recs
    assert "error" in recs["스캔본.txt"], "못 읽은 문서인데 표식이 없다"
    assert "error" not in recs["정상.txt"], "정상 문서의 모양이 바뀌면 안 된다"


#------------------------------------------------------------------
# 오타 인자를 조용히 넘기지 않는다
#=> `--json-erros`(r 하나 빠짐)를 경고 한 줄로 흘려보내면, 옵션이 먹은 줄 알고
#   결과를 읽게 된다. 실제로 그렇게 당했다. 두 판 모두 1003 으로 막아야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 rust 쪽은 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_오타_인자는_막는다(engine, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8",
               CSOCLASSIFY_POLICY_DIR=os.path.join(ROOT, "resources", "policy"))
    r = subprocess.run(cmd + ["--dir", str(tmp_path), "--json-erros", "--json-errors"],
                       cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    line = [l for l in r.stdout.splitlines() if l.startswith('{"error"')]
    assert line, f"오류 JSON 이 없다 :: {r.stdout[:200]} / {r.stderr[:200]}"
    assert json.loads(line[-1])["error"]["code"] == 1003
    assert r.returncode == 3


#------------------------------------------------------------------
# 파이썬 판 전용 옵션은 Rust 에서도 그냥 통과한다
#=> 오타를 막느라 --model·-r·--timing 까지 막으면, 두 판을 같은 명령으로 부르던
#   호출부가 깨진다. 값을 데리고 오는 옵션은 그 값까지 함께 삼켜야 뒤가 안 밀린다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 skip
#------------------------------------------------------------------
def test_파이썬전용_옵션은_통과한다(tmp_path):
    if not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    doc = tmp_path / "안내문.txt"
    doc.write_text("이 문서는 사내 규정에 따른 일반 안내문입니다. " * 5, encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               CSOCLASSIFY_POLICY_DIR=os.path.join(ROOT, "resources", "policy"))
    r = subprocess.run([RS_EXE, "--dir", str(tmp_path), "--format", "jsonl", "--rule-only",
                        "-r", "--timing", "--model", "e5-small-ko", "--max-tokens", "512"],
                       cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[-300:]
    assert '"file"' in r.stdout, r.stderr[-300:]


#------------------------------------------------------------------
# 전체 레코드도 '읽는 차례'로 나온다
#=> 만들어진 순서 그대로면 가장 궁금한 업무분류가 labels 안에 묻혀 열 번째에
#   있고, 판정 근거(signals)가 결과보다 먼저 나온다. 앞 네 칸을 --simple 과
#   같게 두어, 축약본이 전체의 '앞부분만 떼어낸 것'이 되게 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 rust 쪽은 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_전체레코드도_읽는_차례로_나온다(engine, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    (tmp_path / "연구소안전관리규정.txt").write_text(
        "연구소안전관리규정\n제1조 이 규정은 안전관리에 관한 사항을 정한다.\n" * 5,
        encoding="utf-8")
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8",
               CSOCLASSIFY_POLICY_DIR=os.path.join(ROOT, "resources", "policy"))
    r = subprocess.run(cmd + ["--dir", str(tmp_path), "--rule-only", "--hash",
                              "--format", "jsonl", "--nosummary"],
                       cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8")
    rec = json.loads([l for l in r.stdout.splitlines() if '"file"' in l][0])
    keys = list(rec)
    # 앞 네 칸은 --simple 과 같아야 한다.
    assert keys[:4] == ["file", "hash", "grade", "doctype"], keys
    # 결과가 근거보다 앞에 온다.
    assert keys.index("labels") < keys.index("signals"), keys
    assert keys.index("grade") < keys.index("labels"), keys
    # 버전 세 칸은 흩어지지 않고 붙어 있다.
    vers = [k for k in keys if k.endswith("version")]
    assert vers == ["rule_version", "taxonomy_version", "doctype_rule_version"], keys
    i = keys.index("rule_version")
    assert keys[i:i + 3] == vers, keys
    # 최상위 doctype 은 labels 안의 값에서 뽑은 것이다 — 어긋나면 안 된다.
    inner = [v["dc_id"] for v in rec["labels"]["doctype"]["values"]]
    assert rec["doctype"] == inner, (rec["doctype"], inner)
