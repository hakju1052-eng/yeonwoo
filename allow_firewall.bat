@echo off
rem ============================================================
rem  allow_firewall.bat - 서버 PC(노트북) 방화벽에 앱 포트(80, 8501, 경영 자료 8502) 허용 규칙 추가
rem  다른 PC에서 http://192.168.0.92:8501 (경영 자료 :8502) 이 안 열릴 때 한 번 실행 (관리자 확인 창 "예")
rem  사내 네트워크가 "공용"으로 잡혀 있으면 "개인"으로 바꿔 규칙이 적용되게 함 (2026-10-13 유선 연결 뒤 추가)
rem ============================================================
chcp 949 >nul
net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo 관리자 권한이 필요합니다. 확인 창이 뜨면 "예"를 누르세요.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
netsh advfirewall firewall delete rule name="스마트팜 분석 시스템 (TCP 80)" >nul 2>&1
netsh advfirewall firewall delete rule name="스마트팜 분석 시스템 (TCP 8501)" >nul 2>&1
netsh advfirewall firewall delete rule name="통합관리 경영 자료 (TCP 8502)" >nul 2>&1
netsh advfirewall firewall add rule name="스마트팜 분석 시스템 (TCP 80)" dir=in action=allow protocol=TCP localport=80 profile=private,domain
netsh advfirewall firewall add rule name="스마트팜 분석 시스템 (TCP 8501)" dir=in action=allow protocol=TCP localport=8501 profile=private,domain
netsh advfirewall firewall add rule name="통합관리 경영 자료 (TCP 8502)" dir=in action=allow protocol=TCP localport=8502 profile=private,domain
echo.
echo 네트워크 종류가 "공용"이면 "개인"으로 바꿉니다 (사내망이라 안전, 위 규칙은 개인 네트워크에만 적용됨)...
powershell -NoProfile -Command "Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' } | ForEach-Object { Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private; Write-Host ('  ' + $_.InterfaceAlias + ' : 공용 -> 개인') }"
echo.
echo 현재 네트워크 종류 (개인 이어야 위 규칙이 적용됨. "공용"이면 설정 ^> 네트워크 ^> 이더넷/Wi-Fi 에서 "개인"으로):
powershell -NoProfile -Command "Get-NetConnectionProfile | Select-Object Name, NetworkCategory | Format-Table -AutoSize"
echo.
echo 앱 포트 상태:
netstat -ano | findstr /R /C:":80 .*LISTENING" /C:":8501 .*LISTENING" /C:":8502 .*LISTENING"
echo.
echo 완료. 다른 PC에서 http://192.168.0.92:8501 (경영 자료는 :8502) 을 다시 열어 보세요.
pause
