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
    (pol / "cso_rules.yaml").write_text("rules", encoding="utf-8")
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
                 "doc_classification_export.json", "cso_rules.yaml",
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
