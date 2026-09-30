"""Тесты вкладки Bits: дерево сигналов с фильтром по 8-16 битным целым.

Проверяет, что в дереве только BYTE/INT16/UINT16 (без FLOAT/BOOL/DWORD),
что BYTE переключает график на 8 дорожек, а INT16/UINT16 — на 16,
и что выбор в дереве подставляет правильный тег в график.
"""
import os
import sys
import tempfile
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


def main():
    test_is_bit_tag()
    test_bits_tree_filter_and_tracks()
    print("BITS TREE TESTS PASSED")


if __name__ == "__main__":
    main()
