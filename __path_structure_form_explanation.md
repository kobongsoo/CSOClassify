# path, structure, form이 "매칭 값이 없다"는 의미

## 요약
코드에는 **세 신호 모두 정의되어 있지만**, 현재 `doc_rule.yaml`에서는 이들을 채우지 않았기 때문에:
- `form`: 기본값 = `None` → 신호 생성 안 함
- `structure`: 기본값 = `()` (빈 튜플) → 신호 생성 안 함  
- `paths`: 기본값 = `()` (빈 튜플) → 신호 생성 안 함

따라서 **미래용 확장 기능**이거나 **설계 단계에서 예약해 둔 필드**입니다.

---

## 각 신호의 역할 & 현재 상태

| 신호 | doc_rule.yaml 에서 역할 | 코드 정의 | 현재 상태 |
|------|----------------------|---------|---------|
| **title_terms** | 첫 줄 → 단어 목록 | ✅ tuple | ✅ 활용 중 |
| **head_terms** | 앞 400자 → 단어 목록 | ✅ tuple | ✅ 활용 중 |
| **terms** | 문서 전체 → 단어 목록 | ✅ tuple | ✅ 활용 중 |
| **filename** | 파일명 → 단어 목록 | ✅ tuple | ✅ 활용 중 |
| **form** | 서식 필드어 세트 (예: 갑·을·제O조·계약기간 모두 있어야 계약서) | ✅ FormSpec \| None | ❌ None(미사용) |
| **structure** | 구조 정규식 (예: `제\d+조`, 금액표 패턴) | ✅ tuple | ❌ ()(미사용) |
| **paths** | 폴더 경로 (예: `/contract/`, `/board/notice/`) | ✅ tuple | ❌ ()(미사용) |

---

## 미래용이 정의된 이유

doctype.py 파일 처음 주석(1-15줄)을 보면:

```
#   [재설계 1단계 반영] 내용 신호를 위치별로 쪼개고 점수를 누적한다.
#     · title  : 첫 비어있지 않은 줄       — 가장 강한 증거
#     · head   : 앞 head_chars 자(표제부)  — 강한 증거
#     · form   : 서식 필드어 세트          — 강한 증거     ← 예약됨
#     · structure : 구조 정규식            — 보조(단독 채택 불가)  ← 예약됨
#     · body   : 문서 전체 terms           — 약한 증거(단독 채택 불가)
#     · name/path : 파일명·경로            — 중간 증거
```

**재설계의 7개 신호 설계**에서:
- `title`, `head`, `form`, `structure`, `body`, `name`, `path` 가 이론적으로 정의됨
- 코드에는 모두 구현해 뒀음 (호환성 & 향후 확장용)
- 하지만 현재 `doc_rule.yaml` 규칙은 이 중 `title`, `head`, `terms(body)`, `filename(name)` 4개만 채움

---

## 코드의 방어 로직

### form 신호 (doctype.py 393-400줄)
```python
ok, matched = _match_form(text, rule.form, rule.exclude)
if ok:
    add("form", ...)
```
- `rule.form` 이 `None`이면 `_match_form()` 첫 줄에서:
  ```python
  if form is None or not form.usable or not text:
      return False, []
  ```
  조기 반환 → 신호 추가 안 함

### structure 신호 (doctype.py 405-410줄)
```python
st = _match_structure(text, rule.structure)
if st:
    add("structure", ...)
```
- `rule.structure` 이 `()` (빈 튜플)이면:
  ```python
  if not patterns or not text:
      return []
  ```
  빈 리스트 반환 → `if st:` 거짓 → 신호 추가 안 함

### paths 신호 (doctype.py 428-433줄)
```python
ph = _match_paths(norm_path, rule)
if ph:
    add("path", ...)
```
- `rule.paths` 이 `()` (빈 튜플)이면:
  ```python
  if not rule.paths:
      return []
  ```
  빈 리스트 반환 → `if ph:` 거짓 → 신호 추가 안 함

---

## 결론

| 상태 | 의미 |
|------|------|
| **코드에는 있음** | 3가지 신호 모두 `_scan_rule()` 함수와 `DoctypeRule` 클래스에서 정의 완료 |
| **yaml에서는 비움** | 현재 규칙에서 `form`, `structure`, `paths` 을 지정하지 않음 |
| **동작** | 기본값(`None` 또는 `()`)을 사용 → 신호 생성 안 함 |
| **용도** | 설계 문서(재설계 시리즈)에서는 7개 신호를 정의했지만, MVP(초기 구현)는 4개만 우선 활용 |

**미래에 doc_rule.yaml에 이 필드들을 채우면 자동으로 활용된다**는 점에서 좋은 설계입니다.
