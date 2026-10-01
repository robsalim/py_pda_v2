"""Тесты вкладки Bits: дерево сигналов с фильтром по 8-16 битным целым.

Проверяет, что в дереве только BYTE/INT16/UINT16 (без FLOAT/BOOL/DWORD),
что BYTE переключает график на 8 дорожек, а INT16/UINT16 — на 16,
и что выбор в дереве подставляет правильный тег в график.
"""
import os
import sys
import tempfile
import types
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from database.db_service import DatabaseService
from models.connection import Connection
from models.tag import Tag
from ui.bits_widget import BitsWidget, is_bit_tag

app = QApplication.instance() or QApplication(sys.argv)


def _leaf_ids(tree):
    """Собираем (tag_id, тип) всех листьев дерева."""
    out = []

    def walk(item):
        for i in range(item.childCount()):
            ch = item.child(i)
            tid = ch.data(0, Qt.ItemDataRole.UserRole)
            if tid is not None:
                out.append((tid, ch.data(0, Qt.ItemDataRole.UserRole + 1)))
            walk(ch)
    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i))
    return out


def test_is_bit_tag():
    assert is_bit_tag("BYTE") and is_bit_tag("int16") and is_bit_tag("UINT16")
    assert not is_bit_tag("FLOAT") and not is_bit_tag("BOOL")
    assert not is_bit_tag("DWORD") and not is_bit_tag("")


def test_bits_tree_filter_and_tracks():
    tmp = tempfile.mkdtemp(prefix="pda_bits_test_")
    db = DatabaseService(engine="sqlite", sqlite_path=os.path.join(tmp, "t.db"))
    assert db.is_available

    conn_id = db.add_connection(Connection(
        id=0, name="C1", driver_type="snap7", enabled=False,
        poll_interval_ms=100, config={"ip": "127.0.0.1"}))
    id_byte = db.add_tag(Tag(id=0, connection_id=conn_id, name="b",
                             address_str="DB1.DBB0", data_type="BYTE",
                             group_name="G"))
    id_int = db.add_tag(Tag(id=0, connection_id=conn_id, name="w",
                            address_str="DB1.DBW2", data_type="INT16",
                            group_name="G"))
    id_uint = db.add_tag(Tag(id=0, connection_id=conn_id, name="uw",
                             address_str="DB1.DBW4", data_type="UINT16",
                             group_name="G"))
    db.add_tag(Tag(id=0, connection_id=conn_id, name="f",
                   address_str="DB1.DBD6", data_type="FLOAT", group_name="G"))
    db.add_tag(Tag(id=0, connection_id=conn_id, name="bit",
                   address_str="DB1.DBX0.0", data_type="BOOL", group_name="G"))

    w = BitsWidget(db)
    leaves = dict(_leaf_ids(w.tag_tree))
    # только три целочисленных тега в дереве
    assert set(leaves) == {id_byte, id_int, id_uint}, leaves
    assert leaves[id_int] == "INT16"

    # выбран первый -> 16 дорожек (INT16/UINT16 по умолчанию, здесь зависит от порядка)
    # явно выбираем BYTE: 8 дорожек
    flags = Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive
    item_byte = w.tag_tree.findItems("b [BYTE]", flags, 0)[0]
    w.tag_tree.setCurrentItem(item_byte)
    assert w._selected_tag_id == id_byte
    assert w._bit_count == 8
    assert sum(1 for c in w.curves if c.isVisible()) == 8

    item_uint = w.tag_tree.findItems("uw [UINT16]", flags, 0)[0]
    w.tag_tree.setCurrentItem(item_uint)
    assert w._selected_tag_id == id_uint
    assert w._bit_count == 16
    assert "uw" in w.lbl_current.text()

    # график на пустом теге не падает (в архиве ничего нет)
    w.update_bit_chart(force_fit=True)


def test_charts_tree_multiselect_drag():
    """Регрессия: дерево сигналов на Charts должно давать мультивыбор (Ctrl/Shift)
    и передавать на график id всех выделенных листьев, а группы — разворачивать
    в список тегов.
    """
    from PyQt6.QtWidgets import QApplication
    from ui.main_window import DraggableTagTree, QTreeWidgetItem

    app = QApplication.instance() or QApplication(sys.argv)
    tree = DraggableTagTree()

    # режим мультивыбора включён (без него Ctrl/Shift не работают)
    from PyQt6.QtWidgets import QAbstractItemView
    assert tree.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection

    conn = QTreeWidgetItem(["C"])
    grp = QTreeWidgetItem(["G"])
    leaf_ids = []
    for i in range(1, 4):
        it = QTreeWidgetItem([f"t{i}"])
        # как в реальном дереве: в UserRole лежит объект тега с полем id
        it.setData(0, Qt.ItemDataRole.UserRole, types.SimpleNamespace(id=i))
        grp.addChild(it)
        leaf_ids.append(i)
    conn.addChild(grp)
    conn.setData(0, Qt.ItemDataRole.UserRole + 1, leaf_ids)
    tree.addTopLevelItem(conn)

    # выделяем первый и третий лист (эмуляция Ctrl-кликов)
    leaves = [grp.child(i) for i in (0, 2)]
    for it in leaves:
        it.setSelected(True)
    assert tree.selectedItems() == leaves

    # drag выделенных -> "1,3" (в исходном порядке, без дублей)
    ids = []
    for it in tree.selectedItems():
        ids.extend(DraggableTagTree._tag_ids_of(it))
    assert ids == [1, 3], ids

    # drag группы -> все её теги
    assert DraggableTagTree._tag_ids_of(grp) == [1, 2, 3]
    # drag подключения -> тоже все теги
    assert DraggableTagTree._tag_ids_of(conn) == [1, 2, 3]


def test_bits_tree_refresh_on_io_changes():
    """Регрессия: дерево Bits должно обновляться сразу после изменений в I/O.

    Связь io_tab.tags_changed -> bits_tab.refresh_tags существовала, но сигнал
    эмитился только через _sync_registry(), а _del_tag() и импорт JSON его не
    вызывали — удалённый/импортированный тег оставался в дереве Bits до
    перезапуска приложения.
    """
    import types as _types
    from PyQt6.QtWidgets import QMessageBox
    from ui.io_widget import IOWidget
    import ui.io_widget as io_mod

    tmp = tempfile.mkdtemp(prefix="pda_bits_refresh_")
    db = DatabaseService(engine="sqlite", sqlite_path=os.path.join(tmp, "r.db"))
    assert db.is_available
    conn_id = db.add_connection(Connection(
        id=0, name="C1", driver_type="snap7", enabled=False,
        poll_interval_ms=100, config={"ip": "127.0.0.1"}))
    tid = db.add_tag(Tag(id=0, connection_id=conn_id, name="w1",
                         address_str="DB1.DBW2", data_type="INT16", group_name="G"))

    dm = _types.SimpleNamespace(
        drivers={},
        reload_tags=lambda: None,
        registry=_types.SimpleNamespace(
            drop=lambda i: None,
            upsert=lambda t: None,
        ),
        restart_all=lambda: None,
    )
    io = IOWidget(db, dm)
    bits = BitsWidget(db)
    io.tags_changed.connect(bits.refresh_tags)

    io.selected_conn = db.get_all_connections()[0]
    io.selected_group = "G"
    tag_obj = db.get_all_tags()[0]
    io._selected_tags = lambda: [tag_obj]

    # подтверждение удаления без модального диалога
    _orig_question = QMessageBox.question
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes)
    try:
        io._del_tag()
    finally:
        QMessageBox.question = _orig_question

    assert db.get_all_tags() == [], "тег не удалился из БД"
    assert _leaf_ids(bits.tag_tree) == [], \
        "после удаления тег остался в дереве Bits (не обновляется)"

    # обратный путь: импорт/создание через _sync_registry обновляет дерево
    tid2 = db.add_tag(Tag(id=0, connection_id=conn_id, name="w2",
                          address_str="DB1.DBW4", data_type="UINT16", group_name="G"))
    io._sync_registry()
    assert dict(_leaf_ids(bits.tag_tree)) == {tid2: "UINT16"}, \
        "новый тег не появился в дереве Bits"


def main():
    test_is_bit_tag()
    test_bits_tree_filter_and_tracks()
    test_charts_tree_multiselect_drag()
    test_bits_tree_refresh_on_io_changes()
    print("BITS TREE TESTS PASSED")


if __name__ == "__main__":
    main()
