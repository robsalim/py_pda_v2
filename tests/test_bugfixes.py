"""Регрессии на три бага из отчёта пользователя.

1. Удаление группы с тегами (delete_group with_tags=True).
2. Пакетное удаление тегов (delete_tags) + кнопка «✕ Удалить» для
   нескольких выделенных строк.
3. «Время обновления» тикает при каждом опросе, даже если значение
   сигнала не меняется (read_values / apply_time_refresh).

Запуск: QT_QPA_PLATFORM=offscreen python tests/test_bugfixes.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from database.db_service import DatabaseService
from models.connection import Connection
from models.tag import Tag
from ui.tag_table_model import TagTableModel, COL_TIME, COL_VALUE

app = QApplication.instance() or QApplication(sys.argv)


def _mk_db(tmp):
    db = DatabaseService(engine="sqlite", sqlite_path=os.path.join(tmp, "t.db"))
    assert db.is_available
    return db


def _seed(db):
    conn_id = db.add_connection(Connection(
        id=0, name="C", driver_type="snap7", enabled=False,
        poll_interval_ms=100, config={"ip": "127.0.0.1"},
    ))
    ids = {}
    for i in range(1, 6):
        ids[i] = db.add_tag(Tag(
            id=0, connection_id=conn_id, name=f"tag{i}",
            address_str=f"DB1.DBW{i * 2}", data_type="INT",
            scale=1.0, offset_val=0.0, unit="",
            group_name="G1" if i <= 3 else "G2",
        ))
    db.add_group(conn_id, "G1")
    db.add_group(conn_id, "G2")
    return conn_id, ids


def test_delete_group_with_tags():
    """Баг 1: группа с тегами удаляется целиком, история — вместе с ними."""
    tmp = tempfile.mkdtemp(prefix="pda_bug1_")
    db = _mk_db(tmp)
    conn_id, ids = _seed(db)

    now = datetime.now()
    db.insert_batch([(now, ids[1], 1.0, 0), (now, ids[2], 2.0, 0), (now, ids[4], 4.0, 0)])

    db.delete_group(conn_id, "G1", with_tags=True)

    left = db.get_tags_by_connection(conn_id)
    assert [t.id for t in left] == [ids[4], ids[5]], [t.id for t in left]
    assert "G1" not in db.get_groups_by_connection(conn_id)
    counts = db.get_group_counts_by_connection(conn_id)
    assert "G1" not in counts, counts
    # история удалённых тегов не осталась, чужая — цела
    assert db.get_data_points_by_data_span(ids[1], timedelta(hours=1))[0] == []
    assert len(db.get_data_points_by_data_span(ids[4], timedelta(hours=1))[0]) == 1
    print("delete_group with_tags OK")


def test_delete_group_moves_to_common():
    """Старое поведение сохранено: без with_tags теги едут в «Общие»."""
    tmp = tempfile.mkdtemp(prefix="pda_bug1b_")
    db = _mk_db(tmp)
    conn_id, ids = _seed(db)

    db.delete_group(conn_id, "G1")
    tags = {t.id: t.group_name for t in db.get_tags_by_connection(conn_id)}
    assert tags[ids[1]] == "Общие" and tags[ids[3]] == "Общие", tags
    assert "G1" not in db.get_groups_by_connection(conn_id)
    print("delete_group -> Общие OK")


def test_delete_tags_batch():
    """Баг 2: пакетное удаление тегов вместе с историей."""
    tmp = tempfile.mkdtemp(prefix="pda_bug2_")
    db = _mk_db(tmp)
    conn_id, ids = _seed(db)

    now = datetime.now()
    db.insert_batch([(now, tid, float(i), 0) for i, tid in ids.items()])

    db.delete_tags([ids[1], ids[2], ids[2]])  # дубликат не должен сломать
    left = {t.id for t in db.get_tags_by_connection(conn_id)}
    assert left == {ids[3], ids[4], ids[5]}, left
    assert db.get_data_points_by_data_span(ids[1], timedelta(hours=1))[0] == []
    assert len(db.get_data_points_by_data_span(ids[3], timedelta(hours=1))[0]) == 1
    db.delete_tags([])  # пустой список — без исключений
    print("delete_tags batch OK")


def test_delete_tags_in_ui():
    """Кнопка «✕ Удалить» работает для нескольких выделенных строк."""
    tmp = tempfile.mkdtemp(prefix="pda_bug2ui_")
    from drivers.driver_manager import DriverManager
    from ui.io_widget import IOWidget

    db = _mk_db(tmp)
    conn_id, ids = _seed(db)
    dm = DriverManager(db)
    w = IOWidget(db, dm)

    # выделяем строки тегов 1..3 (id==строка+1 по порядку загрузки)
    sm = w.tag_table.selectionModel()
    from PyQt6.QtCore import QItemSelectionModel
    for row in (0, 1, 2):
        sm.select(w.tag_proxy.index(row, 0),
                  QItemSelectionModel.SelectionFlag.Select |
                  QItemSelectionModel.SelectionFlag.Rows)
    chosen = {t.id for t in w._selected_tags()}
    assert len(chosen) == 3, chosen

    # не показываем модальный диалог — отвечаем «Да» автоматически
    from PyQt6.QtWidgets import QMessageBox
    orig = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    try:
        w._del_tag()
    finally:
        QMessageBox.question = orig

    left = {t.id for t in db.get_tags_by_connection(conn_id)}
    assert len(left) == 2, left
    assert w.tag_model.rowCount() == 2, w.tag_model.rowCount()
    dm.stop_all()
    print("UI delete selected tags OK")


def test_time_ticks_for_constant_value():
    """Баг 3: time обновляется при каждом чтении, даже если значение то же."""
    from core.tag_registry import TagRegistry
    from core.tag_point import QUALITY_GOOD

    tmp = tempfile.mkdtemp(prefix="pda_bug3_")
    db = _mk_db(tmp)
    conn_id, ids = _seed(db)

    reg = TagRegistry()
    reg.replace_all(db.get_tags_by_connection(conn_id))

    t0 = datetime(2026, 9, 30, 12, 0, 0)
    reg.update_value(ids[1], 7.0, QUALITY_GOOD, t0)
    dirty = reg.take_dirty()
    assert ids[1] in dirty
    assert reg.read_values([ids[1]])[ids[1]] == (t0, 7.0, QUALITY_GOOD)

    # тот же сигнал в следующем цикле: значение не изменилось -> в _dirty не попал,
    # но timestamp свежий и read_values его отдаёт
    t1 = t0 + timedelta(milliseconds=50)
    reg.update_value(ids[1], 7.0, QUALITY_GOOD, t1)
    assert reg.take_dirty() == {}, "неизменившийся сигнал не должен попадать в dirty"
    got = reg.read_values([ids[1]])[ids[1]]
    assert got[0] == t1 and got[1] == 7.0, got
    print("registry read_values OK")


def test_driver_tags_from_registry_have_id():
    """Баг: драйвер брал теги из реестра (TagPoint) и читал t.id -> AttributeError.

    Цикл опроса работает с двумя типами: Tag (offline, registry=None) и
    TagPoint (из реестра). Оба должны давать одинаковый интерфейс для драйвера.
    """
    from core.tag_point import TagPoint

    t = Tag(id=7, connection_id=1, name="tag7", address_str="DB1.DBD0",
            data_type="FLOAT", scale=2.0, offset_val=1.0)
    p = TagPoint(t)

    # алиас id для TagPoint (у Tag поле id есть всегда)
    assert p.id == 7 and p.id == p.tag_id

    # атрибуты, которые читает цикл опроса драйвера (одинаковы у обоих типов)
    for attr in ("id", "name", "address_str", "data_type",
                 "scale", "offset_val", "unit", "group_name"):
        getattr(p, attr)
        getattr(t, attr)

    # offline-путь (реестра нет) не должен сломаться: batch пишет по t.id
    values = {p.id: 3.0}
    batch = [(datetime.now(), tid, float(v * p.scale + p.offset_val))
             for tid, v in values.items()]
    assert batch[0][1] == 7 and abs(batch[0][2] - 7.0) < 1e-9, batch
    print("TagPoint.id alias OK")


def test_apply_time_refresh_marks_only_time():
    """apply_time_refresh обновляет ячейку времени, но не помечает тег изменённым."""
    tags = [Tag(id=i, connection_id=1, name=f"t{i}", address_str="DB1.DBW0",
                data_type="INT", scale=1.0, offset_val=0.0, unit="", group_name="G")
            for i in range(1, 6)]
    m = TagTableModel()
    m.set_rows(tags, live={})

    t0 = datetime(2026, 9, 30, 12, 0, 0)
    t1 = t0 + timedelta(seconds=1)

    m.apply_updates({3: (t0, 5.0, 0)})
    assert 3 in m.recent
    marks_before = dict(m._recent)
    signals = []
    m.dataChanged.connect(lambda tl, br, roles=None: signals.append(tl.row()))

    # повторный тот же снапшот — тишина (так registry и не отдаст неизменившийся тег)
    m.apply_updates({3: (t0, 5.0, 0)})
    assert signals == [], signals

    # новое время при том же значении: refresh тикает, но recent НЕ продлевается
    m.apply_time_refresh({3: (t1, 5.0, 0), 1: (t1, None, 0), 99: (t1, 0.0, 0)})
    assert sorted(signals) == [0, 2], signals   # строки тегов 1 и 3 (0-based)
    assert m._recent == marks_before, "apply_time_refresh не должен трогать recent"
    txt = m.data(m.index(2, COL_TIME), Qt.ItemDataRole.DisplayRole)
    assert txt and txt != "—", txt
    print("apply_time_refresh OK")


def test_visible_time_refresh_in_widget():
    """refresh_online_values двигает время даже без изменений значений."""
    tmp = tempfile.mkdtemp(prefix="pda_bug3ui_")
    from drivers.driver_manager import DriverManager
    from ui.io_widget import IOWidget

    db = _mk_db(tmp)
    conn_id, _ = _seed(db)
    dm = DriverManager(db)
    dm.reload_tags()          # как это делает start_all() в приложении
    reg = dm.registry
    w = IOWidget(db, dm)

    tid = next(iter([t.id for t in w.current_tags]))
    t0 = datetime(2026, 9, 30, 12, 0, 0)
    reg.update_value(tid, 1.0, 0, t0)
    w.refresh_online_values()
    row = w.tag_model._row_of[tid]
    txt_before = w.tag_model.data(w.tag_model.index(row, COL_TIME), Qt.ItemDataRole.DisplayRole)

    t1 = t0 + timedelta(seconds=2)
    reg.update_value(tid, 1.0, 0, t1)   # значение то же
    w.tag_table.resize(600, 400)
    w.tag_table.show()
    app.processEvents()
    w.refresh_online_values()
    txt_after = w.tag_model.data(w.tag_model.index(row, COL_TIME), Qt.ItemDataRole.DisplayRole)
    assert txt_after != txt_before, (txt_before, txt_after)
    dm.stop_all()
    print("visible time tick OK")


if __name__ == "__main__":
    test_delete_group_with_tags()
    test_delete_group_moves_to_common()
    test_delete_tags_batch()
    test_delete_tags_in_ui()
    test_time_ticks_for_constant_value()
    test_driver_tags_from_registry_have_id()
    test_apply_time_refresh_marks_only_time()
    test_visible_time_refresh_in_widget()
    print("ALL BUGFIX TESTS PASSED")
