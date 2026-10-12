@echo off
rem 스마트팜 분석 시스템 by p.h.j 실행 (창 보이는 방식). 다른 PC: http://이PC의IP  (포트 80이면 :포트 생략)
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d %~dp0
set "APP_PORT=8501"
if exist "%~dp0app_port.txt" set /p APP_PORT=<"%~dp0app_port.txt"
python -c "import streamlit, pyarrow, openpyxl, altair, anthropic, matplotlib" 2>nul || (echo 필요한 패키지를 설치합니다... && python -m pip install -r mushroom_app\requirements.txt)
python -m streamlit run mushroom_app\app.py --server.address 0.0.0.0 --server.port %APP_PORT% --server.headless true --browser.gatherUsageStats false
pause
