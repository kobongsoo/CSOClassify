#------------------------------------------------------------------
# 규칙셋 등급 검증(Phase 0 — fail-open 제거) 단위 테스트
#=> cso_rule.yaml 에 잘못된 등급이 적혔을 때 '조용히 무시'하지 않고 확실히
#   실패하는지 검증한다. 예전 동작은 모르는 등급을 건너뛰어 그 규칙이 판정에서
#   통째로 빠졌고, 결과적으로 문서 등급이 실제보다 낮게 나왔다(보안 사고).
#   여기서는 그 회귀를 막는 것이 목적이다.
#
#   검증 항목:
#     V0 = 구조 오류(섹션이 목록이 아님 / 항목이 매핑이 아님)
#     V5 = 정의되지 않은 등급 참조
#     V6 = bulk_grade 가 base_grade 보다 낮음(상향이어야 하는데 하향)
#------------------------------------------------------------------

import pytest
import yaml

from csoclassify.classify import rules as R


#------------------------------------------------------------------
# 최소 규칙셋 만들기
#=> 검사하려는 섹션만 담은 작은 dict 를 만든다. 실제 yaml 파일을 읽지 않으므로
#   테스트가 규칙셋 내용 변화에 흔들리지 않는다.
#
# -in: **sections = 섹션 키 → 항목 리스트 (예: regex_pii=[{...}])
#
# -out: dict = validate_rules_data 에 넣을 수 있는 규칙셋 원본 형태
# -out: error = 없음
#------------------------------------------------------------------
def mk(**sections):
    data = {"version": "test-1"}
    data.update(sections)
    return data


#------------------------------------------------------------------
# 위반 코드만 뽑기
#=> 단언문을 짧게 쓰려고 위반 목록에서 코드 문자열만 모은다.
#
# -in: violations = validate_rules_data 결과
#
# -out: list = ["V5", "V6", ...]
# -out: error = 없음
#------------------------------------------------------------------
def codes(violations):
    return [v["code"] for v in violations]


# ── 정상 케이스 ────────────────────────────────────────────────────

#------------------------------------------------------------------
# 실제 배포 규칙셋은 검증을 통과해야 한다
#=> 지금 쓰고 있는 cso_rule.yaml 에 이미 오타가 있으면 배포 즉시 분류가 멈춘다.
#   그런 일이 없도록 저장소의 규칙셋 자체를 회귀 테스트로 묶어 둔다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 위반이 있으면 AssertionError(위반 내용을 메시지에 표시)
#------------------------------------------------------------------
def test_배포_규칙셋은_검증을_통과한다():
    path = R.default_rules_path()
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    violations = R.validate_rules_data(data)
    assert violations == [], R.format_violations(path, violations)


#------------------------------------------------------------------
# 등급 키가 아예 없으면 통과 (로더 기본값이 쓰임)
#=> base_grade 를 안 적은 규칙은 로더가 기본값을 넣어 주므로 위반이 아니다.
#   '값이 틀린 것'과 '값을 안 적은 것'을 구분하는지 확인한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_등급_키가_없으면_위반이_아니다():
    data = mk(regex_pii=[{"id": "rrn", "label": "RRN", "weight": "high"}])
    assert R.validate_rules_data(data) == []


#------------------------------------------------------------------
# label 없는 regex_pii 항목은 검증에서도 건너뛴다
#=> 로더가 label 없는 옛 항목을 건너뛰므로, 검증도 같은 기준을 써야 한다.
#   쓰이지도 않는 규칙 때문에 분류가 멈추면 이행 중 혼란만 커진다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_label_없는_regex_항목은_검증에서_제외된다():
    data = mk(regex_pii=[{"id": "old", "base_grade": "완전히틀린값"}])
    assert R.validate_rules_data(data) == []


# ── V5 : 정의되지 않은 등급 ────────────────────────────────────────

#------------------------------------------------------------------
# 소문자 오타를 잡고 올바른 등급을 제안한다
#=> 실제로 가장 흔한 실수. 예전에는 이 값이 조용히 무시돼 rrn 규칙이 통째로
#   빠졌다. 이제는 V5 위반으로 잡히고 "혹시 C ?" 힌트까지 나와야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_소문자_등급은_V5_위반이고_힌트를_준다():
    data = mk(regex_pii=[{"id": "rrn", "label": "RRN", "base_grade": "c"}])
    v = R.validate_rules_data(data)
    assert codes(v) == ["V5"]
    assert v[0]["rule_id"] == "rrn"
    assert v[0]["field"] == "base_grade"
    assert v[0]["hint"] == "C"


#------------------------------------------------------------------
# 앞뒤 공백이 붙은 등급도 잡는다
#=> "C " 는 눈으로는 정상으로 보이지만 문자열 비교에서는 다른 값이다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_공백_붙은_등급은_V5_위반이다():
    data = mk(stamps=[{"id": "st", "grade": "C "}])
    v = R.validate_rules_data(data)
    assert codes(v) == ["V5"]
    assert v[0]["hint"] == "C"


#------------------------------------------------------------------
# 다른 등급체계의 값은 힌트 없이 위반으로 잡는다
#=> 짚이는 오타가 아니면 억지 제안을 하지 않는다(잘못된 제안이 더 위험).
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_모르는_등급은_힌트없이_위반이다():
    data = mk(stamps=[{"id": "p1", "grade": "비밀"}])
    v = R.validate_rules_data(data)
    assert codes(v) == ["V5"]
    assert v[0]["hint"] is None


#------------------------------------------------------------------
# 등급 값이 null 이면 위반
#=> 'grade:' 만 적고 값을 빠뜨린 경우. 키는 있으므로 로더 기본값이 안 먹는다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_등급이_null_이면_위반이다():
    data = mk(sensitive=[{"id": "s1", "grade": None}])
    assert codes(R.validate_rules_data(data)) == ["V5"]


#------------------------------------------------------------------
# 등급이 문자열이 아니면 위반
#=> yaml 에서 따옴표 없이 숫자를 적으면 문자열이 아니게 된다.
#   자료형 이름은 파이썬 용어(int)가 아니라 YAML 용어(number)로 낸다 — Rust 판과
#   메시지를 맞춰야 두 구현의 보고문이 갈리지 않는다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_등급이_문자열이_아니면_위반이다():
    data = mk(pii_combos=[{"id": "c1", "grade": 3}])
    v = R.validate_rules_data(data)
    assert codes(v) == ["V5"]
    assert "number" in v[0]["detail"]


#------------------------------------------------------------------
# 모든 섹션의 등급 필드를 빠짐없이 본다
#=> 한 섹션만 검사하고 나머지를 놓치면 그 섹션이 fail-open 으로 남는다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_다섯_섹션_모두_검사한다():
    data = mk(
        regex_pii=[{"id": "r", "label": "RRN", "base_grade": "x"}],
        pii_combos=[{"id": "c", "grade": "x"}],
        keywords=[{"id": "k", "base_grade": "x"}],
        sensitive=[{"id": "s", "grade": "x"}],
        stamps=[{"id": "t", "grade": "x"}],
    )
    v = R.validate_rules_data(data)
    assert codes(v) == ["V5"] * 5
    assert {x["section"] for x in v} == {
        "regex_pii", "pii_combos", "keywords", "sensitive", "stamps"}


#------------------------------------------------------------------
# 위반이 여럿이면 전부 모아서 보고한다
#=> 첫 오류에서 멈추면 관리자가 고치고 다시 돌리기를 반복해야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_위반을_전부_모아_보고한다():
    data = mk(stamps=[{"id": "p1", "grade": "x"},
                     {"id": "p2", "grade": "y"},
                     {"id": "p3", "grade": "z"}])
    assert len(R.validate_rules_data(data)) == 3


# ── V6 : bulk 상향 방향 ────────────────────────────────────────────

#------------------------------------------------------------------
# bulk 가 base 보다 낮으면 위반
#=> bulk_grade 는 "많이 나오면 등급을 올린다"는 설계다. 거꾸로 적으면
#   주민번호가 많이 나올수록 등급이 내려가는데, 문법상으론 멀쩡해 보인다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_bulk_가_base_보다_낮으면_V6_위반이다():
    data = mk(regex_pii=[{"id": "ph", "label": "PHONE",
                          "base_grade": "S", "bulk_grade": "O"}])
    v = R.validate_rules_data(data)
    assert codes(v) == ["V6"]
    assert v[0]["field"] == "bulk_grade"


#------------------------------------------------------------------
# 정상 상향과 동일 등급은 통과
#=> 같은 등급은 무의미하지만 위험하지 않으므로 막지 않는다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
@pytest.mark.parametrize("base,bulk", [("S", "C"), ("O", "S"), ("S", "S")])
def test_bulk_상향과_동일등급은_통과한다(base, bulk):
    data = mk(keywords=[{"id": "k", "base_grade": base, "bulk_grade": bulk}])
    assert R.validate_rules_data(data) == []


#------------------------------------------------------------------
# 등급 자체가 오타면 V6 를 중복 보고하지 않는다
#=> V5 로 이미 보고했으므로 방향 검사는 건너뛴다. 한 실수에 두 줄이 나오면
#   관리자가 원인을 두 개로 오해한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_오타일_때_V6를_중복보고하지_않는다():
    data = mk(keywords=[{"id": "k", "base_grade": "s", "bulk_grade": "O"}])
    assert codes(R.validate_rules_data(data)) == ["V5"]


# ── V0 : 구조 오류 ────────────────────────────────────────────────

#------------------------------------------------------------------
# 섹션이 목록이 아니면 위반
#=> 들여쓰기 실수로 리스트가 dict 가 되면 예전에는 순회 중 AttributeError 로
#   원시 스택이 터졌다. 이제는 사람이 읽을 수 있는 위반으로 잡는다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_섹션이_목록이_아니면_V0_위반이다():
    data = mk(stamps={"id": "p1", "grade": "C"})
    assert codes(R.validate_rules_data(data)) == ["V0"]


#------------------------------------------------------------------
# 항목이 매핑이 아니면 위반
#=> '- C' 처럼 값만 적힌 항목.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_항목이_매핑이_아니면_V0_위반이다():
    data = mk(stamps=["C"])
    v = R.validate_rules_data(data)
    assert codes(v) == ["V0"]
    assert v[0]["rule_id"] == "#0"


# ── max_grade 의 fail-closed 동작 ──────────────────────────────────

#------------------------------------------------------------------
# None 은 정상적으로 무시된다
#=> "이 신호는 등급을 내지 않았다"는 정상 상태라 예외가 아니다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_max_grade_는_None_을_건너뛴다():
    assert R.max_grade(["O", None, "C"]) == "C"
    assert R.max_grade([None, None]) is None
    assert R.max_grade([]) is None


#------------------------------------------------------------------
# 모르는 등급이 오면 예외 (핵심 회귀 방지)
#=> 예전에는 rank -1 로 조용히 건너뛰어 등급이 낮게 나왔다. 이 테스트가
#   그 동작으로 되돌아가는 것을 막는다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_max_grade_는_모르는_등급에_예외를_던진다():
    with pytest.raises(R.UnknownGradeError):
        R.max_grade(["O", "c"])
    with pytest.raises(R.UnknownGradeError):
        R.max_grade(["비밀"])


#------------------------------------------------------------------
# 예외 메시지에 문제 값과 위치가 들어간다
#=> 사용자가 어디를 고쳐야 하는지 바로 알 수 있어야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_예외_메시지에_값과_위치가_담긴다():
    with pytest.raises(R.UnknownGradeError) as ei:
        R.max_grade(["zz"], where="signals.rule.grade")
    msg = str(ei.value)
    assert "zz" in msg and "signals.rule.grade" in msg
    assert "O < S < C" in msg


# ── load_rules 통합 ───────────────────────────────────────────────

#------------------------------------------------------------------
# 잘못된 규칙셋은 RuleSet 을 만들지 않고 실패한다
#=> 절반쯤 잘못 분류된 결과가 만들어지는 것이 최악이므로, 스캔 전에 막는다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_load_rules_는_잘못된_규칙셋을_거부한다(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("version: t\nstamps:\n- id: st\n  grade: c\n", encoding="utf-8")
    with pytest.raises(R.RuleSetValidationError) as ei:
        R.load_rules(str(p))
    assert len(ei.value.violations) == 1
    assert "혹시 C ?" in str(ei.value)


#------------------------------------------------------------------
# validate=False 면 검증을 건너뛴다
#=> 검증기 자체를 시험하거나 잘못된 파일을 일부러 읽어야 하는 도구용 탈출구.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_validate_False_면_검증을_건너뛴다(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("version: t\nstamps:\n- id: st\n  grade: c\n", encoding="utf-8")
    rs = R.load_rules(str(p), validate=False)
    assert rs.stamp_rules[0].grade == "c"


#------------------------------------------------------------------
# 보고문에 필요한 정보가 모두 들어간다
#=> 파일 경로·건수·정의된 등급·고치는 법이 한 화면에 있어야 한다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_보고문_형식():
    data = mk(stamps=[{"id": "p1", "grade": "x"}])
    text = R.format_violations("C:/rules.yaml", R.validate_rules_data(data))
    assert "C:/rules.yaml" in text
    assert "검증 실패 1건" in text
    assert "O < S < C" in text
    assert "stamps[p1].grade" in text
    assert "고치는 법" in text


# ── V12 : 이제 안 쓰는 블록(paths) 안내 ──────────────────────────
# [A안, 2026-08-24] 경로 규칙은 grade 를 생략하고 acl_restricted: true 만 적을 수 있다.
#   그런 규칙은 스스로 등급을 내지 않고, 다른 신호가 전혀 없을 때에만 fail-safe 로
#   최고 등급을 만든다. 예전에는 grade 를 생략하면 조용히 "S" 가 되어 이 표현이
#   아예 불가능했고, 그 탓에 fuse 의 failsafe_acl 분기가 죽어 있었다.


#------------------------------------------------------------------
# 옛 paths 블록이 남아 있으면 알린다 (V12 의 새 뜻)
#=> 이 파서는 모르는 최상위 키를 조용히 무시한다. 그대로 두면 경로로 C 가
#   붙던 문서가 아무 말 없이 등급을 잃는다 — 배포된 규칙셋 3벌에 실제로
#   paths: 가 들어 있었으므로, 이 안내가 없으면 현장에서 조용히 바뀐다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_옛_paths_블록은_사유를_알린다():
    data = mk(paths=[{"id": "p", "match": ["/인사/"], "grade": "S"}])
    v = R.validate_rules_data(data)
    hits = [x for x in v if x["code"] == "V12"]
    assert hits, v
    assert "더 이상 쓰지 않는 블록" in hits[0]["detail"], hits[0]
    # 보고문에도 '지우라'는 안내가 붙는다.
    txt = R.format_violations("D:/x/cso_rule.yaml", v)
    assert "통째로 지우세요" in txt and "--failsafe" in txt, txt


#------------------------------------------------------------------
# 빈 paths 블록은 알리지 않는다
#=> 블록만 남고 내용이 없으면 판정에 영향이 없다. 알릴 것이 없는데 알리면
#   진짜 문제가 있는 경고 사이에 묻힌다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_빈_paths_는_알리지_않는다():
    assert not [x for x in R.validate_rules_data(mk(paths=[])) if x["code"] == "V12"]
