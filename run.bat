@echo off
REM Keep this file ASCII-only (same cmd.exe + chcp 65001 parsing issue as setup.bat).
REM Launch the app with Python from the local virtual environment (pythonw = no console).
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
