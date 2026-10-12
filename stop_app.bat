@echo off
rem 스마트팜 분석 시스템 종료 (앱 포트 + 80 + 8501 에서 듣고 있는 프로세스를 모두 끝냄)
chcp 949 >nul
set "APP_PORT=8501"
if exist "%~dp0app_port.txt" set /p APP_PORT=<"%~dp0app_port.txt"
for %%P in (%APP_PORT% 80 8501) do (
    for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":%%P .*LISTENING"') do (
        echo 포트 %%P 프로세스 %%p 종료
        taskkill /PID %%p /F >nul 2>&1
    )
)
echo 종료했습니다.
timeout /t 3 >nul
