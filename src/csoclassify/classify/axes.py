#------------------------------------------------------------------
# 업무 분류(doctype) 축 — 분류체계 스냅샷 로드·경로 계산
#=> MpowerV11 DOC_CLASSIFICATION 을 내보낸 doc_taxonomy.yaml 스냅샷을 읽어,
#   dc_id 색인과 전체경로(예: "기술/개발 > 설계문서 > 요구사항정의서")를 만든다.
#   security(C/S/O) 축과 달리 이 축은 트리(kind: taxonomy)이고 서열이 없다.
#   MpowerClassify 는 오프라인 CLI 이므로 DB 를 직접 보지 않고, 이 스냅샷 파일 하나만
#   본다. 스냅샷을 만드는 쪽(MPOWER JSON → doc_taxonomy.yaml 변환)도 이 파일에
#   함께 둔다 — MpowerClassify.exe(--export-taxonomy)와 scripts/export_taxonomy.py
#   (개발용 스크립트) 가 이 로직을 그대로 공유해야, exe 로 얼려도(소스 트리의
#   scripts/ 는 PyInstaller 번들에 안 들어간다) 같은 동작을 낼 수 있다.
#   (설계: 문서분류체계 연동 설계서 §3·4, 로드맵 D1)
#------------------------------------------------------------------

import datetime
import json
import os
import sys
from dataclasses import dataclass

import yaml

from ..resources import resource_path, exe_dir

# doc_taxonomy.yaml 은 고정 파일명이다 — 내보낼 때마다 같은 경로를 덮어쓴다.
# 타임스탬프를 파일명에 넣지 않는 이유는 설계서 4-2-1 참조(실행기가 "어디를
# 읽어야 할지"를 항상 명확하게 하기 위함 — cso_rule.yaml 과 같은 규약).
TAXONOMY_FILENAME = "doc_taxonomy.yaml"


#------------------------------------------------------------------
# 분류체계 노드 1개
#=> DOC_CLASSIFICATION 한 행에 대응한다. 이름(title)이 아니라 dc_id 로 참조하는
#   것이 규약이다 — 관리 화면에서 이름을 바꿔도 이 노드를 가리키는 규칙이
#   끊어지지 않게 하기 위함(설계서 4-3).
#
# -필드: dc_id   = 분류체계 아이디(PK, 예: "DC_002_001")
# -필드: parent  = 상위 노드 dc_id. 없으면 None(=최상위)
# -필드: order   = 정렬 순서(같은 부모 안에서 오름차순)
# -필드: title   = 분류 이름(예: "설계문서"). 사람이 읽을 때만 쓰고 참조 키로는
#                  쓰지 않는다
# -필드: status  = 사용여부. 1=사용, 0=미사용(관리 화면에서 끈 노드)
#------------------------------------------------------------------
@dataclass(frozen=True)
class TaxonomyNode:
    dc_id: str
    parent: str = None
    order: int = 0
    title: str = ""
    status: int = 1

    #------------------------------------------------------------------
    # 사용 중인 노드인가
    #=> status 를 불리언으로 바꿔 주는 편의 프로퍼티. 4-5(미사용 노드 처리)에서
    #   자주 쓰인다.
    #
    # -in: 없음
    #
    # -out: bool = status == 1 이면 True
    # -out: error = 없음
    #------------------------------------------------------------------
    @property
    def active(self):
        return self.status == 1


#------------------------------------------------------------------
# 분류체계 트리(스냅샷 전체)
#=> doc_taxonomy.yaml 을 통째로 담고, dc_id 색인·부모→자식 색인·전체경로 계산을
#   제공한다. load_taxonomy() 로 만들거나, 테스트에서 노드 목록을 직접 넣어
#   만들 수 있다(이 경우 사전 검증은 호출자 책임).
#
# -필드: source      = 원본 출처 표기(예: "Mpower10U.DOC_CLASSIFICATION")
# -필드: exported_at = 이 스냅샷을 내보낸 시각(YYYYMMDDHH24MISS 문자열).
#                       결과 레코드의 taxonomy_version 이 된다(설계서 7-1)
# -필드: node_count  = 내보낸 노드 총수(감사용 — 실제 nodes 길이와 다르면
#                       export_taxonomy.py 가 낡았거나 손상됐다는 신호)
# -필드: nodes       = TaxonomyNode 튜플(원본 파일 순서 그대로)
#------------------------------------------------------------------
class Taxonomy:

    #------------------------------------------------------------------
    # 생성자 — 색인 구성
    #=> 노드 목록을 받아 dc_id→노드, parent→자식목록 두 색인을 만든다. 경로는
    #   여기서 미리 계산하지 않고(트리가 크면 낭비) path_ids() 최초 호출 때
    #   계산해 캐시한다(지연 계산).
    #    1) by_id: dc_id → TaxonomyNode
    #    2) _children: parent(=None 이면 최상위) → 자식 노드 목록,
    #       (order, dc_id) 순으로 정렬해 관리 화면과 같은 순서를 보장
    #
    # -in: source      = 원본 출처 문자열
    # -in: exported_at = 스냅샷 시각 문자열
    # -in: node_count  = 내보낸 노드 총수(파일에 적힌 값 그대로, 검증하지 않음)
    # -in: nodes       = TaxonomyNode 반복가능 객체
    #
    # -out: 없음
    # -out: error = 없음(중복 dc_id 가 섞여 있으면 나중 것이 조용히 앞선 것을
    #                덮어쓴다 — 이 경우를 막는 것은 validate_taxonomy_data 의 몫)
    #------------------------------------------------------------------
    def __init__(self, source, exported_at, node_count, nodes):
        self.source = source
        self.exported_at = exported_at
        self.node_count = node_count
        self.nodes = tuple(nodes)
        self.by_id = {n.dc_id: n for n in self.nodes}
        self._children = {}
        for n in self.nodes:
            self._children.setdefault(n.parent, []).append(n)
        for siblings in self._children.values():
            siblings.sort(key=lambda n: (n.order, n.dc_id))
        # path_ids() 결과 캐시. 트리는 로드 후 바뀌지 않으므로 무효화가 필요 없다.
        self._path_ids_cache = {}

    #------------------------------------------------------------------
    # dc_id 로 노드 찾기
    #=> 없으면 None(호출자가 상황에 맞게 처리 — 예: T5 는 오류로, 조회 화면은
    #   "(삭제된 분류)"로).
    #
    # -in: dc_id = 찾을 분류체계 아이디
    #
    # -out: TaxonomyNode | None
    # -out: error = 없음
    #------------------------------------------------------------------
    def get(self, dc_id):
        return self.by_id.get(dc_id)

    #------------------------------------------------------------------
    # 자식 노드 목록
    #=> dc_id 의 바로 아래 자식들을 정렬된 순서로 돌려준다. dc_id=None 이면
    #   최상위(root) 노드들을 돌려준다.
    #
    # -in: dc_id = 부모 dc_id(기본 None=최상위)
    #
    # -out: tuple[TaxonomyNode] = (order, dc_id) 순 정렬, 없으면 빈 튜플
    # -out: error = 없음
    #------------------------------------------------------------------
    def children_of(self, dc_id=None):
        return tuple(self._children.get(dc_id, ()))

    #------------------------------------------------------------------
    # 최상위 노드 목록
    #=> children_of(None) 의 별칭. 트리 순회의 시작점.
    #
    # -in: 없음
    #
    # -out: tuple[TaxonomyNode]
    # -out: error = 없음
    #------------------------------------------------------------------
    @property
    def roots(self):
        return self.children_of(None)

    #------------------------------------------------------------------
    # 사용 중(status=1)인 노드만
    #=> 규칙이 참조 가능한 노드 후보(4-5) — 미사용 노드는 규칙이 걸어도 비활성
    #   처리되므로, 이 목록은 "지금 분류에 쓰이는" 노드를 보여줄 때 쓴다.
    #
    # -in: 없음
    #
    # -out: tuple[TaxonomyNode]
    # -out: error = 없음
    #------------------------------------------------------------------
    @property
    def active_nodes(self):
        return tuple(n for n in self.nodes if n.active)

    #------------------------------------------------------------------
    # 루트부터 이 노드까지의 dc_id 경로
    #=> 관리 화면의 "전체경로"를 재현한다(설계서 1-3·4-4). 결과는 캐시되므로
    #   같은 dc_id 를 여러 번 물어도 매번 부모를 다시 타지 않는다.
    #    1) 캐시에 있으면 즉시 반환
    #    2) 없으면 dc_id → root 방향으로 parent 를 따라 올라가며 모은다
    #    3) 도중 이미 지나온 dc_id 를 다시 만나면 순환(트리가 깨졌다는 뜻) —
    #       validate_taxonomy_data 가 로드 시점에 이미 걸러내므로 정상 경로에서는
    #       나지 않는다. 직접 만든 Taxonomy(검증 생략)에 대한 안전망이다
    #    4) 다 모으면 뒤집어(root → 자기 자신 순) 튜플로 반환
    #
    # -in: dc_id = 경로를 구할 노드의 dc_id
    #
    # -out: tuple[str] = root 부터 dc_id 까지의 dc_id 나열(자기 자신 포함)
    # -out: error = dc_id 가 스냅샷에 없으면 KeyError
    # -out: error = 트리에 순환이 있으면 ValueError(T7 — 정상 로드 경로에선 안 남)
    #------------------------------------------------------------------
    def path_ids(self, dc_id):
        cached = self._path_ids_cache.get(dc_id)
        if cached is not None:
            return cached
        node = self.by_id.get(dc_id)
        if node is None:
            raise KeyError(f"분류체계에 없는 dc_id 입니다: {dc_id!r}")

        chain = []
        seen = set()
        cur = node
        while cur is not None:
            if cur.dc_id in seen:
                raise ValueError(
                    f"분류체계 트리에 순환이 있습니다(dc_id={cur.dc_id!r}) — "
                    f"scripts/export_taxonomy.py 로 다시 내보내 확인하세요"
                )
            seen.add(cur.dc_id)
            chain.append(cur.dc_id)
            cur = self.by_id.get(cur.parent) if cur.parent is not None else None
        chain.reverse()

        result = tuple(chain)
        self._path_ids_cache[dc_id] = result
        return result

    #------------------------------------------------------------------
    # 루트부터 이 노드까지의 이름 경로
    #=> path_ids() 를 title 로 옮긴 것. 결과 레코드의 path_titles(설계서 4-4)에 쓴다.
    #
    # -in: dc_id = 경로를 구할 노드의 dc_id
    #
    # -out: tuple[str] = root 부터 dc_id 까지의 title 나열
    # -out: error = path_ids() 와 동일
    #------------------------------------------------------------------
    def path_titles(self, dc_id):
        return tuple(self.by_id[i].title for i in self.path_ids(dc_id))

    #------------------------------------------------------------------
    # 관리 화면과 같은 표기의 전체경로 문자열
    #=> 예: "기술/개발 > 설계문서 > 요구사항정의서". 사람이 읽는 보고문·검증
    #   메시지에 쓴다(설계서 4-3 note — "규칙 편집기와 검증 보고문은 항상 경로를
    #   함께 보여 준다").
    #
    # -in: dc_id = 경로를 구할 노드의 dc_id
    #
    # -out: str = " > " 로 이은 title 경로
    # -out: error = path_ids() 와 동일
    #------------------------------------------------------------------
    def path(self, dc_id):
        return " > ".join(self.path_titles(dc_id))

    #------------------------------------------------------------------
    # dc_id 보유 여부
    #=> `dc_id in taxonomy` 문법을 쓸 수 있게 한다.
    #
    # -in: dc_id = 확인할 dc_id
    #
    # -out: bool
    # -out: error = 없음
    #------------------------------------------------------------------
    def __contains__(self, dc_id):
        return dc_id in self.by_id

    #------------------------------------------------------------------
    # 전체 노드 수
    #=> `len(taxonomy)` 문법을 쓸 수 있게 한다.
    #
    # -in: 없음
    #
    # -out: int
    # -out: error = 없음
    #------------------------------------------------------------------
    def __len__(self):
        return len(self.nodes)

    #------------------------------------------------------------------
    # 노드 순회
    #=> `for node in taxonomy` 문법을 쓸 수 있게 한다. 파일에 적힌 원본 순서
    #   그대로 돈다(정렬된 트리 순서가 필요하면 children_of 를 재귀 호출할 것).
    #
    # -in: 없음
    #
    # -out: iterator[TaxonomyNode]
    # -out: error = 없음
    #------------------------------------------------------------------
    def __iter__(self):
        return iter(self.nodes)


#------------------------------------------------------------------
# 값 하나를 사람이 읽는 문자열로
#=> 검증 메시지에서 None/빈문자열도 눈에 보이게 표시한다.
#
# -in: value = 임의 값
#
# -out: str = repr 문자열(None 은 "(없음)")
# -out: error = 없음
#------------------------------------------------------------------
def _show(value):
    return "(없음)" if value is None else repr(value)


#------------------------------------------------------------------
# 분류체계 위반 1건 만들기
#=> 검증 결과를 보고문·테스트가 함께 쓰는 dict 한 벌로 통일한다(rules.py 의
#   _violation 과 같은 역할, taxonomy 전용으로 단순화한 버전).
#
# -in: code   = 위반 코드("T0"|"T7"|"T8")
# -in: dc_id  = 문제 노드의 dc_id(구조 오류처럼 노드를 특정할 수 없으면 None)
# -in: field  = 문제 필드명(없으면 "")
# -in: value  = 문제 값(원본 그대로 보존)
# -in: detail = 사람이 읽는 설명 한 줄
#
# -out: dict = {code, dc_id, field, value, detail}
# -out: error = 없음
#------------------------------------------------------------------
def _tv(code, dc_id, field, value, detail):
    return {"code": code, "dc_id": dc_id, "field": field, "value": value, "detail": detail}


#------------------------------------------------------------------
# 트리 순환 탐색
#=> parent 를 따라 root 방향으로 걷다가 이미 지나온 dc_id 를 다시 만나면
#   그 구간이 순환이다. 부모가 스냅샷에 없는(고아) 경우는 T8 에서 이미 보고
#   하므로 여기서는 조용히 걷기를 멈춘다(중복 보고 방지).
#
# -in: by_id = {dc_id: parent} — validate_taxonomy_data 가 구조 검증을 통과한
#              항목만으로 만든 맵(중복 dc_id 없음이 보장된 상태)
#
# -out: list[str] | None = 순환을 이루는 dc_id 나열(발견 순), 없으면 None
# -out: error = 없음
#------------------------------------------------------------------
def _find_cycle(by_id):
    for start in by_id:
        seen = []
        cur = start
        while cur is not None:
            if cur in seen:
                idx = seen.index(cur)
                return seen[idx:] + [cur]
            seen.append(cur)
            parent = by_id.get(cur)
            if parent is not None and parent not in by_id:
                break   # 고아 부모 — T8 담당, 순환 아님
            cur = parent
    return None


#------------------------------------------------------------------
# 분류체계 스냅샷 원본(YAML) 검증 — T0·T7·T8
#=> load_taxonomy 가 파싱한 원시 dict 를 훑어 구조·트리 위반을 '전부' 모은다.
#   rules.py 의 validate_rules_data 와 같은 원칙 — 첫 오류에서 멈추지 않고 한
#   번에 다 보여 준다.
#    1) 최상위 "taxonomy:" 매핑과 "nodes:" 목록이 있는지(없으면 T0, 더 못 감)
#    2) 항목별로 dc_id·title 이 유효하고 dc_id 가 중복되지 않는지(T0)
#    3) 구조가 깨졌으면(T0 발생) 트리 검사는 신뢰할 수 없으므로 여기서 멈춤
#    4) parent 가 스냅샷에 실제로 있는 dc_id 를 가리키는지(T8, 고아 검사)
#    5) parent 를 따라 걸어 자기 자신으로 돌아오지 않는지(T7, 순환 검사)
#
# -in: data = yaml.safe_load 결과 dict
#
# -out: violations = 위반 dict 리스트(문제 없으면 빈 리스트)
# -out: error = 없음(예외를 던지지 않는다 — 판단은 호출자 몫)
#------------------------------------------------------------------
def validate_taxonomy_data(data):
    violations = []
    data = data or {}

    root = data.get("taxonomy")
    if not isinstance(root, dict):
        violations.append(_tv("T0", None, "taxonomy", root,
                              "최상위 'taxonomy:' 매핑이 없습니다"))
        return violations

    raw_nodes = root.get("nodes")
    if not isinstance(raw_nodes, list):
        violations.append(_tv("T0", None, "taxonomy.nodes", raw_nodes,
                              "'nodes' 가 목록(list)이 아닙니다"))
        return violations

    seen_ids = set()
    parsed = []   # [(dc_id, parent), ...] — 구조 검증을 통과한 항목만
    for idx, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            violations.append(_tv("T0", None, f"nodes[{idx}]", item,
                                  "항목이 매핑(mapping)이 아닙니다"))
            continue
        dc_id = item.get("dc_id")
        if not dc_id or not isinstance(dc_id, str):
            violations.append(_tv("T0", None, f"nodes[{idx}].dc_id", dc_id,
                                  "dc_id 가 비어 있거나 문자열이 아닙니다"))
            continue
        if dc_id in seen_ids:
            violations.append(_tv("T0", dc_id, "dc_id", dc_id, "dc_id 가 중복됩니다"))
            continue
        if not item.get("title"):
            violations.append(_tv("T0", dc_id, "title", item.get("title"),
                                  "title 이 비어 있습니다"))
        seen_ids.add(dc_id)
        parsed.append((dc_id, item.get("parent")))

    # 구조 자체가 흔들리면(중복 dc_id·매핑 아님 등) 부모-자식 관계를 신뢰할 수
    # 없으므로, T7·T8 검사는 구조가 온전할 때만 의미가 있다.
    if any(v["code"] == "T0" for v in violations):
        return violations

    by_id = dict(parsed)

    for dc_id, parent in parsed:
        if parent is not None and parent not in by_id:
            violations.append(_tv("T8", dc_id, "parent", parent,
                                  f"상위 dc_id {parent!r} 가 스냅샷에 없습니다(삭제됐거나 "
                                  f"스냅샷이 낡았을 수 있습니다)"))

    cycle = _find_cycle(by_id)
    if cycle:
        violations.append(_tv("T7", cycle[0], "parent", " -> ".join(cycle),
                              "parent 를 따라가면 자기 자신으로 되돌아옵니다"))

    return violations


#------------------------------------------------------------------
# 위반 목록 → 사람이 읽는 보고문
#=> 검증 코드(T0/T7/T8)별로 묶어 한 화면에 정리한다. cli.py 가 종료 시 그대로
#   stderr 에 찍을 수 있는 형태다(rules.py 의 format_violations 와 같은 역할).
#
# -in: path       = 분류체계 스냅샷 파일 경로
# -in: violations = validate_taxonomy_data 결과
#
# -out: text = 여러 줄 문자열(끝에 개행 없음)
# -out: error = 없음
#------------------------------------------------------------------
def format_taxonomy_violations(path, violations):
    titles = {
        "T0": "[T0] 분류체계 스냅샷 구조 오류",
        "T7": "[T7] 분류체계 트리에 순환이 있음",
        "T8": "[T8] 상위 dc_id 가 스냅샷에 없음(고아 노드)",
    }
    lines = [
        f"[분류체계 오류] {path} — 검증 실패 {len(violations)}건. doctype 축을 로드하지 않았습니다.",
        "",
    ]
    for code in ("T0", "T7", "T8"):
        group = [v for v in violations if v["code"] == code]
        if not group:
            continue
        lines.append(f"  {titles.get(code, '[' + code + ']')}")
        for v in group:
            where = f"dc_id={v['dc_id']}" if v["dc_id"] else "(구조)"
            if v["field"]:
                where += f".{v['field']}"
            lines.append(f"    {where} = {_show(v['value'])}  — {v['detail']}")
        lines.append("")
    lines.append(f"  고치는 법: {path} 를 열어 위 항목을 고치거나, "
                 f"DOC_CLASSIFICATION 을 scripts/export_taxonomy.py 로 다시 내보내세요.")
    return "\n".join(lines)


#------------------------------------------------------------------
# 분류체계 스냅샷 위반 예외
#=> doc_taxonomy.yaml 을 읽는 데는 성공했지만 내용이 규칙에 맞지 않을 때 던진다.
#   cso_rule.yaml 의 RuleSetValidationError 와 같은 역할이다.
#
# -필드: path       = 문제의 스냅샷 파일 경로
# -필드: violations = 위반 목록. 각 항목은 dict {code, dc_id, field, value, detail}
#------------------------------------------------------------------
class TaxonomyValidationError(Exception):

    #------------------------------------------------------------------
    # 예외 생성
    #=> 파일 경로와 위반 목록을 담고, 사람이 읽을 요약문을 메시지로 만든다.
    #
    # -in: path       = 분류체계 스냅샷 파일 경로
    # -in: violations = 위반 dict 리스트(빈 리스트로는 만들지 않는다)
    #
    # -out: 없음(생성자)
    # -out: error = 없음
    #------------------------------------------------------------------
    def __init__(self, path, violations):
        self.path = path
        self.violations = list(violations)
        super().__init__(format_taxonomy_violations(path, self.violations))


#------------------------------------------------------------------
# 분류체계 스냅샷 기본 경로
#=> cso_rule.yaml 의 default_rules_path() 와 완전히 같은 규약을 따른다 —
#   "규칙셋과 같은 취급"(설계서 4-1)이라는 결정 그대로다.
#    1) 환경변수 CSOCLASSIFY_POLICY_DIR 이 있으면 그 폴더
#    2) exe(PyInstaller) 로 얼린 실행이면 'exe 옆'
#    3) 소스(개발) 실행이면 저장소 트리의 resources/policy/
#
# -in: 없음
#
# -out: path = doc_taxonomy.yaml 기본 경로
# -out: error = 없음
#------------------------------------------------------------------
def default_taxonomy_path():
    env = os.environ.get("CSOCLASSIFY_POLICY_DIR")
    if env:
        return os.path.join(env, TAXONOMY_FILENAME)
    if getattr(sys, "frozen", False):
        return os.path.join(exe_dir(), TAXONOMY_FILENAME)
    return resource_path("policy", TAXONOMY_FILENAME)


#------------------------------------------------------------------
# 분류체계 스냅샷 로드(YAML → Taxonomy)
#=> doc_taxonomy.yaml 을 읽어 Taxonomy 를 만든다. 경로를 안 주면
#   default_taxonomy_path().
#    1) 파일이 없으면 안내 메시지와 함께 FileNotFoundError
#       (이 축이 "있으면 켜지고 없으면 꺼지는 외장 자산"이라는 정책 자체는
#       cli.py 쪽 몫이다 — 여기서는 항상 필수로 취급하고, 없어도 계속 진행할지는
#       호출자가 이 예외를 잡아 결정한다)
#    2) YAML 파싱 → T0·T7·T8 검증(validate=True 일 때). 하나라도 위반이면
#       Taxonomy 를 만들지 않고 실패한다 — 절반쯤 잘못된 트리로 경로를 계산하면
#       조용히 틀린 분류가 나갈 수 있어서다
#    3) TaxonomyNode 목록을 만들고 Taxonomy 로 감싸 반환
#
# -in: path     = 스냅샷 파일 경로(없으면 기본 경로)
# -in: validate = False 면 검증을 건너뛴다. 검증기 자체를 시험하거나 잘못된
#                 스냅샷을 일부러 읽어야 하는 도구용 탈출구다(기본 True)
#
# -out: Taxonomy
# -out: error = 파일 없음 시 FileNotFoundError(어디에 두면 되는지 안내 포함)
# -out: error = 구조·트리 검증 실패 시 TaxonomyValidationError(위반 전체 목록 포함)
#------------------------------------------------------------------
def load_taxonomy(path=None, validate=True):
    path = path or default_taxonomy_path()
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"분류체계 스냅샷({TAXONOMY_FILENAME})을 찾을 수 없습니다: {path}\n"
            f"  · exe 와 같은 폴더에 {TAXONOMY_FILENAME} 을 두거나,\n"
            f"  · --taxonomy <파일경로> 로 지정하거나,\n"
            f"  · 환경변수 CSOCLASSIFY_POLICY_DIR 로 폴더를 지정하거나,\n"
            f"  · scripts/export_taxonomy.py 로 새로 내보내세요."
        )
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # 구조·트리 검증을 '파싱 직후·객체 생성 전'에 한다. Taxonomy 를 만들고 나서
    # 검사하면 이미 절반쯤 틀린 트리로 경로 계산이 돌아갈 수 있다.
    if validate:
        violations = validate_taxonomy_data(data)
        if violations:
            raise TaxonomyValidationError(path, violations)

    root = data.get("taxonomy") or {}
    raw_nodes = root.get("nodes") or []
    nodes = tuple(
        TaxonomyNode(
            dc_id=n["dc_id"],
            parent=n.get("parent"),
            order=int(n.get("order", 0) or 0),
            title=n.get("title", ""),
            status=int(n.get("status", 1)),
        )
        for n in raw_nodes
    )

    return Taxonomy(
        source=root.get("source", ""),
        exported_at=str(root.get("exported_at", "")),
        node_count=int(root.get("node_count", len(nodes)) or len(nodes)),
        nodes=nodes,
    )


#------------------------------------------------------------------
# MPOWER JSON 내보내기 원본 기본 경로
#=> MpowerV11 관리 화면/배치가 그대로 뽑은 파일(사람이 손대지 않음). cso_rule.yaml
#   과 같은 "policy" 폴더에 두되, 이건 '원본'이라 doc_taxonomy.yaml(파생 스냅샷)
#   과는 성격이 다르다 — exe 옆에 있을 수도, 없을 수도 있는 입력 자료다.
#
# -in: 없음
#
# -out: path = resources/policy/doc_classification_export.json (개발 실행 기준.
#              exe 배포에서는 보통 --taxonomy-input 으로 실제 위치를 지정한다)
# -out: error = 없음
#------------------------------------------------------------------
def default_taxonomy_export_input_path():
    return resource_path("policy", "doc_classification_export.json")


#------------------------------------------------------------------
# MPOWER JSON 노드 1건 → 스냅샷 노드 dict
#=> MPOWER JSON 필드명(parent_dc_id·order_num)을 doc_taxonomy.yaml 규약
#   (parent·order)으로 바꾼다. path_ids·path_titles·path 는 원본에 있어도
#   버린다 — Taxonomy.path()가 로드 시점에 다시 계산하므로, 옮겨 두면 두 값이
#   어긋날 때(예: 사람이 원본 JSON 을 손으로 고침) 어느 쪽이 진실인지 모호해진다.
#   dc_id 하나만 진실 출처로 남긴다(설계서 3-2-1).
#
# -in: raw = JSON nodes[] 의 항목 하나(dict)
#
# -out: dict = {dc_id, parent, order, title, status} — doc_taxonomy.yaml 노드 1행
# -out: error = 필수 필드(dc_id) 가 없으면 KeyError(호출자가 원본 위치와 함께 보고)
#------------------------------------------------------------------
def _convert_export_node(raw):
    return {
        "dc_id": raw["dc_id"],
        "parent": raw.get("parent_dc_id"),
        "order": int(raw.get("order_num", 0) or 0),
        "title": raw.get("title", ""),
        "status": int(raw.get("status", 1)),
    }


#------------------------------------------------------------------
# MPOWER JSON 원본 → 스냅샷 dict 변환(핵심)
#=> 파일을 아직 쓰지 않고, doc_taxonomy.yaml 에 그대로 yaml.safe_dump 할 수 있는
#   파이썬 dict 를 만든다.
#    1) source 는 원본 JSON 값을 그대로 옮긴다(예: "Mpower10U.DOC_CLASSIFICATION")
#    2) exported_at 은 "지금"(이 변환을 실행한 시각) — DB 조회 시각이 아니라
#       "MpowerClassify 가 이 스냅샷을 받아들인 시각"이 감사에 더 값지다
#    3) node_count 는 원본 값이 있으면 쓰되, 실제 nodes 길이와 다르면 경고만
#       하고 진행한다(원본이 이미 낡았을 수 있어도, 변환 자체는 막을 이유가 없다)
#
# -in: raw_data = json.load 결과(dict, {source, node_count, nodes:[...]})
#
# -out: (dict, list[str]) = (doc_taxonomy.yaml 로 쓸 dict, 경고 메시지 목록)
# -out: error = raw_data 가 dict 가 아니거나 "nodes" 가 리스트가 아니면 ValueError
#------------------------------------------------------------------
def convert_mpower_json(raw_data):
    if not isinstance(raw_data, dict):
        raise ValueError("원본 JSON 최상위가 매핑(mapping)이 아닙니다")
    raw_nodes = raw_data.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ValueError("원본 JSON 에 'nodes' 목록이 없습니다")

    warnings = []
    nodes = []
    for idx, raw in enumerate(raw_nodes):
        try:
            nodes.append(_convert_export_node(raw))
        except KeyError as e:
            warnings.append(f"nodes[{idx}] 에 {e} 필드가 없어 건너뜁니다: {raw!r}")

    declared = raw_data.get("node_count")
    if declared is not None and int(declared) != len(nodes):
        warnings.append(
            f"원본의 node_count({declared})와 실제 변환된 노드 수({len(nodes)})가 다릅니다"
        )

    exported_at = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    snapshot = {
        "taxonomy": {
            "source": raw_data.get("source", "DOC_CLASSIFICATION"),
            "exported_at": exported_at,
            "node_count": len(nodes),
            "nodes": nodes,
        }
    }
    return snapshot, warnings


#------------------------------------------------------------------
# 스냅샷 dict → doc_taxonomy.yaml 파일 기록
#=> 한글이 깨지지 않도록 allow_unicode, 원본 순서를 보존하도록 sort_keys=False
#   로 저장한다. 편집기가 아니라 기계가 매번 새로 만드는 파일이므로(설계서
#   4-2-1) round-trip 주석 보존(ruamel)은 쓰지 않는다 — 사람이 손대는 파일이 아니다.
#
# -in: snapshot = convert_mpower_json() 이 만든 dict
# -in: out_path = 저장할 doc_taxonomy.yaml 경로
#
# -out: 없음(파일 기록)
# -out: error = 디렉터리가 없으면 만들고 재시도. 그래도 쓰기 실패하면 OSError 전파
#------------------------------------------------------------------
def write_taxonomy_yaml(snapshot, out_path):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(snapshot, f, allow_unicode=True, sort_keys=False,
                       default_flow_style=False)


#------------------------------------------------------------------
# MPOWER JSON → doc_taxonomy.yaml 내보내기(핵심 진입점)
#=> 입력 JSON 경로 하나로 변환·저장·round-trip 검증까지 끝낸다. MpowerClassify.exe
#   (--export-taxonomy)와 scripts/export_taxonomy.py 가 이 함수 하나를 공유해
#   "exe 로 얼려도 스크립트와 똑같이 동작"을 보장한다.
#    1) JSON 을 읽는다(BOM 허용)
#    2) convert_mpower_json 으로 변환(구조 오류면 ValueError)
#    3) write_taxonomy_yaml 로 저장
#    4) load_taxonomy 로 다시 읽어 검증(T0·T7·T8) — 방금 쓴 파일이 실제로
#       문제없이 로드되는지 여기서 확인하지 않으면, 문제를 다음 실행(실제
#       분류) 때에야 알게 된다
#
# -in: input_path  = 원본 JSON 경로
# -in: output_path = doc_taxonomy.yaml 저장 경로
#
# -out: (Taxonomy, list[str]) = (round-trip 검증까지 끝난 Taxonomy, 변환 경고 목록)
# -out: error = 입력 파일 없으면 FileNotFoundError, JSON 파싱 실패면
#                json.JSONDecodeError(ValueError 의 하위클래스), 구조 오류면
#                ValueError, 저장 결과가 깨졌으면 TaxonomyValidationError
#------------------------------------------------------------------
def export_from_mpower_json(input_path, output_path):
    with open(input_path, encoding="utf-8-sig") as f:
        raw_data = json.load(f)
    snapshot, warnings = convert_mpower_json(raw_data)
    write_taxonomy_yaml(snapshot, output_path)
    taxonomy = load_taxonomy(output_path)
    return taxonomy, warnings
