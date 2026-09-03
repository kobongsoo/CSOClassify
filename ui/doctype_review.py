#------------------------------------------------------------------
# 업무분류(doctype) 축 검토 — 순수 로직
#=> 분류 검토 UI(app.py)가 쓰는 "제안(proposed) → 확정/거절" 흐름의 입출력을
#   담당한다. UI(Streamlit) 위젯과 분리해 이 모듈만 단위테스트할 수 있게 한다
#   (seedstore.py·report.py 와 같은 원칙).
#    - cso_override.jsonl 을 app.py 의 security 오버라이드와 '같은 파일'에
#      append-only 로 함께 쌓되, axis:"doctype" 로 구분한다(설계서 7-4-1).
#      security 오버라이드(axis 없음 또는 axis:"security")는 이 모듈이 건드리지
#      않는다 — app.py 의 기존 load_overrides()/append_override() 그대로 둔다.
#    - 원본 분류 레코드(cso_result.jsonl)는 절대 고치지 않는다. 확정/거절 결정은
#      오버라이드 파일에만 쌓인다(감사 추적).
#------------------------------------------------------------------

import datetime
import json

import docidkey
import os
from collections import defaultdict

# 화면에 나갈 말은 한곳에서 관리한다(uiwords). Streamlit 의존이 없는
# 순수 데이터 모듈이라 이 모듈에서 가져다 써도 테스트가 깨지지 않는다.
import uiwords as W


#------------------------------------------------------------------
# 현재 시각 ISO 문자열
#
# -in: 없음
# -out: str = 예 "2026-08-24T10:00:00+09:00"
# -out: error = 없음
#------------------------------------------------------------------
def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


#------------------------------------------------------------------
# doctype 오버라이드 로드
#=> cso_override.jsonl 에서 axis=="doctype" 인 줄만 골라, 파일별 "가장 최근
#   결정"과 전체 이력을 만든다. security 오버라이드(axis 없음/"security")는
#   여기서 조용히 건너뛴다 — app.py 의 load_overrides() 가 그쪽을 담당한다.
#
# -in: path = cso_override.jsonl 경로(없으면 빈 결과)
#
# -out: (latest, history) = {file: 최신 entry}, {file: [entry,...]}
# -out: error = 없음(파일 없거나 깨진 줄은 건너뜀)
#------------------------------------------------------------------
def load_doctype_overrides(path):
    latest, history = {}, defaultdict(list)
    if not path or not os.path.isfile(path):
        return latest, history
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("axis") != "doctype":
                continue
            file = e.get("file")
            if not file:
                continue
            latest[file] = e            # 나중 줄이 앞 줄을 덮음 → 최신 유효
            history[file].append(e)
    # 보안등급 쪽과 같은 매칭 순서(doc_id → 정규화 경로 → 대소문자)를 쓴다.
    return docidkey.OverrideIndex(latest), history


#------------------------------------------------------------------
# doctype 확정/거절 1건 기록(append-only)
#=> 담당자가 후보를 확정/거절할 때, 문서당 한 줄을 cso_override.jsonl 에 덧붙인다.
#   confirmed 는 매핑 테이블에 실제로 들어갈 후보, rejected 는 "제안됐지만 사람이
#   뺀" 후보다 — "애초에 제안 안 됨"과 구분해야 규칙 정밀도를 나중에 잴 수
#   있다(설계서 7-4-1 note).
#
# -in: path      = cso_override.jsonl 경로
# -in: file      = 대상 문서 경로
# -in: doc_id    = 레코드의 doc_id(있으면, 설계서 doc_id 필드 — 아직 없는 배포가
#                  많아 None 일 수 있다)
# -in: confirmed = 확정한 dc_id 리스트
# -in: rejected  = 거절한 dc_id 리스트
# -in: reason    = 결정 사유(감사용)
# -in: reviewer  = 검토자 이름
#
# -out: 없음(파일에 append)
# -out: error = 쓰기 실패 시 예외 전파
#------------------------------------------------------------------
def append_doctype_override(path, file, doc_id, confirmed, rejected, reason, reviewer):
    entry = {
        "file": file,
        "doc_id": doc_id,
        "axis": "doctype",
        "confirmed": list(confirmed),
        "rejected": list(rejected),
        "reason": reason,
        "reviewer": reviewer,
        "ts": now_iso(),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


#------------------------------------------------------------------
# 레코드 하나의 doctype 후보 + 검토 상태
#=> labels.doctype.values(제안 후보)에 최신 오버라이드 결정을 얹어, 각 후보가
#   proposed(아직 결정 안 됨)/confirmed(확정)/rejected(거절) 중 무엇인지 매긴다.
#   원본 레코드는 건드리지 않는다 — 이 함수는 화면에 보여줄 값만 계산한다.
#
# -in: rec       = 분류 레코드(labels.doctype 가 없을 수 있음 — 그 축을 안 쓴 배포)
# -in: latest_dt = load_doctype_overrides() 의 latest(파일별 최신 결정)
# -in: tax       = taxonomy.load_taxonomy() 결과. '직접 추가'로 확정된 분류의
#                  이름(전체경로)을 찾는 데만 쓴다. None 이면 DC_ID 를 그대로 쓴다
#
# -out: (candidates, reviewed) = candidates: [{dc_id,path,path_ids,confidence,
#        from,status}] (신뢰도 내림차순), reviewed: 이 문서가 한 번이라도
#        검토됐는지(오버라이드 존재 여부)
# -out: error = 없음
#------------------------------------------------------------------
def effective_doctype(rec, latest_dt, tax=None):
    dt = (rec.get("labels") or {}).get("doctype")
    if not dt:
        return [], False

    ov, _how = (latest_dt.for_rec(rec)
               if isinstance(latest_dt, docidkey.OverrideIndex)
               else (latest_dt.get(rec.get("file")), None))
    confirmed_ids = set(ov.get("confirmed") or []) if ov else set()
    rejected_ids = set(ov.get("rejected") or []) if ov else set()

    values = sorted(dt.get("values") or [], key=lambda v: v.get("confidence", 0.0), reverse=True)
    candidates = []
    seen = set()
    for v in values:
        dc_id = v.get("dc_id")
        # [자동 확정] 관리자가 아직 아무 결정도 안 한 제안은 '확정'으로 본다.
        # 제안이 맞는 경우가 대부분이라, 맞을 때마다 확정을 누르게 하면 화면이
        # 일만 늘린다. 대신 auto=True 로 "사람이 아직 확인하지 않았다"는 사실을
        # 잃지 않는다 — 거버넌스 도구에서 '누가 정했는가'는 지워지면 안 된다.
        auto = False
        if dc_id in confirmed_ids:
            status = "confirmed"
        elif dc_id in rejected_ids:
            status = "rejected"
        else:
            status, auto = "confirmed", True
        seen.add(dc_id)
        candidates.append({**v, "status": status, "auto": auto})

    # 관리자가 '직접 추가'로 고른 분류는 기계 후보 목록에 없다. 이것을 여기서
    # 얹지 않으면, 사람이 분명히 확정했는데도 표에는 "— 없음"으로 나오고 상태만
    # "확정됨"이 되어 앞뒤가 맞지 않는다(사람이 정한 사실이 화면에서 사라지는 셈).
    import taxonomy as taxlib
    for dc_id in sorted(confirmed_ids - seen):
        candidates.append({
            "dc_id": dc_id,
            "path": taxlib.path_of(tax, dc_id) if tax else dc_id,
            "confidence": None,
            "from": ["manual"],       # 기계 신호가 아니라 사람이 직접 고른 것
            "status": "confirmed",
            "auto": False,            # 사람이 직접 고른 것이므로 자동 확정이 아니다
        })
    return candidates, ov is not None


#------------------------------------------------------------------
# doctype 요약 한 줄
#=> 목록 표에 쓸 짧은 요약 문자열을 만든다("관리자 확정 1 · 거절 1 · 자동 확정 2" 형태).
#   자동 확정도 '확정'이지만 사람이 확인한 것과는 갈라 센다 — 합쳐 버리면 요약만
#   보고는 "이 문서를 사람이 봤는가"를 알 수 없게 된다.
#   6-3 의 "저장은 all, 화면은 상위 N" 원칙에 따라 목록에서는 이 요약 한 줄만
#   보여주고, 후보 전체(경로·신뢰도·근거)는 문서를 선택했을 때만 펼쳐 보여준다.
#
# -in: candidates = effective_doctype() 의 candidates
#
# -out: str = 요약 문자열(후보가 없으면 "후보 없음")
# -out: error = 없음
#------------------------------------------------------------------
def summarize_status(candidates):
    if not candidates:
        return "후보 없음"
    n_confirmed = sum(1 for c in candidates
                      if c["status"] == "confirmed" and not c.get("auto"))
    n_rejected = sum(1 for c in candidates if c["status"] == "rejected")
    # 사람이 확정한 것과 자동 확정을 갈라 센다 — 둘을 합쳐 버리면 "이 문서를
    # 사람이 봤는가"를 요약만 보고는 알 수 없게 된다.
    n_auto = sum(1 for c in candidates
                 if c.get("auto") or c["status"] == "proposed")
    parts = []
    if n_confirmed:
        parts.append(f"관리자 확정 {n_confirmed}")
    if n_rejected:
        parts.append(f"거절 {n_rejected}")
    if n_auto:
        parts.append(f"자동 확정 {n_auto}")
    return " · ".join(parts)


#------------------------------------------------------------------
# 근거 한 신호를 사람 문장으로
#=> evidence 블록 하나(제목/앞부분/본문/파일이름/서식/짜임새/비슷한문서)를
#   "어디에서 어떤 말이 몇 번" 형태의 한 줄로 바꾼다. 신호마다 담긴 내용이
#   달라서 한 가지 틀로는 안 되고, 여기서 갈라 준다.
#   [프라이버시] 규칙에 적힌 말과 횟수만 쓴다. 문서 원문은 근거 블록에 애초에
#   담기지 않으므로(재설계 13-3), 이 함수도 원문을 만질 일이 없다.
#
# -in: signal = 신호 이름("title"/"head"/"body"/"name"/"form"/"structure"/"path"/"embed")
# -in: ev     = 그 신호의 근거 dict
#
# -out: str = 사람이 읽는 한 줄(모르는 신호면 신호 이름만)
# -out: error = 없음
#------------------------------------------------------------------
def reason_text(signal, ev):
    label = W.SIGNAL_LABEL.get(signal, signal)
    ev = ev or {}

    # 제목·앞부분·본문 — 걸린 말과 횟수를 그대로 보여준다.
    if signal in ("title", "head", "body"):
        terms = ev.get("terms") or []
        shown = ", ".join(f"**{t.get('term')}** {t.get('count')}회"
                          for t in terms[:6] if isinstance(t, dict))
        more = f" 외 {len(terms) - 6}개" if len(terms) > 6 else ""
        where = label
        if signal == "head" and ev.get("window"):
            where = f"{label}(앞 {ev['window']}자)"
        if signal == "body":
            # 본문은 "몇 종류가 몇 번"이라는 기준을 넘겼는지가 판정의 근거다.
            return (f"{where}에서 {shown}{more} "
                    f"— {ev.get('distinct', 0)}종 {ev.get('total', 0)}회"
                    f"(기준 {ev.get('min_distinct', 1)}종 {ev.get('min_count', 1)}회 이상)")
        return f"{where}에 {shown}{more}"

    # 파일 이름 — 횟수가 아니라 '있다/없다'라서 말만 나온다.
    if signal == "name":
        terms = [t if isinstance(t, str) else t.get("term") for t in (ev.get("terms") or [])]
        return f"{label}에 " + ", ".join(f"**{t}**" for t in terms[:6] if t)

    if signal == "path":
        return f"{label} — " + ", ".join(str(x) for x in (ev.get("matched") or [])[:4])

    # 서식 항목 — "이 서식에 반드시 있어야 할 항목"이 몇 개 맞았는지.
    if signal == "form":
        matched = ev.get("matched") or []
        need = ev.get("min_types")
        tail = f"(최소 {need}종 필요)" if need else ""
        return (f"{label} " + ", ".join(f"**{m}**" for m in matched[:6]) + f" {tail}").rstrip()

    if signal == "structure":
        return f"{label} — " + ", ".join(str(x) for x in (ev.get("matched") or [])[:4])

    # 비슷한 문서 — 이웃 문서의 경로는 내지 않는다(Q7 이 아직 미결이다).
    if signal == "embed":
        return (f"{label} — 가장 비슷한 문서 {W.pct(ev.get('top_sim'))} · "
                f"이웃 중 {W.pct(ev.get('share'))} 가 이 분류")

    return label


#------------------------------------------------------------------
# 후보 하나 → 화면에 그릴 근거 묶음
#=> "왜 이 분류가 제안됐는가"를 화면이 그대로 찍기만 하면 되도록 정리해 준다.
#   문장 만들기를 여기 두는 이유는 Streamlit 없이 테스트하기 위해서다.
#    1) 사람이 직접 고른 분류 · 예전 형식 레코드는 각각 한 줄로 끝낸다
#    2) 그 밖에는 기여도가 큰 신호부터 줄을 만든다 — 가장 센 근거를 먼저 읽게
#    3) 근거가 둘 이상이면 "왜 이 숫자가 됐는지" 한 줄을 덧붙인다
#    4) 규칙 근거 없이 벡터만으로 뜬 후보는 그 사실을 경고로 남긴다
#
# -in: cand = effective_doctype() 이 낸 후보 dict
#             (evidence·score_parts·stage 는 없을 수도 있다 — 예전 레코드)
#
# -out: dict = {"kind": "manual"|"legacy"|"evidence",
#               "lines": [{"text","conf"}], "summary": str|None, "warn": str|None}
# -out: error = 없음
#------------------------------------------------------------------
def reason_block(cand):
    cand = cand or {}
    ev = cand.get("evidence") or {}
    parts = [p for p in (cand.get("score_parts") or []) if isinstance(p, dict)]

    # 사람이 직접 고른 분류는 기계 근거가 없다 — 그렇게 말해 준다.
    if "manual" in (cand.get("from") or []):
        return {"kind": "manual", "lines": [],
                "summary": "관리자가 직접 고른 분류입니다.", "warn": None}

    if not ev:
        # 예전 형식으로 분류된 레코드. 신호 이름만이라도 보여준다.
        why = ", ".join(W.SIGNAL_LABEL.get(s, s) for s in (cand.get("from") or []))
        return {"kind": "legacy", "lines": [],
                "summary": f"근거: {why}" if why else "근거 정보가 없습니다(예전 형식 레코드).",
                "warn": None}

    conf_by = {p.get("signal"): p.get("c") for p in parts}
    order = sorted(ev.keys(), key=lambda s: (-(conf_by.get(s) or 0), s))
    lines = [{"text": reason_text(s, ev[s]), "conf": conf_by.get(s)} for s in order]

    summary = None
    if len(parts) > 1:
        # 점수를 어떻게 합쳤는지는 엔진 설정(doc_rule.yaml 의 defaults.scoring)에
        # 달렸다. 화면은 그 설정을 모르지만 결과를 보면 알 수 있다 —
        # 최종 점수가 근거들의 '최댓값과 같으면' 가장 강한 하나만 쓴 것(legacy),
        # 그보다 크면 근거가 쌓여 올라간 것(staged, noisy-OR).
        # 이걸 구분하지 않고 늘 "겹쳐서 올라간다"고 적으면, 최댓값만 쓴 결과에
        # 대해 화면이 사실과 다른 설명을 하게 된다.
        conf = cand.get("confidence")
        top = max((p.get("c") or 0) for p in parts)
        try:
            stacked = float(conf) - float(top) > 0.005
        except (TypeError, ValueError):
            stacked = False
        if stacked:
            summary = (f"→ 근거 {len(parts)}가지가 겹쳐 {W.pct(conf)} "
                       f"(하나라도 강하면 올라가고, 여러 개면 더 올라가는 방식)")
        else:
            summary = (f"→ 근거 {len(parts)}가지 중 가장 강한 하나로 {W.pct(conf)} "
                       f"(여러 개라고 더 올라가지는 않는 방식)")
    warn = None
    if cand.get("stage") == "embed":
        warn = ("규칙에 걸린 말은 없고 **비슷한 문서**만 보고 제안한 후보입니다 "
                "— 특히 눈으로 확인해 주세요.")
    return {"kind": "evidence", "lines": lines, "summary": summary, "warn": warn}
