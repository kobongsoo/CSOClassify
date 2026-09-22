#------------------------------------------------------------------
# CLI 배선 — doctype 축 관련 헬퍼 단위 테스트
#=> cli.py 의 _parse_conflict_override(6-5·T2·T10·T11)·_doctype_breakdown(요약 집계)·
#   _load_doctype_axis(4-6 — 자동 생성 규칙 하나로 켜기)·없어진 옵션 안내가 설계대로
#   동작하는지 검증한다. 실제 문서 추출·임베딩은 거치지
#   않는다(cli.py 의 기존 테스트 관례 — 무거운 파이프라인은 여기서 다루지 않음).
#------------------------------------------------------------------

import datetime
from types import SimpleNamespace

import pytest
import yaml

from csoclassify import cli
from csoclassify import config
from csoclassify import errcodes
from csoclassify.classify import axes as A
from csoclassify.classify import doc_rules as D


#------------------------------------------------------------------
# 로거 대역
#=> _load_doctype_axis 가 요구하는 최소 인터페이스(error/warning)만 흉내낸다.
#
# -in: 없음
# -out: SimpleNamespace(error=..., warning=...)
# -out: error = 없음
#------------------------------------------------------------------
def _fake_log():
    return SimpleNamespace(error=lambda *a, **k: None, warning=lambda *a, **k: None)


# ── _parse_conflict_override (6-5·T2·T10·T11) ────────────────────────

#------------------------------------------------------------------
# "doctype=top_n:3" 은 (axis, ConflictSpec(top_n,n=3)) 으로 파싱된다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_conflict_override_top_n_파싱():
    axis, spec = cli._parse_conflict_override("doctype=top_n:3")
    assert axis == "doctype"
    assert spec == D.ConflictSpec(strategy="top_n", n=3)


#------------------------------------------------------------------
# "doctype=all" 은 n 없이 파싱된다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_conflict_override_all_파싱():
    axis, spec = cli._parse_conflict_override("doctype=all")
    assert axis == "doctype"
    assert spec == D.ConflictSpec(strategy="all")


#------------------------------------------------------------------
# '=' 없는 형식은 ValueError
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_conflict_override_등호없으면_ValueError():
    with pytest.raises(ValueError):
        cli._parse_conflict_override("doctype")


#------------------------------------------------------------------
# max 는 doctype 축에 쓸 수 없다 — T2 로 ValueError
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_conflict_override_max는_ValueError():
    with pytest.raises(ValueError):
        cli._parse_conflict_override("doctype=max")


#------------------------------------------------------------------
# top_n 인데 n 이 정수가 아니면 ValueError
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_conflict_override_n_정수아니면_ValueError():
    with pytest.raises(ValueError):
        cli._parse_conflict_override("doctype=top_n:abc")


# ── _doctype_breakdown ──────────────────────────────────────────────

#------------------------------------------------------------------
# path 문자열에서 뿌리·노드 쌍을 뽑는다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_doctype_breakdown_뿌리_노드_추출():
    label = {"values": [
        {"dc_id": "A", "path": "기술/개발 > 설계문서 > 요구사항정의서"},
        {"dc_id": "B", "path": "법무/규정 > 계약서"},
    ]}
    assert cli._doctype_breakdown(label) == [
        ("기술/개발", "요구사항정의서"), ("법무/규정", "계약서"),
    ]


#------------------------------------------------------------------
# values 가 비어 있으면 빈 목록
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_doctype_breakdown_빈값():
    assert cli._doctype_breakdown({"values": []}) == []


# ── _load_doctype_axis (4-6 · 2026-09-22 자동 생성 규칙 하나로) ─────────

#------------------------------------------------------------------
# 시험용 자동 생성 규칙 만들기
#=> 저장소 사전·본보기·예시 체계 JSON 으로 임시 폴더에 doc_rule.yaml 을 만든다.
#
# -in: tmp_path = pytest 임시 폴더
#
# -out: str = 만든 doc_rule.yaml 경로
# -out: error = 없음
#------------------------------------------------------------------
def _generated_rules(tmp_path):
    import os
    import shutil
    from csoclassify.classify import docbuild as DB
    here = os.path.dirname(os.path.abspath(__file__))
    pol_src = os.path.join(here, "..", "resources", "policy")
    pol = tmp_path / "policy"
    shutil.copytree(os.path.join(pol_src, "synonyms"), pol / "synonyms")
    lp = pol / "synonyms" / "doc_synonyms.local.yaml"
    if lp.exists():
        lp.unlink()
    shutil.copy(os.path.join(pol_src, "doc_rule_template.yaml"), pol / "doc_rule_template.yaml")
    exp = pol / "doc_classification_export.json"
    shutil.copy(os.path.join(pol_src, "samples", "doc_classification_export.itsec.json"), exp)
    rules = str(pol / "doc_rule.yaml")
    doc, _ = DB.build_doc_rule(str(exp), str(pol))
    DB.write_doc_rule(rules, doc)
    return rules


#------------------------------------------------------------------
# --axis security 면 파일을 아예 건드리지 않는다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_axis_security면_시도조차_안함(tmp_path, capsys):
    args = SimpleNamespace(axis="security", doc_rules=None)
    taxonomy, drs, code = cli._load_doctype_axis(args, _fake_log())
    assert (taxonomy, drs, code) == (None, None, None)
    assert capsys.readouterr().err == ""   # 경고조차 안 남긴다


#------------------------------------------------------------------
# 기본(축 미지정)인데 규칙 파일이 없으면 경고 후 정상 통과(축만 끈다)
#=> 분류체계가 규칙 안에 있으므로 규칙이 없으면 업무분류를 돌릴 수 없다.
#   예전의 'seed 전파 전용'은 없다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_기본축_규칙없으면_경고후_통과(tmp_path, capsys):
    args = SimpleNamespace(axis=None, doc_rules=str(tmp_path / "doc_rule.yaml"))
    taxonomy, drs, code = cli._load_doctype_axis(args, _fake_log())
    assert (taxonomy, drs, code) == (None, None, None)
    err = capsys.readouterr().err
    assert "건너뜁니다" in err and "--build-doc-rule" in err


#------------------------------------------------------------------
# --axis doctype 인데 규칙 파일이 없으면 종료(3 = 인자 오류, code 2003)
#=> [2026-09-01 계약] '파일이 없다'는 부른 쪽이 경로를 고치면 되는 문제라 3 이다.
#   코드 이름(taxonomy_missing)은 그대로 — 분류체계가 없다는 뜻은 같다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_axis_doctype인데_규칙없으면_종료3(tmp_path):
    args = SimpleNamespace(axis="doctype", doc_rules=str(tmp_path / "doc_rule.yaml"))
    taxonomy, drs, code = cli._load_doctype_axis(args, _fake_log())
    assert (taxonomy, drs) == (None, None)
    assert code == config.EXIT_ARG_ERROR
    assert errcodes.code_of("taxonomy_missing") == 2003


#------------------------------------------------------------------
# 옛 모양 규칙(자동 생성본이 아님)은 축과 무관하게 종료(4)하고 새로 만드는 법을 알린다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
@pytest.mark.parametrize("axis", [None, "doctype"])
def test_옛_모양_규칙은_종료4(tmp_path, capsys, axis):
    dr_path = tmp_path / "doc_rule.yaml"
    dr_path.write_text(yaml.safe_dump({
        "conflict": "all",
        "doctype_rules": [{"id": "dt_a", "node": "A", "terms": ["루트A"]}],
    }, allow_unicode=True), encoding="utf-8")
    args = SimpleNamespace(axis=axis, doc_rules=str(dr_path))
    taxonomy, drs, code = cli._load_doctype_axis(args, _fake_log())
    assert (taxonomy, drs) == (None, None)
    assert code == config.EXIT_RULES_INVALID
    err = capsys.readouterr().err
    assert "[G0]" in err and "--build-doc-rule" in err


#------------------------------------------------------------------
# YAML 이 깨진 규칙 파일은 '옛 모양'이 아니라 읽기 오류로 종료(4)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_깨진_규칙파일은_읽기오류로_종료4(tmp_path, capsys):
    dr_path = tmp_path / "doc_rule.yaml"
    dr_path.write_text("doctype_rules: [\n  - {id: a,\n", encoding="utf-8")
    args = SimpleNamespace(axis=None, doc_rules=str(dr_path))
    _t, _d, code = cli._load_doctype_axis(args, _fake_log())
    assert code == config.EXIT_RULES_INVALID
    err = capsys.readouterr().err
    assert "읽지 못했습니다" in err and "[G0]" not in err


#------------------------------------------------------------------
# 자동 생성 규칙이면 분류체계를 규칙에서 지어 켠다(다른 파일은 읽지 않는다)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_자동생성_규칙이면_로드된다(tmp_path):
    rules = _generated_rules(tmp_path)
    args = SimpleNamespace(axis=None, doc_rules=rules)
    taxonomy, drs, code = cli._load_doctype_axis(args, _fake_log())
    assert code is None
    assert taxonomy is not None and len(taxonomy) > 0
    assert len(drs.rules) > 0 and drs.taxonomy is taxonomy


#------------------------------------------------------------------
# 규칙이 0건이어도 labels.doctype 키는 생긴다(값은 비어 있음)
#=> "축을 안 씀"(키 부재)과 "축은 돌았지만 못 찾음"(values=[])의 구분이
#   전파 단계의 분기 조건이라, 규칙 0건일 때 어느 쪽이 되는지가 중요하다
#   (--doctype-vector-only 가 규칙 목록을 비운 경우가 이렇다).
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_규칙0건이면_doctype키는_있고_값은_빈다():
    from csoclassify.classify import axes as AX
    from csoclassify.classify.doctype import scan_doctype
    tax = AX.taxonomy_from_snapshot({"taxonomy": {
        "source": "t", "exported_at": "20260824000000", "node_count": 1,
        "nodes": [{"dc_id": "DC_001", "parent": None, "order": 1,
                   "title": "계약서", "status": 1}],
    }})
    empty = D.DocRuleSet(conflict=D.ConflictSpec(), version="none", rules=(), warnings=())
    sig = scan_doctype("계약서 내용", "계약서.txt", empty, tax)
    d = sig.as_dict()
    # 규칙이 없으니 본문에 "계약서"가 있어도 못 맞힌다 — 그래도 키(=축이 돌았다)는 남는다.
    assert d["values"] == []
    # [2026-09-10] 값이 하나뿐이던 칸을 걷어냈다. 잘리지도 부딪치지도 않았으면
    # 그 칸은 아예 없다 — '없음 = 기본'이 규약이다. strategy 는 --conflict 로
    # 덮어썼을 때만 실린다(여기서는 안 덮어썼다).
    assert "status" not in d
    assert "truncated" not in d and "conflicts" not in d
    assert "strategy" not in d


# ── 없어진 옵션 (2026-09-22) ─────────────────────────────────────────

#------------------------------------------------------------------
# 없어진 옵션을 주면 무엇을 대신 쓰는지 알리고 종료(3 = unsupported_option)
#=> 조용히 무시하면 엠파워 배치는 옛 명령이 먹힌 줄 안다. --taxonomy 는 값을 받던
#   옵션이라 값이 있어도 없어도 같은 안내로 멈춰야 한다.
#
# -in: argv = 시험할 명령행
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
@pytest.mark.parametrize("argv", [
    ["--export-taxonomy"],
    ["--scaffold-doc-rule"],
    ["--sync-doc-rule"],
    ["--sync-doc-rule", "--no-fill-blank"],
    ["--sync-doc-rule", "--sync-enrich"],
    ["--taxonomy", "doc_taxonomy.yaml", "--check-rules"],
    ["--check-rules", "--taxonomy"],
])
def test_없어진_옵션은_안내하고_종료3(argv, capsys):
    assert cli.main(argv) == config.EXIT_ARG_ERROR
    err = capsys.readouterr().err
    assert "없어졌습니다" in err
    assert errcodes.code_of("unsupported_option") == 1012
