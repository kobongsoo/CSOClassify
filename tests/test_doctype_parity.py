#------------------------------------------------------------------
# 두 판(Python·Rust)의 업무분류 결과가 전파까지 켠 채 같은지 — 회귀 시험
#=> tests/test_signals_parity.py 와 같은 정신이다. 거기서는 등급 신호를
#   못 박았고, 여기서는 **업무분류(doctype)의 dc_id 와 confidence** 를 못 박는다.
#
#   [무엇이 실제로 갈려 있었나 — 2026-09-10]
#   기본 경로(--auto-propagate)에서 두 판이 "어떤 문서에 벡터를 만들 것인가"를
#   다르게 골랐다. 임베딩은 비싸서 필요한 문서만 고르는데, 고르는 조건이 셋이다:
#     ① 등급 보류(grade=None)  ② seed 승격 후보(seed_eligible)  ③ 업무분류 미정
#   Rust 는 ② 를 최상위 rec["seed_eligible"] 에서 읽고 있었다. 그런데 레코드
#   평탄화로 그 칸은 등급 축 안으로 옮겨져 최상위에는 없다 — 늘 false 였다.
#   오류도 경고도 안 나고 값만 조용히 뒤집히는 종류였다.
#
#   그래서 규칙이 고신뢰로 찍어 준 문서가 임베딩에서 통째로 빠지고, 업무분류
#   2단계(seed 비교)가 사라졌다. 실측(d:/sample 18건)에서 세 문서의 confidence 가
#   갈렸다 — 25.출장여비규정 1.0↔0.755 · 취업규칙 1.0↔0.906.
#
#   [왜 confidence 까지 보나] 라벨(dc_id)은 우연히 같았다. 갈린 것은 숫자뿐이라
#   dc_id 만 견주는 시험은 이 사고를 통과시킨다. 게다가 이 숫자는 장식이 아니다 —
#   검토 큐 차례를 정하고, seedgen.candidate_ok 의 t_seed(기본 0.85)를 넘느냐를
#   가른다. 0.755 와 1.0 은 그 문턱의 양쪽이라, 같은 문서가 판에 따라 업무분류
#   seed 가 되기도 하고 안 되기도 했다.
#
#   [왜 픽스처를 seed 로 다시 쓰나] 2단계를 태우려면 비교할 seed 가 있어야 한다.
#   문서 자신의 벡터를 seed 로 주면 유사도가 1.0 이 되어 dup_inherit 가지를
#   확실히 타므로, 임베딩이 돌았는지 여부가 결과에 또렷이 드러난다.
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

# 규칙으로 등급이 확정되고 seed_eligible 까지 붙는 문서여야 한다 — 그래야 위
# ①③ 이 아니라 오직 ② 때문에 임베딩 대상이 되고, 이 시험이 그 한 가지를 겨눈다.
# '대외비' 스탬프가 그 자리를 만든다(cso_rule.yaml 의 stamp 규칙).
FIXTURE_HTML = """<!doctype html>
<html><head><title>출장여비규정</title></head>
<body>
<h1>대외비 — 출장여비규정</h1>
<p>제1조(목적) 이 규정은 임직원의 국내외 출장에 따른 여비의 지급 기준을 정함을
   목적으로 한다. 본 규정은 사내규정으로서 취업규칙에 준하여 적용한다.</p>
<p>제2조(적용범위) 이 규정은 회사의 전 임직원에게 적용한다.</p>
<p>제3조(여비의 구성) 여비는 운임·일비·숙박비·식비로 구성한다.</p>
</body></html>
"""


#------------------------------------------------------------------
# 한 엔진을 전파까지 켠 채 돌려 레코드 목록을 돌려준다
#=> 두 판을 같은 정책·같은 seed·같은 모델로 돌려야 비교에 뜻이 있다.
#   --rule-only 를 주지 않는다 — 이 시험이 겨누는 것이 바로 그 2단계다.
#
# -in: engine  = "python" | "rust"
# -in: doc_dir = 분류할 폴더
# -in: seeds   = 업무분류 seed 파일(class_seed.jsonl) 경로
#
# -out: list = 결과 레코드 목록(run 헤더·summary 줄은 걸러낸 것)
# -out: error = 실행 실패·레코드 0건이면 AssertionError(표준오류를 함께 보여 준다)
#------------------------------------------------------------------
def _run(engine, doc_dir, seeds):
    cmd = [RS_EXE] if engine == "rust" else [sys.executable, "-m", "csoclassify"]
    # Rust 는 모델 경로를 인자로, Python 은 환경변수로 받는다.
    extra = ["--model", MODEL_DIR] if engine == "rust" else []
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8", CSOCLASSIFY_MODEL_DIR=MODEL_DIR)
    r = subprocess.run(
        cmd + ["--dir", str(doc_dir), "--rules", POLICY,
               "--doc-rules", DOC_RULES, "--taxonomy", TAXONOMY,
               "--seeds", str(seeds), "--format", "jsonl", "--nosummary"] + extra,
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    recs = [json.loads(l) for l in r.stdout.splitlines()
            if l.strip().startswith("{") and '"file"' in l]
    assert recs, "%s: 레코드가 없다 :: %s" % (engine, r.stderr[-600:])
    return recs


#------------------------------------------------------------------
# 레코드에서 업무분류 후보 목록 꺼내기 — 세 세대 모양을 모두 흡수
#=> 레코드 모양이 2026-09-10 하루에만 두 번 바뀌었다(최상위 → security 칸 →
#   grade + why). 시험이 한 모양에만 매달리면 다음 이관에서 조용히 깨진다.
#   csoclassify.record.doctype_values 를 그대로 쓰지 않는 이유는, 이 시험이
#   Python 판을 '바깥에서' 보는 시험이기 때문이다 — 검사 대상의 도우미로
#   검사하면 그 도우미가 틀렸을 때 두 쪽이 함께 틀린다.
#
# -in: rec = 결과 레코드 1건
#
# -out: list = doctype 후보 dict 목록. 축을 안 돌린 레코드면 None
# -out: error = 없음
#------------------------------------------------------------------
def _doctype_values(rec):
    for block in ((rec.get("why") or {}).get("doctype"),          # 3세대
                  rec.get("doctype"),                              # 2세대
                  (rec.get("labels") or {}).get("doctype")):       # 1세대
        if isinstance(block, dict):
            return block.get("values") or []
    return None


#------------------------------------------------------------------
# 견주기 좋은 모양으로 — (dc_id, confidence) 를 정렬해 돌려준다
#=> 후보 차례는 판마다 다를 수 있고(HashMap 순회) 그건 이 시험이 겨누는
#   것이 아니다. 정렬해 두면 "무엇을 몇 점으로 봤나"만 남는다.
#
# -in: values = _doctype_values() 가 준 후보 목록
#
# -out: list = (dc_id, 소수 셋째 자리까지 반올림한 confidence) 정렬 목록
# -out: error = 없음
#------------------------------------------------------------------
def _key(values):
    return sorted((v["dc_id"], round(float(v["confidence"]), 3)) for v in values)


#------------------------------------------------------------------
# 픽스처를 만들고, 그 문서 자신의 벡터를 업무분류 seed 로 깔아 둔다
#=> 2단계가 돌 상대가 있어야 이 시험이 뜻을 갖는다. Python 판을 --with-vector
#   로 한 번 돌려 벡터를 얻고, class_seed.jsonl 한 줄로 적는다.
#
# -in: tmp_path = pytest 가 준 임시 폴더
#
# -out: (doc_dir, seeds_path) = 문서 폴더와 seed 파일 경로
# -out: error = 벡터·라벨을 못 얻으면 pytest.skip(모델이 없거나 정책이 바뀐 환경)
#------------------------------------------------------------------
def _make_fixture(tmp_path):
    doc_dir = tmp_path / "docs"
    doc_dir.mkdir()
    (doc_dir / "출장여비규정.html").write_text(FIXTURE_HTML, encoding="utf-8")

    # 1) 벡터를 먼저 뽑는다(--with-vector 는 전량 임베딩이라 조건과 무관하게 나온다).
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8", CSOCLASSIFY_MODEL_DIR=MODEL_DIR)
    r = subprocess.run(
        [sys.executable, "-m", "csoclassify", "--dir", str(doc_dir),
         "--rules", POLICY, "--doc-rules", DOC_RULES, "--taxonomy", TAXONOMY,
         "--with-vector", "--format", "jsonl", "--nosummary"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    recs = [json.loads(l) for l in r.stdout.splitlines()
            if l.strip().startswith("{") and '"file"' in l]
    if not recs or not recs[0].get("vector"):
        pytest.skip("임베딩 모델이 없어 2단계를 태울 수 없다: %s" % MODEL_DIR)

    # 2) 그 벡터를 seed 한 줄로 적는다. dc_id 는 문서가 실제로 받은 라벨을 쓴다 —
    #    taxonomy 에 없는 id 를 적으면 두 판 모두 조용히 무시해 시험이 무력해진다.
    vals = _doctype_values(recs[0]) or []
    if not vals:
        pytest.skip("픽스처가 업무분류 라벨을 못 받았다 — doc_rule.yaml 이 바뀌었는지 보라")
    seeds = tmp_path / "class_seed.jsonl"
    seeds.write_text(json.dumps({
        "file": "fixture-seed.html",
        "grade": "C",
        "doctype": [v["dc_id"] for v in vals],
        "vector": recs[0]["vector"],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc_dir, seeds


#------------------------------------------------------------------
# 전파를 켜면 두 판의 업무분류가 dc_id 도 confidence 도 같다
#=> 이 시험이 못 박는 바로 그 사고다. Rust 가 seed_eligible 을 잘못된
#   자리에서 읽던 동안, 이 픽스처는 Rust 에서만 2단계를 못 타
#   confidence 가 규칙 점수에 머물고 Python 은 1.0 이 됐다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.skipif(not os.path.isdir(MODEL_DIR),
                    reason="임베딩 모델 없음(%s)" % MODEL_DIR)
def test_전파를_켠_업무분류가_두_판에서_같다(tmp_path):
    doc_dir, seeds = _make_fixture(tmp_path)
    py = _run("python", doc_dir, seeds)
    rs = _run("rust", doc_dir, seeds)
    assert len(py) == len(rs), ("레코드 건수가 다르다", len(py), len(rs))

    for a, b in zip(py, rs):
        va, vb = _doctype_values(a), _doctype_values(b)
        assert (va is None) == (vb is None), \
            ("한쪽만 업무분류 축을 돌렸다", a.get("file"), va, vb)
        if va is None:
            continue
        assert _key(va) == _key(vb), \
            ("업무분류가 다르다", a.get("file"), _key(va), _key(vb))


#------------------------------------------------------------------
# seed 승격 후보(seed_eligible)는 두 판 모두에서 실제로 임베딩된다
#=> 위 시험은 '결과가 같은가'를 본다. 이 시험은 '왜 같은가'를 본다 —
#   두 판이 나란히 2단계를 건너뛰어도 결과는 같아 위 시험은 통과한다.
#   2단계를 실제로 탔다는 증거는 후보의 from 에 "embed" 가 들어가는 것이다.
#------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isfile(RS_EXE),
                    reason="Rust exe 없음(cargo build --release 먼저)")
@pytest.mark.skipif(not os.path.isdir(MODEL_DIR),
                    reason="임베딩 모델 없음(%s)" % MODEL_DIR)
@pytest.mark.parametrize("engine", ["python", "rust"])
def test_seed후보는_2단계를_실제로_탄다(engine, tmp_path):
    doc_dir, seeds = _make_fixture(tmp_path)
    rec = _run(engine, doc_dir, seeds)[0]
    vals = _doctype_values(rec) or []
    assert vals, "%s: 업무분류 후보가 없다" % engine
    assert any("embed" in (v.get("from") or []) for v in vals), \
        ("%s: 어느 후보도 2단계를 안 탔다 — 임베딩 대상 판정이 어긋났다" % engine, vals)
