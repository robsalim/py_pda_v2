import time
from typing import Dict, Optional
from config import JOURNAL_CONFIG
from core.historian import Historian
from core.tag_registry import TagRegistry
from database.db_service import DatabaseService
from drivers.base_driver import BaseDriver
from drivers.registry import get_driver_class


class DriverManager:
    """
    Владелец runtime-состояния тегов.

    registry  — единый реестр tag_id -> TagPoint, куда драйверы пишут показания;
    historian — фоновый журнал: выборка подлежащих точек + пакетная запись в БД.

    UI и веб-сервер читают текущие значения из registry, не трогая историю.
    """

    def __init__(self, db_service: DatabaseService, cfg: Optional[Dict] = None):
        self.db = db_service
        self.drivers: Dict[int, BaseDriver] = {}
        self.registry = TagRegistry()

        merged = dict(JOURNAL_CONFIG)
        if cfg:
            merged.update(cfg)
        cfg = merged
        self.historian = Historian(
            self.registry,
            db_service,
            scan_interval_ms=int(cfg.get("journal_scan_ms", 200)),
            flush_interval=float(cfg.get("journal_flush_ms", 1000)) / 1000.0,
            batch_limit=int(cfg.get("journal_batch", 2000)),
            max_queue=int(cfg.get("journal_queue", 200000)),
            retention_days=float(cfg.get("retention_days", 0) or 0),
        )

    # ---------------------------------------------------------------- tags
    def reload_tags(self):
        """Обновляет реестр тегов из БД (старт, изменение конфигурации)."""
        try:
            self.registry.replace_all(self.db.get_all_tags())
        except Exception as exc:
            print(f"[Drivers] Не удалось загрузить теги в реестр: {exc}")

    # -------------------------------------------------------------- drivers
    def start_all(self):
        try:
            conns = self.db.get_all_connections()
        except Exception as exc:
            print(f"[Drivers] Не удалось загрузить подключения: {exc}")
            return

        self.reload_tags()
        for c in conns:
            if not c.enabled:
                continue
            if c.id in self.drivers:
                continue

            driver_class = get_driver_class(c.driver_type)
            if driver_class is None:
                print(f"[Drivers] Неизвестный тип драйвера '{c.driver_type}' "
                      f"у подключения '{c.name}' (id={c.id}), пропущено")
                continue

            driver = driver_class(c, self.db, registry=self.registry)
            driver.start()
            self.drivers[c.id] = driver

        self.historian.start()

    def stop_all(self):
        for c_id, d in list(self.drivers.items()):
            try:
                d.stop()
            except Exception:
                pass
        self.drivers.clear()
        # Останавливать журнал после драйверов: накопленные точки сольются в БД
        try:
            self.historian.stop()
        except Exception:
            pass

    def restart_all(self):
        self.stop_all()
        time.sleep(0.3)
        self.start_all()

    # ------------------------------------------------------------------ misc
    def set_db(self, db_service: DatabaseService):
        """Смена сервиса БД (переподключение): журнал и реестр должны писать в новую базу."""
        self.db = db_service
        self.historian.collector.db = db_service

    def journal_stats(self) -> dict:
        return self.historian.stats()

    def driver_status(self, conn_id: int) -> str:
        d = self.drivers.get(conn_id)
        return d.status if d else "Stopped"
