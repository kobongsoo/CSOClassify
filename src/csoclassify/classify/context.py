#------------------------------------------------------------------
# 경로/파일명 신호 (Signal C · D) — 파일을 "열지 않고" 판단하는 값싼 신호
#=> 문서를 열지도(텍스트 추출도) 않고, 딱 두 가지만 보고 등급을 매긴다.
#    - path(경로) : 파일이 "어디에 있나"(폴더·서버 위치)  → Signal C
#    - name(파일명): 파일 "이름이 뭔가"(파일명 속 단어)    → Signal D
#   추출·임베딩이 필요 없어 초고속이라, 대량 코퍼스의 1차 선별에 쓴다.
#
#   [path 는 이렇게 검출]
#     · 전체 경로를 정규화(\→/, 소문자) 후, cso_rules.yaml 의 paths 규칙을 위에서부터
#       검사해 "경로에 그 조각이 있으면" 그 등급. (예: /인사/ → S, //hr-server/ → C)
#   [name 은 이렇게 검출]
#     · 폴더를 뺀 파일명에, 내용검사에 쓰던 그 기밀사전(키워드)을 그대로 적용.
#       (예: 파일명에 "대외비" → C, "계약서" → S)
#
#   ※ 둘 다 파일을 열지 않으므로 "위치/이름"만으로 판단한다 → 참고 신호. 최종 등급은
#     rule/path/name/embed 를 융합(max)해 정한다.
#------------------------------------------------------------------

import os
from dataclasses import dataclass, field

from .rules import max_grade

# 경로/파일명 신호의 신뢰도 기본값(weight → conf).
_PATH_CONF = {"high": 0.90, "medium": 0.70, "low": 0.50}
# 파일명은 오해 소지가 있어(이름만으로 단정 못 함) 살짝 낮춘다.
_NAME_CONF = 0.60


#------------------------------------------------------------------
# 경로 정규화
#=> 경로 비교를 위해 구분자를 '/' 로 통일하고 소문자로 만든다. 윈도우 역슬래시·
#   UNC(\\서버)와 슬래시 혼용을 한 형태로 흡수한다.
#
# -in: s = 경로 문자열
#
# -out: str = 정규화된 경로(소문자, '/' 구분자)
# -out: error = 없음
#------------------------------------------------------------------
def _norm(s):
    return (s or "").replace("\\", "/").lower()


#------------------------------------------------------------------
# 경로 신호(Signal C) 결과
#=> 경로 택소노미 매칭 결과. 어느 규칙에 걸려 어떤 등급이 됐는지 담는다.
#
# -필드: grade          = 매칭 등급(없으면 None)
# -필드: confidence     = 신뢰도(0~1)
# -필드: seed_eligible  = 전파 seed 승격 가능 여부
# -필드: acl_restricted = 강한 제한 표식(fail-safe C 근거)
# -필드: source         = 걸린 규칙 id(없으면 None)
#------------------------------------------------------------------
@dataclass(frozen=True)
class PathSignal:
    grade: str
    confidence: float
    seed_eligible: bool
    acl_restricted: bool
    source: str

    #------------------------------------------------------------------
    # 직렬화용 dict
    #=> 분류 레코드의 signals.path 칸에 넣을 순수 dict.
    #
    # -in: 없음
    # -out: dict = {grade, confidence, seed_eligible, acl_restricted, source}
    # -out: error = 없음
    #------------------------------------------------------------------
    def as_dict(self):
        return {
            "grade": self.grade,
            "confidence": round(self.confidence, 3),
            "seed_eligible": self.seed_eligible,
            "acl_restricted": self.acl_restricted,
            "source": self.source,
        }


#------------------------------------------------------------------
# 파일명 신호(Signal D) 결과
#=> 파일명(basename)에서 걸린 키워드와 그 최종 등급을 담는다.
#
# -필드: grade         = 파일명 기반 등급(없으면 None)
# -필드: confidence    = 신뢰도(0~1)
# -필드: seed_eligible = 전파 seed 승격 가능 여부(파일명은 힌트라 항상 False)
# -필드: hits          = [{id, name, grade}] 근거
#------------------------------------------------------------------
@dataclass(frozen=True)
class NameSignal:
    grade: str
    confidence: float
    seed_eligible: bool
    hits: list = field(default_factory=list)

    #------------------------------------------------------------------
    # 직렬화용 dict
    #=> 분류 레코드의 signals.name 칸에 넣을 순수 dict.
    #
    # -in: 없음
    # -out: dict = {grade, confidence, seed_eligible, hits:[...]}
    # -out: error = 없음
    #------------------------------------------------------------------
    def as_dict(self):
        return {
            "grade": self.grade,
            "confidence": round(self.confidence, 3),
            "seed_eligible": self.seed_eligible,
            "hits": list(self.hits),
        }


#------------------------------------------------------------------
# 경로 스캔(Signal C) — "파일이 어느 폴더/서버에 있나"로 등급
#=> 파일이 놓인 "위치"만 보고 등급을 매긴다(내용·이름 아님, 파일 안 엶).
#    1) 경로를 정규화한다: 역슬래시\→/, 전부 소문자
#       예) D:\collected\인사\평가.hwp  →  d:/collected/인사/평가.hwp
#    2) cso_rules.yaml 의 paths 규칙을 위에서부터 검사 → 경로에 규칙의 "조각"이
#       하나라도 들어 있으면 그 등급으로 확정하고 멈춘다(먼저 걸린 규칙이 우선).
#
#   예) \\hr-server\share\x.hwp → '//hr-server/' 포함 → C(+강한제한)
#       D:\docs\인사\급여.xlsx   → '/인사/' 포함        → S
#       D:\web\public\공지.pdf   → '/public/' 포함      → O
#       D:\기타\메모.txt         → 걸리는 조각 없음      → None
#   (조각은 대부분 /폴더/ 형태라 사실상 "어느 폴더/서버냐"를 보는 것. 규칙 목록은
#    운영자가 cso_rules.yaml 의 paths 섹션에서 관리한다.)
#
# -in: file    = 파일 경로(절대/상대 무관)
# -in: ruleset = RuleSet(path_rules 사용)
#
# -out: PathSignal = 매칭 결과(없으면 grade=None)
# -out: error = 없음
#------------------------------------------------------------------
def scan_path(file, ruleset):
    # 신뢰도 표는 규칙셋(cso_rules.yaml confidence)에서, 없으면 모듈 기본값.
    conf_map = (getattr(ruleset, "confidence", None) or {}).get("path") or _PATH_CONF
    np = _norm(file)
    for rule in ruleset.path_rules:
        # 조각 중 하나라도 경로에 들어 있으면 이 규칙으로 확정(부분일치, 첫 매칭 우선).
        if any(m in np for m in rule.matches):
            conf = conf_map.get(rule.weight, 0.7)
            return PathSignal(
                grade=rule.grade, confidence=conf,
                seed_eligible=rule.seed_eligible,
                acl_restricted=rule.acl_restricted, source=rule.id,
            )
    return PathSignal(grade=None, confidence=0.0, seed_eligible=False,
                      acl_restricted=False, source=None)


#------------------------------------------------------------------
# 파일명 스캔(Signal D) — "파일 이름에 든 단어"로 등급
#=> 폴더는 빼고 파일 "이름"만 보고 등급을 매긴다(위치·내용 아님, 파일 안 엶).
#    1) 폴더를 뺀 순수 파일명만 소문자로 취한다(경로는 Signal C 담당).
#    2) 내용검사에 쓰던 그 기밀사전(keyword_rules)을 파일명에 그대로 적용 →
#       단어가 이름에 있으면 그 규칙의 등급을 하나 기록(파일명은 짧아 건수는 무의미).
#    3) 걸린 규칙들의 등급 중 가장 높은 것을 최종 등급으로.
#
#   예) 대외비_보고서.hwp   → "대외비"(C)        → C
#       계약서_한빛테크.docx → "계약서"(S)        → S
#       엠파워_설치안내.pptx → "엠파워"(C)        → C
#       회의록_초안.hwp      → 걸리는 단어 없음   → None
#   ※ 파일명은 실제 내용을 보증하지 못해(누가 이름만 그럴싸하게 지을 수 있음) 참고용
#     이며, 전파 seed 로는 절대 쓰지 않는다(seed_eligible 항상 False).
#
# -in: file    = 파일 경로
# -in: ruleset = RuleSet(keyword_rules 재사용)
#
# -out: NameSignal = 파일명 기반 등급 신호(없으면 grade=None)
# -out: error = 없음
#------------------------------------------------------------------
def scan_filename(file, ruleset):
    # 폴더를 뺀 순수 파일명만 대상으로(경로는 Signal C 담당).
    base = os.path.basename(file or "").lower()
    hits = []
    for rule in ruleset.keyword_rules:
        for term in rule.terms:
            if term and term.lower() in base:
                # 어떤 단어가 파일명에 있었는지 term 으로 남긴다(키워드라 노출 안전).
                hits.append({"id": rule.id, "name": rule.name,
                             "grade": rule.base_grade, "term": term})
                break   # 같은 규칙은 한 번만 기록(파일명은 건수 무의미)

    if not hits:
        return NameSignal(grade=None, confidence=0.0, seed_eligible=False, hits=[])

    name_conf = (getattr(ruleset, "confidence", None) or {}).get("name", _NAME_CONF)
    final = max_grade(h["grade"] for h in hits)
    return NameSignal(grade=final, confidence=name_conf, seed_eligible=False, hits=hits)
