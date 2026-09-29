import time
import struct
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from pymodbus.client import ModbusTcpClient

from drivers.base_driver import BaseDriver
from models.connection import Connection
from database.db_service import DatabaseService


class ModbusClientDriver(BaseDriver):
    DRIVER_TYPE = "modbus_client"

    ADDRESS_HINT = "Десятичный адрес регистра: 0, 100, 40001"

    ADDRESS_EXAMPLES = [
        ("0", "INT16", "Holding register №0"),
        ("100", "UINT16", "Holding register №100"),
        ("40001", "FLOAT", "Занимает 2 регистра: 40001 и 40002"),
    ]

    # Лимит регистров в одном запросе FC03 по спецификации Modbus
    MAX_REGS_PER_REQUEST = 125
    # Разрыв между адресами, который выгоднее прочитать «заодно»
    merge_gap = 8

    @classmethod
    def validate_address(cls, address: str, data_type: str = "FLOAT"):
        base_err = super().validate_address(address, data_type)
        if base_err:
            return base_err
        try:
            value = int(str(address).strip())
        except ValueError:
            return (f"Не понимаю адрес '{address}'. Modbus — это целое число "
                    f"адреса регистра, например 0, 100 или 40001")
        if value < 0:
            return "Адрес регистра не может быть отрицательным"
        return None

    def __init__(self, connection: Connection, db_service: DatabaseService, registry=None):
        super().__init__(connection, db_service, registry=registry)
        self.ip = self.connection.config.get("ip", "127.0.0.1")
        self.port = int(self.connection.config.get("port", 502))
        self.slave_id = int(self.connection.config.get("slave_id", 1))
        self.client = None
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

    # ------------------------------------------------------------------ reads
    def _plan_blocks(self, tags) -> List[Tuple[int, int]]:
        """
        Собирает из адресов тегов регистровые блоки: соседние адреса читаются
        одним запросом FC03 вместо запроса на каждый тег.
        Возвращает [(start, count), ...].
        """
        needed = set()
        for t in tags:
            try:
                addr = int(t.address_str)
            except (TypeError, ValueError):
                continue
            needed.add(addr)
            if (t.data_type or "").upper() == "FLOAT":
                needed.add(addr + 1)
        if not needed:
            return []

        addrs = sorted(needed)
        blocks = []
        start = prev = addrs[0]
        for a in addrs[1:]:
            if a <= prev + 1 + self.merge_gap and (a - start + 1) <= self.MAX_REGS_PER_REQUEST:
                prev = a
                continue
            blocks.append((start, prev - start + 1))
            start = prev = a
        blocks.append((start, prev - start + 1))
        return blocks

    def _read_blocks(self, tags) -> Dict[int, int]:
        """Читает все блоки и возвращает {номер_регистра: значение}."""
        regs: Dict[int, int] = {}
        for start, count in self._plan_blocks(tags):
            rr = self.client.read_holding_registers(start, count=count, slave=self.slave_id)
            self._req_count += 1
            if rr is None or rr.isError():
                self.last_error = f"FC03 {start}+{count}: {rr}"
                continue
            for i, v in enumerate(rr.registers):
                regs[start + i] = v
        return regs

    # ----------------------------------------------------------------- decode
    @staticmethod
    def _decode_raw(tag, regs: Dict[int, int]) -> Optional[float]:
        try:
            addr = int(tag.address_str)
        except (TypeError, ValueError):
            return None
        dtype = (tag.data_type or "").upper()
        if dtype == "FLOAT":
            if addr not in regs or addr + 1 not in regs:
                return None
            raw_b = struct.pack(">HH", regs[addr], regs[addr + 1])
            return struct.unpack(">f", raw_b)[0]
        if addr not in regs:
            return None
        v = regs[addr]
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
