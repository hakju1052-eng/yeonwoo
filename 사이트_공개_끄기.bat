@echo off
chcp 949 >nul
title 스마트팜 - 인터넷 공개 끄기
rem ============================================================
rem  사이트_공개_끄기.bat - 인터넷 공개를 멈춥니다.
rem   끈 뒤에도 사내망 접속(192.168.0.62:8501)은 그대로입니다.
rem ============================================================
net session >nul 2>&1
if not "%errorlevel%"=="0" (
    echo 관리자 권한이 필요합니다. 창이 하나 더 뜨면 "예"를 눌러 주세요.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
set "TS=tailscale"
where tailscale >nul 2>&1
if not "%errorlevel%"=="0" set "TS=%ProgramFiles%\Tailscale\tailscale.exe"
"%TS%" funnel --bg off
echo.
echo  인터넷 공개를 껐습니다. 사내망 접속은 그대로입니다.
echo.
"%TS%" funnel status
echo.
pause
