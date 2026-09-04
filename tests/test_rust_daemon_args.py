# -*- coding: utf-8 -*-
"""Rust 판이 지원 못 하는 인자를 '조용히 무시'하지 않는가.

파이썬 판은 임베딩 모델을 물고 있는 상주 데몬을 쓰지만 Rust 판에는 없다.
그런데 예전에는 --serve/--status/--stop/--daemon 을 인자만 받고 아무 일도 하지
않았다. 두 판을 같은 명령으로 부르던 쪽은 요청이 받아들여진 줄 알고, --serve 라면
오지 않을 서버를 기다린다(부록 C-2 가 "--serve 를 조용히 무시하는 것과 같은 종류의
함정"이라고 부른 그것이다).

무엇을 요구했는지에 따라 답이 다르다 — 이 시험이 그 구분을 지킨다.
  · --serve  : 못 해 준다 → 오류(코드 3)
  · --status : 답할 수 있다("없다") → 알리고 정상 종료
  · --stop   : 멈출 것이 없고 원하는 끝 상태는 이미 참 → 알리고 정상 종료
  · --daemon : 결과는 같고 속도만 다르다 → 경고하고 계속
  · --no-daemon : 이미 그 상태 → 아무 말도 하지 않는다
"""
import json
import os
import subprocess

import pytest

from csoclassify import errcodes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 개발 중에는 debug, 배포 검증에는 release 를 쓴다. 있는 쪽을 집는다.
_CANDIDATES = [
    os.path.join(ROOT, "Rust", "target", "release", "csoclassify-rs.exe"),
    os.path.join(ROOT, "Rust", "target", "debug", "csoclassify-rs.exe"),
]
RS_EXE = next((p for p in _CANDIDATES if os.path.isfile(p)), None)

pytestmark = pytest.mark.skipif(RS_EXE is None, reason="Rust 실행파일 없음")


#------------------------------------------------------------------
# Rust 판을 인자만 주고 한 번 돌린다
#=> 데몬 관련 인자는 문서·규칙셋이 없어도 답해야 하는 것들이라(질의·모드 요청),
#   대상 파일을 주지 않고 그대로 부른다. 그래야 "규칙셋이 없다" 같은 다른 실패에
#   가려지지 않고 이 동작만 본다.
#
# -in: args = 붙일 CLI 인자들
#
# -out: CompletedProcess = stdout/stderr/returncode
# -out: error = 없음
#------------------------------------------------------------------
def _run(*args):
    return subprocess.run([RS_EXE, *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8")


#------------------------------------------------------------------
# --serve 는 오류로 멈춘다 (핵심 회귀)
#=> 이 프로세스가 서버가 되기를 요구한 것이라, 조용히 분류하고 끝내면 서버를
#   기다리던 쪽은 영영 기다린다. 종료코드·오류코드는 두 판이 공유하는 표가 정한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 조용히 넘어가면 AssertionError
#------------------------------------------------------------------
def test_serve는_지원안함으로_멈춘다():
    r = _run("--serve")
    assert r.returncode == errcodes.exit_of("unsupported_option") == 3, \
        f"exit={r.returncode} :: {r.stderr[-300:]}"
    assert "--serve" in r.stderr and "지원하지 않습니다" in r.stderr
    # 무엇을 대신 쓰면 되는지까지 알려 준다 — 막기만 하면 부르는 쪽이 막힌다.
    assert "파이썬" in r.stderr


#------------------------------------------------------------------
# --serve 의 오류 JSON 이 표와 일치한다
#=> --json-errors 계약(코드·kind)을 기계가 읽는다. 표와 어긋나면 분기가 깨진다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 코드·kind 가 표와 다르면 AssertionError
#------------------------------------------------------------------
def test_serve_오류JSON이_표와_같다():
    r = _run("--serve", "--json-errors")
    obj = json.loads(r.stdout.strip().splitlines()[-1])["error"]
    assert obj["kind"] == "unsupported_option"
    assert obj["code"] == errcodes.code_of("unsupported_option") == 1012


#------------------------------------------------------------------
# --status · --stop 은 사실을 알리고 정상 종료한다
#=> 둘 다 '답할 수 있는' 요청이다. 파이썬 판도 데몬이 없을 때 같은 모양으로
#   답하고 0 을 낸다 — 실패로 만들면 두 판의 계약이 갈린다.
#
# -in: opt = 시험할 인자
# -out: 없음(단언)
# -out: error = 종료코드나 안내가 달라지면 AssertionError
#------------------------------------------------------------------
@pytest.mark.parametrize("opt", ["--status", "--stop"])
def test_status_stop은_알리고_정상종료(opt):
    r = _run(opt)
    assert r.returncode == 0, f"exit={r.returncode} :: {r.stderr[-300:]}"
    assert "데몬" in r.stdout, f"안내가 없다 :: {r.stdout!r}"


#------------------------------------------------------------------
# --daemon 은 경고만 하고 분류를 계속한다
#=> 결과는 어차피 같고 속도만 다르다. 여기서 멈추면 두 판을 같은 명령으로 부르던
#   배치가 통째로 깨진다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 멈추거나 경고가 없으면 AssertionError
#------------------------------------------------------------------
def test_daemon은_경고만_하고_계속한다(tmp_path):
    doc = tmp_path / "문서.txt"
    doc.write_text("월간 운영 보고\n\n정기 점검을 예정대로 수행했습니다.\n",
                   encoding="utf-8")
    rules = os.path.join(ROOT, "resources", "policy", "cso_rules.yaml")
    r = _run("--daemon", "--file", str(doc), "--rules", rules,
             "--rule-only", "--format", "jsonl", "--nosummary", "--no-timing")
    assert r.returncode == 0, f"exit={r.returncode} :: {r.stderr[-300:]}"
    assert "--daemon" in r.stderr, f"경고가 없다 :: {r.stderr[-300:]}"
    # 경고만 하고 실제로 분류까지 마쳤는가 — 여기까지 봐야 '계속한다'가 증명된다.
    assert '"file"' in r.stdout, f"분류가 돌지 않았다 :: {r.stderr[-300:]}"


#------------------------------------------------------------------
# --no-daemon 에는 아무 말도 하지 않는다 (오탐 방지)
#=> 이 판은 이미 데몬 없이 돈다. 요청이 이미 충족됐는데 경고를 내면, 매 실행마다
#   뜨는 소음이 되어 정작 진짜 경고를 가린다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 불필요한 경고가 생기면 AssertionError
#------------------------------------------------------------------
def test_no_daemon은_조용하다(tmp_path):
    doc = tmp_path / "문서.txt"
    doc.write_text("월간 운영 보고\n\n정기 점검을 예정대로 수행했습니다.\n",
                   encoding="utf-8")
    rules = os.path.join(ROOT, "resources", "policy", "cso_rules.yaml")
    r = _run("--no-daemon", "--file", str(doc), "--rules", rules,
             "--rule-only", "--format", "jsonl", "--nosummary", "--no-timing")
    assert r.returncode == 0, f"exit={r.returncode} :: {r.stderr[-300:]}"
    assert '"file"' in r.stdout, f"분류가 돌지 않았다 :: {r.stderr[-300:]}"
    assert "데몬" not in r.stderr, f"쓸데없는 경고가 떴다 :: {r.stderr[-300:]}"


#------------------------------------------------------------------
# --synap-only 는 오류로 멈춘다 (결과가 갈리는 요청이라 더 엄격하다)
#=> 이 판에는 사이냅(snf)이 없다. 조용히 무시하면 다른 추출기로 돌면서 본문이
#   달라지고, 본문이 달라지면 PII 검출과 등급까지 갈린다. --daemon 처럼 "결과는
#   같고 속도만 다른" 요청이 아니므로 경고가 아니라 멈춤이 맞다.
#   두 판을 비교하는 자리에서 "Rust 는 snf 에서도 결과가 같더라"는 잘못된 결론이
#   남는 것을 막는 것이기도 하다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 조용히 넘어가면 AssertionError
#------------------------------------------------------------------
def test_synap_only는_지원안함으로_멈춘다():
    r = _run("--synap-only")
    assert r.returncode == errcodes.exit_of("unsupported_option") == 3,         f"exit={r.returncode} :: {r.stderr[-300:]}"
    assert "--synap-only" in r.stderr and "지원하지 않습니다" in r.stderr
    # 대신 무엇을 쓰면 되는지까지 알려 준다.
    assert "파이썬" in r.stderr


#------------------------------------------------------------------
# --hybridparse 에는 아무 말도 하지 않는다 (오탐 방지)
#=> 이 판이 늘 하는 일이라 요청이 이미 충족돼 있다. 경고를 내면 매 실행 소음이 된다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 불필요한 경고가 생기면 AssertionError
#------------------------------------------------------------------
def test_hybridparse는_조용하다(tmp_path):
    doc = tmp_path / "문서.txt"
    doc.write_text("월간 운영 보고" + chr(10) * 2 +
                   "정기 점검을 예정대로 수행했습니다." + chr(10), encoding="utf-8")
    rules = os.path.join(ROOT, "resources", "policy", "cso_rules.yaml")
    r = _run("--hybridparse", "--file", str(doc), "--rules", rules,
             "--rule-only", "--format", "jsonl", "--nosummary", "--no-timing")
    assert r.returncode == 0, f"exit={r.returncode} :: {r.stderr[-300:]}"
    assert '"file"' in r.stdout, f"분류가 돌지 않았다 :: {r.stderr[-300:]}"
    assert "추출기" not in r.stderr and "synap" not in r.stderr
