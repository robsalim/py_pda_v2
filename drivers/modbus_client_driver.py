import time
import struct
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from pymodbus.client import ModbusTcpClient

from drivers.base_driver import BaseDriver
from drivers.modbus_address import (
    AREA_COIL,
    AREA_DISCRETE,
    AREA_HOLDING,
    AREA_INPUT,
    MAX_BITS_PER_REQUEST,
    MAX_REGS_PER_REQUEST,
    parse_modbus_address,
    validate_modbus_address,
)
from models.connection import Connection
from database.db_service import DatabaseService


class ModbusClientDriver(BaseDriver):
    DRIVER_TYPE = "modbus_client"

    ADDRESS_HINT = "Адрес Modbus: 100 (HR), HR100, IR200, C7, DI15"

    ADDRESS_EXAMPLES = [
        ("0", "INT16", "Holding register №0 (число без префикса = HR)"),
        ("HR100", "UINT16", "Holding register №100"),
        ("HR40001", "FLOAT", "Занимает 2 регистра: HR40001 и HR40002"),
        ("IR200", "UINT16", "Input register №200 (FC04, только чтение)"),
        ("C7", "BOOL", "Coil №7 — дискретный выход (FC01)"),
        ("DI15", "BOOL", "Discrete input №15 — дискретный вход (FC02)"),
    ]

    # Методы pymodbus по областям: FC03 / FC04 / FC01 / FC02
    READ_METHODS = {
        AREA_HOLDING: "read_holding_registers",
        AREA_INPUT: "read_input_registers",
        AREA_COIL: "read_coils",
        AREA_DISCRETE: "read_discrete_inputs",
    }

    # Разрыв между адресами, который выгоднее прочитать «заодно»
    merge_gap = 8

    @classmethod
    def validate_address(cls, address: str, data_type: str = "FLOAT"):
        return validate_modbus_address(address, data_type)

    def __init__(self, connection: Connection, db_service: DatabaseService, registry=None):
        super().__init__(connection, db_service, registry=registry)
        self.ip = self.connection.config.get("ip", "127.0.0.1")
        self.port = int(self.connection.config.get("port", 502))
        self.slave_id = int(self.connection.config.get("slave_id", 1))
        self.client = None
        # pymodbus >= 3.7 переименовал slave= в device_id=, а в 3.15 старое
        # имя удалено полностью. Запоминаем, какое ключевое слово принимает
        # клиент, чтобы драйвер работал и на 3.5, и на 3.15.
        self._device_kw = "device_id"
        # Кэш списка тегов, чтобы не читать БД каждый цикл
        self.tag_cache_ttl = 2.0
        self._tags = []
        self._tags_at = 0.0
        self._req_count = 0
        self.last_error = None

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        self.status = "Connecting..."
        self.client = ModbusTcpClient(self.ip, port=self.port, timeout=2.0)
        period = max(0.001, self.connection.poll_interval_ms / 1000.0)

        while self.is_running:
            if not self.client.connected:
                if not self.client.connect():
                    self.status = "Connect Error"
                    time.sleep(2.0)
                    continue

            cycle_start = time.perf_counter()
            self.last_error = None
            try:
                tags = self._get_tags()
                self._req_count = 0
                t0 = time.perf_counter()
                regs = self._read_blocks(tags)
                read_ms = time.perf_counter() - t0

                now = datetime.now()
                values = {}
                for t in tags:
                    raw = self._decode_raw(t, regs)
                    if raw is not None:
                        # submit_values / TagPoint применят scale/offset сами
                        values[t.id] = raw
                published = self.submit_values(values, now)
                if not published:
                    batch = []
                    for t in tags:
                        raw = values.get(t.id)
                        if raw is not None:
                            batch.append((now, t.id, float(raw * t.scale + t.offset_val)))
                    if batch:
                        self.db.insert_batch(batch)
                missing = [t.id for t in tags if t.id not in values]
                if missing:
                    self.mark_cycle_errors(missing)

                cycle_s = time.perf_counter() - cycle_start
                self._update_stats(cycle_s, read_ms, self._req_count, len(tags))
                over = " ПЕРЕГРУЗ" if cycle_s > period * 1.05 else ""
                s = self.stats
                self.status = (
                    f"Polling · {s['tags']} сигн./{s['requests']} запр. · "
                    f"{s['cycle_ms']:.1f} мс/цикл ({s['hz']:.0f} Гц){over}"
                )
            except Exception as e:
                self.status = f"Poll error: {e}"

            remaining = period - (time.perf_counter() - cycle_start)
            if remaining > 0:
                time.sleep(remaining)

        if self.client:
            self.client.close()
        self.status = "Stopped"

    def _get_tags(self):
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

    # ------------------------------------------------------------- transport
    def _request(self, method_name: str, address: int, count: int):
        """
        Вызов read_* с совместимостью по имени параметра адреса устройства:
        pymodbus 3.5-3.6 знает только slave=, 3.7+ — device_id= (в 3.15
        slave= удалён). Первый TypeError переключает имя, дальше без проб.
        """
        meth = getattr(self.client, method_name)
        names = ("device_id", "slave") if self._device_kw == "device_id" else ("slave", "device_id")
        for i, kw in enumerate(names):
            try:
                result = meth(address, count=count, **{kw: self.slave_id})
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                if i + 1 >= len(names):
                    return None
                continue
            # Запоминаем имя, которое реально принял клиент
            self._device_kw = kw
            return result
        return None

    # ------------------------------------------------------------------ plan
    def _plan_by_area(self, tags) -> Dict[str, List[Tuple[int, int]]]:
        """
        Собирает блоки чтения по областям: соседние адреса одной области
        читаются одним запросом вместо запроса на каждый тег.
        Возвращает {область: [(start, count), ...]}.
        """
        needed: Dict[str, set] = {}
        for t in tags:
            a = parse_modbus_address(t.address_str, t.data_type or "")
            if a is None:
                continue
            offs = needed.setdefault(a.area, set())
            offs.add(a.offset)
            if not a.is_bit and (t.data_type or "").upper() == "FLOAT":
                # FLOAT занимает второй регистр: его тоже надо прочитать
                offs.add(a.offset + 1)

        blocks: Dict[str, List[Tuple[int, int]]] = {}
        for area, offs in needed.items():
            limit = MAX_BITS_PER_REQUEST if area in (AREA_COIL, AREA_DISCRETE) \
                else MAX_REGS_PER_REQUEST
            addrs = sorted(offs)
            out = []
            start = prev = addrs[0]
            for x in addrs[1:]:
                if x <= prev + 1 + self.merge_gap and (x - start + 1) <= limit:
                    prev = x
                    continue
                out.append((start, prev - start + 1))
                start = prev = x
            out.append((start, prev - start + 1))
            blocks[area] = out
        return blocks

    # ----------------------------------------------------------------- reads
    def _read_blocks(self, tags) -> Dict[tuple, object]:
        """
        Читает все блоки всех областей.
        Возвращает {(область, смещение): значение}: int для регистров,
        bool для битовых областей.
        """
        cells: Dict[tuple, object] = {}
        for area, blocks in self._plan_by_area(tags).items():
            method = self.READ_METHODS[area]
            bitwise = area in (AREA_COIL, AREA_DISCRETE)
            for start, count in blocks:
                rr = self._request(method, start, count)
                self._req_count += 1
                if rr is None or rr.isError():
                    self.last_error = f"FC{area} {start}+{count}: {rr}"
                    continue
                if bitwise:
                    for i, bit in enumerate(rr.bits[:count]):
                        cells[(area, start + i)] = bool(bit)
                else:
                    for i, v in enumerate(rr.registers[:count]):
                        cells[(area, start + i)] = int(v)
        return cells

    # ----------------------------------------------------------------- decode
    @staticmethod
    def _decode_raw(tag, cells: Dict[tuple, object]) -> Optional[float]:
        """Превращает прочитанную ячейку области в числовое значение тега."""
        a = parse_modbus_address(tag.address_str, tag.data_type or "")
        if a is None:
            return None
        dtype = (tag.data_type or "").upper()

        if a.is_bit:
            bit = cells.get((a.area, a.offset))
            if bit is None:
                return None
            return 1.0 if bit else 0.0

        v = cells.get((a.area, a.offset))
        if v is None:
            return None
        v = int(v)
        if dtype == "FLOAT":
            v2 = cells.get((a.area, a.offset + 1))
            if v2 is None:
                return None
            raw_b = struct.pack(">HH", v, int(v2))
            return struct.unpack(">f", raw_b)[0]
        if dtype == "INT16":
            return float(struct.unpack(">h", struct.pack(">H", v))[0])
        if dtype == "BOOL":
            return 1.0 if v > 0 else 0.0
        return float(v)

    def _update_stats(self, cycle_s, read_s, requests, tag_count):
        k = 0.2
        s = self.stats
        base = s["cycle_ms"] == 0
        s["read_ms"] = read_s * 1000.0 if base else s["read_ms"] * (1 - k) + read_s * 1000.0 * k
        s["cycle_ms"] = cycle_s * 1000.0 if base else s["cycle_ms"] * (1 - k) + cycle_s * 1000.0 * k
        s["requests"] = requests
        s["tags"] = tag_count
        s["hz"] = (1.0 / cycle_s) if cycle_s > 0 else 0.0

    def stop(self):
        self.is_running = False
        self.status = "Stopped"
