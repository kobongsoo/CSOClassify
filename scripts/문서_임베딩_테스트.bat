@echo off
chcp 65001 >nul
title MpowerClassify 문서 임베딩 테스트
cd /d "%~dp0"

rem ============================================================
rem  이 .bat 는 같은 폴더의 MpowerClassify.exe 를 호출하는 테스트용 실행기다.
rem  사용법 1) 이 .bat 아이콘 위로 문서 파일을 끌어다 놓기(드래그&드롭)
rem  사용법 2) 그냥 더블클릭 → 문서 경로를 물어봄
rem ============================================================

if not exist "MpowerClassify.exe" (
  echo [오류] 이 .bat 와 같은 폴더에 MpowerClassify.exe 가 없습니다.
  echo        MpowerClassify.exe 가 있는 폴더에 이 파일을 두고 실행하세요.
  echo.
  pause
  exit /b 1
)

set "DOC=%~1"
if "%DOC%"=="" (
  echo ============================================
  echo   MpowerClassify 문서 임베딩 테스트
  echo ============================================
  echo.
  echo  문서 파일 경로를 입력하고 Enter 를 누르세요.
  echo  (또는 이 .bat 위로 문서를 드래그해도 됩니다)
  echo.
  set /p "DOC=문서 경로: "
)

echo.
echo [실행] MpowerClassify.exe --embed --file "%DOC%"
echo ------------------------------------------------------------
MpowerClassify.exe --embed --file "%DOC%"
set "RC=%ERRORLEVEL%"
echo ------------------------------------------------------------
if "%RC%"=="0" (
  echo [완료] 위 결과는 JSON 형식입니다(file/dim/vector/elapsed_ms 등).
  echo        벡터를 공백구분 텍스트로 보려면 뒤에 --format text 를 붙이세요.
) else (
  echo [실패] 종료코드=%RC%  (1=추출실패 2=임베딩실패 3=인자오류)
)
echo.
pause
