@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Missing virtual environment at .venv
    echo Create it once with:
    echo python -m venv .venv
    echo .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "desktop_launcher.py"
