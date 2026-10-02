"""Modbus: четыре области памяти (HR/IR/C/DI) и конструктор сигнала.

Покрывает:
  1. Парсер адресов drivers/modbus_address.py (формы "0", "HR100", "IR200",
     "C7", "DI15", классические 4x/3x/0x/1x).
  2. Клиент (Master): планирование блоков по областям, чтение FC01/02/03/04,
     декодирование INT16/UINT16/FLOAT/BOOL, совместимость device_id/slave.
  3. Сервер (Slave) вживую через TCP: FC01/02/03/04 чтение, FC05/06/15/16
     запись, исключения Modbus, публикация записанных тегов в реестр.
  4. Диалог переменной: конструктор области+номер, обратный разбор ручного
     ввода, типы по области, клон адреса.

Запуск: QT_QPA_PLATFORM=offscreen python tests/test_modbus_areas.py
"""
import os
import socket
import struct
import sys
import tempfile
import threading
import time
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication

from drivers.modbus_address import (
    AREA_COIL,
    AREA_DISCRETE,
    AREA_HOLDING,
    AREA_INPUT,
    parse_modbus_address,
    validate_modbus_address,
)

app = QApplication.instance() or QApplication(sys.argv)


# ------------------------------------------------------------------ парсер
def test_parser_areas():
    """Число без префикса = HR; префиксы и классические коды областей."""
    cases = [
        ("0", AREA_HOLDING, 0),
        ("100", AREA_HOLDING, 100),
        ("40001", AREA_HOLDING, 40001),
        ("HR100", AREA_HOLDING, 100),
        ("holding5", AREA_HOLDING, 5),
        ("IR200", AREA_INPUT, 200),
        ("input10", AREA_INPUT, 10),
        ("C7", AREA_COIL, 7),
        ("COIL3", AREA_COIL, 3),
        ("DI15", AREA_DISCRETE, 15),
        ("disc2", AREA_DISCRETE, 2),
        ("4x10", AREA_HOLDING, 10),
        ("3x20", AREA_INPUT, 20),
        ("0x5", AREA_COIL, 5),
        ("1x9", AREA_DISCRETE, 9),
        ("%MW230%", None, None),   # не наш формат (проценты + M-область)
        ("DB1.DBW4", None, None),
        ("", None, None),
        ("70000", None, None),     # за пределами 16-битного смещения
    ]
    for text, area, offset in cases:
        got = parse_modbus_address(text)
        if area is None:
            assert got is None, f"{text} должен быть неразборным, got {got}"
        else:
            assert got is not None and got.area == area and got.offset == offset, \
                f"{text} -> {got}"
    print("парсер областей OK")


def test_type_area_consistency():
    """Регистровый тип в битовой области — ошибка; BIT в HR — легально."""
    assert validate_modbus_address("C7", "UINT16") is not None
    assert validate_modbus_address("DI3", "FLOAT") is not None
    assert validate_modbus_address("C7", "BOOL") is None
    assert validate_modbus_address("100", "BOOL") is None
    assert validate_modbus_address("IR10", "FLOAT") is None
    assert validate_modbus_address("junk", "INT16") is not None
    print("валидация тип/область OK")


# ------------------------------------------------------------------ клиент
class _FakeRegsResp:
    def __init__(self, registers):
        self.registers = registers

    def isError(self):
        return False


class _FakeBitsResp:
    def __init__(self, bits):
        self.bits = bits

    def isError(self):
        return False


class FakeModbusClient315:
    """Псевдо-клиент pymodbus 3.15: read_* только с device_id=, slave= — TypeError."""

    def __init__(self):
        self.calls = []
        self.holding = {0: 1234, 100: 7, 101: 8}
        self.input = {200: 555}
        self.coils = {7: True, 8: False}
        self.discrete = {15: True}

    def _fetch(self, name, address, count, store):
        self.calls.append((name, address, count))
        if name in ("read_coils", "read_discrete_inputs"):
            return _FakeBitsResp([bool(store.get(address + i, False)) for i in range(count)])
        return _FakeRegsResp([int(store.get(address + i, 0)) for i in range(count)])

    def read_holding_registers(self, address, *, count, device_id, no_response_expected=False):
        assert device_id is not None
        return self._fetch("read_holding_registers", address, count, self.holding)

    def read_input_registers(self, address, *, count, device_id, no_response_expected=False):
        return self._fetch("read_input_registers", address, count, self.input)

    def read_coils(self, address, *, count, device_id, no_response_expected=False):
        return self._fetch("read_coils", address, count, self.coils)

    def read_discrete_inputs(self, address, *, count, device_id, no_response_expected=False):
        return self._fetch("read_discrete_inputs", address, count, self.discrete)


class _Tag:
    def __init__(self, tid, addr, dtype):
        self.id = tid
        self.address_str = addr
        self.data_type = dtype
        self.scale = 1.0
        self.offset_val = 0.0


def test_client_reads_all_areas():
    """Клиент читает FC01/02/03/04 по областям и корректно декодирует."""
    from drivers.modbus_client_driver import ModbusClientDriver
    from models.connection import Connection

    conn = Connection(id=1, name="c", driver_type="modbus_client",
                      poll_interval_ms=1000, config={"ip": "127.0.0.1"})
    d = ModbusClientDriver(conn, db_service=None, registry=None)
    fake = FakeModbusClient315()
    d.client = fake

    tags = [
        _Tag(1, "0", "UINT16"),         # HR0 = 1234
        _Tag(2, "HR100", "INT16"),      # HR100 = 7
        _Tag(8, "HR101", "UINT16"),     # HR101 = 8 — сливается с HR100 в один блок
        _Tag(3, "IR200", "UINT16"),     # IR200 = 555
        _Tag(4, "C7", "BOOL"),          # coil True
        _Tag(5, "C8", "BOOL"),          # coil False — тот же блок FC01
        _Tag(6, "DI15", "BOOL"),        # discrete True
        _Tag(7, "garbage", "INT16"),    # неразборный -> None
    ]
    cells = d._read_blocks(tags)
    assert d._decode_raw(tags[0], cells) == 1234.0
    assert d._decode_raw(tags[1], cells) == 7.0
    assert d._decode_raw(tags[2], cells) == 8.0
    assert d._decode_raw(tags[3], cells) == 555.0
    assert d._decode_raw(tags[4], cells) == 1.0
    assert d._decode_raw(tags[5], cells) == 0.0
    assert d._decode_raw(tags[6], cells) == 1.0
    assert d._decode_raw(tags[7], cells) is None
    # вызовы шли правильными методами и не по одному тегу, а блоками по областям
    names = {c[0] for c in fake.calls}
    assert names == {"read_holding_registers", "read_input_registers",
                     "read_coils", "read_discrete_inputs"}, fake.calls
    hr = [c for c in fake.calls if c[0] == "read_holding_registers"]
    assert (0, 1) in [(c[1], c[2]) for c in hr], hr
    assert (100, 2) in [(c[1], c[2]) for c in hr], \
        f"HR100 и HR101 не слились в один запрос: {hr}"
    c_blocks = [c for c in fake.calls if c[0] == "read_coils"]
    assert len(c_blocks) == 1 and c_blocks[0][2] >= 2, \
        f"C7 и C8 не слились в один запрос FC01: {c_blocks}"
    print("клиент: чтение 4 областей OK")


def test_client_float_two_registers():
    """FLOAT в IR занимает два регистра (big-endian, слово старшего младше)."""
    from drivers.modbus_client_driver import ModbusClientDriver
    from models.connection import Connection

    conn = Connection(id=1, name="c", driver_type="modbus_client",
                      poll_interval_ms=1000, config={})
    d = ModbusClientDriver(conn, db_service=None, registry=None)

    class F(FakeModbusClient315):
        def __init__(self):
            super().__init__()
            self.input[200] = 0x4049          # 3.14159... старшее слово
            self.input[201] = 0x0FDB          # младшее

    d.client = F()
    t = _Tag(1, "IR200", "FLOAT")
    cells = d._read_blocks([t])
    val = d._decode_raw(t, cells)
    assert abs(val - 3.14159) < 1e-4, val
    # второй регистр попал в план
    ir = [c for c in d.client.calls if c[0] == "read_input_registers"][0]
    assert ir[2] >= 2, ir
    print("клиент: FLOAT из 2 регистров OK")


def test_client_slave_kw_fallback():
    """Старый pymodbus (только slave=): драйвер переключается на slave и читает."""
    from drivers.modbus_client_driver import ModbusClientDriver
    from models.connection import Connection

    class Old(FakeModbusClient315):
        def read_holding_registers(self, address, *, count, slave, no_response_expected=False):
            return self._fetch("read_holding_registers", address, count, self.holding)

    conn = Connection(id=1, name="c", driver_type="modbus_client",
                      poll_interval_ms=1000, config={})
    d = ModbusClientDriver(conn, db_service=None, registry=None)
    d.client = Old()
    t = _Tag(1, "0", "UINT16")
    cells = d._read_blocks([t])
    assert d._decode_raw(t, cells) == 1234.0
    assert d._device_kw == "slave", d._device_kw
    print("клиент: fallback на slave= OK")


# ------------------------------------------------------------------ сервер
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _RecordingRegistry:
    """Минимальная замена TagRegistry: пишет в себе последние значения."""

    def __init__(self):
        self.points = {}
        self.values = {}

    def points_for(self, conn_id):
        return list(self.points.values())

    def update_value(self, tag_id, raw, quality, ts):
        self.values[tag_id] = raw


def _make_server_and_tags(port):
    from database.db_service import DatabaseService
    from drivers.modbus_server_driver import ModbusServerDriver
    from models.connection import Connection
    from models.tag import Tag

    tmp = tempfile.mkdtemp(prefix="pda_mb_srv_")
    db = DatabaseService(engine="sqlite", sqlite_path=os.path.join(tmp, "t.db"))
    assert db.is_available
    conn = Connection(id=1, name="mb", driver_type="modbus_server",
                      enabled=False, poll_interval_ms=100,
                      config={"port": port, "host": "127.0.0.1"})
    reg = _RecordingRegistry()
    for i, (addr, dtype) in enumerate([
        ("HR10", "INT16"), ("C3", "BOOL"),
    ], start=1):
        t = Tag(id=i, connection_id=1, name=f"t{i}", address_str=addr,
                data_type=dtype, scale=1.0, offset_val=0.0)
        reg.points[i] = t
    d = ModbusServerDriver(conn, db, registry=reg)
    d.start()
    deadline = time.time() + 5
    while time.time() < deadline and "Running" not in d.status:
        time.sleep(0.05)
    assert "Running" in d.status, d.status
    return db, d


def test_server_master_slave_roundtrip():
    """Живой обмен: pymodbus-клиент <-> ModbusServerDriver (HR + Coils)."""
    from pymodbus.client import ModbusTcpClient

    port = _free_port()
    db, srv = _make_server_and_tags(port)
    try:
        mc = ModbusTcpClient("127.0.0.1", port=port, timeout=2.0)
        assert mc.connect(), "клиент не подключился к серверу"

        # FC16 Write Multiple Registers + FC03 Read Holding Registers
        wr = mc.write_registers(10, values=[0xFFF9], device_id=1)   # -7 как int16
        assert not wr.isError(), wr
        time.sleep(0.2)  # посылка обрабатывается в event loop сервера
        rr = mc.read_holding_registers(10, count=2, device_id=1)
        assert not rr.isError(), rr
        assert rr.registers[0] == 0xFFF9, rr.registers
        # тег HR10 опубликован в реестр со знаком -7
        deadline = time.time() + 2
        while time.time() < deadline and 1 not in srv.registry.values:
            time.sleep(0.05)
        assert srv.registry.values.get(1) == -7.0, srv.registry.values

        # FC06 Write Single Register
        wr = mc.write_register(11, 4242, device_id=1)
        assert not wr.isError(), wr
        time.sleep(0.2)
        rr = mc.read_holding_registers(11, count=1, device_id=1)
        assert rr.registers[0] == 4242, rr.registers

        # FC05 Write Single Coil + FC01 Read Coils
        wr = mc.write_coil(3, True, device_id=1)
        assert not wr.isError(), wr
        time.sleep(0.2)
        rr = mc.read_coils(2, count=3, device_id=1)
        assert not rr.isError(), rr
        assert rr.bits[0] is False and rr.bits[1] is True, rr.bits  # C2, C3
        # тег C3 (ID 2) обновлён
        deadline = time.time() + 2
        while time.time() < deadline and 2 not in srv.registry.values:
            time.sleep(0.05)
        assert srv.registry.values.get(2) == 1.0, srv.registry.values

        # FC15 Write Multiple Coils
        wr = mc.write_coils(4, values=[True, True, False], device_id=1)
        assert not wr.isError(), wr
        time.sleep(0.2)
        rr = mc.read_coils(4, count=3, device_id=1)
        assert rr.bits[:3] == [True, True, False], rr.bits

        # Незвестный FC -> исключение 0x01 (Illegal Function).
        # Raw-посылку шлём отдельным сокетом, читаем ответ руками:
        # MBAP занимает 7 байт, код функции исключения — resp[7].
        bad = struct.pack(">HHHB", 1, 0, 5, 1) + struct.pack(">BHH", 8, 0, 1)
        with socket.create_connection(("127.0.0.1", port), timeout=2) as raw:
            raw.sendall(bad)
            resp = raw.recv(64)
        assert len(resp) >= 9, resp.hex()
        assert resp[7] == 0x88 and resp[8] == 0x01, resp.hex()

        mc.close()
        print("сервер: roundtrip HR+Coils OK")
    finally:
        srv.stop()
        time.sleep(0.2)


def test_server_illegal_address_exception():
    """Запрос за пределами карты и некорректный счётчик -> исключения Modbus.

    pymodbus-клиент сам отсекает count>125, поэтому шлём raw-посылки.
    """
    port = _free_port()
    db, srv = _make_server_and_tags(port)
    try:
        def raw_fc(req: bytes) -> bytes:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as s:
                s.sendall(struct.pack(">HHHB", 1, 0, len(req) + 1, 1) + req)
                return s.recv(256)

        # FC03 с count=200 (>125) -> IllegalDataValue (0x03)
        resp = raw_fc(struct.pack(">BHH", 3, 0, 200))
        assert len(resp) >= 9 and resp[7] == 0x83 and resp[8] == 0x03, resp.hex()

        # FC01 с count=2001 (>2000) -> IllegalDataValue
        resp = raw_fc(struct.pack(">BHH", 1, 0, 2001))
        assert len(resp) >= 9 and resp[7] == 0x81 and resp[8] == 0x03, resp.hex()

        # Незаполненный адрес в допустимом диапазоне -> 0, не исключение
        resp = raw_fc(struct.pack(">BHH", 3, 60000, 1))
        assert resp[7] == 0x03 and struct.unpack(">H", resp[9:11])[0] == 0, resp.hex()

        # FC04 и FC02 сервер не эмулирует (нет карт) -> Illegal Function (0x01)
        resp = raw_fc(struct.pack(">BHH", 4, 30000, 1))
        assert len(resp) >= 9 and resp[7] == 0x84 and resp[8] == 0x01, resp.hex()

        resp = raw_fc(struct.pack(">BHH", 2, 0, 10))
        assert len(resp) >= 9 and resp[7] == 0x82 and resp[8] == 0x01, resp.hex()

        print("сервер: исключения и отказ от IR/DI OK")
    finally:
        srv.stop()
        time.sleep(0.3)


# ------------------------------------------------------------------ UI
def _dlg(driver_type="modbus_client"):
    from ui.io_widget import TagEditDialog
    return TagEditDialog(conn_id=1, current_group="G", driver_type=driver_type)


def test_ui_modbus_builder():
    """Конструктор Modbus: область+номер дают адрес; HR — простым числом."""
    cases = [
        (AREA_HOLDING, 100, "100", "INT16"),
        (AREA_INPUT, 200, "IR200", "INT16"),
        (AREA_COIL, 7, "C7", "BOOL"),
        (AREA_DISCRETE, 15, "DI15", "BOOL"),
    ]
    for area, offset, want_addr, want_type in cases:
        d = _dlg()
        d._set_if(d.mb_combo_area, area)
        d.mb_spin_offset.setValue(offset)
        assert d.txt_addr.text() == want_addr, (area, offset, d.txt_addr.text())
        assert d.combo_type.currentText() == want_type, (area, d.combo_type.currentText())
        assert d._validate_address(), d.lbl_addr_hint.text()
    print("UI: конструктор областей OK")


def test_ui_modbus_roundtrip_and_types():
    """Ручной ввод IR200/C7 отражается в конструкторе; в C/DI только BOOL."""
    for text, want_area in [("IR200", AREA_INPUT), ("C7", AREA_COIL),
                            ("DI15", AREA_DISCRETE), ("40001", AREA_HOLDING)]:
        d = _dlg()
        d.txt_addr.setText(text)
        assert d.mb_combo_area.currentData() == want_area, (text, d.mb_combo_area.currentData())
        assert d._validate_address(), text

    d = _dlg()
    d._set_if(d.mb_combo_area, AREA_COIL)
    types = [d.combo_type.itemText(i) for i in range(d.combo_type.count())]
    assert types == ["BOOL"], types

    # регистровая область возвращает полный набор типов
    d._set_if(d.mb_combo_area, AREA_HOLDING)
    types = [d.combo_type.itemText(i) for i in range(d.combo_type.count())]
    assert types == ["INT16", "UINT16", "FLOAT", "BOOL"], types
    print("UI: обратный разбор и типы по области OK")


def test_ui_edit_keeps_modbus_address():
    """Открытие существующего тега не портит адрес/тип; неразборный — подсвечен."""
    from models.tag import Tag
    from ui.io_widget import TagEditDialog

    for addr, dtype in [("0", "UINT16"), ("HR100", "INT16"), ("IR200", "FLOAT"),
                        ("C7", "BOOL"), ("DI15", "BOOL")]:
        t = Tag(id=1, connection_id=1, name="x", address_str=addr, data_type=dtype)
        d = TagEditDialog(conn_id=1, tag=t, driver_type="modbus_client")
        assert d.txt_addr.text() == addr, (addr, d.txt_addr.text())
        assert d.combo_type.currentText() == dtype
        assert d._validate_address()

    t = Tag(id=1, connection_id=1, name="x", address_str="garbage", data_type="INT16")
    d = TagEditDialog(conn_id=1, tag=t, driver_type="modbus_client")
    assert d._validate_address() is False
    assert d.txt_addr.text() == "garbage", "конструктор не должен съедать ввод"
    print("UI: редактирование сохраняет Modbus-адрес OK")


def test_ui_next_address_with_areas():
    """Клон сигнала: сдвиг в своей области, FLOAT -> +2."""
    from ui.io_widget import IOWidget

    assert IOWidget._next_address("C7", "BOOL", None) == "C8"
    assert IOWidget._next_address("DI15", "BOOL", None) == "DI16"
    assert IOWidget._next_address("IR200", "UINT16", None) == "IR201"
    assert IOWidget._next_address("HR100", "FLOAT", None) == "HR102"
    assert IOWidget._next_address("100", "UINT16", None) == "101"
    assert IOWidget._next_address("DB1.DBW4", "INT16", None) == "DB1.DBW6"
    print("UI: клон адреса по областям OK")


def test_s7_dlg_has_no_modbus_builder():
    """S7-диалог не должен обрастать Modbus-конструктором."""
    d = _dlg("snap7")
    assert hasattr(d, "combo_area") and not hasattr(d, "mb_combo_area")
    print("S7-диалог без Modbus-конструктора OK")


def test_server_rejects_ir_di_validation():
    """Драйвер сервера отвергает адреса IR/DI при валидации."""
    from drivers.modbus_server_driver import ModbusServerDriver
    assert ModbusServerDriver.validate_address("0", "UINT16") is None
    assert ModbusServerDriver.validate_address("HR100", "FLOAT") is None
    assert ModbusServerDriver.validate_address("C7", "BOOL") is None
    assert ModbusServerDriver.validate_address("IR200", "UINT16") is not None
    assert ModbusServerDriver.validate_address("DI15", "BOOL") is not None
    print("валидация сервера: IR/DI отвергнуты OK")


def test_ui_server_dlg_only_hr_and_coils():
    """Диалог для modbus_server содержит только области HR и C."""
    d = _dlg("modbus_server")
    areas = [d.mb_combo_area.itemData(i) for i in range(d.mb_combo_area.count())]
    assert areas == [AREA_HOLDING, AREA_COIL], areas
    print("UI: диалог сервера содержит только HR и Coils OK")


if __name__ == "__main__":
    test_parser_areas()
    test_type_area_consistency()
    test_client_reads_all_areas()
    test_client_float_two_registers()
    test_client_slave_kw_fallback()
    test_server_master_slave_roundtrip()
    test_server_illegal_address_exception()
    test_server_rejects_ir_di_validation()
    test_ui_modbus_builder()
    test_ui_server_dlg_only_hr_and_coils()
    test_ui_modbus_roundtrip_and_types()
    test_ui_edit_keeps_modbus_address()
    test_ui_next_address_with_areas()
    test_s7_dlg_has_no_modbus_builder()
    print("ALL MODBUS AREA TESTS PASSED")