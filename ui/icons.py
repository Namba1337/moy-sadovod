"""Единая точка иконок приложения.

Все иконки — глифы шрифта Material Symbols Rounded (variable font из
resources/fonts). Обращение по имени, а не по «магическому» codepoint:

    from ui.icons import get_icon, icon_char, icon_font

    btn.setIcon(get_icon("add", 18))
    lbl = QLabel(icon_char("star"));  lbl.setFont(icon_font(16, fill=1))

Именованный реестр ICONS сверен с файлом
``MaterialSymbolsRounded[...].codepoints``. Часть значений — legacy-коды
Material Icons (они дублируются в Symbols-шрифте и уже проверены в бою
текущим кодом) — помечены комментарием «legacy».

Emoji (📄 ⚙ …), текстовые «＋»/«✕» и шрифт "Material Icons" в новых местах
не используются. Исключение — Segoe MDL2 Assets для кнопок min/max/close
заголовка окна (системная идиома Windows, живёт в main.py).
"""
from __future__ import annotations

import os
import tempfile

from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

from ui.theme import C

FONT_FAMILY = "Material Symbols Rounded"

ICONS: dict[str, int] = {
    # Навигация (сайдбар)
    "home":          0xE587,  # cottage
    "plots":         0xF8EE,  # dataset
    "detail":        0xF191,  # table
    "vznosy":        0xEAEC,  # currency_ruble
    "energy":        0xEC1C,  # electric_bolt
    "save_base":     0xF09B,  # upload
    "load_base":     0xF090,  # download
    "chevron_left":  0xE5CB,
    "chevron_right": 0xE5CC,

    # Действия
    "add":           0xE145,
    "close":         0xE5CD,
    "delete":        0xE92B,  # delete_forever (проверен в бою)
    "save":          0xE161,
    "settings":      0xE8B8,
    "refresh":       0xE5D5,
    "search":        0xE8B6,
    "edit":          0xF097,
    "copy":          0xE14D,  # content_copy
    "check":         0xE5CA,
    "expand_more":   0xE5CF,
    "expand_less":   0xE5CE,
    "folder_open":   0xE2C8,
    "history":       0xE8B3,
    "tune":          0xE429,
    "category":      0xE574,

    # Документы / экспорт
    "pdf":           0xE415,  # picture_as_pdf
    "receipt":       0xEF6E,  # receipt_long
    "excel":         0xF1BE,  # table_view
    "calendar":      0xEBCC,  # calendar_month
    "ruler":         0xE41C,  # straighten (нормативы)
    "description":   0xE873,

    # Статусы
    "warning":       0xF083,
    "info":          0xE88E,
    "error":         0xF8B6,
    "lock":          0xE897,  # legacy, проверен в бою (detail_widget)
    "star":          0xE838,  # legacy, проверен в бою (plots_widget)
    "check_box":         0xE834,
    "check_box_blank":   0xE835,

    # Дашборд (KPI-карточки)
    "balance":       0xE84F,  # account_balance
    "collect":       0xE263,  # monetization_on
    "spend":         0xE8A1,  # credit_card
    "flash":         0xE3E7,  # flash_on
    "bars":          0xE26B,  # bar_chart
    "donut":         0xE917,  # donut_large
}


def icon_char(name: str | int) -> str:
    """Глиф иконки как строка (для QLabel.setText + icon_font)."""
    cp = ICONS[name] if isinstance(name, str) else name
    return chr(cp)


def icon_font(px: int, fill: int = 0) -> QFont:
    """Material Symbols Rounded нужного размера. fill: 0=контур, 1=заливка."""
    f = QFont(FONT_FAMILY)
    f.setPixelSize(px)
    try:
        f.setVariableAxis(QFont.Tag(b"FILL"), float(fill))
    except Exception:
        pass
    return f


_OVERSAMPLE = 4  # запас разрешения: Qt всегда down-, а не upscale'ит пиксмап


def get_icon(name: str | int, size: int = 18, color: str = C.BRAND,
             fill: int = 0) -> QIcon:
    """Рендерит иконку в QIcon (для кнопок).

    Пиксмап рисуется с запасом (devicePixelRatio=_OVERSAMPLE) — иначе при
    масштабировании Windows >100% или несовпадении iconSize кнопки Qt
    растягивает низкое разрешение и иконка мылится. Вызывающий код должен
    выставить кнопке setIconSize(QSize(size, size)).
    """
    cp = ICONS[name] if isinstance(name, str) else name
    px = size * _OVERSAMPLE
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(_OVERSAMPLE)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setFont(icon_font(size, fill))
    p.setPen(QColor(color))
    p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, chr(cp))
    p.end()
    return QIcon(pm)


def icon_png_path(name: str | int, size: int = 16, color: str = C.BRAND,
                  fill: int = 0) -> str:
    """Путь к PNG-файлу глифа — для QSS ``image: url(...)``, куда QIcon не
    подставить (например ::down-arrow). Рендерится 1:1 без оверсэмплинга
    (QSS не масштабирует image, а обрезает), кэшируется во временной папке.
    Путь возвращается с прямыми слэшами — обратные QSS не понимает."""
    cp = ICONS[name] if isinstance(name, str) else name
    fname = f"{cp:04x}_{size}_{color.lstrip('#')}_{fill}.png"
    path = os.path.join(tempfile.gettempdir(), "snt_helper_icons", fname)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(icon_font(size, fill))
        p.setPen(QColor(color))
        p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, chr(cp))
        p.end()
        pm.save(path, "PNG")
    return path.replace("\\", "/")
