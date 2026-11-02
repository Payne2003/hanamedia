@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Hay chay CAI_DAT_WINDOWS.cmd truoc.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" app.py
pause
