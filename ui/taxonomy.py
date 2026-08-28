#------------------------------------------------------------------
# 회사 분류 체계(doc_taxonomy.yaml) 읽기 — 순수 로직
#=> 화면이 "이 회사에 어떤 업무분류가 있는가"를 알아야 하는 곳이 세 군데다.
#     ① 설정 화면의 '회사 분류 체계' 블록(몇 개 분류인지·언제 가져왔는지)
#     ② 문서 상세의 '직접 추가'(기계가 제안하지 않은 분류를 사람이 고를 때)
#     ③ 문서함의 업무분류 필터(대분류로 좁히기)
#   이 세 곳이 각자 YAML 을 파싱하지 않도록 여기 한 곳에 모은다.
#
#   [원칙] 이 모듈은 파일을 읽기만 한다. 분류 체계의 진실 출처는 MpowerV11 의
#   DOC_CLASSIFICATION 이고, doc_taxonomy.yaml 은 거기서 내보낸 스냅샷이다
#   (상위 설계 4-1). 그래서 여기에 저장·수정 함수는 두지 않는다.
#------------------------------------------------------------------

import datetime
import os

import yaml


#------------------------------------------------------------------
# 분류 체계 스냅샷 로드
#=> doc_taxonomy.yaml 을 읽어 화면이 바로 쓸 수 있는 형태로 바꾼다.
#    1) YAML 의 taxonomy.nodes 목록을 dc_id 로 찾을 수 있는 사전으로 만든다
#    2) 각 노드의 전체경로("법무/규정 > 계약서")를 미리 계산해 넣는다 —
#       화면은 언제나 경로로 보여주기 때문이다(상위 설계 4-4)
#
# -in: path = doc_taxonomy.yaml 경로(없거나 깨졌으면 None 반환)
#
# -out: dict|None = {"source":…, "exported_at":…, "node_count":N,
#                    "by_id": {dc_id: {dc_id,parent,title,status,path}},
#                    "path": 읽은 파일 경로}
# -out: error = 파일 없음·파싱 실패 시 None(예외를 올리지 않는다 —
#               "분류 체계가 없으면 업무분류를 끈다"가 정상 동작이라서)
#------------------------------------------------------------------
def load_taxonomy(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return None

    tax = doc.get("taxonomy") or {}
    nodes = tax.get("nodes") or []
    by_id = {}
    for n in nodes:
        dc_id = n.get("dc_id")
        if dc_id:
            by_id[dc_id] = dict(n)

    # 전체경로는 부모를 따라 올라가며 만든다. 부모가 스냅샷에 없으면(꼬인 데이터)
    # 거기서 멈춘다 — 무한 반복을 막으려고 방문한 노드를 기억한다.
    for dc_id, node in by_id.items():
        titles, cur, seen = [], node, set()
        while cur and cur.get("dc_id") not in seen:
            seen.add(cur.get("dc_id"))
            titles.append(cur.get("title") or cur.get("dc_id") or "?")
            cur = by_id.get(cur.get("parent"))
        node["path"] = " > ".join(reversed(titles))

    return {
        "source": tax.get("source", ""),
        "exported_at": str(tax.get("exported_at") or ""),
        "node_count": tax.get("node_count") or len(by_id),
        "by_id": by_id,
        "path": path,
    }


#------------------------------------------------------------------
# dc_id → 전체경로 문자열
#=> 규칙 파일과 결과 레코드는 DC_ID 로만 적혀 있어 사람이 읽을 수 없다.
#   화면에는 언제나 경로로 보여준다(상위 설계 4-3 — "규칙 편집기와 검증 보고문은
#   항상 경로를 함께 보여준다").
#
# -in: tax   = load_taxonomy() 결과(None 이면 dc_id 를 그대로 돌려준다)
# -in: dc_id = 찾을 분류 아이디
#
# -out: str = "법무/규정 > 계약서" · 스냅샷에 없으면 "DC0061 (삭제된 분류)"
# -out: error = 없음
#------------------------------------------------------------------
def path_of(tax, dc_id):
    if not tax:
        return dc_id or ""
    node = (tax.get("by_id") or {}).get(dc_id)
    if not node:
        # 과거 결과가 참조하던 노드가 지워졌을 수 있다. 결과를 고치지 않고
        # 표시만 이렇게 한다(상위 설계 4-5 — "과거 판정은 과거의 사실이다").
        return f"{dc_id} (삭제된 분류)"
    return node.get("path") or node.get("title") or dc_id


#------------------------------------------------------------------
# 고를 수 있는 분류 목록(사용 중인 것만)
#=> 상세 화면의 '직접 추가' 선택 목록을 만든다. STATUS=0(미사용)은 운영자가
#   "당분간 안 쓴다"고 끈 것이므로 새로 고르는 목록에서는 뺀다(상위 설계 4-5).
#
# -in: tax = load_taxonomy() 결과(None 이면 빈 목록)
#
# -out: list = [(dc_id, "전체경로"), …] — 경로 가나다순
# -out: error = 없음
#------------------------------------------------------------------
def selectable(tax):
    if not tax:
        return []
    items = [(dc_id, n.get("path") or n.get("title") or dc_id)
             for dc_id, n in (tax.get("by_id") or {}).items()
             if str(n.get("status", "1")) == "1"]
    return sorted(items, key=lambda t: t[1])


#------------------------------------------------------------------
# 대분류(뿌리) 목록
#=> 문서함의 업무분류 필터를 22개 잎이 아니라 6개 대분류로 좁히기 위해 쓴다
#   (설계서 6-1 — "잎 22줄을 그대로 나열하지 않는다").
#
# -in: tax = load_taxonomy() 결과
#
# -out: list = ["경영/관리", "기술/개발", …] — 정렬 순서(order) 기준
# -out: error = 없음
#------------------------------------------------------------------
def roots(tax):
    if not tax:
        return []
    rs = [n for n in (tax.get("by_id") or {}).values()
          if not n.get("parent") and str(n.get("status", "1")) == "1"]
    rs.sort(key=lambda n: (n.get("order") or 0, n.get("title") or ""))
    return [n.get("title") or n.get("dc_id") for n in rs]


#------------------------------------------------------------------
# 스냅샷을 가져온 지 며칠 됐나
#=> 묵은 스냅샷을 쓰는 실수를 완전히 막지는 못해도 화면에서 알려는 준다
#   (상위 설계 T13 — 90일이 지나면 경고). exported_at 은 "YYYYMMDDHH24MISS" 형식이다.
#
# -in: tax = load_taxonomy() 결과
#
# -out: int|None = 경과 일수. 형식이 다르거나 값이 없으면 None
# -out: error = 없음(파싱 실패는 None)
#------------------------------------------------------------------
def age_days(tax):
    raw = (tax or {}).get("exported_at") or ""
    try:
        dt = datetime.datetime.strptime(raw[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return (datetime.datetime.now() - dt).days


#------------------------------------------------------------------
# 가져온 날짜를 사람이 읽는 형태로
#=> "20260824110743" 을 "2026-08-24 11:07" 로 바꾼다. 설정 화면에 그대로 쓴다.
#
# -in: tax = load_taxonomy() 결과
#
# -out: str = "2026-08-24 11:07" · 형식이 다르면 원본 문자열 그대로
# -out: error = 없음
#------------------------------------------------------------------
def exported_at_kr(tax):
    raw = (tax or {}).get("exported_at") or ""
    try:
        dt = datetime.datetime.strptime(raw[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return raw or "알 수 없음"
    return dt.strftime("%Y-%m-%d %H:%M")
