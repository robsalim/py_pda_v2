from abc import ABC, abstractmethod
import threading
from typing import List, Optional, Tuple
from models.connection import Connection
from database.db_service import DatabaseService

class BaseDriver(ABC):
    # Канонический тип драйвера (имя библиотеки), пишется в connections.driver_type
    DRIVER_TYPE: str = ""

    # Подсказка по формату адреса в поле ввода
    ADDRESS_HINT: str = ""

    # Примеры для справки и подсказок: (адрес, тип данных, описание)
    ADDRESS_EXAMPLES: List[Tuple[str, str, str]] = []

    @classmethod
    def validate_address(cls, address: str, data_type: str = "FLOAT") -> Optional[str]:
        """Возвращает текст ошибки для адреса либо None, если адрес корректен."""
        if not (address or "").strip():
            return "Адрес не заполнен"
        return None

    def __init__(self, connection: Connection, db_service: DatabaseService,
                 registry=None):
        self.connection = connection
        self.db = db_service
        # Единый реестр тегов DriverManager: драйвер пишет показания сюда,
        # а не в собственные словари. Без него (тесты, offline-режим)
        # драйвер работает по-старому — пишет сразу в историю.
        self.registry = registry
        self.is_running = False
        self.status = "Stopped"
        self._thread: threading.Thread = None
        # Метрики нагрузки для отображения в GUI
        self.stats = {"read_ms": 0.0, "db_ms": 0.0, "cycle_ms": 0.0,
                      "requests": 0, "tags": 0, "hz": 0.0}

    def submit_values(self, values: dict, ts):
        """
        Драйвер вызывает этот метод с парами {tag_id: raw_value}.
        Масштабирование и публикация живого состояния — на стороне реестра,
        а в историю пишет журнал по своим правилам (COV + log_interval_ms),
        поэтому цикл опроса не зависит от скорости базы.
        Возвращает False, если реестра нет — вызывающий должен писать сам.
        """
        if self.registry is None:
            return False
        from core.tag_point import QUALITY_GOOD
        for tid, raw in values.items():
            self.registry.update_value(tid, raw, QUALITY_GOOD, ts)
        return True

    def mark_cycle_errors(self, tag_ids):
        if self.registry is not None:
            self.registry.mark_bad(tag_ids)

    @abstractmethod
    def start(self):
        pass

    @abstractmethod
    def stop(self):
        pass
