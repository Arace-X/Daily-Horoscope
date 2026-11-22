@echo off
rem Starts the voice agent on Windows. Double-click this file.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The agent isn't installed yet. Double-click install.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" start.py
pause
