"""Объект-модель тега в оперативной памяти (runtime state).

TagPoint хранит ТЕКУЩЕЕ состояние сигнала: значение, качество, метку времени,
версию изменения. Это развязывает три независимых контура:

    опрос драйвером (сотни Гц)  ->  регистрация в журнал (свой интервал + COV)
                                ->  отображение в GUI (не чаще 20 Гц)

Без TagPoint каждому контуру приходилось бы читать историю из БД, что и
является главным ограничителем масштабируемости при тысячах тегов.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from models.tag import Tag

# Специальные значения качества
QUALITY_GOOD = 0
QUALITY_BAD = 1


@dataclass
class TagPoint:
    """Живое состояние одного сигнала."""

    __slots__ = (
        "tag_id", "connection_id", "name", "group_name", "address_str",
        "data_type", "unit", "scale", "offset_val",
        "log_interval_ms", "deadband",
        "value", "raw_value", "quality", "timestamp", "version",
        "_last_logged_value", "_last_logged_quality", "_last_logged_ts",
    )

    def __init__(self, tag: Tag):
        self.tag_id = tag.id
        self.connection_id = tag.connection_id
        self.name = tag.name
        self.group_name = tag.group_name or "Общие"
        self.address_str = tag.address_str
        self.data_type = tag.data_type
        self.unit = tag.unit or ""
        self.scale = tag.scale
        self.offset_val = tag.offset_val
        # Собственная частота регистрации: 0 = только по изменению (COV)
        self.log_interval_ms = int(getattr(tag, "log_interval_ms", 1000) or 0)
        # Мёртвая зона: изменение меньше deadband не считается изменением
        self.deadband = float(getattr(tag, "deadband", 0.0) or 0.0)

        # Текущее состояние (нет данных = None)
        self.value: Optional[float] = None
        self.raw_value: Optional[float] = None
        self.quality: int = QUALITY_BAD
        self.timestamp: Optional[datetime] = None
        # Растёт при каждом реальном изменении значения/качества
        self.version: int = 0

        # Состояние регистрации в журнале
        self._last_logged_value: Optional[float] = None
        self._last_logged_quality: Optional[int] = None
        self._last_logged_ts: Optional[datetime] = None

    # Драйверы исторически обращаются к тегу как к Tag (с атрибутом .id),
    # поэтому проксируем id -> tag_id, чтобы TagPoint был совместим с Tag.
    @property
    def id(self) -> int:
        return self.tag_id

    # ------------------------------------------------------------------ update
    def update(self, raw_value: Optional[float], value: Optional[float],
               quality: int, ts: datetime) -> bool:
        """Записывает свежее показание. Возвращает True, если значение изменилось."""
        changed = False
        if quality != self.quality:
            self.quality = quality
            changed = True
        if value is None:
            # Сигнал пропал — фиксируем качество, значение оставляем прежним
            return changed
        if self.value is None:
            changed = True
        else:
            delta = abs(float(value) - float(self.value))
            if quality != QUALITY_GOOD or (delta > 0.0 and delta > self.deadband):
                changed = True
        self.raw_value = raw_value
        self.value = value
        self.timestamp = ts
        if changed:
            self.version += 1
        return changed

    # -------------------------------------------------------------------- log
    def due_for_log(self, now: datetime) -> bool:
        """Нужно ли зарегистрировать точку сейчас (COV + собственный интервал)."""
        if self.value is None or self.quality is None:
            return False
        if self._last_logged_ts is None:
            return True
        if self.quality != self._last_logged_quality:
            return True
        # Изменение за мёртвую зону — пишем сразу, не дожидаясь интервала
        if (self._last_logged_value is None
                or abs(float(self.value) - float(self._last_logged_value)) > self.deadband):
            return True
        # Периодическая регистрация («heartbeat»), если задан интервал
        if self.log_interval_ms > 0:
            elapsed = (now - self._last_logged_ts).total_seconds() * 1000.0
            if elapsed >= self.log_interval_ms:
                return True
        return False

    def mark_logged(self, now: datetime):
        self._last_logged_value = self.value
        self._last_logged_quality = self.quality
        self._last_logged_ts = now

    # ---------------------------------------------------------------- snapshot
    def as_dict(self) -> dict:
        return {
            "tag_id": self.tag_id,
            "connection_id": self.connection_id,
            "name": self.name,
            "group_name": self.group_name,
            "address": self.address_str,
            "data_type": self.data_type,
            "unit": self.unit,
            "value": self.value,
            "raw": self.raw_value,
            "quality": self.quality,
            "timestamp": self.timestamp,
            "version": self.version,
        }
