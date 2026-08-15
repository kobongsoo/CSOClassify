#------------------------------------------------------------------
# 문서 C/S/O 분류 검토 UI (Streamlit)
#=> csoclassify 이 만든 분류 결과(grades.jsonl)를 담당자가 검토·확정하는 화면.
#   문서중앙화로 수집된 문서의 자동 분류(C 기밀/S 민감/O 공개/보류)를 사람이
#   근거와 함께 확인하고, 필요하면 등급을 수정(오버라이드)한다.
#   [화면] 요약 대시보드 · 문서 목록 · 근거 상세 · 검토 큐 · 오버라이드 · 내보내기
#   [원칙] 원본 자동분류(grades.jsonl)는 절대 수정하지 않고, 사람의 수정은
#          overrides.jsonl 에 append-only 로 쌓아 감사 추적을 남긴다.
#   실행:  streamlit run ui/app.py   (또는 ui/실행.bat)
#------------------------------------------------------------------

import json
import os
import sys
import glob as globmod
import tempfile
import time
import datetime
from collections import Counter, defaultdict

import pandas as pd
import streamlit as st

# 같은 폴더의 seed 저장소·csoclassify 러너 모듈을 확실히 import 하도록 경로 추가.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seedstore
import gerunner
import rulesedit
import report

# 등급 표시 규약(색 이모지 + 한글). 보류(None)는 "보류" 키로 다룬다.
GRADE_LABEL = {"C": "🔴 C 기밀", "S": "🟠 S 민감", "O": "🟢 O 공개", "보류": "⚪ 보류"}
GRADE_ORDER = ["C", "S", "O", "보류"]
GRADE_CHOICES = ["C", "S", "O"]   # 오버라이드로 지정 가능한 등급


#------------------------------------------------------------------
# 등급 값 정규화(None → "보류")
#=> 레코드의 grade 는 None 일 수 있는데, 표/집계에서 다루기 쉽게 문자열 "보류"로 바꾼다.
#
# -in: g = 등급 값("C"/"S"/"O"/None)
#
# -out: str = "C"/"S"/"O"/"보류"
# -out: error = 없음
#------------------------------------------------------------------
def norm_grade(g):
    return g if g in ("C", "S", "O") else "보류"


#------------------------------------------------------------------
# 분류 결과(grades.jsonl) 로드
#=> jsonl 한 줄당 문서 1건인 분류 레코드를 리스트로 읽는다. 파일 수정시각(mtime)을
#   캐시 키에 넣어, 파일이 바뀌면 자동으로 다시 읽는다.
#
# -in: path  = grades.jsonl 경로
# -in: mtime = 파일 수정시각(캐시 무효화용, 값 자체는 안 씀)
#
# -out: list = 레코드 dict 리스트
# -out: error = 파일 없음/파싱 오류 시 예외 전파(호출측에서 처리)
#------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_records(path, mtime):
    recs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    return recs


#------------------------------------------------------------------
# 오버라이드(사람 수정) 로드
#=> overrides.jsonl 을 읽어, 파일별 "가장 최근 수정"과 전체 이력을 만든다.
#   같은 문서를 여러 번 고쳤으면 마지막 것이 유효하다(append-only, 나중 것 우선).
#
# -in: path = overrides.jsonl 경로(없으면 빈 결과)
#
# -out: (latest, history) = {file: 최신 entry}, {file: [entry,...]}
# -out: error = 없음(파일 없거나 깨진 줄은 건너뜀)
#------------------------------------------------------------------
def load_overrides(path):
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
            latest[e["file"]] = e            # 나중 줄이 앞 줄을 덮음 → 최신 유효
            history[e["file"]].append(e)
    return latest, history


#------------------------------------------------------------------
# 초 → "m분 ss초"/"s초" 표기
#=> 실행시간·경과시간을 사람이 읽기 좋은 한글 표기로 바꾼다.
#
# -in: sec = 초(실수/정수)
#
# -out: str = "3분 05초" 또는 "42초"
# -out: error = 없음
#------------------------------------------------------------------
def fmt_duration(sec):
    sec = int(sec)
    return f"{sec // 60}분 {sec % 60:02d}초" if sec >= 60 else f"{sec}초"


#------------------------------------------------------------------
# 실행 메타(총 실행시간) 사이드카 경로
#=> 분류 결과(grades_path) 옆에 실행 요약을 담는 작은 json 경로를 만든다.
#   결과 본문(jsonl)은 손대지 않고, 실행시간·완료시각만 따로 보관한다.
#
# -in: grades_path = 분류 결과 jsonl 경로
#
# -out: str = "<grades_path>.meta.json"
# -out: error = 없음
#------------------------------------------------------------------
def run_meta_path(grades_path):
    return grades_path + ".meta.json"


#------------------------------------------------------------------
# 실행 메타 저장(총 실행시간 등)
#=> 분류 실행이 끝나면 총 소요시간·대상 파일수·완료시각을 사이드카 json 에 쓴다.
#   대시보드가 이 값을 읽어 "총 실행시간"을 표시한다.
#
# -in: grades_path = 분류 결과 jsonl 경로
# -in: seconds     = 총 실행시간(초, wall-clock)
# -in: files       = 대상 파일 수
# -in: propagated  = 임베딩 전파까지 포함했는지 여부
#
# -out: 없음(사이드카 파일 기록)
# -out: error = 쓰기 실패는 호출부에서 무시(부가정보라 실행을 막지 않음)
#------------------------------------------------------------------
def save_run_meta(grades_path, seconds, files, propagated):
    meta = {
        "seconds": round(float(seconds), 1),
        "files": int(files),
        "propagated": bool(propagated),
        "finished_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(run_meta_path(grades_path), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


#------------------------------------------------------------------
# 실행 메타 로드
#=> 사이드카 json 에서 총 실행시간 등을 읽는다. 없거나 깨졌으면 None.
#
# -in: grades_path = 분류 결과 jsonl 경로
#
# -out: dict|None = {seconds, files, propagated, finished_at} 또는 None
# -out: error = 없음(문제 있으면 조용히 None)
#------------------------------------------------------------------
def load_run_meta(grades_path):
    p = run_meta_path(grades_path)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


#------------------------------------------------------------------
# 오버라이드 1건 기록(append-only)
#=> 담당자가 등급을 바꿀 때, 누가·언제·왜·이전→새 등급을 overrides.jsonl 에 한 줄
#   추가한다. 원본 grades.jsonl 은 건드리지 않는다(감사 추적 보존).
#
# -in: path      = overrides.jsonl 경로
# -in: file      = 대상 문서 경로
# -in: old_grade = 바꾸기 전 등급(자동 또는 이전 오버라이드)
# -in: new_grade = 새 등급("C"/"S"/"O"/"보류")
# -in: reason    = 수정 사유(감사용)
# -in: reviewer  = 검토자 이름
#
# -out: 없음(파일에 append)
# -out: error = 쓰기 실패 시 예외 전파
#------------------------------------------------------------------
def append_override(path, file, old_grade, new_grade, reason, reviewer):
    entry = {
        "file": file,
        "old_grade": old_grade,
        "new_grade": new_grade,
        "reason": reason,
        "reviewer": reviewer,
        "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


#------------------------------------------------------------------
# 최종 등급 계산(오버라이드 우선)
#=> 자동 등급 위에 사람 수정이 있으면 그걸 최종으로 삼는다.
#
# -in: rec    = 분류 레코드
# -in: latest = 파일별 최신 오버라이드 맵
#
# -out: (final_grade, is_overridden) = 최종 등급("C"/"S"/"O"/"보류"), 수정 여부
# -out: error = 없음
#------------------------------------------------------------------
def effective_grade(rec, latest):
    auto = norm_grade(rec.get("grade"))
    ov = latest.get(rec.get("file"))
    if ov:
        return norm_grade(ov.get("new_grade")), True
    return auto, False


#------------------------------------------------------------------
# 레코드 → 표 DataFrame
#=> 목록/집계에 쓰기 좋게 레코드들을 한 줄씩 평평한 표로 만든다.
#
# -in: records = 레코드 리스트
# -in: latest  = 파일별 최신 오버라이드 맵
#
# -out: DataFrame = file/folder/auto/final/overridden/confidence/method/decided_by...
# -out: error = 없음
#------------------------------------------------------------------
def records_to_df(records, latest):
    rows = []
    for r in records:
        final, overridden = effective_grade(r, latest)
        rows.append({
            "file": r.get("file", ""),
            "folder": os.path.dirname(r.get("file", "")),
            "name": os.path.basename(r.get("file", "")),
            "auto": norm_grade(r.get("grade")),
            "final": final,
            "overridden": overridden,
            "confidence": round(float(r.get("confidence", 0.0)), 3),
            "method": r.get("method", ""),
            "decided_by": ", ".join(r.get("decided_by", []) or []),
            "rule_version": r.get("rule_version", ""),
        })
    return pd.DataFrame(rows)


#------------------------------------------------------------------
# 근거 상세 렌더
#=> 선택한 문서가 "왜 이 등급인지"를 신호별로 풀어 보여준다.
#   rule(단어·건수)·path(위치)·name(파일명)·embed(비슷한 문서)를 모두 표시한다.
#
# -in: rec    = 분류 레코드
# -in: latest = 오버라이드 맵(최종 등급 표시용)
#
# -out: 없음(Streamlit 위젯 출력)
# -out: error = 없음
#------------------------------------------------------------------
def render_evidence(rec, latest):
    final, overridden = effective_grade(rec, latest)
    sig = rec.get("signals", {}) or {}

    c1, c2, c3 = st.columns(3)
    c1.metric("최종 등급", GRADE_LABEL[final] + (" (수정됨)" if overridden else ""))
    c2.metric("자동 등급", GRADE_LABEL[norm_grade(rec.get("grade"))])
    c3.metric("신뢰도", f"{rec.get('confidence', 0):.2f}")
    st.caption(f"판정방식: `{rec.get('method','')}`  ·  결정신호: "
               f"`{', '.join(rec.get('decided_by', []) or []) or '-'}`  ·  "
               f"룰버전: `{rec.get('rule_version','')}`")

    # ── 규칙(내용) 신호 ──
    rule = sig.get("rule", {})
    with st.expander(f"📄 내용 규칙(rule) — 등급 {rule.get('grade') or '-'}", expanded=True):
        hits = rule.get("hits", [])
        if not hits:
            st.write("검출된 PII·키워드 없음")
        for h in hits:
            terms = h.get("terms", [])
            # L2 키워드는 어떤 단어가 몇 번인지, L1 PII 는 건수만(원문 미노출).
            if terms:
                detail = ", ".join(f"{t['term']}×{t['count']}" for t in terms)
            else:
                detail = f"{h.get('count',0)}건(validated={h.get('validated',0)})"
            st.write(f"- **[{h.get('layer','')}] {h.get('name','')}** "
                     f"→ {h.get('grade','')}  ·  {detail}")

    # ── 경로 신호 ──
    path = sig.get("path", {})
    with st.expander(f"📁 경로(path) — 등급 {path.get('grade') or '-'}"):
        if path.get("grade"):
            st.write(f"- 걸린 규칙: `{path.get('source')}`  "
                     f"{'· 강한제한(ACL)' if path.get('acl_restricted') else ''}")
        else:
            st.write("경로 규칙에 걸리지 않음")

    # ── 파일명 신호 ──
    name = sig.get("name", {})
    with st.expander(f"🏷️ 파일명(name) — 등급 {name.get('grade') or '-'}"):
        nhits = name.get("hits", [])
        if not nhits:
            st.write("파일명에 키워드 없음")
        for h in nhits:
            st.write(f"- **{h.get('name','')}** → {h.get('grade','')} "
                     f"(단어: `{h.get('term','')}`)")

    # ── 임베딩 전파 신호 ──
    embed = sig.get("embed")
    if embed:
        with st.expander(f"🧭 임베딩 전파(embed) — 등급 {embed.get('grade') or '-'} "
                         f"(방식 {embed.get('method')}, top_sim {embed.get('top_sim')})"):
            neigh = embed.get("neighbors", [])
            if neigh:
                st.write("비슷한 문서(이웃):")
                ndf = pd.DataFrame([
                    {"유사도": n.get("sim"), "등급": n.get("grade"),
                     "문서": os.path.basename(n.get("file") or "")}
                    for n in neigh
                ])
                st.dataframe(ndf, hide_index=True, use_container_width=True)
            else:
                st.write("이웃 정보 없음")


#------------------------------------------------------------------
# 오버라이드 패널 렌더
#=> 선택 문서의 등급을 담당자가 수정하고 사유와 함께 저장한다. 이력도 보여준다.
#
# -in: rec       = 분류 레코드
# -in: latest    = 최신 오버라이드 맵
# -in: history   = 파일별 오버라이드 이력
# -in: ov_path   = overrides.jsonl 경로
# -in: reviewer  = 검토자 이름
# -in: scope     = 위젯 key 접두(호출 위치 구분용). st.tabs 는 모든 탭을 한 번에
#                  실행하므로, 같은 문서가 '문서목록'과 '검토큐'에 동시에 그려지면
#                  key 가 충돌한다(StreamlitDuplicateElementKey). 호출부마다 다른
#                  scope("list"/"queue")를 줘서 key 를 유일하게 만든다.
#
# -out: 없음(저장 시 파일 append + rerun)
# -out: error = 없음
#------------------------------------------------------------------
def render_override_panel(rec, latest, history, ov_path, reviewer, scope="list"):
    file = rec.get("file", "")
    cur, _ = effective_grade(rec, latest)

    st.markdown("##### ✍️ 등급 수정(오버라이드)")
    opts = GRADE_CHOICES + ["보류"]
    new_grade = st.radio("새 등급", opts,
                         index=opts.index(cur) if cur in opts else 0,
                         horizontal=True, key=f"ov_grade_{scope}_{file}")
    reason = st.text_input("수정 사유(감사 기록)", key=f"ov_reason_{scope}_{file}",
                           placeholder="예: 계약서지만 대외공개용이라 O 로 하향")

    if st.button("💾 오버라이드 저장", key=f"ov_save_{scope}_{file}", type="primary"):
        if not reviewer.strip():
            st.error("사이드바에 '검토자 이름'을 먼저 입력하세요(감사 기록용).")
        elif not reason.strip():
            st.error("수정 사유를 입력하세요.")
        else:
            append_override(ov_path, file, cur, new_grade, reason.strip(), reviewer.strip())
            st.success(f"저장됨: {GRADE_LABEL[cur]} → {GRADE_LABEL[new_grade]}")
            st.rerun()

    hist = history.get(file, [])
    if hist:
        st.markdown("###### 수정 이력")
        for e in reversed(hist):
            st.caption(f"- {e.get('ts','')} · {e.get('reviewer','')} · "
                       f"{norm_grade(e.get('old_grade'))}→{norm_grade(e.get('new_grade'))} "
                       f"· {e.get('reason','')}")


#------------------------------------------------------------------
# 요약 대시보드 렌더
#=> 전체 분포와 "검토 필요" 규모를 한눈에 보여준다.
#
# -in: df        = 문서 표 DataFrame
# -in: low_conf  = 저신뢰 기준값(이 미만이면 검토 권장)
# -in: run_meta  = 실행 메타(총 실행시간 등) 또는 None
#
# -out: 없음(Streamlit 출력)
# -out: error = 없음
#------------------------------------------------------------------
def render_dashboard(df, low_conf, run_meta=None):
    total = len(df)
    counts = Counter(df["final"])
    review_need = int(((df["final"] == "보류") |
                       (df["confidence"] < low_conf)).sum())
    overridden = int(df["overridden"].sum())

    m = st.columns(6)
    m[0].metric("전체", total)
    m[1].metric("🔴 C 기밀", counts.get("C", 0))
    m[2].metric("🟠 S 민감", counts.get("S", 0))
    m[3].metric("🟢 O 공개", counts.get("O", 0))
    m[4].metric("⚪ 보류", counts.get("보류", 0))
    m[5].metric("검토 필요", review_need, help=f"보류 + 신뢰도<{low_conf}")

    # 총 실행시간(마지막 분류 실행 기준). 실행 메타가 있으면 표기한다.
    if run_meta and run_meta.get("seconds") is not None:
        t1, t2 = st.columns([1, 3])
        t1.metric("⏱ 총 실행시간", fmt_duration(run_meta["seconds"]))
        detail = []
        if run_meta.get("files"):
            detail.append(f"{run_meta['files']}개 파일")
        detail.append("전파 포함" if run_meta.get("propagated") else "분류만")
        if run_meta.get("finished_at"):
            detail.append(f"완료 {run_meta['finished_at']}")
        t2.caption("마지막 실행 · " + " · ".join(detail))

    if overridden:
        st.caption(f"사람이 수정(오버라이드)한 문서: {overridden}건")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**등급 분포**")
        dist = pd.DataFrame({"등급": GRADE_ORDER,
                             "건수": [counts.get(g, 0) for g in GRADE_ORDER]}).set_index("등급")
        st.bar_chart(dist, horizontal=True)
    with c2:
        st.markdown("**폴더별 최다 등급 분포(상위 폴더)**")
        by_folder = (df.groupby("folder")["final"].count()
                     .sort_values(ascending=False).head(10))
        st.bar_chart(by_folder, horizontal=True)


#------------------------------------------------------------------
# 문서 목록 + 상세/오버라이드 탭 렌더
#=> 필터로 좁힌 문서 표를 보여주고, 행을 선택하면 아래에 근거 상세와 오버라이드
#   패널을 띄운다.
#
# -in: df        = 문서 표
# -in: records   = 원본 레코드(선택 문서의 상세용)
# -in: latest    = 최신 오버라이드 맵
# -in: history   = 오버라이드 이력
# -in: ov_path   = overrides.jsonl 경로
# -in: reviewer  = 검토자 이름
#
# -out: 없음(Streamlit 출력)
# -out: error = 없음
#------------------------------------------------------------------
def render_list(df, records, latest, history, ov_path, reviewer):
    rec_by_file = {r.get("file"): r for r in records}

    f1, f2, f3 = st.columns([2, 2, 3])
    grades = f1.multiselect("등급 필터", GRADE_ORDER, default=GRADE_ORDER)
    only_ov = f2.checkbox("수정된 것만")
    kw = f3.text_input("파일명/경로 검색", placeholder="예: 계약서, 인사")

    view = df[df["final"].isin(grades)]
    if only_ov:
        view = view[view["overridden"]]
    if kw.strip():
        k = kw.strip().lower()
        view = view[view["file"].str.lower().str.contains(k)]

    st.caption(f"{len(view)}건")
    show = view[["final", "auto", "overridden", "confidence", "method", "decided_by", "file"]]
    event = st.dataframe(
        show, hide_index=True, use_container_width=True,
        on_select="rerun", selection_mode="single-row",
        column_config={
            "final": "최종", "auto": "자동", "overridden": "수정",
            "confidence": "신뢰도", "method": "판정방식",
            "decided_by": "결정신호", "file": "경로",
        },
    )

    rows = event.selection.rows if event and event.selection else []
    if not rows:
        st.info("표에서 문서를 한 건 선택하면 근거 상세와 등급 수정이 나옵니다.")
        return
    sel_file = show.iloc[rows[0]]["file"]
    rec = rec_by_file.get(sel_file)
    if not rec:
        return

    st.divider()
    st.markdown(f"#### 🔎 {os.path.basename(sel_file)}")
    st.caption(sel_file)
    left, right = st.columns([3, 2])
    with left:
        render_evidence(rec, latest)
    with right:
        render_override_panel(rec, latest, history, ov_path, reviewer, scope="list")
        st.divider()
        render_promote(rec, effective_grade(rec, latest)[0])


#------------------------------------------------------------------
# 검토 큐 렌더
#=> 보류 또는 저신뢰 문서만 모아, 위에서부터 사람이 빠르게 등급을 확정하게 한다.
#
# -in: df        = 문서 표
# -in: records   = 원본 레코드
# -in: latest    = 최신 오버라이드 맵
# -in: history   = 오버라이드 이력
# -in: ov_path   = overrides.jsonl 경로
# -in: reviewer  = 검토자 이름
# -in: low_conf  = 저신뢰 기준값
#
# -out: 없음(Streamlit 출력)
# -out: error = 없음
#------------------------------------------------------------------
def render_review_queue(df, records, latest, history, ov_path, reviewer, low_conf):
    rec_by_file = {r.get("file"): r for r in records}
    queue = df[(df["final"] == "보류") | (df["confidence"] < low_conf)]
    # 아직 사람이 안 본 것(수정 안 됨)을 먼저, 신뢰도 낮은 순으로.
    queue = queue.sort_values(["overridden", "confidence"])

    st.caption(f"검토 대상 {len(queue)}건 (보류 또는 신뢰도<{low_conf})")
    if queue.empty:
        st.success("검토할 문서가 없습니다 👍")
        return

    for _, row in queue.head(30).iterrows():
        rec = rec_by_file.get(row["file"])
        with st.expander(f"{GRADE_LABEL[row['final']]}  ·  신뢰도 {row['confidence']:.2f}  ·  "
                         f"{os.path.basename(row['file'])}"):
            st.caption(row["file"])
            if rec:
                render_evidence(rec, latest)
                render_override_panel(rec, latest, history, ov_path, reviewer, scope="queue")
                st.divider()
                render_promote(rec, effective_grade(rec, latest)[0], scope="queue")


#------------------------------------------------------------------
# 내보내기 렌더
#=> 최종 등급(자동+오버라이드 반영)을 CSV/JSONL 로 내려받아 문서중앙화 시스템이
#   접근권한 적용에 쓰게 한다.
#
# -in: df = 문서 표
#
# -out: 없음(다운로드 버튼)
# -out: error = 없음
#------------------------------------------------------------------
def render_export(df):
    out = df[["file", "final", "auto", "overridden", "confidence", "method", "rule_version"]]
    out = out.rename(columns={"final": "grade"})
    st.markdown("확정 등급(최종 = 자동분류 + 사람 오버라이드)을 내보냅니다.")
    st.download_button("⬇️ CSV 내보내기", out.to_csv(index=False).encode("utf-8-sig"),
                       "classification_final.csv", "text/csv")
    jsonl = "\n".join(json.dumps(r, ensure_ascii=False) for r in out.to_dict("records"))
    st.download_button("⬇️ JSONL 내보내기", jsonl.encode("utf-8"),
                       "classification_final.jsonl", "application/json")
    st.dataframe(out, hide_index=True, use_container_width=True)


#------------------------------------------------------------------
# 파일 1개 온디맨드 임베딩
#=> 빠른 분류(선택적 임베딩)로 벡터가 생략된 문서를 seed 로 승격할 때, 그 문서
#   하나만 지금 임베딩해서 벡터를 얻는다("확정 문서는 나중 승격 시점에 임베딩" 정책).
#
# -in: cfg  = seedcfg(csoclassify_cmd/pythonpath)
# -in: file = 대상 문서 경로(원본이 그 위치에 있어야 함)
#
# -out: list|None = 384차원 벡터(실패하면 None, 화면에 오류 표시)
# -out: error = 없음(예외는 화면 표시 후 None 반환)
#------------------------------------------------------------------
def _embed_file_ondemand(cfg, file):
    base = gerunner.parse_base_cmd(cfg.get("csoclassify_cmd"))
    try:
        with st.spinner(f"임베딩 계산 중… {os.path.basename(file)}"):
            r = gerunner.run_csoclassify_file(base, file, pythonpath=cfg.get("pythonpath"))
        return r.get("vector")
    except Exception as e:
        st.error(f"임베딩 실패: {e}")
        return None


#------------------------------------------------------------------
# seed 승격 버튼 렌더
#=> 확정된 문서를 seed 저장소(cso_seed.jsonl)에 씨앗으로 올린다(Phase 4 ①).
#   벡터가 없으면(빠른 분류로 생략됨) 승격 시 그 문서만 임베딩해 벡터를 확보한다.
#   기존 seed 와 near-dup 이면 중복 경고 후 확인받는다.
#
# -in: rec         = 분류 레코드(vector 는 없을 수 있음 → 승격 시 온디맨드 계산)
# -in: final_grade = 승격할 최종 등급(C/S/O)
# -in: scope       = 위젯 key 접두(호출 위치 구분). render_override_panel 과 같은
#                    이유로, 같은 문서가 여러 탭에 동시에 그려질 때 key 충돌을 막는다.
#
# -out: 없음(추가 시 파일 저장 + 감사 + rerun)
# -out: error = 없음
#------------------------------------------------------------------
def render_promote(rec, final_grade, scope="list"):
    cfg = st.session_state.get("seedcfg", {})
    file = rec.get("file", "")
    vec = rec.get("vector")

    st.markdown("##### 🌱 seed로 승격")
    if final_grade not in ("C", "S", "O"):
        st.caption("보류 문서는 등급을 먼저 확정한 뒤 승격하세요.")
        return

    seeds = seedstore.load_seeds(cfg.get("seed_path"))
    already = any(s.get("file") == file for s in seeds)
    if already:
        st.caption("이 문서는 이미 seed 에 있습니다 → 추가 시 최신 등급/벡터로 갱신됩니다.")

    # 벡터가 있으면 미리 near-dup 을 경고(자기 자신 제외), 없으면 승격 시 온디맨드 임베딩.
    force = True
    sim = 0.0
    if vec is not None:
        sim, near = seedstore.nearest_seed_sim(vec, seeds, exclude_file=file)
        if sim >= 0.985:
            st.warning(f"다른 seed 와 거의 같은 문서(sim={sim:.3f} · {os.path.basename(near or '')}) "
                       f"— 사본일 수 있어 추가해도 정보량이 늘지 않습니다.")
            force = st.checkbox("중복이어도 추가", key=f"seedforce_{scope}_{file}")
    else:
        st.caption("이 문서는 벡터가 없어(빠른 분류로 생략됨), 승격 시 이 문서만 임베딩을 계산합니다.")

    if st.button(f"🌱 {final_grade} seed 로 추가", key=f"promote_{scope}_{file}"):
        if not cfg.get("reviewer", "").strip():
            st.error("사이드바 '검토자 이름'을 입력하세요.")
            return
        if not force:
            st.info("중복이라 추가하지 않았습니다.")
            return
        v = vec
        if v is None:                       # 벡터 없으면 지금 이 문서만 임베딩해 확보
            v = _embed_file_ondemand(cfg, file)
            if v is None:
                return
        seeds2 = seedstore.add_seed(seeds, file, final_grade, v,
                                    cfg["reviewer"].strip(), source="phase4")
        seedstore.save_seeds(cfg["seed_path"], seeds2)
        seedstore.append_seed_audit(cfg["audit_path"], "add", file,
                                    f"promote grade={final_grade} sim={sim:.3f}",
                                    cfg["reviewer"].strip())
        st.success("seed 저장소에 추가됨")
        st.rerun()


#------------------------------------------------------------------
# seed 관리 화면 렌더 (Phase 4)
#=> seed 구성 확인 + 이번 분류에서 고신뢰 C/S 일괄 승격 + 신규 파일 직접 업로드 추가
#   + 기존 seed 인라인 편집(등급/메모/삭제).
#
# -in: records = 이번 분류 레코드 리스트(일괄 승격 후보 추출용, 벡터 포함)
# -in: latest  = 파일별 최신 오버라이드 맵(승격 시 사람이 고친 최종등급 반영)
#
# -out: 없음(Streamlit 출력)
# -out: error = 없음(개별 실패는 화면에 표시)
#------------------------------------------------------------------
def render_seed_manager(records, latest):
    cfg = st.session_state.get("seedcfg", {})
    seed_path = cfg.get("seed_path")
    audit_path = cfg.get("audit_path")
    reviewer = cfg.get("reviewer", "")

    seeds = seedstore.load_seeds(seed_path)
    counts = seedstore.grade_counts(seeds)

    st.markdown(f"**seed 저장소**: `{seed_path}` · 총 {len(seeds)}건")
    m = st.columns(4)
    m[0].metric("총 seed", len(seeds))
    m[1].metric("🔴 C", counts["C"])
    m[2].metric("🟠 S", counts["S"])
    m[3].metric("🟢 O", counts["O"])
    if seeds:
        lo = min(counts.values())
        if lo == 0 or lo < max(counts.values()) / 4:
            weak = "/".join(g for g, n in counts.items() if n == lo)
            st.warning(f"등급 구성 편중 — {weak} seed 가 부족합니다. "
                       "임베딩이 해당 등급을 잘 구분 못 할 수 있어요(보강 권장).")

    st.divider()
    st.markdown("##### ① seed 승격")
    st.caption("규칙으로 확실하게 등급이 붙은(=고신뢰) 문서와 사람이 직접 확정한 문서를 "
               "seed 로 올립니다. 아래에서 승격할 등급을 골라 [일괄승격]을 누르세요. "
               "사본(near-dup)은 자동으로 건너뛰어 저장소를 깔끔하게 유지합니다.")
    # 후보: 최종등급 C/S/O + 고신뢰(규칙 seed_eligible 또는 사람이 오버라이드로 확정).
    # 벡터 있는 건 바로, 없는 건(빠른 분류로 생략) 승격 시 온디맨드로 임베딩한다.
    cands_by_grade = {"C": [], "S": [], "O": []}   # 등급별 벡터 보유 후보 {file,grade,vector}
    novec_by_grade = {"C": [], "S": [], "O": []}   # 등급별 벡터 없는 후보 {file,grade}
    for rec in records:
        g, overridden = effective_grade(rec, latest)
        if g in ("C", "S", "O") and (rec.get("seed_eligible") or overridden):
            if rec.get("vector"):
                cands_by_grade[g].append({"file": rec.get("file"), "grade": g, "vector": rec.get("vector")})
            else:
                novec_by_grade[g].append({"file": rec.get("file"), "grade": g})

    # 등급별 체크박스 — 각 등급 후보 수를 라벨에 표시(후보 0건이면 비활성).
    grade_emoji = {"C": "🔴", "S": "🟠", "O": "🟢"}
    cc = st.columns(3)
    picked = {}
    for i, g in enumerate(("C", "S", "O")):
        n_g = len(cands_by_grade[g]) + len(novec_by_grade[g])
        picked[g] = cc[i].checkbox(f"{grade_emoji[g]} {g} ({n_g}건)",
                                   value=(g in ("C", "S")),   # 기본: C/S 선택, O 미선택
                                   disabled=not n_g, key=f"seedpromo_pick_{g}")

    # 선택된 등급의 후보만 모은다.
    chosen = [g for g in ("C", "S", "O") if picked.get(g)]
    cands = [c for g in chosen for c in cands_by_grade[g]]
    novec_cands = [c for g in chosen for c in novec_by_grade[g]]
    total_cand = len(cands) + len(novec_cands)
    st.caption(f"선택 등급 **{'/'.join(chosen) if chosen else '없음'}** · 승격 후보 **{total_cand}건** "
               "(중복·기존 등록분은 자동 제외)"
               + (f" · 그중 {len(novec_cands)}건은 벡터가 없어 승격 시 임베딩을 계산합니다(지연)"
                  if novec_cands else ""))
    if st.button("🌱 일괄승격(중복 제외)", type="primary", disabled=not total_cand):
        if not reviewer.strip():
            st.error("사이드바 '검토자 이름'을 먼저 입력하세요(감사 기록용).")
        else:
            allcands = list(cands)
            # 벡터 없는 후보는 그 문서만 임베딩해 벡터를 채운다(실패분은 조용히 제외).
            if novec_cands:
                prog = st.progress(0.0, text=f"벡터 없는 후보 {len(novec_cands)}건 임베딩 중…")
                base = gerunner.parse_base_cmd(cfg.get("csoclassify_cmd"))
                for j, c in enumerate(novec_cands):
                    try:
                        r = gerunner.run_csoclassify_file(base, c["file"],
                                                          pythonpath=cfg.get("pythonpath"))
                        v = r.get("vector")
                        if v:
                            allcands.append({"file": c["file"], "grade": c["grade"], "vector": v})
                    except Exception:
                        pass
                    prog.progress((j + 1) / len(novec_cands))
            new_seeds, added, stats = seedstore.bulk_add_confident(
                seeds, allcands, reviewer.strip(), source="phase2", note="일괄 승격")
            if added:
                seedstore.save_seeds(seed_path, new_seeds)
                for a in added:
                    seedstore.append_seed_audit(audit_path, "add", a["file"],
                                                f"bulk grade={a['grade']}", reviewer.strip())
                st.success(f"{stats['added']}건 승격 · 건너뜀: 중복 {stats['dup']} · "
                           f"기존 {stats['exist']} · 벡터없음 {stats['novec']}")
                st.rerun()
            else:
                st.info(f"새로 추가된 seed 가 없습니다(중복 {stats['dup']} · "
                        f"기존 {stats['exist']} · 벡터없음 {stats['novec']}).")

    st.divider()
    st.markdown("##### ② 신규 파일 직접 업로드로 추가")
    st.caption("분류 배치를 거치지 않고, 관리자가 대표 문서를 직접 씨앗으로 등록합니다.")
    # 완료 후 업로드/등급/메모 위젯을 비우려고 버전 카운터로 key 를 갈아끼운다(초기화 트릭).
    ver = st.session_state.get("up_ver", 0)
    flash = st.session_state.pop("up_flash", None)   # 초기화 직후에도 결과 메시지는 보이게
    if flash:
        st.success(flash)
    up = st.file_uploader("파일 업로드(여러 개 가능)", accept_multiple_files=True,
                          key=f"up_file_{ver}")
    ug = st.radio("부여할 등급", ["C", "S", "O"], horizontal=True, key=f"up_grade_{ver}")
    unote = st.text_input("메모(선택)", key=f"up_note_{ver}")
    if st.button("실행 + seed 추가", type="primary"):
        if not reviewer.strip():
            st.error("사이드바 '검토자 이름'을 먼저 입력하세요.")
        elif not up:
            st.warning("파일을 올리세요.")
        else:
            base = gerunner.parse_base_cmd(cfg.get("csoclassify_cmd"))
            cur = seeds
            added = 0
            n_up = len(up)
            prog = st.progress(0.0, text=f"업로드 처리 중… (0/{n_up})")
            for i, uf in enumerate(up):
                # 처리 시작 시 현재 파일명을 진행바에 표시(임베딩이 느릴 수 있어).
                prog.progress(i / n_up, text=f"임베딩·등록 중… {i}/{n_up} · {uf.name}")
                tmpd = tempfile.mkdtemp()
                tp = os.path.join(tmpd, uf.name)
                with open(tp, "wb") as f:
                    f.write(uf.getbuffer())
                try:
                    rec = gerunner.run_csoclassify_file(base, tp, pythonpath=cfg.get("pythonpath"))
                    vec = rec.get("vector")
                    if not vec:
                        st.error(f"{uf.name}: 벡터를 얻지 못했습니다.")
                    else:
                        cur = seedstore.add_seed(cur, uf.name, ug, vec, reviewer.strip(),
                                                 source="upload", note=unote.strip())
                        seedstore.append_seed_audit(audit_path, "add", uf.name,
                                                    f"upload grade={ug}", reviewer.strip())
                        added += 1
                except Exception as e:
                    st.error(f"{uf.name}: {e}")
                prog.progress((i + 1) / n_up, text=f"완료 {i + 1}/{n_up} · {uf.name}")
            if added:
                seedstore.save_seeds(seed_path, cur)
                # 완료 → 다음 실행에서 업로드/등급/메모 초기화(ver+1) + 결과 메시지 표시(flash)
                st.session_state["up_flash"] = f"{added}건 seed 추가됨 · 업로드/등급/메모 초기화됨"
                st.session_state["up_ver"] = ver + 1
                st.rerun()
            else:
                st.warning("추가된 seed 가 없습니다(위 오류 메시지를 확인하세요).")

    st.divider()
    st.markdown("##### ③ 기존 seed 편집(등급 변경 · 메모 · 삭제)")
    if not seeds:
        st.info("아직 seed 가 없습니다. 위에서 업로드하거나, 문서 목록에서 '승격' 하세요.")
        return
    st.caption(f"총 {len(seeds)}건 · 🔴 C {counts['C']} · 🟠 S {counts['S']} · 🟢 O {counts['O']}")
    sdf = pd.DataFrame([{"file": s.get("file"), "grade": s.get("grade"),
                         "source": s.get("source", ""), "note": s.get("note", ""),
                         "삭제": False} for s in seeds])
    edited = st.data_editor(
        sdf, hide_index=True, use_container_width=True, key="seed_editor",
        column_config={
            "file": st.column_config.TextColumn("문서", disabled=True),
            "grade": st.column_config.SelectboxColumn("등급", options=["C", "S", "O"]),
            "source": st.column_config.TextColumn("출처", disabled=True),
            "note": st.column_config.TextColumn("메모"),
            "삭제": st.column_config.CheckboxColumn("삭제"),
        },
    )
    if st.button("변경 저장"):
        if not reviewer.strip():
            st.error("사이드바 '검토자 이름'을 먼저 입력하세요.")
            return
        orig = {s.get("file"): s for s in seeds}
        new, changes = [], 0
        for _, row in edited.iterrows():
            f = row["file"]
            o = orig.get(f, {})
            if row["삭제"]:
                seedstore.append_seed_audit(audit_path, "delete", f, "deleted", reviewer.strip())
                changes += 1
                continue
            s = dict(o)   # 벡터·출처 등 기존 필드 보존
            if row["grade"] != o.get("grade"):
                seedstore.append_seed_audit(audit_path, "update", f,
                                            f"grade {o.get('grade')}→{row['grade']}", reviewer.strip())
                changes += 1
            if (row["note"] or "") != (o.get("note") or ""):
                changes += 1
            s["grade"] = row["grade"]
            s["note"] = row["note"] or ""
            new.append(s)
        if changes:
            seedstore.save_seeds(seed_path, new)
            st.success(f"{changes}건 변경 저장됨")
            st.rerun()
        else:
            st.info("변경 사항이 없습니다.")


#------------------------------------------------------------------
# 분류 실행 패널 렌더 (STEP 3 / Phase 1 수집 · Phase 3 분류의 "실행")
#=> 대상 폴더를 골라 csoclassify 분류를 UI에서 바로 돌린다. 결과는 사이드바가 가리키는
#   경로(grades_path)에 저장되어 다른 탭이 곧바로 읽는다.
#    1) 폴더·패턴·재귀·전파 선택
#    2) csoclassify --embed-needed 실행(분류 기본 · 확정문서 임베딩 생략, 진행바)
#    3) (선택) 임베딩 전파로 보류분 구제(내부+외부 seed) → 최종 결과
#    4) 완료 후 rerun → 결과 자동 로드
#
# -in: grades_path = 결과를 저장(=읽을) 경로(사이드바 값)
#
# -out: 없음(실행 시 파일 생성 + rerun)
# -out: error = 실패는 화면에 표시
#------------------------------------------------------------------
def render_run_panel(grades_path):
    cfg = st.session_state.get("seedcfg", {})
    st.markdown("##### ▶ 폴더를 분류해 결과를 만듭니다")
    folder = st.text_input("대상 폴더 경로", placeholder=r"D:\collected\... (이 PC의 폴더)")
    c1, c2 = st.columns(2)
    pattern = c1.text_input("파일 패턴", value="*")
    recursive = c2.checkbox("하위 폴더 포함(-r)", value=True)

    do_prop = st.checkbox("임베딩 전파로 보류분 구제", value=True,
                          help="보류(미확정) 문서를 '내부 seed(seed_eligible 문서) + 외부 cso_seed.jsonl'와 "
                               "비교해 등급을 구제합니다. 외부 seed가 없으면 내부 seed만 사용합니다.")
    st.caption(f"결과 저장 위치(=다른 탭이 읽는 곳): `{grades_path}`")

    if st.button("▶ 분류 실행", type="primary"):
        if not folder or not os.path.isdir(folder):
            st.error("유효한 폴더 경로를 입력하세요.")
            return
        # 진행 규모 안내용으로 대상 파일 수를 미리 센다(CLI 와 동일하게 콤마·중괄호
        # 다중 패턴을 펼쳐 각각 매칭 후 중복 제거).
        matched = set()
        for gp in gerunner.expand_glob_patterns(pattern):
            pat = os.path.join(folder, "**", gp) if recursive else os.path.join(folder, gp)
            matched.update(f for f in globmod.glob(pat, recursive=recursive) if os.path.isfile(f))
        n = len(matched)
        if n == 0:
            st.warning("패턴에 맞는 파일이 없습니다.")
            return
        st.info(f"{n}개 파일 분류 — 규칙/경로/파일명으로 확정된 문서는 임베딩을 건너뛰어 빠릅니다"
                "(보류·seed 후보만 임베딩).")
        base = gerunner.parse_base_cmd(cfg.get("csoclassify_cmd"))

        # (1) 분류 — 규칙/경로/파일명 + 선택적 임베딩(보류·seed_eligible). 진행바 실시간 표시
        prog = st.progress(0.0, text=f"분류 준비 중… (0/{n})")
        start = time.perf_counter()

        # 초 → "m분 ss초"/"s초" 표기(경과·잔여 시간 표시용).
        _fmt = fmt_duration

        # csoclassify 가 파일 1개 끝낼 때마다 불려 진행바·경과·잔여시간을 갱신.
        def _on_prog(done, total, path=""):
            elapsed = time.perf_counter() - start
            # 첫 신호(0/N): 모델 로딩 구간이라 잠시 0% 에서 멈춰 보이는 게 정상.
            if done == 0:
                prog.progress(0.0, text=f"모델 로딩·첫 파일 준비 중… (0/{total}) · "
                                        f"첫 파일은 수십 초 걸릴 수 있어요")
                return
            frac = (done / total) if total else 0.0
            name = os.path.basename(path.rstrip()) if path else ""
            # 지금까지 속도로 남은 시간 추정(1건 이상 끝났을 때만).
            eta = (elapsed / done * (total - done)) if done else 0
            msg = f"분류 중… {done}/{total} ({int(frac * 100)}%) · 경과 {_fmt(elapsed)}"
            if done and done < total:
                msg += f" · 남은 시간 ≈ {_fmt(eta)}"
            if name:
                msg += f" · {name}"
            prog.progress(min(frac, 1.0), text=msg)

        try:
            r = gerunner.run_csoclassify_dir_stream(
                base, folder, grades_path, pattern, recursive,
                pythonpath=cfg.get("pythonpath"), on_progress=_on_prog)
        except Exception as e:
            st.error(f"실행 오류: {e}")
            return
        if not os.path.isfile(grades_path):
            st.error(f"분류 결과 파일이 생성되지 않았습니다.\n{r.stderr[-600:]}")
            return
        total_elapsed = time.perf_counter() - start
        prog.progress(1.0, text=f"분류 완료 · {n}/{n} · 총 {_fmt(total_elapsed)}")
        st.success(f"분류 완료 · {r.summary or 'OK'} · 소요 {_fmt(total_elapsed)}")

        # (2) 선택: 임베딩 전파 — 보류 문서를 내부 seed(+있으면 외부 seed)와 비교해 구제
        if do_prop:
            sp = cfg.get("seed_path")
            use_ext = bool(sp and os.path.isfile(sp))
            try:
                with st.spinner("보류 문서 임베딩 비교(전파) 중…"):
                    r2 = gerunner.run_propagate(base, grades_path, grades_path,
                                                sp if use_ext else None,
                                                pythonpath=cfg.get("pythonpath"))
                note = "외부+내부 seed" if use_ext else "내부 seed"
                st.success(f"전파 완료({note}) · {r2.summary or 'OK'}")
            except Exception as e:
                st.error(f"전파 오류: {e}")

        # 총 실행시간(분류+선택적 전파까지)을 사이드카에 남겨, 대시보드가 표기하게 한다.
        run_seconds = time.perf_counter() - start
        try:
            save_run_meta(grades_path, run_seconds, n, do_prop)
        except OSError:
            pass  # 부가정보라 저장 실패해도 실행은 정상 처리

        st.info("결과가 로드되었습니다. 위 탭에서 확인하세요.")
        st.rerun()


#------------------------------------------------------------------
# 진단 리포트 화면 렌더 (STEP 5)
#=> seed 저장소 균형·중복과 분류 결과의 신호분포·신뢰도·중복문서를 한 화면에서
#   진단해, 어디를 보강하고 무엇을 정리할지 알려준다.
#
# -in: df       = 문서 표(final/confidence)
# -in: records  = 원본 레코드(decided_by/vector)
# -in: low_conf = 저신뢰 기준
#
# -out: 없음(Streamlit 출력)
# -out: error = 없음
#------------------------------------------------------------------
def render_report(df, records, low_conf):
    cfg = st.session_state.get("seedcfg", {})
    thr = st.slider("중복 판정 임계값(코사인)", 0.90, 1.0, 0.97, 0.01,
                    help="이 값 이상이면 '사실상 같은 문서'로 봅니다.")

    st.markdown("### 🌱 seed 저장소 진단")
    seeds = seedstore.load_seeds(cfg.get("seed_path"))
    counts, total, weak, msg = report.seed_balance(seeds)
    c = st.columns(4)
    c[0].metric("총 seed", total)
    c[1].metric("🔴 C", counts["C"])
    c[2].metric("🟠 S", counts["S"])
    c[3].metric("🟢 O", counts["O"])
    (st.warning if weak else st.success)(msg)

    dpairs, _ = report.near_dups(
        [{"file": s.get("file"), "vector": s.get("vector")} for s in seeds], thr)
    if dpairs:
        st.markdown(f"**중복 seed {len(dpairs)}쌍** — 하나만 남기고 'seed 관리'에서 삭제 권장")
        st.dataframe(pd.DataFrame([{"유사도": round(s, 3),
                                    "문서 A": os.path.basename(a or ""),
                                    "문서 B": os.path.basename(b or "")}
                                   for a, b, s in dpairs[:50]]),
                     hide_index=True, use_container_width=True)
    else:
        st.caption("중복 seed 없음(또는 seed 에 벡터가 없음).")

    st.divider()
    st.markdown("### 📊 분류 결과 진단")
    review = int(((df["final"] == "보류") | (df["confidence"] < low_conf)).sum())
    cc = st.columns(3)
    cc[0].metric("전체 문서", len(df))
    cc[1].metric("검토 필요", review, help=f"보류 + 신뢰도<{low_conf}")
    cc[2].metric("보류", int((df["final"] == "보류").sum()))

    db = report.decided_by_counts(records)
    if db:
        st.markdown("**판정 신호 분포** — 어떤 신호가 분류를 이끌었나(임베딩 의존도 확인)")
        st.bar_chart(pd.DataFrame({"건수": db}), horizontal=True)

    cb = report.confidence_bins(records)
    st.markdown("**신뢰도 분포**")
    st.bar_chart(pd.DataFrame({"건수": cb}))

    st.markdown("**중복 문서(코퍼스 near-dup)** — 같은 문서가 여러 위치에 복사됨")
    cpairs, ctr = report.near_dups(
        [{"file": r.get("file"), "vector": r.get("vector")} for r in records], thr)
    if ctr:
        st.caption("문서가 많아 앞부분만 비교했습니다(상한 도달).")
    if cpairs:
        st.caption(f"중복 후보 {len(cpairs)}쌍")
        st.dataframe(pd.DataFrame([{"유사도": round(s, 3), "문서 A": a, "문서 B": b}
                                   for a, b, s in cpairs[:100]]),
                     hide_index=True, use_container_width=True)
    else:
        st.caption("중복 문서 없음(또는 결과에 벡터 없음 — `--with-vector` 로 분류해야 감지).")


#------------------------------------------------------------------
# 규칙 설정 화면 렌더 (STEP 4 / Phase 0)
#=> cso_rules.yaml 을 UI에서 편집한다. 기밀사전(키워드)·경로·bulk 임계값을 고치고,
#   샘플 텍스트로 미리보기한 뒤, 버전을 자동 상향하며 저장(백업)한다. 정규식 PII 는
#   위험도가 커서 읽기 전용으로 두고, 패턴은 YAML 직접 편집하도록 안내한다.
#
# -in: 없음(설정은 st.session_state["rulescfg"])
# -out: 없음(저장 시 파일 기록 + rerun)
# -out: error = 로드/미리보기 실패는 화면에 표시
#------------------------------------------------------------------
def render_rules_editor():
    cfg = st.session_state.get("rulescfg", {})
    path = cfg.get("rules_path")
    if not path or not os.path.isfile(path):
        st.warning("사이드바에서 유효한 cso_rules.yaml 경로를 지정하세요.")
        return

    try:
        doc = rulesedit.load_doc(path)
    except Exception as e:
        st.error(f"규칙셋을 읽지 못했습니다: {e}")
        return

    st.markdown(f"**규칙셋**: `{path}` · 버전 `{doc.get('version')}`")
    bulk = st.number_input("bulk 임계값 (PII 가 이 건수 이상이면 등급 상향)",
                           min_value=1, value=int(doc.get("defaults", {}).get("bulk_threshold", 5)))

    st.markdown("##### 기밀사전(키워드) — 단어는 콤마로 구분")
    kdf = pd.DataFrame([{"id": k.get("id"), "name": k.get("name"),
                         "terms": ", ".join(k.get("terms") or []),
                         "base_grade": k.get("base_grade"),
                         "seed": bool(k.get("seed_eligible"))} for k in doc.get("keywords", [])])
    kedit = st.data_editor(
        kdf, num_rows="dynamic", use_container_width=True, key="kw_editor",
        column_config={
            "id": st.column_config.TextColumn("id"),
            "name": st.column_config.TextColumn("이름"),
            "terms": st.column_config.TextColumn("단어(콤마 구분)", width="large"),
            "base_grade": st.column_config.SelectboxColumn("등급", options=["C", "S", "O"]),
            "seed": st.column_config.CheckboxColumn("seed"),
        })

    st.markdown("##### 경로 규칙 — 조각은 콤마로 구분 (예: /인사/, //hr-server/)")
    pdf = pd.DataFrame([{"id": p.get("id"), "name": p.get("name"),
                         "match": ", ".join(p.get("match") or []),
                         "grade": p.get("grade"),
                         "seed": bool(p.get("seed_eligible"))} for p in doc.get("paths", [])])
    pedit = st.data_editor(
        pdf, num_rows="dynamic", use_container_width=True, key="path_editor",
        column_config={
            "id": st.column_config.TextColumn("id"),
            "name": st.column_config.TextColumn("이름"),
            "match": st.column_config.TextColumn("경로 조각(콤마 구분)", width="large"),
            "grade": st.column_config.SelectboxColumn("등급", options=["C", "S", "O"]),
            "seed": st.column_config.CheckboxColumn("seed"),
        })

    st.markdown("##### PII 유형 (검출 엔진: ko-pii) — 유형 → 등급 정책")
    _labopts = sorted(set(rulesedit.kopii_labels()) |
                      {r.get("label") for r in doc.get("regex_pii", []) if r.get("label")})
    st.caption(f"검출은 **ko-pii** 가 담당합니다(패턴·체크섬·문맥·우회방어 내장). 여기선 "
               f"'label(ko-pii 유형) → 등급' 정책만 정합니다. 기본등급 → 상향등급 은 검출 건수가 "
               f"**bulk 임계값({int(bulk)}건)** 이상일 때입니다.")
    rdf = pd.DataFrame([{"id": r.get("id"), "name": r.get("name"),
                         "label": r.get("label"),
                         "base_grade": r.get("base_grade"), "bulk_grade": r.get("bulk_grade"),
                         "weight": r.get("weight", "medium"),
                         "seed": bool(r.get("seed_eligible"))}
                        for r in doc.get("regex_pii", [])])
    redit = st.data_editor(
        rdf, num_rows="dynamic", use_container_width=True, key="regex_editor",
        column_config={
            "id": st.column_config.TextColumn("id"),
            "name": st.column_config.TextColumn("이름"),
            "label": st.column_config.SelectboxColumn("ko-pii 유형", options=_labopts),
            "base_grade": st.column_config.SelectboxColumn("기본등급", options=["C", "S", "O"]),
            "bulk_grade": st.column_config.SelectboxColumn("상향등급", options=["C", "S", "O"]),
            "weight": st.column_config.SelectboxColumn("가중", options=["high", "medium", "low"]),
            "seed": st.column_config.CheckboxColumn("seed"),
        })

    # 편집 행(공셀 정리) 준비 — 미리보기·저장 공용.
    krows = kedit.fillna("").to_dict("records")
    prows = pedit.fillna("").to_dict("records")
    rrows = redit.fillna("").to_dict("records")

    with st.expander("신뢰도(weight→conf) — 읽기 전용"):
        st.caption("신뢰도는 **검토 큐 편입·정렬**에만 쓰이고 C/S/O 등급은 바꾸지 않습니다. "
                   "수정하려면 **cso_rules.yaml 을 직접 편집**하세요(이 표는 표기 전용).")
        conf = doc.get("confidence", {}) or {}
        st.dataframe(pd.DataFrame([{"신호": grp,
                                    "high": (conf.get(grp) or {}).get("high"),
                                    "medium": (conf.get(grp) or {}).get("medium"),
                                    "low": (conf.get(grp) or {}).get("low")}
                                   for grp in ("regex", "keyword", "path")]),
                     hide_index=True, use_container_width=True)
        st.caption(f"파일명(name) 고정 신뢰도: {conf.get('name')}")

    st.divider()
    st.markdown("##### 미리보기 — 샘플 텍스트로 등급 확인(저장 안 함)")
    # 기본 샘플 = ko-pii 18종 PII 를 모두 검출하도록 실측으로 맞춘 예시(전부 가짜값).
    # ⚠ 라인 배치 주의: 차량번호(VEHICLE) 바로 뒤에 건강보험(1-2345678901)이 오면
    #   ko-pii 겹침해소로 차량이 사라져, 차량은 이메일·주소 사이에 떨어뜨려 둔다.
    _pii18_sample = (
        "[개인정보 예시 — 18종 PII 검출 확인용]\n"
        "성명 홍길동, 국적: 대한민국\n"
        "휴대전화 010-1234-5678\n"
        "이메일 hong@example.com\n"
        "차량번호 12가3456\n"
        "주소 서울특별시 강남구 테헤란로 152\n"
        "주민등록번호 900101-1234567\n"
        "외국인등록번호 900101-5234567\n"
        "여권번호 M12345678\n"
        "운전면허번호 11-12-345678-90\n"
        "신용카드 4111-1111-1111-1111\n"
        "계좌번호 국민은행 123456-04-123456\n"
        "사업자등록번호 124-81-00998\n"
        "법인등록번호 130111-0006246\n"
        "건강보험증번호 1-2345678901\n"
        "처방전번호 202401150001\n"
        "의약품 EDI코드 640500051\n"
        "사건번호 2023가단12345\n"
        "부동산 고유번호 1111010100100010001"
    )
    st.caption("기본 샘플은 ko-pii 18종 PII(주민·외국인·여권·면허·전화·이메일·주소·카드·계좌·"
               "사업자·법인·건강보험·처방전·의약품EDI·차량·사건번호·부동산·국적)를 모두 포함합니다.")
    sample = st.text_area("샘플 텍스트", value=_pii18_sample, height=320)
    if st.button("미리보기 실행"):
        preview_doc = rulesedit.load_doc(path)      # 원본 사본에 편집 반영(디스크 미변경)
        rulesedit.apply_all(preview_doc, bulk, krows, prows, rrows)
        perrs, pwarn = rulesedit.validate_regex_rules(preview_doc)
        if perrs:
            st.error("PII 라벨 오류 — 고친 뒤 다시: " + "; ".join(f"{i}: {m}" for i, m in perrs))
            return
        if pwarn:
            st.warning("경고: " + "; ".join(f"{i}: {m}" for i, m in pwarn))
        try:
            sig = rulesedit.preview_scan(preview_doc, sample, cfg.get("src_path"))
            st.success(f"등급: {sig.grade or '보류'} · 신뢰도 {sig.confidence:.2f}")
            for h in sig.hits:
                ts = h.terms
                detail = ", ".join(f"{t}×{c}" for t, c in ts) if ts else f"{h.count}건"
                st.write(f"- [{h.layer}] {h.name} → {h.grade} · {detail}")
            if not sig.hits:
                st.caption("걸린 규칙 없음")
        except Exception as e:
            st.error(f"미리보기 실패: {e}")

    st.divider()
    if st.button("💾 저장 (버전 자동 상향 + .bak 백업)", type="primary"):
        rulesedit.apply_all(doc, bulk, krows, prows, rrows)
        errs, warns = rulesedit.validate_regex_rules(doc)
        if errs:
            st.error("저장 취소 — PII 라벨 오류: " + "; ".join(f"{i}: {m}" for i, m in errs))
            return
        if warns:
            st.warning("경고(저장은 진행됨): " + "; ".join(f"{i}: {m}" for i, m in warns))
        doc["version"] = rulesedit.bump_version(doc.get("version"))
        try:
            rulesedit.save_doc(path, doc)
            st.success(f"저장됨 · 새 버전 `{doc['version']}` (백업: {os.path.basename(path)}.bak)")
            st.rerun()
        except Exception as e:
            st.error(f"저장 실패: {e}")


#------------------------------------------------------------------
# 메인
#=> 사이드바(데이터 경로·검토자·임계값)를 받고, 탭으로 각 화면을 구성한다.
#
# -in: 없음
# -out: 없음(Streamlit 앱)
# -out: error = 데이터 로드 실패 시 화면에 안내
#------------------------------------------------------------------
def main():
    st.set_page_config(page_title="문서 C/S/O 분류 검토", page_icon="🗂️", layout="wide")
    st.title("🗂️ 문서 C/S/O 분류 검토")
    st.caption("문서중앙화로 수집된 문서의 자동 분류 결과를 검토·확정하는 도구")

    with st.sidebar:
        st.header("데이터")
        # 결과 경로는 ui 폴더 기준 '절대경로'로 고정한다. 상대경로면 streamlit 실행
        # 작업폴더(cwd)에 따라 exe 가 엉뚱한 곳에 --out 을 쓰고 UI 가 못 찾는다.
        _here = os.path.dirname(os.path.abspath(__file__))
        _cands = [os.path.join(_here, "cso_result.jsonl"),
                  os.path.join(_here, "sample_cso_result.jsonl")]
        _default = next((p for p in _cands if os.path.isfile(p)),
                        os.path.join(_here, "cso_result.jsonl"))
        grades_path = st.text_input("분류 결과 (cso_result.jsonl)", value=_default)
        default_ov = (os.path.join(os.path.dirname(grades_path), "cso_override.jsonl")
                      if grades_path else "cso_override.jsonl")
        ov_path = st.text_input("오버라이드 파일 (cso_override.jsonl)", value=default_ov)
        reviewer = st.text_input("검토자 이름", placeholder="감사 기록에 남습니다")
        low_conf = st.slider("저신뢰 기준(검토 큐)", 0.0, 1.0, 0.6, 0.05)
        st.caption("csoclassify 분류(기본) / --propagate 가 만든 cso_result.jsonl 을 지정하세요.")

        st.divider()
        st.header("seed 저장소")
        _dir = os.path.dirname(grades_path) if grades_path else ""
        seed_path = st.text_input("seed 저장소 (cso_seed.jsonl)",
                                  value=os.path.join(_dir, "cso_seed.jsonl") if _dir else "cso_seed.jsonl")
        seed_audit_path = st.text_input("seed 감사 (cso_seed_audit.jsonl)",
                                        value=os.path.join(_dir, "cso_seed_audit.jsonl") if _dir else "cso_seed_audit.jsonl")
        # 실행 명령 기본값 = 배포 exe(dist-pkg\csoclassify.exe). 아직 없으면 소스 모듈로 폴백.
        _exe = os.path.abspath(os.path.join(_here, "..", "dist-pkg", "csoclassify.exe"))
        _src = os.path.join(_here, "..", "src")
        if os.path.isfile(_exe):
            # 경로에 공백이 있으면 따옴표로 감싼다(parse_base_cmd 가 따옴표를 벗겨 처리).
            _default_cmd = f'"{_exe}"' if " " in _exe else _exe
            _default_pp = ""
        else:
            _default_cmd, _default_pp = f"{sys.executable} -m csoclassify", os.path.abspath(_src)
        csoclassify_cmd = st.text_input("csoclassify 실행 명령(업로드 추가용)", value=_default_cmd)
        csoclassify_pp = st.text_input("PYTHONPATH(모듈 실행 시)", value=_default_pp,
                                    help="'python -m csoclassify' 형태일 때만 필요(exe면 비워둠)")

    # 하위 렌더 함수들이 참조할 seed 설정을 세션에 보관.
    st.session_state["seedcfg"] = {
        "seed_path": seed_path, "audit_path": seed_audit_path,
        "csoclassify_cmd": csoclassify_cmd, "pythonpath": csoclassify_pp or None,
        "reviewer": reviewer,
    }

    with st.sidebar:
        st.divider()
        st.header("규칙")
        _rules_default = os.path.abspath(os.path.join(_here, "..", "resources", "policy", "cso_rules.yaml"))
        rules_path = st.text_input("규칙셋 (cso_rules.yaml)", value=_rules_default)
    st.session_state["rulescfg"] = {
        "rules_path": rules_path,
        "src_path": os.path.abspath(os.path.join(_here, "..", "src")),
    }

    if not grades_path:
        st.warning("사이드바에서 결과 경로(cso_result.jsonl)를 지정하세요.")
        st.stop()

    # 아직 분류 결과가 없으면, 폴더 분류(생성)와 규칙 설정만 먼저 할 수 있게 한다.
    if not os.path.isfile(grades_path):
        st.info("아직 분류 결과가 없습니다. 먼저 규칙을 설정하고, 폴더를 분류해 결과를 생성하세요.")
        t0 = st.tabs(["⚙️ 규칙 설정", "▶ 실행"])
        with t0[0]:
            render_rules_editor()
        with t0[1]:
            render_run_panel(grades_path)
        st.stop()

    try:
        records = load_records(grades_path, os.path.getmtime(grades_path))
    except Exception as e:
        st.error(f"분류 결과를 읽지 못했습니다: {e}")
        st.stop()

    latest, history = load_overrides(ov_path)
    df = records_to_df(records, latest)
    run_meta = load_run_meta(grades_path)

    tabs = st.tabs(["⚙️ 규칙 설정", "▶ 실행", "📊 요약 대시보드", "🩺 리포트",
                    "📋 문서 목록 · 근거 · 수정", "⚠️ 검토 큐", "🌱 seed 관리", "⬇️ 내보내기"])
    with tabs[0]:
        render_rules_editor()
    with tabs[1]:
        render_run_panel(grades_path)
    with tabs[2]:
        render_dashboard(df, low_conf, run_meta)
    with tabs[3]:
        render_report(df, records, low_conf)
    with tabs[4]:
        render_list(df, records, latest, history, ov_path, reviewer)
    with tabs[5]:
        render_review_queue(df, records, latest, history, ov_path, reviewer, low_conf)
    with tabs[6]:
        render_seed_manager(records, latest)
    with tabs[7]:
        render_export(df)


# Streamlit 은 이 파일을 __main__ 으로 실행한다. 가드를 두어 테스트 시 import 로
# 함수만 가져다 쓸 수 있게 한다(그때는 main 이 안 돈다).
if __name__ == "__main__":
    main()
