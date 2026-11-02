@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
  set "HUY_PYTHON=python"
) else (
  set "HUY_PYTHON=py -3"
)
%HUY_PYTHON% -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
  echo Can cai Python 3.11 tro len tu https://www.python.org/downloads/windows/
  echo Khi cai, chon Add Python to PATH.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" %HUY_PYTHON% -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo.
echo Cai dat xong. Mo CHAY_WINDOWS.cmd de bat dau.
pause
exit /b 0
:failed
echo Cai dat chua thanh cong. Kiem tra Internet va Python roi chay lai.
pause
exit /b 1
