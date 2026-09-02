# -*- coding: utf-8 -*-
"""정책 파일을 어느 순서로 찾는가 — 두 판이 같아야 한다.

관리자가 CSOCLASSIFY_POLICY_DIR 을 지정했는데 exe 옆에도 같은 이름의 파일이
있으면, 어느 쪽을 읽느냐로 **등급이 통째로 달라진다**. 2026-09-01 이전 Rust 판은
exe 옆을 먼저 읽어 파이썬 판과 다른 규칙셋을 썼고, 실측 15건 중 11건의 등급이
갈렸다 — 그것도 아무 경고 없이.
"""
import os
import shutil
import subprocess
import sys

import pytest

from csoclassify.classify import rules as R

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS_EXE = os.path.join(ROOT, "Rust", "target", "release", "csoclassify-rs.exe")
POLICY = os.path.join(ROOT, "resources", "policy")


#------------------------------------------------------------------
# 환경변수가 exe 옆보다 먼저다 (파이썬 판)
#=> 환경변수는 관리자가 "이 정책을 써라"고 직접 지시한 것이고, exe 옆 파일은
#   그냥 거기 있을 뿐이다. 지시가 우선해야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_환경변수가_먼저다(monkeypatch, tmp_path):
    monkeypatch.setenv("CSOCLASSIFY_POLICY_DIR", str(tmp_path))
    assert R.default_rules_path() == os.path.join(str(tmp_path), "cso_rules.yaml")


#------------------------------------------------------------------
# 두 판이 같은 규칙셋 파일을 고른다 (실제 실행)
#=> 순서가 어긋나면 등급이 조용히 갈린다. 'exe 옆에도 파일이 있는' 상황을
#   시험이 직접 만들어 낸다 — 우연히 그런 파일이 있을 때만 도는 시험은,
#   정작 그 파일을 치운 날 조용히 건너뛰어 버그를 놓친다(실제로 그럴 뻔했다).
#    1) exe 옆에 알아볼 수 있는 규칙셋을 잠깐 놓는다
#    2) 환경변수로 진짜 규칙셋을 가리킨다
#    3) 두 판이 모두 환경변수 쪽을 골랐는지 본다
#    4) 놓아 둔 파일은 반드시 치운다(다음 실행에 영향을 주면 안 된다)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = Rust exe 가 없으면 skip
#------------------------------------------------------------------
def test_두_판이_같은_규칙셋을_고른다():
    if not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    beside = os.path.join(os.path.dirname(RS_EXE), "cso_rules.yaml")
    if os.path.exists(beside):
        pytest.skip(f"exe 옆에 이미 파일이 있다(건드리지 않는다): {beside}")

    # 진짜 규칙셋을 그대로 복사해 둔다 — 내용이 유효해야 --check-rules 가 통과한다.
    shutil.copyfile(os.path.join(POLICY, "cso_rules.yaml"), beside)
    try:
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
                   PYTHONIOENCODING="utf-8", CSOCLASSIFY_POLICY_DIR=POLICY)
        picked = {}
        for name, cmd in (("python", [sys.executable, "-m", "csoclassify"]),
                          ("rust", [RS_EXE])):
            r = subprocess.run(cmd + ["--check-rules"], cwd=ROOT, env=env,
                               capture_output=True, text=True, encoding="utf-8")
            head = r.stdout.splitlines()[0]
            # "규칙셋 정상: <경로>" 의 경로만 뽑아 정규화해서 견준다.
            picked[name] = os.path.normcase(os.path.abspath(head.split(": ", 1)[1].strip()))
    finally:
        os.remove(beside)
    want = os.path.normcase(os.path.join(POLICY, "cso_rules.yaml"))
    assert picked["python"] == want, "파이썬 판이 환경변수를 무시했다"
    assert picked["rust"] == want, "Rust 판이 환경변수를 무시했다(2026-09-01 이전 버그)"
