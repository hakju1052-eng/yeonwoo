@echo off
chcp 949 > nul
setlocal EnableDelayedExpansion
rem ============================================================
rem  통합관리 앱 - GitHub 저장소 연결 (처음 한 번만 실행)
rem   1. Git 이 없으면 설치 (winget)
rem   2. 이 폴더(unifarm\app)를 저장소로 만들고 첫 커밋
rem   3. GitHub 에 올리기 (처음엔 브라우저 로그인 창이 뜹니다)
rem   그 뒤로는 app_watch.ps1 이 1분마다 받아오기(pull)·올리기(push)를 자동으로 합니다.
rem   미리 할 일: github.com 에서 비공개(Private) 저장소를 "비어 있게"(README 없이) 만들어 두세요.
rem   비밀 파일(cluade_api.txt, mail_config.json 등)은 .gitignore 로 제외됩니다.
rem ============================================================
cd /d "%~dp0"
echo.
echo [1/5] Git 확인
where git > nul 2>&1
if errorlevel 1 (
  if exist "%ProgramFiles%\Git\cmd\git.exe" (
    set "PATH=%ProgramFiles%\Git\cmd;!PATH!"
  ) else (
    echo   Git 이 없어 설치합니다. 창이 뜨면 "예"를 누르세요...
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
    if exist "%ProgramFiles%\Git\cmd\git.exe" (
      set "PATH=%ProgramFiles%\Git\cmd;!PATH!"
    ) else (
      echo   설치가 안 됐습니다. https://git-scm.com/download/win 에서 설치한 뒤 이 파일을 다시 실행하세요.
      pause
      exit /b 1
    )
  )
)
git --version

echo.
echo [2/5] 저장소 준비
if not exist ".git" (
  git init -b main > nul
  echo   새 저장소를 만들었습니다.
) else (
  echo   이미 저장소입니다.
)
git config core.autocrlf false
git config core.quotepath false
git config core.longpaths true
git config pull.rebase false
git config push.default simple
for /f "delims=" %%N in ('git config user.name') do set "GNAME=%%N"
if "!GNAME!"=="" (
  git config user.name "unifarm-mainpc"
  git config user.email "hakju1052@gmail.com"
)
if not exist ".gitignore" (
  echo   .gitignore 가 없습니다. 같이 받은 .gitignore 를 이 폴더에 넣고 다시 실행하세요.
  pause
  exit /b 1
)

echo.
echo [3/5] GitHub 저장소 주소
for /f "delims=" %%U in ('git remote get-url origin 2^>nul') do set "URL=%%U"
if "!URL!"=="" (
  echo   GitHub 에서 만든 저장소 주소를 붙여넣으세요 (예: https://github.com/아이디/unifarm-app.git^)
  set /p "URL=  주소: "
  if "!URL!"=="" (
    echo   주소가 없어 중단합니다.
    pause
    exit /b 1
  )
  git remote add origin "!URL!"
) else (
  echo   연결된 곳: !URL!
)

echo.
echo [4/5] 첫 커밋 (비밀 파일 제외 확인)
git add -A
git reset -q -- cluade_api.txt mail_config.json kakao_config.json users.json sessions.json 2> nul
git status --short | findstr /i "cluade_api mail_config.json kakao_config users.json sessions.json" > nul
if not errorlevel 1 (
  echo   !!! 비밀 파일이 올라가려 합니다. 중단합니다. .gitignore 를 확인하세요.
  pause
  exit /b 1
)
git commit -q -m "통합관리 앱 등록 (메인PC)" 2> nul
if errorlevel 1 echo   올릴 변경이 없습니다(이미 커밋됨).
echo   파일 수:
git ls-files | find /c /v ""

echo.
echo [5/5] GitHub 에 올리기 (처음이면 브라우저에 GitHub 로그인 창이 뜹니다)
git push -u origin main
if errorlevel 1 (
  echo   바로 올리지 못했습니다. 저장소에 이미 파일이 있으면 합쳐서 다시 시도합니다...
  git pull --no-rebase --no-edit --allow-unrelated-histories -X ours origin main
  git push -u origin main
  if errorlevel 1 (
    echo   실패했습니다. 주소·로그인·저장소 권한을 확인한 뒤 다시 실행하세요.
    pause
    exit /b 1
  )
)
echo ok > "%~dp0git_sync.on"
echo.
echo 완료. 이제 1분마다 자동으로 받아오기/올리기를 합니다 (기록: watch_log.txt).
echo 같이 고칠 사람은 GitHub 저장소 Settings - Collaborators 에서 초대하세요.
pause
