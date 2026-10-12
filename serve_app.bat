@echo off
chcp 65001 >nul
rem 스마트팜 분석 시스템 by p.h.j - 창 없이 백그라운드 실행 (start_app.vbs 가 이 파일을 호출)
rem 포트는 app_port.txt 의 숫자를 쓴다 (없으면 8501). 바꾸려면 set_port.bat 실행
set PYTHONIOENCODING=utf-8
cd /d %~dp0
set "APP_PORT=8501"
if exist "%~dp0app_port.txt" set /p APP_PORT=<"%~dp0app_port.txt"
python -c "import streamlit, pyarrow, openpyxl, altair, anthropic, matplotlib" 2>nul || python -m pip install -r mushroom_app\requirements.txt
echo ===== %DATE% %TIME% start port %APP_PORT% >> "%~dp0app_log.txt"
python -m streamlit run mushroom_app\app.py --server.address 0.0.0.0 --server.port %APP_PORT% --server.headless true --browser.gatherUsageStats false --logger.level warning >> "%~dp0app_log.txt" 2>&1
