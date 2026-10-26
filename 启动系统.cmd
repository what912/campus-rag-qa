@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run.py
    goto finish
)
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -c "import fastapi, uvicorn, multipart, httpx, numpy, scipy, sklearn, pypdf, docx" >nul 2>&1
    if not errorlevel 1 (
        "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" run.py
        goto finish
    )
)
python -c "import fastapi, uvicorn, multipart, httpx, numpy, scipy, sklearn, pypdf, docx" >nul 2>&1
if errorlevel 1 (
    echo Dependencies missing. Open README.md and follow the installation steps.
    goto finish
)
python run.py
:finish
pause
