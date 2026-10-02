import time
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from drivers.base_driver import BaseDriver
from models.connection import Connection
from database.db_service import DatabaseService

try:
    import snap7
    from snap7.util import get_bool, get_int, get_uint, get_real, get_dword, get_byte
    try:
        # python-snap7 >= 2.x
        from snap7.type import Areas
    except ImportError:
        # python-snap7 1.x
        import importlib
        Areas = importlib.import_module("snap7.types").Areas
    try:
        # код области передаётся в низкоуровневый протокол (SM=0x86 вне enum Areas)
        from snap7.datatypes import S7WordLen
    except ImportError:
        S7WordLen = None
    HAS_SNAP7 = True
    SNAP7_IMPORT_ERROR = None
except ImportError as _e:
    HAS_SNAP7 = False
    SNAP7_IMPORT_ERROR = str(_e)


# ----------------------------------------------------------------------
# Разбор адреса Siemens S7
# ----------------------------------------------------------------------
# Области памяти по букве адреса (значение — имя элемента Areas,
# чтобы разбор не зависел от установленной библиотеки)
AREA_BY_LETTER = {
    "M": "MK",   # Memory (Merker)
    "I": "PE",   # Образ процесса входа
    "E": "PE",   # Eingänge (немецкое обозначение входа)
    "Q": "PA",   # Образ процесса выхода
    "A": "PA",   # Ausgänge (немецкое обозначение выхода)
    "P": "PA",   # Periphery
}



# Буква размера в адресе: W=2 байта, B=1 байт, D=4 байта, X=бит
SIZE_BY_LETTER = {"W": 2, "B": 1, "D": 4, "X": 1}

# Размер по умолчанию, если буква размера в адресе не указана
# BYTE: беззнаковый байт (get_byte), DWORD: беззнаковое 32-битное (get_dword)
SIZE_BY_DATA_TYPE = {"FLOAT": "D", "INT16": "W", "UINT16": "W",
                     "BOOL": "X", "BYTE": "B", "DWORD": "D"}


@dataclass
class S7Address:
    kind: str          # 'DB' — блок данных, 'AREA' — M / I / Q (V-память — kind='DB', v_area=True)
    area: str          # для DB: DBD/DBW/DBB/DBX; для AREA: MK/PE/PA
    db: int            # номер DB (0 для M/I/Q)
    size: str          # W / B / D / X
    offset: int        # смещение в байтах
    bit: int = 0       # номер бита (только для X)
    # True для V-памяти S7-200 SMART: читается как DB1, но в сообщениях
    # об ошибках показывается исходный адрес VB/VW/VD, а не DB1.DBx
    v_area: bool = False

    @property
    def byte_len(self) -> int:
        return SIZE_BY_LETTER[self.size]


def normalize_address(address: str) -> str:
    """'%MW230' (стиль TIA Portal) и 'mw 230' -> 'MW230'."""
    return (address or "").strip().upper().replace("%", "").replace(" ", "").replace("	", "")


def parse_s7_address(address: str, data_type: str = "INT16") -> Optional[S7Address]:
    """
    Разбирает адрес сигнала S7. Возвращает None, если формат неизвестен.

    Поддерживается:
        DB1.DBD0   DB1.DBW4   DB1.DBB2   DB1.DBX0.0
        MW230  MB5  MD20  MX3.4        (Merker)
        IW64   IB0  ID100 IX0.1        (входы)
        QW64   QB0  QD100 QX0.1        (выходы)
        M10 / Q0.1                     (размер берётся из типа тега)
    """
    addr = normalize_address(address)
    if not addr:
        return None

    # DB1.DBD0 / DB1.DBW4 / DB1.DBB2 / DB1.DBX0.0
    m = re.fullmatch(r"DB(\d+)\.(DBD|DBW|DBB|DBX)(\d+)(?:\.(\d+))?", addr)
    if m:
        db_num = int(m.group(1))
        token = m.group(2)
        return S7Address(
            kind="DB",
            area=token,
            db=db_num,
            size=token[-1],
            offset=int(m.group(3)),
            bit=int(m.group(4)) if m.group(4) is not None else 0,
        )

    # Мнемоника области: буква области + необязательная буква размера + смещение
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

    return None


class Snap7Driver(BaseDriver):
    """
    Драйвер контроллеров Siemens S7 на базе библиотеки python-snap7.
    Каноническое имя типа драйвера совпадает с именем библиотеки.
    """

    DRIVER_TYPE = "snap7"

    ADDRESS_HINT = "Примеры: MW230, MD100, M3.4, I0.1, Q0.0, DB1.DBW4, DB1.DBD0, DB1.DBX0.0"

    # Области памяти для конструктора адреса в GUI (код, подпись)
    ADDRESS_AREAS = [
        ("DB", "DB — блок данных"),
        ("M", "M — Merker (флаги)"),
        ("I", "I — образ входа"),
        ("Q", "Q — образ выхода"),
    ]

    ADDRESS_EXAMPLES = [
        ("MW230", "INT16", "Merker word — 2 байта, начиная с MB230"),
        ("MB5", "BYTE", "Merker byte — 1 байт MB5"),
        ("MD100", "FLOAT", "Merker dword — 4 байта MD100 (REAL)"),
        ("M3.4", "BOOL", "Бит 4 в байте MB3 (без буквы X)"),
        ("I0.1", "BOOL", "Бит 1 в байте IB0 (без буквы X)"),
        ("Q0.0", "BOOL", "Бит 0 в байте QB0 (без буквы X)"),
        ("IW64", "INT16", "Образ входа (I), 2 байта с IW64"),
        ("QW0", "INT16", "Образ выхода (Q), 2 байта с QW0"),
        ("DB1.DBW4", "INT16", "Слово в блоке данных DB1, смещение 4"),
        ("DB5.DBD0", "FLOAT", "Real в DB5, смещение 0"),
        ("DB5.DBD0", "DWORD", "Беззнаковое 32-битное в DB5, смещение 0"),
        ("DB1.DBX0.0", "BOOL", "Бит 0 в байте 0 блока DB1"),
    ]

    @staticmethod
    def parse_address(address: str, data_type: str = "INT16") -> Optional[S7Address]:
        """
        Точка переопределения для наследников: разбор адреса сигнала.
        По умолчанию — адресация старших S7 (DB/M/I/Q).
        """
        return parse_s7_address(address, data_type)

    def __init__(self, connection: Connection, db_service: DatabaseService, registry=None):
        super().__init__(connection, db_service, registry=registry)
        self.ip = self.connection.config.get("ip", "192.168.0.1")
        self.rack = int(self.connection.config.get("rack", 0))
        self.slot = int(self.connection.config.get("slot", 1))
        self.client = None
        self.last_error = None

        # Максимум байт в одном запросе чтения (уточняется после подключения)
        self.max_read_len = 200
        # Разрыв между адресами, который выгоднее прочитать «заодно»,
        # чем отправлять отдельный запрос
        self.merge_gap = 16
        # Кэш списка тегов: чтение БД каждый цикл съедает десятки миллисекунд
        self.tag_cache_ttl = 2.0
        self._tags = []
        self._tags_at = 0.0
        self._req_count = 0
        # Статистика цикла для диагностики минимального времени опроса
        self.stats = {"read_ms": 0.0, "db_ms": 0.0, "cycle_ms": 0.0,
                      "requests": 0, "tags": 0, "hz": 0.0}

    def start(self):
        if not HAS_SNAP7:
            self.status = f"snap7 not installed: {SNAP7_IMPORT_ERROR}"
            return
        if self.is_running:
            return
        self.is_running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _connect_client(self):
        """
        Точка переопределения: установка соединения с ЦПУ.
        По умолчанию — классический rack/slot (старшие S7).
        """
        self.client.connect(self.ip, self.rack, self.slot)

    def _run(self):
        self.status = "Connecting..."
        self.client = snap7.client.Client()
        period = max(0.001, self.connection.poll_interval_ms / 1000.0)
        while self.is_running:
            if not self.client.get_connected():
                try:
                    self._connect_client()
                    self._refresh_pdu_length()
                    self._tags_at = 0.0
                    self.status = "Connected"
                except Exception as e:
                    self.status = f"Connect Error: {e}"
                    time.sleep(3.0)
                    continue

            cycle_start = time.perf_counter()
            self.last_error = None
            read_ms = db_ms = 0.0
            requests = 0
            try:
                t0 = time.perf_counter()
                tags = self._get_tags()
                db_ms += time.perf_counter() - t0

                t0 = time.perf_counter()
                self._req_count = 0
                values = self._read_all_values(tags)
                read_ms += time.perf_counter() - t0
                requests = self._req_count

                now = datetime.now()
                published = self.submit_values(values, now)
                if not published:
                    # Реестра нет (offline/тесты) — пишем в базу по-старому
                    batch = []
                    for t in tags:
                        raw = values.get(t.id)
                        if raw is not None:
                            batch.append((now, t.id, float(raw * t.scale + t.offset_val)))
                    if batch:
                        t0 = time.perf_counter()
                        self.db.insert_batch(batch)
                        db_ms += time.perf_counter() - t0
                # Теги, не вошедшие в values, — плохое качество
                missing = [t.id for t in tags if t.id not in values]
                if missing:
                    self.mark_cycle_errors(missing)
            except Exception as e:
                self.last_error = str(e)

            cycle_s = time.perf_counter() - cycle_start
            self._update_stats(cycle_s, read_ms, db_ms, requests, len(self._tags))

            if self.last_error:
                self.status = f"Polling (err): {self.last_error}"
            else:
                s = self.stats
                over = " ПЕРЕГРУЗ" if cycle_s > period * 1.05 else ""
                self.status = (
                    f"Polling · {s['tags']} сигн./{s['requests']} запр. · "
                    f"{s['cycle_ms']:.1f} мс/цикл ({s['hz']:.0f} Гц){over}"
                )

            # poll_interval_ms = целевой ПЕРИОД, поэтому спим только остаток цикла
            remaining = period - (time.perf_counter() - cycle_start)
            if remaining > 0:
                time.sleep(remaining)

        if self.client and self.client.get_connected():
            self.client.disconnect()
        self.status = "Stopped"

    def _refresh_pdu_length(self):
        """Ограничивает длину пакета размером PDU, согласованным с ЦПУ."""
        try:
            pdu = int(self.client.get_pdu_length())
            # 18 байт — заголовок S7 + параметры/ответ заголовка запроса на чтение
            self.max_read_len = max(48, min(222, pdu - 32))
        except Exception:
            self.max_read_len = 200

    def _get_tags(self):
        """
        Список тегов для опроса с кэшем.
        При наличии реестра берём живые точки из него — обращение к БД
        вовсе не нужно в цикле опроса.
        """
        now = time.perf_counter()
        if self.registry is not None:
            if not self._tags or (now - self._tags_at) >= self.tag_cache_ttl:
                self._tags = self.registry.points_for(self.connection.id)
                self._tags_at = now
            return self._tags
        if not self._tags or (now - self._tags_at) >= self.tag_cache_ttl:
            self._tags = self.db.get_tags_by_connection(self.connection.id)
            self._tags_at = now
        return self._tags

    def _read_all_values(self, tags):
        """
        Читает все теги за минимальное число round-trip.
        Приоритет — S7 multi-read (несколько блоков в одном PDU),
        запасной вариант — пакетные чтения по областям.
        """
        values, ok = self._read_multi(tags)
        if ok:
            return values
        return self._read_plan(self._plan_reads(tags))

    def _read_multi(self, tags):
        """
        Один запрос S7 содержит до MAX_VARS адресных спецификаций, поэтому
        20 разнесённых тегоv читаются за один round-trip вместо двадцати.
        Теги special-областей (SM, код вне enum Areas) multi-read не
        поддерживаются — они дочитываются пакетами через _read_plan.
        """
        addrs = []
        raw_tags = []
        for t in tags:
            a = self.parse_address(t.address_str, t.data_type)
            if a is None:
                continue
            addrs.append((t, a))
        if not addrs:
            return {}, True
        if not hasattr(self.client, "read_multi_vars"):
            return {}, False

        max_vars = getattr(self.client, "MAX_VARS", 20)
        values = {}
        try:
            for i in range(0, len(addrs), max_vars):
                chunk = addrs[i:i + max_vars]
                items = []
                for _t, a in chunk:
                    area = Areas.DB if a.kind == "DB" else getattr(Areas, a.area)
                    items.append({
                        "area": int(area),
                        "db_number": a.db if a.kind == "DB" else 0,
                        "start": a.offset,          # байтовое смещение
                        "size": a.byte_len,
                    })
                _rc, data = self.client.read_multi_vars(items)
                self._req_count += 1
                for (t, a), buf in zip(chunk, data):
                    # bytearray обязателен: геттеры snap7 пишут в срез
                    values[t.id] = self._extract(bytearray(buf), 0, a, t)
        except Exception as e:
            self.last_error = f"multi-read ({e}); читаю пакетами по областям"
            return {}, False

        if raw_tags:
            values.update(self._read_plan(self._plan_reads(raw_tags)))
        return values, True

    def _update_stats(self, cycle_s, read_s, db_s, requests, tag_count):
        # Сглаживание по скользящему среднему, чтобы показания не прыгали
        k = 0.2
        s = self.stats
        s["read_ms"] = read_s * 1000.0 if s["cycle_ms"] == 0 else s["read_ms"] * (1 - k) + read_s * 1000.0 * k
        s["db_ms"] = db_s * 1000.0 if s["cycle_ms"] == 0 else s["db_ms"] * (1 - k) + db_s * 1000.0 * k
        s["cycle_ms"] = cycle_s * 1000.0 if s["cycle_ms"] == 0 else s["cycle_ms"] * (1 - k) + cycle_s * 1000.0 * k
        s["requests"] = requests
        s["tags"] = tag_count
        s["hz"] = (1.0 / cycle_s) if cycle_s > 0 else 0.0

    # ------------------------------------------------------------------
    # Пакетное чтение: соседние адреса читаются одним запросом
    # ------------------------------------------------------------------
    def _plan_reads(self, tags):
        """
        Группирует теги по области и объединяет соседние адреса в пакеты.
        Возвращает [(kind, area, db, start, length, [(tag, addr, rel_offset), ...])].
        """
        groups = {}
        for t in tags:
            a = self.parse_address(t.address_str, t.data_type)
            if a is None:
                continue
            # Для DB область не важна — db_read читает любые размеры из одного
            # блока, поэтому все теги одного DB группируются вместе
            key = (a.kind, "" if a.kind == "DB" else a.area, a.db)
            groups.setdefault(key, []).append((a.offset, a.byte_len, t, a))

        plan = []
        for (kind, area, db), items in groups.items():
            items.sort(key=lambda x: x[0])
            block = None
            for offset, length, t, a in items:
                end = offset + length
                if block and offset <= block[1] + self.merge_gap and (end - block[0]) <= self.max_read_len:
                    block[1] = max(block[1], end)
                    block[2].append((t, a, offset - block[0]))
                else:
                    if block:
                        plan.append((kind, area, db, block[0], block[1] - block[0], block[2]))
                    block = [offset, end, [(t, a, 0)]]
            if block:
                plan.append((kind, area, db, block[0], block[1] - block[0], block[2]))
        return plan

    def _read_plan(self, plan):
        """Выполняет план и возвращает {tag_id: значение}."""
        values = {}
        for kind, area, db, start, length, members in plan:
            buf = None
            try:
                # ВАЖНО: именно bytearray — геттеры snap7 (get_int и др.)
                # присваивают элементы в срез, bytes для них не подходит
                if kind == "DB":
                    buf = bytearray(self.client.db_read(db, start, length))
                else:
                    buf = self._read_area_block(area, db, start, length)
                self._req_count += 1
            except Exception as e:
                self.last_error = f"{kind} {area}{'' if kind == 'AREA' else str(db)}.{start}+{length}: {e}"

            if buf is None:
                # Пакет не читается целиком (например, вышел за границу DB) —
                # пробуем каждый тег отдельно, чтобы не потерять остальные значения
                for t, a, _ in members:
                    v = self._read_tag(t)
                    if v is not None:
                        values[t.id] = v
                continue

            for t, a, rel in members:
                if rel + a.byte_len > len(buf):
                    continue
                try:
                   # values[t.id] = self._extract(buf, rel, a, t)
                    # ДОБАВИТЬ ЭТИ 3 СТРОКИ:
                    val = self._extract(buf, rel, a, t)
                    values[t.id] = val
                    print(f"[DEBUG] РАСПАКОВКА SCADA: {t.address_str} = {val}")
                except Exception as e:
                    self.last_error = f"{t.address_str}: {e}"
        return values

    @staticmethod
    def _extract(buf, rel, addr: S7Address, tag):
        """Достаёт значение тега из общего буфера пакета."""
        if addr.size == "X":
            return 1.0 if get_bool(buf, rel, addr.bit) else 0.0
        if addr.size == "D":
            return get_real(buf, rel) if tag.data_type == "FLOAT" else get_dword(buf, rel)
        if addr.size == "W":
            return get_uint(buf, rel) if tag.data_type == "UINT16" else get_int(buf, rel)
        return get_byte(buf, rel)

    # Подсказка формата для сообщения об ошибке разбора адреса
    ADDRESS_FORMATS_HINT = ("MW230, MB5, MD100, MX3.4, IW64, QW0, "
                            "DB1.DBW4, DB5.DBD0, DB1.DBX0.0")

    @classmethod
    def validate_address(cls, address: str, data_type: str = "FLOAT") -> Optional[str]:
        base_err = super().validate_address(address, data_type)
        if base_err:
            return base_err
        if cls.parse_address(address, data_type) is None:
            return (f"Не понимаю адрес '{address}'. Форматы: {cls.ADDRESS_FORMATS_HINT}")
        return None

    # ------------------------------------------------------------------
    # Разбор адреса и чтение
    # ------------------------------------------------------------------
    def _read_tag(self, tag):
        s7 = self.parse_address(tag.address_str, tag.data_type)
        # print(f"[DEBUG] Парсинг тега {tag.address_str} -> {s7}") # <--- ДОБАВИТЬ ЭТУ СТРОКУ
        if s7 is None:
            self.last_error = f"Неизвестный формат адреса: '{tag.address_str}'"
            return None
        if s7.kind == "DB":
            return self._read_db(s7, tag)
        return self._read_area(s7, tag)

    def _read_db(self, s7: S7Address, tag):
        """Одиночное чтение из DB (запасной путь, когда пакет не читается)."""
        try:
            data = bytearray(self.client.db_read(s7.db, s7.offset, s7.byte_len))
            self._req_count += 1
            return self._extract(data, 0, s7, tag)
        except Exception as e:
            self.last_error = f"DB{s7.db}.{s7.area}{s7.offset}: {e}"
            return None

    def _read_area(self, s7: S7Address, tag):
        """Одиночное чтение из MK / PE / PA ."""
        try:
            data = self._read_area_block(s7.area, 0, s7.offset, s7.byte_len)
            self._req_count += 1
            return self._extract(data, 0, s7, tag)
        except Exception as e:
            self.last_error = f"{s7.area}{s7.offset}: {e}"
            return None

    def _read_area_block(self, area: str, db: int, start: int, length: int) -> bytearray:
        """
        Читает length байт из именованной области. Обычные области (MK/PE/PA)
        читаются штатным read_area.
        """
        if not HAS_SNAP7:
            raise RuntimeError(f"snap7 не установлен: {SNAP7_IMPORT_ERROR}")
        enum_area = getattr(Areas, area, None)
        if enum_area is None:
            raise ValueError(f"Неизвестная область памяти: {area}")
        # bytearray обязателен: геттеры snap7 пишут в срез
        return bytearray(self.client.read_area(enum_area, db, start, length))

        
    def stop(self):
        self.is_running = False
        self.status = "Stopped"