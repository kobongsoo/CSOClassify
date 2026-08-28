#------------------------------------------------------------------
# 업무 분류(doctype) 축 — doc_rule.yaml 로더
#=> "무엇을 계약서로 볼 것인가" 같은 매칭 규칙(doctype_rules)과 축 전체 충돌
#   전략(conflict)을 읽는다. cso_rules.yaml(security 축)과는 완전히 다른
#   파일이며(설계서 3-2), 이 로더는 그 파일을 전혀 건드리지 않는다.
#   doc_rule.yaml 이 없어도 시스템은 계속 동작해야 한다 — 이 축은 "있으면
#   켜지고 없으면 꺼지는 외장 자산"이다(설계서 4-6과 같은 원칙, T16).
#   (설계: 문서분류체계 연동 설계서 §5·6, 로드맵 D2)
#------------------------------------------------------------------

import os
import re
import sys
from dataclasses import dataclass

import yaml

from ..resources import resource_path, exe_dir

# doc_rule.yaml 도 cso_rules.yaml·doc_taxonomy.yaml 과 같은 "exe 옆 외장 파일"
# 규약을 따른다(설계서 3-2 표 — 배포 원칙은 세 파일이 모두 같다).
DOC_RULE_FILENAME = "doc_rule.yaml"

# doc_rule.yaml 이 정의할 수 있는 conflict 전략. taxonomy 축(kind: taxonomy)은
# 서열이 없어 max 는 애초에 문법에 없다(3-1 표) — 오타로 적혀도 T2 로 막는다.
CONFLICT_STRATEGIES = ("all", "top_n")


#------------------------------------------------------------------
# 충돌 해소 전략 설정
#=> doc_rule.yaml 최상위 conflict: 값을 파싱한 결과. 3-2-1 결정으로 doctype
#   축에서 유일하게 파일에 적는 값이다(id·kind·cardinality·required·taxonomy
#   경로는 전부 코드에 고정돼 사라졌다).
#
# -필드: strategy       = "all"(기본) | "top_n"
# -필드: n              = top_n 일 때 상위 몇 개를 낼지(정수 ≥1). all 이면 None
# -필드: min_confidence = top_n 일 때 함께 적용할 최소 신뢰도(선택, 없으면 None)
#------------------------------------------------------------------
@dataclass(frozen=True)
class ConflictSpec:
    strategy: str = "all"
    n: int = None
    min_confidence: float = None


#------------------------------------------------------------------
# 서식 필드어 세트 (재설계 7장)
#=> "이 문서가 무슨 종류인가"를 문서종류명이 아니라 '그 양식에만 있는 항목명'
#   으로 판별한다. 회의록을 회의록으로 만드는 건 '회의록'이라는 단어가 아니라
#   일시·장소·참석자·안건이라는 서식 골격이기 때문이다.
#   security 축 ComboRule 의 all_of / of+min_types 문법을 그대로 가져와,
#   관리자가 두 축에서 같은 문법을 쓰게 한다.
#
# -필드: all_of    = 이 항목들이 '모두' 있어야 성립(비어 있으면 이 조건 미사용)
# -필드: any_of    = min_types 판정 대상 항목 그룹
# -필드: min_types = any_of 에서 필요한 '서로 다른 항목' 최소 종수(0이면 미사용)
#------------------------------------------------------------------
@dataclass(frozen=True)
class FormSpec:
    all_of: tuple = ()
    any_of: tuple = ()
    min_types: int = 0

    #------------------------------------------------------------------
    # 조건이 하나라도 적혀 있는지
    #=> all_of 도 없고 any_of/min_types 도 없으면 이 규칙은 아무 것도 판정하지
    #   않는다. 그런 빈 껍데기가 "항상 성립"으로 오작동하지 않도록 스캔 쪽에서
    #   이 값을 먼저 확인한다.
    #
    # -in: 없음
    #
    # -out: bool = 실제로 판정 가능한 조건이 있으면 True
    # -out: error = 없음
    #------------------------------------------------------------------
    @property
    def usable(self):
        return bool(self.all_of) or bool(self.any_of and self.min_types > 0)


#------------------------------------------------------------------
# 전역 기본값 블록 (doc_rule.yaml 의 defaults:)
#=> 규칙마다 반복해 적지 않아도 되게 하는 공통값. 규칙에 같은 이름 필드가
#   있으면 그쪽이 이긴다.
#   [하위호환] min_distinct/min_count 의 코드 기본값은 1 이다 — 현행
#   "본문에 1건이라도 있으면 히트"와 완전히 같게 동작한다. 2 로 조이는 것은
#   정책 파일에서 명시적으로 켜는 옵트인이다(재설계 14-1).
#
# -필드: head_chars   = 표제부로 볼 앞부분 글자 수(기본 400)
# -필드: min_distinct = 본문 terms 중 '서로 다른 단어' 최소 종수(기본 1)
# -필드: min_count    = 본문 terms 총 등장 건수 하한(기본 1)
# -필드: t_high       = 확정 후보 임계(기본 0.70)
# -필드: t_low        = 약한 후보 임계(기본 0.30). 이 미만은 후보에서 탈락
#                        [0.35 가 아닌 이유] 재설계 8-3 초안은 0.35 였으나,
#                        같은 문서 3-B-8 의 실측은 "파일명만으로 37건(44%)이
#                        즉시 회수된다"를 전제한다. name 신호가 0.30 이라
#                        0.35 로 두면 파일명 단독 후보가 통째로 사라져 두 절이
#                        모순된다. 실측 쪽에 맞춰 0.30 으로 확정한다.
# -필드: t_seed       = seed 승격 후보 임계(기본 0.85)
# -필드: embed_cap    = 벡터 단독 후보의 신뢰도 상한(기본 0.65)
# -필드: scoring      = 점수 결합 방식(기본 "legacy")
#                        "legacy" — 종전과 동일(신호 중 max, body 1건이면 히트).
#                                   재설계 14-2 의 0단계 baseline 측정용으로 남긴다
#                        "staged" — 재설계 방식(noisy-OR + 본문 격하 + 단독 채택 금지)
#                        [왜 스위치인가] 재설계 14-1 은 점수 재조정이 옵트인이
#                        될 수 없다고 못박았지만, 같은 14-2 는 "병행 실행 기간에
#                        차이를 측정한 뒤 전환"을 요구한다. 두 방식을 같은
#                        빌드에서 번갈아 돌릴 수 있어야 그 측정이 성립한다.
#------------------------------------------------------------------
SCORING_MODES = ("legacy", "staged")


@dataclass(frozen=True)
class Defaults:
    head_chars: int = 400
    min_distinct: int = 1
    min_count: int = 1
    t_high: float = 0.70
    t_low: float = 0.30
    t_seed: float = 0.85
    embed_cap: float = 0.65
    scoring: str = "legacy"


#------------------------------------------------------------------
# 임베딩 전파 설정 블록 (doc_rule.yaml 의 embed:)
#=> 2단계(seed 벡터 비교)의 임계값을 정책 파일에서 조정할 수 있게 한다.
#   지금까지는 propagate.py 의 모듈 상수로 박혀 있어 재빌드 없이는 못 바꿨다.
#
# -필드: enabled       = False 면 2단계를 아예 돌리지 않는다(기본 False — 옵트인)
# -필드: k             = kNN 투표인단 크기(기본 10)
# -필드: dup_threshold = 이 유사도 이상이면 사본으로 보고 라벨 전체 상속(기본 0.95)
# -필드: min_sim       = 최근접이 이보다 멀면 보류(기본 0.55)
# -필드: min_share     = 라벨별 득표율 채택 임계(기본 0.25)
# -필드: prototype     = 프로토타입 벡터 사용 범위("off"|"l1_only"|"all", 기본 "off")
#------------------------------------------------------------------
@dataclass(frozen=True)
class EmbedSpec:
    enabled: bool = False
    k: int = 10
    dup_threshold: float = 0.950
    min_sim: float = 0.550
    min_share: float = 0.250
    prototype: str = "off"

    #------------------------------------------------------------------
    # propagate_doctype() 에 넘길 인자로
    #=> 이 값들이 정책 파일에 있는데도 여태 아무 데서도 쓰이지 않았다.
    #   전파 쪽 함수 시그니처와 이름이 같으므로 그대로 풀어 넘기면 된다.
    #   prototype·enabled 는 전파 함수의 인자가 아니라서 뺀다.
    #
    # -in: 없음
    #
    # -out: dict = {k, dup_threshold, min_sim, min_share}
    # -out: error = 없음
    #------------------------------------------------------------------
    def kwargs(self):
        return {"k": self.k, "dup_threshold": self.dup_threshold,
                "min_sim": self.min_sim, "min_share": self.min_share}


#------------------------------------------------------------------
# 업무 분류 규칙 1개
#=> doc_rule.yaml 의 doctype_rules 항목 하나. cso_rules.yaml 의 KeywordRule 과
#   문법은 거의 같지만(terms·exclude·weight), grade 대신 node(dc_id)를 갖는다
#   — 이 한 줄 차이가 security 축과 doctype 축을 가른다(설계서 5-1 note).
#
# -필드: id       = 규칙 식별자(예: "dt_contract"). doc_taxonomy.yaml 과 무관하게
#                   관리자가 임의로 붙인다
# -필드: node     = 대응하는 분류체계 dc_id(예: "DC_006_001"). doc_taxonomy.yaml
#                   에 있는 값이어야 한다(T5) — 이 규칙이 doc_taxonomy.yaml 을
#                   참조하는 유일한 연결점이다(설계서 5-1-1)
# -필드: weight   = 신뢰도 가중("high"|"medium"|"low")
# -필드: title_terms = 문서 '첫 비어있지 않은 줄'에서만 찾을 문서종류어(재설계 6-3).
#                   가장 강한 증거다. 실측에서 첫 줄이 60자 이하인 문서가 91%,
#                   hwp/hwpx/doc/docx/pptx 는 100% 였다. 단 ppt/xls/xlsx 는
#                   첫 줄이 제목이 아니어서 스캔 쪽이 자동으로 건너뛴다
# -필드: head_terms = 앞 head_chars 자(표제부)에서만 찾을 문서종류어(재설계 6-2)
# -필드: head_chars = 이 규칙의 표제부 범위. None 이면 defaults.head_chars
# -필드: form     = FormSpec | None. 서식 필드어 세트(재설계 7장)
# -필드: structure = 구조 신호 정규식 목록(예: r"제\d+조"). 보조 전용이라
#                   단독으로는 후보를 만들지 못한다
# -필드: terms    = 내용 키워드 목록. [재설계 8-2] '약한 증거'로 격하됐다 —
#                   문서 전체에서 찾되 단독으로는 후보를 만들지 못한다
# -필드: min_distinct = terms 중 서로 다른 단어 최소 종수. None 이면 defaults
# -필드: min_count = terms 총 등장 건수 하한. None 이면 defaults
# -필드: exclude  = 오탐 제외어 목록
# -필드: filename = 파일명 신호 목록(선택)
# -필드: paths    = 경로 신호 목록(선택). load_doc_rules 가 '/' 정규화 + 소문자화해
#                   둔다(rules.py 의 PathRule.matches 와 같은 규약)
# -필드: active   = load_doc_rules 가 채운다. node 가 doc_taxonomy.yaml 에서
#                   status=0(미사용)이면 False — 매칭을 시도하지 않는다(T6).
#                   taxonomy 없이 로드했으면 항상 True(교차검증을 안 했으므로
#                   비활성 여부를 판단할 수 없다)
#------------------------------------------------------------------
@dataclass(frozen=True)
class DoctypeRule:
    id: str
    node: str
    weight: str = "medium"
    title_terms: tuple = ()
    head_terms: tuple = ()
    head_chars: int = None
    form: FormSpec = None
    structure: tuple = ()
    terms: tuple = ()
    min_distinct: int = None
    min_count: int = None
    exclude: tuple = ()
    filename: tuple = ()
    paths: tuple = ()
    active: bool = True


#------------------------------------------------------------------
# doc_rule.yaml 전체
#=> 로드된 doc_rule.yaml 을 통째로 담는 컨테이너. cso_rules.yaml 의 RuleSet 과
#   같은 역할을 doctype 축에서 한다.
#
# -필드: version  = 규칙셋 버전 문자열(감사용). cso_rules.yaml 의 version: 과 같은
#                   문법 — doc_rule.yaml 최상위에 적으면 결과 레코드의
#                   doctype_rule_version 으로 그대로 나간다(설계서 7-1). 생략하면
#                   "unknown"(rules.py 의 RuleSet.version 기본값과 동일 규약)
# -필드: conflict = ConflictSpec(축 전체 충돌 전략)
# -필드: rules    = DoctypeRule 튜플(파일에 적힌 순서 그대로, 비활성 규칙도 포함)
# -필드: defaults = Defaults(전역 기본값 블록). 규칙에 같은 필드가 있으면 그쪽이 이긴다
# -필드: embed    = EmbedSpec(2단계 임베딩 전파 설정)
# -필드: warnings = 로드 중 발견한 경고 메시지 목록(T6·T12 등 — 로드를 막지
#                   않는 문제). 종료 여부는 호출자(cli.py)가 정책으로 정한다
#------------------------------------------------------------------
@dataclass(frozen=True)
class DocRuleSet:
    conflict: ConflictSpec
    version: str = "unknown"
    rules: tuple = ()
    warnings: tuple = ()
    defaults: Defaults = Defaults()
    embed: EmbedSpec = EmbedSpec()

    #------------------------------------------------------------------
    # 실제로 매칭에 쓸 규칙만
    #=> active=False(T6, 미사용 노드 참조)인 규칙을 뺀 목록. 스캔 엔진은 이
    #   목록만 순회하면 된다 — "규칙은 있되 매칭은 안 한다"를 매번 if 로
    #   따로 챙기지 않아도 되게 한다.
    #
    # -in: 없음
    #
    # -out: tuple[DoctypeRule] = active=True 인 규칙만
    # -out: error = 없음
    #------------------------------------------------------------------
    @property
    def active_rules(self):
        return tuple(r for r in self.rules if r.active)


#------------------------------------------------------------------
# 값 하나를 사람이 읽는 문자열로
#=> 검증 메시지에서 None/빈문자열도 눈에 보이게 표시한다(axes.py 의 _show 와
#   동일 목적, 모듈을 독립적으로 두기 위해 각자 갖는다).
#
# -in: value = 임의 값
#
# -out: str = repr 문자열(None 은 "(없음)")
# -out: error = 없음
#------------------------------------------------------------------
def _show(value):
    return "(없음)" if value is None else repr(value)


#------------------------------------------------------------------
# doc_rule.yaml 위반 1건 만들기
#=> rules.py 의 _violation, axes.py 의 _tv 와 같은 역할.
#
# -in: code    = 위반 코드("T0"|"T2"|"T5"|"T10")
# -in: rule_id = 문제 규칙의 id(구조 오류처럼 규칙을 특정할 수 없으면 None)
# -in: field   = 문제 필드명(없으면 "")
# -in: value   = 문제 값(원본 그대로 보존)
# -in: detail  = 사람이 읽는 설명 한 줄
#
# -out: dict = {code, rule_id, field, value, detail}
# -out: error = 없음
#------------------------------------------------------------------
def _dv(code, rule_id, field, value, detail):
    return {"code": code, "rule_id": rule_id, "field": field, "value": value, "detail": detail}


#------------------------------------------------------------------
# conflict: 값 검사(T2·T10)
#=> doctype 축(kind: taxonomy)은 서열이 없어 max 를 쓸 수 없다(3-1). 이 함수는
#   "무엇을 적어도 되는지"가 아니라 "무엇이 틀렸는지"만 판단한다 — 정상이면
#   None, 문제가 있으면 위반 dict 하나를 돌려준다.
#    1) 생략 또는 "all" → 정상(기본값)
#    2) 문자열 "top_n"(n 없이) → n 이 필수인데 없음(T10)
#    3) 그 밖의 문자열(예: "max"·"min"·오타) → 정의되지 않은 값(T2)
#    4) 매핑이면 strategy 가 all/top_n 인지, top_n 이면 n 이 1 이상 정수인지 확인
#    5) 그 밖의 타입(숫자·목록 등) → T2
#
# -in: raw = data.get("conflict") 원본 값(생략 시 호출자가 "all" 을 넘겨준다)
#
# -out: dict | None = 위반이 있으면 위반 dict, 없으면 None
# -out: error = 없음
#------------------------------------------------------------------
def _check_conflict(raw):
    if raw is None or raw == "all":
        return None
    if isinstance(raw, str):
        if raw == "top_n":
            return _dv("T10", None, "conflict", raw, "top_n 은 n 값 없이 쓸 수 없습니다")
        return _dv("T2", None, "conflict", raw,
                   f"conflict 값이 올바르지 않습니다(taxonomy 축엔 서열이 없어 "
                   f"max 는 쓸 수 없습니다) — {' 또는 '.join(CONFLICT_STRATEGIES)} 중 하나여야 합니다")
    if isinstance(raw, dict):
        strategy = raw.get("strategy")
        if strategy not in CONFLICT_STRATEGIES:
            return _dv("T2", None, "conflict.strategy", strategy,
                       f"conflict.strategy 는 {' 또는 '.join(CONFLICT_STRATEGIES)} 중 하나여야 합니다")
        if strategy == "top_n":
            n = raw.get("n")
            if not isinstance(n, int) or isinstance(n, bool) or n < 1:
                return _dv("T10", None, "conflict.n", n,
                           "top_n 은 n 이 1 이상의 정수여야 합니다")
        return None
    return _dv("T2", None, "conflict", raw, "conflict 값의 형식이 올바르지 않습니다")


#------------------------------------------------------------------
# conflict: 값 파싱(검증 통과 후)
#=> _check_conflict 가 None(위반 없음)을 돌려준 원본만 이 함수에 들어온다는
#   전제로, ConflictSpec 을 만든다.
#
# -in: raw = data.get("conflict") 원본 값(검증 통과분)
#
# -out: ConflictSpec
# -out: error = 없음
#------------------------------------------------------------------
def _parse_conflict(raw):
    if raw is None or raw == "all":
        return ConflictSpec(strategy="all")
    if isinstance(raw, dict):
        return ConflictSpec(
            strategy=raw.get("strategy", "all"),
            n=raw.get("n"),
            min_confidence=raw.get("min_confidence"),
        )
    return ConflictSpec(strategy=raw)


#------------------------------------------------------------------
# defaults: 블록 검사·파싱 (T20)
#=> 숫자여야 할 자리에 문자열이 오거나 범위를 벗어난 값이 오면, 스캔 단계에서
#   조용히 이상하게 동작하는 대신 로드 시점에 막는다. 임계값 하나가 잘못되면
#   전체 분류 결과가 통째로 틀어지기 때문이다.
#
# -in: raw = data.get("defaults") 원본(없으면 None)
#
# -out: (Defaults, violations) = 파싱 결과와 위반 목록
# -out: error = 없음(문제는 violations 로 돌려준다)
#------------------------------------------------------------------
def _parse_defaults(raw):
    d = Defaults()
    if raw is None:
        return d, []
    if not isinstance(raw, dict):
        return d, [_dv("T20", None, "defaults", raw, "defaults 는 매핑(mapping)이어야 합니다")]

    violations = []
    vals = {}
    # (필드, 형, 최소, 최대) — 최대가 None 이면 상한 없음.
    spec = [
        ("head_chars", int, 1, None),
        ("min_distinct", int, 1, None),
        ("min_count", int, 1, None),
        ("t_high", float, 0.0, 1.0),
        ("t_low", float, 0.0, 1.0),
        ("t_seed", float, 0.0, 1.0),
        ("embed_cap", float, 0.0, 1.0),
    ]
    for name, typ, lo, hi in spec:
        if name not in raw:
            continue
        v = raw[name]
        # bool 은 int 의 하위형이라 그냥 두면 True 가 1 로 통과한다 — 명시적으로 막는다.
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            violations.append(_dv("T20", None, f"defaults.{name}", v, "숫자여야 합니다"))
            continue
        if typ is int and not float(v).is_integer():
            violations.append(_dv("T20", None, f"defaults.{name}", v, "정수여야 합니다"))
            continue
        if v < lo or (hi is not None and v > hi):
            rng = (f"{lo} 이상 {hi} 이하" if hi is not None else f"{lo} 이상")
            violations.append(_dv("T20", None, f"defaults.{name}", v, f"{rng}여야 합니다"))
            continue
        vals[name] = typ(v)

    if "scoring" in raw:
        mode = raw["scoring"]
        if mode not in SCORING_MODES:
            violations.append(_dv("T20", None, "defaults.scoring", mode,
                                  f"{' | '.join(SCORING_MODES)} 중 하나여야 합니다"))
        else:
            vals["scoring"] = mode

    d = Defaults(**{**d.__dict__, **vals})
    # 임계값의 대소 관계가 뒤집히면 "약한 후보가 확정 후보보다 세다" 같은
    # 모순이 생긴다. 값 하나하나가 정상이어도 조합이 틀리면 막는다.
    if d.t_low > d.t_high:
        violations.append(_dv("T20", None, "defaults.t_low", d.t_low,
                              f"t_low 는 t_high({d.t_high}) 이하여야 합니다"))
    if d.t_high > d.t_seed:
        violations.append(_dv("T20", None, "defaults.t_seed", d.t_seed,
                              f"t_seed 는 t_high({d.t_high}) 이상이어야 합니다"))
    return d, violations


#------------------------------------------------------------------
# embed: 블록 검사·파싱 (T20)
#=> 2단계 임계값. defaults 와 같은 원칙으로 로드 시점에 막는다.
#
# -in: raw = data.get("embed") 원본(없으면 None)
#
# -out: (EmbedSpec, violations)
# -out: error = 없음
#------------------------------------------------------------------
def _parse_embed(raw):
    e = EmbedSpec()
    if raw is None:
        return e, []
    if not isinstance(raw, dict):
        return e, [_dv("T20", None, "embed", raw, "embed 는 매핑(mapping)이어야 합니다")]

    violations = []
    vals = {}
    if "enabled" in raw:
        if not isinstance(raw["enabled"], bool):
            violations.append(_dv("T20", None, "embed.enabled", raw["enabled"],
                                  "true 또는 false 여야 합니다"))
        else:
            vals["enabled"] = raw["enabled"]
    if "k" in raw:
        k = raw["k"]
        if isinstance(k, bool) or not isinstance(k, int) or k < 1:
            violations.append(_dv("T20", None, "embed.k", k, "1 이상의 정수여야 합니다"))
        else:
            vals["k"] = k
    for name in ("dup_threshold", "min_sim", "min_share"):
        if name not in raw:
            continue
        v = raw[name]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0.0 <= v <= 1.0):
            violations.append(_dv("T20", None, f"embed.{name}", v, "0.0~1.0 사이 숫자여야 합니다"))
        else:
            vals[name] = float(v)
    if "prototype" in raw:
        p = raw["prototype"]
        if p not in ("off", "l1_only", "all"):
            violations.append(_dv("T20", None, "embed.prototype", p,
                                  "off | l1_only | all 중 하나여야 합니다"))
        else:
            vals["prototype"] = p

    return EmbedSpec(**{**e.__dict__, **vals}), violations


#------------------------------------------------------------------
# form: 블록 → FormSpec
#=> 검증(_check_rule_fields)을 통과한 원본만 들어온다는 전제로 변환한다.
#   조건이 하나도 없는 빈 블록은 None 으로 접는다 — 빈 껍데기를 그대로 두면
#   스캔 쪽에서 "조건 0개를 모두 만족"으로 오해할 여지가 생긴다.
#
# -in: raw = item.get("form") 원본(없으면 None)
#
# -out: FormSpec | None
# -out: error = 없음
#------------------------------------------------------------------
def _parse_form(raw):
    if not isinstance(raw, dict):
        return None
    spec = FormSpec(
        all_of=tuple(raw.get("all_of") or []),
        any_of=tuple(raw.get("any_of") or []),
        min_types=int(raw.get("min_types") or 0),
    )
    return spec if spec.usable else None


#------------------------------------------------------------------
# 규칙 1개의 신규 필드 검사 (T21·T22)
#=> form 블록의 형식(T21)과 structure 정규식이 실제로 컴파일되는지(T22)를 본다.
#   정규식 오타는 스캔이 시작돼야 터지는데, 그때는 어느 규칙이 범인인지
#   알기 어렵다 — 로드 시점에 규칙 id 와 함께 잡아 준다.
#
# -in: item = doctype_rules 항목 dict(구조 검증 통과분)
#
# -out: violations = 위반 dict 리스트
# -out: error = 없음
#------------------------------------------------------------------
def _check_rule_fields(item):
    violations = []
    rid = item.get("id")

    form = item.get("form")
    if form is not None:
        if not isinstance(form, dict):
            violations.append(_dv("T21", rid, "form", form, "form 은 매핑(mapping)이어야 합니다"))
        else:
            for key in ("all_of", "any_of"):
                if key in form and not isinstance(form[key], list):
                    violations.append(_dv("T21", rid, f"form.{key}", form[key],
                                          "목록(list)이어야 합니다"))
            mt = form.get("min_types")
            if mt is not None and (isinstance(mt, bool) or not isinstance(mt, int) or mt < 1):
                violations.append(_dv("T21", rid, "form.min_types", mt,
                                      "1 이상의 정수여야 합니다"))
            # any_of 보다 큰 min_types 는 절대 성립하지 않는다 — 조용히 죽은
            # 규칙이 되므로 미리 알린다.
            anyof = form.get("any_of") or []
            if isinstance(anyof, list) and isinstance(mt, int) and not isinstance(mt, bool):
                if mt > len(anyof):
                    violations.append(_dv("T21", rid, "form.min_types", mt,
                                          f"any_of 항목 수({len(anyof)})보다 클 수 없습니다 "
                                          f"— 이 조건은 절대 성립하지 않습니다"))

    for pat in (item.get("structure") or []):
        try:
            re.compile(pat)
        except re.error as exc:
            violations.append(_dv("T22", rid, "structure", pat,
                                  f"정규식으로 해석할 수 없습니다: {exc}"))

    for name in ("min_distinct", "min_count", "head_chars"):
        v = item.get(name)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            violations.append(_dv("T20", rid, name, v, "1 이상의 정수여야 합니다"))

    return violations


#------------------------------------------------------------------
# doc_rule.yaml 원본(YAML) 구조 검증 — T0·T2·T10
#=> load_doc_rules 가 파싱한 원시 dict 를 훑어, 파일 자체(doc_taxonomy.yaml 을
#   보지 않고 판단 가능한) 위반을 전부 모은다. rules.py 의 validate_rules_data
#   와 같은 원칙 — 첫 오류에서 멈추지 않는다.
#    1) conflict: 값 검사(T2·T10)
#    2) doctype_rules 가 목록인지(아니면 T0, 더 못 감)
#    3) 항목별로 매핑인지·id/node 가 유효한 문자열인지·id 가 중복되지 않는지(T0)
#
#   [taxonomy 교차검증은 여기 없다] node 가 실제로 doc_taxonomy.yaml 에 있는지
#   (T5)는 이 함수의 책임이 아니다 — Taxonomy 객체가 있어야 판단 가능하므로
#   load_doc_rules 가 taxonomy 인자를 받았을 때만 별도로 검사한다.
#
# -in: data = yaml.safe_load 결과 dict
#
# -out: violations = 위반 dict 리스트(문제 없으면 빈 리스트)
# -out: error = 없음(예외를 던지지 않는다 — 판단은 호출자 몫)
#------------------------------------------------------------------
def validate_doc_rule_data(data):
    violations = []
    data = data or {}

    v = _check_conflict(data.get("conflict"))
    if v:
        violations.append(v)

    # 전역 블록(defaults/embed)은 규칙보다 먼저 본다 — 여기가 틀리면 모든
    # 규칙의 판정이 함께 틀어지므로 규칙 오류보다 먼저 보여 주는 게 낫다.
    violations += _parse_defaults(data.get("defaults"))[1]
    violations += _parse_embed(data.get("embed"))[1]

    raw_rules = data.get("doctype_rules")
    if raw_rules is None:
        raw_rules = []
    if not isinstance(raw_rules, list):
        violations.append(_dv("T0", None, "doctype_rules", raw_rules,
                              "'doctype_rules' 가 목록(list)이 아닙니다"))
        return violations

    seen_ids = set()
    for idx, item in enumerate(raw_rules):
        if not isinstance(item, dict):
            violations.append(_dv("T0", None, f"doctype_rules[{idx}]", item,
                                  "항목이 매핑(mapping)이 아닙니다"))
            continue
        rule_id = item.get("id")
        if not rule_id or not isinstance(rule_id, str):
            violations.append(_dv("T0", None, f"doctype_rules[{idx}].id", rule_id,
                                  "id 가 비어 있거나 문자열이 아닙니다"))
            continue
        if rule_id in seen_ids:
            violations.append(_dv("T0", rule_id, "id", rule_id, "id 가 중복됩니다"))
        seen_ids.add(rule_id)

        node = item.get("node")
        if not node or not isinstance(node, str):
            violations.append(_dv("T0", rule_id, "node", node,
                                  "node 가 비어 있거나 문자열이 아닙니다"))

        violations += _check_rule_fields(item)

    return violations


#------------------------------------------------------------------
# node 참조 교차검증 — T5(스냅샷에 없는 노드)
#=> doc_taxonomy.yaml(Taxonomy)이 있을 때만 부를 수 있다. validate_doc_rule_data
#   를 통과한(id·node 가 유효한 문자열인) 항목만 들어온다는 전제.
#
# -in: raw_rules = data["doctype_rules"](구조 검증 통과분)
# -in: taxonomy  = axes.Taxonomy
#
# -out: violations = T5 위반 dict 리스트(문제 없으면 빈 리스트)
# -out: error = 없음
#------------------------------------------------------------------
def _cross_validate_nodes(raw_rules, taxonomy):
    violations = []
    for item in raw_rules:
        node = item["node"]
        if taxonomy.get(node) is None:
            violations.append(_dv("T5", item["id"], "node", node,
                                  "분류체계 스냅샷(doc_taxonomy.yaml)에 없는 dc_id 입니다 "
                                  "— 노드가 삭제됐거나 스냅샷이 낡았을 수 있습니다"))
    return violations


#------------------------------------------------------------------
# 위반 목록 → 사람이 읽는 보고문
#=> 검증 코드별로 묶어 한 화면에 정리한다(rules.py 의 format_violations,
#   axes.py 의 format_taxonomy_violations 와 같은 역할).
#
# -in: path       = doc_rule.yaml 파일 경로
# -in: violations = validate_doc_rule_data(+_cross_validate_nodes) 결과
#
# -out: text = 여러 줄 문자열(끝에 개행 없음)
# -out: error = 없음
#------------------------------------------------------------------
def format_doc_rule_violations(path, violations):
    titles = {
        "T0": "[T0] doc_rule.yaml 구조 오류",
        "T2": "[T2] conflict 값이 올바르지 않음",
        "T5": "[T5] 분류체계 스냅샷에 없는 노드 참조",
        "T10": "[T10] top_n 설정이 올바르지 않음",
        "T20": "[T20] 임계값·수치 설정이 올바르지 않음",
        "T21": "[T21] form(서식 필드어) 형식 오류",
        "T22": "[T22] structure 정규식 오류",
    }
    lines = [
        f"[규칙셋 오류] {path} — 검증 실패 {len(violations)}건. doctype 축을 로드하지 않았습니다.",
        "",
    ]
    for code in ("T0", "T2", "T5", "T10", "T20", "T21", "T22"):
        group = [v for v in violations if v["code"] == code]
        if not group:
            continue
        lines.append(f"  {titles.get(code, '[' + code + ']')}")
        for v in group:
            where = f"id={v['rule_id']}" if v.get("rule_id") else "(구조)"
            if v["field"]:
                where += f".{v['field']}"
            lines.append(f"    {where} = {_show(v['value'])}  — {v['detail']}")
        lines.append("")
    lines.append(f"  고치는 법: {path} 를 열어 위 항목을 고치세요.")
    if any(v["code"] == "T2" for v in violations):
        lines.append(f"    · conflict 는 {' 또는 '.join(CONFLICT_STRATEGIES)} 중 하나여야 합니다"
                     f"(예: conflict: all, conflict: {{strategy: top_n, n: 3}}).")
    if any(v["code"] == "T5" for v in violations):
        lines.append("    · node 는 doc_taxonomy.yaml 의 dc_id 여야 합니다. "
                     "scripts/export_taxonomy.py 로 스냅샷을 다시 내보내 확인하세요.")
    return "\n".join(lines)


#------------------------------------------------------------------
# doc_rule.yaml 위반 예외
#=> doc_rule.yaml 을 읽는 데는 성공했지만 내용이 규칙에 맞지 않을 때 던진다.
#   cso_rules.yaml 의 RuleSetValidationError, doc_taxonomy.yaml 의
#   TaxonomyValidationError 와 같은 역할이다.
#
# -필드: path       = 문제의 doc_rule.yaml 경로
# -필드: violations = 위반 목록. 각 항목은 dict {code, rule_id, field, value, detail}
#------------------------------------------------------------------
class DocRuleValidationError(Exception):

    #------------------------------------------------------------------
    # 예외 생성
    #=> 파일 경로와 위반 목록을 담고, 사람이 읽을 요약문을 메시지로 만든다.
    #
    # -in: path       = doc_rule.yaml 경로
    # -in: violations = 위반 dict 리스트(빈 리스트로는 만들지 않는다)
    #
    # -out: 없음(생성자)
    # -out: error = 없음
    #------------------------------------------------------------------
    def __init__(self, path, violations):
        self.path = path
        self.violations = list(violations)
        super().__init__(format_doc_rule_violations(path, self.violations))


#------------------------------------------------------------------
# doc_rule.yaml 기본 경로
#=> cso_rules.yaml 의 default_rules_path(), doc_taxonomy.yaml 의
#   default_taxonomy_path() 와 완전히 같은 규약("exe 옆 외장 파일").
#    1) 환경변수 CSOCLASSIFY_POLICY_DIR 이 있으면 그 폴더
#    2) exe(PyInstaller) 로 얼린 실행이면 'exe 옆'
#    3) 소스(개발) 실행이면 저장소 트리의 resources/policy/
#
# -in: 없음
#
# -out: path = doc_rule.yaml 기본 경로
# -out: error = 없음
#------------------------------------------------------------------
def default_doc_rules_path():
    env = os.environ.get("CSOCLASSIFY_POLICY_DIR")
    if env:
        return os.path.join(env, DOC_RULE_FILENAME)
    if getattr(sys, "frozen", False):
        return os.path.join(exe_dir(), DOC_RULE_FILENAME)
    return resource_path("policy", DOC_RULE_FILENAME)


#------------------------------------------------------------------
# 규칙 없는 빈 규칙셋 — "seed 전파 전용" 모드용
#=> doc_rule.yaml 이 없을 때 쓰는 '규칙 0건짜리 정상 규칙셋'이다. 이걸 쓰면
#   doctype 축이 꺼지지 않고 켜진 채로 돌되, 키워드·파일명 규칙이 하나도
#   없으니 1차 스캔에서는 아무것도 못 맞힌다. 그 상태로 class_seed.jsonl 과의
#   임베딩 전파가 라벨을 채운다(seed 도 없으면 그냥 미분류로 남고, 사람이
#   나중에 분류한다).
#   [왜 None 이 아니라 빈 규칙셋인가] 축을 None 으로 꺼 버리면 레코드에
#   labels.doctype 키 자체가 안 생기고, 그러면 전파 단계가 "이 배포는 축을
#   안 쓴다"고 보고 건너뛴다(engine.propagate_doctype_records). 빈 규칙셋으로
#   켜 두어야 '규칙은 없지만 축은 쓴다'가 표현된다.
#
# -in: 없음
#
# -out: DocRuleSet = conflict=all(기본) · version="none" · 규칙 0건
# -out: error = 없음
#------------------------------------------------------------------
def seed_only_ruleset():
    # version 을 "none" 으로 박아 두면 결과 레코드의 doctype_rule_version 만 보고도
    # "이 배포는 규칙 없이 전파로만 돌았다"를 나중에 구분할 수 있다.
    return DocRuleSet(conflict=ConflictSpec(), version="none", rules=(), warnings=())


#------------------------------------------------------------------
# doc_rule.yaml 로드(YAML → DocRuleSet)
#=> 경로를 안 주면 default_doc_rules_path(). 이 축은 "있으면 켜지고 없으면
#   꺼지는 외장 자산"이므로(설계서 4-6·T16), 파일이 없을 때 어떻게 할지는
#   이 함수가 정하지 않는다 — FileNotFoundError 를 던질 뿐이고, 그것을
#   "치명적 오류"로 볼지 "doctype 축만 끄고 계속"으로 볼지는 호출자(cli.py)
#   의 정책이다(--axis doctype 명시 여부에 따라 달라짐, T16).
#    1) 파일이 없으면 안내와 함께 FileNotFoundError
#    2) YAML 파싱 → 구조 검증(T0·T2·T10). taxonomy 를 줬으면 노드 참조 검증도
#       더해(T5) 하나라도 위반이면 DocRuleSet 을 만들지 않고 실패
#    3) conflict: 파싱, DoctypeRule 목록 구성
#    4) taxonomy 가 있으면 규칙마다 미사용 노드 참조(T6)를 찾아 active=False
#       로 표시하고 경고를 남기며, 어떤 규칙도 참조하지 않는 사용 노드(T12)도
#       경고로 모은다. taxonomy 가 없으면(D1 이 아직 안 붙었거나 스냅샷이
#       없는 배포) 이 교차검증들은 전부 건너뛴다 — 판단할 근거가 없어서다
#
# -in: path     = doc_rule.yaml 경로(없으면 기본 경로)
# -in: taxonomy = axes.Taxonomy(있으면 T5·T6·T12 교차검증을 함께 수행).
#                 없으면(None, 기본) node 값을 그대로 믿고 로드한다
# -in: validate = False 면 검증을 건너뛴다. 검증기 자체를 시험하거나 잘못된
#                 규칙셋을 일부러 읽어야 하는 도구용 탈출구다(기본 True)
#
# -out: DocRuleSet
# -out: error = 파일 없음 시 FileNotFoundError(어디에 두면 되는지 안내 포함)
# -out: error = 구조·conflict·(taxonomy 줬으면)노드 참조 검증 실패 시
#               DocRuleValidationError(위반 전체 목록 포함)
#------------------------------------------------------------------
def load_doc_rules(path=None, taxonomy=None, validate=True):
    path = path or default_doc_rules_path()
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"업무분류 규칙셋({DOC_RULE_FILENAME})을 찾을 수 없습니다: {path}\n"
            f"  · exe 와 같은 폴더에 {DOC_RULE_FILENAME} 을 두거나,\n"
            f"  · --doc-rules <파일경로> 로 지정하거나,\n"
            f"  · 환경변수 CSOCLASSIFY_POLICY_DIR 로 폴더를 지정하거나,\n"
            f"  · scripts/export_taxonomy.py --scaffold-doc-rule 로 골격을 만든 뒤 채우세요.\n"
            f"  (이 파일이 없어도 security 축 분류는 그대로 동작합니다.)"
        )
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    raw_rules = data.get("doctype_rules") or []

    if validate:
        violations = validate_doc_rule_data(data)
        if not violations and taxonomy is not None:
            violations = violations + _cross_validate_nodes(raw_rules, taxonomy)
        if violations:
            raise DocRuleValidationError(path, violations)

    conflict = _parse_conflict(data.get("conflict"))
    # 검증을 이미 통과했으므로 여기서 나오는 위반 목록은 버린다(값만 필요).
    defaults, _ = _parse_defaults(data.get("defaults"))
    embed, _ = _parse_embed(data.get("embed"))

    warnings = []
    rules = []
    referenced = set()
    for item in raw_rules:
        node = item["node"]
        active = True
        if taxonomy is not None:
            tnode = taxonomy.get(node)
            if tnode is not None:
                referenced.add(node)
                if not tnode.active:
                    active = False
                    warnings.append(
                        f"[T6] doctype_rules[{item['id']}].node={node!r} "
                        f"({taxonomy.path(node)}) 는 미사용(status=0) 노드입니다 — "
                        f"이 규칙은 비활성 처리됩니다"
                    )
        rules.append(DoctypeRule(
            id=item["id"],
            node=node,
            weight=item.get("weight", "medium"),
            title_terms=tuple(item.get("title_terms") or []),
            head_terms=tuple(item.get("head_terms") or []),
            head_chars=item.get("head_chars"),
            form=_parse_form(item.get("form")),
            structure=tuple(item.get("structure") or []),
            terms=tuple(item.get("terms") or []),
            min_distinct=item.get("min_distinct"),
            min_count=item.get("min_count"),
            exclude=tuple(item.get("exclude") or []),
            filename=tuple(item.get("filename") or []),
            # 경로 조각은 로드 시점에 정규화해 둔다(구분자 '/' 통일 + 소문자) —
            # rules.py 의 PathRule 과 같은 규약. 매 스캔마다 다시 정규화하지 않아도
            # 되고, 스캔 쪽 코드가 규칙 파일 표기(백슬래시 등)를 몰라도 되게 한다.
            paths=tuple(p.replace("\\", "/").lower() for p in (item.get("paths") or [])),
            active=active,
        ))

    if taxonomy is not None:
        for node in taxonomy.active_nodes:
            if node.dc_id not in referenced:
                warnings.append(
                    f"[T12] {node.dc_id}({taxonomy.path(node.dc_id)}) 를 참조하는 규칙이 "
                    f"없습니다 — 이 분류로는 자동분류되는 문서가 없습니다"
                )

    if not rules:
        warnings.append("doctype_rules 가 비어 있어 doctype 축이 아무 문서도 분류하지 않습니다")

    return DocRuleSet(conflict=conflict, version=str(data.get("version", "unknown")),
                      rules=tuple(rules), warnings=tuple(warnings),
                      defaults=defaults, embed=embed)


# 새 doc_rule.yaml 의 머리 부분을 가져올 본보기 파일 이름(화면·CLI 공용 규약).
TEMPLATE_NAME = "doc_rule_template.yaml"

# 본보기에는 있지만 만들어진 doc_rule.yaml 에는 넣지 않는 섹션.
UI_ONLY_KEYS = ("new_rule",)

# 본보기가 없을 때 쓸 새 규칙 기본값(본보기의 new_rule 이 이긴다).
NEW_RULE_FALLBACK = {"weight": "medium"}

# 만들어진 doc_rule.yaml 맨 위에 남길 안내 두 줄(ui/docruleedit.py 와 같은 문구).
SCAFFOLD_HEADER = (
    "# 업무분류 판단 기준 — 화면(설정 ③ 판단 기준)이 저장할 때마다 다시 쓰는 파일이라\n"
    "# 주석이 남지 않는다. 각 값의 뜻과 그 값을 고른 이유는 doc_rule_template.yaml 에 있다.\n"
)


#------------------------------------------------------------------
# doc_rule.yaml 골격 생성(선택)
#=> "무엇이 계약서인가"는 사람만 안다(설계서 5장 서두) — 이 함수는 내용을
#   채우지 않는다. 활성 노드마다 id·node·빈 terms: [] 한 줄만 만들어 주고,
#   filename 만 title 을 최초 제안값으로 한 번 복사한다(설계서 5-1-1 note —
#   그 뒤로는 이 값이 doc_rule.yaml 을 다시 내보내도 따라 바뀌지 않는다).
#    1) 트리 순서(부모→자식, order 순)로 순회해 사람이 읽기 편하게 만든다
#    2) 미사용(status=0) 노드는 스캐폴드에서 뺀다 — 어차피 규칙을 걸어도
#       비활성 처리되므로(T6) 빈 골격만 늘려 놓을 이유가 없다
#    3) id 는 dc_id 를 소문자로 바꿔 만든다 — 사람이 나중에 원하는 이름으로
#       고칠 것을 전제로 한 시작값일 뿐이다
#
# -in: taxonomy = axes.Taxonomy(axes.export_from_mpower_json() 등으로 이미 만든 것)
# -in: template_path = 본보기(doc_rule_template.yaml) 경로. 안 주면 자동으로 찾는다
#
# -out: dict = {conflict: "all", doctype_rules: [...]}  — 그대로 yaml.safe_dump
#              할 수 있는 doc_rule.yaml 골격
# -out: error = 없음
#------------------------------------------------------------------
def build_scaffold(taxonomy, template_path=None):
    tpl_all = load_scaffold_template(template_path)
    new_rule = dict(tpl_all.get("new_rule") or NEW_RULE_FALLBACK)
    rules = []

    def walk(dc_id):
        for child in taxonomy.children_of(dc_id):
            if child.active:
                # title 자체가 문서종류어인 경우가 대부분이라, 표제부/첫줄
                # 신호의 최초 제안값으로 함께 채워 둔다(재설계 12-5).
                # form 은 사람만 채울 수 있어 빈 골격조차 만들지 않는다.
                # id·node 다음에 본보기의 new_rule 값(weight 등)을 얹고 그다음이
                # 말 목록이다(ui/docruleedit.py 와 같은 순서).
                rule = {"id": f"dt_{child.dc_id.lower()}", "node": child.dc_id}
                rule.update(new_rule)
                rule.update({
                    "title_terms": [child.title],
                    "head_terms": [child.title],
                    "terms": [],
                    "filename": [child.title],
                })
                rules.append(rule)
            walk(child.dc_id)

    walk(None)
    # 머리 부분(scoring·문턱·embed 등)은 본보기 파일에서 가져온다. 코드에
    # 박아 두면 회사마다 다른 기본값을 주려 할 때 프로그램을 고쳐야 하고,
    # 본보기가 없던 시절처럼 conflict 한 줄만 있는 파일이 만들어지면 엔진이
    # 조용히 기본값(legacy 채점)으로 돌아 화면에서 본 것과 판정이 달라진다.
    head = {k: v for k, v in tpl_all.items() if k not in UI_ONLY_KEYS}
    scaffold = dict(head)
    scaffold.setdefault("conflict", "all")
    scaffold["doctype_rules"] = rules
    return scaffold


#------------------------------------------------------------------
# 판단 기준 본보기(doc_rule_template.yaml) 읽기
#=> 새로 만드는 doc_rule.yaml 의 머리 부분(version·industry·conflict·defaults·
#   embed)을 어디서 가져올지 정한다. 찾는 순서는 '가까운 곳부터'다.
#    1) 인자로 준 경로
#    2) 만들려는 doc_rule.yaml 과 같은 폴더
#    3) 패키지가 들고 다니는 정책 폴더(resources/policy)
#   하나도 없으면 빈 dict — 본보기가 없다고 골격 생성이 실패하면 안 된다.
#
# -in: path     = 본보기 경로(없으면 아래 순서로 찾는다)
# -in: near     = 이 파일과 같은 폴더에서도 찾는다(보통 만들려는 doc_rule.yaml 경로)
#
# -out: dict = 본보기 최상위 값(doctype_rules 는 뺀다) · 못 찾으면 {}
# -out: error = 없음(읽기·파싱 실패는 {} 로 환원)
#------------------------------------------------------------------
def load_scaffold_template(path=None, near=None):
    cands = []
    if path:
        cands.append(path)
    if near:
        cands.append(os.path.join(os.path.dirname(os.path.abspath(near)),
                                  TEMPLATE_NAME))
    # 마지막 후보는 '이 배포가 정책 파일을 두는 곳' — doc_rule.yaml 을 찾는
    # 규칙(default_doc_rules_path)과 같은 자리에서 본보기도 찾는다.
    try:
        cands.append(os.path.join(os.path.dirname(default_doc_rules_path()),
                                  TEMPLATE_NAME))
    except Exception:       # noqa: BLE001  (자원 경로를 못 찾아도 골격은 만들어야 한다)
        pass
    for c in cands:
        if c and os.path.isfile(c):
            try:
                with open(c, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except (OSError, yaml.YAMLError):
                continue
            data.pop("doctype_rules", None)
            return data
    return {}


#------------------------------------------------------------------
# doc_rule.yaml 골격을 파일로 저장(이미 있으면 건너뜀)
#=> 사람이 이미 채워 둔 doc_rule.yaml 을 실수로 빈 골격으로 덮어쓰지 않도록,
#   기본은 파일이 있으면 아무 것도 안 하고 조용히 알린다(force=True 로만 덮어쓸 수 있음).
#
# -in: taxonomy = axes.Taxonomy
# -in: out_path = 저장할 doc_rule.yaml 경로
# -in: force    = True 면 이미 있어도 덮어쓴다(기본 False)
# -in: template_path = 본보기 경로(안 주면 out_path 옆 → resources/policy 순으로 찾는다)
#
# -out: dict | None = 실제로 썼으면 build_scaffold() 결과, 건너뛰었으면 None
# -out: error = 디렉터리가 없으면 만들고 재시도. 그래도 쓰기 실패하면 OSError 전파
#------------------------------------------------------------------
def write_scaffold(taxonomy, out_path, force=False, template_path=None):
    if os.path.isfile(out_path) and not force:
        return None
    scaffold = {k: v for k, v
                in load_scaffold_template(template_path, near=out_path).items()
                if k not in UI_ONLY_KEYS}
    scaffold.pop("doctype_rules", None)
    body = build_scaffold(taxonomy, template_path=template_path)
    scaffold.setdefault("conflict", "all")
    for k, v in body.items():
        scaffold.setdefault(k, v)
    scaffold["doctype_rules"] = body["doctype_rules"]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        # 이 파일은 화면이 저장할 때마다 통째로 다시 쓰여 주석이 남지 않는다.
        # 값의 뜻은 본보기에 적어 두고, 여기서는 어디를 볼지만 가리킨다.
        f.write(SCAFFOLD_HEADER)
        yaml.safe_dump(scaffold, f, allow_unicode=True, sort_keys=False,
                       default_flow_style=False)
    return scaffold
