#------------------------------------------------------------------
# 업무분류 규칙 자동 생성(docbuild · --build-doc-rule) 시험
#=> 가짜 예시 체계(resources/policy/samples/…itsec.json)와 저장소의 사전·본보기로
#   임시 정책 폴더를 만들어, 규칙 생성·local 조정·새 형식 로딩·점검 경고를 본다.
#   고객 정책 파일은 쓰지 않는다.
#------------------------------------------------------------------

import os
import shutil

import pytest
import yaml

from csoclassify.classify import axes as AX
from csoclassify.classify import doc_rules as DR
from csoclassify.classify import docbuild as DB

_HERE = os.path.dirname(os.path.abspath(__file__))
_POLICY = os.path.join(_HERE, "..", "resources", "policy")
_SAMPLE = os.path.join(_POLICY, "samples", "doc_classification_export.itsec.json")


#------------------------------------------------------------------
# 임시 정책 폴더 만들기
#=> 제품 사전(core·업종)·본보기·예시 체계 JSON 을 복사하고, 조정 파일은 인자로 받는다.
#
# -in: tmp_path = pytest 임시 폴더
# -in: local    = doc_rule.local.yaml 에 쓸 dict(None 이면 파일 없음)
#
# -out: (policy_dir, export_path)
# -out: error = 없음
#------------------------------------------------------------------
def _policy(tmp_path, local=None):
    pol = tmp_path / "policy"
    shutil.copytree(os.path.join(_POLICY, "synonyms"), pol / "synonyms")
    # 개발 PC 에 회사 겹이 남아 있어도 시험은 제품 기본값으로만 돈다.
    lp = pol / "synonyms" / "doc_synonyms.local.yaml"
    if lp.exists():
        lp.unlink()
    shutil.copy(os.path.join(_POLICY, "doc_rule_template.yaml"), pol / "doc_rule_template.yaml")
    exp = pol / "doc_classification_export.json"
    shutil.copy(_SAMPLE, exp)
    if local is not None:
        (pol / DB.RULE_LOCAL).write_text(yaml.safe_dump(local, allow_unicode=True),
                                         encoding="utf-8")
    return str(pol), str(exp)


#------------------------------------------------------------------
# 규칙 dict 에서 한 분류의 규칙 찾기
#
# -in: doc  = build_doc_rule 결과
# -in: node = dc_id
#
# -out: dict|None = 그 분류의 규칙
# -out: error = 없음
#------------------------------------------------------------------
def _rule(doc, node):
    return next((r for r in doc["doctype_rules"] if r["node"] == node), None)


#------------------------------------------------------------------
# 같은 입력이면 글자까지 같다 · 분류체계 칸이 없다 · 경로가 규칙 줄에 있다
#=> 설계서 4장 목표 1과 9장 모양. 꺼 둔 분류(구버전매뉴얼)와 서랍(대분류)에는
#   규칙이 없고, 자식이 있는 분류(매뉴얼)에는 규칙이 있다(지금 불러오기와 같은 기준).
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_build_is_deterministic_and_has_no_taxonomy_block(tmp_path):
    pol, exp = _policy(tmp_path)
    a, _ = DB.build_doc_rule(exp, pol)
    b, _ = DB.build_doc_rule(exp, pol)
    assert DB.dump_doc_rule(a) == DB.dump_doc_rule(b)
    assert a["version"].startswith("doctype-gen-")
    assert "taxonomy" not in a
    nodes = {r["node"] for r in a["doctype_rules"]}
    assert "DC_002_005" not in nodes          # 꺼 둔 분류
    assert "DC_001" not in nodes              # 서랍(대분류)
    assert "DC_002_001" in nodes              # 자식이 있어도 뿌리가 아니면 규칙이 있다
    r = _rule(a, "DC_002_001_002")
    assert r["path"] == "제품/기술 > 매뉴얼 > 관리자매뉴얼"
    assert r["path_ids"] == ["DC_002", "DC_002_001", "DC_002_001_002"]


#------------------------------------------------------------------
# local 조정 — clear → remove → add · weight · 숫자 칸 · enabled
#=> P2 처럼 칸을 비우고, 말을 빼고 더하고, 분류 하나를 끈다. 적용 표지(local)도 남는다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_local_adjustments_are_applied(tmp_path):
    pol, exp = _policy(tmp_path)
    base, _ = DB.build_doc_rule(exp, pol)
    first = _rule(base, "DC_001_001")["title_terms"][1]
    local = {"rules": {
        "DC_001_001": {"clear": ["terms"], "remove": {"title_terms": [first]},
                       "add": {"title_terms": ["입찰제안"], "terms": ["RFP 응답"]},
                       "weight": "high", "head_chars": 200},
        "DC_004_005": {"enabled": False},
    }}
    pol, exp = _policy(tmp_path / "b", local)
    doc, warns = DB.build_doc_rule(exp, pol)
    r = _rule(doc, "DC_001_001")
    assert r["terms"] == ["RFP 응답"]                     # 비운 뒤 더한 말만
    assert first not in r["title_terms"] and r["title_terms"][-1] == "입찰제안"
    assert r["weight"] == "high" and r["head_chars"] == 200
    assert r["local"] == ["clear:terms", "remove:title_terms", "add:title_terms",
                          "add:terms", "weight", "head_chars"]
    assert _rule(doc, "DC_004_005") is None
    assert any("DC_004_005" in w and "enabled=false" in w for w in warns)


#------------------------------------------------------------------
# 조정이 가리키는 분류가 없거나 이름이 바뀌었으면 알린다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_local_warns_unknown_node_and_renamed_title(tmp_path):
    local = {"rules": {"DC_999": {"weight": "low"},
                       "DC_002_001": {"title_at_decision": "사용설명서", "weight": "low"},
                       "DC_001_002": {"remove": {"terms": ["없는말"]}}}}
    pol, exp = _policy(tmp_path, local)
    _, warns = DB.build_doc_rule(exp, pol)
    assert any("DC_999" in w for w in warns)
    assert any("DC_002_001" in w and "사용설명서" in w for w in warns)
    assert any("없는말" in w for w in warns)


#------------------------------------------------------------------
# 조정 파일 검증 — 틀린 칸은 규칙을 만들지 않는다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_local_validation_rejects_bad_fields(tmp_path):
    bad = {"rules": {"DC_001_001": {"clear": ["body"], "weight": "big", "min_count": 0,
                                    "enabld": False}},
           "setting": {}}
    pol, exp = _policy(tmp_path, bad)
    with pytest.raises(DB.RuleLocalValidationError) as ei:
        DB.build_doc_rule(exp, pol)
    codes = sorted(v["code"] for v in ei.value.violations)
    assert codes == ["L0", "L3", "L4", "L5", "L5"]


#------------------------------------------------------------------
# 새 형식 로딩 — doc_taxonomy.yaml 없이 옛 길과 같은 규칙·핵어·경로
#=> 같은 입력으로 옛 길(체계 스냅샷 + 규칙)과 새 길(생성 규칙 하나)을 견준다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_generated_file_loads_like_old_path(tmp_path):
    pol, exp = _policy(tmp_path)
    doc, _ = DB.build_doc_rule(exp, pol)
    path = os.path.join(pol, "doc_rule.yaml")
    DB.write_doc_rule(path, doc)
    new = DR.load_doc_rules(path)
    assert new.taxonomy is not None and not new.warnings

    # 옛 길 — 같은 규칙 줄에서 생성 전용 칸을 빼고 스냅샷과 함께 읽는다.
    snap, _ = AX.convert_mpower_json(__import__("json").load(open(exp, encoding="utf-8")))
    tax = AX.taxonomy_from_snapshot(snap)
    old_doc = {k: v for k, v in doc.items() if k not in ("generated", "noise_tails")}
    old_doc["doctype_rules"] = [{k: v for k, v in r.items()
                                 if k not in ("title", "path", "path_ids", "heads")}
                                for r in doc["doctype_rules"]]
    op = os.path.join(pol, "old.yaml")
    with open(op, "w", encoding="utf-8") as f:
        yaml.safe_dump(old_doc, f, allow_unicode=True)
    old = DR.load_doc_rules(op, taxonomy=tax)

    assert new.rules == old.rules
    assert new.head_lexicon == old.head_lexicon
    assert new.head_noise == old.head_noise
    for r in new.rules:
        assert new.taxonomy.path(r.node) == tax.path(r.node)
        assert new.taxonomy.path_ids(r.node) == tax.path_ids(r.node)


#------------------------------------------------------------------
# 실행 때 점검 — 손으로 고친 흔적 · 입력이 바뀜
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_check_generated_warns_hand_edit_and_stale_input(tmp_path):
    pol, exp = _policy(tmp_path)
    doc, _ = DB.build_doc_rule(exp, pol)
    path = os.path.join(pol, "doc_rule.yaml")
    DB.write_doc_rule(path, doc)
    assert DR.load_doc_rules(path).warnings == ()

    # 사전을 고치고 다시 만들지 않았다.
    core = os.path.join(pol, "synonyms", "doc_synonyms.core.yaml")
    with open(core, "a", encoding="utf-8") as f:
        f.write("\n# 바뀜\n")
    assert any("입력이 바뀌었습니다" in w for w in DR.load_doc_rules(path).warnings)

    # 규칙 파일을 손으로 고쳤다.
    text = open(path, encoding="utf-8").read().replace('weight: "medium"', 'weight: "high"', 1)
    open(path, "w", encoding="utf-8").write(text)
    assert any("손으로 고쳐졌습니다" in w for w in DR.load_doc_rules(path).warnings)


#------------------------------------------------------------------
# 경로 칸이 어긋난 생성 규칙은 로드에서 막는다(G1)
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_generated_path_mismatch_is_g1(tmp_path):
    pol, exp = _policy(tmp_path)
    doc, _ = DB.build_doc_rule(exp, pol)
    doc["doctype_rules"][0]["path_ids"] = ["DC_X"]
    path = os.path.join(pol, "doc_rule.yaml")
    DB.write_doc_rule(path, doc)
    with pytest.raises(DR.DocRuleValidationError) as ei:
        DR.load_doc_rules(path)
    assert ei.value.violations[0]["code"] == "G1"


#------------------------------------------------------------------
# 업종은 조정 파일이 정한다
#=> 본보기에는 업종 일곱 개가 적혀 있다. 조정 파일에 하나만 적으면 그 사전만 얹는다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_industry_comes_from_local(tmp_path):
    pol, exp = _policy(tmp_path, {"industry": "finance"})
    doc, _ = DB.build_doc_rule(exp, pol)
    files = [e["file"] for e in doc["generated"]["inputs"]]
    assert doc["generated"]["industry"] == ["finance"]
    assert "synonyms/doc_synonyms.finance.yaml" in files
    assert "synonyms/doc_synonyms.medical.yaml" not in files


#------------------------------------------------------------------
# --check-rules 는 자동 생성 규칙이면 doc_taxonomy.yaml 없이 통과한다
#=> 실제 분류(_load_doctype_axis)와 같은 갈래로 가야 한다 — 스냅샷이 없다고
#   "doctype 축 미사용"으로 끝내면, 검사는 통과했는데 실행은 다르게 돈다.
#
# -in: tmp_path = pytest 임시 폴더
# -in: capsys   = 표준출력 잡기
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_check_rules_generated_needs_no_snapshot(tmp_path, capsys):
    from types import SimpleNamespace
    from csoclassify import cli, config
    pol, exp = _policy(tmp_path)
    rules = os.path.join(pol, "doc_rule.yaml")
    doc, _ = DB.build_doc_rule(exp, pol)
    DB.write_doc_rule(rules, doc)
    args = SimpleNamespace(rules=None, taxonomy=os.path.join(pol, "없음.yaml"),
                           doc_rules=rules, seeds=None)
    assert cli.run_check_rules(args) == config.EXIT_OK
    out = capsys.readouterr().out
    assert "자동 생성 — 분류체계 내장" in out
    assert "스냅샷 없음" not in out
