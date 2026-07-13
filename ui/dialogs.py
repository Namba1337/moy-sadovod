"""Диалоги приложения: единая база и стандартные окна.

Заменяет две исторические базы (`_FramelessDialog` из ui.detail_widget и
`_BasePromptDialog` из ui.plots_widget) — у них была продублирована логика
маски/скругления, а стили кнопок и полей каждый диалог копировал себе,
потому что глобальный QSS главного окна до top-level диалогов не доходит.

  BaseDialog    — скруглённая белая карточка без нативной рамки; общий блок
                  стилей (ui.theme.dialog_qss) подключён автоматически;
                  перетаскивается мышью за верхнюю зону; Esc = отмена.
  PromptDialog  — фиксированная раскладка «заголовок + сообщение + кнопки».
  AlertDialog   — однокнопочное информационное окно (замена QMessageBox).
  ConfirmDialog — подтверждение (по умолчанию деструктивное: красная кнопка).
  Save3WayDialog— «Сохранить / Не сохранять / Отмена».
  exec_dialog() — модальный запуск с затемняющим оверлеем и центрированием.

Порядок кнопок единый по приложению: ряд прижат вправо, primary — крайняя
правая, «Отмена» — слева от него.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ui.buttons import DangerButton, PrimaryButton, SecondaryButton
from ui.theme import C, FS, RAD, dialog_qss


class BaseDialog(QDialog):
    """Базовый класс всех диалогов: белая скруглённая карточка без
    стандартной рамки/шапки Windows.

    Карточку (фон + рамка + скругление) рисует paintEvent с антиалиасингом
    на прозрачном окне. Раньше углы вырезала 1-битная setMask-маска — она
    давала грязную «лесенку» без сглаживания (тот же отказ от маски, что и
    _RoundedFrame в main.py). QSS-фон здесь не работает: на top-level
    QDialog с WA_TranslucentBackground Qt его просто не рисует.

    Подклассы могут дополнять стиль: ``self.setStyleSheet(self.base_qss() +
    "…свои правила…")`` — свои правила добавляются ПОСЛЕ общих и потому
    побеждают при равной специфичности.
    """

    _RADIUS = RAD.DIALOG
    _BORDER = C.BORDER          # None/"" у подкласса → карточка без рамки
    _DRAG_ZONE = 44   # высота зоны у верхнего края, за которую окно тянется

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("framelessDialog")
        self._drag_off = None
        self.setStyleSheet(self.base_qss())

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Полупиксельный сдвиг — центр 1px-рамки на границе, иначе она
        # срезается краем окна.
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._BORDER:
            p.setPen(QPen(QColor(self._BORDER)))
        else:
            p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C.BG_SURFACE))
        p.drawRoundedRect(rect, self._RADIUS, self._RADIUS)

    # -- стиль -------------------------------------------------------------

    def base_qss(self) -> str:
        return (
            f"QDialog#framelessDialog{{background:{C.BG_SURFACE};"
            f"border:1px solid {self._BORDER};border-radius:{self._RADIUS}px;}}"
            + dialog_qss()
        )

    # Совместимость: исторические подклассы вызывали _frame_qss().
    _frame_qss = base_qss

    # -- маска скругления (упразднена) ---------------------------------------

    def _apply_mask(self):
        """No-op. Скругление рисует QSS на прозрачном окне; маска больше
        не применяется. Метод сохранён: его зовут исторические подклассы."""

    # -- перетаскивание за верхнюю зону --------------------------------------

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and event.position().y() <= self._DRAG_ZONE):
            self._drag_off = (event.globalPosition().toPoint()
                              - self.frameGeometry().topLeft())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_off is not None and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_off)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_off = None
        super().mouseReleaseEvent(event)

    # -- стандартные элементы -------------------------------------------------

    def make_header(self, title: str, *, closable: bool = False) -> QHBoxLayout:
        """Строка заголовка: титул (QLabel#dlgTitle) + опциональный ✕."""
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(title, objectName="dlgTitle")
        row.addWidget(lbl)
        row.addStretch()
        if closable:
            btn = QPushButton("✕", objectName="btnPanelClose")
            btn.setFixedSize(24, 24)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(self.reject)
            row.addWidget(btn)
        return row

    def make_button_row(self, *buttons: QPushButton) -> QHBoxLayout:
        """Ряд кнопок, прижатый вправо. Передавайте кнопки в визуальном
        порядке слева направо; primary — последней (крайняя правая)."""
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()
        for b in buttons:
            row.addWidget(b)
        return row


def exec_dialog(dlg: QDialog, parent) -> int:
    """exec() с затемняющим оверлеем и центрированием поверх parent/host —
    единая точка входа для модальных вызовов диалогов."""
    dlg.adjustSize()
    overlay = PromptDialog._show_centered(dlg, parent)
    try:
        return dlg.exec()
    finally:
        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()


class PromptDialog(BaseDialog):
    """База уточняющих окон (Confirm/Alert/Save3Way) — фиксированная ширина,
    чтобы короткое и длинное сообщение выглядели одним «прямоугольником»
    (высота свободно растёт под перенос текста)."""

    _WIDTH = 380
    _MARGIN_H = 24  # см. lay.setContentsMargins ниже — по 24px слева/справа
    _BORDER = None  # прежний вид семейства: без рамки (только скругление)

    def __init__(self, title: str, message: str, *, parent=None):
        super().__init__(parent)
        self.setObjectName("confirmDialog")
        self.setStyleSheet(
            f"QDialog#confirmDialog{{background:{C.BG_SURFACE};"
            f"border-radius:{self._RADIUS}px;}}" + dialog_qss())
        self.setModal(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(self._MARGIN_H, 22, self._MARGIN_H, 20)
        lay.setSpacing(8)

        # Ширина текста фиксируется явно (а не через setFixedWidth диалога) —
        # иначе высота, посчитанная adjustSize() ДО применения итоговой
        # ширины диалога, может не совпасть с реальным переносом текста.
        _text_w = self._WIDTH - 2 * self._MARGIN_H

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            f"font-size:{FS.H2}px; font-weight:700; color:{C.TEXT};"
            f"background:transparent;")
        title_lbl.setWordWrap(True)
        title_lbl.setFixedWidth(_text_w)
        lay.addWidget(title_lbl)

        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setFixedWidth(_text_w)
        msg_lbl.setStyleSheet(
            f"font-size:{FS.BODY}px; color:{C.TEXT_MUTED}; background:transparent;")
        lay.addWidget(msg_lbl)

        lay.addSpacing(10)

        self._body_lay = lay
        self._btn_row = QHBoxLayout()
        self._btn_row.setSpacing(8)
        self._btn_row.addStretch()
        lay.addLayout(self._btn_row)

    def _insert_widget(self, widget: QWidget):
        """Вставляет widget между сообщением и строкой кнопок (для окон с
        доп. содержимым — см. _NewGroupDialog в ui.plots_widget)."""
        self._body_lay.insertWidget(self._body_lay.count() - 1, widget)

    def _add_button(self, text: str, style, slot) -> QPushButton:
        """Добавляет кнопку в ряд. style — класс кнопки из ui.buttons
        (Primary/Secondary/DangerButton) или готовая QSS-строка (legacy)."""
        if isinstance(style, str):
            btn = QPushButton(text)
            btn.setStyleSheet(style)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            btn = style(text)
        btn.clicked.connect(slot)
        self._btn_row.addWidget(btn)
        return btn

    def _finalize(self):
        """Вызывается подклассом после добавления всех кнопок: фиксирует
        итоговую ширину и подгоняет высоту/маску под содержимое."""
        self.setFixedWidth(self._WIDTH)
        self.adjustSize()
        self._apply_mask()

    @staticmethod
    def _show_centered(dlg: QDialog, parent):
        """Затемняющий оверлей на хосте + центрирование поверх parent/host.
        Вызывающий должен вручную скрыть/удалить overlay после dlg.exec()
        (см. try/finally в exec_dialog)."""
        host = parent.window() if parent is not None else None
        overlay = None
        if host is not None:
            overlay = QWidget(host)
            overlay.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            overlay.setStyleSheet("background: rgba(17, 24, 39, 0.45);")
            overlay.setGeometry(host.rect())
            overlay.show()
            overlay.raise_()
        if parent is not None:
            geo = parent.geometry() if parent.isWindow() else host.geometry()
            dlg.move(geo.center().x() - dlg.width() // 2,
                     geo.center().y() - dlg.height() // 2)
        return overlay


class AlertDialog(PromptDialog):
    """Однокнопочное информационное окно — замена QMessageBox.warning/
    information/critical в едином визуальном языке приложения."""

    def __init__(self, title: str, message: str, *, ok_text: str = "Понятно",
                 parent=None):
        super().__init__(title, message, parent=parent)
        self._add_button(ok_text, PrimaryButton, self.accept)
        self._finalize()

    @staticmethod
    def show_alert(parent, title: str, message: str, *, ok_text: str = "Понятно"):
        dlg = AlertDialog(title, message, ok_text=ok_text, parent=parent)
        exec_dialog(dlg, parent)


class ConfirmDialog(PromptDialog):
    """Окно подтверждения с двумя исходами: подтвердить / отмена.

    danger=True (по умолчанию) — красная кнопка подтверждения (удаление);
    danger=False — обычная primary (создание/сохранение)."""

    def __init__(self, title: str, message: str, *,
                 confirm_text: str = "Удалить", cancel_text: str = "Отмена",
                 danger: bool = True, parent=None):
        super().__init__(title, message, parent=parent)
        self._add_button(cancel_text, SecondaryButton, self.reject)
        cls = DangerButton if danger else PrimaryButton
        self._confirm_btn = self._add_button(confirm_text, cls, self.accept)
        self._finalize()

    @staticmethod
    def confirm(parent, title: str, message: str, *,
                confirm_text: str = "Удалить", cancel_text: str = "Отмена",
                danger: bool = True) -> bool:
        dlg = ConfirmDialog(title, message, confirm_text=confirm_text,
                            cancel_text=cancel_text, danger=danger, parent=parent)
        return exec_dialog(dlg, parent) == QDialog.DialogCode.Accepted


class Save3WayDialog(PromptDialog):
    """Окно «Сохранить / Не сохранять / Отмена» для несохранённых правок."""

    def __init__(self, title: str, message: str, *, parent=None):
        super().__init__(title, message, parent=parent)
        self.choice = "cancel"

        def _pick(choice: str):
            self.choice = choice
            self.accept()

        self._add_button("Отмена", SecondaryButton, lambda: _pick("cancel"))
        self._add_button("Не сохранять", SecondaryButton, lambda: _pick("discard"))
        self._add_button("Сохранить", PrimaryButton, lambda: _pick("save"))
        self._finalize()

    @staticmethod
    def ask(parent, title: str, message: str) -> str:
        """Возвращает 'save' | 'discard' | 'cancel'."""
        dlg = Save3WayDialog(title, message, parent=parent)
        result = exec_dialog(dlg, parent)
        return dlg.choice if result == QDialog.DialogCode.Accepted else "cancel"
