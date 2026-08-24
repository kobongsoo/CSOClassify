# csoclassify-rs — CSOClassify 코어의 Rust 포팅

CSOClassify(Python)의 **추출 + 규칙 분류 + 임베딩 전파(Signal B)** 코어를 Rust로 옮긴 것.
목표는 **정적 단일 바이너리·이식성**(CentOS7/glibc 무관). 무거운 자산(ONNX 모델·onnxruntime·
pdfium)은 바이너리에 넣지 않고 **exe 옆 외부 파일로 런타임 로딩**(pdfium 방식)해 바이너리는 작게 유지.
기존 Python 소스는 건드리지 않고 이 폴더에만 포팅했다.

## 분류 모드 (원본과 동일)

| 모드 | 동작 | parity(실측) |
|---|---|---|
| **기본** | 규칙 분류 + 보류문서 자동 전파(seed 있으면 on) | **100%** (교육 34/34) |
| `--rule-only` | 규칙만(임베딩·전파 없음, 가장 빠름) | 99.5% (211중 210) |
| `--vector-only` | 규칙 없이 벡터-seed 비교만(--seeds 필수) | **100%** (AI 10/10) |

> 기본 모드는 **exe 옆 `cso_seed.jsonl`(또는 `--seeds`)이 있으면 자동으로 전파를 켠다** — 원본과 동일.
> 임베딩은 e5-small-ko ONNX 를 onnxruntime 으로 돌리며, Python(pypdfium2 아님, onnxruntime)과
> **임베딩 벡터가 완전 일치**(코사인=1.000000, probe 3종 검증). 전파 알고리즘(near-dup 상속·k-최근접
> 유사도 가중 다수결)도 propagate.py 그대로.
> `--rule-only` 의 유일한 1건 불일치는 **ko-pii(PII) 문제가 아니라 html 추출 한계**(Marp 슬라이드의
> 누출 JS "mou"에 L2 키워드 'MOU' 대소문자무시 오매칭; Python 은 DOM 파서로 JS 제거).

## 배포 구성 (exe 옆 외부 자산 — pdfium 방식)

| 자산 | 크기 | 용도 |
|---|---|---|
| `csoclassify-rs.exe` | **5.3MB** | 바이너리(규칙·추출·전파 로직 + 임베딩 사전) |
| `onnxruntime.dll` | 16MB | 임베딩 추론 런타임(전파/--vector-only 시) |
| `models/e5-small-ko/` | 129MB | ONNX 모델 + tokenizer(전파/--vector-only 시) |
| `pdfium.dll` | 7MB | PDF 추출 시 |
| `cso_seed.jsonl` | 0.6MB | 전파 비교 기준 seed(있으면 기본 전파 on) |

> 모델·런타임 경로 탐색: `CSO_MODEL`/`ORT_DYLIB_PATH` 환경변수 → **exe 옆** `models/e5-small-ko/`·
> `onnxruntime.dll`. 없으면 전파를 조용히 생략(규칙 결과만). `--rule-only` 는 이들 없이도 동작.

## 무엇을 포팅했나

**규칙 엔진(전부 포팅)** — `cso_rules.yaml`을 그대로 읽어(데이터는 포팅 대상 아님):
- L1 PII + 결합식별성(combo), L2 키워드(제외어·bulk 상향), 민감정보군(Signal F),
  스탬프(머리/반복 게이트, Signal E), 경로(Signal C), 파일명(Signal D)
- 보수적 max + fail-safe 융합(`fuse`), 신뢰도표, 등급서열(C>S>O)
- 레코드/신호 JSON 이 Python 과 동일 스키마

**추출(PoC 지원)** — text · html(태그제거) · docx · xlsx · pptx · hwpx (zip+XML) · **pdf(pdfium-render)**
- docx/pptx 는 **문단 인식**(런 이어붙임)으로 Python 과 동일 — 런 경계로 값이 갈라지는 오탐 방지.
- xlsx 는 공유문자열표 + **워크시트 인라인 문자열(inlineStr)** 까지 읽음(sharedStrings 없는 파일 대응).
- PDF 는 pdfium 동적 라이브러리 필요(아래). pypdfium2 의 `pdfium.dll` 을 그대로 재사용 가능.

**PII 검출(ko-pii 20라벨 전면 포팅 — 체크섬·문맥앵커·행정구역 사전·겹침해소)**:

| 라벨 | 방식 |
|---|---|
| **RRN·FRN** | 형식+세기(성별자리)+실날짜+**미래일자 배제(GS1 880 바코드 차단)**+체크섬→확신도, RRN prefix패턴/법인번호 예외 |
| **CARD** | 구분자/무구분 + **BIN 첫자리 화이트리스트{2,3,4,5,6,9}** + 길이-브랜드 일관성 + Luhn |
| **BUSINESS_REG·CORP_REG** | 국세청/법인 가중합 체크섬 |
| **PASSPORT** | 대문자 prefix 화이트리스트(PP/PM/…/M/S/G…) + 8자리(all-zero 제외) |
| **VEHICLE** | 용도한글 화이트리스트(~50자) + 4자리(0000 제외) + **뒤 단위어(원·억·명…) 거부** |
| **PHONE** | 모바일/유선 구분(위험도차) | 
| EMAIL·URL | 패턴 |
| **IP** | 옥텟+예약대역+버전/섹션 문맥게이팅 |
| **ACCOUNT** | 은행명(~60)/‘계좌’ 앵커 + 10~16자리 |
| **NATIONALITY** | 국가사전(82)+조사제거+‘인’ 접미 |
| **ADDRESS** | 4브랜치(도로명·지번·대화체·단독행정구역) + **행정구역 사전**(광역17·기초206·법정동 1만개)로 (광역+기초) 조합검증·앵커 |
| **DRIVER_LICENSE** | 하이픈형/무하이픈(키워드앵커) + 지역코드11~28 |
| **MEDICAL_INSURANCE** | 11자리 + 건강/의료보험·보험증 키워드(25자) |
| **PRESCRIPTION_ID** | 발행번호(날짜검증)+영문접두ID+의료기관기호, 각 키워드앵커 |
| **EDI_DRUG** | 9/13자리 + 키워드앵커 + 국가코드880/881/888 |
| **COURT_CASE** | 연도+부호문자(대법원 예규 화이트리스트)+일련번호 |
| **PNU** | 19자리 + 시도코드 + 필지구분·본번 검증 |

**겹침 해소(ko-pii core.overlap)** — 전 라벨 span 을 위험도→확신도→길이→시작 우선으로 채택,
겹치면 드롭. (예: GS1 바코드가 RRN·EDI 둘 다 걸릴 때 원본과 동일하게 정리.)

## 빌드 / 사용

> **처음 빌드한다면 → [빌드 가이드(Rust)](../doc/csoclassify-빌드가이드-rust.html)** — Windows·Linux 전 과정을
> 단계별로(툴체인 설치·외장 자산 조달·배포본 조립·zip/tgz 패키징·트러블슈팅·복붙용 명령) 정리한 문서다.
> 원본 파이썬판은 [빌드 가이드(Python)](../doc/csoclassify-빌드가이드-python.html).

```bat
cargo build --release           REM → target\release\csoclassify-rs.exe (2.3MB)
csoclassify-rs --dir "D:\분류함" --rules cso_rules.yaml --simple --nosummary --format jsonl
csoclassify-rs --file a.docx    --rules cso_rules.yaml
```
옵션: `--file/--dir · --rules · --format json|jsonl · --out · --simple · --hash · --with-pii · --rule-only · --vector-only · --auto-propagate · --seeds · --summary · --nosummary · --failsafe`
- `--with-pii` : [프라이버시 예외] 검출된 **원문 PII 값**을 `pii:[{label,value,start,end}]` 로 함께 저장(기본 off, 건수만).
- `--rule-only` : 규칙만(임베딩·전파 없음). `--vector-only` 와 배타.
- `--vector-only` : 규칙 없이 벡터-seed 비교만(`--seeds` 또는 exe 옆 cso_seed.jsonl 필수). `--rule-only` 와 배타.
- `--auto-propagate` : 보류 문서 전파(seed 있으면 기본 on이라 명시 불필요).
- `--seeds <cso_seed.jsonl>` : 전파 기준 seed(미지정 시 exe 옆 cso_seed.jsonl).

리눅스 바이너리 — **CentOS7(glibc 2.17)에서 gnu 타깃으로 빌드**한다. 실행파일에 `RPATH=$ORIGIN` 을 박아
`libonnxruntime.so` 가 요구하는 신형 `libstdc++.so.6` 를 exe 옆에서 찾게 하는 것이 핵심이다
(빠뜨리면 실행 시 `CXXABI_1.3.8 not found`).
```bash
export RUSTFLAGS='-C link-arg=-Wl,-rpath,$ORIGIN -C link-arg=-Wl,--disable-new-dtags'
cargo build --release        # → target/release/csoclassify-rs (5.4MB, glibc 2.17+ 어디서나 동작)
```
> musl 정적 빌드(`x86_64-unknown-linux-musl`)는 현 빌드서버에 `musl-gcc` 가 없어 `tokenizers` 의
> C 코드(oniguruma)를 컴파일할 수 없다. 또 `libonnxruntime.so`·`libpdfium.so` 가 외부 glibc 라이브러리라
> musl 로도 완전 정적이 되지 않는다. 실용적으로는 CentOS7 gnu 빌드가 호환 범위가 더 넓다.

## PDF 실행 요건 (pdfium)

PDF 추출은 pdfium 동적 라이브러리가 필요하다(pdfium-render). 다음 순서로 찾는다:
`CSO_PDFIUM` 환경변수 → **exe 옆 `pdfium.dll`**(리눅스 `libpdfium.so`) → 시스템 라이브러리.
가장 쉬운 방법: pypdfium2 가 쓰는 `pdfium.dll` 을 exe 옆에 복사(≈7MB).
```
copy dist-onedir\windows\_internal\pypdfium2_raw\pdfium.dll  target\release\
```
※ 이 pdfium.dll 은 pypdfium2 와 동일 엔진이라 Python(pypdfium2)과 추출 결과가 일치.

## 아직 안 된 것 (별도 과제 / 문서화된 갭)

- **구형 바이너리 포맷 추출**: doc·xls·ppt(OLE)·hwp5·이미지 → 추출 미지원(스킵).
  (Python 은 olefile/snf 로 처리. Rust 는 자체 OLE 파서 후속.) parity 공통집합에서 제외됨.
  ※ 단 이 파일들도 **경로·파일명 신호는 확장자 무관하게 동작**한다.
- **html 추출(정규식 기반)의 한계**: `<script>` 내부에 마크업 문자열이 박힌 Marp/JS-heavy
  export 는 정규식으로 완전 분리 불가 → 누출된 JS 조각이 L2 키워드에 오매칭될 수 있음
  (실측 유일 1건 diff). 완전 해소는 실제 DOM 파서 필요(단일 바이너리 목표와 상충 → 보류).
- **ko-pii 미포팅 라벨(0 반환)**: NAME/PERSON·BIRTH·HEALTH 등 — CSOClassify 의 cso_rules.yaml
  이 등급판정에 쓰지 않는 라벨이라 포팅 대상 아님(현 20라벨로 등급 parity 충족).
- **PERSON 사전(surnames/hanja/romanization) 미포팅**: 위와 같은 이유로 스코프 밖.
- **압축 확장 · 상주 데몬**: 후속(현재 압축파일은 추출 스킵, 임베딩은 매 실행 모델 로드).
  ※ **임베딩 전파(Signal B)·--vector-only 는 포팅 완료**(외부 ONNX 로딩).

## parity 검증 방법

```
Rust:   csoclassify-rs --dir <t> --rules <r> --simple --nosummary --format jsonl
Python: csoclassify.exe --dir <t> --hybridparse --rule-only --simple --nosummary --format jsonl
```
두 결과를 파일명 기준으로 등급 비교. **라벨별 건수 검증**은 ko-pii `detect_all(text, include=<20라벨>)`
결과와 Rust 검출 건수를 직접 대조(probe 문서로 20라벨 모두 건수 일치 확인).
