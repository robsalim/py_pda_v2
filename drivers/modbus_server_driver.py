import asyncio
import struct
import threading
import time
from datetime import datetime
from typing import Dict, List, Tuple

from database.db_service import DatabaseService
from drivers.base_driver import BaseDriver
from models.connection import Connection


class ModbusServerDriver(BaseDriver):
    DRIVER_TYPE = "modbus_server"

    ADDRESS_HINT = "Десятичный адрес регистра: 0, 100, 40001"

    ADDRESS_EXAMPLES = [
        ("0", "INT16", "Holding register №0"),
        ("100", "UINT16", "Holding register №100"),
        ("40001", "FLOAT", "Занимает 2 регистра: 40001 и 40002"),
    ]

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
        self.port = int(self.connection.config.get("port", 502))
        self.host = self.connection.config.get("host", "0.0.0.0")
        self.registers: Dict[int, int] = {}
        self._loop = None
        self._server = None
        self._lock = threading.Lock()
        # Кэш «регистр -> теги», чтобы не читать БД на каждом входящем пакете
        self.tag_cache_ttl = 2.0
        self._addr_tags: Dict[int, List] = {}
        self._addr_tags_at = 0.0

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.status = "Starting..."
        self._thread = threading.Thread(target=self._run_server, daemon=True)
        self._thread.start()

    def _run_server(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._start_async())
        except Exception as e:
            self.is_running = False
            self.status = f"Error: {e}"
        finally:
            self._loop.close()

    async def _start_async(self):
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        self.status = f"Running (Port {self.port})"
        async with self._server:
            await self._server.serve_forever()

    # ------------------------------------------------------------------ tags
    def _get_addr_tags(self) -> Dict[int, List]:
        """{адрес_регистра: [тег, ...]} с кэшем — источник: реестр или БД."""
        now = time.perf_counter()
        if not self._addr_tags or (now - self._addr_tags_at) >= self.tag_cache_ttl:
            if self.registry is not None:
                tags = self.registry.points_for(self.connection.id)
            else:
                tags = self.db.get_tags_by_connection(self.connection.id)
            mapping: Dict[int, List] = {}
            for t in tags:
                try:
                    addr = int(t.address_str)
                except (TypeError, ValueError):
                    continue
                mapping.setdefault(addr, []).append(t)
                if (t.data_type or "").upper() == "FLOAT":
                    # FLOAT занимает второй регистр: запись в него тоже важна
                    mapping.setdefault(addr + 1, []).append(t)
            self._addr_tags = mapping
            self._addr_tags_at = now
        return self._addr_tags

    def _apply_write(self, start_addr: int, reg_cnt: int, rx_timestamp: datetime):
        """
        После записи регистров: декодируем затронутые теги и публикуем их
        в реестр с меткой времени прихода посылки. Журнал запишет в БД сам,
        поэтому сетевой цикл не блокируется доступом к базе.
        """
        mapping = self._get_addr_tags()
        values: Dict[int, float] = {}
        touched: Dict[int, Tuple] = {}
        for addr in range(start_addr, start_addr + reg_cnt):
            for t in mapping.get(addr, ()):
                raw = self._decode(t)
                if raw is not None:
                    values[t.id] = raw
                    touched[t.id] = t
        if not values:
            return
        if not self.submit_values(values, rx_timestamp):
            batch = [(rx_timestamp, tid, float(raw * touched[tid].scale + touched[tid].offset_val))
                     for tid, raw in values.items()]
            if batch:
                try:
                    self.db.insert_batch(batch)
                except Exception:
                    pass

    def _decode(self, tag) -> float:
        """Декодирует raw-значение тега из текущей карты регистров."""
        addr = int(tag.address_str)
        dtype = (tag.data_type or "").upper()
        if dtype == "FLOAT":
            r0 = self.registers.get(addr, 0)
            r1 = self.registers.get(addr + 1, 0)
            return struct.unpack(">f", struct.pack(">HH", r0, r1))[0]
        v = self.registers.get(addr, 0)
        if dtype == "INT16":
            return float(struct.unpack(">h", struct.pack(">H", v))[0])
        if dtype == "BOOL":
            return 1.0 if v > 0 else 0.0
        return float(v)

    # ---------------------------------------------------------------- traffic
    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        while self.is_running:
            # Читаем MBAP Header (7 байт: Transaction ID, Protocol ID, Length, Unit ID)
            header = await reader.read(7)

            # ВАЖНО: Фиксируем точное время прихода посылки сразу в момент чтения первого байта
            rx_timestamp = datetime.now()

            if not header or len(header) < 7:
                break

            tid, pid, length, uid = struct.unpack(">HHHB", header)
            pdu = await reader.read(length - 1)
            if not pdu:
                break

            fc = pdu[0]
            resp_pdu = b""

            # Функция 16 (0x10): Write Multiple Registers
            if fc == 16:
                start_addr, reg_cnt, byte_cnt = struct.unpack(">HHB", pdu[1:6])
                reg_bytes = pdu[6:6 + byte_cnt]

                with self._lock:
                    for i in range(reg_cnt):
                        val = struct.unpack(">H", reg_bytes[i * 2: (i + 1) * 2])[0]
                        self.registers[start_addr + i] = val

                self._apply_write(start_addr, reg_cnt, rx_timestamp)
                resp_pdu = struct.pack(">BHH", 16, start_addr, reg_cnt)

            # Функция 6 (0x06): Write Single Register
            elif fc == 6:
                addr, val = struct.unpack(">HH", pdu[1:5])
                with self._lock:
                    self.registers[addr] = val
                self._apply_write(addr, 1, rx_timestamp)
                resp_pdu = struct.pack(">BHH", 6, addr, val)

            # Функция 3 (0x03): Read Holding Registers
            elif fc == 3:
                start_addr, reg_cnt = struct.unpack(">HH", pdu[1:5])
                vals_bytes = bytearray()
                with self._lock:
                    for i in range(reg_cnt):
                        v = self.registers.get(start_addr + i, 0)
                        vals_bytes.extend(struct.pack(">H", v))
                resp_pdu = struct.pack(">BB", 3, reg_cnt * 2) + vals_bytes

            if resp_pdu:
                resp_mbap = struct.pack(">HHHB", tid, pid, len(resp_pdu) + 1, uid)
                writer.write(resp_mbap + resp_pdu)
                await writer.drain()

        writer.close()
        await writer.wait_closed()

    def stop(self):
        self.is_running = False
        if self._loop and self._server:
            self._loop.call_soon_threadsafe(self._server.close)
        self.status = "Stopped"
