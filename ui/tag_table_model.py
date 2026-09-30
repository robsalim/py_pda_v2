"""Модель таблицы тегов поверх живого реестра (QAbstractTableModel).

QTableWidget хранит каждую ячейку как объект — при 10 000 тегов это
сотни тысяч QTableWidgetItem и полное перерисовывание таблицы. Модель
хранит только список тегов и словарь живых значений, а view тянет данные
лишь для видимых строк. Обновление значения — это один dataChanged по
двум колонкам конкретной строки.
"""
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import (
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt,
)
from PyQt6.QtGui import QColor

from ui import theme

COL_ID, COL_NAME, COL_GROUP, COL_ADDR, COL_TYPE, COL_SCALE, COL_OFFSET, \
    COL_UNIT, COL_VALUE, COL_TIME = range(10)

HEADERS = ["ID", "Name", "Группа", "Address", "Type", "Scale", "Offset",
           "Unit", "Онлайн Значение", "Время обновления"]

# Как долго тег считается «изменившимся recently» для фильтра
RECENT_WINDOW_S = 2.5

_QUALITY_BAD = 1


class TagTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: List = []                       # Tag или TagPoint
        self._row_of: Dict[int, int] = {}          # tag_id -> номер строки
        self._live: Dict[int, Tuple[Optional[datetime], Optional[float], int]] = {}
        self._recent: Dict[int, float] = {}        # tag_id -> время изменения
        self.favorites: set = set()

    # ------------------------------------------------------------------ rows
    @staticmethod
    def _norm_live(live: Dict) -> Dict:
        """Приводит (ts, val) из БД или (ts, val, quality) из реестра к 3-кортежу."""
        out = {}
        for tid, entry in live.items():
            if entry is None:
                continue
            if len(entry) == 3:
                out[tid] = tuple(entry)
            else:
                ts, val = entry
                out[tid] = (ts, val, 0)
        return out

    def set_rows(self, rows: List, live: Optional[Dict] = None):
        """Полная перезагрузка строк (выбрана другая группа/модуль)."""
        self.beginResetModel()
        self.rows = list(rows)
        self._row_of = {t.id: i for i, t in enumerate(self.rows)}
        if live is not None:
            self._live = self._norm_live(live)
        self._recent.clear()
        self.endResetModel()

    def row_at(self, source_row: int):
        if 0 <= source_row < len(self.rows):
            return self.rows[source_row]
        return None

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return HEADERS[section]
        return None

    # ------------------------------------------------------------------ data
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        if r >= len(self.rows):
            return None
        t = self.rows[r]
        live = self._live.get(t.id)
        ts, val, qual = (None, None, 0) if live is None else live

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole,
                    Qt.ItemDataRole.UserRole):
            raw = self._raw(t, c, ts, val, qual)
            if role != Qt.ItemDataRole.DisplayRole:
                return raw
            return self._format(raw, c)

        if role == Qt.ItemDataRole.ForegroundRole and c == COL_VALUE:
            p = theme.current()
            if val is None or qual == _QUALITY_BAD:
                return QColor(p.error)
            return QColor(p.value_good)
        if role == Qt.ItemDataRole.BackgroundRole and t.id in self.favorites:
            return QColor(theme.current().favorite_bg)
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{t.name} [{t.address_str}] {t.unit or ''}".strip()
        return None

    @staticmethod
    def _raw(t, c, ts, val, qual):
        if c == COL_ID:
            return t.id
        if c == COL_NAME:
            return t.name
        if c == COL_GROUP:
            return getattr(t, "group_name", "") or "Общие"
        if c == COL_ADDR:
            return t.address_str
        if c == COL_TYPE:
            return t.data_type
        if c == COL_SCALE:
            return t.scale
        if c == COL_OFFSET:
            return t.offset_val
        if c == COL_UNIT:
            return t.unit or ""
        if c == COL_VALUE:
            return val
        if c == COL_TIME:
            return ts
        return None

    @staticmethod
    def _format(raw, c):
        if raw is None:
            return "—" if c in (COL_VALUE, COL_TIME) else ""
        if c == COL_VALUE:
            return f"{raw:.2f}"
        if c == COL_TIME and isinstance(raw, datetime):
            return raw.strftime("%H:%M:%S.%f")[:-3]
        if c in (COL_SCALE, COL_OFFSET):
            return f"{raw:g}"
        return str(raw)

    # --------------------------------------------------------------- updates
    def apply_updates(self, entries: Dict[int, Tuple]):
        """
        Точечное обновление колонок «значение/время».
        dataChanged уходит только по грязным строкам; view запросит данные
        лишь для тех из них, что сейчас видны на экране.
        """
        if not entries:
            return
        touched = []
        now = time.monotonic()
        for tid, entry in entries.items():
            if len(entry) == 2:
                ts, val = entry
                qual = 0
            else:
                ts, val, qual = entry
            old = self._live.get(tid)
            if old == (ts, val, qual):
                continue
            self._live[tid] = (ts, val, qual)
            self._recent[tid] = now
            row = self._row_of.get(tid)
            if row is not None:
                touched.append(row)
        if touched:
            # По одному dataChanged на строку: view перерисует только те
            # из них, что попадают в видимый участок.viewport'а, и не будет
            # обходить весь прямоугольник min..max (иначе при редких
            # изменениях в начале и конце таблицы обновлялись бы все 10 000).
            for row in touched:
                left = self.index(row, COL_VALUE)
                right = self.index(row, COL_TIME)
                self.dataChanged.emit(left, right)

    def apply_time_refresh(self, entries: Dict[int, Tuple]):
        """
        Освежает (ts, val, quality) только для видимых строк при каждом опросе.
        В отличие от apply_updates() НЕ помечает тег «изменившимся» (_recent):
        сигнал мог остаться прежним, но время последнего чтения должно тикать.
        """
        if not entries:
            return
        touched = []
        for tid, entry in entries.items():
            old = self._live.get(tid)
            if old == entry:
                continue
            self._live[tid] = entry
            row = self._row_of.get(tid)
            if row is not None:
                touched.append(row)
        for row in touched:
            self.dataChanged.emit(self.index(row, COL_VALUE), self.index(row, COL_TIME))

    def prune_recent(self) -> bool:
        """Убирает теги из «недавно изменившихся» по истечении окна."""
        cutoff = time.monotonic() - RECENT_WINDOW_S
        stale = [tid for tid, tm in self._recent.items() if tm < cutoff]
        for tid in stale:
            del self._recent[tid]
        return bool(stale)

    @property
    def recent(self) -> Dict[int, float]:
        """tag_id -> monotonic time последнего изменения (для фильтра)."""
        return self._recent

    def toggle_favorite(self, tag_id: int):
        if tag_id in self.favorites:
            self.favorites.discard(tag_id)
        else:
            self.favorites.add(tag_id)
        row = self._row_of.get(tag_id)
        if row is not None:
            self.dataChanged.emit(self.index(row, 0), self.index(row, COL_TIME))

    def live_of(self, tag_id: int):
        return self._live.get(tag_id)


class TagFilterModel(QSortFilterProxyModel):
    """Поиск по имени/адресу/группе + режимы «изменившиеся» / «избранные»."""

    MODE_ALL, MODE_CHANGED, MODE_FAV = "all", "changed", "fav"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pattern = ""
        self.mode = self.MODE_ALL

    def set_search(self, text: str):
        self._pattern = (text or "").strip().casefold()
        self.invalidateFilter()

    def set_mode(self, mode: str):
        self.mode = mode
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None:
            return True
        t = model.row_at(source_row)
        if t is None:
            return False

        if self.mode == self.MODE_CHANGED and t.id not in model.recent:
            return False
        if self.mode == self.MODE_FAV and t.id not in model.favorites:
            return False

        if self._pattern:
            hay = " ".join((
                str(t.name), str(t.address_str),
                str(getattr(t, "group_name", "") or ""),
                str(t.data_type), str(t.unit or ""),
            )).casefold()
            if self._pattern not in hay:
                return False
        return True
