"""Диалог тега: конструктор адреса Siemens S7.

Проверяет, что выбор область/размер/смещение/номер DB/бит собирает корректный
адрес и согласованный с ним тип данных, а ручной ввод в поле адреса
обратно разворачивается в элементы конструктора.

Запуск: QT_QPA_PLATFORM=offscreen python tests/test_s7_address_builder.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication

from drivers.snap7_driver import parse_s7_address
from ui.io_widget import TagEditDialog

app = QApplication.instance() or QApplication(sys.argv)


def _dlg(driver_type="snap7"):
    return TagEditDialog(conn_id=1, current_group="G", driver_type=driver_type)


def test_area_size_offset_build_address():
    """Область + размер + смещение дают адрес в формате, который понимает драйвер."""
    d = _dlg()
    cases = [
        # (область, размер, смещение, бит, ожидаемый адрес, ожидаемый тип)
        ("M", "W", 230, "0", "MW230", "INT16"),
        ("M", "B", 5, "0", "MB5", "BYTE"),
        ("M", "D", 100, "0", "MD100", "FLOAT"),
        ("M", "X", 3, "4", "MX3.4", "BOOL"),
        ("I", "W", 64, "0", "IW64", "INT16"),
        ("Q", "B", 0, "0", "QB0", "BYTE"),
        ("DB", "W", 4, "0", "DB1.DBW4", "INT16"),
        ("DB", "D", 0, "0", "DB1.DBD0", "FLOAT"),
        ("DB", "X", 0, "0", "DB1.DBX0.0", "BOOL"),
    ]
    for area, size, offset, bit, want_addr, want_type in cases:
        # Каждый кейс — свежий диалог: тогда тип задаётся размером с нуля,
        # а не наследуется от предыдущей итерации
        d = _dlg()
        d._set_if(d.combo_area, area)
        d._set_if(d.combo_size, size)
        d.spin_byte.setValue(offset)
        d._set_if(d.combo_bit, bit)
        d.spin_db.setValue(1)

        got_addr = d.txt_addr.text()
        assert got_addr == want_addr, f"{area}{size}{offset}: {got_addr} != {want_addr}"
        assert d.combo_type.currentText() == want_type, (got_addr, d.combo_type.currentText())
        assert d._validate_address(), got_addr
        # адрес обязан разбираться драйвером с тем же смещением/битом
        parsed = parse_s7_address(got_addr, want_type)
        assert parsed is not None and parsed.offset == offset, (got_addr, parsed)
    print("S7 constructor -> адрес + тип OK")


def test_db_number_and_widget_gating():
    """Номер DB попадает в адрес; DB-поле активно только для DB, бит — только для X."""
    d = _dlg()
    d._set_if(d.combo_area, "DB")
    d.spin_db.setValue(5)
    d._set_if(d.combo_size, "D")
    d.spin_byte.setValue(12)
    assert d.txt_addr.text() == "DB5.DBD12", d.txt_addr.text()
    assert d._validate_address()
    # isHidden, а не not isVisible: isVisible() требует show() родителя
    assert not d.spin_db.isHidden() and d.combo_bit.isHidden()

    d._set_if(d.combo_size, "X")
    assert not d.combo_bit.isHidden()
    d._set_if(d.combo_area, "M")
    assert d.spin_db.isHidden(), "для M/I/Q номер DB не нужен"
    assert d.txt_addr.text().startswith("MX"), d.txt_addr.text()
    print("DB / бит-гейтинг OK")


def test_roundtrip_from_manual_text():
    """Ручной ввод адреса в текстовое поле разворачивается в конструктор."""
    for text, want_area, want_size, want_byte, want_db in [
        ("DB12.DBW8", "DB", "W", 8, 12),
        ("MW230", "M", "W", 230, None),
        ("QD4", "Q", "D", 4, None),
    ]:
        d = _dlg()
        d.txt_addr.setText(text)
        assert d.combo_area.currentData() == want_area, (text, d.combo_area.currentData())
        assert d.combo_size.currentData() == want_size, (text, d.combo_size.currentData())
        assert d.spin_byte.value() == want_byte, (text, d.spin_byte.value())
    print("обратный разбор ручного ввода OK")


def test_bit_and_type_follow_manual_bool():
    """Размер X -> единственный актуальный тип BOOL; адрес с битом валиден."""
    d = _dlg()
    # выбор размера X сам ограничивает список типов до BOOL
    d._set_if(d.combo_size, "X")
    assert [d.combo_type.itemText(i) for i in range(d.combo_type.count())] == ["BOOL"]
    assert d.combo_type.currentText() == "BOOL"
    d._set_if(d.combo_area, "M")
    d.spin_byte.setValue(3)
    d._set_if(d.combo_bit, "7")
    assert d.txt_addr.text() == "MX3.7", d.txt_addr.text()
    assert d._validate_address()
    assert parse_s7_address(d.txt_addr.text(), "BOOL").bit == 7
    print("BOOL/бит OK")


def test_non_s7_driver_has_no_builder():
    """Для Modbus конструктор не показывается — там адрес целочисленный."""
    d = _dlg(driver_type="modbus_client")
    assert not hasattr(d, "combo_area"), "у modbus не должен появляться S7-конструктор"
    d.txt_addr.setText("100")
    d.combo_type.setCurrentText("FLOAT")
    assert d._validate_address()
    print("modbus без конструктора OK")


def test_edit_keeps_compatible_type():
    """Открытие существующей переменной не должно портить её тип и адрес."""
    from models.tag import Tag

    for addr, dtype, want_addr, want_type in [
        ("DB1.DBW4", "UINT16", "DB1.DBW4", "UINT16"),   # слово + UINT16 — норма
        ("DB5.DBD0", "FLOAT", "DB5.DBD0", "FLOAT"),
        # DBD/MD может быть и REAL, и DWORD — размер D допускает оба
        ("DB5.DBD0", "DWORD", "DB5.DBD0", "DWORD"),
        ("MD100", "DWORD", "MD100", "DWORD"),
        ("MX3.4", "BOOL", "MX3.4", "BOOL"),
        ("MW230", "INT16", "MW230", "INT16"),
    ]:
        t = Tag(id=1, connection_id=1, name="x", address_str=addr, data_type=dtype)
        d = TagEditDialog(conn_id=1, tag=t, driver_type="snap7")
        assert d.txt_addr.text() == want_addr, (addr, d.txt_addr.text())
        assert d.combo_type.currentText() == want_type, (addr, d.combo_type.currentText())
        assert d.get_tag_data().address_str == want_addr
    print("редактирование сохраняет адрес/тип OK")


def test_types_follow_size():
    """Список типов = только актуальные для размера: B->BYTE, W->INT16/UINT16,
    D->FLOAT/DWORD, X->BOOL."""
    d = _dlg()

    def types():
        return [d.combo_type.itemText(i) for i in range(d.combo_type.count())]

    # стартовый размер W: целые, без FLOAT/DWORD
    assert types() == ["INT16", "UINT16"], types()
    # байт: get_byte читает 0..255 — тип должен называться BYTE, а не UINT16
    d._set_if(d.combo_area, "M")
    d._set_if(d.combo_size, "B")
    assert types() == ["BYTE"], types()
    assert d.txt_addr.text() == "MB0", d.txt_addr.text()
    d.spin_byte.setValue(5)
    assert d.txt_addr.text() == "MB5"
    assert parse_s7_address("MB5", "BYTE").size == "B"

    d._set_if(d.combo_size, "D")
    assert "DWORD" in types() and "FLOAT" in types(), types()
    assert "INT16" not in types() and "BYTE" not in types(), types()
    d.combo_type.setCurrentText("DWORD")
    assert d.txt_addr.text().startswith("MD"), d.txt_addr.text()
    # BIT -> только BOOL
    d._set_if(d.combo_size, "X")
    assert types() == ["BOOL"], types()

    # BYTE/DWORD умеет читать только snap7 (get_byte / get_dword)
    md = TagEditDialog(conn_id=1, driver_type="modbus_client")
    md_types = [md.combo_type.itemText(i) for i in range(md.combo_type.count())]
    assert "DWORD" not in md_types and "BYTE" not in md_types, md_types
    print("типы по размеру (BYTE/DWORD), только для S7 OK")


def test_unparsable_address_not_scrambled():
    """Неразборный адрес не должен быть молча перезаписан конструктором."""
    from models.tag import Tag

    t = Tag(id=1, connection_id=1, name="x", address_str="garbage", data_type="INT16")
    d = TagEditDialog(conn_id=1, tag=t, driver_type="snap7")
    assert d._validate_address() is False
    assert d.txt_addr.text() == "garbage", "конструктор не должен портить ввод"
    assert d.get_tag_data() is not None
    print("мусорный адрес подсвечен, не съеден OK")


if __name__ == "__main__":
    test_area_size_offset_build_address()
    test_db_number_and_widget_gating()
    test_roundtrip_from_manual_text()
    test_bit_and_type_follow_manual_bool()
    test_non_s7_driver_has_no_builder()
    test_edit_keeps_compatible_type()
    test_types_follow_size()
    test_unparsable_address_not_scrambled()
    print("ALL S7 ADDRESS BUILDER TESTS PASSED")
