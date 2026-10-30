@echo off
rem Checks the running duo_server from this PC (no iPhone needed). Start the server with start.bat first.
rem Options: --window (also try showing a single window)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run start.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m duo_server.selfcheck %*
pause
