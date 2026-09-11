@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title MpowerClassify PII 검출 테스트

rem ============================================================
rem  D:\분류함\PII 의 PII 시험문서를 분류기에 돌려 검출 결과를 본다.
rem
rem  사용법 1) 그냥 더블클릭        → D:\분류함\PII 전체를 돌린다
rem  사용법 2) 폴더/파일을 끌어다 놓기 → 그 대상만 돌린다
rem
rem  바꿔 쓰고 싶으면 아래 환경변수를 미리 set 해 두면 된다.
rem    CSO_EXE   = 쓸 실행파일 경로
rem    CSO_RULES = 쓸 규칙 yaml 경로
rem ============================================================

rem 이 .bat 는 <저장소>\scripts\ 에 있으므로 한 칸 위가 저장소 뿌리다.
set "ROOT=%~dp0.."

rem ── 실행파일 찾기 ───────────────────────────────────────────
rem  Rust 판을 먼저 본다(이 테스트가 검증하려는 대상이 Rust 검출기라서).
rem  없으면 배포본 파이썬 exe 로 물러선다.
if not defined CSO_EXE (
  if exist "%ROOT%\Rust\target\release\MpowerClassify-rs.exe" (
    set "CSO_EXE=%ROOT%\Rust\target\release\MpowerClassify-rs.exe"
  ) else if exist "%ROOT%\dist-onedir\windows\MpowerClassify.exe" (
    set "CSO_EXE=%ROOT%\dist-onedir\windows\MpowerClassify.exe"
  )
)
if not defined CSO_EXE (
  echo [오류] 실행파일을 찾지 못했습니다.
  echo        아래 중 하나를 만들거나, CSO_EXE 환경변수로 경로를 지정하세요.
  echo          %ROOT%\Rust\target\release\MpowerClassify-rs.exe
  echo          %ROOT%\dist-onedir\windows\MpowerClassify.exe
  echo.
  echo        Rust 판을 만들려면: cd Rust ^&^& cargo build --release
  goto :끝실패
)
if not exist "%CSO_EXE%" (
  echo [오류] CSO_EXE 가 가리키는 파일이 없습니다: %CSO_EXE%
  goto :끝실패
)

rem ── 규칙셋 찾기 ─────────────────────────────────────────────
rem  규칙이 없으면 PII 유형별 등급을 못 정해 결과가 의미 없어진다.
if not defined CSO_RULES (
  if exist "%ROOT%\dist-onedir\windows\cso_rule.yaml" (
    set "CSO_RULES=%ROOT%\dist-onedir\windows\cso_rule.yaml"
  ) else if exist "%ROOT%\resources\policy\cso_rule.yaml" (
    set "CSO_RULES=%ROOT%\resources\policy\cso_rule.yaml"
  )
)
if not exist "%CSO_RULES%" (
  echo [오류] 규칙 yaml 을 찾지 못했습니다. CSO_RULES 환경변수로 지정하세요.
  goto :끝실패
)

rem ── 대상 정하기 ─────────────────────────────────────────────
rem  끌어다 놓은 게 있으면 그것, 없으면 PII 시험문서 폴더.
set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=D:\분류함\PII"
if not exist "%TARGET%" (
  echo [오류] 대상이 없습니다: %TARGET%
  echo        D:\분류함\PII 를 아직 안 만들었다면 _generator\build_pii_docs.py 를 먼저 돌리세요.
  goto :끝실패
)

rem 폴더면 --dir, 파일이면 --file. (폴더 판정은 확장자가 아니라 속성으로 본다)
set "MODE=--file"
if exist "%TARGET%\" set "MODE=--dir"

rem --dir 일 때만 패턴이 의미가 있다. _generator\*.py 와 __pycache__ 를 빼려고 건다.
rem 00_README.md 도 뺀다 — 골든표를 적어 둔 설명서지 시험문서가 아니라서,
rem 넣어 두면 'PII 0건인데 등급 C' 인 줄이 섞여 표를 읽기 어려워진다.
set "GLOBOPT="
if "%MODE%"=="--dir" set "GLOBOPT=--glob *.txt,*.csv"

set "OUTFILE=%TEMP%\cso_pii_결과.json"

echo ============================================================
echo   MpowerClassify PII 검출 테스트
echo ============================================================
echo   실행파일 : %CSO_EXE%
echo   규칙셋   : %CSO_RULES%
echo   대상     : %TARGET%  (%MODE%)
echo   결과저장 : %OUTFILE%
echo ------------------------------------------------------------
echo.

rem --rule-only : 임베딩·전파 없이 규칙만 — PII 검출만 보려는 목적에 맞고 가장 빠르다
rem --with-pii  : 검출된 원문 PII 값까지 출력(가짜 시험문서라 안전하다)
rem   ※ --simple 은 쓰지 않는다. 그걸 켜면 결과에서 pii 배열이 빠져
rem      '무엇을 몇 건 잡았나'를 볼 수 없다(등급과 근거 요약만 남는다).
"%CSO_EXE%" %MODE% "%TARGET%" --rules "%CSO_RULES%" --rule-only --with-pii ^
  %GLOBOPT% --out "%OUTFILE%"
set "RC=%ERRORLEVEL%"

echo ------------------------------------------------------------
if not "%RC%"=="0" (
  echo [실패] 종료코드=%RC%
  echo        1=추출실패 2=임베딩실패 3=인자오류 4=규칙검증실패
  goto :끝실패
)

echo [완료] 결과를 저장했습니다: %OUTFILE%
echo.
echo  파일별 검출 요약  (등급 / 라벨 종수 / 총 건수 / 라벨별 내역)
echo  ------------------------------------------------------------
rem 저장한 JSON 의 pii 배열을 라벨별로 묶어 화면에 보여 준다.
rem D:\분류함\PII\00_README.md 의 골든표와 같은 모양이라 눈으로 바로 대조된다.
powershell -NoProfile -Command ^
  "$docs = Get-Content -Raw -Encoding UTF8 '%OUTFILE%' | ConvertFrom-Json;" ^
  "if ($docs -isnot [array]) { $docs = @($docs) };" ^
  "foreach ($d in ($docs | Where-Object { $_.file })) {" ^
  "  $g = $d.pii | Group-Object label | Sort-Object Name;" ^
  "  $n = ($d.pii | Measure-Object).Count;" ^
  "  $detail = ($g | ForEach-Object { '{0}={1}' -f $_.Name, $_.Count }) -join ', ';" ^
  "  '{0}  {1,-34} {2,2}종 {3,3}건' -f $d.grade, (Split-Path $d.file -Leaf), $g.Count, $n;" ^
  "  if ($detail) { '     ' + $detail } }"

echo.
echo  ※ 라벨 종수·건수가 00_README.md 의 골든표와 다르면 검출기가 바뀐 것입니다.
echo     어떤 규칙이 등급을 올렸는지는 %OUTFILE% 의 signals 를 보세요.
echo.
pause
endlocal
exit /b 0

:끝실패
echo.
pause
endlocal
exit /b 1
