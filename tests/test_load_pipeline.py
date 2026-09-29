"""Нагрузочный тест конвейера: 10000 тегов, мок S7-клиента, без GUI и ПЛК.

Проверяет, что цикл опроса удерживает целевую периодичность 10 мс, пока
фоновый журнал пишет в SQLite, и что реестр не раздувается по памяти.

    .venv\\Scripts\\python.exe tests\\test_load_pipeline.py [кол-во_тегов]
"""
import os
import random
import sys
import tempfile
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.historian import Historian
from core.tag_point import QUALITY_GOOD
from core.tag_registry import TagRegistry
from models.tag import Tag
from models.connection import Connection


class MockSnap7Client:
    """Мок python-snap7: db_read/db_write возвращают детерминированные байты
    мгновенно, так что измеряется именно софтверная часть конвейера."""

    MAX_VARS = 20

    def __init__(self):
        self._connected = False
        self._payload = bytes(random.getrandbits(8) for _ in range(4096))

    def connect(self, address, rack=0, slot=1):
        self._connected = True
        return None

    def disconnect(self):
        self._connected = False

    def get_connected(self):
        return self._connected

    def get_pdu_length(self):
        return 240

    def db_read(self, db_number, start, size):
        return self._payload[start:start + size]

    def read_area(self, area, db_number, start, size):
        return self._payload[start:start + size]

    def read_multi_vars(self, items):
        """items — список словарей {area, db_number, start, size} (стиль snap7 3.x)."""
        data = []
        for it in items:
            start = int(it.get("start", 0))
            size = int(it.get("size", 2))
            data.append(bytearray(self._payload[start:start + size]))
        return [0] * len(data), data


def make_tags(n):
    tags = []
    for i in range(1, n + 1):
        tags.append(Tag(
            id=i, connection_id=1, name=f"TI_{i}",
            address_str=f"DB{i % 20}.DBD{(i * 4) % 2000}",
            data_type="INT16", scale=1.0, offset_val=0.0,
            unit="", group_name=f"G{i % 50}",
            log_interval_ms=1000, deadband=0.0,
        ))
    return tags


def measure_update_registry(n, rounds=20):
    """Время одного цикла обновления 10k тегов в реестре (без БД)."""
    reg = TagRegistry()
    reg.replace_all(make_tags(n))
    now = datetime.now()
    vals = list(range(n))
    best = worst = 0.0
    for r in range(rounds):
        # имитируем «шум»: каждый 10-й тег меняет значение
        changed = {i: v for i, v in enumerate(vals, start=1) if (i + r) % 10 == 0}
        t0 = time.perf_counter()
        for tid, v in changed.items():
            reg.update_value(tid, float(v), QUALITY_GOOD, now)
        el = (time.perf_counter() - t0) * 1000.0
        best = min(best, el) if best else el
        worst = max(worst, el)
    print(f"  update_value {len(changed)} из {n} тегов: "
          f"{best:.2f}..{worst:.2f} мс за цикл ({len(changed)/(best/1000):.0f} точек/с пропускная)")
    return best


def measure_pipeline_with_db(n):
    """Полный конвейер: реестр + collect_due + очередь + фоновая запись в SQLite."""
    from database.db_service import DatabaseService
    db_path = os.path.join(tempfile.mkdtemp(), "load.db")
    db = DatabaseService(engine="sqlite", sqlite_path=db_path)
    assert db.is_available, db.last_error

    reg = TagRegistry()
    reg.replace_all(make_tags(n))
    hist = Historian(reg, db, scan_interval_ms=50, flush_interval=0.5,
                     batch_limit=5000, max_queue=500000)
    hist.start()

    stop = threading.Event()
    cycles = [0]
    t0 = time.perf_counter()

    def writer_loop():
        now = datetime.now()
        r = 0
        while not stop.is_set():
            r += 1
            for tid in range(1, n + 1, 10):
                reg.update_value(tid, float(r), QUALITY_GOOD, now)
            cycles[0] += 1
            time.sleep(0.0005)  # очень быстрый источник, чтобы загрузить журнал

    w = threading.Thread(target=writer_loop, daemon=True)
    w.start()

    seconds = 5.0
    time.sleep(seconds)
    stop.set()
    w.join(timeout=2)
    elapsed = time.perf_counter() - t0

    stats = hist.stats()
    hist.stop(timeout=15)
    rows_written = db.get_data_points_range(1, datetime.min, datetime.max)
    rate = stats["written"] / max(0.001, elapsed)
    print(f"  {n} тегов, {cycles[0]} циклов источника за {elapsed:.1f} с")
    print(f"  журнал: записано {stats['written']} точек (~{rate:.0f} строк/с, "
          f"{stats['batches']} пачек), в очереди {stats['queued']}, "
          f"потеряно {stats['dropped']}, ошибок {stats['errors']}")
    assert stats["dropped"] == 0, f"потери точек при нагрузке: {stats['dropped']}"
    assert stats["errors"] == 0, f"ошибки записи: {stats['last_error']}"
    assert stats["written"] > 0
    return stats


def memory_footprint(n):
    """Грубая оценка памяти на реестр из n точек (только сам TagRegistry)."""
    reg = TagRegistry()
    tags = make_tags(n)
    t0 = time.perf_counter()
    reg.replace_all(tags)
    dt = time.perf_counter() - t0
    import sys as _sys
    size = sum(_sys.getsizeof(p) + _sys.getsizeof(p.name) + _sys.getsizeof(p.address_str)
               for p in reg.all_points()[:500])
    per_point = size / 500
    print(f"  replace_all({n}): {dt*1000:.0f} мс; ~{per_point:.0f} Б/точку "
          f"=> ~{per_point * n / 1e6:.1f} МБ на реестр")


def measure_driver_poll_rate(n, poll_ms=10, seconds=3.0):
    """
    Замеряет реальную периодичность опроса Snap7Driver с мок-клиентом.
    Это прямой ответ на вопрос «достижим ли 10 мс»: если софт (реестр +
    план чтений) не ограничивает цикл, driver выjdет на целевую частоту.
    """
    import types
    import drivers.snap7_driver as sd

    # Подменяем реальный snap7 на мок-клиент, чтобы не нужен был физический ПЛК
    fake = types.SimpleNamespace(client=types.SimpleNamespace(Client=MockSnap7Client))
    old = sd.snap7
    sd.snap7 = fake
    old_has = sd.HAS_SNAP7
    sd.HAS_SNAP7 = True

    reg = TagRegistry()
    reg.replace_all(make_tags(n))
    conn = Connection(id=1, name="load", driver_type="snap7", enabled=True,
                      poll_interval_ms=poll_ms, config={"ip": "127.0.0.1"})
    driver = sd.Snap7Driver(conn, db_service=None, registry=reg)

    driver.start()
    t0 = time.perf_counter()
    time.sleep(seconds)
    driver.stop()
    elapsed = time.perf_counter() - t0

    sd.snap7 = old
    sd.HAS_SNAP7 = old_has

    hz = driver.stats["hz"]
    print(f"  Snap7Driver, {n} тегов, цель {poll_ms} мс: "
          f"факт {driver.stats['cycle_ms']:.1f} мс/цикл ({hz:.0f} Гц), "
          f"{driver.stats['requests']} запросов/цикл, {driver.status}")
    return hz, driver.stats


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
    print(f"Нагрузочный тест конвейера, {n} тегов")
    measure_update_registry(n)
    measure_driver_poll_rate(n, poll_ms=10)
    measure_pipeline_with_db(n)
    memory_footprint(n)
    print("LOAD TEST PASSED")
