# -*- coding: utf-8 -*-
"""전파용 seed 가 없을 때 '조용히' 넘어가지 않는가.

seed 파일이 없으면 전파(Signal B)가 통째로 꺼진다. 규칙이 못 정한 문서는
구제받지 못하고 등급 없이(null) 나가는데, 화면에는 그냥 '미분류'로 보인다.
"읽어 봤더니 신호가 없더라"와 "구제할 수단 자체가 없었다"는 대응이 전혀 다른데,
겉모습이 같아서 구분되지 않는다.

실제로 2026-08 Python 배포본에서 class_seed.jsonl 이 빠져 보류 21건이 미분류로
나갔고, 아무 오류도 없어 한동안 아무도 몰랐다(부록 C-5). 배포 폴더는 git 이
추적하지 않으므로 파일을 넣는 것만으로는 재발을 막을 수 없다 — 그래서 '무엇이
꺼졌는지'를 실행할 때마다 말하게 하고, 그 말을 이 시험이 지킨다.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 이 문구가 바뀌면 시험도 함께 바뀌어야 한다 — 사람이 읽는 경고라 문구가 곧 계약이다.
WARN = "전파용 seed 파일이 없어 자동 전파를 건너뜁니다"


#------------------------------------------------------------------
# csoclassify 를 별도 프로세스로 한 번 돌린다
#=> 경고는 stderr 로 나가므로 프로세스를 실제로 띄워야 확인할 수 있다.
#   CSOCLASSIFY_POLICY_DIR 를 임시폴더로 돌려 seed 유무를 이 시험이 직접 정하되,
#   규칙셋만은 --rules 로 저장소 파일을 명시해 준다(빈 폴더면 '규칙셋 없음'으로
#   먼저 죽어 seed 판정에 닿지 못한다 — 실제로 그렇게 거짓 통과한 적이 있다).
#
# -in: tmp    = 정책 디렉터리로 쓸 임시 경로(pathlib.Path)
# -in: doc    = 분류할 문서 경로
# -in: extra  = 덧붙일 CLI 인자 리스트
#
# -out: CompletedProcess = stdout/stderr 가 담긴 실행 결과
# -out: error = 없음(종료코드는 호출부가 본다)
#------------------------------------------------------------------
def _run(tmp, doc, extra=()):
    # 규칙셋은 저장소의 실제 파일을 명시적으로 준다 — 이걸 임시폴더에 맡기면
    # '규칙셋 없음'으로 먼저 죽어, seed 판정에 닿지도 못한 채 시험이 통과해 버린다.
    env = dict(os.environ,
               PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8",
               CSOCLASSIFY_POLICY_DIR=str(tmp))
    rules = os.path.join(ROOT, "resources", "policy", "cso_rule.yaml")
    return subprocess.run(
        [sys.executable, "-m", "csoclassify", "--file", str(doc),
         "--rules", rules,
         "--format", "jsonl", "--no-daemon", "--nosummary", *extra],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")


#------------------------------------------------------------------
# 시험용 문서 하나 만들기
#=> 규칙에 걸릴 만한 내용을 넣지 않는다 — 등급이 정해지든 말든 이 시험이 보는
#   것은 '경고를 냈는가'뿐이고, 본문이 짧으면 '본문 없음' 경로로 새기 때문이다.
#
# -in: tmp_path = pytest 임시 폴더
#
# -out: Path = 만들어진 문서 경로
# -out: error = 없음
#------------------------------------------------------------------
def _doc(tmp_path):
    d = tmp_path / "문서.txt"
    d.write_text(
        "월간 운영 보고\n\n"
        "이번 달 시스템 운영 현황을 아래와 같이 정리하여 보고합니다.\n"
        "장애는 없었고 정기 점검을 예정대로 수행했습니다.\n",
        encoding="utf-8")
    return d


#------------------------------------------------------------------
# seed 가 없으면 '전파가 꺼졌다'고 분명히 말한다 (핵심 회귀)
#=> 예전에는 이 자리에서 아무 말도 하지 않았다. 배포 폴더에 파일이 빠진 것을
#   아무도 알아채지 못한 원인이 정확히 이것이다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 경고가 사라지면 AssertionError
#------------------------------------------------------------------
def test_seed_없으면_경고한다(tmp_path):
    r = _run(tmp_path, _doc(tmp_path))
    assert WARN in r.stderr, f"seed 없는데 조용히 넘어갔다 :: {r.stderr[-500:]}"
    # 무엇을 해야 하는지도 함께 알려 준다 — 경고만 있고 대응이 없으면 무시된다.
    assert "--rule-only" in r.stderr


#------------------------------------------------------------------
# --rule-only 면 경고하지 않는다 (오탐 방지)
#=> 규칙만 쓰는 배포는 seed 가 없는 것이 정상이다. 그런 실행마다 경고가 뜨면
#   금세 무시하게 되고, 정작 진짜 누락일 때도 눈에 안 들어온다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 불필요한 경고가 생기면 AssertionError
#------------------------------------------------------------------
def test_rule_only면_경고하지_않는다(tmp_path):
    r = _run(tmp_path, _doc(tmp_path), ["--rule-only"])
    # 먼저 '실제로 분류가 돌았는지'를 확인한다 — 규칙셋 부재 같은 조기 실패로
    # 끝나도 경고는 안 뜨므로, 그대로 두면 이 시험이 거짓 통과한다.
    assert '"file"' in r.stdout, f"분류가 돌지 않았다 :: {r.stderr[-500:]}"
    assert WARN not in r.stderr, f"의도적으로 끈 실행에 경고가 떴다 :: {r.stderr[-500:]}"


#------------------------------------------------------------------
# seed 가 있으면 경고하지 않고 그 파일을 읽는다
#=> 배포가 제대로 된 상태. '몇 건을 읽었는지'가 화면에 남아야 사람이 확인할 수
#   있다(빌드 가이드의 확인 절차가 이 줄을 보라고 안내한다).
#   임베딩 모델이 없는 환경에서도 seed 를 '읽는' 데까지는 문제가 없어야 한다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 경고가 뜨거나 로드 줄이 없으면 AssertionError
#------------------------------------------------------------------
def test_seed_있으면_경고없이_읽는다(tmp_path):
    # 저장소 정본을 그대로 쓰지 않고 최소 seed 를 만든다 — 시험이 정본의 내용
    # 변화에 흔들리지 않게 하려는 것이다.
    src = os.path.join(ROOT, "ui", "policy", "class_seed.jsonl")
    if not os.path.isfile(src):
        pytest.skip("정본 seed 없음")
    with open(src, encoding="utf-8") as f:
        one = f.readline()
    (tmp_path / "class_seed.jsonl").write_text(one, encoding="utf-8")

    r = _run(tmp_path, _doc(tmp_path))
    assert WARN not in r.stderr, f"seed 가 있는데 경고가 떴다 :: {r.stderr[-500:]}"
    assert "외부 seed" in r.stderr, f"seed 를 읽었다는 표시가 없다 :: {r.stderr[-500:]}"
