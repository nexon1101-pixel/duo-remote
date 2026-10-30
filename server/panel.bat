@echo off
rem Starts the duo_server control panel (a window to start/stop the server, change settings and update).
rem See start.ps1 for details.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" --panel %*
