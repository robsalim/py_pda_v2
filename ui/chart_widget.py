from datetime import datetime, timedelta
from typing import List, Dict
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QFrame, QDateTimeEdit, QFileDialog
)



from PyQt6.QtCore import Qt, QTimer, QDateTime, QEvent, QRectF
from PyQt6.QtGui import (
    QPainter, QColor, QPdfWriter, QPageSize, QPageLayout
)
import numpy as np

from database.db_service import DatabaseService
from models.tag import Tag

def make_step_curve(times: np.ndarray, values: np.ndarray):
    if len(times) == 0:
        return np.array([]), np.array([])
    if len(times) == 1:
        return times, values

    # NaN разрывает линию pyqtgraph, поэтому пропуски не выглядят как непрерывный сигнал.
    gaps = np.diff(times)
    typical_step = float(np.median(gaps)) if len(gaps) else 0.0
    gap_limit = max(typical_step * 5, 60.0)
    segments = []
    start = 0
    for end in np.flatnonzero(gaps > gap_limit) + 1:
        segments.append((times[start:end], values[start:end]))
        start = end
    segments.append((times[start:], values[start:]))

    step_times = []
    step_values = []
    for index, (segment_times, segment_values) in enumerate(segments):
        segment_x, segment_y = _make_step_segment(segment_times, segment_values)
        if index:
            step_times.append(np.array([np.nan]))
            step_values.append(np.array([np.nan]))
        step_times.append(segment_x)
        step_values.append(segment_y)
    return np.concatenate(step_times), np.concatenate(step_values)


def _make_step_segment(times: np.ndarray, values: np.ndarray):
    if len(times) == 1:
        return times, values
    step_times = np.empty(2 * len(times) - 1, dtype=times.dtype)
    step_values = np.empty(2 * len(values) - 1, dtype=values.dtype)
    step_times[0] = times[0]
    step_times[1::2] = times[1:]
    step_times[2::2] = times[1:]
    step_values[0::2] = values
    step_values[1::2] = values[:-1]
    return step_times, step_values

class DateAxisItem(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        strings = []
        for val in values:
            try:
                dt = datetime.fromtimestamp(val)
                if spacing < 60:
                    timestamp = dt.strftime("%H:%M:%S")
                    milliseconds = dt.microsecond // 1000
                    strings.append(f"{timestamp}.{milliseconds:03d}" if milliseconds else timestamp)
                elif spacing > 43200:
                    strings.append(dt.strftime("%d.%m %H:%M"))
                else:
                    strings.append(dt.strftime("%H:%M:%S"))
            except Exception:
                strings.append("")
        return strings

class ChartWidget(QWidget):
    def __init__(self, title: str, db_service: DatabaseService, on_close_callback=None, parent=None):
        super().__init__(parent)
        self.title = title
        self.db = db_service
        self.on_close_callback = on_close_callback
        self.setMinimumHeight(260)
        self.active_tags: Dict[int, Dict] = {}
        self.user_is_zoomed = False
        self.is_paused = False

        self.setAcceptDrops(True)
        self._init_ui()

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._on_live_tick)
        self.timer.start()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        label_style = "color: #FFFFFF; font-weight: bold; font-size: 12px;"
        combo_style = """
            QComboBox { background-color: #2D2D30; color: #FFFFFF; border: 1px solid #55555A; border-radius: 4px; padding: 4px 8px; font-size: 12px; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background-color: #252526; color: #FFFFFF; selection-background-color: #007ACC; selection-color: #FFFFFF; }
        """
        btn_style = """
            QPushButton { background-color: #49657A; color: #FFFFFF; border: 1px solid #60A5FA; border-radius: 4px; padding: 4px 10px; font-size: 12px; font-weight: bold; }
            QPushButton:hover { background-color: #5A778D; border-color: #718A99; }
        """

        header1 = QHBoxLayout()
        self.lbl_title = QLabel(f"<b>{self.title}</b>")
        self.lbl_title.setStyleSheet("color: #FFFFFF; font-size: 13px;")
        header1.addWidget(self.lbl_title)
        header1.addSpacing(10)
        lbl_mode = QLabel("Режим:")
        lbl_mode.setStyleSheet(label_style)
        header1.addWidget(lbl_mode)

        self.combo_mode = QComboBox()
        self.combo_mode.setStyleSheet(combo_style)
        self.combo_mode.addItems(["Live", "Archive"])
        self.combo_mode.currentTextChanged.connect(self._on_mode_changed)
        header1.addWidget(self.combo_mode)

        self.btn_pause = QPushButton("⏸ Пауза")
        self.btn_pause.setStyleSheet(btn_style)
        self.btn_pause.clicked.connect(self._toggle_pause)
        header1.addWidget(self.btn_pause)

        self.lbl_live_span = QLabel("Окно Live:")
        self.lbl_live_span.setStyleSheet(label_style)
        self.combo_live_span = QComboBox()
        self.combo_live_span.setStyleSheet(combo_style)
        self.combo_live_span.addItems(["15 мин", "1 час", "4 часа", "12 часов", "24 часа", "72 часа"])
        self.combo_live_span.setCurrentText("24 часа")
        self.combo_live_span.currentTextChanged.connect(self._on_span_changed)
        header1.addWidget(self.lbl_live_span)
        header1.addWidget(self.combo_live_span)

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

        self.chips_container = QHBoxLayout()
        header1.addLayout(self.chips_container)
        header1.addStretch()

        self.btn_reset_zoom = QPushButton("🔍 100%")
        self.btn_reset_zoom.setStyleSheet(btn_style)
        self.btn_reset_zoom.clicked.connect(self._reset_zoom)
        header1.addWidget(self.btn_reset_zoom)

        self.btn_clear = QPushButton("✕ Очистить")
        self.btn_clear.setStyleSheet(btn_style)
        self.btn_clear.clicked.connect(self.clear_chart)
        header1.addWidget(self.btn_clear)

        self.btn_export_pdf = QPushButton("PDF")
        self.btn_export_pdf.setStyleSheet("background-color: #476B73; color: white; border: 1px solid #64858A; border-radius: 4px; padding: 4px 10px; font-weight: bold;")
        self.btn_export_pdf.setToolTip("Экспорт графика с легендой в PDF")
        self.btn_export_pdf.clicked.connect(self._export_pdf)
        header1.addWidget(self.btn_export_pdf)

        if self.on_close_callback:
            self.btn_close = QPushButton("×")
            self.btn_close.setFixedSize(24, 24)
            self.btn_close.setToolTip("Закрыть график")
            self.btn_close.setStyleSheet("background-color: #7A4B50; color: white; border: 1px solid #956168; border-radius: 4px; font-weight: normal; font-size: 16px; padding: 0;")
            self.btn_close.clicked.connect(self.on_close_callback)
            header1.addWidget(self.btn_close)
        layout.addLayout(header1)

        self.header2 = QHBoxLayout()
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
        self.combo_duration.currentTextChanged.connect(self._on_span_changed)

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
        self.btn_load_archive.clicked.connect(lambda: self.update_data(force_fit=True))

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
        self.legend_panel = QFrame(self.plot_widget)
        self.legend_panel.setStyleSheet("QFrame { background-color: rgba(28, 28, 28, 180); border: 1px solid rgba(130, 130, 130, 80); border-radius: 4px; }")
        self.legend_layout = QVBoxLayout(self.legend_panel)
        self.legend_layout.setContentsMargins(8, 5, 8, 5)
        self.legend_layout.setSpacing(3)
        self.legend_panel.hide()

        y_axis = self.plot_widget.getAxis('left')
        y_axis.setPen(pg.mkPen(color='#858585', width=1))
        y_axis.setTextPen(pg.mkPen(color='#D8D8D8'))

        self.plot_widget.setMouseEnabled(x=True, y=False)
        view_box = self.plot_widget.getViewBox()
        view_box.setMouseMode(pg.ViewBox.RectMode)
        view_box.disableAutoRange(pg.ViewBox.XAxis)
        # Ограничиваем увеличение: примерно 10 мс на деление при 10 делениях.
        view_box.setLimits(minXRange=0.1)
        self.plot_widget.enableAutoRange(axis='y')
        self.plot_widget.setAutoVisible(y=True)
        self.plot_widget.sigRangeChangedManually.connect(self._on_user_interaction)
        self.plot_widget.scene().sigMouseClicked.connect(self._on_plot_clicked)
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

        self.plot_widget.addItem(self.cursor_x1, ignoreBounds=True)
        self.plot_widget.addItem(self.cursor_x2, ignoreBounds=True)

        # Перекрестие под курсором как на вебе: две тонкие линии;
        # значения по Y в точке под курсором выводятся в легенду
        self.cross_v = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen('#9DA5B4', width=1))
        self.cross_h = pg.InfiniteLine(
            angle=0, movable=False,
            pen=pg.mkPen('#9DA5B4', width=1))
        for line in (self.cross_v, self.cross_h):
            line.setZValue(90)
            line.hide()
            self.plot_widget.addItem(line, ignoreBounds=True)
        self._mouse_in_plot = False
        self._hover_x = None
        self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

        layout.addWidget(self.plot_widget, 1)
        self.plot_widget.installEventFilter(self)

        self.resize_handle = QWidget()
        self.resize_handle.setFixedHeight(8)
        self.resize_handle.setCursor(Qt.CursorShape.SizeVerCursor)
        self.resize_handle.setStyleSheet("background-color: #3F596B; border-radius: 3px;")
        self.resize_handle.installEventFilter(self)
        layout.addWidget(self.resize_handle)
        self._update_legend_panel()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "legend_panel"):
            self._position_legend_panel()

    def _position_legend_panel(self):
        self.legend_layout.activate()
        size = self.legend_panel.sizeHint()
        self.legend_panel.setFixedSize(size)
        self.legend_panel.move(12, 12)
        self.legend_panel.raise_()

    def _update_legend_panel(self):
        while self.legend_layout.count():
            item = self.legend_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

        for item in self.active_tags.values():
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            line = QFrame()
            line.setFixedSize(38, 3)
            line.setStyleSheet(f"background-color: {item['color']}; border: none;")
            name = QLabel(item["tag"].name)
            name.setStyleSheet(f"color: {item['color']}; background: transparent; font-weight: normal;")
            # Значение по Y под курсором, как на вебе в легенде uPlot
            val_lbl = QLabel("")
            val_lbl.setMinimumWidth(64)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val_lbl.setStyleSheet(f"color: {item['color']}; background: transparent; font-weight: bold;")
            item["val_lbl"] = val_lbl
            remove_button = QPushButton("×")
            remove_button.setFixedSize(18, 18)
            remove_button.setToolTip("Убрать сигнал с графика")
            remove_button.setStyleSheet("QPushButton { color: #C8C8C8; background: transparent; border: none; padding: 0; font-weight: normal; font-size: 14px; } QPushButton:hover { color: #FFFFFF; }")
            tag_id = item["tag"].id
            remove_button.clicked.connect(lambda checked=False, signal_id=tag_id: self.remove_signal(signal_id))
            row.addWidget(line)
            row.addWidget(name)
            row.addStretch()
            row.addWidget(val_lbl)
            row.addWidget(remove_button)
            self.legend_layout.addWidget(row_widget)

        self.legend_panel.setVisible(bool(self.active_tags))
        self._position_legend_panel()
        QTimer.singleShot(0, self._position_legend_panel)

    def eventFilter(self, watched, event):
        if watched is self.plot_widget and event.type() == QEvent.Type.Leave:
            # курсор ушёл с графика — перекрестие и значения в легенде гаснем
            self._hide_crosshair()
        if watched is getattr(self, "resize_handle", None):
            if event.type() == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._resize_start_y = event.globalPosition().toPoint().y()
                self._resize_start_height = self.height()
                return True
            if event.type() == event.Type.MouseMove and event.buttons() & Qt.MouseButton.LeftButton:
                delta = event.globalPosition().toPoint().y() - self._resize_start_y
                self.setMinimumHeight(max(260, self._resize_start_height + delta))
                self.resize(self.width(), max(260, self._resize_start_height + delta))
                return True
            if event.type() == event.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                return True
        return super().eventFilter(watched, event)

    def _on_user_interaction(self):
        self.user_is_zoomed = True

    # ------------------------------------------------------------- crosshair
    def _on_mouse_moved(self, pos):
        """Перекрестие под курсором + значения по Y в легенде (как на вебе)."""
        view_box = self.plot_widget.getViewBox()
        if not view_box.sceneBoundingRect().contains(pos):
            self._hide_crosshair()
            return
        point = view_box.mapSceneToView(pos)
        self.cross_v.setValue(point.x())
        self.cross_h.setValue(point.y())
        self.cross_v.show()
        self.cross_h.show()
        self._update_hover_values(point.x())

    def _hide_crosshair(self):
        self.cross_v.hide()
        self.cross_h.hide()
        for item in self.active_tags.values():
            lbl = item.get("val_lbl")
            if lbl is not None:
                lbl.setText("")

    def _update_hover_values(self, hover_x: float):
        for item in self.active_tags.values():
            lbl = item.get("val_lbl")
            if lbl is None:
                continue
            times = item.get("times")
            values = item.get("values")
            if times is None or len(times) == 0 or hover_x < times[0]:
                lbl.setText("")
                continue
            # step-кривая с align=1: значение держится до следующей точки
            idx = int(np.searchsorted(times, hover_x, side="right")) - 1
            lbl.setText(self._fmt_hover_value(float(values[idx])))

    @staticmethod
    def _fmt_hover_value(v: float) -> str:
        """
        Компактное значение без экспоненты: .4g превращал INT-регистры
        вида 65535 в «6.554e+04». Целые пишем целиком, дробные — до 3 знаков.
        """
        if abs(v) >= 1 and abs(v - round(v)) < 1e-6:
            return str(int(round(v)))
        return f"{v:.3f}".rstrip("0").rstrip(".")

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
            self.btn_pause.setStyleSheet("background-color: #3E3E42; color: white; font-weight: bold; border-radius: 4px; padding: 4px 10px;")
            self.update_data()

    def _on_span_changed(self):
        self.user_is_zoomed = False
        self.update_data(force_fit=True)

    def _parse_span_text(self, text: str) -> timedelta:
        t = str(text).strip()
        if "15" in t and "мин" in t: return timedelta(minutes=15)
        if "1" in t and "час" in t and "12" not in t: return timedelta(hours=1)
        if "4" in t and "час" in t and "24" not in t: return timedelta(hours=4)
        if "12" in t: return timedelta(hours=12)
        if "72" in t: return timedelta(hours=72)
        if "24" in t: return timedelta(hours=24)
        return timedelta(hours=24)

    def _get_live_span_delta(self) -> timedelta:
        return self._parse_span_text(self.combo_live_span.currentText())

    def _get_duration_timedelta(self) -> timedelta:
        return self._parse_span_text(self.combo_duration.currentText())

    def _shift_archive_window(self, direction: int):
        dur = self._get_duration_timedelta()
        curr_py_dt = self.dt_start.dateTime().toPyDateTime()
        next_py_dt = curr_py_dt + (dur * direction)
        self.dt_start.setDateTime(QDateTime(next_py_dt))
        self.user_is_zoomed = False
        self.update_data(force_fit=True)

    def _on_mode_changed(self, mode: str):
        is_live = (mode == "Live")
        self.lbl_live_span.setVisible(is_live)
        self.combo_live_span.setVisible(is_live)
        self.btn_pause.setVisible(is_live)
        self.user_is_zoomed = False

        is_archive = not is_live
        for w in [self.lbl_arch_start, self.dt_start, self.lbl_arch_dur, self.combo_duration,
                 self.btn_nav_prev, self.btn_nav_next, self.btn_load_archive]:
            w.setVisible(is_archive)

        if is_live:
            self.timer.start()
            self.update_data(force_fit=True)
        else:
            self.timer.stop()
            self.update_data(force_fit=True)

    def _on_live_tick(self):
        if self.combo_mode.currentText() == "Live" and not self.is_paused:
            self.update_data(force_fit=False)

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

    def _reset_zoom(self):
        self.user_is_zoomed = False
        self.update_data(force_fit=True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        text = event.mimeData().text()
        if not text:
            return
        # Принимаем одиночный ID или несколько ID через запятую (при перетаскивании папки)
        for part in text.split(","):
            try:
                tag_id = int(part.strip())
                self.add_signal(tag_id)
            except ValueError:
                pass

    def add_signal(self, tag_id: int):
        if tag_id in self.active_tags:
            return
        tags = self.db.get_all_tags()
        tag = next((t for t in tags if t.id == tag_id), None)
        if not tag:
            return
        colors = ['#667EEA', '#FF6384', '#4BC0C0', '#FF9F40', '#9966FF', '#FFCD56', '#00E676', '#E91E63']
        color = colors[len(self.active_tags) % len(colors)]
        curve = self.plot_widget.plot(pen=pg.mkPen(color=color, width=2.0), name=tag.name)

        self.active_tags[tag_id] = {"tag": tag, "curve": curve, "chip": None, "color": color}
        self._update_legend_panel()
        self.user_is_zoomed = False
        self.update_data(force_fit=True)

    def remove_signal(self, tag_id: int):
        if tag_id in self.active_tags:
            data = self.active_tags.pop(tag_id)
            self.plot_widget.removeItem(data["curve"])
            self._update_legend_panel()
            self.plot_widget.enableAutoRange(axis='y')

    def clear_chart(self):
        for tag_id in list(self.active_tags.keys()):
            self.remove_signal(tag_id)

    def _export_pdf(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт графика в PDF", f"{self.title}.pdf", "PDF Files (*.pdf)"
        )
        if not file_path:
            return

        writer = QPdfWriter(file_path)
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setPageOrientation(QPageLayout.Orientation.Landscape)  # ← альбомная
        writer.setResolution(96)

        painter = QPainter(writer)
        try:
            margin = 45
            page_width = writer.width()
            page_height = writer.height()
            chart_rect = QRectF(margin, margin, page_width - 2 * margin, page_height - 200)

            axes = [self.plot_widget.getAxis(name) for name in ('left', 'bottom')]
            old_axis_pens = [axis.pen() for axis in axes]
            old_text_pens = [axis.textPen() for axis in axes]

            self.plot_widget.setBackground('#FFFFFF')
            for axis in axes:
                axis.setPen(pg.mkPen('#303030', width=1))
                axis.setTextPen(pg.mkPen('#303030'))
            self.plot_widget.showGrid(x=True, y=True, alpha=0.18)

            try:
                pixmap = self.plot_widget.grab()
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        int(chart_rect.width()),
                        int(chart_rect.height()),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    target = QRectF(
                        chart_rect.left() + (chart_rect.width() - scaled.width()) / 2,
                        chart_rect.top(),
                        scaled.width(),
                        scaled.height()
                    )
                    painter.drawPixmap(target.toRect(), scaled)
            finally:
                self.plot_widget.setBackground('#181818')
                self.plot_widget.showGrid(x=True, y=True, alpha=0.35)
                for axis, pen, text_pen in zip(axes, old_axis_pens, old_text_pens):
                    axis.setPen(pen)
                    axis.setTextPen(text_pen)

            y = chart_rect.bottom() + 35
            painter.setPen(QColor("#202020"))
            painter.drawText(int(margin), int(y), self.title)
            y += 24
            for item in self.active_tags.values():
                color = QColor(item["color"])
                painter.setBrush(color)
                painter.setPen(color)
                painter.drawRect(int(margin), int(y - 12), 16, 16)
                painter.setPen(QColor("#202020"))
                painter.drawText(int(margin + 24), int(y), item["tag"].name)
                y += 22
        finally:
            painter.end()

    def update_data(self, force_fit: bool = False):
        if not self.active_tags:
            return
        is_live = (self.combo_mode.currentText() == "Live")

        if is_live:
            delta = self._get_live_span_delta()
            target_t_start = None
            target_t_end = None
            for tag_id, item in self.active_tags.items():
                points, t_start, t_end = self.db.get_data_points_by_data_span(tag_id, delta)
                if not points:
                    item["curve"].setData([], [])
                    item["times"] = None
                    item["values"] = None
                    continue
                times = np.array([p.timestamp.timestamp() for p in points])
                values = np.array([p.value for p in points])
                item["times"] = times
                item["values"] = values
                step_x, step_y = make_step_curve(times, values)
                item["curve"].setData(step_x, step_y)
                if target_t_start is None or t_start < target_t_start: target_t_start = t_start
                if target_t_end is None or t_end > target_t_end: target_t_end = t_end

            if (not self.user_is_zoomed or force_fit) and target_t_start and target_t_end:
                self.plot_widget.blockSignals(True)
                self.plot_widget.setXRange(target_t_start, target_t_end, padding=0)
                self.plot_widget.blockSignals(False)
                span = target_t_end - target_t_start
                self.cursor_x1.setValue(target_t_start + span * 0.05)
                self.cursor_x2.setValue(target_t_end - span * 0.05)
                self._update_cursor_labels()
        else:
            start_dt = self.dt_start.dateTime().toPyDateTime()
            end_dt = start_dt + self._get_duration_timedelta()
            t_start = start_dt.timestamp()
            t_end = end_dt.timestamp()
            for tag_id, item in self.active_tags.items():
                points = self.db.get_data_points_range(tag_id, start_dt, end_dt)
                if not points:
                    item["curve"].setData([], [])
                    item["times"] = None
                    item["values"] = None
                    continue
                times = np.array([p.timestamp.timestamp() for p in points])
                values = np.array([p.value for p in points])
                item["times"] = times
                item["values"] = values
                step_x, step_y = make_step_curve(times, values)
                item["curve"].setData(step_x, step_y)

            if not self.user_is_zoomed or force_fit:
                self.plot_widget.blockSignals(True)
                self.plot_widget.setXRange(t_start, t_end, padding=0)
                self.plot_widget.blockSignals(False)
                span = t_end - t_start
                self.cursor_x1.setValue(t_start + span * 0.05)
                self.cursor_x2.setValue(t_end - span * 0.05)
                self._update_cursor_labels()

        self.plot_widget.enableAutoRange(axis='y')
