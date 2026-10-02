import asyncio
import struct
import threading
import time
from datetime import datetime
from typing import Dict, List, Tuple

from database.db_service import DatabaseService
from drivers.base_driver import BaseDriver
from drivers.modbus_address import (
    AREA_COIL,
    AREA_HOLDING,
    MODBUS_AREAS_SERVER,
    parse_modbus_address,
    validate_modbus_address,
)
from models.connection import Connection


class ModbusServerDriver(BaseDriver):
    DRIVER_TYPE = "modbus_server"

    # Сервер эмулирует только записываемые области: HR и C. Input Registers
    # и Discrete Inputs наполнять нечем (их writes в спецификации нет),
    # поэтому тег в такой области — ошибка конфигурации.
    ADDRESS_HINT = "Адрес Modbus (сервер): 100 (HR), HR100, C7"

    #: Список областей для конструктора в диалоге переменной
    ADDRESS_AREAS = MODBUS_AREAS_SERVER

    ADDRESS_EXAMPLES = [
        ("0", "INT16", "Holding register №0 (число без префикса = HR)"),
        ("HR100", "UINT16", "Holding register №100 (FC03/06/16)"),
        ("HR40001", "FLOAT", "Занимает 2 регистра: HR40001 и HR40002"),
        ("C7", "BOOL", "Coil №7 (FC01/05/15)"),
    ]

    # FC -> (область, запись?)
    _FUNCTION_AREAS = {
        1: (AREA_COIL, False),
        3: (AREA_HOLDING, False),
        5: (AREA_COIL, True),
        6: (AREA_HOLDING, True),
        15: (AREA_COIL, True),
        16: (AREA_HOLDING, True),
    }

    # Коды исключений Modbus
    EX_ILLEGAL_FUNCTION = 0x01
    EX_ILLEGAL_ADDRESS = 0x02
    EX_ILLEGAL_DATA_VALUE = 0x03

    @classmethod
    def validate_address(cls, address: str, data_type: str = "FLOAT"):
        err = validate_modbus_address(address, data_type)
        if err:
            return err
        a = parse_modbus_address(address, data_type)
        if a is not None and not a.writable:
            return (f"Сервер не эмулирует область {a.area}: Input Registers и "
                    f"Discrete Inputs по Modbus нельзя записать — наполнять их нечем. "
                    f"Доступны Holding Registers (HR) и Coils (C)")
        return None

    def __init__(self, connection: Connection, db_service: DatabaseService, registry=None):
        super().__init__(connection, db_service, registry=registry)
        self.port = int(self.connection.config.get("port", 502))
        self.host = self.connection.config.get("host", "0.0.0.0")
        # Две карты памяти: записываемые области сервера. self.registers
        # оставлено как ссылка на holding-область: старые тесты/консоль
        # обращаются именно к нему.
        self.maps: Dict[str, Dict[int, object]] = {
            AREA_HOLDING: {},
            AREA_COIL: {},
        }
        self.registers = self.maps[AREA_HOLDING]
        self._loop = None
        self._server = None
        self._lock = threading.Lock()
        # Кэш «(область, адрес) -> теги», чтобы не читать БД на каждом пакете
        self.tag_cache_ttl = 2.0
        self._addr_tags: Dict[tuple, List] = {}
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
        except (asyncio.CancelledError, OSError):
            # Нормальная остановка: stop() закрывает сервер, serve_forever
            # отменяется. Без этого поток печатает traceback в консоль.
            pass
        except Exception as e:
            self.status = f"Error: {e}"
        finally:
            self.is_running = False
            try:
                self._loop.run_until_complete(self._shutdown_async())
            except Exception:
                pass
            self._loop.close()

    async def _shutdown_async(self):
        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass

    async def _start_async(self):
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        self.status = f"Running (Port {self.port})"
        async with self._server:
            await self._server.serve_forever()

    # ------------------------------------------------------------------ tags
    def _get_addr_tags(self) -> Dict[tuple, List]:
        """
        {(область, смещение): [тег, ...]} с кэшем — источник: реестр или БД.
        Регистровые теги перекрывают и второй регистр FLOAT, битовые —
        только свою ячейку.
        """
        now = time.perf_counter()
        if not self._addr_tags or (now - self._addr_tags_at) >= self.tag_cache_ttl:
            if self.registry is not None:
                tags = self.registry.points_for(self.connection.id)
            else:
                tags = self.db.get_tags_by_connection(self.connection.id)
            mapping: Dict[tuple, List] = {}
            for t in tags:
                a = parse_modbus_address(t.address_str, t.data_type or "")
                if a is None:
                    continue
                mapping.setdefault((a.area, a.offset), []).append(t)
                if not a.is_bit and (t.data_type or "").upper() == "FLOAT":
                    # FLOAT занимает второй регистр: запись в него тоже важна
                    mapping.setdefault((a.area, a.offset + 1), []).append(t)
            self._addr_tags = mapping
            self._addr_tags_at = now
        return self._addr_tags

    def _apply_write(self, area: str, start_addr: int, reg_cnt: int, rx_timestamp: datetime):
        """
        После записи карт памяти: декодируем затронутые теги и публикуем их
        в реестр с меткой времени прихода посылки. Журнал запишет в БД сам,
        поэтому сетевой цикл не блокируется доступом к базе.
        """
        mapping = self._get_addr_tags()
        values: Dict[int, float] = {}
        touched: Dict[int, Tuple] = {}
        for off in range(start_addr, start_addr + reg_cnt):
            for t in mapping.get((area, off), ()):
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
        """Декодирует raw-значение тега из текущей карты памяти его области."""
        a = parse_modbus_address(tag.address_str, tag.data_type or "")
        if a is None:
            return None
        cell = self.maps[a.area]
        dtype = (tag.data_type or "").upper()

        if a.is_bit:
            return 1.0 if cell.get(a.offset, False) else 0.0

        if dtype == "FLOAT":
            r0 = cell.get(a.offset, 0)
            r1 = cell.get(a.offset + 1, 0)
            return struct.unpack(">f", struct.pack(">HH", int(r0), int(r1)))[0]
        v = int(cell.get(a.offset, 0))
        if dtype == "INT16":
            return float(struct.unpack(">h", struct.pack(">H", v))[0])
        if dtype == "BOOL":
            return 1.0 if v > 0 else 0.0
        return float(v)

    # ---------------------------------------------------------------- traffic
    @staticmethod
    def _exception(fc: int, code: int) -> bytes:
        """PDU исключения: 0x80|fc + код."""
        return struct.pack(">BB", fc + 0x80, code)

    def _read_cells(self, area: str, start: int, count: int, bitwise: bool):
        """
        Возвращает (byte_count, payload) для ответа FC01/02/03/04 либо
        None-кортеж с кодом ошибки.
        """
        if count < 1:
            return None, self.EX_ILLEGAL_DATA_VALUE
        limit = 2000 if bitwise else 125
        if count > limit:
            return None, self.EX_ILLEGAL_DATA_VALUE
        if start + count > 0x10000:
            return None, self.EX_ILLEGAL_ADDRESS

        cell = self.maps[area]
        with self._lock:
            if bitwise:
                bits = [bool(cell.get(start + i, False)) for i in range(count)]
                nbytes = (count + 7) // 8
                data = bytearray(nbytes)
                for i, b in enumerate(bits):
                    if b:
                        data[i // 8] |= (1 << (i % 8))
                return nbytes, bytes(data)
            vals = [int(cell.get(start + i, 0) or 0) & 0xFFFF for i in range(count)]
        return len(vals) * 2, b"".join(struct.pack(">H", v) for v in vals)

    def _pack_read_resp(self, fc: int, byte_count: int, payload: bytes) -> bytes:
        return struct.pack(">BB", fc, byte_count) + payload

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        while self.is_running:
            try:
                # MBAP Header: Transaction ID, Protocol ID, Length, Unit ID (7 байт)
                header = await reader.readexactly(7)
            except (asyncio.IncompleteReadError, ConnectionError):
                break

            # ВАЖНО: Фиксируем точное время прихода посылки сразу в момент чтения первого байта
            rx_timestamp = datetime.now()

            tid, pid, length, uid = struct.unpack(">HHHB", header)
            if length < 2 or length > 260:
                break
            try:
                pdu = await reader.readexactly(length - 1)
            except (asyncio.IncompleteReadError, ConnectionError):
                break

            fc = pdu[0]
            resp_pdu = b""

            # ---------------------------------- чтение: FC01/03 (FC02/04 — нет карт)
            if fc in (1, 3):
                area = {1: AREA_COIL, 3: AREA_HOLDING}[fc]
                if len(pdu) < 5:
                    resp_pdu = self._exception(fc, self.EX_ILLEGAL_DATA_VALUE)
                else:
                    start_addr, count = struct.unpack(">HH", pdu[1:5])
                    byte_count, payload = self._read_cells(area, start_addr, count,
                                                           bitwise=fc in (1, 2))
                    if byte_count is None:
                        resp_pdu = self._exception(fc, payload)
                    else:
                        resp_pdu = self._pack_read_resp(fc, byte_count, payload)

            # ---------------------------------- запись: FC05 coil
            elif fc == 5:
                if len(pdu) < 5:
                    resp_pdu = self._exception(fc, self.EX_ILLEGAL_DATA_VALUE)
                else:
                    addr, raw = struct.unpack(">HH", pdu[1:5])
                    if raw not in (0x0000, 0xFF00):
                        resp_pdu = self._exception(fc, self.EX_ILLEGAL_DATA_VALUE)
                    else:
                        value = raw == 0xFF00
                        with self._lock:
                            self.maps[AREA_COIL][addr] = value
                        self._apply_write(AREA_COIL, addr, 1, rx_timestamp)
                        resp_pdu = struct.pack(">BHH", 5, addr, raw)

            # ---------------------------------- запись: FC15 multiple coils
            elif fc == 15:
                if len(pdu) < 6:
                    resp_pdu = self._exception(fc, self.EX_ILLEGAL_DATA_VALUE)
                else:
                    start_addr, bit_cnt, byte_cnt = struct.unpack(">HHB", pdu[1:6])
                    need = 6 + byte_cnt
                    if len(pdu) < need or bit_cnt < 1 or byte_cnt != (bit_cnt + 7) // 8:
                        resp_pdu = self._exception(fc, self.EX_ILLEGAL_DATA_VALUE)
                    else:
                        data = pdu[6:need]
                        with self._lock:
                            for i in range(bit_cnt):
                                bit = bool(data[i // 8] & (1 << (i % 8)))
                                self.maps[AREA_COIL][start_addr + i] = bit
                        self._apply_write(AREA_COIL, start_addr, bit_cnt, rx_timestamp)
                        resp_pdu = struct.pack(">BHH", 15, start_addr, bit_cnt)

            # ---------------------------------- запись: FC16 multiple registers
            elif fc == 16:
                if len(pdu) < 6:
                    resp_pdu = self._exception(fc, self.EX_ILLEGAL_DATA_VALUE)
                else:
                    start_addr, reg_cnt, byte_cnt = struct.unpack(">HHB", pdu[1:6])
                    need = 6 + byte_cnt
                    if len(pdu) < need or reg_cnt < 1 or byte_cnt != reg_cnt * 2:
                        resp_pdu = self._exception(fc, self.EX_ILLEGAL_DATA_VALUE)
                    else:
                        reg_bytes = pdu[6:need]
                        with self._lock:
                            for i in range(reg_cnt):
                                val = struct.unpack(">H", reg_bytes[i * 2: (i + 1) * 2])[0]
                                self.maps[AREA_HOLDING][start_addr + i] = val
                        self._apply_write(AREA_HOLDING, start_addr, reg_cnt, rx_timestamp)
                        resp_pdu = struct.pack(">BHH", 16, start_addr, reg_cnt)

            # ---------------------------------- запись: FC06 single register
            elif fc == 6:
                if len(pdu) < 5:
                    resp_pdu = self._exception(fc, self.EX_ILLEGAL_DATA_VALUE)
                else:
                    addr, val = struct.unpack(">HH", pdu[1:5])
                    with self._lock:
                        self.maps[AREA_HOLDING][addr] = val
                    self._apply_write(AREA_HOLDING, addr, 1, rx_timestamp)
                    resp_pdu = struct.pack(">BHH", 6, addr, val)

            else:
                resp_pdu = self._exception(fc, self.EX_ILLEGAL_FUNCTION)

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
