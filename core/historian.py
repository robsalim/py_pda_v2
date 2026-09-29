"""Асинхронный журнал: реестр тегов -> очередь -> фоновая запись в БД.

Регистрация точек отделена от опроса:

    поток опроса (драйвер)   только обновляет TagPoint в реестре;
    поток журнала (Historian) раз в scan_interval выбирает подлежащие записи
                            (COV + мёртвая зона + log_interval_ms) и ставит
                            их в ограниченную очередь;
    поток писателя (Collector) пачками пишет очередь в БД.

Если БД недоступна или отстаёт, точки теряются только при переполнении
очереди — это фиксируется счётчиком dropped. Опрос при этом не блокируется.
"""
import threading
import time
from datetime import datetime
from typing import Optional

from core.tag_registry import TagRegistry
from database.collector import Collector


class Historian:
    def __init__(self, registry: TagRegistry, db_service,
                 scan_interval_ms: int = 200,
                 flush_interval: float = 1.0,
                 batch_limit: int = 2000,
                 max_queue: int = 200000,
                 retention_days: float = 0.0,
                 on_status=None):
        self.registry = registry
        self.collector = Collector(
            db_service,
            flush_interval=flush_interval,
            batch_limit=batch_limit,
            max_queue=max_queue,
            retention_days=retention_days,
            on_status=on_status,
        )
        self.scan_interval = max(0.02, scan_interval_ms / 1000.0)

        self._stop = threading.Event()
        self._scan_thread: Optional[threading.Thread] = None
        self._points_selected = 0

    # ------------------------------------------------------------------ life
    def start(self):
        if self._scan_thread is not None:
            return
        self.collector.start()
        self._stop.clear()
        self._scan_thread = threading.Thread(target=self._scan_loop,
                                             name="journal-scanner", daemon=True)
        self._scan_thread.start()

    def stop(self, timeout: float = 10.0):
        self._stop.set()
        if self._scan_thread is not None:
            self._scan_thread.join(timeout=timeout)
            self._scan_thread = None
        # Писатель сам сольёт остаток очереди при остановке
        self.collector.stop(timeout=timeout)

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        s = self.collector.stats()
        s["selected"] = self._points_selected
        return s

    def set_flush_interval_ms(self, ms: int):
        self.collector.set_flush_interval(max(50, int(ms)) / 1000.0)

    # -------------------------------------------------------------------- run
    def _scan_loop(self):
        next_log = 0.0
        while not self._stop.is_set():
            started = time.perf_counter()
            now = datetime.now()
            due = self.registry.collect_due(now)
            if due:
                self._points_selected += len(due)
                self.collector.submit_many(due)

            # Периодически тряхнём очередь, чтобы данные уходили в БД вовремя
            if started >= next_log:
                next_log = started + self.collector.flush_interval
                self.collector.flush_now()

            elapsed = time.perf_counter() - started
            self._stop.wait(max(0.0, self.scan_interval - elapsed))
