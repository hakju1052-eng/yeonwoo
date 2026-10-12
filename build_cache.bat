@echo off
rem 캐시 갱신 (처음 한 번은 20~30분, 이후에는 새 데이터만 몇 초)
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d %~dp0
python -c "import pandas, pyarrow, openpyxl" 2>nul || (echo 필요한 패키지를 설치합니다... && python -m pip install -r mushroom_app\requirements.txt)
python -m mushroom_app.etl_cli
pause
