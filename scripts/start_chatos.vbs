' ChatOS Gateway hidden launcher
' Prevents duplicate launches: start_chatos.mjs itself checks verify-entry
WScript.Quit CreateObject("WScript.Shell").Run("""" & "C:\ChatOS\scripts\start_chatos.bat" & """", 0, True)
