@echo off
chcp 949 > nul
rem ============================================================
rem  통합관리 앱 자동 재시작 등록 - 처음 한 번만 실행
rem   1분마다 app_watch.vbs 가 확인해서
rem   - mushroom_app 안의 .py 파일이 바뀌면(업데이트) 앱을 끄고 다시 켬
rem   - 앱이 꺼져 있으면 다시 켬
rem   - RESTART_REQUEST.txt 파일이 있으면 다시 켬
rem   기록: watch_log.txt
rem  해제: schtasks /delete /tn "통합관리 앱 자동 재시작" /f
rem ============================================================
set "VBS=%~dp0app_watch.vbs"
schtasks /create /tn "통합관리 앱 자동 재시작" /tr "wscript.exe \"%VBS%\"" /sc minute /mo 1 /f
if errorlevel 1 (
  echo 등록에 실패했습니다. 관리자 권한 명령창에서 다시 실행해 보세요.
) else (
  echo 등록했습니다. 이제 업데이트하면 1~2분 안에 앱이 저절로 다시 켜집니다.
  echo 지금 한 번 확인합니다...
  wscript.exe "%VBS%"
  timeout /t 5 > nul
  if exist "%~dp0watch_log.txt" type "%~dp0watch_log.txt"
)
pause
