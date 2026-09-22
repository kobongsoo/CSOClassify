#------------------------------------------------------------------
# 화면의 '자동 생성 방식' 로직(ui/docrulegen.py) 시험
#=> 표에서 고친 값이 조정 파일로 제대로 되돌아가는지, 단어 제안·핵어 끄기·옮기기가
#   조정으로 남는지 본다. 임시 정책 폴더(예시 체계 + 저장소 사전)에서만 돈다.
#------------------------------------------------------------------

import copy
import os
import shutil
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "ui"))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

import docrulegen as G  # noqa: E402
from csoclassify.classify import docbuild as DB  # noqa: E402

_POLICY = os.path.join(_HERE, "..", "resources", "policy")
_SAMPLE = os.path.join(_POLICY, "samples", "doc_classification_export.itsec.json")


#------------------------------------------------------------------
# 임시 정책 폴더 — 사전·본보기·예시 체계
#
# -in: tmp_path = pytest 임시 폴더
#
# -out: (rules_path, export_path)
# -out: error = 없음
#------------------------------------------------------------------
def _policy(tmp_path):
    pol = tmp_path / "policy"
    shutil.copytree(os.path.join(_POLICY, "synonyms"), pol / "synonyms")
    lp = pol / "synonyms" / "doc_synonyms.local.yaml"
    if lp.exists():
        lp.unlink()
    shutil.copy(os.path.join(_POLICY, "doc_rule_template.yaml"), pol / "doc_rule_template.yaml")
    shutil.copy(_SAMPLE, pol / "doc_classification_export.json")
    return str(pol / "doc_rule.yaml"), str(pol / "doc_classification_export.json")


#------------------------------------------------------------------
# 표 행 하나 찾기
#
# -in: rows = to_rows 결과
# -in: node = dc_id
#
# -out: dict = 그 분류의 행
# -out: error = 없으면 StopIteration
#------------------------------------------------------------------
def _row(rows, node):
    return next(r for r in rows if r["_node"] == node)


#------------------------------------------------------------------
# 새 방식 판정 — 조정 파일이 있거나 규칙 파일이 자동 생성본이면
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_generated_mode_detection(tmp_path):
    rules, exp = _policy(tmp_path)
    assert not G.is_generated_mode(rules)
    doc, _ = DB.build_doc_rule(exp, os.path.dirname(rules))
    DB.write_doc_rule(rules, doc)
    assert G.is_generated_mode(rules)
    os.remove(rules)
    G.save_local(rules, {"version": "t"})
    assert G.is_generated_mode(rules)


#------------------------------------------------------------------
# 표에서 칸을 비우고·빼고·더하면 조정으로 저장되고, 되돌리면 조정이 사라진다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_table_edits_become_adjustments_and_revert(tmp_path):
    rules, exp = _policy(tmp_path)
    local = {}
    view, base, _, _ = G.views(rules, exp, local)
    rows = G.to_rows(view, local)
    r = _row(rows, "DC_001_001")
    words = G.split_terms(r["제목에"])
    r["이 말이 나오면"] = ""                                  # 비움
    r["제목에"] = ", ".join(words[1:] + ["입찰제안"])          # 뺌 + 더함
    r["이유"] = "시험"
    _row(rows, "DC_004_005")["사용"] = False                   # 끄기
    assert G.apply_rows(local, base, rows, today="2026-09-22") == 2

    adj = local["rules"]["DC_001_001"]
    assert adj["clear"] == ["terms"]
    assert adj["remove"] == {"title_terms": [words[0]]}
    assert adj["add"] == {"title_terms": ["입찰제안"]}
    assert adj["why"] == "시험" and adj["title_at_decision"] == "제안서"
    assert local["rules"]["DC_004_005"]["enabled"] is False
    assert local["rules"]["DC_004_005"]["why"] == "화면에서 고침 2026-09-22"

    # 저장한 조정으로 다시 만들면 표에서 고친 그대로 나온다.
    G.save_local(rules, local)
    view2, base2, real2, _ = G.views(rules, exp, G.load_local(rules))
    r2 = _row(G.to_rows(view2, local), "DC_001_001")
    assert r2["이 말이 나오면"] == "" and r2["제목에"].endswith("입찰제안")
    assert "DC_004_005" not in {x["node"] for x in real2["doctype_rules"]}

    # 자동값으로 되돌리면 조정이 사라진다(끄기도 다시 켠다).
    rows3 = G.to_rows(base2, {})
    for x in rows3:
        x["이유"] = ""
    G.apply_rows(local, base2, rows3)
    assert "rules" not in local


#------------------------------------------------------------------
# 단어 제안으로 더한 말은 add 조정으로 남는다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_suggested_terms_become_add(tmp_path):
    from csoclassify.classify import termsuggest
    rules, exp = _policy(tmp_path)
    local = {}
    view, base, _, _ = G.views(rules, exp, local)
    edited = copy.deepcopy(view)
    termsuggest.apply_terms(edited, "DC_003_004", [{"term": "장애보고", "fields": ["head_terms"]}])
    assert G.apply_rule_edit(local, base, view, edited, "DC_003_004", today="2026-09-22")
    assert local["rules"]["DC_003_004"]["add"] == {"head_terms": ["장애보고"]}


#------------------------------------------------------------------
# 핵어 끄기는 조정 파일의 head 칸으로
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_head_off_round_trip():
    import docruleedit
    local = {}
    doc = G.head_as_doc(local)
    docruleedit.apply_head_off(doc, ["회의록"], ["회의록", "제안서"])
    docruleedit.set_node_off(doc, "DC_001_001", "제안서")
    G.head_from_doc(local, doc)
    assert local["head"] == {"title_exclude": ["회의록"],
                             "node_off": [{"node": "DC_001_001", "title_at_decision": "제안서"}]}
    doc = G.head_as_doc(local)
    docruleedit.apply_head_off(doc, [], ["회의록", "제안서"])
    docruleedit.set_node_off(doc, "DC_001_001", None)
    G.head_from_doc(local, doc)
    assert "head" not in local


#------------------------------------------------------------------
# 옛 방식 규칙 → 조정 초안 — 사람이 비운 칸(P2 꼴)이 clear 로 옮겨지고 재현된다
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_migrate_old_rules(tmp_path):
    rules, exp = _policy(tmp_path)
    doc, _ = DB.build_doc_rule(exp, os.path.dirname(rules))
    old = {"industry": doc["generated"]["industry"], "conflict": doc["conflict"],
           "defaults": doc["defaults"], "signals": doc["signals"], "embed": doc["embed"],
           "doctype_rules": [{k: v for k, v in r.items()
                              if k not in ("title", "path", "path_ids", "heads", "local")}
                             for r in doc["doctype_rules"]]}
    target = next(r for r in old["doctype_rules"] if r["node"] == "DC_004_002")
    target["head_terms"], target["terms"] = [], []
    with open(rules, "w", encoding="utf-8") as f:
        yaml.safe_dump(old, f, allow_unicode=True)
    local, warns, gaps = G.migrate(rules, exp, today="2026-09-22")
    assert gaps == [] and warns == []
    assert local["rules"] == {"DC_004_002": {
        "title_at_decision": "구입의뢰서", "clear": ["head_terms", "terms"],
        "why": "옛 규칙에서 옮겨 옴 2026-09-22"}}


#------------------------------------------------------------------
# [규칙 다시 만들기] 미리 보기 — 새 분류·말 바뀜·빠지는 분류
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_preview_changes():
    cur = {"doctype_rules": [{"node": "A", "path": "A", "title_terms": ["가"]},
                             {"node": "B", "path": "B"}]}
    new = {"doctype_rules": [{"node": "A", "path": "A", "title_terms": ["가", "나"]},
                             {"node": "C", "path": "C"}]}
    got = {c["분류"]: c for c in G.preview_changes(cur, new)}
    assert got["A"]["더해질 말"] == "나(제목에)"
    assert got["C"]["무엇"] == "새 분류"
    assert got["B"]["무엇"] == "빠지는 분류"
    assert G.preview_changes(new, new) == []
