# CSOClassify

한국어 문서를 **C/S/O(기밀·민감·공개) 자동 분류**하고, 필요하면 **임베딩 벡터**로도 변환하는 로컬 CLI 도구입니다.
사이냅 문서필터(snf_exe)로 텍스트를 뽑고, 규칙·경로·파일명 신호로 등급을 판정합니다(임베딩은 보류 문서 구제·seed 비교에 사용). 분류가 **기본 동작**이며, 벡터만 필요하면 `--embed` 를 줍니다.
서버·인터넷 없이 로컬에서 동작하며, PyInstaller 로 `csoclassify.exe` 단일 도구로 패키징합니다.

> 상세 설계 근거는 [plan/설계서.md](plan/설계서.md) 참조.

## 빠른 사용

```bash
# C/S/O 자동 분류 (기본 동작 — 모드 플래그 불필요)
csoclassify.exe -file D:\AAA.hwp
```
→ 문서마다 등급(C/S/O·보류) 레코드를 JSON 으로 출력.

```bash
# 임베딩 벡터만 뽑기 — --embed
csoclassify.exe --embed -file D:\AAA.hwp
```
→ `파일명 <TAB> 벡터...` 를 표준출력으로, 처리 시간을 표준에러로 출력.

```bash
# 폴더 배치로 벡터 저장
csoclassify.exe --embed -dir D:\docs --glob "*.hwp" --format jsonl --out vecs.jsonl
```
→ 폴더 배치 처리, 파일당 1줄 JSON 저장.

## 처리 흐름

```
문서 → [사이냅 snf_exe] 텍스트 → 정제 → 토큰 청킹 → [ONNX 임베더] 벡터 → 출력(+시간)
```

## 주요 옵션 (전체는 `--help`)

| 옵션 | 설명 | 기본 |
|---|---|---|
| `-file` / `-dir` | 단일 파일 / 폴더 배치 | — |
| (모드 없음) | **C/S/O 분류**(기본). 예전 `--classify` 는 생략 가능(붙여도 무시) | 분류 |
| `--embed` | 분류 대신 **임베딩 벡터만** 출력 | — |
| `--model` | 임베딩 모델 별칭 | `e5-small-ko` |
| `--format` | `text` / `json` / `jsonl` | `text` |
| `--out` | 결과 저장 파일 | stdout |
| `--per-chunk` | 청크별 벡터 | off(문서벡터) |
| `--max-tokens` / `--overlap` | 청크 크기 / 겹침 | 256 / 32 |
| `--no-normalize` | L2 정규화 끄기 | 정규화 on |
| `--timing` / `--no-timing` | 처리 시간 출력 | on |
| `--save-text [DIR]` | 추출 텍스트 보존 | off(자동삭제) |
| `--text-only` | 벡터 없이 추출 텍스트만 | off |
| `--daemon` / `--no-daemon` | 상주 데몬 모드 | **on** |
| `--status` / `--stop` | 데몬 상태 / 정지 | — |

## 상주 데몬 모드 (기본 on)

반복 호출 시 모델을 매번 로딩하지 않도록, 첫 호출에서 데몬을 자동 기동해 모델을
메모리에 상주시킵니다(설계서 결정5). 2회차부터 모델 로딩 시간은 0 입니다.

- 모델별로 별도 데몬(레지스트리 키 = 모델 별칭), loopback + 토큰 인증.
- 동시 요청 경쟁 조건은 **엔드포인트/잠금 원자적 생성**으로 데몬 1개만 생존.
- 유휴 `--idle-timeout`(기본 600초) 지나면 스스로 종료.
- 데몬 기동/통신 실패 시 **in-process 로 자동 폴백** → 기능이 멈추지 않음.
- 단발/디버그는 `--no-daemon`(또는 `CSOCLASSIFY_DAEMON=0`).

## 개발 셋업

```bash
pip install -r requirements.txt
```

### 1) 임베딩 모델을 ONNX 로 준비 (최초 1회, 인터넷 필요)

런타임(exe)은 `onnxruntime`+`tokenizers` 만 쓰지만, 모델을 처음 만들 때는
`optimum`/`transformers`/`torch` 가 필요합니다.

```bash
pip install "optimum[onnxruntime]" transformers
python scripts/export_onnx.py --model e5-small-ko      # fp32 model.onnx(470MB) 생성
python scripts/quantize_onnx.py --model e5-small-ko    # int8 로 경량화(→118MB)
```
→ `resources/models/e5-small-ko/{model.onnx, tokenizer.json, ...}` 생성.

**경량화(권장)**: `quantize_onnx.py` 가 model.onnx 를 int8 로 동적 양자화한다
(470MB→118MB, ~4배). 원본은 `model.fp32.onnx` 로 백업되며 exe 번들에서는 제외된다.
검증상 int8 는 retrieval 랭킹을 보존한다(절대 유사도는 fp32 대비 ~0.96). 절대값
정밀도가 중요하면 fp16(235MB, fp32와 동일 수준)로 대체 가능.

### 2) 소스로 실행

```bash
# Windows PowerShell
$env:PYTHONPATH="src"; python -m csoclassify -file D:\AAA.hwp
```

### 3) 테스트

```bash
python -m pytest tests/ -q
```
(사이냅 통합 테스트는 `resources/synap/snf_exe.exe` 가 있을 때만 수행)

### 4) exe 빌드 — 두 가지 방식

**(A) onedir (기본·자기완결): 모델 포함**
```bash
pyinstaller build/csoclassify.spec
```
→ `dist/csoclassify/csoclassify.exe` (폴더 배포, 총 ~227MB). 사이냅 바이너리·int8 모델이
함께 번들. 폴더째 zip 으로 전송하면 ~90MB. 데몬 완전 안정. **가장 단순.**
※ C/S/O 규칙셋 `cso_rules.yaml` 은 exe 에 내장하지 않으므로 **`csoclassify.exe` 와 같은
폴더에 함께 둔다**(`resources/policy/cso_rules.yaml` 을 그 폴더로 복사). 재빌드 없이 규칙만 교체 가능.

**(B) onefile + 외부 모델/사이냅 (작은 단일 exe)**
```bash
python scripts/pack_model.py --model e5-small-ko     # dist-model/e5-small-ko.tar.xz (75MB)
pyinstaller build/csoclassify-onefile.spec              # dist-onefile/csoclassify.exe (~34MB, 코드만)
```
배포 폴더 구성(5개):
```
csoclassify.exe                # 34MB (코드+onnxruntime만)
cso_rules.yaml              # C/S/O 규칙셋(외장) — exe 옆 필수. 재빌드 없이 교체 가능
synap/snf_exe.exe           # 사이냅 바이너리(외부)
synap/snf_win.dll
e5-small-ko.tar.xz          # 75MB (외부 모델, 최초 1회 캐시에 해제)
```
런타임이 규칙셋은 **exe 옆 `cso_rules.yaml`**, 사이냅은 **exe 옆**에서, 모델은 **tar.xz 를 최초 1회**
`%LOCALAPPDATA%\CSOClassify\models\` 에 풀어 사용한다. 이후 실행은 캐시 재사용.
**규칙셋이 없으면** 실행 시 어디에 둬야 하는지 안내 메시지를 내고 종료한다(원시 스택 아님).

**왜 onefile 은 모델/사이냅을 밖에 두나?** onefile 은 실행마다 내부를 임시(_MEI)폴더에
푸는데, ① 112MB 모델을 매 호출(데몬 클라이언트 포함) 푸는 비용이 크고, ② 상주 데몬이
임시폴더의 `snf_exe.exe` 를 참조하면 정리 레이스로 사라져 실패한다(실측 확인). 둘 다 밖으로
빼면 exe 가 작아지고 데몬이 안정된다. **모델을 exe 안에 압축해 넣는 방식은 효과가 없다**
— PyInstaller 가 이미 zlib 로 압축하고(112→75MB, int8 한계), 매 실행 해제 비용은 그대로다.

**경로 재정의(선택)**: `CSOCLASSIFY_MODELS_DIR`(모델 폴더/아카이브 위치),
`CSOCLASSIFY_SYNAP_DIR`(사이냅 폴더), `CSOCLASSIFY_POLICY_DIR`(규칙셋 폴더) 환경변수로
위치를 바꿀 수 있다. 규칙셋은 `--rules <파일경로>` 로 실행마다 지정할 수도 있다.

**(C) 리눅스(CentOS/Ubuntu x86-64) 빌드**

PyInstaller 는 크로스컴파일이 안 되므로 **리눅스 실행파일은 리눅스에서 빌드**해야 한다.
코드는 이미 OS 분기가 되어 있어(사이냅 이름·폴더: Windows=`synap/windows/snf_exe.exe`,
Linux=`synap/linux/snf_exe`) 아래 절차만 따르면 된다.

```bash
# 0) 준비: 프로젝트를 리눅스로 전송(src/, build/, resources/) + 리눅스 사이냅 바이너리 배치
#    resources/synap/linux/snf_exe  (v4.29.0 centOS 빌드, 정적 링크). 실행권한 부여:
chmod +x resources/synap/linux/snf_exe

# 1) 파이썬 환경(conda 예): 3.12 + 빌드/런타임 의존성 설치
conda create -y -n csobuild python=3.12
conda activate csobuild
pip install pyinstaller onnxruntime tokenizers numpy pyyaml ko-pii==1.15.2

# 2) 빌드 — onedir(모델까지 번들, 자기완결) 또는 onefile(코드만, 모델/사이냅 외장)
pyinstaller build/csoclassify.spec            # → dist/csoclassify/csoclassify (ELF)
#   또는
pyinstaller build/csoclassify-onefile.spec    # → dist/csoclassify (ELF, 코드만)

# 3) (선택) 배포용 이름으로 리네임
mv dist/csoclassify dist/csoclassify_linux
chmod +x dist/csoclassify_linux
```

- `collect_synap()` 이 빌드 OS 를 감지해 **자동으로 `synap/linux/snf_exe` 를 번들**한다
  (onedir). onefile 은 사이냅을 번들하지 않으므로, 배포 시 exe 옆 `synap/linux/snf_exe`
  를 함께 둔다(런타임이 `resources.synap_exe_path()` 로 그 경로를 찾음).
- 리눅스 `snf_exe` 는 **정적 링크**라 `.so`/`libsnf.a`/헤더 없이 단독 실행된다(그 파일 하나면 충분).
- 배포 구성(리눅스): `csoclassify_linux` + `cso_rules.yaml` + `synap/linux/snf_exe` +
  `e5-small-ko.tar.xz`. 실행파일과 `snf_exe` 에 실행권한(`chmod +x`) 필수.
- ko-pii·onnxruntime·numpy·규칙엔진은 크로스플랫폼이라 코드 수정 없이 동작한다.

### 배포 구조 (정리본)

조립된 배포는 두 형태가 있다. **둘 다 리소스(사이냅·모델·규칙셋)를 exe 옆 '외장'으로 두는
구성**이며, 차이는 실행파일 패키징(onefile vs onedir)뿐이다.

| 배포 | 폴더 | 실행파일 | 웜 시작속도 | 특징 |
|---|---|---|---|---|
| **onedir (권장·기본)** | `dist-onedir/` | 폴더(exe + `_internal/`) | **~2초** | 매 실행 압축해제 없음 → 빠름. 대화형/반복 실행에 적합 |
| onefile | `dist-pkg/` | 단일 exe | ~4.6초 | 실행마다 임시폴더에 자기 압축해제(느림). 단일 파일로 배포 간편 |

공통 외장 리소스(각 폴더 안, 실행파일과 같은 위치):
- `cso_rules.yaml` — C/S/O 규칙셋 (재빌드 없이 교체 가능)
- `synap/<os>/` — 텍스트 추출기 (windows=`snf_exe.exe`+`snf_win.dll`, linux=`snf_exe`)
- 모델 — onedir=`models/e5-small-ko/`(해제본, 첫 실행 지연 없음), onefile=`e5-small-ko.tar.xz`(최초 1회 캐시에 해제)

```
dist-onedir/                          dist-pkg/  (onefile)
├─ windows/                           ├─ csoclassify.exe            # Windows
│  ├─ csoclassify.exe + _internal/    ├─ csoclassify_linux.exe      # Linux(리눅스에서 실행)
│  ├─ cso_rules.yaml                  ├─ cso_rules.yaml
│  ├─ synap/windows/                  ├─ e5-small-ko.tar.xz
│  └─ models/e5-small-ko/             ├─ synap/{windows,linux}/
└─ linux/                             └─ 샘플.hwp · 사용법.txt · 문서_임베딩_테스트.bat
   ├─ csoclassify-onedir-linux.tgz    # 리눅스에서 tar 풀기(심볼릭링크 보존)
   ├─ cso_rules.yaml · synap/linux/
   ├─ models/e5-small-ko/
   └─ README.txt                      # 리눅스 설치·실행 안내
```

**실행**
- Windows onedir: `dist-onedir/windows/csoclassify.exe --file "문서"` — 바로 실행.
- Linux onedir: `dist-onedir/linux/` 를 리눅스로 복사 → `tar xzf csoclassify-onedir-linux.tgz`
  (**반드시 리눅스에서** — Windows 에서 풀면 `_internal` 의 .so 심볼릭링크가 깨진다) →
  `chmod +x csoclassify synap/linux/snf_exe` → `./csoclassify --file "문서"`.
- onefile(`dist-pkg`)은 실행파일 하나만 옮겨도 되지만 매 실행 시작이 느리다. **반복 실행이면 onedir 권장.**

### 로그

- 기본으로 **exe 옆 `log/` 폴더**에 날짜별 상세 로그를 남긴다: `<exe폴더>/log/csoclassify-YYYYMMDD.log`
  (소스 실행 시 프로젝트 루트 아래 `log/`). 실행/추출·정제 규모/모델 로딩/청크 수/단계별 ms/오류 스택이 기록된다.
- 경로를 바꾸려면 `--log <경로>`. 화면에도 실시간으로 보려면 `-v`(DEBUG).
- **데몬 모드도 로그가 남는다** — 클라이언트가 데몬 기동 시 `--log` 경로를 함께 넘겨 같은 파일에
  기록하며, 각 줄의 `[PID …]` 로 클라이언트/데몬을 구분한다.
- 로그 파일은 날짜별로 나뉘어 무한정 커지지 않는다. exe 폴더가 쓰기 불가(예: Program Files)면
  로그가 안 남을 수 있으니 그때는 `--log` 로 쓰기 가능한 경로를 지정한다.

```bash
csoclassify.exe -file 문서.hwp                         # log/csoclassify-날짜.log 에 기록
csoclassify.exe -file 문서.hwp --log D:\logs\run.txt    # 지정 경로에 기록
csoclassify.exe -file 문서.hwp -v --no-daemon           # 화면에도 상세 로그
```

## 프로젝트 구조

```
src/csoclassify/
  cli.py            CLI 진입점(모드 분기, 데몬 우선+폴백)
  pipeline.py       추출→정제→임베딩 오케스트레이션(+시간기록)
  config.py         모델 레지스트리/기본값
  resources.py      번들/소스 리소스 경로 해석(_MEIPASS)
  timing.py         단계별 처리시간 측정
  clean.py          텍스트 정제      chunk.py  토큰 청킹(순수)
  extract/          사이냅 추출기(base + synap_exe)
  embed/            ONNX 임베더(base + onnx_embedder)
  daemon/           상주 데몬(server/client/ipc/registry)
resources/
  synap/            snf_exe.exe, snf_win.dll
  models/e5-small-ko/  model.onnx, tokenizer.json  (export_onnx.py 로 생성)
scripts/export_onnx.py   모델 → ONNX 변환(개발용)
build/csoclassify.spec      PyInstaller 스펙
```

## 현재 상태

- ✅ 텍스트 추출(사이냅), 정제, 청킹, CLI, 데몬(서버/클라이언트/싱글턴/폴백), 출력, 시간측정 구현.
- ✅ 단위/통합 테스트 통과(청킹·정제·레지스트리 싱글턴·사이냅 추출).
- ⏳ 임베딩 벡터 실측은 `scripts/export_onnx.py` 로 ONNX 모델을 만든 뒤 가능
  (모델 파일은 저장소에 포함하지 않음 — 용량/라이선스).

## 라이선스/주의

- 사이냅 문서필터(snf_exe/snf_win.dll)의 재배포는 사이냅 라이선스 조건을 확인해야 합니다(설계서 §14).
- `--save-text` 는 원문 텍스트를 디스크에 남기므로 민감문서 처리 시 주의하세요.
