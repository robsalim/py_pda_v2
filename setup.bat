@echo off
chcp 65001 > nul
echo [Установка] Создание виртуального окружения Python...
python -m venv .venv
echo [Установка] Установка зависимостей...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo [Готово] Окружение создано! Теперь можно запускать run.bat.
pause