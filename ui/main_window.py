import webbrowser
import html
import sys
import ctypes
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QSplitter, QLabel, QPushButton, QScrollArea, QTextBrowser
)
from PyQt6.QtCore import Qt, QMimeData
from PyQt6.QtGui import QDrag

from config import WEB_PORT
from database.db_service import DatabaseService
from drivers.driver_manager import DriverManager
from drivers.registry import driver_type_keys, get_driver_class, get_driver_label
from ui.io_widget import IOWidget
from ui.chart_widget import ChartWidget
from ui.bits_widget import BitsWidget
from ui.db_settings_widget import DatabaseSettingsWidget

class DraggableTagTree(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragEnabled(True)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return

        tag = item.data(0, Qt.ItemDataRole.UserRole)
        if tag:
            drag = QDrag(self)
            mime = QMimeData()
            mime.setText(str(tag.id))
            drag.setMimeData(mime)
            drag.exec(Qt.DropAction.CopyAction)
            return

        tag_ids = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if tag_ids:
            drag = QDrag(self)
            mime = QMimeData()
            mime.setText(",".join(map(str, tag_ids)))
            drag.setMimeData(mime)
            drag.exec(Qt.DropAction.CopyAction)

class MainWindow(QMainWindow):
    def __init__(self, db_service: DatabaseService, driver_manager: DriverManager):
        super().__init__()
        self.setWindowTitle("PDA Next-Gen (Python Edition)")
        self.resize(1450, 850)
        self._apply_dark_title_bar()

        self.setStyleSheet("""
        /* Базовые цвета для всех окон и виджетов */
                    QMainWindow, QWidget {
                        background-color: #1E1E1E;
                        color: #D4D4D4;
                        font-family: 'Segoe UI', Tahoma, sans-serif;
                        font-size: 12px;
                    }
        
                    /* Вкладки */
                    QTabWidget::pane {
                        border: 1px solid #3F3F46;
                        background-color: #1E1E1E;
                    }
                    QTabBar::tab {
                        background: #252526;
                        color: #A0A0A0;
                        padding: 8px 20px;
                        font-weight: bold;
                        border-top-left-radius: 4px;
                        border-top-right-radius: 4px;
                        border: 1px solid #2D2D30;
                        margin-right: 2px;
                    }
                    QTabBar::tab:selected {
                        background: #1E1E1E;
                        color: #FFFFFF;
                        border-top: 2px solid #007ACC;
                        border-bottom: none;
                    }
        
                    /* Деревья (QTreeWidget) */
                    QTreeWidget {
                        background-color: #252526;
                        color: #FFFFFF;
                        border: 1px solid #3F3F46;
                        outline: none;
                    }
                    QTreeWidget::item:hover {
                        background-color: #2D2D30;
                    }
                    QTreeWidget::item:selected {
                        background-color: #007ACC;
                        color: #FFFFFF;
                    }
        
                    /* Таблицы (QTableWidget) и заголовок */
                    QTableWidget {
                        background-color: #1E1E1E;
                        color: #FFFFFF;
                        gridline-color: #2D2D30;
                        border: 1px solid #3F3F46;
                        selection-background-color: #007ACC;
                        outline: none;
                    }
                    QHeaderView::section {
                        background-color: #252526;
                        color: #E0E0E0;
                        font-weight: bold;
                        border: 1px solid #3F3F46;
                        padding: 5px;
                    }
                    /* Левый верхний угол таблицы над номерами строк */
                    QTableCornerButton::section {
                        background-color: #252526;
                        border: 1px solid #3F3F46;
                    }
        
                    /* Поля ввода и выпадающие списки */
                    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                        background-color: #2D2D30;
                        color: #FFFFFF;
                        border: 1px solid #55555A;
                        border-radius: 4px;
                        padding: 4px 6px;
                    }
                    QComboBox::drop-down {
                        border: none;
                    }
                    QComboBox QAbstractItemView {
                        background-color: #252526;
                        color: #FFFFFF;
                        selection-background-color: #007ACC;
                        border: 1px solid #3F3F46;
                    }
        
                    /* Разделитель (Splitter) */
                    QSplitter::handle {
                        background-color: #2D2D30;
                    }
                    QSplitter::handle:hover {
                        background-color: #007ACC;
                    }
        
                    /* Тёмные полосы прокрутки (Scrollbars) */
                    QScrollBar:vertical {
                        border: none;
                        background-color: #1E1E1E;
                        width: 10px;
                        margin: 0px;
                    }
                    QScrollBar::handle:vertical {
                        background-color: #424242;
                        min-height: 20px;
                        border-radius: 5px;
                    }
                    QScrollBar::handle:vertical:hover {
                        background-color: #686868;
                    }
                    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                        height: 0px;
                    }
                    QScrollBar:horizontal {
                        border: none;
                        background-color: #1E1E1E;
                        height: 10px;
                        margin: 0px;
                    }
                    QScrollBar::handle:horizontal {
                        background-color: #424242;
                        min-width: 20px;
                        border-radius: 5px;
                    }
                    QScrollBar::handle:horizontal:hover {
                        background-color: #686868;
                    }
                    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                        width: 0px;
                    }
        
                    /* Меню и диалоговые окна */
                    QMenu {
                        background-color: #252526;
                        color: #FFFFFF;
                        border: 1px solid #3F3F46;
                    }
                    QMenu::item:selected {
                        background-color: #007ACC;
                    }
                    QDialog {
                        background-color: #1E1E1E;
                        color: #FFFFFF;
                    }""")

    
        self.db = db_service
        self.dm = driver_manager
        self.chart_widgets = []
        self._init_ui()

    def _init_ui(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.io_tab = IOWidget(self.db, self.dm)
        self.tabs.addTab(self.io_tab, "I/O Configuration")

        charts_page = QWidget()
        ch_layout = QVBoxLayout(charts_page)
        ch_layout.setContentsMargins(8, 8, 8, 8)

        top_bar = QHBoxLayout()
        lbl_c = QLabel("<b>Графиков:</b>")
        lbl_c.setStyleSheet("color: white;")
        top_bar.addWidget(lbl_c)

        self.btn_add_chart = QPushButton("＋ Добавить график")
        self.btn_add_chart.setStyleSheet("background-color: #466653; color: white; font-weight: bold;")
        self.btn_add_chart.clicked.connect(self._add_chart)
        top_bar.addWidget(self.btn_add_chart)

        top_bar.addSpacing(20)
        self.btn_open_web = QPushButton("🌐 Открыть Web-клиент")
        self.btn_open_web.setStyleSheet("background-color: #49657A; color: white;")
        self.btn_open_web.clicked.connect(lambda: webbrowser.open(f"http://localhost:{WEB_PORT}"))
        top_bar.addWidget(self.btn_open_web)

        top_bar.addStretch()
        ch_layout.addLayout(top_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left_panel = QWidget()
        lp_layout = QVBoxLayout(left_panel)
        lp_layout.setContentsMargins(0, 0, 0, 0)
        lp_layout.addWidget(QLabel("<b>Дерево сигналов (Drag & Drop):</b>"))
        self.tag_tree = DraggableTagTree()
        lp_layout.addWidget(self.tag_tree)
        splitter.addWidget(left_panel)

        self.charts_scroll = QScrollArea()
        self.charts_scroll.setWidgetResizable(True)
        self.charts_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.charts_container = QWidget()
        self.charts_layout = QVBoxLayout(self.charts_container)
        self.charts_layout.setContentsMargins(0, 0, 0, 0)
        self.charts_layout.setSpacing(6)
        self.charts_layout.addStretch(1)
        self.charts_scroll.setWidget(self.charts_container)
        splitter.addWidget(self.charts_scroll)

        splitter.setSizes([260, 1180])
        ch_layout.addWidget(splitter)

        self._update_charts(1)
        self._reload_tag_tree()
        self.tabs.addTab(charts_page, "Charts")

        self.bits_tab = BitsWidget(self.db)
        self.tabs.addTab(self.bits_tab, "Bits")

        self.db_tab = DatabaseSettingsWidget(self.db, self.dm, on_reconnect_callback=self._reconnect_database)
        self.tabs.addTab(self.db_tab, "Database & Storage")

        self.help_tab = self._create_help_tab()
        self.tabs.addTab(self.help_tab, "Help")

        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _address_reference_html(self):
        """Справочник адресов собирается из самих драйверов и не может устареть."""
        rows = []
        for key in driver_type_keys():
            cls = get_driver_class(key)
            if cls is None:
                continue
            label = get_driver_label(key)
            examples = getattr(cls, "ADDRESS_EXAMPLES", [])
            cells = "".join(
                f"<tr><td><code>{html.escape(addr)}</code></td>"
                f"<td>{html.escape(dtype)}</td>"
                f"<td>{html.escape(desc)}</td></tr>"
                for addr, dtype, desc in examples
            )
            rows.append(
                f"<h3>{html.escape(label)}</h3>"
                f"<table border='0' cellpadding='4' cellspacing='0' width='100%'>"
                f"<tr style='color:#9CDCFE'><th align='left'>Адрес</th>"
                f"<th align='left'>Тип</th><th align='left'>Что читается</th></tr>"
                f"{cells}</table>"
            )
        return "".join(rows)

    def _create_help_tab(self):
        help_tab = QWidget()
        layout = QVBoxLayout(help_tab)
        layout.setContentsMargins(14, 14, 14, 14)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        help_html = """
        <h1>Справка пользователя</h1>
        <p style='color:#A0A0A0'>PDA Next-Gen. Быстрое руководство по просмотру данных и настройке приложения.</p>

        <h2>1. Первый запуск</h2>
        <ol>
          <li>Откройте вкладку <b>Database &amp; Storage</b>.</li>
          <li>Выберите движок базы данных.</li>
          <li>Для PostgreSQL/MySQL укажите хост, порт, имя базы, пользователя и пароль.</li>
          <li>Для SQLite выберите существующий файл кнопкой <b>Выбрать файл...</b> или укажите новый путь кнопкой <b>Новый файл</b>.</li>
          <li>Нажмите <b>Проверить подключение</b>, затем <b>Применить и переключить базу</b>.</li>
        </ol>

        <h2>2. Статус и диагностика</h2>
        <p>В нижней статусной панели отображаются состояние БД, количество модулей, работающие драйверы и причины ошибок.</p>
        <ul>
          <li><b>БД: подключена</b> означает, что чтение и запись доступны.</li>
          <li><b>БД: недоступна</b> сопровождается причиной ошибки подключения.</li>
          <li>Если порт занят, модуль будет показан как требующий внимания с текстом ошибки.</li>
          <li>После исправления настроек нажмите <b>Применить</b> или перезапустите модуль.</li>
        </ul>

        <h2>3. I/O Configuration</h2>
        <p>Здесь создаются подключения, группы сигналов и сами переменные.</p>
        <ul>
          <li>Добавьте подключение и выберите тип драйвера.</li>
          <li>Заполните адрес, порт и параметры протокола.</li>
          <li>Создайте группу и добавьте в неё сигналы.</li>
          <li>Зелёный индикатор означает рабочий модуль, красный или сообщение об ошибке требует проверки.</li>
          <li>Кнопки экспорта и импорта позволяют перенести конфигурацию.</li>
        </ul>

        <h2>4. Форматы адресов сигналов</h2>
        <p>Формат адреса зависит от драйвера модуля. Поле «Адрес» в диалоге переменной
        проверяет ввод сразу и подсказывает правильный формат.
        Регистр букв роли не играет, пробелы и префикс <code>%</code> (стиль TIA Portal) игнорируются:
        <code>%MW230</code>, <code>mw 230</code> и <code>MW230</code> — один и тот же адрес.</p>
        __ADDRESS_REFERENCE__

        <h2>5. Минимальное время опроса</h2>
        <p>«Период опроса (мс)» — это <b>целевой период</b>, а не пауза после чтения.
        Драйвер выполняет чтение и досыпает только остаток цикла. Поэтому период никогда
        не может быть меньше времени самого чтения:</p>
        <p><code>цикл = число_запросов_к_ПЛК × отклик_ПЛК + время_записи_в_БД</code></p>
        <p>Отклик S7-1200/1500 по 100 Мбит сети обычно <b>3-10 мс на один запрос</b>.
        Значит, 12 тегов по одному запросу — это 12 × 6 = 72 мс и больше, сколько ни
        ставь в поле. Приложение само сокращает число запросов:</p>
        <ul>
          <li><b>multi-read</b> — до 20 разнесённых тегов читаются одним пакетом S7
              (один round-trip вместо двадцати);</li>
          <li><b>пакетное чтение</b> — соседние адреса объединяются в один запрос,
              если между ними небольшой разрыв;</li>
          <li>список тегов кэшируется, чтобы не обращаться к базе каждый цикл.</li>
        </ul>
        <p><b>Как посчитать свой минимум:</b> посмотрите в статус модуля на вкладке
        I/O Configuration — там видно фактическое время цикла, частоту и число запросов:</p>
        <p><code>Polling · 12 сигн./1 запр. · 9.8 мс/цикл (102 Гц)</code></p>
        <ul>
          <li>Слово <b>ПЕРЕГРУЗ</b> означает, что чтение длится дольше заданного периода —
              реальная частота ниже указанной. Уменьшите число тегов или увеличьте период.</li>
          <li>Если запросов много, теги сильно разнесены по разным областям — объединить
              их в один пакет не всегда возможно, это ограничение протокола.</li>
          <li>Ставить период заведомо меньше времени цикла бессмысленно: драйвер просто
              будет работать без паузы. Ориентируйтесь на измеренное значение из статуса.</li>
          <li>Ориентир: 1 запрос + отклик ПЛК 6 мс + запись в базу 3 мс ≈ <b>9-10 мс</b>
              (≈100 Гц). Ниже опуститься мешает уже сама сеть и ЦПУ.</li>
        </ul>

        <h2>6. Charts</h2>
        <ul>
          <li>Перетащите сигнал из дерева слева на график или дважды щёлкните по нему.</li>
          <li>Кнопка <b>＋ Добавить график</b> создаёт дополнительное окно просмотра.</li>
          <li>Крестик в правой части панели закрывает конкретный график.</li>
          <li>Левый клик устанавливает курсор <b>X1</b>.</li>
          <li><b>Ctrl/Shift + левый клик</b> или нажатие колеса устанавливает <b>X2</b>.</li>
          <li>Курсоры можно перетаскивать мышью.</li>
          <li><b>100%</b> возвращает полный диапазон, <b>Очистить</b> убирает сигналы.</li>
          <li><b>PDF</b> сохраняет график вместе с легендой.</li>
        </ul>

        <h2>7. Bits</h2>
        <p>Вкладка показывает 16 бит выбранного регистра отдельными дорожками.</p>
        <ul>
          <li>Y-масштаб подстраивается под отображаемые битовые дорожки.</li>
          <li>Курсоры работают так же, как на обычном графике.</li>
          <li>Режим <b>Live</b> обновляет данные автоматически.</li>
          <li>Режим <b>Archive</b> позволяет просматривать исторический диапазон.</li>
        </ul>

        <h2>8. Web-клиент</h2>
        <p>Кнопка <b>Открыть Web-клиент</b> запускает просмотр графиков в браузере. Веб-клиент предназначен для просмотра данных и не заменяет настройку базы или модулей.</p>

        <h2>Если данные не отображаются</h2>
        <ol>
          <li>Проверьте статус БД.</li>
          <li>Проверьте статус драйвера и текст ошибки в конфигурации.</li>
          <li>Убедитесь, что порт не занят другим приложением.</li>
          <li>Проверьте адреса сигналов и наличие новых точек в архиве.</li>
          <li>Для Live-подключений убедитесь, что модуль не находится на паузе.</li>
        </ol>
        """
        browser.setHtml(help_html.replace("__ADDRESS_REFERENCE__", self._address_reference_html()))
        layout.addWidget(browser)
        return help_tab

    def _on_tab_changed(self, idx):
        if idx == 1:
            self._reload_tag_tree()

    def _apply_dark_title_bar(self):
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            dark_mode = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_mode), ctypes.sizeof(dark_mode))
        except Exception:
            pass

    def _reload_tag_tree(self):
        self.tag_tree.clear()
        conns = self.db.get_all_connections()
        all_tags = self.db.get_all_tags()

        for c in conns:
            conn_item = QTreeWidgetItem([f"🔌 {c.name}"])
            conn_item.setFlags(conn_item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)

            c_tags = [t for t in all_tags if t.connection_id == c.id]
            groups_dict = {}
            for t in c_tags:
                g_name = t.group_name or "Общие"
                groups_dict.setdefault(g_name, []).append(t)

            for g_name, g_tags in sorted(groups_dict.items()):
                group_item = QTreeWidgetItem([f"📁 {g_name} ({len(g_tags)})"])
                group_item.setData(0, Qt.ItemDataRole.UserRole + 1, [t.id for t in g_tags])

                for t in g_tags:
                    label = f"{t.name} [{t.unit}]" if t.unit else t.name
                    tag_item = QTreeWidgetItem([label])
                    tag_item.setData(0, Qt.ItemDataRole.UserRole, t)
                    group_item.addChild(tag_item)

                conn_item.addChild(group_item)
                group_item.setExpanded(True)

            self.tag_tree.addTopLevelItem(conn_item)
            conn_item.setExpanded(True)

    def _reconnect_database(self, new_cfg):
        self.dm.stop_all()
        new_db = DatabaseService(**new_cfg)
        self.db = new_db
        self.dm.set_db(self.db)
        self.dm.start_all()

        self.io_tab.db = self.db
        self.io_tab.reload_tree()
        self._reload_tag_tree()
        for ch in self.chart_widgets:
            ch.db = self.db
            ch.update_data(force_fit=True)

        # Веб-сервер держит ссылку на старый сервис БД — обновляем её
        try:
            import web_server
            web_server.db_service = self.db
        except Exception:
            pass

    def _add_chart(self):
        chart = ChartWidget(f"Chart {len(self.chart_widgets) + 1}", self.db, on_close_callback=lambda: self._remove_chart(chart))
        self.chart_widgets.append(chart)
        self.charts_layout.insertWidget(self.charts_layout.count() - 1, chart)
        chart.setMinimumHeight(260)
        chart.resize(chart.width(), max(260, chart.height()))

    def _remove_chart(self, chart):
        if chart not in self.chart_widgets:
            return
        self.chart_widgets.remove(chart)
        self.charts_layout.removeWidget(chart)
        chart.deleteLater()
        for index, item in enumerate(self.chart_widgets, start=1):
            item.title = f"Chart {index}"
            item.lbl_title.setText(f"<b>{item.title}</b>")

    def _update_charts(self, count: int):
        while len(self.chart_widgets) < count:
            self._add_chart()
        while len(self.chart_widgets) > count:
            self._remove_chart(self.chart_widgets[-1])

    def closeEvent(self, event):
        self.dm.stop_all()
        event.accept()
