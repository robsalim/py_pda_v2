@echo off
REM Keep this file ASCII-only (same cmd.exe + chcp 65001 parsing issue as setup.bat).
REM Launch the app with Python from the local virtual environment (pythonw = no console).
REM The app resolves relative paths (db_config.json, data/) against the working
REM directory, so switch to this script's folder before starting.
cd /d "%~dp0"
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
