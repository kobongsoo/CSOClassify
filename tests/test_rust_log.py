# -*- coding: utf-8 -*-
"""Rust 판도 일반 로그(log/class_날짜.log)를 남기는가 — 두 판이 같은 자리에.

예전에는 Rust 판에 **오류 로그(class_err_날짜.log)만** 있었다. 그래서 정상으로
끝난 실행은 흔적이 하나도 남지 않았다. 같은 폴더에서 두 판을 번갈아 쓰면
파이썬 판이 남긴 class_날짜.log 만 보이고 Rust 실행분은 통째로 빠져, 로그만
보고는 "그날 Rust 로는 아무것도 안 돌렸다"고 잘못 읽게 된다.

게다가 --log 는 값까지 받아 놓고 아무 데도 쓰지 않았다(조용한 실패). 로그를
지정했으니 남았겠거니 하고 나중에 찾으면 파일이 없다.

이 시험이 지키는 것:
  · 기본 경로  : <exe폴더>/log/class_YYYYMMDD.log — 파이썬 판과 같은 규칙
  · --log      : 준 경로에 실제로 쓴다
  · 환경변수   : CSOCLASSIFY_LOGDIR 을 두 판이 똑같이 따른다
  · 줄 모양    : "날짜 [PID n] INFO 자리: 내용" — 두 판의 로그를 나란히 읽을 수 있게
  · 오류       : 일반 로그에도 ERROR 로 남는다(오류 로그에만 있으면 못 보고 지나친다)
"""
import os
import re
import subprocess

import pytest

from csoclassify import logsetup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 개발 중에는 debug, 배포 검증에는 release 를 쓴다. 있는 쪽을 집는다.
_CANDIDATES = [
    os.path.join(ROOT, "Rust", "target", "release", "MpowerClassify-rs.exe"),
    os.path.join(ROOT, "Rust", "target", "debug", "MpowerClassify-rs.exe"),
]
RS_EXE = next((p for p in _CANDIDATES if os.path.isfile(p)), None)

# 정상 분류까지 보려면 규칙셋·모델이 함께 있는 배포본이 필요하다. target/ 밑의
# 실행 파일만으로는 문서를 끝까지 못 보므로, 그 시험만 배포본을 쓴다.
DIST_EXE = os.path.join(ROOT, "Rust", "dist-onedir", "windows", "MpowerClassify-rs.exe")
HAS_DIST = os.path.isfile(DIST_EXE)

pytestmark = pytest.mark.skipif(RS_EXE is None, reason="Rust 실행파일 없음")

# 로그 한 줄의 모양. 파이썬 판 logsetup 의 Formatter 와 같은 순서여야 한다 —
# 두 판의 로그를 한 파일에 섞어 놓고도 눈으로 훑을 수 있어야 하기 때문이다.
LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[PID \d+\] (INFO|DEBUG|ERROR) \S+: ")


#------------------------------------------------------------------
# Rust 판을 로그 폴더만 바꿔서 한 번 돌린다
#=> 진짜 exe 폴더에 로그를 흘리면 시험이 개발자의 배포본을 더럽힌다. 환경변수로
#   임시 폴더를 물려 그 안에서만 놀게 한다(이 환경변수를 두 판이 함께 따른다는
#   사실 자체도 이 시험이 확인하는 항목이다).
#
# -in: logdir = 로그를 받을 임시 폴더
# -in: args   = 붙일 CLI 인자들
#
# -out: CompletedProcess = stdout/stderr/returncode
# -out: error = 없음
#------------------------------------------------------------------
def _run(logdir, *args):
    env = dict(os.environ)
    env["CSOCLASSIFY_LOGDIR"] = str(logdir)
    return subprocess.run([RS_EXE, *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env)


#------------------------------------------------------------------
# 로그 파일을 읽는다(BOM 을 떼고)
#=> 새 파일에는 UTF-8 BOM 이 붙는다. 메모장이 cp949 로 오해해 한글을 깨뜨리는
#   것을 막으려고 일부러 붙인 것이라, 읽을 때는 utf-8-sig 로 떼어 준다.
#
# -in: path = 로그 파일 경로
#
# -out: str = 파일 내용(없으면 빈 문자열)
# -out: error = 없음
#------------------------------------------------------------------
def _read(path):
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return f.read()


#------------------------------------------------------------------
# 기본 경로·줄 모양 — 핵심 회귀
#=> "Rust 로 돌렸는데 class_날짜.log 가 안 생긴다"가 이 시험이 막는 바로 그것이다.
#   파일 이름을 파이썬 판의 default_log_path() 에서 그대로 가져와 비교한다 —
#   여기서 이름 규칙을 손으로 다시 적으면, 한쪽만 바뀌어도 시험이 눈치채지 못한다.
#------------------------------------------------------------------
def test_기본_경로에_일반_로그가_남는다(tmp_path):
    # --help 로는 확인할 수 없다. 도움말은 '실행'이 아니라서 로그를 열기 전에
    # 끝난다(그래야 --help 한 번에 빈 로그가 쌓이지 않는다). 그래서 실제로
    # 실행 길을 타는 호출을 쓴다 — 규칙셋을 못 찾아 멈추더라도, 그 지점은
    # 이미 로그를 연 뒤라 실행 기록은 남아야 한다.
    r = _run(tmp_path, "--file", str(tmp_path / "없는파일.doc"), "--nosummary")
    assert r.returncode != 0, "없는 파일인데 성공으로 끝났다"

    # 파이썬 판이 정한 파일 이름을 그대로 쓴다(같은 폴더에서 짝이 맞아야 한다).
    name = os.path.basename(logsetup.default_log_path())
    path = tmp_path / name
    body = _read(path)
    assert body, f"{name} 이 생기지 않았다 — 목록: {os.listdir(tmp_path)}"

    # 실행 경계를 눈으로 찾는 구분선.
    assert "-------------" in body
    # 로그 파일만 받아도 무슨 명령이었는지 알 수 있어야 재현이 된다.
    assert "실행 cmd=" in body
    assert "실행 argv=" in body

    # 줄 모양이 파이썬 판과 같은가 — 구분선을 뺀 모든 줄을 본다.
    lines = [l for l in body.splitlines() if l.strip() and not l.startswith("---")]
    assert lines, body
    for l in lines:
        assert LINE_RE.match(l), f"줄 모양이 파이썬 판과 다르다: {l!r}"


#------------------------------------------------------------------
# --log 로 준 경로에 실제로 쓰는가
#=> 예전에는 값까지 받아 놓고 버렸다. 오류도 경고도 없이 성공으로 끝나서,
#   "로그를 지정했으니 남았겠지" 하고 나중에 찾으면 파일이 없었다.
#------------------------------------------------------------------
def test_log_옵션이_준_경로에_쓴다(tmp_path):
    want = tmp_path / "내가_정한.log"
    r = _run(tmp_path, "--log", str(want),
             "--file", str(tmp_path / "없는파일.doc"), "--nosummary")
    assert r.returncode != 0, "없는 파일인데 성공으로 끝났다"

    body = _read(want)
    assert body, "--log 로 준 경로에 아무것도 안 썼다(조용한 실패)"
    assert "실행 cmd=" in body
    # --log 를 줬으면 기본 경로에는 만들지 않는다 — 두 군데로 갈리면 어느 쪽을
    # 봐야 하는지 알 수 없다.
    assert not (tmp_path / os.path.basename(logsetup.default_log_path())).exists()


#------------------------------------------------------------------
# 오류도 일반 로그에 남는가
#=> 파이썬 판은 로거 하나에 핸들러 둘을 달아 오류가 두 파일 모두에 들어간다.
#   Rust 판만 오류 로그에만 남기면, 일반 로그를 훑던 사람이 "그때 아무 문제
#   없었네" 하고 정반대로 읽는다.
#------------------------------------------------------------------
def test_오류가_일반_로그에도_남는다(tmp_path):
    r = _run(tmp_path, "--file", str(tmp_path / "없는파일.doc"), "--nosummary")
    assert r.returncode != 0, "없는 파일인데 성공으로 끝났다"

    body = _read(tmp_path / os.path.basename(logsetup.default_log_path()))
    assert "ERROR" in body, f"일반 로그에 ERROR 가 없다: {body!r}"

    # 오류 로그에도 그대로 있어야 한다(둘 중 하나만 남으면 안 된다).
    errname = os.path.basename(logsetup.default_err_log_path())
    assert _read(tmp_path / errname), f"{errname} 이 비었다"


#------------------------------------------------------------------
# 정상으로 끝난 실행도 흔적을 남기는가 — 이 로그의 존재 이유
#=> 앞의 시험들은 '멈춘' 실행을 본다. 정작 고치려던 것은 **잘 끝난 실행이
#   아무것도 안 남기던 것**이다. 그래서 실제로 문서 하나를 분류시키고,
#   파일별 결과와 요약이 로그에 들어갔는지 본다.
#   규칙셋·모델이 있어야 하므로 배포본으로 돌린다(없으면 건너뛴다).
#
# -in: tmp_path = pytest 가 주는 임시 폴더(로그와 시험용 문서를 함께 둔다)
#
# -out: 없음
# -out: error = 단언 실패 시 AssertionError
#------------------------------------------------------------------
@pytest.mark.skipif(not HAS_DIST, reason="배포본(dist-onedir) 없음")
def test_정상_실행도_결과와_요약을_남긴다(tmp_path):
    doc = tmp_path / "plain.txt"
    doc.write_text("봄이 오면 마당에 심은 나무에 새 잎이 돋는다. "
                   "아이들이 마당에서 뛰논다. "
                   "저녁이면 온 식구가 둘러앉아 밥을 먹는다.\n", encoding="utf-8")

    env = dict(os.environ)
    env["CSOCLASSIFY_LOGDIR"] = str(tmp_path)
    r = subprocess.run([DIST_EXE, "--file", str(doc), "--simple"],
                       cwd=os.path.dirname(DIST_EXE), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env)
    assert r.returncode == 0, r.stderr

    body = _read(tmp_path / os.path.basename(logsetup.default_log_path()))
    # 파일별 판정 — 파이썬 판 cli._emit 이 남기는 줄과 같은 이름을 쓴다.
    assert "결과 " in body, f"파일별 결과가 없다: {body!r}"
    assert "plain.txt" in body
    # 이번 실행을 몇 건으로 끝냈는지. 로그를 뒤에서부터 읽을 때 가장 먼저 볼 줄이다.
    assert "요약 " in body, f"요약이 없다: {body!r}"

    # 정상 실행이므로 오류 로그는 생기지 않아야 한다 — 파일이 있다는 것만으로
    # "오류가 있었다"를 뜻해야 그 파일이 쓸모가 있다.
    errname = os.path.basename(logsetup.default_err_log_path())
    assert not (tmp_path / errname).exists(), "정상 실행인데 오류 로그가 생겼다"
