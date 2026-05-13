Set WshShell = CreateObject("WScript.Shell")
' 0 代表隱藏視窗執行
WshShell.Run chr(34) & "start.bat" & Chr(34), 0
Set WshShell = Nothing
