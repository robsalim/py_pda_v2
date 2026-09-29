import asyncio
import struct
import threading
from datetime import datetime
from typing import Dict
from drivers.base_driver import BaseDriver

class ModbusServerDriver(BaseDriver):
    def __init__(self, connection, tags, on_data_received):
        super().__init__(connection, tags, on_data_received)
        self.port = self.connection.config.get("port", 502)
        self.host = self.connection.config.get("host", "0.0.0.0")
        self.registers: Dict[int, int] = {}
        self.loop = None
        self.server = None
        self.thread = None

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        self.status = "Running"

    def stop(self):
        self.is_running = False
        if self.loop and self.server:
            self.loop.call_soon_threadsafe(self.server.close)
        self.status = "Stopped"

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start_server())

    async def _start_server(self):
        try:
            self.server = await asyncio.start_server(self._handle_client, self.host, self.port)
            self.status = f"Running on {self.port}"
            async with self.server:
                await self.server.serve_forever()
        except Exception as e:
            self.status = f"Error: {e}"

    async def _handle_client(self, reader, writer):
        while self.is_running:
            header = await reader.read(7)
            if not header or len(header) < 7:
                break
            tid, pid, length, uid = struct.unpack(">HHHB", header)
            pdu = await reader.read(length - 1)
            if not pdu:
                break
            fc = pdu[0]
            resp_pdu = b""
            now = datetime.now()
            records = []

            if fc == 16:  # Write Multiple Registers
                start_addr, reg_cnt, byte_cnt = struct.unpack(">HHB", pdu[1:6])
                reg_bytes = pdu[6:6 + byte_cnt]
                for i in range(reg_cnt):
                    val = struct.unpack(">H", reg_bytes[i*2:(i+1)*2])[0]
                    addr = start_addr + i
                    self.registers[addr] = val
                    for t in self.tags:
                        try:
                            if int(t.address_str) == addr:
                                records.append((now, t.id, float(val * t.scale + t.offset_val)))
                        except Exception:
                            pass
                resp_pdu = struct.pack(">BHH", 16, start_addr, reg_cnt)

            elif fc == 3:  # Read Holding Registers
                start_addr, reg_cnt = struct.unpack(">HH", pdu[1:5])
                byte_cnt = reg_cnt * 2
                vals_bytes = bytearray()
                for i in range(reg_cnt):
                    val = self.registers.get(start_addr + i, 0)
                    vals_bytes.extend(struct.pack(">H", val))
                resp_pdu = struct.pack(">BB", 3, byte_cnt) + vals_bytes

            if records:
                self.on_data_received(records)

            if resp_pdu:
                resp_mbap = struct.pack(">HHHB", tid, pid, len(resp_pdu) + 1, uid)
                writer.write(resp_mbap + resp_pdu)
                await writer.drain()

        writer.close()
        await writer.wait_closed()
