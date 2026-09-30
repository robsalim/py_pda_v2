import os
import sys
import threading
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

# Защита кодировок для консоли Windows и библиотек PostgreSQL/MySQL
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PGCLIENTENCODING"] = "utf-8"

from config import DB_CONFIG, APP_NAME, APP_VERSION, JOURNAL_CONFIG
from database.db_service import DatabaseService
from drivers.driver_manager import DriverManager
from ui.main_window import MainWindow
from ui import theme

def enable_high_resolution_timer():
    """
    Windows по умолчанию имеет разрешение таймера ~15.6 мс, из-за чего
    time.sleep(0.01) спит 15-31 мс и быстрый опрос ПЛК становится невозможным.
    timeBeginPeriod(1) lowers the granularity to 1 ms.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        winmm = ctypes.windll.winmm
        winmm.timeBeginPeriod(1)
        return lambda: winmm.timeEndPeriod(1)
    except Exception:
        return None


def main():
    restore_timer = enable_high_resolution_timer()

    # Включение High-DPI масштабирования для четких шрифтов на 2K/4K экранах
    if hasattr(Qt.ApplicationAttribute, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt.ApplicationAttribute, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName(f"{APP_NAME} v{APP_VERSION}")
    # Глобальный стиль из выбранной темы (светлая/тёмная)
    theme.apply_theme(app, theme.load_choice())
    
    print("[System] Проверка структуры БД...")
    # Запуск в offline-режиме позволяет открыть настройки при недоступной СУБД.
    db_service = DatabaseService(allow_offline=True)
    if not db_service.is_available:
        print(f"[DB] База данных недоступна: {db_service.last_error}")
        print("[DB] Приложение запущено в offline-режиме. Настройте подключение в GUI.")

    # Инициализация менеджера сетевых драйверов (Modbus Server, Client, S7)
    driver_manager = DriverManager(db_service, JOURNAL_CONFIG)

    # 1. Запуск сбора данных со всех активных сетевых модулей
    driver_manager.start_all()

    # 2. Фоновый старт веб-сервера FastAPI (web_server.py)
    import web_server
    web_server.start_web_thread(db_service, driver_manager.registry)

    # 3. Инициализация главного окна PyQt6
    window = MainWindow(db_service, driver_manager)
    window.show()

    # Корректное завершение драйверов при закрытии GUI
    exit_code = app.exec()
    print("[System] Остановка драйверов сбора данных...")
    driver_manager.stop_all()
    if restore_timer:
        try:
            restore_timer()
        except Exception:
            pass
    sys.exit(exit_code)

if __name__ == "__main__":
    main()