#------------------------------------------------------------------
# 업무분류 seed 부트스트랩 — 파일명 약한 라벨로 씨앗 만들기 (재설계 10장)
#=> 2단계(벡터 비교)를 켜려면 seed 가 있어야 하는데, 사람이 수천 건을 라벨링할
#   수는 없다. 그래서 "1단계 규칙이 아주 자신 있게 분류한 문서"만 골라 씨앗으로
#   쓴다. 파일명·제목이 멀쩡한 절반이 나머지 절반을 덮어 주는 구조다.
#
#   [왜 필요한가] 규칙은 제목·파일명에 단서가 있는 문서만 잡는다. 실측에서
#   미라벨 층(B군) 111건 중 약 83건이 실제 업무문서였는데, 이 문서들은
#   'O.txt'·'5-28.pdf'처럼 이름이 무의미해 규칙으로는 원리적으로 못 잡는다
#   (재설계 3-C-4). 내용이 비슷한 이웃을 보고 붙이는 수밖에 없다.
#
#   [절대 원칙 — drift 방지, 재설계 10-4]
#     전파로 붙은 라벨은 절대 seed 가 되지 않는다. seed 자격은
#     (a) 1단계 규칙 고신뢰, (b) 사람 확정, 이 두 경로뿐이다.
#     이게 깨지면 초기 오분류가 스스로를 강화하며 코퍼스 전체로 번진다.
#------------------------------------------------------------------

import json
import os
from collections import Counter

# 벡터에서 온 후보는 씨앗이 될 수 없다(10-4). 신호원에 이것이 섞이면 탈락.
_EMBED_SOURCES = frozenset({"embed"})

# 단독으로는 증거가 못 되는 약한 신호(재설계 8-4). '독립된 두 신호원' 을 셀 때
# 이것만으로는 한 개로 쳐 주지 않는다 — 본문에 단어가 있다는 것만으로
# 씨앗을 만들면 규칙의 오탐이 그대로 씨앗이 된다.
_WEAK_SOURCES = frozenset({"structure", "body"})

# 기본 상한값. 왜 이 값인지는 각 인자 설명 참조.
DEFAULT_MIN_CHARS = 200
DEFAULT_PER_DIR = 3
DEFAULT_PER_NODE = 50
DEFAULT_MIN_SOURCES = 2


#------------------------------------------------------------------
# 레코드의 본문 길이 알아내기
#=> 본문을 통째로 들고 다니면(--with-text) 메모리가 크게 든다. 그래서 호출자가
#   길이만 넘길 수도 있게 두 가지를 다 받는다. 둘 다 없으면 '모름'(None)이고,
#   모르는 것을 짧다고 단정하지 않는다 — 그러면 멀쩡한 씨앗이 통째로 떨어진다.
#
# -in: rec = 레코드 dict("text" 또는 "text_len" 중 하나를 본다)
#
# -out: int | None = 본문 길이(모르면 None)
# -out: error = 없음
#------------------------------------------------------------------
def _text_len(rec):
    n = rec.get("text_len")
    if isinstance(n, int):
        return n
    t = rec.get("text")
    return len(t) if t is not None else None


#------------------------------------------------------------------
# 후보 하나가 씨앗 자격을 갖췄는지
#=> 재설계 10-1 단계 1 의 조건을 그대로 옮긴다. 하나라도 어긋나면 탈락시키고
#   왜 탈락했는지를 함께 돌려준다 — 씨앗이 안 모일 때 원인을 알아야 한다.
#    1) 벡터에서 온 후보가 아닐 것 (10-4 drift 방지)
#    2) 신뢰도가 t_seed 이상일 것
#    3) 독립된 신호원이 min_sources 개 이상일 것(약한 신호는 세지 않는다)
#    4) leaf 노드일 것 (중간 노드는 조상 흡수로 어차피 사라진다)
#
# -in: cand        = labels.doctype.values 의 후보 dict
# -in: taxonomy    = axes.Taxonomy (leaf 판정에 쓴다)
# -in: t_seed      = 씨앗 채택 최소 신뢰도(재설계 8-3 · Defaults.t_seed)
# -in: min_sources = 필요한 독립 신호원 수(기본 2)
#
# -out: (bool, str) = (자격 여부, 탈락 사유 키). 자격이면 사유는 ""
# -out: error = 없음
#------------------------------------------------------------------
def candidate_ok(cand, taxonomy, t_seed, min_sources=DEFAULT_MIN_SOURCES):
    srcs = set(cand.get("from") or ())

    # 1) 전파로 붙은 라벨은 씨앗이 될 수 없다 — 이 한 줄이 drift 를 막는다.
    if srcs & _EMBED_SOURCES:
        return False, "벡터후보"

    if float(cand.get("confidence") or 0.0) < t_seed:
        return False, "신뢰도미달"

    # 3) 약한 신호(본문·짜임새)는 신호원 수에 넣지 않는다. "본문에 단어가 있다"는
    #    것만 두 개 모아 봐야 여전히 약한 증거다.
    strong = srcs - _WEAK_SOURCES
    if len(strong) < min_sources:
        return False, "신호원부족"

    # 4) leaf 가 아니면 씨앗으로 쓰지 않는다. 중간 노드는 자손이 함께 걸리면
    #    조상 흡수로 사라지므로, 씨앗에 넣어 봐야 kNN 투표만 흐려 놓는다.
    dc_id = cand.get("dc_id")
    if taxonomy is None or taxonomy.get(dc_id) is None:
        return False, "노드없음"
    # 자식이 있으면 중간 노드다. Node 에는 children 필드가 없고 트리 관계는
    # Taxonomy 가 들고 있으므로 children_of 로 물어본다.
    if taxonomy.children_of(dc_id):
        return False, "중간노드"

    return True, ""


#------------------------------------------------------------------
# 레코드 목록에서 씨앗으로 쓸 문서를 고른다
#=> 문서 단위 조건(라벨 1개·본문 길이·벡터 유무)까지 걸러 낸 뒤, 편향 완화를
#   위해 폴더별·노드별 상한을 적용한다.
#
#   [왜 상한을 두는가 — 재설계 10-3]
#     파일명으로 뽑은 씨앗은 편향돼 있다. "회의록.hwp" 라고 이름 붙는 문서는
#     정식 서식 회의록이고, 이름이 안 붙는 회의록은 메모에 가깝다. 한 폴더에서
#     수십 건을 뽑으면 그 폴더의 문서 모양이 곧 그 분류의 정의가 되어 버린다.
#     노드별 상한도 같은 이유 — 한 분류가 씨앗을 독식하면 kNN 투표가 그쪽으로
#     쏠린다.
#
#   [정렬] 신뢰도 높은 순으로 담는다. 상한에 걸려 잘릴 때 확실한 것이 남는다.
#
# -in: records     = build_record 결과 리스트(vector·labels.doctype 를 본다)
# -in: taxonomy    = axes.Taxonomy
# -in: t_seed      = 씨앗 채택 최소 신뢰도(기본 0.85 — Defaults.t_seed)
# -in: min_chars   = 본문 최소 길이. 빈 스캔 PDF 처럼 내용이 없는 문서를 씨앗으로
#                    쓰면 그 벡터가 '아무거나와 비슷한' 벡터가 된다(기본 200자)
# -in: per_dir     = 한 폴더에서 뽑을 최대 건수(기본 3 · 10-3 완화 1)
# -in: per_node    = 한 분류에서 뽑을 최대 건수(기본 50 · 10-3 완화 2)
# -in: min_sources = 필요한 독립 신호원 수(기본 2)
#
# -out: (seeds, stats) = seeds 는 [{"file","labels":{"doctype":[dc_id]},"vector":[...]}],
#                        stats 는 탈락 사유별 건수 + 노드별 채택 수
# -out: error = 없음(레코드가 이상하면 그 건만 조용히 건너뛴다)
#------------------------------------------------------------------
def select_doctype_seeds(records, taxonomy, t_seed=0.85,
                         min_chars=DEFAULT_MIN_CHARS, per_dir=DEFAULT_PER_DIR,
                         per_node=DEFAULT_PER_NODE,
                         min_sources=DEFAULT_MIN_SOURCES):
    reasons = Counter()
    picked = []

    for rec in records or ():
        dt = (rec.get("labels") or {}).get("doctype")
        if not dt:
            reasons["업무분류축꺼짐"] += 1
            continue

        # class_seed.jsonl 파일에 vector 항목이 있는 경우에만 추가
        vec = rec.get("vector")
        if not vec:
            # 벡터가 없으면 seed 될 수 없다. 
            reasons["벡터없음"] += 1
            continue

        values = dt.get("values") or []
        # 문서에 채택된 노드가 여러 개면 씨앗으로 모호하다(10-1). 이 문서의 벡터가
        # 어느 분류를 대표하는지 정할 수 없기 때문이다.
        if len(values) != 1:
            reasons["라벨0개" if not values else "다중라벨"] += 1
            continue

        tlen = _text_len(rec)
        if tlen is not None and tlen < min_chars:
            reasons["본문짧음"] += 1
            continue

        ok, why = candidate_ok(values[0], taxonomy, t_seed, min_sources)
        if not ok:
            reasons[why] += 1
            continue

        picked.append((float(values[0]["confidence"]), rec, values[0]["dc_id"]))

    # 확실한 것부터 담아야 상한에 걸려 잘릴 때 좋은 씨앗이 남는다.
    picked.sort(key=lambda x: (-x[0], str(x[1].get("file") or "")))

    per_dir_n, per_node_n = Counter(), Counter()
    seeds = []
    for conf, rec, dc_id in picked:
        f = str(rec.get("file") or "")
        d = os.path.dirname(f).lower()
        if per_dir and per_dir_n[d] >= per_dir:
            reasons["폴더상한"] += 1
            continue
        if per_node and per_node_n[dc_id] >= per_node:
            reasons["노드상한"] += 1
            continue
        per_dir_n[d] += 1
        per_node_n[dc_id] += 1
        seeds.append({"file": f, "labels": {"doctype": [dc_id]},
                      "vector": list(rec["vector"])})

    stats = {"입력": len(records or ()), "채택": len(seeds),
             "탈락사유": dict(reasons), "노드별": dict(per_node_n)}
    return seeds, stats


#------------------------------------------------------------------
# 씨앗을 class_seed.jsonl 형식으로 저장
#=> DoctypeSeedIndex.from_seed_file() 이 이미 읽는 형식 그대로 쓴다(10-1 단계 2).
#   새 저장 포맷을 만들지 않는 것이 이 설계의 요점이다.
#
#   [덮어쓰기 주의] security 축 seed 와 같은 파일을 공유할 수 있다. 그래서
#   기본은 '새 파일'이고, 합치려면 merge_into 를 쓴다.
#
# -in: seeds = select_doctype_seeds() 가 낸 리스트
# -in: path  = 저장 경로
#
# -out: int = 기록한 줄 수
# -out: error = 디렉터리를 못 만들거나 쓰기 실패 시 예외 전파
#------------------------------------------------------------------
def write_seed_file(seeds, path):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for s in seeds:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    return len(seeds)


#------------------------------------------------------------------
# 기존 seed 파일에 업무분류 seed 얹기
#=> security seed가 이미 들어 있는 class_seed.jsonl 을 그대로 살리면서 doctype
#   씨앗만 갈아 끼운다. 같은 파일을 두 축이 나눠 쓰는 규약(설계서 5-4)이라,
#   통째로 덮어쓰면 보안등급 전파가 조용히 죽는다.
#    1) 기존 줄을 읽어 doctype 씨앗(labels.doctype 이 있는 줄)만 걷어낸다
#    2) 나머지(security seed)는 순서 그대로 남긴다
#    3) 새 doctype seed를 뒤에 붙인다
#
# -in: seeds = 새로 넣을 doctype seed 리스트
# -in: path  = class_seed.jsonl 경로(없으면 새로 만든다)
#
# -out: dict = {"기존유지": n, "이전doctype제거": n, "신규": n}
# -out: error = 쓰기 실패 시 예외 전파. 깨진 줄은 조용히 버린다
#------------------------------------------------------------------
def merge_into(seeds, path):
    keep, dropped = [], 0
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (e.get("labels") or {}).get("doctype"):
                    dropped += 1      # 이전 회차의 doctype 씨앗 — 새 것으로 갈아 끼운다
                else:
                    keep.append(e)

    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in keep:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        for s in seeds:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
            
    return {"기존유지": len(keep), "이전doctype제거": dropped, "신규": len(seeds)}
