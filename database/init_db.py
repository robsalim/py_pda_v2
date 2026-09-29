import os
import psycopg2
from config import DB_CONFIG

os.environ["PGCLIENTENCODING"] = "utf-8"

def init_tables():
    params = dict(DB_CONFIG)
    params["client_encoding"] = "utf-8"
    try:
        conn = psycopg2.connect(**params)
        conn.close()
        print("[DB] Подключение к pda_data успешно подтверждено.")
    except Exception as e:
        print(f"[DB Warning] Ошибка проверки базы: {e}")

if __name__ == "__main__":
    init_tables()