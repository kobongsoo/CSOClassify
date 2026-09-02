#------------------------------------------------------------------
# 규칙 스캔(Signal A) — 추출 텍스트 → C/S/O 등급 신호
#=> L1(PII)은 ko-pii 엔진에, L2(기밀사전)는 자체 키워드 매칭에 맡겨, 문서의 내용
#   위험도 등급(C/S/O)과 근거(어떤 유형이 몇 건)를 만든다.
#   [A안, 2026-08] L1 검출을 자체 정규식에서 **ko-pii(Marker-Inc-Korea,
#   MIT, 순수 파이썬)**로 전면 교체. ko-pii 가 체크섬(주민 mod11·카드 Luhn·사업자
#   국세청·법인)·문맥·회피(전각/homoglyph) 방어까지 처리하므로 정확도가 오른다.
#   [프라이버시 불변식] 매칭된 "원문 값"은 절대 저장하지 않는다 — 유형 id 와
#   건수만 남긴다. ko-pii 가 돌려준 값(span)도 즉시 버리고 건수만 센다.
#------------------------------------------------------------------

import os
import sys
from dataclasses import dataclass, field

import yaml

from ..resources import resource_path, exe_dir

# ko-pii(L1 PII 엔진)는 런타임 필수 의존성이다(A안: 폴백 없음). import 실패는 스캔
# 시점에 설치 안내와 함께 분명한 오류로 알린다(모듈 로드 자체는 막지 않는다).
#
# [성능 최적화, 2026-08] ko_pii.detect_all() 은 28개 검출기를 '전부' 돌린 뒤 include
# 로 결과만 필터링한다 → 우리가 쓰지 않는 검출기(person·personal_attr 등, 밀집 입력
# 에서 O(n²))까지 매번 실행해 낭비다. 그래서 detect_all 대신, 설정 라벨에 해당하는
# 검출기만 직접 도는 _detect_subset() 를 쓴다. 정규화(전각/제로폭/homoglyph 방어)·
# 원본 더블패스·겹침 해소는 ko-pii 내부 함수를 '그대로' 재사용하므로 검출 결과·정확도
# 는 detect_all 과 동일하고 실행시간만 줄어든다(실측 약 -40%/건, 대형 문서에선 더 큼).
# 전체 엔진(_kopii_detect_all_full)은 우리가 매핑 못 한 라벨이 오면 쓰는 안전 폴백용.
try:
    import re as _re
    from ko_pii import detect_all as _kopii_detect_all_full
    from ko_pii.core.unicode_norm import (
        needs_normalization as _kopii_needs_norm,
        normalize_unicode as _kopii_normalize,
        remap_to_source as _kopii_remap,
    )
    from ko_pii.core.overlap import resolve_overlaps as _kopii_resolve_overlaps
    from ko_pii.patterns import (
        rrn as _p_rrn, frn as _p_frn, business_reg as _p_breg,
        corp_reg as _p_creg, driver_license as _p_dl, passport as _p_pass,
        card as _p_card, medical_insurance as _p_mi, prescription as _p_presc,
        pnu as _p_pnu, phone as _p_phone, email as _p_email, vehicle as _p_veh,
        address as _p_addr, nationality as _p_nat, account as _p_acct,
        edi_drug as _p_edi, court_case as _p_court,
        url as _p_url, ip as _p_ip,
    )
    # 원본 재검사(더블패스) 필요 판정용 — detect_all 의 _PII_RUN 과 동일 정의.
    _PII_RUN = _re.compile(r"[0-9A-Za-z０-９Ａ-Ｚａ-ｚ]+")
    # 주소 프리필터용 신호 — address.detect 의 4개 패턴이 '반드시' 포함해야 하는 것.
    #  · 도로명/지번(패턴1·2): (대로|로|길|동|읍|면|리) 바로 뒤 숫자(전각숫자까지 허용).
    #  · 대화체/단독(패턴3·4): _LOOSE_ANCHORS(주소·자택·살던·이사·명함 등) 키워드.
    # loose anchor 목록은 ko-pii 원본에서 그대로 import 해 버전 간 동기화 어긋남을 막는다
    # (import 실패 시 아래 except 로 떨어져 프리필터가 '항상 실행'으로 안전 폴백).
    from ko_pii.patterns.address import _LOOSE_ANCHORS as _ADDR_LOOSE_ANCHORS
    _ADDR_STRUCT_RE = _re.compile(r"(?:대로|로|길|동|읍|면|리)\s*[0-9０-９]")
    _ADDR_LOOSE_RE = _re.compile("|".join(_re.escape(a) for a in _ADDR_LOOSE_ANCHORS))
    _HAVE_KOPII = True
    _KOPII_IMPORT_ERR = None
except Exception as _e:   # pragma: no cover - ko-pii 미설치 환경에서만
    _HAVE_KOPII = False
    _KOPII_IMPORT_ERR = _e
    _kopii_detect_all_full = None
    _PII_RUN = None
    _ADDR_STRUCT_RE = None
    _ADDR_LOOSE_RE = None
    _p_addr = None

# 성능/지연 가드: subset(필요한 검출기만) 으로 바꾼 뒤에도 phone(O(n²))·address·
# account 때문에 검출 시간이 입력 길이에 '초선형'으로 는다(실측: 10K≈0.08s,
# 20K≈0.2s, 50K≈0.9s, 300K≈20s). 예전엔 앞 10K 만 남기고 '잘라' 지연을 눌렀지만,
# 그러면 10K 뒤쪽 PII 를 전부 놓쳤다. 지금은 두 단계로 '커버리지 회복 + 지연 선형화'
# 를 함께 얻는다(설계: A안 + 초선형 청킹).
#   (1) 선형 검출기(초선형 셋 밖): 전체 텍스트를 한 번에 훑는다. 정규식이 선형이라
#       길이에 비례해 싸고 '전량' 커버가 된다(상한 없음).
#   (2) 초선형 검출기(_KOPII_SUPERLINEAR): _KOPII_MAX_CHARS(=청크 크기, 10K) 창으로
#       잘라 돌린다. 통짜 1회의 초선형 비용(50K=0.9s) 대신 창마다 0.08s 고정이라
#       총비용이 선형이 된다(50K=5×0.08≈0.4s). 인접 창은 _KOPII_OVERLAP 만큼 겹쳐
#       경계에 걸친 PII 미탐을 막고, 절대 오프셋 구간 병합으로 경계 중복은 없앤다.
#       최악 지연을 묶으려 초선형 스캔 범위는 앞 _KOPII_MAX_TOTAL 자로 상한을 둔다
#       (기본 100K ≈ ~1s). 선형 검출기는 이 상한과 무관하게 전량 커버.
# 세 값 모두 환경변수로 조절 가능(배치=커버리지 우선이면 상향, 실시간=지연 우선이면 하향).
def _env_pos_int(name, default):
    # 환경변수를 양의 정수로 읽되, 없거나 잘못됐거나 0 이하면 안전 기본값으로 되돌린다
    # (0/음수면 검출이 통째로 꺼지거나 무한루프가 날 수 있어 방어).
    try:
        v = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


_KOPII_MAX_CHARS = _env_pos_int("CSOCLASSIFY_KOPII_MAX_CHARS", 10_000)    # 청크 크기(=소형 문서 단일패스 기준)
_KOPII_MAX_TOTAL = _env_pos_int("CSOCLASSIFY_KOPII_MAX_TOTAL", 100_000)   # 초선형 검출기 총 스캔 상한
_KOPII_OVERLAP = _env_pos_int("CSOCLASSIFY_KOPII_OVERLAP", 128)           # 인접 청크 오버랩(최장 PII span 보다 크게)
# 총상한이 청크보다 작으면 최소 한 청크는 돌도록, 오버랩이 청크 이상이면 창이 전진
# 못 하므로(무한루프) 각각 안전하게 보정한다.
if _KOPII_MAX_TOTAL < _KOPII_MAX_CHARS:
    _KOPII_MAX_TOTAL = _KOPII_MAX_CHARS
if _KOPII_OVERLAP >= _KOPII_MAX_CHARS:
    _KOPII_OVERLAP = _KOPII_MAX_CHARS // 4

# 입력 길이에 '초선형'으로 비싸지는 검출기 라벨(청킹 대상). 나머지는 선형이라 전량 스캔한다.
# (근거: 위 실측 초선형 비용의 원인은 phone(O(n²))·address·account 세 검출기다.)
_KOPII_SUPERLINEAR = frozenset({"PHONE", "ADDRESS", "ACCOUNT"})

# 스탬프(Signal E) 게이트 파라미터.
#=> 키워드(L2)와의 차이: 본문 중 '단발 언급'("대외비가 아님" 같은)의 오탐을 막으려고,
#   문서 '머리(앞 _STAMP_HEAD_CHARS 자)' 안에 있거나 '_STAMP_REPEAT_MIN 회 이상 반복'
#   (페이지마다 찍히는 워터마크 정황)일 때만 진짜 스탬프로 인정한다. 두 값 모두 환경변수로
#   조정 가능(배치=회수 우선이면 상향, 실시간=민감 우선이면 하향).
_STAMP_HEAD_CHARS = _env_pos_int("CSOCLASSIFY_STAMP_HEAD_CHARS", 400)   # '머리' 판정 범위(문자수)
_STAMP_REPEAT_MIN = _env_pos_int("CSOCLASSIFY_STAMP_REPEAT_MIN", 2)     # 반복 인정 최소 횟수


#------------------------------------------------------------------
# 영숫자 런 모양 계산 (더블패스 필요 판정)
#=> 문자열에서 '영숫자 덩어리'들의 길이 나열을 만든다. 정규화(제로폭 제거 등)가
#   이 모양을 바꿨다면 인접 PII 두 개가 경계에서 융합/분리된 것이므로 원본도 다시
#   검사해야 한다. detect_all 내부의 동명 헬퍼와 동일한 판정을 재현한다.
#
# -in: s = 검사할 문자열(정규화본 또는 원본)
#
# -out: list[int] = 각 영숫자 런의 길이 목록
# -out: error = 없음 (_PII_RUN 미초기화 시엔 호출되지 않음 — _HAVE_KOPII 가드 하에서만 사용)
#------------------------------------------------------------------
def _run_shape(s):
    return [m.end() - m.start() for m in _PII_RUN.finditer(s)]


#------------------------------------------------------------------
# 주소 검출 프리필터 (address.detect 실행 여부 판정)
#=> address.detect 는 4개 정규식을 전체 텍스트에 훑어 주소 없는 문서에서도 비용이
#   든다(실측 10K≈24ms). 이 함수는 '주소가 있을 가능성' 신호가 하나도 없으면
#   False 를 돌려 그 비싼 검출을 통째로 건너뛰게 한다.
#    - 신호1: (대로|로|길|동|읍|면|리) 바로 뒤 숫자 → 도로명/지번 주소 후보
#    - 신호2: _LOOSE_ANCHORS(주소·자택·살던·이사·명함 등) → 대화체/단독 주소 후보
#   [안전 불변식] address.detect 의 4개 패턴은 모두 위 두 신호 중 하나를 '반드시'
#   포함하므로, 신호가 없으면 매칭도 없다 → False 를 줘도 미탐이 생기지 않는다.
#   (실측: 실제 주소 표본 전건이 프리필터를 통과, 미탐 0)
#
# -in: text = 검사할 텍스트(정규화본 또는 원본)
#
# -out: bool = 주소 검출을 돌려야 하면 True, 확실히 없으면 False
# -out: error = 없음 (ko-pii import 실패로 정규식 미초기화면 항상 True → 안전)
#------------------------------------------------------------------
def _address_prefilter(text):
    # 정규식 미초기화(ko-pii 미설치 등)면 판정 불가 → 항상 실행해 안전하게 처리.
    if _ADDR_STRUCT_RE is None:
        return True
    return bool(_ADDR_STRUCT_RE.search(text) or _ADDR_LOOSE_RE.search(text))


#------------------------------------------------------------------
# 주소 검출기 (프리필터로 감싼 버전)
#=> _LABEL_TO_DETECTOR 에서 ADDRESS 자리에 원본 address.detect 대신 이걸 넣는다.
#   프리필터가 '주소 없음'이라 판정하면 비싼 검출을 건너뛰고 빈 결과를 돌린다.
#
# -in: text = 스캔 대상 텍스트
#
# -out: iterable[DetectionResult] = 주소 검출 결과(없거나 프리필터 skip 이면 빈 튜플)
# -out: error = 없음
#------------------------------------------------------------------
def _gated_address_detect(text):
    # 주소 신호가 전혀 없으면 address.detect 자체를 호출하지 않는다.
    if not _address_prefilter(text):
        return ()
    return _p_addr.detect(text)


# 설정 라벨 → 검출기. 튜플 순서는 ko_pii.detect.DETECTORS 실행 순서를 보존해,
# 겹침 해소의 '동점 시 먼저 시작한 span' 규칙까지 detect_all 과 동일하게 만든다.
# ADDRESS 만 프리필터로 감싼 _gated_address_detect 를 쓴다(결과 동일, 주소 없는
# 문서에서 비용 절감). import 실패(_HAVE_KOPII=False)면 빈 맵으로 둔다.
if _HAVE_KOPII:
    _LABEL_TO_DETECTOR = (
        ("RRN", _p_rrn.detect), ("FRN", _p_frn.detect),
        ("BUSINESS_REG", _p_breg.detect), ("CORP_REG", _p_creg.detect),
        ("DRIVER_LICENSE", _p_dl.detect), ("PASSPORT", _p_pass.detect),
        ("CARD", _p_card.detect), ("MEDICAL_INSURANCE", _p_mi.detect),
        # 처방전 주의: 검출기는 'PRESCRIPTION_ID' 라벨을 내보내지만 cso_rules.yaml 은
        # 'PRESCRIPTION' 을 쓴다. 두 별칭 모두 이 검출기를 고르게 하되, 실제 결과는
        # 최종 include 필터에서 라벨 불일치로 걸러진다 → 현재 detect_all 동작(설정상
        # 처방전 0건)과 정확히 일치. (설정 라벨 오타로 보이나, 본 패치는 동작 보존이
        # 목적이라 고치지 않는다. 처방전을 실제로 잡으려면 yaml label 을 고쳐야 함.)
        ("PRESCRIPTION", _p_presc.detect), ("PRESCRIPTION_ID", _p_presc.detect),
        ("PNU", _p_pnu.detect), ("PHONE", _p_phone.detect),
        ("EMAIL", _p_email.detect), ("VEHICLE", _p_veh.detect),
        ("ADDRESS", _gated_address_detect), ("NATIONALITY", _p_nat.detect),
        ("ACCOUNT", _p_acct.detect), ("EDI_DRUG", _p_edi.detect),
        ("COURT_CASE", _p_court.detect),
        ("URL", _p_url.detect), ("IP", _p_ip.detect),
    )
    _MAPPED_LABELS = frozenset(lbl for lbl, _ in _LABEL_TO_DETECTOR)
else:
    _LABEL_TO_DETECTOR = ()
    _MAPPED_LABELS = frozenset()


#------------------------------------------------------------------
# 필요한 검출기만 실행 (detect_all 축소판)
#=> ko_pii.detect_all 은 28개 검출기를 전부 돌린 뒤 include 로 결과만 걸러 낭비가
#   크다. 이 함수는 요청 라벨(include)에 해당하는 검출기만 골라 돌려 같은 결과를
#   더 빠르게 낸다. 정규화·역매핑·겹침해소는 ko-pii 내부 함수를 그대로 써서
#   detect_all 과 결과가 동일하도록 맞춘다.
#    1) 매핑 못 한 라벨이 섞였으면 정확도 보장 위해 전체 엔진으로 폴백
#    2) 요청 라벨에 해당하는 검출기만 (중복 없이, 원래 순서로) 선택
#    3) normalize=True 면 전각/제로폭/homoglyph 우회를 detect_all 과 똑같이 방어
#       (정규화본 검사 → 원본 역매핑 → 런 모양이 바뀌었으면 원본 더블패스)
#    4) include 로 최종 필터 후 겹침 해소해 반환
#
# -in: text      = 스캔 대상 텍스트
# -in: include   = 셀 ko-pii 라벨들(반복가능). 이 라벨의 검출기만 실행한다.
# -in: normalize = True(기본)면 우회 방어 정규화 수행. detect_all 과 동일 의미.
#
# -out: list[DetectionResult] = include 라벨에 해당하는 검출 결과(겹침 해소됨)
# -out: error = 없음 (ko-pii 미설치 시엔 _kopii_counts 가 먼저 RuntimeError 를 낸다)
#------------------------------------------------------------------
def _detect_subset(text, include, *, normalize=True):
    inc = set(include)
    # (1) 우리가 매핑 못 한 라벨이 요청에 있으면(예: 향후 yaml 에 PERSON 추가) 놓치지
    #     않도록 전체 엔진으로 안전 폴백한다. 현재 설정 라벨은 모두 매핑돼 있어 미발생.
    if inc - _MAPPED_LABELS:
        return _kopii_detect_all_full(text, include=list(inc), normalize=normalize)
    # (2) 요청 라벨에 걸리는 검출기만, detect_all 순서를 보존해 중복 없이 고른다.
    seen = set()
    detectors = []
    for label, fn in _LABEL_TO_DETECTOR:
        if label in inc and fn not in seen:
            seen.add(fn)
            detectors.append(fn)
    if not detectors:
        return []

    source = text
    offset_map = None
    # (3) detect_all 과 동일한 정규화 경로: 전각/호환문자 폴딩 + 제로폭 제거로 우회 차단.
    if normalize and _kopii_needs_norm(text):
        norm, omap = _kopii_normalize(text)
        if norm != text:
            text, offset_map = norm, omap

    raw = []
    for fn in detectors:
        raw.extend(fn(text))

    if offset_map is not None:
        # 정규화본 검출 offset 을 원본 기준으로 역매핑.
        raw = _kopii_remap(raw, offset_map, source)
        # 보이지 않는 문자 제거가 '영숫자 런 모양'을 바꾼 경우(=PII 경계 융합/분리)만
        # 원본을 한 번 더 검사해 합집합한다. detect_all 의 더블패스 조건과 동일.
        if _run_shape(source) != _run_shape(text):
            for fn in detectors:
                raw.extend(fn(source))

    # (4) detect_all 과 동일하게 include 로 최종 필터(검출기가 include 밖 라벨을 낼
    #     경우 방어; 예: prescription 이 PRESCRIPTION_ID 를 내면 걸러짐) 후 겹침 해소.
    raw = [d for d in raw if d.label in inc]
    return _kopii_resolve_overlaps(raw)


# 등급 서열(높을수록 민감). None 은 "해당 없음".
_GRADE_RANK = {"O": 0, "S": 1, "C": 2}

# 정의된 등급을 '서열 낮은 → 높은' 순으로 나열한 튜플. 오류 메시지에 "O < S < C" 처럼
# 보여 주거나, 유효성 검사에서 "이 값이 등급 맞나"를 볼 때 쓴다.
GRADES = tuple(sorted(_GRADE_RANK, key=_GRADE_RANK.get))


#------------------------------------------------------------------
# 알 수 없는 등급 문자열 예외
#=> 등급 서열에 없는 값(예: 소문자 "c", 오타 "SS")이 등급 계산에 들어왔을 때 던진다.
#   예전에는 이런 값을 조용히 '무시'해서 그 규칙이 판정에서 통째로 빠졌고, 결과적으로
#   문서 등급이 실제보다 낮게 나오는 사고로 이어졌다(fail-open). 이제는 소리 내어 실패한다.
#
# -필드: grade = 문제가 된 원본 값(그대로 보존해 사용자가 오타를 눈으로 확인)
#------------------------------------------------------------------
class UnknownGradeError(ValueError):

    #------------------------------------------------------------------
    # 예외 생성
    #=> 문제 값과 "정의된 등급은 무엇인지"를 한 문장으로 만들어 담는다.
    #
    # -in: grade = 서열에 없는 등급 값(문자열이 아닐 수도 있어 repr 로 보여 준다)
    # -in: where = 어디서 나왔는지 설명(예: "regex_pii[rrn].base_grade"). 없으면 생략
    #
    # -out: 없음(생성자)
    # -out: error = 없음
    #------------------------------------------------------------------
    def __init__(self, grade, where=None):
        self.grade = grade
        loc = f" ({where})" if where else ""
        super().__init__(
            f"정의되지 않은 등급 값{loc}: {grade!r} — 정의된 등급: {' < '.join(GRADES)}"
        )


#------------------------------------------------------------------
# 규칙셋 검증 실패 예외
#=> cso_rules.yaml 을 읽는 데는 성공했지만 내용이 규칙에 맞지 않을 때 던진다.
#   위반을 '처음 하나에서 멈추지 않고 전부 모아' 담는 게 핵심 — 관리자가 한 번에
#   고칠 수 있어야 하기 때문이다.
#
# -필드: path       = 문제의 규칙셋 파일 경로
# -필드: violations = 위반 목록. 각 항목은 dict
#                     {code, section, rule_id, field, value, detail, hint}
#------------------------------------------------------------------
class RuleSetValidationError(Exception):

    #------------------------------------------------------------------
    # 예외 생성
    #=> 파일 경로와 위반 목록을 담고, 사람이 읽을 요약문을 메시지로 만든다.
    #
    # -in: path       = 규칙셋 파일 경로
    # -in: violations = 위반 dict 리스트(빈 리스트로는 만들지 않는다)
    #
    # -out: 없음(생성자)
    # -out: error = 없음
    #------------------------------------------------------------------
    def __init__(self, path, violations):
        self.path = path
        self.violations = list(violations)
        super().__init__(format_violations(path, self.violations))

# weight → 신뢰도 기본값(cso_rules.yaml 의 confidence 블록으로 덮어쓸 수 있음).
# 신뢰도는 '검토 큐' 편입/정렬에만 쓰이고 C/S/O 등급은 바꾸지 않는다.
_REGEX_CONF = {"high": 0.90, "medium": 0.70, "low": 0.50}
_KEYWORD_CONF = {"high": 0.85, "medium": 0.70, "low": 0.50}
# 스탬프는 '작성자의 명시적 분류 의도'라 오탐이 매우 낮다 → 키워드보다 높게 잡는다.
_STAMP_CONF = {"high": 0.95, "medium": 0.80, "low": 0.65}
# 민감정보군(법상 민감정보) 키워드 — 키워드 기반이라 신뢰도는 기밀사전과 비슷하게 둔다.
_SENSITIVE_CONF = {"high": 0.85, "medium": 0.70, "low": 0.55}


#------------------------------------------------------------------
# 기본 신뢰도 표(정규식/키워드/경로/파일명)
#=> cso_rules.yaml 에 confidence 블록이 없을 때 쓰는 폴백. yaml 값이 있으면
#   load_rules 가 이 위에 덮어써 RuleSet.confidence 로 담는다.
#
# -in: 없음
# -out: dict = {"regex":{high/med/low}, "keyword":{...}, "sensitive":{...}, "stamp":{...}, "path":{...}, "name":float}
# -out: error = 없음
#------------------------------------------------------------------
def _default_confidence():
    return {
        "regex": dict(_REGEX_CONF),
        "keyword": dict(_KEYWORD_CONF),
        "sensitive": dict(_SENSITIVE_CONF),
        "stamp": dict(_STAMP_CONF),
        "path": {"high": 0.90, "medium": 0.70, "low": 0.50},
        "name": 0.60,
    }


#------------------------------------------------------------------
# 등급 최댓값 계산 (보수적 융합의 핵심)
#=> 여러 등급 후보 중 "가장 높은(민감한)" 등급을 고른다. C > S > O 서열을 쓰며,
#   None(해당 없음)은 무시한다. 후보가 전부 None 이면 None 을 반환한다.
#    1) None 은 "판단 근거 없음"이라 건너뛴다(정상)
#    2) 서열에 없는 값이 오면 조용히 넘기지 않고 UnknownGradeError 로 실패시킨다
#    3) _GRADE_RANK 로 순위를 매겨 최댓값 선택
#
#   [왜 예외인가] 예전에는 모르는 값을 rank -1 로 취급해 그냥 건너뛰었다. 그러면
#   cso_rules.yaml 에 'base_grade: c' 같은 오타가 있어도 아무 경고 없이 그 규칙만
#   판정에서 빠져 문서 등급이 실제보다 낮게 나온다. 등급이 낮게 나오는 실패는
#   보안 사고라, 조용히 넘기는 대신 시끄럽게 멈추는 쪽이 안전하다.
#   규칙셋은 load_rules 에서 미리 검증하므로, 정상 경로에서는 이 예외가 나지 않는다.
#
# -in: grades = 등급 문자열/None 들의 반복가능 객체 (예: ["O","C",None])
# -in: where  = 오류 메시지에 넣을 위치 설명(선택). 예: "signals.rule.grade"
#
# -out: grade = 가장 높은 등급 문자열, 전부 None 이면 None
# -out: error = 서열에 없는 값이 있으면 UnknownGradeError
#------------------------------------------------------------------
def max_grade(grades, where=None):
    best = None
    best_rank = -1
    for g in grades:
        # None 은 "이 신호는 등급을 내지 않았다"는 정상 상태 → 후보에서만 제외.
        if g is None:
            continue
        r = _GRADE_RANK.get(g)
        # 서열에 없는 값 = 오타이거나 다른 등급체계의 값 → 즉시 실패(fail-closed).
        if r is None:
            raise UnknownGradeError(g, where)
        if r > best_rank:
            best_rank, best = r, g
    return best


#------------------------------------------------------------------
# 스캔 기본값 묶음
#=> cso_rules.yaml 의 defaults 블록을 담는다. 개별 규칙이 값을 지정하지 않았을 때
#   여기 값을 대신 쓴다.
#
# -필드: bulk_threshold  = 이 건수 이상이면 bulk_grade 로 상향(명단/대장 정황)
# -필드: case_insensitive = True 면 영문 키워드 대소문자 무시
#------------------------------------------------------------------
@dataclass(frozen=True)
class Defaults:
    bulk_threshold: int = 5
    case_insensitive: bool = True


#------------------------------------------------------------------
# PII 유형 규칙 1개(L1) — ko-pii 라벨 → 등급 정책
#=> 검출은 ko-pii 가 하고(패턴·체크섬·문맥 내장), 이 규칙은 "그 유형이 몇 건이면
#   몇 등급인가"라는 정책만 담는다. (A안 이전엔 정규식 pattern 을 직접 담았다)
#
# -필드: id            = 유형 식별자 (예: "rrn") — 결과 근거의 rule_id
# -필드: name          = 사람이 읽는 이름 (예: "주민등록번호")
# -필드: label         = 대응하는 ko-pii 라벨 (예: "RRN","CARD","BUSINESS_REG")
# -필드: base_grade    = 1건이라도 있으면 부여할 등급
# -필드: bulk_grade    = bulk_threshold 초과 시 상향 등급(없으면 상향 안 함)
# -필드: bulk_threshold = 이 유형 전용 임계값(없으면 Defaults 값 사용)
# -필드: weight        = 신뢰도 가중("high"|"medium"|"low")
# -필드: seed_eligible = True 면 이 히트로 확정된 문서를 전파 seed 로 승격 가능
#------------------------------------------------------------------
@dataclass(frozen=True)
class RegexRule:
    id: str
    name: str
    label: str
    base_grade: str = "S"
    bulk_grade: str = None
    bulk_threshold: int = None
    weight: str = "medium"
    seed_eligible: bool = False


#------------------------------------------------------------------
# 키워드 규칙 1개(L2)
#=> 기밀사전. 지정한 단어가 텍스트에 있으면 히트로 본다.
#
# -필드: id            = 규칙 식별자 (예: "mark_confidential")
# -필드: name          = 사람이 읽는 이름 (예: "기밀 표식")
# -필드: terms         = 탐지 단어 리스트
# -필드: exclude       = 제외 문구 리스트. 단어가 이 문구 안에 든 매치는 오탐으로 보고
#                        세지 않는다(예: terms=[전과], exclude=[산전과·충전과]).
# -필드: base_grade    = 1건이라도 있으면 부여할 등급
# -필드: bulk_grade    = bulk_threshold 초과 시 상향 등급(없으면 상향 안 함)
# -필드: bulk_threshold = 이 규칙 전용 임계값(없으면 Defaults 값 사용)
# -필드: weight        = 신뢰도 가중
# -필드: seed_eligible = 전파 seed 승격 가능 여부
#------------------------------------------------------------------
@dataclass(frozen=True)
class KeywordRule:
    id: str
    name: str
    terms: tuple
    exclude: tuple = ()
    base_grade: str = "S"
    bulk_grade: str = None
    bulk_threshold: int = None
    weight: str = "medium"
    seed_eligible: bool = False


#------------------------------------------------------------------
# 결합식별성 규칙 1개(L1-combo) — PII '조합' 가중
#=> 개별 PII 는 등급이 낮아도(예: 전화·이메일 O), 여러 유형이 '한 문서에' 모이면
#   특정 개인을 재식별하기 쉬워진다(GDPR/개인정보보호법의 '결합용이성'). 이 규칙은
#   그런 조합을 만나면 상향 등급을 준다. 판정은 '문서 단위 공존'만 본다(근접성 아님).
#    - all_of  : 이 라벨들이 '모두' 있으면 성립(예: RRN+ACCOUNT → 신원+금융)
#    - of+min_types : of 그룹에서 '서로 다른' 라벨이 min_types 종 이상이면 성립
#                     (예: 연락처 [PHONE,EMAIL,ADDRESS] 중 2종 이상 → 개인 특정)
#   두 조건을 다 적으면 둘 다 만족해야 한다(AND). 하나도 안 적으면 그 규칙은 무시.
#
# -필드: id            = 규칙 식별자 (예: "identity_financial")
# -필드: name          = 사람이 읽는 이름 (예: "신원+금융 결합")
# -필드: all_of        = 모두 존재해야 하는 ko-pii 라벨들(튜플, 없으면 빈 튜플)
# -필드: of            = min_types 판정 대상 라벨 그룹(튜플)
# -필드: min_types     = of 그룹에서 필요한 '서로 다른 라벨' 최소 종수(0이면 미사용)
# -필드: grade         = 조합 성립 시 부여할 상향 등급(기본 C)
# -필드: weight        = 신뢰도 가중("high"|"medium"|"low")
# -필드: seed_eligible = True 면 이 조합으로 확정된 문서를 전파 seed 로 승격 가능
#------------------------------------------------------------------
@dataclass(frozen=True)
class ComboRule:
    id: str
    name: str
    all_of: tuple = ()
    of: tuple = ()
    min_types: int = 0
    grade: str = "C"
    weight: str = "high"
    seed_eligible: bool = False


#------------------------------------------------------------------
# 민감정보군 규칙 1개(Signal F) — 법상 '민감정보'
#=> 개인정보보호법 제23조의 민감정보(건강·유전·성생활·사상/신념·정치·노조·범죄경력·
#   인종 등)는 별도 취급 대상이다. 일반 기밀사전(L2)과 섞지 않고 독립 신호로 두어
#   "왜 민감으로 봤는가(어느 범주)"를 감사에서 명확히 드러낸다.
#   [주의] 키워드 기반이라 오탐 가능(예: 일반 기사 속 '정치'). 보수적 max 융합·검토큐가
#   과분류를 사람 검토로 흡수하므로, 안전측(상향)으로 둔다.
#
# -필드: id            = 규칙 식별자 (예: "health")
# -필드: name          = 사람이 읽는 이름 (예: "건강정보")
# -필드: category      = 민감정보 범주(건강/유전/성생활/사상·정치/노조/범죄경력/인종)
# -필드: terms         = 탐지 단어 리스트
# -필드: exclude       = 제외 문구 리스트(오탐 방지). 예: terms=[전과], exclude=[산전과]
# -필드: grade         = 히트 시 부여 등급(법상 민감 → 기본 C)
# -필드: weight        = 신뢰도 가중("high"|"medium"|"low")
# -필드: seed_eligible = True 면 이 히트로 확정된 문서를 전파 seed 로 승격 가능
#------------------------------------------------------------------
@dataclass(frozen=True)
class SensitiveRule:
    id: str
    name: str
    category: str
    terms: tuple
    exclude: tuple = ()
    grade: str = "C"
    weight: str = "high"
    seed_eligible: bool = False


#------------------------------------------------------------------
# 스탬프 규칙 1개(Signal E) — 보안분류 표식
#=> "문서에 찍힌 작성자의 분류 의도"를 잡는 규칙. 키워드(L2)와 달리, 본문 단발
#   언급의 오탐을 막으려 '머리 위치'나 '반복'일 때만 인정한다(always 규칙은 예외).
#
# -필드: id            = 규칙 식별자 (예: "stamp_confidential")
# -필드: name          = 사람이 읽는 이름 (예: "대외비/기밀 스탬프")
# -필드: terms         = 표식 문구 리스트(예: "대외비","CONFIDENTIAL")
# -필드: grade         = 스탬프로 인정될 때 부여할 등급(기본 C)
# -필드: weight        = 신뢰도 가중("high"|"medium"|"low")
# -필드: seed_eligible = True 면 이 스탬프로 확정된 문서를 전파 seed 로 승격 가능
# -필드: always        = True 면 위치/반복 게이트 없이 1건이라도 스탬프로 인정
#                        (거의 스탬프 전용 문구용. 예: "관계자 외 열람금지")
#------------------------------------------------------------------
@dataclass(frozen=True)
class StampRule:
    id: str
    name: str
    terms: tuple
    grade: str = "C"
    weight: str = "high"
    seed_eligible: bool = False
    always: bool = False


#------------------------------------------------------------------
# 경로 규칙 1개(Signal C)
#=> "파일 경로에 이 조각이 있으면 이 등급" 매핑. 텍스트를 열지 않는 출처 대용 신호.
#
# -필드: id            = 규칙 식별자 (예: "secure_server")
# -필드: name          = 사람이 읽는 이름
# -필드: matches       = 부분일치 조각들(로드 시 '/' 정규화 + 소문자화)
# -필드: grade         = 매칭 시 부여 등급. acl_restricted=True 인 규칙에 한해 None 가능
#                       (None = "이 폴더인 건 분명하지만 등급은 내용을 보고 정하라")
# -필드: acl_restricted = True 면 강한 제한 표식. 내용·파일명 어디서도 신호가 없으면
#                       fail-safe 로 최고 등급을 준다(fuse.fuse_signals 의 failsafe_acl)
# -필드: weight        = 신뢰도 가중
# -필드: seed_eligible = 전파 seed 승격 가능 여부
#------------------------------------------------------------------
@dataclass(frozen=True)
class PathRule:
    id: str
    name: str
    matches: tuple
    # 기본값을 None 으로 둔다. 예전에는 "S" 였는데, 그 탓에 grade 를 생략한 규칙이
    # 조용히 S 가 되어 'acl_restricted 만 있는 규칙'을 아예 표현할 수 없었다.
    grade: str = None
    acl_restricted: bool = False
    weight: str = "high"
    seed_eligible: bool = False


#------------------------------------------------------------------
# 규칙셋 전체
#=> 로드된 cso_rules.yaml 을 통째로 담는 컨테이너. 스캔 함수에 넘겨 쓴다.
#
# -필드: version       = 규칙셋 버전 문자열(감사용, 결과에 기록)
# -필드: defaults      = Defaults(기본 임계값 등)
# -필드: regex_rules    = RegexRule 리스트(L1)
# -필드: pii_combos     = ComboRule 리스트(L1-combo, PII 결합식별성)
# -필드: keyword_rules  = KeywordRule 리스트(L2)
# -필드: sensitive_rules = SensitiveRule 리스트(Signal F, 법상 민감정보군)
# -필드: stamp_rules    = StampRule 리스트(Signal E, 보안분류 스탬프)
# -필드: path_rules     = PathRule 리스트(Signal C, 경로 택소노미)
# -필드: confidence     = weight→신뢰도 표({regex/keyword/sensitive/stamp/path:{high/med/low}, name:float})
#------------------------------------------------------------------
@dataclass(frozen=True)
class RuleSet:
    version: str
    defaults: Defaults
    regex_rules: list = field(default_factory=list)
    pii_combos: list = field(default_factory=list)
    keyword_rules: list = field(default_factory=list)
    sensitive_rules: list = field(default_factory=list)
    stamp_rules: list = field(default_factory=list)
    path_rules: list = field(default_factory=list)
    confidence: dict = field(default_factory=_default_confidence)


#------------------------------------------------------------------
# 규칙 히트 1건(근거)
#=> "어떤 규칙이 몇 건 걸렸고 그래서 어떤 등급인가"를 담는다. 원문 값은 없다.
#
# -필드: rule_id         = 걸린 규칙 id
# -필드: name            = 규칙 이름
# -필드: layer           = "L1"(정규식) | "L2"(키워드)
# -필드: count           = 인정된 매칭 건수(문맥·검증 통과분)
# -필드: validated_count = 체크섬까지 통과한 건수(키워드는 0)
# -필드: grade           = 이 규칙이 기여한 등급(상향 반영)
# -필드: seed_eligible   = 이 히트가 전파 seed 로 쓸 만큼 신뢰되는가
# -필드: confidence      = 이 히트의 신뢰도(0~1)
# -필드: terms           = 매칭된 (단어, 건수) 목록 — L2 키워드만 채운다.
#                          L1 정규식은 매칭값이 곧 원문(PII)이라 항상 비워 둔다.
#------------------------------------------------------------------
@dataclass(frozen=True)
class RuleHit:
    rule_id: str
    name: str
    layer: str
    count: int
    validated_count: int
    grade: str
    seed_eligible: bool
    confidence: float
    terms: tuple = ()


#------------------------------------------------------------------
# 규칙 스캔 결과 신호(Signal A)
#=> 문서 하나에 대한 규칙 스캔의 최종 산출. 등급 결정과 감사에 이 값을 쓴다.
#
# -필드: grade         = 규칙 기반 최종 등급(히트 없으면 None)
# -필드: confidence    = 최종 등급을 만든 히트들의 최고 신뢰도(없으면 0.0)
# -필드: seed_eligible = 이 문서를 전파 seed 로 승격해도 되는가
# -필드: hits          = RuleHit 리스트(근거 전체, 원문 없음)
#------------------------------------------------------------------
@dataclass(frozen=True)
class GradeSignal:
    grade: str
    confidence: float
    seed_eligible: bool
    hits: list = field(default_factory=list)

    #------------------------------------------------------------------
    # 결과를 직렬화용 dict 로
    #=> 분류 레코드(jsonl)의 "rule" 신호 칸에 넣기 좋은 순수 dict 로 바꾼다.
    #   원문 값이 없으므로 그대로 로그에 남겨도 안전하다.
    #
    # -in: 없음
    #
    # -out: dict = {grade, confidence, seed_eligible, hits:[{id,name,layer,count,...}]}
    # -out: error = 없음
    #------------------------------------------------------------------
    def as_dict(self):
        return {
            "grade": self.grade,
            "confidence": round(self.confidence, 3),
            "seed_eligible": self.seed_eligible,
            "hits": [
                {
                    "id": h.rule_id,
                    "name": h.name,
                    "layer": h.layer,
                    "count": h.count,
                    "validated": h.validated_count,
                    "grade": h.grade,
                    # 어떤 단어가 걸렸는지(키워드만). PII 정규식은 항상 빈 목록.
                    "terms": [{"term": t, "count": c} for t, c in h.terms],
                }
                for h in self.hits
            ],
        }


#------------------------------------------------------------------
# 건수 → 상향 등급 반영
#=> 히트 건수가 bulk_threshold 이상이면 bulk_grade(상향)를, 아니면 base_grade 를 준다.
#   bulk_grade 가 없는 규칙(대부분 키워드)은 항상 base_grade.
#
# -in: rule     = RegexRule 또는 KeywordRule
# -in: count    = 인정된 히트 건수
# -in: defaults = Defaults(규칙에 임계값이 없을 때 대체값)
#
# -out: grade = 최종 반영 등급 문자열
# -out: error = 없음
#------------------------------------------------------------------
def _escalate_grade(rule, count, defaults):
    if not rule.bulk_grade:
        return rule.base_grade
    # 규칙 전용 임계값이 없으면 전역 기본값을 쓴다.
    thr = rule.bulk_threshold if rule.bulk_threshold else defaults.bulk_threshold
    return rule.bulk_grade if count >= thr else rule.base_grade


#------------------------------------------------------------------
# 청크 창 경계 생성기
#=> [0, covered) 구간을 size 크기 창으로 (size-overlap) 만큼 전진하며 덮는다.
#   인접 창이 overlap 만큼 겹쳐, 경계에 걸친 PII 가 어느 한 창에는 '온전히' 들어온다.
#
# -in: covered = 덮을 총 길이(문자수)
# -in: size    = 창 크기(_KOPII_MAX_CHARS)
# -in: overlap = 인접 창 겹침(_KOPII_OVERLAP, size 미만 보장됨)
#
# -out: (start, end) 쌍을 순차 yield (end 는 covered 로 클리핑)
# -out: error = 없음
#------------------------------------------------------------------
def _chunk_windows(covered, size, overlap):
    step = max(1, size - overlap)
    start = 0
    while start < covered:
        end = min(start + size, covered)
        yield start, end
        if end >= covered:      # 마지막 창이 covered 에 닿으면 종료(불필요한 꼬리 창 방지)
            break
        start += step


#------------------------------------------------------------------
# 절대 오프셋 구간 병합 건수
#=> 겹치는 창들에서 같은 실제 PII 가 두 번 잡힐 수 있다. 절대 오프셋 (start,end)
#   구간을 정렬해 '겹치면 같은 검출'로 병합하고, 병합된 구간의 개수를 센다.
#   (같은 라벨끼리만 넘겨야 한다. 서로 다른 실제 PII 는 span 이 안 겹쳐 따로 세어진다.)
#   경계에서 문맥 차로 span 이 조금 달라져도(정확히 같지 않아도) '겹치면 1건'이라
#   이중계수가 안 생긴다.
#
# -in: spans = [(start,end), ...]  동일 라벨의 절대 오프셋 구간들
#
# -out: int = 병합 후 구간(=실제 PII) 개수
# -out: error = 없음
#------------------------------------------------------------------
def _count_merged_spans(spans):
    if not spans:
        return 0
    spans = sorted(spans)
    count = 1
    cur_end = spans[0][1]
    for start, end in spans[1:]:
        if start < cur_end:            # 앞 구간과 겹침 = 같은 실제 PII(경계 중복) → 병합
            cur_end = max(cur_end, end)
        else:
            count += 1
            cur_end = end
    return count


#------------------------------------------------------------------
# ko-pii 라벨별 검출 건수 집계
#=> 활성 라벨을 검출해 라벨별 건수를 센다. 값(원문)은 세는 즉시 버려 프라이버시
#   불변식을 지킨다. 큰 문서에서도 커버리지를 잃지 않도록 검출기를 선형/초선형으로
#   나눠 처리한다(설계: A안 + 초선형 청킹, 상세는 _KOPII_MAX_CHARS 주석 참조).
#    1) ko-pii 미설치면 설치 안내와 함께 오류(A안: 폴백 없음)
#    2) 소형 문서(≤ 청크 크기)는 기존과 동일한 단일 패스(교차라벨 겹침해소 보존)
#    3) 큰 문서: 선형 검출기는 전량 1회 스캔, 초선형 검출기는 앞 _KOPII_MAX_TOTAL 까지
#       겹치는 청크로 나눠 스캔 후 절대 오프셋 병합으로 경계 중복 제거
#    4) normalize=True 로 전각/homoglyph 우회까지 방어(창마다 개별 정규화)
#   [참고] 선형/초선형을 각각 별도 스캔하므로, 초선형↔선형 라벨이 '겹치는' 드문
#   경우엔 detect_all 의 교차라벨 겹침해소가 적용되지 않아 양쪽이 각각 세어질 수 있다
#   (건수 기반 등급엔 영향 미미). 소형 문서(대다수)는 단일 패스라 완전히 동일하다.
#
# -in: text   = 스캔 대상 텍스트
# -in: labels = 셀 ko-pii 라벨 목록(활성 유형들)
#
# -out: dict = {라벨: 건수}  (검출 없으면 빈 dict)
# -out: error = ko-pii 미설치 시 RuntimeError(설치 안내)
#------------------------------------------------------------------
def _kopii_counts(text, labels):
    if not _HAVE_KOPII:
        raise RuntimeError(
            "PII 검출 엔진 ko-pii 가 설치되어 있지 않습니다. "
            "`pip install ko-pii==1.15.2` 후 다시 실행하세요. "
            f"(원인: {_KOPII_IMPORT_ERR})")
    if not text or not labels:
        return {}
    labels = set(labels)

    # (소형) 청크 크기 이하 문서는 예전과 100% 동일한 단일 패스로 처리한다.
    # 모든 라벨을 한 스캔에서 검출해 교차라벨 겹침해소(detect_all 동작)까지 그대로 보존.
    if len(text) <= _KOPII_MAX_CHARS:
        counts = {}
        for m in _detect_subset(text, labels, normalize=True):
            counts[m.label] = counts.get(m.label, 0) + 1
        return counts

    counts = {}
    # (1) 선형 검출기: 전체 텍스트를 한 번에 스캔(전량 커버, 저비용, 상한 없음).
    linear = labels - _KOPII_SUPERLINEAR
    if linear:
        for m in _detect_subset(text, linear, normalize=True):
            counts[m.label] = counts.get(m.label, 0) + 1

    # (2) 초선형 검출기: 앞 _KOPII_MAX_TOTAL 까지 겹치는 청크로 나눠 스캔하고,
    #     창 경계에 걸친 중복은 절대 오프셋 구간 병합으로 제거해 건수를 센다.
    #     (_detect_subset 은 창 문자열 기준 오프셋을 돌려주므로 base 를 더해 절대화.)
    superlinear = labels & _KOPII_SUPERLINEAR
    if superlinear:
        covered = min(len(text), _KOPII_MAX_TOTAL)
        spans_by_label = {}
        for base, end in _chunk_windows(covered, _KOPII_MAX_CHARS, _KOPII_OVERLAP):
            for m in _detect_subset(text[base:end], superlinear, normalize=True):
                spans_by_label.setdefault(m.label, []).append((base + m.start, base + m.end))
        for label, spans in spans_by_label.items():
            counts[label] = counts.get(label, 0) + _count_merged_spans(spans)

    return counts


#------------------------------------------------------------------
# PII(L1) 스캔 — ko-pii 건수 → RuleHit 목록
#=> 활성 PII 유형들을 ko-pii 로 한 번에 검출하고, 유형별로 등급 정책을 적용해
#   RuleHit 를 만든다. ko-pii 가 이미 체크섬·문맥으로 검증한 검출이므로 별도 검증
#   단계는 없고, validated_count 는 count 와 같게 둔다.
#
# -in: text        = 스캔 대상 텍스트
# -in: regex_rules = PII 유형 규칙(RegexRule) 목록
# -in: defaults    = Defaults(bulk 임계 등)
# -in: conf_map    = weight→신뢰도 표(없으면 기본)
#
# -out: (list[RuleHit], dict) = (검출된 유형별 히트, {라벨:건수} 맵)
#       건수 맵은 결합식별성(_combo_hits) 판정에 재사용한다(재스캔 방지).
# -out: error = ko-pii 미설치 시 RuntimeError 전파
#------------------------------------------------------------------
def _scan_pii(text, regex_rules, defaults, conf_map=None):
    conf_map = conf_map if conf_map is not None else _REGEX_CONF
    if not regex_rules:
        return [], {}
    # 라벨→규칙 매핑(같은 라벨을 두 규칙이 쓰면 첫 규칙만; 설정 실수 방어).
    by_label = {}
    for rule in regex_rules:
        by_label.setdefault(rule.label, rule)

    counts = _kopii_counts(text, by_label.keys())
    hits = []
    for label, count in counts.items():
        rule = by_label.get(label)
        if not rule or count <= 0:
            continue
        grade = _escalate_grade(rule, count, defaults)
        hits.append(RuleHit(
            rule_id=rule.id, name=rule.name, layer="L1",
            count=count, validated_count=count,   # ko-pii 검출 = 검증 완료로 취급
            grade=grade, seed_eligible=rule.seed_eligible,
            confidence=conf_map.get(rule.weight, 0.6),
        ))
    return hits, counts


#------------------------------------------------------------------
# 결합식별성 평가(L1-combo) — PII 조합 → 상향 히트
#=> _scan_pii 가 만든 {라벨:건수} 맵을 보고, pii_combos 규칙의 조합 조건이 성립하면
#   상향 등급 RuleHit 를 만든다. 개별 PII 히트와 '함께' rule 신호로 들어가 max 융합되어
#   '상향 전용'으로 기여한다(개별 등급을 내리지 않는다).
#    1) 건수>0 인 라벨 집합(present)을 만든다
#    2) 규칙별로 all_of(모두 존재)·of/min_types(그룹에서 N종 이상)를 판정(둘 다면 AND)
#    3) 성립하면 어떤 유형이 결합됐는지(라벨+건수)를 terms 로 남긴 COMBO 히트 생성
#   [프라이버시] terms 에 담는 건 ko-pii '라벨'과 '건수'뿐 — 원문 PII 값이 아니라 안전.
#
# -in: counts      = {ko-pii 라벨: 건수} (_scan_pii 반환값)
# -in: combo_rules = ComboRule 목록(ruleset.pii_combos)
# -in: conf_map    = weight→신뢰도 표(없으면 정규식 기본)
#
# -out: list[RuleHit] = 성립한 조합별 상향 히트(layer="COMBO", 없으면 빈 리스트)
# -out: error = 없음
#------------------------------------------------------------------
def _combo_hits(counts, combo_rules, conf_map=None):
    conf_map = conf_map if conf_map is not None else _REGEX_CONF
    if not counts or not combo_rules:
        return []
    # 한 번이라도 검출된(건수>0) 라벨만 '문서에 존재'로 본다.
    present = {lbl for lbl, c in (counts or {}).items() if c > 0}
    hits = []
    for rule in combo_rules:
        has_cond = False      # 이 규칙이 조건을 하나라도 정의했는가(무조건 규칙 오발동 방지)
        conds_met = True      # 정의된 조건을 모두 만족하는가(AND)
        matched = {}          # 성립에 기여한 (라벨→건수)

        if rule.all_of:
            has_cond = True
            if all(lbl in present for lbl in rule.all_of):
                for lbl in rule.all_of:
                    matched[lbl] = counts[lbl]
            else:
                conds_met = False

        if rule.of and rule.min_types:
            has_cond = True
            got = [lbl for lbl in rule.of if lbl in present]
            if len(got) >= rule.min_types:
                for lbl in got:
                    matched[lbl] = counts[lbl]
            else:
                conds_met = False

        # 조건 미정의(오설정)거나 하나라도 불만족이면 이 조합은 성립 안 함.
        if not has_cond or not conds_met:
            continue

        # 어떤 PII 유형이 결합됐는지 라벨 오름차순으로 근거를 남긴다(라벨+건수, 원문 아님).
        comps = tuple(sorted(matched.items()))
        hits.append(RuleHit(
            rule_id=rule.id, name=rule.name, layer="COMBO",
            count=len(comps), validated_count=len(comps),
            grade=rule.grade, seed_eligible=rule.seed_eligible,
            confidence=conf_map.get(rule.weight, 0.8),
            terms=comps,
        ))
    return hits


#------------------------------------------------------------------
# 제외어(exclude) 구간 찾기
#=> 규칙의 제외 문구들이 텍스트 어디에 있는지 (start,end) 구간 목록으로 모은다.
#   한국어는 단어 경계(띄어쓰기)가 없어 부분문자열 오탐이 잦다 — 예: '전과'가
#   '산전과 산후'(산전+과)에 걸린다. 그런 오탐을 규칙별 제외 문구로 걸러내기 위한 준비.
#
# -in: hay     = 검사 대상(대소문자 정책 적용 후) 문자열
# -in: excludes = 제외 문구 목록(rule.exclude)
# -in: ci      = case_insensitive 여부(제외 문구도 같은 정책으로 소문자화)
#
# -out: list[(start,end)] = 제외 문구들이 걸린 구간(정렬 안 함, 빈 리스트 가능)
# -out: error = 없음
#------------------------------------------------------------------
def _exclude_spans(hay, excludes, ci):
    spans = []
    for p in (excludes or ()):
        if not p:
            continue
        needle = p.lower() if ci else p
        i = hay.find(needle)
        n = len(needle)
        while i >= 0:
            spans.append((i, i + n))
            i = hay.find(needle, i + n)   # 비중첩
    return spans


#------------------------------------------------------------------
# 제외 구간 밖 등장 횟수
#=> needle 의 비중첩 등장 횟수를 세되, 제외 구간(ex_spans)에 '완전히 포함'되는
#   매치는 뺀다. 제외 구간이 없으면 str.count 와 동일하다(하위호환).
#
# -in: hay      = 검사 대상 문자열
# -in: needle   = 찾을 단어(정책 적용 후)
# -in: ex_spans = _exclude_spans() 결과
#
# -out: int = 제외 후 인정 건수
# -out: error = 없음
#------------------------------------------------------------------
def _count_outside(hay, needle, ex_spans):
    if not needle:
        return 0
    if not ex_spans:
        return hay.count(needle)
    total = 0
    n = len(needle)
    i = hay.find(needle)
    while i >= 0:
        j = i + n
        # 이 매치가 어떤 제외 구간 안에 통째로 들어가면(예: '전과' ⊂ '산전과') 세지 않는다.
        if not any(s <= i and j <= e for s, e in ex_spans):
            total += 1
        i = hay.find(needle, j)   # 비중첩(str.count 과 동일 진행)
    return total


#------------------------------------------------------------------
# 키워드 규칙 1개 스캔
#=> 규칙의 단어들이 텍스트에 몇 번 나오는지 세어 RuleHit 를 만든다. 하나도 없으면 None.
#   대소문자 무시(defaults.case_insensitive)면 양쪽을 소문자로 맞춰 센다.
#   제외어(rule.exclude)가 있으면 그 문구 안에 든 매치는 오탐으로 보고 세지 않는다.
#
# -in: text     = 스캔 대상 텍스트
# -in: rule     = KeywordRule
# -in: defaults = Defaults
#
# -out: RuleHit | None
# -out: error = 없음
#------------------------------------------------------------------
def _scan_keyword(text, rule, defaults, conf_map=None):
    conf_map = conf_map if conf_map is not None else _KEYWORD_CONF
    haystack = text.lower() if defaults.case_insensitive else text
    # 제외어(exclude) 구간을 미리 찾아 둔다 — 한국어는 띄어쓰기가 없어 부분문자열 오탐이
    # 잦다(예: '전과'가 '산전과 산후'에 걸림). 제외 문구 안에 든 매치는 세지 않는다.
    ex_spans = _exclude_spans(haystack, getattr(rule, "exclude", ()), defaults.case_insensitive)
    total = 0
    # 단어별 건수를 따로 모아, 나중에 "어떤 단어가 몇 번 걸렸는지" 근거로 남긴다.
    per_term = []
    for term in rule.terms:
        if not term:
            continue
        needle = term.lower() if defaults.case_insensitive else term
        # 비중첩 등장 횟수(str.count 동등) 중, 제외 구간에 든 매치는 뺀다.
        c = _count_outside(haystack, needle, ex_spans)
        if c:
            per_term.append((term, c))
            total += c

    if total == 0:
        return None

    grade = _escalate_grade(rule, total, defaults)
    conf = conf_map.get(rule.weight, 0.6)
    return RuleHit(
        rule_id=rule.id, name=rule.name, layer="L2",
        count=total, validated_count=0,
        grade=grade, seed_eligible=rule.seed_eligible, confidence=conf,
        terms=tuple(per_term),
    )


#------------------------------------------------------------------
# 텍스트 규칙 스캔(핵심 진입점)
#=> 정제 텍스트에 L1(정규식)·L2(키워드) 규칙을 모두 걸어 GradeSignal 을 만든다.
#    1) 모든 규칙을 돌려 RuleHit 를 모은다
#    2) 히트가 없으면 grade=None 신호 반환(=규칙상 특이사항 없음)
#    3) 히트가 있으면 max_grade 로 최종 등급, 그 등급을 만든 히트들의
#       최고 신뢰도·seed 여부를 뽑아 담는다
#
# -in: text    = CSOClassify 가 추출·정제한 문서 텍스트
# -in: ruleset = load_rules() 로 만든 RuleSet
#
# -out: GradeSignal = 규칙 기반 등급 신호(근거 hits 포함, 원문 없음)
# -out: error = 없음 (빈 텍스트/무매칭은 grade=None 으로 정상 반환)
#------------------------------------------------------------------
def scan_text(text, ruleset):
    text = text or ""
    conf = getattr(ruleset, "confidence", None) or _default_confidence()
    hits = []

    # L1: ko-pii 로 PII 유형을 한 번에 검출(유형별 RuleHit) + 건수 맵 회수.
    pii_hits, pii_counts = _scan_pii(text, ruleset.regex_rules, ruleset.defaults, conf.get("regex"))
    hits.extend(pii_hits)

    # L1-combo: 개별 PII 는 낮아도 여러 유형이 한 문서에 모이면(결합용이성) 재식별↑ → 상향.
    hits.extend(_combo_hits(pii_counts, getattr(ruleset, "pii_combos", ()), conf.get("regex")))
    for rule in ruleset.keyword_rules:
        h = _scan_keyword(text, rule, ruleset.defaults, conf.get("keyword"))
        if h:
            hits.append(h)

    if not hits:
        # 규칙상 아무것도 안 걸림 → 등급 미정(상위 융합에서 경로/전파가 결정).
        return GradeSignal(grade=None, confidence=0.0, seed_eligible=False, hits=[])

    final = max_grade(h.grade for h in hits)

    # 최종 등급을 실제로 만든(=같은 등급인) 히트만 신뢰도·seed 판단에 쓴다.
    deciding = [h for h in hits if h.grade == final]
    confidence = max(h.confidence for h in deciding)
    seed_eligible = any(h.seed_eligible for h in deciding)
    return GradeSignal(
        grade=final, confidence=confidence,
        seed_eligible=seed_eligible, hits=hits,
    )


#------------------------------------------------------------------
# 스탬프 스캔 결과 신호(Signal E)
#=> 문서에 찍힌 보안분류 표식의 최종 산출. 원문 PII 가 아니라 '정책 표식 문구'라
#   근거(term)를 그대로 남겨도 안전하다.
#
# -필드: grade         = 스탬프 기반 등급(표식 없으면 None)
# -필드: confidence    = 최종 등급을 만든 스탬프들의 최고 신뢰도(없으면 0.0)
# -필드: seed_eligible = 이 문서를 전파 seed 로 승격해도 되는가
# -필드: hits          = [{id,name,grade,mode,count,term,confidence,seed_eligible}] 근거
#                        (mode: always|head|repeat|head+repeat — 왜 스탬프로 인정됐는지)
#------------------------------------------------------------------
@dataclass(frozen=True)
class StampSignal:
    grade: str
    confidence: float
    seed_eligible: bool
    hits: list = field(default_factory=list)

    #------------------------------------------------------------------
    # 직렬화용 dict
    #=> 분류 레코드의 signals.stamp 칸에 넣을 순수 dict.
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
            "hits": [dict(h) for h in self.hits],
        }


#------------------------------------------------------------------
# 스탬프 스캔(Signal E) — "문서에 찍힌 분류 표식"으로 등급
# => 문서 본문에서 보안분류 스탬프(대외비/기밀/내부용 등)를 찾는다. 키워드(L2)와의
#   결정적 차이는 '스탬프답게 찍혔는가'를 따진다는 점이다 → 본문 한복판의 단발 언급
#   ("이 문서는 대외비가 아님")은 스탬프로 치지 않아 오탐이 낮다.
#    1) 각 규칙의 표식 문구를 (대소문자 정책대로) 텍스트에서 센다
#    2) 인정 조건 판정:
#         · always 규칙  → 1건이라도 인정(거의 스탬프 전용 문구)
#         · head        → 첫 등장이 문서 머리(앞 _STAMP_HEAD_CHARS 자) 안
#         · repeat      → _STAMP_REPEAT_MIN 회 이상 반복(페이지 워터마크 정황)
#       셋 중 아무 것도 아니면(=본문 단발 언급) 스탬프로 인정하지 않는다.
#    3) 인정된 스탬프들의 등급을 max_grade 로 합쳐 최종 등급 산정
#   [프라이버시] 표식 문구는 정책 키워드라 원문 PII 가 아니다 → term 을 근거로 남겨도 안전.
#   [융합] 결과는 build_record 에서 다른 신호와 함께 max 융합되어 '상향 전용'으로 기여한다.
#
# -in: text    = CSOClassify 가 추출·정제한 문서 텍스트
# -in: ruleset = load_rules() 로 만든 RuleSet(stamp_rules 사용)
#
# -out: StampSignal = 스탬프 기반 등급 신호(근거 hits 포함)
# -out: error = 없음 (빈 텍스트/규칙없음/무매칭은 grade=None 으로 정상 반환)
#------------------------------------------------------------------
def scan_stamp(text, ruleset):
    rules = getattr(ruleset, "stamp_rules", None)
    if not text or not rules:
        return StampSignal(grade=None, confidence=0.0, seed_eligible=False, hits=[])

    ci = ruleset.defaults.case_insensitive
    # 대소문자 무시 정책이면 양쪽을 소문자로 맞춰(count/find) 찾는다.
    hay = text.lower() if ci else text
    conf_map = (getattr(ruleset, "confidence", None) or {}).get("stamp") or _STAMP_CONF

    hits = []
    for rule in rules:
        total = 0
        head = False
        head_term = None      # 머리에서 걸린 문구(근거 표기 우선)
        first_term = None     # 아무거나 처음 걸린 문구(머리 없을 때 근거용)
        for term in rule.terms:
            if not term:
                continue
            needle = term.lower() if ci else term
            c = hay.count(needle)
            if not c:
                continue
            total += c
            if first_term is None:
                first_term = term
            # 첫 등장이 문서 '머리' 범위 안이면 head 로 인정(제목/표지 스탬프 정황).
            if not head and 0 <= hay.find(needle) < _STAMP_HEAD_CHARS:
                head, head_term = True, term

        if total == 0:
            continue

        repeat = total >= _STAMP_REPEAT_MIN
        # 인정 사유(mode) 결정 — 하나도 해당 없으면 본문 단발 언급이라 스탬프 아님.
        if rule.always:
            mode = "always"
        elif head and repeat:
            mode = "head+repeat"
        elif head:
            mode = "head"
        elif repeat:
            mode = "repeat"
        else:
            continue

        hits.append({
            "id": rule.id, "name": rule.name, "grade": rule.grade,
            "mode": mode, "count": total, "term": head_term or first_term,
            "confidence": conf_map.get(rule.weight, 0.8),
            "seed_eligible": bool(rule.seed_eligible),
        })

    if not hits:
        return StampSignal(grade=None, confidence=0.0, seed_eligible=False, hits=[])

    final = max_grade(h["grade"] for h in hits)
    # 최종 등급을 실제로 만든(=같은 등급) 스탬프만 신뢰도·seed 판단에 쓴다.
    deciding = [h for h in hits if h["grade"] == final]
    confidence = max(h["confidence"] for h in deciding)
    seed_eligible = any(h["seed_eligible"] for h in deciding)
    return StampSignal(grade=final, confidence=confidence,
                       seed_eligible=seed_eligible, hits=hits)


#------------------------------------------------------------------
# 민감정보군 스캔 결과 신호(Signal F)
#=> 법상 민감정보 키워드 매칭의 최종 산출. 근거(범주·단어)는 정책 키워드라
#   원문 PII 가 아니므로 로그/결과에 남겨도 안전하다.
#
# -필드: grade         = 민감정보 기반 등급(매칭 없으면 None)
# -필드: confidence    = 최종 등급을 만든 히트들의 최고 신뢰도(없으면 0.0)
# -필드: seed_eligible = 이 문서를 전파 seed 로 승격해도 되는가
# -필드: hits          = [{id,name,category,grade,count,terms:[(단어,건수)],confidence,seed_eligible}]
#------------------------------------------------------------------
@dataclass(frozen=True)
class SensitiveSignal:
    grade: str
    confidence: float
    seed_eligible: bool
    hits: list = field(default_factory=list)

    #------------------------------------------------------------------
    # 직렬화용 dict
    #=> 분류 레코드의 signals.sensitive 칸에 넣을 순수 dict.
    #
    # -in: 없음
    # -out: dict = {grade, confidence, seed_eligible, hits:[{...,terms:[{term,count}]}]}
    # -out: error = 없음
    #------------------------------------------------------------------
    def as_dict(self):
        out = []
        for h in self.hits:
            d = dict(h)
            # terms 는 (단어,건수) 튜플 목록 → 직렬화 친화적 dict 목록으로.
            d["terms"] = [{"term": t, "count": c} for t, c in h.get("terms", ())]
            out.append(d)
        return {
            "grade": self.grade,
            "confidence": round(self.confidence, 3),
            "seed_eligible": self.seed_eligible,
            "hits": out,
        }


#------------------------------------------------------------------
# 민감정보군 스캔(Signal F) — 법상 '민감정보' 키워드
#=> 개인정보보호법상 민감정보(건강·유전·성생활·사상/신념·정치·노조·범죄경력·인종)를
#   범주별 키워드로 찾는다. 일반 기밀사전(L2, scan_text 내부)과 '분리된' 독립 신호라,
#   결과에서 "어느 민감범주 때문에 등급이 올랐는지"가 명확히 드러난다.
#    1) 각 규칙의 단어를 (대소문자 정책대로) 텍스트에서 센다
#    2) 하나라도 걸리면 그 규칙의 등급(기본 C)을 기여 → max_grade 로 최종 등급
#   [프라이버시] 근거로 남기는 건 정책 키워드(단어)와 범주·건수뿐 — 원문 PII 아님.
#   [융합] build_record 에서 다른 신호와 max 융합되어 '상향 전용'으로 기여한다.
#
# -in: text    = CSOClassify 가 추출·정제한 문서 텍스트
# -in: ruleset = load_rules() 로 만든 RuleSet(sensitive_rules 사용)
#
# -out: SensitiveSignal = 민감정보 기반 등급 신호(근거 hits 포함)
# -out: error = 없음 (빈 텍스트/규칙없음/무매칭은 grade=None 으로 정상 반환)
#------------------------------------------------------------------
def scan_sensitive(text, ruleset):
    rules = getattr(ruleset, "sensitive_rules", None)
    if not text or not rules:
        return SensitiveSignal(grade=None, confidence=0.0, seed_eligible=False, hits=[])

    ci = ruleset.defaults.case_insensitive
    # 대소문자 무시 정책이면 양쪽을 소문자로 맞춰 센다.
    hay = text.lower() if ci else text
    conf_map = (getattr(ruleset, "confidence", None) or {}).get("sensitive") or _SENSITIVE_CONF

    hits = []
    for rule in rules:
        # 제외어 구간(오탐 방지) — 예: '전과'가 '산전과 산후'에 부분일치하는 것을 뺀다.
        ex_spans = _exclude_spans(hay, getattr(rule, "exclude", ()), ci)
        per_term = []
        total = 0
        for term in rule.terms:
            if not term:
                continue
            needle = term.lower() if ci else term
            c = _count_outside(hay, needle, ex_spans)
            if c:
                per_term.append((term, c))
                total += c
        if total == 0:
            continue
        hits.append({
            "id": rule.id, "name": rule.name, "category": rule.category,
            "grade": rule.grade, "count": total, "terms": tuple(per_term),
            "confidence": conf_map.get(rule.weight, 0.8),
            "seed_eligible": bool(rule.seed_eligible),
        })

    if not hits:
        return SensitiveSignal(grade=None, confidence=0.0, seed_eligible=False, hits=[])

    final = max_grade(h["grade"] for h in hits)
    # 최종 등급을 실제로 만든(=같은 등급) 히트만 신뢰도·seed 판단에 쓴다.
    deciding = [h for h in hits if h["grade"] == final]
    confidence = max(h["confidence"] for h in deciding)
    seed_eligible = any(h["seed_eligible"] for h in deciding)
    return SensitiveSignal(grade=final, confidence=confidence,
                           seed_eligible=seed_eligible, hits=hits)


#------------------------------------------------------------------
# 겹치는 span 병합(원문값 보존)
#=> 초선형 검출기를 겹치는 청크로 나눠 돌리면 같은 실제 PII 가 두 번 잡힐 수 있다.
#   절대 오프셋 (start,end) 이 겹치면 '같은 PII'로 보고 하나만 남긴다(대표값 유지).
#   _count_merged_spans 의 '값 보존' 판이다(같은 라벨끼리만 넘겨야 한다).
#
# -in: items = [(start, end, value), ...] 동일 라벨의 절대 오프셋 + 원문값
#
# -out: list[(start, end, value)] = 병합 후 대표 검출들(정렬됨)
# -out: error = 없음
#------------------------------------------------------------------
def _merge_spans_keep_value(items):
    if not items:
        return []
    items = sorted(items, key=lambda x: (x[0], x[1]))
    out = [items[0]]
    cur_end = items[0][1]
    for s, e, v in items[1:]:
        if s < cur_end:               # 앞 구간과 겹침 = 같은 PII → 대표(먼저 잡힌 것) 유지
            cur_end = max(cur_end, e)
        else:
            out.append((s, e, v))
            cur_end = e
    return out


#------------------------------------------------------------------
# 검출된 PII '원문 값' 수집 (옵션 --with-pii 전용)
#=> [프라이버시 예외] 기본 동작은 원문 값을 절대 저장하지 않는다(건수만). 하지만
#   사용자가 --with-pii 로 '명시 요청'하면 이 함수로 실제 검출된 PII 값을 모아
#   결과(--out)에만 싣는다(로그엔 남기지 않음 — cli._loggable_record 가 가림).
#   [커버리지] 건수 스캔(_kopii_counts)과 '완전히 동일한 범위'를 본다 — 그래야 hits 의
#   건수와 pii 값 개수가 일치한다(대형 문서에서 10K 뒤 PII 를 놓치던 버그 수정, 2026-08):
#    1) 소형(≤ 청크 크기): 단일 패스로 전부 수집(교차라벨 겹침해소 포함)
#    2) 대형: 선형 검출기는 전량 스캔, 초선형(PHONE/ADDRESS/ACCOUNT)은 앞 _KOPII_MAX_TOTAL
#       까지 겹치는 청크로 스캔 후 절대 오프셋 병합으로 경계 중복 제거(값 보존)
#    3) 규칙에 등록된 PII 라벨(regex_rules)만 검출 — 설정에 없는 유형은 안 남긴다
#    4) 검출 1건마다 {label, value, start, end} (offset 은 원문 text 기준 절대값)
#
# -in: text    = 스캔 대상 텍스트
# -in: ruleset = RuleSet(활성 PII 라벨 = regex_rules)
#
# -out: list[dict] = [{"label","value","start","end"}, ...] (없으면 빈 리스트)
# -out: error = ko-pii 미설치 시엔 이미 scan_text 가 먼저 RuntimeError → 여기선 [] 로 안전 처리
#------------------------------------------------------------------
def collect_pii(text, ruleset):
    if not _HAVE_KOPII or not text or not getattr(ruleset, "regex_rules", None):
        return []
    labels = {r.label for r in ruleset.regex_rules}
    if not labels:
        return []

    out = []
    # (소형) 청크 크기 이하: 예전과 동일한 단일 패스로 전부 수집(교차라벨 겹침해소 보존).
    if len(text) <= _KOPII_MAX_CHARS:
        for m in _detect_subset(text, labels, normalize=True):
            out.append({"label": m.label, "value": m.text, "start": m.start, "end": m.end})
        return out

    # (대형) _kopii_counts 와 동일 커버리지: 선형=전량, 초선형=앞 _KOPII_MAX_TOTAL 청킹+병합.
    linear = labels - _KOPII_SUPERLINEAR
    if linear:
        for m in _detect_subset(text, linear, normalize=True):
            out.append({"label": m.label, "value": m.text, "start": m.start, "end": m.end})

    superlinear = labels & _KOPII_SUPERLINEAR
    if superlinear:
        covered = min(len(text), _KOPII_MAX_TOTAL)
        by_label = {}
        for base, end in _chunk_windows(covered, _KOPII_MAX_CHARS, _KOPII_OVERLAP):
            for m in _detect_subset(text[base:end], superlinear, normalize=True):
                # 창 문자열 기준 오프셋을 base 로 절대화해 원문 text 기준으로 맞춘다.
                by_label.setdefault(m.label, []).append((base + m.start, base + m.end, m.text))
        for label, items in by_label.items():
            for s, e, v in _merge_spans_keep_value(items):
                out.append({"label": label, "value": v, "start": s, "end": e})

    return out


#------------------------------------------------------------------
# 기본 규칙셋 파일 경로
#=> cso_rules.yaml 의 위치를 정한다. 규칙셋은 exe 에 내장하지 않고 '외장 파일'로
#   두어(exe 옆) 재빌드 없이 규칙만 교체·배포할 수 있게 한다.
#    1) 환경변수 CSOCLASSIFY_POLICY_DIR 이 있으면 그 폴더의 cso_rules.yaml
#    2) exe 로 실행 중이면 exe 실행 경로(exe 옆)의 cso_rules.yaml  ← 기본(외장)
#    3) 소스(개발) 실행이면 트리의 resources/policy/cso_rules.yaml
#
# -in: 없음
#
# -out: path = cso_rules.yaml 절대경로(존재 여부는 확인 안 함 — 없으면 load_rules 가
#              FileNotFoundError 로 그 경로를 알려 준다)
# -out: error = 없음
#------------------------------------------------------------------
def default_rules_path():
    env = os.environ.get("CSOCLASSIFY_POLICY_DIR")
    if env:
        return os.path.join(env, "cso_rules.yaml")
    # exe(PyInstaller) 로 얼린 실행: 내장 번들 대신 'exe 옆'의 외장 규칙셋을 쓴다.
    if getattr(sys, "frozen", False):
        return os.path.join(exe_dir(), "cso_rules.yaml")
    # 소스(개발) 실행: 저장소 트리의 resources/policy/cso_rules.yaml.
    return resource_path("policy", "cso_rules.yaml")


#------------------------------------------------------------------
# 기본 seed 저장소 경로
#=> 전파 비교 기준 seed 파일(class_seed.jsonl)의 위치를 규칙셋과 '같은 규약'으로 정한다.
#   기본 분류(--file/--dir)에서 이 파일이 있으면 자동 전파에 쓴다(--seeds 로 덮어쓸 수 있음).
#    1) 환경변수 CSOCLASSIFY_POLICY_DIR 이 있으면 그 폴더의 class_seed.jsonl
#    2) exe 로 실행 중이면 exe 실행 경로(exe 옆)의 class_seed.jsonl  ← 배포 기본
#    3) 소스(개발) 실행이면 트리의 resources/policy/class_seed.jsonl
#
# -in: 없음
#
# -out: path = class_seed.jsonl 절대경로(존재 여부는 확인 안 함 — 호출부가 isfile 로 판단)
# -out: error = 없음
#------------------------------------------------------------------
def default_seed_path():
    env = os.environ.get("CSOCLASSIFY_POLICY_DIR")
    if env:
        return os.path.join(env, "class_seed.jsonl")
    if getattr(sys, "frozen", False):
        return os.path.join(exe_dir(), "class_seed.jsonl")
    return resource_path("policy", "class_seed.jsonl")


# ────────────────────────────────────────────────────────────────────────
# 규칙셋 검증 (Phase 0 — fail-open 제거)
#   yaml 에 적힌 등급 값이 정말 정의된 등급인지, bulk 상향이 뒤집히진 않았는지를
#   '문서를 한 건도 스캔하기 전에' 확인한다. 여기서 걸러야 잘못 분류된 결과가
#   절반쯤 만들어지는 최악을 막을 수 있다.
# ────────────────────────────────────────────────────────────────────────

# 등급 필드가 어느 섹션의 어느 키에 있는지 정의.
#   (YAML 섹션 키, 단일등급 필드들, (base 필드, bulk 필드) 또는 None,
#    등급 생략을 허용해 주는 조건 필드 또는 None)
# bulk 쌍이 있는 섹션만 V6(상향 방향) 검사를 한다.
# paths 만 네 번째 자리가 채워져 있다 — "acl_restricted: true 인 경로 규칙은 grade 를
# 생략할 수 있다"는 뜻이다. 그 규칙은 등급을 스스로 내지 않고, 내용·파일명 어디서도
# 신호가 없을 때만 fail-safe 로 최고 등급을 만든다(fuse 의 failsafe_acl 경로).
_GRADE_SECTIONS = (
    ("regex_pii",  (),         ("base_grade", "bulk_grade"), None),
    ("pii_combos", ("grade",), None,                         None),
    ("keywords",   (),         ("base_grade", "bulk_grade"), None),
    ("sensitive",  ("grade",), None,                         None),
    ("stamps",     ("grade",), None,                         None),
    ("paths",      ("grade",), None,                         "acl_restricted"),
)

# "키가 아예 없음"과 "키는 있는데 값이 비었음(null)"을 구분하기 위한 표식.
#   키가 없으면 로더가 기본값을 넣으므로 정상, null 이면 관리자의 실수다.
_ABSENT = object()


#------------------------------------------------------------------
# 오타 후보 제안
#=> 잘못 적힌 등급 값이 흔한 오타(소문자/앞뒤 공백)인지 보고, 맞을 법한 등급을 알려준다.
#   실제로 가장 많이 나오는 실수가 'c'(소문자)와 'C '(뒤 공백)라 이 둘만 잡아도 충분하다.
#
# -in: value = 문제가 된 원본 값(문자열이 아닐 수도 있다)
#
# -out: hint = 제안할 등급 문자열, 짚이는 게 없으면 None
# -out: error = 없음
#------------------------------------------------------------------
def _grade_hint(value):
    if not isinstance(value, str):
        return None
    # 앞뒤 공백을 없애고 대문자로 맞췄을 때 정의된 등급이 되면 그걸 제안한다.
    cand = value.strip().upper()
    return cand if cand in GRADES and cand != value else None


#------------------------------------------------------------------
# 위반 값 표시 문자열
#=> 보고문에 값을 어떻게 보여 줄지 한 곳에서 정한다. Rust 판(rules.rs 의 show)과
#   글자 하나까지 같아야 두 구현의 보고문이 일치한다.
#    · 키 자체가 없음 → "(없음)"   · null → "(null)"
#    · 문자열 → 'C ' 처럼 따옴표로 감싸 앞뒤 공백이 눈에 보이게
#    · 그 외(숫자·불리언·목록) → JSON 표기 그대로
#
# -in: value = 원본 값(_ABSENT 이면 키가 없었다는 뜻)
#
# -out: text = 보고문에 넣을 문자열
# -out: error = 없음
#------------------------------------------------------------------
def _show(value):
    if value is _ABSENT:
        return "(없음)"
    if value is None:
        return "(null)"
    if isinstance(value, str):
        return f"'{value}'"
    import json as _json
    return _json.dumps(value, ensure_ascii=False)


#------------------------------------------------------------------
# 값의 자료형 이름
#=> 오류 메시지에 쓸 자료형 이름을 YAML 쪽 용어로 통일한다. 파이썬 이름(int/dict)을
#   그대로 쓰면 Rust 판(number/mapping)과 메시지가 달라져 두 구현의 보고문이 갈린다.
#   관리자에게도 "int" 보다 "number" 가 YAML 문법과 가깝다.
#
# -in: value = 원본 값
#
# -out: name = "number" | "bool" | "list" | "mapping" | 파이썬 형이름(그 외)
# -out: error = 없음
#------------------------------------------------------------------
def _type_name(value):
    # bool 은 int 의 하위형이라 반드시 먼저 판별해야 한다.
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, (list, tuple)):
        return "list"
    if isinstance(value, dict):
        return "mapping"
    return type(value).__name__


#------------------------------------------------------------------
# 위반 1건 만들기
#=> 검증 결과를 화면 출력과 테스트가 함께 쓰는 dict 한 벌로 통일한다.
#
# -in: code    = 검증 항목 코드("V5"/"V6"/"V0")
# -in: section = YAML 섹션 키(예: "regex_pii")
# -in: rule_id = 규칙 id(없으면 "#3" 처럼 순번)
# -in: field   = 문제가 된 필드명(항목 자체 문제면 빈 문자열)
# -in: value   = 문제가 된 값
# -in: detail  = 사람이 읽을 설명 한 줄
#
# -out: dict = {code, section, rule_id, field, value, detail, hint}
# -out: error = 없음
#------------------------------------------------------------------
def _violation(code, section, rule_id, field, value, detail):
    return {
        "code": code, "section": section, "rule_id": rule_id,
        # value 는 원본(테스트·프로그램용), value_text 는 보고문용 표시 문자열.
        "field": field, "value": None if value is _ABSENT else value,
        "value_text": _show(value), "detail": detail,
        "hint": _grade_hint(value),
    }


#------------------------------------------------------------------
# 등급 필드 1개 검사 (V5)
#=> 값이 정의된 등급인지 본다. 키가 아예 없으면 로더가 기본값을 넣으므로 통과시킨다.
#    1) 키 없음 → 검사 대상 아님(None 반환)
#    2) 값이 null/문자열 아님/서열에 없음 → 위반
#
# -in: item        = 규칙 항목 dict
# -in: field       = 볼 필드명
# -in: section     = 섹션 키(메시지용)
# -in: rule_id     = 규칙 id(메시지용)
# -in: omit_okay_if = 이 불리언 필드가 True 면 등급 생략/null 을 허용한다(없으면 None).
#                    paths 의 "acl_restricted" 가 유일한 사용처
#
# -out: (violation, grade) = 위반 dict 또는 None, 그리고 검증을 통과한 등급 값(아니면 None)
# -out: error = 없음
#------------------------------------------------------------------
def _check_grade_field(item, field, section, rule_id, omit_okay_if=None):
    value = item.get(field, _ABSENT)
    # 생략(또는 null)을 조건부로 허용하는 섹션 — 현재는 paths 뿐.
    if omit_okay_if is not None and (value is _ABSENT or value is None):
        if item.get(omit_okay_if) is True:
            # 등급 없이 acl_restricted 만 있는 규칙 → 의도된 형태. 등급은 None 으로 둔다.
            return None, None
        return _violation(
            "V12", section, rule_id, field, value,
            f"등급을 생략하려면 {omit_okay_if}: true 여야 합니다"
            f"(둘 다 없으면 이 규칙은 아무 일도 하지 않습니다)"), None
    # 키 자체가 없으면 로더 기본값(항상 유효)이 쓰인다 → 검사할 것이 없다.
    if value is _ABSENT:
        return None, None
    if value is None:
        return _violation("V5", section, rule_id, field, value, "값이 비어 있습니다(null)"), None
    if not isinstance(value, str):
        return _violation("V5", section, rule_id, field, value,
                          f"등급은 문자열이어야 하는데 {_type_name(value)} 입니다"), None
    if value not in _GRADE_RANK:
        return _violation("V5", section, rule_id, field, value, "정의되지 않은 등급"), None
    return None, value


#------------------------------------------------------------------
# 규칙셋 원본(YAML) 검증 — V5·V6
#=> load_rules 가 파싱한 원시 dict 를 그대로 훑어, 등급 관련 위반을 '전부' 모은다.
#   첫 오류에서 멈추지 않는 이유는 관리자가 한 번에 고칠 수 있게 하기 위해서다.
#    1) 섹션마다 항목을 돌며 등급 필드를 검사(V5)
#    2) base/bulk 쌍이 있으면 bulk 가 base 보다 낮지 않은지 검사(V6)
#    3) paths 는 grade 를 생략할 수 있는 대신, 그럴 땐 acl_restricted: true 여야 한다(V12)
#
#   [V12 가 필요한 이유] 경로 규칙이 grade 도 acl_restricted 도 없으면 그 규칙은
#   매칭돼도 아무 등급을 만들지 않는다 — 즉 있으나 마나다. 예전에는 grade 를 생략하면
#   조용히 S 가 됐는데, 그건 관리자가 적지도 않은 등급을 시스템이 지어내는 것이라
#   더 나빴다. 이제는 둘 중 하나를 반드시 적게 한다.
#
#   [V6 가 필요한 이유] bulk_grade 는 "대량 검출 시 등급을 올린다"는 설계다.
#   그런데 base=S, bulk=O 처럼 거꾸로 적으면 주민번호가 많이 나올수록 등급이
#   내려간다. 문법상으론 멀쩡해 보여서 눈으로는 놓치기 쉬운 종류의 실수다.
#
#   [검증 대상 범위] 로더가 실제로 읽어들이는 항목만 본다. 예를 들어 regex_pii 에서
#   label 이 없는 옛 항목은 로더가 건너뛰므로 검증도 건너뛴다(쓰이지 않는 규칙을
#   두고 오류를 내면 이행 중 혼란만 준다).
#
# -in: data = yaml.safe_load 결과 dict
#
# -out: violations = 위반 dict 리스트(문제 없으면 빈 리스트)
# -out: error = 없음(예외를 던지지 않는다 — 판단은 호출자 몫)
#------------------------------------------------------------------
def validate_rules_data(data):
    violations = []
    data = data or {}

    for section, single_fields, bulk_pair, omit_okay_if in _GRADE_SECTIONS:
        items = data.get(section) or []
        # 섹션이 리스트가 아니면(예: 들여쓰기 실수로 dict 가 됨) 순회 자체가 무의미하다.
        if not isinstance(items, list):
            violations.append(_violation("V0", section, "-", "", items,
                                         "섹션이 목록(list)이 아닙니다"))
            continue

        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                violations.append(_violation("V0", section, f"#{idx}", "", item,
                                             "항목이 매핑(mapping)이 아닙니다"))
                continue
            rule_id = item.get("id") or f"#{idx}"
            # 로더가 label 없는 regex_pii 항목을 건너뛰므로 검증도 같은 기준을 쓴다.
            if section == "regex_pii" and not (item.get("label") or item.get("kopii_label")):
                continue

            for field in single_fields:
                v, _ = _check_grade_field(item, field, section, rule_id,
                                          omit_okay_if=omit_okay_if)
                if v:
                    violations.append(v)

            if bulk_pair:
                base_field, bulk_field = bulk_pair
                v_base, base_grade = _check_grade_field(item, base_field, section, rule_id)
                v_bulk, bulk_grade = _check_grade_field(item, bulk_field, section, rule_id)
                if v_base:
                    violations.append(v_base)
                if v_bulk:
                    violations.append(v_bulk)
                # 둘 다 유효할 때만 방향을 본다(하나가 오타면 V5 로 이미 보고됨).
                if base_grade and bulk_grade:
                    if _GRADE_RANK[bulk_grade] < _GRADE_RANK[base_grade]:
                        violations.append(_violation(
                            "V6", section, rule_id, bulk_field, bulk_grade,
                            f"bulk 는 상향이어야 하는데 낮습니다 "
                            f"(base={base_grade}({_GRADE_RANK[base_grade]}) → "
                            f"bulk={bulk_grade}({_GRADE_RANK[bulk_grade]}))"))

    return violations


#------------------------------------------------------------------
# 위반 목록 → 사람이 읽는 보고문
#=> 검증 코드(V5/V6/V0)별로 묶어 한 화면에 정리한다. 오타 후보가 있으면 함께 보여
#   관리자가 파일을 뒤지지 않고 바로 고칠 수 있게 한다.
#
# -in: path       = 규칙셋 파일 경로
# -in: violations = validate_rules_data 결과
#
# -out: text = 여러 줄 문자열(끝에 개행 없음)
# -out: error = 없음
#------------------------------------------------------------------
def format_violations(path, violations):
    titles = {
        "V0": "[V0] 규칙셋 구조 오류",
        "V5": "[V5] 정의되지 않은 등급 참조",
        "V6": "[V6] bulk 상향 규칙 위반 (bulk_grade 가 base_grade 보다 낮음)",
        "V12": "[V12] 경로 규칙에 grade 도 acl_restricted 도 없음",
    }
    lines = [
        f"[규칙셋 오류] {path} — 검증 실패 {len(violations)}건. 분류를 시작하지 않았습니다.",
        "",
        f"  정의된 등급: {' < '.join(GRADES)}",
        "",
    ]
    # 코드 순서를 고정해(V0 → V5 → V6) 실행할 때마다 보고서 모양이 흔들리지 않게 한다.
    for code in ("V0", "V5", "V6", "V12"):
        group = [v for v in violations if v["code"] == code]
        if not group:
            continue
        lines.append(f"  {titles.get(code, '[' + code + ']')}")
        for v in group:
            where = f"{v['section']}[{v['rule_id']}]"
            if v["field"]:
                where += f".{v['field']}"
            hint = f"   → 혹시 {v['hint']} ?" if v.get("hint") else ""
            lines.append(f"    {where} = {v.get('value_text', v['value'])}  — {v['detail']}{hint}")
        lines.append("")
    lines.append(f"  고치는 법: {path} 를 열어 위 항목을 고치세요.")
    lines.append(f"    · 등급 값은 {'/'.join(GRADES)} 중 하나여야 합니다.")
    # V12 는 '등급을 고치라'는 안내만으로는 해결이 안 되므로 항목을 하나 더 붙인다.
    if any(v["code"] == "V12" for v in violations):
        lines.append("    · 경로 규칙(paths)은 grade 또는 acl_restricted: true 중 "
                     "하나 이상이 있어야 합니다.")
    return "\n".join(lines)


#------------------------------------------------------------------
# 규칙셋 로드(YAML → RuleSet)
#=> cso_rules.yaml 을 읽어 RuleSet 을 만든다. 경로를 안 주면 default_rules_path().
#    1) YAML 파싱 → defaults 블록 해석
#    2) regex_pii 각 항목을 (ko-pii 라벨 기반) RegexRule 로
#    3) keywords 각 항목을 KeywordRule 로, paths 를 PathRule 로
#    4) 등급 값 검증(V5·V6) — 하나라도 어긋나면 RuleSet 을 만들지 않고 실패
#
#   [4단계를 왜 로드에 붙였나] 규칙셋이 잘못된 채로 스캔이 시작되면, 절반쯤
#   잘못 분류된 결과가 만들어진다. 그 결과는 겉보기에 정상이라 더 위험하다.
#   그래서 문서를 한 건도 열기 전에 여기서 막는다.
#
# -in: path     = 규칙셋 파일 경로(없으면 기본 경로)
# -in: validate = False 면 검증을 건너뛴다. 검증기 자체를 시험하거나, 잘못된
#                 규칙셋을 일부러 읽어 봐야 하는 도구용 탈출구다(기본 True)
#
# -out: RuleSet
# -out: error = 파일 없음 시 FileNotFoundError(어디에 두면 되는지 안내 메시지 포함)
# -out: error = 등급 값이 틀리면 RuleSetValidationError(위반 전체 목록 포함)
#------------------------------------------------------------------
def load_rules(path=None, validate=True):
    path = path or default_rules_path()
    # 규칙셋은 exe 옆 외장 파일이라 '깜빡 누락'이 흔하다. 원시 스택 대신 어디에 무엇을
    # 둬야 하는지 알려 주는 친절한 오류로 바꿔, 사용자가 바로 조치할 수 있게 한다.
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"규칙셋(cso_rules.yaml)을 찾을 수 없습니다: {path}\n"
            f"  · exe 와 같은 폴더에 cso_rules.yaml 을 두거나,\n"
            f"  · --rules <파일경로> 로 지정하거나,\n"
            f"  · 환경변수 CSOCLASSIFY_POLICY_DIR 로 폴더를 지정하세요."
        )
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # 등급 값 검증을 '파싱 직후·객체 생성 전'에 한다. 로더가 기본값을 채워 넣기 전의
    # 원본을 봐야 관리자가 실제로 적은 값을 그대로 짚어 줄 수 있다.
    if validate:
        violations = validate_rules_data(data)
        if violations:
            raise RuleSetValidationError(path, violations)

    d = data.get("defaults", {}) or {}
    defaults = Defaults(
        bulk_threshold=int(d.get("bulk_threshold", 5)),
        case_insensitive=bool(d.get("case_insensitive", True)),
    )

    # 신뢰도 표: 기본값 위에 yaml 의 confidence 블록을 덮어쓴다(없으면 기본값 그대로).
    confidence = _default_confidence()
    yc = data.get("confidence", {}) or {}
    for grp in ("regex", "keyword", "sensitive", "stamp", "path"):
        g = yc.get(grp) or {}
        for w in ("high", "medium", "low"):
            if w in g:
                confidence[grp][w] = float(g[w])
    if "name" in yc:
        confidence["name"] = float(yc["name"])

    regex_rules = []
    for r in data.get("regex_pii", []) or []:
        # A안: pattern/validate 대신 ko-pii 라벨을 읽는다(label 필수).
        label = r.get("label") or r.get("kopii_label")
        if not label:
            # 라벨 없는 옛 항목은 조용히 건너뛴다(설정 이행 중 안전).
            continue
        regex_rules.append(RegexRule(
            id=r["id"],
            name=r.get("name", r["id"]),
            label=label,
            base_grade=r.get("base_grade", "S"),
            bulk_grade=r.get("bulk_grade"),
            bulk_threshold=r.get("bulk_threshold"),
            weight=r.get("weight", "medium"),
            seed_eligible=bool(r.get("seed_eligible", False)),
        ))

    pii_combos = []
    for r in data.get("pii_combos", []) or []:
        pii_combos.append(ComboRule(
            id=r["id"],
            name=r.get("name", r["id"]),
            all_of=tuple(r.get("all_of") or []),
            of=tuple(r.get("of") or []),
            min_types=int(r.get("min_types", 0) or 0),
            grade=r.get("grade", "C"),
            weight=r.get("weight", "high"),
            seed_eligible=bool(r.get("seed_eligible", False)),
        ))

    keyword_rules = []
    for r in data.get("keywords", []) or []:
        keyword_rules.append(KeywordRule(
            id=r["id"],
            name=r.get("name", r["id"]),
            terms=tuple(r.get("terms") or []),
            exclude=tuple(r.get("exclude") or []),
            base_grade=r.get("base_grade", "S"),
            bulk_grade=r.get("bulk_grade"),
            bulk_threshold=r.get("bulk_threshold"),
            weight=r.get("weight", "medium"),
            seed_eligible=bool(r.get("seed_eligible", False)),
        ))

    sensitive_rules = []
    for r in data.get("sensitive", []) or []:
        sensitive_rules.append(SensitiveRule(
            id=r["id"],
            name=r.get("name", r["id"]),
            category=r.get("category", ""),
            terms=tuple(r.get("terms") or []),
            exclude=tuple(r.get("exclude") or []),
            grade=r.get("grade", "C"),
            weight=r.get("weight", "high"),
            seed_eligible=bool(r.get("seed_eligible", False)),
        ))

    stamp_rules = []
    for r in data.get("stamps", []) or []:
        stamp_rules.append(StampRule(
            id=r["id"],
            name=r.get("name", r["id"]),
            terms=tuple(r.get("terms") or []),
            grade=r.get("grade", "C"),
            weight=r.get("weight", "high"),
            seed_eligible=bool(r.get("seed_eligible", False)),
            always=bool(r.get("always", False)),
        ))

    path_rules = []
    for r in data.get("paths", []) or []:
        path_rules.append(PathRule(
            id=r["id"],
            name=r.get("name", r["id"]),
            # 경로 매칭은 구분자 정규화('/')+소문자로 일관되게 비교하도록 미리 변환.
            matches=tuple(
                m.replace("\\", "/").lower() for m in (r.get("match") or [])
            ),
            # 기본값을 주지 않는다 — 생략/null 이면 None(등급 없음)이 되고,
            # 그것이 허용되는지는 검증(V12)이 acl_restricted 와 함께 판단한다.
            grade=r.get("grade"),
            acl_restricted=bool(r.get("acl_restricted", False)),
            weight=r.get("weight", "high"),
            seed_eligible=bool(r.get("seed_eligible", False)),
        ))

    return RuleSet(
        version=str(data.get("version", "unknown")),
        defaults=defaults,
        regex_rules=regex_rules,
        pii_combos=pii_combos,
        keyword_rules=keyword_rules,
        sensitive_rules=sensitive_rules,
        stamp_rules=stamp_rules,
        path_rules=path_rules,
        confidence=confidence,
    )
