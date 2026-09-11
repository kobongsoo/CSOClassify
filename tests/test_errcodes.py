# -*- coding: utf-8 -*-
"""CLI 오류 계약 — 표 자체와 두 엔진의 일치를 지킨다.

설계: plan/CLI-오류출력-설계.html
"""
import json
import os
import re
import subprocess
import sys

import pytest

from csoclassify import cli
from csoclassify import errcodes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS_EXE = os.path.join(ROOT, "Rust", "target", "release", "MpowerClassify-rs.exe")
RS_SRC = os.path.join(ROOT, "Rust", "src", "errcodes.rs")


#------------------------------------------------------------------
# 표 자체가 성립하는가 — 번호도 이름도 겹치면 안 된다
#=> 번호가 겹치면 받는 쪽의 분기가 두 뜻을 가지게 되고, 이름이 겹치면 표를
#   찾을 때 먼저 만난 쪽이 조용히 이긴다. 둘 다 눈으로는 안 보이는 사고다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_코드는_저마다_유일하다():
    codes = [c for c, _ in errcodes.ERRORS.values()]
    assert len(set(codes)) == len(codes), "중복된 code 가 있다"


#------------------------------------------------------------------
# 종료코드는 지금까지의 약속(0~4) 안에 있어야 한다
#=> 이 계약은 code 를 늘리는 것이지 종료코드를 늘리는 것이 아니다. 셸 스크립트가
#   종료코드만 보고도 지금까지처럼 굴러가야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_종료코드는_0에서_4_사이다():
    for kind, (_, ex) in errcodes.ERRORS.items():
        assert 0 <= ex <= 4, f"{kind} 의 종료코드가 범위 밖: {ex}"


#------------------------------------------------------------------
# 갈래(백의 자리)와 종료코드가 어긋나지 않는가
#=> 받는 쪽에 "모르는 code 는 백의 자리로 뭉뚱그려 처리하라"고 안내했으므로,
#   한 갈래 안에서 뜻이 흔들리면 그 안내가 거짓말이 된다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_갈래별_종료코드가_일관된다():
    for kind, (code, ex) in errcodes.ERRORS.items():
        if code // 1000 == 1:
            # 1000 인자·입력은 부른 쪽 잘못이라 3. 1009 만 정책 위반(T11)이라 4.
            assert ex == 3 or code == 1009, f"{kind} 는 3 이어야 한다"
        elif code // 1000 == 2:
            # 2000 정책 파일은 '없으면 3 · 내용이 틀리면 4' 두 가지뿐이다.
            assert ex in (3, 4), f"{kind} 가 이상하다"


#------------------------------------------------------------------
# path 는 있을 때만 넣는다
#=> "경로가 비었다"와 "경로와 무관하다"는 다른 뜻이다. 빈 문자열을 넣으면
#   받는 쪽이 그 둘을 구분할 수 없다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_path_는_있을_때만_들어간다():
    assert errcodes.error_object("no_input", "대상 없음", "D:/x")["error"]["path"] == "D:/x"
    assert "path" not in errcodes.error_object("bad_args", "인자 오류")["error"]
    assert "path" not in errcodes.error_object("bad_args", "인자 오류", "")["error"]


#------------------------------------------------------------------
# 여러 줄 안내도 JSON 은 한 줄이어야 한다
#=> 부르는 쪽이 stdout 을 줄 단위로 읽는다. 안내가 줄바꿈을 품고 나가면
#   한 오류가 여러 줄로 쪼개져 파싱이 깨진다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_여러줄_안내도_한_줄로_담는다():
    obj = errcodes.error_object("no_input", "첫 줄\n  둘째 줄\n\n셋째 줄")
    assert obj["error"]["message"] == "첫 줄 / 둘째 줄 /  / 셋째 줄" or \
           "\n" not in obj["error"]["message"]
    assert "\n" not in json.dumps(obj, ensure_ascii=False)


#------------------------------------------------------------------
# 옵션을 안 주면 stdout 은 예전 그대로다
#=> 지금 이 CLI 를 쓰고 있는 쪽은 stdout 을 결과로만 읽는다. 옵션 없이도 JSON 이
#   섞여 나가면 그 프로그램이 그날로 깨진다 — 그래서 '선택' 옵션으로 두었다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_옵션이_없으면_JSON을_내지_않는다(capsys):
    errcodes.enable_json(False)
    try:
        errcodes.emit("no_input", "대상 없음", "D:/x")
        assert capsys.readouterr().out == ""
    finally:
        errcodes.enable_json(False)


#------------------------------------------------------------------
# 옵션을 주면 stdout 에 한 줄이 나간다
#=> 실패하면 결과가 없으므로 결과와 부딪히지 않고, 부르는 쪽은 stdout 만
#   파싱하면 된다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_옵션을_주면_stdout에_한_줄(capsys):
    errcodes.enable_json(True)
    try:
        errcodes.emit("no_input", "대상 없음", "D:/x")
        out = capsys.readouterr().out.strip()
    finally:
        errcodes.enable_json(False)
    assert json.loads(out) == {"error": {
        "code": 1001, "kind": "no_input", "message": "대상 없음", "path": "D:/x"}}


#------------------------------------------------------------------
# fail_err 는 표가 정한 종료코드를 돌려준다
#=> 실패 자리에서 종료코드를 손으로 고르지 않게 하는 것이 이 계약의 핵심이다.
#   이름만 고르면 번호와 종료코드는 표가 정한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_fail_err_는_표가_정한_코드로_끝낸다(capsys):
    assert cli.fail_err("taxonomy_missing", "[MpowerClassify] 없다", "D:/t.yaml") == 3
    assert cli.fail_err("taxonomy_invalid", "[MpowerClassify] 깨졌다", "D:/t.yaml") == 4
    # 사람이 읽는 안내는 예전처럼 stderr 에 그대로 남는다.
    assert "없다" in capsys.readouterr().err


#------------------------------------------------------------------
# 인자 해석 실패도 계약에 태운다 — argparse 의 종료코드 2 를 쓰지 않는다
#=> argparse 기본값 2 는 우리 표의 2(임베딩 실패)와 뜻이 겹친다. 받는 쪽이 그 2 를
#   보고 "모델이 없구나" 하고 재설치를 안내하면 실제 원인(오타)과 다른 대응을 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
@pytest.mark.parametrize("argv, kind", [
    (["--dir", ".", "--axis", "secuirty"], "bad_axis"),
    (["--dir", ".", "--rule-only", "--vector-only"], "mode_conflict"),
])
def test_argparse_오류도_인자오류3_이다(argv, kind, capsys):
    errcodes.enable_json(True)
    try:
        with pytest.raises(SystemExit) as ex:
            cli.build_parser().parse_args(argv)
        out = capsys.readouterr().out.strip()
    finally:
        errcodes.enable_json(False)
    assert ex.value.code == 3
    assert json.loads(out)["error"]["kind"] == kind


#------------------------------------------------------------------
# --failsafe 로 준 등급이 두 엔진에서 똑같이 적용된다
#=> Rust 판은 값을 읽지 않고 늘 "S" 를 박았다(2026-09-01 수정). 규칙으로 못 정한
#   문서에 다른 등급이 조용히 들어가던 것이라, 종료코드가 아니라 '결과'가 틀렸다.
#   번호만 맞추는 테스트로는 못 잡으므로 실제 등급을 맞춰 본다.
#
#   [왜 합성 문서를 쓰나] 예전에는 저장소의 doc/*.html 을 훑었다. 그런데 그 폴더가
#   PII 예시로 가득한 문서들로 채워지면서 12건 전부 규칙에 걸리게 됐고, failsafe 가
#   아예 발동하지 않았다. 더 나쁜 것은 [C]·[S] 가 그때도 통과했다는 점이다 —
#   규칙이 매긴 등급에 마침 C 와 S 가 있어서였지 failsafe 때문이 아니었다.
#   즉 이 시험은 한동안 아무것도 검증하지 못하면서 초록불이었다(2026-09-08 발견).
#   그래서 신호가 하나도 없는 문서를 직접 만들어 쓴다 — 저장소 문서 내용이 바뀌어도
#   흔들리지 않는다.
#
#   [method 까지 보는 이유] 등급만 보면 규칙이 우연히 같은 등급을 냈을 때 또 통과한다.
#   failsafe 로 정해졌을 때만 나오는 method("failsafe_default")를 함께 단언해,
#   '규칙이 정한 등급'으로는 절대 통과할 수 없게 만든다.
#
# -in: grade    = 시험할 failsafe 등급(C·O·S)
# -in: tmp_path = pytest 임시 폴더
#
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 skip
#------------------------------------------------------------------
@pytest.mark.parametrize("grade", ["C", "O", "S"])
def test_두_엔진이_같은_failsafe_등급을_준다(grade, tmp_path):
    if not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    # 규칙에 걸릴 만한 것이 하나도 없는 본문. PII·기밀어·스탬프 어느 것도 없고,
    # 파일명도 기밀사전에 안 걸리는 이름으로 둔다(파일명은 Signal D 가 본다).
    doc = tmp_path / "plain.txt"
    doc.write_text(
        "봄이 오면 마당에 심은 나무에 새 잎이 돋는다." + chr(10) +
        "아침마다 물을 주며 자라는 모습을 지켜보는 일이 즐겁다." + chr(10) +
        "여름에는 그늘이 넓어져 앉아 쉬기에 좋다." + chr(10),
        encoding="utf-8")

    rules = os.path.join(ROOT, "resources", "policy", "cso_rule.yaml")
    got = {}
    for name, cmd in (("python", [sys.executable, "-m", "csoclassify"]),
                      ("rust", [RS_EXE])):
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
                   PYTHONIOENCODING="utf-8")
        r = subprocess.run(cmd + ["--file", str(doc),
                                  "--format", "jsonl", "--rule-only",
                                  "--rules", rules, "--failsafe", grade],
                           cwd=ROOT, env=env, capture_output=True, text=True,
                           encoding="utf-8")
        recs = [json.loads(l) for l in r.stdout.splitlines()
                if l.strip().startswith("{") and '"file"' in l]
        assert recs, f"{name}: 레코드가 없다 :: {r.stderr[-400:]}"
        got[name] = recs[0]

    # 두 판이 같은 답을 내는가 — 이 시험이 처음 잡으려던 '늘 S' 버그가 여기 걸린다.
    assert got["python"]["grade"] == got["rust"]["grade"]
    for name, rec in got.items():
        sec = rec["why"]["security"]
        assert rec["grade"] == grade, f"{name}: {sec['grade']} != {grade}"
        # 규칙이 아니라 failsafe 가 정했는가. 이 줄이 없으면 저장소 내용이 바뀌었을 때
        # 규칙이 매긴 등급으로 조용히 통과한다(예전에 실제로 그랬다).
        assert sec.get("method") == "failsafe_default",             f"{name}: failsafe 가 아니라 {sec.get('method')} 로 정해졌다"


#------------------------------------------------------------------
# --json-errors 를 주면 오류 안내를 stderr 로 되풀이하지 않는다
#=> 부르는 쪽이 두 갈래를 합쳐 받으면(2>&1 · stderr=STDOUT) JSON 뒤에 사람용
#   문장이 따라붙어 파싱이 깨진다. 옵션을 준 호출은 "기계가 읽겠다"는 뜻이므로
#   사람용 줄을 뺀다. 옵션이 없으면 예전 그대로 stderr 로 나가야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 rust 쪽은 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_json_errors면_stderr에_오류를_되풀이하지_않는다(engine):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    # 정책 파일 위치를 못 찾으면 "축을 건너뜁니다" 경고가 stderr 로 섞여, 이
    # 테스트가 무엇을 보는지 흐려진다(경고는 오류가 아니라 옵션과 무관하게 나간다).
    # 두 엔진 모두 이 환경변수로 정책 폴더를 잡는다.
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8",
               CSOCLASSIFY_POLICY_DIR=os.path.join(ROOT, "resources", "policy"))
    cwd = ROOT
    argv = ["--dir", "D:/없는폴더_zzz"]

    on = subprocess.run(cmd + argv + ["--json-errors"], cwd=cwd, env=env,
                        capture_output=True, text=True, encoding="utf-8")
    assert on.stderr.strip() == "", f"stderr 에 무언가 남았다: {on.stderr!r}"
    assert json.loads(on.stdout.strip())["error"]["code"] == 1001

    off = subprocess.run(cmd + argv, cwd=cwd, env=env,
                         capture_output=True, text=True, encoding="utf-8")
    assert "처리할 파일이 없습니다" in off.stderr, "옵션이 없으면 예전처럼 나가야 한다"
    assert off.stdout.strip() == "", "옵션이 없으면 stdout 에 JSON 을 내지 않는다"


#------------------------------------------------------------------
# Rust 표를 읽어 파이썬 표와 한 줄씩 맞춘다
#=> 두 엔진이 같은 상황에서 다른 번호를 내면 계약이 무의미해진다. 표를 한쪽만
#   고치는 실수를 여기서 잡는다(빌드 없이 소스만 읽으므로 항상 돌 수 있다).
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust 소스가 없으면 skip
#------------------------------------------------------------------
def test_두_엔진의_표가_같다():
    if not os.path.isfile(RS_SRC):
        pytest.skip("Rust 소스 없음")
    src = open(RS_SRC, encoding="utf-8").read()
    body = src.split("pub const ERRORS", 1)[1].split("];", 1)[0]
    rust = {m.group(1): (int(m.group(2)), int(m.group(3)))
            for m in re.finditer(r'\("([a-z_]+)",\s*(\d+),\s*(\d+)\)', body)}
    assert rust == errcodes.ERRORS


#------------------------------------------------------------------
# 같은 상황에서 두 엔진이 같은 code 를 낸다 (실제 실행)
#=> 표가 같아도 '어느 자리에서 어떤 이름을 고르는가'가 어긋나면 소용이 없다.
#   그래서 표 대조와 별개로 실제로 두 프로세스를 돌려 맞춰 본다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 skip
#------------------------------------------------------------------
@pytest.mark.parametrize("argv, code", [
    ([], 1002),                                             # 대상을 아예 안 줌
    (["--dir", "D:/없는폴더_zzz"], 1001),                   # 줬는데 0건
    (["--propagate", "D:/없는입력_zzz.jsonl"], 1008),       # 전파 입력 없음
    (["--export-taxonomy", "--export-input", "D:/없는원본_zzz.json"], 2007),
    # Rust 판은 --failsafe 값을 통째로 무시해(늘 S) 이 오류가 아예 안 났다.
    # 2026-09-01 에 고쳤고, 이 줄이 두 엔진이 같이 막는지를 지킨다.
    (["--failsafe", "X"], 1005),
    # --file 로 없는 경로를 주면 '추출 실패' 레코드를 만들어 결과처럼 내보내고
    # 있었다(2026-09-01 수정). 대상이 없는 것은 결과가 아니라 부른 쪽의 문제다.
    (["--file", "D:/없는파일_zzz.hwp"], 1001),
])
def test_두_엔진이_같은_code를_낸다(argv, code, tmp_path):
    if not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    got = {}
    for name, cmd in (("python", [sys.executable, "-m", "csoclassify"]),
                      ("rust", [RS_EXE])):
        # 정책 폴더를 짚어 준다 — 안 그러면 Rust 판은 규칙셋을 못 찾아(exe 옆에
        # 없다) 이 시험이 보려는 것과 다른 오류가 난다.
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
                   PYTHONIOENCODING="utf-8",
                   CSOCLASSIFY_POLICY_DIR=os.path.join(ROOT, "resources", "policy"))
        r = subprocess.run(cmd + argv + ["--json-errors"], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8")
        # stdout 의 마지막 줄이 오류 JSON 이다(앞에 다른 출력이 있을 수 있다).
        line = [ln for ln in r.stdout.splitlines() if ln.startswith('{"error"')]
        assert line, f"{name}: 오류 JSON 이 안 나왔다\n{r.stdout}\n{r.stderr}"
        err = json.loads(line[-1])["error"]
        got[name] = (err["code"], r.returncode)
    assert got["python"] == got["rust"] == (code, errcodes.exit_of(
        next(k for k, (c, _) in errcodes.ERRORS.items() if c == code)))


#------------------------------------------------------------------
# 실행해서 상태 줄만 뽑아 오는 도우미
#=> 아래 시험들이 같은 방식으로 stdout 의 마지막 상태 줄을 읽게 한다.
#
# -in: engine = "python" | "rust"
# -in: argv   = 인자 목록(--json-errors 는 여기서 붙인다)
#
# -out: dict = {"code":…, "kind":…, …}
# -out: error = 상태 줄이 없으면 AssertionError
#------------------------------------------------------------------
def _run_status(engine, argv):
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8",
               CSOCLASSIFY_POLICY_DIR=os.path.join(ROOT, "resources", "policy"))
    r = subprocess.run(cmd + argv + ["--json-errors"], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8")
    lines = [l for l in r.stdout.splitlines() if l.startswith('{"error"')]
    assert lines, f"상태 줄이 없다 :: {r.stdout[:200]} / {r.stderr[:300]}"
    st = json.loads(lines[-1])["error"]
    st["_exit"] = r.returncode
    return st


#------------------------------------------------------------------
# 성공이어도 마지막 한 줄이 나간다
#=> 실패했을 때만 줄이 나가면 부르는 쪽은 '줄이 없음'을 성공으로 읽어야 한다.
#   그건 프로세스가 조용히 죽은 경우와 구분되지 않는다. 그래서 언제나 낸다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 rust 쪽은 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_성공해도_상태줄이_나온다(engine, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    doc = tmp_path / "안내문.txt"
    doc.write_text("이 문서는 사내 규정에 따른 일반 안내문입니다. " * 5, encoding="utf-8")
    st = _run_status(engine, ["--dir", str(tmp_path), "--rule-only",
                              "--format", "jsonl", "--out", str(tmp_path / "o.jsonl")])
    assert st["code"] == 0 and st["kind"] == "success"
    assert (st["total"], st["failed"]) == (1, 0)


#------------------------------------------------------------------
# 부분 실패는 결과를 살리고 숫자로 알린다
#=> 16건 중 1건을 못 읽었다고 15건을 버릴 수는 없다. 결과는 만들고(종료 1)
#   몇 건인지 숫자로 준다 — 그 숫자로 무엇을 할지는 부르는 쪽이 정한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 rust 쪽은 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_부분실패는_건수로_알린다(engine, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    (tmp_path / "정상.txt").write_text("사내 규정에 따른 일반 안내문입니다. " * 5,
                                      encoding="utf-8")
    (tmp_path / "스캔본.txt").write_text("〮 ∽ …", encoding="utf-8")
    out = tmp_path / "o.jsonl"
    st = _run_status(engine, ["--dir", str(tmp_path), "--rule-only",
                              "--format", "jsonl", "--out", str(out)])
    assert st["code"] == 3001 and st["kind"] == "extract_failed"
    assert (st["total"], st["failed"]) == (2, 1)
    # 결과 파일은 버리지 않는다 — 읽은 문서는 그대로 살아 있어야 한다.
    lines = [l for l in out.read_text(encoding="utf-8").splitlines()
             if '"file"' in l]
    assert len(lines) == 2, lines


#------------------------------------------------------------------
# 상태 줄은 언제나 '마지막 한 줄'이다
#=> 두 줄이 나가면 마지막 줄만 읽는 쪽이 엉뚱한 것을 본다. 실패했을 때 오류 줄을
#   냈으면 끝에서 또 내지 않아야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 rust 쪽은 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_상태줄은_한_줄뿐이다(engine, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8",
               CSOCLASSIFY_POLICY_DIR=os.path.join(ROOT, "resources", "policy"))
    r = subprocess.run(cmd + ["--file", str(tmp_path / "없다.hwp"), "--json-errors"],
                       cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8")
    lines = [l for l in r.stdout.splitlines() if l.startswith('{"error"')]
    assert len(lines) == 1, lines
    assert json.loads(lines[0])["error"]["code"] == 1001


#------------------------------------------------------------------
# --json-errors 면 stderr 에 기계용 줄만 남는다
#=> [본문없음]·[전파] 같은 사람용 알림이 섞이면 로그가 시끄럽고, 두 갈래를 합쳐
#   받는 쪽은 파싱이 흔들린다. 진행바가 읽는 [progress] 와 완료 메시지가 쓰는
#   [summary] 만 남긴다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 rust 쪽은 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_json이면_stderr에_기계용_줄만(engine, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    (tmp_path / "스캔본.txt").write_text("〮 ∽ …", encoding="utf-8")
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8",
               CSOCLASSIFY_POLICY_DIR=os.path.join(ROOT, "resources", "policy"))
    argv = ["--dir", str(tmp_path), "--rule-only", "--progress",
            "--format", "jsonl", "--out", str(tmp_path / "o.jsonl")]

    on = subprocess.run(cmd + argv + ["--json-errors"], cwd=ROOT, env=env,
                        capture_output=True, text=True, encoding="utf-8")
    bad = [l for l in on.stderr.splitlines()
           if l.strip() and not l.startswith(("[progress]", "[summary]"))]
    assert not bad, bad
    # 진행바가 읽는 줄은 살아 있어야 한다 — 여기까지 지우면 화면이 멈춘다.
    assert any(l.startswith("[progress]") for l in on.stderr.splitlines())

    off = subprocess.run(cmd + argv, cwd=ROOT, env=env,
                         capture_output=True, text=True, encoding="utf-8")
    assert "본문" in off.stderr, "옵션이 없으면 사람용 알림이 그대로 나가야 한다"


#------------------------------------------------------------------
# --out 으로 저장하면 결과 파일에도 상태가 남는다
#=> 파일만 받아 나중에 읽는 쪽은 stdout 을 이미 흘려보낸 뒤다. 파일 자체가
#   "이 결과가 온전한가"를 말해 줘야 한다. json 은 배열의 마지막 원소로,
#   jsonl 은 마지막 줄로 넣는다 — summary 와 같은 자리라 새 규약이 아니다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 rust 쪽은 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
@pytest.mark.parametrize("fmt", ["jsonl", "json"])
def test_결과파일에도_상태가_남는다(engine, fmt, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    (tmp_path / "정상.txt").write_text("사내 규정에 따른 일반 안내문입니다. " * 5,
                                      encoding="utf-8")
    (tmp_path / "스캔본.txt").write_text("〮 ∽ …", encoding="utf-8")
    out = tmp_path / f"r.{fmt}"
    st = _run_status(engine, ["--dir", str(tmp_path), "--rule-only",
                              "--format", fmt, "--out", str(out)])
    assert st["code"] == 3001

    text = out.read_text(encoding="utf-8")
    if fmt == "json":
        # 배열 '밖'에 객체를 붙이면 파일이 통째로 깨진다 — 여기서 그것을 막는다.
        arr = json.loads(text)
        last = arr[-1]
    else:
        last = json.loads(text.strip().splitlines()[-1])
    assert "error" in last, last
    assert last["error"]["code"] == 3001
    assert (last["error"]["total"], last["error"]["failed"]) == (2, 1)


#------------------------------------------------------------------
# --out 이 없으면 같은 줄이 두 번 나가지 않는다
#=> 결과가 stdout 으로 나가는데 거기에도 상태를 넣으면, 마지막 줄만 읽는 쪽은
#   문제없지만 줄 수를 세는 쪽이 어긋난다. 상태 줄은 언제나 하나여야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 rust 쪽은 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_out없이_stdout이면_상태줄은_하나(engine, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    (tmp_path / "스캔본.txt").write_text("〮 ∽ …", encoding="utf-8")
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8",
               CSOCLASSIFY_POLICY_DIR=os.path.join(ROOT, "resources", "policy"))
    r = subprocess.run(cmd + ["--dir", str(tmp_path), "--rule-only", "--simple",
                              "--format", "jsonl", "--json-errors"],
                       cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8")
    lines = [l for l in r.stdout.splitlines() if l.startswith('{"error"')]
    assert len(lines) == 1, lines


#------------------------------------------------------------------
# 상태 줄에 정책 버전이 실린다 (④)
#=> 결과만 있고 '어떤 규칙으로 판정했는지'가 없으면 반년 뒤에 재현할 수 없다.
#   --simple --nosummary 를 함께 쓰면 버전이 지금까지 어디에도 안 남았다 —
#   모르는 것보다 '그새 규칙이 바뀐 줄 모르고 지금 규칙으로 재현해 보고 맞다고
#   하는 것'이 더 위험하다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 rust 쪽은 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_상태줄에_정책_버전이_실린다(engine, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    (tmp_path / "안내문.txt").write_text("사내 규정에 따른 일반 안내문입니다. " * 5,
                                        encoding="utf-8")
    st = _run_status(engine, ["--dir", str(tmp_path), "--rule-only", "--simple",
                              "--nosummary", "--format", "jsonl",
                              "--out", str(tmp_path / "o.jsonl")])
    assert st["rule_version"], st
    assert st["taxonomy_version"], st
    assert st["doctype_rule_version"], st


#------------------------------------------------------------------
# --simple 이면 감사용 전체 파일이 옆에 함께 생긴다 (①)
#=> 축약본 4칸만으로는 "왜 이 등급인가"를 나중에 댈 수 없다. 분류를 다시 하는
#   것이 아니라 손에 든 레코드를 한 번 더 적는 것이라 비용이 사실상 없다
#   (실측 — 분류 2,320ms 대 0.27ms).
#   --out 이 가리키는 파일은 지금까지처럼 축약본이어야 한다(계약 유지).
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 rust 쪽은 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_simple이면_감사용_전체파일이_함께_생긴다(engine, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    (tmp_path / "안내문.txt").write_text("사내 규정에 따른 일반 안내문입니다. " * 5,
                                        encoding="utf-8")
    out = tmp_path / "r.jsonl"
    _run_status(engine, ["--dir", str(tmp_path), "--rule-only", "--simple",
                         "--nosummary", "--format", "jsonl", "--out", str(out)])
    full = tmp_path / "r.full.jsonl"
    assert full.is_file(), "감사용 전체 파일이 없다"

    def recs(p):
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()
                if l.strip().startswith("{") and '"file"' in l]
    simple, whole = recs(out), recs(full)
    # --out 은 지금까지처럼 축약본이어야 한다 — 읽고 있는 쪽의 계약을 바꾸지 않는다.
    assert set(simple[0]) <= {"file", "grade", "hash", "doctype", "doc_id",
                              "error", "why"}
    # 전체 파일에는 근거가 통째로 들어 있어야 한다.
    # [2026-09-10] 근거는 그 축 안(security.signals)에, 버전은 meta 에 있다.
    assert "signals" in whole[0]["why"]["security"]
    # [2026-09-10 오후] 버전은 레코드가 아니라 맨 앞 실행 헤더 한 줄에 있다.
    head = [json.loads(l) for l in full.read_text(encoding="utf-8").splitlines()
            if l.strip().startswith("{") and '"run"' in l]
    assert head and "rule_version" in head[0]["run"], head
    assert len(simple) == len(whole)
    # 연동용에 --nosummary 를 줘도 감사용에는 요약이 남는다(정책 버전이 필요하다).
    assert '"summary"' in full.read_text(encoding="utf-8")
    assert '"summary"' not in out.read_text(encoding="utf-8")


#------------------------------------------------------------------
# --simple-why 는 근거 요약만 얹는다 (③)
#=> 전체를 주면 26배로 커진다. 판정에 실제로 쓰인 것만 추린다.
#   매칭된 원문 값은 넣지 않는다 — 이 도구의 불변식이다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 rust 쪽은 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_simple_why는_근거_요약을_얹는다(engine, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    # 규칙에 확실히 걸리는 문서 — 근거가 비면 이 시험이 아무것도 못 본다.
    (tmp_path / "대외비문서.txt").write_text(
        "대외비\n이 문서는 영업비밀과 소스코드를 담고 있습니다.\n" * 5, encoding="utf-8")
    out = tmp_path / "r.jsonl"
    _run_status(engine, ["--dir", str(tmp_path), "--rule-only", "--simple-why",
                         "--nosummary", "--format", "jsonl", "--out", str(out)])
    rec = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()
           if '"file"' in l][0]
    why = rec["why"]
    # 축별로 묶여 있어야 한다 — 평평하면 보안등급의 conf 를 업무분류 값으로 오해한다.
    sec = why["security"]
    assert sec["by"], why           # 어느 신호가 정했는가
    assert sec["conf"] is not None  # 그 판정의 확신(= labels.security.confidence)
    assert sec["hits"], why         # 어느 규칙이 몇 건
    for h in sec["hits"]:
        assert set(h) <= {"sig", "id", "n", "src"}, f"근거에 군더더기가 있다: {h}"
    # 원문 값이 새면 안 된다 — 규칙 id 와 건수만 남긴다.
    blob = json.dumps(why, ensure_ascii=False)
    assert "영업비밀" not in blob and "소스코드" not in blob, blob


#------------------------------------------------------------------
# 업무분류 신뢰도에는 '어디서 나왔는가'가 붙는다
#=> 같은 0.91 이라도 뜻이 다르다 — rule 은 "정한 낱말이 제목·머리·파일명 여러
#   군데서 나왔다", embed 는 "이미 분류해 둔 기준 문서와 닮았다". 검토자가 다르게
#   봐야 하는 값인데, 처음에는 dc 와 c 만 넣어 구분할 수 없었다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 rust 쪽은 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_업무분류_신뢰도에_판정경로가_붙는다(engine, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    # 규칙으로 잡히는 이름 — doc_rule.yaml 의 '사내규정' 어휘에 걸린다.
    (tmp_path / "연구소안전관리규정.txt").write_text(
        "연구소안전관리규정\n제1조 이 규정은 안전관리에 관한 사항을 정한다.\n" * 5,
        encoding="utf-8")
    out = tmp_path / "r.jsonl"
    _run_status(engine, ["--dir", str(tmp_path), "--rule-only", "--simple-why",
                         "--nosummary", "--format", "jsonl", "--out", str(out)])
    rec = json.loads([l for l in out.read_text(encoding="utf-8").splitlines()
                      if '"file"' in l][0])
    # 칸 차례도 못박는다 — 무엇을(file·hash·doc_id) → 어떻게 됐나(grade·doctype) →
    # 왜(why). doc_id 는 '이 문서가 무엇인가'라서 판정값 앞이다(2026-09-07 옮김).
    # 두 판이 같은 차례로 내야 결과 파일을 그대로 견줄
    # 수 있다(Rust 는 기본값이 사전순이라 Cargo.toml 에 preserve_order 를 켜 두었다).
    # 여기서는 --filelist 를 안 줬으므로 doc_id 는 폴백이라 값이 None 이다.
    assert list(rec) == ["file", "hash", "doc_id", "grade", "doctype", "why"], list(rec)
    assert rec["doc_id"] is None, rec["doc_id"]
    assert list(rec["why"]) == ["security", "doctype"], list(rec["why"])
    assert list(rec["why"]["security"]) == ["by", "conf", "hits"], rec["why"]["security"]
    dt = (rec.get("why") or {}).get("doctype")
    assert dt, rec
    for v in dt:
        assert set(v) == {"dc", "c", "by"}, v
        assert v["by"] in ("rule", "embed"), v
    # --rule-only 로 돌렸으니 임베딩이 관여할 수 없다.
    assert all(v["by"] == "rule" for v in dt), dt


#------------------------------------------------------------------
# T7·T8 — --textsave 를 두 판이 같은 규칙으로 처리한다
#=> Rust 판은 예전에 --save-text 를 삼키고 아무 일도 안 했다. 오류도 경고도 없이
#   폴더가 비어 있어서, 사용자는 "저장했는데 왜 없지"를 혼자 헤맸다. 그 조용한
#   실패가 되살아나지 않게 못박는다.
#
#   [본문이 두 판에서 같지 않은 것은 정상이다] 저장하는 것은 원본이 아니라
#   *추출한 본문*이고 두 판은 파서가 다르다(실측: 같은 pptx 가 9,312 / 9,330바이트).
#   그래서 파일 '내용'이 아니라 규칙 — 이름·색인 칸·표식 — 이 같은지를 본다.
#
# -in: engine   = "python" | "rust"
# -in: tmp_path = pytest 임시 폴더
#
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_textsave가_본문을_해시이름으로_남긴다(engine, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    (tmp_path / "규정.txt").write_text(
        "연구소안전관리규정\n제1조 이 규정은 안전관리에 관한 사항을 정한다.\n" * 5,
        encoding="utf-8")
    save_dir = tmp_path / "본문"
    out = tmp_path / "r.jsonl"
    _run_status(engine, ["--dir", str(tmp_path), "--rule-only", "--nosummary",
                         "--textsave", str(save_dir),
                         "--format", "jsonl", "--out", str(out)])

    rec = json.loads([l for l in out.read_text(encoding="utf-8").splitlines()
                      if '"file"' in l][0])
    # 이름이 레코드의 hash 와 이어져야 "결과 한 줄에서 본문으로" 갈 수 있다.
    assert rec["text_saved"] == rec["hash"] + ".txt", rec
    saved = save_dir / rec["text_saved"]
    assert saved.is_file(), sorted(p.name for p in save_dir.iterdir())
    assert "연구소안전관리규정" in saved.read_text(encoding="utf-8")

    # 색인 — 칸 이름·차례가 두 판에서 같아야 한 파일로 섞어 읽을 수 있다.
    rows = [json.loads(l) for l in
            (save_dir / "_index.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1, rows
    assert list(rows[0]) == ["hash", "txt", "file", "doc_id", "chars", "truncated", "ts"], rows[0]
    assert rows[0]["hash"] == rec["hash"]
    assert rows[0]["txt"] == rec["text_saved"]
    assert rows[0]["truncated"] is False
    # --filelist 를 안 줬으니 폴백이다 — 폴백 doc_id 는 싣지 않는다.
    assert rows[0]["doc_id"] is None, rows[0]

    # 폴더만 발견한 사람에게 경고가 닿아야 한다.
    assert (save_dir / "_README.md").is_file()


#------------------------------------------------------------------
# 옵션을 안 주면 두 판 모두 아무것도 남기지 않는다
#=> 기본 꺼짐은 프라이버시 장치다(설계 P4). 한쪽만 켜져 있으면 엔진을 바꾼 것만으로
#   개인정보가 디스크에 남는다.
#
# -in: engine   = "python" | "rust"
# -in: tmp_path = pytest 임시 폴더
#
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 건너뛴다
#------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_textsave_없으면_아무것도_안_남긴다(engine, tmp_path):
    if engine == "rust" and not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    (tmp_path / "규정.txt").write_text("연구소안전관리규정\n" * 5, encoding="utf-8")
    out = tmp_path / "r.jsonl"
    _run_status(engine, ["--dir", str(tmp_path), "--rule-only", "--nosummary",
                         "--format", "jsonl", "--out", str(out)])
    rec = json.loads([l for l in out.read_text(encoding="utf-8").splitlines()
                      if '"file"' in l][0])
    assert "text_saved" not in rec, rec
    assert not list(tmp_path.glob("**/_index.jsonl"))
