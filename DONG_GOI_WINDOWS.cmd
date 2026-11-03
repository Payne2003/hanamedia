@echo off
rem Dong goi HANA MEDIA thanh app Windows: dist\HanaMedia\HanaMedia.exe va (neu co Inno Setup 6) dist\HanaMedia-Setup-x.y.z.exe
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Hay chay CAI_DAT_WINDOWS.cmd truoc.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements-app.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m unittest discover -s tests
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean packaging\HanaMedia.spec
if errorlevel 1 goto failed
for /f "delims=" %%v in ('".venv\Scripts\python.exe" -c "from runner.version import APP_VERSION; print(APP_VERSION)"') do set "HANA_VERSION=%%v"
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
  "%ISCC%" /DAppVersion=%HANA_VERSION% packaging\HanaMedia.iss
  if errorlevel 1 goto failed
  echo Da tao dist\HanaMedia-Setup-%HANA_VERSION%.exe
) else (
  echo Chua co Inno Setup 6: bo qua buoc tao bo cai. App chay duoc tai dist\HanaMedia\HanaMedia.exe
)
pause
exit /b 0
:failed
echo Dong goi chua thanh cong. Xem loi phia tren.
pause
exit /b 1
