@echo off
rem 로그온할 때 스마트팜 분석 시스템을 자동으로(창 없이) 시작하도록 작업 스케줄러에 등록
chcp 949 >nul
set "VBS=%~dp0start_app.vbs"
schtasks /Create /SC ONLOGON /TN "스마트팜 분석 시스템" /TR "wscript.exe \"%VBS%\"" /F
if %ERRORLEVEL% NEQ 0 (
    echo 등록 실패. 관리자 권한 명령창에서 다시 실행해 보세요.
) else (
    echo 등록 완료. 다음 로그온부터 자동 시작됩니다. 지금 바로 시작하려면 start_app.vbs 를 더블클릭하세요.
)
pause
