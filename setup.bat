@echo off
REM Keep this file ASCII-only: cmd.exe + chcp 65001 + UTF-8 Cyrillic in a batch
REM file makes cmd read lines at wrong byte offsets (lines get cut mid-emoji/
REM letter and executed as garbage commands like "'v' is not recognized").
setlocal
cd /d "%~dp0"

echo [Setup] Creating Python virtual environment (.venv)...
python -m venv .venv
if errorlevel 1 (
    echo [Error] Could not create .venv. Check that Python 3.10+ is installed and in PATH.
    pause
    exit /b 1
)

echo [Setup] Upgrading pip inside .venv...
".venv\Scripts\python.exe" -m pip install --upgrade pip

echo [Setup] Installing dependencies from requirements.txt...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [Error] Dependency installation failed.
    pause
    exit /b 1
)

echo [Done] Environment ready. Start the app with run.bat.
pause