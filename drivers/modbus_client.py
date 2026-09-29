import time
import threading
from datetime import datetime
from drivers.base_driver import BaseDriver

class ModbusClientDriver(BaseDriver):
    def __init__(self, connection, tags, on_data_received):
        super().__init__(connection, tags, on_data_received)
        self.ip = self.connection.config.get("ip", "127.0.0.1")
        self.port = self.connection.config.get("port", 502)
        self.unit_id = self.connection.config.get("unit_id", 1)
        self.thread = None

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        self.status = "Polling"

    def stop(self):
        self.is_running = False
        self.status = "Stopped"

    def _poll_loop(self):
        try:
            from pymodbus.client import ModbusTcpClient
        except ImportError:
            self.status = "Error: pymodbus not installed"
            return

        client = ModbusTcpClient(self.ip, port=self.port, timeout=1.0)
        interval = max(0.02, self.connection.poll_interval_ms / 1000.0)

        while self.is_running:
            if not client.connected:
                if not client.connect():
                    self.status = "Connection Failed"
                    time.sleep(2.0)
                    continue
                self.status = "Connected"

            now = datetime.now()
            records = []
            for t in self.tags:
                try:
                    addr = int(t.address_str)
                    rr = client.read_holding_registers(address=addr, count=1, slave=self.unit_id)
                    if not rr.isError():
                        val = rr.registers[0]
                        records.append((now, t.id, float(val * t.scale + t.offset_val)))
                except Exception:
                    pass

            if records:
                self.on_data_received(records)

            time.sleep(interval)

        client.close()
