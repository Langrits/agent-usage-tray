@echo off
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :nopython

%PY% -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto :nopython

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY% -m venv .venv || goto :failed
)
echo Installing packages...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :failed

echo.
echo Done. Run start.cmd to launch AI Usage Tray.
pause
exit /b 0

:nopython
echo Python 3.10 or newer (with tkinter) is required.
echo Download it from https://www.python.org/downloads/ and run install.cmd again.
pause
exit /b 1

:failed
echo.
echo Installation failed. See the messages above.
pause
exit /b 1
