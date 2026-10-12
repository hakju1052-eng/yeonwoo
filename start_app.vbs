' 스마트팜 분석 시스템 - 창 없이 실행. 더블클릭하면 백그라운드에서 서버가 뜬다.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
port = "8501"
If fso.FileExists(dir & "\app_port.txt") Then
    port = Trim(fso.OpenTextFile(dir & "\app_port.txt", 1).ReadLine)
End If
' 이미 그 포트가 열려 있으면 중복 실행하지 않음
Set ex = sh.Exec("cmd /c netstat -ano | findstr /R /C:"":" & port & " .*LISTENING""")
out = ex.StdOut.ReadAll
If Len(Trim(out)) > 0 Then
    MsgBox "스마트팜 분석 시스템이 이미 실행 중입니다. (http://localhost:" & port & ")", 64, "스마트팜 분석 시스템"
Else
    sh.Run "cmd /c """ & dir & "\serve_app.bat""", 0, False
End If
