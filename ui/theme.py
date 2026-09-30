"""Светлая/тёмная тема приложения.

Все цвета UI берутся из токенов палитры. Переключение темы: apply_theme(app,
"light"|"dark") — пересобирает глобальные QSS, красит системную полосу
заголовка окон (Windows DWM) и перекрашивает inline-виджеты, подписанные
через themed()/on_theme().

Шаблоны QSS содержат токены вида {%token%}; themed(widget, tpl) заполняет их
текущей палитрой и подписывает виджет на смену темы (подписка снимается при
уничтожении виджета).
"""
from dataclasses import dataclass
import re

from PyQt6.QtWidgets import QApplication


# ------------------------------------------------------------------ палитры
@dataclass(frozen=True)
class Palette:
    name: str
    # Фоны
    window: str          # фон окна/диалогов
    panel: str           # панели, вкладки, заголовки таблиц, дерево
    input: str           # поля ввода, нейтральные кнопки
    hover: str           # hover кнопок/строк
    disabled_bg: str
    disabled_fg: str
    # Акценты
    accent: str
    accent_dark: str
    accent_soft: str     # выбор в меню/списках светлых тонов
    selection: str       # выделение в таблицах/деревьях
    selection_text: str
    # Рамки
    border: str          # рамка инпутов
    border_soft: str     # разделители/рамки панелей
    border_muted: str    # сетка таблиц, тонкие линии
    # Тексты
    text: str
    text_soft: str
    text_dim: str
    hint: str            # подписи-хинты
    # Статусы
    ok: str
    error: str
    warn: str
    error_bg: str
    value_good: str      # «свежее» значение тега в таблице
    favorite_bg: str     # фон избранного тега (RGBA)
    # Специфичные виджеты
    spin_btn: str
    plot_bg: str
    plot_axis: str
    plot_text: str
    legend_bg: str
    legend_border: str
    handle: str          # ручка ресайза графика
    scrollbar: str
    scrollbar_hover: str
    text_muted: str      # приглушённый сине-серый (счётчики, подписи)
    text_faint: str      # самый тусклый служебный текст
    # Цветные кнопки действий
    btn_add: str
    btn_add_border: str
    btn_action: str
    btn_action_border: str
    btn_action_hover: str
    btn_action_pressed: str
    btn_action_border_hover: str
    btn_violet: str
    btn_violet_border: str
    btn_danger: str
    btn_danger_border: str
    btn_teal: str
    btn_teal_border: str
    chip_line: str       # крестик удаления в чипе легенды
    cursor_x1: str       # подписи и курсоры X1/X2/ΔT на графике
    cursor_x1_hover: str
    cursor_x2: str
    cursor_x2_hover: str
    cursor_dt: str
    crosshair: str       # перекрестие под мышью
    grid_alpha: float    # прозрачность сетки графика
    title_bar: bool      # тёмная системная полоса заголовка


DARK = Palette(
    name="dark",
    window="#1E1E1E", panel="#252526", input="#2D2D30", hover="#3E3E42",
    disabled_bg="#252526", disabled_fg="#656565",
    accent="#007ACC", accent_dark="#005999", accent_soft="#007ACC",
    selection="#007ACC", selection_text="#FFFFFF",
    border="#55555A", border_soft="#3F3F46", border_muted="#2D2D30",
    text="#FFFFFF", text_soft="#D4D4D4", text_dim="#A0A0A0",
    hint="#9CDCFE",
    ok="#7FBD8A", error="#E08A8A", warn="#E0B04A", error_bg="#3A2A2A",
    value_good="#8FCF8F", favorite_bg="#5A3F596B",
    spin_btn="#333337",
    plot_bg="#181818", plot_axis="#858585", plot_text="#D8D8D8",
    legend_bg="rgba(28, 28, 28, 180)", legend_border="rgba(130, 130, 130, 80)",
    handle="#3F596B",
    scrollbar="#424242", scrollbar_hover="#686868",
    text_muted="#9DA5B4",
    text_faint="#858585",
    btn_add="#466653", btn_add_border="#628570",
    btn_action="#49657A", btn_action_border="#647C8C",
    btn_action_hover="#5A778D", btn_action_pressed="#314653",
    btn_action_border_hover="#718A99",
    btn_violet="#665477", btn_violet_border="#806B91",
    btn_danger="#7A4B50", btn_danger_border="#956168",
    btn_teal="#476B73", btn_teal_border="#64858A",
    chip_line="#C8C8C8",
    cursor_x1="#FFD700", cursor_x1_hover="#FFE600",
    cursor_x2="#00E5FF", cursor_x2_hover="#80F3FF",
    cursor_dt="#00E676",
    crosshair="#9DA5B4", grid_alpha=0.35,
    title_bar=True,
)

LIGHT = Palette(
    name="light",
    window="#F3F3F3", panel="#EAEAEA", input="#FFFFFF", hover="#DADEE4",
    disabled_bg="#E0E0E0", disabled_fg="#9A9A9A",
    accent="#007ACC", accent_dark="#005999", accent_soft="#0063B1",
    selection="#0078D4", selection_text="#FFFFFF",
    border="#B9BCC2", border_soft="#D0D3D8", border_muted="#E0E3E7",
    text="#1F1F1F", text_soft="#3A3A3A", text_dim="#5C5C5C",
    hint="#0B5FA5",
    ok="#2E7D32", error="#C0392B", warn="#9A6A00", error_bg="#FBE4E2",
    value_good="#2E7D32", favorite_bg="#280078D4",
    spin_btn="#E4E6EA",
    plot_bg="#FFFFFF", plot_axis="#9AA0A6", plot_text="#44474B",
    legend_bg="rgba(255, 255, 255, 225)", legend_border="rgba(154, 160, 166, 150)",
    handle="#AFC4D6",
    scrollbar="#C2C4C9", scrollbar_hover="#A2A5AB",
    text_muted="#5C6B7A",
    text_faint="#8A8F98",
    btn_add="#5E8F6E", btn_add_border="#4E7B5C",
    btn_action="#5B7C99", btn_action_border="#4C6A84",
    btn_action_hover="#6D8EA9", btn_action_pressed="#466075",
    btn_action_border_hover="#3F596E",
    btn_violet="#8A6BA8", btn_violet_border="#71578A",
    btn_danger="#C25E66", btn_danger_border="#A34F57",
    btn_teal="#5F8B95", btn_teal_border="#4F767F",
    chip_line="#5A5A5A",
    cursor_x1="#8A6D00", cursor_x1_hover="#B58F00",
    cursor_x2="#0077A0", cursor_x2_hover="#009FD4",
    cursor_dt="#1B7F3B",
    crosshair="#7A828C", grid_alpha=0.30,
    title_bar=False,
)

PALETTES = {"dark": DARK, "light": LIGHT}

THEME_FILE = "ui_theme.json"

_CURRENT = DARK
_listeners = []
_title_windows = []


def save_choice(name: str):
    """Запоминает выбор темы между запусками."""
    import json
    try:
        with open(THEME_FILE, "w", encoding="utf-8") as f:
            json.dump({"theme": name}, f)
    except Exception:
        pass


def load_choice(default: str = "dark") -> str:
    """Загружает сохранённую тему (по умолчанию тёмная)."""
    import json
    import os
    try:
        with open(THEME_FILE, encoding="utf-8") as f:
            name = json.load(f).get("theme", default)
        return name if name in PALETTES else default
    except Exception:
        return default


def current() -> Palette:
    return _CURRENT


def current_name() -> str:
    return _CURRENT.name


def other_name() -> str:
    return "light" if _CURRENT.name == "dark" else "dark"


# --------------------------------------------------------------- токены QSS
_TOKEN = re.compile(r"\{%([A-Za-z0-9_]+)%\}")


def S(name: str) -> str:
    """Токен темы для подстановки в f-шаблон: S('text') -> '{%text%}'."""
    return "{%" + name + "%}"


def fill(tpl: str, p: Palette = None) -> str:
    """Заменяет {%token%} на значение из палитры (по умолчанию — текущей)."""
    p = p or _CURRENT
    def _sub(m):
        key = m.group(1)
        val = getattr(p, key, None)
        if val is None:
            raise KeyError(f"Неизвестный токен темы: {key}")
        return str(val)
    return _TOKEN.sub(_sub, tpl)


def hint_qss(p: Palette, state: str = "hint", font_size: int = 11) -> str:
    """Подпись-подсказка: state = hint|ok|error|warn|dim|faint."""
    color = getattr(p, {"hint": "hint", "ok": "ok",
                        "error": "error", "warn": "warn",
                        "dim": "text_dim", "faint": "text_faint"}.get(state, "hint"))
    return f"color: {color}; font-size: {font_size}px; padding-left: 2px;"


def field_error_qss(p: Palette) -> str:
    """Поле ввода с ошибкой валидации (подсвеченный фон и рамка)."""
    return (f"QLineEdit {{ background-color: {p.error_bg}; "
            f"border: 1px solid {p.error}; color: {p.text}; }}")


# Вид action-кнопок: фоновая кнопка + тонкая рамка того же тона
_BTN_KINDS = {
    "add": "btn_add",          # зелёная (создать/добавить)
    "action": "btn_action",    # синяя (главное действие)
    "violet": "btn_violet",    # фиолетовая (изменить/импорт)
    "danger": "btn_danger",    # красная (удалить)
    "teal": "btn_teal",        # сине-зелёная (экспорт/web/PDF)
    "neutral": "hover",        # серая (пауза)
}


def btn_qss(kind: str, bold: bool = True, border: bool = True) -> str:
    """Шаблон QSS цветной кнопки с токенами (для themed/builders)."""
    base = _BTN_KINDS[kind]
    parts = ["background-color: {%" + base + "%};", " color: {%text%};"]
    if border and base != "hover":
        parts.append(" border: 1px solid {%" + base + "_border%};")
    if bold:
        parts.append(" font-weight: bold;")
    return "".join(parts)


# Общие шаблоны для виджетов графиков
COMBO_QSS = """
    QComboBox { background-color: {%input%}; color: {%text%}; border: 1px solid {%border%}; border-radius: 4px; padding: 4px 8px; font-size: 12px; }
    QComboBox::drop-down { border: none; }
    QComboBox QAbstractItemView { background-color: {%panel%}; color: {%text%}; selection-background-color: {%selection%}; selection-color: {%selection_text%}; }
"""

ACTION_BTN_QSS = """
    QPushButton { background-color: {%btn_action%}; color: {%text%}; border: 1px solid {%btn_action_border_hover%}; border-radius: 4px; padding: 4px 10px; font-size: 12px; font-weight: bold; }
    QPushButton:hover { background-color: {%btn_action_hover%}; border-color: {%btn_action_border_hover%}; }
"""

DTEDIT_QSS = """
    QDateTimeEdit { background-color: {%input%}; color: {%text%}; border: 1px solid {%border%}; border-radius: 4px; padding: 4px 6px; font-size: 12px; }
    QCalendarWidget QWidget { background-color: {%panel%}; color: {%text%}; }
"""

LOAD_BTN_QSS = """
    QPushButton { background-color: {%btn_action%}; color: {%text%}; font-weight: bold; border-radius: 4px; padding: 5px 14px; font-size: 12px; border: 1px solid {%btn_action_hover%}; }
    QPushButton:hover { background-color: {%btn_action_hover%}; }
"""


def on_theme(callback):
    """callback(palette) вызывается при каждой смене темы."""
    _listeners.append(callback)


def themed(widget, tpl):
    """Перекрашивает виджет сейчас и подписывает его на смену темы.

    tpl — строка QSS с токенами {%%token%%} либо callable(palette) -> str
    (для динамических состояний). Возвращает функцию повторного применения
    apply(palette=None) — удобно вызывать после смены внутреннего состояния.
    """
    def _apply(p=None):
        p = p or _CURRENT
        style = tpl(p) if callable(tpl) else tpl
        widget.setStyleSheet(fill(style, p))
    _apply()
    _listeners.append(_apply)
    widget.destroyed.connect(lambda: _listeners.remove(_apply) if _apply in _listeners else None)
    return _apply


# --------------------------------------------------------------- глобальные QSS
def spin_qss(p: Palette) -> str:
    """SPIN_BUTTON_QSS с заменой дефолтных (тёмных) цветов на токены палитры."""
    from ui.styles import SPIN_BUTTON_QSS
    out = SPIN_BUTTON_QSS
    for old, new in (
        ("#55555A", p.border), ("#3F3F46", p.border_soft),
        ("#333337", p.spin_btn), ("#49657A", p.btn_action),
        ("#FFFFFF", p.text),
    ):
        out = out.replace(old, new)
    return out


def app_qss(p: Palette) -> str:
    """Глобальный QSS приложения: кнопки, поля, меню, диалоги, подсказки."""
    return f"""
        QToolTip {{
            background-color: {p.panel};
            color: {p.text_soft};
            border: 1px solid {p.border};
        }}
        QPushButton {{
            background-color: {p.input};
            color: {p.text};
            border: 1px solid {p.border_soft};
            border-radius: 4px;
            padding: 5px 12px;
            font-size: 12px;
            font-weight: 500;
            outline: none;
        }}
        QPushButton:hover {{ background-color: {p.hover}; border-color: {p.accent}; }}
        QPushButton:pressed {{ background-color: {p.accent}; border-color: {p.accent_dark}; }}
        QPushButton:disabled {{
            background-color: {p.disabled_bg};
            color: {p.disabled_fg};
            border-color: {p.border_muted};
        }}
        QDialog, QFileDialog, QMessageBox {{
            background-color: {p.window};
            color: {p.text};
        }}
        QDialog QLabel, QFileDialog QLabel, QMessageBox QLabel {{
            background: transparent;
            color: {p.text_soft};
        }}
        QLineEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QComboBox {{
            background-color: {p.input};
            color: {p.text};
            border: 1px solid {p.border};
            border-radius: 4px;
            padding: 4px 6px;
            selection-background-color: {p.accent_soft};
            selection-color: {"#FFFFFF" if p.name == "light" else p.text};
        }}
        QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
        QDateTimeEdit:disabled, QComboBox:disabled {{
            background-color: {p.disabled_bg};
            color: {p.disabled_fg};
        }}
        QComboBox::drop-down {{ border: none; }}
        QComboBox QAbstractItemView, QListView, QTreeView, QTableView {{
            background-color: {p.panel};
            color: {p.text};
            border: 1px solid {p.border};
            selection-background-color: {p.accent_soft};
            selection-color: {"#FFFFFF" if p.name == "light" else p.text};
        }}
        QMenu {{ background-color: {p.panel}; color: {p.text}; border: 1px solid {p.border_soft}; }}
        QMenu::item:selected {{ background-color: {p.accent_soft}; color: #FFFFFF; }}
        QMenu::separator {{ height: 1px; background: {p.border_soft}; margin: 4px 8px; }}
        QCalendarWidget QWidget {{ background-color: {p.panel}; color: {p.text}; }}
        QCalendarWidget QAbstractItemView {{
            background-color: {p.input}; color: {p.text};
            selection-background-color: {p.accent_soft}; selection-color: #FFFFFF;
        }}
    """ + spin_qss(p)


def window_qss(p: Palette) -> str:
    """QSS уровня главного окна: базовые виджеты, вкладки, деревья, таблицы."""
    return f"""
        QMainWindow, QWidget {{
            background-color: {p.window};
            color: {p.text_soft};
            font-family: 'Segoe UI', Tahoma, sans-serif;
            font-size: 12px;
        }}
        QLabel {{ background: transparent; color: {p.text_soft}; }}
        QTabWidget::pane {{
            border: 1px solid {p.border_soft};
            background-color: {p.window};
        }}
        QTabBar {{ background: transparent; }}
        QTabBar::tab {{
            background: {p.panel};
            color: {p.text_dim};
            padding: 8px 20px;
            font-weight: bold;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
            border: 1px solid {p.border_muted};
            margin-right: 2px;
        }}
        QTabBar::tab:selected {{
            background: {p.window};
            color: {p.text};
            border-top: 2px solid {p.accent};
            border-bottom: none;
        }}
        QTabBar::tab:hover:!selected {{ color: {p.text_soft}; }}
        QTreeWidget, QTreeView {{
            background-color: {p.panel};
            color: {p.text};
            border: 1px solid {p.border_soft};
            outline: none;
        }}
        QTreeView::item:hover {{ background-color: {p.hover}; }}
        QTreeView::item:selected {{ background-color: {p.selection}; color: {p.selection_text}; }}
        QTableWidget, QTableView {{
            background-color: {p.window};
            color: {p.text};
            gridline-color: {p.border_muted};
            border: 1px solid {p.border_soft};
            selection-background-color: {p.selection};
            selection-color: {p.selection_text};
            outline: none;
        }}
        QHeaderView::section {{
            background-color: {p.panel};
            color: {p.text};
            font-weight: bold;
            border: 1px solid {p.border_soft};
            padding: 5px;
        }}
        QTableCornerButton::section {{
            background-color: {p.panel};
            border: 1px solid {p.border_soft};
        }}
        QSplitter::handle {{ background-color: {p.border_muted}; }}
        QSplitter::handle:hover {{ background-color: {p.accent}; }}
        QGroupBox {{
            border: 1px solid {p.border_soft};
            border-radius: 4px;
            margin-top: 10px;
            background-color: transparent;
        }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}
        QCheckBox {{ background: transparent; spacing: 6px; }}
        QRadioButton {{ background: transparent; spacing: 6px; }}
        QScrollBar:vertical {{
            border: none; background: {p.window}; width: 10px; margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {p.scrollbar}; min-height: 20px; border-radius: 4px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {p.scrollbar_hover}; }}
        QScrollBar:horizontal {{
            border: none; background: {p.window}; height: 10px; margin: 0;
        }}
        QScrollBar::handle:horizontal {{
            background: {p.scrollbar}; min-width: 20px; border-radius: 4px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {p.scrollbar_hover}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """ + spin_qss(p)


# --------------------------------------------------------------- применение темы
def apply_theme(app: QApplication, name: str) -> Palette:
    """Применяет тему глобально: палитра, QSS, полосы заголовков, подписчики."""
    global _CURRENT
    p = PALETTES.get(name, DARK)
    _CURRENT = p
    app.setStyleSheet(app_qss(p) + window_qss(p))
    for win in list(_title_windows):
        set_title_bar_dark(win, p.title_bar)
    for cb in list(_listeners):
        try:
            cb(p)
        except Exception:
            pass
    return p


def register_title_window(win):
    """Окно перекрашивает системную полосу заголовка вместе с темой."""
    if win not in _title_windows:
        _title_windows.append(win)
    set_title_bar_dark(win, _CURRENT.title_bar)
    win.destroyed.connect(lambda: _title_windows.remove(win) if win in _title_windows else None)


def set_title_bar_dark(win, dark: bool):
    """Цвет системной полосы заголовка окна (Windows DWM, атрибут 20)."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        val = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(win.winId()), 20, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass
