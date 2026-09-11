"""doc_rule.yaml 의 signals: 블록 — 신호별 신뢰도를 정책 파일에서 설정한다.

여기서 지키는 것:
  · 정책 파일이 **실제로 점수를 바꾼다**(안 바뀌면 옵션이 있으나 마나다).
  · 잘못 적은 것은 **조용히 무시되지 않는다** — 오타 하나가 "고쳤는데 왜 안 바뀌지"가
    되면, 값을 만질 수 있게 만든 의미 자체가 사라진다.
  · signals: 가 없던 예전 파일도 **그대로 돈다**(하위호환).
  · 지금 설정할 수 있는 것은 실제로 도는 4개(title·head·body·name)뿐이고,
    안 도는 신호는 그 사유를 밝히며 막는다.
"""

import io
import json
import os
import subprocess
import sys

import pytest
import yaml

from csoclassify.classify import doc_rules
from csoclassify.classify.doctype import _SIG_CONF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS_EXE = os.path.join(ROOT, "Rust", "target", "release", "MpowerClassify-rs.exe")
POLICY = os.path.join(ROOT, "resources", "policy")


#------------------------------------------------------------------
# 검증에 걸릴 doc_rule 문서 하나 만들기
#=> 규칙 본문은 이 시험의 관심사가 아니므로 최소한만 둔다.
#
# -in: signals = signals: 블록에 넣을 값(None 이면 키 자체를 안 넣는다)
#
# -out: dict = validate_doc_rule_data 에 그대로 넣을 수 있는 문서
# -out: error = 없음
#------------------------------------------------------------------
def _doc(signals=None):
    d = {"version": "t", "conflict": "all",
         "defaults": {"scoring": "staged"},
         "doctype_rules": []}
    if signals is not None:
        d["signals"] = signals
    return d


#------------------------------------------------------------------
# 일부만 적어도 되고, 안 적은 칸은 코드 기본값이 남는다
#=> "title 만 0.90 으로 올려 보자"가 두 줄로 끝나야 실제로 시도된다.
#   전부 적게 하면 아무도 안 만진다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_일부만_적으면_나머지는_기본값():
    got, vio = doc_rules._parse_signals({"title": {"high": 0.90}})
    assert vio == [], vio
    assert got["title"]["high"] == 0.90
    # 안 적은 칸은 그대로.
    assert got["title"]["medium"] == _SIG_CONF["title"]["medium"]
    assert got["head"] == _SIG_CONF["head"]


#------------------------------------------------------------------
# signals: 가 없으면 None — 예전 파일도 그대로 돈다
#=> 이미 현장에 깔린 doc_rule.yaml 에는 이 블록이 없다. None 이면 채점 쪽이
#   코드 기본값으로 돌아, 이번 변경으로 기존 판정이 흔들리지 않는다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_블록이_없으면_None():
    assert doc_rules._parse_signals(None) == (None, [])


#------------------------------------------------------------------
# 잘못 적은 것은 로드 시점에 막힌다 (T20)
#=> 조용히 무시하면 사람은 "고쳤는데 왜 안 바뀌지"를 혼자 헤맨다.
#   특히 오타(titel)는 값이 정상 범위라 더 알아채기 어렵다.
#
# -in: signals = 잘못 적은 블록
# -in: where   = 위반 메시지에 나와야 할 자리 이름
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
@pytest.mark.parametrize("signals, where", [
    ({"title": {"high": 1.5}},      "signals.title.high"),     # 범위 밖
    ({"title": {"high": -0.1}},     "signals.title.high"),     # 음수
    ({"titel": {"high": 0.8}},      "signals.titel"),          # 신호 이름 오타
    ({"title": {"highest": 0.8}},   "signals.title.highest"),  # 등급 키 오타
    ({"title": {"high": "세게"}},    "signals.title.high"),     # 숫자 아님
    ({"title": {"high": True}},     "signals.title.high"),     # bool 은 숫자가 아니다
    ({"title": 0.8},                "signals.title"),          # 매핑이 아님
    ("문자열",                        "signals"),                # 블록 자체가 매핑이 아님
])
def test_잘못된_값은_막는다(signals, where):
    vio = doc_rules.validate_doc_rule_data(_doc(signals))
    hits = [v for v in vio if where in str(v)]
    assert hits, f"{where} 위반이 안 잡혔다: {vio}"


#------------------------------------------------------------------
# (b)안 — name·structure 는 세 값이 같아야 한다
#=> 코드가 이 둘만 weight 를 보지 않고 medium 칸을 직접 집어 쓴다. 그래서
#   high 를 다르게 적어도 아무 효과가 없다 — 설정할 수 있게 해 놓고 조용히
#   무시하는 셈이라, 아예 막아서 그 자리에서 알려 준다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_name_은_세_값이_같아야_한다():
    vio = doc_rules.validate_doc_rule_data(_doc({"name": {"high": 0.9}}))
    hits = [v for v in vio if "signals.name" in str(v)]
    assert hits, "세 값이 다른데 안 막혔다"
    assert "medium" in str(hits[0])
    # 세 값을 같게 적으면 통과해야 한다 — 값 자체를 못 바꾸게 막는 게 아니다.
    ok = doc_rules.validate_doc_rule_data(_doc({"name": {"high": 0.4, "medium": 0.4, "low": 0.4}}))
    assert not [v for v in ok if "signals.name" in str(v)], ok


#------------------------------------------------------------------
# 본보기(doc_rule_template.yaml)의 값이 코드 기본값과 같다
#=> 본보기가 코드와 어긋나면, 새로 만든 doc_rule.yaml 이 예전 파일과 다른
#   점수를 낸다 — 파일을 새로 만들었을 뿐인데 판정이 바뀌는 셈이다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_본보기_값이_코드_기본값과_같다():
    p = os.path.join(POLICY, "doc_rule_template.yaml")
    tpl = yaml.safe_load(io.open(p, encoding="utf-8").read())
    sig = tpl.get("signals")
    assert sig, "본보기에 signals: 블록이 없다"
    # 본보기에는 '설정할 수 있는 것'만 있어야 한다 — 안 도는 신호를 적어 두면
    # 사람이 그 값을 만지다 "고쳤는데 점수가 그대로다"에 빠진다.
    assert set(sig) == set(doc_rules.CONFIGURABLE_SIGNALS), sorted(sig)
    for name in doc_rules.CONFIGURABLE_SIGNALS:
        for w, v in _SIG_CONF[name].items():
            assert abs(sig[name][w] - v) < 1e-9, f"{name}.{w}: 본보기 {sig[name][w]} != 코드 {v}"


#------------------------------------------------------------------
# 없어진 신호 이름은 '모르는 이름'으로 거절한다
#=> form·structure·path 는 2026-09-07 에 코드에서 걷어냈다. 이제 아는 이름이
#   아니므로 오타와 같은 갈래로 막힌다 — 쓸 수 있는 목록을 함께 보여 주므로
#   사람은 그 자리에서 무엇을 적어야 하는지 안다.
#
# -in: sig = 없어진 신호 이름
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
@pytest.mark.parametrize("sig", ["form", "structure", "path"])
def test_없어진_신호_이름은_막힌다(sig):
    vio = doc_rules.validate_doc_rule_data(_doc({sig: {"high": 0.5, "medium": 0.5, "low": 0.5}}))
    hits = [str(v) for v in vio if f"signals.{sig}" in str(v)]
    assert hits, f"{sig}: 안 막혔다"
    assert "모르는 신호" in hits[0], hits[0]
    # 무엇을 쓸 수 있는지까지 알려 준다.
    assert "title" in hits[0] and "head" in hits[0], hits[0]


#------------------------------------------------------------------
# 옛 규칙에 남은 form·structure·paths 필드는 조용히 무시하지 않는다
#=> 이 파서는 모르는 필드를 그냥 넘긴다. 그대로 두면 예전에 그 필드를 적어 둔
#   파일에서 신호가 소리 없이 사라진다 — 사실대로 알려서, 규칙에서 지우게 한다.
#
# -in: field = 이제 안 쓰는 규칙 필드 이름
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
@pytest.mark.parametrize("field, value", [
    ("form", {"all_of": ["갑", "을"]}),
    ("structure", [r"제\s*\d+\s*조"]),
    ("paths", ["/계약/"]),
])
def test_옛_규칙_필드는_사유를_알린다(field, value):
    d = _doc()
    d["doctype_rules"] = [{"id": "x", "node": "DC_001", "weight": "medium",
                           "title_terms": ["가"], field: value}]
    vio = doc_rules.validate_doc_rule_data(d)
    hits = [str(v) for v in vio if f"'{field}'" in str(v)]
    assert hits, f"{field}: 조용히 무시됐다 :: {vio}"
    assert "더 이상 쓰지 않는 필드" in hits[0], hits[0]


#------------------------------------------------------------------
# 두 판이 같은 점수를 낸다 — 여기가 이 기능의 진짜 위험 지점
#=> Rust 는 표가 하드코딩 match 라, 파이썬만 정책 파일을 읽으면 두 판이
#   **조용히 다른 점수**를 낸다. 종료코드도 오류 메시지도 없이 결과만 갈리는,
#   가장 알아채기 어려운 종류의 어긋남이다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = Rust exe 가 없으면 skip
#------------------------------------------------------------------
def test_두_판이_같은_점수를_낸다(tmp_path):
    if not os.path.isfile(RS_EXE):
        pytest.skip("Rust exe 없음(cargo build --release 먼저)")
    dr = os.path.join(POLICY, "doc_rule.yaml")
    tx = os.path.join(POLICY, "doc_taxonomy.yaml")
    rules = os.path.join(POLICY, "cso_rule.yaml")
    if not (os.path.isfile(dr) and os.path.isfile(tx)):
        pytest.skip("정책 파일 없음")

    base = yaml.safe_load(io.open(dr, encoding="utf-8").read())
    doc = tmp_path / "문서"
    doc.mkdir()
    (doc / "계약.txt").write_text(
        "용역 계약서\n제1조 이 계약은 갑과 을 사이의 용역에 관한 사항을 정한다.\n"
        "계약기간: 2026-01-01\n", encoding="utf-8")

    def scores(rule_path):
        out = {}
        for name, cmd in (("python", [sys.executable, "-m", "csoclassify"]), ("rust", [RS_EXE])):
            env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"), PYTHONIOENCODING="utf-8")
            r = subprocess.run(cmd + ["--dir", str(doc), "--rule-only", "--nosummary",
                                      "--rules", rules, "--doc-rules", str(rule_path),
                                      "--taxonomy", tx, "--format", "jsonl"],
                               cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
            got = []
            for line in r.stdout.splitlines():
                if line.startswith("{") and '"file"' in line:
                    dt = (json.loads(line).get("why") or {}).get("doctype") or {}
                    got += [(v.get("dc_id"), round(v.get("confidence", 0), 4))
                            for v in (dt.get("values") or [])]
            assert got, f"{name}: 라벨이 없다 :: {r.stderr[-300:]}"
            out[name] = sorted(got)
        return out

    # ① signals: 없이 — 두 판이 코드 기본값으로 같은 점수
    p0 = tmp_path / "base.yaml"
    p0.write_text(yaml.safe_dump(base, allow_unicode=True, sort_keys=False), encoding="utf-8")
    s0 = scores(p0)
    assert s0["python"] == s0["rust"], s0

    # ② signals: 로 title 을 올리면 — 두 판이 **똑같이** 올라간다
    boosted = dict(base)
    boosted["signals"] = {"title": {"high": 0.95, "medium": 0.95, "low": 0.95}}
    p1 = tmp_path / "boost.yaml"
    p1.write_text(yaml.safe_dump(boosted, allow_unicode=True, sort_keys=False), encoding="utf-8")
    s1 = scores(p1)
    assert s1["python"] == s1["rust"], s1
    # 정책 파일이 실제로 점수를 바꿨는지 — 안 바뀌면 이 기능이 있으나 마나다.
    assert s1["python"] != s0["python"], (s0, s1)
