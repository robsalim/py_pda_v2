"""Единый реестр тегов в оперативной памяти.

Владелец — DriverManager. Драйверы опроса пишут сюда показаний через
update_value(), GUI и веб-сервер читают текущие состояния без обращения к
истории в БД. Журнал (core/historian.py) выбирает из реестра точки,
подлежащие регистрации.
"""
import threading
import time
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set, Tuple

from core.tag_point import QUALITY_BAD, QUALITY_GOOD, TagPoint
from models.tag import Tag


class TagRegistry:
    # как часто полный проход ради «heartbeat»-тегов (log_interval_ms > 0).
    # Значения меняющихся тегов собираются через _pending_ids, поэтому
    # дорогой проход по всем точкам нужен редко.
    HEARTBEAT_PERIOD = 0.25

    def __init__(self):
        self._points: Dict[int, TagPoint] = {}
        self._by_conn: Dict[int, Set[int]] = {}
        self._lock = threading.Lock()
        # Теги, изменившиеся с момента последней выборки (для GUI)
        self._dirty: Set[int] = set()
        self._dirty_at: float = 0.0
        # Теги с незаписанными переходами (значения лежат в буфере TagPoint)
        self._pending_ids: Set[int] = set()
        self._next_heartbeat: float = 0.0

    # ------------------------------------------------------------------ build
    def replace_all(self, tags: Iterable[Tag]):
        """Полная перезагрузка реестра из БД (при старте и reload конфигурации)."""
        points = {}
        by_conn: Dict[int, Set[int]] = {}
        with self._lock:
            old = self._points
            for t in tags:
                point = old.get(t.id)
                if point is None:
                    point = TagPoint(t)
                else:
                    # Сохраняем живое значение, обновляем конфигурацию
                    point.name = t.name
                    point.group_name = t.group_name or "Общие"
                    point.address_str = t.address_str
                    point.data_type = t.data_type
                    point.unit = t.unit or ""
                    point.scale = t.scale
                    point.offset_val = t.offset_val
                    point.log_interval_ms = int(getattr(t, "log_interval_ms", 1000) or 0)
                    point.deadband = float(getattr(t, "deadband", 0.0) or 0.0)
                points[t.id] = point
                by_conn.setdefault(t.connection_id, set()).add(t.id)
            self._points = points
            self._by_conn = by_conn

    def forget_connection(self, conn_id: int):
        with self._lock:
            for tid in self._by_conn.pop(conn_id, set()):
                self._points.pop(tid, None)

    def drop(self, tag_id: int):
        """Удаляет точку из реестра (тег удалён из конфигурации)."""
        with self._lock:
            self._dirty.discard(tag_id)
            self._points.pop(tag_id, None)
            for ids in self._by_conn.values():
                ids.discard(tag_id)

    # -------------------------------------------------------------------- get
    def get(self, tag_id: int) -> Optional[TagPoint]:
        return self._points.get(tag_id)

    def points_for(self, conn_id: int) -> List[TagPoint]:
        with self._lock:
            return [self._points[i] for i in self._by_conn.get(conn_id, ()) if i in self._points]

    def all_points(self) -> List[TagPoint]:
        with self._lock:
            return list(self._points.values())

    def __len__(self) -> int:
        return len(self._points)

    # ----------------------------------------------------------------- update
    def update_value(self, tag_id: int, raw_value: Optional[float],
                     quality: int = QUALITY_GOOD, ts: Optional[datetime] = None):
        """Обновляет показание тега; масштабирование применяется по его scale/offset."""
        point = self._points.get(tag_id)
        if point is None:
            return
        value = None if raw_value is None else float(raw_value) * point.scale + point.offset_val
        changed = point.update(raw_value, value, quality, ts or datetime.now())
        if changed and value is not None:
            # переход записан в буфер TagPoint — журнал заберёт его на следующем
            # скане даже если значение «вернётся» к прежнему до выборки
            with self._lock:
                self._pending_ids.add(tag_id)
                self._dirty.add(tag_id)
                self._dirty_at = time.monotonic()
        elif changed:
            with self._lock:
                self._dirty.add(tag_id)
                self._dirty_at = time.monotonic()

    def mark_bad(self, tag_ids: Iterable[int], ts: Optional[datetime] = None):
        """Сигналы, не прочитанные в этом цикле, помечаются плохим качеством."""
        now = ts or datetime.now()
        for tid in tag_ids:
            point = self._points.get(tid)
            if point is not None and point.quality != QUALITY_BAD:
                point.quality = QUALITY_BAD
                point.version += 1
                with self._lock:
                    self._dirty.add(tid)

    # ------------------------------------------------------------------ dirty
    def take_dirty(self) -> Dict[int, TagPoint]:
        """
        Забирает накопившиеся изменения (GUI вызывает не чаще 20 Гц).
        Возвращает копии точек, чтобы читатель не зависел от того,
        перестроит ли в этот момент реестр драйвер или конфигурация.
        """
        with self._lock:
            ids, self._dirty = self._dirty, set()
            return {i: self._points[i] for i in ids if i in self._points}

    def live_snapshot(self) -> Dict[int, Tuple[Optional[datetime], Optional[float], int]]:
        """
        Текущие значения всех сигналов: {tag_id: (время, значение, качество)}.
        Заменяет собой запрос последних значений из истории — GUI больше не
        зависит от скорости записи в БД.
        """
        with self._lock:
            points = list(self._points.values())
        return {p.tag_id: (p.timestamp, p.value, p.quality) for p in points}

    def read_values(self, tag_ids: Iterable[int]) -> Dict[int, Tuple[Optional[datetime], Optional[float], int]]:
        """
        Текущие значения только запрошенных тегов: {tag_id: (время, значение, качество)}.
        Дешевле live_snapshot() при тысячах тегов — используется GUI, чтобы
        обновить «время обновления» лишь для видимых строк таблицы.
        """
        out = {}
        get = self._points.get
        for tid in tag_ids:
            p = get(tid)
            if p is not None:
                out[tid] = (p.timestamp, p.value, p.quality)
        return out

    # ----------------------------------------------------------------- journal
    def collect_due(self, now: datetime) -> List[Tuple[datetime, int, float, int]]:
        """
        Точки, подлежащие регистрации (COV + собственный интервал + мёртвая зона).
        Двойные переходы между сканами (100->101->100) не теряются: журнал
        выбирается медленнее опроса, поэтому для изменившихся со прошлого
        скана точек возвращаются все промежуточные значения из Ring-буфера.
        """
        with self._lock:
            points = list(self._points.values())
        out = []
        for point in points:
            samples = point.drain_samples()
            if not samples and point.due_for_log(now):
                # первая точка / смена качества / heartbeat: отдаём текущее значение
                samples = [(point.timestamp or now, float(point.value), point.quality)]
            if samples:
                out.extend((ts, point.tag_id, value, quality) for ts, value, quality in samples)
                point.mark_logged(now, samples[-1])
            elif point._last_logged_ts is None and point.value is not None:
                point.mark_logged(now)
        return out
