@echo off
rem Start the tray app without a console window (does nothing if already running)
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo Run install.cmd first.
  pause
  exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0run.pyw"
