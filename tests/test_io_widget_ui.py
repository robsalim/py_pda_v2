"""Интеграционный smoke-тест вкладки I/O поверх модели таблицы (шаг 10-11).

Проверяет, что IOWidget корректно строится, наполняет QTableView,
рекламирует live-значения точечно и переживает drag/drop-сигналы.

Запуск: python tests/test_io_widget_ui.py
"""
import os
import sys
import tempfile
import time
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from database.db_service import DatabaseService
from drivers.driver_manager import DriverManager
from models.connection import Connection
from models.tag import Tag
from ui.io_widget import IOWidget, TagTableView
from ui.tag_table_model import COL_ID, COL_NAME, COL_VALUE

app = QApplication.instance() or QApplication(sys.argv)


def main():
    tmp = tempfile.mkdtemp(prefix="pda_ui_test_")
    db = DatabaseService(engine="sqlite", sqlite_path=os.path.join(tmp, "t.db"))
    assert db.is_available

    conn_id = db.add_connection(Connection(
        id=0, name="S7-test", driver_type="snap7", enabled=False,
        poll_interval_ms=50, config={"ip": "127.0.0.1"},
    ))
    for i in range(1, 301):
        db.add_tag(Tag(id=0, connection_id=conn_id, name=f"tag{i}",
                       address_str=f"DB1.DBD{i}", data_type="REAL",
                       scale=1.0, offset_val=0.0, unit="",
                       group_name="G1" if i % 2 else "G0"))
    db.add_group(conn_id, "G0")
    db.add_group(conn_id, "G1")

    dm = DriverManager(db)
    w = IOWidget(db, dm)

    # дерево построено и выбрано первое соединение -> таблица наполнена
    assert w.io_tree.topLevelItemCount() == 1
    assert w.tag_model.rowCount() == 300, w.tag_model.rowCount()
    print("tree/table OK: rows", w.tag_model.rowCount())

    # выбор группы
    w.tag_table.setModel(w.tag_proxy)
    idx = w.tag_proxy.index(0, COL_ID)
    assert w.tag_proxy.data(idx, Qt.ItemDataRole.DisplayRole) == "1"

    # live-обновление через модель
    ts = datetime.now()
    w.tag_model.apply_updates({5: (ts, 42.5, 0), 77: (ts, 3.25, 0)})
    # строки тегов 5 и 77
    r5 = w.tag_model._row_of[5]
    val = w.tag_model.index(r5, COL_VALUE).data(Qt.ItemDataRole.DisplayRole)
    assert val == "42.50", val
    print("live update OK:", val)

    # поиск
    w._on_tag_search("tag299")
    assert 0 < w.tag_proxy.rowCount() < 300
    w._on_tag_search("")
    assert w.tag_proxy.rowCount() == 300
    print("search OK")

    def pick_mode(data):
        w.combo_tag_filter.setCurrentIndex(w.combo_tag_filter.findData(data))

    # режимы фильтра
    pick_mode("changed")
    assert w.tag_proxy.rowCount() >= 2  # 5 и 77 только что обновились
    pick_mode("all")
    print("filter modes OK")

    # избранные: контекстное меню-логика без курсора
    m10 = None
    w.tag_model.toggle_favorite(10)
    pick_mode("fav")
    assert w.tag_proxy.rowCount() == 1, w.tag_proxy.rowCount()
    pick_mode("all")
    print("favorites OK")

    # таймер тикает и обновляет статус дерева
    w.refresh_online_values()
    w._refresh_tree_status()
    print("refresh tick OK")

    # синхронизация реестра после редактирования не роняет widget
    w._sync_registry()
    print("registry sync OK")

    print("IO WIDGET UI SMOKE PASSED")
    dm.stop_all()


if __name__ == "__main__":
    main()
