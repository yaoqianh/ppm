@echo off
REM ============================================================
REM  Update dashboard data - double click to run
REM
REM  Note: the "python.exe" that Windows puts on PATH by default is only a
REM  Microsoft Store shortcut. This script verifies it is a real interpreter
REM  before using it, so it will never pop up the Store by accident.
REM ============================================================
setlocal
cd /d "%~dp0"

set "PY="
set "PYTEST="

REM 1) Is the python on PATH a real interpreter?
for /f "delims=" %%i in ('python -c "print(1)" 2^>nul') do set "PYTEST=%%i"
if "%PYTEST%"=="1" set "PY=python"

REM 2) Not found - fall back to known real interpreters
if not defined PY (
    if exist "C:\Users\Baidu BV\AppData\Local\Programs\Python\Python313\python.exe" set "PY=C:\Users\Baidu BV\AppData\Local\Programs\Python\Python313\python.exe"
)
if not defined PY (
    if exist "C:\Python313\python.exe" set "PY=C:\Python313\python.exe"
)
if not defined PY (
    if exist "C:\Users\Baidu BV\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PY=C:\Users\Baidu BV\.workbuddy\binaries\python\versions\3.13.12\python.exe"
)

if not defined PY goto :nopy

"%PY%" run_update.py
if errorlevel 1 goto :failed
echo.
echo [OK] Done. Run preview.bat to view it.
echo.
pause
exit /b 0

:failed
echo.
echo [FAIL] Update failed - previous data was kept untouched.
echo       Check your network, then try again.
echo.
pause
exit /b 1

:nopy
echo.
echo [!] No usable Python found on this PC.
echo.
echo     You have two options:
echo       1. Install Python (tick "Add Python to PATH"):
echo          https://www.python.org/downloads/
echo       2. Do nothing - GitHub Actions updates the site in the cloud,
echo          so you never need to run this locally.
echo.
pause
exit /b 1
