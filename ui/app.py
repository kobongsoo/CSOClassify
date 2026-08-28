#------------------------------------------------------------------
# 문서자동분류 (Streamlit) — 보안등급 · 업무분류 2축 화면
#=> csoclassify 가 만든 분류 결과(cso_result.jsonl)를 담당자가 검토·확정하는 화면.
#   한 문서가 동시에 갖는 두 가지를 '한 화면에서' 다룬다.
#     · 보안등급(security) — 얼마나 조심할 문서인가. C 기밀/S 민감/O 공개, 문서당 1개
#     · 업무분류(doctype)  — 무엇에 관한 문서인가. 회사 분류 체계 트리, 문서당 여러 개
#
#   [화면 — plan/보안등급-업무분류-UIUX-설계.html 5장]
#     상단 고정: 관점 스위치(보안등급 / 업무분류 / 전체) + 검토자 이름
#     ① 현황   — 두 축 요약 카드
#     ② 문서함 — 한 행에 두 축을 같이 둔 표 + 선택 시 상세(좌 보안 / 우 업무분류)
#     ③ 검토함 — 관점에 따라 대상과 조작이 바뀌는 '오늘 할 일'
#     ④ 설정   — 폴더·회사 분류 체계·판단 기준·분류 실행(+고급: 경로/기준 문서/리포트)
#
#   [원칙]
#     · 관점 스위치는 '보기 설정'일 뿐이다 — 데이터·판정·저장 위치를 바꾸지 않는다.
#     · 보안등급은 하나만 고르므로 라디오, 업무분류는 여럿이 맞으므로 체크박스로 받는다.
#     · 원본 자동분류(cso_result.jsonl)는 절대 수정하지 않고, 사람의 결정은
#       cso_override.jsonl 에 append-only 로 쌓아 감사 추적을 남긴다.
#     · 화면에 보이는 모든 말은 uiwords.py 가 정한다(개발 용어를 그대로 쓰지 않는다).
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
import doctype_review
import docruleedit
import taxonomy as taxlib
import uisettings
import uierrlog
import uireset
import uiwords as W

# 메뉴 4개(설계서 5장). 문자열 자체가 화면에 그대로 나가고 session_state 의 값도 된다.
MENU_HOME = "현황"
MENU_BOX = "문서함"
MENU_INBOX = "검토함"
MENU_SET = "설정"

# 화면 이동 예약을 담아 두는 세션 키. 위젯 key("menu")와 '반드시 달라야' 한다 —
# 위젯 key 에 직접 값을 넣는 것이 바로 아래에서 설명하는 금지 동작이다.
_NAV_MENU = "_pending_menu"


#------------------------------------------------------------------
# 다른 화면으로 이동 예약
#=> "이 버튼을 누르면 검토함으로 가라" 같은 화면 이동을 처리한다.
#   [왜 st.session_state["menu"] 에 바로 못 넣나]
#   메뉴는 위젯(segmented_control)이 key="menu" 로 값을 들고 있다.
#   streamlit 은 **위젯이 만들어진 뒤에** 그 key 에 값을 넣는 것을 금지한다
#   (StreamlitAPIException: cannot be modified after the widget ... is instantiated).
#   그런데 화면 이동 버튼은 전부 위젯이 만들어진 다음에 그려지므로, 거기서
#   바로 넣으면 반드시 이 예외가 난다.
#   그래서 여기서는 '예약'만 별도 키에 적어 두고 rerun 하고, 다음 실행의 맨 앞
#   (위젯을 만들기 전)에서 apply_pending_nav() 가 실제 값으로 옮긴다.
#
# -in: menu = 옮겨 갈 메뉴(None 이면 메뉴는 그대로)
#
# -out: 없음(예약만 하고 st.rerun() 으로 화면을 다시 그린다 — 이 함수 뒤 코드는
#        실행되지 않는다)
# -out: error = 없음
#------------------------------------------------------------------
def goto(menu=None):
    if menu is not None:
        st.session_state[_NAV_MENU] = menu
    st.rerun()


#------------------------------------------------------------------
# 예약된 화면 이동을 실제 값으로 옮기기
#=> main() 이 위젯을 만들기 '직전'에 딱 한 번 부른다. 이 시점에는 아직 위젯이
#   없으므로 위젯 key 에 값을 넣어도 막히지 않는다.
#   예약은 한 번 쓰고 지운다(pop) — 남겨 두면 사용자가 손으로 메뉴를 바꿔도
#   매번 예약된 화면으로 되돌아가 버린다.
#
# -in: 없음
#
# -out: 없음(session_state 갱신)
# -out: error = 없음
#------------------------------------------------------------------
def apply_pending_nav():
    m = st.session_state.pop(_NAV_MENU, None)
    if m is not None:
        st.session_state["menu"] = m

# 등급 표기 규약은 uiwords.py 한 곳에서 정한다(설계서 4장). 아래는 옛 이름을
# 그대로 쓰는 코드가 많아 남겨 둔 별칭이다 — 값의 출처는 uiwords 하나뿐이다.
GRADE_LABEL = W.GRADE_LABEL
GRADE_ORDER = W.GRADE_ORDER
GRADE_CHOICES = W.GRADE_CHOICES

# 저확신 기준(검토함 편입선). 예전엔 사이드바 슬라이더였으나, 실무자가 매번 정할
# 값이 아니라서 화면에서 내리고 상수로 고정했다(설계서 원칙 5).
LOW_CONF = 0.6

# '비슷한 문서 참고'가 등급을 옮길지 정하는 문턱값. 엔진의 판정 기준을 화면이
# 그대로 되뇌는 값이라, 엔진이 바뀌면 여기도 같이 고쳐야 한다
# (원본: classify/propagate.py 의 MIN_SIM · DUP_THRESHOLD).
EMBED_MIN_SIM = 0.55       # 가장 비슷한 문서가 이보다 멀면 등급을 옮기지 않는다
EMBED_DUP_SIM = 0.95       # 이보다 가까우면 사본으로 보고 그 등급을 그대로 상속

# 위와 헷갈리기 쉬운 '다른' 문턱값. 이건 등급을 옮기는 기준이 아니라, 기준 문서를
# 등록할 때 "이미 있는 것과 사실상 같은 문서인가"를 경고하는 기준이다. 값이 더
# 엄격한 이유 — 잘못 옮긴 등급은 사람이 화면에서 바로잡을 수 있지만, 사본이
# 기준 문서로 쌓이면 그 문서가 표를 두 번 던져 조용히 판정을 기울인다.
# (seedstore.bulk_add_confident 의 dup_thresh 기본값과 같은 값을 쓴다.)
SEED_DUP_SIM = 0.985

# 일괄 등록에서 "올릴 것이 없어 뺀다"를 나타내는 표기. 화면 표와 실제 등록이 같은
# 값을 보고 판단해야 해서 상수로 둔다(문구를 고치면 두 곳이 함께 바뀐다).
BULK_SKIP = "빠짐 — 정한 것이 없음"

# 왜 옮겼는지/안 옮겼는지 한 줄 설명(엔진이 남긴 method 값 그대로 대응).
EMBED_WHY = {
    "dup_inherit": "거의 같은 문서가 있어 그 등급을 그대로 가져왔습니다",
    "knn_vote": "비슷한 문서들의 등급이 한쪽으로 모여 그 등급을 옮겼습니다",
    "too_far": f"가장 비슷한 문서가 {int(EMBED_MIN_SIM * 100)}% 에 못 미쳐 "
               "등급을 옮기지 않았습니다",
    "mixed": "비슷한 문서들의 등급이 갈려 등급을 옮기지 않았습니다",
    "no_seeds": "비교할 기준 문서가 없어 등급을 옮기지 않았습니다",
}


#------------------------------------------------------------------
# 등급 값 정규화(None → "보류")
#=> 레코드의 grade 는 None 일 수 있는데, 표/집계에서 다루기 쉽게 문자열 "보류"로 바꾼다.
#   화면에는 "판단 못 함"으로 나간다(uiwords.GRADE_LABEL).
#
# -in: g = 등급 값("C"/"S"/"O"/None)
#
# -out: str = "C"/"S"/"O"/"보류"
# -out: error = 없음
#------------------------------------------------------------------
def norm_grade(g):
    return W.norm_grade(g)


#------------------------------------------------------------------
# 분류 결과(grades.jsonl) 로드
#=> jsonl 을 읽어 '문서 레코드'만 리스트로 돌려준다. 파일 수정시각(mtime)을
#   캐시 키에 넣어, 파일이 바뀌면 자동으로 다시 읽는다.
#   [문서가 아닌 줄을 걸러내는 이유] csoclassify 는 결과 파일 맨 끝에 실행 요약
#   ({"summary": {...}})을 한 줄 덧붙인다. 이 줄에는 file 필드가 없어서, 그대로
#   문서 목록에 넣으면 문서함에 **이름도 폴더도 빈 유령 행**이 하나 생기고
#   전체 건수도 1건 부풀려진다(등급이 없으니 '보류·등급 확인'으로도 잡힌다).
#   파일 없이 만들어진 줄은 문서가 아니므로 여기서 한 번에 걸러 낸다.
#
# -in: path  = grades.jsonl 경로
# -in: mtime = 파일 수정시각(캐시 무효화용, 값 자체는 안 씀)
#
# -out: list = 문서 레코드 dict 리스트(요약 줄 등 문서가 아닌 줄은 제외)
# -out: error = 파일 없음/파싱 오류 시 예외 전파(호출측에서 처리)
#------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_records(path, mtime):
    recs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            # 문서 레코드의 조건: dict 이고 file 이 채워져 있을 것.
            # (요약 줄은 file 이 아예 없다.)
            if isinstance(rec, dict) and rec.get("file"):
                recs.append(rec)
    return recs


#------------------------------------------------------------------
# 보안등급 수정 기록 로드
#=> cso_override.jsonl 을 읽어, 파일별 "가장 최근 수정"과 전체 이력을 만든다.
#   같은 문서를 여러 번 고쳤으면 마지막 것이 유효하다(append-only, 나중 것 우선).
#
#   [중요] 같은 파일에 업무분류 결정(axis:"doctype")도 함께 쌓인다. 그 줄을 여기서
#   걸러내지 않으면 "업무분류를 확정했을 뿐인데 보안등급이 사람 손을 탄 것으로
#   보이고, new_grade 가 없어 등급이 판단 못 함으로 바뀌는" 사고가 난다.
#   그래서 security 줄(axis 가 "security" 이거나, axis 자체가 없는 옛 기록)만 읽는다.
#
# -in: path = cso_override.jsonl 경로(없으면 빈 결과)
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
            # axis 가 없는 줄은 두 축이 나뉘기 전에 쌓인 옛 기록 = security 다.
            if e.get("axis", "security") != "security":
                continue
            file = e.get("file")
            if not file:
                continue
            latest[file] = e                 # 나중 줄이 앞 줄을 덮음 → 최신 유효
            history[file].append(e)
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
#   axis:"security" 를 함께 찍어 doctype_review.append_doctype_override() 가
#   같은 파일에 쌓는 axis:"doctype" 줄과 섞이지 않게 한다(설계서 7-4-1 —
#   "축을 명시해 두 축의 수정이 섞이지 않게 한다"). load_overrides() 는 이
#   필드를 보지 않고 그대로 읽으므로 옛 파일(axis 없음)도 계속 잘 읽힌다.
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
def append_override(path, file, old_grade, new_grade, reason, reviewer,
                    kind="change"):
    entry = {
        "file": file,
        "axis": "security",
        # 값을 바꾼 것("change")과 자동 판정이 맞다고 확인만 한 것("confirm")을
        # 구분해 둔다. 둘 다 "사람이 판단을 끝냈다"는 뜻이라 검토함에서는 똑같이
        # 내려가지만, 나중에 규칙 정확도를 잴 때는 정반대의 뜻이라 섞이면 안 된다.
        "kind": kind,
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
# 사람이 '값을 바꾼' 문서인가
#=> effective_grade 의 두 번째 값은 "사람이 판단을 끝냈는가"(확인만 한 것도 포함)라
#   화면에 "관리자가 고쳤습니다"라고 쓰기에는 너무 넓다. 여기서는 자동 판정과
#   실제로 값이 달라진 문서만 골라낸다 — 표·집계·내보내기의 '고침' 열은 이 값이다.
#
# -in: rec    = 분류 레코드
# -in: latest = 파일별 최신 보안등급 결정 맵
#
# -out: bool = 자동 판정과 다른 등급으로 바뀌었으면 True(확인만 했으면 False)
# -out: error = 없음
#------------------------------------------------------------------
def grade_changed(rec, latest):
    ov = latest.get(rec.get("file"))
    if not ov:
        return False
    return norm_grade(ov.get("new_grade")) != norm_grade(rec.get("grade"))


#------------------------------------------------------------------
# 레코드 → 표 DataFrame (두 축을 한 줄에)
#=> 목록·집계에 쓰기 좋게 레코드들을 한 줄씩 평평한 표로 만든다. 설계서 7장의
#   핵심 요구("한 행에 보안등급과 업무분류가 동시에 보인다")를 데이터 층에서
#   만족시키는 자리다 — 화면이 두 표를 합치는 게 아니라, 표가 처음부터 하나다.
#    1) 보안등급: 자동 판정 위에 사람의 수정을 얹어 최종 등급을 낸다
#    2) 업무분류: 후보 목록에 확정/거절 결정을 얹어 표에 쓸 요약 문자열을 만든다
#    3) 두 축 각각 "사람이 더 볼 것이 남았는지"(need_sec/need_doc)를 계산한다
#
# -in: records   = 레코드 리스트
# -in: latest    = 파일별 최신 보안등급 수정 맵(load_overrides 의 latest)
# -in: latest_dt = 파일별 최신 업무분류 결정 맵(없으면 업무분류 열이 빈 값)
# -in: tax       = 회사 분류 체계('직접 추가'로 확정된 분류의 이름을 찾는 데 쓴다)
# -in: seed_files = 기준 문서로 등록된 경로 집합(seedstore.load_seed_files 결과).
#                   기준 문서는 관리자가 "이 판정을 잣대로 삼겠다"고 정한 문서라
#                   더 볼 것이 없다 — 검토 대상에서 뺀다
#
# -out: DataFrame = file/folder/name/auto/final/overridden/confidence/…
#                   + doctype(표시 문자열)/dt_n/dt_reviewed/need_sec/need_doc/todo
# -out: error = 없음
#------------------------------------------------------------------
def records_to_df(records, latest, latest_dt=None, tax=None, seed_files=None):
    latest_dt = latest_dt or {}
    seed_files = seed_files or set()
    rows = []
    for r in records:
        # decided = "사람이 판단을 끝냈다"(고침 + 확인). 검토함에서 내리는 기준.
        final, decided = effective_grade(r, latest)
        conf = round(float(r.get("confidence", 0.0)), 3)
        cands, reviewed = doctype_review.effective_doctype(r, latest_dt, tax)
        # 업무분류에서 사람이 손대야 하는 것은 "아직 분류가 하나도 안 붙은 문서"다.
        # 기계가 후보를 낸 문서는 이미 분류된 것으로 본다.
        #   [축이 꺼진 배포와 구분] labels 에 "doctype" 키가 아예 없으면 이번 배포는
        #   그 축을 안 쓴 것이라, 셀 것도 검토할 것도 없다. 키는 있는데 values 가
        #   비어 있을 때만 '미분류'다 — 이 구분이 없으면 보안등급만 쓰는 배포에서
        #   모든 문서가 "업무분류 확인 필요"로 잡힌다.
        dt_axis_on = "doctype" in (r.get("labels") or {})
        # 사람이 한 번 보고 "해당 없음"으로 정리한 문서는 다시 부르지 않는다.
        need_doc = dt_axis_on and (not cands) and (not reviewed)
        # 보안등급은 판단 못 했거나 확신이 낮으면 사람이 본다. 이미 사람이 고친
        # 문서는 다시 부르지 않는다 — 검토를 끝낸 문서가 큐에 계속 남으면 안 된다.
        need_sec = (not decided) and (final == "보류" or conf < LOW_CONF)
        # 기준 문서로 올린 문서는 검토 대상에서 뺀다 — 관리자가 "이 문서를 잣대로
        # 삼겠다"고 직접 정한 문서를 다시 "확인이 필요합니다"라고 불러 세우면
        # 큐가 영영 줄지 않는다. (등급이 아직 '보류'인 채 업무분류만 등록한 문서도
        # 마찬가지로 뺀다. 그 사실은 문서함의 상태 열에 그대로 남는다.)
        is_seed = seedstore.norm_file(r.get("file", "")) in seed_files
        if is_seed:
            need_sec = need_doc = False
        rows.append({
            "is_seed": is_seed,
            "file": r.get("file", ""),
            "folder": os.path.dirname(r.get("file", "")),
            "name": os.path.basename(r.get("file", "")),
            "auto": norm_grade(r.get("grade")),
            "final": final,
            # 표·내보내기의 '고침'은 값이 실제로 달라진 문서만이다(확인만 한 것 제외).
            "overridden": grade_changed(r, latest),
            "confidence": conf,
            "method": r.get("method", ""),
            "decided_by": ", ".join(r.get("decided_by", []) or []),
            "rule_version": r.get("rule_version", ""),
            "doctype": W.doctype_cell(cands),
            "dt_n": len(cands),
            "dt_reviewed": reviewed,
            "dt_roots": " ".join(sorted({(c.get("path") or "").split(" > ")[0]
                                         for c in cands
                                         if c.get("status") != "rejected"})),
            "need_sec": need_sec,
            "need_doc": need_doc,
            "todo": W.todo_label(need_sec, need_doc),
        })
    return pd.DataFrame(rows)


#------------------------------------------------------------------
# 근거의 '무엇이 몇 번' 한 칸을 사람 글자로
#=> 엔진이 남기는 terms 한 칸의 모양이 자리마다 다르다. 화면이 한 가지만 알고
#   있으면 다른 모양을 만나는 순간 화면 전체가 죽는다 — 실제로 Rust 엔진의
#   '복합 개인정보(COMBO)' 규칙이 ["ADDRESS", 1] 짝 배열을 써서 화면이 멈췄다.
#   엔진은 dict 로 통일해 두었지만, 이미 저장된 결과 파일에는 옛 모양이 남아
#   있으므로 화면은 세 가지를 모두 읽는다.
#    1) {"term": "경조", "count": 20}  → "경조 20회"   (표준)
#    2) ["ADDRESS", 1]                 → "ADDRESS 1회" (옛 Rust COMBO)
#    3) "계약서"                        → "계약서"      (건수 없는 목록)
#
# -in: t = terms 목록의 한 칸
#
# -out: str = 화면에 그대로 쓸 한 조각
# -out: error = 없음(모르는 모양이면 그대로 글자로 만든다)
#------------------------------------------------------------------
def term_count_text(t):
    if isinstance(t, dict):
        term, cnt = t.get("term", ""), t.get("count")
    elif isinstance(t, (list, tuple)):
        term = t[0] if len(t) > 0 else ""
        cnt = t[1] if len(t) > 1 else None
    else:
        term, cnt = t, None
    return f"{term} {cnt}회" if cnt is not None else f"{term}"


#------------------------------------------------------------------
# 보안등급 근거 렌더 ("왜 이렇게 판단했나")
#=> 선택한 문서가 왜 이 등급인지를 신호별로 한 줄씩 풀어 보여준다.
#   [설계서 원칙 4] 근거는 접지 않는다. 예전에는 신호마다 expander 로 접어 뒀는데,
#   접혀 있으면 아무도 열지 않아 "결과만 보고 판단"하게 된다. 사람은 근거를 보고
#   판단해야 하므로 처음부터 펼쳐 놓는다.
#    1) 최종/자동 등급과 확신도를 위에 요약
#    2) 내용 검사·폴더 규칙·파일 이름·비슷한 문서 참고 순으로 근거를 나열
#    3) 채택되지 않은 후보(candidates)를 "다른 의견"으로 항상 보여준다 —
#       남기기만 하고 안 보여주면 남긴 이유가 없다(상위 설계 7-1)
#
# -in: rec    = 분류 레코드
# -in: latest = 보안등급 수정 맵(최종 등급 표시용)
#
# -out: 없음(Streamlit 위젯 출력)
# -out: error = 없음
#------------------------------------------------------------------
def render_evidence(rec, latest):
    final, decided = effective_grade(rec, latest)
    sig = rec.get("signals", {}) or {}

    # 두 축은 화면에서 대등해야 한다 — 오른쪽 업무분류 칸과 똑같은 제목 크기·모양을
    # 쓴다. 예전에는 이쪽만 metric 의 작은 회색 라벨이라 한 축이 곁다리로 보였다.
    st.markdown(f"##### {W.AXIS_SEC} <span style='font-weight:400;color:gray'>— "
                f"{W.AXIS_SEC_SUB}</span>", unsafe_allow_html=True)
    st.caption("문서당 **하나만** · 자동 판정이 맞으면 아래에서 [이대로 확정] 하세요")

    c1, c2 = st.columns(2)
    c1.metric("지금 등급", GRADE_LABEL[final], help="관리자가 고쳤으면 그 값이 최종입니다")
    c2.metric("확신", W.pct(rec.get("confidence", 0)) or "-")
    if decided and grade_changed(rec, latest):
        st.caption(f"관리자가 고친 등급입니다 · 자동 판정은 "
                   f"{GRADE_LABEL[norm_grade(rec.get('grade'))]} 이었습니다")
    elif decided:
        # 확인만 한 것도 사람이 남긴 판단이다. 화면에 안 쓰면 "왜 검토함에서
        # 사라졌지?" 를 아무도 답할 수 없다.
        st.caption("관리자가 **이대로 맞다고 확정**한 등급입니다 · 자동 판정 그대로")

    st.markdown("**왜 이렇게 판단했나**")
    lines = []

    # ── 내용 검사(개인정보·키워드) ──
    rule = sig.get("rule", {}) or {}
    for h in rule.get("hits", []) or []:
        terms = h.get("terms", [])
        # 키워드는 어떤 단어가 몇 번인지, 개인정보는 건수만(원문은 화면에 내지 않는다).
        if terms:
            detail = ", ".join(term_count_text(t) for t in terms)
        else:
            detail = f"{h.get('count', 0)}건 발견"
        lines.append(f"{W.GRADE_EMOJI.get(norm_grade(h.get('grade')), '·')} "
                     f"**{h.get('name', '')}** {detail} · _{W.SIGNAL_LABEL['rule']}_")
    if not (rule.get("hits") or []):
        lines.append(f"· 개인정보·키워드가 발견되지 않았습니다 · _{W.SIGNAL_LABEL['rule']}_")

    # ── 폴더 규칙 ──
    path = sig.get("path", {}) or {}
    if path.get("grade"):
        acl = " · 접근이 강하게 제한된 폴더" if path.get("acl_restricted") else ""
        lines.append(f"{W.GRADE_EMOJI.get(norm_grade(path.get('grade')), '·')} "
                     f"폴더 규칙 **{path.get('source')}** 에 해당{acl} · "
                     f"_{W.SIGNAL_LABEL['path']}_")
    else:
        lines.append(f"· 이 폴더에 대한 규칙이 없습니다 · _{W.SIGNAL_LABEL['path']}_")

    # ── 파일 이름 ──
    name = sig.get("name", {}) or {}
    for h in name.get("hits", []) or []:
        lines.append(f"{W.GRADE_EMOJI.get(norm_grade(h.get('grade')), '·')} "
                     f"파일 이름에 **{h.get('term', '')}** 이(가) 있습니다 · "
                     f"_{W.SIGNAL_LABEL['name']}_")

    # ── 비슷한 문서 참고(임베딩 전파) ──
    embed = sig.get("embed") or {}
    neigh = list((embed.get("neighbors") or [])) if embed else []
    if embed:
        top = embed.get("top_sim")
        if neigh:
            same = sum(1 for n in neigh if norm_grade(n.get("grade")) == final)
            lines.append(f"🧭 비슷한 문서 {len(neigh)}건 중 {same}건이 같은 등급입니다"
                         f"(가장 비슷한 문서 {W.pct(top)}) · _{W.SIGNAL_LABEL['embed']}_")
        else:
            lines.append(f"🧭 비슷한 문서를 찾지 못했습니다 · _{W.SIGNAL_LABEL['embed']}_")

    for ln in lines:
        st.markdown(f"- {ln}")

    # 비슷하다고 판단한 문서가 '무엇인지'를 이름까지 펼친다. 건수만 보여 주면
    # "둘 다 C 인데 왜 판단 못 함이지?" 를 풀 방법이 없다 — 사람이 근거를 보고
    # 판단해야 하므로 이름·닮은 정도·그 문서의 등급을 그대로 내놓는다.
    if neigh:
        for n in neigh[:5]:
            g = norm_grade(n.get("grade"))
            st.caption(f"　· {os.path.basename(n.get('file') or '')} · "
                       f"닮은 정도 {W.pct(n.get('sim'))} · "
                       f"{W.GRADE_EMOJI.get(g, '·')} {W.GRADE_PLAIN.get(g, g)}")
        if len(neigh) > 5:
            st.caption(f"　… 외 {len(neigh) - 5}건")
    # 왜 그 등급을 옮겼는지(또는 왜 안 옮겼는지)까지 적는다.
    why = EMBED_WHY.get(embed.get("method") or "")
    if why:
        st.caption(f"　→ {why}")

    # ── 다른 의견(채택되지 않은 후보) ──
    render_other_opinions(rec, final)


#------------------------------------------------------------------
# "다른 의견" 렌더 (채택되지 않은 보안등급 후보)
#=> 보안등급은 신호가 엇갈리면 더 엄격한 쪽 하나로 정해진다. 그 순간 나머지 근거가
#   화면에서 사라지면 검토자가 "왜 이 등급인가"를 되짚을 수 없다. 그래서 결과에
#   남아 있는 후보 목록(labels.security.candidates)을 사람 말로 풀어 항상 보여준다.
#   더 엄격한 후보가 있는데 채택되지 않았다면(=사람이 이미 등급을 낮춘 경우 등)
#   그 사실을 특히 눈에 띄게 알린다 — 덜 보호하는 실수가 더 비싸기 때문이다.
#
# -in: rec   = 분류 레코드(labels.security.candidates 가 없을 수 있다 — 옛 결과)
# -in: final = 지금 적용 중인 최종 등급("C"/"S"/"O"/"보류")
#
# -out: 없음(후보가 없거나 1개뿐이면 아무것도 그리지 않는다)
# -out: error = 없음
#------------------------------------------------------------------
def render_other_opinions(rec, final):
    sec = ((rec.get("labels") or {}).get("security") or {})
    cands = sec.get("candidates") or []
    # 지금 등급과 다른 값을 주장한 후보만 "다른 의견"이다.
    others = [c for c in cands if norm_grade(c.get("value")) != final]
    if not others:
        return

    # 서열이 위인 등급(더 엄격한 쪽)을 주장한 후보가 있으면 먼저 경고한다.
    rank = {"O": 0, "S": 1, "C": 2, "보류": -1}
    stricter = [c for c in others if rank.get(norm_grade(c.get("value")), -1) > rank.get(final, -1)]
    msg = " · ".join(
        f"{GRADE_LABEL[norm_grade(c.get('value'))]}({W.pct(c.get('confidence'))}, "
        f"{W.SIGNAL_LABEL.get(c.get('from'), c.get('from') or '규칙')})"
        for c in others)
    if stricter:
        st.warning(f"💬 **다른 의견도 있었습니다** — {msg}\n\n"
                   f"더 엄격한 등급을 주장한 근거가 있습니다. 등급을 올려야 하는지 확인해 주세요.")
    else:
        st.info(f"💬 **다른 의견도 있었습니다** — {msg}")


#------------------------------------------------------------------
# 오버라이드 패널 렌더
#=> 선택 문서의 보안등급을 담당자가 '확정'하거나 '수정'하고 저장한다. 이력도 보여준다.
#   버튼은 하나지만 하는 일은 둘이다 — 고른 등급이 지금 등급과 같으면 "자동 판정이
#   맞다"는 확인(kind="confirm", 이유 선택), 다르면 정정(kind="change", 이유 필수)이다.
#   둘 중 무엇이든 한 줄이 남아야 그 문서가 검토함에서 내려간다.
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

    st.markdown("**등급 정하기**")
    # 보안등급은 문서당 하나뿐이므로 '라디오'로 받는다 — 위젯 모양만으로 "하나만
    # 고르는 축"이라는 것이 전달된다(설계서 원칙 2).
    opts = GRADE_CHOICES + ["보류"]
    new_grade = st.radio("새 등급", opts,
                         index=opts.index(cur) if cur in opts else 0,
                         horizontal=True, key=f"ov_grade_{scope}_{file}",
                         format_func=lambda g: GRADE_LABEL[g], label_visibility="collapsed")

    # 검토함에 오는 문서 대부분은 "판정은 맞는데 확신만 낮은" 문서다. 예전에는
    # 고른 등급이 지금 등급과 같으면 저장을 거절해서("지금 등급과 같습니다"),
    # 판정이 맞다고 본 담당자는 그 문서를 끝낼 방법이 아예 없었다 — 아무리 눌러도
    # 검토함에서 내려가지 않았다. 그래서 버튼 하나가 둘을 다 받는다.
    #   · 값을 바꿨으면  → '고침'(change) · 기계를 뒤집는 것이라 이유 필수
    #   · 그대로면       → '확인'(confirm) · 원래 사람이 하기로 된 절차라 이유 선택
    # 업무분류 쪽 [이대로 확정]과 같은 뜻, 같은 자리다.
    changed = (new_grade != cur)

    reason = st.text_input(
        "바꾸는 이유를 적어 주세요 (기록에 남습니다)" if changed
        else "확정 이유 (선택)",
        key=f"ov_reason_{scope}_{file}",
        placeholder="예: 계약서지만 대외 공개용이라 O 공개로 낮춤" if changed
        else "예: 내용 확인함 — 사번·연락처가 실제로 들어 있음")

    btn = (f"{GRADE_LABEL[cur]} → {GRADE_LABEL[new_grade]} 로 바꾸기" if changed
           else f"{GRADE_LABEL[cur]} 이대로 확정")
    if st.button(btn, key=f"ov_save_{scope}_{file}", type="primary", width="stretch"):
        if not reviewer.strip():
            st.error("화면 오른쪽 위에 '검토자 이름'을 먼저 입력하세요(기록에 남깁니다).")
        elif changed and not reason.strip():
            st.error("바꾸는 이유를 적어 주세요.")
        elif new_grade == "보류":
            # '판단 못 함'을 확정으로 받아 주면 등급 없는 문서가 검토함에서
            # 사라진다 — 그 문서야말로 사람이 봐야 하는 문서다.
            st.error("‘판단 못 함’으로는 확정할 수 없습니다. C·S·O 중에서 골라 주세요.")
        else:
            append_override(ov_path, file, cur, new_grade, reason.strip(),
                            reviewer.strip(), kind="change" if changed else "confirm")
            st.success(f"바뀌었습니다: {GRADE_LABEL[cur]} → {GRADE_LABEL[new_grade]}"
                       if changed else f"확정됨: {GRADE_LABEL[new_grade]}")
            st.rerun()
    st.caption("✔ 자동 판정이 맞으면 등급을 그대로 두고 눌러 확정하세요 — "
               "확정해야 검토함에서 내려갑니다. 원래 값은 지워지지 않고 남습니다.")

    hist = history.get(file, [])
    if hist:
        with st.expander(f"결정 기록 {len(hist)}건"):
            for e in reversed(hist):
                og = GRADE_LABEL[norm_grade(e.get("old_grade"))]
                ng = GRADE_LABEL[norm_grade(e.get("new_grade"))]
                # 옛 기록에는 kind 가 없다. 그때는 값이 바뀔 때만 저장됐으므로
                # 값으로 되짚어 판단한다.
                kind = e.get("kind") or ("confirm" if og == ng else "change")
                what = f"{ng} 이대로 확정" if kind == "confirm" else f"{og} → {ng}"
                st.caption(f"- {e.get('ts','')} · {e.get('reviewer','')} · "
                           f"{what} · {e.get('reason','')}")


#------------------------------------------------------------------
# ① 현황 화면 렌더 (설계서 6장)
#=> "지금 무엇을 해야 하는지"를 정하는 화면. 읽는 화면이 아니라 다음 행동을 고르는
#   화면이므로 스크롤 없이 끝나야 한다.
#    1) 숫자 5개(전체 · 둘다 확인 필요 · 보안등급만 · 업무분류만 · 마지막 실행)
#       세 '확인 필요' 칸은 서로 겹치지 않는다 — 더하면 '할 일 있는 문서 수'가 된다
#    2) 두 축 카드를 좌우로 나란히(보안등급 왼쪽 고정)
#
# -in: df         = 문서 표(records_to_df 결과)
# -in: doctype_on = 업무분류 축을 쓰는 배포인가(회사 분류 체계가 연결됐는가)
# -in: run_meta   = 마지막 분류 실행 메타(없으면 None)
#
# -out: 없음(Streamlit 출력)
# -out: error = 없음
#------------------------------------------------------------------
def render_home(df, doctype_on, run_meta=None):
    total = len(df)
    counts = Counter(df["final"])
    # 세 칸이 서로 겹치지 않게 나눈다 — 같은 문서가 두 칸에 동시에 잡히면 합계가
    # 전체보다 커져 "몇 건을 처리하면 끝나는가"를 셀 수 없다.
    both_n = int((df["need_sec"] & df["need_doc"]).sum())
    sec_only = int((df["need_sec"] & ~df["need_doc"]).sum())
    doc_only = int((~df["need_sec"] & df["need_doc"]).sum())

    m = st.columns(5)
    m[0].metric("전체 문서", f"{total}건")
    m[1].metric("둘 다 확인 필요", f"{both_n}건",
                help="보안등급과 업무분류 양쪽 모두 관리자가 봐야 하는 문서입니다. "
                     "아래 두 칸에는 중복해서 세지 않습니다.")
    m[2].metric("보안등급만 확인 필요", f"{sec_only}건",
                help="판단하지 못했거나 확신이 낮은 문서입니다(업무분류는 문제 없음).")
    if doctype_on:
        m[3].metric("업무분류만 확인 필요", f"{doc_only}건",
                    help="업무분류가 아직 하나도 붙지 않아 관리자가 직접 정해야 하는 "
                         "문서입니다(보안등급은 문제 없음).")
    else:
        m[3].metric("업무분류", "사용 안 함")
    # 마지막 실행은 '언제'가 곧 신뢰도라서 날짜에 시:분까지 붙여 보여준다.
    # 초와 소요시간까지는 필요할 때만 보면 되므로 도움말에 둔다.
    last = (run_meta or {}).get("finished_at")
    m[4].metric("마지막 분류 실행", last[:16] if last else "아직 없음",
                help=(f"{last} · {fmt_duration(run_meta['seconds'])} 소요"
                      if last and run_meta.get("seconds") is not None else "기록이 없습니다"))
    # 위 세 칸을 더한 값이 곧 '검토함에서 처리할 문서 수'라는 것을 한 줄로 알려 준다.
    todo_all = both_n + sec_only + doc_only
    if todo_all:
        st.caption(f"→ 관리자 확인이 필요한 문서는 모두 **{todo_all}건**입니다 "
                   f"({both_n} + {sec_only} + {doc_only}). **검토함**에서 처리하세요.")
    else:
        st.caption("→ 관리자 확인이 필요한 문서가 없습니다 👍")

    #--------------------------------------------------------------
    # 보안등급 카드 그리기(좌/우 어느 칸에 놓든 내용은 같아 함수로 뺐다).
    #--------------------------------------------------------------
    def _card_security():
        st.markdown(f"##### {W.AXIS_SEC} "
                    f"<span style='font-weight:400;color:gray'>— {W.AXIS_SEC_SUB}</span>",
                    unsafe_allow_html=True)
        st.caption("문서 1건에 등급 1개. 신호가 엇갈리면 **더 엄격한 쪽**으로 정합니다.")
        dist = pd.DataFrame({"등급": [GRADE_LABEL[g] for g in GRADE_ORDER],
                             "건수": [counts.get(g, 0) for g in GRADE_ORDER]}).set_index("등급")
        # 카드 가장자리에 막대가 닿으면 답답해 보인다. 양옆에 빈 칸을 둬서
        # 그래프를 가운데로 좁힌다(그래프 자체는 화면 폭을 따라 늘어난다).
        _pad_l, _mid, _pad_r = st.columns([1, 12, 1])
        with _mid:
            st.bar_chart(dist, horizontal=True, height=190)
        st.info(f"확인이 필요한 문서 **{both_n + sec_only}건** — 판단 못 함 "
                f"{counts.get('보류', 0)}건 + 확신 {int(LOW_CONF * 100)}% 미만")

    #--------------------------------------------------------------
    # 업무분류 카드 그리기. 분류마다 색을 나누지 않고 전부 같은 막대로 둔다 —
    # 색을 나누면 없는 서열이 생긴다(설계서 원칙 3).
    #--------------------------------------------------------------
    def _card_doctype():
        st.markdown(f"##### {W.AXIS_DOC} "
                    f"<span style='font-weight:400;color:gray'>— {W.AXIS_DOC_SUB}</span>",
                    unsafe_allow_html=True)
        st.caption("문서 1건에 분류 **여러 개** 가능. 문서자동분류도우미가 제안하고 "
                   "**관리자가 최종 확정**합니다.")
        # 대분류(뿌리)로 롤업해 5~6줄로 보여준다. 잎 22줄을 그대로 내면 안 읽힌다.
        roll = Counter()
        for roots in df["dt_roots"]:
            for r in (roots or "").split():
                if r:
                    roll[r] += 1
        none_n = int((df["dt_n"] == 0).sum())
        if roll:
            rdf = pd.DataFrame({"분류": list(roll.keys()),
                                "건수": list(roll.values())}).set_index("분류")
            _pad_l, _mid, _pad_r = st.columns([1, 12, 1])
            with _mid:
                st.bar_chart(rdf.sort_values("건수", ascending=False),
                             horizontal=True, height=190)
            # 위 지표(업무분류 확인 필요)는 '해당 없음'으로 정리한 문서를 뺀 수라,
            # 차트 밖 문서 수(none_n)와 다를 수 있다. 두 숫자가 어긋나 보이지
            # 않도록 그 차이를 여기서 밝힌다.
            done_n = none_n - (both_n + doc_only)
            st.caption(f"아직 분류 없음 {none_n}건"
                       + (f"(그중 {done_n}건은 ‘해당 없음’으로 정리됨)" if done_n > 0 else "")
                       + " · 합계가 전체 문서 수보다 큰 것은 "
                       "**정상**입니다 — 한 문서가 여러 분류에 해당할 수 있습니다.")
        else:
            st.caption("아직 업무분류가 붙은 문서가 없습니다.")

    c1, c2 = st.columns(2)
    # 보안등급을 항상 왼쪽에 둔다 — 덜 보호하는 실수가 더 비싸므로 화면에서도
    # 보안이 앞선다(상위 설계 2-1). 두 카드는 늘 함께 보인다.
    left, right = (_card_security, _card_doctype)
    with c1:
        with st.container(border=True):
            left()
    with c2:
        with st.container(border=True):
            right()

    ov_n = int(df["overridden"].sum())
    if ov_n:
        st.caption(f"관리자가 등급을 고친 문서: {ov_n}건")


#------------------------------------------------------------------
# ② 문서함 화면 렌더 (설계서 7장)
#=> 모든 문서를 한 표에서 찾는 화면. 이 표의 존재 이유는 "한 행에 두 축이 같이
#   있다"는 것이다 — 예전처럼 보안등급 목록과 업무분류 목록이 다른 탭에 있으면
#   "계약서인데 O 공개로 잡힌 문서" 같은 교차 질문에 답할 수 없다.
#    1) 필터 4개(검색 · 보안등급 · 업무분류 대분류 · 할 일 상태)를 항상 같이 노출
#    2) 관점에 따라 기본 정렬만 바꾼다(보이는 열은 그대로 — 설계서 5-1)
#    3) 행을 고르면 바로 아래에 상세를 펼친다(새 창으로 보내지 않는다)
#
# -in: df        = 문서 표
# -in: records   = 원본 레코드(선택 문서의 상세용)
# -in: latest    = 보안등급 수정 맵
# -in: history   = 보안등급 수정 이력
# -in: latest_dt = 업무분류 결정 맵
# -in: history_dt= 업무분류 결정 이력
# -in: ov_path   = cso_override.jsonl 경로
# -in: reviewer  = 검토자 이름
# -in: tax       = 회사 분류 체계(없으면 None)
#
# -out: 없음(Streamlit 출력)
# -out: error = 없음
#------------------------------------------------------------------
def render_docbox(df, records, latest, history, latest_dt, history_dt,
                  ov_path, reviewer, tax):
    rec_by_file = {r.get("file"): r for r in records}
    doctype_on = tax is not None

    # 보안등급 칸은 네 등급이 한 줄에 들어갈 만큼 넓게 잡는다 — 좁으면 칩이
    # 두 줄로 접혀 그 아래 표까지 밀린다.
    f1, f2, f3, f4 = st.columns([3, 4, 3, 2])
    # 라벨을 감추면 이 칸만 다른 필터보다 한 줄 위로 올라가 줄이 안 맞는다.
    # 다른 칸과 같은 자리에 라벨을 세워 네 칸의 위아래를 맞춘다.
    kw = f1.text_input("🔎 파일명 검색", key="box_kw",
                       placeholder="예: 계약서, 인사",
                       help="파일 이름과 폴더 경로를 함께 찾습니다")
    grades = f2.multiselect(W.AXIS_SEC, GRADE_ORDER, default=GRADE_ORDER,
                            format_func=lambda g: W.GRADE_PLAIN[g], key="box_grade")
    root_opts = taxlib.roots(tax) if doctype_on else []
    picked_roots = f3.multiselect(W.AXIS_DOC, root_opts, default=[], key="box_root",
                                  disabled=not root_opts,
                                  help="대분류로 좁힙니다(비워 두면 전체)")
    todo_opts = ["전체", "확인 필요만", "확정된 것만"]
    # 관점이 정해져 있으면 그 축의 '할 일'을 기본으로 보여준다 — 스위치가 바꾸는
    # 것은 기본 필터·정렬뿐이고, 열이나 데이터는 그대로다.
    todo = f4.selectbox("상태", todo_opts, index=0, key="box_todo")

    # 등급 칩을 모두 지운 것은 "아무것도 보지 않겠다"가 아니라 "등급으로는 안
    # 거르겠다"는 뜻으로 받는다 — 업무분류 칸이 비었을 때 전체가 나오는 것과 같은
    # 규칙이다. (예전에는 여기서 0건이 되어 화면이 통째로 비었다.)
    v = df[df["final"].isin(grades or GRADE_ORDER)]
    if kw.strip():
        v = v[v["file"].str.lower().str.contains(kw.strip().lower())]
    if picked_roots:
        # astype(bool) 을 빼면 안 된다. 걸러진 결과가 0건일 때 apply 가 object 형
        # 빈 Series 를 돌려주고, 그것으로 색인하면 pandas 가 '열까지' 지운 빈 표를
        # 만든다 → 뒤따르는 정렬이 KeyError('need_sec') 로 죽는다.
        mask = v["dt_roots"].apply(
            lambda s: any(r in (s or "").split() for r in picked_roots)).astype(bool)
        v = v[mask]
    if todo == "확인 필요만":
        v = v[v["need_sec"] | v["need_doc"]]
    elif todo == "확정된 것만":
        v = v[~(v["need_sec"] | v["need_doc"])]

    # 기본 정렬 — 할 일이 남은 문서를 위로. 두 축을 함께 보므로 어느 한쪽 기준으로
    # 줄 세우지 않는다(정렬은 표 머리를 눌러 바꿀 수 있다).
    if not v.empty:
        v = v.sort_values(["need_sec", "need_doc"], ascending=[False, False])

    top1, top2 = st.columns([4, 1])
    top1.caption(f"전체 {len(df)}건 중 **{len(v)}건**")
    # 내보내기는 탭 하나를 쓸 만큼 큰 기능이 아니다 — 지금 필터 결과 그대로 낸다.
    with top2:
        render_export_button(v)

    if v.empty:
        st.info("조건에 맞는 문서가 없습니다. 위 필터를 비우면 전체가 나옵니다.")
        return

    show = v[["is_seed", "name", "final", "confidence", "doctype", "todo", "file"]].copy()
    # confidence 는 0.0~1.0 으로 저장돼 있다. 여기서 100을 곱하지 않으면 0.9 가
    # "%.0f%%" 서식을 만나 "1%" 로 찍힌다 — 확신 90% 문서가 1% 로 보이던 원인.
    show["confidence"] = show["confidence"] * 100
    # 기준 문서로 올린 문서를 목록 맨 앞에서 바로 알아보게 한다. 예전에는 문서를
    # 고르고 접힌 칸을 펼쳐야만 등록 여부를 알 수 있어, 같은 문서를 또 올리기 쉬웠다.
    show["is_seed"] = show["is_seed"].map(lambda x: "🌱" if x else "")
    show = show.rename(columns={"is_seed": "seed"})
    event = st.dataframe(
        show, hide_index=True, width="stretch", height=380,
        # 여러 줄 선택 — 한 건이면 상세를, 여러 건이면 '한꺼번에 등록'을 보여준다.
        # 기준 문서를 하나씩 등록하는 것이 번거롭다는 요청에서 나온 설계다.
        on_select="rerun", selection_mode="multi-row",
        column_config={
            "seed": st.column_config.TextColumn(
                "기준", width=60,
                help="🌱 은 '기준 문서'로 등록된 문서입니다 — 다음 분류 때 잣대가 되고, "
                     "검토함에는 올라오지 않습니다"),
            "name": st.column_config.TextColumn("문서", width="large"),
            # 값이 한두 글자뿐이라 픽셀로 좁게 못박는다 — 남는 폭은 문서·업무분류에.
            "final": st.column_config.TextColumn(W.AXIS_SEC, width=90),
            "confidence": st.column_config.NumberColumn("확신", format="%.0f%%",
                                                        width=70),
            "doctype": st.column_config.TextColumn(W.AXIS_DOC, width="medium"),
            "todo": st.column_config.TextColumn("상태", width="small"),
            "file": st.column_config.TextColumn("폴더", width="medium"),
        },
    )
    st.caption("업무분류는 자동으로 찾은 것도 **확정으로 봅니다** — 틀린 것이 있으면 "
               "그 문서를 골라 고치세요.")

    rows = event.selection.rows if event and event.selection else []
    if not rows:
        st.info("표에서 문서를 **한 건** 고르면 자세한 내용과 결정 화면이, "
                "**여러 건**을 고르면 기준 문서로 한꺼번에 등록하는 화면이 나옵니다.")
        return

    # 여러 건을 골랐으면 상세를 그릴 수 없다(다섯 건의 상세를 동시에 펼칠 수는
    # 없다). 대신 그 문서들을 한 번에 기준 문서로 올리는 화면을 보여준다.
    if len(rows) > 1:
        st.divider()
        render_bulk_seed_promote([show.iloc[i]["file"] for i in rows],
                                 rec_by_file, latest, latest_dt, tax)
        return

    sel_file = show.iloc[rows[0]]["file"]
    rec = rec_by_file.get(sel_file)
    if rec:
        st.divider()
        render_detail(rec, latest, history, latest_dt, history_dt,
                      ov_path, reviewer, tax, scope="box")


#------------------------------------------------------------------
# 무엇을 등록할지 먼저 계산한다(화면 없는 순수 계산)
#=> "고른 문서들을 기준 문서로 올리면 무슨 일이 벌어지는가"를 표로 보여 주는 것과
#   실제로 올리는 것이 같은 계산을 쓰게 하려고 함수로 떼어냈다. 화면에 보여 준
#   것과 실제 등록이 어긋나는 사고를 구조적으로 막는다.
#    1) 보안등급은 사람이 고친 값(오버라이드)을 우선한다
#    2) 업무분류는 '확정된 것'만 올린다 — 자동 제안(?)은 기준이 될 수 없다
#    3) 이번에 확정 분류가 없어도 예전에 등록해 둔 분류는 지우지 않는다
#    4) 등급도 확정 분류도 없으면 올릴 것이 없으므로 이유와 함께 뺀다
#
# -in: files       = 고른 문서 경로 목록
# -in: rec_by_file = 파일 경로 → 분류 레코드
# -in: latest      = 보안등급 수정 맵
# -in: latest_dt   = 업무분류 결정 맵
# -in: seeds       = 지금 기준 문서 목록(새로 등록인지 갱신인지 판단에 쓴다)
# -in: tax         = 회사 분류 체계(직접 추가한 분류를 알아보는 데 쓴다)
#
# -out: list = [{file, rec, grade, dc_ids, how}] — how 는 "새로 등록"/"갱신"/
#        "빠짐 — 정한 것이 없음"
# -out: error = 없음
#------------------------------------------------------------------
def plan_bulk_seed(files, rec_by_file, latest, latest_dt, seeds, tax=None):
    plan = []
    for f in files:
        rec = rec_by_file.get(f) or {}
        grade, _ = effective_grade(rec, latest)
        cands, _ = doctype_review.effective_doctype(rec, latest_dt, tax)
        dc_ids = [c["dc_id"] for c in cands if c["status"] == "confirmed"]
        prev = seedstore.find_seed(seeds, f)
        has_grade = grade in ("C", "S", "O")
        # 이번에 확정 분류가 없더라도 예전에 등록해 둔 분류는 살려 둔다.
        if not dc_ids and prev:
            dc_ids = list(seedstore.axis_value(prev, "doctype") or [])
        if not has_grade and not dc_ids:
            how = BULK_SKIP
        else:
            how = "갱신" if prev else "새로 등록"
        plan.append({"file": f, "rec": rec, "grade": grade if has_grade else None,
                     "dc_ids": dc_ids, "how": how})
    return plan


#------------------------------------------------------------------
# 계산한 대로 실제로 등록한다(화면 없는 순수 적용)
#=> plan_bulk_seed 가 정한 것을 그대로 저장소에 얹는다. 임베딩만 바깥에서
#   주입받으므로(embed 함수), 테스트에서는 가짜 벡터로 전 과정을 검증할 수 있다.
#    1) 벡터가 없으면 embed 로 그 문서만 구한다 — 실패하면 그 문서만 건너뛴다
#    2) 지금까지 쌓인 것과 사실상 같은 사본이면 건너뛴다(force 면 그래도 등록)
#    3) 축마다 따로 얹고, 축마다 따로 감사 기록을 남긴다
#
# -in: plan        = plan_bulk_seed 결과
# -in: seeds       = 기존 기준 문서 목록(변형하지 않는다)
# -in: reviewer    = 승인자 이름
# -in: audit_path  = 감사 로그 경로(None 이면 기록하지 않는다)
# -in: force       = True 면 사본이어도 등록
# -in: embed       = 벡터 없는 문서를 임베딩하는 함수 f(path) -> list|None
# -in: dup_sim     = 사본으로 볼 유사도(기본 SEED_DUP_SIM)
# -in: on_progress = 진행 알림 콜백 f(끝난수, 전체, 경로)(없어도 된다)
#
# -out: (new_seeds, stats) = 갱신된 목록, {"new","update","dup","fail"}
# -out: error = 없음(개별 실패는 stats.fail 로 센다)
#------------------------------------------------------------------
def apply_bulk_seed(plan, seeds, reviewer, audit_path=None, force=False,
                    embed=None, dup_sim=SEED_DUP_SIM, on_progress=None):
    todo = [x for x in plan if x["how"] != BULK_SKIP]
    cur = list(seeds)
    stats = {"new": 0, "update": 0, "dup": 0, "fail": 0}

    def audit(action, file, axis, before, after, reason):
        if audit_path:
            seedstore.append_seed_audit(audit_path, action, file, axis=axis,
                                        before=before, after=after,
                                        reviewer=reviewer, reason=reason)

    for i, x in enumerate(todo):
        f = x["file"]
        if on_progress:
            on_progress(i, len(todo), f)
        v = x["rec"].get("vector")
        if v is None and embed:
            v = embed(f)                  # 실패 이유는 embed 쪽에서 화면에 표시한다
        if v is None:
            stats["fail"] += 1
            continue
        # 사본 검사는 '지금까지 쌓인 것'과 한다 — 이번에 함께 고른 문서끼리 사본이면
        # 뒤엣것이 걸러진다(사본이 쌓이면 그 문서가 전파에서 표를 두 번 던진다).
        sim, _near = seedstore.nearest_seed_sim(v, cur, exclude_file=f)
        if sim >= dup_sim and not force:
            stats["dup"] += 1
            continue
        doc_meta = seed_doc_meta(x["rec"])
        prev = seedstore.find_seed(cur, f)
        if x["grade"]:
            before = seedstore.axis_value(prev, "security") if prev else None
            cur = seedstore.set_axis(cur, f, "security", x["grade"], reviewer,
                                     source="phase4", vector=v, doc=doc_meta)
            audit("update" if before else "add", f, "security", before, x["grade"],
                  f"문서함 일괄 등록 sim={sim:.3f}")
        if x["dc_ids"]:
            before = list(seedstore.axis_value(prev, "doctype") or []) if prev else None
            cur = seedstore.set_axis(cur, f, "doctype", x["dc_ids"], reviewer,
                                     source="phase6", vector=v, doc=doc_meta)
            audit("update" if before else "add", f, "doctype", before, x["dc_ids"],
                  "문서함 일괄 등록")
        stats["update" if prev else "new"] += 1
    if on_progress:
        on_progress(len(todo), len(todo), "")
    return cur, stats


#------------------------------------------------------------------
# 고른 문서들을 한꺼번에 기준 문서로 등록 (문서함 여러 줄 선택)
#=> 기준 문서를 한 건씩 올리는 것이 번거롭다는 요청에서 나온 화면이다. 문서함에서
#   여러 줄을 고르면 그 문서들의 보안등급·확정 업무분류를 그대로 한 번에 올린다.
#   판단(plan_bulk_seed)과 적용(apply_bulk_seed)은 위 두 함수가 하고, 여기서는
#   보여 주고 눌러 주는 일만 한다.
#
# -in: files       = 고른 문서 경로 목록
# -in: rec_by_file = 파일 경로 → 분류 레코드
# -in: latest      = 보안등급 수정 맵
# -in: latest_dt   = 업무분류 결정 맵
# -in: tax         = 회사 분류 체계(분류 이름 표기용)
#
# -out: 없음(등록 시 저장 + 감사 기록 + rerun)
# -out: error = 없음(실패는 화면에 이유와 함께 표시)
#------------------------------------------------------------------
def render_bulk_seed_promote(files, rec_by_file, latest, latest_dt, tax=None):
    cfg = st.session_state.get("seedcfg", {})
    seeds = seedstore.load_seeds(cfg.get("seed_path"))
    plan = plan_bulk_seed(files, rec_by_file, latest, latest_dt, seeds, tax)

    st.markdown(f"### 🌱 고른 문서 {len(files)}건을 기준 문서로 등록")
    # 등록 결과는 st.rerun() 을 지나 살아남아야 한다. 화면을 다시 그려야 표의
    # 🌱 표시가 갱신되는데, rerun 하면 그 직전에 띄운 성공 문구가 함께 지워진다
    # — 그래서 결과를 session_state 에 맡겨 두고 다시 그린 화면에서 보여준다.
    flash = st.session_state.pop("bulkseed_flash", None)
    if flash:
        st.success(flash)
    st.caption("기준 문서는 다음 분류 때 '비슷한 문서 참고'의 잣대가 됩니다 — "
               "확실한 문서만 올리세요. 등급이나 업무분류가 아직 정해지지 않은 "
               "문서는 자동으로 빠집니다.")

    st.dataframe(
        pd.DataFrame([{"문서": os.path.basename(x["file"]),
                       W.AXIS_SEC: W.GRADE_PLAIN[x["grade"]] if x["grade"] else "—",
                       W.AXIS_DOC: (" · ".join(taxlib.path_of(tax, d) for d in x["dc_ids"])
                                    if x["dc_ids"] else "—"),
                       "처리": x["how"]} for x in plan]),
        hide_index=True, width="stretch")

    todo = [x for x in plan if x["how"] != BULK_SKIP]
    skipped = len(plan) - len(todo)
    if skipped:
        st.caption(f"↳ {skipped}건은 보안등급도 확정 업무분류도 없어 등록에서 빠집니다 "
                   "— 먼저 검토함에서 정해 주세요.")
    if not todo:
        st.info("등록할 수 있는 문서가 없습니다.")
        return
    n_novec = sum(1 for x in todo if not x["rec"].get("vector"))
    if n_novec:
        st.caption(f"↳ {n_novec}건은 벡터가 없어(빠른 분류로 생략) 등록할 때 그 문서만 "
                   "임베딩합니다 — 건수만큼 시간이 걸립니다.")

    force = st.checkbox("이미 있는 기준 문서와 거의 같아도 등록",
                        key="bulkseed_force",
                        help=f"닮은 정도 {W.pct(SEED_DUP_SIM)} 이상이면 사본으로 보고 "
                             "건너뜁니다. 사본이 쌓이면 그 문서가 표를 두 번 던집니다")

    if not st.button(f"🌱 {len(todo)}건 기준 문서로 등록", type="primary",
                     key="bulkseed_go"):
        return
    if not cfg.get("reviewer", "").strip():
        st.error("화면 오른쪽 위에 '검토자 이름'을 먼저 입력하세요.")
        return

    prog = st.progress(0.0, text=f"등록 중… 0/{len(todo)}")

    # 진행바 갱신 — 임베딩이 느릴 수 있어 지금 어느 문서인지까지 보여준다.
    def _on_prog(done, total, path):
        txt = f"등록 중… {done}/{total}"
        if path:
            txt += f" · {os.path.basename(path)}"
        prog.progress(done / total if total else 1.0, text=txt)

    cur, stats = apply_bulk_seed(
        plan, seeds, cfg["reviewer"].strip(), audit_path=cfg.get("audit_path"),
        force=force, embed=lambda f: _embed_file_ondemand(cfg, f),
        on_progress=_on_prog)
    prog.progress(1.0, text="등록 완료")

    parts = [f"새로 등록 {stats['new']}건", f"갱신 {stats['update']}건"]
    if stats["dup"]:
        parts.append(f"사본이라 건너뜀 {stats['dup']}건")
    if stats["fail"]:
        parts.append(f"벡터 실패 {stats['fail']}건")
    if stats["new"] or stats["update"]:
        try:
            seedstore.save_seeds(cfg["seed_path"], cur)
        except OSError as e:
            uierrlog.show_error(f"기준 문서 저장 실패: {e}", exc=e, where="기준 문서 저장")
            return
        st.session_state["bulkseed_flash"] = (
            " · ".join(parts) + f" · 저장 위치: {cfg['seed_path']}")
        st.rerun()
    else:
        st.info(" · ".join(parts) + " — 저장할 것이 없었습니다.")


#------------------------------------------------------------------
# 문서 상세 렌더 — 두 축을 좌우로 나란히 (설계서 8장)
#=> 이 설계에서 가장 중요한 화면. 한 문서의 보안등급과 업무분류를 '동시에' 보여
#   준다. 위아래로 두면 아래 카드는 스크롤해야 보여서 결국 두 번 보는 예전 문제로
#   돌아가므로, 반드시 가로로 나란히 둔다(좁은 화면에서는 Streamlit 이 알아서 쌓는다).
#   왼쪽은 라디오(하나만), 오른쪽은 체크박스(여러 개) — 위젯 모양이 축의 성격을
#   말해 준다(설계서 원칙 2).
#
# -in: rec        = 분류 레코드
# -in: latest     = 보안등급 수정 맵
# -in: history    = 보안등급 수정 이력
# -in: latest_dt  = 업무분류 결정 맵
# -in: history_dt = 업무분류 결정 이력
# -in: ov_path    = cso_override.jsonl 경로
# -in: reviewer   = 검토자 이름
# -in: tax        = 회사 분류 체계(None 이면 업무분류 칸을 '사용 안 함'으로)
# -in: scope      = 위젯 key 접두(문서함/검토함에서 같은 문서를 그릴 때 충돌 방지)
#
# -out: 없음(Streamlit 출력)
# -out: error = 없음
#------------------------------------------------------------------
def render_detail(rec, latest, history, latest_dt, history_dt,
                  ov_path, reviewer, tax, scope="box"):
    file = rec.get("file", "")
    h1, h2 = st.columns([5, 1])
    h1.markdown(f"#### 📄 {os.path.basename(file)}")
    h1.caption(file)
    # 원본 미리보기는 만들지 않는다 — 기본 프로그램에 맡긴다(설계서 15장 비목표).
    h2.caption("원본은 탐색기에서 열어 확인하세요")

    def _panel_security():
        with st.container(border=True):
            render_evidence(rec, latest)
            st.divider()
            render_override_panel(rec, latest, history, ov_path, reviewer, scope=scope)

    def _panel_doctype():
        with st.container(border=True):
            render_doctype_panel(rec, latest_dt, history_dt, ov_path, reviewer,
                                 tax, scope=scope)

    c1, c2 = st.columns(2)
    left, right = (_panel_security, _panel_doctype)
    with c1:
        left()
    with c2:
        right()

    # 기준 문서 등록은 두 축 아래에 '하나'만 둔다. 예전에는 보안등급 칸과 업무분류
    # 칸에 따로 있었는데, 저장소(class_seed.jsonl)에는 어차피 문서 하나가 한 줄로
    # 들어간다 — 버튼이 둘이면 "둘 다 눌러야 하나"를 매번 고민하게 된다.
    cands, _ = doctype_review.effective_doctype(rec, latest_dt, tax)
    confirmed_dc_ids = [c["dc_id"] for c in cands if c["status"] == "confirmed"]
    # 접힌 칸 제목만 보고도 등록 여부를 알 수 있게 한다(펼쳐야 알면 또 누르게 된다).
    _seeded = seedstore.norm_file(file) in seedstore.load_seed_files(
        st.session_state.get("seedcfg", {}).get("seed_path"))
    with st.expander("🌱 기준 문서로 등록됨 — 내용 갱신" if _seeded
                     else "🌱 이 문서를 '기준 문서'로 등록"):
        render_seed_promote(rec, effective_grade(rec, latest)[0],
                            confirmed_dc_ids, tax, scope=scope)


#------------------------------------------------------------------
# ③ 검토함 화면 렌더 (설계서 9장)
#=> "오늘 할 일"만 한 건씩 처리하는 화면. 찾는 화면이 아니라 처리하는 화면이라
#   검색·필터를 두지 않고, 큐의 맨 앞 한 건만 크게 보여준다.
#   관점이 바꾸는 것은 '누가 큐에 담기는가'와 '무엇을 눌러 끝내는가' 둘이다.
#     · 보안등급 관점 — 판단 못 함·저확신 문서 → 등급 버튼 3개 중 하나로 끝
#     · 업무분류 관점 — 확정 안 된 제안이 남은 문서 → 체크하고 확정으로 끝
#     · 전체 관점    — 둘 중 하나라도 할 일이 남은 문서
#    1) 진행률(처리한 건수/전체)을 위에 두어 끝이 보이게 한다
#    2) 맨 앞 한 건은 근거까지 펼쳐서, 나머지는 접어서 보여준다
#    3) '나중에'는 이 문서를 이번 목록의 뒤로 미룬다(저장하지 않는다)
#
# -in: df         = 문서 표
# -in: records    = 원본 레코드
# -in: latest     = 보안등급 수정 맵
# -in: history    = 보안등급 수정 이력
# -in: latest_dt  = 업무분류 결정 맵
# -in: history_dt = 업무분류 결정 이력
# -in: ov_path    = cso_override.jsonl 경로
# -in: reviewer   = 검토자 이름
# -in: tax        = 회사 분류 체계(None 이면 업무분류 큐는 비어 있다)
#
# -out: 없음(Streamlit 출력)
# -out: error = 없음
#------------------------------------------------------------------
def render_inbox(df, records, latest, history, latest_dt, history_dt,
                 ov_path, reviewer, tax):
    rec_by_file = {r.get("file"): r for r in records}

    # 두 축을 함께 처리한다 — 한 문서를 열었을 때 등급과 분류를 한 번에 끝내는 편이
    # 같은 문서를 두 번 여는 것보다 낫다. 양쪽 다 봐야 하는 문서를 맨 앞에 둔다.
    queue = df[df["need_sec"] | df["need_doc"]].copy()
    queue["_both"] = queue["need_sec"] & queue["need_doc"]
    queue = queue.sort_values(["_both", "need_sec", "confidence"],
                              ascending=[False, False, True]).drop(columns="_both")
    n_both = int((df["need_sec"] & df["need_doc"]).sum())
    n_sec = int((df["need_sec"] & ~df["need_doc"]).sum())
    n_doc = int((~df["need_sec"] & df["need_doc"]).sum())
    head = (f"**할 일이 남은 문서 {len(queue)}건** — "
            f"둘 다 {n_both}건 · 보안등급만 {n_sec}건 · 업무분류만 {n_doc}건")

    # 이번 화면에서 '나중에'로 미룬 문서는 뒤로 보낸다(저장하지 않는 임시 순서).
    later = st.session_state.setdefault("later", [])
    if later:
        queue = pd.concat([queue[~queue["file"].isin(later)],
                           queue[queue["file"].isin(later)]])

    if queue.empty:
        st.success("오늘 검토할 문서가 없습니다 👍")
        if st.button("현황으로 돌아가기", key="inbox_home"):
            goto(menu=MENU_HOME)
        return

    total_axis = int((df["need_sec"] | df["need_doc"]).sum())
    st.markdown(head)
    st.progress(0.0 if not total_axis else max(0.0, 1 - len(queue) / max(total_axis, 1)),
                text=f"남은 {len(queue)}건")

    files = list(queue["file"])[:30]
    for i, f in enumerate(files):
        rec = rec_by_file.get(f)
        if not rec:
            continue
        row = queue[queue["file"] == f].iloc[0]
        label = (f"{i + 1} / {len(queue)} · {os.path.basename(f)} · "
                 f"{GRADE_LABEL[row['final']]} {W.pct(row['confidence'])} · {row['doctype']}")
        # 맨 앞 한 건만 펼친다 — 한 번에 한 건씩 처리하게 하려는 것이다.
        with st.expander(label, expanded=(i == 0)):
            st.caption(f)
            render_detail(rec, latest, history, latest_dt, history_dt,
                          ov_path, reviewer, tax, scope="inbox")
            if st.button("나중에 보기", key=f"later_{f}"):
                if f not in later:
                    later.append(f)
                st.rerun()

    if len(queue) > 30:
        st.caption(f"…외 {len(queue) - 30}건. 위에서부터 처리하면 계속 채워집니다.")


#------------------------------------------------------------------
# 업무분류 후보 하나의 근거 렌더
#=> "왜 이 분류가 제안됐는가"를 신호별로 한 줄씩, 기여도와 함께 보여준다.
#   [설계서 원칙 4] 접지 않는다 — 접어 두면 아무도 열지 않고, 그러면 결과만
#   보고 확정하게 된다. 근거를 보고 판단하라고 만든 화면이므로 펼쳐 둔다.
#   문장 만들기는 doctype_review.reason_block() 이 한다 — 이 함수는 그리기만
#   한다. 그래야 문장 쪽을 Streamlit 없이 테스트할 수 있다.
#
# -in: cand = effective_doctype() 이 낸 후보 dict
#
# -out: 없음(Streamlit 위젯 출력)
# -out: error = 없음
#------------------------------------------------------------------
def render_doctype_evidence(cand):
    block = doctype_review.reason_block(cand)

    for ln in block["lines"]:
        c = ln["conf"]
        tail = f"　<span style='color:gray'>기여 {W.pct(c)}</span>" if c is not None else ""
        st.markdown(f"　· {ln['text']}{tail}", unsafe_allow_html=True)

    if block["summary"]:
        st.caption(f"　{block['summary']}")
    if block["warn"]:
        st.warning(block["warn"])


#------------------------------------------------------------------
# doctype 후보 확정/거절 패널 렌더
#=> 선택 문서의 업무분류 후보를 하나씩 제안(보류)/확정/거절로 고르게 하고,
#   저장하면 문서당 한 줄(axis:"doctype")로 cso_override.jsonl 에 남긴다.
#   원본 레코드(labels.doctype)는 절대 고치지 않는다 — 결정은 오버라이드
#   파일에만 쌓인다(render_override_panel 과 같은 append-only 원칙, 설계서 7-4-1).
#
# -in: rec        = 분류 레코드
# -in: latest_dt  = doctype_review.load_doctype_overrides() 의 latest
# -in: history_dt = doctype_review.load_doctype_overrides() 의 history
# -in: ov_path    = cso_override.jsonl 경로(security 오버라이드와 같은 파일 — axis 로 구분)
# -in: reviewer   = 검토자 이름
# -in: scope      = 위젯 key 접두(호출 위치 구분용 — render_override_panel 과 같은 이유로
#                   같은 문서가 여러 곳에 동시에 그려질 때 key 충돌을 막는다)
#
# -out: 없음(저장 시 파일 append + rerun)
# -out: error = 없음
#------------------------------------------------------------------
def render_doctype_panel(rec, latest_dt, history_dt, ov_path, reviewer, tax, scope="doctype"):
    file = rec.get("file", "")
    candidates, reviewed = doctype_review.effective_doctype(rec, latest_dt, tax)

    n_conf = sum(1 for c in candidates if c["status"] == "confirmed")
    n_auto = sum(1 for c in candidates if c.get("auto"))
    # 자동 확정은 '확정'이되 사람이 아직 확인하지 않은 확정이다. 그 사실을 배지에
    # 남긴다 — 나중에 "이 분류 누가 정했어?" 를 화면만 보고 답할 수 있어야 한다.
    if reviewed and n_conf:
        badge = "확정됨"
    elif reviewed:
        badge = "해당 없음으로 확정"
    elif n_auto:
        badge = "자동 확정 · 관리자 확인 전"
    else:
        badge = "확정 대기"
    st.markdown(f"##### {W.AXIS_DOC} <span style='font-weight:400;color:gray'>— "
                f"{W.AXIS_DOC_SUB}</span>", unsafe_allow_html=True)
    st.caption(f"{badge} · 문서당 **여러 개 가능** · 자동으로 찾은 분류는 "
               "**확정된 것으로 봅니다** — 틀린 것만 체크를 풀고 [이대로 확정] 하세요")

    # 회사 분류 체계를 연결하지 않은 배포에서는 기능을 숨기지 않고 '꺼짐'으로 보여
    # 준다 — 숨기면 그런 기능이 있다는 것을 영영 모른다(설계서 12-1).
    if tax is None:
        st.info("업무분류는 아직 사용하지 않습니다. 설정 화면에서 **회사 분류 체계**를 "
                "연결하면 “이 문서가 무엇에 관한 것인지”도 함께 정리됩니다.")
        return

    # 후보 전체를 여기서는 모두 보여준다 — 목록의 "상위 N개" 제한은 표에만 적용한다.
    # 결정을 내리려면 검토자는 전부 봐야 한다(상위 설계 6-3).
    # 계층 정합성 경고(재설계 11-3) — 서로 다른 큰 갈래가 동시에 제안된 경우다.
    # 후보를 자동으로 버리지 않고 사람에게 올린다.
    # 엔진이 준 detail 은 개발자용 설명이라 그대로 쓰지 않는다. 대신 "어느
    # 분류끼리 부딪쳤는지"를 이름으로 보여준다 — 검토자에게 필요한 건 그것뿐이다.
    path_by_id = {c.get("dc_id"): (c.get("path") or c.get("dc_id")) for c in candidates}
    for cf in ((rec.get("labels") or {}).get("doctype") or {}).get("conflicts") or []:
        names = [path_by_id.get(i, i) for i in (cf.get("dc_ids") or [])]
        if not names:
            continue
        st.warning("서로 다른 갈래의 분류가 함께 제안됐습니다 — "
                   + " · ".join(f"**{n}**" for n in names)
                   + ". 어느 쪽이 맞는지(또는 둘 다 맞는지) 확인해 주세요.")

    picked = {}
    if candidates:
        # 관리자가 직접 고른 분류도 이 목록에 함께 들어온다(근거 줄에 표시된다).
        st.markdown("**이 문서에 붙은 분류**")
        for c in candidates:
            dc_id = c["dc_id"]
            # 체크박스를 쓰는 이유 — 업무분류는 여러 개가 동시에 맞을 수 있다.
            # 위젯 모양이 곧 "여러 개 고를 수 있다"는 안내가 된다(설계서 원칙 2).
            # 기본으로 체크해 둔다 — 제안이 맞는 경우가 대부분이라, 맞을 때마다
            # 하나씩 체크하는 것보다 아닌 것만 풀고 [이대로 확정] 하는 쪽이 손이
            # 훨씬 덜 간다. 다만 예전에 관리자가 '아니라고 표시한'(rejected) 것은
            # 다시 체크해 두면 그 판단을 되돌리는 셈이라 그대로 비워 둔다.
            on = st.checkbox(f"**{c.get('path') or dc_id}**  ·  {W.pct(c.get('confidence'))}",
                             value=(c["status"] != "rejected"),
                             key=f"dt_ck_{scope}_{file}_{dc_id}")
            picked[dc_id] = on
            # 확정은 확정이되, 자동인지 사람이 정한 것인지는 눈에 보여야 한다.
            st.caption(f"　{W.doctype_by(c)}")
            render_doctype_evidence(c)
    else:
        st.caption("자동으로 제안된 분류가 없습니다. 아래에서 직접 고를 수 있습니다.")

    # 직접 추가 — 규칙이 아직 없는 분류는 사람이 직접 고른다. 반드시 목록에서만
    # 고르게 한다(직접 타이핑하면 오타가 그대로 잘못된 DC_ID 가 된다, 상위 설계 T5).
    opts = taxlib.selectable(tax)
    label_by_id = dict(opts)
    extra = st.multiselect("직접 추가 — 회사 분류 체계에서 찾기",
                           [dc for dc, _ in opts],
                           default=[c["dc_id"] for c in candidates
                                    if c["status"] == "confirmed"
                                    and c["dc_id"] not in {d for d, _ in opts}],
                           format_func=lambda dc: label_by_id.get(dc, dc),
                           key=f"dt_extra_{scope}_{file}")

    # "아직 안 본 문서"와 "봤는데 해당 분류가 없는 문서"는 완전히 다르다. 이 체크가
    # 없으면 해당 분류가 없는 문서가 영원히 대기 목록에 남는다(설계서 8-1).
    none_of = st.checkbox("해당하는 분류가 없습니다",
                          key=f"dt_none_{scope}_{file}")

    # 업무분류 확정은 '정정'이 아니라 원래 사람이 하기로 되어 있는 정상 절차라
    # 이유를 필수로 받지 않는다(설계서 9-1). 보안등급 쪽과 다른 점이다.
    reason = st.text_input("결정 이유 (선택)", key=f"dt_reason_{scope}_{file}",
                           placeholder="예: 제안 내용은 첨부일 뿐 계약서가 본체")

    chosen = [] if none_of else (
        [dc for dc, on in picked.items() if on] +
        [dc for dc in extra if not picked.get(dc)])
    btn = ("‘해당 없음’으로 확정" if none_of else f"이대로 확정 ({len(chosen)}개)")
    if st.button(btn, key=f"dt_save_{scope}_{file}", type="primary",
                 width="stretch"):
        if not reviewer.strip():
            st.error("화면 오른쪽 위에 '검토자 이름'을 먼저 입력하세요(기록에 남깁니다).")
        else:
            # 체크하지 않은 제안은 '아니라고 표시함'으로 남긴다 — "애초에 제안되지
            # 않음"과 구분해야 나중에 규칙 정밀도를 잴 수 있다(상위 설계 7-4-1).
            rejected = [c["dc_id"] for c in candidates if c["dc_id"] not in chosen]
            doctype_review.append_doctype_override(
                ov_path, file, rec.get("doc_id"), chosen, rejected,
                reason.strip(), reviewer.strip())
            st.success(f"확정됨: {len(chosen)}개" +
                       (f" · 아니라고 표시함 {len(rejected)}개" if rejected else ""))
            st.rerun()
    st.caption("✔ 체크하지 않은 제안은 “아니라고 표시함”으로 기록돼 다음 분류가 좋아집니다.")

    hist = history_dt.get(file, [])
    if hist:
        with st.expander(f"결정 기록 {len(hist)}건"):
            for e in reversed(hist):
                st.caption(f"- {e.get('ts','')} · {e.get('reviewer','')} · "
                           f"확정 {len(e.get('confirmed') or [])}개 · "
                           f"아니라고 표시함 {len(e.get('rejected') or [])}개 "
                           f"· {e.get('reason','')}")



#------------------------------------------------------------------
# 내보내기 버튼 렌더 (문서함 오른쪽 위)
#=> 확정 결과를 CSV/JSONL 로 내려받아 문서중앙화 시스템이 접근권한 적용에 쓰게 한다.
#   탭 하나를 쓸 만큼 큰 기능이 아니라서 문서함 구석 버튼으로 옮겼고, 지금 화면에
#   걸린 필터 결과를 그대로 낸다 — "보이는 것이 곧 내보내지는 것"이라야 헷갈리지 않는다.
#   두 축을 모두 담는다(보안등급 1개 + 업무분류 요약) — 한 파일로 두 축을 넘긴다.
#
# -in: df = 지금 화면에 보이는(필터 적용된) 문서 표
#
# -out: 없음(다운로드 버튼 2개)
# -out: error = 없음
#------------------------------------------------------------------
def render_export_button(df):
    cols = [c for c in ["file", "final", "auto", "overridden", "confidence",
                        "doctype", "dt_reviewed", "method", "rule_version"] if c in df.columns]
    out = df[cols].rename(columns={"final": "grade", "doctype": "업무분류",
                                   "dt_reviewed": "업무분류_확정여부"})
    with st.popover("⬇️ 내보내기", width="stretch"):
        st.caption(f"지금 보이는 **{len(out)}건**을 내보냅니다(보안등급 + 업무분류).")
        st.download_button("CSV 파일로", out.to_csv(index=False).encode("utf-8-sig"),
                           "classification_final.csv", "text/csv",
                           width="stretch")
        jsonl = "\n".join(json.dumps(r, ensure_ascii=False) for r in out.to_dict("records"))
        st.download_button("JSONL 파일로", jsonl.encode("utf-8"),
                           "classification_final.jsonl", "application/json",
                           width="stretch")


#------------------------------------------------------------------
# 기준 문서에 함께 적어 둘 '원본의 지문'
#=> 기준 문서는 원본의 사본(벡터)을 들고 있어서, 원본이 나중에 수정되면 낡은
#   벡터로 계속 전파하게 된다. 그것을 알아채려면 등록 시점의 지문을 남겨야 한다.
#    1) 내용 해시는 분류 실행이 --hash 로 이미 구해 둔 값을 그대로 쓴다(공짜다)
#    2) 크기·수정시각은 사람이 눈으로 확인할 때 쓰는 보조 정보다
#
# -in: rec = 분류 레코드(hash 가 없을 수 있다 — 옛 결과이거나 --hash 없이 돌린 경우)
#
# -out: dict = {"hash","size","mtime"} 중 알아낸 것만(아무것도 없으면 빈 dict)
# -out: error = 없음(파일을 못 읽어도 조용히 건너뛴다)
#------------------------------------------------------------------
def seed_doc_meta(rec):
    meta = {}
    h = (rec or {}).get("hash")
    if h:
        meta["hash"] = h
    f = (rec or {}).get("file") or ""
    try:
        stt = os.stat(f)
        meta["size"] = stt.st_size
        meta["mtime"] = datetime.datetime.fromtimestamp(
            stt.st_mtime).astimezone().isoformat(timespec="seconds")
    except OSError:
        pass          # 원본이 없거나 못 읽으면 지문 없이 등록한다(등록 자체를 막지 않는다)
    return meta


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
    if not base:
        st.error("분류 실행 명령이 비어 있습니다 — 설정 ▸ 고급 ▸ 실행 명령을 먼저 채워 주세요.")
        return None
    if not os.path.isfile(file):
        st.error(f"원본 파일을 찾을 수 없어 임베딩을 계산하지 못했습니다: {file}")
        return None
    try:
        with st.spinner(f"임베딩 계산 중… {os.path.basename(file)}"):
            r = gerunner.run_csoclassify_file(base, file, pythonpath=cfg.get("pythonpath"))
    except Exception as e:
        uierrlog.show_error(f"임베딩 실패: {e}", exc=e, where="임베딩")
        return None
    vec = r.get("vector")
    if vec is None:
        # 실행은 됐는데 벡터가 안 온 경우. 예전에는 여기서 조용히 None 을 돌려줘
        # "눌러도 아무 일이 없다"로 보였다 — 이유를 반드시 말한다. 실행 명령이
        # --with-vector 를 모르는 옛 빌드인지(그러면 stderr 에 경고가 찍힌다),
        # 아니면 문서에서 텍스트를 못 뽑은 것인지는 stderr 이 알려 준다.
        err = str(r.get("_stderr") or "")
        if "with-vector" in err:
            st.error("실행 명령이 `--with-vector` 를 모릅니다 — 기준 문서로 쓸 벡터를 "
                     "받을 수 없습니다. 실행 파일이 옛 빌드일 수 있으니 다시 빌드하거나, "
                     "설정 ▸ 고급 ▸ 실행 명령을 벡터를 내는 버전으로 바꿔 주세요.")
        else:
            st.error("임베딩 벡터를 얻지 못했습니다 — 이 문서에서 텍스트를 뽑지 "
                     "못했을 수 있습니다(추출 실패). 문서를 열어 내용이 있는지 "
                     "확인해 주세요.")
        if err.strip():
            st.caption(f"실행 로그: {err.strip().splitlines()[-1][:200]}")
    return vec


#------------------------------------------------------------------
# 기준 문서 등록 버튼 렌더 (두 축 통합 · Phase 4 ① + 설계서 5-4)
#=> 확정된 문서를 기준 문서 저장소(class_seed.jsonl)에 한 줄로 올린다.
#   [왜 버튼이 하나인가] 저장소에는 문서 하나가 한 줄이다 — 그 한 줄이 등급
#   (grade)과 확정 분류(labels.doctype)를 같이 들고 있다. 예전처럼 보안등급 칸과
#   업무분류 칸에 버튼이 따로 있으면, 같은 한 줄을 만들자고 두 번 누르는 꼴이고
#   한쪽만 누른 반쪽 기준 문서가 생긴다.
#    1) 이번에 등록될 내용(등급 · 확정 분류)을 먼저 글로 보여준다
#    2) 벡터가 없으면 이 문서만 임베딩해 확보한다 — 벡터 없는 기준 문서는
#       비교 대상이 못 되므로 저장할 이유가 없다
#    3) 기존 기준 문서와 거의 같으면(사본) 경고하고 확인을 받는다
#
# -in: rec              = 분류 레코드(vector 는 없을 수 있음 → 등록 시 온디맨드 계산)
# -in: final_grade      = 최종 보안등급(C/S/O · "보류" 면 등급은 등록하지 않는다)
# -in: confirmed_dc_ids = 이미 저장된 확정 분류 dc_id 목록(없으면 업무분류는 건너뜀)
# -in: tax              = 회사 분류 체계(등록될 분류 이름을 보여주는 데만 쓴다)
# -in: scope            = 위젯 key 접두(같은 문서가 여러 화면에 그려질 때 충돌 방지)
#
# -out: 없음(등록 시 파일 저장 + 감사 기록 + rerun)
# -out: error = 없음(실패는 화면에 이유와 함께 표시)
#------------------------------------------------------------------
def render_seed_promote(rec, final_grade, confirmed_dc_ids, tax=None, scope="list"):
    cfg = st.session_state.get("seedcfg", {})
    file = rec.get("file", "")
    vec = rec.get("vector")
    has_grade = final_grade in ("C", "S", "O")

    st.markdown("**이 문서를 기준 문서로 등록**")
    st.caption("기준 문서는 다음 분류 때 '비슷한 문서 참고'의 잣대가 됩니다 — "
               "확실한 문서만 올리세요.")

    # 둘 다 없으면 올릴 것이 없다. 무엇을 먼저 해야 하는지까지 알려 준다.
    if not has_grade and not confirmed_dc_ids:
        st.caption("등급을 정하거나 업무분류를 확정한 뒤에 등록할 수 있습니다.")
        return

    # 이번에 저장될 내용을 그대로 보여준다 — 눌러 보고 나서야 알게 되면 안 된다.
    parts = []
    if has_grade:
        parts.append(f"보안등급 **{W.GRADE_PLAIN[final_grade]}**")
    if confirmed_dc_ids:
        names = " · ".join(taxlib.path_of(tax, dc) for dc in confirmed_dc_ids)
        parts.append(f"업무분류 **{len(confirmed_dc_ids)}건** ({names})")
    st.caption("등록할 내용 — " + " / ".join(parts))
    if not has_grade:
        st.caption("↳ 보안등급은 아직 정해지지 않아 업무분류만 등록합니다.")
    elif not confirmed_dc_ids:
        st.caption("↳ 확정된 업무분류가 없어 보안등급만 등록합니다.")

    seeds = seedstore.load_seeds(cfg.get("seed_path"))
    # 경로 표기(대소문자·슬래시)만 다른 같은 문서를 "처음 등록"으로 오해하지 않게
    # 정규화해서 찾는다. 이미 있으면 각 축이 지금 어떤 상태인지도 알려 준다.
    prev = seedstore.find_seed(seeds, file)
    if prev:
        cur = []
        for _ax in seedstore.AXES:
            _st = seedstore.axis_state(prev, _ax)
            if _st:
                cur.append(f"{seedstore.AXIS_LABEL[_ax]} {W.SEED_STATE[_st]}")
        st.caption("이미 기준 문서로 등록돼 있습니다("
                   + " · ".join(cur) + ") → 다시 누르면 최신 내용으로 갱신됩니다.")

    # 벡터가 있으면 미리 near-dup 을 경고(자기 자신 제외), 없으면 등록 시 온디맨드 임베딩.
    force = True
    sim = 0.0
    if vec is not None:
        sim, near = seedstore.nearest_seed_sim(vec, seeds, exclude_file=file)
        if sim >= SEED_DUP_SIM:
            st.warning(f"이미 등록된 기준 문서와 거의 같습니다(닮은 정도 {W.pct(sim)} · "
                       f"{os.path.basename(near or '')}) — 사본일 수 있어 등록해도 도움이 되지 않습니다.")
            force = st.checkbox("그래도 등록", key=f"seedforce_{scope}_{file}")
    else:
        st.caption("이 문서는 벡터가 없어(빠른 분류로 생략됨), 등록할 때 이 문서만 "
                   "임베딩을 계산합니다.")

    if st.button("🌱 기준 문서로 등록", key=f"promote_{scope}_{file}", type="primary"):
        if not cfg.get("reviewer", "").strip():
            st.error("화면 오른쪽 위에 '검토자 이름'을 먼저 입력하세요.")
            return
        if not force:
            st.info("중복이라 추가하지 않았습니다.")
            return
        v = vec
        if v is None:                       # 벡터 없으면 지금 이 문서만 임베딩해 확보
            v = _embed_file_ondemand(cfg, file)
            if v is None:
                return                      # 실패 이유는 _embed_file_ondemand 가 표시
        # 축을 하나씩 확정한다(set_axis 가 저장소로 들어가는 유일한 통로다).
        # 한 축만 확정해도 다른 축은 건드리지 않으므로 순서를 신경 쓸 필요가 없다.
        dc_ids = list(confirmed_dc_ids)
        if not dc_ids:
            # 이번엔 확정 분류가 없더라도, 예전에 등록해 둔 분류는 지우지 않는다.
            dc_ids = list(seedstore.axis_value(prev, "doctype") or []) if prev else []
        # 원본이 나중에 바뀐 것을 알아채려면 지금의 지문을 함께 적어 둬야 한다.
        doc_meta = seed_doc_meta(rec)
        who = cfg["reviewer"].strip()
        seeds2 = seeds
        if has_grade:
            before = seedstore.axis_value(prev, "security") if prev else None
            seeds2 = seedstore.set_axis(seeds2, file, "security", final_grade, who,
                                        source="phase4", vector=v, doc=doc_meta)
            seedstore.append_seed_audit(cfg["audit_path"],
                                        "update" if before else "add", file,
                                        axis="security", before=before, after=final_grade,
                                        reviewer=who, reason=f"문서함 등록 sim={sim:.3f}")
        if dc_ids:
            before = list(seedstore.axis_value(prev, "doctype") or []) if prev else None
            seeds2 = seedstore.set_axis(seeds2, file, "doctype", dc_ids, who,
                                        source="phase6", vector=v, doc=doc_meta)
            seedstore.append_seed_audit(cfg["audit_path"],
                                        "update" if before else "add", file,
                                        axis="doctype", before=before, after=dc_ids,
                                        reviewer=who, reason="문서함 등록")
        try:
            seedstore.save_seeds(cfg["seed_path"], seeds2)
        except OSError as e:
            uierrlog.show_error(f"기준 문서 저장 실패: {e}", exc=e, where="기준 문서 저장")
            return
        st.success(f"기준 문서로 등록됐습니다 — {' / '.join(parts)} · "
                   f"저장 위치: {cfg['seed_path']}")
        st.rerun()


#------------------------------------------------------------------
# seed 관리 화면 렌더 (Phase 4)
#=> 기준 문서 구성 확인 + 이번 분류에서 고신뢰 C/S 일괄 등록 + 신규 파일 직접
#   업로드 등록 + 등록된 기준 문서를 축별로 고치기 + 저장소 점검.
#
# -in: records = 이번 분류 레코드 리스트(등록 후보 추출용, 벡터 포함)
# -in: latest  = 파일별 최신 오버라이드 맵(등록 시 사람이 고친 최종등급 반영)
# -in: tax     = 회사 분류 체계(없는 분류코드 점검에 쓴다. None 이면 그 점검만 생략)
#
# -out: 없음(Streamlit 출력)
# -out: error = 없음(개별 실패는 화면에 표시)
#------------------------------------------------------------------
def render_seed_manager(records, latest, tax=None):
    cfg = st.session_state.get("seedcfg", {})
    seed_path = cfg.get("seed_path")
    audit_path = cfg.get("audit_path")
    reviewer = cfg.get("reviewer", "")

    seeds = seedstore.load_seeds(seed_path)
    counts = seedstore.grade_counts(seeds)

    ax = seedstore.axis_counts(seeds)
    st.caption("“이런 문서는 이 등급 · 이 분류” 본보기로 등록해 둔 문서입니다 · "
               f"총 {ax['total']}건 — {W.AXIS_SEC} {ax['security']} · "
               f"{W.AXIS_DOC} {ax['doctype']} (둘 다 {ax['both']})"
               + (f" · 확인 필요 {ax['suspect']}" if ax["suspect"] else ""))
    m = st.columns(4)
    m[0].metric("기준 문서", len(seeds))
    m[1].metric(W.GRADE_LABEL["C"], counts["C"])
    m[2].metric(W.GRADE_LABEL["S"], counts["S"])
    m[3].metric(W.GRADE_LABEL["O"], counts["O"])
    if seeds:
        lo = min(counts.values())
        if lo == 0 or lo < max(counts.values()) / 4:
            weak = "/".join(g for g, n in counts.items() if n == lo)
            st.warning(f"등급이 한쪽으로 쏠려 있습니다 — {weak} 기준 문서가 부족합니다. "
                       "비슷한 문서 비교가 그 등급을 잘 못 맞출 수 있어요(보강 권장).")

    st.divider()
    st.markdown("**① 이번 분류에서 골라 등록**")
    st.caption("규칙으로 확실하게 등급이 정해진 문서와 관리자가 직접 확정한 문서를 "
               "본보기로 등록합니다. 등록할 등급을 고르고 [한꺼번에 등록]을 누르세요. "
               "사본은 자동으로 건너뜁니다.")
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
    st.caption(f"고른 등급 **{'/'.join(chosen) if chosen else '없음'}** · 등록 후보 **{total_cand}건** "
               "(중복·이미 등록된 것은 자동 제외)"
               + (f" · 그중 {len(novec_cands)}건은 벡터가 없어 승격 시 임베딩을 계산합니다(지연)"
                  if novec_cands else ""))
    if st.button("🌱 한꺼번에 등록(중복 제외)", type="primary", disabled=not total_cand):
        if not reviewer.strip():
            st.error("화면 오른쪽 위에 '검토자 이름'을 먼저 입력하세요(기록에 남깁니다).")
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
                                                axis="security", before=None,
                                                after=a["grade"], reviewer=reviewer.strip(),
                                                reason="한꺼번에 등록")
                st.success(f"{stats['added']}건 등록 · 건너뜀: 중복 {stats['dup']} · "
                           f"기존 {stats['exist']} · 벡터없음 {stats['novec']}")
                st.rerun()
            else:
                st.info(f"새로 등록된 기준 문서가 없습니다(중복 {stats['dup']} · "
                        f"기존 {stats['exist']} · 벡터없음 {stats['novec']}).")

    st.divider()
    st.markdown("**② 파일을 직접 올려 등록**")
    st.caption("분류를 돌리지 않고, 관리자가 대표 문서를 직접 본보기로 등록합니다.")
    # 완료 후 업로드/등급/메모 위젯을 비우려고 버전 카운터로 key 를 갈아끼운다(초기화 트릭).
    ver = st.session_state.get("up_ver", 0)
    flash = st.session_state.pop("up_flash", None)   # 초기화 직후에도 결과 메시지는 보이게
    if flash:
        st.success(flash)
    up = st.file_uploader("파일 업로드(여러 개 가능)", accept_multiple_files=True,
                          key=f"up_file_{ver}")
    ug = st.radio("부여할 등급", ["C", "S", "O"], horizontal=True, key=f"up_grade_{ver}",
                  format_func=lambda g: W.GRADE_LABEL[g])
    unote = st.text_input("메모(선택)", key=f"up_note_{ver}")
    if st.button("올린 파일을 기준 문서로 등록", type="primary"):
        if not reviewer.strip():
            st.error("화면 오른쪽 위에 '검토자 이름'을 먼저 입력하세요.")
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
                                                    axis="security", before=None,
                                                    after=ug, reviewer=reviewer.strip(),
                                                    reason="파일 업로드 등록")
                        added += 1
                except Exception as e:
                    uierrlog.show_error(f"{uf.name}: {e}", exc=e, where="기준 문서 업로드")
                prog.progress((i + 1) / n_up, text=f"완료 {i + 1}/{n_up} · {uf.name}")
            if added:
                seedstore.save_seeds(seed_path, cur)
                # 완료 → 다음 실행에서 업로드/등급/메모 초기화(ver+1) + 결과 메시지 표시(flash)
                st.session_state["up_flash"] = f"{added}건을 기준 문서로 등록했습니다"
                st.session_state["up_ver"] = ver + 1
                st.rerun()
            else:
                st.warning("등록된 기준 문서가 없습니다(위 오류 메시지를 확인하세요).")

    st.divider()
    st.markdown("**③ 등록된 기준 문서 고치기(축별 해제 · 등급 · 메모 · 삭제)**")
    if not seeds:
        st.info("아직 등록된 기준 문서가 없습니다. 위에서 파일을 올리거나, 문서함에서 등록하세요.")
        return
    st.caption("한 문서가 두 축(보안등급 · 업무분류)을 함께 떠받칩니다. "
               "한 축만 빼려면 **그 축의 해제**를 체크하세요 — 다른 축은 그대로 남습니다. "
               "**삭제**는 두 축을 함께 지웁니다.")
    st.caption(f"{W.GRADE_LABEL['C']} {counts['C']} · {W.GRADE_LABEL['S']} {counts['S']} · "
               f"{W.GRADE_LABEL['O']} {counts['O']}")

    sdf = pd.DataFrame([_seed_row(s, tax) for s in seeds])
    edited = st.data_editor(
        sdf, hide_index=True, width="stretch", key="seed_editor",
        column_config={
            "file": st.column_config.TextColumn("문서", disabled=True, width="large"),
            "grade": st.column_config.SelectboxColumn("등급", options=["C", "S", "O"], width="small"),
            "doctype": st.column_config.TextColumn("업무분류", disabled=True),
            "state": st.column_config.TextColumn("상태", disabled=True),
            "note": st.column_config.TextColumn("메모"),
            "다시 사용": st.column_config.CheckboxColumn("다시 사용", width="small",
                                                     help="‘확인 필요’로 빠져 있는 축을 다시 잣대로 씁니다"),
            "보안 해제": st.column_config.CheckboxColumn("보안 해제", width="small"),
            "분류 해제": st.column_config.CheckboxColumn("분류 해제", width="small"),
            "삭제": st.column_config.CheckboxColumn("삭제", width="small"),
        },
    )
    if st.button("변경 저장"):
        if not reviewer.strip():
            st.error("화면 오른쪽 위에 '검토자 이름'을 먼저 입력하세요.")
            return
        who = reviewer.strip()
        cur, changes, dropped = list(seeds), 0, 0
        for _, row in edited.iterrows():
            f = row["file"]
            o = seedstore.find_seed(seeds, f) or {}

            # 삭제가 가장 강하다 — 두 축을 함께 지운다(되돌릴 이력은 감사 로그에).
            if row["삭제"]:
                cur = seedstore.remove_seed(cur, f)
                seedstore.append_seed_audit(audit_path, "delete", f, axis="-",
                                            before=_axis_summary(o), after=None,
                                            reviewer=who, reason="표에서 삭제")
                changes += 1
                continue

            # 축별 해제 — 값은 남기고 상태만 바꾼다(무엇을 뺐는지 남아야 한다).
            for axis, col in (("security", "보안 해제"), ("doctype", "분류 해제")):
                if row[col] and seedstore.axis_state(o, axis) not in ("", "retired"):
                    cur, gone = seedstore.retire_axis(cur, f, axis)
                    seedstore.append_seed_audit(audit_path, "retire", f, axis=axis,
                                                before=seedstore.axis_value(o, axis),
                                                after=None, reviewer=who,
                                                reason="표에서 해제")
                    changes += 1
                    if gone:
                        dropped += 1

            # '다시 사용' — 시스템이 잠시 빼 둔(확인 필요) 축을 사람이 되살린다.
            if row["다시 사용"]:
                for axis in seedstore.AXES:
                    if seedstore.axis_state(o, axis) == seedstore.STATE_SUSPECT:
                        cur = seedstore.restore_axis(cur, f, axis)
                        seedstore.append_seed_audit(audit_path, "restore", f, axis=axis,
                                                    before=None,
                                                    after=seedstore.axis_value(o, axis),
                                                    reviewer=who, reason="관리자 확인")
                        changes += 1

            # 등급 변경 — 보안축을 해제하는 중이 아닐 때만 의미가 있다.
            before_g = seedstore.axis_value(o, "security")
            if row["grade"] and row["grade"] != before_g and not row["보안 해제"]:
                cur = seedstore.set_axis(cur, f, "security", row["grade"], who)
                seedstore.append_seed_audit(audit_path, "update", f, axis="security",
                                            before=before_g, after=row["grade"],
                                            reviewer=who, reason="표에서 수정")
                changes += 1

            # 메모는 줄 전체에 붙는 값이라 축과 무관하게 그대로 적는다.
            if (row["note"] or "") != (o.get("note") or ""):
                cur = [({**x, "note": row["note"] or ""}
                        if seedstore.norm_file(x.get("file")) == seedstore.norm_file(f) else x)
                       for x in cur]
                changes += 1

        if changes:
            try:
                left = seedstore.save_seeds(seed_path, cur)
            except OSError as e:
                uierrlog.show_error(f"기준 문서 저장 실패: {e}", exc=e, where="기준 문서 저장")
                return
            msg = f"{changes}건 변경 저장됨 · 남은 기준 문서 {left}건"
            if dropped:
                msg += f" (두 축이 모두 해제돼 {dropped}건은 목록에서 빠졌습니다)"
            st.success(msg)
            st.rerun()
        else:
            st.info("변경 사항이 없습니다.")

    st.divider()
    render_seed_check(seeds, records, tax, seed_path, audit_path, reviewer)


#------------------------------------------------------------------
# 기준 문서 표의 한 줄 만들기
#=> 두 축의 값과 상태를 한 줄에 사람이 읽을 수 있게 눌러 담는다.
#
# -in: s   = 기준 문서 항목
# -in: tax = 회사 분류 체계(분류 이름을 보여주는 데만 쓴다)
#
# -out: dict = data_editor 한 줄
# -out: error = 없음
#------------------------------------------------------------------
def _seed_row(s, tax=None):
    dc_ids = list(seedstore.axis_value(s, "doctype") or [])
    names = " · ".join(taxlib.path_of(tax, d) for d in dc_ids) if dc_ids else ""
    # 상태는 축마다 다를 수 있으므로 '축: 상태' 를 이어 붙인다(사유가 있으면 함께).
    bits = []
    for a in seedstore.AXES:
        stt = seedstore.axis_state(s, a)
        if not stt:
            continue
        txt = f"{seedstore.AXIS_LABEL[a]} {W.SEED_STATE.get(stt, stt)}"
        reason = ((s.get("axes") or {}).get(a) or {}).get("reason")
        if reason:
            txt += f"({W.SEED_REASON.get(reason, reason)})"
        bits.append(txt)
    return {
        "file": s.get("file"),
        "grade": seedstore.axis_value(s, "security") or "",
        "doctype": names,
        "state": " · ".join(bits),
        "note": s.get("note", "") or "",
        "다시 사용": False, "보안 해제": False, "분류 해제": False, "삭제": False,
    }


#------------------------------------------------------------------
# 감사 로그에 남길 '줄 전체' 요약
#=> 줄을 통째로 지울 때 무엇이 사라졌는지 한 줄로 남긴다.
#
# -in: s = 기준 문서 항목
#
# -out: dict = {"security":..., "doctype":[...]}
# -out: error = 없음
#------------------------------------------------------------------
def _axis_summary(s):
    return {a: seedstore.axis_value(s, a) for a in seedstore.AXES
            if seedstore.axis_state(s, a)}


#------------------------------------------------------------------
# 파일 내용 해시(SHA-256)
#=> 기준 문서를 다시 읽어 갱신할 때 '새 지문'을 구한다. 엔진의 --hash 와 같은
#   방식(내용 전체 SHA-256, 소문자 16진수)이어야 다음 분류에서 값이 맞는다.
#
# -in: path = 파일 경로
#
# -out: str|None = 16진수 해시(읽기 실패면 None)
# -out: error = 없음(실패를 None 으로 환원)
#------------------------------------------------------------------
def _file_sha256(path):
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


#------------------------------------------------------------------
# ④ 기준 문서 점검 화면
#=> 기준 문서는 원본의 사본(벡터)을 들고 있어서 원본과 조용히 어긋난다.
#   무엇이 썩었는지 한 화면에서 세어 보여 주고, 고칠 수단을 바로 옆에 둔다.
#   [원칙] 시스템은 표시만 한다 — 지우는 것은 언제나 사람이 누른다.
#    1) seedstore.check_seeds 가 판정을 다 하고, 여기서는 세어 보여 주기만 한다
#    2) '다시 읽어 갱신' 은 그 문서만 다시 임베딩하고 새 지문을 적은 뒤 되살린다
#    3) '코드 정리' 는 분류 체계에서 사라진 dc_id 만 빼낸다
#
# -in: seeds      = 기준 문서 리스트
# -in: records    = 이번 분류 레코드(원본 지문 대조에 쓴다)
# -in: tax        = 회사 분류 체계(없으면 분류코드 점검 생략)
# -in: seed_path  = 저장소 경로
# -in: audit_path = 감사 로그 경로
# -in: reviewer   = 검토자 이름
#
# -out: 없음(Streamlit 출력)
# -out: error = 없음(개별 실패는 화면에 표시)
#------------------------------------------------------------------
def render_seed_check(seeds, records, tax, seed_path, audit_path, reviewer):
    cfg = st.session_state.get("seedcfg", {})
    st.markdown("**④ 기준 문서 점검**")
    rep = seedstore.check_seeds(seeds, records=records, tax=tax)
    c = rep["counts"]
    st.caption(f"기준 문서 {c['total']}건 — {W.AXIS_SEC} {c['security']} · "
               f"{W.AXIS_DOC} {c['doctype']} (둘 다 {c['both']})")

    rows = [
        ("stale", "원본이 바뀜", len(rep["stale"])),
        ("missing", "원본을 못 찾음", len(rep["missing"])),
        ("orphan", "없는 업무분류 코드", len(rep["orphan"])),
        ("novec", "벡터 없음", len(rep["novec"])),
        ("dim", "다른 모델로 만든 벡터", len(rep["dim"])),
        ("conflict", "거의 같은 문서인데 등급이 다름", len(rep["conflict"])),
        ("suspect", "확인 필요로 빠져 있음", len(rep["suspect"])),
    ]
    if not any(n for _, _, n in rows):
        st.success("이상 없음 — 모든 기준 문서가 원본과 맞고, 겹치거나 충돌하는 것도 "
                   "없으며, 확인이 필요한 것도 없습니다.")
        return

    for key, label, n in rows:
        if not n:
            continue
        with st.expander(f"⚠ {label} — {n}{'쌍' if key == 'conflict' else '건'}"):
            if key == "stale":
                st.caption("등록할 때 적어 둔 원본 지문과 지금 원본이 다릅니다. 등급까지 "
                           "바뀌었는지는 사람만 알 수 있어 자동으로 고치지 않았습니다.")
                st.dataframe(pd.DataFrame([{"문서": os.path.basename(x["file"]),
                                            "위치": x["file"]} for x in rep["stale"]]),
                             hide_index=True, width="stretch")
                if st.button("🔄 다시 읽어 갱신", key="seedfix_stale"):
                    _refresh_seeds(cfg, seeds, [x["file"] for x in rep["stale"]],
                                   seed_path, audit_path, reviewer)
            elif key == "missing":
                st.caption("그 경로에 파일이 없습니다. 잠깐 드라이브가 안 붙은 것일 수도 있어 "
                           "자동으로 지우지 않았습니다 — 확실할 때 위 ③ 표에서 지우세요.")
                st.dataframe(pd.DataFrame([{"문서": os.path.basename(f or ""), "위치": f}
                                           for f in rep["missing"]]),
                             hide_index=True, width="stretch")
            elif key == "orphan":
                st.caption("분류 체계에서 사라진 분류를 가리키고 있습니다. 그 코드만 빼냅니다"
                           "(남은 분류가 없으면 그 축은 ‘확인 필요’로 빠집니다).")
                st.dataframe(pd.DataFrame([{"문서": os.path.basename(x["file"]),
                                            "사라진 코드": ", ".join(x["dc_ids"])}
                                           for x in rep["orphan"]]),
                             hide_index=True, width="stretch")
                if st.button("🧹 코드 정리", key="seedfix_orphan"):
                    _clean_orphans(seeds, rep["orphan"], tax, seed_path, audit_path, reviewer)
            elif key == "novec":
                st.caption("벡터가 없어 ‘비슷한 문서 참고’에 쓰이지 못합니다.")
                st.dataframe(pd.DataFrame([{"문서": os.path.basename(f or ""), "위치": f}
                                           for f in rep["novec"]]),
                             hide_index=True, width="stretch")
                if st.button("🧮 벡터 만들기", key="seedfix_novec"):
                    _refresh_seeds(cfg, seeds, rep["novec"], seed_path, audit_path, reviewer)
            elif key == "dim":
                st.caption("다른 임베딩 모델로 만든 벡터가 섞여 있습니다 — 유사도 비교가 "
                           "의미를 잃습니다. 위 ‘다시 읽어 갱신’으로 다시 만드세요.")
                st.dataframe(pd.DataFrame([{"문서": os.path.basename(x["file"]),
                                            "이 문서": x["dim"], "대다수": x["common"]}
                                           for x in rep["dim"]]),
                             hide_index=True, width="stretch")
            elif key == "suspect":
                st.caption("시스템이 이상을 발견해 잠시 잣대에서 빼 둔 축입니다. 확인한 뒤 "
                           "위 ③ 표에서 **다시 사용**을 체크하거나, 더 쓰지 않을 것이면 "
                           "**해제**·**삭제**를 체크하세요.")
                st.dataframe(pd.DataFrame([{"문서": os.path.basename(x["file"] or ""),
                                            "축": seedstore.AXIS_LABEL[x["axis"]],
                                            "이유": W.SEED_REASON.get(x["reason"], x["reason"])}
                                           for x in rep["suspect"]]),
                             hide_index=True, width="stretch")
            else:
                st.caption("사실상 같은 문서가 서로 다른 등급으로 등록돼 있습니다. 이 근처 "
                           "문서는 ‘등급이 갈려서’ 전파가 되지 않습니다 — 한쪽을 지우거나 "
                           "등급을 맞추세요.")
                st.dataframe(pd.DataFrame([{"닮은 정도": W.pct(x["sim"]),
                                            "문서 A": os.path.basename(x["a"] or ""),
                                            "등급 A": x["ga"],
                                            "문서 B": os.path.basename(x["b"] or ""),
                                            "등급 B": x["gb"]} for x in rep["conflict"]]),
                             hide_index=True, width="stretch")


#------------------------------------------------------------------
# 기준 문서 다시 읽어 갱신(재임베딩 + 새 지문)
#=> 원본이 바뀌었거나 벡터가 없는 기준 문서를 지금 원본으로 다시 만든다.
#    1) 그 문서만 임베딩해 새 벡터를 얻는다
#    2) 새 내용 해시·크기·수정시각을 적는다
#    3) 시스템이 잠시 빼 두었던(확인 필요) 축을 다시 잣대로 되살린다
#
# -in: cfg        = seedcfg(실행 명령 등)
# -in: seeds      = 기준 문서 리스트
# -in: files      = 갱신할 문서 경로들
# -in: seed_path  = 저장소 경로
# -in: audit_path = 감사 로그 경로
# -in: reviewer   = 검토자 이름
#
# -out: 없음(저장 후 rerun)
# -out: error = 없음(개별 실패는 화면에 표시하고 나머지는 계속 진행)
#------------------------------------------------------------------
def _refresh_seeds(cfg, seeds, files, seed_path, audit_path, reviewer):
    if not reviewer.strip():
        st.error("화면 오른쪽 위에 '검토자 이름'을 먼저 입력하세요.")
        return
    cur, done, failed = list(seeds), 0, []
    prog = st.progress(0.0, text=f"다시 읽는 중… 0/{len(files)}")
    for i, f in enumerate(files):
        prog.progress(i / max(len(files), 1), text=f"다시 읽는 중… {i}/{len(files)} · "
                                                   f"{os.path.basename(f or '')}")
        v = _embed_file_ondemand(cfg, f)
        if v is None:
            failed.append(f)
            continue
        doc = {"hash": _file_sha256(f)}
        try:
            stt = os.stat(f)
            doc.update({"size": stt.st_size,
                        "mtime": datetime.datetime.fromtimestamp(
                            stt.st_mtime).astimezone().isoformat(timespec="seconds")})
        except OSError:
            pass
        cur = seedstore.set_meta(cur, f, vector=v, doc=doc)
        # 원본이 바뀌어 빼 두었던 축을 되살린다(사람이 갱신을 눌렀다 = 확인했다).
        for axis in seedstore.AXES:
            if seedstore.axis_state(seedstore.find_seed(cur, f) or {}, axis) == seedstore.STATE_SUSPECT:
                cur = seedstore.restore_axis(cur, f, axis)
                seedstore.append_seed_audit(audit_path, "restore", f, axis=axis,
                                            before=None, after=None,
                                            reviewer=reviewer.strip(),
                                            reason="다시 읽어 갱신")
        done += 1
    prog.progress(1.0, text=f"완료 {done}/{len(files)}")
    if done:
        seedstore.save_seeds(seed_path, cur)
        st.success(f"{done}건을 다시 읽어 갱신했습니다"
                   + (f" · 실패 {len(failed)}건" if failed else ""))
        st.rerun()
    else:
        st.warning("갱신된 기준 문서가 없습니다(위 오류 메시지를 확인하세요).")


#------------------------------------------------------------------
# 사라진 분류코드 정리
#=> 분류 체계에 더 이상 없는 dc_id 만 업무분류축에서 빼낸다. 남은 코드가 없으면
#   그 축은 '확인 필요'로 빠진다 — 값을 통째로 지우지 않는 이유는, 무엇을 가리키던
#   기준이었는지가 사라지면 사람이 판단할 근거도 사라지기 때문이다.
#
# -in: seeds      = 기준 문서 리스트
# -in: orphans    = check_seeds 의 orphan 목록 [{file, dc_ids}]
# -in: tax        = 회사 분류 체계
# -in: seed_path  = 저장소 경로
# -in: audit_path = 감사 로그 경로
# -in: reviewer   = 검토자 이름
#
# -out: 없음(저장 후 rerun)
# -out: error = 없음
#------------------------------------------------------------------
def _clean_orphans(seeds, orphans, tax, seed_path, audit_path, reviewer):
    if not reviewer.strip():
        st.error("화면 오른쪽 위에 '검토자 이름'을 먼저 입력하세요.")
        return
    cur, n = list(seeds), 0
    for o in orphans:
        f = o["file"]
        before = list(seedstore.axis_value(seedstore.find_seed(cur, f) or {}, "doctype") or [])
        keep = [d for d in before if d not in o["dc_ids"]]
        if keep:
            cur = seedstore.set_axis(cur, f, "doctype", keep, reviewer.strip())
        else:
            cur = seedstore.suspect_axis(cur, f, "doctype", "orphan")
        seedstore.append_seed_audit(audit_path, "update", f, axis="doctype",
                                    before=before, after=keep,
                                    reviewer=reviewer.strip(),
                                    reason="사라진 분류코드 정리")
        n += 1
    if n:
        seedstore.save_seeds(seed_path, cur)
        st.success(f"{n}건에서 사라진 분류코드를 정리했습니다.")
        st.rerun()


#------------------------------------------------------------------
# 분류 직후 기준 문서 자동 대조
#=> 방금 만든 결과에는 문서마다 내용 지문(hash)이 들어 있다. 기준 문서가 적어 둔
#   지문과 견주면 "등록할 때의 그 원본이 맞는가"를 공짜로 알 수 있다.
#    1) 지문이 다르거나(stale) 원본이 사라졌으면(missing) 두 축에 확인 필요 표시
#    2) 원본이 제자리로 돌아왔으면 표시를 푼다
#   [원칙] 절대 지우지 않는다. 기준 문서 하나가 잘못 사라지면 그와 비슷한 문서
#   수십 건의 분류가 함께 흔들린다 — 오탐으로 지우는 쪽이 훨씬 비싸다.
#
# -in: cfg         = seedcfg(저장소·감사 로그 경로)
# -in: grades_path = 방금 만들어진 분류 결과 파일
#
# -out: 없음(바뀐 것이 있으면 저장 + 화면에 한 줄 안내)
# -out: error = 없음(실패해도 분류 결과 자체는 이미 저장돼 있으므로 조용히 넘어간다)
#------------------------------------------------------------------
def _sync_seeds_after_run(cfg, grades_path):
    seed_path = cfg.get("seed_path")
    if not seed_path or not os.path.isfile(seed_path):
        return
    try:
        seeds = seedstore.load_seeds(seed_path)
        if not seeds:
            return
        recs = load_records(grades_path, os.path.getmtime(grades_path))
        new, events = seedstore.sync_with_records(seeds, recs)
        if not events:
            return
        seedstore.save_seeds(seed_path, new)
        for e in events:
            seedstore.append_seed_audit(cfg.get("audit_path"), e["action"], e["file"],
                                        axis=e["axis"], before=None, after=None,
                                        reviewer="system", reason=e["reason"])
    except (OSError, ValueError, json.JSONDecodeError):
        return      # 대조는 부가 기능이다 — 실패해도 분류 결과를 망치지 않는다

    n_sus = len({e["file"] for e in events if e["action"] == "suspect"})
    n_res = len({e["file"] for e in events if e["action"] == "restore"})
    if n_sus:
        st.warning(f"기준 문서 {n_sus}건의 원본이 바뀌었거나 사라졌습니다 — "
                   "‘설정 ▸ 기준 문서 ▸ ④ 점검’에서 확인해 주세요"
                   "(그때까지 그 문서는 잣대로 쓰지 않습니다).")
    if n_res:
        st.info(f"기준 문서 {n_res}건이 원본과 다시 맞아 잣대로 되살아났습니다.")


#------------------------------------------------------------------
# 설정 ④ 분류 실행 렌더 (설계서 10장)
#=> 고른 폴더를 실제로 분류한다. 예전에는 실행 명령·PYTHONPATH 같은 개발용 입력칸이
#   화면에 있었는데, 실무 관리자가 정할 값이 아니라서 전부 감추고 **체크박스 3개**만
#   남겼다. 체크박스가 곧 CLI 옵션(--axis / --taxonomy·--doc-rules / 전파)이 된다.
#    1) 무엇을 판정할지 고른다(보안등급 · 업무분류 · 비슷한 문서 참고)
#    2) csoclassify 를 한 번만 실행한다 — 엔진이 분류하면서 보류 문서 전파까지
#       그 자리에서 끝내므로, 결과 파일을 다시 읽는 2차 패스는 두지 않는다
#    3) 끝나면 결과를 곧바로 읽어 현황 화면으로 보낸다
#
# -in: grades_path = 결과를 저장(=다른 화면이 읽을) 경로
# -in: folder      = 분류할 폴더(설정 ①에서 고른 값)
# -in: tax         = 회사 분류 체계(None 이면 업무분류 체크박스를 끈 채 보여준다)
#
# -out: 없음(실행 시 결과 파일 생성 + rerun)
# -out: error = 실패는 화면에 표시(예외를 올리지 않는다)
#------------------------------------------------------------------
def render_run_panel(grades_path, folder=None, tax=None):
    cfg = st.session_state.get("seedcfg", {})
    paths = st.session_state.get("paths", {})
    folder = folder or paths.get("folder") or ""

    c1, c2, c3 = st.columns(3)
    do_sec = c1.checkbox(f"{W.AXIS_SEC} 판정", value=True, key="run_sec",
                         help="개인정보·키워드·폴더·파일 이름을 검사합니다")
    do_doc = c2.checkbox(f"{W.AXIS_DOC} 제안", value=tax is not None, key="run_doc",
                         disabled=tax is None,
                         help="회사 분류 체계를 연결해야 켤 수 있습니다")
    # 이 체크는 '2단계를 한 번 더 돌릴까'가 아니라 '전파를 할까 말까'다.
    # 엔진(Python·Rust 모두)은 분류하면서 그 자리에서 보류 문서를 기준 문서와
    # 비교해 구제한다 — 끄면 --rule-only 로 그 과정을 통째로 뺀다.
    do_prop = c3.checkbox("비슷한 문서 참고(기준 문서와 비교)", value=True, key="run_prop",
                          help="판단하지 못한 문서를 이미 등록된 '기준 문서'와 비교해 "
                               "구제합니다(분류하면서 함께 처리됩니다). 끄면 규칙만으로 "
                               "판정해 가장 빠릅니다")

    pattern = st.text_input("파일 종류", value="*", key="run_pattern",
                            help="예: *  ·  *.hwp,*.docx  (비워 두면 전체)") or "*"
    # 예전에는 '하위 폴더까지 모두' 체크박스가 있었다. 그런데 두 엔진 모두 --dir
    # 이면 언제나 재귀라, 체크를 꺼도 하위 폴더가 그대로 분류됐다 — 끌 수 없는
    # 것을 끌 수 있는 것처럼 보여 주는 화면이었다. 사실만 한 줄로 알린다.
    st.caption("하위 폴더까지 모두 검사합니다.")

    if not folder or not os.path.isdir(folder):
        st.warning("먼저 위 **① 분류할 폴더**에서 폴더를 지정하세요.")
        return
    if not (do_sec or do_doc):
        st.warning("보안등급과 업무분류 중 적어도 하나는 켜야 합니다.")
        return

    # 체크박스 → CLI 인자. 한쪽만 켜면 --axis 로 그 축만 돌린다(업무분류만 돌리면
    # 가장 무거운 개인정보 검출을 통째로 건너뛰어 크게 빨라진다, 상위 설계 6-4).
    extra = []
    # 문서 내용의 지문(SHA-256). 기준 문서가 "등록할 때의 그 원본"인지 확인하는
    # 유일한 근거다 — 어차피 전 문서를 여는 김에 함께 구하므로 값이 싸다.
    extra += ["--hash"]
    if do_doc and tax:
        extra += ["--taxonomy", tax.get("path") or "",
                  "--doc-rules", paths.get("doc_rules") or ""]
    if do_sec and not do_doc:
        extra += ["--axis", "security"]
    elif do_doc and not do_sec:
        extra += ["--axis", "doctype"]

    if not do_prop:
        # 전파를 끄는 유일한 스위치. 이걸 주면 엔진이 임베딩·전파를 아예 하지
        # 않는다(기준 문서 파일이 옆에 있어도 무시).
        extra += ["--rule-only"]

    # 기준 문서 저장소를 'UI 가 관리하는 파일 하나'로 못박는다.
    # 이 인자를 주지 않으면 엔진은 조용히 실행 파일 옆의 class_seed.jsonl 을 쓴다 —
    # UI 에서 등록한 기준 문서가 다음 분류에 전혀 반영되지 않던 원인이었다.
    sp = cfg.get("seed_path") if do_prop else None
    if sp and not os.path.isfile(sp):
        # 아직 등록한 기준 문서가 없어도 빈 파일을 만들어 둔다. 파일이 없으면
        # 엔진이 다시 실행 파일 옆 저장소로 되돌아가기 때문이다(빈 파일은
        # seed=0 으로 정상 동작한다 — 기준 문서 없이 규칙만으로 분류).
        try:
            os.makedirs(os.path.dirname(os.path.abspath(sp)), exist_ok=True)
            open(sp, "a", encoding="utf-8").close()
        except OSError as e:
            st.warning(f"기준 문서 저장소를 만들지 못했습니다({e}) — 이번 실행은 "
                       "실행 파일 옆 저장소를 쓸 수 있습니다.")
            sp = None
    if sp:
        extra += ["--seeds", sp]
        n_seed = len(seedstore.load_seeds(sp))
        st.caption(f"기준 문서 **{n_seed}건**을 잣대로 씁니다 · `{sp}`"
                   + ("" if n_seed else " — 아직 등록된 기준 문서가 없어 "
                                        "이번에는 규칙만으로 분류합니다"))

    if st.button("▶ 분류 시작", type="primary", key="run_go"):
        # 진행 규모 안내용으로 대상 파일 수를 미리 센다(CLI 와 동일하게 콤마·중괄호
        # 다중 패턴을 펼쳐 각각 매칭 후 중복 제거).
        matched = set()
        for gp in gerunner.expand_glob_patterns(pattern):
            # 실행이 항상 재귀이므로 미리 세는 쪽도 재귀여야 한다 — 안 그러면
            # "18개 파일을 분류합니다" 라고 알린 뒤 100개를 분류하는 일이 생긴다.
            pat = os.path.join(folder, "**", gp)
            matched.update(f for f in globmod.glob(pat, recursive=True) if os.path.isfile(f))
        n = len(matched)
        if n == 0:
            st.warning("패턴에 맞는 파일이 없습니다.")
            return
        st.info(f"{n}개 파일을 분류합니다 — 규칙·폴더·파일 이름으로 확실히 정해진 문서는 "
                "임베딩을 건너뛰어 빠릅니다.")
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
                base, folder, grades_path, pattern,
                pythonpath=cfg.get("pythonpath"), on_progress=_on_prog,
                extra_args=extra)
        except Exception as e:
            uierrlog.show_error(f"실행 오류: {e}", exc=e, where="분류 실행")
            return
        if not os.path.isfile(grades_path):
            # 엔진이 남긴 stderr 를 통째로 로그에 실어 둔다 — 화면에는 뒤 600자만
            # 보여 주지만, 진짜 원인은 그 앞쪽에 있는 경우가 많다.
            uierrlog.log_error("분류 결과 파일이 생성되지 않았습니다.\n"
                               f"명령 stderr 전문:\n{r.stderr}", where="분류 실행")
            st.error(f"분류 결과 파일이 생성되지 않았습니다.\n{r.stderr[-600:]}")
            return
        total_elapsed = time.perf_counter() - start
        prog.progress(1.0, text=f"분류 완료 · {n}/{n} · 총 {_fmt(total_elapsed)}")
        st.success(f"분류 완료 · {r.summary or 'OK'} · 소요 {_fmt(total_elapsed)}")

        # 분류 결과와 기준 문서를 대조해, 원본이 바뀌었거나 사라진 기준 문서에
        # '확인 필요' 표시를 붙인다(지우지는 않는다 — 사람이 판단할 일이다).
        _sync_seeds_after_run(cfg, grades_path)

        # 예전에는 여기서 --propagate 로 결과 파일을 다시 읽어 한 번 더 전파했다.
        # 엔진이 분류하면서 이미 같은 일을 끝내 놓기 때문에(Python cli.py 의
        # auto_prop, Rust main.rs 의 auto_prop 둘 다) 두 번째 패스는 같은 문서를
        # 다시 읽고 다시 쓰는 중복이었다. 없앴다.

        # 총 실행시간을 사이드카에 남겨, 대시보드가 표기하게 한다.
        run_seconds = time.perf_counter() - start
        try:
            save_run_meta(grades_path, run_seconds, n, do_prop)
        except OSError:
            pass  # 부가정보라 저장 실패해도 실행은 정상 처리

        # 끝나면 곧바로 현황으로 보낸다 — "다 됐는데 이제 뭘 누르지"를 없앤다.
        goto(menu=MENU_HOME)


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

    st.markdown("### 🌱 기준 문서 진단")
    seeds = seedstore.load_seeds(cfg.get("seed_path"))
    counts, total, weak, msg = report.seed_balance(seeds)
    c = st.columns(4)
    c[0].metric("기준 문서", total)
    c[1].metric(W.GRADE_LABEL["C"], counts["C"])
    c[2].metric(W.GRADE_LABEL["S"], counts["S"])
    c[3].metric(W.GRADE_LABEL["O"], counts["O"])
    (st.warning if weak else st.success)(msg)

    dpairs, _ = report.near_dups(
        [{"file": s.get("file"), "vector": s.get("vector")} for s in seeds], thr)
    if dpairs:
        st.markdown(f"**거의 같은 기준 문서 {len(dpairs)}쌍** — 하나만 남기고 위 ③에서 지우기를 권합니다")
        st.dataframe(pd.DataFrame([{"유사도": round(s, 3),
                                    "문서 A": os.path.basename(a or ""),
                                    "문서 B": os.path.basename(b or "")}
                                   for a, b, s in dpairs[:50]]),
                     hide_index=True, width="stretch")
    else:
        st.caption("겹치는 기준 문서 없음.")

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
    st.markdown("**확신 분포**")
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
                     hide_index=True, width="stretch")
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
        st.warning("아래 **고급**에서 보안등급 기준 파일의 위치를 확인해 주세요.")
        return

    try:
        doc = rulesedit.load_doc(path)
    except Exception as e:
        uierrlog.show_error(f"보안등급 기준을 읽지 못했습니다: {e}", exc=e, where="규칙셋 읽기")
        return

    st.caption(f"기준 파일 `{os.path.basename(path)}` · 버전 `{doc.get('version')}`")
    bulk = st.number_input("bulk 임계값 (PII 가 이 건수 이상이면 등급 상향)",
                           min_value=1, value=int(doc.get("defaults", {}).get("bulk_threshold", 5)))

    st.markdown("**이 말이 나오면 → 이 등급으로** (단어는 콤마로 구분)")
    kdf = pd.DataFrame([{"id": k.get("id"), "name": k.get("name"),
                         "terms": ", ".join(k.get("terms") or []),
                         "base_grade": k.get("base_grade"),
                         "seed": bool(k.get("seed_eligible"))} for k in doc.get("keywords", [])])
    kedit = st.data_editor(
        kdf, num_rows="dynamic", width="stretch", key="kw_editor",
        column_config={
            "id": st.column_config.TextColumn("id"),
            "name": st.column_config.TextColumn("이름"),
            "terms": st.column_config.TextColumn("단어(콤마 구분)", width="large"),
            "base_grade": st.column_config.SelectboxColumn("등급", options=["C", "S", "O"]),
            "seed": st.column_config.CheckboxColumn("기준 문서 후보"),
        })

    st.markdown("**이 폴더 안이면 → 이 등급으로** (예: /인사/, //hr-server/)")
    pdf = pd.DataFrame([{"id": p.get("id"), "name": p.get("name"),
                         "match": ", ".join(p.get("match") or []),
                         "grade": p.get("grade"),
                         "seed": bool(p.get("seed_eligible"))} for p in doc.get("paths", [])])
    pedit = st.data_editor(
        pdf, num_rows="dynamic", width="stretch", key="path_editor",
        column_config={
            "id": st.column_config.TextColumn("id"),
            "name": st.column_config.TextColumn("이름"),
            "match": st.column_config.TextColumn("경로 조각(콤마 구분)", width="large"),
            "grade": st.column_config.SelectboxColumn("등급", options=["C", "S", "O"]),
            "seed": st.column_config.CheckboxColumn("기준 문서 후보"),
        })

    st.markdown("**개인정보가 나오면 → 이 등급으로** (종류별 정책)")
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
        rdf, num_rows="dynamic", width="stretch", key="regex_editor",
        column_config={
            "id": st.column_config.TextColumn("id"),
            "name": st.column_config.TextColumn("이름"),
            "label": st.column_config.SelectboxColumn("ko-pii 유형", options=_labopts),
            "base_grade": st.column_config.SelectboxColumn("기본등급", options=["C", "S", "O"]),
            "bulk_grade": st.column_config.SelectboxColumn("상향등급", options=["C", "S", "O"]),
            "weight": st.column_config.SelectboxColumn("가중", options=["high", "medium", "low"]),
            "seed": st.column_config.CheckboxColumn("기준 문서 후보"),
        })

    # 편집 행(공셀 정리) 준비 — 미리보기·저장 공용.
    krows = kedit.fillna("").to_dict("records")
    prows = pedit.fillna("").to_dict("records")
    rrows = redit.fillna("").to_dict("records")

    with st.expander("확신 계산값 — 읽기 전용"):
        st.caption("확신은 **검토함에 담기는 기준과 정렬**에만 쓰이고 등급 자체는 바꾸지 않습니다. "
                   "수정하려면 **cso_rules.yaml 을 직접 편집**하세요(이 표는 표기 전용).")
        conf = doc.get("confidence", {}) or {}
        st.dataframe(pd.DataFrame([{"신호": grp,
                                    "high": (conf.get(grp) or {}).get("high"),
                                    "medium": (conf.get(grp) or {}).get("medium"),
                                    "low": (conf.get(grp) or {}).get("low")}
                                   for grp in ("regex", "keyword", "path")]),
                     hide_index=True, width="stretch")
        st.caption(f"파일 이름 신호의 고정 확신값: {conf.get('name')}")

    st.divider()
    st.markdown("**미리보기 — 예시 글로 등급을 확인합니다(저장하지 않음)**")
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
            st.success(f"등급: {W.grade_badge(sig.grade)} · 확신 {W.pct(sig.confidence)}")
            for h in sig.hits:
                ts = h.terms
                detail = ", ".join(f"{t}×{c}" for t, c in ts) if ts else f"{h.count}건"
                st.write(f"- [{h.layer}] {h.name} → {h.grade} · {detail}")
            if not sig.hits:
                st.caption("걸린 규칙 없음")
        except Exception as e:
            uierrlog.show_error(f"미리보기 실패: {e}", exc=e, where="규칙셋 미리보기")

    st.divider()
    if st.button("💾 보안등급 기준 저장", type="primary", key="rules_save"):
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
            uierrlog.show_error(f"저장 실패: {e}", exc=e, where="규칙셋 저장")




#------------------------------------------------------------------
# 기본 파일 경로 묶음 만들기
#=> 화면이 쓰는 파일 경로(결과·수정기록·기준문서·규칙·분류체계)의 기본값을 한 번에
#   계산한다. 예전에는 이 값들이 사이드바 입력칸 7개로 늘 보였는데, 실무 관리자가
#   정할 값이 아니라서 설정 화면의 '고급'으로 내렸다(설계서 원칙 5).
#   경로는 반드시 ui 폴더 기준 '절대경로'로 만든다 — 상대경로면 streamlit 을 어느
#   폴더에서 띄웠느냐에 따라 결과가 엉뚱한 곳에 쓰이고 화면이 못 찾는다.
#
# -in: 없음
#
# -out: dict = {result, override, seed, seed_audit, rules, taxonomy, doc_rules,
#               folder, cmd, pythonpath}
# -out: error = 없음(파일이 없어도 경로 문자열은 만든다)
#------------------------------------------------------------------
def default_paths():
    here = os.path.dirname(os.path.abspath(__file__))
    result = next((p for p in (os.path.join(here, "cso_result.jsonl"),
                               os.path.join(here, "sample_cso_result.jsonl"))
                   if os.path.isfile(p)), os.path.join(here, "cso_result.jsonl"))
    policy = os.path.abspath(os.path.join(here, "..", "resources", "policy"))

    # 분류 체계·업무분류 규칙은 ui 폴더에 둔 것을 먼저 쓰고, 없으면 배포 정책 폴더를 본다.
    def _pick(name):
        local = os.path.join(here, name)
        return local if os.path.isfile(local) else os.path.join(policy, name)

    # 실행 명령 기본값 = 배포 exe. 아직 없으면 소스 모듈로 폴백한다.
    exe = os.path.abspath(os.path.join(here, "..", "dist-pkg", "csoclassify.exe"))
    if os.path.isfile(exe):
        cmd = f'"{exe}"' if " " in exe else exe
        pythonpath = ""
    else:
        cmd = f"{sys.executable} -m csoclassify"
        pythonpath = os.path.abspath(os.path.join(here, "..", "src"))

    return {
        "result": result,
        "override": os.path.join(here, "cso_override.jsonl"),
        "seed": os.path.join(here, "class_seed.jsonl"),
        "seed_audit": os.path.join(here, "class_seed_audit.jsonl"),
        "rules": os.path.join(policy, "cso_rules.yaml"),
        "taxonomy": _pick("doc_taxonomy.yaml"),
        # 분류 체계를 '다시 가져올' 때 읽는 원본 JSON(MpowerV11 내보내기 결과).
        "export_input": _pick("doc_classification_export.json"),
        "doc_rules": _pick("doc_rule.yaml"),
        "folder": "",
        "cmd": cmd,
        "pythonpath": pythonpath,
    }


#------------------------------------------------------------------
# 업무분류 판단 기준 편집 렌더 (설계서 10-1)
#=> doc_rule.yaml 을 "이 말이 나오면 → 이 분류로" 표로 고친다. YAML 을 화면에
#   보여주지 않는 것이 핵심이다. 분류(node)는 사람이 치는 곳이 없고 회사 분류
#   체계에서만 들어온다 — 오타가 잘못된 DC_ID 로 저장되는 길을 막는다(상위 설계 T5).
#    1) 지금 규칙을 표로 펼친다(분류는 전체경로로 표시)
#    2) 빠진 분류는 아래 '분류 불러오기' 로 doc_taxonomy.yaml 에서 한 번에 가져오고,
#       그때 분류 최하위 명칭과 비슷한 말이 시작값으로 채워진다
#    3) 저장하면 .bak 을 남기고 덮어쓴다
#
# -in: path = doc_rule.yaml 경로
# -in: tax  = 회사 분류 체계(None 이면 편집을 막고 안내만 한다)
#
# -out: 없음(Streamlit 출력)
# -out: error = 로드/저장 실패는 화면에 표시
#------------------------------------------------------------------
def render_doc_rules_editor(path, tax):
    if tax is None:
        st.info("회사 분류 체계를 먼저 연결하면 업무분류 기준을 만들 수 있습니다.")
        return
    try:
        doc = docruleedit.load_doc(path)
    except Exception as e:
        uierrlog.show_error(f"업무분류 기준을 읽지 못했습니다: {e}", exc=e, where="업무분류 규칙 읽기")
        return

    # 유의어 사전은 규칙 파일 옆에 둔다(없으면 띄어쓰기 표기만 만든다).
    syn = docruleedit.load_synonyms(path)
    # 새 규칙에 얹을 값(weight 등)도 본보기에서 가져온다 — 코드에 박아 두면
    # 회사마다 다른 값을 주려 할 때 프로그램을 고쳐야 한다.
    new_rule = docruleedit.load_template(path).get("new_rule")
    rows = docruleedit.to_rows(doc, tax)
    edited = pd.DataFrame(rows)
    if rows:
        edited = st.data_editor(
            edited, hide_index=True, width="stretch",
            key="docrule_editor", num_rows="fixed",
            column_config={
                "분류": st.column_config.TextColumn("이 분류로", disabled=True, width="medium"),
                # 판정력이 센 칸부터 놓는다. 같은 말이라도 어느 칸에 넣느냐로
                # 결과가 갈린다 — "규정"은 제목 칸이면 정확하고 앞부분 칸이면 오탐이다.
                "제목에": st.column_config.TextColumn("제목(첫 줄)에 이 말이 있으면",
                                                    width="large"),
                "앞부분에": st.column_config.TextColumn("앞부분(400자)에 이 말이 있으면",
                                                     width="large"),
                "이 말이 나오면": st.column_config.TextColumn("본문 어디든 이 말이 나오면(보조)",
                                                        width="large"),
                "파일 이름에": st.column_config.TextColumn("파일 이름에 이 말이 있으면"),
                "이 폴더 안이면": st.column_config.TextColumn("이 폴더 안이면"),
                "제외할 말": st.column_config.TextColumn("단, 이 말이 있으면 제외"),
                "_id": None, "_node": None,      # 화면에 감춘다(저장에만 쓰는 값)
            })
    else:
        st.caption("아직 업무분류 기준이 없습니다. 아래 **분류 불러오기** 를 누르면 "
                   "회사 분류 체계를 그대로 가져와 시작값까지 채워 줍니다.")

    # ── 분류 체계에서 자동으로 불러오기 ──
    # 예전에는 여기서 분류를 하나 고르고 '추가' 를 눌러 빈 줄을 만들었다. 22개
    # 분류를 22번 반복해야 했고, 그러고도 단어는 전부 사람이 쳐야 했다.
    # 지금은 doc_taxonomy.yaml 을 통째로 읽어 빠진 분류를 한 번에 만들고,
    # 분류 최하위 명칭에서 뽑은 비슷한 말을 시작값으로 넣어 준다.
    exist = {r["_node"] for r in rows}
    n_missing = sum(1 for n in docruleedit.syncable_nodes(tax)
                    if n.get("dc_id") not in exist)
    n_blank = sum(1 for r in rows if not (r["이 말이 나오면"] or r["파일 이름에"]))

    with st.container(border=True):
        st.markdown("**분류 체계에서 규칙 불러오기**")
        st.caption("회사 분류 체계에 있는 분류를 한 번에 가져와 규칙 줄을 만듭니다. "
                   "'이 말이 나오면'·'파일 이름에' 칸에는 그 분류의 **가장 아래 이름**과 "
                   "띄어쓰기만 다른 말, 그리고 유의어 사전에 적어 둔 **같은 뜻 다른 말**이 "
                   "자동으로 채워집니다 (예: 기술/개발 > 설계문서 > **요구사항정의서** → "
                   "`요구사항정의서, 요구사항 정의서, 요구사항명세서, 요구정의서, SRS`).")
        if syn:
            # 사전은 여러 겹이라 어느 파일이 얹혔는지 보여 줘야 관리자가 자기가
            # 고친 파일이 실제로 쓰이고 있는지 확인할 수 있다.
            겹 = " → ".join(os.path.basename(p) for p in syn.get("layers") or [])
            st.caption(f"유의어 사전 `{겹}` 적용 중(뒤가 우선) · "
                       f"별칭 {len(syn.get('aliases') or {})}개 · "
                       f"꼬리말 {len(syn.get('tails') or {})}개 — 회사에서 쓰는 말은 "
                       "`doc_synonyms.local.yaml` 에 더하면 다음 불러오기부터 반영됩니다.")
        else:
            st.caption("유의어 사전(doc_synonyms.core.yaml 등)이 없어 "
                       "띄어쓰기 표기만 만듭니다.")
        b1, b2 = st.columns([3, 1])
        fill_blank = b1.checkbox(
            f"단어가 하나도 없는 기존 규칙({n_blank}개)도 시작값으로 채우기",
            value=True, key="docrule_fill_blank", disabled=not n_blank)
        # 사전을 나중에 늘렸을 때 '이미 채운 규칙'이 영영 옛날 말만 갖게 되는 것을
        # 막는 통로. 기본은 꺼 둔다 — 누르지 않은 사람의 규칙이 조용히 길어지면 안 된다.
        n_done = len(rows) - n_blank
        enrich = b1.checkbox(
            f"이미 채워 둔 규칙({n_done}개)에도 **빠진 유의어만 더하기** "
            "(적어 둔 말은 지우지 않습니다)",
            value=False, key="docrule_enrich", disabled=not n_done)
        b1.caption(f"가져올 분류 **{n_missing}개** · 대분류(경영/관리 같은 서랍 이름)와 "
                   "꺼 둔 분류는 가져오지 않습니다.")
        if b2.button("🔄 분류 불러오기", key="docrule_sync", type="primary",
                     disabled=not (n_missing or (n_blank and fill_blank)
                                   or (n_done and enrich))):
            added, filled, enriched = docruleedit.sync_from_taxonomy(
                doc, tax, fill_existing=bool(fill_blank),
                enrich_existing=bool(enrich), syn=syn, new_rule=new_rule)
            try:
                docruleedit.save_doc(path, doc)
                st.success(f"새 분류 {added}개 · 빈 규칙 채움 {filled}개 · "
                           f"유의어 더함 {enriched}개. 표에서 필요 없는 말은 지우고, "
                           "회사에서 쓰는 다른 표현을 더해 주세요.")
                st.rerun()
            except Exception as e:
                uierrlog.show_error(f"저장 실패: {e}", exc=e, where="업무분류 규칙 저장")

        # 대분류처럼 자동으로 가져오지 않는 분류에도 규칙을 걸고 싶을 때가 있다.
        # 흔한 일이 아니므로 접어 둔다 — 평소 화면은 버튼 하나로 끝나야 한다.
        with st.expander("분류 하나만 직접 추가하기"):
            opts = taxlib.selectable(tax)
            label_by_id = dict(opts)
            addable = [dc for dc, _ in opts if dc not in exist]
            c1, c2 = st.columns([3, 1])
            new_node = c1.selectbox("규칙을 추가할 분류", addable,
                                    format_func=lambda dc: label_by_id.get(dc, dc),
                                    index=None, placeholder="분류를 고르세요",
                                    key="docrule_add_node", disabled=not addable)
            if c2.button("＋ 규칙 추가", key="docrule_add", disabled=not new_node):
                node = (tax.get("by_id") or {}).get(new_node) or {}
                # 직접 추가할 때도 시작값은 넣어 준다 — 빈 줄을 주면 결국 사람이
                # 같은 말을 손으로 치게 된다.
                rule = {"id": f"dt_{new_node.lower()}", "node": new_node}
                rule.update(new_rule or docruleedit.NEW_RULE_FALLBACK)
                rule["terms"] = docruleedit.title_variants(node.get("title"), syn=syn)
                rule["filename"] = docruleedit.title_variants(
                    node.get("title"), for_filename=True, syn=syn)
                doc.setdefault("doctype_rules", []).append(rule)
                try:
                    docruleedit.save_doc(path, doc)
                    st.success(f"'{label_by_id.get(new_node)}' 규칙을 추가했습니다. "
                               "표에서 단어를 다듬어 주세요.")
                    st.rerun()
                except Exception as e:
                    uierrlog.show_error(f"저장 실패: {e}", exc=e, where="분류체계 저장")

    if rows and st.button("💾 업무분류 기준 저장", type="primary", key="docrule_save"):
        n = docruleedit.apply_rows(doc, edited.fillna("").to_dict("records"))
        try:
            docruleedit.save_doc(path, doc)
            st.success(f"저장됨 · 규칙 {n}개 (백업: {os.path.basename(path)}.bak)")
            st.rerun()
        except Exception as e:
            uierrlog.show_error(f"저장 실패: {e}", exc=e, where="분류체계 저장")

    missing = docruleedit.nodes_without_rules(doc, tax)
    if missing:
        st.caption(f"⚠ **{len(missing)}개 분류에는 기준이 없어** 자동으로 제안되지 않습니다 — "
                   + ", ".join(missing[:6]) + (" …" if len(missing) > 6 else ""))


#------------------------------------------------------------------
# ④ 설정 화면 렌더 (설계서 10장)
#=> 처음 1회 잡고 잘 안 건드리는 것들을 한 화면에 모았다. 예전에는 사이드바 7칸 +
#   탭 4개(규칙 설정·실행·seed 관리·내보내기)에 흩어져 있던 것들이다.
#   번호를 붙인 블록 4개로, 위에서 아래로 따라가면 첫 실행이 끝나게 했다.
#    ① 분류할 폴더  ② 회사 분류 체계  ③ 판단 기준  ④ 분류 실행  (+ 고급)
#
# -in: paths   = 파일 경로 묶음(session_state["paths"])
# -in: tax     = 회사 분류 체계(None 이면 '연결 안 됨')
# -in: records = 지금 로드된 레코드(고급의 기준 문서·리포트에서 쓴다. 없으면 [])
# -in: df      = 문서 표(없으면 None)
# -in: latest  = 보안등급 수정 맵(없으면 {})
#
# -out: 없음(Streamlit 출력)
# -out: error = 없음(각 블록이 자기 오류를 표시)
#------------------------------------------------------------------
def render_settings(paths, tax, records, df, latest):
    # ── ① 분류할 폴더 ──
    with st.container(border=True):
        st.markdown("##### 1️⃣ 분류할 폴더")
        folder = st.text_input("폴더 경로", value=paths.get("folder", ""),
                               placeholder="예: D:\\collected", key="set_folder",
                               label_visibility="collapsed")
        paths["folder"] = folder
        if folder and not os.path.isdir(folder):
            st.warning("그런 폴더가 없습니다. 경로를 확인해 주세요.")
        else:
            # '이 폴더에 몇 개가 있나'를 직접 센다. 예전에는 여기에 '지금 결과'
            # 건수를 붙였는데, 그건 지난번 분류 결과(다른 폴더일 수 있다) 수라서
            # 폴더 경로 바로 밑에 있으면 이 폴더의 개수처럼 읽혔다.
            msg = "하위 폴더까지 모두 검사합니다."
            if folder:
                n_files, capped = gerunner.count_files(folder, recursive=True)
                msg += (f" · 이 폴더에 **{n_files:,}개 이상**의 파일이 있습니다."
                        if capped else f" · 이 폴더에 **{n_files:,}개**의 파일이 있습니다.")
            st.caption(msg)
            # 지난 결과 건수는 '지난 결과'라고 분명히 못박아 따로 보여 준다.
            if records:
                st.caption(f"↳ 지난 분류 결과 파일에는 {len(records):,}개 문서가 담겨 있습니다"
                           " — 다시 분류하면 이 폴더 기준으로 새로 만들어집니다.")

    # ── ② 회사 분류 체계 ──
    with st.container(border=True):
        c1, c2 = st.columns([4, 1])
        c1.markdown(f"##### 2️⃣ 회사 분류 체계 {'✅ 연결됨' if tax else '⛔ 연결 안 됨'}")
        if tax:
            c1.caption(f"**{tax['node_count']}개 분류** · " + " · ".join(taxlib.roots(tax)))
            c1.caption(f"가져온 날짜 **{taxlib.exported_at_kr(tax)}** · "
                       "회사 관리 화면에서 분류를 바꾸면 다시 가져와야 반영됩니다")
            age = taxlib.age_days(tax)
            # 묵은 스냅샷을 조용히 쓰지 않도록 90일이 지나면 알린다(상위 설계 T13).
            if age is not None and age > 90:
                st.warning(f"가져온 지 **{age}일** 지났습니다. 회사 분류 체계가 바뀌었을 수 "
                           "있으니 다시 가져오는 것을 권합니다.")
        else:
            c1.caption("연결하면 “이 문서가 무엇에 관한 것인지”도 함께 정리됩니다. "
                       "연결하지 않으면 보안등급만 사용합니다.")
        # 연결 전에는 '연결', 연결된 뒤에는 '다시 가져오기' — 하는 일은 같다.
        if c2.button("🔗 연결" if not tax else "🔄 다시 가져오기",
                     type="primary" if not tax else "secondary",
                     width="stretch", key="tax_import",
                     help="회사에서 내보낸 파일을 읽어 분류 체계를 만듭니다"):
            import_taxonomy(paths)
        c1.caption("분류 체계의 원본은 MpowerV11 입니다 — 이 화면에서 고치면 두 곳이 "
                   "어긋나므로 여기서는 읽기만 합니다.")

        # 버튼이 무엇을 하는지 명령으로도 밝혀 둔다. 화면이 못 도는 상황(권한·경로
        # 문제)에서 관리자가 명령창에서 그대로 쳐 볼 수 있어야 하고, 무엇보다
        # "버튼이 몰래 무슨 일을 하는지 모르는" 상태를 만들지 않기 위해서다.
        # 실제로 실행되는 것과 같은 값을 보여준다 — 안내와 동작이 어긋나면
        # 그대로 따라 해도 안 맞는다.
        _base = gerunner.parse_base_cmd(paths.get("cmd"))
        st.caption("이 버튼이 실행하는 명령 — 명령창에서 직접 돌려도 결과는 같습니다")
        st.code(" ".join(_base or ["csoclassify"])
                + f" --export-taxonomy"
                  f" --export-input {paths.get('export_input') or 'doc_classification_export.json'}"
                  f" --taxonomy {paths.get('taxonomy') or 'doc_taxonomy.yaml'}",
                language="text")

    # ── ③ 판단 기준 ──
    with st.container(border=True):
        st.markdown("##### 3️⃣ 판단 기준")
        which = st.segmented_control("어느 기준", [W.AXIS_SEC, W.AXIS_DOC],
                                     default=W.AXIS_SEC, key="set_rules_axis",
                                     label_visibility="collapsed")
        if which == W.AXIS_DOC:
            st.caption("“무엇을 계약서로 볼 것인가”를 정합니다. 신호가 여러 개면 "
                       "**해당하는 분류를 모두** 냅니다.")
            render_doc_rules_editor(paths["doc_rules"], tax)
        else:
            st.caption("“얼마나 조심할 문서인가”를 정합니다. 신호가 엇갈리면 "
                       "**더 엄격한 등급**이 적용됩니다.")
            render_rules_editor()

    # ── ④ 분류 실행 ──
    with st.container(border=True):
        st.markdown("##### 4️⃣ 분류 실행")
        render_run_panel(paths["result"], paths.get("folder"), tax)

    # ── 고급 ── (파일 경로·기준 문서·리포트 — 평소에는 열 일이 없다)
    with st.expander("고급 — 파일 위치 · 기준 문서 · 진단 리포트"):
        st.markdown("**파일 위치**")
        g1, g2 = st.columns(2)
        paths["result"] = g1.text_input("분류 결과", value=paths["result"], key="p_result")
        paths["override"] = g2.text_input("내가 고친 기록", value=paths["override"], key="p_ov")
        paths["seed"] = g1.text_input("기준 문서", value=paths["seed"], key="p_seed")
        paths["seed_audit"] = g2.text_input("기준 문서 변경 기록",
                                            value=paths["seed_audit"], key="p_audit")
        paths["rules"] = g1.text_input("보안등급 기준 파일", value=paths["rules"], key="p_rules")
        paths["taxonomy"] = g2.text_input("회사 분류 체계 파일",
                                          value=paths["taxonomy"], key="p_tax")
        paths["doc_rules"] = g1.text_input("업무분류 기준 파일",
                                           value=paths["doc_rules"], key="p_drules")
        paths["export_input"] = g2.text_input(
            "회사 분류 체계 원본(내보낸 JSON)", value=paths.get("export_input", ""),
            key="p_expin",
            help="②의 ‘다시 가져오기’ 안내에 쓰이는 MpowerV11 내보내기 파일입니다.")
        paths["cmd"] = g1.text_input("분류 실행 명령", value=paths["cmd"], key="p_cmd")
        # cmd 와 짝인 값이라 함께 둔다 — 소스로 실행할 때만 필요하고, exe 로 바꾸면
        # 비워도 된다. 저장되는 값이므로 고칠 수 있어야 한다.
        paths["pythonpath"] = g2.text_input(
            "PYTHONPATH", value=paths.get("pythonpath", ""), key="p_pypath",
            help="‘분류 실행 명령’을 python -m csoclassify 로 쓸 때 필요한 src 폴더입니다. "
                 "exe 로 실행하면 비워 두세요.")

        # 여기서 고친 값은 settings.yaml 에 바로 적어 둔다 — 다시 띄워도 그대로 쓰게.
        # (streamlit 은 화면을 건드릴 때마다 스크립트를 다시 돌리므로, 직전에 저장한
        #  내용과 다를 때만 쓴다.)
        status, snap = uisettings.save_if_changed(paths, st.session_state.get("paths_saved"))
        st.session_state["paths_saved"] = snap
        cfg_file = uisettings.settings_path()
        if status == "failed":
            st.warning(f"설정을 저장하지 못했습니다(쓰기 권한을 확인하세요): {cfg_file}")
        else:
            st.caption(f"여기서 고친 값은 **{cfg_file}** 에 저장되어 다음 실행 때 그대로 불러옵니다."
                       + (" · 방금 저장했습니다." if status == "saved" else ""))

        if st.button("기본값으로 되돌리기", key="p_reset",
                     help="경로를 처음 상태로 되돌리고 settings.yaml 에도 그대로 반영합니다."):
            d = default_paths()
            # 입력칸은 key 로 자기 값을 따로 들고 있어서, paths 만 바꾸면 화면이 안 바뀐다.
            # 위젯 상태를 지워야 새 기본값이 입력칸에 다시 그려진다.
            for k in ("p_result", "p_ov", "p_seed", "p_audit", "p_rules",
                      "p_tax", "p_expin", "p_drules", "p_cmd", "p_pypath", "set_folder"):
                st.session_state.pop(k, None)
            st.session_state["paths"] = d
            uisettings.save_paths(d)
            st.session_state["paths_saved"] = {k: (d.get(k) or "") for k in uisettings.KEYS}
            st.rerun()

        if records:
            st.divider()
            st.markdown("**기준 문서(‘이런 문서는 이 등급’ 본보기)**")
            render_seed_manager(records, latest, tax)
            st.divider()
            st.markdown("**진단 리포트**")
            render_report(df, records, LOW_CONF)


#------------------------------------------------------------------
# 회사 분류 체계 연결(가져오기) 실행
#=> [연결] / [다시 가져오기] 가 부르는 함수. 회사에서 내보낸 JSON 을 읽어
#   doc_taxonomy.yaml 을 만든다.
#    1) 원본 파일과 실행 명령이 있는지 먼저 확인한다 — 없으면 어디를 고쳐야
#       하는지까지 말해 준다(그냥 "실패"라고만 하면 손쓸 방법이 없다)
#    2) csoclassify --export-taxonomy 를 돌린다
#    3) 파일이 정말 만들어졌는지 확인하고, 실패하면 실행한 명령을 그대로 보여
#       준다 — 관리자가 명령창에서 같은 명령을 쳐 볼 수 있게
#
# -in: paths = 화면 경로 묶음(export_input · taxonomy · cmd · pythonpath 를 쓴다)
#
# -out: 없음(성공하면 화면을 새로 그린다)
# -out: error = 없음(실패는 화면에 이유와 함께 표시)
#------------------------------------------------------------------
def import_taxonomy(paths):
    src = paths.get("export_input") or ""
    out = paths.get("taxonomy") or ""
    if not os.path.isfile(src):
        st.error(f"회사에서 내보낸 파일을 찾을 수 없습니다: {src or '(경로 미설정)'}\n\n"
                 "MpowerV11 관리 화면에서 문서 분류 체계를 내보낸 뒤, "
                 "‘고급 · 파일 위치 ▸ 회사 분류 체계 원본’ 에 그 파일 경로를 넣어 주세요.")
        return
    if not out:
        st.error("만들 파일 경로가 비어 있습니다 — ‘고급 · 파일 위치 ▸ 회사 분류 체계 파일’ "
                 "을 채워 주세요.")
        return
    base = gerunner.parse_base_cmd(paths.get("cmd"))
    if not base:
        st.error("분류 실행 명령이 비어 있습니다 — ‘고급 · 실행 명령’ 을 먼저 채워 주세요.")
        return

    try:
        with st.spinner("회사 분류 체계를 가져오는 중…"):
            os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
            r = gerunner.run_export_taxonomy(base, src, out,
                                             pythonpath=paths.get("pythonpath") or None)
    except Exception as e:
        uierrlog.show_error(f"분류 체계 가져오기 실패: {e}", exc=e, where="분류 체계 연결")
        return

    if r.returncode != 0 or not os.path.isfile(out):
        st.error("분류 체계를 만들지 못했습니다.")
        st.caption("실행한 명령 — 명령창에서 그대로 쳐 보면 자세한 원인을 볼 수 있습니다")
        st.code(" ".join(r.args), language="text")
        if r.stderr.strip():
            st.caption(f"실행 로그: {r.stderr.strip()[-400:]}")
        uierrlog.log_error("분류 체계 가져오기 실패\n"
                           f"명령: {' '.join(r.args)}\nstderr:\n{r.stderr}",
                           where="분류 체계 연결")
        return

    # 새로 만든 파일을 곧바로 읽어 몇 개가 들어왔는지 알린다.
    tax = taxlib.load_taxonomy(out)
    n = tax.get("node_count") if tax else 0
    st.session_state["tax_flash"] = (f"회사 분류 체계를 연결했습니다 — {n}개 분류 · {out}")
    st.cache_data.clear()
    st.rerun()


#------------------------------------------------------------------
# 초기화 확인 창
#=> [초기화]를 누르면 열리는 대화상자. 되돌릴 수 없는 일이므로 세 가지를 지킨다.
#    1) 지울 파일을 '이름과 크기까지' 그대로 먼저 보여준다 — 무엇이 사라지는지
#       모르는 채로 누르게 하지 않는다
#    2) 남기는 것도 함께 밝힌다(소스 코드·회사 분류 체계 원본·기준 규칙)
#    3) 확인 체크를 해야만 지우는 버튼이 눌린다
#   화면이 보여 준 목록(plan_reset)과 실제로 지우는 목록이 같은 값이라, "보여 준
#   것과 다른 것이 지워지는" 사고가 구조적으로 생기지 않는다.
#
# -in: 없음(ui 폴더 위치는 이 파일 위치에서 구한다)
#
# -out: 없음(지운 뒤 캐시를 비우고 화면을 새로 그린다)
# -out: error = 없음(지우지 못한 파일은 화면에 이유와 함께 표시)
#------------------------------------------------------------------
@st.dialog("초기화 — 처음 상태로 되돌리기")
def reset_dialog():
    ui_dir = os.path.dirname(os.path.abspath(__file__))
    drop_gen = st.checkbox(
        "만들어진 분류 체계·업무분류 규칙(doc_taxonomy.yaml · doc_rule.yaml)도 지우기",
        value=True,
        help="회사 분류 체계 원본(doc_classification_export.json)과 "
             "doc_rule_template.yaml 로 다시 만들 수 있습니다")
    targets, kept = uireset.plan_reset(ui_dir, drop_generated=drop_gen)

    if not targets:
        st.success("지울 것이 없습니다 — 이미 처음 상태입니다.")
        return

    total = uireset.human_size(sum(t["size"] for t in targets))
    st.markdown(f"**지울 파일 {len(targets)}건 · {total}**")
    st.dataframe(
        pd.DataFrame([{"파일": os.path.relpath(t["path"], ui_dir),
                       "크기": uireset.human_size(t["size"]),
                       "무엇": t["why"]} for t in targets]),
        hide_index=True, width="stretch")

    st.caption("남는 것 — 소스 코드 · " + " · ".join(k["name"] for k in kept)
               + " · cso_rules.yaml · doc_rule_template.yaml · synonyms 폴더 · "
                 "화면 설정(settings.yaml)")
    st.warning("되돌릴 수 없습니다. 분류 결과와 기준 문서가 모두 사라지고, "
               "폴더를 다시 분류해야 합니다.")

    ok = st.checkbox("위 파일들을 지웁니다", key="reset_ok")
    if st.button("🗑 초기화", type="primary", disabled=not ok, key="reset_go"):
        deleted, failed = uireset.run_reset(targets)
        # 결과 파일을 읽어 둔 캐시가 남아 있으면, 파일이 없는데도 화면에 옛 문서가
        # 그대로 보인다 — 지운 직후에는 반드시 캐시를 비운다.
        st.cache_data.clear()
        st.session_state["reset_flash"] = (
            f"초기화 완료 — {len(deleted)}건을 지웠습니다"
            + (f" · 지우지 못함 {len(failed)}건" if failed else ""))
        if failed:
            st.session_state["reset_failed"] = failed
        st.rerun()


#------------------------------------------------------------------
# 메인 — 상단 관점 스위치 + 메뉴 4개 (설계서 5장)
#=> 화면 전체의 뼈대. 예전에는 탭 9개 + 사이드바 7칸이었는데, 매일 쓰는 화면 3개와
#   처음 1회 쓰는 설정 1개로 접었다.
#    1) 설정을 session_state 에 한 번만 만들어 화면들이 같은 값을 보게 한다
#    2) 상단에 관점 스위치(보안등급/업무분류/전체)와 검토자 이름을 고정 배치
#    3) 메뉴는 st.tabs 가 아니라 segmented_control 로 둔다 — 그래야 "검토 시작"
#       같은 버튼이 화면을 옮길 수 있고, 한 번에 한 화면만 그려 빠르다
#    4) 결과 파일이 없으면 설정만 열어 준다(처음 실행 흐름, 설계서 5-2)
#
# -in: 없음
# -out: 없음(Streamlit 앱)
# -out: error = 결과 로드 실패 시 화면에 안내하고 멈춘다
#------------------------------------------------------------------
def main():
    st.set_page_config(page_title="문서자동분류", page_icon="🗂️", layout="wide")

    # 현황 화면의 숫자 칸(st.metric) 글씨 줄이기.
    # 기본 2.25rem 은 다섯 칸을 나란히 놓으면 "2026-08-25 16:41" 같은 값이 잘려
    # "2026-08-25 16…" 로 나온다. 숫자는 조금 작아도 충분히 읽히지만, 잘린 값은
    # 아예 못 읽는다 — 그래서 폰트를 줄여 값이 온전히 보이는 쪽을 택한다.
    st.markdown("""
        <style>
        [data-testid="stMetricValue"] { font-size: 1.6rem; }
        [data-testid="stMetricLabel"] p { font-size: 0.85rem; }
        </style>
    """, unsafe_allow_html=True)

    # 설정값은 한 번만 만들고 이후에는 session_state 의 것을 계속 쓴다.
    #   기본값(default_paths) 위에 settings.yaml 에 저장해 둔 값을 덮어 시작한다 —
    #   그래야 '고급 · 파일 위치' 에서 고친 경로가 다시 띄워도 그대로 남는다.
    if "paths" not in st.session_state:
        loaded = uisettings.load_paths(default_paths())
        st.session_state["paths"] = loaded
        # 지금 값이 곧 '저장된 상태'다. 이걸 기준으로 나중에 바뀐 것만 다시 쓴다.
        # 단 파일이 아직 없으면 None 을 넣어, 설정 화면을 처음 열 때 기본값 그대로
        # 한 번 써 두게 한다 — 파일이 실제로 있어야 관리자가 열어 보고 손댈 수 있다.
        st.session_state["paths_saved"] = (
            {k: (loaded.get(k) or "") for k in uisettings.KEYS}
            if os.path.isfile(uisettings.settings_path()) else None)
    paths = st.session_state["paths"]
    st.session_state.setdefault("menu", MENU_HOME)
    # 예약된 화면 이동을 '위젯을 만들기 전에' 반영한다 — 아래 두 줄 순서가 중요하다.
    apply_pending_nav()

    # ── 상단 고정 줄 ──
    # 예전에는 여기에 '관점(보안등급/업무분류/전체)' 스위치가 있었다. 없앤 이유:
    # 두 축은 한 문서에 대해 함께 판단해야 하는 것이라, 관점을 고르게 하면 한쪽을
    # 보는 동안 다른 쪽이 보이지 않아 같은 문서를 두 번 열게 된다. 이제 모든 화면이
    # 두 축을 나란히 보여준다.
    # 메뉴를 제목과 검토자 이름 사이에 둔다 — 제목·이동·작성자가 한 줄에 모여
    # 화면 위쪽 두 줄을 한 줄로 줄인다(표가 그만큼 위로 올라온다).
    # vertical_alignment="bottom": 제목·입력칸 높이가 서로 달라도 아랫줄을 맞춘다.
    t1, t2, t3, t4 = st.columns([3, 5, 2, 1], vertical_alignment="bottom")
    t1.markdown("### 🗂️ 문서자동분류")
    # 메뉴는 '검토함 · N' 의 N 을 알아야 그릴 수 있는데 그 수는 결과 파일을 읽어야
    # 나온다. 자리만 먼저 잡아 두고 아래(결과를 읽은 뒤)에서 채운다.
    menu_slot = t2.empty()
    reviewer = t3.text_input("검토자 이름", key="reviewer",
                             placeholder="기록에 남습니다")
    # 초기화는 되돌릴 수 없다 — 누르면 곧바로 지우지 않고 확인 창을 먼저 연다.
    if t4.button("초기화", key="reset_open", width="stretch",
                 help="분류 결과·기준 문서 등 앱이 만든 데이터를 지우고 처음 상태로 되돌립니다"):
        reset_dialog()

    # 분류 체계 연결 결과도 rerun 을 지나 살아남아야 한다.
    _tax_flash = st.session_state.pop("tax_flash", None)
    if _tax_flash:
        st.success(_tax_flash)

    # 초기화 결과는 rerun 을 지나 살아남아야 한다(지운 뒤 화면을 새로 그리므로).
    _flash = st.session_state.pop("reset_flash", None)
    if _flash:
        st.success(_flash)
        for _f, _why in st.session_state.pop("reset_failed", []):
            st.error(f"지우지 못했습니다: {os.path.basename(_f)} — {_why} "
                     "(다른 프로그램이 열고 있는지 확인하세요)")

    # 하위 화면들이 참조할 설정을 세션에 보관(기준 문서 승격·분류 실행에 쓴다).
    st.session_state["seedcfg"] = {
        "seed_path": paths["seed"], "audit_path": paths["seed_audit"],
        "csoclassify_cmd": paths["cmd"], "pythonpath": paths["pythonpath"] or None,
        "reviewer": reviewer or "",
    }
    st.session_state["rulescfg"] = {
        "rules_path": paths["rules"],
        "src_path": os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "src")),
    }

    # 회사 분류 체계는 '있으면 켜지고 없으면 꺼지는 외장 자산'이다(상위 설계 4-6).
    tax = taxlib.load_taxonomy(paths["taxonomy"])
    doctype_on = tax is not None

    grades_path = paths["result"]
    have_result = bool(grades_path) and os.path.isfile(grades_path)

    # ── 결과가 아직 없으면: 설정만 ──
    if not have_result:
        st.info("아직 분류한 문서가 없습니다. 아래에서 폴더를 고르고 **분류 시작**을 누르세요.")
        render_settings(paths, tax, [], None, {})
        return

    try:
        records = load_records(grades_path, os.path.getmtime(grades_path))
    except Exception as e:
        uierrlog.show_error(f"분류 결과를 읽지 못했습니다: {e}", exc=e, where="결과 파일 읽기")
        return

    latest, history = load_overrides(paths["override"])
    latest_dt, history_dt = doctype_review.load_doctype_overrides(paths["override"])
    # 기준 문서 목록은 화면마다 다시 읽지 않고 여기서 한 번만 읽어 표에 실어 둔다.
    seed_files = seedstore.load_seed_files(paths.get("seed"))
    df = records_to_df(records, latest, latest_dt, tax, seed_files)
    run_meta = load_run_meta(grades_path)

    # ── 메뉴 ── 검토함에는 남은 건수를 붙여, 열지 않고도 할 일이 있는지 보이게 한다.
    todo_n = int((df["need_sec"] | df["need_doc"]).sum())
    labels = {MENU_HOME: MENU_HOME, MENU_BOX: MENU_BOX,
              MENU_INBOX: f"{MENU_INBOX} · {todo_n}" if todo_n else MENU_INBOX,
              MENU_SET: MENU_SET}
    # 위에서 잡아 둔 제목 옆자리에 그린다. st.empty() 는 한 요소만 담으므로
    # container() 로 감싼다 — 나중에 안내 문구를 덧붙일 여지도 남는다.
    with menu_slot.container():
        menu = st.segmented_control(
            "메뉴", list(labels), key="menu",
            format_func=lambda m: labels[m], label_visibility="collapsed") or MENU_HOME

    # 업무분류를 안 쓰는 배포에서는 기능을 숨기지 않고 '꺼짐'을 알린다(설계서 12-1).
    if not doctype_on and menu in (MENU_HOME, MENU_BOX):
        b1, b2 = st.columns([5, 1])
        b1.warning(f"{W.AXIS_DOC}는 아직 사용하지 않습니다 — 회사 분류 체계를 연결하면 "
                   "“이 문서가 무엇에 관한 것인지”도 함께 정리됩니다.")
        if b2.button("설정에서 연결 →", key="go_set"):
            goto(menu=MENU_SET)

    if menu == MENU_HOME:
        render_home(df, doctype_on, run_meta)
    elif menu == MENU_BOX:
        render_docbox(df, records, latest, history, latest_dt, history_dt,
                      paths["override"], reviewer or "", tax)
    elif menu == MENU_INBOX:
        render_inbox(df, records, latest, history, latest_dt, history_dt,
                     paths["override"], reviewer or "", tax)
    else:
        render_settings(paths, tax, records, df, latest)


# Streamlit 은 이 파일을 __main__ 으로 실행한다. 가드를 두어 테스트 시 import 로
# 함수만 가져다 쓸 수 있게 한다(그때는 main 이 안 돈다).
if __name__ == "__main__":
    # 화면 어디서 예외가 터지든 파일(csoclassify_err_YYYYMMDD.log)에 스택까지 남긴다.
    # Streamlit 은 예외를 자기가 잡아 브라우저에만 그리기 때문에, 감싸 두지 않으면
    # 원격에 띄운 화면에서는 무슨 일이 있었는지 볼 방법이 없다.
    uierrlog.install_hooks()
    uierrlog.run_guarded(main)
