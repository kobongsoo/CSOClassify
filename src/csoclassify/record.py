#------------------------------------------------------------------
# 분류 레코드 읽기 도우미 — 여러 세대의 모양을 한 자리에서 흡수한다
#=> 레코드 모양은 세 번 바뀌었다.
#
#   (1세대) 최상위 grade + labels:{security:{value,…}, doctype:{…}} + signals
#   (2세대, 2026-09-10 오전) security:{grade,…,signals} + doctype:{values,…} + meta
#   (3세대, 2026-09-10 오후) 판정은 앞에, 근거는 why 안에
#
#       {file, hash, doc_id, doc_id_source,
#        grade: "C",                       ← 등급 판정(한 벌뿐)
#        doctype: ["DC_002_002", …],       ← 문서분류 판정(dc_id 목록)
#        why: {security: {confidence, method, decided_by, seed_eligible, signals},
#              doctype:  {values, [strategy], [truncated], [conflicts], [embed]}},
#        meta: {ts}}
#
#   3세대의 뜻은 "무엇을 → 어떻게 됐나 → 왜 → 장부" 다. 판정을 맨 앞에 한 벌만
#   두고, 그 판정을 뒷받침하는 것은 전부 why 안에 둔다. --simple 출력이 앞의
#   두 묶음만 떼어낸 것과 같은 모양이라, 축약본과 전체가 같은 낱말을 쓴다.
#
#   [왜 별도 모듈인가] 레코드를 읽는 곳이 엔진·CLI·검토 화면에 흩어져 있다.
#   각자 "새 모양이면 …, 옛 모양이면 …" 을 적으면 그 조건문이 스무 군데로
#   퍼지고, 한 군데만 빠뜨려도 옛 결과 파일이 조용히 안 보이게 된다.
#   그래서 흡수를 이 파일 하나에 모은다.
#
#   [옛 파일을 왜 계속 읽나] 이 도구는 "언제 무엇을 왜 그렇게 판정했는가"를
#   쌓아 두는 것이 일이다. 형식을 바꿨다고 그동안 쌓인 판정을 못 읽게 되면
#   도구의 존재 이유가 사라진다.
#------------------------------------------------------------------


#------------------------------------------------------------------
# 옛 labels 껍데기 안전하게 꺼내기
#=> seed 파일(class_seed.jsonl)도 labels 라는 이름의 칸을 쓰는데 모양이 다르다
#   (거기서는 labels.security 가 등급 '문자열'이다). 결과 레코드로 착각하고
#   .get() 을 부르면 AttributeError 로 터지므로, dict 일 때만 통과시킨다.
#
# -in: rec = 아무 dict
#
# -out: dict = rec["labels"] 가 dict 이면 그것, 아니면 빈 dict
# -out: error = 없음
#------------------------------------------------------------------
def _labels(rec):
    lab = rec.get("labels")
    return lab if isinstance(lab, dict) else {}


#------------------------------------------------------------------
# 근거 묶음(why) 꺼내기
#=> 3세대에만 있는 칸이다. 없으면 빈 dict 를 돌려, 부르는 쪽이 옛 세대 자리를
#   이어서 보게 한다.
#
# -in: rec = 분류 레코드
#
# -out: dict = rec["why"] 가 dict 이면 그것, 아니면 빈 dict
# -out: error = 없음
#------------------------------------------------------------------
def _why(rec):
    w = rec.get("why")
    return w if isinstance(w, dict) else {}


#------------------------------------------------------------------
# 최종 등급
#=> 3세대·1세대는 최상위 grade 가 곧 답이다(같은 이름을 그대로 되살렸다).
#   2세대만 security.grade 안에 있었다.
#
# -in: rec = 분류 레코드
#
# -out: "C"|"S"|"O"|None — 아무 신호도 못 정했으면 None(보류)
# -out: error = 없음
#------------------------------------------------------------------
def grade_of(rec):
    rec = rec or {}
    if "grade" in rec:
        return rec.get("grade")
    sec = rec.get("security")
    if isinstance(sec, dict):
        return sec.get("grade")
    old = _labels(rec).get("security")
    return old.get("value") if isinstance(old, dict) else None


#------------------------------------------------------------------
# 등급 축의 판정·근거 묶음
#=> 3세대는 why.security 다. 편의를 위해 grade 를 함께 채워 돌려준다 —
#   읽는 쪽 대부분이 "등급과 확신을 같이" 보고 싶어 하기 때문이다(레코드에
#   실제로 적히는 모양과는 다르다. 레코드에는 grade 가 맨 앞에 한 벌뿐이다).
#
# -in: rec = 분류 레코드(어느 세대든)
#
# -out: dict = {grade, confidence, method, decided_by, seed_eligible, signals}
# -out: error = 없음
#------------------------------------------------------------------
def security_of(rec):
    rec = rec or {}
    sec = _why(rec).get("security")
    if not isinstance(sec, dict):
        sec = rec.get("security")
    if isinstance(sec, dict):
        out = dict(sec)
        out["grade"] = grade_of(rec)
        return out
    # ── 1세대 ────────────────────────────────────────────────
    old = _labels(rec).get("security")
    old = old if isinstance(old, dict) else {}
    return {
        "grade": grade_of(rec),
        "confidence": old.get("confidence", rec.get("confidence", 0.0)),
        "method": old.get("method", rec.get("method", "unclassified")),
        "decided_by": old.get("decided_by", rec.get("decided_by") or []),
        "seed_eligible": rec.get("seed_eligible", False),
        "signals": rec.get("signals") or {},
    }


#------------------------------------------------------------------
# 문서분류체계 축의 판정·근거 묶음
#=> [축을 안 쓰는 배포와 구분] 이 축을 아예 안 돌린 배포는 칸 자체가 없고,
#   돌렸는데 후보를 못 찾은 문서는 values 가 빈 목록이다. 이 둘은 뜻이
#   완전히 다르므로(전자는 "안 봄", 후자는 "봤는데 없음") None 과 dict 로
#   갈라서 돌려준다 — 합치면 "왜 비었는지"를 영영 알 수 없다.
#
#   3세대의 최상위 doctype 은 dc_id **배열**이라 여기서 찾는 대상이 아니다.
#   상세는 why.doctype 에 있고, 배열은 그것에서 뽑은 값이다.
#
# -in: rec = 분류 레코드
#
# -out: dict|None = {values, [strategy], [truncated], [conflicts], [embed]}
#                   또는 축을 안 돌렸으면 None
# -out: error = 없음
#------------------------------------------------------------------
def doctype_of(rec):
    rec = rec or {}
    dt = _why(rec).get("doctype")
    if isinstance(dt, dict):
        return dt
    dt = rec.get("doctype")          # 2세대는 최상위가 객체였다
    if isinstance(dt, dict):
        return dt
    old = _labels(rec).get("doctype")
    return old if isinstance(old, dict) else None


#------------------------------------------------------------------
# 문서분류체계 후보 목록
#=> 축을 안 돌렸든 후보가 없든 '빈 목록'으로 통일한다. 그 둘을 갈라야 하는
#   곳은 doctype_of() 로 None 여부를 직접 보면 된다.
#
# -in: rec = 분류 레코드
#
# -out: list = 후보 dict 목록(없으면 빈 목록)
# -out: error = 없음
#------------------------------------------------------------------
def doctype_values(rec):
    dt = doctype_of(rec)
    return (dt.get("values") or []) if dt else []


#------------------------------------------------------------------
# 확정된 문서분류 dc_id 목록
#=> 3세대 레코드는 이 목록이 최상위 doctype 에 이미 적혀 있다(판정을 앞에
#   두자는 뜻). 그 밖의 세대는 후보에서 뽑아 만든다.
#
# -in: rec = 분류 레코드
#
# -out: list = dc_id 문자열 목록
# -out: error = 없음
#------------------------------------------------------------------
def doctype_ids(rec):
    rec = rec or {}
    dt = rec.get("doctype")
    if isinstance(dt, list):
        return list(dt)
    return [v.get("dc_id") for v in doctype_values(rec) if v.get("dc_id")]


#------------------------------------------------------------------
# 부가 정보(시각·버전) 꺼내기
#=> 3세대부터 규칙셋 버전 세 칸은 레코드마다 적지 않고 결과 파일 맨 앞의
#   실행 헤더({"run": …})에 한 번만 적는다. 레코드에 남는 것은 ts 뿐이다.
#   옛 세대 파일에는 여전히 레코드마다 들어 있어, 있으면 그대로 읽는다.
#
# -in: rec = 분류 레코드
#
# -out: dict = {ts[, rule_version, taxonomy_version, doctype_rule_version]}
# -out: error = 없음
#------------------------------------------------------------------
def meta_of(rec):
    rec = rec or {}
    meta = rec.get("meta")
    if isinstance(meta, dict):
        return meta
    return {k: rec[k] for k in
            ("rule_version", "taxonomy_version", "doctype_rule_version", "ts")
            if k in rec}
