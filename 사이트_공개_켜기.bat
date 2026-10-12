@echo off
chcp 949 >nul
title 스마트팜 - 인터넷 공개 켜기
rem ============================================================
rem  사이트_공개_켜기.bat
rem   메인PC(앱이 돌아가는 노트북)에서 두 번 누르면 됩니다.
rem   관리자 권한이 필요해서, 아니면 스스로 다시 띄웁니다.
rem   끄려면 사이트_공개_끄기.bat
rem ============================================================

rem --- 관리자 권한 확인, 아니면 관리자로 다시 실행 ---
net session >nul 2>&1
if not "%errorlevel%"=="0" (
    echo 관리자 권한이 필요합니다. 창이 하나 더 뜨면 "예"를 눌러 주세요.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

rem --- 앱 포트 읽기 (없으면 8501) ---
set "PORT=8501"
if exist "%~dp0app_port.txt" set /p PORT=<"%~dp0app_port.txt"

rem --- tailscale 설치 확인 ---
set "TS=tailscale"
where tailscale >nul 2>&1
if not "%errorlevel%"=="0" (
    if exist "%ProgramFiles%\Tailscale\tailscale.exe" (
        set "TS=%ProgramFiles%\Tailscale\tailscale.exe"
    ) else (
        echo.
        echo [안내] Tailscale 이 설치돼 있지 않습니다.
        echo        https://tailscale.com/download/windows 에서 받아 설치하고
        echo        구글 계정 등으로 로그인한 뒤 이 파일을 다시 눌러 주세요.
        echo.
        pause
        exit /b 1
    )
)

echo.
echo  앱 포트 %PORT% 을 인터넷에 공개합니다...
echo.
set "OUT=%TEMP%\smartfarm_funnel.txt"
"%TS%" funnel --bg %PORT% > "%OUT%" 2>&1
type "%OUT%"

rem --- "Funnel is not enabled" 처럼 허용 링크가 나왔으면 브라우저로 바로 열어 준다 ---
set "LINK="
for /f "usebackq tokens=1" %%L in (`findstr /C:"https://login.tailscale.com" "%OUT%"`) do set "LINK=%%L"
if not "%LINK%"=="" (
    echo.
    echo  아직 끝난 게 아닙니다 - 계정에서 한 번 허용해 줘야 합니다.
    echo  브라우저를 엽니다. 그 화면에서 허용(Enable) 을 누른 뒤,
    echo  이 파일을 한 번 더 누르면 주소가 나옵니다.
    echo.
    start "" "%LINK%"
    pause
    exit /b 1
)
findstr /C:"HTTPS" /C:"MagicDNS" "%OUT%" >nul 2>&1
if "%errorlevel%"=="0" (
    echo.
    echo  MagicDNS 와 HTTPS 인증서를 켜야 합니다.
    echo  브라우저에서 DNS 설정을 엽니다. 두 항목을 켜고 이 파일을 다시 눌러 주세요.
    echo.
    start "" "https://login.tailscale.com/admin/dns"
    pause
    exit /b 1
)

echo.
echo  === 지금 상태 ===
"%TS%" funnel status
echo.
echo  위에 https:// 로 시작하는 주소가 보이면 성공입니다.
echo  그 주소를 두 사내망 어디서든, 휴대폰에서도 열 수 있습니다.
echo.
echo  * 메인PC가 켜져 있어야 열립니다.
echo  * 끄려면 같은 폴더의 사이트_공개_끄기.bat 를 누르세요.
echo.
pause
