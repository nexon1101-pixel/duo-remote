@echo off
rem Starts the server as an administrator (needed to turn the virtual display on and off). A UAC prompt appears.
if "%~1"=="" (
  powershell -NoProfile -Command "Start-Process -FilePath '%~dp0start.bat' -WorkingDirectory '%~dp0' -Verb RunAs"
) else (
  powershell -NoProfile -Command "Start-Process -FilePath '%~dp0start.bat' -ArgumentList '%*' -WorkingDirectory '%~dp0' -Verb RunAs"
)
