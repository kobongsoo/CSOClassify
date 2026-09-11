#------------------------------------------------------------------
# ui/uireset.py — 초기화(공장 초기화) 단위 테스트
#=> 되돌릴 수 없는 기능이라 "무엇을 지우고 무엇을 남기는가"를 코드가 아니라
#   테스트로 못박는다. 특히 ui 폴더에는 프로그램 소스가 함께 있어서,
#   "목록에 없는 것을 전부 지운다" 식으로 바뀌는 순간 앱이 사라진다.
#------------------------------------------------------------------

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "ui"))

import uireset as R   # noqa: E402


#------------------------------------------------------------------
# 실제 ui 폴더와 같은 모양의 가짜 폴더 만들기
#=> 소스·데이터·남길 자산·백업이 한곳에 섞여 있는 상태를 그대로 재현한다.
#
# -in: root = pytest tmp_path
#
# -out: str = 만든 ui 폴더 경로
# -out: error = 없음
#------------------------------------------------------------------
def make_ui(root):
    ui = root / "ui"
    pol = ui / "policy"
    syn = pol / "synonyms"
    for d in (ui, pol, syn):
        d.mkdir(parents=True, exist_ok=True)

    # 소스·설정(지우면 안 되는 것들)
    (ui / "app.py").write_text("code", encoding="utf-8")
    (ui / "seedstore.py").write_text("code", encoding="utf-8")
    (ui / "app.py.bak-dtev").write_text("code backup", encoding="utf-8")
    (ui / "실행.bat").write_text("bat", encoding="utf-8")
    (ui / "requirements.txt").write_text("streamlit", encoding="utf-8")
    (ui / "settings.yaml").write_text("paths: {}", encoding="utf-8")

    # 앱이 만든 데이터(지울 것)
    (ui / "cso_result.jsonl").write_text("{}", encoding="utf-8")
    (ui / "cso_result.jsonl.meta.json").write_text("{}", encoding="utf-8")
    (ui / "cso_override.jsonl").write_text("{}", encoding="utf-8")
    (ui / "class_seed_audit.jsonl").write_text("{}", encoding="utf-8")
    (ui / "grades.jsonl").write_text("{}", encoding="utf-8")
    (pol / "class_seed.jsonl").write_text("{}", encoding="utf-8")

    # policy 의 자산(남길 것)
    (pol / "doc_classification_export.json").write_text("{}", encoding="utf-8")
    (pol / "cso_rule.yaml").write_text("rules", encoding="utf-8")
    (pol / "doc_rule_template.yaml").write_text("tpl", encoding="utf-8")
    (syn / "doc_synonyms.core.yaml").write_text("syn", encoding="utf-8")

    # 만들어진 설정(선택 삭제)
    (pol / "doc_taxonomy.yaml").write_text("tax", encoding="utf-8")
    (pol / "doc_rule.yaml").write_text("rule", encoding="utf-8")

    # 데이터 백업(확장자가 .jsonl 이 아니라 대상이 아니다)
    (pol / "class_seed.jsonl.bak-20260828").write_text("{}", encoding="utf-8")
    return str(ui)


#------------------------------------------------------------------
# 앱이 만든 .json/.jsonl 만 지운다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_데이터_파일만_지운다(tmp_path):
    ui = make_ui(tmp_path)
    targets, kept = R.plan_reset(ui, drop_generated=False)
    names = sorted(t["name"] for t in targets)
    assert names == ["class_seed.jsonl", "class_seed_audit.jsonl",
                     "cso_override.jsonl", "cso_result.jsonl",
                     "cso_result.jsonl.meta.json", "grades.jsonl"]
    assert [k["name"] for k in kept] == ["doc_classification_export.json"]


#------------------------------------------------------------------
# 소스 코드·설정·자산은 절대 대상이 아니다 (가장 중요한 안전 계약)
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_소스와_자산은_건드리지_않는다(tmp_path):
    ui = make_ui(tmp_path)
    targets, _ = R.plan_reset(ui, drop_generated=True)
    names = {t["name"] for t in targets}
    for safe in ("app.py", "seedstore.py", "app.py.bak-dtev", "실행.bat",
                 "requirements.txt", "settings.yaml",
                 "doc_classification_export.json", "cso_rule.yaml",
                 "doc_rule_template.yaml", "doc_synonyms.core.yaml",
                 "class_seed.jsonl.bak-20260828"):
        assert safe not in names, f"{safe} 를 지우려 한다"


#------------------------------------------------------------------
# 만들어진 분류 체계·규칙은 선택으로 함께 지운다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_만들어진_설정은_선택으로_지운다(tmp_path):
    ui = make_ui(tmp_path)
    off = {t["name"] for t in R.plan_reset(ui, drop_generated=False)[0]}
    on = {t["name"] for t in R.plan_reset(ui, drop_generated=True)[0]}
    assert "doc_taxonomy.yaml" not in off and "doc_rule.yaml" not in off
    assert on - off == {"doc_taxonomy.yaml", "doc_rule.yaml"}


#------------------------------------------------------------------
# 하위 폴더(synonyms)로는 내려가지 않는다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_synonyms_폴더는_들어가지_않는다(tmp_path):
    ui = make_ui(tmp_path)
    syn = os.path.join(ui, "policy", "synonyms")
    # 하위 폴더에 .json 을 넣어 둬도 대상에 들어오면 안 된다.
    with open(os.path.join(syn, "x.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    targets, _ = R.plan_reset(ui, drop_generated=True)
    # 경로 전체로 비교하면 임시폴더 이름에 'synonyms' 가 들어가 오판한다 —
    # 그 파일이 들어 있는 폴더 이름만 본다.
    assert all(os.path.basename(t["dir"]) != "synonyms" for t in targets)
    assert "x.json" not in {t["name"] for t in targets}


#------------------------------------------------------------------
# 보여 준 목록만 지운다(지우면서 새로 찾지 않는다)
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_계획한_것만_지운다(tmp_path):
    ui = make_ui(tmp_path)
    targets, _ = R.plan_reset(ui, drop_generated=True)
    # 계획을 세운 뒤 새 데이터가 생겨도, 이번 삭제에는 포함되지 않아야 한다.
    with open(os.path.join(ui, "새로생긴.jsonl"), "w", encoding="utf-8") as f:
        f.write("{}")
    deleted, failed = R.run_reset(targets)
    assert failed == []
    assert len(deleted) == len(targets)
    assert os.path.isfile(os.path.join(ui, "새로생긴.jsonl"))
    assert os.path.isfile(os.path.join(ui, "app.py"))
    assert os.path.isfile(os.path.join(ui, "policy", "doc_classification_export.json"))
    assert not os.path.isfile(os.path.join(ui, "cso_result.jsonl"))


#------------------------------------------------------------------
# 이미 초기 상태면 지울 것이 없다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_두_번_지우면_두_번째는_없다(tmp_path):
    ui = make_ui(tmp_path)
    R.run_reset(R.plan_reset(ui, drop_generated=True)[0])
    again, kept = R.plan_reset(ui, drop_generated=True)
    assert again == []
    assert [k["name"] for k in kept] == ["doc_classification_export.json"]


#------------------------------------------------------------------
# 없는 폴더를 줘도 조용히 빈 결과를 준다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_없는_폴더는_조용히_넘어간다(tmp_path):
    targets, kept = R.plan_reset(str(tmp_path / "없는폴더"))
    assert targets == [] and kept == []


#------------------------------------------------------------------
# 용량 표기
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_용량_표기():
    assert R.human_size(0) == "0B"
    assert R.human_size(512) == "512B"
    assert R.human_size(2048) == "2.0KB"
    assert R.human_size(1024 * 1024 * 3) == "3.0MB"


#------------------------------------------------------------------
# 기준 문서는 선택으로 남길 수 있다
#=> 분류 결과는 다시 돌리면 되살아나지만 기준 문서는 사람이 하나씩 확정해 쌓은
#   것이라 지우면 끝이다. "결과만 갈아엎고 기준 문서는 살려 두고 다시 분류"가
#   실무에서 필요해 이 파일만 따로 뺄 수 있게 했다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_기준문서는_선택으로_남긴다(tmp_path):
    ui = make_ui(tmp_path)
    on = {t["name"] for t in R.plan_reset(ui, drop_seed=True)[0]}
    off = {t["name"] for t in R.plan_reset(ui, drop_seed=False)[0]}

    assert "class_seed.jsonl" in on          # 기본은 지운다
    assert "class_seed.jsonl" not in off     # 끄면 남는다
    # 그 파일 하나만 빠져야 한다 — 다른 것까지 덩달아 남으면 안 된다.
    assert on - off == {"class_seed.jsonl"}


#------------------------------------------------------------------
# 남긴 기준 문서는 '남는 것' 목록에 뜬다
#=> 지울 목록에서 조용히 빠지기만 하면, 사람이 남았는지 지워졌는지 알 수 없다.
#   이 대화상자의 존재 이유가 "무엇이 사라지는지 먼저 보여준다" 이므로,
#   남는 것도 같은 무게로 보여야 한다.
#------------------------------------------------------------------
def test_남긴_기준문서는_남는것_목록에_나온다(tmp_path):
    ui = make_ui(tmp_path)
    _, kept_on = R.plan_reset(ui, drop_seed=True)
    _, kept_off = R.plan_reset(ui, drop_seed=False)

    assert "class_seed.jsonl" not in {k["name"] for k in kept_on}
    row = [k for k in kept_off if k["name"] == "class_seed.jsonl"]
    assert len(row) == 1 and row[0]["why"]        # 왜 남는지도 적혀 있어야 한다


#------------------------------------------------------------------
# 기본값은 '지움' 이다
#=> 이 기능의 이름이 '처음 상태로 되돌리기'다. 인자를 안 주면 종전과 똑같이
#   전부 지워야 한다 — 기본값이 바뀌면 기존 호출부의 동작이 조용히 달라진다.
#------------------------------------------------------------------
def test_인자를_안_주면_종전대로_전부_지운다(tmp_path):
    ui = make_ui(tmp_path)
    assert "class_seed.jsonl" in {t["name"] for t in R.plan_reset(ui)[0]}


#------------------------------------------------------------------
# 남기기로 했으면 실제로 파일이 살아 있다
#=> 계획(plan)만 맞고 실행(run)에서 지워지면 최악이다. 끝까지 확인한다.
#   변경 이력(class_seed_audit.jsonl)은 이 선택과 무관하게 지운다 —
#   사람이 고른 것은 '기준 문서'이지 그 로그가 아니다.
#------------------------------------------------------------------
def test_남기기로_하면_실제로_파일이_살아있다(tmp_path):
    ui = make_ui(tmp_path)
    seed = os.path.join(ui, "policy", "class_seed.jsonl")
    audit = os.path.join(ui, "class_seed_audit.jsonl")
    assert os.path.isfile(seed) and os.path.isfile(audit)

    targets, _ = R.plan_reset(ui, drop_seed=False)
    deleted, failed = R.run_reset(targets)

    assert not failed
    assert os.path.isfile(seed), "남기기로 했는데 지워졌다"
    assert not os.path.isfile(audit), "변경 이력은 지워져야 한다"
    assert not os.path.isfile(os.path.join(ui, "cso_result.jsonl"))
