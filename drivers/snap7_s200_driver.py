"""
Драйвер Siemens S7-200 SMART поверх python-snap7.

Отличия от «старших» S7 (S7-300/400/1200/1500):
  * основной пользовательский регион — V-память (Variable Memory), а не DB-блоки.
    Адресуется как VB/VW/VD, бит — как V100.3 (без буквы X!).
    По протоколу S7 Ethernet V-память отображается на DB1, поэтому физически
    читается через db_read(1, offset, len) и multi-read Areas.DB.
  * образы входов/выходов и Merker читаются как обычно: IW/QW/MW (PE/PA/MK).

Транспорт, цикл опроса, пакетное чтение и метрики наследуются от Snap7Driver —
переопределён разбор адреса (parse_address) и параметры соединения.

Подключение: у SMART фиксированный удалённый TSAP 0x0301. Обычный
connect(ip, rack=0, slot=1) подходит для большинства ЦПУ; если требуется
иной TSAP, задайте в конфигурации подключения local_tsap / remote_tsap.
"""
import re
from typing import Optional

from drivers.snap7_driver import (
    Snap7Driver,
    S7Address,
    AREA_BY_LETTER,
    SIZE_BY_DATA_TYPE,
    normalize_address,
)


def parse_s200_address(address: str, data_type: str = "INT16") -> Optional[S7Address]:
    """
    Разбирает адрес сигнала S7-200 SMART. Возвращает None, если формат неизвестен.

    Поддерживается:
        VB100        VW100        VD100        V100.3          (V-память -> DB1)
        IW0  IB0  ID0  I0.0 / IX0.0            (входы, PE)
        QW0  QB0  QD0  Q0.1 / QX0.1            (выходы, PA)
        MW10 MB10 MD10 M10.0 / MX10.0          (Merker, MK)
    """
    addr = normalize_address(address)
    if not addr:
        return None

    # V-память: VB/VW/VD или бит V100.3 (буквы размера X для бита нет)
    m = re.fullmatch(r"V([WBXD]?)(\d+)(?:\.(\d+))?", addr)
    if m:
        size_letter, offset, bit = m.groups()
        if bit is not None:
            size = "X"
        elif size_letter in ("", "X"):
            # без буквы размер берём из типа; X явно означает бит
            size = "X" if size_letter == "X" else SIZE_BY_DATA_TYPE.get(
                (data_type or "").upper(), "B")
        else:
            size = size_letter
        token = "DB" + size          # DBB / DBW / DBD / DBX — формат для региона DB
        return S7Address(
            kind="DB",
            area=token,
            db=1,                    # V-память S7-200 SMART = DB1
            size=size,
            offset=int(offset),
            bit=int(bit) if bit is not None else 0,
            v_area=True,
        )

    # I / Q / M: мнемоника области + необязательная буква размера + смещение (+ бит).
    # Бит на SMART пишут без буквы X: I0.0, Q0.1, M10.3 — наличие бита и есть
    # признак битового адреса.
    m = re.fullmatch(r"([MIEQAP])([WBXD]?)(\d+)(?:\.(\d+))?", addr)
    if m:
        area_letter, size_letter, offset, bit = m.groups()
        area = AREA_BY_LETTER.get(area_letter)
        if area is None:
            return None
        if bit is not None:
            size = "X"
        else:
            size = size_letter or SIZE_BY_DATA_TYPE.get((data_type or "").upper(), "B")
        return S7Address(
            kind="AREA",
            area=area,
            db=0,
            size=size,
            offset=int(offset),
            bit=int(bit) if bit is not None else 0,
        )

    # DB*.DBx: у SMART нет блоков данных, но запись «DB1.DBW100» — это тот же
    # байт V100. Принимаем её, чтобы старые теги не ломались при смене типа.
    m = re.fullmatch(r"DB(\d+)\.(DBD|DBW|DBB|DBX)(\d+)(?:\.(\d+))?", addr)
    if m:
        token = m.group(2)
        return S7Address(
            kind="DB",
            area=token,
            db=int(m.group(1)),
            size=token[-1],
            offset=int(m.group(3)),
            bit=int(m.group(4)) if m.group(4) is not None else 0,
        )

    return None


class Snap7S200Driver(Snap7Driver):
    """
    S7-200 SMART на python-snap7. Отличается только адресацией:
    V-память (VB/VW/VD/V100.3) вместо DB-блоков, читается как DB1.
    """

    DRIVER_TYPE = "snap7_s200"

    ADDRESS_HINT = "Примеры: VW100, VB5, VD20, V100.3, IW0, QW0, MW10"

    # V-память вместо DB-блоков: код конструктора 'V' -> буквы VB/VW/VD/V100.3
    ADDRESS_AREAS = [
        ("V", "V — V-память (DB1)"),
        ("M", "M — Merker (флаги)"),
        ("I", "I — образ входа"),
        ("Q", "Q — образ выхода"),
    ]

    ADDRESS_FORMATS_HINT = ("VB100, VW100, VD100, V100.3 (V-память), "
                            "IW0/IX0.0, QW0/QX0.1, MW10/MX3.4")

    ADDRESS_EXAMPLES = [
        ("VW100", "INT16", "V-память, слово — 2 байта VW100 (читается из DB1)"),
        ("VB5", "BYTE", "V-память, байт VB5"),
        ("VD20", "FLOAT", "V-память, двойное слово VD20 (REAL)"),
        ("VD20", "DWORD", "V-память, беззнаковое 32-битное VD20"),
        ("V100.3", "BOOL", "V-память, бит 3 в байте 100 (без буквы X)"),
        ("IW0", "INT16", "Образ входа (I), 2 байта с IW0"),
        ("QW0", "INT16", "Образ выхода (Q), 2 байта с QW0"),
        ("M10.0", "BOOL", "Merker, бит 0 в байте MB10"),
        ("MW10", "INT16", "Merker word — 2 байта MB10"),
    ]

    @staticmethod
    def parse_address(address: str, data_type: str = "INT16") -> Optional[S7Address]:
        return parse_s200_address(address, data_type)

    def __init__(self, connection, db_service, registry=None):
        super().__init__(connection, db_service, registry=registry)
        # TSAP подключения: удалённый TSAP S7-200 SMART фиксированный —
        # 0x0301 (тип S7Basic=3, rack 0, slot 1). snap7 вычисляет его из
        # connection_type/rack/slot, поэтому достаточно set_connection_type(3).
        # Для редких ЦПУ/кабелей можно переопределить из конфигурации.
        self.connection_type = int(self.connection.config.get("connection_type", 3))

    def _connect_client(self):
        setter = getattr(self.client, "set_connection_type", None)
        if setter is not None:
            setter(self.connection_type)
        self.client.connect(self.ip, self.rack, self.slot)
