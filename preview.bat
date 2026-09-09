@echo off
REM ============================================================
REM  Start local preview - double click to run
REM  Prints a LAN address so your phone can open it on same WiFi
REM  Press Ctrl+C to stop
REM ============================================================
setlocal
cd /d "%~dp0"

set "PY="

REM 1) PATH 中的 python 是否为真实解释器（不是微软商店的跳转存根）
for /f "delims=" %%i in ('python -c "print(1)" 2^>nul') do set "PYTEST=%%i"
if "%PYTEST%"=="1" set "PY=python"

REM 2) 常见本地安装路径
if not defined PY (
    if exist "C:\Users\Baidu BV\AppData\Local\Programs\Python\Python313\python.exe" set "PY=C:\Users\Baidu BV\AppData\Local\Programs\Python\Python313\python.exe"
)
if not defined PY (
    if exist "C:\Python313\python.exe" set "PY=C:\Python313\python.exe"
)
REM 3) 最后兜底到 WorkBuddy 自带的隔离解释器
if not defined PY (
    if exist "C:\Users\Baidu BV\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PY=C:\Users\Baidu BV\.workbuddy\binaries\python\versions\3.13.12\python.exe"
)

if not defined PY goto :nopy

"%PY%" serve.py --lan
echo.
pause
exit /b 0

:nopy
echo.
echo [!] No usable Python found on this PC.
echo     Install Python from https://www.python.org/downloads/
echo     or wait for the cloud version to update automatically.
echo.
pause
exit /b 1
