@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_ai3_windows.ps1" %*
if errorlevel 1 pause
