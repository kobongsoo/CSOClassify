#------------------------------------------------------------------
# 규칙 스캔(Signal A) 단위 테스트
#=> csoclassify.classify.rules 가 정규식(L1)·기밀사전(L2)으로 텍스트를 C/S/O 로
#   올바로 분류하는지, 그리고 오탐 방지 장치(체크섬·문맥·구체형 키워드)가
#   동작하는지 검증한다. 실제 문서·모델·네트워크 없이 합성 텍스트로만 돈다.
#   [주의] 여기 쓰인 주민번호/카드/사업자번호는 체크섬만 유효한 "가짜" 예시로,
#   실제 개인정보가 아니다.
#------------------------------------------------------------------

import pytest

from csoclassify.classify import rules as R

# 체크섬이 유효한(형식+검증 통과) 가짜 예시 값들.
VALID_RRN = "900101-1234568"      # ko-pii RRN 검출(체크섬 통과)
INVALID_RRN = "900101-1234561"    # 형식만 맞고 체크섬 실패(2020 임의화 유사 상황)
VALID_CARD = "4539-5787-6362-1486"   # Luhn 통과
INVALID_CARD = "4539-5787-6362-1480" # Luhn 실패
# 처방전 발행번호 = YYYYMMDD + 4자리 일련번호 = 정확히 12자리(2024-03-15 + 0001).
# ko-pii 처방전 검출기는 앞에 "처방번호" 같은 키워드 anchor 가 있어야 인정한다.
VALID_PRESCRIPTION = "202403150001"


#------------------------------------------------------------------
# 규칙셋 로드 픽스처
#=> 기본 cso_rules.yaml 을 한 번 로드해 모든 테스트가 공유한다.
#
# -in: 없음
# -out: RuleSet
# -out: error = 파일 없으면 로드 단계에서 실패(테스트 에러로 표면화)
#------------------------------------------------------------------
@pytest.fixture(scope="module")
def rs():
    return R.load_rules()


#------------------------------------------------------------------
# 로드 정상성
#=> 규칙셋이 비어 있지 않고 정규식·키워드가 모두 실렸는지 확인한다.
#
# -in: rs = 규칙셋 픽스처
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_load_rules(rs):
    assert rs.version.startswith("cso-")
    assert len(rs.regex_rules) >= 5
    assert len(rs.keyword_rules) >= 5
    ids = {r.id for r in rs.regex_rules} | {r.id for r in rs.keyword_rules}
    assert {"rrn", "credit_card", "mark_confidential", "project_codenames"} <= ids


#------------------------------------------------------------------
# 등급 최댓값 유틸
#=> C>S>O 서열과 None 무시가 맞는지 확인한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_max_grade():
    assert R.max_grade(["O", "S", None]) == "S"
    assert R.max_grade(["O", "C"]) == "C"
    assert R.max_grade([None, None]) is None
    assert R.max_grade([]) is None


#------------------------------------------------------------------
# 특이사항 없는 문서 → 등급 미정
#=> PII·키워드가 전혀 없으면 grade=None(규칙상 판단 보류)이어야 한다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_benign_text_no_grade(rs):
    sig = R.scan_text("다음 주 회의 일정을 공유드립니다. 장소는 3층 대회의실입니다.", rs)
    assert sig.grade is None
    assert sig.hits == []


#------------------------------------------------------------------
# 유효 주민번호 1건 → S, seed 승격
#=> 체크섬 통과 주민번호가 1건이면 base_grade S 이고, 검증 통과라 seed_eligible=True.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_valid_rrn_single_S(rs):
    sig = R.scan_text(f"직원 정보: 주민등록번호 {VALID_RRN}", rs)
    assert sig.grade == "S"
    rrn_hit = next(h for h in sig.hits if h.rule_id == "rrn")
    assert rrn_hit.validated_count == 1
    assert rrn_hit.seed_eligible is True


#------------------------------------------------------------------
# 유효 주민번호 대량(5건) → C 상향
#=> bulk_threshold(기본 5) 이상이면 명단/대장 정황으로 보아 C 로 상향된다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_bulk_rrn_escalates_C(rs):
    text = "명단\n" + "\n".join(f"{i}. {VALID_RRN}" for i in range(5))
    sig = R.scan_text(text, rs)
    assert sig.grade == "C"
    rrn_hit = next(h for h in sig.hits if h.rule_id == "rrn")
    assert rrn_hit.count == 5


#------------------------------------------------------------------
# 옛 체크섬 미통과 주민번호도 ko-pii 는 진짜 RRN 으로 인정(A안)
#=> 2020년 이후 주민번호는 뒷자리 임의화로 옛 체크섬을 통과하지 못한다. ko-pii 는
#   이를 오탐이 아니라 실제 RRN 으로 검출하므로, 카운트되고 등급(S)도 정상 부여된다.
#   (과거의 soft/validated 구분은 A안에서 ko-pii 검출로 대체됨)
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_rrn_2020_style_detected(rs):
    sig = R.scan_text(f"직원 주민등록번호 {INVALID_RRN}", rs)
    rrn_hit = next(h for h in sig.hits if h.rule_id == "rrn")
    assert rrn_hit.count == 1
    assert sig.grade == "S"


#------------------------------------------------------------------
# 유효 카드(Luhn) → S, 무효 카드 → 매칭 폐기
#=> credit_card 는 hard 검증이라 Luhn 통과분만 히트가 되고, 실패하면 히트가 없어야 한다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_credit_card_luhn(rs):
    ok = R.scan_text(f"결제 카드 {VALID_CARD}", rs)
    assert any(h.rule_id == "credit_card" for h in ok.hits)
    assert ok.grade == "S"

    bad = R.scan_text(f"결제 카드 {INVALID_CARD}", rs)
    assert not any(h.rule_id == "credit_card" for h in bad.hits)


#------------------------------------------------------------------
# 처방전 발행번호 검출 → C (라벨 오타 회귀 방지)
#=> 유효 형식(키워드 anchor + 12자리 발행번호)의 처방전이 실제로 검출되고 C 로
#   잡히는지 확인한다. 과거 cso_rules.yaml 이 검출기 라벨(PRESCRIPTION_ID)과 다른
#   'PRESCRIPTION' 을 써서 한 건도 안 잡히던 회귀를 이 테스트가 막는다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_prescription_detected_C(rs):
    sig = R.scan_text(f"환자 안내문. 처방번호 {VALID_PRESCRIPTION} 로 조회하세요.", rs)
    assert sig.grade == "C"
    presc_hit = next(h for h in sig.hits if h.rule_id == "prescription")
    assert presc_hit.count == 1
    assert presc_hit.grade == "C"


#------------------------------------------------------------------
# 처방전 키워드 anchor 없으면 미검출(오탐 방지)
#=> 12자리 숫자만 있고 "처방번호" 같은 키워드가 없으면 처방전으로 잡히면 안 된다.
#   (ko-pii 처방전 검출기의 anchor 조건이 살아 있는지 확인)
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_prescription_needs_keyword_anchor(rs):
    sig = R.scan_text(f"임의 코드 {VALID_PRESCRIPTION} 참고", rs)
    assert not any(h.rule_id == "prescription" for h in sig.hits)


#------------------------------------------------------------------
# 규칙 라벨 == 검출기 라벨 (구조적 회귀 가드)
#=> cso_rules.yaml 의 각 PII 규칙 label 이 ko-pii 검출기가 실제로 내보내는 라벨과
#   일치하는지 검사한다. 처방전 오타(PRESCRIPTION vs PRESCRIPTION_ID)처럼 "설정과
#   엔진 라벨이 어긋나 조용히 미검출되는" 부류의 버그를 일반적으로 차단한다.
#   _detect_subset 이 아는 라벨(_MAPPED_LABELS)에 규칙 label 이 모두 들어 있어야
#   폴백 없이 최적 경로로 검출된다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError / ko-pii 미설치 시 skip
#------------------------------------------------------------------
def test_rule_labels_known_to_detectors(rs):
    if not R._HAVE_KOPII:
        pytest.skip("ko-pii 미설치 환경")
    labels = {r.label for r in rs.regex_rules}
    unknown = labels - R._MAPPED_LABELS
    assert not unknown, f"검출기가 모르는 규칙 라벨(설정 오타 의심): {unknown}"


#------------------------------------------------------------------
# _detect_subset == detect_all (성능 패치 동작 동일성 가드)
#=> 필요한 검출기만 도는 축소 래퍼가 전체 엔진(detect_all)과 같은 검출 결과를
#   내는지 확인한다. 우회(전각/제로폭) 입력 포함. 성능 패치가 결과를 바꾸는
#   회귀를 막는다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError / ko-pii 미설치 시 skip
#------------------------------------------------------------------
def test_detect_subset_matches_full(rs):
    if not R._HAVE_KOPII:
        pytest.skip("ko-pii 미설치 환경")
    from ko_pii import detect_all as full
    labels = list({r.label for r in rs.regex_rules})

    def counts(dets):
        c = {}
        for m in dets:
            c[m.label] = c.get(m.label, 0) + 1
        return c

    samples = [
        f"주민 {VALID_RRN} 카드 {VALID_CARD} 처방번호 {VALID_PRESCRIPTION}",
        "서울특별시 강남구 테헤란로 152 여권 M12345678 010-1234-5678",
        "전각우회 ９００１０１－１２３４５６８ 및 hong@example.com",
        "제로폭​주민​" + VALID_RRN,
        "일반 문서. 개인정보 없음.",
        "",
    ]
    for s in samples:
        old = counts(full(s, include=labels, normalize=True))
        new = counts(R._detect_subset(s, labels, normalize=True))
        assert old == new, f"불일치: {s[:40]!r}\n old={old}\n new={new}"


#------------------------------------------------------------------
# 주소 프리필터: 실제 주소는 절대 skip 안 함 (미탐 방지 — 안전 불변식)
#=> _address_prefilter 는 address.detect 실행을 건너뛰는 최적화라, 실제 주소가
#   있는데 skip 하면 치명적 미탐이 된다. 다양한 형식(도로명/지번/대화체)의 주소가
#   모두 프리필터를 통과(FIRE)하고, 그 문서에서 ADDRESS 가 실제로 검출되는지 본다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError / ko-pii 미설치 시 skip
#------------------------------------------------------------------
def test_address_prefilter_no_false_negative(rs):
    if not R._HAVE_KOPII:
        pytest.skip("ko-pii 미설치 환경")
    addresses = [
        "서울특별시 강남구 테헤란로 152",
        "성남시 분당구 판교로 235 12층",
        "주소: 세종대로 209",
        "경기도 수원시 영통구 매영로 1234",
        "본적은 청평면 청평리 45-6 입니다",
        "예전에 응암동에서 살던 기억",
        "자택은 해운대구 우동 1435",
    ]
    for s in addresses:
        assert R._address_prefilter(s) is True, f"실제 주소인데 프리필터가 skip: {s!r}"
        sig = R.scan_text(s, rs)
        assert any(h.rule_id == "address" for h in sig.hits), f"주소 미검출: {s!r}"


#------------------------------------------------------------------
# 주소 프리필터: 주소 없는 문서는 skip (선별성 — 절감 효과 보장)
#=> 숫자가 섞인 일반 업무 문서(주소 아님)는 프리필터가 skip 해 비싼 address.detect
#   를 건너뛰어야 한다. skip 여부와 무관하게 결과(등급/히트)는 게이트 없는 전체
#   엔진과 동일해야 하므로, 그 동일성도 함께 확인한다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError / ko-pii 미설치 시 skip
#------------------------------------------------------------------
def test_address_prefilter_skips_addressfree(rs):
    if not R._HAVE_KOPII:
        pytest.skip("ko-pii 미설치 환경")
    free = ("2026년 1분기 매출 목표 대비 3.5% 초과. 지표 5개 중 4개 안정. "
            "15일 3시 회의. 자료 12건 시스템 등록 완료. 예산 2억 집행.")
    assert R._address_prefilter(free) is False, "주소 없는 문서인데 프리필터가 FIRE"
    # 게이트가 결과를 바꾸지 않는지: 전체 엔진과 검출 집합 동일해야 한다.
    from ko_pii import detect_all as full
    labels = list({r.label for r in rs.regex_rules})
    def counts(dets):
        c = {}
        for m in dets:
            c[m.label] = c.get(m.label, 0) + 1
        return c
    assert counts(full(free, include=labels, normalize=True)) == \
           counts(R._detect_subset(free, labels, normalize=True))


#------------------------------------------------------------------
# 계좌번호 문맥 조건
#=> 계좌 패턴은 오탐이 심해 "계좌/예금주" 같은 라벨 근처일 때만 인정한다.
#   라벨이 있으면 히트, 없으면 히트가 없어야 한다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_bank_account_context(rs):
    with_ctx = R.scan_text("입금 계좌 123-456-7890 으로 보내주세요", rs)
    assert any(h.rule_id == "bank_account" for h in with_ctx.hits)

    no_ctx = R.scan_text("관리코드 123-456-7890 참고 바랍니다", rs)
    assert not any(h.rule_id == "bank_account" for h in no_ctx.hits)


#------------------------------------------------------------------
# 기밀 표식 키워드 → C
#=> "대외비" 같은 기밀 표식은 C 이고 seed 로 쓸 수 있어야 한다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_keyword_confidential_C(rs):
    sig = R.scan_text("본 문서는 대외비이므로 외부 유출을 금합니다.", rs)
    assert sig.grade == "C"
    assert sig.seed_eligible is True


#------------------------------------------------------------------
# 조직 특화 코드명 → C
#=> 프로젝트/제품명(엠파워 등)이 언급되면 C 로 잡혀야 한다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_project_codename_C(rs):
    sig = R.scan_text("엠파워 서버에 최신 빌드를 업로드했습니다.", rs)
    assert sig.grade == "C"
    assert any(h.rule_id == "project_codenames" for h in sig.hits)


#------------------------------------------------------------------
# 사내 민감 주제(구체형) → S
#=> "인사발령" 같은 구체형은 S 로 잡혀야 한다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_internal_topic_S(rs):
    sig = R.scan_text("이번 정기 인사발령 결과를 안내드립니다.", rs)
    assert sig.grade == "S"
    assert any(h.rule_id == "internal_topics" for h in sig.hits)


#------------------------------------------------------------------
# 인사(greeting) 오탐 방지
#=> "인사드립니다/새해 인사"는 greeting 이라 internal_topics 로 잡히면 안 된다.
#   (구체형 키워드로 좁힌 효과 검증) — PII·다른 키워드도 없어 grade=None.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_greeting_not_flagged(rs):
    sig = R.scan_text("임직원 여러분께 새해 인사드립니다. 건강하세요.", rs)
    assert not any(h.rule_id == "internal_topics" for h in sig.hits)
    assert sig.grade is None


#------------------------------------------------------------------
# 키워드 히트는 매칭 단어를 노출한다(감사/튜닝용)
#=> 어떤 단어가 몇 번 걸렸는지 terms 에 남아야 한다(키워드는 규칙사전 단어라 안전).
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_keyword_hit_exposes_terms(rs):
    sig = R.scan_text("본 문서는 대외비이며 극비 자료입니다. 대외비 재확인.", rs)
    hit = next(h for h in sig.hits if h.rule_id == "mark_confidential")
    terms = {t: c for t, c in hit.terms}
    assert terms.get("대외비") == 2
    assert terms.get("극비") == 1
    # as_dict 에도 단어별 건수가 실려야 한다.
    d = sig.as_dict()
    mc = next(h for h in d["hits"] if h["id"] == "mark_confidential")
    assert {"term": "대외비", "count": 2} in mc["terms"]


#------------------------------------------------------------------
# 정규식(PII) 히트는 단어를 노출하지 않는다(프라이버시)
#=> rrn 등 PII 히트의 terms 는 항상 비어 있어야 한다(매칭값=원문이라 노출 금지).
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_regex_hit_terms_empty(rs):
    sig = R.scan_text(f"주민등록번호 {VALID_RRN}", rs)
    rrn = next(h for h in sig.hits if h.rule_id == "rrn")
    assert rrn.terms == ()
    d = sig.as_dict()
    rd = next(h for h in d["hits"] if h["id"] == "rrn")
    assert rd["terms"] == []


#------------------------------------------------------------------
# 결과 dict 에 원문 값이 없다(프라이버시 불변식)
#=> as_dict() 산출물엔 매칭 원문(주민번호 숫자 등)이 절대 들어가면 안 된다.
#   건수·규칙 id 만 있어야 한다.
#
# -in: rs = 규칙셋
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_as_dict_has_no_raw_values(rs):
    sig = R.scan_text(f"주민등록번호 {VALID_RRN} 포함 문서", rs)
    d = sig.as_dict()
    blob = str(d)
    # 원문 숫자열(하이픈 유무 모두)이 결과 어디에도 남지 않아야 한다.
    assert VALID_RRN not in blob
    assert VALID_RRN.replace("-", "") not in blob
    assert d["grade"] == "S"
    assert any(hit["id"] == "rrn" for hit in d["hits"])


#------------------------------------------------------------------
# 초선형 검출기 청킹: 청크 크기 뒤쪽 PII 도 검출 (커버리지 회복)
#=> 예전엔 앞 _KOPII_MAX_CHARS 만 스캔해 그 뒤 전화/주소를 놓쳤다. 이제 청크로 나눠
#   돌리므로 청크 크기 뒤에 있는 전화도 검출돼야 한다. 큰 입력 없이 상수를 작게
#   바꿔(monkeypatch) 청킹 경로를 태운다.
#
# -in: monkeypatch = 상수 축소용
# -out: 없음(assert) / ko-pii 미설치 시 skip
#------------------------------------------------------------------
def test_kopii_chunking_covers_beyond_chunk(monkeypatch):
    if not R._HAVE_KOPII:
        pytest.skip("ko-pii 미설치 환경")
    monkeypatch.setattr(R, "_KOPII_MAX_CHARS", 200)
    monkeypatch.setattr(R, "_KOPII_MAX_TOTAL", 100_000)
    monkeypatch.setattr(R, "_KOPII_OVERLAP", 64)
    filler = "가나다라 " * 100           # 숫자 없는 한글 채움(오탐 없음)
    # 전화를 첫 청크(200자) 한참 뒤에 둔다 → 예전엔 미검출, 지금은 검출돼야 함.
    text = filler[:400] + " 연락처 010-9876-5432 " + filler[400:]
    counts = R._kopii_counts(text, {"PHONE"})
    assert counts.get("PHONE", 0) >= 1


#------------------------------------------------------------------
# 청크 오버랩 경계 중복 제거: 두 창에 걸친 같은 PII 는 1건
#=> 오버랩 구간 안에 전화 하나만 두면 인접 두 창이 모두 '온전히' 잡는다. 절대 오프셋
#   병합으로 최종 1건이어야 한다(이중계수 방지).
#
# -in: monkeypatch = 상수 축소용
# -out: 없음(assert) / ko-pii 미설치 시 skip
#------------------------------------------------------------------
def test_kopii_chunking_dedup_overlap(monkeypatch):
    if not R._HAVE_KOPII:
        pytest.skip("ko-pii 미설치 환경")
    monkeypatch.setattr(R, "_KOPII_MAX_CHARS", 200)
    monkeypatch.setattr(R, "_KOPII_MAX_TOTAL", 100_000)
    monkeypatch.setattr(R, "_KOPII_OVERLAP", 64)
    # 창1[0,200]·창2[136,336], 오버랩 [136,200]. 전화를 offset 150 에 둬 두 창 모두
    # 온전히 보게 한다 → 병합 후 정확히 1건.
    text = ("가" * 150) + "010-1234-5678" + ("나" * 200)
    counts = R._kopii_counts(text, {"PHONE"})
    assert counts.get("PHONE", 0) == 1


#------------------------------------------------------------------
# 선형=전량 / 초선형=상한: 총상한 밖에서 선형은 검출, 초선형은 미검출
#=> 초선형(전화)은 앞 _KOPII_MAX_TOTAL 까지만 스캔(지연 캡)하고, 선형(이메일)은 상한과
#   무관하게 전량 스캔한다. 둘 다 상한 밖에 둬 이 분리를 검증한다.
#
# -in: monkeypatch = 상수 축소용
# -out: 없음(assert) / ko-pii 미설치 시 skip
#------------------------------------------------------------------
def test_kopii_superlinear_capped_linear_full(monkeypatch):
    if not R._HAVE_KOPII:
        pytest.skip("ko-pii 미설치 환경")
    monkeypatch.setattr(R, "_KOPII_MAX_CHARS", 200)
    monkeypatch.setattr(R, "_KOPII_MAX_TOTAL", 400)     # 초선형은 앞 400자만
    monkeypatch.setattr(R, "_KOPII_OVERLAP", 0)
    filler = "가" * 500                                  # 상한(400) 밖으로 밀어냄
    text = filler + " 연락처 010-1234-5678 메일 hong@example.com " + ("나" * 30)
    counts = R._kopii_counts(text, {"PHONE", "EMAIL"})
    assert counts.get("PHONE", 0) == 0                  # 초선형: 상한 밖이라 미검출(지연 캡)
    assert counts.get("EMAIL", 0) >= 1                  # 선형: 전량 스캔이라 검출


#==================================================================
# 스탬프 신호(Signal E) 테스트
#=> scan_stamp 가 '진짜 표식(머리/반복/명시문구)'만 인정하고 본문 단발 언급은
#   걸러내는지(오탐 방지), 등급/신뢰도/seed 를 올바로 내는지 검증한다.
#==================================================================

#------------------------------------------------------------------
# 머리 스탬프 → head 인정 C
#=> 문서 앞부분(표지)에 '대외비'가 찍히면 head 사유로 C 스탬프가 잡혀야 한다.
#------------------------------------------------------------------
def test_stamp_head(rs):
    s = R.scan_stamp("대외비\n\n2026 사업계획 보고서\n개요 ...", rs)
    assert s.grade == "C"
    assert s.hits and s.hits[0]["mode"] in ("head", "head+repeat")
    assert s.confidence >= 0.9 and s.seed_eligible is True


#------------------------------------------------------------------
# 반복 워터마크 → repeat 인정
#=> 머리에 없어도 문서 곳곳에 여러 번(페이지마다) 반복되면 repeat 사유로 인정.
#------------------------------------------------------------------
def test_stamp_repeat_only(rs):
    head_pad = "가" * (R._STAMP_HEAD_CHARS + 50)      # 첫 등장을 '머리' 밖으로 밀어냄
    text = head_pad + " 기밀 " + ("나" * 300) + " 기밀 " + ("다" * 50)
    s = R.scan_stamp(text, rs)
    assert s.grade == "C"
    modes = {h["mode"] for h in s.hits}
    assert "repeat" in modes            # head 없이 반복만으로 인정됐는지 확인


#------------------------------------------------------------------
# 본문 단발 언급 → 인정 안 함(오탐 방지의 핵심)
#=> 머리 밖에서 딱 1번만 언급된 표식은 스탬프로 치지 않는다(예: "대외비가 아님").
#------------------------------------------------------------------
def test_stamp_midbody_single_ignored(rs):
    text = ("x" * (R._STAMP_HEAD_CHARS + 100)) + " 이 문서는 대외비가 아닙니다. " + ("y" * 100)
    s = R.scan_stamp(text, rs)
    assert s.grade is None
    assert s.hits == []


#------------------------------------------------------------------
# always 문구 → 위치/반복 무관 1건도 인정
#=> '관계자 외 열람금지' 같은 스탬프 전용 문구는 문서 끝에 1번만 있어도 C.
#------------------------------------------------------------------
def test_stamp_always_phrase(rs):
    text = ("본문 내용 " * 500) + " 관계자 외 열람금지"
    s = R.scan_stamp(text, rs)
    assert s.grade == "C"
    assert any(h["mode"] == "always" for h in s.hits)


#------------------------------------------------------------------
# 내부용 스탬프 → S
#=> 내부용 표식은 기밀보다 한 단계 낮은 S 로 잡혀야 한다.
#------------------------------------------------------------------
def test_stamp_internal_grade_s(rs):
    s = R.scan_stamp("내부용\n\n회의 준비 자료", rs)
    assert s.grade == "S"


#------------------------------------------------------------------
# 표식 없음 → None
#=> 어떤 스탬프 문구도 없으면 grade=None(무신호).
#------------------------------------------------------------------
def test_stamp_none(rs):
    s = R.scan_stamp("오늘 점심은 김치찌개입니다.", rs)
    assert s.grade is None


#------------------------------------------------------------------
# 융합: 스탬프가 등급을 상향한다
#=> 규칙/경로/파일명이 전부 O/None 이라도, 머리 스탬프가 있으면 최종 C 로 상향되고
#   decided_by 에 'stamp' 가 포함돼야 한다(상향 전용 기여 확인).
#------------------------------------------------------------------
def test_stamp_fusion_upgrades(rs):
    from csoclassify.classify import build_record
    # 내용/파일명/경로에 아무 등급 근거가 없고, 오직 머리 스탬프만 있는 문서.
    rec = build_record("D:/tmp/무제.txt", "극비\n\n일반적인 서술 문장입니다.", rs, ts="T")
    assert rec["grade"] == "C"
    assert "stamp" in rec["decided_by"]
    assert rec["signals"]["stamp"]["grade"] == "C"


#------------------------------------------------------------------
# 로그 안전성: 스탬프 근거(term)는 원문 PII 가 아니다
#=> 스탬프 hits 의 term 은 정책 표식 문구라 로그/결과에 남겨도 안전(값 검증).
#------------------------------------------------------------------
def test_stamp_terms_are_policy_keywords(rs):
    s = R.scan_stamp("대외비\n대외비\n보고서", rs)
    assert s.hits
    assert s.hits[0]["term"] in ("대외비", "극비", "기밀", "사외비",
                                 "CONFIDENTIAL", "SECRET", "RESTRICTED")


#==================================================================
# 결합식별성(L1-combo) 테스트
#=> _combo_hits 가 PII '조합'을 만나면 상향 히트를 내되(all_of / of+min_types),
#   단일 유형·미성립 조합은 발동하지 않는지, 근거(terms)가 라벨+건수뿐인지 검증한다.
#==================================================================

#------------------------------------------------------------------
# 로드: pii_combos 가 실린다
#------------------------------------------------------------------
def test_combo_rules_loaded(rs):
    assert len(rs.pii_combos) >= 1
    ids = {c.id for c in rs.pii_combos}
    assert "identity_financial" in ids and "contact_cluster" in ids


#------------------------------------------------------------------
# all_of 성립 → 상향 C (신원+금융)
#=> RRN·ACCOUNT 가 모두 있으면 identity_financial(C) 조합이 잡혀야 한다.
#------------------------------------------------------------------
def test_combo_all_of_fires(rs):
    hits = R._combo_hits({"RRN": 1, "ACCOUNT": 2}, rs.pii_combos, rs.confidence.get("regex"))
    ids = {h.rule_id: h for h in hits}
    assert "identity_financial" in ids
    assert ids["identity_financial"].grade == "C"
    assert ids["identity_financial"].layer == "COMBO"


#------------------------------------------------------------------
# all_of 미성립 → 발동 안 함
#=> 조합 라벨 중 하나만 있으면(RRN 단독) 어떤 all_of 조합도 잡히면 안 된다.
#------------------------------------------------------------------
def test_combo_all_of_single_no_fire(rs):
    hits = R._combo_hits({"RRN": 1}, rs.pii_combos, rs.confidence.get("regex"))
    assert all(h.rule_id not in ("identity_financial", "identity_card", "identity_address")
               for h in hits)


#------------------------------------------------------------------
# of+min_types 성립 → 상향 S (연락처 2종)
#=> 개별론 O 인 PHONE·EMAIL 이 2종 모이면 contact_cluster(S)가 잡혀야 한다.
#------------------------------------------------------------------
def test_combo_min_types_fires(rs):
    hits = R._combo_hits({"PHONE": 1, "EMAIL": 1}, rs.pii_combos, rs.confidence.get("regex"))
    ids = {h.rule_id: h for h in hits}
    assert "contact_cluster" in ids
    assert ids["contact_cluster"].grade == "S"


#------------------------------------------------------------------
# of+min_types 미달 → 발동 안 함
#=> 연락처가 1종(PHONE 만)이면 min_types(2) 미달로 잡히면 안 된다.
#------------------------------------------------------------------
def test_combo_min_types_below_no_fire(rs):
    hits = R._combo_hits({"PHONE": 3}, rs.pii_combos, rs.confidence.get("regex"))
    assert all(h.rule_id != "contact_cluster" for h in hits)


#------------------------------------------------------------------
# 근거(terms)는 라벨+건수뿐 — 원문 PII 값이 아니다
#=> 결합 근거로 남는 건 ko-pii 라벨과 건수라, 로그/결과에 남겨도 안전하다.
#------------------------------------------------------------------
def test_combo_terms_are_labels_only(rs):
    hits = R._combo_hits({"RRN": 1, "ADDRESS": 1}, rs.pii_combos, rs.confidence.get("regex"))
    h = next(x for x in hits if x.rule_id == "identity_address")
    labels = {t for t, _ in h.terms}
    assert labels == {"RRN", "ADDRESS"}          # 라벨 집합
    assert all(isinstance(c, int) for _, c in h.terms)   # 값이 아니라 건수(int)


#------------------------------------------------------------------
# 빈 입력/무규칙 방어
#=> counts 가 비었거나 조합 규칙이 없으면 조용히 빈 리스트.
#------------------------------------------------------------------
def test_combo_empty_inputs(rs):
    assert R._combo_hits({}, rs.pii_combos) == []
    assert R._combo_hits({"RRN": 1}, []) == []


#------------------------------------------------------------------
# end-to-end: 연락처 결합이 O→S 로 상향
#=> 개별 O 인 전화+이메일이 한 문서에 모이면 rule 신호 등급이 S 가 된다.
#------------------------------------------------------------------
def test_combo_contact_escalates_end_to_end(rs):
    if not R._HAVE_KOPII:
        pytest.skip("ko-pii 미설치 환경")
    sig = R.scan_text("연락처 010-1234-5678 이메일 hong@example.com 회신 바랍니다.", rs)
    assert sig.grade == "S"
    assert any(h.layer == "COMBO" and h.rule_id == "contact_cluster" for h in sig.hits)


#------------------------------------------------------------------
# end-to-end: 신원+금융 결합이 S→C 로 상향
#=> RRN(단독 S)+계좌가 한 문서에 모이면 C 로 상향된다(결합용이성).
#------------------------------------------------------------------
def test_combo_identity_financial_end_to_end(rs):
    if not R._HAVE_KOPII:
        pytest.skip("ko-pii 미설치 환경")
    text = f"주민등록번호 {VALID_RRN}, 계좌 국민은행 123456-78-901234 입금 바랍니다."
    sig = R.scan_text(text, rs)
    assert sig.grade == "C"
    assert any(h.layer == "COMBO" and h.rule_id == "identity_financial" for h in sig.hits)


#==================================================================
# 민감정보군 신호(Signal F) 테스트
#=> scan_sensitive 가 법상 민감정보 범주 키워드를 잡아 C 를 부여하고, 근거에
#   범주·단어를 남기며(원문 아님), 무매칭은 None 인지 검증한다.
#==================================================================

#------------------------------------------------------------------
# 로드: sensitive_rules 가 실린다(범주 포함)
#------------------------------------------------------------------
def test_sensitive_rules_loaded(rs):
    assert len(rs.sensitive_rules) >= 1
    cats = {r.category for r in rs.sensitive_rules}
    assert "건강" in cats and "범죄경력" in cats


#------------------------------------------------------------------
# 건강정보 → C
#=> 진단서/병력 같은 건강 키워드가 있으면 grade=C, 근거에 category='건강'.
#------------------------------------------------------------------
def test_sensitive_health(rs):
    s = R.scan_sensitive("첨부: 진단서 1부. 환자 병력 참고 바랍니다.", rs)
    assert s.grade == "C"
    assert any(h["category"] == "건강" for h in s.hits)
    assert s.confidence >= 0.8


#------------------------------------------------------------------
# 범죄경력 → C
#------------------------------------------------------------------
def test_sensitive_criminal(rs):
    s = R.scan_sensitive("대상자 전과 및 범죄경력 조회 결과.", rs)
    assert s.grade == "C"
    assert any(h["category"] == "범죄경력" for h in s.hits)


#------------------------------------------------------------------
# 무매칭 → None
#=> 민감 범주 단어가 하나도 없으면 grade=None.
#------------------------------------------------------------------
def test_sensitive_none(rs):
    s = R.scan_sensitive("다음 주 회식 장소를 안내드립니다.", rs)
    assert s.grade is None
    assert s.hits == []


#------------------------------------------------------------------
# 근거(terms)는 정책 키워드 — 원문 PII 가 아니다
#=> 결과 근거로 남는 건 매칭 단어·범주·건수라 로그/결과에 남겨도 안전.
#------------------------------------------------------------------
def test_sensitive_terms_are_keywords(rs):
    s = R.scan_sensitive("노동조합 조합원명부 첨부.", rs)
    h = next(x for x in s.hits if x["category"] == "노조")
    words = {t for t, _ in h["terms"]}
    assert "노동조합" in words
    # as_dict 직렬화도 안전한 형태({term,count})인지 확인
    d = s.as_dict()
    assert d["hits"][0]["terms"][0]["term"] in words


#------------------------------------------------------------------
# 융합: 민감정보 신호가 단독으로 등급을 올린다
#=> PII·기밀키워드가 없어도 건강정보만 있으면 최종 C, decided_by 에 'sensitive'.
#------------------------------------------------------------------
def test_sensitive_fusion_decides(rs):
    from csoclassify.classify import build_record
    rec = build_record("D:/tmp/무제.txt", "환자 진단서 및 투약내역을 첨부합니다.", rs, ts="T")
    assert rec["grade"] == "C"
    assert "sensitive" in rec["decided_by"]
    assert rec["signals"]["sensitive"]["grade"] == "C"
