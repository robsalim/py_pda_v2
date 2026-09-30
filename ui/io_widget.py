import csv
import json
import re
from typing import Tuple
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QTreeWidget, QTreeWidgetItem,
    QLabel, QPushButton, QTableView, QHeaderView, QLineEdit, QComboBox,
    QSplitter, QDialog, QFormLayout, QDoubleSpinBox,
    QSpinBox, QMessageBox, QAbstractItemView, QInputDialog, QFileDialog,
    QMenu, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, QMimeData, pyqtSignal
from PyQt6.QtGui import QDrag, QDropEvent, QDragEnterEvent, QDragMoveEvent
from database.db_service import DatabaseService
from models.connection import Connection
from models.tag import Tag
from ui.tag_table_model import TagTableModel, TagFilterModel
from ui.styles import SPIN_BUTTON_QSS
from ui import theme
from ui.theme import S
from drivers.registry import (
    driver_type_keys,
    get_driver_class,
    get_driver_label,
    normalize_driver_type,
)
from drivers.snap7_driver import Snap7Driver
from drivers.snap7_s200_driver import Snap7S200Driver

# ---------------------------------------------------------------------------
# Конструктор адреса Siemens S7
# ---------------------------------------------------------------------------
# Память S7 разбита на области; адрес = область + размер + смещение (+ бит).
# Набор областей берётся из класса драйвера (ADDRESS_AREAS): у старших S7
# это DB/M/I/Q, у S7-200 SMART вместо DB — V-память (VB/VW/VD, бит V100.3).
S7_AREAS = [
    ("DB", "DB — блок данных"),
    ("M", "M — Merker (флаги)"),
    ("I", "I — образ входа"),
    ("Q", "Q — образ выхода"),
]

# Буква размера в адресе и её смысл
S7_SIZES = [
    ("B", "B (1 байт)"),
    ("W", "W (2 байта)"),
    ("D", "D (4 байта)"),
    ("X", "X (бит)"),
]

# Типы адресов snap7-семейства (старшие S7 и S7-200 SMART)
SNAP7_DRIVER_TYPES = {Snap7Driver.DRIVER_TYPE, Snap7S200Driver.DRIVER_TYPE}

# Размер однозначно диктует тип только для X/D/B; для W допустимы оба целых.
# Иначе драйвер прочтёт не то (REAL в слове или INT16 в бите — классическая
# причина «плывущих» значений).
TYPE_BY_SIZE = {"B": "BYTE", "W": "INT16", "D": "FLOAT", "X": "BOOL"}
# Типы, которые драйвер реально умеет читать из адреса данного размера
# (см. Snap7Driver._extract): B -> byte, W -> int/uint, D -> real/dword,
# X -> бит. Навязываем тип, только если выбранный в этот набор не входит,
# чтобы не портить корректную пару «DB1.DBW4 + UINT16» при редактировании.
SIZE_TYPES = {"B": ("BYTE",), "W": ("INT16", "UINT16"),
              "D": ("FLOAT", "DWORD"), "X": ("BOOL",)}
# Обратная связь: по выбранному типу подсказываем минимально подходящий размер
SIZE_BY_TYPE = {"BOOL": "X", "BYTE": "B", "FLOAT": "D", "INT16": "W",
                "UINT16": "W", "DWORD": "D"}

# Набор типов: BYTE (get_byte) и DWORD (get_dword) читает только snap7
BASE_DATA_TYPES = ["FLOAT", "INT16", "UINT16", "BOOL"]
S7_EXTRA_DATA_TYPES = ["BYTE", "DWORD"]
# Имя элемента Areas из драйвера -> буква области для ввода пользователя
LETTER_BY_AREA = {"MK": "M", "PE": "I", "PA": "Q"}


# ---------------------------------------------------------------------------
# QSS-шаблоны диалогов (токены {%token%} заполняются палитрой ui/theme.py)
# ---------------------------------------------------------------------------
def _dialog_qss(p):
    return f"""
        QWidget {{
            background-color: {p.window};
            color: {p.text};
            font-size: 12px;
        }}
        QLabel {{
            color: {p.text};
            background: transparent;
        }}
        QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
            background-color: {p.input};
            color: {p.text};
            border: 1px solid {p.border};
            border-radius: 4px;
            padding: 4px 6px;
        }}
    """ + theme.spin_qss(p) + f"""
        /* Стиль для ВСЕХ кнопок по умолчанию (с hover и pressed) */
        QPushButton {{
            background-color: {p.btn_action};
            color: {p.btn_action_text};
            border: 1px solid {p.border};
            border-radius: 4px;
            padding: 5px 12px;
            font-weight: bold;
            outline: none;
        }}
        QPushButton:hover {{
            background-color: {p.btn_action_hover};
            border-color: {p.btn_action_border_hover};
        }}
        QPushButton:pressed {{
            background-color: {p.btn_action_pressed};
            border-color: {p.accent_dark};
        }}
        QPushButton:disabled {{
            background-color: {p.disabled_bg};
            color: {p.disabled_fg};
            border-color: {p.border_muted};
        }}
    """


OK_BTN_QSS = """
    QPushButton { background-color: {%btn_action%}; color: {%btn_action_text%}; border: 1px solid {%btn_action_border%}; font-weight: bold; border-radius: 4px; padding: 5px 12px; outline: none; }
    QPushButton:hover { background-color: {%btn_action_hover%}; }
    QPushButton:pressed { background-color: {%btn_action_pressed%}; }
"""

CONN_DIALOG_QSS = """
    QDialog { background-color: {%window%}; color: {%text%}; }
    QLabel { color: {%text%}; font-size: 12px; background: transparent; }
    QLineEdit, QComboBox, QSpinBox {
        background-color: {%input%}; color: {%text%}; border: 1px solid {%border%};
        border-radius: 4px; padding: 4px 6px; font-size: 12px;
    }
    QPushButton {
        background-color: {%btn_action%}; color: {%btn_action_text%}; border: 1px solid {%btn_action_border%};
        border-radius: 4px; padding: 5px 14px; font-weight: bold; font-size: 12px;
    }
    QPushButton:hover { background-color: {%btn_action_hover%}; border-color: {%btn_action_border_hover%}; }
"""


# Индикаторы валидации/подсказок адреса: цвет по состоянию поля.
def hint_qss(p, state: str) -> str:
    color = {"ok": p.ok, "error": p.error}.get(state, p.hint)
    return f"color: {color}; font-size: 11px; padding-left: 2px;"


class TagEditDialog(QDialog):
    def __init__(self, conn_id: int, current_group: str = "Общие", existing_groups=None,
                 tag: Tag = None, driver_type: str = None, parent=None):
        super().__init__(parent)
        self.conn_id = conn_id
        self.tag = tag
        # Защита от рекурсии: конструктор <-> текстовое поле <-> тип
        self._syncing = False
        self.driver_type = normalize_driver_type(driver_type) if driver_type else None
        self.driver_class = get_driver_class(self.driver_type) if self.driver_type else None
        self.setWindowTitle("Редактирование переменной" if tag else "Создать переменную")
        self.is_s7 = self.driver_type in SNAP7_DRIVER_TYPES
        self.setFixedWidth(560 if self.is_s7 else 440)
        # Базовый стиль виджета и всех дочерних элементов по умолчанию
        theme.themed(self, _dialog_qss)

        layout = QFormLayout(self)
        self.txt_name = QLineEdit(tag.name if tag else "New_Signal")

        self.combo_group = QComboBox()
        self.combo_group.setEditable(True)
        groups = set(existing_groups or [])
        groups.update(["Общие", "Подача", "Сервопривод", "Смазка", "Связь и диагностика", "Статус / Автоматика"])
        for g in sorted(groups):
            self.combo_group.addItem(g)

        default_g = tag.group_name if tag else (current_group or "Общие")
        self.combo_group.setCurrentText(default_g)

        self.txt_addr = QLineEdit(tag.address_str if tag else "0")
        hint = getattr(self.driver_class, "ADDRESS_HINT", "") if self.driver_class else ""
        self.addr_hint = hint
        if hint:
            self.txt_addr.setPlaceholderText(hint)
        self.combo_type = QComboBox()
        # Для Siemens список типов всё равно ограничивается размером адреса
        # (_restrict_types_to_size), а полный набор актуален для modbus.
        # BYTE (get_byte) и DWORD (get_dword) умеет читать только snap7.
        types = BASE_DATA_TYPES + (S7_EXTRA_DATA_TYPES if self.is_s7 else [])
        self.combo_type.addItems(types)
        if tag and tag.data_type in types:
            self.combo_type.setCurrentText(tag.data_type)

        # Проверка адреса при вводе: размер чтения зависит и от адреса, и от типа
        self.txt_addr.textChanged.connect(self._on_addr_text_changed)
        self.combo_type.currentTextChanged.connect(self._on_type_changed)

        self.spin_scale = QDoubleSpinBox()
        self.spin_scale.setRange(-100000.0, 100000.0)
        self.spin_scale.setDecimals(4)
        self.spin_scale.setValue(tag.scale if tag else 1.0)

        self.spin_offset = QDoubleSpinBox()
        self.spin_offset.setRange(-100000.0, 100000.0)
        self.spin_offset.setDecimals(2)
        self.spin_offset.setValue(tag.offset_val if tag else 0.0)

        self.txt_unit = QLineEdit(tag.unit if tag else "")

        layout.addRow("Имя переменной (Name):", self.txt_name)
        layout.addRow("📁 Группа (Group):", self.combo_group)
        layout.addRow("Адрес регистра (Address):", self.txt_addr)

        if self.is_s7:
            self._build_s7_address_widgets(layout)

        self.lbl_addr_hint = QLabel(hint or "Адрес сигнала в формате драйвера")
        self.lbl_addr_hint.setWordWrap(True)
        # Цвет подсказки зависит от состояния валидации (_addr_state) и темы
        self._addr_state = "hint"
        self._restyle_addr_hint = theme.themed(
            self.lbl_addr_hint, lambda p: theme.hint_qss(p, self._addr_state))
        self._restyle_addr_field = theme.themed(
            self.txt_addr, lambda p: theme.field_error_qss(p) if self._addr_state == "error" else "")
        layout.addRow("", self.lbl_addr_hint)

        layout.addRow("Тип данных (Type):", self.combo_type)
        layout.addRow("Масштаб (Scale):", self.spin_scale)
        layout.addRow("Смещение (Offset):", self.spin_offset)
        layout.addRow("Ед. изм. (Unit):", self.txt_unit)

        btn_box = QHBoxLayout()
        btn_ok = QPushButton("Сохранить")
        theme.themed(btn_ok, OK_BTN_QSS)
        btn_ok.clicked.connect(self._on_accept)
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addRow(btn_box)

        self._validate_address()

    # ------------------------------------------------------- S7 адрес-конструктор
    def _build_s7_address_widgets(self, layout: QFormLayout):
        """
        Виджеты выбора адреса S7. Результат всегда попадает в txt_addr,
        поэтому валидация и сохранение работают по прежним правилам.
        Набор областей берётся из класса драйвера: DB/M/I/Q у старших S7,
        V/M/I/Q у S7-200 SMART (V-память читается как DB1).
        """
        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self._s7_areas = list(getattr(self.driver_class, "ADDRESS_AREAS", S7_AREAS))
        self.combo_area = QComboBox()
        for code, label in self._s7_areas:
            self.combo_area.addItem(label, code)

        self.spin_db = QSpinBox()
        self.spin_db.setRange(1, 9999)
        self.spin_db.setPrefix("DB")
        self.spin_db.setMinimumWidth(90)

        self.combo_size = QComboBox()
        for letter, label in S7_SIZES:
            self.combo_size.addItem(label, letter)
        self._set_if(self.combo_size, "W")   # типичный старт: слово (INT16)
        self.combo_size.setMinimumWidth(110)

        self.spin_byte = QSpinBox()
        self.spin_byte.setRange(0, 65535)
        self.spin_byte.setMinimumWidth(90)

        self.combo_bit = QComboBox()
        for b in range(8):
            # data обязателен: обратный разбор ищет бит через findData
            self.combo_bit.addItem(str(b), str(b))
        self.combo_bit.setMinimumWidth(110)

        # Явная высота: без неё QFormLayout сжимает поля конструктора до ~18px,
        # и стрелки спинбокса (вверх/вниз) становятся почти недостижимыми —
        # приходится попадать узкой кнопкой сбоку. 30px дают нормальные кнопки.
        for w in (self.combo_area, self.spin_db, self.combo_size,
                  self.spin_byte, self.combo_bit):
            w.setMinimumHeight(30)

        # Подписи-соседи нужны для синхронной видимости: только поле спрятать
        # нельзя — осталась бы сиротная подпись
        self.lbl_db = QLabel("Номер DB:")
        self.lbl_bit = QLabel("Бит:")
        grid.addWidget(QLabel("Область:"), 0, 0)
        grid.addWidget(self.combo_area, 0, 1, 1, 3)
        grid.addWidget(self.lbl_db, 1, 0)
        grid.addWidget(self.spin_db, 1, 1)
        grid.addWidget(QLabel("Размер:"), 1, 2)
        grid.addWidget(self.combo_size, 1, 3)
        grid.addWidget(QLabel("Смещение:"), 2, 0)
        grid.addWidget(self.spin_byte, 2, 1)
        grid.addWidget(self.lbl_bit, 2, 2)
        grid.addWidget(self.combo_bit, 2, 3)

        box = QWidget()
        box.setLayout(grid)
        # Фиксируем высоту контейнера по содержимому, чтобы layout не сжимал строки
        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addRow("🧩 Конструктор S7:", box)

        self.combo_area.currentIndexChanged.connect(self._s7_parts_changed)
        self.combo_size.currentIndexChanged.connect(self._s7_parts_changed)
        self.combo_bit.currentIndexChanged.connect(self._s7_parts_changed)
        self.spin_db.valueChanged.connect(self._s7_parts_changed)
        self.spin_byte.valueChanged.connect(self._s7_parts_changed)

        # Первичная загрузка: под guard'ом, иначе промежуточные значения
        # спина/комбо вызвали бы _s7_parts_changed и испортили тип и адрес
        self._syncing = True
        try:
            loaded = self._s7_load_from_text(self.txt_addr.text())
        finally:
            self._syncing = False
        if loaded:
            # Тип при редактировании не трогаем, но список ограниняем размером
            self._restrict_types_to_size(self.combo_size.currentData())
            self._s7_update_gating()
        elif self.tag is None:
            # Новый тег: генерируем разумный адрес по умолчанию. Существующий с
            # неразборным адресом оставляем как есть — иначе потеряли бы данные.
            self._s7_parts_changed()

    def _restrict_types_to_size(self, size: str):
        """
        В списке типов оставляем только те, что драйвер реально читает из
        адреса данного размера (B -> BYTE, W -> INT16/UINT16,
        D -> FLOAT/DWORD, X -> BOOL). Текущий тип сохраняем, если он совместим.
        """
        allowed = SIZE_TYPES.get(size, ())
        if not allowed:
            return
        current = [self.combo_type.itemText(i)
                   for i in range(self.combo_type.count())]
        if tuple(current) != tuple(allowed):
            keep = self.combo_type.currentText()
            self.combo_type.blockSignals(True)
            self.combo_type.clear()
            self.combo_type.addItems(allowed)
            if keep in allowed:
                self.combo_type.setCurrentText(keep)
            self.combo_type.blockSignals(False)

    def _s7_load_from_text(self, address: str) -> bool:
        """
        Заполняет конструктор из строки адреса (обратный разбор).
        Возвращает False, если адрес не разбирается.
        """
        parser = getattr(self.driver_class, "parse_address", None)
        if parser is None:
            from drivers.snap7_driver import parse_s7_address as parser

        addr = parser(address, self.combo_type.currentText())
        if addr is None:
            return False

        if getattr(addr, "v_area", False):
            # V-память SMART: физически это DB1, но в конструкторе — область V
            area_code, db_num = "V", addr.db
        elif addr.kind == "DB":
            area_code, db_num = "DB", addr.db
        else:
            area_code = LETTER_BY_AREA.get(addr.area, "M")
            db_num = 1

        self._set_if(self.combo_area, area_code)
        self.spin_db.setValue(max(1, db_num))
        self._set_if(self.combo_size, addr.size)
        self.spin_byte.setValue(addr.offset)
        self._set_if(self.combo_bit, str(addr.bit))
        return True

    @staticmethod
    def _set_if(combo, value):
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _s7_parts_changed(self, _=None):
        """Части -> строка адреса; одновременно подстраиваем тип данных."""
        if self._syncing:
            return
        self._syncing = True
        try:
            area = self.combo_area.currentData()
            size = self.combo_size.currentData()
            offset = self.spin_byte.value()

            if area == "DB":
                base = f"DB{self.spin_db.value()}.DB{size}{offset}"
            elif area == "V":
                # V-память SMART: бит пишется как V100.3, без буквы X
                base = f"V{'' if size == 'X' else size}{offset}"
            else:
                base = f"{area}{size}{offset}"
            full = f"{base}.{self.combo_bit.currentText()}" if size == "X" else base

            self.txt_addr.setText(full)

            # Тип: только те варианты, которые драйвер реально читает из адреса
            # выбранного размера
            self._restrict_types_to_size(size)

            # Номер DB имеет смысл только для DB, бит — только для X:
            # ненужные поля скрываем вместе с подписями
            self._s7_update_gating()
        finally:
            self._syncing = False

        # При первичной сборке виджеты подсказки ещё не созданы
        if hasattr(self, "lbl_addr_hint"):
            self._validate_address()

    def _s7_update_gating(self):
        """Показываем только те поля конструктора, что участвуют в адресе."""
        is_db = self.combo_area.currentData() == "DB"
        is_x = self.combo_size.currentData() == "X"
        self.lbl_db.setVisible(is_db)
        self.spin_db.setVisible(is_db)
        self.lbl_bit.setVisible(is_x)
        self.combo_bit.setVisible(is_x)

    def _on_addr_text_changed(self, _=None):
        """Ручной ввод в поле адреса: разбираем и отражаем в конструкторе."""
        if self._syncing:
            return
        if self.is_s7:
            self._syncing = True
            try:
                loaded = self._s7_load_from_text(self.txt_addr.text())
            finally:
                self._syncing = False
            if loaded:
                # ВАЖНО: адрес не пересобираем через _s7_parts_changed —
                # setText сбросил бы курсор в конец и сорвал ручной ввод
                # (например правку номера в DB87.DBX88.7). Конструктор уже
                # обновлён разбором, осталось подогнать список типов и видимость
                self._restrict_types_to_size(self.combo_size.currentData())
                self._s7_update_gating()
        self._validate_address()

    def _on_type_changed(self, _=None):
        """Выбор типа: подсказываем минимально подходящий размер адреса."""
        if self._syncing:
            self._validate_address()
            return
        if self.is_s7:
            want = SIZE_BY_TYPE.get(self.combo_type.currentText())
            if want and self.combo_size.currentData() != want:
                # Рекурсия не возникает: _s7_parts_changed меняет тип только
                # когда он несовместим с размером, а здесь как раз совместимый
                self._set_if(self.combo_size, want)
        self._validate_address()

    def _validate_address(self) -> bool:
        """Подсвечивает поле и показывает текст ошибки для неразборного адреса."""
        # Виджеты ещё не собраны (идёт первичная инициализация) — не на что выводить
        if not hasattr(self, "lbl_addr_hint"):
            return True
        address = self.txt_addr.text().strip()
        data_type = self.combo_type.currentText()
        error = None
        if self.driver_class:
            error = self.driver_class.validate_address(address, data_type)

        if error:
            self._addr_state = "error"
            self.lbl_addr_hint.setText("⚠ " + error)
            self._restyle_addr_hint()
            self._restyle_addr_field()
            return False

        self._addr_state = "ok"
        self.lbl_addr_hint.setText("✓ Адрес разобран корректно." + (f" {self.addr_hint}" if self.addr_hint else ""))
        self._restyle_addr_hint()
        self._restyle_addr_field()
        return True

    def _on_accept(self):
        if not self._validate_address():
            QMessageBox.warning(
                self, "Проверьте адрес",
                self.lbl_addr_hint.text().replace("⚠ ", "") +
                "\n\nСохранение выполнено не будет."
            )
            return
        self.accept()

    def get_tag_data(self) -> Tag:
        tag_id = self.tag.id if self.tag else 0
        group = self.combo_group.currentText().strip() or "Общие"
        return Tag(
            id=tag_id,
            connection_id=self.conn_id,
            name=self.txt_name.text().strip() or "Unnamed",
            address_str=self.txt_addr.text().strip() or "0",
            data_type=self.combo_type.currentText(),
            scale=self.spin_scale.value(),
            offset_val=self.spin_offset.value(),
            unit=self.txt_unit.text().strip(),
            group_name=group
        )

class DroppableTreeWidget(QTreeWidget):
    def __init__(self, parent_widget, parent=None):
        super().__init__(parent)
        self.parent_widget = parent_widget
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent):
        item = self.itemAt(event.position().toPoint())
        if item:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get("type") in ["group", "conn"]:
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        item = self.itemAt(event.position().toPoint())
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        text = event.mimeData().text()
        if not text:
            return
        target_conn = data["conn"]
        target_group = data.get("group", "Общие")
        try:
            tag_ids = [int(i.strip()) for i in text.split(",") if i.strip()]
            self.parent_widget.move_tags_to_group(tag_ids, target_conn.id, target_group)
            event.acceptProposedAction()
        except ValueError:
            pass

class TagTableView(QTableView):
    """
    QTableView поверх TagTableModel: строки тянет view, а drag передаёт
    tag_id тех строк, что сейчас выделены (через модель, не через ячейки).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSortingEnabled(True)
        self.verticalHeader().setVisible(False)
        self._drag_start_pos = None

    def _drag_tag_ids(self):
        model = self.model()
        if model is None:
            return []
        source = model.sourceModel() if hasattr(model, "sourceModel") else model
        ids = []
        for proxy in sorted(self.selectionModel().selectedRows(), key=lambda i: i.row()):
            src = model.mapToSource(proxy)
            tag = source.row_at(src.row()) if hasattr(source, "row_at") else None
            if tag is not None:
                ids.append(int(tag.id))
        return ids

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if self._drag_start_pos is None:
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < 5:
            return
        if not self.selectionModel().hasSelection():
            return
        tag_ids = self._drag_tag_ids()
        if not tag_ids:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(",".join(str(i) for i in tag_ids))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def contextMenuEvent(self, event):
        index = self.indexAt(event.pos())
        if not index.isValid():
            return
        model = self.model()
        source = model.sourceModel()
        tag = source.row_at(model.mapToSource(index).row())
        if tag is None:
            return
        menu = QMenu(self)
        mark_fav = menu.addAction("☆ В избранное" if tag.id not in source.favorites else "★ Убрать из избранного")
        act = menu.exec(event.globalPos())
        if act == mark_fav:
            source.toggle_favorite(tag.id)
            if isinstance(model, TagFilterModel) and model.mode == TagFilterModel.MODE_FAV:
                model.invalidateFilter()

class ConnectionDialog(QDialog):
    def __init__(self, conn: Connection = None, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.setWindowTitle("Редактирование подключения" if conn else "Новое подключение")
        self.setFixedWidth(400)
        theme.themed(self, lambda p: theme.fill(CONN_DIALOG_QSS, p) + theme.spin_qss(p))

        layout = QFormLayout(self)
        cfg = conn.config if (conn and isinstance(conn.config, dict)) else {}

        self.txt_name = QLineEdit(conn.name if conn else "Siemens S7 (snap7)")

        self.combo_type = QComboBox()
        for key in driver_type_keys():
            self.combo_type.addItem(get_driver_label(key), key)
        if conn:
            wanted = normalize_driver_type(conn.driver_type)
            idx = self.combo_type.findData(wanted)
            if idx >= 0:
                self.combo_type.setCurrentIndex(idx)

        self.txt_ip = QLineEdit(cfg.get("ip", "192.168.0.1"))
        
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1, 65535)
        default_port = 102 if self.combo_type.currentData() in SNAP7_DRIVER_TYPES else 502
        self.spin_port.setValue(cfg.get("port", default_port))

        self.spin_rack = QSpinBox()
        self.spin_rack.setRange(0, 15)
        self.spin_rack.setValue(cfg.get("rack", 0))

        self.spin_slot = QSpinBox()
        self.spin_slot.setRange(0, 15)
        self.spin_slot.setValue(cfg.get("slot", 1))

        self.spin_slave_id = QSpinBox()
        self.spin_slave_id.setRange(1, 255)
        self.spin_slave_id.setValue(cfg.get("slave_id", 1))

        self.spin_poll = QSpinBox()
        self.spin_poll.setRange(1, 60000)
        self.spin_poll.setSingleStep(5)
        self.spin_poll.setValue(conn.poll_interval_ms if conn else 100)
        self.spin_poll.setToolTip(
            "Целевой ПЕРИОД опроса: драйвер досыпает только остаток цикла, "
            "поэтому фактический период не больше этого значения, но никогда "
            "не меньше времени самого чтения.\n\n"
            "Реальный цикл = число_запросов_к_ПЛК × отклик_ПЛК + время_записи_в_БД.\n"
            "Отклик S7-1200/1500 по LAN обычно 3-10 мс на запрос, поэтому для 10 мс "
            "нужен один суммарный запрос (он и используется: multi-read до 20 тегов).\n\n"
            "Фактическое время цикла и число запросов видно в статусе модуля "
            "(вкладка I/O Configuration)."
        )

        layout.addRow("Имя модуля:", self.txt_name)
        layout.addRow("Тип драйвера:", self.combo_type)
        layout.addRow("IP-адрес контроллера:", self.txt_ip)
        layout.addRow("TCP Порт:", self.spin_port)

        self.row_rack_label = QLabel("Стойка (Rack):")
        self.row_slot_label = QLabel("Слот ЦПУ (Slot):")
        self.row_slave_label = QLabel("Slave ID:")

        layout.addRow(self.row_rack_label, self.spin_rack)
        layout.addRow(self.row_slot_label, self.spin_slot)
        layout.addRow(self.row_slave_label, self.spin_slave_id)
        layout.addRow("Период опроса (мс):", self.spin_poll)

        lbl_poll_hint = QLabel(
            "Это целевой период, а не пауза после чтения. Меньше, чем длится само "
            "чтение, цикл стать не может — фактическое время и число запросов к ПЛК "
            "показаны в статусе модуля на вкладке I/O Configuration."
        )
        lbl_poll_hint.setWordWrap(True)
        theme.themed(lbl_poll_hint, lambda p: theme.hint_qss(p, "hint"))
        layout.addRow("", lbl_poll_hint)

        self.combo_type.currentIndexChanged.connect(
            lambda _: self._on_driver_type_changed(self.combo_type.currentData())
        )
        self._on_driver_type_changed(self.combo_type.currentData())

        btn_box = QHBoxLayout()
        btn_ok = QPushButton("Сохранить" if conn else "Создать")
        theme.themed(btn_ok, f"background-color: {S('btn_action')}; color: {S('btn_action_text')};")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addRow(btn_box)

    def _on_driver_type_changed(self, dtype: str):
        if dtype in SNAP7_DRIVER_TYPES:
            self.spin_port.setValue(102)
            self.row_rack_label.show()
            self.spin_rack.show()
            self.row_slot_label.show()
            self.spin_slot.show()
            self.row_slave_label.hide()
            self.spin_slave_id.hide()
        elif dtype == "modbus_client":
            self.spin_port.setValue(502)
            self.row_rack_label.hide()
            self.spin_rack.hide()
            self.row_slot_label.hide()
            self.spin_slot.hide()
            self.row_slave_label.show()
            self.spin_slave_id.show()
        else:
            self.spin_port.setValue(502)
            self.row_rack_label.hide()
            self.spin_rack.hide()
            self.row_slot_label.hide()
            self.spin_slot.hide()
            self.row_slave_label.hide()
            self.spin_slave_id.hide()

    def get_data(self):
        dtype = self.combo_type.currentData()
        config = {
            "ip": self.txt_ip.text().strip(),
            "port": self.spin_port.value()
        }
        if dtype in SNAP7_DRIVER_TYPES:
            config["rack"] = self.spin_rack.value()
            config["slot"] = self.spin_slot.value()
        elif dtype == "modbus_client":
            config["slave_id"] = self.spin_slave_id.value()

        return {
            "name": self.txt_name.text().strip() or "Connection",
            "driver_type": dtype,
            "poll_interval_ms": self.spin_poll.value(),
            "config": config
        }

class IOWidget(QWidget):
    # конфигурация тегов изменилась (добавление/удаление/редактирование/перемещение)
    tags_changed = pyqtSignal()

    def __init__(self, db_service: DatabaseService, driver_manager, parent=None):
        super().__init__(parent)
        self.db = db_service
        self.dm = driver_manager
        self.current_tags = []
        self.selected_conn = None
        self.selected_group = None
        self._init_ui()

        self.timer = QTimer(self)
        # Обновление таблицы не чаще 20 Гц и только по изменённым ячейкам
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.refresh_online_values)
        self.timer.start()

    def _sync_registry(self):
        """После изменения конфигурации тегов обновляем живой реестр драйверов."""
        try:
            self.dm.reload_tags()
        except Exception:
            pass
        self.tags_changed.emit()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_box = QWidget()
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        lbl_tree = QLabel("**Дерево I/O:**")
        theme.themed(lbl_tree, f"color: {S('text')}; font-size: 13px;")
        left_layout.addWidget(lbl_tree)

        self.io_tree = DroppableTreeWidget(parent_widget=self)
        self.io_tree.setHeaderHidden(True)
        theme.themed(self.io_tree, """
            QTreeWidget { background: {%panel%}; color: {%text%}; border: 1px solid {%border_soft%}; font-size: 12px; }
            QTreeWidget::item { padding: 4px; border-bottom: 1px solid {%border_muted%}; }
            QTreeWidget::item:selected { background: {%selection%}; color: {%selection_text%}; }
        """)
        self.io_tree.currentItemChanged.connect(self._on_tree_selection_changed)
        left_layout.addWidget(self.io_tree)

        box_conn_btns = QHBoxLayout()
        self.btn_add_conn = QPushButton("➕ Модуль")
        theme.themed(self.btn_add_conn, theme.btn_qss("add"))
        self.btn_add_conn.clicked.connect(self._add_conn)
        self.btn_edit_conn = QPushButton("✏ Модуль")
        theme.themed(self.btn_edit_conn, theme.btn_qss("violet"))
        self.btn_edit_conn.clicked.connect(self._edit_conn)
        self.btn_del_conn = QPushButton("✕ Модуль")
        theme.themed(self.btn_del_conn, theme.btn_qss("danger"))
        self.btn_del_conn.clicked.connect(self._del_conn)
        box_conn_btns.addWidget(self.btn_add_conn)
        box_conn_btns.addWidget(self.btn_edit_conn)
        box_conn_btns.addWidget(self.btn_del_conn)
        left_layout.addLayout(box_conn_btns)

        box_grp_btns = QHBoxLayout()
        self.btn_add_grp = QPushButton("➕ Группа")
        theme.themed(self.btn_add_grp, theme.btn_qss("add"))
        self.btn_add_grp.clicked.connect(self._add_group)

        self.btn_ren_grp = QPushButton("✏ Группа")
        theme.themed(self.btn_ren_grp, theme.btn_qss("violet"))
        self.btn_ren_grp.clicked.connect(self._rename_group)

        self.btn_del_grp = QPushButton("✕ Группа")
        theme.themed(self.btn_del_grp, theme.btn_qss("danger"))
        self.btn_del_grp.clicked.connect(self._del_group)

        box_grp_btns.addWidget(self.btn_add_grp)
        box_grp_btns.addWidget(self.btn_ren_grp)
        box_grp_btns.addWidget(self.btn_del_grp)
        left_layout.addLayout(box_grp_btns)

        box_tree_backup = QHBoxLayout()
        self.btn_export_all = QPushButton("📦 Экспорт дерева")
        theme.themed(self.btn_export_all, theme.btn_qss("teal"))
        self.btn_export_all.clicked.connect(self._export_full_tree_json)

        self.btn_import_all = QPushButton("📥 Импорт дерева")
        theme.themed(self.btn_import_all, theme.btn_qss("violet"))
        self.btn_import_all.clicked.connect(self._import_full_tree_json)

        box_tree_backup.addWidget(self.btn_export_all)
        box_tree_backup.addWidget(self.btn_import_all)
        left_layout.addLayout(box_tree_backup)

        splitter.addWidget(left_box)

        right_box = QWidget()
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        right_header = QHBoxLayout()
        self.lbl_conn_title = QLabel("**Переменные**")
        theme.themed(self.lbl_conn_title, f"color: {S('text')}; font-size: 13px;")
        right_header.addWidget(self.lbl_conn_title)
        right_header.addStretch()

        self.btn_add_tag = QPushButton("➕ Переменная")
        theme.themed(self.btn_add_tag, theme.btn_qss("action"))
        self.btn_add_tag.clicked.connect(self._add_tag)
        right_header.addWidget(self.btn_add_tag)

        self.btn_edit_tag = QPushButton("✏ Изменить")
        theme.themed(self.btn_edit_tag, theme.btn_qss("violet"))
        self.btn_edit_tag.clicked.connect(self._edit_tag)
        right_header.addWidget(self.btn_edit_tag)

        self.btn_copy_tag = QPushButton("⎘ Копировать")
        theme.themed(self.btn_copy_tag, theme.btn_qss("add"))
        self.btn_copy_tag.clicked.connect(self._copy_last_tag)
        right_header.addWidget(self.btn_copy_tag)

        self.btn_del_tag = QPushButton("✕ Удалить")
        theme.themed(self.btn_del_tag, theme.btn_qss("danger"))
        self.btn_del_tag.clicked.connect(self._del_tag)
        right_header.addWidget(self.btn_del_tag)

        self.btn_import_csv = QPushButton("📥 Импорт CSV")
        theme.themed(self.btn_import_csv, theme.btn_qss("violet"))
        self.btn_import_csv.clicked.connect(self._import_csv)
        right_header.addWidget(self.btn_import_csv)

        self.btn_export_csv = QPushButton("📤 Экспорт CSV")
        theme.themed(self.btn_export_csv, theme.btn_qss("teal"))
        self.btn_export_csv.clicked.connect(self._export_csv)
        right_header.addWidget(self.btn_export_csv)

        right_layout.addLayout(right_header)

        # Панель поиска и фильтров таблицы тегов
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        self.txt_tag_search = QLineEdit()
        self.txt_tag_search.setPlaceholderText("Поиск по имени / адресу / группе…")
        self.txt_tag_search.setClearButtonEnabled(True)
        self.txt_tag_search.textChanged.connect(self._on_tag_search)
        filter_row.addWidget(self.txt_tag_search)

        self.combo_tag_filter = QComboBox()
        self.combo_tag_filter.addItem("Все переменные", TagFilterModel.MODE_ALL)
        self.combo_tag_filter.addItem("Изменившиеся", TagFilterModel.MODE_CHANGED)
        self.combo_tag_filter.addItem("Избранные", TagFilterModel.MODE_FAV)
        self.combo_tag_filter.currentIndexChanged.connect(self._on_tag_filter_mode)
        filter_row.addWidget(self.combo_tag_filter)

        self.lbl_tag_count = QLabel("")
        theme.themed(self.lbl_tag_count, f"color: {S('text_muted')}; font-size: 12px;")
        filter_row.addWidget(self.lbl_tag_count)
        filter_row.addStretch()
        right_layout.addLayout(filter_row)

        self.tag_model = TagTableModel(self)
        self.tag_proxy = TagFilterModel(self)
        self.tag_proxy.setSourceModel(self.tag_model)
        self.tag_proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.tag_proxy.setDynamicSortFilter(True)

        self.tag_table = TagTableView()
        self.tag_table.setModel(self.tag_proxy)
        header = self.tag_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        theme.themed(self.tag_table, """
            QTableView { background: {%window%}; color: {%text%}; gridline-color: {%spin_btn%}; font-size: 12px; }
            QHeaderView::section { background: {%panel%}; color: {%text%}; font-weight: bold; border: 1px solid {%border_soft%}; padding: 4px; }
            QTableView::item:selected { background-color: {%handle%}; color: {%text%}; }
        """)
        self.tag_table.doubleClicked.connect(self._edit_tag)
        right_layout.addWidget(self.tag_table)

        splitter.addWidget(right_box)
        splitter.setSizes([320, 1100])

        layout.addWidget(splitter)
        self.reload_tree()
        QTimer.singleShot(0, self._fit_tag_table_columns)

    def _fit_tag_table_columns(self):
        """
        Начальные ширины колонок. resizeColumnsToContents() пришлось бы
        обойти все строки модели, а тегов может быть десять тысяч, поэтому
        задаём фиксированные разумные значения.
        """
        header = self.tag_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        widths = [50, 160, 110, 90, 85, 70, 70, 80, 140, 150]
        for column, width in enumerate(widths):
            self.tag_table.setColumnWidth(column, width)
        self.tag_table.horizontalHeader().setStretchLastSection(True)

    def move_tags_to_group(self, tag_ids: list, target_conn_id: int, target_group: str):
        with self.db._get_connection() as conn:
            cur = conn.cursor()
            ph = "?" if self.db.engine == "sqlite" else "%s"
            for tid in tag_ids:
                cur.execute(f"UPDATE tags SET group_name = {ph}, connection_id = {ph} WHERE id = {ph};",
                            (target_group, target_conn_id, tid))
            conn.commit()
            cur.close()
        self._sync_registry()
        self.reload_tree(preserve_group=target_group)

    def reload_tree(self, preserve_group: str = None):
        """
        Дерево модулей и групп. Теги в него не загружаются — счётчики групп
        берутся из SQL-агрегата, а список тегов подгружается по требованию
        при выборе группы (reload_tags).
        """
        prev = self.io_tree.currentItem()
        prev_data = prev.data(0, Qt.ItemDataRole.UserRole) if prev else None

        self.io_tree.clear()
        conns = self.db.get_all_connections()

        item_to_select = None
        same_conn_item = None

        for c in conns:
            driver_instance = self.dm.drivers.get(c.id)
            status = driver_instance.status if driver_instance else "Stopped"
            has_err = "Error" in status or "err" in status or "not installed" in status
            if has_err:
                icon = "🔴"
            elif "Running" in status or "Connected" in status or "Polling" in status:
                icon = "🟢"
            else:
                icon = "⚪"

            conn_item = QTreeWidgetItem([f"{icon} {c.name} [{c.driver_type}] — {status}"])
            conn_item.setToolTip(0, f"Статус: {status}")
            conn_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "conn", "conn": c})

            group_counts = self.db.get_group_counts_by_connection(c.id)

            for g in sorted(group_counts):
                count = group_counts[g]
                grp_item = QTreeWidgetItem([f"📁 {g} ({count})"])
                grp_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "group", "conn": c, "group": g})
                conn_item.addChild(grp_item)

                if preserve_group and g == preserve_group and self.selected_conn and self.selected_conn.id == c.id:
                    item_to_select = grp_item

            if prev_data and prev_data.get("conn") and prev_data["conn"].id == c.id:
                # узел того же модуля, который был выбран до перезагрузки
                same_conn_item = conn_item if prev_data["type"] == "conn" else same_conn_item
                if prev_data["type"] == "group" and item_to_select is None:
                    for i in range(conn_item.childCount()):
                        ch = conn_item.child(i)
                        d = ch.data(0, Qt.ItemDataRole.UserRole)
                        if d and d.get("group") == prev_data.get("group"):
                            same_conn_item = ch
                            break

            self.io_tree.addTopLevelItem(conn_item)
            conn_item.setExpanded(True)

        if item_to_select:
            self.io_tree.setCurrentItem(item_to_select)
        elif same_conn_item:
            self.io_tree.setCurrentItem(same_conn_item)
        elif self.io_tree.topLevelItemCount() > 0 and not self.io_tree.currentItem():
            self.io_tree.setCurrentItem(self.io_tree.topLevelItem(0))

    def _on_tree_selection_changed(self, current, previous):
        if not current:
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        c: Connection = data["conn"]
        self.selected_conn = c

        if data["type"] == "conn":
            self.selected_group = None
            self.lbl_conn_title.setText(f"**Все переменные модуля: {c.name}**")
            self.reload_tags(c.id, group_filter=None)
        elif data["type"] == "group":
            g = data["group"]
            self.selected_group = g
            self.lbl_conn_title.setText(f"**Модуль: {c.name} ➔ Группа: 📁 {g}**")
            self.reload_tags(c.id, group_filter=g)

    def _live_values(self):
        """Текущие значения из реестра DriverManager (без запроса к истории)."""
        reg = getattr(self.dm, "registry", None)
        if reg is not None:
            try:
                return reg.live_snapshot()
            except Exception:
                pass
        try:
            return self.db.get_tag_last_values()
        except Exception:
            return {}

    def reload_tags(self, conn_id: int, group_filter: str = None):
        all_tags = self.db.get_tags_by_connection(conn_id)
        if group_filter:
            self.current_tags = [t for t in all_tags if (t.group_name or "Общие") == group_filter]
        else:
            self.current_tags = all_tags

        self.tag_model.set_rows(self.current_tags, live=self._live_values())
        self._update_tag_count()

    def _on_tag_search(self, text: str):
        self.tag_proxy.set_search(text)
        self._update_tag_count()

    def _on_tag_filter_mode(self, _index: int):
        self.tag_proxy.set_mode(self.combo_tag_filter.currentData())
        self._update_tag_count()

    def _update_tag_count(self):
        total = self.tag_model.rowCount()
        shown = self.tag_proxy.rowCount()
        if shown == total:
            self.lbl_tag_count.setText(f"{total} шт.")
        else:
            self.lbl_tag_count.setText(f"{shown} из {total} шт.")

    def _refresh_tree_status(self):
        """Обновляет иконку и текст статуса у узлов подключений в дереве."""
        for i in range(self.io_tree.topLevelItemCount()):
            item = self.io_tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data or data.get("type") != "conn":
                continue
            c = data["conn"]
            driver_instance = self.dm.drivers.get(c.id)
            status = driver_instance.status if driver_instance else "Stopped"
            has_err = "Error" in status or "err" in status or "not installed" in status
            if has_err:
                icon = "🔴"
            elif "Running" in status or "Connected" in status or "Polling" in status:
                icon = "🟢"
            else:
                icon = "⚪"
            new_text = f"{icon} {c.name} [{c.driver_type}] — {status}"
            if item.text(0) != new_text:
                item.setText(0, new_text)
                item.setToolTip(0, f"Статус: {status}")

    def refresh_online_values(self):
        """
        Обновляет столбцы «Онлайн Значение» и «Время обновления» из реестра
        живых значений. Модель сама шлёт dataChanged только по грязным
        строкам, а QTableView запросит текст лишь для видимых из них.
        """
        self._refresh_tree_status()
        reg = getattr(self.dm, "registry", None)
        if reg is not None:
            try:
                changed = reg.take_dirty()
            except Exception:
                changed = {}
            if changed:
                entries = {tid: (p.timestamp, p.value, p.quality)
                           for tid, p in changed.items()}
                self.tag_model.apply_updates(entries)
            # «Время обновления» должно тикать у КАЖДОГО опроса, а не только
            # при смене значения. Обновляем колонку времени для видимых строк:
            # реестр всегда хранит свежий timestamp последнего чтения, а
            # перерисовка идёт лишь для того, что видно на экране.
            self._refresh_visible_time(reg)
        else:
            try:
                entries = self.db.get_tag_last_values()
            except Exception:
                return
            self.tag_model.apply_updates(entries)

        # «Остывшие» теги убираем из recent всегда, чтобы словарь не рос;
        # перевалидировать фильтр нужно только в режиме «изменившиеся»
        if self.tag_model.prune_recent() and self.tag_proxy.mode == TagFilterModel.MODE_CHANGED:
            self.tag_proxy.invalidateFilter()
        self._update_tag_count()

    def _refresh_visible_time(self, reg):
        """Освежает (ts,val,q) только для строк в видимой области таблицы."""
        tbl = self.tag_table
        vh = max(tbl.rowHeight(0), 1)
        h = tbl.viewport().height()
        if h <= 0:
            return
        seen, seen_rows = [], set()
        y = 0
        while y <= h:
            proxy_row = tbl.rowAt(y)
            if proxy_row is not None and proxy_row >= 0 and proxy_row not in seen_rows:
                seen_rows.add(proxy_row)
                src_row = self.tag_proxy.mapToSource(self.tag_proxy.index(proxy_row, 0)).row()
                tag = self.tag_model.row_at(src_row)
                if tag is not None:
                    seen.append(tag.id)
            y += vh
        if not seen:
            return
        try:
            entries = reg.read_values(seen)
        except Exception:
            return
        if entries:
            self.tag_model.apply_time_refresh(entries)

    def _selected_tags(self):
        """Все выделенные теги (для пакетного удаления)."""
        sm = self.tag_table.selectionModel()
        if sm is None:
            return []
        out = []
        for proxy in sm.selectedRows():
            src = self.tag_proxy.mapToSource(proxy)
            tag = self.tag_model.row_at(src.row())
            if tag is not None:
                out.append(tag)
        return out

    def _selected_tag(self):
        """Тег из выделенной строки с учётом сортировки/фильтра."""
        proxy = self.tag_table.selectionModel().currentIndex()
        if not proxy.isValid():
            return None
        src = self.tag_proxy.mapToSource(proxy)
        return self.tag_model.row_at(src.row())

    def _export_full_tree_json(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт полного дерева I/O", "pda_io_tree_backup.json", "JSON Files (*.json)"
        )
        if not file_path:
            return

        try:
            conns = self.db.get_all_connections()
            all_tags = self.db.get_all_tags()

            tree_data = {
                "version": "2.0",
                "connections": []
            }

            for c in conns:
                groups = self.db.get_groups_by_connection(c.id)
                c_tags = [t for t in all_tags if t.connection_id == c.id]

                c_dict = {
                    "name": c.name,
                    "driver_type": c.driver_type,
                    "enabled": c.enabled,
                    "poll_interval_ms": c.poll_interval_ms,
                    "config": c.config,
                    "groups": groups,
                    "tags": [
                        {
                            "name": t.name,
                            "group_name": t.group_name or "Общие",
                            "address_str": t.address_str,
                            "data_type": t.data_type,
                            "scale": t.scale,
                            "offset_val": t.offset_val,
                            "unit": t.unit
                        }
                        for t in c_tags
                    ]
                }
                tree_data["connections"].append(c_dict)

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(tree_data, f, ensure_ascii=False, indent=2)

            QMessageBox.information(
                self, "Успех",
                f"Полное дерево I/O сохранено:\n- Модулей: {len(tree_data['connections'])}\n- Всего тегов: {len(all_tags)}\nФайл: {file_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Ошибка экспорта", f"Не удалось экспортировать дерево:\n{e}")

    def _import_full_tree_json(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Импорт полного дерева I/O", "", "JSON Files (*.json);;All Files (*)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                tree_data = json.load(f)

            if "connections" not in tree_data:
                QMessageBox.critical(self, "Ошибка", "Файл не содержит структуры подключений (connections)!")
                return

            reply = QMessageBox.question(
                self, "Режим импорта дерева",
                "Как импортировать дерево I/O?\n\n- [Да]: Очистить старое дерево и записать новое с нуля\n- [Нет]: Добавить модули к текущим\n- [Отмена]: Прервать",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
            )

            if reply == QMessageBox.StandardButton.Cancel:
                return

            clear_existing = (reply == QMessageBox.StandardButton.Yes)

            if clear_existing:
                all_c = self.db.get_all_connections()
                for c in all_c:
                    self.db.delete_connection(c.id)

            total_tags_imported = 0
            for c_dict in tree_data["connections"]:
                new_c = Connection(
                    id=0,
                    name=c_dict["name"],
                    driver_type=c_dict["driver_type"],
                    enabled=c_dict.get("enabled", True),
                    poll_interval_ms=c_dict.get("poll_interval_ms", 100),
                    config=c_dict.get("config", {})
                )
                new_conn_id = self.db.add_connection(new_c)

                for grp in c_dict.get("groups", []):
                    self.db.add_group(new_conn_id, grp)

                for t_dict in c_dict.get("tags", []):
                    new_t = Tag(
                        id=0,
                        connection_id=new_conn_id,
                        name=t_dict["name"],
                        address_str=str(t_dict.get("address_str", "0")),
                        data_type=t_dict.get("data_type", "FLOAT"),
                        scale=float(t_dict.get("scale", 1.0)),
                        offset_val=float(t_dict.get("offset_val", 0.0)),
                        unit=t_dict.get("unit", ""),
                        group_name=t_dict.get("group_name", "Общие")
                    )
                    self.db.add_tag(new_t)
                    total_tags_imported += 1

            self.dm.restart_all()
            self.reload_tree()
            QMessageBox.information(
                self, "Успех",
                f"Дерево I/O успешно импортировано!\nМодулей: {len(tree_data['connections'])}\nПеременных: {total_tags_imported}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Ошибка импорта", f"Не удалось импортировать дерево:\n{e}")

    def _export_csv(self):
        if not self.selected_conn:
            QMessageBox.warning(self, "Внимание", "Сначала выберите модуль подключения для экспорта!")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт сигналов в CSV", f"{self.selected_conn.name}_tags.csv", "CSV Files (*.csv)"
        )
        if not file_path:
            return

        tags = self.db.get_tags_by_connection(self.selected_conn.id)
        try:
            with open(file_path, mode="w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(["Name", "Group", "Address", "Type", "Scale", "Offset", "Unit"])
                for t in tags:
                    writer.writerow([t.name, t.group_name or "Общие", t.address_str, t.data_type, t.scale, t.offset_val, t.unit or ""])

            QMessageBox.information(self, "Успех", f"Успешно экспортировано {len(tags)} переменных в файл:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка экспорта", f"Не удалось сохранить CSV:\n{e}")

    def _import_csv(self):
        if not self.selected_conn:
            QMessageBox.warning(self, "Внимание", "Сначала выберите модуль подключения, в который загружать сигналы!")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Импорт сигналов из CSV", "", "CSV Files (*.csv);;All Files (*)"
        )
        if not file_path:
            return

        try:
            imported_count = 0
            with open(file_path, mode="r", encoding="utf-8-sig") as f:
                sample = f.read(2048)
                f.seek(0)
                delimiter = ";" if ";" in sample else ","
                reader = csv.DictReader(f, delimiter=delimiter)

                existing_tags = {t.name: t for t in self.db.get_tags_by_connection(self.selected_conn.id)}

                for row in reader:
                    r = {k.strip().lower(): v.strip() for k, v in row.items() if k}
                    name = r.get("name")
                    if not name:
                        continue

                    group = r.get("group") or r.get("группа") or "Общие"
                    addr = r.get("address") or r.get("адрес") or "0"
                    dtype = r.get("type") or r.get("тип") or "FLOAT"
                    scale = float(r.get("scale") or r.get("масштаб") or 1.0)
                    offset_val = float(r.get("offset") or r.get("смещение") or 0.0)
                    unit = r.get("unit") or r.get("ед") or ""

                    if name in existing_tags:
                        old_t = existing_tags[name]
                        updated = Tag(
                            id=old_t.id,
                            connection_id=self.selected_conn.id,
                            name=name,
                            address_str=addr,
                            data_type=dtype,
                            scale=scale,
                            offset_val=offset_val,
                            unit=unit,
                            group_name=group
                        )
                        self.db.update_tag(updated)
                    else:
                        new_t = Tag(
                            id=0,
                            connection_id=self.selected_conn.id,
                            name=name,
                            address_str=addr,
                            data_type=dtype,
                            scale=scale,
                            offset_val=offset_val,
                            unit=unit,
                            group_name=group
                        )
                        self.db.add_tag(new_t)

                    imported_count += 1

            self._sync_registry()
            self.reload_tree()
            self.reload_tags(self.selected_conn.id, self.selected_group)
            QMessageBox.information(self, "Успех", f"Успешно обработано {imported_count} переменных из CSV!")

        except Exception as e:
            QMessageBox.critical(self, "Ошибка импорта", f"Не удалось прочитать CSV файл:\n{e}")

    def _get_existing_groups(self):
        if not self.selected_conn:
            return ["Общие"]
        return self.db.get_groups_by_connection(self.selected_conn.id)

    def _add_group(self):
        if not self.selected_conn:
            QMessageBox.warning(self, "Внимание", "Сначала выберите модуль подключения!")
            return

        name, ok = QInputDialog.getText(self, "Новая группа", "Введите название новой группы (папки):")
        if ok and name.strip():
            grp_name = name.strip()
            self.db.add_group(self.selected_conn.id, grp_name)
            self.reload_tree(preserve_group=grp_name)

    def _rename_group(self):
        item = self.io_tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "group":
            QMessageBox.information(self, "Инфо", "Выберите конкретную папку группы в дереве для переименования.")
            return

        c = data["conn"]
        old_group = data["group"]
        new_name, ok = QInputDialog.getText(self, "Переименовать группу", f"Новое имя для группы '{old_group}':", text=old_group)
        if ok and new_name.strip() and new_name.strip() != old_group:
            new_grp = new_name.strip()
            self.db.rename_group(c.id, old_group, new_grp)
            self._sync_registry()
            self.reload_tree(preserve_group=new_grp)

    def _del_group(self):
        item = self.io_tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "group":
            QMessageBox.information(self, "Инфо", "Выберите конкретную папку группы в дереве для удаления.")
            return

        c = data["conn"]
        group_name = data["group"]

        n_tags = self.db.get_group_counts_by_connection(c.id).get(group_name, 0)
        if n_tags:
            hint = (
                f"В группе '{group_name}' {n_tags} перем.:\n\n"
                f"- «Удалить с тегами»: группа и все её переменные (и история) исчезнут\n"
                f"- «В Общие»: переменные переедут в группу 'Общие', удалится только папка\n"
                f"- «Отмена»: ничего не меняется"
            )
        else:
            hint = f"Удалить пустую группу '{group_name}'?"

        # addButton(text, <ButtonRole>): роль — не StandardButton, иначе
        # PyQt6 бросает TypeError и удаление группы вообще не выполняется.
        box = QMessageBox(self)
        box.setWindowTitle("Удаление группы")
        box.setText(hint)
        btn_with = (box.addButton("Удалить с тегами",
                                 QMessageBox.ButtonRole.DestructiveRole)
                    if n_tags else None)
        btn_move = box.addButton("В Общие" if n_tags else "Удалить",
                                 QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()

        if clicked is btn_with:
            self.db.delete_group(c.id, group_name, with_tags=True)
            self._sync_registry()
        elif clicked is btn_move:
            self.db.delete_group(c.id, group_name)
            self._sync_registry()

        if self.selected_group == group_name:
            self.selected_group = None
            self.reload_tags(c.id, group_filter=None)
        self.reload_tree()

    def _add_conn(self):
        dlg = ConnectionDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            new_c = Connection(id=0, **data)
            self.db.add_connection(new_c)
            self.dm.restart_all()
            self.reload_tree()

    def _edit_conn(self):
        if not self.selected_conn:
            QMessageBox.warning(self, "Внимание", "Сначала выберите модуль подключения для редактирования!")
            return
        dlg = ConnectionDialog(conn=self.selected_conn, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            with self.db._get_connection() as conn:
                cur = conn.cursor()
                ph = "?" if self.db.engine == "sqlite" else "%s"
                cur.execute(f"""
                    UPDATE connections 
                    SET name = {ph}, driver_type = {ph}, poll_interval_ms = {ph}, config = {ph}
                    WHERE id = {ph};
                """, (data["name"], data["driver_type"], data["poll_interval_ms"], json.dumps(data["config"]), self.selected_conn.id))
                conn.commit()
                cur.close()
            self.dm.restart_all()
            self.reload_tree()

    def _del_conn(self):
        if not self.selected_conn:
            return
        res = QMessageBox.question(self, "Удаление", f"Удалить подключение '{self.selected_conn.name}' и все его переменные?")
        if res == QMessageBox.StandardButton.Yes:
            self.db.delete_connection(self.selected_conn.id)
            self.selected_conn = None
            self.dm.restart_all()
            self.reload_tree()

    def _add_tag(self):
        if not self.selected_conn:
            QMessageBox.warning(self, "Внимание", "Сначала выберите модуль подключения в левом дереве!")
            return

        dlg = TagEditDialog(
            conn_id=self.selected_conn.id,
            current_group=self.selected_group or "Общие",
            existing_groups=self._get_existing_groups(),
            driver_type=self.selected_conn.driver_type,
            parent=self
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_tag = dlg.get_tag_data()
            self.db.add_tag(new_tag)
            self._sync_registry()
            self.reload_tree(preserve_group=new_tag.group_name)

    def _edit_tag(self):
        tag = self._selected_tag()
        if tag is None or not isinstance(tag, Tag):
            return
        conn_type = self.selected_conn.driver_type if self.selected_conn else None
        dlg = TagEditDialog(
            conn_id=tag.connection_id,
            current_group=tag.group_name,
            existing_groups=self._get_existing_groups(),
            tag=tag,
            driver_type=conn_type,
            parent=self
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            updated = dlg.get_tag_data()
            self.db.update_tag(updated)
            self._sync_registry()
            self.reload_tree(preserve_group=updated.group_name)

    def _del_tag(self):
        tags = self._selected_tags()
        if not tags:
            return
        if len(tags) == 1:
            msg = f"Удалить переменную '{tags[0].name}'?"
        else:
            names = ", ".join(t.name for t in tags[:8])
            if len(tags) > 8:
                names += f", … (+{len(tags) - 8})"
            msg = f"Удалить выбранные переменные ({len(tags)} шт.)?\n{names}"
        res = QMessageBox.question(self, "Удаление", msg)
        if res != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_tags([t.id for t in tags])
        for t in tags:
            try:
                self.dm.registry.drop(t.id)
            except Exception:
                pass
        self.reload_tree(preserve_group=self.selected_group)

    # ------------------------------------------------------------ copy tag
    @staticmethod
    def _next_name(base: str, taken) -> str:
        """
        Имя клона: 'DB3.DBW0' -> 'DB3.DBW01', 'Feed2' -> 'Feed3'.
        Если хвост уже занят числом — берём следующее свободное.
        """
        m = re.search(r"(\d+)$", base)
        stem = base[:m.start()] if m else base
        nxt = int(m.group(1)) + 1 if m else 1
        name = f"{stem}{nxt}"
        while name in taken:
            nxt += 1
            name = f"{stem}{nxt}"
        return name

    @staticmethod
    def _next_address(address: str, data_type: str, conn) -> str:
        """
        Адрес клона: следующий элемент того же типа.
        Modbus — целочисленный регистр (+1, у FLOAT две ячейки),
        S7 — смещение на размер чтения (DB1.DBW4 -> DB1.DBW6, VW100 -> VW102,
        бит -> следующий).
        """
        addr = (address or "").strip()

        if addr.lstrip("-").isdigit():
            step = 2 if (data_type or "").upper() == "FLOAT" else 1
            return str(int(addr) + step)

        # S7: DB1.DBW4 / DB1.DBX0.0 / MW230 / VW100 / V100.3 / MX3.4
        # V — V-память S7-200 SMART; бит у SMART пишут без буквы X (V100.3)
        m = re.fullmatch(r"(?:DB(\d+)\.DB([WBXD])(\d+)|([MVIEQ])([WBXD]?)(\d+))(?:\.(\d+))?",
                         addr.upper().replace("%", "").replace(" ", ""))
        if not m:
            return addr
        bit = int(m.group(7) or 0)
        size_letter = (m.group(2) or m.group(5)
                       or {"FLOAT": "D", "DWORD": "D", "BOOL": "X",
                          "BYTE": "B"}.get((data_type or "").upper(), "W"))
        byte_len = {"W": 2, "B": 1, "D": 4, "X": 1}[size_letter]

        if size_letter == "X":
            if bit < 7:
                return addr[:len(addr) - 1] + str(bit + 1)
            bit = 0

        if m.group(1):  # форма DB<n>.DBx<offset>
            prefix = f"DB{m.group(1)}.DB{size_letter}{int(m.group(3)) + byte_len}"
            return f"{prefix}.{bit}" if size_letter == "X" else prefix

        # форма <область><размер><смещение> (V для SMART — без буквы X у бита)
        area = m.group(4)
        if area == "V" and size_letter == "X":
            return f"{area}{int(m.group(6)) + byte_len}.{bit}"
        prefix = f"{area}{size_letter}{int(m.group(6)) + byte_len}"
        return f"{prefix}.{bit}" if size_letter == "X" else prefix

        # форма <область><размер><смещение>
        prefix = f"{m.group(4)}{size_letter}{int(m.group(6)) + byte_len}"
        return f"{prefix}.{bit}" if size_letter == "X" else prefix

    def _copy_last_tag(self):
        """Дублирует выбранную (или последнюю) переменную со сдвигом адреса и имени."""
        if not self.selected_conn:
            QMessageBox.warning(self, "Внимание", "Сначала выберите модуль подключения в левом дереве!")
            return
        if not self.current_tags:
            QMessageBox.warning(self, "Внимание", "В текущем списке нет ни одной переменной!")
            return

        source = self._selected_tag() or self.current_tags[-1]
        taken = {t.name for t in self.db.get_tags_by_connection(self.selected_conn.id)}
        clone = Tag(
            id=0,
            connection_id=self.selected_conn.id,
            name=self._next_name(source.name, taken),
            address_str=self._next_address(source.address_str, source.data_type, self.selected_conn),
            data_type=source.data_type,
            scale=source.scale,
            offset_val=source.offset_val,
            unit=source.unit,
            group_name=source.group_name,
            log_interval_ms=source.log_interval_ms,
            deadband=source.deadband,
        )
        try:
            self.db.add_tag(clone)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось создать копию: {exc}")
            return
        self._sync_registry()
        self.reload_tree(preserve_group=self.selected_group)
        if self.selected_group is None or clone.group_name == self.selected_group:
            self.reload_tags(self.selected_conn.id, group_filter=self.selected_group)
