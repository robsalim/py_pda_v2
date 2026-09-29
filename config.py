import os

DB_CONFIG = {
    "dbname": "pda_data",
    "user": "postgres",
    "password": "25052015",
    "host": "localhost",
    "port": 5432,
    "client_encoding": "utf-8"
}

DEFAULT_MODBUS_SERVER_PORT = 502
WEB_HOST = "0.0.0.0"
WEB_PORT = 8084
APP_NAME = "PDA Next"
APP_VERSION = "2.0.0"

# ------------------------------------------------------------------ журнал
# Регистрация значений отделена от опроса: драйверы пишут в оперативный
# реестр тегов, а поток писателя складывает точки в БД пачками.
JOURNAL_CONFIG = {
    "journal_scan_ms": 200,      # как часто выбирать подлежащие записи точки
    "journal_flush_ms": 1000,    # как часто (минимум) сливать очередь в БД
    "journal_batch": 2000,       # максимум точек в одном INSERT-е
    "journal_queue": 200000,     # глубина буфера; при переполнении точки теряются
    "retention_days": 0,         # 0 = не чистить историю; N = хранить N дней
}
