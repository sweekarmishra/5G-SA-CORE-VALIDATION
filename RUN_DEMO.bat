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
echo [Run A] Normal run - every test should PASS
%PY% run_suite.py
echo.
echo [Run B] Fault injected - some tests should FAIL
%PY% run_suite.py --fault pfcp_fail --out reports_fault
start "" "reports\report.html"
start "" "reports_fault\report.html"
pause
exit /b 0
:nopython
echo Python 3 was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH").
pause
exit /b 1
