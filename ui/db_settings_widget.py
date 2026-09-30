import os
import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox,
    QLineEdit, QSpinBox, QPushButton, QLabel, QFrame, QMessageBox, QFileDialog
)
from PyQt6.QtCore import Qt, QTimer, QDateTime

from ui import theme
from ui.theme import S

class DatabaseSettingsWidget(QWidget):
    def __init__(self, db_service, driver_manager, on_reconnect_callback, parent=None):
        super().__init__(parent)
        self.db = db_service
        self.dm = driver_manager
        self.on_reconnect = on_reconnect_callback
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        title = QLabel("<h2>Настройки базы данных и архивации (Data Storage)</h2>")
        theme.themed(title, f"color: {S('text')};")
        layout.addWidget(title)

        frame = QFrame()
        theme.themed(frame, "background-color: {%panel%}; border: 1px solid {%border_soft%}; border-radius: 6px; padding: 12px;")
        f_layout = QFormLayout(frame)

        self.combo_engine = QComboBox()
        self.combo_engine.addItems([
            "PostgreSQL / TimescaleDB (Сетевая)",
            "MariaDB / MySQL InnoDB (Сетевая)",
            "LocalDB - SQLite (Локальный файл)"
        ])
        self.combo_engine.currentIndexChanged.connect(self._on_engine_changed)

        self.txt_host = QLineEdit("localhost")
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(5432)

        self.txt_dbname = QLineEdit("pda_data")
        self.txt_user = QLineEdit("postgres")
        self.txt_pass = QLineEdit()
        self.txt_pass.setEchoMode(QLineEdit.EchoMode.Password)

        self.box_sqlite = QHBoxLayout()
        self.txt_sqlite_path = QLineEdit("data/pda_local.db")
        self.btn_browse = QPushButton("📁 Выбрать файл...")
        self.btn_browse.setToolTip("Выбрать существующую SQLite-базу данных")
        self.btn_browse.clicked.connect(self._browse_sqlite)
        self.btn_new_sqlite = QPushButton("＋ Новый файл")
        self.btn_new_sqlite.setToolTip("Указать путь для новой SQLite-базы")
        self.btn_new_sqlite.clicked.connect(self._new_sqlite_file)
        self.box_sqlite.addWidget(self.txt_sqlite_path)
        self.box_sqlite.addWidget(self.btn_browse)
        self.box_sqlite.addWidget(self.btn_new_sqlite)

        f_layout.addRow("<b>Движок СУБД (Storage Engine):</b>", self.combo_engine)
        
        self.lbl_host = QLabel("Хост / IP сервера:")
        f_layout.addRow(self.lbl_host, self.txt_host)

        self.lbl_port = QLabel("TCP Порт:")
        f_layout.addRow(self.lbl_port, self.spin_port)

        self.lbl_db = QLabel("Имя базы данных:")
        f_layout.addRow(self.lbl_db, self.txt_dbname)

        self.lbl_user = QLabel("Пользователь:")
        f_layout.addRow(self.lbl_user, self.txt_user)

        self.lbl_pass = QLabel("Пароль:")
        f_layout.addRow(self.lbl_pass, self.txt_pass)

        self.lbl_sqlite = QLabel("Файл базы данных:")
        f_layout.addRow(self.lbl_sqlite, self.box_sqlite)

        layout.addWidget(frame)

        btn_box = QHBoxLayout()
        self.btn_test = QPushButton("🔌 Проверить подключение")
        theme.themed(self.btn_test, theme.btn_qss("action", border=False))
        self.btn_test.clicked.connect(self._test_connection)

        self.btn_save = QPushButton("💾 Применить и переключить базу")
        theme.themed(self.btn_save, theme.btn_qss("add", border=False))
        self.btn_save.clicked.connect(self._save_and_reconnect)

        self.btn_create_db = QPushButton("＋ Создать базу")
        theme.themed(self.btn_create_db, theme.btn_qss("violet", border=False))
        self.btn_create_db.clicked.connect(self._create_database)

        btn_box.addWidget(self.btn_test)
        btn_box.addWidget(self.btn_create_db)
        btn_box.addWidget(self.btn_save)
        btn_box.addStretch()
        layout.addLayout(btn_box)

        self.lbl_status = QLabel("Статус: готов к подключению")
        self._status_state = "faint"
        self._restyle_status = theme.themed(self.lbl_status, lambda p: theme.hint_qss(p, self._status_state, 13))
        layout.addWidget(self.lbl_status)

        self.status_panel = QFrame()
        theme.themed(self.status_panel, "QFrame { background: {%panel%}; border: 1px solid {%border_soft%}; border-radius: 5px; }")
        status_layout = QVBoxLayout(self.status_panel)
        status_layout.setContentsMargins(10, 8, 10, 8)
        status_layout.setSpacing(3)
        self.lbl_db_status = QLabel()
        self.lbl_modules_status = QLabel()
        self.lbl_journal_status = QLabel()
        self.lbl_errors_status = QLabel()
        self.lbl_details_status = QLabel()
        self._db_state = "faint"
        self._journal_state = "faint"
        self._errors_state = "ok"
        self._restyle_db_status = theme.themed(self.lbl_db_status, lambda p: theme.hint_qss(p, self._db_state, 12))
        self._restyle_journal_status = theme.themed(self.lbl_journal_status, lambda p: theme.hint_qss(p, self._journal_state, 11))
        self._restyle_errors_status = theme.themed(self.lbl_errors_status, lambda p: theme.hint_qss(p, self._errors_state, 12))
        theme.themed(self.lbl_modules_status, f"color: {S('text_soft')}; font-size: 12px;")
        theme.themed(self.lbl_details_status, f"color: {S('text_faint')}; font-size: 11px;")
        for label in (self.lbl_db_status, self.lbl_modules_status, self.lbl_journal_status, self.lbl_errors_status, self.lbl_details_status):
            label.setWordWrap(True)
            status_layout.addWidget(label)
        layout.addWidget(self.status_panel)
        layout.addStretch()

        self._load_current_config()
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(1000)
        self.status_timer.timeout.connect(self._refresh_status_panel)
        self.status_timer.start()
        self._refresh_status_panel()

    def _refresh_status_panel(self):
        available = getattr(self.db, "is_available", False)
        db_error = getattr(self.db, "last_error", "")
        if available:
            self.lbl_db_status.setText("БД: подключена")
            self._db_state = "ok"
        else:
            reason = f": {db_error}" if db_error else ""
            self.lbl_db_status.setText(f"БД: недоступна{reason}")
            self._db_state = "error"
        self._restyle_db_status()

        try:
            connections = self.db.get_all_connections() if available else []
        except Exception as e:
            connections = []
            db_error = str(e)
        drivers = getattr(self.dm, "drivers", {})
        connection_names = {getattr(conn, "id", None): getattr(conn, "name", f"Модуль {getattr(conn, 'id', '?')}") for conn in connections}
        running = sum(1 for driver in drivers.values() if getattr(driver, "is_running", False))
        errors = []
        for connection_id, driver in drivers.items():
            status = str(getattr(driver, "status", "нет статуса"))
            status_lower = status.lower()
            if status_lower.startswith(("error", "connect error", "connection failed", "poll error")) or "error" in status_lower or "failed" in status_lower:
                errors.append(f"{connection_names.get(connection_id, f'Модуль {connection_id}')}: {status}")
        self.lbl_modules_status.setText(f"Модули: всего {len(connections)}, запущено {len(drivers)}, работают {running}, требуют внимания {len(errors)}")

        # Метрики журнала: глубина очереди писателя, запись, потери
        try:
            j = self.dm.journal_stats()
            journal_line = (f"Журнал: в очереди {j.get('queued', 0)} · записано {j.get('written', 0)} "
                            f"пачками {j.get('batches', 0)} · потеряно {j.get('dropped', 0)} "
                            f"· ошибок записи {j.get('errors', 0)}")
            if j.get("last_error"):
                journal_line += f" | {j['last_error'][:80]}"
            bad = j.get("dropped", 0) > 0 or j.get("errors", 0) > 0
            self.lbl_journal_status.setText(journal_line)
            self._journal_state = "warn" if bad else "faint"
            self._restyle_journal_status()
        except Exception:
            self.lbl_journal_status.setText("Журнал: нет данных")

        self.lbl_errors_status.setText("Ошибки: " + ("; ".join(errors) if errors else "нет активных ошибок"))
        self._errors_state = "error" if errors else "ok"
        self._restyle_errors_status()
        self.lbl_details_status.setText("Последнее обновление: " + QDateTime.currentDateTime().toString("dd.MM.yyyy HH:mm:ss"))

    def _load_current_config(self):
        engine = getattr(self.db, "engine", "postgres")
        if engine == "postgres":
            self.combo_engine.setCurrentIndex(0)
            self.txt_host.setText(self.db.cfg.get("host", "localhost"))
            self.spin_port.setValue(int(self.db.cfg.get("port", 5432)))
            self.txt_dbname.setText(self.db.cfg.get("dbname", "pda_data"))
            self.txt_user.setText(self.db.cfg.get("user", "postgres"))
            self.txt_pass.setText(self.db.cfg.get("password", ""))
        elif engine in ("mariadb", "mysql"):
            self.combo_engine.setCurrentIndex(1)
            self.txt_host.setText(self.db.cfg.get("host", "localhost"))
            self.spin_port.setValue(int(self.db.cfg.get("port", 3306)))
            self.txt_dbname.setText(self.db.cfg.get("dbname", "pda_data"))
            self.txt_user.setText(self.db.cfg.get("user", "root"))
            self.txt_pass.setText(self.db.cfg.get("password", ""))
        else:
            self.combo_engine.setCurrentIndex(2)
            self.txt_sqlite_path.setText(self.db.cfg.get("sqlite_path", "data/pda_local.db"))
        self._on_engine_changed(self.combo_engine.currentIndex())

    def _on_engine_changed(self, idx):
        is_sqlite = (idx == 2)
        self.lbl_host.setVisible(not is_sqlite)
        self.txt_host.setVisible(not is_sqlite)
        self.lbl_port.setVisible(not is_sqlite)
        self.spin_port.setVisible(not is_sqlite)
        self.lbl_db.setVisible(not is_sqlite)
        self.txt_dbname.setVisible(not is_sqlite)
        self.lbl_user.setVisible(not is_sqlite)
        self.txt_user.setVisible(not is_sqlite)
        self.lbl_pass.setVisible(not is_sqlite)
        self.txt_pass.setVisible(not is_sqlite)

        self.lbl_sqlite.setVisible(is_sqlite)
        self.txt_sqlite_path.setVisible(is_sqlite)
        self.btn_browse.setVisible(is_sqlite)
        self.btn_new_sqlite.setVisible(is_sqlite)

        if idx == 0:
            self.spin_port.setValue(5432)
            if not self.txt_user.text(): self.txt_user.setText("postgres")
        elif idx == 1:
            self.spin_port.setValue(3306)
            if not self.txt_user.text(): self.txt_user.setText("root")

    def _browse_sqlite(self):
        dialog = QFileDialog(self, "Выбрать существующую SQLite-базу", self.txt_sqlite_path.text() or "data", "SQLite DB (*.db *.sqlite);;Все файлы (*.*)")
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        if dialog.exec():
            file_path = dialog.selectedFiles()[0]
        else:
            file_path = ""
        if file_path:
            self.txt_sqlite_path.setText(file_path)
            self.lbl_status.setText("Файл SQLite выбран. Нажмите «Проверить подключение» или «Применить»." )

    def _new_sqlite_file(self):
        dialog = QFileDialog(self, "Создать SQLite-базу", self.txt_sqlite_path.text() or "data/pda_local.db", "SQLite DB (*.db *.sqlite)")
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        if dialog.exec():
            file_path = dialog.selectedFiles()[0]
        else:
            file_path = ""
        if file_path:
            self.txt_sqlite_path.setText(file_path)
            self.lbl_status.setText("Путь новой SQLite-базы выбран. Нажмите «Применить»." )

    def _get_form_config(self):
        idx = self.combo_engine.currentIndex()
        if idx == 0:
            return {
                "engine": "postgres",
                "host": self.txt_host.text().strip(),
                "port": self.spin_port.value(),
                "dbname": self.txt_dbname.text().strip(),
                "user": self.txt_user.text().strip(),
                "password": self.txt_pass.text().strip()
            }
        elif idx == 1:
            return {
                "engine": "mariadb",
                "host": self.txt_host.text().strip(),
                "port": self.spin_port.value(),
                "dbname": self.txt_dbname.text().strip(),
                "user": self.txt_user.text().strip(),
                "password": self.txt_pass.text().strip()
            }
        else:
            return {
                "engine": "sqlite",
                "sqlite_path": self.txt_sqlite_path.text().strip()
            }

    def _create_database(self):
        cfg = self._get_form_config()
        if cfg.get("engine") == "sqlite":
            QMessageBox.information(self, "Создание базы", "Для SQLite база создаётся автоматически при применении подключения.")
            return
        try:
            from database.db_service import create_database
            created = create_database(cfg)
            self.lbl_status.setText("🟢 База создана и готова к подключению.")
            message = "База данных создана." if created else "База данных уже существует."
            QMessageBox.information(self, "Создание базы", message + "\nТеперь нажмите «Применить и переключить базу».")
        except Exception as e:
            self.lbl_status.setText("🔴 Не удалось создать базу")
            QMessageBox.critical(self, "Ошибка создания базы", f"Не удалось создать базу данных:\n{e}")

    def _test_connection(self):
        cfg = self._get_form_config()
        self.lbl_status.setText("Тестирование соединения...")
        try:
            from database.db_service import DatabaseService
            test_db = DatabaseService(**cfg)
            conns = test_db.get_all_connections()
            self.lbl_status.setText(f"🟢 Подключение успешно! Найдено модулей: {len(conns)}")
            QMessageBox.information(self, "Тест соединения", f"Связь с базой успешно установлена!\nОбнаружено модулей: {len(conns)}")
        except Exception as e:
            self.lbl_status.setText("🔴 Ошибка соединения!")
            QMessageBox.critical(self, "Ошибка подключения", f"Не удалось подключиться к базе:\n{e}")

    def _save_and_reconnect(self):
        cfg = self._get_form_config()
        self.lbl_status.setText("Проверка подключения...")
        try:
            from database.db_service import DatabaseService
            test_db = DatabaseService(**cfg)
            test_db.get_all_connections()

            with open("db_config.json", "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)

            self.on_reconnect(cfg)
            engine_name = cfg.get("engine", "UNKNOWN").upper()
            self.lbl_status.setText(f"🟢 База переключена на [{engine_name}]. Сервисы перезапущены.")
            QMessageBox.information(self, "Успех", f"База данных успешно переключена на {engine_name}!\nКонфигурация сохранена в db_config.json.")
        except Exception as e:
            self.lbl_status.setText("🔴 Ошибка переключения базы!")
            QMessageBox.critical(self, "Ошибка подключения", f"Не удалось подключиться к СУБД:\n{e}\n\nТекущее подключение сохранено без изменений.")
