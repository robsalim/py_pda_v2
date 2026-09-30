"""Общие фрагменты QSS.

Без явной геометрии up/down кнопок стилизованный через QSS спинбокс
растягивает текстовое поле на всю ширину: поле перекрывает стрелки,
и вместо кнопки «вверх» показывается курсор ввода текста.
"""

# Подключать в каждый setStyleSheet, где стилизуются QSpinBox/QDoubleSpinBox
SPIN_BUTTON_QSS = """
    QSpinBox, QDoubleSpinBox { padding: 2px 20px 2px 6px; }
    QSpinBox::up-button, QDoubleSpinBox::up-button, QDateTimeEdit::up-button {
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 18px;
        border-left: 1px solid #55555A;
        border-bottom: 1px solid #3F3F46;
        border-top-right-radius: 4px;
        background: #333337;
    }
    QSpinBox::down-button, QDoubleSpinBox::down-button, QDateTimeEdit::down-button {
        subcontrol-origin: border;
        subcontrol-position: bottom right;
        width: 18px;
        border-left: 1px solid #55555A;
        border-bottom-right-radius: 4px;
        background: #333337;
    }
    QSpinBox::up-button:hover, QSpinBox::down-button:hover,
    QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover,
    QDateTimeEdit::up-button:hover, QDateTimeEdit::down-button:hover {
        background: #49657A;
    }
    QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
    QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed,
    QDateTimeEdit::up-button:pressed, QDateTimeEdit::down-button:pressed {
        background: #007ACC;
    }
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateTimeEdit::up-arrow {
        width: 0; height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-bottom: 5px solid #DDDDDD;
    }
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QDateTimeEdit::down-arrow {
        width: 0; height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid #DDDDDD;
    }
"""
