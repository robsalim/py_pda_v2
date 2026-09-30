"""Smoke-запуск приложения: реальное окно MainWindow на 5 секунд.

Проверяет, что импорты, стили (включая SPIN_BUTTON_QSS) и сборка вкладок
не падают. Запуск: QT_QPA_PLATFORM=offscreen python tests/test_app_smoke.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

from config import DB_CONFIG, JOURNAL_CONFIG
from database.db_service import DatabaseService
from drivers.driver_manager import DriverManager
from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    db = DatabaseService(allow_offline=True)
    dm = DriverManager(db, JOURNAL_CONFIG)
    window = MainWindow(db, dm)
    window.show()
    app.processEvents()
    tabs = window.tabs.count() if hasattr(window, "tabs") else -1
    if tabs <= 0:
        raise AssertionError(f"MainWindow не собрал вкладки: {tabs}")
    print(f"MainWindow OK: вкладок {tabs}, БД offline={not db.is_available}")

    # диалог тега в реальном окне: конструктор S7 собирается без падения
    from ui.io_widget import TagEditDialog
    d = TagEditDialog(conn_id=1, driver_type="snap7")
    d.show()
    app.processEvents()
    assert d.spin_db.height() >= 26, f"спинбокты снова сжаты: {d.spin_db.height()}px"
    print(f"TagEditDialog OK: высота DB-спина {d.spin_db.height()}px, ширина {d.width()}px")
    QTimer.singleShot(200, app.quit)   # короткая выдержка событий Qt-таймеров
    app.exec()
    dm.stop_all()
    print("APP SMOKE PASSED")


if __name__ == "__main__":
    main()
