@echo off
REM ------------------------------------------------------------------
REM 문서자동분류 UI 실행 (Streamlit)
REM  - 이 배치는 ui/app.py 를 로컬 브라우저로 띄운다(오프라인).
REM  - 최초 1회: pip install -r requirements.txt
REM ------------------------------------------------------------------
cd /d %~dp0
streamlit run app.py
pause
