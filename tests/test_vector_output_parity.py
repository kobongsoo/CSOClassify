#------------------------------------------------------------------
# 벡터가 '언제 결과에 실려 나가는가' — 두 판(Python·Rust) 회귀 시험
#=> tests/test_doctype_parity.py 와 같은 정신이다. 거기서는 업무분류 값을
#   못 박았고, 여기서는 **vector 칸의 유무**를 못 박는다.
#
#   [무엇이 갈려 있었나 — 2026-09-11]
#   임베딩한 문서면 파이썬은 **언제나** 384차원 실수 배열을 결과에 실었다.
#   Rust 는 `--with-vector` / `--embed-needed` 를 준 때만 실었다. 그래서
#   인자 없이 같은 명령을 돌려도 한쪽 결과 파일에만 벡터가 들어 있었다.
#
#   두 가지가 걸린다:
#     · 두 판의 결과를 나란히 견줄 수 없다(이 프로젝트의 기본 계약).
#     · 벡터는 본문의 파생물이다. 인자를 준 적 없는 사용자의 결과 파일에
#       그것이 들어가면, 공유·백업 때 본문과 같은 급으로 다뤄야 할 것이
#       조용히 딸려 나간다.
#   두 인자의 도움말은 이미 "벡터 산출/포함"이라고 말하고 있었다 — 코드만
#   안 따랐다. 그래서 Rust 쪽 동작을 기준으로 삼았다.
#
#   [왜 세 갈래를 다 보나] '안 실린다'만 보면, 두 판이 나란히 아무것도 안
#   싣게 만들어도 시험이 통과한다. 인자를 줬을 때 **실제로 실리는지**와
#   그 건수까지 함께 봐야 판정이 뜻을 갖는다.
#------------------------------------------------------------------

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS_EXE = os.path.join(ROOT, "Rust", "target", "release", "MpowerClassify-rs.exe")
POLICY = os.path.join(ROOT, "ui", "policy", "cso_rule.yaml")
DOC_RULES = os.path.join(ROOT, "ui", "policy", "doc_rule.yaml")
TAXONOMY = os.path.join(ROOT, "ui", "policy", "doc_taxonomy.yaml")
MODEL_DIR = os.path.join(ROOT, "dist-onedir", "windows", "models", "e5-small-ko")

# 규칙으로 등급이 확정되고 seed 승격 후보(seed_eligible)까지 붙는 문서.
# '대외비' 스탬프가 그 자리를 만든다 — 그래야 --embed-needed 가 이 문서를
# 임베딩 대상으로 골라, 세 갈래의 건수가 서로 달라진다.
FIXTURE_HTML = """<!doctype html>
<html><head><title>출장여비규정</title></head>
<body>
<h1>대외비 — 출장여비규정</h1>
<p>제1조(목적) 이 규정은 임직원의 국내외 출장에 따른 여비의 지급 기준을 정한다.</p>
<p>제2조(적용범위) 이 규정은 회사의 전 임직원에게 적용한다.</p>
</body></html>
"""


#------------------------------------------------------------------
# 한 엔진을 돌려 '문서 레코드'만 돌려준다
#=> 결과에는 문서 줄 말고도 실행 헤더({"run":…})와 요약({"summary":…})이
#   섞여 있다. 'file' 이 있는 줄만 문서다 — 두 판이 쓰는 기준과 같다.
#
# -in: engine  = "python" | "rust"
# -in: doc_dir = 분류할 폴더
# -in: extra   = 덧붙일 인자 목록(예: ["--with-vector"])
#
# -out: list = 문서 레코드 목록
# -out: error = 실행 실패·레코드 0건이면 AssertionError(표준오류를 함께 보여 준다)
#------------------------------------------------------------------
def _run(engine, doc_dir, seeds, extra):
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    # Rust 는 모델 경로를 인자로, Python 은 환경변수로 받는다.
    model = ["--model", MODEL_DIR] if engine == "rust" else []
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8", CSOCLASSIFY_MODEL_DIR=MODEL_DIR)
    r = subprocess.run(
        cmd + ["--dir", str(doc_dir), "--rules", POLICY,
               "--doc-rules", DOC_RULES, "--taxonomy", TAXONOMY,
               "--seeds", str(seeds),
               "--format", "jsonl", "--nosummary"] + model + list(extra),
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    recs = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(j, dict) and isinstance(j.get("file"), str):
            recs.append(j)
    assert recs, "%s %s: 레코드가 없다 :: %s" % (engine, extra, r.stderr[-600:])
    return recs


#------------------------------------------------------------------
# 픽스처 폴더 만들기
#=> 시험마다 새 임시 폴더를 쓴다(pytest 가 병렬로 돌아도 안 섞이게).
#
# -in: tmp_path = pytest 가 준 임시 폴더
#
# -out: 문서 폴더 경로
# -out: error = 없음
#------------------------------------------------------------------
def _fixture(tmp_path):
    d = tmp_path / "docs"
    # parents=True — 부르는 쪽이 tmp_path 밑에 한 겹 더 판 자리를 넘기기도 한다
    # (두 판을 나란히 돌릴 때 서로 다른 폴더를 써야 결과가 안 섞인다).
    d.mkdir(parents=True)
    (d / "출장여비규정.html").write_text(FIXTURE_HTML, encoding="utf-8")

    # seed 파일을 함께 깐다. **이게 없으면 시험이 통째로 무의미해진다** —
    # 비교할 seed 가 없으면 두 판 모두 임베딩을 아예 건너뛰고, 그러면
    # "벡터가 안 실린다"가 저절로 참이 되어 게이트를 꺼도 시험이 통과한다.
    # (실제로 처음 판이 그랬다. 수정을 되돌려 보고서야 알았다.)
    # 문서 자신의 벡터를 seed 로 주면 유사도 1.0 이라 2단계가 확실히 돈다.
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8", CSOCLASSIFY_MODEL_DIR=MODEL_DIR)
    r = subprocess.run(
        [sys.executable, "-m", "csoclassify", "--dir", str(d),
         "--rules", POLICY, "--doc-rules", DOC_RULES, "--taxonomy", TAXONOMY,
         "--with-vector", "--format", "jsonl", "--nosummary"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    vec, dc_ids = None, []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(j, dict) and isinstance(j.get("vector"), list):
            vec = j["vector"]
            block = (j.get("why") or {}).get("doctype")
            if not isinstance(block, dict):
                block = j["doctype"] if isinstance(j.get("doctype"), dict) else {}
            dc_ids = [v["dc_id"] for v in (block.get("values") or [])]
            break
    if vec is None:
        pytest.skip("임베딩 모델이 없어 벡터를 못 얻었다: %s" % MODEL_DIR)
    # doctype 라벨이 **함께** 있어야 한다. 등급과 벡터만 적으면 업무분류
    # seed 저장소가 0건이 되어 2단계가 아예 안 돌고, 그러면 아래 시험이
    # 다시 무의미해진다(처음에 그렇게 적었다가 liveness 단언에 걸렸다).
    if not dc_ids:
        pytest.skip("픽스처가 업무분류 라벨을 못 받았다 — doc_rule.yaml 이 바뀌었는지 보라")
    seeds = tmp_path / "class_seed.jsonl"
    seeds.write_text(json.dumps({"file": "fixture-seed.html", "grade": "C",
                                 "doctype": dc_ids, "vector": vec},
                                ensure_ascii=False) + "\n", encoding="utf-8")
    return d, seeds


# 두 판을 나란히 돌리는 시험이라 둘 다 갖춰져야 뜻이 있다. 갖춰지지 않은
# 환경에서는 '통과'가 아니라 '건너뜀'이어야 한다 — 통과로 세면 지켜 주는
# 것이 없는데 지켜 주는 줄 알게 된다.
_needs_both = pytest.mark.skipif(
    not (os.path.isfile(RS_EXE) and os.path.isdir(MODEL_DIR)),
    reason="Rust exe(cargo build --release) 또는 임베딩 모델이 없다")


#------------------------------------------------------------------
# 인자를 안 주면 두 판 모두 벡터를 안 싣는다
#=> 이 시험이 못 박는 바로 그 사고다. 파이썬이 인자 없이도 384차원 배열을
#   실어, 같은 명령의 결과가 판마다 달랐다.
#------------------------------------------------------------------
@_needs_both
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_인자가_없으면_벡터를_안_싣는다(engine, tmp_path):
    doc_dir, seeds = _fixture(tmp_path)
    recs = _run(engine, doc_dir, seeds, [])
    bad = [r["file"] for r in recs if "vector" in r]
    assert not bad, ("%s: 요청하지 않았는데 벡터가 실렸다" % engine, bad)
    # 이 실행이 임베딩을 **실제로 했는지** 못 박는다. 안 했다면 위 단언은
    # 저절로 참이 되어 아무것도 지켜 주지 않는다 — 2단계를 탄 흔적(from 에
    # "embed")이 있어야 "만들었지만 안 실었다"가 확인된다.
    staged = []
    for r in recs:
        block = (r.get("why") or {}).get("doctype")
        if not isinstance(block, dict):
            block = r.get("doctype") if isinstance(r.get("doctype"), dict) else {}
        for v in (block.get("values") or []):
            staged += [f for f in (v.get("from") or []) if f == "embed"]
    assert staged, ("%s: 임베딩이 안 돌아 이 시험이 아무것도 지켜 주지 않는다" % engine)


#------------------------------------------------------------------
# 인자를 주면 두 판 모두 벡터를 싣고, 건수까지 같다
#=> '안 실린다'만 보면 둘 다 아무것도 안 실어도 통과한다. 실제로 실리는지와
#   몇 건인지를 함께 봐야 판정이 뜻을 갖는다.
#   --with-vector 는 전량, --embed-needed 는 '아직 못 정했거나 seed 승격
#   후보'만 — 두 인자의 뜻이 다르므로 갈래를 나눠 본다.
#------------------------------------------------------------------
@_needs_both
@pytest.mark.parametrize("flag", ["--with-vector", "--embed-needed"])
def test_인자를_주면_두_판이_같은_건수의_벡터를_싣는다(flag, tmp_path):
    pdir, pseeds = _fixture(tmp_path / "p")
    rdir, rseeds = _fixture(tmp_path / "r")
    py = _run("python", pdir, pseeds, [flag])
    rs = _run("rust", rdir, rseeds, [flag])
    npy = sum(1 for r in py if isinstance(r.get("vector"), list))
    nrs = sum(1 for r in rs if isinstance(r.get("vector"), list))
    assert npy == nrs, ("%s: 벡터를 실은 건수가 다르다" % flag, npy, nrs)
    assert npy > 0, ("%s: 인자를 줬는데 어느 판도 벡터를 안 실었다" % flag)
    # 차원까지 같아야 한다 — 한쪽만 잘려 나가면 전파가 조용히 어긋난다.
    dims = {len(r["vector"]) for r in py + rs if isinstance(r.get("vector"), list)}
    assert len(dims) == 1, ("벡터 차원이 판마다 다르다", dims)


#------------------------------------------------------------------
# 2차 패스(--propagate)가 실행 헤더를 문서로 세지 않는다
#=> 1차 결과에는 맨 앞에 {"run":…} 헤더가 있다. 파이썬은 그것을 문서로 받아
#   등급 없는 유령 레코드 한 줄을 결과에 실어 보냈다(실측 19줄 vs 18줄).
#   오류가 안 나는 종류라, 2차 패스를 돌릴 때마다 미분류가 1건씩 늘어나는데도
#   그것이 문서인 줄 알았다.
#------------------------------------------------------------------
@_needs_both
def test_전파_2차패스가_실행헤더를_문서로_세지_않는다(tmp_path):
    doc_dir, _seeds = _fixture(tmp_path)
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8", CSOCLASSIFY_MODEL_DIR=MODEL_DIR)
    pol = ["--rules", POLICY, "--doc-rules", DOC_RULES, "--taxonomy", TAXONOMY]
    p1 = tmp_path / "p1.jsonl"
    # 1차 — 헤더 줄이 들어간 jsonl 을 만든다(--nosummary 를 주지 않아야 요약도 붙는다).
    r1 = subprocess.run(
        [sys.executable, "-m", "csoclassify", "--dir", str(doc_dir),
         "--out", str(p1), "--format", "jsonl", "--with-vector"] + pol,
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    raw = p1.read_text(encoding="utf-8") if p1.exists() else ""
    if '"run"' not in raw:
        pytest.skip("1차 결과에 실행 헤더가 없다 — 이 시험이 겨누는 자리가 사라졌다: %s"
                    % r1.stderr[-300:])

    # 2차 — 헤더가 섞인 그 파일을 그대로 먹인다.
    p2 = tmp_path / "p2.json"
    subprocess.run(
        [sys.executable, "-m", "csoclassify", "--propagate", str(p1),
         "--out", str(p2), "--format", "json"] + pol,
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    # --format json 은 문서가 하나뿐이면 배열이 아니라 객체 하나로 나온다.
    # 세 모양(객체 1개 · {"records":[…]} · 배열)을 모두 받아야 한다.
    data = json.loads(p2.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        recs = data["records"] if "records" in data else [data]
    else:
        recs = data
    ghost = [r for r in recs
             if not (isinstance(r, dict) and isinstance(r.get("file"), str))]
    assert not ghost, ("문서가 아닌 줄이 결과에 실렸다", ghost[:1])
