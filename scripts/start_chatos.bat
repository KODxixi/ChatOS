@echo off
rem Node is already a console prerequisite. No PowerShell execution policy changes.
rem Forward all args (e.g. --status / --watch) to the Node launcher.
node "%~dp0start_chatos.mjs" %*
exit /b %errorlevel%
