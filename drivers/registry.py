"""
Реестр драйверов.

Каноническое имя типа драйвера (driver_type) совпадает с названием библиотеки,
на которой драйвер построен, а не с моделью конкретного устройства.
Например: 'snap7' (python-snap7), а не 's7_1200'.

Старые имена, встретившиеся в БД или в JSON-резервных копиях, приводятся
к каноническим через LEGACY_DRIVER_TYPES — менять данные вручную не нужно.
"""

from importlib import import_module
from typing import Dict, Optional, Type

from drivers.base_driver import BaseDriver


# Ключ -> "модуль:Класс". Ключ пишется в таблицу connections.driver_type.
# Классы импортируются лениво: отсутствие pymodbus или snap7 не должно ломать
# загрузку моделей (Connection вызывает normalize_driver_type).
DRIVER_ENTRY_POINTS: Dict[str, str] = {
    "snap7": "drivers.snap7_driver:Snap7Driver",
    "modbus_client": "drivers.modbus_client_driver:ModbusClientDriver",
    "modbus_server": "drivers.modbus_server_driver:ModbusServerDriver",
}

# Ключ -> подпись для интерфейса.
DRIVER_LABELS: Dict[str, str] = {
    "snap7": "snap7 (Siemens S7-300/400/1200/1500)",
    "modbus_client": "pymodbus (Modbus TCP Client)",
    "modbus_server": "pymodbus (Modbus TCP Server)",
}

# Устаревшие написания -> каноническое имя.
LEGACY_DRIVER_TYPES: Dict[str, str] = {
    "s7_1200": "snap7",
    "s7-1200": "snap7",
    "s7_300": "snap7",
    "s7_1500": "snap7",
    "siemens_s7": "snap7",
    "siemens": "snap7",
    "s7": "snap7",
}

_class_cache: Dict[str, Type[BaseDriver]] = {}


def normalize_driver_type(value: str) -> str:
    """Приводит любое написание типа драйвера к каноническому имени."""
    if not value:
        return value
    key = str(value).strip().lower()
    return LEGACY_DRIVER_TYPES.get(key, key)


def get_driver_class(driver_type: str) -> Optional[Type[BaseDriver]]:
    """Возвращает класс драйвера по его типу (с учётом старых имён)."""
    key = normalize_driver_type(driver_type)
    entry_point = DRIVER_ENTRY_POINTS.get(key)
    if not entry_point:
        return None
    if key in _class_cache:
        return _class_cache[key]
    module_name, _, class_name = entry_point.partition(":")
    try:
        driver_class = getattr(import_module(module_name), class_name, None)
    except ImportError:
        # Библиотека драйвера не установлена — драйвер просто недоступен
        return None
    if driver_class is not None:
        _class_cache[key] = driver_class
    return driver_class


def get_driver_label(driver_type: str) -> str:
    """Возвращает подпись драйвера для отображения в интерфейсе."""
    key = normalize_driver_type(driver_type)
    return DRIVER_LABELS.get(key, key)


def driver_type_keys():
    """Список канонических имён типов драйверов в порядке отображения."""
    return list(DRIVER_ENTRY_POINTS.keys())

