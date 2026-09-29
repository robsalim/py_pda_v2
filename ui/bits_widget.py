from datetime import datetime, timedelta
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QComboBox, QPushButton, QDateTimeEdit
)
from PyQt6.QtCore import Qt, QTimer, QDateTime, QEvent

import numpy as np

from database.db_service import DatabaseService
from ui.chart_widget import DateAxisItem, make_step_curve

class BitsWidget(QWidget):
    def __init__(self, db_service: DatabaseService, parent=None):
        super().__init__(parent)
        self.db = db_service
        self.bit_names = {}
        self.is_paused = False
        self.user_is_zoomed = False
        self._cursors_initialized = False

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

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        label_style = "color: #FFFFFF; font-weight: bold; font-size: 12px;"
        combo_style = """
            QComboBox { background-color: #2D2D30; color: #FFFFFF; border: 1px solid #55555A; border-radius: 4px; padding: 4px 8px; font-size: 12px; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background-color: #252526; color: #FFFFFF; selection-background-color: #007ACC; selection-color: #FFFFFF; }
        """
        btn_style = """
            QPushButton { background-color: #49657A; color: #FFFFFF; border: 1px solid #55555A; border-radius: 4px; padding: 4px 10px; font-size: 12px; font-weight: bold; }
            QPushButton:hover { background-color: #5A778D; border-color: #718A99; }
        """

        header1 = QHBoxLayout()
        lbl_sig = QLabel("Сигнал:")
        lbl_sig.setStyleSheet(label_style)
        header1.addWidget(lbl_sig)

        self.combo_signal = QComboBox()
        self.combo_signal.setStyleSheet(combo_style)
        self._reload_signals()
        self.combo_signal.currentIndexChanged.connect(lambda: self.update_bit_chart(force_fit=True))
        header1.addWidget(self.combo_signal)

        header1.addSpacing(10)
        lbl_m = QLabel("Режим:")
        lbl_m.setStyleSheet(label_style)
        header1.addWidget(lbl_m)

        self.combo_mode = QComboBox()
        self.combo_mode.setStyleSheet(combo_style)
        self.combo_mode.addItems(["Live", "Archive"])
        self.combo_mode.currentTextChanged.connect(self._on_mode_changed)
        header1.addWidget(self.combo_mode)

        self.btn_pause = QPushButton("⏸ Пауза")
        self.btn_pause.setStyleSheet(btn_style)
        self.btn_pause.clicked.connect(self._toggle_pause)
        header1.addWidget(self.btn_pause)

        header1.addSpacing(14)
        self.lbl_x1 = QLabel("X1: —")
        self.lbl_x1.setStyleSheet("color: #FFD700; font-weight: bold; font-size: 12px;")
        header1.addWidget(self.lbl_x1)

        self.lbl_x2 = QLabel("X2: —")
        self.lbl_x2.setStyleSheet("color: #00E5FF; font-weight: bold; font-size: 12px;")
        header1.addWidget(self.lbl_x2)

        self.lbl_dt = QLabel("ΔT: —")
        self.lbl_dt.setStyleSheet("color: #00E676; font-weight: bold; font-size: 12px;")
        header1.addWidget(self.lbl_dt)
        header1.addStretch()

        self.btn_reset_zoom = QPushButton("🔍 100%")
        self.btn_reset_zoom.setStyleSheet(btn_style)
        self.btn_reset_zoom.clicked.connect(self._reset_zoom)
        header1.addWidget(self.btn_reset_zoom)
        layout.addLayout(header1)

        self.header2 = QHBoxLayout()
        self.lbl_span = QLabel("Окно Live:")
        self.lbl_span.setStyleSheet(label_style)
        self.combo_span = QComboBox()
        self.combo_span.setStyleSheet(combo_style)
        self.combo_span.addItems(["15 мин", "1 час", "4 часа", "12 часов", "24 часа", "72 часа"])
        self.combo_span.setCurrentText("24 часа")
        self.combo_span.currentTextChanged.connect(lambda: self.update_bit_chart(force_fit=True))
        self.header2.addWidget(self.lbl_span)
        self.header2.addWidget(self.combo_span)

        self.lbl_arch_start = QLabel("Начало:")
        self.lbl_arch_start.setStyleSheet(label_style)
        self.dt_start = QDateTimeEdit()
        self.dt_start.setStyleSheet("""
            QDateTimeEdit { background-color: #2D2D30; color: #FFFFFF; border: 1px solid #55555A; border-radius: 4px; padding: 4px 6px; font-size: 12px; }
            QCalendarWidget QWidget { background-color: #252526; color: #FFFFFF; }
        """)
        self.dt_start.setDisplayFormat("dd.MM.yyyy HH:mm:ss")
        self.dt_start.setCalendarPopup(True)
        self.dt_start.setDateTime(QDateTime.currentDateTime().addSecs(-86400))

        self.lbl_arch_dur = QLabel("Длительность:")
        self.lbl_arch_dur.setStyleSheet(label_style)
        self.combo_duration = QComboBox()
        self.combo_duration.setStyleSheet(combo_style)
        self.combo_duration.addItems(["15 мин", "1 час", "4 часа", "12 часов", "24 часа", "72 часа"])
        self.combo_duration.setCurrentText("24 часа")

        self.btn_nav_prev = QPushButton("◀")
        self.btn_nav_prev.setStyleSheet(btn_style)
        self.btn_nav_prev.clicked.connect(lambda: self._shift_archive_window(-1))

        self.btn_nav_next = QPushButton("▶")
        self.btn_nav_next.setStyleSheet(btn_style)
        self.btn_nav_next.clicked.connect(lambda: self._shift_archive_window(1))

        self.btn_load_archive = QPushButton("📥 Загрузить архив")
        self.btn_load_archive.setStyleSheet("""
            QPushButton { background-color: #49657A; color: #FFFFFF; font-weight: bold; border-radius: 4px; padding: 5px 14px; font-size: 12px; border: 1px solid #5A778D; }
            QPushButton:hover { background-color: #5A778D; }
        """)
        self.btn_load_archive.clicked.connect(lambda: self.update_bit_chart(force_fit=True))

        for w in [self.lbl_arch_start, self.dt_start, self.lbl_arch_dur, self.combo_duration,
                 self.btn_nav_prev, self.btn_nav_next, self.btn_load_archive]:
            self.header2.addWidget(w)
            w.hide()

        self.header2.addStretch()
        layout.addLayout(self.header2)

        self.date_axis = DateAxisItem(orientation='bottom')
        self.date_axis.setPen(pg.mkPen(color='#858585', width=1))
        self.date_axis.setTextPen(pg.mkPen(color='#D8D8D8'))

        self.plot_widget = pg.PlotWidget(axisItems={'bottom': self.date_axis})
        self.plot_widget.setBackground('#181818')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.35)
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
        y_ax.setPen(pg.mkPen(color='#858585', width=1))
        y_ax.setTextPen(pg.mkPen(color='#D8D8D8'))

        self.cursor_x1 = pg.InfiniteLine(
            angle=90, movable=True,
            pen=pg.mkPen('#FFD700', width=1.8, style=Qt.PenStyle.DashLine),
            hoverPen=pg.mkPen('#FFE600', width=2.5)
        )
        self.cursor_x2 = pg.InfiniteLine(
            angle=90, movable=True,
            pen=pg.mkPen('#00E5FF', width=1.8, style=Qt.PenStyle.DashLine),
            hoverPen=pg.mkPen('#80F3FF', width=2.5)
        )
        self.cursor_x1.sigPositionChanged.connect(self._update_cursor_labels)
        self.cursor_x2.sigPositionChanged.connect(self._update_cursor_labels)
        self.cursor_x1.setZValue(100)
        self.cursor_x2.setZValue(100)

        self.plot_widget.addItem(self.cursor_x1, ignoreBounds=True)
        self.plot_widget.addItem(self.cursor_x2, ignoreBounds=True)


        self.curves = []
        colors = [
            '#FF6384', '#4BC0C0', '#FFCE56', '#36A2EB', '#FF9F40', '#9966FF',
            '#C9CBCF', '#00E676', '#E91E63', '#00BCD4', '#FF5722', '#8BC34A',
            '#FFEB3B', '#9C27B0', '#795548', '#607D8B'
        ]
        for i in range(16):
            c = self.plot_widget.plot(pen=pg.mkPen(color=colors[i], width=1.5))
            self.curves.append(c)

        layout.addWidget(self.plot_widget)

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
            self.btn_pause.setStyleSheet("background-color: #466653; color: white; font-weight: bold; border-radius: 4px; padding: 4px 10px;")
        else:
            self.btn_pause.setText("⏸ Пауза")
            self.btn_pause.setStyleSheet("background-color: #49657A; color: white; font-weight: bold; border-radius: 4px; padding: 4px 10px;")
            self.update_bit_chart()

    def _on_tick(self):
        if not self.is_paused and self.combo_mode.currentText() == "Live":
            self.update_bit_chart(force_fit=False)

    def _reload_signals(self):
        tags = self.db.get_all_tags()
        self.combo_signal.clear()
        for t in tags:
            self.combo_signal.addItem(t.name, t.id)
        idx = self.combo_signal.findText("BIT")
        if idx >= 0:
            self.combo_signal.setCurrentIndex(idx)

    def update_bit_chart(self, force_fit: bool = False):
        tag_id = self.combo_signal.currentData()
        if tag_id is None:
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

        for bit in range(16):
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
