# -*- coding: utf-8 -*-
"""화면이 엔진의 오류 계약(--json-errors)을 제대로 받아 쓰는가.

설계: plan/CLI-오류출력-설계.html
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "ui") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "ui"))

import gerunner  # noqa: E402


#------------------------------------------------------------------
# 오류 JSON 한 줄을 골라 읽는다
#=> 엔진의 stdout 에는 결과 본문이 함께 흐를 수 있다. 그 안에서 오류 줄만
#   골라야 하고, 여러 줄이면 마지막 것이 실제 실패다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_결과와_섞여_있어도_오류줄만_읽는다():
    out = ('{"file":"a.hwp","grade":"S"}\n'
           '{"error":{"code":1001,"kind":"no_input","message":"없다","path":"D:/x"}}\n')
    assert gerunner.parse_error_json(out) == {
        "code": 1001, "kind": "no_input", "message": "없다", "path": "D:/x"}


#------------------------------------------------------------------
# 오류가 없거나 JSON 이 깨져 있으면 조용히 None
#=> 화면은 오류 표시를 하려다 자기가 죽으면 안 된다. 계약을 못 읽으면
#   예전처럼 stderr 를 보여 주는 길로 돌아간다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_없거나_깨진_JSON은_None():
    assert gerunner.parse_error_json("") is None
    assert gerunner.parse_error_json(None) is None
    assert gerunner.parse_error_json("[summary] 총 3개\n") is None
    assert gerunner.parse_error_json('{"error": 여기서깨짐\n') is None


#------------------------------------------------------------------
# 사람이 읽는 한 줄에 kind(code) 가 함께 붙는다
#=> 화면과 오류 로그가 같은 문장을 쓰게 하려는 것이다. 문구는 다듬어질 수
#   있으므로, 나중에 같은 사고를 찾을 때 쓰는 것은 번호다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_화면_한줄에_kind와_code가_붙는다():
    line = gerunner.error_line({"code": 2001, "kind": "rules_missing",
                                "message": "규칙셋이 없습니다"})
    assert line == "규칙셋이 없습니다 [rules_missing(2001)]"
    assert gerunner.error_line(None) == ""


#------------------------------------------------------------------
# --json-errors 는 한 번만 붙는다
#=> 중복 인자를 주면 엔진이 경고를 찍어 로그가 지저분해진다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_json_errors는_한_번만_붙는다():
    assert gerunner.with_json_errors(["exe", "--dir", "."]) == \
        ["exe", "--dir", ".", "--json-errors"]
    once = gerunner.with_json_errors(["exe", "--json-errors"])
    assert once.count("--json-errors") == 1


#------------------------------------------------------------------
# 실제로 엔진을 돌려 실패를 계약으로 받는다
#=> 인자 조립·stdout 수집·JSON 해석이 한 줄로 이어지는지는 실제 프로세스로만
#   확인할 수 있다. stdout 을 버리지 않고 받도록 바꾼 것이 이 테스트의 핵심이다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 skip
#------------------------------------------------------------------
def test_실행해서_실패를_계약으로_받는다(tmp_path):
    import pytest
    exe = os.path.join(ROOT, "Rust", "target", "release", "MpowerClassify-rs.exe")
    if not os.path.isfile(exe):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    # 정책 폴더를 짚어 준다 — 안 그러면 규칙셋을 못 찾아(exe 옆에 없다) 이 시험이
    # 보려는 '대상 없음'이 아니라 '규칙셋 없음'이 난다.
    os.environ["CSOCLASSIFY_POLICY_DIR"] = os.path.join(ROOT, "resources", "policy")
    try:
        r = gerunner.run_csoclassify_dir_stream(
            [exe], str(tmp_path / "없는폴더"), str(tmp_path / "out.jsonl"), "*")
    finally:
        os.environ.pop("CSOCLASSIFY_POLICY_DIR", None)
    assert r.returncode == 3
    assert r.error["kind"] == "no_input"
    assert r.error["code"] == 1001
