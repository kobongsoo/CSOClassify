#------------------------------------------------------------------
# CLI 배선 — doctype 축 관련 헬퍼 단위 테스트
#=> cli.py 에 새로 붙인 _check_stale_taxonomy(T13)·_parse_conflict_override
#   (6-5·T2·T10·T11)·_doctype_breakdown(요약 집계)·_load_doctype_axis(4-6·
#   T14·T16)가 설계대로 동작하는지 검증한다. 실제 문서 추출·임베딩은 거치지
#   않는다(cli.py 의 기존 테스트 관례 — 무거운 파이프라인은 여기서 다루지 않음).
#------------------------------------------------------------------

import datetime
from types import SimpleNamespace

import pytest
import yaml

from csoclassify import cli
from csoclassify import config
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


# ── _check_stale_taxonomy (T13) ─────────────────────────────────────

#------------------------------------------------------------------
# 최근 스냅샷은 경고가 없다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_최근_스냅샷은_경고없음():
    now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    taxonomy = A.Taxonomy("t", now, 0, [])
    assert cli._check_stale_taxonomy(taxonomy) is None


#------------------------------------------------------------------
# 91일 전 스냅샷은 경고를 낸다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_91일_전_스냅샷은_경고():
    old = (datetime.datetime.now() - datetime.timedelta(days=91)).strftime("%Y%m%d%H%M%S")
    taxonomy = A.Taxonomy("t", old, 0, [])
    warning = cli._check_stale_taxonomy(taxonomy)
    assert warning is not None
    assert "91일" in warning or "일 전" in warning


#------------------------------------------------------------------
# exported_at 형식이 이상하면 판단하지 않는다(None)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_exported_at_형식이상하면_None():
    taxonomy = A.Taxonomy("t", "이상한값", 0, [])
    assert cli._check_stale_taxonomy(taxonomy) is None


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


# ── _load_doctype_axis (4-6·T14·T16) ─────────────────────────────────

#------------------------------------------------------------------
# --axis security 면 파일을 아예 건드리지 않는다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_axis_security면_시도조차_안함(tmp_path, capsys):
    args = SimpleNamespace(axis="security", taxonomy=str(tmp_path / "없음.yaml"), doc_rules=None)
    taxonomy, drs, code = cli._load_doctype_axis(args, _fake_log())
    assert (taxonomy, drs, code) == (None, None, None)
    assert capsys.readouterr().err == ""   # 경고조차 안 남긴다


#------------------------------------------------------------------
# 기본(축 미지정)인데 스냅샷이 없으면 경고 후 정상 통과(축만 비활성)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_기본축_스냅샷없으면_경고후_통과(tmp_path, capsys):
    args = SimpleNamespace(axis=None, taxonomy=str(tmp_path / "없음.yaml"), doc_rules=None)
    taxonomy, drs, code = cli._load_doctype_axis(args, _fake_log())
    assert (taxonomy, drs, code) == (None, None, None)
    assert "건너뜁니다" in capsys.readouterr().err


#------------------------------------------------------------------
# --axis doctype 인데 스냅샷이 없으면 종료(4)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_axis_doctype인데_스냅샷없으면_종료4(tmp_path):
    args = SimpleNamespace(axis="doctype", taxonomy=str(tmp_path / "없음.yaml"), doc_rules=None)
    taxonomy, drs, code = cli._load_doctype_axis(args, _fake_log())
    assert (taxonomy, drs) == (None, None)
    assert code == config.EXIT_RULES_INVALID


#------------------------------------------------------------------
# 스냅샷은 있는데 doc_rule.yaml 이 없으면 축을 끄지 않고 'seed 전파 전용'으로 켠다
#=> 규칙이 없어도 class_seed.jsonl 과의 임베딩 전파라는 분류 수단이 남아 있으므로,
#   축을 통째로 끄지 않고 빈 규칙셋으로 켠 채 둔다. 축을 끄면 labels.doctype 키가
#   아예 안 생겨 전파 단계까지 건너뛴다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_스냅샷은_있고_doc_rule_없으면_seed전파전용으로_켜진다(tmp_path, capsys):
    tax_path = tmp_path / "doc_taxonomy.yaml"
    tax_path.write_text(yaml.safe_dump({"taxonomy": {
        "source": "t", "exported_at": "20260824000000", "node_count": 0, "nodes": [],
    }}, allow_unicode=True), encoding="utf-8")
    args = SimpleNamespace(axis=None, taxonomy=str(tax_path),
                           doc_rules=str(tmp_path / "없음_doc_rule.yaml"))
    taxonomy, drs, code = cli._load_doctype_axis(args, _fake_log())
    assert code is None
    # 축은 켜져 있다 — taxonomy 와 규칙셋 둘 다 값이 있어야 engine 이 labels.doctype 을 만든다.
    assert taxonomy is not None and drs is not None
    # 규칙은 0건이고, 나중에 "규칙 없이 돌았다"를 구분할 수 있게 version 이 "none" 이다.
    assert drs.rules == () and drs.version == "none"
    err = capsys.readouterr().err
    assert "doc_rule.yaml" in err and "seed 전파 전용" in err


#------------------------------------------------------------------
# --axis doctype 이어도 doc_rule.yaml 이 없다고 멈추지는 않는다
#=> 규칙이 없어도 seed 전파로 분류할 수 있으므로 '축을 못 쓴다'가 아니다.
#   (스냅샷이 없을 때만 종료 4 — 그건 대체 수단이 없다.)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_axis_doctype라도_doc_rule만_없으면_계속한다(tmp_path):
    tax_path = tmp_path / "doc_taxonomy.yaml"
    tax_path.write_text(yaml.safe_dump({"taxonomy": {
        "source": "t", "exported_at": "20260824000000", "node_count": 0, "nodes": [],
    }}, allow_unicode=True), encoding="utf-8")
    args = SimpleNamespace(axis="doctype", taxonomy=str(tax_path),
                           doc_rules=str(tmp_path / "없음_doc_rule.yaml"))
    taxonomy, drs, code = cli._load_doctype_axis(args, _fake_log())
    assert code is None and taxonomy is not None and drs.version == "none"


#------------------------------------------------------------------
# 규칙이 없어도 labels.doctype 키는 생긴다(값은 비어 있음)
#=> "축을 안 씀"(키 부재)과 "축은 돌았지만 못 찾음"(values=[])의 구분이
#   전파 단계의 분기 조건이라, 규칙 0건일 때 어느 쪽이 되는지가 중요하다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_규칙0건이면_doctype키는_있고_값은_빈다(tmp_path):
    from csoclassify.classify import axes as AX
    from csoclassify.classify import doc_rules as DR
    from csoclassify.classify.doctype import scan_doctype
    tax_path = tmp_path / "doc_taxonomy.yaml"
    tax_path.write_text(yaml.safe_dump({"taxonomy": {
        "source": "t", "exported_at": "20260824000000", "node_count": 1,
        "nodes": [{"dc_id": "DC_001", "parent": None, "order": 1,
                   "title": "계약서", "status": 1}],
    }}, allow_unicode=True), encoding="utf-8")
    tax = AX.load_taxonomy(str(tax_path))
    sig = scan_doctype("계약서 내용", "계약서.txt", DR.seed_only_ruleset(), tax)
    d = sig.as_dict()
    # 규칙이 없으니 본문에 "계약서"가 있어도 못 맞힌다 — 그래도 키(=축이 돌았다)는 남는다.
    assert d["values"] == [] and d["strategy"] == "all" and d["truncated"] == 0


#------------------------------------------------------------------
# 둘 다 있고 정상이면 (taxonomy, doc_rules_set, None) 을 돌려준다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_둘다_정상이면_로드된다(tmp_path):
    tax_path = tmp_path / "doc_taxonomy.yaml"
    tax_path.write_text(yaml.safe_dump({"taxonomy": {
        "source": "t", "exported_at": "20260824000000", "node_count": 1,
        "nodes": [{"dc_id": "A", "parent": None, "order": 1, "title": "루트A", "status": 1}],
    }}, allow_unicode=True), encoding="utf-8")
    dr_path = tmp_path / "doc_rule.yaml"
    dr_path.write_text(yaml.safe_dump({
        "conflict": "all",
        "doctype_rules": [{"id": "dt_a", "node": "A", "terms": ["루트A"]}],
    }, allow_unicode=True), encoding="utf-8")

    args = SimpleNamespace(axis=None, taxonomy=str(tax_path), doc_rules=str(dr_path))
    taxonomy, drs, code = cli._load_doctype_axis(args, _fake_log())
    assert code is None
    assert len(taxonomy) == 1
    assert len(drs.rules) == 1


#------------------------------------------------------------------
# 스냅샷 검증 실패(T7 순환)는 축과 무관하게 항상 치명적(4)
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_스냅샷_검증실패는_항상_종료4(tmp_path):
    tax_path = tmp_path / "doc_taxonomy.yaml"
    tax_path.write_text(yaml.safe_dump({"taxonomy": {
        "source": "t", "exported_at": "20260824000000", "node_count": 1,
        "nodes": [{"dc_id": "A", "parent": "A", "order": 1, "title": "A", "status": 1}],
    }}, allow_unicode=True), encoding="utf-8")
    args = SimpleNamespace(axis=None, taxonomy=str(tax_path), doc_rules=None)
    taxonomy, drs, code = cli._load_doctype_axis(args, _fake_log())
    assert (taxonomy, drs) == (None, None)
    assert code == config.EXIT_RULES_INVALID
