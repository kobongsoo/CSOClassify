#------------------------------------------------------------------
# 기준 문서 저장소 관리 (class_seed.jsonl) — 순수 로직 · 스키마 v2
#=> 분류 검토 UI 가 쓰는 "큐레이션된 기준 문서 저장소"의 입출력·편집·감사를 담당한다.
#   UI(Streamlit) 위젯과 분리해, 이 모듈만 단위테스트할 수 있게 한다.
#    - class_seed.jsonl      : 현재 기준 문서 상태(추가/수정/삭제로 갱신, 통째 저장)
#    - class_seed_audit.jsonl: 변경 이력(append-only, 누가·언제·어느 축을·무엇에서 무엇으로)
#
#   [스키마 v2 — 두 축(보안등급·업무분류)을 한 줄에] 설계서 '기준문서-두축-seed저장소'
#   한 문서 = 한 줄 = 벡터 하나다. 그 한 줄이 두 축을 나눠 쓴다.
#     axes.security = {state, value:"C"/"S"/"O", ...}   ← 진실
#     axes.doctype  = {state, value:[dc_id,...],  ...}   ← 진실
#     grade / labels.security / labels.doctype          ← axes 로부터 만든 파생값
#   엔진(Python·Rust 의 SeedIndex/DoctypeSeedIndex)은 파생값만 읽는다. 그래서
#   파생값의 '모양'은 절대 바꾸지 않는다 — labels.doctype 을 배열이 아닌 것으로
#   바꾸면 Rust 가 그 줄을 에러 없이 통째로 건너뛴다(기준 문서가 조용히 사라진다).
#   새로 얹는 정보(축별 이력·상태·원본 지문)는 전부 엔진이 무시하는 키에 넣는다.
#------------------------------------------------------------------

import json
import os
import datetime

import numpy as np


# 저장 파일의 스키마 판. 행마다 v 로 적어 둔다(없으면 옛 행으로 보고 자동 승격).
SCHEMA_VERSION = 2

# 이 저장소가 다루는 두 축. 순서는 화면 표기 순서이기도 하다.
AXES = ("security", "doctype")
AXIS_LABEL = {"security": "보안등급", "doctype": "업무분류"}

# 축의 상태값 3가지.
#   active  = 관리자가 확정한 기준 → 전파에 쓰인다
#   retired = 관리자가 이 축을 해제 → 안 쓰인다(이력만 남는다)
#   suspect = 시스템이 의심 표시(원본 변경·원본 없음·유령 분류코드) → 안 쓰인다
STATE_ACTIVE = "active"
STATE_RETIRED = "retired"
STATE_SUSPECT = "suspect"

# 기준 문서를 '등록'할 때 사본이라고 경고하는 문턱값.
# 엔진 전파의 사본 상속 문턱(0.950)과는 다른 판단이라 값이 달라도 틀린 게 아니다.
# 이름을 갈라 둔 이유가 그것이다(propagate 쪽은 PROPAGATE_DUP_THRESHOLD).
SEED_DUP_THRESHOLD = 0.985

# 두 문서가 사실상 같은데 등급이 서로 다른지 볼 때 쓰는 문턱값(점검 화면).
CONFLICT_SIM = 0.97


#------------------------------------------------------------------
# 현재 시각 ISO 문자열
#=> 기준 문서 항목·감사 로그에 찍을 지역시간 ISO8601(초) 문자열을 만든다.
#
# -in: 없음
#
# -out: str = 예 "2026-08-28T10:00:00+09:00"
# -out: error = 없음
#------------------------------------------------------------------
def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


#------------------------------------------------------------------
# 문서 경로를 비교용 키로 정규화
#=> 같은 문서가 "d:/sample/a.doc" 와 "D:\sample\A.doc" 처럼 다르게 적히면
#   기준 문서 저장소에 같은 문서가 두 줄로 앉는다(전파 때 한 문서가 두 번 투표한다).
#   비교할 때만 표기를 통일한다 — 저장되는 값은 원래 표기 그대로 둔다
#   (원본 표기가 곧 감사 기록이라서).
#
# -in: path = 문서 경로
#
# -out: str = 소문자 + 슬래시로 통일한 비교용 키
# -out: error = 없음
#------------------------------------------------------------------
def norm_file(path):
    return str(path or "").replace("\\", "/").rstrip("/").lower()


#------------------------------------------------------------------
# 빈 축 블록 만들기
#=> axes 아래에 들어갈 한 축의 기본 모양이다. 한곳에서만 만들어야 키가 빠진
#   축 블록이 생기지 않는다.
#
# -in: value = 이 축의 값(security 는 "C"/"S"/"O", doctype 은 dc_id 리스트)
# -in: state = 축 상태(기본 active)
#
# -out: dict = 축 블록
# -out: error = 없음
#------------------------------------------------------------------
def _blank_axis(value=None, state=STATE_ACTIVE):
    return {"state": state, "value": value, "source": "", "approved_by": "",
            "ts": "", "note": ""}


#------------------------------------------------------------------
# 옛 행(v 없음)을 v2 로 끌어올리기
#=> 손으로 만든 줄이나 다른 도구가 쓴 줄이 섞여 들어와도 화면이 같은 모양으로
#   다루게 한다. 파생값(grade · labels.doctype)에서 axes 를 거꾸로 만들어 준다.
#    1) 이미 axes 가 있으면 빠진 축만 채운다
#    2) 없으면 grade → axes.security, labels.doctype → axes.doctype 으로 역산
#    3) 마지막에 project() 로 파생값을 다시 계산해 앞뒤를 맞춘다
#
# -in: entry = 저장소에서 읽은 한 줄(dict)
#
# -out: dict = v2 모양으로 맞춘 새 dict(원본은 건드리지 않는다)
# -out: error = 없음
#------------------------------------------------------------------
def ensure_v2(entry):
    e = dict(entry or {})
    axes = dict(e.get("axes") or {})

    # 옛 행: 맨 위 grade 가 곧 보안축의 확정값이었다.
    if "security" not in axes:
        g = e.get("grade") or (e.get("labels") or {}).get("security")
        if g in ("C", "S", "O"):
            ax = _blank_axis(g)
            ax.update({"source": e.get("source", ""), "approved_by": e.get("approved_by", ""),
                       "ts": e.get("ts", ""), "note": e.get("note", "")})
            axes["security"] = ax

    # 옛 행: labels.doctype 배열이 곧 업무분류축의 확정값이었다.
    if "doctype" not in axes:
        dc = (e.get("labels") or {}).get("doctype")
        if isinstance(dc, (list, tuple)) and dc:
            ax = _blank_axis([str(x) for x in dc])
            ax.update({"source": e.get("source", ""), "approved_by": e.get("approved_by", ""),
                       "ts": e.get("ts", ""), "note": e.get("note", "")})
            axes["doctype"] = ax

    # 축 블록에 키가 빠져 있으면 기본값으로 메운다(화면이 .get 지옥에 빠지지 않게).
    for name, ax in list(axes.items()):
        base = _blank_axis()
        base.update(ax if isinstance(ax, dict) else {})
        axes[name] = base

    e["axes"] = axes
    e["v"] = SCHEMA_VERSION
    return project(e)


#------------------------------------------------------------------
# axes → 엔진이 읽는 파생값 다시 만들기 (투영)
#=> 저장 직전에 반드시 통과시키는 함수. 이 함수 말고 어떤 코드도 grade 나
#   labels 를 직접 쓰지 않는다 — 진실은 axes 한 곳뿐이어야 앞뒤가 어긋나지 않는다.
#    1) 보안축이 active 면 grade 와 labels.security 를 쓴다
#    2) 업무분류축이 active 면 labels.doctype(배열)을 쓴다
#    3) 그 외에는 해당 키를 아예 지운다 — 빈 값으로 두지 않는다.
#       "값이 없다"와 "축을 해제했다"를 사람이 구분할 수 있어야 하기 때문이다
#
# -in: entry = axes 가 채워진 한 줄
#
# -out: dict = 파생값이 갱신된 새 dict
# -out: error = 없음
#------------------------------------------------------------------
def project(entry):
    e = dict(entry or {})
    axes = e.get("axes") or {}
    # 두 축이 쓰는 키만 새로 만들고, 남이 넣어 둔 다른 labels 키는 보존한다.
    labels = {k: v for k, v in (e.get("labels") or {}).items() if k not in AXES}

    sec = axes.get("security") or {}
    if sec.get("state") == STATE_ACTIVE and sec.get("value") in ("C", "S", "O"):
        e["grade"] = sec["value"]
        labels["security"] = sec["value"]
    else:
        e.pop("grade", None)

    dt = axes.get("doctype") or {}
    dc_ids = [str(x) for x in (dt.get("value") or []) if x]
    if dt.get("state") == STATE_ACTIVE and dc_ids:
        labels["doctype"] = dc_ids

    if labels:
        e["labels"] = labels
    else:
        e.pop("labels", None)
    return e


#------------------------------------------------------------------
# 저장할 때 키 순서 정리
#=> 벡터(384개 숫자)가 앞에 오면 사람이 파일을 열었을 때 아무것도 못 읽는다.
#   읽을 값들을 앞에 두고 벡터를 맨 뒤로 보낸다(동작에는 영향이 없다).
#
# -in: entry = 저장할 한 줄
#
# -out: dict = 키 순서를 정리한 새 dict
# -out: error = 없음
#------------------------------------------------------------------
def _ordered(entry):
    head = ("v", "file", "grade", "labels", "axes", "doc", "embed",
            "source", "approved_by", "ts", "note")
    out = {}
    for k in head:
        if k in entry:
            out[k] = entry[k]
    for k, v in entry.items():
        if k not in out and k != "vector":
            out[k] = v
    if "vector" in entry:
        out["vector"] = entry["vector"]
    return out


#------------------------------------------------------------------
# 기준 문서 저장소 로드
#=> class_seed.jsonl 을 읽어 항목 리스트로 만든다. 없으면 빈 리스트.
#   읽으면서 옛 행은 v2 로 끌어올린다 — 화면 코드가 두 가지 모양을 신경 쓰지
#   않아도 되게 하려는 것이다(파일은 저장할 때 비로소 v2 로 바뀐다).
#
# -in: path = class_seed.jsonl 경로
#
# -out: list = 기준 문서 항목 dict 리스트
# -out: error = 깨진 줄은 건너뜀(예외 없음)
#------------------------------------------------------------------
def load_seeds(path):
    seeds = []
    if not path or not os.path.isfile(path):
        return seeds
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                seeds.append(ensure_v2(json.loads(line)))
            except json.JSONDecodeError:
                continue
    return seeds


#------------------------------------------------------------------
# 기준 문서로 등록된 파일 목록만 빠르게 읽기
#=> 문서함 표에 "이 문서가 기준 문서인가"를 표시하려고 매번 부르는 함수다.
#   load_seeds 는 문서마다 384차원 벡터까지 통째로 읽어 무겁다 — 여기서는
#   file 필드 하나만 있으면 되므로 벡터는 버리고 경로만 모은다.
#
# -in: path = class_seed.jsonl 경로
#
# -out: set = 정규화된 경로 집합(파일이 없으면 빈 집합)
# -out: error = 없음(깨진 줄은 건너뜀)
#------------------------------------------------------------------
def load_seed_files(path):
    files = set()
    if not path or not os.path.isfile(path):
        return files
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                f_ = json.loads(line).get("file")
            except json.JSONDecodeError:
                continue
            if f_:
                files.add(norm_file(f_))
    return files


#------------------------------------------------------------------
# 기준 문서 저장소 저장(통째 덮어쓰기)
#=> 편집된 리스트 전체를 class_seed.jsonl 에 다시 쓴다(현재 상태 = 이 파일).
#    1) 저장 직전에 project() 로 파생값을 다시 만든다 — axes 를 고쳐 놓고
#       grade 갱신을 잊는 사고를 구조적으로 없앤다
#    2) 두 축이 모두 '사람이 해제(retired)' 인 줄만 빼고 쓴다. 시스템이 붙인
#       '확인 필요(suspect)' 는 반드시 남긴다 — 사람이 확인해 되살릴 대상인데
#       파일에서 사라지면 되살릴 방법도, 무엇이 있었는지 알 방법도 없어진다
#   변경 감사는 append_seed_audit 로 별도로 남긴다.
#
# -in: path  = class_seed.jsonl 경로
# -in: seeds = 기준 문서 항목 리스트
#
# -out: int = 실제로 쓴 줄 수
# -out: error = 쓰기 실패 시 예외 전파
#------------------------------------------------------------------
def save_seeds(path, seeds):
    rows = []
    for s in seeds:
        e = project(ensure_v2(s))
        if not keepable_axes(e):
            continue
        rows.append(_ordered(e))
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


#------------------------------------------------------------------
# 경로로 항목 찾기(정규화 비교)
#=> 대소문자·슬래시만 다른 같은 문서를 놓치지 않는다.
#
# -in: seeds = 기준 문서 리스트
# -in: file  = 찾을 문서 경로
#
# -out: dict|None = 찾은 항목(없으면 None)
# -out: error = 없음
#------------------------------------------------------------------
def find_seed(seeds, file):
    key = norm_file(file)
    return next((s for s in seeds if norm_file(s.get("file")) == key), None)


#------------------------------------------------------------------
# 한 축의 상태 읽기
#=> 축 블록이 없으면 "없음"으로 본다(예외를 내지 않는다).
#
# -in: entry = 기준 문서 한 줄
# -in: axis  = "security" | "doctype"
#
# -out: str = "active"/"retired"/"suspect" 또는 ""(축 없음)
# -out: error = 없음
#------------------------------------------------------------------
def axis_state(entry, axis):
    return ((entry or {}).get("axes") or {}).get(axis, {}).get("state") or ""


#------------------------------------------------------------------
# 한 축의 값 읽기
#=> security 는 "C"/"S"/"O", doctype 은 dc_id 리스트를 돌려준다.
#
# -in: entry = 기준 문서 한 줄
# -in: axis  = "security" | "doctype"
#
# -out: str|list|None = 그 축의 값(축이 없으면 None)
# -out: error = 없음
#------------------------------------------------------------------
def axis_value(entry, axis):
    return ((entry or {}).get("axes") or {}).get(axis, {}).get("value")


#------------------------------------------------------------------
# 이 기준 문서를 확정한 사람들
#=> 표에 "누가 이 기준을 세웠나"를 보여 주려고 쓴다. 기준 문서는 사람이
#   책임지고 고른 본보기라, 나중에 결과가 이상할 때 물어볼 사람이 누구인지가
#   메모만큼이나 중요한 정보다.
#
#   [왜 축마다 따로 보나] 축이 둘이라 보안등급은 A 가, 업무분류는 B 가 확정한
#   경우가 실제로 생긴다. 맨 위 approved_by 는 '마지막에 손댄 사람' 하나뿐이라
#   그 경우 한 명이 가려진다. 그래서 축을 먼저 보고, 축에 없을 때만 맨 위 값을
#   쓴다(옛 형식으로 저장된 줄이 그렇다).
#
# -in: entry = 기준 문서 한 줄
#
# -out: tuple = 확정자 이름들(중복 없음, 축 순서 유지). 아무도 없으면 빈 튜플
# -out: error = 없음
#------------------------------------------------------------------
def approvers(entry):
    e = entry or {}
    out = []
    for a in AXES:
        who = (((e.get("axes") or {}).get(a) or {}).get("approved_by") or "").strip()
        if who and who not in out:
            out.append(who)
    # 축에 기록이 없는 옛 줄은 맨 위 값으로 메운다.
    if not out:
        who = str(e.get("approved_by") or "").strip()
        if who:
            out.append(who)
    return tuple(out)


#------------------------------------------------------------------
# 이 줄에서 실제로 쓰이는(active) 축 목록
#=> 전파에 쓰이는 축이 하나라도 있는지 판단하는 기준이다. 하나도 없으면
#   save_seeds 가 그 줄을 파일에서 뺀다.
#
# -in: entry = 기준 문서 한 줄
#
# -out: tuple = ("security",) / ("doctype",) / 둘 다 / 빈 튜플
# -out: error = 없음
#------------------------------------------------------------------
def active_axes(entry):
    out = []
    for a in AXES:
        ax = ((entry or {}).get("axes") or {}).get(a) or {}
        if ax.get("state") != STATE_ACTIVE:
            continue
        v = ax.get("value")
        # doctype 은 빈 배열이면 살아 있는 값이 없는 것과 같다.
        if a == "doctype" and not [x for x in (v or []) if x]:
            continue
        if a == "security" and v not in ("C", "S", "O"):
            continue
        out.append(a)
    return tuple(out)


#------------------------------------------------------------------
# 이 줄을 파일에 남겨 둘 이유가 있는 축 목록
#=> active(지금 쓰는 중) 뿐 아니라 suspect(시스템이 잠시 빼 둔 것)도 남긴다.
#   suspect 는 "사람이 확인해서 되살리거나 지울 대상" 이므로, 파일에서 사라지면
#   확인 자체가 불가능해진다. 사람이 직접 해제(retired)한 축만 버릴 수 있다.
#
# -in: entry = 기준 문서 한 줄
#
# -out: tuple = 남겨 둘 이유가 있는 축 이름들(비면 그 줄은 저장하지 않는다)
# -out: error = 없음
#------------------------------------------------------------------
def keepable_axes(entry):
    out = []
    for a in AXES:
        ax = ((entry or {}).get("axes") or {}).get(a) or {}
        if ax.get("state") in (STATE_ACTIVE, STATE_SUSPECT):
            out.append(a)
    return tuple(out)


#------------------------------------------------------------------
# 축 하나를 확정(추가·갱신) — 저장소로 들어가는 유일한 통로
#=> "이 문서의 이 축을 이 값으로 확정한다"를 한 함수로 모은다. 줄이 없으면
#   만들고, 있으면 그 줄의 해당 축만 갈아 끼운다(다른 축은 손대지 않는다).
#    1) 정규화 비교로 같은 문서 줄을 찾는다(표기만 다른 중복 줄을 막는다)
#    2) 그 축 블록을 새로 쓰고 누가·언제·어디서 왔는지 남긴다
#    3) 벡터·원본지문·임베딩정보가 새로 주어졌으면 함께 갱신한다
#    4) project() 로 엔진이 읽는 파생값을 다시 만든다
#
# -in: seeds    = 기존 리스트(변형하지 않고 새 리스트 반환)
# -in: file     = 문서 경로
# -in: axis     = "security" | "doctype"
# -in: value    = security 면 "C"/"S"/"O", doctype 이면 dc_id 리스트
# -in: reviewer = 승인자 이름
# -in: source   = 출처 태그("phase4"/"phase2"/"phase6"/"upload" 등)
# -in: note     = 메모(None 이면 기존 메모 유지)
# -in: state    = 축 상태(기본 active)
# -in: vector   = 문서 벡터(None 이면 기존 것 유지)
# -in: doc      = {"hash","size","mtime"} 원본 지문(None 이면 유지)
# -in: embed    = {"model","dim","ts"} 임베딩 정보(None 이면 유지)
#
# -out: list = 갱신된 리스트
# -out: error = 없음
#------------------------------------------------------------------
def set_axis(seeds, file, axis, value, reviewer, *, source="", note=None,
             state=STATE_ACTIVE, vector=None, doc=None, embed=None):
    key = norm_file(file)
    out, found = [], False
    for s in seeds:
        if norm_file(s.get("file")) != key:
            out.append(s)
            continue
        out.append(_apply_axis(ensure_v2(s), axis, value, reviewer, source, note,
                               state, vector, doc, embed))
        found = True
    if not found:
        base = {"v": SCHEMA_VERSION, "file": file, "axes": {}}
        out.append(_apply_axis(base, axis, value, reviewer, source, note,
                               state, vector, doc, embed))
    return out


#------------------------------------------------------------------
# 한 줄에 축 값을 실제로 얹는 내부 함수
#=> set_axis 가 "어느 줄인지" 를 정하고, 이 함수가 "그 줄을 어떻게 고칠지" 를 한다.
#
# -in: e        = 대상 줄(v2 로 맞춰진 상태)
# -in: 나머지    = set_axis 와 동일
#
# -out: dict = 고쳐진 새 줄(파생값까지 갱신됨)
# -out: error = 없음
#------------------------------------------------------------------
def _apply_axis(e, axis, value, reviewer, source, note, state, vector, doc, embed):
    e = dict(e)
    axes = dict(e.get("axes") or {})
    prev = dict(axes.get(axis) or _blank_axis())
    # doctype 은 항상 리스트로 정규화한다 — 문자열 하나가 들어와도 배열로 만든다.
    if axis == "doctype":
        if value is None:
            value = prev.get("value") or []
        elif isinstance(value, str):
            value = [value]
        value = [str(x) for x in value if x]
    prev.update({"state": state, "value": value, "ts": now_iso()})
    if source:
        prev["source"] = source
    if reviewer:
        prev["approved_by"] = reviewer
    if note is not None:
        prev["note"] = note
    # 의심 표시가 붙어 있던 축을 사람이 다시 확정하면 그 사유는 지운다.
    if state == STATE_ACTIVE:
        prev.pop("reason", None)
    axes[axis] = prev
    e["axes"] = axes

    if vector is not None:
        e["vector"] = vector
    if doc:
        e["doc"] = {**(e.get("doc") or {}), **doc}
    if embed:
        e["embed"] = {**(e.get("embed") or {}), **embed}
    elif vector is not None:
        # 모델 이름은 엔진이 아직 알려 주지 않는다. 차원만이라도 남겨 두면
        # 나중에 모델이 바뀌었을 때 '섞인 벡터'를 점검에서 잡아낼 수 있다.
        e["embed"] = {**(e.get("embed") or {}), "dim": len(vector), "ts": now_iso()}

    # 줄 전체의 '마지막 손댄 사람/시각'(축별 기록과 별개로 훑어보기용).
    e["ts"] = now_iso()
    if reviewer:
        e["approved_by"] = reviewer
    return project(e)


#------------------------------------------------------------------
# 축 하나 해제(retire)
#=> "업무분류만 기준에서 빼고 보안등급은 유지" 를 가능하게 하는 함수.
#   해제해도 값은 지우지 않는다(되살릴 수 있어야 하고, 무엇을 뺐는지도 남아야 한다).
#   두 축을 모두 사람이 해제하면 그 줄은 저장 시 파일에서 빠진다.
#
# -in: seeds = 기존 리스트
# -in: file  = 대상 문서
# -in: axis  = 해제할 축
#
# -out: (list, removed) = 갱신된 리스트, removed 는 이 줄이 통째로 빠지는지 여부
# -out: error = 없음
#------------------------------------------------------------------
def retire_axis(seeds, file, axis):
    key = norm_file(file)
    out, removed = [], False
    for s in seeds:
        if norm_file(s.get("file")) != key:
            out.append(s)
            continue
        e = ensure_v2(s)
        axes = dict(e.get("axes") or {})
        ax = dict(axes.get(axis) or _blank_axis())
        ax["state"] = STATE_RETIRED
        ax["ts"] = now_iso()
        axes[axis] = ax
        e["axes"] = axes
        e = project(e)
        removed = not keepable_axes(e)
        out.append(e)
    return out, removed


#------------------------------------------------------------------
# 축에 의심 표시 붙이기(suspect) — 시스템이 붙인다
#=> 원본이 바뀌었거나(stale) 원본을 못 찾거나(missing) 분류코드가 사라졌을 때
#   (orphan) 그 축을 전파에서 빼되 값은 그대로 둔다. 사람이 확인해 되살리거나
#   지운다 — 시스템은 절대 스스로 지우지 않는다.
#
# -in: seeds  = 기존 리스트
# -in: file   = 대상 문서
# -in: axis   = 대상 축
# -in: reason = 사유 코드("stale"/"missing"/"orphan"/"dim")
#
# -out: list = 갱신된 리스트
# -out: error = 없음
#------------------------------------------------------------------
def suspect_axis(seeds, file, axis, reason):
    key = norm_file(file)
    out = []
    for s in seeds:
        if norm_file(s.get("file")) != key:
            out.append(s)
            continue
        e = ensure_v2(s)
        axes = dict(e.get("axes") or {})
        ax = dict(axes.get(axis) or _blank_axis())
        # 이미 사람이 해제해 둔 축은 건드리지 않는다(사람의 판단이 우선).
        if ax.get("state") == STATE_RETIRED:
            out.append(e)
            continue
        ax["state"] = STATE_SUSPECT
        ax["reason"] = reason
        ax["ts"] = now_iso()
        axes[axis] = ax
        e["axes"] = axes
        out.append(project(e))
    return out


#------------------------------------------------------------------
# 의심 표시 해제(다시 기준으로 씀)
#=> 사람이 확인했거나 원본을 다시 읽어 갱신했을 때 active 로 되돌린다.
#
# -in: seeds = 기존 리스트
# -in: file  = 대상 문서
# -in: axis  = 대상 축
#
# -out: list = 갱신된 리스트
# -out: error = 없음
#------------------------------------------------------------------
def restore_axis(seeds, file, axis):
    key = norm_file(file)
    out = []
    for s in seeds:
        if norm_file(s.get("file")) != key:
            out.append(s)
            continue
        e = ensure_v2(s)
        axes = dict(e.get("axes") or {})
        ax = dict(axes.get(axis) or _blank_axis())
        ax["state"] = STATE_ACTIVE
        ax.pop("reason", None)
        ax["ts"] = now_iso()
        axes[axis] = ax
        e["axes"] = axes
        out.append(project(e))
    return out


#------------------------------------------------------------------
# 원본 지문·임베딩 정보만 갱신
#=> 등급·분류는 그대로 두고 "이 벡터가 어느 원본에서 나왔는가"만 새로 적는다.
#   [다시 읽어 갱신] 이 쓰는 함수다.
#
# -in: seeds  = 기존 리스트
# -in: file   = 대상 문서
# -in: vector = 새 벡터(None 이면 유지)
# -in: doc    = {"hash","size","mtime"}(None 이면 유지)
# -in: embed  = {"model","dim","ts"}(None 이면 벡터 길이로 자동 생성)
#
# -out: list = 갱신된 리스트
# -out: error = 없음
#------------------------------------------------------------------
def set_meta(seeds, file, *, vector=None, doc=None, embed=None):
    key = norm_file(file)
    out = []
    for s in seeds:
        if norm_file(s.get("file")) != key:
            out.append(s)
            continue
        e = ensure_v2(s)
        if vector is not None:
            e["vector"] = vector
        if doc:
            e["doc"] = {**(e.get("doc") or {}), **doc}
        if embed:
            e["embed"] = {**(e.get("embed") or {}), **embed}
        elif vector is not None:
            e["embed"] = {**(e.get("embed") or {}), "dim": len(vector), "ts": now_iso()}
        e["ts"] = now_iso()
        out.append(e)
    return out


#------------------------------------------------------------------
# 기준 문서 줄 통째 삭제
#=> 두 축을 함께 지운다. 경로는 정규화해서 비교한다 — 예전에는 원문 그대로
#   비교해서 "D:\a.doc 로 등록된 것을 d:/a.doc 로 지우면 아무 일도 안 일어나는데
#   성공한 것처럼 보이는" 버그가 있었다.
#
# -in: seeds = 기존 리스트
# -in: file  = 삭제할 문서 경로
#
# -out: list = 제거된 새 리스트
# -out: error = 없음
#------------------------------------------------------------------
def remove_seed(seeds, file):
    key = norm_file(file)
    return [s for s in seeds if norm_file(s.get("file")) != key]


#------------------------------------------------------------------
# 보안등급 기준 문서 추가/갱신 (set_axis 의 얇은 껍데기)
#=> 옛 이름을 그대로 쓰는 호출부가 많아 남겨 둔 편의 함수다.
#
# -in: seeds    = 기존 리스트
# -in: file     = 문서 경로
# -in: grade    = 등급("C"/"S"/"O")
# -in: vector   = 문서 벡터
# -in: reviewer = 승인자
# -in: source   = 출처("phase2"/"phase4"/"upload")
# -in: note     = 메모
# -in: doc      = 원본 지문(선택)
#
# -out: list = 갱신된 리스트
# -out: error = 없음
#------------------------------------------------------------------
def add_seed(seeds, file, grade, vector, reviewer, source="phase4", note="", doc=None):
    return set_axis(seeds, file, "security", grade, reviewer,
                    source=source, note=note, vector=vector, doc=doc)


#------------------------------------------------------------------
# 업무분류 기준 문서 추가/갱신 (set_axis 의 얇은 껍데기)
#=> 보안등급으로 먼저 등록된 줄이 있으면 그 줄에 업무분류축만 얹는다.
#   없으면 업무분류축만 있는 줄을 새로 만든다 — "보안등급은 안 정했지만
#   업무분류만 확정했다" 도 유효한 기준 문서다.
#
# -in: seeds    = 기존 리스트
# -in: file     = 문서 경로
# -in: dc_ids   = 확정된 dc_id 리스트
# -in: vector   = 문서 벡터
# -in: reviewer = 승인자
# -in: source   = 출처(기본 "phase6")
# -in: note     = 메모
# -in: doc      = 원본 지문(선택)
#
# -out: list = 갱신된 리스트
# -out: error = 없음
#------------------------------------------------------------------
def add_doctype_seed(seeds, file, dc_ids, vector, reviewer, source="phase6",
                     note="", doc=None):
    return set_axis(seeds, file, "doctype", list(dc_ids), reviewer,
                    source=source, note=note, vector=vector, doc=doc)


#------------------------------------------------------------------
# 보안등급만 바꾸기(표 편집용)
#=> 기준 문서 표에서 등급 칸만 고쳤을 때 쓴다. 업무분류축은 손대지 않는다.
#
# -in: seeds    = 기존 리스트
# -in: file     = 대상 문서
# -in: grade    = 새 등급
# -in: reviewer = 승인자(빈 문자열이면 기존 승인자 유지)
#
# -out: list = 갱신된 리스트
# -out: error = 없음
#------------------------------------------------------------------
def set_grade(seeds, file, grade, reviewer=""):
    return set_axis(seeds, file, "security", grade, reviewer)


#------------------------------------------------------------------
# 최근접 기준 문서 유사도(사본 판정용)
#=> 주어진 벡터와 기존 기준 문서들 사이 최대 코사인 유사도와 그 문서를 찾는다.
#   중복(정보 없는) 기준 문서 추가를 막기 위한 경고 근거.
#
# -in: vector       = 후보 문서 벡터(list/ndarray)
# -in: seeds        = 기존 리스트(각 벡터 보유)
# -in: exclude_file = 비교에서 뺄 문서 경로. 후보가 이미 저장소에 있으면
#                     자기 자신과 sim=1.000 이 나와 "무조건 중복"으로 오판되므로,
#                     승격 시 후보 자신을 넣어 자기비교를 배제한다
#
# -out: (max_sim, file) = 최대 유사도, 그 문서 경로(없으면 (0.0, None))
# -out: error = 없음
#------------------------------------------------------------------
def nearest_seed_sim(vector, seeds, exclude_file=None):
    key = norm_file(exclude_file) if exclude_file else None
    # 벡터가 있고, 자기 자신(같은 문서)이 아닌 것만 비교 대상으로.
    pairs = [(s.get("vector"), s.get("file")) for s in seeds
             if s.get("vector") and (key is None or norm_file(s.get("file")) != key)]
    if vector is None or not pairs:
        return 0.0, None
    vecs = [p[0] for p in pairs]
    files = [p[1] for p in pairs]
    v = np.asarray(vector, dtype=np.float32)
    n = np.linalg.norm(v)
    v = v / (n if n else 1.0)
    arr = np.asarray(vecs, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    sims = (arr / norms) @ v
    i = int(np.argmax(sims))
    return float(sims[i]), files[i]


#------------------------------------------------------------------
# 고신뢰 후보 일괄 등록(중복 자동 제외)
#=> 이번 분류에서 뽑은 "고신뢰 C/S 후보"들을 한 번에 등록하되, 정보량이 없는
#   사본(near-dup)은 자동으로 건너뛴다.
#    1) 후보를 순회하며: 벡터 없음/이미 등록됨/기존·직전추가와 사본이면 제외
#    2) 통과분만 add_seed 로 누적(뒤 후보는 앞서 추가된 것과도 중복 비교됨)
#   중복 판정은 경로를 정규화해서 한다 — 표기만 다른 같은 문서가 두 줄로 앉으면
#   전파에서 한 문서가 두 번 투표한다.
#
# -in: seeds      = 기존 리스트(변형하지 않고 새 리스트 반환)
# -in: candidates = [{file, grade, vector, doc?}] 후보들
# -in: reviewer   = 승인자(감사 기록용)
# -in: dup_thresh = 사본 판정 코사인 임계(기본 SEED_DUP_THRESHOLD)
# -in: source     = 출처 태그(기본 "phase2" = 배치 등록)
# -in: note       = 각 항목 메모
#
# -out: (new_seeds, added, stats)
#        new_seeds = 갱신된 리스트
#        added     = 실제 추가된 [{file, grade}]
#        stats     = {"added","dup","exist","novec"} 각 사유별 건수
# -out: error = 없음
#------------------------------------------------------------------
def bulk_add_confident(seeds, candidates, reviewer, dup_thresh=SEED_DUP_THRESHOLD,
                       source="phase2", note="일괄 등록"):
    out = list(seeds)
    existing = {norm_file(s.get("file")) for s in out}   # 이미 등록된 문서(정규화 비교)
    added = []
    dup = exist = novec = 0
    for c in candidates:
        f, g, v = c.get("file"), c.get("grade"), c.get("vector")
        if not v:                                # 벡터 없으면 기준 문서로 못 씀
            novec += 1
            continue
        if norm_file(f) in existing:             # 이미 저장소에 있음 → 건너뜀
            exist += 1
            continue
        # 자기 자신 제외하고, 기존+직전 추가분과의 최대 유사도로 사본 여부 판단.
        sim, _ = nearest_seed_sim(v, out, exclude_file=f)
        if sim >= dup_thresh:
            dup += 1
            continue
        out = add_seed(out, f, g, v, reviewer, source=source, note=note,
                       doc=c.get("doc"))
        existing.add(norm_file(f))
        added.append({"file": f, "grade": g})
    stats = {"added": len(added), "dup": dup, "exist": exist, "novec": novec}
    return out, added, stats


#------------------------------------------------------------------
# 등급별 기준 문서 개수
#=> 보안축이 살아 있는(active) 줄만 센다 — 해제·의심 표시된 것은 전파에 쓰이지
#   않으므로 균형 점검에서도 빼야 한다.
#
# -in: seeds = 리스트
#
# -out: dict = {"C":n, "S":n, "O":n}
# -out: error = 없음
#------------------------------------------------------------------
def grade_counts(seeds):
    counts = {"C": 0, "S": 0, "O": 0}
    for s in seeds:
        if "security" in active_axes(s):
            g = axis_value(s, "security")
            if g in counts:
                counts[g] += 1
    return counts


#------------------------------------------------------------------
# 축별 기준 문서 개수
#=> "보안 96 · 업무분류 74 (둘 다 42)" 를 만드는 집계다.
#
# -in: seeds = 리스트
#
# -out: dict = {"total","security","doctype","both","suspect","retired"}
# -out: error = 없음
#------------------------------------------------------------------
def axis_counts(seeds):
    out = {"total": len(seeds), "security": 0, "doctype": 0, "both": 0,
           "suspect": 0, "retired": 0}
    for s in seeds:
        act = active_axes(s)
        if "security" in act:
            out["security"] += 1
        if "doctype" in act:
            out["doctype"] += 1
        if len(act) == 2:
            out["both"] += 1
        for a in AXES:
            st = axis_state(s, a)
            if st == STATE_SUSPECT:
                out["suspect"] += 1
            elif st == STATE_RETIRED:
                out["retired"] += 1
    return out


#------------------------------------------------------------------
# 기준 문서 변경 감사 기록(append-only)
#=> 누가·언제·어느 축을·무엇에서 무엇으로 바꿨는지 class_seed_audit.jsonl 에 남긴다.
#   예전에는 detail 이 자유 문자열이라 나중에 기계가 읽을 수 없었다 — 축과
#   전후값을 각각 칸으로 분리했다. 줄을 지운 뒤에는 이 파일이 유일한 이력이다.
#
# -in: path     = class_seed_audit.jsonl 경로
# -in: action   = "add"|"update"|"retire"|"restore"|"suspect"|"delete"
# -in: file     = 대상 문서 경로
# -in: axis     = "security"|"doctype"|"-"(줄 전체)
# -in: before   = 바뀌기 전 값(없으면 None)
# -in: after    = 바뀐 뒤 값(없으면 None)
# -in: reviewer = 작업자 이름(시스템이 붙인 것이면 "system")
# -in: reason   = 사유 한 줄(선택)
#
# -out: 없음(append)
# -out: error = 쓰기 실패 시 예외 전파
#------------------------------------------------------------------
def append_seed_audit(path, action, file, axis="-", before=None, after=None,
                      reviewer="", reason=""):
    entry = {"ts": now_iso(), "action": action, "axis": axis, "file": file,
             "before": before, "after": after,
             "reviewer": reviewer, "reason": reason}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


#------------------------------------------------------------------
# 기준 문서가 업로드로 등록돼 원본 경로가 없는 줄인가
#=> '파일을 직접 올려 등록' 한 기준 문서는 file 에 파일 이름만 들어 있다.
#   이런 줄까지 "원본을 못 찾음" 으로 세면 점검 화면이 경고로 뒤덮여
#   진짜 사라진 문서를 못 보게 된다.
#
# -in: entry = 기준 문서 한 줄
#
# -out: bool = True 면 원본 경로가 애초에 없는 줄
# -out: error = 없음
#------------------------------------------------------------------
def _is_uploaded(entry):
    f = str(entry.get("file") or "")
    if os.path.isabs(f):
        return False
    return "/" not in f.replace("\\", "/")


#------------------------------------------------------------------
# 기준 문서 점검 — 무엇이 썩었는지 한 번에 센다
#=> 설계서 6장 '기준 문서 점검' 화면이 쓰는 계산. 판정은 전부 여기서 하고
#   화면은 세어 보여 주기만 한다(시스템은 표시만 하고, 지우는 것은 사람이 한다).
#    1) stale   : 저장해 둔 원본 지문(doc.hash)과 이번 분류 결과의 해시가 다름
#    2) missing : 원본 파일이 그 경로에 없음(업로드 등록분은 제외)
#    3) orphan  : 분류 체계에서 사라진 dc_id 를 들고 있음
#    4) novec   : 벡터가 없어 비교에 못 쓰임
#    5) dim     : 벡터 차원이 다수와 다름(임베딩 모델이 바뀐 흔적)
#    6) conflict: 사실상 같은 문서인데 등급이 서로 다름 → 전파가 mixed 로 죽는다
#
# -in: seeds      = 기준 문서 리스트
# -in: records    = 이번 분류 레코드 리스트(hash 비교용, 없으면 stale 판정 생략)
# -in: tax        = 회사 분류 체계(없으면 orphan 판정 생략)
# -in: conflict_sim = 등급 충돌로 볼 유사도 임계(기본 CONFLICT_SIM)
#
# -out: dict = {"counts":축별집계, "stale":[], "missing":[], "orphan":[],
#               "novec":[], "dim":[], "conflict":[], "suspect":[]}
# -out: error = 없음
#------------------------------------------------------------------
def check_seeds(seeds, records=None, tax=None, conflict_sim=CONFLICT_SIM):
    rep = {"counts": axis_counts(seeds), "stale": [], "missing": [], "orphan": [],
           "novec": [], "dim": [], "conflict": [], "suspect": []}

    # 결과 레코드를 경로로 찾아 쓸 수 있게 정리(해시 비교용).
    by_file = {}
    for r in (records or []):
        by_file[norm_file(r.get("file"))] = r

    dims = {}
    for s in seeds:
        f = s.get("file")
        v = s.get("vector")
        if not v:
            rep["novec"].append(f)
        else:
            dims[norm_file(f)] = len(v)

        # 원본 지문 대조 — 양쪽 다 있을 때만 판정한다(모르면 조용히 넘어간다).
        seed_hash = (s.get("doc") or {}).get("hash")
        rec = by_file.get(norm_file(f))
        now_hash = (rec or {}).get("hash")
        if seed_hash and now_hash and seed_hash != now_hash:
            rep["stale"].append({"file": f, "before": seed_hash, "after": now_hash})

        if not _is_uploaded(s) and not os.path.isfile(str(f or "")):
            rep["missing"].append(f)

        # 이미 해제·확인필요로 빠져 있는 축은 분류에 영향을 주지 않는다. 그걸 계속
        # '없는 분류코드'로 세면, 정리를 끝낸 뒤에도 경고가 영영 사라지지 않는다
        # (그 줄은 아래 suspect 목록에서 사람이 처리한다).
        if tax and axis_state(s, "doctype") == STATE_ACTIVE:
            ids = [x for x in (axis_value(s, "doctype") or []) if x]
            gone = [x for x in ids if x not in (tax.get("by_id") or {})]
            if gone:
                rep["orphan"].append({"file": f, "dc_ids": gone})

        for a in AXES:
            if axis_state(s, a) == STATE_SUSPECT:
                rep["suspect"].append({"file": f, "axis": a,
                                       "reason": ((s.get("axes") or {}).get(a) or {}).get("reason", "")})

    # 벡터 차원이 다수와 다른 줄 — 임베딩 모델을 갈아끼운 흔적이다.
    if dims:
        common = max(set(dims.values()), key=list(dims.values()).count)
        for s in seeds:
            d = dims.get(norm_file(s.get("file")))
            if d and d != common:
                rep["dim"].append({"file": s.get("file"), "dim": d, "common": common})

    rep["conflict"] = _conflicting_pairs(seeds, conflict_sim)
    return rep


#------------------------------------------------------------------
# 사실상 같은데 등급이 다른 기준 문서 쌍 찾기
#=> 유사도 0.97 인 두 문서가 하나는 C, 하나는 O 로 등록돼 있으면 그 근처 문서는
#   전부 mixed 로 떨어져 전파가 사실상 꺼진다. 그 쌍을 찾아 준다.
#   문서 수가 많아도 감당되도록 행렬 한 번으로 계산한다.
#
# -in: seeds = 기준 문서 리스트
# -in: thr   = 같은 문서로 볼 유사도 임계
#
# -out: list = [{"a","b","sim","ga","gb"}] 유사도 내림차순(최대 50쌍)
# -out: error = 없음
#------------------------------------------------------------------
def _conflicting_pairs(seeds, thr):
    rows = [(s.get("file"), axis_value(s, "security"), s.get("vector"))
            for s in seeds
            if s.get("vector") and "security" in active_axes(s)]
    if len(rows) < 2:
        return []
    arr = np.asarray([r[2] for r in rows], dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    arr = arr / norms
    sims = arr @ arr.T
    out = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            s = float(sims[i, j])
            if s >= thr and rows[i][1] != rows[j][1]:
                out.append({"a": rows[i][0], "b": rows[j][0], "sim": s,
                            "ga": rows[i][1], "gb": rows[j][1]})
    out.sort(key=lambda d: -d["sim"])
    return out[:50]


#------------------------------------------------------------------
# 분류 결과를 보고 기준 문서에 의심 표시 붙이기(자동 동기화)
#=> 분류를 돌리면 어차피 전 문서를 훑으므로, 그 결과와 기준 문서를 대조하는 일은
#   공짜다. 원본이 바뀌었거나 사라진 기준 문서에 표시만 붙인다.
#   [중요] 절대 지우지 않는다. 기준 문서 한 건이 잘못 사라지면 그와 비슷한 문서
#   수십 건의 분류가 함께 흔들리므로, 오탐으로 지우는 쪽이 훨씬 비싸다.
#    1) 저장된 지문과 이번 해시가 다르면 두 축 모두 suspect(stale)
#    2) 원본이 없으면 suspect(missing) — 업로드 등록분은 제외
#    3) 지문이 다시 같아졌으면(사람이 되돌려 놓았으면) 표시를 푼다
#
# -in: seeds   = 기준 문서 리스트
# -in: records = 이번 분류 레코드 리스트(hash 포함되어야 판정 가능)
#
# -out: (new_seeds, events) = 갱신된 리스트, events 는
#        [{"file","axis","action","reason"}] (감사 로그로 남길 목록)
# -out: error = 없음
#------------------------------------------------------------------
def sync_with_records(seeds, records):
    by_file = {norm_file(r.get("file")): r for r in (records or [])}
    out, events = list(seeds), []
    for s in list(seeds):
        f = s.get("file")
        seed_hash = (s.get("doc") or {}).get("hash")
        rec = by_file.get(norm_file(f))
        now_hash = (rec or {}).get("hash")
        missing = (not _is_uploaded(s)) and (not os.path.isfile(str(f or "")))
        stale = bool(seed_hash and now_hash and seed_hash != now_hash)

        for a in AXES:
            st = axis_state(s, a)
            if st not in (STATE_ACTIVE, STATE_SUSPECT):
                continue          # 사람이 해제한 축은 건드리지 않는다
            reason = "missing" if missing else ("stale" if stale else "")
            cur_reason = ((s.get("axes") or {}).get(a) or {}).get("reason", "")
            if reason and (st != STATE_SUSPECT or cur_reason != reason):
                out = suspect_axis(out, f, a, reason)
                events.append({"file": f, "axis": a, "action": "suspect", "reason": reason})
            elif not reason and st == STATE_SUSPECT and cur_reason in ("stale", "missing"):
                # 원본이 제자리로 돌아왔다 → 표시를 풀어 다시 기준으로 쓴다.
                out = restore_axis(out, f, a)
                events.append({"file": f, "axis": a, "action": "restore", "reason": cur_reason})
    return out, events
