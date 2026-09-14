@echo off
setlocal
rem ChatOS gateway autostart - 8912. 2026-09-14 从 D:\WXWork\chatbox 迁移而来。
cd /d "%~dp0..\apps\gateway"

rem skip if 8912 already listening (prevent duplicate start)
netstat -ano | findstr ":8912" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [ChatOS] already running on 8912, skip.
  exit /b 0
)

if not exist "%~dp0..\var\logs" mkdir "%~dp0..\var\logs"

echo [ChatOS] Starting gateway at http://127.0.0.1:8912 ...
".venv\Scripts\chatbox.exe" serve --port 8912 --no-browser >> "%~dp0..\var\logs\chatos-autostart.stdout.log" 2>> "%~dp0..\var\logs\chatos-autostart.stderr.log"

endlocal
