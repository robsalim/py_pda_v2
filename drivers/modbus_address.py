"""
Разбор и форматирование адресов Modbus с учётом четырёх основных областей.

Modbus делит данные на четыре карты памяти, каждая со своим набором
функционалов (FC = function code):

    HR — Holding Registers  (4x)  FC03 чтение, FC06/FC16 запись
    IR — Input Registers    (3x)  FC04 чтение
    C  — Coils              (0x)  FC01 чтение, FC05/FC15 запись
    DI — Discrete Inputs    (1x)  FC02 чтение

Адрес в переменной — это смещение внутри своей области (0-based), поэтому
область обязана быть частью адреса. Формы, которые понимает разбор:

    "0", "100", "40001"   -> HR (историческая форма, ничего не ломает)
    "HR100", "hr100"      -> HR
    "IR200", "INPUT10"    -> IR
    "C7", "COIL3"         -> C
    "DI15", "DISC2"       -> DI

Смещения не смешиваются между областями: HR100 и IR100 — разные сигналы,
в драйвере они лежат в отдельных картах памяти.
"""
import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# ------------------------------------------------------------------ области
AREA_HOLDING = "HR"
AREA_INPUT = "IR"
AREA_COIL = "C"
AREA_DISCRETE = "DI"

#: Порядок отображения областей в интерфейсе: (код, подпись)
MODBUS_AREAS: Tuple[Tuple[str, str], ...] = (
    (AREA_HOLDING, "HR — Holding Registers (читается/пишется)"),
    (AREA_INPUT, "IR — Input Registers (только чтение)"),
    (AREA_COIL, "C — Coils / дискретные выходы (читается/пишется)"),
    (AREA_DISCRETE, "DI — Discrete Inputs / дискретные входы (только чтение)"),
)

#: Код области -> номер функционала чтения
READ_FC: Dict[str, int] = {
    AREA_HOLDING: 3,
    AREA_INPUT: 4,
    AREA_COIL: 1,
    AREA_DISCRETE: 2,
}

#: Код области -> номера функционалов записи (пусто = область только для чтения)
WRITE_FC: Dict[str, Tuple[int, ...]] = {
    AREA_HOLDING: (6, 16),
    AREA_INPUT: (),
    AREA_COIL: (5, 15),
    AREA_DISCRETE: (),
}

#: Области для конструктора адреса по роли драйвера.
#: Клиент читает всё, что есть в контроллере. Сервер (slave) эмулирует только
#: записываемые карты: IR и DI он наполнить нечем, а врать мастеру нулями
#: хуже, чем честно ответить «такой области нет».
MODBUS_AREAS_CLIENT: Tuple[Tuple[str, str], ...] = MODBUS_AREAS
MODBUS_AREAS_SERVER: Tuple[Tuple[str, str], ...] = tuple(
    item for item in MODBUS_AREAS if item[0] in (AREA_HOLDING, AREA_COIL)
)

#: Битовые области: значения — bool, а не 16-битные слова
BIT_AREAS = frozenset({AREA_COIL, AREA_DISCRETE})

#: Лимиты одного запроса по спецификации Modbus
MAX_REGS_PER_REQUEST = 125   # FC03/FC04
MAX_BITS_PER_REQUEST = 2000  # FC01/FC02/FC05/FC15

#: Максимальное смещение в области (адрес поля — 16 бит)
MAX_OFFSET = 0xFFFF

#: Типы данных, применимые к каждой области
AREA_TYPES: Dict[str, Tuple[str, ...]] = {
    AREA_HOLDING: ("INT16", "UINT16", "FLOAT", "BOOL"),
    AREA_INPUT: ("INT16", "UINT16", "FLOAT", "BOOL"),
    AREA_COIL: ("BOOL",),
    AREA_DISCRETE: ("BOOL",),
}

# Алиасы префиксов -> код области. Порядок важен: длинные совпадения раньше.
_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("HOLDING", AREA_HOLDING),
    ("HOLD", AREA_HOLDING),
    ("DISCRETE", AREA_DISCRETE),
    ("DISC", AREA_DISCRETE),
    ("INPUT", AREA_INPUT),
    ("COIL", AREA_COIL),
    ("HR", AREA_HOLDING),
    ("IR", AREA_INPUT),
    ("DI", AREA_DISCRETE),
    ("H", AREA_HOLDING),
    ("C", AREA_COIL),
)

_ADDR_RE = re.compile(
    r"^\s*%?(?:(?P<prefix>" + "|".join(p for p, _ in _PREFIXES) + r")\s*)?"
    r"(?P<offset>\d{1,6})\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ModbusAddress:
    """Адрес сигнала: область + смещение внутри неё."""

    area: str = AREA_HOLDING
    offset: int = 0

    @property
    def is_bit(self) -> bool:
        """Битовая ли область (coil/discrete input)."""
        return self.area in BIT_AREAS

    @property
    def read_fc(self) -> int:
        return READ_FC[self.area]

    @property
    def writable(self) -> bool:
        return bool(WRITE_FC[self.area])

    @property
    def max_per_request(self) -> int:
        return MAX_BITS_PER_REQUEST if self.is_bit else MAX_REGS_PER_REQUEST

    def size(self, data_type: str = "") -> int:
        """Сколько ячеек области занимает сигнал (FLOAT = 2 регистра)."""
        if self.is_bit:
            return 1
        return 2 if (data_type or "").upper() == "FLOAT" else 1

    def to_string(self) -> str:
        """
        Каноническая строка адреса. HR пишется обычным числом — так адреса
        выглядят ровно как в уже существующих конфигурациях.
        """
        if self.area == AREA_HOLDING:
            return str(self.offset)
        return f"{self.area}{self.offset}"

    def to_display(self) -> str:
        """Строка с явной областью — для подсказок и статусов."""
        return f"{self.area}{self.offset}"


def parse_modbus_address(address: str, data_type: str = "") -> Optional[ModbusAddress]:
    """
    Разбирает строку адреса. Возвращает None, если адрес не читается.
    Число без префикса трактуется как Holding Register — обратная
    совместимость со старыми тегами ("0", "100", "40001").
    """
    if address is None:
        return None
    text = str(address).strip().replace(" ", "").upper().strip("%")
    if not text:
        return None
    # Классическая запись области цифрами: 4x/3x/0x/1x
    area: Optional[str] = None
    if text.startswith("4X"):
        area, text = AREA_HOLDING, text[2:]
    elif text.startswith("3X"):
        area, text = AREA_INPUT, text[2:]
    elif text.startswith("1X"):
        area, text = AREA_DISCRETE, text[2:]
    elif text.startswith("0X"):
        area, text = AREA_COIL, text[2:]

    m = _ADDR_RE.match(text)
    if not m:
        return None
    if area is None:
        prefix = (m.group("prefix") or "").upper()
        area = dict(_PREFIXES).get(prefix, AREA_HOLDING)
    offset = int(m.group("offset"))
    if offset > MAX_OFFSET:
        return None
    return ModbusAddress(area=area, offset=offset)


def format_modbus_address(area: str, offset: int) -> str:
    """Собирает строку адреса из частей конструктора."""
    return ModbusAddress(area=area, offset=offset).to_string()


def allowed_types(area: str) -> Tuple[str, ...]:
    """Типы данных, применимые к области."""
    return AREA_TYPES.get(area, AREA_TYPES[AREA_HOLDING])


def area_of_type(data_type: str, current: str = AREA_HOLDING) -> str:
    """
    Обратная связь конструктора: BOOL просит битовую область,
    регистровые типы не должны оставаться в C/DI.
    """
    dtype = (data_type or "").upper()
    if dtype == "FLOAT":
        return AREA_HOLDING if current in BIT_AREAS else current
    return current


def validate_modbus_address(address: str, data_type: str = "") -> Optional[str]:
    """
    Текст ошибки для адреса либо None, если всё корректно.
    Жёстко отвергается только несовместимость типа и области: BOOL в
    регистровой области — законная трактовка nonzero, а регистровый тип
    в битовой прочитать нельзя.
    """
    text = (address or "").strip()
    if not text:
        return "Адрес не заполнен"
    addr = parse_modbus_address(text, data_type)
    if addr is None:
        return (f"Не понимаю адрес '{text}'. Нужен адрес Modbus: число (HR) либо "
                f"с областью — HR100, IR200, C7, DI15")
    dtype = (data_type or "").upper()
    if addr.is_bit and dtype and dtype != "BOOL":
        return (f"Тип {dtype} не применяется к области {addr.area}: "
                f"Coils и Discrete Inputs — биты, для них нужен BOOL")
    return None
