from dataclasses import dataclass

@dataclass
class Tag:
    id: int
    connection_id: int
    name: str
    address_str: str   # Например "0", "100", "DB1.DBD0", "DB1.DBX0.0"
    data_type: str     # 'FLOAT', 'INT16', 'UINT16', 'BOOL'
    scale: float = 1.0
    offset_val: float = 0.0
    unit: str = ""
    group_name: str = "Общие"
    # Собственная частота регистрации в историю, мс (0 = только по изменению).
    # Отделена от poll_interval_ms подключения: опрос может идти на 100 Гц,
    # а писаться в архив — раз в секунду.
    log_interval_ms: int = 1000
    # Мёртвая зона: изменение меньше этого порога в историю не пишется
    deadband: float = 0.0
