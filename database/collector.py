"""Буферизованная запись точек данных в фоне.

Драйверы опроса складывают значения в очередь, а отдельный поток писателя
пачками пишет их в базу. Такое разделение нужно, чтобы запись не блокировала
цикл опроса: время ожидания БД перестает влиять на периодичность опроса,
а кратковременный пропадание базы не роняет опрос.
"""
import queue
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple


class Collector:
    """Очередь записей и фоновый поток, который пишет их в базу."""

    def __init__(self, db, flush_interval: float = 1.0, batch_limit: int = 2000,
                 max_queue: int = 200000, retention_days: float = 0.0,
                 on_status=None):
        self.db = db
        self.flush_interval = max(0.05, float(flush_interval))
        self.batch_limit = max(1, int(batch_limit))
        self.retention_days = max(0.0, float(retention_days))

        self._queue: "queue.Queue[Tuple[datetime, int, float, int]]" = queue.Queue(maxsize=int(max_queue))
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._lock = threading.Lock()
        self.written = 0
        self.batches = 0
        self.dropped = 0
        self.errors = 0
        self.last_error = ""
        self.last_flush: Optional[datetime] = None
        self.last_purge: Optional[datetime] = None
        self._on_status = on_status

        # Для psycopg2 один запрос на пачку вместо запроса на строку.
        # Путь выбирается при каждой записи по текущему движку БД, т.к.
        # сервис могут менять (переподключение между sqlite и postgres).
        self._execute_values = None
        self._ev_checked = False

    def _get_execute_values(self):
        if getattr(self.db, "engine", "") != "postgres":
            return None
        if not self._ev_checked:
            self._ev_checked = True
            try:
                from psycopg2.extras import execute_values  # type: ignore
                self._execute_values = execute_values
            except Exception:
                self._execute_values = None
        return self._execute_values
        if getattr(self.db, "engine", "") != "postgres":
            return None
        try:
            from psycopg2.extras import execute_values  # type: ignore
            self._execute_values = execute_values
        except Exception:
            self._execute_values = None
        return self._execute_values

    # ------------------------------------------------------------------ life
    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="data-writer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0):
        """Останавливает писателя, предварительно слив накопленное в базу."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def queued(self) -> int:
        return self._queue.qsize()

    # ------------------------------------------------------------------ api
    def submit(self, timestamp: datetime, tag_id: int, value: float, quality: int = 0) -> bool:
        """Ставит одно значение в очередь. Не блокируется и не бросает исключений."""
        try:
            self._queue.put_nowait((timestamp, int(tag_id), float(value), int(quality)))
            return True
        except queue.Full:
            with self._lock:
                self.dropped += 1
            return False

    def submit_many(self, records: Iterable[Tuple[datetime, int, float, int]]) -> int:
        accepted = 0
        for record in records:
            if len(record) == 3:
                ts, tag_id, value = record
                quality = 0
            else:
                ts, tag_id, value, quality = record
            if self.submit(ts, tag_id, value, quality):
                accepted += 1
        return accepted

    def flush_now(self):
        """Требует от писателя слить очередь немедленно."""
        self._wake.set()

    def flush_blocking(self, timeout: float = 10.0) -> int:
        """Сливает очередь и ждёт завершения. Возвращает число записанных точек."""
        if not self.running:
            return self._write(self._drain(self.batch_limit * 20))
        before = self.written
        self.flush_now()
        deadline = time.monotonic() + timeout
        while self._queue.qsize() and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.written - before

    def set_flush_interval(self, seconds: float):
        self.flush_interval = max(0.05, float(seconds))
        self._wake.set()

    def set_retention_days(self, days: float):
        self.retention_days = max(0.0, float(days))
        self._wake.set()

    def stats(self) -> Dict:
        return {
            "queued": self._queue.qsize(),
            "written": self.written,
            "batches": self.batches,
            "dropped": self.dropped,
            "errors": self.errors,
            "last_error": self.last_error,
            "last_flush": self.last_flush,
            "flush_interval": self.flush_interval,
            "retention_days": self.retention_days,
        }

    # ------------------------------------------------------------------ work
    def _run(self):
        purge_period = 300.0
        next_purge = time.monotonic() + purge_period if self.retention_days > 0 else None

        while not self._stop.is_set():
            timeout = self.flush_interval if self._queue.qsize() == 0 else 0.0
            self._wake.wait(timeout)
            self._wake.clear()

            batch = self._drain(self.batch_limit)
            if batch:
                self._write(batch)

            if next_purge is not None and time.monotonic() >= next_purge:
                next_purge = time.monotonic() + purge_period
                self.purge_expired()

        # При остановке терять накопленное нельзя: сливаем всё до последней точки.
        while True:
            batch = self._drain(self.batch_limit * 20)
            if not batch:
                break
            self._write(batch)

    def _drain(self, limit: int) -> List[Tuple[datetime, int, float, int]]:
        batch = []
        get_nowait = self._queue.get_nowait
        while len(batch) < limit:
            try:
                batch.append(get_nowait())
            except queue.Empty:
                break
        return batch

    def _insert_sql(self, with_quality: bool) -> str:
        marks = ", ".join(["%s"] * (4 if with_quality else 3))
        if self.db.engine == "sqlite":
            cols = "timestamp, tag_id, value" + (", quality" if with_quality else "")
            marks = ", ".join(["?"] * (4 if with_quality else 3))
            return f"INSERT INTO data_points ({cols}) VALUES ({marks});"
        cols = '"timestamp", tag_id, value' + (", quality" if with_quality else "")
        return f"INSERT INTO data_points ({cols}) VALUES ({marks});"

    def _write(self, batch) -> int:
        if not batch or not self.db.is_available:
            return 0

        with_quality = bool(getattr(self.db, "has_quality", False))
        rows = [tuple(r) for r in (batch if with_quality else [r[:3] for r in batch])]
        placeholders = 4 if with_quality else 3
        sql = self._insert_sql(with_quality)

        try:
            execute_values = self._get_execute_values()
            with self.db._get_connection() as conn:
                cur = conn.cursor()
                if execute_values is not None:
                    # Один запрос на всю пачку вместо запроса на строку.
                    marks = ", ".join(["%s"] * placeholders)
                    execute_values(
                        cur,
                        sql.replace(f"VALUES ({marks})", "VALUES %s"),
                        rows,
                        template=f"({marks})",
                        page_size=self.batch_limit,
                    )
                else:
                    cur.executemany(sql, rows)
                conn.commit()
                cur.close()
        except Exception as exc:
            with self._lock:
                self.errors += 1
                self.last_error = str(exc)[:200]
            return 0

        with self._lock:
            self.written += len(batch)
            self.batches += 1
            self.last_flush = datetime.now()
        self._publish()
        return len(batch)

    def purge_expired(self) -> int:
        """Удаляет точки старше срока хранения. 0 = хранить вечно."""
        if self.retention_days <= 0 or not self.db.is_available:
            return 0
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        try:
            with self.db._get_connection() as conn:
                cur = conn.cursor()
                if self.db.engine == "sqlite":
                    # Разделитель ' ' вместо 'T': строки пишутся адаптером
                    # sqlite3 с пробелом, смешанное сравнение некорректно
                    cur.execute("DELETE FROM data_points WHERE timestamp < ?;",
                                (cutoff.isoformat(sep=" "),))
                else:
                    cur.execute('DELETE FROM data_points WHERE "timestamp" < %s;', (cutoff,))
                removed = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                conn.commit()
                cur.close()
        except Exception as exc:
            with self._lock:
                self.errors += 1
                self.last_error = str(exc)[:200]
            return 0

        with self._lock:
            self.last_purge = datetime.now()
        if removed:
            self._publish()
        return removed

    def _publish(self):
        if self._on_status is None:
            return
        try:
            self._on_status(self.stats())
        except Exception:
            pass
