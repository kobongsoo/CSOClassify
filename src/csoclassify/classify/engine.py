#------------------------------------------------------------------
# 분류 레코드 조립 (Signal A → 문서 등급 레코드)
#=> 규칙 스캔 결과(GradeSignal)를 감사·인덱싱에 쓰기 좋은 "분류 레코드 dict"로
#   조립한다. 지금은 규칙 신호 하나뿐이므로 최종 등급 = 규칙 등급이며, 규칙이
#   아무것도 못 잡으면 (옵션에 따라) fail-safe 기본등급을 준다.
#   [경계] 경로/ACL(Signal C)·임베딩 전파(Signal B)는 아직 없다 → 이 레코드는
#   그 신호들이 붙으면 fuse 단계에서 상향될 수 있는 "1차 등급"이다.
#------------------------------------------------------------------

import datetime
from types import SimpleNamespace

from .rules import scan_text, scan_stamp, scan_sensitive, collect_pii
from .context import scan_path, scan_filename
from .fuse import fuse_signals
from .propagate import SeedIndex, propagate


#------------------------------------------------------------------
# 현재 시각 ISO 문자열
#=> 레코드에 찍을 생성 시각을 지역시간 오프셋 포함 ISO8601(초 단위)로 만든다.
#   테스트는 시각을 직접 주입하므로 이 함수는 CLI 등 실사용에서만 쓴다.
#
# -in: 없음
#
# -out: str = 예 "2026-08-12T10:30:00+09:00"
# -out: error = 없음
#------------------------------------------------------------------
def now_iso():
    # astimezone() 로 로컬 타임존 오프셋을 붙여, 나중에 시점 비교가 명확하게 한다.
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


#------------------------------------------------------------------
# 분류 레코드 조립(핵심)
#=> 다섯 신호(규칙·민감정보·스탬프·경로·파일명)를 모아 융합하고, 설계서의 분류 레코드
#   스키마에 맞춰 dict 를 만든다.
#    1) 규칙 스캔(Signal A, 텍스트) + 민감정보군(Signal F, 법상 민감정보)
#       + 스탬프(Signal E, 보안분류 표식) + 경로(Signal C) + 파일명(Signal D)
#    2) fuse_signals 로 보수적 최댓값 + fail-safe 융합 → 최종 등급/근거
#    3) signals 에 다섯 신호의 근거를 모두 담아 감사 가능하게 한다
#   [프라이버시] 모든 신호 dict 는 원문 값이 없다(건수·규칙 id·경로 규칙명만).
#   [경계] 임베딩 전파(Signal B)는 아직 없다 → 붙으면 fuse 후보로 추가만 하면 된다.
#
# -in: file     = 문서 경로(식별 + 경로/파일명 신호의 입력)
# -in: text     = CSOClassify 가 추출·정제한 텍스트(규칙 스캔 입력)
# -in: ruleset  = load_rules() 로 만든 RuleSet
# -in: ts       = 레코드 시각 문자열(없으면 None 으로 둠 — 테스트 결정성 위해 주입식)
# -in: failsafe = 어떤 신호도 없을 때 부여할 기본등급("S"/"C" 등, 없으면 None)
# -in: vector   = 이 문서의 임베딩 벡터(있으면 레코드에 실어 전파·인덱싱에 재사용)
# -in: embed_sig = 임베딩 전파 신호(EmbedSignal). 있으면 융합 후보로 추가(상향전용)
# -in: rules_enabled = True(기본)면 규칙 신호를 모두 돌린다. False 면(=벡터-only 분류)
#                      규칙 신호를 아예 돌리지 않고 '보류(grade=None)' 레코드만 만든다
#                      → 등급은 이후 임베딩 전파(propagate_records)가 seed 비교로 정한다.
#
# -out: dict = {file, grade, confidence, method, decided_by, seed_eligible,
#               signals:{rule,sensitive,stamp,path,name[,embed]}, [vector,] rule_version, ts}
# -out: error = 없음 (스캔·융합은 예외를 내지 않음)
#------------------------------------------------------------------
def build_record(file, text, ruleset, ts=None, failsafe=None, vector=None, embed_sig=None,
                 with_pii=False, rules_enabled=True):
    # [벡터-only 모드] 규칙 신호를 하나도 돌리지 않고 '보류' 레코드만 만든다. failsafe 도
    # 여기선 적용하지 않는다(전파가 못 정했을 때 propagate_records 재융합에서 적용됨).
    if not rules_enabled:
        rec = {
            "file": file, "grade": None, "confidence": 0.0,
            "method": "unclassified", "decided_by": [], "seed_eligible": False,
            "signals": {}, "rule_version": ruleset.version, "ts": ts,
        }
        if vector is not None:
            rec["vector"] = list(vector)
        return rec

    rule_sig = scan_text(text, ruleset)       # Signal A (내용)
    sens_sig = scan_sensitive(text, ruleset)  # Signal F (법상 민감정보군)
    stamp_sig = scan_stamp(text, ruleset)     # Signal E (보안분류 스탬프)
    path_sig = scan_path(file, ruleset)       # Signal C (경로)
    name_sig = scan_filename(file, ruleset)   # Signal D (파일명)

    # 이름표를 붙여 융합에 넘긴다(어떤 신호가 결정했는지 추적하기 위해).
    signals = [("rule", rule_sig), ("sensitive", sens_sig), ("stamp", stamp_sig),
               ("path", path_sig), ("name", name_sig)]
    if embed_sig is not None:
        # embed 는 그냥 후보로 추가만 하면, 융합이 max 라 자동으로 "상향 전용"이 된다.
        signals.append(("embed", embed_sig))
    fused = fuse_signals(signals, failsafe=failsafe)

    rec = {
        "file": file,
        "grade": fused.grade,
        "confidence": round(fused.confidence, 3),
        "method": fused.method,
        "decided_by": fused.decided_by,
        # 전파(Signal B)의 seed 수집 필터로 바로 쓰도록 최상위에도 노출.
        "seed_eligible": fused.seed_eligible,
        "signals": {
            "rule": rule_sig.as_dict(),
            "sensitive": sens_sig.as_dict(),
            "stamp": stamp_sig.as_dict(),
            "path": path_sig.as_dict(),
            "name": name_sig.as_dict(),
        },
        "rule_version": ruleset.version,
        "ts": ts,
    }
    if embed_sig is not None:
        rec["signals"]["embed"] = embed_sig.as_dict()
    # 벡터는 전파(2차)와 RAG 인덱싱에 재사용하도록 레코드에 그대로 싣는다.
    if vector is not None:
        rec["vector"] = list(vector)
    # [프라이버시 예외] --with-pii 로 명시 요청 시에만 검출된 원문 PII 값을 싣는다.
    # 기본은 건수만(원문 미저장). 로그엔 안 남기고(_loggable_record 가 가림) --out 파일에만.
    if with_pii:
        rec["pii"] = collect_pii(text, ruleset)
    return rec


#------------------------------------------------------------------
# 저장된 신호 dict → 융합용 객체
#=> 1차 분류 레코드의 signals[name] dict 를 fuse_signals 가 읽을 수 있는 최소
#   속성 객체로 되살린다(재융합 때 사용).
#
# -in: d = 신호 dict (grade/confidence/seed_eligible/(acl_restricted))
#
# -out: SimpleNamespace = .grade/.confidence/.seed_eligible/.acl_restricted
# -out: error = 없음
#------------------------------------------------------------------
def _ns_from_signal(d):
    return SimpleNamespace(
        grade=d.get("grade"),
        confidence=d.get("confidence", 0.0),
        seed_eligible=d.get("seed_eligible", False),
        acl_restricted=d.get("acl_restricted", False),
    )


#------------------------------------------------------------------
# 배치 임베딩 전파(Signal B, 2차 패스) — 핵심
#=> 벡터가 실린 1차 레코드들을 받아, 고신뢰 등급을 seed 로 삼아 "보류(grade=None)"
#   문서에만 라벨을 전파한다. 이미 등급이 확정된 문서는 손대지 않는다.
#    1) seed 인덱스 구성(seed_eligible + 등급 + 벡터) — 모든 레코드에서 씨앗 수집
#    2) grade=None(보류) 레코드만 propagate → embed 신호
#    3) rule/path/name(+embed) 재융합 → 등급/근거 갱신
#   [왜 보류만?] 임베딩은 주제는 잡아도 민감도 자체는 보증 못 한다(공개문서가
#   기밀문서와 주제상 이웃일 수 있음). 그래서 규칙/경로로 이미 확정된 등급을
#   임베딩으로 흔들지 않고(공개문서 과분류 방지), 근거가 없어 보류된 문서를
#   구제하는 데만 쓴다. seed 가 자기 자신을 이웃으로 잡는 자기매칭도 사라진다.
#
# -in: records    = 1차 분류 레코드 리스트(각 rec 에 'grade','vector')
# -in: seed_index = 외부 큐레이션 seed(cso_seed.jsonl 로 만든 것). 주면 코퍼스 내부
#                   seed(seed_eligible 문서)와 "합쳐서" 비교 기준으로 쓰고, None 이면
#                   내부 seed 만 쓴다.
# -in: failsafe   = 재융합 시에도 무신호면 부여할 기본등급(없으면 None)
# -in: kw         = propagate() 임계값 override(k/dup_threshold/min_sim/min_share)
#
# -out: (records, stats) = 갱신된 레코드들, 통계 dict
#       stats = {seeds, already_graded, embed_decided, still_unclassified, no_vector}
# -out: error = 없음
#------------------------------------------------------------------
def propagate_records(records, seed_index=None, failsafe=None, **kw):
    # 비교 기준 seed = 코퍼스 내부 seed(seed_eligible 문서) + (있으면) 외부 큐레이션 seed.
    # 외부 seed 를 주면 '외부 + 내부' 양쪽과 비교하고(사용자 정책), 없으면 내부만 쓴다.
    internal = SeedIndex.from_records(records)
    if seed_index is not None and seed_index.size:
        seeds = SeedIndex.merge(seed_index, internal)
    else:
        seeds = internal
    stats = {"seeds": seeds.size, "already_graded": 0,
             "embed_decided": 0, "still_unclassified": 0, "no_vector": 0}

    for rec in records:
        # 이미 등급이 확정된 문서는 전파 대상이 아니다 → 그대로 둔다(과분류 방지).
        if rec.get("grade") is not None:
            stats["already_graded"] += 1
            continue

        vec = rec.get("vector")
        if vec is None:
            # 보류인데 벡터도 없으면 전파 불가 → 여전히 미분류.
            stats["no_vector"] += 1
            continue

        esig = propagate(vec, seeds, **kw)
        sig = rec.setdefault("signals", {})
        sig["embed"] = esig.as_dict()

        # 저장된 rule/sensitive/stamp/path/name 신호를 되살려 embed 와 함께 재융합.
        parts = [(name, _ns_from_signal(sig[name]))
                 for name in ("rule", "sensitive", "stamp", "path", "name") if name in sig]
        parts.append(("embed", esig))
        fused = fuse_signals(parts, failsafe=failsafe)

        rec["grade"] = fused.grade
        rec["confidence"] = round(fused.confidence, 3)
        rec["method"] = fused.method
        rec["decided_by"] = fused.decided_by
        rec["seed_eligible"] = fused.seed_eligible

        if "embed" in fused.decided_by:
            stats["embed_decided"] += 1
        if fused.grade is None:
            stats["still_unclassified"] += 1

    return records, stats
