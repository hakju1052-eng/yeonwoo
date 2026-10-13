@echo off
chcp 65001 >nul
rem 경영 자료 전용 화면 (v1.29.0) - 본 앱과 따로 다른 포트(office_port.txt, 없으면 8502)에서 창 없이 실행
rem app_watch.ps1 이 1분마다 확인해서 꺼져 있으면 켜고, 코드가 바뀌면 다시 켭니다.
set PYTHONIOENCODING=utf-8
cd /d %~dp0
set "OFFICE_PORT=8502"
if exist "%~dp0office_port.txt" set /p OFFICE_PORT=<"%~dp0office_port.txt"
echo ===== %DATE% %TIME% start port %OFFICE_PORT% >> "%~dp0office_log.txt"
python -m streamlit run mushroom_app\office_app.py --server.address 0.0.0.0 --server.port %OFFICE_PORT% --server.headless true --browser.gatherUsageStats false --server.enableWebsocketCompression true --logger.level warning >> "%~dp0office_log.txt" 2>&1
