@echo off
rem ============================================================
rem  client_setup.bat - 다른 PC(살균솥 PC·사무실 PC)에서 한 번 실행
rem   1) hosts 에 smartfarm 이름 등록 → http://smartfarm 으로 접속 (관리자 권한 필요, 자동으로 물어봄)
rem   2) 바탕화면에 "스마트팜 분석 시스템" 바로가기 (주소창 없는 앱 창, 전체 화면)
rem   3) 원하면 로그온할 때 자동으로 열리게 등록 (터치스크린 PC용)
rem  서버 주소를 바꾸려면 아래 SERVER_IP 만 고친다.
rem ============================================================
chcp 949 >nul
set "SERVER_IP=192.168.0.62"
set "NAME=smartfarm"
set "URL=http://%NAME%"

rem --- 관리자 권한 확인 (hosts 수정 때문) ---
net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo 관리자 권한이 필요합니다. 확인 창이 뜨면 "예"를 누르세요.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

rem --- 1) hosts ---
set "HOSTS=%SystemRoot%\System32\drivers\etc\hosts"
findstr /I /C:"%NAME%" "%HOSTS%" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    powershell -NoProfile -Command "(Get-Content '%HOSTS%') | Where-Object { $_ -notmatch '\s%NAME%\s*$' } | Set-Content '%HOSTS%'"
)
echo %SERVER_IP% %NAME%>> "%HOSTS%"
ipconfig /flushdns >nul
echo [1/3] hosts 등록: %SERVER_IP% %NAME%

rem --- 2) 바탕화면 바로가기 (Edge 앱 창; 없으면 Chrome; 둘 다 없으면 기본 브라우저) ---
set "BR="
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "BR=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "BR=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "BR=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
for /f "usebackq delims=" %%d in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESK=%%d"
set "LNK=%DESK%\스마트팜 분석 시스템.lnk"
if defined BR (
    powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%'); $s.TargetPath='%BR%'; $s.Arguments='--app=%URL% --start-maximized'; $s.IconLocation='%BR%,0'; $s.Save()"
) else (
    powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%'); $s.TargetPath='%URL%'; $s.Save()"
)
echo [2/3] 바탕화면 바로가기 만듦: %LNK%

rem --- 3) 자동 실행 ---
set /p AUTO=[3/3] 로그온할 때 자동으로 열리게 할까요? (y/n): 
if /I "%AUTO%"=="y" (
    copy /Y "%LNK%" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\" >nul
    echo      시작프로그램에 넣었습니다. 다음 로그온부터 자동으로 열립니다.
) else (
    echo      자동 실행은 등록하지 않았습니다.
)
echo.
echo 완료. 바탕화면 바로가기를 눌러 %URL% 이 열리는지 확인하세요.
echo (안 열리면 서버 PC에서 앱이 켜져 있는지, 포트가 80인지 - set_port.bat - 확인)
start "" "%LNK%"
pause
