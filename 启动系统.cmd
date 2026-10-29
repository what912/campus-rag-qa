@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "%~dp0run.py" %*
    goto finish
)
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -c "import fastapi, uvicorn, multipart, httpx, numpy, scipy, sklearn, pypdf, docx" >nul 2>&1
    if not errorlevel 1 (
        "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%~dp0run.py" %*
        goto finish
    )
)
python -c "import fastapi, uvicorn, multipart, httpx, numpy, scipy, sklearn, pypdf, docx" >nul 2>&1
if errorlevel 1 (
    echo Dependencies missing. Open README.md and follow the installation steps.
    goto finish
)
python "%~dp0run.py" %*
:finish
pause
