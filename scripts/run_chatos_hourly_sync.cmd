@echo off
setlocal
set "CHATOS_ROOT=%~dp0.."
cd /d "%CHATOS_ROOT%"
"C:\Program Files\nodejs\node.exe" "%CHATOS_ROOT%\scripts\hourly_chatos_sync.mjs"
exit /b %ERRORLEVEL%
