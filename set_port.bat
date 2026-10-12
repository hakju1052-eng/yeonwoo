@echo off
rem ============================================================
rem  set_port.bat - 앱 포트 바꾸기 (기본 80 → 주소에서 :8501 생략 가능)
rem  사용: set_port.bat        → 80 으로
rem        set_port.bat 8501   → 원래대로
rem  포트가 다른 프로그램에 쓰이고 있으면 바꾸지 않는다.
rem ============================================================
chcp 949 >nul
set "P=%~1"
if "%P%"=="" set "P=80"
netstat -ano | findstr /R /C:":%P% .*LISTENING" | findstr /V /C:"python" >nul
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":%P% .*LISTENING"') do (
    for /f "tokens=1" %%n in ('tasklist /FI "PID eq %%p" /NH ^| findstr /V "python"') do (
        if not "%%n"=="" if not "%%n"=="INFO:" (
            echo [중지] 포트 %P% 를 이미 다른 프로그램이 쓰고 있습니다: %%n ^(PID %%p^)
            echo        다른 포트를 쓰거나 그 프로그램을 끄고 다시 실행하세요.
            pause
            exit /b 1
        )
    )
)
echo %P%> "%~dp0app_port.txt"
echo 포트를 %P% 로 설정했습니다. 앱을 다시 시작합니다...
call "%~dp0stop_app.bat"
wscript.exe "%~dp0start_app.vbs"
timeout /t 8 >nul
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /C:"IPv4"') do set "IP=%%a"
set "IP=%IP: =%"
if "%P%"=="80" (
    echo 접속 주소:  http://localhost   ^(이 PC^)   /   http://%IP%   ^(다른 PC^)
) else (
    echo 접속 주소:  http://localhost:%P%   ^(이 PC^)   /   http://%IP%:%P%   ^(다른 PC^)
)
echo 다른 PC에서 안 열리면 방화벽에서 python.exe 를 개인 네트워크에 허용했는지 확인하세요.
pause
