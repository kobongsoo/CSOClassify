# CSOClassify

한국어(다국어) **수집 문서를 C/S/O(기밀·민감·공개)로 자동 분류**하는 로컬 CLI 도구입니다.
사이냅 문서필터(`snf_exe`)로 텍스트를 뽑고, **여러 판정 신호**(내용·민감정보·보안분류 스탬프·경로·파일명)를
보수적으로 융합해 등급을 매깁니다. 임베딩 벡터는 보류 문서 구제·seed 비교(전파)에 씁니다.
분류가 **기본 동작**이며, 벡터만 필요하면 `--embed` 를 줍니다. 서버·인터넷 없이 로컬에서 동작하고
PyInstaller `onedir` 로 패키징합니다.

> 상세: [실행 가이드](doc/csoclassify-실행가이드.html) · [규칙셋 필드 레퍼런스](doc/CSO_Rule.html) · [설계서](plan/설계서.md)

## 빠른 사용

```bash
# C/S/O 자동 분류 (기본 동작 — 모드 플래그 불필요). 출력 기본값 = JSON
csoclassify.exe --file "D:\docs\보고서.hwp"

# 폴더 전체 분류 → 결과 파일 (가장 많이 쓰는 형태)
csoclassify.exe --dir "D:\collected" -r --embed-needed --format jsonl --out result.jsonl

# 임베딩 벡터만 뽑기(분류 없이)
csoclassify.exe --embed --file "D:\docs\보고서.hwp"
```

`--file` 단건은 JSON 객체 하나, `--dir` 배치는 레코드들의 **JSON 배열**을 출력합니다.
줄단위 스트리밍/후처리는 `--format jsonl`(문서당 1줄).

## 판정 신호 (보수적 max 융합)

문서 하나에 여러 신호가 붙고, 최종 등급은 **가장 민감한 등급**으로 융합됩니다(각 신호는 등급을
**올릴 수만** 있고 함부로 내리지 않음). 어느 신호로도 못 정하면 **보류(none)** → 검토 대상.

| 신호 | 무엇을 보나 | 비고 |
|---|---|---|
| `rule` | 본문의 **PII 유형**(ko-pii 20종) + **기밀사전 키워드** + **결합식별성(combo)** | 내용 기반 |
| `sensitive` | **법상 민감정보**(건강·유전·성생활·사상/정치·노조·범죄경력·인종) 키워드 → C | 일반 키워드와 분리된 독립 신호 |
| `stamp` | 문서에 찍힌 **보안분류 표식**("대외비/기밀"→C, "내부용"→S) | 머리·반복일 때만 인정(오탐↓) |
| `path` | 파일이 놓인 **폴더/서버**(예: `/인사/`, `//hr-server/`) | 텍스트 안 엶 |
| `name` | **파일명** 속 키워드 | 참고용 |
| `embed` | 보류 문서를 seed와 **임베딩 유사도**로 구제 | `--propagate`(2차 패스) |

- **PII 20종(ko-pii)**: RRN·FRN·CARD·ACCOUNT·PASSPORT·DRIVER_LICENSE·ADDRESS·MEDICAL_INSURANCE·
  PRESCRIPTION_ID·EDI_DRUG·VEHICLE·COURT_CASE·PNU·NATIONALITY(민감/기밀) · PHONE·EMAIL·BUSINESS_REG·
  CORP_REG·URL·IP(공개, 대량이면 상향). 검출은 ko-pii(패턴·체크섬·문맥·우회방어 내장)가 담당.
- **결합식별성(combo)**: 개별론 낮아도 여러 PII가 한 문서에 모이면(결합용이성) 상향 — 예:
  `주민번호+계좌 → C`, `전화·이메일·주소 2종+ → S`. `rule` 신호의 `layer:"COMBO"` 히트로 표시.
- 정책(유형·키워드·조합·경로·등급)은 모두 `cso_rules.yaml` 또는 UI 규칙설정에서 조정 —
  필드 의미는 [CSO_Rule.html](doc/CSO_Rule.html) 참조.

> **프라이버시 불변식**: 매칭된 **원문 PII 값은 저장하지 않습니다**(유형 id·건수만).
> `--with-pii` 를 명시할 때만 검출 원문값을 **`--out` 파일에** 싣고(로그엔 `[pii N detected]` 만), 취급에 주의.

## 분류 방식 선택 (배타 옵션)

| 옵션 | 동작 | 임베딩 | seed |
|---|---|---|---|
| (기본) | 규칙으로 등급 → 필요할 때만 임베딩(보류·seed 후보) | 선택적 | 선택 |
| `--rule-only` | 규칙(내용·민감정보·스탬프·경로·파일명)**만**으로 분류 | 안 함(가장 빠름) | 불필요 |
| `--vector-only` | 규칙 검사 **없이** 문서 벡터를 seed와 비교(전파)해서만 분류 | 전량 임베딩 | **필수** `--seeds` |

## 주요 옵션 (전체는 `--help`)

| 옵션 | 설명 | 기본 |
|---|---|---|
| `--file` / `--dir` | 단일 파일 / 폴더 배치 (구 `-file`/`-dir` 호환) | — |
| `-r`, `--recursive` | `--dir` 배치 시 하위 폴더까지 | off |
| `--glob` | 배치 필터(예: `"*.hwp,*.pdf"`) | `*` |
| (모드 없음) | **C/S/O 분류**(기본). 예전 `--classify` 는 생략 가능 | 분류 |
| `--rule-only` / `--vector-only` | 규칙만 / 벡터-seed 비교만 (배타) | — |
| `--embed` | 분류 대신 **임베딩 벡터만** 출력 | — |
| `--text-only` | 벡터 없이 **추출 텍스트만** | — |
| `--rules` | 규칙셋 경로 | exe 옆 `cso_rules.yaml` |
| `--failsafe [등급]` | 어느 신호로도 못 정한 문서의 기본등급(값 생략=S) | 미부여(보류) |
| `--embed-needed` | **보류·seed 후보**에만 임베딩(확정문서 생략 → 빠름) | — |
| `--with-vector` | **모든 문서**에 임베딩 | — |
| `--auto-propagate` | 분류 직후 보류 문서를 seed로 **전파까지** | — |
| `--propagate <jsonl>` | 1차 레코드에 임베딩 전파 적용(모델 불필요) | — |
| `--seeds <경로>` | 전파 비교 기준 seed(`cso_seed.jsonl`) | 내부 seed |
| `--with-text` | 결과에 추출 텍스트(`text`) 포함 | off(프라이버시) |
| `--with-pii` | **[주의]** 검출 원문 PII 값을 `pii` 필드로 저장 | off |
| `--format` | `text` / `json` / `jsonl` | **`json`** |
| `--out` | 결과 저장 파일 | stdout |
| `--max-tokens` / `--overlap` | 청크 크기 / 겹침 | 512 / 32 |
| `--progress` | 진행상황(stderr) 표시 | off |
| `--daemon` / `--no-daemon` | 상주 데몬 모드 | **on** |
| `--status` / `--stop` | 데몬 상태 / 정지 | — |

## 처리 흐름

```
문서 → [사이냅 snf_exe] 텍스트 → 정제 → 규칙 스캔(rule·sensitive·stamp·path·name 융합) → 등급 레코드
                                     └(선택) 토큰 청킹 → [ONNX 임베더] 벡터 → 전파(보류 구제)
```

## 상주 데몬 모드 (기본 on)

반복 호출 시 모델을 매번 로딩하지 않도록, 첫 호출에서 데몬을 자동 기동해 모델을 메모리에
상주시킵니다(설계서 결정5). 2회차부터 모델 로딩 시간은 0 입니다.

- 모델별 별도 데몬(레지스트리 키=모델 별칭), loopback + 토큰 인증.
- 동시 요청 경쟁은 **엔드포인트/잠금 원자적 생성**으로 데몬 1개만 생존.
- 유휴 `--idle-timeout`(기본 600초) 지나면 스스로 종료.
- 기동/통신 실패 시 **in-process 자동 폴백** → 멈추지 않음.
- 단발/디버그는 `--no-daemon`(또는 `CSOCLASSIFY_DAEMON=0`).

## 개발 셋업

```bash
pip install -r requirements.txt          # ko-pii, onnxruntime, tokenizers, numpy, pyyaml ...
$env:PYTHONPATH="src"; python -m csoclassify --file "D:\docs\보고서.hwp"   # 소스 실행
python -m pytest tests/ -q               # 테스트(사이냅 통합은 snf_exe 있을 때만)
```

### 임베딩 모델을 ONNX 로 준비 (최초 1회, 인터넷 필요)

런타임(exe)은 `onnxruntime`+`tokenizers` 만 쓰지만, 모델을 처음 만들 때는 `optimum`/`transformers`/`torch` 가 필요합니다.

```bash
pip install "optimum[onnxruntime]" transformers
python scripts/export_onnx.py   --model e5-small-ko   # fp32 model.onnx(470MB) 생성
python scripts/quantize_onnx.py --model e5-small-ko   # int8 경량화(→118MB, 랭킹 보존)
```
→ `resources/models/e5-small-ko/{model.onnx, tokenizer.json, ...}` 생성(저장소 미포함 — 용량/라이선스).

## 빌드 · 배포 (onedir)

정식 배포는 **onedir 2종(Windows·Linux)** 입니다. 실행본체(`csoclassify`)와 파이썬 런타임(`_internal/`)이
한 폴더에 있고, **규칙셋·모델·사이냅은 그 옆에 외장 리소스**로 둡니다(재빌드 없이 규칙 교체 가능,
매 실행 압축해제 없어 빠름 ~2초).

```bash
# Windows (로컬)
pyinstaller build/csoclassify.spec        # → dist/csoclassify/{csoclassify.exe, _internal/}

# Linux (반드시 리눅스에서 — 크로스컴파일 불가). conda 예:
pip install pyinstaller onnxruntime tokenizers numpy pyyaml ko-pii==1.15.2
pyinstaller build/csoclassify.spec        # → dist/csoclassify/csoclassify (ELF)
```

빌드 후, 실행파일 옆에 외장 리소스를 배치합니다.

```
dist-onedir/windows/                    dist-onedir/linux/
├─ csoclassify.exe + _internal/         ├─ csoclassify-onedir-linux.tgz  (리눅스에서 tar 풀기: 심볼릭링크 보존)
├─ cso_rules.yaml                       ├─ cso_rules.yaml
├─ synap/windows/{snf_exe.exe,          ├─ synap/linux/snf_exe
│              snf_win.dll}             ├─ models/e5-small-ko/
└─ models/e5-small-ko/                  └─ README.txt
```

- **Windows**: `dist-onedir/windows/csoclassify.exe --file "문서"` — 바로 실행.
- **Linux**: 폴더를 리눅스로 복사 → `tar xzf csoclassify-onedir-linux.tgz`(**반드시 리눅스에서** —
  Windows에서 풀면 `_internal`의 .so 심볼릭링크가 깨짐) → `chmod +x csoclassify synap/linux/snf_exe`
  → `./csoclassify --file "문서"`.
- 규칙셋이 없으면 실행 시 어디에 둘지 안내하고 종료(코드 3).
- 리눅스 `snf_exe` 는 정적 링크라 `.so`/헤더 없이 단독 실행.

> onefile(단일 exe) 방식(`build/csoclassify-onefile.spec`)은 스펙만 남겨두고 **현재 빌드 대상이 아닙니다**
> (매 실행 임시폴더 해제로 느리고 데몬이 임시 `snf_exe` 참조에 실패). onedir만 유지·배포합니다.

### 환경변수 · 로그

| 변수 | 설명 |
|---|---|
| `CSOCLASSIFY_POLICY_DIR` | 규칙셋 폴더(`--rules` 로도 지정) |
| `CSOCLASSIFY_MODELS_DIR` / `CSOCLASSIFY_SYNAP_DIR` | 모델 / 사이냅 위치 재정의 |
| `CSOCLASSIFY_DAEMON=0` | 데몬 전역 off |
| `CSOCLASSIFY_STAMP_HEAD_CHARS` / `_REPEAT_MIN` | 스탬프 머리범위(400) / 반복횟수(2) |

- 기본 로그: `<exe폴더>/log/csoclassify-YYYYMMDD.log`(실행 커맨드·파일별 결과 JSON, 벡터는 축약).
  경로는 `--log`, 화면 출력·DEBUG는 `-v`. 데몬도 같은 파일에 `[PID …]` 로 구분해 기록.

## 검토·규칙편집 UI (Streamlit)

```bash
pip install -r ui/requirements.txt
streamlit run ui/app.py        # 또는 ui/실행.bat
```
분류 결과 대시보드·문서목록·검토 큐·seed 관리·**규칙 편집**(주석 보존)·균형/중복 리포트를 제공합니다.
수동 오버라이드는 원본 불변, `cso_override.jsonl` 에 append-only 로 기록.

## 프로젝트 구조

```
src/csoclassify/
  cli.py            CLI 진입점(모드 분기, 데몬 우선+폴백)
  pipeline.py       추출→정제→임베딩 오케스트레이션(+시간기록)
  clean.py chunk.py 텍스트 정제 · 토큰 청킹
  classify/         분류 정책층
    rules.py        규칙 스캔(PII·combo·기밀사전·민감정보·스탬프) + 규칙셋 로드
    context.py      경로(path)·파일명(name) 신호
    fuse.py         보수적 max 융합 + fail-safe
    engine.py       분류 레코드 조립 + 임베딩 전파(propagate_records)
    propagate.py    seed 인덱스 · 임베딩 유사도 전파
  extract/          사이냅 추출기(base + synap_exe)
  embed/            ONNX 임베더(base + onnx_embedder)
  daemon/           상주 데몬(server/client/ipc/registry)
resources/policy/cso_rules.yaml   C/S/O 규칙셋(외장)
ui/                 Streamlit 검토·규칙편집 UI
build/*.spec        PyInstaller 스펙
doc/                실행 가이드 · 규칙셋 레퍼런스 · 작업기록 (HTML)
```

## 현재 상태

- ✅ 추출(사이냅)·정제·청킹·CLI·데몬(싱글턴/폴백)·출력·시간측정.
- ✅ C/S/O 분류: 5신호(rule·sensitive·stamp·path·name) 융합 + 결합식별성(combo) + 임베딩 전파(embed).
- ✅ PII 20종(ko-pii) · 분류 방식 옵션(`--rule-only`/`--vector-only`) · 검토/규칙편집 UI.
- ✅ onedir 배포(Windows·Linux) · 규칙셋/모델/사이냅 외장화.
- ✅ 단위/통합 테스트 통과.

## 라이선스 · 주의

- 사이냅 문서필터(`snf_exe`)의 재배포는 사이냅 라이선스 조건 확인이 필요합니다(설계서 §14).
  이 저장소에는 사이냅 바이너리·임베딩 모델·자격증명을 포함하지 않습니다.
- `--with-text` / `--with-pii` 는 원문 텍스트·PII를 결과 파일에 남기므로 민감문서 처리 시 주의하세요.
