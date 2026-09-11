#------------------------------------------------------------------
# 규칙셋(cso_rule.yaml) 편집 — 주석 보존 round-trip
#=> 규칙 설정 화면(Phase 0)이 쓰는 로드/편집/저장/버전/미리보기 로직.
#   ruamel.yaml 로 한글 주석·서식을 보존하며 편집한다. 기존 항목은 "제자리 수정"
#   해서 그 항목에 달린 경고 주석(예: "인사" 과매칭 주의)이 사라지지 않게 한다.
#------------------------------------------------------------------

import os
import re
import shutil
import tempfile
import datetime

from ruamel.yaml import YAML

# round-trip 로더(주석·따옴표·서식 보존). width 크게 잡아 긴 리스트가 접히지 않게.
_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096


#------------------------------------------------------------------
# 규칙셋 문서 로드(round-trip)
#=> cso_rule.yaml 을 주석까지 담은 편집 가능한 문서 객체로 읽는다.
#
# -in: path = cso_rule.yaml 경로
# -out: doc = ruamel 문서(dict 처럼 접근)
# -out: error = 파일 없음/문법 오류 시 예외 전파
#------------------------------------------------------------------
def load_doc(path):
    with open(path, encoding="utf-8") as f:
        return _yaml.load(f)


#------------------------------------------------------------------
# 규칙셋 문서 저장(백업 후 덮어쓰기)
#=> 저장 전 원본을 .bak 로 백업하고, 주석을 보존한 채 다시 쓴다.
#
# -in: path = 저장 경로
# -in: doc  = 편집된 문서 객체
# -out: 없음(파일 기록)
# -out: error = 쓰기 실패 시 예외 전파
#------------------------------------------------------------------
def save_doc(path, doc):
    if os.path.isfile(path):
        shutil.copy(path, path + ".bak")
    with open(path, "w", encoding="utf-8") as f:
        _yaml.dump(doc, f)


#------------------------------------------------------------------
# 버전 자동 상향
#=> "cso-YYYY.MM[.rev]" 에서 rev 를 1 올린다(없으면 .1). 규칙 변경을 결과의
#   rule_version 으로 추적하기 위함. 형식이 안 맞으면 오늘 날짜 기반으로 새로 만든다.
#
# -in: ver = 현재 버전 문자열
# -out: str = 새 버전
# -out: error = 없음
#------------------------------------------------------------------
def bump_version(ver):
    m = re.match(r"^(cso-\d{4}\.\d{2})(?:\.(\d+))?$", str(ver or ""))
    if m:
        rev = int(m.group(2) or 0) + 1
        return f"{m.group(1)}.{rev}"
    d = datetime.date.today()
    return f"cso-{d.year}.{d.month:02d}.1"


#------------------------------------------------------------------
# 문자열 정리 도우미
#=> data_editor 셀 값(None/NaN 가능)을 안전한 문자열로.
#
# -in: v = 임의 값
# -out: str = 정리된 문자열(빈값은 "")
# -out: error = 없음
#------------------------------------------------------------------
def _s(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


#------------------------------------------------------------------
# 콤마/줄바꿈 구분 문자열 → 리스트
#=> "a, b\nc" → ["a","b","c"]. 단어/경로조각 입력을 리스트로 바꾼다.
#
# -in: text = 구분 문자열
# -out: list[str]
# -out: error = 없음
#------------------------------------------------------------------
def _split_terms(text):
    return [t.strip() for t in _s(text).replace("\n", ",").split(",") if t.strip()]


#------------------------------------------------------------------
# 키워드 편집 반영(제자리 수정)
#=> 편집 표 행들을 doc['keywords'] 에 반영한다. 기존 id 는 제자리 수정(주석 보존),
#   새 id 는 추가, 표에서 사라진 id 는 삭제한다.
#
# -in: doc  = 규칙 문서
# -in: rows = data_editor 행 리스트({id,name,terms,base_grade,seed})
# -out: 없음(doc 변형)
# -out: error = 없음
#------------------------------------------------------------------
def apply_keyword_edits(doc, rows):
    kw = doc.setdefault("keywords", [])
    by_id = {_s(k.get("id")): k for k in kw}
    seen = set()
    for row in rows:
        rid = _s(row.get("id"))
        if not rid:
            continue
        seen.add(rid)
        terms = _split_terms(row.get("terms"))
        # 파일명 칸은 비워 둘 수 있다 — 비우면 그 규칙은 파일 이름을 보지 않는다
        # (본문 단어를 빌려 쓰지 않는다, 2026-09-08).
        fnames = _split_terms(row.get("filename"))
        grade = _s(row.get("base_grade")) or "S"
        seed = bool(row.get("seed"))
        if rid in by_id:
            g = by_id[rid]
            g["terms"] = terms
            # 비었으면 키를 지운다 — 빈 목록을 남기는 것보다 없는 편이 읽기 쉽다.
            if fnames:
                g["filename"] = fnames
            else:
                g.pop("filename", None)
            g["base_grade"] = grade
            g["seed_eligible"] = seed
            nm = _s(row.get("name"))
            if nm:
                g["name"] = nm
        else:
            item = {"id": rid, "name": _s(row.get("name")) or rid, "terms": terms,
                    "base_grade": grade, "weight": "medium", "seed_eligible": seed}
            if fnames:
                item["filename"] = fnames
            kw.append(item)
    # 표에서 빠진 id 삭제.
    for i in range(len(kw) - 1, -1, -1):
        if _s(kw[i].get("id")) not in seen:
            del kw[i]


#------------------------------------------------------------------
# ko-pii 지원 라벨 목록(방출 라벨 기준)
#=> ko-pii 가 검출 결과로 "실제 내보내는" 라벨 집합을 반환한다. 우선순위:
#    1) ko_pii.labels.ALL_LABELS (공식 레지스트리) — 가장 정확
#    2) 각 patterns 서브모듈의 LABEL 상수(실제 방출 라벨) 수집
#    3) 위가 모두 실패하면 폴백 스냅샷
#   [주의] 과거처럼 patterns '모듈명'을 대문자로 쓰면 prescription→PRESCRIPTION 이
#   되어 실제 라벨 PRESCRIPTION_ID 와 어긋나 '모르는 라벨' 오탐이 났다.
#
# -in: 없음
# -out: list[str] = 정렬된 라벨 목록
# -out: error = 없음(실패 시 폴백)
#------------------------------------------------------------------
def kopii_labels():
    # 1) 공식 레지스트리
    try:
        from ko_pii.labels import ALL_LABELS
        labs = sorted({str(x).upper() for x in ALL_LABELS})
        if labs:
            return labs
    except Exception:
        pass
    # 2) 패턴 모듈의 LABEL 상수(방출 라벨) 수집
    try:
        import pkgutil
        import importlib
        import ko_pii.patterns as _P
        labs = set()
        for _m in pkgutil.iter_modules(_P.__path__):
            try:
                _mod = importlib.import_module(f"ko_pii.patterns.{_m.name}")
                _lab = getattr(_mod, "LABEL", None)
                if _lab:
                    labs.add(str(_lab).upper())
            except Exception:
                continue
        if labs:
            return sorted(labs)
    except Exception:
        pass
    # 3) 폴백 스냅샷
    return list(_KOPII_FALLBACK)


#------------------------------------------------------------------
# PII 규칙 편집 반영(제자리 수정) — ko-pii 라벨 기반
#=> 편집 표 행들을 doc['regex_pii'] 에 반영한다. 검출은 ko-pii 가 하므로 규칙은
#   'label(ko-pii 유형) → 등급 정책'만 담는다(pattern/validate/context 없음).
#
# -in: doc  = 규칙 문서
# -in: rows = data_editor 행({id,name,label,base_grade,bulk_grade,weight,seed})
# -out: 없음(doc 변형)
# -out: error = 없음
#------------------------------------------------------------------
def apply_regex_edits(doc, rows):
    rx = doc.setdefault("regex_pii", [])
    by_id = {_s(r.get("id")): r for r in rx}
    seen = set()
    for row in rows:
        rid = _s(row.get("id"))
        if not rid:
            continue
        seen.add(rid)
        label = _s(row.get("label"))
        base = _s(row.get("base_grade")) or "S"
        bulk = _s(row.get("bulk_grade")) or base
        weight = _s(row.get("weight")) or "medium"
        seed = bool(row.get("seed"))
        if rid in by_id:
            r = by_id[rid]
            r["label"] = label
            r["base_grade"] = base
            r["bulk_grade"] = bulk
            r["weight"] = weight
            r["seed_eligible"] = seed
            nm = _s(row.get("name"))
            if nm:
                r["name"] = nm
        else:
            rx.append({"id": rid, "name": _s(row.get("name")) or rid, "label": label,
                       "base_grade": base, "bulk_grade": bulk, "weight": weight,
                       "seed_eligible": seed})
    for i in range(len(rx) - 1, -1, -1):
        if _s(rx[i].get("id")) not in seen:
            del rx[i]


#------------------------------------------------------------------
# PII 규칙 유효성 검사 — ko-pii 라벨 확인
#=> 저장 전 각 PII 규칙의 label 이 비었거나 ko-pii 가 모르는 값인지 본다.
#   빈 label 은 하드 오류(저장 차단), 미지원 label 은 경고(무검출 위험 안내).
#
# -in: doc = 규칙 문서
# -out: (errors, warnings) = [(id,메시지)] 두 리스트
# -out: error = 없음
#------------------------------------------------------------------
def validate_regex_rules(doc):
    errors, warnings = [], []
    valid = set(kopii_labels())
    for r in doc.get("regex_pii", []):
        rid = _s(r.get("id")) or "(id없음)"
        lab = _s(r.get("label"))
        if not lab:
            errors.append((rid, "ko-pii 라벨(label)이 비어 있습니다"))
        elif lab not in valid:
            warnings.append((rid, f"ko-pii 가 모르는 라벨 '{lab}' — 아무것도 검출되지 않습니다"))
    return errors, warnings


#------------------------------------------------------------------
# 편집 일괄 반영
#=> 기본값(bulk_threshold)·키워드 편집을 doc 에 모두 반영한다(미리보기/저장 공용).
#
# -in: doc          = 규칙 문서
# -in: bulk         = bulk 임계값(int)
# -in: keyword_rows = 키워드 편집 행
# -in: regex_rows   = 정규식 편집 행(없으면 정규식은 건드리지 않음)
# -out: 없음(doc 변형)
# -out: error = 없음
#------------------------------------------------------------------
def apply_all(doc, bulk, keyword_rows, regex_rows=None):
    d = doc.setdefault("defaults", {})
    d["bulk_threshold"] = int(bulk)
    apply_keyword_edits(doc, keyword_rows)
    if regex_rows is not None:
        apply_regex_edits(doc, regex_rows)


#------------------------------------------------------------------
# 미리보기 스캔
#=> 편집된 규칙 문서를 임시 파일로 써서 csoclassify 로더로 읽고, 샘플 텍스트에
#   규칙 스캔을 돌려 결과(등급·히트)를 돌려준다. 저장 전에 효과를 확인하는 용도.
#
# -in: doc      = (편집 반영된) 규칙 문서
# -in: text     = 샘플 텍스트
# -in: src_path = csoclassify 소스 경로(import 위해 sys.path 에 추가)
# -out: GradeSignal = scan_text 결과(grade/hits/confidence)
# -out: error = csoclassify import 실패/스캔 오류 시 예외 전파
#------------------------------------------------------------------
def preview_scan(doc, text, src_path):
    import sys
    if src_path and src_path not in sys.path:
        sys.path.insert(0, src_path)
    from csoclassify.classify import load_rules, scan_text

    fd, tmp = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            _yaml.dump(doc, f)
        rs = load_rules(tmp)
        return scan_text(text, rs)
    finally:
        os.remove(tmp)
