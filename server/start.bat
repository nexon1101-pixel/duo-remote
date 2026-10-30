@echo off
rem Starts duo_server. See start.ps1 for details.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
