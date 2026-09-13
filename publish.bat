@echo off
REM ============================================================
REM  生成发布临时目录 .publish（只含前端需要的文件）
REM
REM  用途：把 index.html / assets / dashboard.json 拷贝到 .publish，
REM        再由 WorkBuddy「发布为应用」把它推到云端分享链接。
REM        只发布前端，所以不会把持仓 source(positions.json)、
REM        trades.json、Python 引擎上传到云端。
REM
REM  流程：1) 先跑 update.bat 更新数据  2) 双击本脚本  3) 让 AI 重新发布
REM ============================================================
setlocal
cd /d "%~dp0"

if not exist "index.html" (
  echo [FAIL] 请在 portfolio-dashboard 目录下运行。
  pause
  exit /b 1
)

if exist ".publish" rmdir /s /q ".publish"
mkdir ".publish\data" 2>nul
mkdir ".publish\assets" 2>nul

copy /y "index.html" ".publish\index.html" >nul
copy /y "assets\style.css" ".publish\assets\style.css" >nul
copy /y "assets\app.js" ".publish\assets\app.js" >nul
copy /y "data\dashboard.json" ".publish\data\dashboard.json" >nul

echo.
echo [OK] .publish 已重建，可以让 AI 执行「发布为应用 / 更新线上版本」了。
echo     目录内容：index.html + assets/ + data/dashboard.json
echo.
pause
