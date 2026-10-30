@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
cd /d "%~dp0"
if not exist "%~dp0scripts\start_windows.ps1" (
    echo 缺少启动脚本，请先完整解压 GitHub 下载的项目 ZIP，再双击“启动系统.cmd”。
    set "START_EXIT=1"
    goto finish
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_windows.ps1" %*
set "START_EXIT=%ERRORLEVEL%"
:finish
if not "%CAMPUS_NO_PAUSE%"=="1" pause
exit /b %START_EXIT%
