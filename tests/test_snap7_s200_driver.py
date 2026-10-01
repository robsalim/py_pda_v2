"""Драйвер Siemens S7-200 SMART: адресация V-памяти.

Проверяет разбор адресов SMART (VB/VW/VD, бит V100.3 без буквы X, I/Q/M),
маппинг V-памяти на DB1, обратный разбор в конструкторе адреса GUI и
чтение через мок-snap7 (db_read/multi-read должны уходить в DB1).

Запуск: QT_QPA_PLATFORM=offscreen python tests/test_snap7_s200_driver.py
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication

from drivers.snap7_s200_driver import Snap7S200Driver, parse_s200_address
from drivers.registry import get_driver_class, normalize_driver_type, get_driver_label
from models.tag import Tag

app = QApplication.instance() or QApplication(sys.argv)


def test_parse_v_memory():
    """V-память разбирается и маппится на DB1 (смещение сохраняется)."""
    a = parse_s200_address("VW100", "INT16")
    assert a is not None and a.kind == "DB" and a.db == 1
    assert a.size == "W" and a.offset == 100 and a.v_area
    assert a.byte_len == 2

    a = parse_s200_address("%VBD20", "FLOAT") or parse_s200_address("VD20", "FLOAT")
    assert a is not None and a.size == "D" and a.offset == 20 and a.v_area

    a = parse_s200_address("VB5", "BYTE")
    assert a is not None and a.size == "B" and a.offset == 5

    # бит: V100.3 (без буквы X!) и VX100.3 — оба валидны
    for text in ("V100.3", "VX100.3"):
        a = parse_s200_address(text, "BOOL")
        assert a is not None and a.size == "X" and a.offset == 100 and a.bit == 3, text

    # тип без буквы размера подсказывает размер
    a = parse_s200_address("V20", "FLOAT")
    assert a is not None and a.size == "D", a
    print("V-память OK")


def test_parse_io_and_merker():
    """I/Q/M — как у старших S7; бит допустим без буквы X (I0.0)."""
    a = parse_s200_address("IW0", "INT16")
    assert a is not None and a.kind == "AREA" and a.area == "PE" and a.size == "W"

    a = parse_s200_address("Q0.1", "BOOL")
    assert a is not None and a.area == "PA" and a.size == "X" and a.bit == 1

    a = parse_s200_address("MX3.4", "BOOL")
    assert a is not None and a.area == "MK" and a.size == "X" and a.bit == 4

    a = parse_s200_address("MW10", "INT16")
    assert a is not None and a.area == "MK" and a.size == "W"

    # DB1.DBW100 — то же самое, что V100: старые теги не ломаются
    a = parse_s200_address("DB1.DBW100", "INT16")
    assert a is not None and a.kind == "DB" and a.db == 1 and a.offset == 100
    assert not a.v_area

    assert parse_s200_address("garbage", "INT16") is None
    assert Snap7S200Driver.validate_address("V100.3", "BOOL") is None
    assert Snap7S200Driver.validate_address("DB5.DBX0.0", "BOOL") is None
    assert Snap7S200Driver.validate_address("QQ7", "BOOL") is not None
    print("I/Q/M + fallback DB OK")


def test_parse_sm_area():
    """Special Memory S7-200: SMB/SMW/SDM и бит SM0.0 (без буквы размера)."""
    a = parse_s200_address("SMB0", "BYTE")
    assert a is not None and a.kind == "AREA" and a.area == "SM"
    assert a.size == "B" and a.offset == 0 and not a.v_area

    a = parse_s200_address("SMW10", "UINT16")
    assert a is not None and a.area == "SM" and a.size == "W" and a.offset == 10

    a = parse_s200_address("SM0.0", "BOOL")
    assert a is not None and a.area == "SM" and a.size == "X"
    assert a.offset == 0 and a.bit == 0, "Always ON bit"

    a = parse_s200_address("SMX0.4", "BOOL")
    assert a is not None and a.area == "SM" and a.size == "X" and a.bit == 4

    assert Snap7S200Driver.validate_address("SMB0", "BYTE") is None
    assert Snap7S200Driver.validate_address("SM0.5", "BOOL") is None
    # нормализация регистра/пробелов/процента — как у остальных областей
    assert parse_s200_address("smw 10", "UINT16").area == "SM"
    assert parse_s200_address("%SM0.0", "BOOL").area == "SM"
    # у старшего snap7 область SM не принимается
    from drivers.snap7_driver import parse_s7_address
    assert parse_s7_address("SMB0", "BYTE") is None
    print("SM-область разбор OK")


def test_registry_resolution():
    """Новый тип доступен из реестра; старые написания — через алиасы."""
    assert get_driver_class("snap7_s200") is Snap7S200Driver
    assert normalize_driver_type("S7-200 SMART") == "snap7_s200"
    assert normalize_driver_type("s7_200") == "snap7_s200"
    assert "SMART" in get_driver_label("snap7_s200").upper()
    # snap7 остался старшим S7
    from drivers.snap7_driver import Snap7Driver
    assert get_driver_class("snap7") is Snap7Driver
    print("реестр драйверов OK")


def test_read_via_mock_client():
    """
    Мок-snap7: теги V-памяти обязаны читаться через DB1 (db_read/multi-read),
    I/Q/M — через read_area, SM — через read_area(0x86) и НЕ через multi-read.
    Значения должны доходить до реестра.
    """
    from core.tag_registry import TagRegistry
    from models.connection import Connection

    calls = {"db_read": [], "read_area": [], "multi": []}
    payload = bytes(range(256)) * 4  # детерминированные байты

    class MockClient:
        MAX_VARS = 20

        def __init__(self):
            self._conn = False

        def connect(self, address, rack=0, slot=1):
            self._conn = True

        def set_connection_type(self, ct):
            calls["conn_type"] = ct

        def get_connected(self):
            return self._conn

        def get_pdu_length(self):
            return 240

        def disconnect(self):
            self._conn = False

        def db_read(self, db, start, size):
            calls["db_read"].append((db, start, size))
            return payload[start:start + size]

        def read_area(self, area, db, start, size):
            calls["read_area"].append((int(area), db, start, size))
            return payload[start:start + size]

        def read_multi_vars(self, items):
            calls["multi"].append([(it["area"], it["db_number"], it["start"]) for it in items])
            data = [bytearray(payload[it["start"]:it["start"] + it["size"]]) for it in items]
            return [0] * len(data), data

    import drivers.snap7_driver as mod
    old_snap7, old_has = mod.snap7, mod.HAS_SNAP7
    mod.snap7 = types.SimpleNamespace(client=types.SimpleNamespace(Client=MockClient))
    mod.HAS_SNAP7 = True
    try:
        conn = Connection(id=7, name="s200", driver_type="snap7_s200", enabled=True,
                          poll_interval_ms=10, config={"ip": "127.0.0.1"})
        reg = TagRegistry()
        tags = [
            Tag(id=101, connection_id=7, name="vword", address_str="VW100", data_type="INT16"),
            Tag(id=102, connection_id=7, name="vbit", address_str="V100.3", data_type="BOOL"),
            Tag(id=103, connection_id=7, name="in_bit", address_str="I0.0", data_type="BOOL"),
            Tag(id=104, connection_id=7, name="always_on", address_str="SM0.0", data_type="BOOL"),
            Tag(id=105, connection_id=7, name="sm_byte", address_str="SMB0", data_type="BYTE"),
        ]
        reg.replace_all(tags)
        driver = Snap7S200Driver(conn, db_service=None, registry=reg)
        driver.client = MockClient()
        driver._refresh_pdu_length = lambda: setattr(driver, "max_read_len", 200)

        tags_ = reg.points_for(7)
        values = driver._read_all_values(tags_)
        assert set(values) == {101, 102, 103, 104, 105}, values
        # multi-read должен уходить в DB для V-тегов (db_number=1)
        assert any(any(db == 1 for _a, db, _s in batch) for batch in calls["multi"]) or \
               any(db == 1 for db, _s, _l in calls["db_read"])
        # V100.3 и VW100 дают тот же байт: 100 -> payload[100]
        assert values[101] == driver._extract(bytearray(payload[100:102]), 0,
                                              parse_s200_address("VW100", "INT16"), tags[0])
        assert values[102] == float((payload[100] >> 3) & 1)
        # SM читается read_area(0x86, 0, ...); область 0x86 вне multi-read
        sm_calls = [(a, d, s, l) for a, d, s, l in calls["read_area"] if a == 0x86]
        assert sm_calls, f"SM не читался через read_area(0x86): {calls['read_area']}"
        assert all(0x86 not in [int(x[0]) for x in batch] for batch in calls["multi"]), \
            "SM ушёл в multi-read — CPU 0x86 там не поддержан"
        # payload[0] = 0b00000000 => bit0 SM0.0 = 0, SMB0 = 0
        assert values[105] == payload[0]
        assert values[104] == float(payload[0] & 1)
    finally:
        mod.snap7, mod.HAS_SNAP7 = old_snap7, old_has
    print("чтение через мок (V->DB1, SM->read_area 0x86) OK")


def test_read_sm_raw_protocol():
    """
    На python-snap7 3.x области нет в enum Areas (0x86) — драйвер обязан
    читать SM низкоуровневым протоколом (build_read_request + raw send).
    Тест пропускается, если snap7 не установлен.
    """
    import drivers.snap7_driver as mod
    if not mod.HAS_SNAP7 or mod.S7WordLen is None:
        print("SM raw-чтение пропущено (snap7/S7WordLen недоступен)")
        return

    from core.tag_registry import TagRegistry
    from models.connection import Connection

    sent = []

    class FakeProtocol:
        def build_read_request(self, area, db_number, start, word_len, count):
            sent.append((area, db_number, start, word_len, count))
            return b"REQ"

        def extract_read_data(self, response, word_len, count):
            assert response == "RESP"
            # как настоящий snap7: список байт-значений (int)
            return [0b00000001] * count

    class MockClient:
        MAX_VARS = 20
        protocol = FakeProtocol()

        def connect(self, address, rack=0, slot=1):
            pass

        def get_connected(self):
            return True

        def get_pdu_length(self):
            return 240

        def set_connection_type(self, ct):
            pass

        def disconnect(self):
            pass

        def _send_receive_with_reconnect(self, builder):
            assert builder() == b"REQ"
            return "RESP"

    driver = Snap7S200Driver(Connection(id=9, name="s", driver_type="snap7_s200",
                                        enabled=True, poll_interval_ms=10,
                                        config={"ip": "127.0.0.1"}),
                             db_service=None, registry=TagRegistry())
    driver.client = MockClient()
    a = parse_s200_address("SM0.0", "BOOL")
    tag = Tag(id=1, connection_id=9, name="x", address_str="SM0.0", data_type="BOOL")
    v = driver._read_area(a, tag)
    assert sent, "build_read_request не вызван (не сработал raw-путь)"
    area, db_num, start, wl, cnt = sent[0]
    assert area == 0x86 and db_num == 0 and start == 0 and cnt == 1, sent[0]
    assert v == 1.0, v  # payload 0b00000001 -> бит 0 = 1
    print("SM raw-чтение (protocol 3.x) OK")


def test_dialog_builder_for_s200():
    """GUI-конструктор для snap7_s200: области V/M/I/Q/SM, адрес без DB-блоков."""
    from ui.io_widget import TagEditDialog

    d = TagEditDialog(conn_id=1, driver_type="snap7_s200")
    areas = [d.combo_area.itemData(i) for i in range(d.combo_area.count())]
    assert areas == ["V", "M", "I", "Q", "SM"], areas

    d._set_if(d.combo_area, "V")
    d._set_if(d.combo_size, "W")
    d.spin_byte.setValue(100)
    assert d.txt_addr.text() == "VW100", d.txt_addr.text()
    assert d.combo_type.currentText() == "INT16"
    assert d.spin_db.isHidden(), "номер DB для SMART не нужен"

    d._set_if(d.combo_size, "X")
    d.spin_byte.setValue(100)
    d._set_if(d.combo_bit, "3")
    assert d.txt_addr.text() == "V100.3", d.txt_addr.text()
    assert d._validate_address()

    # обратный разбор: ручной ввод в конструктор
    d.txt_addr.setText("VD20")
    assert d.combo_area.currentData() == "V"
    assert d.combo_size.currentData() == "D"
    assert d.spin_byte.value() == 20
    assert d.combo_type.currentText() in ("FLOAT", "DWORD")

    # I без буквы размера для бита
    d._set_if(d.combo_area, "I")
    d._set_if(d.combo_size, "X")
    d.spin_byte.setValue(0)
    d._set_if(d.combo_bit, "0")
    assert d.txt_addr.text() == "IX0.0", d.txt_addr.text()
    assert d._validate_address()

    # старший snap7 не потерялся: DB там по-прежнему есть
    d2 = TagEditDialog(conn_id=1, driver_type="snap7")
    areas2 = [d2.combo_area.itemData(i) for i in range(d2.combo_area.count())]
    assert areas2[0] == "DB" and "V" not in areas2

    # SM: конструктор собирает SMB0/SMW0, бит — SM0.0 без буквы X? (буква X допустима)
    d._set_if(d.combo_area, "SM")
    d._set_if(d.combo_size, "B")
    d.spin_byte.setValue(0)
    assert d.txt_addr.text() == "SMB0", d.txt_addr.text()
    d._set_if(d.combo_size, "X")
    d.spin_byte.setValue(0)
    d._set_if(d.combo_bit, "0")
    assert d.txt_addr.text() == "SMX0.0", d.txt_addr.text()
    assert d._validate_address()
    d.txt_addr.setText("SM0.0")
    assert d.combo_area.currentData() == "SM"
    assert d.combo_size.currentData() == "X" and d.spin_byte.value() == 0
    print("GUI-конструктор SMART OK")


if __name__ == "__main__":
    test_parse_v_memory()
    test_parse_io_and_merker()
    test_registry_resolution()
    test_read_via_mock_client()
    test_dialog_builder_for_s200()
    print("ALL S7-200 SMART DRIVER TESTS PASSED")
