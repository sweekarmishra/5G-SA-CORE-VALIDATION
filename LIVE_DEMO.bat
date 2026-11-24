@echo off
setlocal
cd /d "%~dp0"
set "PY="
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"
if not defined PY (
  py --version >nul 2>&1
  if not errorlevel 1 set "PY=py"
)
if not defined PY goto nopython
echo Starting the live console. Your browser will open. Keep this window open; close it to stop.
%PY% live_console.py
exit /b 0
:nopython
echo Python 3 was not found. Install it from https://www.python.org/downloads/
echo On the first installer screen tick "Add python.exe to PATH", then run this file again.
pause
exit /b 1
