@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"

set "WEB_URL=http://127.0.0.1:8123"
set "PYTHON_EXE=D:\Anaconda\envs\review-fusion-py313\python.exe"

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Python env not found:
  echo %PYTHON_EXE%
  pause
  exit /b 1
)

echo Starting fusion-reviewer Web...
echo URL: %WEB_URL%
start "" cmd /c "ping 127.0.0.1 -n 3 >nul && start \"\" %WEB_URL%"
"%PYTHON_EXE%" -m fusion_reviewer.cli serve

echo.
echo Web service exited.
pause
