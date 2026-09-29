"""Интеграционный смоук-тест конвейера: драйвер -> TagRegistry -> Historian -> БД.

Запускается без ПЛК и без GUI:
    .venv\\Scripts\\python.exe tests\\test_pipeline.py
"""
import os
import sys
import tempfile
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.tag_point import QUALITY_GOOD, TagPoint
from core.tag_registry import TagRegistry
from core.historian import Historian
from models.tag import Tag


def make_db():
    from database.db_service import DatabaseService
    path = os.path.join(tempfile.mkdtemp(), "smoke.db")
    db = DatabaseService(engine="sqlite", sqlite_path=path)
    assert db.is_available, db.last_error
    return db


def mk_tag(tid, conn_id=1, name=None, log_ms=100, scale=1.0):
    return Tag(id=tid, connection_id=conn_id, name=name or f"tag{tid}",
               address_str=f"DB1.DBW{tid * 2}", data_type="INT16",
               scale=scale, offset_val=0.0, unit="", group_name="G",
               log_interval_ms=log_ms, deadband=0.0)


def test_registry_basics():
    reg = TagRegistry()
    reg.replace_all([mk_tag(1), mk_tag(2)])
    assert len(reg) == 2

    reg.update_value(1, 10.0, QUALITY_GOOD, datetime.now())
    p = reg.get(1)
    assert p.value == 10.0 and p.quality == QUALITY_GOOD

    # масштабирование
    reg.replace_all([mk_tag(3, scale=2.0)])
    reg.update_value(3, 5.0)
    assert reg.get(3).value == 10.0, reg.get(3).value

    # dirty для GUI
    dirty = reg.take_dirty()
    assert 3 in dirty and not reg.take_dirty()

    # live_snapshot
    snap = reg.live_snapshot()
    assert snap[3][1] == 10.0

    # reload сохраняет живое значение
    reg2 = TagRegistry()
    reg2.replace_all([mk_tag(3, scale=2.0)])
    reg2.update_value(3, 7.0)
    before = reg2.get(3).value
    reg2.replace_all([mk_tag(3, scale=2.0, name="renamed")])
    assert reg2.get(3).value == before and reg2.get(3).name == "renamed"

    # mark_bad
    reg2.mark_bad([3])
    assert reg2.get(3).quality == 1
    print("ok  registry basics")


def test_cov_and_interval():
    """COV пишет сразу, стабильное значение — только по heartbeat-интервалу."""
    reg = TagRegistry()
    reg.replace_all([mk_tag(1, log_ms=50)])
    now = datetime.now()
    reg.update_value(1, 1.0, QUALITY_GOOD, now)
    due = reg.collect_due(now)
    assert len(due) == 1, due                       # первое значение пишем всегда
    due2 = reg.collect_due(now)                    # не менялось, интервал не прошёл
    assert not due2
    reg.update_value(1, 100.0, QUALITY_GOOD, now)   # изменение -> пишем
    assert len(reg.collect_due(now)) == 1
    time.sleep(0.08)                               # heartbeat при стабильном значении
    assert len(reg.collect_due(datetime.now())) == 1
    print("ok  COV + heartbeat")


def test_full_pipeline():
    db = make_db()
    for tid in (1, 2, 3):
        db.add_tag(mk_tag(tid, log_ms=1000))

    reg = TagRegistry()
    reg.replace_all(db.get_all_tags())
    hist = Historian(reg, db, scan_interval_ms=20, flush_interval=0.1)
    hist.start()

    stop = threading.Event()

    def driver():
        v = 0
        while not stop.is_set():
            now = datetime.now()
            for tid in (1, 2, 3):
                reg.update_value(tid, float(v), QUALITY_GOOD, now)
            v += 1
            time.sleep(0.005)   # 200 Гц опроса

    th = threading.Thread(target=driver, daemon=True)
    th.start()
    time.sleep(1.0)
    stop.set()
    th.join(timeout=1)
    hist.stop()

    stats = hist.stats()
    assert stats["written"] > 0 and stats["dropped"] == 0 and stats["errors"] == 0, stats
    rows = db.get_data_points_range(1, datetime.min, datetime.max)
    assert len(rows) > 0
    # Журнал не должен писать 200 точек/с при log_interval_ms=1000 и неизменном... 
    # значение меняется каждый цикл, поэтому COV пишет часто — это ожидаемо.
    # Проверим обратное: стабильное значение пишется редко.
    reg2 = TagRegistry()
    db2 = make_db()
    db2.add_tag(mk_tag(9, log_ms=1000))
    reg2.replace_all(db2.get_all_tags())
    hist2 = Historian(reg2, db2, scan_interval_ms=20, flush_interval=0.1)
    hist2.start()
    reg2.update_value(9, 42.0, QUALITY_GOOD, datetime.now())
    time.sleep(1.2)
    s2 = hist2.stats()
    hist2.stop()
    assert s2["written"] <= 4, f"стабильный тег пишется слишком часто: {s2}"
    print("ok  full pipeline", stats)


def test_thread_pool_isolation():
    """Опрос не блокируется, даже если писатель занято (разные потоки)."""
    db = make_db()
    db.add_tag(mk_tag(1, log_ms=50))
    reg = TagRegistry()
    reg.replace_all(db.get_all_tags())
    hist = Historian(reg, db, scan_interval_ms=20, flush_interval=0.05)
    hist.start()
    cycles = 0
    t0 = time.perf_counter()
    now = datetime.now()
    for i in range(2000):
        reg.update_value(1, float(i), QUALITY_GOOD, now)
        cycles += 1
    elapsed = time.perf_counter() - t0
    hist.stop()
    hz = cycles / elapsed
    assert hz > 1000, f"обновление реестра слишком медленное: {hz:.0f}/с"
    print(f"ok  poll loop not blocked: {cycles} обновлений за {elapsed*1000:.0f} мс ({hz:.0f}/с)")


if __name__ == "__main__":
    test_registry_basics()
    test_cov_and_interval()
    test_full_pipeline()
    test_thread_pool_isolation()
    print("ALL PIPELINE TESTS PASSED")
