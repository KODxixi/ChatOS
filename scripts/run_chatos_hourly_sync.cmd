@echo off
setlocal
set "CHATOS_ROOT=%~dp0.."
cd /d "%CHATOS_ROOT%"
rem 网关不在就幂等拉起（已在跑则立即返回）。后台同步因此不依赖看门狗的探活节奏。
"C:\Program Files\nodejs\node.exe" "%CHATOS_ROOT%\scripts\start_chatos.mjs"
if errorlevel 1 exit /b 1
"C:\Program Files\nodejs\node.exe" "%CHATOS_ROOT%\scripts\hourly_chatos_sync.mjs"
exit /b %ERRORLEVEL%
