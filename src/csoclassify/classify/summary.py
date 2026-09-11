#------------------------------------------------------------------
# 분류 결과 집계·요약 — 설계서 §7-3
#=> 이미 만들어진 레코드(engine.build_record 결과) 목록을 받아, 사람이 읽는
#   집계 텍스트를 만든다. 보안등급은 값 하나(C/S/O/미분류)라 단순 집계지만,
#   업무분류는 문서 하나가 여러 분류를 가질 수 있어(all/top_n) 총건수와
#   업무분류 합계가 다를 수 있다 — 그 사실 자체를 함께 알려야 오해가 없다.
#   레코드 dict 만 있으면 되므로(taxonomy 객체가 필요 없다 — path 는 이미
#   labels.doctype.values[].path 에 문자열로 박혀 있다), 순수 함수로 남는다.
#   (설계: 문서분류체계 연동 설계서 §7-3, 로드맵 D5)
#------------------------------------------------------------------

from collections import Counter, defaultdict

from .. import record


#------------------------------------------------------------------
# 레코드 하나의 보안등급 값
#=> 새 모양(security.grade)과 옛 모양(labels.security.value / 최상위 grade)을
#   record.grade_of() 가 한 자리에서 흡수한다.
#
# -in: rec = 분류 레코드 dict
#
# -out: str | None = "C"|"S"|"O"|None
# -out: error = 없음
#------------------------------------------------------------------
def _security_value(rec):
    return record.grade_of(rec)


#------------------------------------------------------------------
# 보안등급 집계 줄
#=> "[보안등급]  C n · S n · O n · 미분류 n" 한 줄을 만든다.
#
# -in: records = 분류 레코드 dict 리스트
#
# -out: str = 집계 줄(끝에 개행 없음)
# -out: error = 없음
#------------------------------------------------------------------
def _summarize_security(records):
    dist = Counter()
    for rec in records:
        v = _security_value(rec)
        dist[v if v in ("C", "S", "O") else None] += 1
    return (f"  [보안등급]  C {dist.get('C', 0)} · S {dist.get('S', 0)} · "
           f"O {dist.get('O', 0)} · 미분류 {dist.get(None, 0)}")


#------------------------------------------------------------------
# 업무분류 집계 줄들
#=> doctype 축이 이 배치에서 전혀 안 돌았으면(레코드 어디에도 doctype
#   칸이 없으면) None 을 돌려 요약에서 이 섹션 자체를 뺀다(설계서 4-6 —
#   "축을 안 씀"은 "미분류"와 다르다). 돌았으면 뿌리 분류(최상위 카테고리)별로
#   묶어 롤업하고, 그 안에서 실제로 걸린 노드별 건수를 괄호로 덧붙인다
#   (설계서 7-3 — "트리 축은 상위 노드로 롤업해 보여준다").
#    1) doctype 축이 돌아간 레코드만 대상으로 한다
#    2) values 가 비어 있으면 그 레코드는 "미분류"
#    3) values 가 있으면 각 후보의 path 에서 첫 조각(뿌리)·마지막 조각(실제
#       걸린 노드 이름)을 뽑아 뿌리별·노드별로 센다
#    4) 뿌리는 건수 내림차순, 같은 뿌리 안 노드도 건수 내림차순으로 나열
#    5) 업무분류 합계(문서-분류 쌍 수) > 총건수(doctype 축이 돈 문서 수) 면
#       "정상"이라는 안내를 덧붙인다(설계서 7-3 note)
#
# -in: records = 분류 레코드 dict 리스트
#
# -out: list[str] | None = 집계 줄 목록(섹션 없으면 None)
# -out: error = 없음
#------------------------------------------------------------------
def _summarize_doctype(records):
    with_axis = [rec for rec in records if record.doctype_of(rec) is not None]
    if not with_axis:
        return None

    root_totals = Counter()
    root_breakdown = defaultdict(Counter)
    unclassified = 0
    total_hits = 0

    for rec in with_axis:
        values = record.doctype_values(rec)
        if not values:
            unclassified += 1
            continue
        for v in values:
            total_hits += 1
            segments = [s for s in (v.get("path") or "").split(" > ") if s]
            root_title = segments[0] if segments else v.get("dc_id", "?")
            node_title = segments[-1] if segments else v.get("dc_id", "?")
            root_totals[root_title] += 1
            root_breakdown[root_title][node_title] += 1

    lines = ["  [업무분류]"]
    for root_title, total in root_totals.most_common():
        breakdown = " · ".join(
            f"{t} {c}" for t, c in root_breakdown[root_title].most_common())
        lines.append(f"    {root_title} {total} ({breakdown})")
    lines.append(f"    미분류 {unclassified}")

    if total_hits > len(with_axis):
        lines.append(
            f"  ※ 업무분류 합계({total_hits})가 총건수({len(with_axis)})보다 "
            f"큰 것은 정상 — 한 문서가 여러 분류를 가질 수 있다."
        )
    return lines


#------------------------------------------------------------------
# 분류 결과 집계 텍스트(핵심)
#=> 설계서 7-3 의 요약 형태(총건수 → 보안등급 → 업무분류)를 그대로 만든다.
#   doctype 축이 이 실행에서 아예 안 돌았으면 업무분류 섹션 자체를 뺀다.
#
# -in: records = engine.build_record 로 만든 분류 레코드 dict 리스트
#
# -out: str = 여러 줄 요약 텍스트(끝에 개행 없음)
# -out: error = 없음
#------------------------------------------------------------------
def summarize_records(records):
    lines = [f"총 {len(records)}건", _summarize_security(records)]
    doctype_lines = _summarize_doctype(records)
    if doctype_lines:
        lines.extend(doctype_lines)
    return "\n".join(lines)
