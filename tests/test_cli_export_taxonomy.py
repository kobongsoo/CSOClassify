#------------------------------------------------------------------
# CLI 배선 — --export-taxonomy 모드 단위 테스트
#=> MpowerClassify.exe --export-taxonomy 가 scripts/export_taxonomy.py 와 같은
#   axes.export_from_mpower_json()/doc_rules.write_scaffold() 를 통해 동작하는지,
#   그리고 실패 상황(원본 없음·구조 오류·검증 실패)이 올바른 종료코드로
#   환원되는지 검증한다. 문서 추출·임베딩은 거치지 않는다(입력이 JSON 이라
#   애초에 필요 없다).
#------------------------------------------------------------------

import json
from types import SimpleNamespace

import yaml

from csoclassify import cli
from csoclassify import config


#------------------------------------------------------------------
# --export-taxonomy 용 최소 args 만들기
#=> run_export_taxonomy 가 읽는 필드만 채운다(SimpleNamespace 로 충분 — 실제
#   argparse.Namespace 와 속성 접근 방식이 같다).
#
# -in: **overrides = export_input/taxonomy/doc_rules/scaffold_doc_rule 등 덮어쓸 값
#
# -out: SimpleNamespace
# -out: error = 없음
#------------------------------------------------------------------
def mk_args(**overrides):
    base = {"export_input": None, "taxonomy": None, "doc_rules": None,
            "scaffold_doc_rule": False}
    base.update(overrides)
    return SimpleNamespace(**base)


#------------------------------------------------------------------
# 최소 MPOWER JSON 원본 파일 만들기
#
# -in: tmp_path = pytest 임시폴더
# -in: node_count = 원본에 적을 node_count(기본 노드 실제 수와 동일)
#
# -out: str = 만든 JSON 파일 경로
# -out: error = 없음
#------------------------------------------------------------------
def mk_input_json(tmp_path, node_count=None):
    nodes = [
        {"dc_id": "DC_001", "parent_dc_id": None, "order_num": 1,
         "title": "경영/관리", "status": 1},
        {"dc_id": "DC_001_001", "parent_dc_id": "DC_001", "order_num": 1,
         "title": "사업계획서", "status": 1},
    ]
    data = {"source": "Mpower10U.DOC_CLASSIFICATION",
            "node_count": node_count if node_count is not None else len(nodes),
            "nodes": nodes}
    p = tmp_path / "doc_classification_export.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ── 정상 경로 ────────────────────────────────────────────────────

#------------------------------------------------------------------
# 정상 JSON → doc_taxonomy.yaml 생성, exit 0
#
# -in: tmp_path, capsys
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_정상_변환은_exit0이고_파일이_생긴다(tmp_path, capsys):
    input_path = mk_input_json(tmp_path)
    out_path = tmp_path / "doc_taxonomy.yaml"
    args = mk_args(export_input=input_path, taxonomy=str(out_path))

    code = cli.run_export_taxonomy(args)

    assert code == config.EXIT_OK
    assert out_path.is_file()
    saved = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    assert saved["taxonomy"]["node_count"] == 2
    assert "생성 완료" in capsys.readouterr().out


#------------------------------------------------------------------
# --scaffold-doc-rule 을 함께 주면 doc_rule.yaml 골격도 생긴다
#
# -in: tmp_path, capsys
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_scaffold_doc_rule_옵션으로_골격도_생성(tmp_path, capsys):
    input_path = mk_input_json(tmp_path)
    out_path = tmp_path / "doc_taxonomy.yaml"
    dr_path = tmp_path / "doc_rule.yaml"
    args = mk_args(export_input=input_path, taxonomy=str(out_path),
                   doc_rules=str(dr_path), scaffold_doc_rule=True)

    code = cli.run_export_taxonomy(args)

    assert code == config.EXIT_OK
    assert dr_path.is_file()
    scaffold = yaml.safe_load(dr_path.read_text(encoding="utf-8"))
    assert len(scaffold["doctype_rules"]) == 2


#------------------------------------------------------------------
# 골격 파일이 이미 있으면 덮어쓰지 않고 건너뛴다(경고만)
#
# -in: tmp_path, capsys
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_이미있는_골격은_덮어쓰지_않는다(tmp_path, capsys):
    input_path = mk_input_json(tmp_path)
    out_path = tmp_path / "doc_taxonomy.yaml"
    dr_path = tmp_path / "doc_rule.yaml"
    dr_path.write_text("사람이 이미 채운 내용", encoding="utf-8")
    args = mk_args(export_input=input_path, taxonomy=str(out_path),
                   doc_rules=str(dr_path), scaffold_doc_rule=True)

    code = cli.run_export_taxonomy(args)

    assert code == config.EXIT_OK
    assert dr_path.read_text(encoding="utf-8") == "사람이 이미 채운 내용"
    assert "건너뜁니다" in capsys.readouterr().err


# ── 실패 경로 ────────────────────────────────────────────────────

#------------------------------------------------------------------
# 원본 JSON 이 없으면 EXIT_ARG_ERROR(3)
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_원본없으면_EXIT_ARG_ERROR(tmp_path, capsys):
    args = mk_args(export_input=str(tmp_path / "없음.json"), taxonomy=str(tmp_path / "out.yaml"))
    code = cli.run_export_taxonomy(args)
    assert code == config.EXIT_ARG_ERROR
    assert not (tmp_path / "out.yaml").exists()


#------------------------------------------------------------------
# 원본 JSON 구조가 틀리면(nodes 없음) EXIT_RULES_INVALID(4)
#=> [2026-09-01 계약 변경] '파일 없음(3)'과 '내용이 틀림(4)'을 갈랐다. 부르는 쪽이
#   "경로를 다시 묻는다"와 "원본 데이터를 고친다"를 구분할 수 있어야 한다.
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_구조오류_원본은_EXIT_RULES_INVALID(tmp_path):
    p = tmp_path / "doc_classification_export.json"
    p.write_text(json.dumps({"source": "x"}), encoding="utf-8")
    args = mk_args(export_input=str(p), taxonomy=str(tmp_path / "out.yaml"))
    code = cli.run_export_taxonomy(args)
    assert code == config.EXIT_RULES_INVALID


#------------------------------------------------------------------
# 원본 JSON 문법 자체가 깨졌으면(JSONDecodeError) EXIT_RULES_INVALID(4)
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_JSON_문법오류는_EXIT_RULES_INVALID(tmp_path):
    p = tmp_path / "doc_classification_export.json"
    p.write_text("{이건 json이 아님", encoding="utf-8")
    args = mk_args(export_input=str(p), taxonomy=str(tmp_path / "out.yaml"))
    code = cli.run_export_taxonomy(args)
    assert code == config.EXIT_RULES_INVALID


#------------------------------------------------------------------
# 변환 결과 트리가 깨지면(순환) EXIT_RULES_INVALID(4)
#=> 원본 JSON 자체는 문법상 유효하지만, parent_dc_id 가 자기 자신을 가리켜
#   변환 후 순환(T7)이 생기는 경우를 재현한다.
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_변환결과_순환은_EXIT_RULES_INVALID(tmp_path):
    p = tmp_path / "doc_classification_export.json"
    p.write_text(json.dumps({
        "source": "x", "node_count": 1,
        "nodes": [{"dc_id": "A", "parent_dc_id": "A", "order_num": 1,
                  "title": "A", "status": 1}],
    }), encoding="utf-8")
    args = mk_args(export_input=str(p), taxonomy=str(tmp_path / "out.yaml"))
    code = cli.run_export_taxonomy(args)
    assert code == config.EXIT_RULES_INVALID
