@echo off
rem Installs the voice agent on Windows. Double-click this file, or run: install.bat --no-pause
setlocal
cd /d "%~dp0"
if /i "%~1"=="--no-pause" set "NOPAUSE=1"
echo Installing the voice agent...

set "PY="
for %%V in (3.13 3.12 3.11 3.10 3) do (
  if not defined PY (
    py -%%V -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY goto nopython

if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv || goto failed
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto failed
if not exist ".env" copy ".env.example" ".env" >nul

echo.
echo Done. Next, add your settings to .env (README Part 1, step 5).
if not defined NOPAUSE pause
exit /b 0

:nopython
echo.
echo Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH"), then run install.bat again.
if not defined NOPAUSE pause
exit /b 1

:failed
echo.
echo The install failed. See the messages above.
if not defined NOPAUSE pause
exit /b 1
