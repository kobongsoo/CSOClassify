# 문서분류체계 내보내기 예시 (doc_classification_export.*.json)

MPOWER 의 `DOC_CLASSIFICATION` 테이블을 내려받은 JSON 과 **똑같은 모양**의 예시 파일들이다.
고객사 실데이터는 저장소에 올리지 않으므로(`.gitignore`), 형식을 확인하거나 업무분류 축을
시험해 보려면 이 파일들을 쓴다. 내용은 전부 **가짜**다 — 실제 회사·기관의 분류가 아니다.

트리 모양은 `D:\분류함` 에 실제로 쌓여 있는 문서들(제안서·매뉴얼·확인서·보도자료·판결문·
교안·표준절차서 등 440건)을 보고, 그런 문서를 다루는 조직이라면 어떤 분류를 둘지로 짰다.

## 파일

| 파일 | 노드 | 깊이 | 어떤 조직인가 | 분류함의 어느 문서에서 왔나 |
|---|---|---|---|---|
| `.itsec.json` | 31 | 3단 | IT·보안 솔루션 기업 | 엠파워제안서 · 제품매뉴얼 · 구축확인서 · 정비정검확인서 · 라이센스증서 · 회사규정 |
| `.public.json` | 28 | 3단 | 정부·공공기관 | 3.정부보도자료 · 업무계획/기본계획 · 제안요청서(HWP) |
| `.finance.json` | 23 | 3단 | 은행·증권·연금 | 통화신용정책 · 인가절차 보도자료 · 연구보고서 · 퇴직연금 포럼 |
| `.medical.json` | 18 | 2단 | 병원·의료기관 | 진료지침 · 질환안내서 · 약제정보 · 학술논문 |
| `.legal.json` | 21 | 3단 | 법무법인·법무팀 | 민사/형사/행정/특허 판결문 · 채널사협력계약 |
| `.education.json` | 19 | 2단 | 교육기관·기업교육 | LGCNS 교안 · 강의자료 · 실습 노트북 · 평가 시험지 |
| `.manufacturing.json` | 23 | 3단 | 제조·연구소 | 표준절차서 · 계측기 매뉴얼/명령어레퍼런스 · 소요제기서 · 안전관리규정 |
| `.commerce.json` | 12 | 2단 | 이커머스·마케팅 | 산업보고서 · 트렌드리포트 · 솔루션소개자료 |

## 일부러 넣어 둔 까다로운 경우

형식만 맞는 예시가 아니라 **로더·검증을 실제로 흔들어 보는** 값들을 섞어 두었다.

- **3단 분기** (`itsec` `public` `finance` `legal` `manufacturing`)
  `제품/기술 > 매뉴얼 > 사용자매뉴얼` 처럼 대-중-소 3단. 화면의 전체경로 표시를 시험한다.
- **2단만** (`medical` `education` `commerce`) — 얕은 트리도 정상이다.
- **자식 없는 최상위** (`commerce` 의 `정산자료`) — 대분류 자체가 잎인 경우.
- **폐지된 분류 `status: 0`** (`itsec` 의 `구버전매뉴얼`)
  새 분류로 제안되지 않지만 이름은 남는다 — 예전에 그 분류로 확정된 문서를 나중에도
  화면에 띄울 수 있어야 하기 때문이다. 골격 생성 시 규칙이 만들어지지 않는 것도 확인용이다
  (`itsec` 은 31노드지만 규칙 골격은 30건).
- **부모째 폐지된 갈래** (`public` 의 `종이민원`)
  부모가 `status: 0` 인데 자식은 `1` 인, 현장에서 실제로 나오는 어긋난 상태다.
- **`/` 가 들어간 제목** (`영업/제안` `기술/개발`) — 경로 구분자 `>` 와 헷갈리지 않는지.

## 쓰는 법

```bash
python scripts/export_taxonomy.py \
  --input  resources/policy/samples/doc_classification_export.itsec.json \
  --output resources/policy/doc_taxonomy.yaml \
  --scaffold-doc-rule resources/policy/doc_rule.yaml
```

`--scaffold-doc-rule` 을 붙이면 분류마다 `terms` 가 빈 규칙 골격까지 함께 나온다.
**골격만으로는 아무것도 분류되지 않는다** — 화면(설정 ③ 판단 기준)에서 키워드를 채워야 한다.

배포된 exe 에서는 같은 일을 이렇게 한다.

```bash
csoclassify --export-taxonomy --export-input <json> --scaffold-doc-rule
```

## 칸 설명

| 칸 | 뜻 |
|---|---|
| `source` | 어디서 뽑았는지(스키마.테이블). 감사 추적용 |
| `export_server` | 뽑아온 관리 화면 주소. 예시 파일은 가짜 값이다 |
| `node_count` | `nodes` 개수. 실제 개수와 다르면 로드 단계에서 실패한다 |
| `dc_id` | 분류 고유 ID. `doc_rule.yaml` 의 `node:` 가 이 값을 가리킨다 |
| `parent_dc_id` | 부모 `dc_id`. 최상위는 `null` |
| `order_num` | 같은 부모 안에서의 표시 순서 |
| `title` | 화면에 보이는 이름 |
| `status` | `1` 사용 · `0` 사용 안 함 |
| `path_ids` · `path_titles` · `path` | 최상위부터 자기까지의 전체경로. 부모를 따라 계산한 값이라 손으로 고치면 어긋난다 |

## 실데이터와 다른 점

실제 `doc_classification_export.json` 은 `export_server` 에 사내 관리 화면 주소가 들어가고,
`source` 가 실제 스키마 이름이다. 예시 파일은 둘 다 가짜로 채워 두었다.
