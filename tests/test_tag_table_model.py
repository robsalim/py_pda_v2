"""Headless-проверка TagTableModel/TagFilterModel (шаг 10-11 плана).

Запуск: QT_QPA_PLATFORM=offscreen python tests/test_tag_table_model.py
"""
import os
import sys
import time
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from models.tag import Tag
from ui.tag_table_model import (
    TagTableModel, TagFilterModel,
    COL_ID, COL_NAME, COL_VALUE, COL_TIME,
)

app = QApplication.instance() or QApplication(sys.argv)


def make_tags(n):
    return [
        Tag(id=i, connection_id=1, name=f"tag{i}", address_str=f"DB1.DBW{i * 2}",
            data_type="INT", scale=1.0, offset_val=0.0, unit="",
            group_name="G1" if i % 2 else "G0")
        for i in range(1, n + 1)
    ]


def test_rows_and_cells():
    m = TagTableModel()
    tags = make_tags(2000)
    live = {1: (datetime.now(), 5.0, 0)}
    m.set_rows(tags, live=live)
    assert m.rowCount() == 2000
    assert m.columnCount() == 10
    assert m.data(m.index(0, COL_ID), Qt.ItemDataRole.DisplayRole) == "1"
    assert m.data(m.index(0, COL_VALUE), Qt.ItemDataRole.DisplayRole) == "5.00"
    assert m.data(m.index(5, COL_VALUE), Qt.ItemDataRole.DisplayRole) == "—"
    # EditRole даёт число, а не строку -> сортировка числовая
    assert m.data(m.index(0, COL_ID), Qt.ItemDataRole.EditRole) == 1
    print("rows/cells OK")


def test_search_and_modes():
    m = TagTableModel()
    f = TagFilterModel()
    f.setSourceModel(m)
    f.setSortRole(Qt.ItemDataRole.UserRole)
    m.set_rows(make_tags(2000), live={})

    f.set_search("tag199")
    n = f.rowCount()
    assert 0 < n < 2000, n
    f.set_search("DBW1999999")  # заведомо нет такого адреса
    assert f.rowCount() == 0
    f.set_search("DBW100")
    assert 0 < f.rowCount() < 2000
    f.set_search("")
    assert f.rowCount() == 2000

    now = datetime.now()
    changed = {t.id: (now, float(t.id), 0) for t in make_tags(2000)[100:110]}
    m.apply_updates(changed)
    f.set_mode(TagFilterModel.MODE_CHANGED)
    assert f.rowCount() == len(changed), f.rowCount()

    f.set_mode(TagFilterModel.MODE_FAV)
    assert f.rowCount() == 0
    m.toggle_favorite(555)
    f.invalidateFilter()  # как делает контекстное меню в IOWidget
    assert f.rowCount() == 1
    txt = m.data(m.index(554, COL_NAME), Qt.ItemDataRole.DisplayRole)
    assert txt == "tag555"
    print("search/modes OK")


def test_only_dirty_cells_emitted():
    """dataChanged — ровно по грязным строкам, не по всей таблице."""
    m = TagTableModel()
    m.set_rows(make_tags(10000), live={})
    signals = []
    m.dataChanged.connect(lambda tl, br, roles=None: signals.append((tl.row(), br.row())))
    now = datetime.now()
    m.apply_updates({9999: (now, 1.0, 0), 5: (now, 2.0, 0)})
    assert sorted(signals) == [(4, 4), (9998, 9998)], signals
    assert len(signals) == 2
    # повторный тот же снапшот не должен порождать сигналов
    signals.clear()
    m.apply_updates({9999: (now, 1.0, 0)})
    assert not signals
    print("dirty-only dataChanged OK")


def test_prune_recent():
    m = TagTableModel()
    m.set_rows(make_tags(10), live={})
    m.apply_updates({3: (datetime.now(), 1.0, 0)})
    assert 3 in m.recent
    # искусственно «остужаем»
    for tid in list(m.recent):
        m._recent[tid] = time.monotonic() - 100
    assert m.prune_recent() is True
    assert 3 not in m.recent
    print("prune_recent OK")


def test_perf_10k():
    """10 000 тегов: перезагрузка строк и обновление 500 ячеек — быстрее кадра."""
    m = TagTableModel()
    f = TagFilterModel()
    f.setSourceModel(m)
    tags = make_tags(10000)

    t0 = time.perf_counter()
    m.set_rows(tags, live={})
    t1 = time.perf_counter()
    print(f"set_rows(10k) = {(t1 - t0) * 1000:.1f} мс")

    now = datetime.now()
    upd = {t.id: (now, float(t.id), 0) for t in tags[::20]}  # 500 тегов
    t0 = time.perf_counter()
    for _ in range(4):  # 4 тика таймера
        m.apply_updates(upd)
    t1 = time.perf_counter()
    per_tick = (t1 - t0) / 4 * 1000
    print(f"apply_updates(500) = {per_tick:.2f} мс/тик")
    assert per_tick < 20, "обновление таблицы должно оставлять время на 50-мс тике"

    t0 = time.perf_counter()
    f.set_search("tag9")
    t1 = time.perf_counter()
    print(f"invalidateFilter(10k) = {(t1 - t0) * 1000:.1f} мс (поиск)")
    print("perf OK")


if __name__ == "__main__":
    test_rows_and_cells()
    test_search_and_modes()
    test_only_dirty_cells_emitted()
    test_prune_recent()
    test_perf_10k()
    print("ALL TAG TABLE MODEL TESTS PASSED")
