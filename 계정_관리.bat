@echo off
chcp 949 >nul
rem ============================================================
rem  계정_관리.bat - 스마트팜 분석 시스템 로그인 계정 만들기·바꾸기
rem   아이디 = 한글 이름 그대로, 비밀번호 = 숫자 8자리 이상
rem   계정 목록: Downloads\unifarm\users.json
rem   비밀번호는 해시로만 저장되고 원문은 파일에도 화면에도 남지 않습니다.
rem   계정을 하나도 안 만들면 로그인 없이 지금처럼 열립니다.
rem ============================================================
setlocal
cd /d "%USERPROFILE%\Downloads\unifarm\app"

:menu
echo.
echo   === 스마트팜 계정 관리 ===
echo    1. 계정 목록 보기
echo    2. 계정 새로 만들기
echo    3. 비밀번호 바꾸기
echo    4. 등급 바꾸기 ^(관리자 / 직원^)
echo    5. 계정 지우기
echo    0. 끝내기
echo.
set "S="
set /p "S=  번호: "
if "%S%"=="1" goto do_list
if "%S%"=="2" goto do_add
if "%S%"=="3" goto do_passwd
if "%S%"=="4" goto do_role
if "%S%"=="5" goto do_remove
if "%S%"=="0" exit /b 0
goto menu

:do_list
python -m mushroom_app.auth list
goto menu

:do_add
set "U="
set /p "U=  이름(한글 그대로, 예: 홍길동): "
if not "%U%"=="" python -m mushroom_app.auth add "%U%"
goto menu

:do_passwd
set "U="
set /p "U=  이름: "
if not "%U%"=="" python -m mushroom_app.auth passwd "%U%"
goto menu

:do_role
set "U="
set /p "U=  이름: "
set "R="
set /p "R=  등급(관리자 또는 직원): "
if not "%U%"=="" python -m mushroom_app.auth role "%U%" "%R%"
goto menu

:do_remove
set "U="
set /p "U=  이름: "
if not "%U%"=="" python -m mushroom_app.auth remove "%U%"
goto menu
