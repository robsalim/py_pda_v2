from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass
class Connection:
    id: int
    name: str
    # Канонические имена типов драйверов см. в drivers/registry.py
    # ('snap7', 'modbus_client', 'modbus_server')
    driver_type: str
    enabled: bool = True
    poll_interval_ms: int = 100
    config: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Старые написания из БД/резервных копий ('s7_1200') приводим к каноническому
        from drivers.registry import normalize_driver_type
        self.driver_type = normalize_driver_type(self.driver_type)
