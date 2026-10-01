import webbrowser
import html
import sys
import ctypes
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QSplitter, QLabel, QPushButton, QScrollArea, QTextBrowser,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, QMimeData
from PyQt6.QtGui import QDrag

from config import WEB_PORT
from database.db_service import DatabaseService
from drivers.driver_manager import DriverManager
from drivers.registry import driver_type_keys, get_driver_class, get_driver_label
from ui.io_widget import IOWidget
from ui.styles import SPIN_BUTTON_QSS
from ui import theme
from ui.theme import S
from ui.chart_widget import ChartWidget
from ui.bits_widget import BitsWidget
from ui.db_settings_widget import DatabaseSettingsWidget


class DraggableTagTree(QTreeWidget):
    """Дерево сигналов: мультивыбор (Ctrl/Shift) + drag выделенного на график.

    При перетаскивании передаются id всех выделенных листьев; если среди
    выделенных есть узел группы/подключения — он разворачивается до тегов.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    @staticmethod
    def _tag_ids_of(item):
        """Собирает id тегов из листа или из всей ветки (группы/подключения)."""
        out = []
        tag = item.data(0, Qt.ItemDataRole.UserRole)
        if tag:
            out.append(tag.id)
            return out
        ids = item.data(0, Qt.ItemDataRole.UserRole + 1)   # группа
        if ids:
            out.extend(ids)
            return out
        for i in range(item.childCount()):                 # подключение
            out.extend(DraggableTagTree._tag_ids_of(item.child(i)))
        return out

    def startDrag(self, supportedActions):
        items = self.selectedItems() or ([self.currentItem()] if self.currentItem() else [])
        if not items:
            return
        # сохраняем выбранные листья: id могут повторяться между узлами
        tag_ids = []
        seen = set()
        for it in items:
            for tid in self._tag_ids_of(it):
                if tid not in seen:
                    seen.add(tid)
                    tag_ids.append(tid)
        if not tag_ids:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(",".join(map(str, tag_ids)))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


from config import WEB_PORT
from database.db_service import DatabaseService
from drivers.driver_manager import DriverManager
from drivers.registry import driver_type_keys, get_driver_class, get_driver_label
from ui.io_widget import IOWidget
from ui.styles import SPIN_BUTTON_QSS
from ui import theme
from ui.theme import S
from ui.chart_widget import ChartWidget
from ui.bits_widget import BitsWidget
from ui.db_settings_widget import DatabaseSettingsWidget


class MainWindow(QMainWindow):
    def __init__(self, db_service: DatabaseService, driver_manager: DriverManager):
        super().__init__()
        self.setWindowTitle("PDA Next-Gen (Python Edition)")
        self.resize(1450, 850)
        theme.register_title_window(self)

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
        theme.themed(lbl_c, f"color: {S('text')};")
        top_bar.addWidget(lbl_c)

        self.btn_add_chart = QPushButton("＋ Добавить график")
        theme.themed(self.btn_add_chart,
                     f"background-color: {S('btn_add')}; color: {S('btn_add_text')}; font-weight: bold;")
        self.btn_add_chart.clicked.connect(self._add_chart)
        top_bar.addWidget(self.btn_add_chart)

        top_bar.addSpacing(20)
        self.btn_open_web = QPushButton("🌐 Открыть Web-клиент")
        theme.themed(self.btn_open_web,
                     f"background-color: {S('btn_action')}; color: {S('btn_action_text')};")
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
        # новый/удалённый/переименованный сигнал в I/O должен сразу
        # появляться в списке вкладке Bits и в подписях дорожек
        self.io_tab.tags_changed.connect(self.bits_tab.refresh_tags)

        self.db_tab = DatabaseSettingsWidget(self.db, self.dm, on_reconnect_callback=self._reconnect_database)
        self.tabs.addTab(self.db_tab, "Database & Storage")

        self.help_tab = self._create_help_tab()
        self.tabs.addTab(self.help_tab, "Help")
        theme.on_theme(lambda p: self._refresh_help_theme())

        # Переключатель светлой/тёмной темы — в ряду вкладок, самый правый край.
        self.btn_theme = QPushButton()
        self.btn_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        theme.themed(self.btn_theme,
                     "QPushButton { background: transparent; border: none; padding: 4px 10px;"
                     f" color: {S('text_dim')}; font-weight: bold; font-size: 12px; }}"
                     f" QPushButton:hover {{ color: {S('text')}; }}")
        self._update_theme_btn_text()
        self.btn_theme.clicked.connect(self._toggle_theme)
        self.tabs.setCornerWidget(self.btn_theme, Qt.Corner.TopRightCorner)

        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _address_reference_html(self, p=None):
        """Справочник адресов собирается из самих драйверов и не может устареть."""
        p = p or theme.current()
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
                f"<tr style='color:{p.hint}'><th align='left'>Адрес</th>"
                f"<th align='left'>Тип</th><th align='left'>Что читается</th></tr>"
                f"{cells}</table>"
            )
        return "".join(rows)

    def _build_help_html(self, p):
        """HTML справки, перекрашенный под палитру p (токены __DIM__)."""
        html_text = self._HELP_TEMPLATE.replace("__DIM__", p.text_dim)
        html_text = html_text.replace("__ADDRESS_REFERENCE__", self._address_reference_html(p))
        return html_text

    def _create_help_tab(self):
        help_tab = QWidget()
        layout = QVBoxLayout(help_tab)
        layout.setContentsMargins(14, 14, 14, 14)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        self.help_browser = browser
        self._refresh_help_theme()
        layout.addWidget(browser)
        return help_tab

    _HELP_TEMPLATE = """
        <h1>Справка пользователя</h1>
        <p style='color:__DIM__'>PDA Next-Gen. Быстрое руководство по просмотру данных и настройке приложения.</p>

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
        <p>Вкладка показывает биты выбранного регистра отдельными дорожками. Слева —
        дерево сигналов, отфильтрованное по целочисленным типам 8-16 бит
        (<code>BYTE</code>, <code>INT16</code>, <code>UINT16</code>): FLOAT, BOOL и
        32-битные регистры в него не попадают, потому что разложить их на 16 дорожек
        корректно нельзя.</p>
        <ul>
          <li><code>BYTE</code> — 8 дорожек, <code>INT16</code>/<code>UINT16</code> — 16.</li>
          <li><b>Bit 0</b> — верхняя дорожка, цвет каждой дорожки закреплён за номером бита.</li>
          <li>Кнопка <b>Hi-Lo / Lo-Hi</b> меняет порядок байт 16-битного слова:
              <b>Hi-Lo</b> — как отдаёт Siemens (старший байт первым), <b>Lo-Hi</b> — байты
              меняются местами; выбор сохраняется и доступен в web-клиенте.</li>
          <li>Y-масштаб подстраивается под отображаемые битовые дорожки.</li>
          <li>Курсоры работают так же, как на обычном графике.</li>
          <li>Режим <b>Live</b> обновляет данные автоматически.</li>
          <li>Режим <b>Archive</b> позволяет просматривать исторический диапазон.</li>
          <li>В web-клиенте (вкладка Bits) — такое же дерево сигналов с тем же фильтром.</li>
        </ul>

        <h2>8. Web-клиент</h2>
        <p>Кнопка <b>Открыть Web-клиент</b> запускает просмотр графиков в браузере. Слева на вкладке <b>Charts</b> — дерево сигналов по подключениям и группам: клик добавляет сигнал в активное окно графика, перетаскивание — на любой график. На вкладке <b>Bits</b> — такое же дерево, но только с 8-16 битными регистрами (BYTE/INT16/UINT16). Веб-клиент предназначен для просмотра данных и не заменяет настройку базы или модулей.</p>

        <h2>Если данные не отображаются</h2>
        <ol>
          <li>Проверьте статус БД.</li>
          <li>Проверьте статус драйвера и текст ошибки в конфигурации.</li>
          <li>Убедитесь, что порт не занят другим приложением.</li>
          <li>Проверьте адреса сигналов и наличие новых точек в архиве.</li>
          <li>Для Live-подключений убедитесь, что модуль не находится на паузе.</li>
        </ol>
        """

    def _on_tab_changed(self, idx):
        if idx == 1:
            self._reload_tag_tree()

    def _toggle_theme(self):
        name = theme.other_name()
        theme.apply_theme(QApplication.instance(), name)
        theme.save_choice(name)
        self._update_theme_btn_text()
        self._refresh_help_theme()

    def _update_theme_btn_text(self):
        if theme.current_name() == "dark":
            self.btn_theme.setText("☀️ Светлая тема")
        else:
            self.btn_theme.setText("🌙 Тёмная тема")

    def _refresh_help_theme(self):
        """HTML справки перекрашивается под текущую палитру."""
        p = theme.current()
        self.help_browser.setStyleSheet(
            f"QTextBrowser {{ background-color: {p.window}; color: {p.text_soft};"
            f" border: 1px solid {p.border_soft}; }}")
        self.help_browser.setHtml(self._build_help_html(p))

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
