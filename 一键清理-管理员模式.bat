@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0cleanup-admin.ps1" %*
pause
