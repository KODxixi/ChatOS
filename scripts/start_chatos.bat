@echo off
rem Node is already a console prerequisite. No PowerShell execution policy changes.
node "%~dp0start_chatos.mjs"
exit /b %errorlevel%
