from datetime import datetime, timedelta
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QPushButton, QDateTimeEdit,
    QTreeWidget, QTreeWidgetItem, QSplitter,
)
from PyQt6.QtCore import Qt, QTimer, QDateTime, QEvent

import numpy as np

from database.db_service import DatabaseService
from ui.chart_widget import DateAxisItem, make_step_curve
from ui import theme
from ui.theme import S

# Целочисленные типы шириной 8-16 бит, которые осмысленно раскладывать на биты
BIT_DATA_TYPES = {"BYTE", "INT16", "UINT16"}


def is_bit_tag(data_type: str) -> bool:
    """Проверяет, что тип сигнала разложен на биты (8-16 битный целочисленный)."""
    return (data_type or "").upper() in BIT_DATA_TYPES

class BitsWidget(QWidget):
    def __init__(self, db_service: DatabaseService, parent=None):
        super().__init__(parent)
        self.db = db_service
        self.bit_names = {}
        self.is_paused = False
        self.user_is_zoomed = False
        self._cursors_initialized = False
        self._selected_tag_id = None
        self._bit_count = 16

        self._load_bit_names()
        self._init_ui()

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start()

    def _load_bit_names(self):
        tags = self.db.get_configured_tags()
        self.bit_names = {}
        for t in tags:
            try:
                addr = int(t.address_str)
                if addr <= 8:
                    self.bit_names[addr] = f"B{addr}: {t.name}"
            except Exception:
                pass
        for b in range(16):
            if b not in self.bit_names:
                self.bit_names[b] = f"Bit {b}"

    def refresh_tags(self):
        """
        Перечитать список сигналов после изменений на вкладке I/O.
        Вызывается по сигналу IOWidget.tags_changed — иначе новый тег
        появляется в дереве I/O, но в дереве Bits его нет.
        """
        self._load_bit_names()
        self._reload_signal_tree()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        label_style = f"color: {S('text')}; font-weight: bold; font-size: 12px;"
        combo_style = theme.COMBO_QSS
        btn_style = theme.ACTION_BTN_QSS

        header1 = QHBoxLayout()
        self.lbl_current = QLabel("Регистр: —")
        theme.themed(self.lbl_current, label_style)
        header1.addWidget(self.lbl_current)
        header1.addSpacing(20)

        lbl_m = QLabel("Режим:")
        theme.themed(lbl_m, label_style)
        header1.addWidget(lbl_m)

        self.combo_mode = QComboBox()
        theme.themed(self.combo_mode, combo_style)
        self.combo_mode.addItems(["Live", "Archive"])
        self.combo_mode.currentTextChanged.connect(self._on_mode_changed)
        header1.addWidget(self.combo_mode)

        self.btn_pause = QPushButton("⏸ Пауза")
        self._pause_tpl = btn_style
        self._restyle_pause = theme.themed(self.btn_pause, lambda p: theme.fill(self._pause_tpl, p))
        self.btn_pause.clicked.connect(self._toggle_pause)
        header1.addWidget(self.btn_pause)

        header1.addSpacing(14)
        self.lbl_x1 = QLabel("X1: —")
        theme.themed(self.lbl_x1, f"color: {S('cursor_x1')}; font-weight: bold; font-size: 12px;")
        header1.addWidget(self.lbl_x1)

        self.lbl_x2 = QLabel("X2: —")
        theme.themed(self.lbl_x2, f"color: {S('cursor_x2')}; font-weight: bold; font-size: 12px;")
        header1.addWidget(self.lbl_x2)

        self.lbl_dt = QLabel("ΔT: —")
        theme.themed(self.lbl_dt, f"color: {S('cursor_dt')}; font-weight: bold; font-size: 12px;")
        header1.addWidget(self.lbl_dt)
        header1.addStretch()

        self.btn_reset_zoom = QPushButton("🔍 100%")
        theme.themed(self.btn_reset_zoom, btn_style)
        self.btn_reset_zoom.clicked.connect(self._reset_zoom)
        header1.addWidget(self.btn_reset_zoom)
        layout.addLayout(header1)

        self.header2 = QHBoxLayout()
        self.lbl_span = QLabel("Окно Live:")
        theme.themed(self.lbl_span, label_style)
        self.combo_span = QComboBox()
        theme.themed(self.combo_span, combo_style)
        self.combo_span.addItems(["15 мин", "1 час", "4 часа", "12 часов", "24 часа", "72 часа"])
        self.combo_span.setCurrentText("24 часа")
        self.combo_span.currentTextChanged.connect(lambda: self.update_bit_chart(force_fit=True))
        self.header2.addWidget(self.lbl_span)
        self.header2.addWidget(self.combo_span)

        self.lbl_arch_start = QLabel("Начало:")
        theme.themed(self.lbl_arch_start, label_style)
        self.dt_start = QDateTimeEdit()
        theme.themed(self.dt_start, theme.DTEDIT_QSS)
        self.dt_start.setDisplayFormat("dd.MM.yyyy HH:mm:ss")
        self.dt_start.setCalendarPopup(True)
        self.dt_start.setDateTime(QDateTime.currentDateTime().addSecs(-86400))

        self.lbl_arch_dur = QLabel("Длительность:")
        theme.themed(self.lbl_arch_dur, label_style)
        self.combo_duration = QComboBox()
        theme.themed(self.combo_duration, combo_style)
        self.combo_duration.addItems(["15 мин", "1 час", "4 часа", "12 часов", "24 часа", "72 часа"])
        self.combo_duration.setCurrentText("24 часа")

        self.btn_nav_prev = QPushButton("◀")
        theme.themed(self.btn_nav_prev, btn_style)
        self.btn_nav_prev.clicked.connect(lambda: self._shift_archive_window(-1))

        self.btn_nav_next = QPushButton("▶")
        theme.themed(self.btn_nav_next, btn_style)
        self.btn_nav_next.clicked.connect(lambda: self._shift_archive_window(1))

        self.btn_load_archive = QPushButton("📥 Загрузить архив")
        theme.themed(self.btn_load_archive, theme.LOAD_BTN_QSS)
        self.btn_load_archive.clicked.connect(lambda: self.update_bit_chart(force_fit=True))

        for w in [self.lbl_arch_start, self.dt_start, self.lbl_arch_dur, self.combo_duration,
                 self.btn_nav_prev, self.btn_nav_next, self.btn_load_archive]:
            self.header2.addWidget(w)
            w.hide()

        self.header2.addStretch()
        layout.addLayout(self.header2)

        self.date_axis = DateAxisItem(orientation='bottom')

        self.plot_widget = pg.PlotWidget(axisItems={'bottom': self.date_axis})
        self.plot_widget.setMouseEnabled(x=True, y=False)
        self.plot_widget.enableAutoRange(axis='y')
        self.plot_widget.setLimits(yMin=-0.5, yMax=16.5)

        view_box = self.plot_widget.getViewBox()
        view_box.setMouseMode(pg.ViewBox.RectMode)
        view_box.disableAutoRange(pg.ViewBox.XAxis)
        # Ограничиваем увеличение: примерно 10 мс на деление при 10 делениях.
        view_box.setLimits(minXRange=0.1)
        self.plot_widget.sigRangeChangedManually.connect(self._on_user_interaction)
        self.plot_widget.scene().sigMouseClicked.connect(self._on_plot_clicked)

        ticks = [[(i, self.bit_names.get(i, f"Bit {i}")) for i in range(16)]]
        y_ax = self.plot_widget.getAxis('left')
        y_ax.setTicks(ticks)

        self.cursor_x1 = pg.InfiniteLine(
            angle=90, movable=True,
            pen=pg.mkPen('k', width=1.8, style=Qt.PenStyle.DashLine))
        self.cursor_x2 = pg.InfiniteLine(
            angle=90, movable=True,
            pen=pg.mkPen('k', width=1.8, style=Qt.PenStyle.DashLine))
        self.cursor_x1.sigPositionChanged.connect(self._update_cursor_labels)
        self.cursor_x2.sigPositionChanged.connect(self._update_cursor_labels)
        self.cursor_x1.setZValue(100)
        self.cursor_x2.setZValue(100)

        self.plot_widget.addItem(self.cursor_x1, ignoreBounds=True)
        self.plot_widget.addItem(self.cursor_x2, ignoreBounds=True)

        # Цвета pyqtgraph из палитры, перекрашиваются при смене темы
        theme.on_theme(self._apply_plot_theme)
        self._apply_plot_theme(theme.current())

        self.curves = []
        colors = [
            '#FF6384', '#4BC0C0', '#FFCE56', '#36A2EB', '#FF9F40', '#9966FF',
            '#C9CBCF', '#00E676', '#E91E63', '#00BCD4', '#FF5722', '#8BC34A',
            '#FFEB3B', '#9C27B0', '#795548', '#607D8B'
        ]
        for i in range(16):
            c = self.plot_widget.plot(pen=pg.mkPen(color=colors[i], width=1.5))
            self.curves.append(c)

        # Дерево сигналов (только 8-16 битные целые: BYTE/INT16/UINT16)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        left_panel = QWidget()
        lp_layout = QVBoxLayout(left_panel)
        lp_layout.setContentsMargins(0, 0, 0, 0)
        lbl_tree = QLabel("<b>Дерево сигналов (8-16 бит):</b>")
        lp_layout.addWidget(lbl_tree)
        self.tag_tree = QTreeWidget()
        self.tag_tree.setHeaderHidden(True)
        self.tag_tree.currentItemChanged.connect(self._on_tree_selection)
        lp_layout.addWidget(self.tag_tree)
        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(self.plot_widget)
        self.splitter.setSizes([260, 1000])
        layout.addWidget(self.splitter)
        self._reload_signal_tree()


    def _apply_plot_theme(self, p):
        """Перекрашивает фон, оси и курсоры бит-графика под палитру p."""
        self.plot_widget.setBackground(p.plot_bg)
        self.plot_widget.showGrid(x=True, y=True, alpha=p.grid_alpha)
        axis_pen = pg.mkPen(color=p.plot_axis, width=1)
        text_pen = pg.mkPen(color=p.plot_text)
        self.date_axis.setPen(axis_pen)
        self.date_axis.setTextPen(text_pen)
        left_axis = self.plot_widget.getAxis('left')
        left_axis.setPen(axis_pen)
        left_axis.setTextPen(text_pen)
        self.cursor_x1.setPen(pg.mkPen(p.cursor_x1, width=1.8, style=Qt.PenStyle.DashLine))
        self.cursor_x1.setHoverPen(pg.mkPen(p.cursor_x1_hover, width=2.5))
        self.cursor_x2.setPen(pg.mkPen(p.cursor_x2, width=1.8, style=Qt.PenStyle.DashLine))
        self.cursor_x2.setHoverPen(pg.mkPen(p.cursor_x2_hover, width=2.5))

    def _parse_span_text(self, text: str) -> timedelta:
        t = str(text).strip()
        if "15" in t and "мин" in t: return timedelta(minutes=15)
        if "1" in t and "час" in t and "12" not in t: return timedelta(hours=1)
        if "4" in t and "час" in t and "24" not in t: return timedelta(hours=4)
        if "12" in t: return timedelta(hours=12)
        if "72" in t: return timedelta(hours=72)
        if "24" in t: return timedelta(hours=24)
        return timedelta(hours=24)

    def _on_mode_changed(self, mode: str):
        is_live = (mode == "Live")
        self.lbl_span.setVisible(is_live)
        self.combo_span.setVisible(is_live)
        self.btn_pause.setVisible(is_live)
        self.user_is_zoomed = False

        is_archive = not is_live
        for w in [self.lbl_arch_start, self.dt_start, self.lbl_arch_dur, self.combo_duration,
                 self.btn_nav_prev, self.btn_nav_next, self.btn_load_archive]:
            w.setVisible(is_archive)

        if is_live:
            self.timer.start()
            self.update_bit_chart(force_fit=True)
        else:
            self.timer.stop()
            self.update_bit_chart(force_fit=True)

    def _shift_archive_window(self, direction: int):
        dur = self._parse_span_text(self.combo_duration.currentText())
        curr_py_dt = self.dt_start.dateTime().toPyDateTime()
        next_py_dt = curr_py_dt + (dur * direction)
        self.dt_start.setDateTime(QDateTime(next_py_dt))
        self.user_is_zoomed = False
        self.update_bit_chart(force_fit=True)

    def _format_time(self, ts: float) -> str:
        if ts <= 0: return "—"
        try:
            dt = datetime.fromtimestamp(ts)
            timestamp = dt.strftime("%d.%m %H:%M:%S")
            milliseconds = dt.microsecond // 1000
            return f"{timestamp}.{milliseconds:03d}" if milliseconds else timestamp
        except Exception:
            return "—"

    def _update_cursor_labels(self):
        t1 = self.cursor_x1.value()
        t2 = self.cursor_x2.value()
        self.lbl_x1.setText(f"X1: {self._format_time(t1)}")
        self.lbl_x2.setText(f"X2: {self._format_time(t2)}")
        if t1 > 0 and t2 > 0:
            dt = abs(t2 - t1)
            total_sec = int(dt)
            ms = int((dt - total_sec) * 1000)
            hh = total_sec // 3600
            mm = (total_sec % 3600) // 60
            ss = total_sec % 60
            if hh > 0: self.lbl_dt.setText(f"ΔT: {hh}ч {mm:02d}м {ss:02d}с {ms:03d}мс")
            else: self.lbl_dt.setText(f"ΔT: {mm}м {ss:02d}с {ms:03d}мс")
        else:
            self.lbl_dt.setText("ΔT: —")

    def _on_user_interaction(self):
        self.user_is_zoomed = True

    def _reset_zoom(self):
        self.user_is_zoomed = False
        self.update_bit_chart(force_fit=True)


    def _on_plot_clicked(self, event):
        view_box = self.plot_widget.getViewBox()


        if not view_box.sceneBoundingRect().contains(event.scenePos()):
            return
        if event.button() == Qt.MouseButton.LeftButton:
            cursor = self.cursor_x2 if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier) else self.cursor_x1
        elif event.button() == Qt.MouseButton.MiddleButton:
            cursor = self.cursor_x2
        else:
            return
        cursor.setValue(view_box.mapSceneToView(event.scenePos()).x())
        self._update_cursor_labels()


    def _toggle_pause(self):
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.btn_pause.setText("▶ Продолжить")
            self._pause_tpl = theme.btn_qss("add")
        else:
            self.btn_pause.setText("⏸ Пауза")
            self._pause_tpl = theme.ACTION_BTN_QSS
        self._restyle_pause()
        if not self.is_paused:
            self.update_bit_chart()

    def _on_tick(self):
        if not self.is_paused and self.combo_mode.currentText() == "Live":
            self.update_bit_chart(force_fit=False)

    def _reload_signal_tree(self):
        """Пересобирает дерево: только подключения/группы и 8-16 битные целые теги."""
        keep_id = self._selected_tag_id
        self.tag_tree.blockSignals(True)
        self.tag_tree.clear()
        conns = self.db.get_all_connections()
        all_tags = self.db.get_all_tags()
        first_item = None
        for c in conns:
            c_tags = [t for t in all_tags
                      if t.connection_id == c.id and is_bit_tag(t.data_type)]
            if not c_tags:
                continue
            conn_item = QTreeWidgetItem([f"🔌 {c.name}"])
            groups_dict = {}
            for t in c_tags:
                groups_dict.setdefault(t.group_name or "Общие", []).append(t)
            for g_name, g_tags in sorted(groups_dict.items()):
                group_item = QTreeWidgetItem([f"📁 {g_name} ({len(g_tags)})"])
                for t in g_tags:
                    label = f"{t.name} [{t.data_type}]"
                    tag_item = QTreeWidgetItem([label])
                    tag_item.setData(0, Qt.ItemDataRole.UserRole, t.id)
                    tag_item.setData(0, Qt.ItemDataRole.UserRole + 1, t.data_type.upper())
                    group_item.addChild(tag_item)
                    if first_item is None:
                        first_item = tag_item
                    if keep_id is not None and t.id == keep_id:
                        first_item = tag_item
                conn_item.addChild(group_item)
                group_item.setExpanded(True)
            self.tag_tree.addTopLevelItem(conn_item)
            conn_item.setExpanded(True)
        self.tag_tree.blockSignals(False)
        if first_item is not None:
            self.tag_tree.setCurrentItem(first_item)   # вызовет _on_tree_selection
        else:
            self._set_selected_tag(None, None)
            self.update_bit_chart(force_fit=True)

    def _on_tree_selection(self, current, _previous):
        if current is None:
            return
        tag_id = current.data(0, Qt.ItemDataRole.UserRole)
        if tag_id is None:      # выбран узел подключения/группы — игнорируем
            return
        self._set_selected_tag(tag_id, current.data(0, Qt.ItemDataRole.UserRole + 1))
        self.update_bit_chart(force_fit=True)

    def _set_selected_tag(self, tag_id, data_type):
        """Запоминает выбранный регистр и перестраивает дорожки под его ширину."""
        self._selected_tag_id = tag_id
        self._bit_count = 8 if data_type == "BYTE" else 16
        name = "—"
        if tag_id is not None:
            t = next((x for x in self.db.get_all_tags() if x.id == tag_id), None)
            if t:
                name = f"{t.name} ({t.data_type})"
        self.lbl_current.setText(f"Регистр: {name}")
        ticks = [[(i, self.bit_names.get(i, f"Bit {i}")) for i in range(self._bit_count)]]
        self.plot_widget.getAxis('left').setTicks(ticks)
        self.plot_widget.setLimits(yMin=-0.5, yMax=self._bit_count + 0.5)
        for i, curve in enumerate(self.curves):
            curve.setVisible(i < self._bit_count)


    def update_bit_chart(self, force_fit: bool = False):
        tag_id = self._selected_tag_id
        if tag_id is None:
            for c in self.curves:
                c.setData([], [])
            return

        is_live = (self.combo_mode.currentText() == "Live")
        if is_live:
            delta = self._parse_span_text(self.combo_span.currentText())
            points, t_start, t_end = self.db.get_data_points_by_data_span(tag_id, delta)
        else:
            start_dt = self.dt_start.dateTime().toPyDateTime()
            end_dt = start_dt + self._parse_span_text(self.combo_duration.currentText())
            points = self.db.get_data_points_range(tag_id, start_dt, end_dt)
            t_start = start_dt.timestamp()
            t_end = end_dt.timestamp()

        if not points:
            for c in self.curves: c.setData([], [])
            return

        times = np.array([p.timestamp.timestamp() for p in points])
        raw_values = np.array([int(p.value) for p in points], dtype=np.uint16)

        for bit in range(self._bit_count):
            bit_val = ((raw_values >> bit) & 1).astype(float)
            y_track = bit + (bit_val * 0.75)
            step_x, step_y = make_step_curve(times, y_track)
            self.curves[bit].setData(step_x, step_y)

        if not self.user_is_zoomed or force_fit:
            self.plot_widget.blockSignals(True)
            self.plot_widget.setXRange(t_start, t_end, padding=0)
            self.plot_widget.blockSignals(False)
            if not self._cursors_initialized or force_fit:
                span = t_end - t_start
                self.cursor_x1.setValue(t_start + span * 0.05)
                self.cursor_x2.setValue(t_end - span * 0.05)
                self._update_cursor_labels()
                self._cursors_initialized = True
