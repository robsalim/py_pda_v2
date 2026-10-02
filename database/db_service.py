import os
import json
import socket
import sqlite3
from datetime import datetime, timedelta


def diagnose_postgres_failure(params):
    """
    PostgreSQL на Windows с русской локалью отдаёт сообщения об ошибках на этапе
    запуска подключения в кодировке Windows-1251. psycopg2 ждёт UTF-8 и падает с
    UnicodeDecodeError, из-за чего настоящая причина (нет базы, неверный пароль,
    ...) полностью теряется. Функция восстанавливает внятную диагностику.
    """
    host = str(params.get("host") or "localhost")
    port = int(params.get("port") or 5432)
    dbname = str(params.get("dbname") or "")
    user = str(params.get("user") or "")

    try:
        with socket.create_connection((host, port), timeout=3.0):
            pass
    except OSError as exc:
        return (
            f"Сервер PostgreSQL {host}:{port} не отвечает: {exc}. "
            f"Проверьте, запущена ли служба, и правильность порта."
        )

    import psycopg2

    probe = dict(params)
    probe["dbname"] = "postgres"
    try:
        conn = psycopg2.connect(**probe)
    except UnicodeDecodeError:
        return (
            f"Сервер {host}:{port} отверг учётные данные: пользователь '{user}' "
            f"или пароль не подходят. "
            f"(Текст ошибки сервер присылает в Windows-1251, поэтому psycopg2 не смог его отобразить.)"
        )
    except Exception as exc:
        return f"Сервер {host}:{port} отверг подключение: {exc}"

    exists = False
    allowed = True
    names = []
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT datname FROM pg_database WHERE datistemplate = FALSE ORDER BY datname")
            names = [r[0] for r in cur.fetchall()]
            exists = dbname in names
            if exists:
                cur.execute("SELECT has_database_privilege(%s, %s, 'CONNECT')", (user, dbname))
                allowed = bool(cur.fetchone()[0])
    except Exception:
        pass
    finally:
        conn.close()

    if not exists:
        return (
            f"База данных '{dbname}' на сервере {host}:{port} не существует.\n"
            f"Нажмите «+ Создать базу» или укажите существующую: {', '.join(names) or 'нет'}."
        )
    if not allowed:
        return f"Пользователь '{user}' не имеет права подключаться к базе '{dbname}'."
    return f"Сервер {host}:{port} доступен, база '{dbname}' существует, но подключение не установлено."


def connect_postgres(params):
    """psycopg2.connect с заменой нечитаемых UnicodeDecodeError на осмысленную ошибку."""
    import psycopg2
    try:
        return psycopg2.connect(**params)
    except UnicodeDecodeError:
        raise ConnectionError(diagnose_postgres_failure(params)) from None


def create_database(cfg):
    """Создаёт целевую сетевую БД, подключаясь без выбора целевой базы."""
    engine = cfg.get("engine", "mysql")
    dbname = cfg.get("dbname", "pda_data")
    if not dbname:
        raise ValueError("Не указано имя базы данных")

    if engine == "postgres":
        from psycopg2 import sql

        params = {k: v for k, v in cfg.items() if k not in ("engine", "dbname", "sqlite_path")}
        params["dbname"] = "postgres"
        params["client_encoding"] = "utf-8"
        conn = connect_postgres(params)
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
                if cur.fetchone():
                    return False
                cur.execute(sql.SQL("CREATE DATABASE {}") .format(sql.Identifier(dbname)))
        finally:
            conn.close()
        return True

    if engine in ("mariadb", "mysql"):
        import pymysql
        conn = pymysql.connect(
            host=cfg.get("host", "localhost"),
            user=cfg.get("user", "root"),
            password=cfg.get("password", ""),
            port=int(cfg.get("port", 3306)),
            autocommit=True,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE DATABASE IF NOT EXISTS `%s`" % dbname.replace("`", "``"))
                cur.execute("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s", (dbname,))
                return cur.fetchone() is not None
        finally:
            conn.close()

    raise ValueError("Создание базы через GUI доступно только для PostgreSQL и MariaDB/MySQL")

from typing import List, Dict, Tuple, Optional
from config import DB_CONFIG
from models.connection import Connection
from models.tag import Tag
from models.data_point import DataPoint

os.environ["PGCLIENTENCODING"] = "utf-8"

class DatabaseService:
    def __init__(self, **conn_kwargs):
        allow_offline = conn_kwargs.pop("allow_offline", False)
        self.is_available = False
        self.last_error = None
        self.cfg = dict(DB_CONFIG)

        if os.path.exists("db_config.json"):
            try:
                with open("db_config.json", "r", encoding="utf-8") as f:
                    self.cfg.update(json.load(f))
            except Exception:
                pass

        self.cfg.update(conn_kwargs)
        self.engine = self.cfg.get("engine", "sqlite")
        # Есть ли в data_points колонка quality (создаётся миграцией схемы)
        self.has_quality = False
        try:
            self._init_backend()
            self.is_available = True
        except Exception as exc:
            self.last_error = exc
            if not allow_offline:
                raise

    def _require_available(self):
        if not self.is_available:
            raise ConnectionError("База данных недоступна") from self.last_error

    def _init_backend(self):
        if self.engine == "sqlite":
            db_path = self.cfg.get("sqlite_path", "data/pda_local.db")
            parent = os.path.dirname(db_path) or "."
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as exc:
                raise ConnectionError(
                    f"Нет доступа к папке SQLite-базы '{parent}' ({exc}). "
                    f"Путь в db_config.json мог остаться с другого компьютера — "
                    f"укажите существующий каталог, например data/pda_local.db"
                ) from exc
            self._ensure_sqlite_tables(db_path)
        elif self.engine == "postgres":
            self.cfg["client_encoding"] = "utf-8"
            with self._get_connection():
                pass
            self._ensure_postgres_tables()
        elif self.engine in ("mariadb", "mysql"):
            with self._get_connection():
                pass
            self._ensure_mysql_tables()

    def _get_connection(self):
        if self.engine == "sqlite":
            db_path = self.cfg.get("sqlite_path", "data/pda_local.db")
            conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
            conn.row_factory = sqlite3.Row
            # WAL + NORMAL позволяют фону писать пачками, не блокируя
            # читателей (GUI/веб) на каждой транзакции
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA busy_timeout=5000;")
            # foreign_keys здесь НЕ включаем: в старых базах легально живут
            # ссылки на удалённые подключения, а каскад теперь выполняется
            # явными DELETE в delete_connection
            cur.close()
            return conn
        elif self.engine == "postgres":
            params = {k: v for k, v in self.cfg.items() if k not in ("engine", "sqlite_path")}
            return connect_postgres(params)
        elif self.engine in ("mariadb", "mysql"):
            import pymysql
            return pymysql.connect(
                host=self.cfg.get("host", "localhost"),
                user=self.cfg.get("user", "root"),
                password=self.cfg.get("password", ""),
                database=self.cfg.get("dbname", "pda_data"),
                port=int(self.cfg.get("port", 3306)),
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True
            )

    def _ensure_sqlite_tables(self, path):
        with sqlite3.connect(path) as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS connections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    driver_type TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    poll_interval_ms INTEGER DEFAULT 100,
                    config TEXT NOT NULL DEFAULT '{}'
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tag_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    connection_id INTEGER REFERENCES connections(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    UNIQUE(connection_id, name)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    connection_id INTEGER REFERENCES connections(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    address_str TEXT NOT NULL,
                    data_type TEXT NOT NULL DEFAULT 'FLOAT',
                    scale REAL DEFAULT 1.0,
                    offset_val REAL DEFAULT 0.0,
                    unit TEXT DEFAULT '',
                    group_name TEXT DEFAULT 'Общие',
                    log_interval_ms INTEGER DEFAULT 1000,
                    deadband REAL DEFAULT 0.0
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS data_points (
                    timestamp TEXT NOT NULL,
                    tag_id INTEGER NOT NULL,
                    value REAL NOT NULL,
                    quality INTEGER DEFAULT 0
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_dp_ts ON data_points(tag_id, timestamp DESC);")
            # Миграция старых баз: добавляем колонки регистрации, если их нет
            cols = {r[1] for r in cur.execute("PRAGMA table_info(tags);").fetchall()}
            if "log_interval_ms" not in cols:
                cur.execute("ALTER TABLE tags ADD COLUMN log_interval_ms INTEGER DEFAULT 1000;")
            if "deadband" not in cols:
                cur.execute("ALTER TABLE tags ADD COLUMN deadband REAL DEFAULT 0.0;")
            dp_cols = {r[1] for r in cur.execute("PRAGMA table_info(data_points);").fetchall()}
            if "quality" not in dp_cols:
                cur.execute("ALTER TABLE data_points ADD COLUMN quality INTEGER DEFAULT 0;")
            conn.commit()
        self.has_quality = True

    def _ensure_postgres_tables(self):
        # Таблицы создаём отдельной транзакцией: раньше из-за общей try/except
        # сбой create_hypertable откатывал и создание таблиц, и приложение
        # молча работало без схемы.
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS connections (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(100) NOT NULL,
                        driver_type VARCHAR(50) NOT NULL,
                        enabled BOOLEAN DEFAULT TRUE,
                        poll_interval_ms INT DEFAULT 100,
                        config JSONB NOT NULL DEFAULT '{}'
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tag_groups (
                        id SERIAL PRIMARY KEY,
                        connection_id INT REFERENCES connections(id) ON DELETE CASCADE,
                        name VARCHAR(100) NOT NULL,
                        UNIQUE(connection_id, name)
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tags (
                        id SERIAL PRIMARY KEY,
                        connection_id INT REFERENCES connections(id) ON DELETE CASCADE,
                        name VARCHAR(100) NOT NULL,
                        address_str VARCHAR(100) NOT NULL,
                        data_type VARCHAR(20) NOT NULL DEFAULT 'FLOAT',
                        scale DOUBLE PRECISION DEFAULT 1.0,
                        offset_val DOUBLE PRECISION DEFAULT 0.0,
                        unit VARCHAR(20) DEFAULT '',
                        group_name VARCHAR(100) DEFAULT 'Общие',
                        log_interval_ms INT DEFAULT 1000,
                        deadband DOUBLE PRECISION DEFAULT 0.0
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS data_points (
                        "timestamp" TIMESTAMPTZ NOT NULL,
                        tag_id INT NOT NULL,
                        value DOUBLE PRECISION NOT NULL,
                        quality SMALLINT DEFAULT 0
                    );
                """)
                cur.execute('CREATE INDEX IF NOT EXISTS idx_dp_tag_ts ON data_points (tag_id, "timestamp" DESC);')
                # Миграция старых баз: колонки могли отсутствовать (idempotent)
                cur.execute("""
                    ALTER TABLE tags ADD COLUMN IF NOT EXISTS log_interval_ms INT DEFAULT 1000;
                    ALTER TABLE tags ADD COLUMN IF NOT EXISTS deadband DOUBLE PRECISION DEFAULT 0.0;
                    ALTER TABLE data_points ADD COLUMN IF NOT EXISTS quality SMALLINT DEFAULT 0;
                """)
            conn.commit()
        self.has_quality = True

        # TimescaleDB опционален: без него data_points остаётся обычной таблицей.
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
                    cur.execute("SELECT create_hypertable('data_points', 'timestamp', if_not_exists => TRUE);")
                conn.commit()
        except Exception as exc:
            print(f"[DB] TimescaleDB не применён (data_points остаётся обычной таблицей): {exc}")

    def _ensure_mysql_tables(self):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS connections (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            name VARCHAR(100) NOT NULL,
                            driver_type VARCHAR(50) NOT NULL,
                            enabled TINYINT(1) DEFAULT 1,
                            poll_interval_ms INT DEFAULT 100,
                            config JSON
                        ) ENGINE=InnoDB;
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS tag_groups (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            connection_id INT,
                            name VARCHAR(100) NOT NULL,
                            UNIQUE KEY (connection_id, name),
                            FOREIGN KEY (connection_id) REFERENCES connections(id) ON DELETE CASCADE
                        ) ENGINE=InnoDB;
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS tags (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            connection_id INT,
                            name VARCHAR(100) NOT NULL,
                            address_str VARCHAR(100) NOT NULL,
                            data_type VARCHAR(20) NOT NULL DEFAULT 'FLOAT',
                            scale DOUBLE DEFAULT 1.0,
                            offset_val DOUBLE DEFAULT 0.0,
                            unit VARCHAR(20) DEFAULT '',
                            group_name VARCHAR(100) DEFAULT 'Общие',
                            FOREIGN KEY (connection_id) REFERENCES connections(id) ON DELETE CASCADE
                        ) ENGINE=InnoDB;
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS data_points (
                            timestamp DATETIME(6) NOT NULL,
                            tag_id INT NOT NULL,
                            value DOUBLE NOT NULL,
                            INDEX idx_tag_ts (tag_id, timestamp DESC)
                        ) ENGINE=InnoDB;
                    """)
        except Exception:
            pass

    def get_all_connections(self) -> List[Connection]:
        if not self.is_available:
            return []
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, name, driver_type, enabled, poll_interval_ms, config FROM connections ORDER BY id ASC;")
            rows = cur.fetchall()
            cur.close()
            return [
                Connection(
                    id=r["id"] if isinstance(r, dict) or hasattr(r, "keys") else r[0],
                    name=r["name"] if isinstance(r, dict) or hasattr(r, "keys") else r[1],
                    driver_type=r["driver_type"] if isinstance(r, dict) or hasattr(r, "keys") else r[2],
                    enabled=bool(r["enabled"] if isinstance(r, dict) or hasattr(r, "keys") else r[3]),
                    poll_interval_ms=r["poll_interval_ms"] if isinstance(r, dict) or hasattr(r, "keys") else r[4],
                    config=json.loads(r["config"]) if isinstance(r["config"] if isinstance(r, dict) or hasattr(r, "keys") else r[5], str) else (r["config"] if isinstance(r, dict) or hasattr(r, "keys") else r[5])
                )
                for r in rows
            ]

    def add_connection(self, conn_obj: Connection) -> int:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cfg_json = json.dumps(conn_obj.config)
            if self.engine == "postgres":
                cur.execute("INSERT INTO connections (name, driver_type, enabled, poll_interval_ms, config) VALUES (%s, %s, %s, %s, %s) RETURNING id;",
                            (conn_obj.name, conn_obj.driver_type, conn_obj.enabled, conn_obj.poll_interval_ms, cfg_json))
                new_id = cur.fetchone()[0]
            elif self.engine == "sqlite":
                cur.execute("INSERT INTO connections (name, driver_type, enabled, poll_interval_ms, config) VALUES (?, ?, ?, ?, ?);",
                            (conn_obj.name, conn_obj.driver_type, 1 if conn_obj.enabled else 0, conn_obj.poll_interval_ms, cfg_json))
                new_id = cur.lastrowid
            else:
                cur.execute("INSERT INTO connections (name, driver_type, enabled, poll_interval_ms, config) VALUES (%s, %s, %s, %s, %s);",
                            (conn_obj.name, conn_obj.driver_type, conn_obj.enabled, conn_obj.poll_interval_ms, cfg_json))
                new_id = cur.lastrowid
            conn.commit()
            cur.close()
            return new_id

    def delete_connection(self, conn_id: int):
        with self._get_connection() as conn:
            cur = conn.cursor()
            ph = "?" if self.engine == "sqlite" else "%s"
            # Удаляем теги и их историю явно, не полагаясь на каскад внешних
            # ключей: в старых базах SQLite могли остаться «сироты», а в
            # MySQL таблица data_points вообще не связана с tags.
            cur.execute(f"SELECT id FROM tags WHERE connection_id = {ph};", (conn_id,))
            tag_ids = [r[0] if not isinstance(r, dict) else r["id"] for r in cur.fetchall()]
            if tag_ids:
                marks = ", ".join([ph] * len(tag_ids))
                cur.execute(f"DELETE FROM data_points WHERE tag_id IN ({marks});", tuple(tag_ids))
            cur.execute(f"DELETE FROM tags WHERE connection_id = {ph};", (conn_id,))
            cur.execute(f"DELETE FROM tag_groups WHERE connection_id = {ph};", (conn_id,))
            cur.execute(f"DELETE FROM connections WHERE id = {ph};", (conn_id,))
            conn.commit()
            cur.close()

    def get_groups_by_connection(self, conn_id: int) -> List[str]:
        if not self.is_available:
            return []
        with self._get_connection() as conn:
            cur = conn.cursor()
            ph = "?" if self.engine == "sqlite" else "%s"
            cur.execute(f"SELECT name FROM tag_groups WHERE connection_id = {ph} ORDER BY name ASC;", (conn_id,))
            rows = cur.fetchall()
            cur.close()
            return [r[0] if not isinstance(r, dict) else r["name"] for r in rows]

    def add_group(self, conn_id: int, group_name: str):
        with self._get_connection() as conn:
            cur = conn.cursor()
            if self.engine == "sqlite":
                cur.execute("INSERT OR IGNORE INTO tag_groups (connection_id, name) VALUES (?, ?);", (conn_id, group_name))
            elif self.engine == "postgres":
                cur.execute("INSERT INTO tag_groups (connection_id, name) VALUES (%s, %s) ON CONFLICT (connection_id, name) DO NOTHING;", (conn_id, group_name))
            else:
                cur.execute("INSERT IGNORE INTO tag_groups (connection_id, name) VALUES (%s, %s);", (conn_id, group_name))
            conn.commit()
            cur.close()

    def rename_group(self, conn_id: int, old_name: str, new_name: str):
        with self._get_connection() as conn:
            cur = conn.cursor()
            ph = "?" if self.engine == "sqlite" else "%s"
            cur.execute(f"UPDATE tag_groups SET name = {ph} WHERE connection_id = {ph} AND name = {ph};", (new_name, conn_id, old_name))
            cur.execute(f"UPDATE tags SET group_name = {ph} WHERE connection_id = {ph} AND group_name = {ph};", (new_name, conn_id, old_name))
            conn.commit()
            cur.close()

    def delete_group(self, conn_id: int, group_name: str, with_tags: bool = False):
        """
        Удаляет группу. with_tags=True — вместе с её переменными и их
        историей; иначе переменные перекладываются в «Общие».
        Обе ветки в одной транзакции: при ошибке не откатывается «половина»
        (раньше группа могла исчезнуть, а переменные остаться в ней).
        """
        with self._get_connection() as conn:
            cur = conn.cursor()
            ph = "?" if self.engine == "sqlite" else "%s"
            if with_tags:
                cur.execute(
                    f"SELECT id FROM tags WHERE connection_id = {ph} "
                    f"AND COALESCE(group_name, 'Общие') = {ph};",
                    (conn_id, group_name),
                )
                tag_ids = [r[0] if not isinstance(r, dict) else r["id"] for r in cur.fetchall()]
                for chunk in self._chunks(tag_ids, 500):
                    marks = ", ".join([ph] * len(chunk))
                    cur.execute(f"DELETE FROM data_points WHERE tag_id IN ({marks});", tuple(chunk))
                    cur.execute(f"DELETE FROM tags WHERE id IN ({marks});", tuple(chunk))
            else:
                cur.execute(
                    f"UPDATE tags SET group_name = 'Общие' WHERE connection_id = {ph} AND group_name = {ph};",
                    (conn_id, group_name),
                )
            cur.execute(f"DELETE FROM tag_groups WHERE connection_id = {ph} AND name = {ph};", (conn_id, group_name))
            conn.commit()
            cur.close()

    @staticmethod
    def _chunks(items: List, size: int):
        """Нарезка под лимит количества параметров одного запроса."""
        for i in range(0, len(items), size):
            yield items[i:i + size]

    @staticmethod
    def _row_to_tag(r) -> Tag:
        g = (lambda k: r[k]) if (isinstance(r, dict) or hasattr(r, "keys")) else None
        v = (lambda i: r[i])
        def val(key, idx, default=None):
            if g is not None:
                try:
                    x = r[key]
                except (KeyError, IndexError):
                    x = default
            else:
                x = v(idx) if idx < len(r) else default
            return default if x is None else x
        return Tag(
            id=val("id", 0),
            connection_id=val("connection_id", 1),
            name=val("name", 2),
            address_str=val("address_str", 3),
            data_type=val("data_type", 4),
            scale=float(val("scale", 5, 1.0)),
            offset_val=float(val("offset_val", 6, 0.0)),
            unit=val("unit", 7, "") or "",
            group_name=val("group_name", 8, "Общие") or "Общие",
            log_interval_ms=int(val("log_interval_ms", 9, 1000) or 0),
            deadband=float(val("deadband", 10, 0.0) or 0.0),
        )

    def get_group_counts_by_connection(self, conn_id: int):
        """
        (группы, счётчики) для дерева за один SQL-запрос вместо загрузки
        всех тегов: GROUP BY по tags UNION с declared-группами из tag_groups.
        Возвращает dict {имя_группы: количество_тегов}.
        """
        if not self.is_available:
            return {}
        ph = "?" if self.engine == "sqlite" else "%s"
        counts = {}
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT COALESCE(group_name, 'Общие') AS g, COUNT(*) AS n "
                f"FROM tags WHERE connection_id = {ph} GROUP BY COALESCE(group_name, 'Общие');",
                (conn_id,),
            )
            for r in cur.fetchall():
                if isinstance(r, dict) or hasattr(r, "keys"):
                    g, n = r["g"], r["n"]
                else:
                    g, n = r[0], r[1]
                counts[g or "Общие"] = int(n)
            # Добавляем declared-группы без тегов, чтобы они были видны в дереве
            cur.execute(f"SELECT name FROM tag_groups WHERE connection_id = {ph};", (conn_id,))
            for r in cur.fetchall():
                name = r["name"] if (isinstance(r, dict) or hasattr(r, "keys")) else r[0]
                counts.setdefault(name or "Общие", 0)
            cur.close()
        return counts

    # Колонки SELECT для тегов: обычный список и список с алиасом таблицы t
    _TAG_SELECT = ("id, connection_id, name, address_str, data_type, scale, offset_val, "
                   "unit, COALESCE(group_name, 'Общие') as group_name, "
                   "COALESCE(log_interval_ms, 1000) as log_interval_ms, "
                   "COALESCE(deadband, 0.0) as deadband")
    _TAG_SELECT_T = ("t.id, t.connection_id, t.name, t.address_str, t.data_type, t.scale, t.offset_val, "
                     "t.unit, COALESCE(t.group_name, 'Общие') as group_name, "
                     "COALESCE(t.log_interval_ms, 1000) as log_interval_ms, "
                     "COALESCE(t.deadband, 0.0) as deadband")

    def get_all_tags(self) -> List[Tag]:
        if not self.is_available:
            return []
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT {self._TAG_SELECT} FROM tags ORDER BY id ASC;")
            rows = cur.fetchall()
            cur.close()
            return [self._row_to_tag(r) for r in rows]

    def get_configured_tags(self) -> List[Tag]:
        """
        Теги только тех подключений, которые ещё есть в конфигурации.
        Защищает вкладки графиков от «сирот», оставшихся в старых базах после
        удаления модуля (SQLite не применял каскад внешних ключей).
        """
        if not self.is_available:
            return []
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT {self._TAG_SELECT_T} FROM tags t
                INNER JOIN connections c ON c.id = t.connection_id
                ORDER BY t.id ASC;
            """)
            rows = cur.fetchall()
            cur.close()
            return [self._row_to_tag(r) for r in rows]

    def get_tags_by_connection(self, conn_id: int) -> List[Tag]:
        if not self.is_available:
            return []
        with self._get_connection() as conn:
            cur = conn.cursor()
            ph = "?" if self.engine == "sqlite" else "%s"
            cur.execute(f"SELECT id, connection_id, name, address_str, data_type, scale, offset_val, unit, COALESCE(group_name, 'Общие') as group_name, COALESCE(log_interval_ms, 1000) as log_interval_ms, COALESCE(deadband, 0.0) as deadband FROM tags WHERE connection_id = {ph} ORDER BY id ASC;", (conn_id,))
            rows = cur.fetchall()
            cur.close()
            return [self._row_to_tag(r) for r in rows]

    def add_tag(self, tag: Tag) -> int:
        with self._get_connection() as conn:
            cur = conn.cursor()
            if self.engine == "postgres":
                cur.execute("""
                    INSERT INTO tags (connection_id, name, address_str, data_type, scale, offset_val, unit, group_name, log_interval_ms, deadband)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id;
                """, (tag.connection_id, tag.name, tag.address_str, tag.data_type, tag.scale, tag.offset_val, tag.unit, tag.group_name, tag.log_interval_ms, tag.deadband))
                new_id = cur.fetchone()[0]
                cur.execute("INSERT INTO tag_groups (connection_id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING;", (tag.connection_id, tag.group_name))
            elif self.engine == "sqlite":
                cur.execute("""
                    INSERT INTO tags (connection_id, name, address_str, data_type, scale, offset_val, unit, group_name, log_interval_ms, deadband)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (tag.connection_id, tag.name, tag.address_str, tag.data_type, tag.scale, tag.offset_val, tag.unit, tag.group_name, tag.log_interval_ms, tag.deadband))
                new_id = cur.lastrowid
                cur.execute("INSERT OR IGNORE INTO tag_groups (connection_id, name) VALUES (?, ?);", (tag.connection_id, tag.group_name))
            else:
                cur.execute("""
                    INSERT INTO tags (connection_id, name, address_str, data_type, scale, offset_val, unit, group_name)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                """, (tag.connection_id, tag.name, tag.address_str, tag.data_type, tag.scale, tag.offset_val, tag.unit, tag.group_name))
                new_id = cur.lastrowid
                cur.execute("INSERT IGNORE INTO tag_groups (connection_id, name) VALUES (%s, %s);", (tag.connection_id, tag.group_name))
            conn.commit()
            cur.close()
            return new_id

    def update_tag(self, tag: Tag):
        with self._get_connection() as conn:
            cur = conn.cursor()
            ph = "?" if self.engine == "sqlite" else "%s"
            cur.execute(f"""
                UPDATE tags 
                SET name = {ph}, address_str = {ph}, data_type = {ph}, scale = {ph}, offset_val = {ph}, unit = {ph}, group_name = {ph}, log_interval_ms = {ph}, deadband = {ph}
                WHERE id = {ph};
            """, (tag.name, tag.address_str, tag.data_type, tag.scale, tag.offset_val, tag.unit, tag.group_name, tag.log_interval_ms, tag.deadband, tag.id))
            conn.commit()
            cur.close()

    def delete_tag(self, tag_id: int):
        """Удаляет переменную и всю её историю (без этого запись в
        data_points остаётся с висящим tag_id и не видна в интерфейсе)."""
        self.delete_tags([tag_id])

    def delete_tags(self, tag_ids: List[int]):
        """Пакетное удаление переменных вместе с их историей."""
        ids = list(dict.fromkeys(tag_ids or []))
        if not ids:
            return
        with self._get_connection() as conn:
            cur = conn.cursor()
            ph = "?" if self.engine == "sqlite" else "%s"
            for chunk in self._chunks(ids, 500):
                marks = ", ".join([ph] * len(chunk))
                cur.execute(f"DELETE FROM data_points WHERE tag_id IN ({marks});", tuple(chunk))
                cur.execute(f"DELETE FROM tags WHERE id IN ({marks});", tuple(chunk))
            conn.commit()
            cur.close()

    @staticmethod
    def _dt_str(dt: datetime) -> str:
        """
        Текст datetime для SQLite. Разделитель — пробел: так пишет значения
        сам sqlite3 (адаптер datetime), а строковое сравнение с 'T'-форматом
        isoformat() ломает окна меньше суток (' ' < 'T' лексикографически).
        """
        return dt.isoformat(sep=" ")

    def insert_batch(self, records: List[Tuple]):
        """Принимает (timestamp, tag_id, value) или (timestamp, tag_id, value, quality)."""
        if not records:
            return
        with_quality = len(records[0]) == 4
        with self._get_connection() as conn:
            cur = conn.cursor()
            if self.engine == "sqlite":
                if with_quality and self.has_quality:
                    cur.executemany(
                        "INSERT INTO data_points (timestamp, tag_id, value, quality) VALUES (?, ?, ?, ?);",
                        [(self._dt_str(dt), tid, val, q) for dt, tid, val, q in records],
                    )
                else:
                    cur.executemany(
                        "INSERT INTO data_points (timestamp, tag_id, value) VALUES (?, ?, ?);",
                        [(self._dt_str(dt), tid, val) for dt, tid, val, *_r in records],
                    )
            else:
                if with_quality and self.has_quality:
                    cur.executemany(
                        'INSERT INTO data_points ("timestamp", tag_id, value, quality) VALUES (%s, %s, %s, %s);',
                        [tuple(r) for r in records],
                    )
                else:
                    cur.executemany(
                        'INSERT INTO data_points ("timestamp", tag_id, value) VALUES (%s, %s, %s);',
                        [tuple(r[:3]) for r in records],
                    )
            conn.commit()
            cur.close()

    def get_tag_last_values(self) -> Dict[int, Tuple[datetime, float]]:
        if not self.is_available:
            return {}
        with self._get_connection() as conn:
            cur = conn.cursor()
            if self.engine == "postgres":
                # В PostgreSQL TimescaleDB DISTINCT ON дает строго последнюю запись
                cur.execute('SELECT DISTINCT ON (tag_id) tag_id, "timestamp", value FROM data_points ORDER BY tag_id, "timestamp" DESC;')
            else:
                # Универсальный рабочий запрос для SQLite и MySQL:
                # строго соединяем каждую запись с максимальным временем по этому tag_id
                cur.execute("""
                    SELECT dp.tag_id, dp.timestamp, dp.value 
                    FROM data_points dp
                    INNER JOIN (
                        SELECT tag_id, MAX(timestamp) AS max_ts 
                        FROM data_points 
                        GROUP BY tag_id
                    ) sub ON dp.tag_id = sub.tag_id AND dp.timestamp = sub.max_ts;
                """)
            rows = cur.fetchall()
            cur.close()
            result = {}
            for r in rows:
                tid = r["tag_id"] if isinstance(r, dict) or hasattr(r, "keys") else r[0]
                ts = r["timestamp"] if isinstance(r, dict) or hasattr(r, "keys") else r[1]
                val = r["value"] if isinstance(r, dict) or hasattr(r, "keys") else r[2]
                if isinstance(ts, str):
                    try:
                        ts = datetime.fromisoformat(ts)
                    except Exception:
                        pass
                result[tid] = (ts, float(val))
            return result

    def get_data_points_by_data_span(self, tag_id: int, delta: timedelta) -> Tuple[List[DataPoint], float, float]:
        if not self.is_available:
            now = datetime.now()
            return [], (now - delta).timestamp(), now.timestamp()
        with self._get_connection() as conn:
            cur = conn.cursor()
            ph = "?" if self.engine == "sqlite" else "%s"
            cur.execute(f'SELECT MAX(timestamp) FROM data_points WHERE tag_id = {ph};', (tag_id,))
            row = cur.fetchone()
            max_val = row[0] if (row and row[0]) else None

            if not max_val:
                now = datetime.now()
                cur.close()
                return [], (now - delta).timestamp(), now.timestamp()

            if isinstance(max_val, str):
                max_dt = datetime.fromisoformat(max_val)
            else:
                max_dt = max_val

            start_dt = max_dt - delta
            p1 = self._dt_str(start_dt) if self.engine == "sqlite" else start_dt
            p2 = self._dt_str(max_dt) if self.engine == "sqlite" else max_dt

            cur.execute(f"""
                SELECT timestamp, value FROM data_points 
                WHERE tag_id = {ph} AND timestamp >= {ph} AND timestamp <= {ph}
                ORDER BY timestamp ASC;
            """, (tag_id, p1, p2))
            rows = cur.fetchall()
            cur.close()

            points = []
            for r in rows:
                t = r[0] if not isinstance(r, dict) else r["timestamp"]
                v = r[1] if not isinstance(r, dict) else r["value"]
                dt = datetime.fromisoformat(t) if isinstance(t, str) else t
                points.append(DataPoint(timestamp=dt, value=float(v)))
            return points, start_dt.timestamp(), max_dt.timestamp()

    def get_data_points_range(self, tag_id: int, start_dt: datetime, end_dt: datetime) -> List[DataPoint]:
        if not self.is_available:
            return []
        with self._get_connection() as conn:
            cur = conn.cursor()
            ph = "?" if self.engine == "sqlite" else "%s"
            p1 = self._dt_str(start_dt) if self.engine == "sqlite" else start_dt
            p2 = self._dt_str(end_dt) if self.engine == "sqlite" else end_dt
            cur.execute(f"""
                SELECT timestamp, value FROM data_points 
                WHERE tag_id = {ph} AND timestamp >= {ph} AND timestamp <= {ph}
                ORDER BY timestamp ASC;
            """, (tag_id, p1, p2))
            rows = cur.fetchall()
            cur.close()
            points = []
            for r in rows:
                t = r[0] if not isinstance(r, dict) else r["timestamp"]
                v = r[1] if not isinstance(r, dict) else r["value"]
                dt = datetime.fromisoformat(t) if isinstance(t, str) else t
                points.append(DataPoint(timestamp=dt, value=float(v)))
            return points
