@echo off
rem Checks and controls the virtual display. Usage: virtual.bat status ^| monitors ^| modes ^| on ^| off ^| write-config [--gpu NAME] [--apply]
rem on / off / write-config --apply need an administrator.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run start.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m duo_server.virtual_display %*
pause
