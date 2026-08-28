#------------------------------------------------------------------
# ui/doctype_review.py 단위 테스트 — 설계서 §7-4-1 (제안→확정/거절)
#=> cso_override.jsonl 의 axis:"doctype" 항목 로드/기록, 그리고 레코드의
#   labels.doctype 후보에 검토 상태(proposed/confirmed/rejected)를 매기는
#   로직을 검증한다. Streamlit 은 거치지 않는다(순수 로직 모듈).
#------------------------------------------------------------------

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "ui"))

import doctype_review as DR   # noqa: E402


#------------------------------------------------------------------
# labels.doctype 있는 레코드 만들기
#
# -in: file   = 파일 경로
# -in: values = [{dc_id, path, confidence, from}, ...]
#
# -out: dict = 최소 분류 레코드
# -out: error = 없음
#------------------------------------------------------------------
def mk_record(file, values):
    return {"file": file, "labels": {"doctype": {
        "values": values, "strategy": "all", "status": "proposed", "truncated": 0,
    }}}


# ── load_doctype_overrides ───────────────────────────────────────

#------------------------------------------------------------------
# 파일이 없으면 빈 결과
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_파일_없으면_빈결과():
    latest, history = DR.load_doctype_overrides("D:/no/such/file.jsonl")
    assert latest == {} and history == {}


#------------------------------------------------------------------
# axis:"doctype" 만 골라지고, security 줄(axis 없음/security)은 건너뛴다
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_axis_doctype만_골라진다(tmp_path):
    p = tmp_path / "cso_override.jsonl"
    p.write_text(
        '{"file":"a.hwp","old_grade":"S","new_grade":"C","reason":"x","reviewer":"y","ts":"t"}\n'
        '{"file":"a.hwp","axis":"security","old_grade":"C","new_grade":"S","reason":"x","reviewer":"y","ts":"t"}\n'
        '{"file":"b.hwp","axis":"doctype","confirmed":["A"],"rejected":[],"reason":"r","reviewer":"y","ts":"t1"}\n',
        encoding="utf-8",
    )
    latest, history = DR.load_doctype_overrides(str(p))
    assert set(latest.keys()) == {"b.hwp"}
    assert latest["b.hwp"]["confirmed"] == ["A"]


#------------------------------------------------------------------
# 같은 문서를 여러 번 결정하면 마지막 줄이 유효하고, 이력은 전부 남는다
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_나중_결정이_유효하고_이력은_전부_남는다(tmp_path):
    p = tmp_path / "cso_override.jsonl"
    p.write_text(
        '{"file":"a.hwp","axis":"doctype","confirmed":["A"],"rejected":["B"],"reason":"1","reviewer":"y","ts":"t1"}\n'
        '{"file":"a.hwp","axis":"doctype","confirmed":["A","B"],"rejected":[],"reason":"2","reviewer":"y","ts":"t2"}\n',
        encoding="utf-8",
    )
    latest, history = DR.load_doctype_overrides(str(p))
    assert latest["a.hwp"]["confirmed"] == ["A", "B"]
    assert len(history["a.hwp"]) == 2


#------------------------------------------------------------------
# 깨진 줄·file 없는 줄은 건너뛴다
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_깨진줄과_file없는줄은_건너뛴다(tmp_path):
    p = tmp_path / "cso_override.jsonl"
    p.write_text(
        '이건 json이 아님\n'
        '{"axis":"doctype","confirmed":["A"],"rejected":[],"reason":"x","reviewer":"y","ts":"t"}\n',
        encoding="utf-8",
    )
    latest, history = DR.load_doctype_overrides(str(p))
    assert latest == {}


# ── append_doctype_override ──────────────────────────────────────

#------------------------------------------------------------------
# 기록한 내용을 load_doctype_overrides 로 다시 읽으면 그대로 나온다(round-trip)
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_기록_후_다시_읽으면_동일(tmp_path):
    p = tmp_path / "cso_override.jsonl"
    DR.append_doctype_override(str(p), "c.hwp", "SF001", ["DC_006_001"], ["DC_003_001"],
                               "제안 내용은 첨부일 뿐", "고봉수")
    latest, _ = DR.load_doctype_overrides(str(p))
    e = latest["c.hwp"]
    assert e["axis"] == "doctype"
    assert e["doc_id"] == "SF001"
    assert e["confirmed"] == ["DC_006_001"]
    assert e["rejected"] == ["DC_003_001"]
    assert e["reviewer"] == "고봉수"
    assert "ts" in e


#------------------------------------------------------------------
# security 오버라이드가 이미 있는 파일에 추가로 써도 서로 섞이지 않는다
#=> app.py 의 append_override() 가 쓴 형식(axis 없음/구버전)과 같은 파일에
#   append 했을 때, load_doctype_overrides 가 doctype 줄만 정확히 골라내는지
#   재확인한다(회귀 방지).
#
# -in: tmp_path
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_security_오버라이드와_섞이지_않는다(tmp_path):
    p = tmp_path / "cso_override.jsonl"
    p.write_text(
        '{"file":"d.hwp","old_grade":"O","new_grade":"S","reason":"x","reviewer":"y","ts":"t"}\n',
        encoding="utf-8",
    )
    DR.append_doctype_override(str(p), "d.hwp", None, ["DC_001"], [], "사유", "검토자")
    latest, _ = DR.load_doctype_overrides(str(p))
    assert latest["d.hwp"]["confirmed"] == ["DC_001"]


# ── effective_doctype ────────────────────────────────────────────

#------------------------------------------------------------------
# labels.doctype 이 없으면 빈 후보 + 미검토
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_labels_doctype_없으면_빈결과():
    rec = {"file": "x.hwp", "labels": {"security": {"value": "S"}}}
    candidates, reviewed = DR.effective_doctype(rec, {})
    assert candidates == [] and reviewed is False


#------------------------------------------------------------------
# 오버라이드가 없으면 전부 '자동 확정'(auto=True), 신뢰도 내림차순 정렬
#=> 제안이 맞는 경우가 대부분이라 확정을 기본으로 둔다. 대신 auto 플래그로
#   "사람이 아직 확인하지 않았다"는 사실은 잃지 않는다.
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_오버라이드_없으면_전부_자동확정_신뢰도순():
    rec = mk_record("x.hwp", [
        {"dc_id": "A", "path": "법무/규정 > 계약서", "confidence": 0.6, "from": ["rule"]},
        {"dc_id": "B", "path": "영업/마케팅 > 제안서", "confidence": 0.9, "from": ["name"]},
    ])
    candidates, reviewed = DR.effective_doctype(rec, {})
    assert reviewed is False
    assert [c["dc_id"] for c in candidates] == ["B", "A"]
    assert all(c["status"] == "confirmed" for c in candidates)
    assert all(c["auto"] is True for c in candidates)


#------------------------------------------------------------------
# 오버라이드가 있으면 confirmed/rejected/proposed 가 정확히 매겨진다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_오버라이드_있으면_상태가_매겨진다():
    rec = mk_record("x.hwp", [
        {"dc_id": "A", "path": "법무/규정 > 계약서", "confidence": 0.9, "from": ["rule"]},
        {"dc_id": "B", "path": "영업/마케팅 > 제안서", "confidence": 0.7, "from": ["name"]},
        {"dc_id": "C", "path": "기술/개발 > 매뉴얼", "confidence": 0.5, "from": ["rule"]},
    ])
    latest_dt = {"x.hwp": {"confirmed": ["A"], "rejected": ["B"]}}
    candidates, reviewed = DR.effective_doctype(rec, latest_dt)
    assert reviewed is True
    by_id = {c["dc_id"]: (c["status"], c["auto"]) for c in candidates}
    # A 는 사람이 확정, B 는 사람이 거절, C 는 결정이 없어 자동 확정.
    assert by_id == {"A": ("confirmed", False), "B": ("rejected", False),
                     "C": ("confirmed", True)}


# ── summarize_status ─────────────────────────────────────────────

#------------------------------------------------------------------
# 후보 없으면 "후보 없음"
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_summarize_후보없음():
    assert DR.summarize_status([]) == "후보 없음"


#------------------------------------------------------------------
# 상태별 건수가 요약 문자열에 정확히 들어간다
#
# -in: 없음
# -out: 없음(단언)
# -out: error = 없음
#------------------------------------------------------------------
def test_summarize_상태별_건수():
    candidates = [{"status": "confirmed"}, {"status": "confirmed"},
                  {"status": "rejected"}, {"status": "proposed"}]
    assert DR.summarize_status(candidates) == "관리자 확정 2 · 거절 1 · 자동 확정 1"


#------------------------------------------------------------------
# 근거 블록 — 신호마다 알맞은 문장이 나온다
#=> 신호에 따라 담긴 것이 다르다(제목은 말+횟수, 파일이름은 말만,
#   본문은 기준 충족 여부까지). 한 틀로 찍으면 말이 안 되는 문장이 나온다.
#------------------------------------------------------------------
def test_신호별로_문장_모양이_다르다():
    assert "제목(첫 줄)에" in DR.reason_text("title", {"terms": [{"term": "규정", "count": 1}]})
    # 표제부는 몇 자까지 봤는지가 판정 근거의 일부다.
    assert "앞 400자" in DR.reason_text("head", {"terms": [{"term": "규정", "count": 2}],
                                              "window": 400})
    # 본문은 "몇 종 몇 회"라는 채택 기준을 넘겼는지가 핵심이다.
    body = DR.reason_text("body", {"terms": [{"term": "규정", "count": 4}],
                                   "distinct": 1, "total": 4,
                                   "min_distinct": 1, "min_count": 1})
    assert "1종 4회" in body and "기준" in body
    # 파일 이름은 횟수 개념이 없다 — 있다/없다뿐이다.
    assert "회" not in DR.reason_text("name", {"terms": ["규정"]})


#------------------------------------------------------------------
# 기여도가 큰 신호부터 나온다
#=> 검토자는 위에서부터 읽는다. 가장 센 근거가 맨 아래 있으면 근거를 보여준
#   의미가 줄어든다(재설계 13-4).
#------------------------------------------------------------------
def test_근거는_기여도_큰_순서로_나온다():
    cand = {
        "confidence": 0.9,
        "evidence": {"body": {"terms": [{"term": "규정", "count": 3}], "distinct": 1,
                              "total": 3, "min_distinct": 1, "min_count": 1},
                     "title": {"terms": [{"term": "규정", "count": 1}]},
                     "name": {"terms": ["규정"]}},
        "score_parts": [{"signal": "body", "c": 0.15},
                        {"signal": "title", "c": 0.65},
                        {"signal": "name", "c": 0.30}],
    }
    block = DR.reason_block(cand)
    assert [ln["conf"] for ln in block["lines"]] == [0.65, 0.30, 0.15]
    # 근거가 둘 이상이면 왜 이 숫자가 됐는지 한 줄이 붙는다.
    assert block["summary"] and "90%" in block["summary"]


#------------------------------------------------------------------
# 벡터 단독 후보는 경고를 단다
#=> 규칙 근거가 없는 후보는 "어느 말이 어디에 있었다"를 댈 수 없다.
#   같은 모양으로 보여주면 검토자가 근거가 있는 줄 안다(재설계 11-1).
#------------------------------------------------------------------
def test_벡터_단독_후보는_경고가_붙는다():
    block = DR.reason_block({
        "from": ["embed"], "stage": "embed", "confidence": 0.65,
        "evidence": {"embed": {"method": "knn_vote", "top_sim": 0.88, "share": 0.6}},
        "score_parts": [{"signal": "embed", "c": 0.65}]})
    assert block["warn"] and "비슷한 문서" in block["warn"]

    # 규칙에서 온 후보에는 그 경고가 붙지 않는다.
    ok = DR.reason_block({"stage": "rule", "confidence": 0.8,
                          "evidence": {"title": {"terms": [{"term": "규정", "count": 1}]}},
                          "score_parts": [{"signal": "title", "c": 0.8}]})
    assert ok["warn"] is None


#------------------------------------------------------------------
# 사람이 고른 분류·예전 레코드도 깨지지 않는다
#=> 관리자가 직접 추가한 분류에는 evidence 가 없고, 재설계 이전에 분류된
#   레코드에도 없다. 근거 화면이 그 둘에서 예외를 내면 검토 자체가 멈춘다.
#------------------------------------------------------------------
def test_근거가_없는_후보도_안전하다():
    assert DR.reason_block({"from": ["manual"]})["kind"] == "manual"
    assert DR.reason_block({"from": ["rule"]})["kind"] == "legacy"
    assert DR.reason_block(None)["kind"] == "legacy"          # None 도 견딘다
    assert DR.reason_block({})["lines"] == []


#------------------------------------------------------------------
# 근거 문장에 문서 원문이 섞이지 않는다 <중요>
#=> 재설계 13-3 프라이버시 원칙. 근거에는 '규칙에 적힌 말과 횟수'만 담기며,
#   화면도 그것만 보여줘야 한다. 원문 조각이 evidence 에 섞여 들어오더라도
#   화면 문장이 통째로 뱉어 내면 안 되므로, 말 개수 상한이 있는지도 함께 본다.
#------------------------------------------------------------------
def test_근거_문장에_원문_조각이_없다():
    many = [{"term": f"말{i}", "count": i + 1} for i in range(20)]
    text = DR.reason_text("body", {"terms": many, "distinct": 20, "total": 210,
                                   "min_distinct": 1, "min_count": 1})
    # 20개를 다 뱉지 않고 앞 6개 + "외 N개" 로 줄인다.
    assert "말6" not in text and "외 14개" in text
    assert len(text) < 200
