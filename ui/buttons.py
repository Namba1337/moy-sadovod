"""Переиспользуемые кнопки приложения.

Каждый класс несёт свой стиль сам (setStyleSheet на себя), поэтому выглядит
одинаково и во вкладках главного окна, и в отдельных диалогах — глобальный
QSS ему не нужен. Инлайн-стилизация QPushButton по месту не используется.

    PrimaryButton("Сохранить")                     — главное действие (бренд)
    SecondaryButton("Отмена")                      — второстепенное
    DangerButton("Удалить")                        — деструктивное
    GhostButton(icon="add", tooltip="Добавить")    — прозрачная иконочная
    LinkButton("Ввести ключ")                      — текст-ссылка

Иконка добавляется параметром icon="name" (см. ui.icons.ICONS).
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QPushButton

from ui import icons
from ui.theme import C, FS, RAD


class _BaseButton(QPushButton):
    """Общее для всех кнопок: курсор-рука, опциональная иконка."""

    _ICON_SIZE = 18
    _ICON_COLOR = C.BRAND

    def __init__(self, text: str = "", parent=None, *, icon: str | None = None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if icon is not None:
            self.setIcon(icons.get_icon(icon, self._ICON_SIZE, color=self._ICON_COLOR))
            self.setIconSize(QSize(self._ICON_SIZE, self._ICON_SIZE))
        self.setStyleSheet(self._qss())

    def _qss(self) -> str:  # pragma: no cover - переопределяется
        return ""


class PrimaryButton(_BaseButton):
    """Главное действие: бренд-заливка, белый текст. Одна на диалог/панель."""

    _ICON_COLOR = "#FFFFFF"

    def _qss(self) -> str:
        return f"""
            QPushButton {{
                background: {C.BRAND}; color: #FFFFFF; border: none;
                border-radius: {RAD.CONTROL}px; padding: 7px 18px;
                font-size: {FS.BODY}px; font-weight: 600;
            }}
            QPushButton:hover   {{ background: {C.BRAND_HOVER}; }}
            QPushButton:pressed {{ background: {C.BRAND_PRESSED}; }}
            QPushButton:disabled {{ background: #A9BFC5; color: #EEF2F0; }}
        """


class SecondaryButton(_BaseButton):
    """Второстепенное действие: белая с рамкой."""

    _ICON_COLOR = C.TEXT_BODY

    def _qss(self) -> str:
        return f"""
            QPushButton {{
                background: {C.BG_SURFACE}; color: {C.TEXT_BODY};
                border: 1px solid {C.BORDER}; border-radius: {RAD.CONTROL}px;
                padding: 6px 14px; font-size: {FS.BODY}px;
            }}
            QPushButton:hover   {{ background: {C.BG_HOVER}; color: {C.TEXT}; }}
            QPushButton:pressed {{ background: {C.BORDER_LIGHT}; }}
            QPushButton:disabled {{ color: {C.TEXT_FAINT}; background: {C.BG_SUBTLE}; }}
        """


class DangerButton(_BaseButton):
    """Деструктивное действие (удаление)."""

    _ICON_COLOR = "#FFFFFF"

    def _qss(self) -> str:
        return f"""
            QPushButton {{
                background: {C.DANGER}; color: #FFFFFF; border: none;
                border-radius: {RAD.CONTROL}px; padding: 7px 18px;
                font-size: {FS.BODY}px; font-weight: 600;
            }}
            QPushButton:hover   {{ background: {C.DANGER_HOVER}; }}
            QPushButton:pressed {{ background: #991B1B; }}
            QPushButton:disabled {{ background: {C.DANGER_MUTED}; color: #FFFFFF; }}
        """


class GhostButton(_BaseButton):
    """Прозрачная иконочная кнопка (панели инструментов, шапки карточек).

    По умолчанию 28×28 с иконкой 18px; size=32 даёт крупный вариант шапки
    списка. danger=True — красная иконка и красноватый hover.
    """

    def __init__(self, parent=None, *, icon: str, tooltip: str = "",
                 size: int = 28, icon_size: int = 18, danger: bool = False,
                 color: str | None = None, hover_bg: str | None = None):
        self._danger = danger
        self._hover_bg = hover_bg or (C.DANGER_BG if danger else C.BRAND_GHOST)
        icon_color = color or (C.DANGER if danger else C.BRAND)
        super().__init__("", parent)
        self.setFixedSize(size, size)
        self.setIcon(icons.get_icon(icon, icon_size, color=icon_color))
        self.setIconSize(QSize(icon_size, icon_size))
        if tooltip:
            self.setToolTip(tooltip)

    def _qss(self) -> str:
        return f"""
            QPushButton {{
                background: transparent; border: none;
                border-radius: {RAD.CONTROL}px; padding: 2px;
            }}
            QPushButton:hover {{ background: {self._hover_bg}; }}
            QPushButton:disabled {{ background: transparent; }}
        """


class LinkButton(_BaseButton):
    """Текстовая ссылка-кнопка."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setFlat(True)

    def _qss(self) -> str:
        return f"""
            QPushButton {{
                background: transparent; border: none; color: {C.BRAND};
                font-size: {FS.BODY}px; text-decoration: underline;
                text-align: left; padding: 0px;
            }}
            QPushButton:hover {{ color: {C.BRAND_HOVER}; }}
        """
