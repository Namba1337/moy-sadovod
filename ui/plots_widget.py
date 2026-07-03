import json
import os
import re
import shutil
from datetime import date, datetime

import pandas as pd
from PyQt6.QtCore import (
    Qt, QDate, QEvent, QModelIndex, QAbstractItemModel, QAbstractListModel,
    QObject, QPoint, QRect, QRectF,
    QRegularExpression, QSize, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QBitmap, QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPen,
    QPixmap, QPolygon, QRegion, QRegularExpressionValidator,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QCompleter, QListView, QMessageBox, QPushButton, QScrollArea, QScrollBar, QSizePolicy, QStyle, QStyledItemDelegate,
    QStyleFactory, QStyleOptionViewItem, QTableWidget, QTableWidgetItem, QTreeView,
    QVBoxLayout, QWidget,
)

from core import ownership
from core import people as people_reg
from core.utils import DATA_DIR, fmt_money


# Требования к документам по роли: required=True → «Отсутствует», False → «Не требуется»
_DOC_REQUIRED = {
    "contact": {"opd": True,  "egrn": False, "member": False},
    "owner":   {"opd": True,  "egrn": True,  "member": False},
    "member":  {"opd": True,  "egrn": True,  "member": True},
}

# Цвета круглого фона звезды-«избранного» по роли контакта (bg, fg)
_ROLE_COLORS = {
    "contact": ("#E5E7EB", "#6B7280"),
    "owner":   ("#C9D8E2", "#07414F"),
    "member":  ("#D6EBD5", "#2E7D32"),
}

_INPUT_SS = (
    "QLineEdit{background:#F8F9FA; border:1px solid #D1D5DB;"
    "border-radius:4px; padding:4px 8px; font-size:12px; color:#1F2937;}"
    "QLineEdit:focus{border:1px solid #07414F;}")
_INPUT_ERROR_SS = (
    "QLineEdit{background:#FEF2F2; border:1px solid #DC2626;"
    "border-radius:4px; padding:4px 8px; font-size:12px; color:#1F2937;}"
    "QLineEdit:focus{border:1px solid #DC2626;}")
_FIELD_LABEL_SS = "font-size:10px; color:#9CA3AF; background:transparent;"


def _plot_num_key(s: str):
    try:
        return (0, int(s), s)
    except ValueError:
        return (1, 0, s)


# Базовые аксессоры — единый источник логики в core.ownership
# (обратная совместимость со строками и старым полем relation внутри).
_owner_name = ownership.owner_name
_is_owner = ownership.is_owner
_owner_area = ownership.owner_area


def _owner_share_str(owner) -> str:
    """Доля в праве как строка (для поля ввода), напр. '1/2'. Иначе ''."""
    if isinstance(owner, dict):
        return str(owner.get("share", "") or "")
    return ""


def _is_visible(owner) -> bool:
    if isinstance(owner, dict):
        return bool(owner.get("is_visible", False))
    return False


def _owner_member_doc(owner) -> str:
    if isinstance(owner, dict):
        return owner.get("member_doc", "")
    return ""


def _split_name(full_name: str) -> tuple:
    """'Иванов Иван Иванович' → ('Иванов', 'Иван', 'Иванович')."""
    parts = (full_name or "").strip().split(maxsplit=2)
    return (parts[0] if parts else "",
            parts[1] if len(parts) > 1 else "",
            parts[2] if len(parts) > 2 else "")


def _mat_font(pixel_size: int = 20, fill: int = 0) -> QFont:
    """Material Symbols Rounded с нужным FILL axis (0=outline, 1=filled)."""
    f = QFont("Material Symbols Rounded")
    f.setPixelSize(pixel_size)
    try:
        f.setVariableAxis(QFont.Tag(b"FILL"), float(fill))
    except Exception:
        pass
    return f


_ICON_OVERSAMPLE = 4  # запас разрешения, чтобы Qt всегда down-, а не upscale'ил


def _mat_icon(codepoint: int, size: int = 16, fill: int = 0,
              color: str = "#07414F") -> QIcon:
    """Рендерит символ Material Symbols в QIcon (для кнопок с текстом).

    Пиксмап рисуется с запасом (devicePixelRatio=_ICON_OVERSAMPLE) — иначе
    при масштабировании Windows >100% или при iconSize кнопки, не совпадающем
    с size, Qt растягивает пиксель-в-пиксель низкое разрешение, и иконка
    получается размытой. Оверсэмплинг гарантирует, что Qt всегда уменьшает
    (а не увеличивает) картинку, что даёт чёткий результат при любом масштабе.
    Вызывающий код должен выставить кнопке setIconSize(QSize(size, size)),
    иначе пиксмап всё равно смасштабируется под чужой размер иконки.
    """
    px = size * _ICON_OVERSAMPLE
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(_ICON_OVERSAMPLE)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setFont(_mat_font(size, fill))
    p.setPen(QColor(color))
    p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, chr(codepoint))
    p.end()
    return QIcon(pm)


_F_STAR_OUTLINE  = _mat_font(20, fill=0)
_F_STAR_FILLED   = _mat_font(20, fill=1)
_F_CHEVRON       = _mat_font(18, fill=0)
_F_COPY_OUTLINE  = _mat_font(18, fill=0)
def _make_decorative_star(role_key: str = "contact") -> QLabel:
    """Некликабельная звезда «избранный» для диалогов создания (_NewGroupDialog,
    _QuickAddPlotDialog) — тот же визуальный язык (форма/цвета), что и активная
    звезда в превью контактов группы (см. _refresh_contacts_preview).

    Рисуется через _mat_icon() (растровая иконка с оверсэмплингом), а НЕ через
    QFont-глиф на QLabel.setText() — текстовый рендер шрифта Material Symbols
    оказался чувствителен к субпиксельному позиционированию окна и давал чуть
    разный видимый размер звезды в разных диалогах при полностью идентичном
    коде; растровая иконка от этого не зависит (как и все остальные иконки в
    приложении)."""
    role_bg, _ = _ROLE_COLORS[role_key]
    star = QLabel()
    star.setFixedSize(18, 18)
    star.setAlignment(Qt.AlignmentFlag.AlignCenter)
    star.setStyleSheet(f"background:{role_bg}; border-radius:9px;")
    star.setPixmap(_mat_icon(0xE838, 12, fill=1, color="#07414F").pixmap(12, 12))
    return star


_SS_DIRTY_BADGE = (
    "QLabel{background:#DCFCE7;color:#16A34A;border-radius:4px;"
    "padding:0 8px;font-size:11px;}")


def _people_names(people: list) -> list:
    """Уникальные (без учёта регистра) непустые имена из реестра людей —
    источник для QCompleter автодополнения ФИО."""
    seen, names = set(), []
    for p in people:
        nm = str(p.get("name", "")).strip()
        if nm and nm.casefold() not in seen:
            seen.add(nm.casefold())
            names.append(nm)
    return names


def _normalize_phone(raw: str) -> str:
    """Нормализует произвольную строку в '+7 (XXX) XXX-XX-XX'.

    Возвращает пустую строку если цифр меньше 10 (неполный номер).
    """
    digits = re.sub(r"\D", "", raw)
    if digits and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    return f"+7 ({digits[:3]}) {digits[3:6]}-{digits[6:8]}-{digits[8:10]}"


def _phone_fmt(d: str) -> str:
    """Форматирует строку из цифр (до 10) в +7 (XXX) XXX-XX-XX."""
    if not d:
        return ""
    r = "+7 (" + d[:3]
    if len(d) > 3:
        r += ") " + d[3:6]
    if len(d) > 6:
        r += "-" + d[6:8]
    if len(d) > 8:
        r += "-" + d[8:10]
    return r


_PHONE_PREFIX = "+7 ("  # фиксированная часть маски — не считается «цифрами номера»


def _pos_after_n_digits(formatted: str, n: int) -> int:
    """Позиция в отформатированной строке сразу после n-й цифры НОМЕРА
    (цифра '7' из фиксированного префикса '+7 (' не считается)."""
    if not formatted.startswith(_PHONE_PREFIX):
        return len(formatted)
    if n <= 0:
        return len(_PHONE_PREFIX)
    seen = 0
    for i in range(len(_PHONE_PREFIX), len(formatted)):
        if formatted[i].isdigit():
            seen += 1
            if seen == n:
                return i + 1
    return len(formatted)


def _setup_phone_input(inp: "QLineEdit") -> None:
    """Маскирует поле телефона без setInputMask (тонкий курсор, нет шаблона вне edit-режима).

    Курсор всегда пересчитывается от числа цифр перед ним в уже отредактированном
    тексте — правка/удаление работает корректно в любом месте номера, не только в конце.
    """
    inp.setMaxLength(len("+7 (XXX) XXX-XX-XX"))

    def _on_edited(text: str) -> None:
        cursor = inp.cursorPosition()
        digits_before = sum(1 for ch in text[:cursor] if ch.isdigit())
        raw = re.sub(r"\D", "", text)
        # Ведущие 7/8 (код страны) никогда не записываются — номер уже
        # начинается с фиксированного +7, набор идёт сразу с кода оператора.
        if raw and raw[0] in "78":
            raw = raw[1:]
            digits_before = max(0, digits_before - 1)
        raw = raw[:10]
        digits_before = min(digits_before, len(raw))
        result = _phone_fmt(raw)
        if result != text:
            inp.setText(result)
            inp.setCursorPosition(_pos_after_n_digits(result, digits_before))

    inp.textEdited.connect(_on_edited)


def _make_anchor_label(text: str, style: str):
    """Returns (label, row_widget, dirty_badge).
    row_widget — QWidget фиксированной высоты с меткой и бейджем «Данные обновлены».
    Фиксированная высота предотвращает вертикальный сдвиг элементов при появлении бейджа,
    а отсутствие retainSizeWhenHidden предотвращает горизонтальное переполнение viewport.
    Dirty badge: управляется извне по _refresh_dirty_badges."""
    lbl = QLabel(text)
    lbl.setStyleSheet(style)

    dirty = QLabel("Данные обновлены")
    dirty.setStyleSheet(_SS_DIRTY_BADGE)
    dirty.hide()

    # Контейнер фиксированной высоты: бейдж может появляться/исчезать,
    # не меняя высоту строки и не сдвигая элементы ниже.
    row_w = QWidget()
    row_w.setStyleSheet("background:transparent;")
    row_lay = QHBoxLayout(row_w)
    row_lay.setContentsMargins(0, 0, 0, 0)
    row_lay.setSpacing(6)
    row_lay.addWidget(lbl, 0, Qt.AlignmentFlag.AlignVCenter)
    row_lay.addWidget(dirty, 0, Qt.AlignmentFlag.AlignVCenter)
    row_lay.addStretch()
    # Высота = бейдж с паддингом; задаётся после adjustSize чтобы учесть DPI
    dirty.adjustSize()
    row_w.setFixedHeight(dirty.sizeHint().height() + 2)
    return lbl, row_w, dirty


def _make_copy_btn(target_inp) -> "QPushButton":
    from PyQt6.QtWidgets import QPushButton as _QB
    btn = _QB(chr(0xE2EC))
    btn.setFont(_F_COPY_OUTLINE)
    btn.setFixedSize(26, 26)
    btn.setFlat(True)
    btn.setStyleSheet(
        "QPushButton{background:transparent;border:none;color:#C9D8E2;padding:0;}")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)

    def _enter(e, b=btn):
        b.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#07414F;padding:0;}")
        _QB.enterEvent(b, e)

    def _leave(e, b=btn):
        b.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#C9D8E2;padding:0;}")
        _QB.leaveEvent(b, e)

    def _on_click():
        text = target_inp.text()
        if text:
            QApplication.clipboard().setText(text)

    btn.enterEvent = _enter
    btn.leaveEvent = _leave
    btn.clicked.connect(_on_click)
    return btn


def _make_warn_pill(parent, text: str, *, tooltip: str = "") -> QWidget:
    """Пилюля-предупреждение (жёлто-коричневая, с иконкой) — общий стиль для
    бейджей «не хватает документов» и «номер занят»."""
    badge = QWidget(parent)
    badge.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    badge.setObjectName("docsBadge")
    badge.setFixedHeight(18)
    badge.setStyleSheet(
        "QWidget#docsBadge{background:#F9DCA4;border-radius:9px;}")
    if tooltip:
        badge.installEventFilter(_TooltipFilter(tooltip, badge))
    badge_lay = QHBoxLayout(badge)
    badge_lay.setContentsMargins(6, 0, 7, 0)
    badge_lay.setSpacing(3)
    badge_icon = QLabel(chr(0xE000), badge)
    badge_icon.setFont(_mat_font(12))
    badge_icon.setStyleSheet("color:#73451A; background:transparent;")
    badge_lay.addWidget(badge_icon)
    badge_txt = QLabel(text, badge)
    badge_txt.setStyleSheet(
        "font-size:10px; font-weight:600; color:#73451A; background:transparent;")
    badge_lay.addWidget(badge_txt)
    return badge


def _make_docs_badge(parent, have: int, total: int) -> QWidget:
    """Пилюля «не хватает документов»: X/N загружено из требуемых."""
    return _make_warn_pill(
        parent, f"{have}/{total}",
        tooltip=f"Не хватает документов: {total - have} из {total}")


def _owner_opd_doc(owner) -> str:
    if isinstance(owner, dict):
        return owner.get("opd_doc", "")
    return ""


def _owner_phone(owner) -> str:
    if isinstance(owner, dict):
        return owner.get("phone", "")
    return ""


def _owner_email(owner) -> str:
    if isinstance(owner, dict):
        return owner.get("email", "")
    return ""


def _make_owner(name: str, is_owner: bool = True, area: float | None = None,
                is_visible: bool = False, member_doc: str = "",
                opd_doc: str = "", phone: str = "",
                email: str = "", since: str = "", until: str = "",
                share: str = "", egrn_doc: str = "",
                is_member: bool = False, person_id: str = "") -> dict:
    d: dict = {"name": name, "is_owner": is_owner}
    if person_id:
        d["person_id"] = person_id
    if area is not None:
        d["area"] = area
    if is_visible:
        d["is_visible"] = True
    if is_member:
        d["is_member"] = True
    if member_doc:
        d["member_doc"] = member_doc
    if opd_doc:
        d["opd_doc"] = opd_doc
    if egrn_doc:
        d["egrn_doc"] = egrn_doc
    if phone:
        d["phone"] = phone
    if email:
        d["email"] = email
    if since:
        d["since"] = since
    if until:
        d["until"] = until
    if share:
        d["share"] = share
    return d


def _ensure_single_primary(owners: list) -> bool:
    """Гарантирует единственного «избранного» контакта в группе:
    если контакт один — он обязательно избранный (даже если ещё не отмечен);
    если контактов несколько и никто не отмечен избранным — им становится
    первый в списке. Возвращает True, если что-то изменилось (для
    self._group_dirty)."""
    valid = [o for o in owners if isinstance(o, dict)]
    if not valid:
        return False
    if len(valid) == 1:
        if not valid[0].get("is_visible"):
            valid[0]["is_visible"] = True
            return True
        return False
    if not any(o.get("is_visible") for o in valid):
        valid[0]["is_visible"] = True
        return True
    return False


# ============================================================================ #
#  Кастомная всплывашка — обходит нативный QToolTip Windows                   #
# ============================================================================ #

class _AppTooltip:
    """Синглтон-всплывашка с гарантированным светлым оформлением.

    Используется вместо QToolTip, который на Windows 11 игнорирует стили Qt.
    """
    _w: "QLabel | None" = None

    @classmethod
    def _ensure(cls) -> "QLabel":
        if cls._w is None:
            w = QLabel()
            w.setWindowFlags(
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.WindowStaysOnTopHint
            )
            w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            w.setContentsMargins(6, 4, 6, 4)
            w.setStyleSheet(
                "QLabel { background:#FFFFFF; color:#374151; "
                "border:1px solid #D1D5DB; border-radius:4px; "
                "font-size:12px; padding:4px 6px; }"
            )
            cls._w = w
        return cls._w

    @classmethod
    def show_at(cls, text: str, global_pos: "QPoint"):
        w = cls._ensure()
        w.setText(text)
        w.adjustSize()
        w.move(global_pos.x() + 12, global_pos.y() + 16)
        w.show()
        w.raise_()

    @classmethod
    def hide(cls):
        if cls._w is not None:
            cls._w.hide()


class _TooltipFilter(QObject):
    """EventFilter — навешивает кастомную всплывашку на любой существующий виджет.

    Передавайте виджет как parent, чтобы фильтр жил столько же, сколько виджет.
    """

    def __init__(self, tip: str, parent: "QWidget"):
        super().__init__(parent)
        self._tip = tip

    def eventFilter(self, obj, event):
        from PyQt6.QtGui import QCursor
        if event.type() == QEvent.Type.Enter:
            _AppTooltip.show_at(self._tip, QCursor.pos())
        elif event.type() == QEvent.Type.Leave:
            _AppTooltip.hide()
        return False


class _BasePromptDialog(QDialog):
    """Общая база кастомных уточняющих окон (_ConfirmDialog, _Save3WayDialog) —
    обходит нативный вид QMessageBox (Windows игнорирует Qt-стили для его
    чрома). Единая ФИКСИРОВАННАЯ ширина у всех окон этого семейства — чтобы
    короткое и длинное сообщение выглядели одним и тем же «прямоугольником»
    (высота при этом свободно растёт под перенос текста), а не диалогом то
    квадратной, то узкой вытянутой формы в зависимости от длины текста."""

    _RADIUS = 12
    _WIDTH = 380
    _MARGIN_H = 24  # см. lay.setContentsMargins ниже — по 24px слева/справа

    def __init__(self, title: str, message: str, *, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("confirmDialog")
        self.setStyleSheet(
            f"QDialog#confirmDialog{{background:#FFFFFF;border-radius:{self._RADIUS}px;}}")
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
            "font-size:17px; font-weight:700; color:#111827; background:transparent;")
        title_lbl.setWordWrap(True)
        title_lbl.setFixedWidth(_text_w)
        lay.addWidget(title_lbl)

        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setFixedWidth(_text_w)
        msg_lbl.setStyleSheet(
            "font-size:13px; color:#6B7280; background:transparent;")
        lay.addWidget(msg_lbl)

        lay.addSpacing(10)

        self._body_lay = lay
        self._btn_row = QHBoxLayout()
        self._btn_row.setSpacing(8)
        self._btn_row.addStretch()
        lay.addLayout(self._btn_row)

    def _insert_widget(self, widget: QWidget):
        """Вставляет widget между сообщением и строкой кнопок (для окон с
        доп. содержимым — см. _NewGroupDialog)."""
        self._body_lay.insertWidget(self._body_lay.count() - 1, widget)

    def _add_button(self, text: str, style: str, slot) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(style)
        btn.clicked.connect(slot)
        self._btn_row.addWidget(btn)
        return btn

    def _finalize(self):
        """Вызывается подклассом после добавления всех кнопок: фиксирует
        итоговую ширину и подгоняет высоту/маску под содержимое."""
        self.setFixedWidth(self._WIDTH)
        self.adjustSize()
        self._apply_mask()

    def _apply_mask(self):
        """Обрезает окно маской по скруглённому прямоугольнику — надёжнее,
        чем WA_TranslucentBackground, который на Windows иногда не композит-
        ится и оставляет видимым прямоугольник исходного (непрозрачного) окна."""
        sz = self.size()
        if sz.width() <= 0 or sz.height() <= 0:
            return
        bmp = QBitmap(sz)
        bmp.fill(Qt.GlobalColor.color0)
        p = QPainter(bmp)
        p.setBrush(Qt.GlobalColor.color1)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), self._RADIUS, self._RADIUS)
        p.end()
        self.setMask(QRegion(bmp))

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        self._apply_mask()

    @staticmethod
    def _show_centered(dlg: "_BasePromptDialog", parent):
        """Затемняющий оверлей на хосте + центрирование поверх parent/host.
        Общая обвязка для confirm()/ask() — вызывающий должен вручную
        скрыть/удалить overlay после dlg.exec() (см. try/finally у вызовов)."""
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


class _ConfirmDialog(_BasePromptDialog):
    """Окно подтверждения с двумя исходами: подтвердить / отмена."""

    def __init__(self, title: str, message: str, *,
                 confirm_text: str = "Удалить", cancel_text: str = "Отмена",
                 parent=None):
        super().__init__(title, message, parent=parent)
        self._add_button(
            cancel_text,
            "QPushButton{background:#F3F4F6;color:#111827;border:none;"
            "border-radius:6px;padding:7px 16px;font-size:13px;}"
            "QPushButton:hover{background:#E5E7EB;}",
            self.reject)
        self._confirm_btn = self._add_button(
            confirm_text,
            "QPushButton{background:#DC2626;color:#FFFFFF;border:none;"
            "border-radius:6px;padding:7px 16px;font-size:13px;font-weight:600;}"
            "QPushButton:hover{background:#B91C1C;}"
            "QPushButton:disabled{background:#F3A6A6;color:#FFFFFF;}",
            self.accept)
        self._finalize()

    @staticmethod
    def confirm(parent, title: str, message: str, *,
                confirm_text: str = "Удалить", cancel_text: str = "Отмена") -> bool:
        dlg = _ConfirmDialog(title, message, confirm_text=confirm_text,
                             cancel_text=cancel_text, parent=parent)
        overlay = _BasePromptDialog._show_centered(dlg, parent)
        try:
            return dlg.exec() == QDialog.DialogCode.Accepted
        finally:
            if overlay is not None:
                overlay.hide()
                overlay.deleteLater()


class _Save3WayDialog(_BasePromptDialog):
    """Окно «Сохранить / Не сохранять / Отмена» для несохранённых правок —
    тот же визуальный приём, что и _ConfirmDialog, но с тремя исходами."""

    def __init__(self, title: str, message: str, *, parent=None):
        super().__init__(title, message, parent=parent)
        self.choice = "cancel"

        def _pick(choice: str):
            self.choice = choice
            self.accept()

        self._add_button(
            "Отмена",
            "QPushButton{background:transparent;color:#6B7280;border:none;"
            "border-radius:6px;padding:7px 14px;font-size:13px;}"
            "QPushButton:hover{background:#F3F4F6;}",
            lambda: _pick("cancel"))
        self._add_button(
            "Не сохранять",
            "QPushButton{background:#F3F4F6;color:#111827;border:none;"
            "border-radius:6px;padding:7px 16px;font-size:13px;}"
            "QPushButton:hover{background:#E5E7EB;}",
            lambda: _pick("discard"))
        self._add_button(
            "Сохранить",
            "QPushButton{background:#07414F;color:#FFFFFF;border:none;"
            "border-radius:6px;padding:7px 16px;font-size:13px;font-weight:600;}"
            "QPushButton:hover{background:#062F38;}",
            lambda: _pick("save"))
        self._finalize()

    @staticmethod
    def ask(parent, title: str, message: str) -> str:
        """Возвращает 'save' | 'discard' | 'cancel'."""
        dlg = _Save3WayDialog(title, message, parent=parent)
        overlay = _BasePromptDialog._show_centered(dlg, parent)
        try:
            result = dlg.exec()
            return dlg.choice if result == QDialog.DialogCode.Accepted else "cancel"
        finally:
            if overlay is not None:
                overlay.hide()
                overlay.deleteLater()


class _NewGroupDialog(_ConfirmDialog):
    """«Создать новую группу?» — тот же _ConfirmDialog, плюс дата начала
    новой группы и ОБЯЗАТЕЛЬНОЕ поле ФИО первого контакта (звезда
    «избранный» — единственный контакт новой группы всегда становится
    избранным, см. _ensure_single_primary). Кнопка «Создать» неактивна, пока
    ФИО пусто — так группа никогда не создаётся без ни одного лица (это же
    требование действует и для замены активной группы, см.
    _on_archive_active_group). Дата не может быть раньше начала текущей
    активной группы (min_since) — иначе архивная и новая группы пересекутся
    по датам."""

    def __init__(self, title: str, message: str, people: list, *,
                min_since=None, confirm_text: str = "Создать",
                cancel_text: str = "Отмена", parent=None):
        super().__init__(title, message, confirm_text=confirm_text,
                         cancel_text=cancel_text, parent=parent)

        date_col = QVBoxLayout()
        date_col.setSpacing(2)
        date_col.addWidget(QLabel("Дата начала", styleSheet=_FIELD_LABEL_SS))
        self.inp_since = QDateEdit(calendarPopup=True, displayFormat="dd.MM.yyyy")
        self.inp_since.setDate(QDate.currentDate())
        if min_since is not None:
            self.inp_since.setMinimumDate(
                QDate(min_since.year, min_since.month, min_since.day).addDays(1))
        date_col.addWidget(self.inp_since)
        date_box = QWidget()
        date_box.setLayout(date_col)
        self._insert_widget(date_box)

        row = QWidget()
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(8)

        row_lay.addWidget(_make_decorative_star())

        self.inp_name = QLineEdit()
        self.inp_name.setPlaceholderText("Фамилия Имя Отчество")
        self.inp_name.setStyleSheet(_INPUT_SS)
        completer = QCompleter(_people_names(people), self.inp_name)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.inp_name.setCompleter(completer)
        row_lay.addWidget(self.inp_name, stretch=1)

        self._insert_widget(row)
        self._finalize()

        # «Создать» активна только при непустом ФИО.
        self._confirm_btn.setEnabled(False)
        self.inp_name.textChanged.connect(
            lambda t: self._confirm_btn.setEnabled(bool(t.strip())))
        self.inp_name.setFocus()

    @staticmethod
    def ask(parent, title: str, message: str, people: list,
           min_since=None) -> tuple[bool, str, "date | None"]:
        """Возвращает (confirmed, contact_name, since). Если confirmed=True,
        contact_name гарантированно не пуст (кнопка «Создать» заблокирована
        при пустом ФИО), since — выбранная дата начала (не раньше min_since)."""
        dlg = _NewGroupDialog(title, message, people, min_since=min_since, parent=parent)
        overlay = _BasePromptDialog._show_centered(dlg, parent)
        try:
            confirmed = dlg.exec() == QDialog.DialogCode.Accepted
            qd = dlg.inp_since.date()
            since = date(qd.year(), qd.month(), qd.day())
            return confirmed, dlg.inp_name.text().strip(), since
        finally:
            if overlay is not None:
                overlay.hide()
                overlay.deleteLater()


class _QuickAddPlotDialog(_ConfirmDialog):
    """Быстрое добавление участка — модальное окно вместо панели справа
    (см. PlotsWidget._add_plot): номер и площадь необязательны, обязательно
    только ФИО первого контакта. Кнопка «Создать» активна только при
    заполненном ФИО; указанный номер, совпадающий с уже существующим
    участком, блокирует создание (пилюля «номер занят» — рядом с подписью
    поля, как и у дубля ФИО в карточке контакта)."""

    def __init__(self, title: str, message: str, people: list,
                existing_nums: set, *, parent=None):
        super().__init__(title, message, confirm_text="Создать", parent=parent)
        self._existing_nums = existing_nums

        fields = QWidget()
        f_lay = QVBoxLayout(fields)
        f_lay.setContentsMargins(0, 0, 0, 0)
        f_lay.setSpacing(10)

        # -- Номер участка (необязательно) + пилюля дубля рядом с подписью --
        num_col = QVBoxLayout()
        num_col.setSpacing(2)
        num_lbl_row = QHBoxLayout()
        num_lbl_row.setContentsMargins(0, 0, 0, 0)
        num_lbl_row.setSpacing(6)
        num_lbl_row.addWidget(QLabel("Номер участка", styleSheet=_FIELD_LABEL_SS))
        self._num_taken_pill = _make_warn_pill(fields, "номер занят")
        self._num_taken_pill.hide()
        _pill_sp = self._num_taken_pill.sizePolicy()
        _pill_sp.setRetainSizeWhenHidden(True)
        self._num_taken_pill.setSizePolicy(_pill_sp)
        num_lbl_row.addWidget(self._num_taken_pill)
        num_lbl_row.addStretch(1)
        num_col.addLayout(num_lbl_row)
        self.inp_num = QLineEdit()
        self.inp_num.setPlaceholderText("например: 15 или 15/207")
        self.inp_num.setStyleSheet(_INPUT_SS)
        num_col.addWidget(self.inp_num)
        f_lay.addLayout(num_col)

        # -- Площадь (необязательно) --
        area_col = QVBoxLayout()
        area_col.setSpacing(2)
        area_col.addWidget(QLabel("Площадь, м² (необязательно)", styleSheet=_FIELD_LABEL_SS))
        self.inp_area = QLineEdit()
        self.inp_area.setPlaceholderText("например: 612")
        self.inp_area.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"^\d{0,5}([.,]\d{0,2})?$"), self.inp_area))
        self.inp_area.textEdited.connect(lambda t: (
            self.inp_area.setText(t.replace(".", ",")),
            self.inp_area.setCursorPosition(self.inp_area.cursorPosition()),
        ) if "." in t else None)
        self.inp_area.setStyleSheet(_INPUT_SS)
        area_col.addWidget(self.inp_area)
        f_lay.addLayout(area_col)

        # -- ФИО собственника --
        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        name_col.addWidget(QLabel("ФИО собственника", styleSheet=_FIELD_LABEL_SS))
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        name_row.addWidget(_make_decorative_star())
        self.inp_name = QLineEdit()
        self.inp_name.setPlaceholderText("Фамилия Имя Отчество")
        self.inp_name.setStyleSheet(_INPUT_SS)
        completer = QCompleter(_people_names(people), self.inp_name)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.inp_name.setCompleter(completer)
        name_row.addWidget(self.inp_name, stretch=1)
        name_col.addLayout(name_row)
        f_lay.addLayout(name_col)

        self._insert_widget(fields)
        self._finalize()

        self._confirm_btn.setEnabled(False)
        self.inp_num.textChanged.connect(self._update_state)
        self.inp_area.textChanged.connect(self._update_state)
        self.inp_name.textChanged.connect(self._update_state)
        self.inp_name.setFocus()

    def _update_state(self, *_):
        num = self.inp_num.text().strip()
        num_dup = bool(num) and num in self._existing_nums
        self._num_taken_pill.setVisible(num_dup)

        # Площадь необязательна: пусто — ок, а вот заведомо некорректное
        # значение (например, одна лишь запятая) блокирует создание.
        area_txt = self.inp_area.text().strip().replace(",", ".")
        area_ok = True
        if area_txt:
            try:
                area_ok = float(area_txt) > 0
            except ValueError:
                area_ok = False

        name_ok = bool(self.inp_name.text().strip())
        self._confirm_btn.setEnabled(area_ok and name_ok and not num_dup)

    @staticmethod
    def ask(parent, people: list, existing_nums: set):
        """Возвращает (num, area, name) при подтверждении, иначе None.
        area — None, если поле оставлено пустым (необязательное)."""
        dlg = _QuickAddPlotDialog(
            "Новый участок",
            "Обязательно только ФИО собственника — номер и площадь можно "
            "уточнить позже.",
            people, existing_nums, parent=parent)
        overlay = _BasePromptDialog._show_centered(dlg, parent)
        try:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return None
            num = dlg.inp_num.text().strip()
            area_txt = dlg.inp_area.text().strip().replace(",", ".")
            area = float(area_txt) if area_txt else None
            name = dlg.inp_name.text().strip()
            return num, area, name
        finally:
            if overlay is not None:
                overlay.hide()
                overlay.deleteLater()


# ============================================================================ #
#  Узел дерева                                                                 #
# ============================================================================ #

class _PlotNode:
    """Узел дерева. kind='plot' — строка участка, kind='owner' — строка владельца."""

    __slots__ = ("kind", "plot_ref", "owner_idx", "parent", "children")

    def __init__(self, kind: str, plot_ref: dict | None = None,
                 owner_idx: int = -1, parent=None):
        self.kind = kind
        self.plot_ref = plot_ref
        self.owner_idx = owner_idx
        self.parent = parent
        self.children: list["_PlotNode"] = []

    def row(self) -> int:
        if self.parent is not None:
            return self.parent.children.index(self)
        return 0


# ============================================================================ #
#  Модель дерева                                                               #
# ============================================================================ #

class PlotsTreeModel(QAbstractItemModel):
    COLUMNS = ["№", "Площадь, м²", "Контакт",
               "Контактный номер", "E-mail", "_edit", "_check"]

    # Эмитится только из setData (inline-редактирование), чтобы автосохранять.
    ownerDataEdited = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root = _PlotNode("root")

    def load(self, plots: list):
        self.beginResetModel()
        root = _PlotNode("root")
        for plot in plots:
            plot_node = _PlotNode("plot", plot_ref=plot, parent=root)
            for i in range(len(plot.get("owners", []))):
                owner_node = _PlotNode("owner", plot_ref=plot, owner_idx=i, parent=plot_node)
                plot_node.children.append(owner_node)
            root.children.append(plot_node)
        self._root = root
        self.endResetModel()

    def top_nodes(self) -> list:
        return self._root.children

    # -- core tree ----------------------------------------------------------- #

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        parent_node = parent.internalPointer() if parent.isValid() else self._root
        if 0 <= row < len(parent_node.children):
            return self.createIndex(row, column, parent_node.children[row])
        return QModelIndex()

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        node = index.internalPointer()
        p = node.parent
        if p is None or p is self._root:
            return QModelIndex()
        return self.createIndex(p.row(), 0, p)

    def rowCount(self, parent=QModelIndex()):
        if parent.column() > 0:
            return 0
        node = parent.internalPointer() if parent.isValid() else self._root
        return len(node.children)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLUMNS)

    # -- read ---------------------------------------------------------------- #

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer()
        col = self.COLUMNS[index.column()]

        if col in ("_edit", "_check"):
            return "" if role == Qt.ItemDataRole.DisplayRole else None

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if node.kind == "plot":
                if col == "№":
                    return str(node.plot_ref.get('num', '?'))
                if col == "Контакт":
                    owners = node.plot_ref.get("owners", []) or []
                    if not owners:
                        return "—"
                    visible = next((o for o in owners if _is_visible(o)), None)
                    if visible:
                        return _owner_name(visible)
                    main = next((o for o in owners if _is_owner(o)), owners[0])
                    return _owner_name(main)
                if col == "Площадь, м²":
                    area = node.plot_ref.get("area")
                    try:
                        v = float(area) if area not in (None, "") else None
                    except (TypeError, ValueError):
                        v = None
                    return f"{v:g}" if v is not None else "—"
                if col in ("Контактный номер", "E-mail"):
                    owners = node.plot_ref.get("owners", []) or []
                    visible = next((o for o in owners if _is_visible(o)), None)
                    owner = visible or (next((o for o in owners if _is_owner(o)), owners[0]) if owners else None)
                    if owner is None:
                        return "—"
                    return (_owner_phone(owner) if col == "Контактный номер" else _owner_email(owner)) or "—"
                return ""
            elif node.kind == "owner":
                owners = node.plot_ref.get("owners", [])
                owner = owners[node.owner_idx] if 0 <= node.owner_idx < len(owners) else None
                if col == "Контакт":
                    return _owner_name(owner) if owner is not None else ""
                if col == "Площадь, м²":
                    if owner is None:
                        return ""
                    v = _owner_area(owner)
                    if role == Qt.ItemDataRole.EditRole:
                        return f"{v:g}" if v is not None else ""
                    return f"{v:g}" if v is not None else "—"
                if col == "Контактный номер":
                    return _owner_phone(owner) if owner is not None else ""
                if col == "E-mail":
                    return _owner_email(owner) if owner is not None else ""
                return ""

        if role == Qt.ItemDataRole.ForegroundRole:
            if node.kind == "plot":
                if col == "№":
                    return QColor("#07414F")
                if col == "Площадь, м²":
                    area = node.plot_ref.get("area")
                    return QColor("#9CA3AF") if area in (None, "") else QColor("#374151")
                return QColor("#374151")
            elif node.kind == "owner":
                return QColor("#555F6D")

        if role == Qt.ItemDataRole.FontRole:
            if node.kind == "plot" and col == "№":
                f = QFont()
                f.setBold(True)
                return f

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == "Площадь, м²":
                return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            col = self.COLUMNS[section]
            if col == "_edit":   return chr(0xE3C9)
            if col == "_check":  return chr(0xE92B)
            return col
        return None

    # -- edit ---------------------------------------------------------------- #

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        node = index.internalPointer()
        col = self.COLUMNS[index.column()]
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if node.kind == "owner" and col in ("Контакт", "Площадь, м²"):
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        node = index.internalPointer()
        col = self.COLUMNS[index.column()]
        if node.kind == "owner" and col in ("Контакт", "Площадь, м²"):
            text = str(value).strip()
            owners = node.plot_ref.get("owners", [])
            if not (0 <= node.owner_idx < len(owners)):
                return False
            old = owners[node.owner_idx]
            # Сохраняем ВСЕ поля владельца (телефон, e-mail, документы,
            # since/until/share), меняя только редактируемую ячейку.
            new = dict(old) if isinstance(old, dict) else {"name": str(old), "is_owner": True}
            if col == "Контакт":
                if not text:
                    return False
                new["name"] = text
                owners[node.owner_idx] = new
                pn = node.parent
                if pn is not None:
                    fio_col = self.COLUMNS.index("Контакт")
                    self.dataChanged.emit(self.createIndex(pn.row(), fio_col, pn),
                                         self.createIndex(pn.row(), fio_col, pn))
            else:  # Площадь, м²
                if text in ("", "—"):
                    new.pop("area", None)
                else:
                    try:
                        area = float(text.replace(",", "."))
                        if area <= 0:
                            return False
                    except ValueError:
                        return False
                    new["area"] = area
                owners[node.owner_idx] = new
            self.dataChanged.emit(index, index)
            self.ownerDataEdited.emit()
            return True
        return False

    # -- sort ---------------------------------------------------------------- #

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if not (0 <= column < len(self.COLUMNS)):
            return
        col = self.COLUMNS[column]
        self.beginResetModel()
        reverse = order == Qt.SortOrder.DescendingOrder
        if col == "№":
            self._root.children.sort(
                key=lambda n: _plot_num_key(str(n.plot_ref.get("num", ""))),
                reverse=reverse,
            )
        elif col == "Контакт":
            def _fio_key(n):
                owners = n.plot_ref.get("owners") or []
                main = next((o for o in owners if _is_owner(o)), owners[0] if owners else {})
                return _owner_name(main).lower()
            self._root.children.sort(key=_fio_key, reverse=reverse)
        elif col == "Площадь, м²":
            def _area_key(n):
                try:
                    v = n.plot_ref.get("area")
                    if v in (None, ""):
                        return float("inf")
                    return float(v)
                except (TypeError, ValueError):
                    return float("inf")
            self._root.children.sort(key=_area_key, reverse=reverse)
        self.endResetModel()

# ============================================================================ #
#  Делегат столбца «Контакт»                                          #
# ============================================================================ #

class _FioDelegate(QStyledItemDelegate):
    """Для строк-участков рисует имя + овальную кнопку «Показать/Свернуть (N)»."""

    toggleRequested = pyqtSignal(QModelIndex)

    _BG         = QColor("#FFFFFF")
    _BG_ALT     = QColor("#F0F4F8")
    _BG_HOVER   = QColor("#DDE4EE")
    _BG_SEL     = QColor("#C9D8E2")
    _BORDER     = QColor("#E3E8EF")
    _TEXT_FG    = QColor("#1F2937")
    _OWNER_FG   = QColor("#555F6D")
    _BTN_BG     = QColor("#E8F0F5")
    _BTN_BG_H   = QColor("#C9D8E2")
    _BTN_FG     = QColor("#07414F")
    _BTN_BORDER = QColor("#B5C8D5")
    _BTN_H      = 22
    _FIO_COL    = PlotsTreeModel.COLUMNS.index("Контакт")

    def __init__(self, view):
        super().__init__(view)
        self._view = view
        self._hover_btn_index = QModelIndex()
        self._tooltip_index   = QModelIndex()
        self._tooltip_text    = ""
        self.compact = False  # лаконичный режим (открыт drawer): только имя
        view.viewport().installEventFilter(self)

    # -- hover tracking -------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self._view.viewport():
            if event.type() == QEvent.Type.MouseMove:
                p = event.position().toPoint()
                self._update_btn_hover(p)
                self._update_icon_tooltip(p)
            elif event.type() == QEvent.Type.Leave:
                self._update_btn_hover(None)
                self._tooltip_index = QModelIndex()
                self._tooltip_text  = ""
                _AppTooltip.hide()
        return False

    def _update_btn_hover(self, pos):
        new_hover = QModelIndex()
        if pos is not None:
            idx = self._view.indexAt(pos)
            if idx.isValid() and idx.column() == self._FIO_COL:
                node = idx.internalPointer()
                if node is not None and node.kind == "plot":
                    n = len(node.plot_ref.get("owners", []) or [])
                    if n > 1:
                        rect = self._view.visualRect(idx)
                        text = str(idx.data(Qt.ItemDataRole.DisplayRole) or "")
                        if self._btn_rect(rect, n, text).contains(pos):
                            new_hover = idx
        if new_hover != self._hover_btn_index:
            old = self._hover_btn_index
            self._hover_btn_index = new_hover
            if old.isValid():
                self._view.update(old)
            if new_hover.isValid():
                self._view.update(new_hover)
        if new_hover.isValid():
            self._view.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self._view.viewport().unsetCursor()

    def _update_icon_tooltip(self, pos):
        target     = QModelIndex()
        target_tip = ""
        idx = self._view.indexAt(pos)
        if idx.isValid() and idx.column() == self._FIO_COL:
            node = idx.internalPointer()
            if node is not None:
                owners = node.plot_ref.get("owners", [])
                if node.kind == "owner":
                    owner = owners[node.owner_idx] if 0 <= node.owner_idx < len(owners) else None
                elif node.kind == "plot" and len(owners) == 1:
                    owner = owners[0]
                else:
                    owner = None
                # Агрегированные индикаторы на родительских строках (несколько владельцев)
                if owner is None and node.kind == "plot" and len(owners) > 1:
                    miss_m = sum(1 for o in owners if _is_owner(o) and not _owner_member_doc(o))
                    miss_o = sum(1 for o in owners if _is_owner(o) and not _owner_opd_doc(o))
                    miss_a = sum(1 for o in owners if _is_owner(o) and _owner_area(o) is None)
                    indicators = []
                    if miss_m > 0:
                        indicators.append((18, str(miss_m), f"Нет заявления о вступлении: {miss_m}"))
                    if miss_o > 0:
                        indicators.append((18, str(miss_o), f"Нет заявления на ОПД: {miss_o}"))
                    if miss_a > 0:
                        f_m2 = QFont(); f_m2.setPixelSize(11); f_m2.setBold(True)
                        lw_m2 = QFontMetrics(f_m2).horizontalAdvance("м²")
                        indicators.append((lw_m2, str(miss_a), f"Площадь не заполнена: {miss_a}"))
                    if indicators:
                        f_cnt = QFont(); f_cnt.setPixelSize(11); f_cnt.setBold(True)
                        fm    = QFontMetrics(f_cnt)
                        cell_rect = self._view.visualRect(idx)
                        cur_x = cell_rect.right() - 8
                        for lw, cnt_str, tip in reversed(indicators):
                            cw   = fm.horizontalAdvance(cnt_str)
                            zone = QRect(cur_x - cw - 2 - lw, cell_rect.top(),
                                         lw + 2 + cw, cell_rect.height())
                            if zone.contains(pos):
                                target, target_tip = idx, tip
                                break
                            cur_x -= lw + 2 + cw + 4
                if owner is not None:
                    cell_rect = self._view.visualRect(idx)
                    has_area  = (_owner_area(owner) is not None)
                    m2_w      = 22
                    m2_off    = (m2_w + 2) if not has_area else 0
                    if _is_owner(owner):
                        # м² (самый правый, если площадь не заполнена)
                        if not has_area:
                            m2_zone = QRect(cell_rect.right() - 6 - m2_w, cell_rect.top(),
                                            m2_w + 6, cell_rect.height())
                            if m2_zone.contains(pos):
                                target, target_tip = idx, "Площадь не заполнена"
                        # OPD (левее м²)
                        if not target.isValid():
                            opd_zone = QRect(cell_rect.right() - 26 - m2_off, cell_rect.top(),
                                             26, cell_rect.height())
                            if opd_zone.contains(pos):
                                has_opd = bool(_owner_opd_doc(owner))
                                target, target_tip = idx, (
                                    "Заявление на ОПД прикреплено" if has_opd
                                    else "Заявление на ОПД не прикреплено"
                                )
                        # Член СНТ (левее OPD)
                        if not target.isValid():
                            pc_zone = QRect(cell_rect.right() - 48 - m2_off, cell_rect.top(),
                                            22, cell_rect.height())
                            if pc_zone.contains(pos):
                                has_doc = bool(_owner_member_doc(owner))
                                target, target_tip = idx, (
                                    "Заявление о вступлении прикреплено" if has_doc
                                    else "Заявление о вступлении не прикреплено"
                                )
                        # article_person (Собственник) — левее Член СНТ
                        if not target.isValid():
                            ic_zone = QRect(cell_rect.right() - 70 - m2_off, cell_rect.top(),
                                            22, cell_rect.height())
                            if ic_zone.contains(pos):
                                target, target_tip = idx, "Собственник"
                    # bookmark_star (Видимый) — самый левый из иконок
                    if not target.isValid() and _is_visible(owner):
                        vis_rect = QRect(cell_rect.right() - 92 - m2_off, cell_rect.top(),
                                         22, cell_rect.height())
                        if vis_rect.contains(pos):
                            target, target_tip = idx, "Видимый"
        if target == self._tooltip_index and target_tip == self._tooltip_text:
            return
        self._tooltip_index = target
        self._tooltip_text  = target_tip
        if target.isValid():
            gp = self._view.viewport().mapToGlobal(pos)
            _AppTooltip.show_at(target_tip, gp)
        else:
            _AppTooltip.hide()

    # -- geometry -------------------------------------------------------------

    def _btn_rect(self, cell_rect: QRect, n: int, text: str = "") -> QRect:
        f_btn = QFont()
        f_btn.setPixelSize(11)
        f_btn.setBold(True)
        w = QFontMetrics(f_btn).horizontalAdvance(f"Свернуть ({n})") + 20
        y = cell_rect.top() + (cell_rect.height() - self._BTN_H) // 2
        f_text = QFont()
        f_text.setPixelSize(13)
        text_w = QFontMetrics(f_text).horizontalAdvance(text) if text else 0
        x = cell_rect.left() + 8 + text_w + 8
        return QRect(x, y, w, self._BTN_H)

    # -- painting -------------------------------------------------------------

    def paint(self, painter, option, index):
        painter.save()
        node = index.internalPointer()

        is_alt = bool(option.features & QStyleOptionViewItem.ViewItemFeature.Alternate)
        if option.state & QStyle.StateFlag.State_Selected:
            bg = self._BG_SEL
        elif option.state & QStyle.StateFlag.State_MouseOver:
            bg = self._BG_HOVER
        else:
            bg = self._BG_ALT if is_alt else self._BG
        painter.fillRect(option.rect, bg)
        if not self.compact:  # в лаконичном режиме без разделителей строк (вид списка)
            painter.setPen(QPen(self._BORDER, 1))
            painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())

        if node is None:
            painter.restore()
            return

        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        n = len(node.plot_ref.get("owners", []) or []) if node.kind == "plot" else 0

        f_text = QFont()
        f_text.setPixelSize(13)

        if self.compact:
            # Лаконичный режим (открыт drawer): только имя, без индикаторов и кнопки
            indent = 20 if node.kind == "owner" else 0
            fg = self._OWNER_FG if node.kind == "owner" else self._TEXT_FG
            painter.setPen(fg)
            painter.setFont(f_text)
            painter.drawText(option.rect.adjusted(8 + indent, 0, -8, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             text)
            painter.restore()
            return

        if node.kind == "plot" and n > 1:
            col0 = self._view.model().index(index.row(), 0,
                                            self._view.model().parent(index))
            is_expanded = self._view.isExpanded(col0)
            label = f"Свернуть ({n})" if is_expanded else f"Показать ({n})"

            btn = self._btn_rect(option.rect, n, text)

            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            btn_bg = self._BTN_BG_H if self._hover_btn_index == index else self._BTN_BG
            painter.setBrush(btn_bg)
            painter.setPen(QPen(self._BTN_BORDER, 1))
            # Полупиксельные координаты: 1px-перо ложится точно в пиксельную
            # сетку, без них антиалиасинг размазывает бордер на два пикселя.
            btn_f = QRectF(btn).adjusted(0.5, 0.5, -0.5, -0.5)
            r = btn_f.height() / 2.0
            painter.drawRoundedRect(btn_f, r, r)

            f_btn = QFont()
            f_btn.setPixelSize(11)
            f_btn.setBold(True)
            painter.setPen(self._BTN_FG)
            painter.setFont(f_btn)
            painter.drawText(btn, Qt.AlignmentFlag.AlignCenter, label)

            # Индикаторы пропущенных данных слева от кнопки
            owners_list = node.plot_ref.get("owners", [])
            miss_m = sum(1 for o in owners_list if _is_owner(o) and not _owner_member_doc(o))
            miss_o = sum(1 for o in owners_list if _is_owner(o) and not _owner_opd_doc(o))
            miss_a = sum(1 for o in owners_list if _is_owner(o) and _owner_area(o) is None)
            # (label, count, is_icon) — порядок соответствует расположению left→right
            indicators = []
            if miss_m > 0:
                indicators.append((chr(0xF567), miss_m, True))
            if miss_o > 0:
                indicators.append((chr(0xF0DC), miss_o, True))
            if miss_a > 0:
                indicators.append(("м²", miss_a, False))
            f_ic2 = QFont("Material Symbols Rounded")
            f_ic2.setPixelSize(15)
            f_cnt = QFont()
            f_cnt.setPixelSize(11)
            f_cnt.setBold(True)
            fm_cnt = QFontMetrics(f_cnt)
            # Индикаторы — от правого края влево, независимо от кнопки
            cur_x = option.rect.right() - 8
            for lbl, cnt, is_icon in reversed(indicators):
                cnt_str  = str(cnt)
                cw       = fm_cnt.horizontalAdvance(cnt_str)
                lw       = 18 if is_icon else fm_cnt.horizontalAdvance(lbl)
                lbl_rect  = QRect(cur_x - cw - 2 - lw, option.rect.top(),
                                  lw, option.rect.height())
                cnt_rect2 = QRect(cur_x - cw, option.rect.top(),
                                  cw, option.rect.height())
                painter.setPen(QColor("#F59E0B"))
                painter.setFont(f_ic2 if is_icon else f_cnt)
                painter.drawText(lbl_rect, Qt.AlignmentFlag.AlignCenter, lbl)
                painter.setFont(f_cnt)
                painter.drawText(cnt_rect2, Qt.AlignmentFlag.AlignCenter, cnt_str)
                cur_x -= lw + 2 + cw + 4
            # Текст — от левого края до кнопки
            text_rect = QRect(option.rect.left() + 8, option.rect.top(),
                              btn.left() - option.rect.left() - 10, option.rect.height())
            painter.setPen(self._TEXT_FG)
            painter.setFont(f_text)
            painter.drawText(text_rect,
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             text)
        else:
            fg = self._OWNER_FG if node.kind == "owner" else self._TEXT_FG
            if node.kind == "owner":
                owners = node.plot_ref.get("owners", [])
                owner = owners[node.owner_idx] if 0 <= node.owner_idx < len(owners) else None
            elif node.kind == "plot" and n == 1:
                owners = node.plot_ref.get("owners", [])
                owner = owners[0] if owners else None
            else:
                owner = None
            # Коннектор иерархии для строк-владельцев
            if node.kind == "owner":
                total   = len(node.plot_ref.get("owners", []))
                is_last = (node.owner_idx == total - 1)
                cx = option.rect.left() + 12
                cy = option.rect.top() + option.rect.height() // 2
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                painter.setPen(QPen(self._BTN_BORDER, 1))
                painter.drawLine(cx, option.rect.top(), cx,
                                 cy if is_last else option.rect.bottom())
                painter.drawLine(cx, cy, cx + 10, cy)
                painter.restore()
                indent = 20
            else:
                indent = 0
            if owner is not None and _is_owner(owner):
                    pw   = 20
                    ic_w = 20
                    f_ic = QFont("Material Symbols Rounded")
                    f_ic.setPixelSize(16)
                    painter.setFont(f_ic)
                    # м² будет самым правым — вычисляем смещение заранее
                    m2_w     = 22
                    has_area = (_owner_area(owner) is not None)
                    m2_off   = (m2_w + 2) if not has_area else 0
                    # OPD (левее м²)
                    has_opd   = bool(_owner_opd_doc(owner))
                    opd_char  = chr(0xE8E8) if has_opd else chr(0xF0DC)
                    opd_color = QColor("#07414F") if has_opd else QColor("#F59E0B")
                    opd_rect  = QRect(option.rect.right() - pw - 6 - m2_off, option.rect.top(),
                                      pw, option.rect.height())
                    painter.setPen(opd_color)
                    painter.drawText(opd_rect, Qt.AlignmentFlag.AlignCenter, opd_char)
                    # Член СНТ (левее OPD)
                    pc_x     = option.rect.right() - pw - 6 - m2_off - pw - 2
                    has_doc  = bool(_owner_member_doc(owner))
                    pc_char  = chr(0xF565) if has_doc else chr(0xF567)
                    pc_color = QColor("#07414F") if has_doc else QColor("#F59E0B")
                    pc_rect  = QRect(pc_x, option.rect.top(), pw, option.rect.height())
                    painter.setPen(pc_color)
                    painter.drawText(pc_rect, Qt.AlignmentFlag.AlignCenter, pc_char)
                    # article_person (левее Член СНТ)
                    ic_x    = pc_x - ic_w - 2
                    ic_rect = QRect(ic_x, option.rect.top(), ic_w, option.rect.height())
                    painter.setPen(QColor("#07414F"))
                    painter.drawText(ic_rect, Qt.AlignmentFlag.AlignCenter, "")
                    right_margin = pw + pw + ic_w + m2_off + 14
                    # м² (самый правый, только если площадь не заполнена)
                    if not has_area:
                        f_m2 = QFont()
                        f_m2.setPixelSize(10)
                        f_m2.setBold(True)
                        m2_rect = QRect(option.rect.right() - 6 - m2_w, option.rect.top(),
                                        m2_w, option.rect.height())
                        painter.setPen(QColor("#F59E0B"))
                        painter.setFont(f_m2)
                        painter.drawText(m2_rect, Qt.AlignmentFlag.AlignCenter, "м²")
                    if _is_visible(owner):
                        vis_w    = 20
                        vis_rect = QRect(ic_x - vis_w - 2, option.rect.top(),
                                         vis_w, option.rect.height())
                        painter.setFont(f_ic)
                        painter.setPen(QColor("#07414F"))
                        painter.drawText(vis_rect, Qt.AlignmentFlag.AlignCenter,
                                         chr(0xF454))
                        right_margin += vis_w + 2
                    text_rect = option.rect.adjusted(8 + indent, 0, -right_margin, 0)
            else:
                text_rect = option.rect.adjusted(8 + indent, 0, -8, 0)
            painter.setPen(fg)
            painter.setFont(f_text)
            painter.drawText(text_rect,
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             text)

        painter.restore()

    # -- click ----------------------------------------------------------------

    def editorEvent(self, event, model, option, index):
        if (event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton):
            node = index.internalPointer()
            if node is not None and node.kind == "plot":
                n = len(node.plot_ref.get("owners", []) or [])
                if n > 1:
                    text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
                    btn = self._btn_rect(option.rect, n, text)
                    if btn.contains(event.position().toPoint()):
                        self.toggleRequested.emit(index)
                        return True
        return super().editorEvent(event, model, option, index)


# ============================================================================ #
#  Делегат столбца «_edit» — кнопка редактирования участка                    #
# ============================================================================ #

class _EditBtnDelegate(QStyledItemDelegate):
    """Рисует иконку edit; на hover — filled-версия и курсор-рука."""

    _IC_EDIT  = chr(0xE3C9)  # edit
    _IC_FONT  = "Material Symbols Rounded"
    _IC_COLOR = QColor("#07414F")

    def __init__(self, view):
        super().__init__(view)
        self._view      = view
        self._edit_col  = PlotsTreeModel.COLUMNS.index("_edit")
        self._hover_idx = QModelIndex()
        self._pointing  = False
        self._fill_tag  = QFont.Tag.fromString("FILL")
        view.viewport().installEventFilter(self)

    def _is_btn(self, index: QModelIndex) -> bool:
        node = index.internalPointer() if index.isValid() else None
        return bool(node and node.kind == "plot" and index.column() == self._edit_col)

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        node = index.internalPointer()
        if node is None or node.kind != "plot":
            return
        painter.save()
        f = QFont(self._IC_FONT)
        f.setPixelSize(18)
        f.setVariableAxis(self._fill_tag, 1.0 if self._hover_idx == index else 0.0)
        painter.setFont(f)
        painter.setPen(self._IC_COLOR)
        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, self._IC_EDIT)
        painter.restore()

    def eventFilter(self, obj, event):
        if obj is self._view.viewport():
            if event.type() == QEvent.Type.MouseMove:
                idx  = self._view.indexAt(event.position().toPoint())
                on_btn = self._is_btn(idx)

                # setOverrideCursor не сбрасывается внутренней обработкой Qt
                if on_btn and not self._pointing:
                    self._pointing = True
                    QApplication.setOverrideCursor(Qt.CursorShape.PointingHandCursor)
                elif not on_btn and self._pointing:
                    self._pointing = False
                    QApplication.restoreOverrideCursor()

                # Hover-состояние для fill-эффекта
                new_hover = idx if on_btn else QModelIndex()
                if new_hover != self._hover_idx:
                    old = self._hover_idx
                    self._hover_idx = new_hover
                    if old.isValid():
                        self._view.viewport().update(self._view.visualRect(old))
                    if new_hover.isValid():
                        self._view.viewport().update(self._view.visualRect(new_hover))

            elif event.type() == QEvent.Type.Leave:
                if self._pointing:
                    self._pointing = False
                    QApplication.restoreOverrideCursor()
                if self._hover_idx.isValid():
                    old = self._hover_idx
                    self._hover_idx = QModelIndex()
                    self._view.viewport().update(self._view.visualRect(old))

        return super().eventFilter(obj, event)


# ============================================================================ #
#  Делегат столбца «_check» — чекбоксы выбора для массовых операций           #
# ============================================================================ #

class _CheckDelegate(QStyledItemDelegate):
    """Рисует чекбокс; на hover — filled-версия и курсор-рука. Управляет выбором участков."""

    selectionChanged = pyqtSignal()

    _IC_ON    = chr(0xE834)   # check_box
    _IC_OFF   = chr(0xE835)   # check_box_outline_blank
    _IC_FONT  = "Material Symbols Rounded"
    _IC_COLOR = QColor("#07414F")

    def __init__(self, view):
        super().__init__(view)
        self._view      = view
        self._check_col = PlotsTreeModel.COLUMNS.index("_check")
        self._hover_idx = QModelIndex()
        self._pointing  = False
        self._fill_tag  = QFont.Tag.fromString("FILL")
        self._selected: set[str] = set()
        view.viewport().installEventFilter(self)

    def get_selected(self) -> set[str]:
        return set(self._selected)

    def clear_selection(self):
        if self._selected:
            self._selected.clear()
            self.selectionChanged.emit()
            self._view.viewport().update()

    def remove_plot(self, plot_id: str):
        if plot_id in self._selected:
            self._selected.discard(plot_id)
            self.selectionChanged.emit()

    def _is_btn(self, index: QModelIndex) -> bool:
        node = index.internalPointer() if index.isValid() else None
        return bool(node and node.kind == "plot" and index.column() == self._check_col)

    def _plot_id(self, index: QModelIndex) -> str:
        node = index.internalPointer()
        return str(node.plot_ref.get("num", id(node.plot_ref))) if node else ""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        node = index.internalPointer()
        if node is None or node.kind != "plot":
            return
        pid   = self._plot_id(index)
        hov   = self._hover_idx == index
        icon  = self._IC_ON if pid in self._selected else self._IC_OFF
        painter.save()
        f = QFont(self._IC_FONT)
        f.setPixelSize(18)
        f.setVariableAxis(self._fill_tag, 1.0 if hov else 0.0)
        painter.setFont(f)
        painter.setPen(self._IC_COLOR)
        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, icon)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if (event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
                and self._is_btn(index)):
            pid = self._plot_id(index)
            if pid:
                if pid in self._selected:
                    self._selected.discard(pid)
                else:
                    self._selected.add(pid)
                self._view.viewport().update(self._view.visualRect(index))
                self.selectionChanged.emit()
            return True
        return super().editorEvent(event, model, option, index)

    def eventFilter(self, obj, event):
        try:
            view = self._view
        except RuntimeError:
            return False
        if obj is view.viewport():
            if event.type() == QEvent.Type.MouseMove:
                idx    = self._view.indexAt(event.position().toPoint())
                on_btn = self._is_btn(idx)

                if on_btn and not self._pointing:
                    self._pointing = True
                    QApplication.setOverrideCursor(Qt.CursorShape.PointingHandCursor)
                elif not on_btn and self._pointing:
                    self._pointing = False
                    QApplication.restoreOverrideCursor()

                new_hover = idx if on_btn else QModelIndex()
                if new_hover != self._hover_idx:
                    old = self._hover_idx
                    self._hover_idx = new_hover
                    if old.isValid():
                        self._view.viewport().update(self._view.visualRect(old))
                    if new_hover.isValid():
                        self._view.viewport().update(self._view.visualRect(new_hover))

            elif event.type() == QEvent.Type.Leave:
                if self._pointing:
                    self._pointing = False
                    QApplication.restoreOverrideCursor()
                if self._hover_idx.isValid():
                    old = self._hover_idx
                    self._hover_idx = QModelIndex()
                    self._view.viewport().update(self._view.visualRect(old))

        return super().eventFilter(obj, event)


# ============================================================================ #
#  Делегат столбца «№» — индикатор отсутствия выписки ЕГРН                    #
# ============================================================================ #

class _PlotNumDelegate(QStyledItemDelegate):
    """Для строк-участков рисует иконку real_estate_agent (оранжевую), если egrn_doc не загружен."""

    _IC_MISSING = chr(0xE73A)  # real_estate_agent
    _IC_COLOR   = QColor("#F59E0B")
    _PLOT_COL   = PlotsTreeModel.COLUMNS.index("№")

    def __init__(self, view):
        super().__init__(view)
        self._view         = view
        self._tip_index    = QModelIndex()
        self.compact = False  # лаконичный режим (открыт drawer): без иконки-индикатора
        view.viewport().installEventFilter(self)

    def _ic_rect(self, cell_rect: QRect) -> QRect:
        return QRect(cell_rect.right() - 22, cell_rect.top(), 20, cell_rect.height())

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if self.compact:
            return
        node = index.internalPointer()
        if node is None or node.kind != "plot":
            return
        if node.plot_ref.get("egrn_doc", ""):
            return
        painter.save()
        f = QFont("Material Symbols Rounded")
        f.setPixelSize(15)
        painter.setFont(f)
        painter.setPen(self._IC_COLOR)
        painter.drawText(self._ic_rect(option.rect), Qt.AlignmentFlag.AlignCenter, self._IC_MISSING)
        painter.restore()

    def eventFilter(self, obj, event):
        if obj is self._view.viewport():
            if event.type() == QEvent.Type.MouseMove:
                pos = event.position().toPoint()
                idx = self._view.indexAt(pos)
                tip = QModelIndex()
                if idx.isValid() and idx.column() == self._PLOT_COL:
                    node = idx.internalPointer()
                    if node and node.kind == "plot" and not node.plot_ref.get("egrn_doc", ""):
                        if self._ic_rect(self._view.visualRect(idx)).contains(pos):
                            tip = idx
                if tip != self._tip_index:
                    self._tip_index = tip
                    if tip.isValid():
                        _AppTooltip.show_at(
                            "Выписка ЕГРН не прикреплена",
                            self._view.viewport().mapToGlobal(pos),
                        )
                    else:
                        _AppTooltip.hide()
            elif event.type() == QEvent.Type.Leave:
                if self._tip_index.isValid():
                    self._tip_index = QModelIndex()
                    _AppTooltip.hide()
        return super().eventFilter(obj, event)


# ============================================================================ #
#  Стиль дерева                                                                #
# ============================================================================ #

_TREE_STYLE = """
    QTreeView#mainTable {
        background: #FFFFFF; border: none;
        color: #1F2937; font-size: 13px;
        selection-background-color: #C9D8E2; selection-color: #07414F;
        alternate-background-color: #F0F4F8;
        outline: 0;
    }
    QTreeView#mainTable::item {
        padding: 6px 10px; border-bottom: 1px solid #E3E8EF;
    }
    QTreeView#mainTable::item:hover { background: #DDE4EE; }
    QTreeView#mainTable::item:selected { background: #C9D8E2; color: #07414F; }
    QTreeView#mainTable QScrollBar:vertical {
        width: 12px; background: transparent; border: none;
    }
    QTreeView#mainTable QScrollBar::handle:vertical {
        background: #B5C8D5; border-radius: 5px; min-height: 24px;
        margin: 2px 2px 2px 2px;
    }
    QTreeView#mainTable QScrollBar::add-line:vertical,
    QTreeView#mainTable QScrollBar::sub-line:vertical { height: 0; }
    QTreeView#mainTable QScrollBar::add-page:vertical,
    QTreeView#mainTable QScrollBar::sub-page:vertical { background: none; }
"""

_SB_W = 12  # ширина скроллбара — должна совпадать с QSS выше

# Светлые фоновые варианты цветов долга для строк таблиц (debt_color → bg).
_DEBT_COLOR_LIGHT = {
    "#2e7d32": "#c8e6c9",
    "#f9a825": "#fff9c4",
    "#ef6c00": "#ffe0b2",
    "#c62828": "#ffcdd2",
}


# ============================================================================ #
#  Базовая плоская модель таблиц-долгов                                        #
# ============================================================================ #

class _FlatTableModel(QAbstractItemModel):
    """Плоская модель для таблиц долгов. Подклассы задают ``COLUMNS``.

    Строка — dict с ключами ``_text_/_sort_/_fg_/_bg_/_bold_/_tip_<col>``.
    Используется вкладками «Членские взносы» и «Электроэнергия».
    """

    COLUMNS: list[str] = []

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def load(self, rows: list[dict]):
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def top_nodes(self) -> list[dict]:
        return self._rows

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        return self.createIndex(row, column, self._rows[row])

    def parent(self, index):
        return QModelIndex()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLUMNS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = self.COLUMNS[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row.get(f"_text_{col}", "")
        if role == Qt.ItemDataRole.UserRole:
            return row.get(f"_sort_{col}", 0.0)
        if role == Qt.ItemDataRole.ForegroundRole:
            fg = row.get(f"_fg_{col}")
            return QColor(fg) if fg else None
        if role == Qt.ItemDataRole.BackgroundRole:
            bg = row.get(f"_bg_{col}")
            return QColor(bg) if bg else None
        if role == Qt.ItemDataRole.FontRole:
            if row.get(f"_bold_{col}"):
                f = QFont()
                f.setBold(True)
                return f
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.get(f"_tip_{col}", "")
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if not (0 <= column < len(self.COLUMNS)):
            return
        col = self.COLUMNS[column]
        sort_key = f"_sort_{col}"
        self.layoutAboutToBeChanged.emit()
        self._rows.sort(
            key=lambda r: (r.get(sort_key) is None, r.get(sort_key, 0.0)),
            reverse=(order == Qt.SortOrder.DescendingOrder),
        )
        self.layoutChanged.emit()


# ============================================================================ #
#  Вспомогательные виджеты для скруглённых контейнеров                        #
# ============================================================================ #

class _BorderOverlay(QWidget):
    """Прозрачный виджет-ребёнок, рисует только скруглённую рамку поверх всего."""

    def __init__(self, color: QColor, radius: int, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._color  = color
        self._radius = radius
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())
        self.raise_()

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() == QEvent.Type.Resize:
            self.setGeometry(self.parent().rect())
            self.raise_()
        return False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self._color, 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                                self._radius, self._radius)


class _ClipFrame(QFrame):
    """QFrame, который через setMask обрезает всё содержимое по скруглённому
    прямоугольнику — фон, hover-выделения дочерних виджетов не вылезают за углы."""

    def __init__(self, border_color: QColor, radius: int, parent=None):
        super().__init__(parent)
        self._radius = radius
        self._overlay = None  # создаётся после добавления детей
        self.setStyleSheet("background: transparent; border: none;")
        self._border_color = border_color

    def finish_setup(self):
        """Вызвать после того, как все дочерние виджеты добавлены."""
        self._overlay = _BorderOverlay(self._border_color, self._radius, self)
        self._update_mask()

    def _update_mask(self):
        sz = self.size()
        if sz.width() <= 0 or sz.height() <= 0:
            return
        bmp = QBitmap(sz)
        bmp.fill(Qt.GlobalColor.color0)
        p = QPainter(bmp)
        p.setBrush(Qt.GlobalColor.color1)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), self._radius, self._radius)
        p.end()
        self.setMask(QRegion(bmp))

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        self._update_mask()
        if self._overlay:
            self._overlay.setGeometry(self.rect())
            self._overlay.raise_()


# ============================================================================ #
#  Шапка таблицы с кастомными стрелками сортировки                            #
# ============================================================================ #

class _SortHeaderView(QHeaderView):
    """Шапка с синим фоном, стрелками сортировки и кнопкой удаления выбранных."""

    deleteRequested = pyqtSignal()
    searchChanged   = pyqtSignal(int, str)   # (col_logical, text)

    _BG       = QColor("#C9D8E2")
    _FG       = QColor("#07414F")
    _BORDER   = QColor("#B5C8D5")
    _ARR_ON   = QColor("#07414F")
    _ARR_OFF  = QColor("#9AABB6")
    _DEL_OFF  = QColor("#9CA3AF")   # нет выбора — серый
    _DEL_ON   = QColor("#DC2626")   # есть выбор — красный
    _DEL_HOV  = QColor("#B91C1C")   # наведение — тёмно-красный

    _IC_CHARS = {chr(0xE73A), chr(0xF567), chr(0xF0DC)}  # иконки-индикаторы
    _IC_COLOR = QColor("#F59E0B")

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setSectionsClickable(True)
        self.setSortIndicatorShown(False)
        self.setFixedHeight(34)
        self.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        self.setMouseTracking(True)
        self._del_col        = -1
        self._has_sel        = False
        self._del_hovered    = False
        self._fill_tag       = QFont.Tag.fromString("FILL")
        self._col_indicators: dict[int, list] = {}   # col → [(lbl, cnt, tip), ...]
        self._tip_col        = -1
        self._tip_text       = ""
        self._search_cols:   set  = set()
        self._search_active: dict = {}
        self._search_fields: dict = {}

    # -- публичный API --------------------------------------------------------

    def set_delete_col(self, col: int):
        self._del_col = col

    def set_col_indicators(self, col: int, indicators: list):
        """indicators: [(lbl, count, tooltip_text), ...] — только с count > 0."""
        self._col_indicators[col] = [i for i in indicators if i[1] > 0]
        self.viewport().update()

    def _indicator_zones(self, logical_index: int, rect: QRect) -> list:
        """Возвращает [(QRect, tooltip)] для каждого индикатора, справа налево."""
        indicators = self._col_indicators.get(logical_index, [])
        if not indicators:
            return []
        f_cnt = QFont(); f_cnt.setPixelSize(10); f_cnt.setBold(True)
        fm    = QFontMetrics(f_cnt)
        arr_w = 18
        cur_x = rect.right() - arr_w - 6
        zones = []
        for lbl, cnt, tip in reversed(indicators):
            lw   = 18 if lbl in self._IC_CHARS else fm.horizontalAdvance(lbl)
            cw   = fm.horizontalAdvance(str(cnt))
            zone = QRect(cur_x - cw - 2 - lw, rect.top(), lw + 2 + cw, rect.height())
            zones.append((zone, tip))
            cur_x -= lw + 2 + cw + 4
        return zones

    def set_has_selection(self, has: bool):
        if self._has_sel != has:
            self._has_sel = has
            if not has:
                self._del_hovered = False
                self.viewport().unsetCursor()
            self.viewport().update()

    def add_search_col(self, col: int):
        """Регистрирует столбец как поисковой и создаёт поле ввода."""
        self._search_cols.add(col)
        le = QLineEdit(self.viewport())
        le.setPlaceholderText("Поиск...")
        le.hide()
        le.setStyleSheet(
            "QLineEdit {"
            "  background: rgba(255,255,255,0.45);"
            "  border: 1px solid rgba(7,65,79,0.5);"
            "  border-radius: 3px;"
            "  color: #07414F;"
            "  font-size: 12px;"
            "  padding: 1px 4px;"
            "}"
        )
        le.textChanged.connect(lambda text, c=col: self.searchChanged.emit(c, text))
        self._search_fields[col] = le
        self._search_active[col] = False

    def _toggle_search(self, col: int):
        now = not self._search_active.get(col, False)
        self._search_active[col] = now
        le = self._search_fields.get(col)
        if le:
            if now:
                le.show()
                le.setFocus()
            else:
                le.hide()
                le.clear()
        self.viewport().update()

    def _compute_ind_left_x(self, logical: int, arr_left: int) -> int:
        """Левая граница зоны индикаторов (= правая граница текста/поля)."""
        indicators = self._col_indicators.get(logical, [])
        if not indicators:
            return arr_left - 2
        f_cnt = QFont(); f_cnt.setPixelSize(10); f_cnt.setBold(True)
        fm    = QFontMetrics(f_cnt)
        total = sum(
            (18 if lbl in self._IC_CHARS else fm.horizontalAdvance(lbl))
            + 2 + fm.horizontalAdvance(str(cnt)) + 4
            for lbl, cnt, _ in indicators
        ) - 4
        return arr_left - 2 - total - 6

    def _search_icon_zone(self, logical: int, sec_rect: QRect) -> QRect:
        """QRect иконки поиска или закрытия поиска для кликов/курсора."""
        IC_W   = 22
        ind_lx = self._compute_ind_left_x(logical, sec_rect.right() - 18 - 2)
        if self._search_active.get(logical, False):
            return QRect(ind_lx - IC_W - 4, sec_rect.top(), IC_W, sec_rect.height())
        label  = str(self.model().headerData(
            logical, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or "")
        f      = QFont(); f.setPixelSize(12); f.setBold(True)
        max_tw = max(0, ind_lx - sec_rect.left() - IC_W - 16)
        tw     = min(QFontMetrics(f).horizontalAdvance(label), max_tw)
        si_x   = min(sec_rect.left() + 10 + tw + 4, ind_lx - IC_W - 4)
        return QRect(si_x, sec_rect.top(), IC_W, sec_rect.height())

    # -- mouse events ---------------------------------------------------------

    def mouseMoveEvent(self, event):
        pos  = event.position().toPoint()
        x    = pos.x()
        hand = False

        # -- кнопка удаления --
        if self._del_col >= 0 and self._has_sel:
            sec_x = self.sectionViewportPosition(self._del_col)
            sec_w = self.sectionSize(self._del_col)
            hov   = sec_x <= x < sec_x + sec_w
            if hov != self._del_hovered:
                self._del_hovered = hov
                self.viewport().update()
            if hov:
                hand = True
        else:
            if self._del_hovered:
                self._del_hovered = False
                self.viewport().update()

        # -- иконки поиска --
        if not hand:
            logical = self.logicalIndexAt(x)
            if logical in self._search_cols:
                sec_x    = self.sectionViewportPosition(logical)
                sec_rect = QRect(sec_x, 0, self.sectionSize(logical), self.height())
                if self._search_icon_zone(logical, sec_rect).contains(pos):
                    hand = True

        if hand:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.viewport().unsetCursor()

        # -- тултипы индикаторов --
        tip_col, tip_txt = -1, ""
        logical = self.logicalIndexAt(x)
        if logical >= 0:
            sec_x    = self.sectionViewportPosition(logical)
            sec_rect = QRect(sec_x, 0, self.sectionSize(logical), self.height())
            for zone, tip in self._indicator_zones(logical, sec_rect):
                if zone.contains(pos):
                    tip_col, tip_txt = logical, tip
                    break
        if tip_col != self._tip_col or tip_txt != self._tip_text:
            self._tip_col  = tip_col
            self._tip_text = tip_txt
            if tip_col >= 0:
                _AppTooltip.show_at(tip_txt, self.viewport().mapToGlobal(pos))
            else:
                _AppTooltip.hide()

        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._del_hovered:
            self._del_hovered = False
            self.viewport().update()
        self.viewport().unsetCursor()
        if self._tip_col >= 0:
            self._tip_col  = -1
            self._tip_text = ""
            _AppTooltip.hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            x   = pos.x()

            if self._del_col >= 0 and self._has_sel:
                sec_x = self.sectionViewportPosition(self._del_col)
                sec_w = self.sectionSize(self._del_col)
                if sec_x <= x < sec_x + sec_w:
                    self.deleteRequested.emit()
                    return

            logical = self.logicalIndexAt(x)
            if logical in self._search_cols:
                sec_x    = self.sectionViewportPosition(logical)
                sec_rect = QRect(sec_x, 0, self.sectionSize(logical), self.height())
                if self._search_icon_zone(logical, sec_rect).contains(pos):
                    self._toggle_search(logical)
                    return

        super().mousePressEvent(event)

    # -- paint ----------------------------------------------------------------

    def paintSection(self, painter: QPainter, rect: QRect, logical_index: int):
        if not rect.isValid():
            return
        painter.save()
        painter.fillRect(rect, self._BG)

        painter.setPen(QPen(self._BORDER, 1))
        painter.drawLine(rect.right(), rect.top() + 4, rect.right(), rect.bottom() - 4)

        model = self.model()
        label = (
            str(model.headerData(logical_index, Qt.Orientation.Horizontal,
                                 Qt.ItemDataRole.DisplayRole) or "")
            if model else ""
        )
        if label:
            # Одиночный символ Material Symbols — рисуем как иконку, без стрелок
            if len(label) == 1 and 0xE000 <= ord(label) <= 0xF8FF:
                f_ic = QFont("Material Symbols Rounded")
                f_ic.setPixelSize(18)
                if logical_index == self._del_col:
                    if not self._has_sel:
                        color = self._DEL_OFF
                        fill  = 0.0
                    elif self._del_hovered:
                        color = self._DEL_HOV
                        fill  = 1.0
                    else:
                        color = self._DEL_ON
                        fill  = 0.0
                    f_ic.setVariableAxis(self._fill_tag, fill)
                    painter.setPen(color)
                else:
                    painter.setPen(self._FG)
                painter.setFont(f_ic)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
            else:
                arr_w    = 18
                arr_rect = QRect(rect.right() - arr_w - 2, rect.top(), arr_w, rect.height())

                # Индикаторы (справа налево, перед стрелками сортировки)
                indicators = self._col_indicators.get(logical_index, [])
                ind_left_x = arr_rect.left() - 2
                if indicators:
                    f_ic  = QFont("Material Symbols Rounded"); f_ic.setPixelSize(14)
                    f_cnt = QFont(); f_cnt.setPixelSize(10); f_cnt.setBold(True)
                    fm    = QFontMetrics(f_cnt)
                    cur_x = ind_left_x
                    for lbl, cnt, _ in reversed(indicators):
                        lw    = 18 if lbl in self._IC_CHARS else fm.horizontalAdvance(lbl)
                        cw    = fm.horizontalAdvance(str(cnt))
                        lbl_r = QRect(cur_x - cw - 2 - lw, rect.top(), lw, rect.height())
                        cnt_r = QRect(cur_x - cw, rect.top(), cw, rect.height())
                        painter.setFont(f_ic if lbl in self._IC_CHARS else f_cnt)
                        painter.setPen(self._IC_COLOR)
                        painter.drawText(lbl_r, Qt.AlignmentFlag.AlignCenter, lbl)
                        painter.setFont(f_cnt)
                        painter.drawText(cnt_r, Qt.AlignmentFlag.AlignCenter, str(cnt))
                        cur_x -= lw + 2 + cw + 4
                    ind_left_x = cur_x - 4

                # Стрелки сортировки
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                cx = arr_rect.left() + arr_rect.width() // 2
                cy = arr_rect.top() + arr_rect.height() // 2
                is_sorted = (self.sortIndicatorSection() == logical_index)
                asc  = is_sorted and self.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
                desc = is_sorted and self.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self._ARR_ON if asc else self._ARR_OFF)
                painter.drawPolygon(QPolygon([
                    QPoint(cx - 4, cy - 1), QPoint(cx + 4, cy - 1), QPoint(cx, cy - 6),
                ]))
                painter.setBrush(self._ARR_ON if desc else self._ARR_OFF)
                painter.drawPolygon(QPolygon([
                    QPoint(cx - 4, cy + 1), QPoint(cx + 4, cy + 1), QPoint(cx, cy + 6),
                ]))

                # Заголовок / поле поиска
                IC_W      = 22
                is_srch   = logical_index in self._search_cols
                is_active = self._search_active.get(logical_index, False)

                if is_srch and is_active:
                    off_x    = ind_left_x - IC_W - 4
                    off_rect = QRect(off_x, rect.top(), IC_W, rect.height())
                    le       = self._search_fields[logical_index]
                    le_h     = 22
                    le_rect  = QRect(rect.left() + 8,
                                     rect.top() + (rect.height() - le_h) // 2,
                                     max(0, off_x - rect.left() - 10),
                                     le_h)
                    le.setGeometry(le_rect)
                    if not le.isVisible():
                        le.show()
                        le.setFocus()
                    f_ico = QFont("Material Symbols Rounded"); f_ico.setPixelSize(18)
                    painter.setFont(f_ico)
                    painter.setPen(self._FG)
                    painter.drawText(off_rect, Qt.AlignmentFlag.AlignCenter, chr(0xEA76))
                else:
                    if is_srch:
                        le = self._search_fields.get(logical_index)
                        if le and le.isVisible():
                            le.hide()

                    if is_srch:
                        title_max_w = max(0, ind_left_x - rect.left() - IC_W - 16)
                    else:
                        title_max_w = max(0, ind_left_x - rect.left() - 6)

                    text_rect = QRect(rect.left() + 10, rect.top(), title_max_w, rect.height())
                    painter.setPen(self._FG)
                    f = QFont(); f.setPixelSize(12); f.setBold(True)
                    painter.setFont(f)
                    painter.drawText(text_rect,
                                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                     label)

                    if is_srch:
                        fm_t  = QFontMetrics(f)
                        tw    = min(fm_t.horizontalAdvance(label), title_max_w)
                        si_x  = min(rect.left() + 10 + tw + 4, ind_left_x - IC_W - 4)
                        si_r  = QRect(si_x, rect.top(), IC_W, rect.height())
                        f_ico = QFont("Material Symbols Rounded"); f_ico.setPixelSize(18)
                        painter.setFont(f_ico)
                        painter.setPen(self._FG)
                        painter.drawText(si_r, Qt.AlignmentFlag.AlignCenter, chr(0xE8B6))

        painter.restore()


# ============================================================================ #
#  Чистый список участов (вариант А): модель + делегат строки                  #
# ============================================================================ #

def _plot_primary_owner(plot: dict):
    """Главный контакт активной группы (is_visible, иначе первый)."""
    g = ownership.active_group(plot) or {}
    owners = ownership.group_owners(g)
    return next((o for o in owners if isinstance(o, dict) and o.get("is_visible")),
                owners[0] if owners else None)


def _plot_primary_name(plot: dict) -> str:
    """ФИО главного контакта активной группы (как в карточке детали)."""
    main = _plot_primary_owner(plot)
    return ownership.owner_name(main) if main else ""


def _plot_primary_phone(plot: dict) -> str:
    """Телефон главного контакта активной группы, либо пусто."""
    main = _plot_primary_owner(plot)
    return str(main.get("phone", "")) if isinstance(main, dict) else ""


def _short_name(full: str) -> str:
    """«Фамилия Имя Отчество» → «Фамилия И.О.» (для списка)."""
    parts = str(full or "").split()
    if not parts:
        return ""
    initials = "".join(f"{p[0]}." for p in parts[1:3] if p)
    return f"{parts[0]} {initials}".strip()


def _truncate_name(full: str, limit: int = 20) -> str:
    """Обрезает длинное ФИО до limit символов с «…» на конце —
    чтобы карточка контакта не уезжала за границы соседних полей."""
    s = str(full or "")
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


def _plot_search_names(plot: dict) -> list[str]:
    """Все ФИО участка (по всем группам) — для поиска."""
    out = []
    for g in ownership.plot_groups(plot):
        for o in ownership.group_owners(g):
            nm = ownership.owner_name(o)
            if nm:
                out.append(nm)
    return out


class PlotsListModel(QAbstractListModel):
    """Плоский список участков для QListView. Отрисовкой занимается делегат
    (читает участок через UserRole / plot_at). Долг — из внешнего кэша."""

    PlotRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list = []
        self._debt: dict = {}   # num -> float | None (None = нет данных)

    def set_plots(self, plots: list):
        self.beginResetModel()
        self._rows = list(plots)
        self.endResetModel()

    def set_debt_cache(self, debt: dict):
        self._debt = debt or {}
        if self._rows:
            self.dataChanged.emit(self.index(0), self.index(len(self._rows) - 1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def plot_at(self, row: int):
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def row_of(self, plot) -> int:
        for i, p in enumerate(self._rows):
            if p is plot:
                return i
        return -1

    def debt_text(self, plot: dict) -> str:
        v = self._debt.get(str(plot.get("num", "")))
        if v is None or abs(v) < 0.005:
            return "—"
        return fmt_money(v)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == self.PlotRole:
            return self._rows[index.row()]
        return None


class _PlotRowDelegate(QStyledItemDelegate):
    """Чистая строка списка: № (слева) · ФИО · Долг (справа). Без сетки."""

    _ROW_H   = 36
    _NUM_W   = 44
    _PHONE_W = 140
    _DEBT_W  = 110
    _PAD     = 14
    _FG_NUM  = QColor("#1F2937")
    _FG_NAME = QColor("#1F2937")
    _FG_PHONE = QColor("#374151")
    _FG_DEBT = QColor("#374151")
    _SEL_BG  = QColor("#C9D8E2")
    _HOV_BG  = QColor("#EBF4F6")

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self._ROW_H)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        model = index.model()
        plot = model.plot_at(index.row())
        if plot is None:
            painter.restore()
            return

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover     = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._SEL_BG if selected else self._HOV_BG)
            painter.drawRoundedRect(QRectF(rect.adjusted(4, 3, -4, -3)), 8, 8)

        f = QFont(); f.setPixelSize(13)
        painter.setFont(f)
        # Текст оптически центрируем на 1px выше (AlignVCenter сажает глиф чуть ниже).
        vtop = rect.top() - 1
        # № (слева)
        painter.setPen(self._FG_NUM)
        num_rect = QRect(rect.left() + self._PAD, vtop, self._NUM_W, rect.height())
        painter.drawText(num_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         str(plot.get("num", "")))
        # Долг (справа)
        debt_rect = QRect(rect.right() - self._PAD - self._DEBT_W, vtop,
                          self._DEBT_W, rect.height())
        painter.setPen(self._FG_DEBT)
        painter.drawText(debt_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         model.debt_text(plot))
        # Телефон (перед долгом; зазор 8 — как spacing капшена)
        phone_rect = QRect(debt_rect.left() - 8 - self._PHONE_W, vtop,
                           self._PHONE_W, rect.height())
        phone = _plot_primary_phone(plot)
        painter.setPen(self._FG_PHONE)
        painter.drawText(phone_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         phone or "—")
        # ФИО (между № и телефоном)
        name_left = rect.left() + self._PAD + self._NUM_W + 8
        name_rect = QRect(name_left, vtop, phone_rect.left() - name_left - 8, rect.height())
        name = _short_name(_plot_primary_name(plot)) or "—"
        elided = QFontMetrics(f).elidedText(name, Qt.TextElideMode.ElideRight, name_rect.width())
        painter.setPen(self._FG_NAME)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         elided)
        painter.restore()


# ============================================================================ #
#  PlotsWidget                                                                 #
# ============================================================================ #

class PlotsWidget(QWidget):
    """Вкладка участков: ручное добавление и управление списком."""

    plotsUpdated = pyqtSignal()

    DATA_FILE = os.path.join(DATA_DIR, "snt_plots.json")

    def __init__(self):
        super().__init__()
        # Прозрачный фон страницы — чтобы проступал белый contentFrame (окно вкладки).
        self.setAutoFillBackground(False)
        self._plots: list = self._load()
        self._df = None                       # выписка — нужна для долга в списке/детали
        self._search_text = ""
        self._debt_cache: dict = {}           # num -> float | None (долг ЧВ+электро)
        self._setup_ui()
        self._rebuild_table()
        # Выровнять заглушку капшена под желоб скроллбара после раскладки
        QTimer.singleShot(0, self._sync_caption_stub)

    def refresh(self, df):
        """Принимает загруженную выписку: пересчёт долга и обновление списка."""
        self._df = df
        self._recompute_debt()
        self.list_model.set_debt_cache(self._debt_cache)

    def _load(self) -> list:
        try:
            if os.path.exists(self.DATA_FILE):
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Нормализуем: старый формат owners — list[str], новый — list[dict]
                for plot in data:
                    plot["owners"] = [
                        o if isinstance(o, dict) else _make_owner(o)
                        for o in plot.get("owners", [])
                    ]
                return self._ensure_people_registry(data)
        except Exception:
            pass
        return []

    def _ensure_people_registry(self, plots: list) -> list:
        """Однократная миграция: строит реестр людей и проставляет person_id.

        Запускается только если snt_people.json ещё нет. Делает резервную копию
        участков, проставляет person_id во всех владельцев (дедуп по полному ФИО),
        сохраняет реестр. Идемпотентна (повторно не сработает — файл уже есть)."""
        if os.path.exists(people_reg.PEOPLE_FILE):
            return plots
        ppl, migrated = people_reg.migrate_people_from_plots(plots)
        try:
            if os.path.exists(self.DATA_FILE):
                shutil.copyfile(self.DATA_FILE, self.DATA_FILE + ".backup")
            with open(self.DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(migrated, f, ensure_ascii=False, indent=2)
            people_reg.save_people(ppl)
        except Exception:
            return plots  # запись не удалась — работаем на исходных, повторим позже
        return migrated

    def _save(self):
        try:
            with open(self.DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self._plots, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        self.plotsUpdated.emit()

    def reload(self):
        self._plots = self._load()
        self._rebuild_table()
        self.plotsUpdated.emit()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        # Поля = 0: разделитель список|деталь идёт на всю высоту рамки.
        # Внутренние отступы переносим в колонки (table_vbox / панель детали).
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Чистый список участков (вариант А: список, не таблица) ───────────
        # Шапка списка: заголовок + счётчик слева, действия списка — справа.
        list_hdr = QHBoxLayout()
        list_hdr.setSpacing(8)
        lbl_plots = QLabel("Участки")
        lbl_plots.setStyleSheet(
            "font-size:14px; font-weight:700; color:#1F2937; background:transparent;")
        list_hdr.addWidget(lbl_plots)
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("font-size:12px; color:#9CA3AF; background:transparent;")
        list_hdr.addWidget(self._count_lbl)

        # Поиск — в той же строке, между счётчиком и кнопками (экономит место).
        self._search = QLineEdit()
        self._search.setPlaceholderText("Поиск по номеру или ФИО")
        self._search.setClearButtonEnabled(True)
        # Свой стиль вместо нативного: «подбородок» (нижняя линия), на фокусе —
        # бирюзовый вместо системно-синего. Толщина одинаковая, чтобы текст не прыгал.
        self._search.setStyleSheet(
            "QLineEdit{background:transparent;border:none;border-bottom:2px solid #D1D5DB;"
            "border-radius:0;padding:6px 2px;font-size:13px;color:#1F2937;}"
            "QLineEdit:focus{border-bottom:2px solid #07414F;}")
        self._search.textChanged.connect(self._on_search_text)
        list_hdr.addWidget(self._search, stretch=1)

        def _hdr_icon_btn(tooltip: str, handler) -> QPushButton:
            b = QPushButton()
            b.setFixedSize(32, 32)
            b.setIconSize(QSize(22, 22))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tooltip)
            b.setStyleSheet(
                "QPushButton{background:transparent;border:none;border-radius:6px;}"
                "QPushButton:hover{background:#EBF4F6;}")
            b.clicked.connect(handler)
            return b

        self._btn_import = _hdr_icon_btn("Импорт из Excel", self._import_from_excel)
        self._btn_import.setIcon(_mat_icon(0xEAF3, 22, color="#9CA3AF"))  # file_open
        list_hdr.addWidget(self._btn_import)
        self._btn_add = _hdr_icon_btn("Добавить участок", self._add_plot)
        list_hdr.addWidget(self._btn_add)
        self._refresh_add_icon()

        # Капшен колонок (геометрия ~ как в делегате)
        cap = QWidget()
        cap.setStyleSheet("background:transparent;")
        cap_l = QHBoxLayout(cap)
        cap_l.setContentsMargins(_PlotRowDelegate._PAD, 0, _PlotRowDelegate._PAD, 0)
        cap_l.setSpacing(8)
        cap_num = QLabel("№"); cap_num.setFixedWidth(_PlotRowDelegate._NUM_W)
        cap_name = QLabel("Контакт")
        cap_phone = QLabel("Телефон"); cap_phone.setFixedWidth(_PlotRowDelegate._PHONE_W)
        cap_debt = QLabel("Долг"); cap_debt.setFixedWidth(_PlotRowDelegate._DEBT_W)
        cap_debt.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for c in (cap_num, cap_name, cap_phone, cap_debt):
            c.setStyleSheet("font-size:11px; color:#9CA3AF; background:transparent;")
        cap_l.addWidget(cap_num)
        cap_l.addWidget(cap_name, stretch=1)
        cap_l.addWidget(cap_phone)
        cap_l.addWidget(cap_debt)
        # Заглушка под скроллбар — чтобы колонки капшена совпали с данными
        # (делегат рисует в вьюпорте, уже на ширину желоба). Ширина выставляется
        # в _sync_caption_stub по фактической ширине желоба списка.
        self._cap_stub = QWidget()
        self._cap_stub.setStyleSheet("background:transparent;")
        cap_l.addWidget(self._cap_stub)

        self.list_model = PlotsListModel(self)
        self.list_view = QListView()
        self.list_view.setModel(self.list_model)
        self._row_delegate = _PlotRowDelegate(self.list_view)
        self.list_view.setItemDelegate(self._row_delegate)
        self.list_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list_view.setMouseTracking(True)
        self.list_view.setUniformItemSizes(True)
        # Скроллбар всегда зарезервирован — постоянная ширина вьюпорта, чтобы
        # колонки данных стабильно совпадали с капшеном (см. _sync_caption_stub).
        self.list_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_view.setFrameShape(QFrame.Shape.NoFrame)
        # «transparent» на скроллбаре ненадёжно перекрывает глобальный фон
        # #F0F3F9 у QAbstractScrollArea (виден именно он вместо «ничего»,
        # см. подробный докстринг у _drawer_scroll ниже) — поэтому вместо
        # прозрачности красим «дорожку» тем же сплошным цветом, что и
        # реальный фон под ней (contentFrame — белый), это гарантированно
        # рендерится вне зависимости от тонкостей альфа-композитинга Qt.
        self.list_view.setStyleSheet(
            "QListView{background:transparent;border:none;outline:0;}"
            "QListView QScrollBar:vertical{background:#FFFFFF;width:8px;border:none;}"
            "QListView QScrollBar::handle:vertical{"
            "background:#C3CAD3;border-radius:4px;min-height:30px;}"
            "QListView QScrollBar::handle:vertical:hover{background:#97A1AE;}"
            "QListView QScrollBar::add-line:vertical,"
            "QListView QScrollBar::sub-line:vertical{height:0;}"
            "QListView QScrollBar::add-page:vertical,"
            "QListView QScrollBar::sub-page:vertical{background:#FFFFFF;}")
        # Перебиваем глобальное правило QAbstractScrollArea>QWidget (тонирует вьюпорт)
        self.list_view.viewport().setStyleSheet("background:transparent;")
        self._list_sb_style = QStyleFactory.create("Fusion")
        if self._list_sb_style is not None:
            self.list_view.setStyle(self._list_sb_style)
            self.list_view.verticalScrollBar().setStyle(self._list_sb_style)
        self.list_view.clicked.connect(self._on_list_clicked)
        self._sync_caption_stub()  # выровнять заглушку капшена под желоб скроллбара

        table_vbox = QVBoxLayout()
        table_vbox.setSpacing(8)
        # Паддинги колонки списка (поля страницы = 0): слева/сверху/снизу 24,
        # справа 8 — небольшой зазор скроллбара до разделителя.
        table_vbox.setContentsMargins(24, 24, 8, 24)
        table_vbox.addLayout(list_hdr)
        table_vbox.addWidget(cap)
        table_vbox.addWidget(self.list_view, stretch=1)

        # ── Тело: список (master) | разделитель-линия | панель детали (drawer) ──
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addLayout(table_vbox, stretch=1)

        self._detail_panel = None
        self._editing_plot = None
        self._drawer = QFrame()
        self._drawer.setObjectName("plotDrawer")
        self._drawer.setFixedWidth(470)
        # Разделитель список|деталь — в цвет/толщину рамки окна, на всю высоту.
        self._drawer.setStyleSheet(
            "QFrame#plotDrawer{background:transparent;border:none;"
            "border-left:1px solid #D5DCE4;}")
        # Скролл-область на ВСЮ высоту drawer (контент, как и раньше,
        # обрезается ровно по границе окна — никаких «белых полос» поверх
        # него), а полоса прокрутки — ОТДЕЛЬНЫЙ standalone QScrollBar в своей
        # колонке справа, с отступами сверху/снизу от скруглённых углов окна.
        # Все попытки получить этот зазор на встроенном скроллбаре проваливались:
        #  * QSS margin у QScrollBar — зона отступа красится «сырым» фоном
        #    виджета (не стилем дорожки) и вылазит серым пятном;
        #  * отступ у layout — сжимает саму видимую область прокрутки, и
        #    контент начинает исчезать за постоянной белой полосой ДО границы;
        #  * setMask на drawer — приём, от которого проект уже отказался
        #    (см. _RoundedFrame в main.py: грязный антиалиасинг).
        # Отдельный QScrollBar решает всё сразу: он не потомок
        # QAbstractScrollArea (глобальный QSS-фикс вьюпорта #F0F3F9 его не
        # перекрашивает — прозрачная дорожка честно показывает белый
        # contentFrame), а отступ от углов — обычные layout-поля его колонки,
        # т.е. по-настоящему пустое место, где рисовать нечему.
        drawer_lyt = QHBoxLayout(self._drawer)
        drawer_lyt.setContentsMargins(0, 0, 2, 0)
        drawer_lyt.setSpacing(0)
        self._drawer_scroll = QScrollArea()
        self._drawer_scroll.setWidgetResizable(True)
        self._drawer_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._drawer_scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self._drawer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._drawer_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        drawer_lyt.addWidget(self._drawer_scroll, stretch=1)

        sb_col = QVBoxLayout()
        # 14px — радиус скругления окна (QFrame#contentFrame{border-radius:14px})
        sb_col.setContentsMargins(2, 14, 0, 14)
        self._drawer_vsb = QScrollBar(Qt.Orientation.Vertical)
        self._drawer_sb_style = QStyleFactory.create("Fusion")
        if self._drawer_sb_style is not None:
            self._drawer_vsb.setStyle(self._drawer_sb_style)
        self._drawer_vsb.setFixedWidth(8)
        self._drawer_vsb.setStyleSheet("""
            QScrollBar:vertical { background: transparent; width: 8px; border: none; }
            QScrollBar::handle:vertical {
                background: #C3CAD3; border-radius: 4px; min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: #97A1AE; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
        """)
        self._drawer_vsb.setVisible(False)
        sb_col.addWidget(self._drawer_vsb)
        drawer_lyt.addLayout(sb_col)

        # Синхронизация с внутренним (скрытым) скроллбаром области: диапазон/
        # шаг/видимость — от него к внешнему, значение — в обе стороны.
        _inner = self._drawer_scroll.verticalScrollBar()

        def _sync_range(lo: int, hi: int, inner=_inner):
            self._drawer_vsb.setRange(lo, hi)
            self._drawer_vsb.setPageStep(inner.pageStep())
            self._drawer_vsb.setSingleStep(inner.singleStep())
            self._drawer_vsb.setVisible(hi > lo)

        _inner.rangeChanged.connect(_sync_range)
        _inner.valueChanged.connect(self._drawer_vsb.setValue)
        self._drawer_vsb.valueChanged.connect(_inner.setValue)

        self._drawer.setVisible(False)
        body.addWidget(self._drawer)

        layout.addLayout(body)

    def _open_detail(self, plot):
        """Открывает деталь участка в правом drawer (вместо модального диалога)."""
        if self._detail_panel is not None:
            if self._editing_plot is not None:
                # Редактировали существующий участок — закрываем как при
                # «Закрыть», а не просто отбрасываем панель. Если пользователь
                # отменил закрытие (несохранённые правки в открытой карточке
                # контакта, диалог «Сохранить/Не сохранять/Отмена») — остаёмся
                # на текущем участке, переход/новый участок не открываем.
                if not self._detail_panel._on_close():
                    return
            else:
                self._close_detail()  # черновик нового участка — отбрасываем как есть
        self._editing_plot = plot  # None — новый участок
        if plot is not None:
            existing = {str(p.get("num", "")) for p in self._plots if p is not plot}
        else:
            existing = {str(p.get("num", "")) for p in self._plots}
        panel = PlotEditDialog(plot_data=plot, parent=self, df=self._df,
                               existing_nums=existing)
        panel.closed.connect(self._on_detail_closed)
        panel.personDeleted.connect(self._on_person_deleted)
        if plot is not None:
            panel.deleted.connect(self._on_detail_delete)
        self._detail_panel = panel
        # Скрываем старый виджет до замены, чтобы он не всплыл как top-level окно
        old = self._drawer_scroll.widget()
        if old is not None:
            old.hide()
        self._drawer_scroll.setWidget(panel)
        self._drawer.setVisible(True)
        # Подсветить выбранный участок в списке
        row = self.list_model.row_of(plot) if plot is not None else -1
        if row >= 0:
            self.list_view.setCurrentIndex(self.list_model.index(row))
        else:
            self.list_view.clearSelection()
        self._refresh_add_icon()

    def _refresh_add_icon(self):
        """Иконка «Добавить участок» (f8eb add_home). Статичная — кнопка
        больше не открывает/переключает панель справа, а открывает модальное
        окно быстрого добавления (см. _add_plot), поэтому подсветки
        «активного» состояния больше нет."""
        self._btn_add.setIcon(_mat_icon(0xF8EB, 22, color="#9CA3AF"))

    def _sync_caption_stub(self):
        """Ширина заглушки капшена = ширина желоба скроллбара списка.

        Делегат рисует строки в вьюпорте (уже на ширину желоба), а капшен —
        во всю ширину; заглушка компенсирует разницу, чтобы колонки совпали.
        Берём ширину из стиля (PM_ScrollBarExtent) — не зависит от тайминга
        раскладки (живой замер width−viewport бывает нулевым до первого показа)."""
        if not hasattr(self, "_cap_stub") or not hasattr(self, "list_view"):
            return
        ext = self.list_view.style().pixelMetric(
            QStyle.PixelMetric.PM_ScrollBarExtent, None,
            self.list_view.verticalScrollBar())
        self._cap_stub.setFixedWidth(ext)

    def _on_person_deleted(self, person_id: str):
        """Человек удалён полностью (см. PlotEditDialog._delete_contact).

        Диалог участка видит только СВОЙ участок, поэтому убрать человека
        (вместе с его Выпиской ЕГРН — она у каждого участка своя) со ВСЕХ
        ОСТАЛЬНЫХ участков должен хост — он один видит self._plots целиком."""
        if not person_id:
            return
        changed = False
        for plot in self._plots:
            if plot is self._editing_plot:
                continue  # этот участок уже обработан самим диалогом
            for group in ownership.plot_groups(plot):
                owners = group.get("owners", []) or []
                kept = []
                for o in owners:
                    if isinstance(o, dict) and o.get("person_id") == person_id:
                        egrn_path = o.get("egrn_doc", "")
                        if egrn_path:
                            try:
                                os.remove(egrn_path)
                            except OSError:
                                pass
                        changed = True
                        continue
                    kept.append(o)
                if len(kept) != len(owners):
                    # Если удалённый был избранным — избранным становится
                    # первый в списке; если остался ровно один контакт — он
                    # автоматически избранный (см. _ensure_single_primary).
                    _ensure_single_primary(kept)
                    group["owners"] = kept
        if changed:
            self._save()
            self._recompute_debt()
            self._rebuild_table()

    def _on_detail_closed(self, saved: bool):
        panel = self._detail_panel
        if saved and panel is not None:
            result = panel.get_result()
            if result:
                plot = self._editing_plot
                if plot is not None:
                    for i, p in enumerate(self._plots):
                        if p is plot:
                            self._plots[i] = result
                            break
                else:
                    self._plots.append(result)
                self._save()
                self._recompute_debt()
                self._rebuild_table()
        self._close_detail()

    def _close_detail(self):
        self._drawer.setVisible(False)
        self.list_view.clearSelection()
        if self._detail_panel is not None:
            self._detail_panel.detach()
            self._detail_panel.deleteLater()
            self._detail_panel = None
        self._editing_plot = None
        self._refresh_add_icon()

    def _rebuild_table(self):
        text = self._search_text
        plots = self._plots
        if text:
            plots = [p for p in plots
                     if text in str(p.get("num", "")).lower()
                     or any(text in nm.lower() for nm in _plot_search_names(p))]
        self.list_model.set_plots(plots)
        self.list_model.set_debt_cache(self._debt_cache)
        total, shown = len(self._plots), len(plots)
        self._count_lbl.setText(f"{shown} из {total}" if shown < total else str(total))

    def _on_search_text(self, text: str):
        self._search_text = text.strip().lower()
        self._rebuild_table()

    def _on_list_clicked(self, index: QModelIndex):
        plot = self.list_model.plot_at(index.row())
        if plot is not None:
            self._open_detail(plot)

    def _recompute_debt(self):
        """Кэш долга (ЧВ+электро) по всем участкам. «—» (None), если нет выписки."""
        debt: dict = {}
        if self._df is not None:
            try:
                from core import vznosy as vzn
                from core import energy as en
                rates = vzn.load_rates(); adj = vzn.load_adjustments()
                area_map = vzn.plot_area_map()
                meters = en.load_meters(); en_rates = en.load_rates()
                repl = en.load_replacements(); base = en.load_baseline()
                today = date.today()
                for p in self._plots:
                    num = str(p.get("num", ""))
                    since = ownership.group_since(ownership.active_group(p) or {})
                    total = 0.0; got = False
                    try:
                        gb = vzn.balance_for_active_group(
                            num, area_map.get(num), today, rates, adj, self._df, since=since)
                        total += gb.debt; got = True
                    except Exception:
                        pass
                    try:
                        egb = en.balance_for_active_group(
                            num, today, meters, en_rates, repl, base, self._df, since=since)
                        total += egb.debt; got = True
                    except Exception:
                        pass
                    debt[num] = total if got else None
            except Exception:
                debt = {}
        self._debt_cache = debt

    def _on_detail_delete(self):
        """Удаление участка из панели детали (подтверждение — в самой панели)."""
        plot = self._editing_plot
        if plot is not None:
            self._plots = [p for p in self._plots if p is not plot]
            self._save()
            self._recompute_debt()
            self._rebuild_table()
        self._close_detail()

    def _edit_plot(self, plot: dict):
        self._open_detail(plot)

    def _import_from_excel(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл Excel", "",
            "Excel файлы (*.xlsx *.xls *.xlsm)"
        )
        if not path:
            return

        try:
            df = pd.read_excel(path, dtype=str)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка чтения файла", str(e))
            return

        col_num = None
        col_name = None
        col_area = None
        for col in df.columns:
            col_lower = str(col).lower().strip()
            if col_num is None and ("участк" in col_lower or col_lower in ("№", "n", "номер")):
                col_num = col
            if col_name is None and ("ф.и.о" in col_lower or "фио" in col_lower or "имя" in col_lower or col_lower == "ф.и.о."):
                col_name = col
            if col_area is None and ("площад" in col_lower or "кв.м" in col_lower or "м²" in col_lower or "м2" in col_lower):
                col_area = col

        if col_num is None or col_name is None:
            QMessageBox.warning(
                self, "Неверный формат",
                f"Не удалось найти нужные столбцы.\n"
                f"Ожидается: «№ участка» и «Ф.И.О.»\n"
                f"Найдены столбцы: {', '.join(str(c) for c in df.columns)}"
            )
            return

        imported: dict[str, dict] = {}
        for _, row in df.iterrows():
            num = str(row[col_num]).strip()
            name = str(row[col_name]).strip()
            if not num or num.lower() in ("nan", "none", "") or not name or name.lower() in ("nan", "none", ""):
                continue
            entry = imported.setdefault(num, {"owners": [], "area": None})
            existing_names = [_owner_name(o) for o in entry["owners"]]
            if name not in existing_names:
                entry["owners"].append(_make_owner(name))
            if col_area is not None and entry["area"] is None:
                raw = str(row[col_area]).strip().replace(",", ".")
                if raw and raw.lower() not in ("nan", "none"):
                    try:
                        v = float(raw)
                        if v > 0:
                            entry["area"] = v
                    except ValueError:
                        pass

        if not imported:
            QMessageBox.warning(self, "Пустой файл", "В файле не найдено данных об участках.")
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Импорт участков")
        msg.setText(
            f"Найдено {len(imported)} участков в файле.\n\n"
            "Как импортировать?"
        )
        btn_replace = msg.addButton("Заменить всё", QMessageBox.ButtonRole.DestructiveRole)
        btn_merge   = msg.addButton("Объединить",   QMessageBox.ButtonRole.AcceptRole)
        btn_cancel  = msg.addButton("Отмена",        QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_merge)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked is btn_cancel:
            return

        if clicked is btn_replace:
            new_plots = []
            for num, entry in imported.items():
                item = {"num": num, "owners": entry["owners"]}
                if entry["area"] is not None:
                    item["area"] = entry["area"]
                new_plots.append(item)
            self._plots = new_plots
        else:
            existing = {p["num"]: p for p in self._plots}
            for num, entry in imported.items():
                owners = entry["owners"]
                area = entry["area"]
                if num in existing:
                    current_owners = existing[num].get("owners", [])
                    current_names = [_owner_name(o) for o in current_owners]
                    for o in owners:
                        if _owner_name(o) not in current_names:
                            current_owners.append(o)
                    existing[num]["owners"] = current_owners
                    if area is not None and existing[num].get("area") in (None, "", 0):
                        existing[num]["area"] = area
                else:
                    item = {"num": num, "owners": owners}
                    if area is not None:
                        item["area"] = area
                    existing[num] = item
            self._plots = list(existing.values())

        self._save()
        self._rebuild_table()
        QMessageBox.information(
            self, "Импорт завершён",
            f"Импортировано {len(imported)} участков."
        )

    def _add_plot(self):
        """Быстрое добавление участка через модальное окно (номер/площадь/
        ФИО) вместо панели справа — см. _QuickAddPlotDialog."""
        existing_nums = {str(p.get("num", "")) for p in self._plots}
        people = people_reg.load_people()
        result = _QuickAddPlotDialog.ask(self, people, existing_nums)
        if result is None:
            return
        num, area, name = result
        person = people_reg.find_by_name(people, name)
        if person is None:
            person = people_reg.create_person(name, "", "")
            people.append(person)
            try:
                people_reg.save_people(people)
            except Exception:
                pass
        owner = _make_owner(name, is_owner=False, is_visible=True)
        owner["person_id"] = person["id"]
        plot = {
            "num": num,
            "groups": [{"since": date.today().isoformat(), "until": None,
                       "owners": [owner]}],
        }
        if area is not None:
            plot["area"] = area
        self._plots.append(plot)
        self._save()
        self._recompute_debt()
        self._rebuild_table()


# ============================================================================ #
#  Вспомогательный виджет чекбокса на иконках                                 #
# ============================================================================ #

class _IconCheckBox(QLabel):
    """Кликабельный чекбокс через иконки Material Symbols Rounded."""

    stateChanged = pyqtSignal(bool)

    _FONT   = "Material Symbols Rounded"
    _IC_ON  = ""   # check_box
    _IC_OFF = ""   # check_box_outline_blank
    _C_ON   = "#07414F"
    _C_OFF  = "#9CA3AF"

    def __init__(self, checked: bool = True, parent=None):
        super().__init__(parent)
        self._checked = checked
        f = QFont(self._FONT)
        f.setPixelSize(20)
        self.setFont(f)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(32, 32)
        self._refresh()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, val: bool):
        self._checked = bool(val)
        self._refresh()

    def _refresh(self):
        self.setText(self._IC_ON if self._checked else self._IC_OFF)
        c = self._C_ON if self._checked else self._C_OFF
        self.setStyleSheet(f"color:{c}; background:transparent;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._checked = not self._checked
            self._refresh()
            self.stateChanged.emit(self._checked)
        super().mousePressEvent(event)


# ============================================================================ #
#  Радиокнопка из иконок (эксклюзивный выбор)                                 #
# ============================================================================ #

class _IconRadioButton(QLabel):
    """Радиокнопка на иконках Material Symbols Rounded (radio_button_checked/unchecked).

    toggled() эмитируется при нажатии — диалог должен снять остальные кнопки.
    setLogicalEnabled(False) блокирует выбор и снимает отметку.
    """

    toggled = pyqtSignal()

    _FONT  = "Material Symbols Rounded"
    _IC_ON  = chr(0xE837)   # radio_button_checked
    _IC_OFF = chr(0xE836)   # radio_button_unchecked
    _C_ON   = "#07414F"
    _C_OFF  = "#9CA3AF"
    _C_DIS  = "#D1D5DB"

    def __init__(self, checked: bool = False, enabled: bool = True, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._enabled = enabled
        f = QFont(self._FONT)
        f.setPixelSize(20)
        self.setFont(f)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(32, 32)
        self._refresh()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, val: bool):
        self._checked = bool(val)
        self._refresh()

    def setLogicalEnabled(self, enabled: bool):
        self._enabled = bool(enabled)
        if not self._enabled:
            self._checked = False
        self._refresh()

    def setInteractive(self, interactive: bool):
        """Разрешает/запрещает взаимодействие без сброса состояния."""
        self._enabled = bool(interactive)
        self._refresh()

    def _refresh(self):
        self.setText(self._IC_ON if self._checked else self._IC_OFF)
        if not self._enabled:
            color = self._C_DIS
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            color = self._C_ON if self._checked else self._C_OFF
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"color:{color}; background:transparent;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._enabled and not self._checked:
            self._checked = True
            self._refresh()
            self.toggled.emit()
        super().mousePressEvent(event)


# ============================================================================ #
#  Виджет заявления о вступлении в члены СНТ                                  #
# ============================================================================ #

class _DocFieldWidget(QFrame):
    path_changed = pyqtSignal(str)

    _MAX_SIZE = 20 * 1024 * 1024
    _ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"}

    def __init__(self, doc_path: str = "", *,
                 upload_tip: str = "Загрузить документ",
                 required: bool = False,
                 parent=None):
        super().__init__(parent)
        self._path = doc_path
        self._required = required
        self._upload_tip = upload_tip
        self._interactive = True
        self.setObjectName("_docFW")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(8)

        _ic_font = QFont("Material Symbols Rounded")
        _ic_font.setPixelSize(16)
        self._icon = QLabel()
        self._icon.setFont(_ic_font)
        self._icon.setFixedSize(18, 18)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)

        txt_box = QVBoxLayout()
        txt_box.setContentsMargins(0, 0, 0, 0)
        txt_box.setSpacing(1)
        self._name_lbl = QLabel()
        self._name_lbl.setStyleSheet(
            "QLabel{background:transparent;border:none;padding:0;font-size:12px;font-weight:500;}")
        self._name_lbl.setTextFormat(Qt.TextFormat.PlainText)
        txt_box.addWidget(self._name_lbl)
        self._sub_lbl = QLabel()
        self._sub_lbl.setStyleSheet(
            "QLabel{background:transparent;border:none;padding:0;font-size:10px;}")
        txt_box.addWidget(self._sub_lbl)
        lay.addLayout(txt_box, 1)

        self._btn_box = QHBoxLayout()
        self._btn_box.setContentsMargins(0, 0, 0, 0)
        self._btn_box.setSpacing(4)

        self._del_btn = QPushButton()
        self._del_btn.setFixedSize(22, 22)
        self._del_btn.setIconSize(QSize(16, 16))
        self._del_btn.setFlat(True)
        self._del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del_btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;border-radius:4px;}"
            "QPushButton:hover{background:#FEF2F2;}")
        self._del_btn.setIcon(_mat_icon(0xE92B, 16, color="#DC2626"))
        self._del_btn.installEventFilter(_TooltipFilter("Удалить документ", self._del_btn))
        self._del_btn.clicked.connect(self.delete_path)
        self._del_btn.setVisible(False)
        self._btn_box.addWidget(self._del_btn)

        lay.addLayout(self._btn_box)

        self._refresh()

    def get_path(self) -> str:
        return self._path

    def setEnabled(self, enabled: bool):
        self._interactive = bool(enabled)
        self._refresh()

    def _display_name(self) -> str:
        name = os.path.basename(self._path)
        if len(name) > 24:
            name = name[:17] + "\u2026" + name[-7:]
        return name

    @staticmethod
    def _file_size_str(path: str) -> str:
        try:
            size = os.path.getsize(path)
        except OSError:
            return ""
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    def _refresh(self):
        has = bool(self._path)
        err = False

        if has:
            self.setStyleSheet(
                "QFrame#_docFW{background:#F0FDFA;border:1px solid #07414F;border-radius:6px;}"
                "QFrame#_docFW:hover{background:#E3F7F1;border:1px solid #07414F;}")
            self._icon.setText(chr(0xE86C))
            self._icon.setStyleSheet(
                "QLabel{background:transparent;border:none;padding:0;color:#2E9E5B;}")
            self._icon.setVisible(True)
            self._name_lbl.setText(self._upload_tip)
            self._name_lbl.setStyleSheet(
                "QLabel{background:transparent;border:none;padding:0;font-size:12px;font-weight:500;color:#07414F;}")
            self._name_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            name = self._display_name()
            sz = self._file_size_str(self._path)
            sub = f"{name} \u00b7 {sz}" if sz else name
            self._sub_lbl.setText(sub)
            self._sub_lbl.setStyleSheet(
                "QLabel{background:transparent;border:none;padding:0;font-size:10px;color:#07414F;}")
            self._sub_lbl.setVisible(True)
            self._del_btn.setVisible(True)
        elif self._required:
            self.setStyleSheet(
                "QFrame#_docFW{background:#FFFBEB;border:1.5px dashed #F59E0B;border-radius:6px;}"
                "QFrame#_docFW:hover{background:#FEF3C7;border:1.5px dashed #D97706;}")
            self._icon.setText(chr(0xE000))
            self._icon.setStyleSheet(
                "QLabel{background:transparent;border:none;padding:0;color:#D97706;}")
            self._icon.setVisible(True)
            self._name_lbl.setText(self._upload_tip)
            self._name_lbl.setStyleSheet(
                "QLabel{background:transparent;border:none;padding:0;font-size:12px;font-weight:500;color:#92400E;}")
            self._name_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            self._sub_lbl.setText("Обязательно \u2014 нажмите для загрузки")
            self._sub_lbl.setStyleSheet(
                "QLabel{background:transparent;border:none;padding:0;font-size:10px;color:#92400E;}")
            self._sub_lbl.setVisible(True)
            self._del_btn.setVisible(False)
        else:
            self.setStyleSheet(
                "QFrame#_docFW{background:#FFFFFF;border:1px solid #D1D5DB;border-radius:6px;}"
                "QFrame#_docFW:hover{background:#F9FAFB;border:1px solid #9CA3AF;}")
            self._icon.setText(chr(0xE9FC))
            self._icon.setStyleSheet(
                "QLabel{background:transparent;border:none;padding:0;color:#9CA3AF;}")
            self._icon.setVisible(True)
            self._name_lbl.setText(self._upload_tip)
            self._name_lbl.setStyleSheet(
                "QLabel{background:transparent;border:none;padding:0;font-size:12px;font-weight:500;color:#9CA3AF;}")
            self._name_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            self._sub_lbl.setText("Необязательно")
            self._sub_lbl.setStyleSheet(
                "QLabel{background:transparent;border:none;padding:0;font-size:10px;color:#9CA3AF;}")
            self._sub_lbl.setVisible(True)
            self._del_btn.setVisible(False)

        self.path_changed.emit(self._path)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._path:
                self._on_open()
            elif self._interactive:
                self._on_upload()
        super().mousePressEvent(event)

    def _on_upload(self):
        path, _ = QFileDialog.getOpenFileName(
            self, self._upload_tip, "",
            "Документы и изображения (*.pdf *.jpg *.jpeg *.png *.doc *.docx);;Все файлы (*.*)"
        )
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in self._ALLOWED_EXT:
            return
        try:
            if os.path.getsize(path) > self._MAX_SIZE:
                return
        except OSError:
            pass
        self._path = path
        self._refresh()

    def _on_open(self):
        if not self._path or not os.path.exists(self._path):
            QMessageBox.warning(self, "Ошибка", "Файл не найден")
            return
        try:
            os.startfile(self._path)
        except Exception:
            QMessageBox.warning(self, "Ошибка", "Не удалось открыть файл")

    def delete_path(self):
        self._path = ""
        self._refresh()

    def set_path(self, path: str) -> None:
        self._path = path
        self._refresh()

    def set_required(self, required: bool) -> None:
        self._required = required
        self._refresh()

    def set_error(self, msg: str) -> None:
        pass


# ============================================================================ #
#  GroupEditDialog — редактор состава одной группы                            #
# ============================================================================ #

class GroupEditDialog(QWidget):
    """Редактор состава группы — сворачиваемые карточки.

    Встраиваемая под-панель (Фаза 2): живёт внутри `PlotEditDialog._contacts_view`,
    раньше была модальным `QDialog`. О завершении сообщает сигналом `closed(saved)`
    (вместо accept/reject), результат отдаёт через `get_result()`."""

    closed = pyqtSignal(bool)  # saved? — True если состав группы сохранён

    def __init__(self, group: dict, is_new: bool = False, parent=None,
                 inline: bool = False):
        super().__init__(parent)
        self._group = group
        self._is_new = is_new
        self._inline = inline   # инлайн-аккордеон (без внутр. скролла и кнопок-закрытия)
        if inline:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._title = "Новая группа" if is_new else "Список контактов"
        self._cards: list[dict] = []
        self._primary_idx = 0
        self._btn_save = None
        self._btn_create: QPushButton | None = None  # «Создать» (только для новой группы)
        self._btn_switch_edit: QPushButton | None = None  # удалён, оставлен для совместимости
        self._footer_view: QWidget | None = None
        self._footer_edit: QWidget | None = None
        # Реестр людей: источник для автодополнения ФИО и переиспользования контактов.
        self._people: list = people_reg.load_people()
        self._new_people: list = []  # созданные в этой сессии (сохраняются в _on_accept)
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        # Инлайн-аккордеон: внешние поля даёт контейнер-хост, внутри без отступов.
        lay.setContentsMargins(0, 0, 0, 0) if self._inline else \
            lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(10)

        # -- Контейнер карточек --
        self._cards_container = QWidget()
        self._cards_container.setStyleSheet("background:transparent;")
        self._cards_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._cards_vlay = QVBoxLayout(self._cards_container)
        self._cards_vlay.setSpacing(6)
        self._cards_vlay.setContentsMargins(0, 0, 0 if self._inline else 12, 0)
        self._cards_vlay.addStretch()

        if self._inline:
            # Аккордеон: контейнер напрямую (без своего скролла) — растёт по
            # содержимому, вертикальное переполнение ловит внешний drawer_scroll
            # детали (без вложенных полос). Горизонтальное переполнение лечится
            # сжатием содержимого карточки (короткое ФИО + иконка статуса), а не
            # скроллом — минимальную ширину строки скролл не ужимает.
            self._scroll = None
            lay.addWidget(self._cards_container)
        else:
            # Скролл карточек (модалка/под-панель). Win11 overlay-фикс: Fusion на
            # области И на скроллбаре (стиль храним на self, иначе соберёт GC).
            self._scroll = QScrollArea()
            scroll = self._scroll
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
            self._sb_style = QStyleFactory.create("Fusion")
            _vsb = scroll.verticalScrollBar()
            if self._sb_style is not None:
                scroll.setStyle(self._sb_style)
                _vsb.setStyle(self._sb_style)
            _vsb.setFixedWidth(10)
            # Сплошной белый вместо «transparent» — иначе сквозь дорожку
            # просвечивает базовый фон QAbstractScrollArea (#F0F3F9 из
            # глобального QSS-фикса вьюпорта) вместо истинного белого фона
            # панели (см. подробности у _drawer_scroll в PlotsWidget).
            _vsb.setStyleSheet("""
                QScrollBar:vertical { background:#FFFFFF; width:10px; margin:0; }
                QScrollBar::handle:vertical {
                    background:#9CA3AF; border-radius:4px; min-height:30px; margin:1px;
                }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
                QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:#FFFFFF; }
            """)
            scroll.setWidget(self._cards_container)
            scroll.setMinimumHeight(280)
            lay.addWidget(scroll, stretch=1)

        # Загружаем существующих участников
        owners = ownership.group_owners(self._group)
        primary_idx = 0
        for i, o in enumerate(owners):
            if isinstance(o, dict) and o.get("is_visible"):
                primary_idx = i
                break
        for i, o in enumerate(owners):
            self._add_owner_card(o, is_primary=(i == primary_idx),
                                 expanded=(i == primary_idx))
        self._primary_idx = primary_idx

        # -- Нижняя строка: "+ Добавить контакт" слева, "Закрыть" справа --
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#E5E7EB;background:#E5E7EB;max-height:1px;")
        lay.addWidget(sep)

        self._footer_view = QWidget()
        fv_lay = QHBoxLayout(self._footer_view)
        fv_lay.setContentsMargins(0, 0, 0, 0)
        fv_lay.setSpacing(8)

        self._btn_add = QPushButton("＋  Добавить контакт")
        self._btn_add.setObjectName("btnSecondary")
        self._btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_add.clicked.connect(self._on_add_person)
        fv_lay.addWidget(self._btn_add, stretch=1)

        if self._inline:
            # Аккордеон: только «+ Добавить контакт». Свернуть/сохранить — повторным
            # кликом по заголовку «Список контактов» (хост зовёт back() → коммит).
            pass
        elif self._is_new:
            # Новая группа: «Отмена» откатывает (reject), «Создать» доступна
            # только когда есть хотя бы один контакт с ФИО — пустую не создать.
            btn_cancel = QPushButton("Отмена")
            btn_cancel.setObjectName("btnFooterCancel")
            btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_cancel.clicked.connect(lambda: self._finish(False))
            fv_lay.addWidget(btn_cancel)

            self._btn_create = QPushButton("Создать")
            self._btn_create.setObjectName("btnFooterSave")
            self._btn_create.setCursor(Qt.CursorShape.PointingHandCursor)
            self._btn_create.clicked.connect(self._on_accept)
            fv_lay.addWidget(self._btn_create)
        else:
            btn_close = QPushButton("Закрыть")
            btn_close.setObjectName("btnFooterClose")
            btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
            # «Закрыть» коммитит текущее состояние карточек в результат и accept(),
            # иначе сохранённые per-card правки не вернутся в PlotEditDialog.
            btn_close.clicked.connect(self._on_accept)
            fv_lay.addWidget(btn_close)

        lay.addWidget(self._footer_view)
        self._footer_edit = None  # убран; сохранение — per-card

        # Для новой группы — сразу в режим редактирования
        if self._is_new:
            for cd in self._cards:
                self._set_card_edit_mode(cd, True)
        self._update_edit_footer()

    def _update_edit_footer(self):
        """Обновляет состояние карточек."""
        if self._footer_view is None:
            return  # вызван до завершения _setup_ui
        self._update_save_state()

    def _set_card_edit_mode(self, cd: dict, mode: bool):
        cd["is_editing"] = mode
        if not mode:
            cd.pop("_is_new", None)
        if mode:
            cd["_snap"] = self._card_snapshot(cd)
        else:
            cd.pop("_snap", None)
        for key in ("name_inp", "phone", "email"):
            cd[key].setReadOnly(not mode)
        for role_key in ("rb_contact", "rb_owner", "rb_member"):
            cd[role_key].setInteractive(mode)
        for doc_key in ("opd_doc", "egrn_doc", "member_doc"):
            cd[doc_key].setEnabled(mode)
        cd["btn_del"].setVisible(mode)
        star = cd["star_btn"]
        star.setCursor(
            Qt.CursorShape.PointingHandCursor if mode and not cd["_is_primary"]
            else Qt.CursorShape.ArrowCursor)
        self._refresh_dirty_badges(cd)
        self._update_edit_footer()

    def _on_add_person(self):
        cd = self._add_owner_card({}, is_primary=not self._cards, expanded=True,
                                   start_editing=True)
        cd["_is_new"] = True
        # Collapse все остальные
        for other in self._cards:
            if other is not cd and not other["is_collapsed"]:
                self._apply_collapse(other, collapsed=True)
        # Прокрутка к новой карточке (в inline скролла нет — его роль у drawer_scroll)
        if self._scroll is not None:
            QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(cd["widget"]))

    def _add_owner_card(self, owner: dict, *, is_primary: bool = False,
                        expanded: bool = False, start_editing: bool = False):
        # Сохраняем исходный словарь, чтобы при сохранении не потерять поля,
        # которых нет в UI карточки (area, share, since, until).
        cd: dict = {"is_collapsed": not expanded, "is_editing": False,
                    "_src": owner if isinstance(owner, dict) else {}}

        card = QFrame()
        card.setObjectName("personCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        card_outer = QVBoxLayout(card)
        card_outer.setContentsMargins(0, 0, 0, 0)
        card_outer.setSpacing(0)
        cd["widget"] = card

        # ── Заголовок (всегда виден) ─────────────────────────────────────
        hdr_w = QWidget()
        hdr_w.setStyleSheet("background:transparent;")
        hdr_lyt = QHBoxLayout(hdr_w)
        hdr_lyt.setContentsMargins(10, 8, 10, 8)
        hdr_lyt.setSpacing(6)

        # Звёздочка — выбор главного (слева от ФИО)
        # FILL=0 → outline, FILL=1 → filled (Material Symbols variable font)
        star_btn = QPushButton(chr(0xE838))
        star_btn.setFont(_F_STAR_OUTLINE)
        star_btn.setFixedSize(26, 26)
        star_btn.setFlat(True)
        star_btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#C9D8E2;padding:0;}")
        star_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        star_btn.clicked.connect(lambda _, c=cd: self._set_primary(c))
        cd["star_btn"] = star_btn
        cd["_is_primary"] = False

        def _star_enter(e, btn=star_btn, c=cd):
            if not c["_is_primary"]:
                btn.setFont(_F_STAR_FILLED)
                btn.setStyleSheet(
                    "QPushButton{background:transparent;border:none;"
                    "color:#07414F;padding:0;}")
            QWidget.enterEvent(btn, e)

        def _star_leave(e, btn=star_btn, c=cd):
            if not c["_is_primary"]:
                btn.setFont(_F_STAR_OUTLINE)
                btn.setStyleSheet(
                    "QPushButton{background:transparent;border:none;"
                    "color:#C9D8E2;padding:0;}")
            QWidget.leaveEvent(btn, e)

        star_btn.enterEvent = _star_enter
        star_btn.leaveEvent = _star_leave
        hdr_lyt.addWidget(star_btn)

        # Метка роли (видна только у главного)
        hdr_lbl = QLabel()
        hdr_lbl.setStyleSheet("font-size:12px; font-weight:600; background:transparent;")
        cd["hdr_lbl"] = hdr_lbl
        hdr_lyt.addWidget(hdr_lbl)

        # (место для будущих кнопок — левый кластер убран в пользу авто-сохранения)

        # ── Краткое имя (только в свёрнутом состоянии) ───────────────────
        name_summary = QLabel()
        name_summary.setStyleSheet("font-size:12px; color:#374151; background:transparent;")
        name_summary.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        name_summary.setMinimumWidth(0)
        cd["name_summary"] = name_summary
        hdr_lyt.addWidget(name_summary)
        hdr_lyt.addStretch(1)

        # ── Кнопка-тег «отменить изменения» (видна только при несохранённых правках) ─
        btn_revert = QPushButton("отменить изменения")
        btn_revert.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        btn_revert.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_revert.setFixedHeight(22)
        btn_revert.setFlat(True)
        btn_revert.setStyleSheet(
            "QPushButton{background:#FEF3C7;color:#92400E;border:none;border-radius:11px;"
            "padding:0 10px;font-size:11px;}"
            "QPushButton:hover{background:#FDE68A;color:#78350F;}")
        btn_revert.clicked.connect(lambda _, c=cd: self._cancel_card_edit(c))
        btn_revert.hide()
        cd["btn_revert"] = btn_revert
        hdr_lyt.addWidget(btn_revert)

        # ── Теги статуса (только в свёрнутом состоянии) ──────────────────
        def _make_tag(text: str, bg: str, fg: str) -> QLabel:
            t = QLabel(text)
            t.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            t.setStyleSheet(
                f"font-size:10px; padding:2px 6px; border-radius:6px;"
                f"background:{bg}; color:{fg};")
            return t

        # Компактная иконка-предупреждение (текст «Отсутствуют документы» занимал
        # слишком много ширины в узкой карточке) — смысл в тултипе.
        tag_docs = QLabel(chr(0xF0DC))   # assignment_late — «нет документа» (уже в проекте)
        tag_docs.setFont(_mat_font(16))
        tag_docs.setStyleSheet("color:#F59E0B; background:transparent;")
        tag_docs.setToolTip("Отсутствуют документы")
        tag_docs.hide()
        cd["tag_docs"] = tag_docs
        hdr_lyt.addWidget(tag_docs)

        tags_w = QWidget()
        tags_w.setStyleSheet("background:transparent;")
        tags_w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        tags_lyt = QHBoxLayout(tags_w)
        tags_lyt.setContentsMargins(0, 0, 0, 0)
        tags_lyt.setSpacing(4)
        tag_con = _make_tag("Контакт",    "#E5E7EB", "#6B7280")
        tag_own = _make_tag("Собственник","#C9D8E2", "#07414F")
        tag_mem = _make_tag("Член СНТ",  "#D6EBD5", "#2E7D32")
        tags_lyt.addWidget(tag_con)
        tags_lyt.addWidget(tag_own)
        tags_lyt.addWidget(tag_mem)
        cd["tag_con"] = tag_con
        cd["tag_own"] = tag_own
        cd["tag_mem"] = tag_mem
        cd["tags_w"] = tags_w
        hdr_lyt.addWidget(tags_w)

        # ── Правый кластер: "Удалить" (только в режиме редактирования) ───
        btn_del = QPushButton("Удалить")
        btn_del.setObjectName("btnDelCard")
        btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_del.setFixedHeight(26)
        btn_del.clicked.connect(lambda _, c=cd: self._remove_card(c))
        cd["btn_del"] = btn_del
        hdr_lyt.addWidget(btn_del)

        chevron = QPushButton()
        chevron.setObjectName("btnChevron")
        chevron.setFont(_F_CHEVRON)
        chevron.setCursor(Qt.CursorShape.PointingHandCursor)
        chevron.setFixedSize(22, 22)
        chevron.clicked.connect(lambda _, c=cd: self._toggle_card(c))
        cd["chevron"] = chevron
        hdr_lyt.addWidget(chevron)

        # Клик по заголовку (не по кнопкам) сворачивает/раскрывает
        hdr_w.mousePressEvent = lambda ev, c=cd: self._toggle_card(c)
        card_outer.addWidget(hdr_w)

        # ── Содержимое (сворачивается) ────────────────────────────────────
        content = QWidget()
        content.setStyleSheet("background:transparent;")
        content_lyt = QVBoxLayout(content)
        content_lyt.setContentsMargins(14, 2, 14, 14)
        content_lyt.setSpacing(8)
        cd["content"] = content

        # Разделитель между заголовком и содержимым
        inner_sep = QFrame()
        inner_sep.setFrameShape(QFrame.Shape.HLine)
        inner_sep.setStyleSheet("color:#E5E7EB; background:#E5E7EB; max-height:1px;")
        content_lyt.addWidget(inner_sep)

        # Строка 1: ФИО
        raw_name = owner.get("name", "") if isinstance(owner, dict) else str(owner or "")
        name_col = QVBoxLayout()
        name_col.setSpacing(3)
        lbl_fio, _fio_lbl_row, _fio_dirty = _make_anchor_label(
            "ФИО", "font-size:12px; color:#6B7280; background:transparent;")
        _fio_row = QHBoxLayout()
        _fio_row.setContentsMargins(0, 0, 0, 0)
        _fio_row.setSpacing(6)
        _fio_row.addWidget(_fio_lbl_row)
        dup_pill = _make_warn_pill(card, "занято — не будет сохранено")
        dup_pill.hide()
        # Резервируем место скрытой пилюли — иначе её появление меняет высоту
        # строки и «дёргает» вниз то, что расположено ниже (см. тот же приём
        # выше, в инлайн-карточке _refresh_contacts_preview).
        _dup_sp = dup_pill.sizePolicy()
        _dup_sp.setRetainSizeWhenHidden(True)
        dup_pill.setSizePolicy(_dup_sp)
        cd["dup_pill"] = dup_pill
        _fio_row.addWidget(dup_pill)
        _fio_row.addStretch(1)
        name_col.addLayout(_fio_row)
        cd["name_dirty"] = _fio_dirty
        name_row_h = QHBoxLayout()
        name_row_h.setContentsMargins(0, 0, 0, 0)
        name_row_h.setSpacing(4)
        name_inp = QLineEdit(raw_name)
        name_inp.setPlaceholderText("Фамилия Имя Отчество")
        name_inp.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        name_inp.textChanged.connect(self._update_save_state)
        name_inp.textChanged.connect(lambda _, c=cd: self._update_name_summary(c))
        # Ссылка на человека из реестра + автодополнение ФИО.
        cd["person_id"] = owner.get("person_id") if isinstance(owner, dict) else None
        # Защита от дублей: ФИО остальных карточек этой группы не предлагаются
        # в автодополнении — иначе можно выбрать уже добавленного человека снова.
        _other_names_norm0 = {
            people_reg.norm_name(c["name_inp"].text())
            for c in self._cards if c["name_inp"].text().strip()
        }
        _avail_names = [
            n for n in _people_names(self._people)
            if people_reg.norm_name(n) not in _other_names_norm0
        ]
        completer = QCompleter(_avail_names, name_inp)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        name_inp.setCompleter(completer)
        completer.activated.connect(
            lambda _t, c=cd: QTimer.singleShot(0, lambda: self._on_name_committed(c)))
        name_inp.editingFinished.connect(lambda c=cd: self._on_name_committed(c))
        name_row_h.addWidget(name_inp, stretch=1)
        name_row_h.addWidget(_make_copy_btn(name_inp))
        name_col.addLayout(name_row_h)

        def _check_dup_name(_t="", c=cd, inp=name_inp, pill=dup_pill):
            norm = people_reg.norm_name(inp.text())
            others = {
                people_reg.norm_name(oc["name_inp"].text())
                for oc in self._cards
                if oc is not c and oc["name_inp"].text().strip()
            }
            pill.setVisible(bool(inp.text().strip()) and norm in others)

        name_inp.textChanged.connect(_check_dup_name)
        cd["name_inp"] = name_inp
        content_lyt.addLayout(name_col)

        # Строка 2: Телефон / E-mail
        contact_row = QHBoxLayout()
        contact_row.setSpacing(8)
        phone_raw = owner.get("phone", "") if isinstance(owner, dict) else ""
        phone_val = _normalize_phone(phone_raw)  # приводим к формату маски
        email_val = owner.get("email", "") if isinstance(owner, dict) else ""
        for label_txt, placeholder, val, key in [
            ("Телефон", "+7 (xxx) xxx-xx-xx", phone_val, "phone"),
            ("E-mail", "email@example.com", email_val, "email"),
        ]:
            col = QVBoxLayout()
            col.setSpacing(3)
            lbl_contact, _contact_lbl_row, _contact_dirty = _make_anchor_label(
                label_txt, "font-size:12px; color:#6B7280; background:transparent;")
            col.addWidget(_contact_lbl_row)
            cd[f"{key}_dirty"] = _contact_dirty
            inp_row = QHBoxLayout()
            inp_row.setSpacing(4)
            inp_row.setContentsMargins(0, 0, 0, 0)
            inp = QLineEdit(val)
            inp.setPlaceholderText(placeholder)
            inp.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            inp_row.addWidget(inp)
            inp_row.addWidget(_make_copy_btn(inp))
            col.addLayout(inp_row)
            contact_row.addLayout(col, stretch=1)
            cd[key] = inp
        _setup_phone_input(cd["phone"])
        cd["phone"].textChanged.connect(self._update_save_state)
        cd["email"].textChanged.connect(self._update_save_state)
        content_lyt.addLayout(contact_row)

        # Строка 3: Роль (взаимоисключающие radio)
        if isinstance(owner, dict):
            if owner.get("is_visible", False):   init_role = "contact"
            elif owner.get("is_owner", False):   init_role = "owner"
            elif owner.get("is_member", False):  init_role = "member"
            else:                                init_role = "contact"
        else:
            init_role = "contact"

        role_row = QHBoxLayout()
        role_row.setSpacing(16)
        role_row.setContentsMargins(0, 0, 0, 0)
        for role_key, role_lbl in [
            ("contact", "Контакт"),
            ("owner",   "Собственник"),
            ("member",  "Член СНТ"),
        ]:
            rb = _IconRadioButton(checked=(init_role == role_key))
            rb.toggled.connect(lambda rk=role_key, c=cd: self._set_role(c, rk))
            cd[f"rb_{role_key}"] = rb
            item_lyt = QHBoxLayout()
            item_lyt.setSpacing(4)
            item_lyt.setContentsMargins(0, 0, 0, 0)
            item_lyt.addWidget(rb)
            lbl_r = QLabel(role_lbl)
            lbl_r.setStyleSheet("font-size:12px; color:#374151; background:transparent;")
            item_lyt.addWidget(lbl_r)
            role_row.addLayout(item_lyt)
        role_row.addStretch()
        content_lyt.addLayout(role_row)

        # Строка 4: Документы
        docs_sep = QFrame()
        docs_sep.setFrameShape(QFrame.Shape.HLine)
        docs_sep.setStyleSheet("color:#E5E7EB; background:#E5E7EB; max-height:1px;")
        content_lyt.addWidget(docs_sep)

        docs_hdr = QLabel("Документы")
        docs_hdr.setStyleSheet(
            "font-size:11px; font-weight:600; color:#9CA3AF; background:transparent;")
        content_lyt.addWidget(docs_hdr)

        opd_path    = owner.get("opd_doc", "")    if isinstance(owner, dict) else ""
        egrn_path   = owner.get("egrn_doc", "")   if isinstance(owner, dict) else ""
        member_path = owner.get("member_doc", "")  if isinstance(owner, dict) else ""

        opd_w  = _DocFieldWidget(opd_path, upload_tip="Согласие на ОПД")
        egrn_w = _DocFieldWidget(egrn_path, upload_tip="Выписка ЕГРН")
        mem_w  = _DocFieldWidget(member_path, upload_tip="Заявление в СНТ")
        cd["opd_doc"]    = opd_w
        cd["egrn_doc"]   = egrn_w
        cd["member_doc"] = mem_w

        mem_w.path_changed.connect(lambda path, c=cd: self._update_tags(c))
        for _dw in (opd_w, egrn_w, mem_w):
            _dw.path_changed.connect(lambda _, c=cd: self._update_save_state())
            _dw.path_changed.connect(lambda _, c=cd: self._update_doc_tag(c))
        self._update_doc_badges(cd)

        # QGridLayout гарантирует одинаковую ширину столбцов для всех трёх полей
        docs_grid = QGridLayout()
        docs_grid.setHorizontalSpacing(8)
        docs_grid.setVerticalSpacing(8)
        docs_grid.setContentsMargins(0, 0, 0, 0)
        docs_grid.setColumnStretch(0, 1)
        docs_grid.setColumnStretch(1, 1)

        def _doc_row(doc_w):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            row.addWidget(doc_w)
            return row

        _lbl_ss = "font-size:12px; color:#6B7280; background:transparent;"
        for col_idx, (lbl_txt, doc_w, del_key) in enumerate([
            ("Согласие на ОПД", opd_w, "opd_del"),
            ("Выписка ЕГРН",    egrn_w, "egrn_del"),
        ]):
            _, _lbl_d_row, _doc_dirty = _make_anchor_label(lbl_txt, _lbl_ss)
            docs_grid.addWidget(_lbl_d_row,      0, col_idx)
            docs_grid.addLayout(_doc_row(doc_w), 1, col_idx)
            cd[del_key.replace("_del", "_dirty")] = _doc_dirty

        _, _lbl_mem_row, _mem_dirty = _make_anchor_label("Заявление в СНТ", _lbl_ss)
        docs_grid.addWidget(_lbl_mem_row,    2, 0)
        docs_grid.addLayout(_doc_row(mem_w), 3, 0)
        cd["member_dirty"] = _mem_dirty

        content_lyt.addLayout(docs_grid)

        card_outer.addWidget(content)

        # Вставляем до stretch
        insert_idx = self._cards_vlay.count() - 1
        self._cards_vlay.insertWidget(insert_idx, card)
        self._cards.append(cd)

        self._update_name_summary(cd)
        self._update_tags(cd)
        self._apply_collapse(cd, collapsed=cd["is_collapsed"])
        self._refresh_card_headers()
        self._update_save_state()
        return cd

    # ── Collapse / expand ─────────────────────────────────────────────

    def _toggle_card(self, cd: dict):
        expanding = cd["is_collapsed"]
        self._apply_collapse(cd, collapsed=not cd["is_collapsed"])
        if expanding:
            for other in self._cards:
                if other is not cd and not other["is_collapsed"]:
                    self._apply_collapse(other, collapsed=True)

    def _apply_collapse(self, cd: dict, *, collapsed: bool):
        cd["is_collapsed"] = collapsed
        cd["content"].setVisible(not collapsed)
        cd["name_summary"].setVisible(collapsed)
        cd["tags_w"].setVisible(collapsed)
        if collapsed:
            self._update_doc_tag(cd)
        else:
            cd["tag_docs"].setVisible(False)
        cd["chevron"].setText(chr(0xE313) if collapsed else chr(0xE316))
        self._set_card_edit_mode(cd, not collapsed)

    # ── Обновление Summary / тегов ────────────────────────────────────

    def _update_name_summary(self, cd: dict):
        full = cd["name_inp"].text().strip()
        cd["name_summary"].setText(_short_name(full) if full else "(без имени)")

    def _on_name_committed(self, cd: dict):
        """ФИО введено/выбрано → привязать человека из реестра и подставить контакты.

        Если ФИО совпадает с ДРУГОЙ записью реестра — переключаем привязку и
        заполняем пустые телефон/email из неё (переиспользование), сначала
        стерев то, что было автоподставлено от ПРЕЖНЕГО человека (см.
        _on_inline_name_committed — тот же приём).

        Если совпадений нет вообще — считаем, что это ТОТ ЖЕ человек, которому
        просто исправляют ФИО: person_id не трогаем, переименование запишется
        в реестр при сохранении (_on_accept). Раньше здесь привязка сбрасывалась
        при любом несовпадении, из-за чего правка ФИО создавала в реестре дубль
        вместо переименования. Отвязываем только если поле ФИО очищено совсем."""
        name = cd["name_inp"].text().strip()
        if not name:
            cd["person_id"] = None
            self._update_save_state()
            return
        new_person = people_reg.find_by_name(self._people, name)
        old_person_id = cd.get("person_id")
        new_person_id = new_person.get("id") if new_person else None
        if new_person_id and new_person_id != old_person_id:
            old_person = people_reg.get(self._people, old_person_id)
            if old_person:
                if (cd["phone"].text().strip() and old_person.get("phone")
                        and _normalize_phone(old_person["phone"]) == cd["phone"].text().strip()):
                    cd["phone"].clear()
                if (cd["email"].text().strip() and old_person.get("email")
                        and old_person["email"] == cd["email"].text().strip()):
                    cd["email"].clear()
            cd["person_id"] = new_person_id
            if not cd["phone"].text().strip() and new_person.get("phone"):
                cd["phone"].setText(_normalize_phone(new_person["phone"]))
            if not cd["email"].text().strip() and new_person.get("email"):
                cd["email"].setText(new_person.get("email", ""))
        self._update_save_state()

    def _update_tags(self, cd: dict):
        cd["tag_con"].setVisible(cd["rb_contact"].isChecked())
        cd["tag_own"].setVisible(cd["rb_owner"].isChecked())
        cd["tag_mem"].setVisible(cd["rb_member"].isChecked())

    def _update_doc_tag(self, cd: dict) -> None:
        """Показывает бейдж «Отсутствуют документы» только в свёрнутой карточке,
        если хотя бы один обязательный для роли документ не загружен."""
        if not cd.get("is_collapsed", True):
            return
        role = ("member" if cd["rb_member"].isChecked()
                else "owner" if cd["rb_owner"].isChecked()
                else "contact")
        req = self._DOC_REQUIRED[role]
        missing = (
            (req["opd"]    and not cd["opd_doc"].get_path())
            or (req["egrn"]   and not cd["egrn_doc"].get_path())
            or (req["member"] and not cd["member_doc"].get_path())
        )
        cd["tag_docs"].setVisible(missing)

    # required=True → «Отсутствует», required=False → «Не требуется»
    _DOC_REQUIRED = {
        "contact": {"opd": True,  "egrn": False, "member": False},
        "owner":   {"opd": True,  "egrn": True,  "member": False},
        "member":  {"opd": True,  "egrn": True,  "member": True},
    }

    def _update_doc_badges(self, cd: dict) -> None:
        role = ("member" if cd["rb_member"].isChecked()
                else "owner" if cd["rb_owner"].isChecked()
                else "contact")
        req = self._DOC_REQUIRED[role]
        cd["opd_doc"].set_required(req["opd"])
        cd["egrn_doc"].set_required(req["egrn"])
        cd["member_doc"].set_required(req["member"])

    def _refresh_dirty_badges(self, cd: dict) -> None:
        snap = cd.get("_snap")
        editing = cd.get("is_editing", False)
        if not editing or snap is None:
            for key in ("name_dirty", "phone_dirty", "email_dirty",
                        "opd_dirty", "egrn_dirty", "member_dirty"):
                b = cd.get(key)
                if b:
                    b.setVisible(False)
            b = cd.get("btn_revert")
            if b:
                b.setVisible(False)
            return
        cur = self._card_snapshot(cd)
        any_dirty = False
        for snap_key, badge_key in [
            ("name",   "name_dirty"),
            ("phone",  "phone_dirty"),
            ("email",  "email_dirty"),
            ("opd",    "opd_dirty"),
            ("egrn",   "egrn_dirty"),
            ("member", "member_dirty"),
        ]:
            b = cd.get(badge_key)
            changed = cur[snap_key] != snap[snap_key]
            if b:
                b.setVisible(changed)
            if changed:
                any_dirty = True
        b = cd.get("btn_revert")
        if b:
            b.setVisible(any_dirty)

    def _cancel_card_edit(self, cd: dict) -> None:
        snap = cd.get("_snap")
        if snap is None:
            return
        widgets = [cd["name_inp"], cd["phone"], cd["email"],
                   cd["rb_contact"], cd["rb_owner"], cd["rb_member"]]
        for w in widgets:
            w.blockSignals(True)

        cd["name_inp"].setText(snap["name"])
        cd["phone"].setText(snap["phone"])
        cd["email"].setText(snap["email"])
        cd["rb_contact"].setChecked(snap["role"] == "contact")
        cd["rb_owner"].setChecked(snap["role"] == "owner")
        cd["rb_member"].setChecked(snap["role"] == "member")

        for w in widgets:
            w.blockSignals(False)

        cd["opd_doc"].set_path(snap["opd"])
        cd["egrn_doc"].set_path(snap["egrn"])
        cd["member_doc"].set_path(snap["member"])

        self._update_name_summary(cd)
        self._update_tags(cd)
        self._update_doc_badges(cd)
        self._refresh_dirty_badges(cd)
        self._update_save_state()

    def _set_role(self, cd: dict, role: str):
        for key in ("rb_contact", "rb_owner", "rb_member"):
            if key != f"rb_{role}":
                cd[key].setChecked(False)
        self._update_tags(cd)
        self._update_doc_badges(cd)
        self._update_doc_tag(cd)
        self._update_save_state()

    # ── Сделать главным / удалить ─────────────────────────────────────

    def _set_primary(self, card_data: dict):
        if card_data not in self._cards:
            return
        idx = self._cards.index(card_data)
        if idx != 0:
            self._cards.pop(idx)
            self._cards.insert(0, card_data)
            self._cards_vlay.insertWidget(0, card_data["widget"])
        self._primary_idx = 0
        self._refresh_card_headers()

    def _remove_card(self, card_data: dict):
        idx = self._cards.index(card_data)
        card_data["widget"].hide()
        card_data["widget"].setParent(None)
        card_data["widget"].deleteLater()
        self._cards.pop(idx)
        if self._cards and self._primary_idx >= len(self._cards):
            self._primary_idx = 0
        self._refresh_card_headers()
        self._update_edit_footer()
        QTimer.singleShot(0, self.adjustSize)

    def _refresh_card_headers(self):
        for i, cd in enumerate(self._cards):
            is_primary = (i == self._primary_idx)
            cd["_is_primary"] = is_primary
            star = cd["star_btn"]
            if is_primary:
                star.setFont(_F_STAR_FILLED)
                star.setStyleSheet(
                    "QPushButton{background:transparent;border:none;"
                    "color:#07414F;padding:0;}")
                star.setCursor(Qt.CursorShape.ArrowCursor)
                cd["hdr_lbl"].setVisible(False)
                cd["widget"].setStyleSheet(
                    "QFrame#personCard{"
                    "background:#EBF4F6;border:1.5px solid #C9D8E2;border-radius:10px;}"
                    "QFrame#personCard:hover{"
                    "background:#E1EEF2;border:1.5px solid #9FBCCB;}")
            else:
                star.setFont(_F_STAR_OUTLINE)
                star.setStyleSheet(
                    "QPushButton{background:transparent;border:none;"
                    "color:#C9D8E2;padding:0;}")
                star.setCursor(Qt.CursorShape.PointingHandCursor)
                cd["hdr_lbl"].setVisible(False)
                cd["widget"].setStyleSheet(
                    "QFrame#personCard{"
                    "background:#F9FAFB;border:1px solid #E5E7EB;border-radius:10px;}"
                    "QFrame#personCard:hover{"
                    "background:#F3F4F6;border:1px solid #C9D0D8;}")

    # ── Сохранение ────────────────────────────────────────────────────

    @staticmethod
    def _card_snapshot(cd: dict) -> dict:
        return {
            "name":   cd["name_inp"].text(),
            "phone":  cd["phone"].text(),
            "email":  cd["email"].text(),
            "role":   ("member" if cd["rb_member"].isChecked()
                       else "owner" if cd["rb_owner"].isChecked()
                       else "contact"),
            "opd":    cd["opd_doc"]._path,
            "egrn":   cd["egrn_doc"]._path,
            "member": cd["member_doc"]._path,
        }

    @staticmethod
    def _is_card_dirty(cd: dict) -> bool:
        snap = cd.get("_snap")
        if snap is None:
            return True  # новая карточка — сохранять можно сразу
        return GroupEditDialog._card_snapshot(cd) != snap

    def _update_save_state(self):
        for cd in self._cards:
            self._refresh_dirty_badges(cd)
        if self._btn_create is not None:
            any_named = any(cd["name_inp"].text().strip() for cd in self._cards)
            self._btn_create.setEnabled(any_named)
            self._btn_create.setCursor(
                Qt.CursorShape.PointingHandCursor if any_named
                else Qt.CursorShape.ArrowCursor)

    def _on_accept(self):
        owners = []
        registry_changed = False
        _seen_names_norm: set[str] = set()
        for i, cd in enumerate(self._cards):
            name = cd["name_inp"].text().strip()
            if not name:
                continue
            # Защита от дублей: если ФИО совпадает с уже добавленным в этой
            # группе контактом — пропускаем (не будет сохранено дважды).
            _norm = people_reg.norm_name(name)
            if _norm in _seen_names_norm:
                continue
            _seen_names_norm.add(_norm)
            phone = cd["phone"].text().strip()
            email = cd["email"].text().strip()
            opd_doc = cd["opd_doc"].get_path()
            member_doc = cd["member_doc"].get_path()
            # Значения ДО этой правки — чтобы отличить «поле было и его явно
            # очистили» от «поле изначально пустое», см. сброс кэша реестра ниже.
            _src0 = cd.get("_src") or {}
            prev_phone = str(_src0.get("phone", ""))
            prev_email = str(_src0.get("email", ""))
            prev_opd = str(_src0.get("opd_doc", ""))
            prev_member = str(_src0.get("member_doc", ""))
            # Привязка к человеку: по person_id карточки, иначе по ФИО, иначе —
            # создаём нового. _on_accept — гарантия дедупа (даже без авто-привязки).
            person = people_reg.get(self._people, cd.get("person_id"))
            if person is None:
                person = people_reg.find_by_name(self._people, name)
            if person is None:
                person = people_reg.create_person(
                    name, phone, email, opd_doc=opd_doc, member_doc=member_doc)
                self._people.append(person)
                registry_changed = True
            else:
                # Человек уже был в реестре — актуализируем кэш ФИО/телефона/
                # email и ОПД/заявления в СНТ, иначе автодополнение по ФИО
                # подтягивает устаревшие (или пустые) данные для других
                # контактов того же человека (см. _save_inline_contact — тот
                # же приём; ФИО — так же переименование пронесётся всюду).
                if name and person.get("name") != name:
                    person["name"] = name
                    registry_changed = True
                if phone and person.get("phone") != phone:
                    person["phone"] = phone
                    registry_changed = True
                elif not phone and prev_phone and person.get("phone") == prev_phone:
                    person.pop("phone", None)
                    registry_changed = True
                if email and person.get("email") != email:
                    person["email"] = email
                    registry_changed = True
                elif not email and prev_email and person.get("email") == prev_email:
                    person.pop("email", None)
                    registry_changed = True
                if opd_doc and person.get("opd_doc") != opd_doc:
                    person["opd_doc"] = opd_doc
                    registry_changed = True
                elif not opd_doc and prev_opd and person.get("opd_doc") == prev_opd:
                    person.pop("opd_doc", None)
                    registry_changed = True
                if member_doc and person.get("member_doc") != member_doc:
                    person["member_doc"] = member_doc
                    registry_changed = True
                elif not member_doc and prev_member and person.get("member_doc") == prev_member:
                    person.pop("member_doc", None)
                    registry_changed = True
            o = _make_owner(
                name,
                cd["rb_owner"].isChecked(),
                None,
                cd["rb_contact"].isChecked(),
                member_doc,
                opd_doc,
                phone,
                email,
                egrn_doc=cd["egrn_doc"].get_path(),
                is_member=cd["rb_member"].isChecked(),
                person_id=person["id"],
            )
            # Доносим поля, которых нет в UI карточки, из исходных данных.
            src = cd.get("_src") or {}
            for k in ("area", "share", "since", "until"):
                if k in src and k not in o:
                    o[k] = src[k]
            owners.append(o)

        if registry_changed:
            try:
                people_reg.save_people(self._people)
            except Exception:
                pass

        # Один контакт — он автоматически избранный; если избранного нет
        # вообще — им становится первый в списке (см. _ensure_single_primary).
        _ensure_single_primary(owners)
        result = dict(self._group)
        result["owners"] = owners
        self._result = result
        self._finish(True)

    def _finish(self, saved: bool):
        """Завершает под-панель: сообщает хосту через closed(saved)."""
        self.closed.emit(saved)

    def back(self):
        """Кнопка «Назад» в шапке: коммит для правки, отмена для новой группы."""
        if self._is_new:
            self._finish(False)
        else:
            self._on_accept()

    def get_result(self) -> dict:
        return getattr(self, "_result", dict(self._group))

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog { background: #FFFFFF; color: #374151; }
            QLabel  { background: transparent; color: #374151; font-size: 13px; }
            QCheckBox { background: transparent; font-size: 13px; color: #374151; }
            QScrollArea { background: transparent; border: none; }
            QScrollArea > QWidget > QWidget { background: transparent; }
            QLineEdit {
                background: #F8F9FA; border: 1px solid #D1D5DB;
                border-radius: 5px; color: #374151; padding: 6px 10px; font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid #07414F; }
            QDateEdit {
                background: #F8F9FA; border: 1px solid #D1D5DB;
                border-radius: 5px; color: #374151; padding: 6px 8px; font-size: 13px;
            }
            QPushButton#btnSecondary {
                background: #E5E7EB; color: #6B7280; border: 1px solid #D1D5DB;
                border-radius: 6px; padding: 8px 14px; font-size: 13px;
            }
            QPushButton#btnSecondary:hover { background: #D1D5DB; color: #374151; }
            QPushButton#btnDelCard {
                background: transparent; color: #DC2626;
                border: 1px solid #FCA5A5; border-radius: 6px;
                padding: 3px 10px; font-size: 12px;
            }
            QPushButton#btnDelCard:hover { background: #FEE2E2; border-color: #DC2626; }
            QPushButton#btnCardCancel {
                background: transparent; color: #6B7280;
                border: 1px solid #D1D5DB; border-radius: 6px;
                padding: 3px 10px; font-size: 12px;
            }
            QPushButton#btnCardCancel:hover { background: #F3F4F6; border-color: #9CA3AF; }
            QPushButton#btnCardSave {
                background: #07414F; color: white; border: none;
                border-radius: 6px; padding: 3px 10px; font-size: 12px;
            }
            QPushButton#btnCardSave:hover { background: #0B5A6E; }
            QPushButton#btnCardSave:disabled { background: #E5E7EB; color: #9CA3AF; }
            QPushButton#btnChevron {
                background: transparent; color: #9CA3AF; border: none;
                border-radius: 4px;
            }
            QPushButton#btnChevron:hover { color: #374151; }
            QDialogButtonBox QPushButton {
                background: #07414F; color: white; border: none;
                border-radius: 6px; padding: 8px 20px; font-size: 13px; font-weight: 600;
            }
            QDialogButtonBox QPushButton:hover { background: #0B5A6E; }
            QDialogButtonBox QPushButton:disabled { background: #E5E7EB; color: #9CA3AF; }
            QDialogButtonBox QPushButton[text='Отмена'] {
                background: #E5E7EB; color: #6B7280;
            }
            QDialogButtonBox QPushButton[text='Отмена']:hover {
                background: #D1D5DB; color: #374151;
            }
        """)


class _GroupCardCtx:
    """Состояние + виджет-ссылки одного карточного блока группы (активной или
    архивной) внутри PlotEditDialog. Позволяет переиспользовать один и тот же
    билдер/набор обработчиков инлайн-аккордеона контактов для обеих."""

    def __init__(self, group: dict, *, is_active: bool, orig: dict | None = None):
        self.group = group          # рабочая копия (мутируется инлайн-правками)
        self.is_active = is_active
        self.orig = orig            # исходный dict в self._groups (архивные — для подстановки при сохранении)
        self.contacts_expanded = False
        self.expanded_contacts: set[int] = set()
        self.contact_input_refs: dict = {}   # idx → (inp_name, inp_phone, inp_email, opd_w, egrn_w, mem_w)
        self.contact_snapshots: dict = {}    # idx → {name,phone,email,opd_doc,egrn_doc,member_doc}
        # виджеты — назначаются в PlotEditDialog._build_group_card
        self.card = None
        self.name_lbl = None
        self.since_lbl = None
        self.counts_lbl = None
        self.docs_badge_box = None
        self.docs_badge_lay = None
        self.debt_rows_box = None
        self.debt_rows_lay = None
        self.preview_box = None
        self.preview_lay = None
        self.show_all_row = None
        self.show_all_lbl = None
        self.show_all_arrow = None
        self.new_group_btn = None   # только у активной группы, см. _build_group_card
        # Дата начала активной группы — всегда редактируемый date-пикер
        # (см. _build_group_card, _on_since_changed). У архивных групп вместо
        # него используется since_lbl (период, только для чтения).
        self.since_date_edit = None


# ============================================================================ #
#  PlotEditDialog                                                              #
# ============================================================================ #

class PlotEditDialog(QWidget):
    """Панель добавления / редактирования участка (модель групп).

    Встраивается в правый drawer `PlotsWidget` (вариант А, единое окно). Раньше
    была модальным `QDialog`; теперь сообщает о завершении сигналом `closed(saved)`
    вместо accept/reject, а результат отдаётся через `get_result()`."""

    _IC_FONT = "Material Symbols Rounded"

    closed = pyqtSignal(bool)   # saved? — True если есть сохранённые изменения
    deleted = pyqtSignal()      # запрос на удаление участка (только режим правки)
    personDeleted = pyqtSignal(str)  # person_id — человек удалён полностью;
        # хост (PlotsWidget) должен убрать его со ВСЕХ остальных участков
        # (этот диалог видит только свой участок).

    def __init__(self, plot_data: dict | None = None, parent=None, df=None,
                 existing_nums: set | None = None):
        super().__init__(parent)
        self._is_edit = plot_data is not None
        self._plot_data = dict(plot_data or {})
        self._df = df
        self._existing_nums: set = existing_nums or set()

        # Инициализируем рабочую копию списка групп
        self._groups: list = list(ownership.plot_groups(self._plot_data))
        if not any(g.get("until") is None for g in self._groups):
            self._groups.append({"since": None, "until": None, "owners": []})

        # Рабочая копия активной группы (редактируется через GroupEditDialog)
        act_idx = next(i for i, g in enumerate(self._groups)
                       if g.get("until") is None)
        self._active_group = dict(self._groups[act_idx])
        self._active_group["owners"] = list(
            self._groups[act_idx].get("owners", []) or [])

        self._title = "Информация об участке" if self._is_edit else "Новый участок"
        self._btn_save: QPushButton | None = None
        self._save_btn: QPushButton | None = None   # галочка-сохранить в шапке (edit)
        self._header_editing = False
        self._dirty_fields: set = set()     # поля (num/area) с принятыми изменениями
        self._group_dirty: bool = False     # группа изменена через инлайн-аккордеон контактов
        # Карточные контексты (виджеты + состояние аккордеона контактов):
        # один на активную группу (переживает весь диалог) и по одному на
        # каждую архивную группу (пересоздаются в _refresh_prev_section).
        self._active_ctx = _GroupCardCtx(self._active_group, is_active=True)
        self._archived_cards: list[_GroupCardCtx] = []
        # Реестр людей: источник для автодополнения ФИО и переиспользования контактов.
        self._people: list = people_reg.load_people()
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        # Панель = два экрана: деталь участка ⇄ список контактов (Фаза 2).
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._detail_view = QWidget()
        outer.addWidget(self._detail_view)

        # Под-панель контактов: шапка «← Назад» + слот под GroupEditDialog
        self._contacts_panel = None
        self._contacts_ctx = None
        self._contacts_view = QWidget()
        self._contacts_view.setVisible(False)
        cv = QVBoxLayout(self._contacts_view)
        cv.setContentsMargins(20, 16, 20, 0)
        cv.setSpacing(6)
        self._contacts_back = QPushButton("←  Назад")
        self._contacts_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._contacts_back.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#07414F;"
            "font-size:13px;font-weight:600;text-align:left;padding:2px 0;}"
            "QPushButton:hover{color:#0B5A6E;}")
        self._contacts_back.clicked.connect(self._on_contacts_back)
        _bh = QHBoxLayout(); _bh.setContentsMargins(0, 0, 0, 0)
        _bh.addWidget(self._contacts_back); _bh.addStretch()
        cv.addLayout(_bh)
        self._contacts_slot = QVBoxLayout()
        self._contacts_slot.setContentsMargins(0, 0, 0, 0)
        cv.addLayout(self._contacts_slot, stretch=1)
        outer.addWidget(self._contacts_view)

        # -- Содержимое детали строится в _detail_view --
        # lay сам без горизонтальных отступов — они переехали в content_top/
        # content_bottom (см. ниже), чтобы разделитель перед «Предыдущими
        # группами» мог быть добавлен НАПРЯМУЮ в lay и тянуться на всю
        # ширину панели, а не только на ширину карточки/кнопки над ним
        # (родителем виджета-разделителя должен быть сам self._detail_view,
        # а не что-то уже вписанное в отступы).
        lay = QVBoxLayout(self._detail_view)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        content_top = QVBoxLayout()
        # Верх 26 — чтобы «Участок № X» встал вровень с заголовком списка (у колонки верх 24).
        content_top.setContentsMargins(24, 26, 24, 0)
        content_top.setSpacing(12)
        lay.addLayout(content_top)

        # -- Заголовок участка: «Участок № X» + площадь + одна кнопка правки --
        area_raw = self._plot_data.get("area")
        area_text = ""
        if area_raw not in (None, "", 0):
            try:
                area_text = f"{float(area_raw):g}"
            except (TypeError, ValueError):
                area_text = str(area_raw)
        self._orig_num = str(self._plot_data.get("num", ""))
        self._orig_area = area_text
        self._header_editing = False

        def _icon_btn(cp: int, handler, tooltip: str = "", *,
                      color: str = "#6B7280", hover_bg: str = "#F3F4F6") -> QPushButton:
            b = QPushButton()
            b.setFixedSize(28, 28)
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{background:transparent;border:none;border-radius:4px;padding:2px;}"
                f"QPushButton:hover{{background:{hover_bg};}}")
            b.setIcon(_mat_icon(cp, 18, color=color))
            b.clicked.connect(handler)
            if tooltip:
                b.installEventFilter(_TooltipFilter(tooltip, b))
            return b

        if self._is_edit:
            # Режим отображения: «Участок № X» + кнопки, под ним площадь
            self._disp_w = QWidget()
            dv = QVBoxLayout(self._disp_w)
            dv.setContentsMargins(0, 0, 0, 0)
            dv.setSpacing(4)
            drow = QHBoxLayout()
            drow.setContentsMargins(0, 0, 0, 0)
            self._num_title = QLabel()
            self._num_title.setStyleSheet(
                "font-size:18px; font-weight:700; color:#1F2937; background:transparent;")
            drow.addWidget(self._num_title)
            drow.addStretch()
            # Кнопка сохранения (справа от карандаша, скрыта вне режима правки)
            self._save_btn = _icon_btn(0xE161, self._on_header_save, "Сохранить участок")
            self._save_btn.setVisible(False)
            drow.addWidget(self._save_btn)
            # Карандаш — всегда виден; fill = режим правки активен
            self._edit_btn = _icon_btn(0xF88D, self._on_header_edit, "Редактировать участок")
            drow.addWidget(self._edit_btn)
            # «Удалить участок» / «Закрыть» — справа от карандаша (были в футере).
            del_btn = _icon_btn(0xE92B, self._on_delete_clicked, "Удалить участок",
                               color="#DC2626", hover_bg="#FEF2F2")
            drow.addWidget(del_btn)
            close_btn = _icon_btn(0xE5CD, self._on_close, "Закрыть")
            drow.addWidget(close_btn)
            dv.addLayout(drow)
            self._area_display = QLabel()
            self._area_display.setStyleSheet(
                "font-size:13px; color:#6B7280; background:transparent;")
            dv.addWidget(self._area_display)
            content_top.addWidget(self._disp_w)
        else:
            title_lbl = QLabel(self._title)
            title_lbl.setStyleSheet(
                "font-size:16px; font-weight:700; color:#1F2937; background:transparent;")
            content_top.addWidget(title_lbl)

        # Блок редактирования номера/площади (в режиме правки; для нового — всегда)
        self._edit_block = QWidget()
        eb = QVBoxLayout(self._edit_block)
        eb.setContentsMargins(0, 0, 0, 0)
        eb.setSpacing(6)
        cap_row = QHBoxLayout()
        cap_row.setContentsMargins(0, 0, 0, 0)
        cap_row.setSpacing(6)
        _cap_num = QLabel("Номер участка")
        _cap_num.setStyleSheet("color:#6B7280; background:transparent;")
        cap_row.addWidget(_cap_num)
        self._lbl_num_taken = _make_warn_pill(None, "номер занят")
        self._lbl_num_taken.hide()
        cap_row.addWidget(self._lbl_num_taken)
        cap_row.addStretch()
        if self._is_edit:
            cap_row.addStretch()
        else:
            # Новый участок: иконка-галочка «сохранить» в том же стиле
            self._btn_save = _icon_btn(0xE161, self._on_accept)
            cap_row.addWidget(self._btn_save)
        eb.addLayout(cap_row)
        self.inp_num = QLineEdit(self._orig_num)
        self.inp_num.setPlaceholderText("например: 15 или 15/207")
        self.inp_num.textChanged.connect(lambda: self._on_field_changed("num"))
        eb.addWidget(self.inp_num)
        _cap_area = QLabel("Площадь, м²")
        _cap_area.setStyleSheet("color:#6B7280; background:transparent;")
        eb.addWidget(_cap_area)
        self.inp_area = QLineEdit(area_text)
        self.inp_area.setPlaceholderText("например: 612")
        self.inp_area.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"^\d{0,5}([.,]\d{0,2})?$"), self.inp_area
        ))
        self.inp_area.textEdited.connect(lambda t: (
            self.inp_area.setText(t.replace(".", ",")),
            self.inp_area.setCursorPosition(self.inp_area.cursorPosition()),
        ) if "." in t else None)
        self.inp_area.textChanged.connect(lambda: self._on_field_changed("area"))
        eb.addWidget(self.inp_area)
        content_top.addWidget(self._edit_block)

        if self._is_edit:
            self.inp_num.setReadOnly(True)
            self.inp_area.setReadOnly(True)
            self._edit_block.setVisible(False)
            self._refresh_displays()

        # -- Карточка активной группы (шапка + блок, билдер общий с архивными) --
        self._build_group_card(self._active_ctx, content_top, "Активная группа")
        self._refresh_group_card(self._active_ctx)

        # -- Разделитель перед «Предыдущими группами» --
        # QFrame(HLine) рисует рамку с фаской (градиент светлого/тёмного) —
        # background/color в QSS её не перекрывают, из-за чего линия выходит
        # заметно толще и «внутрь» от краёв, чем остальные тонкие бордеры в
        # панели. Обычный QWidget с залитым фоном — тот же приём, что и у
        # разделителя внутри карточки группы. ВАЖНО: добавлен НАПРЯМУЮ в lay
        # (без горизонтальных отступов — см. lay.setContentsMargins(0,0,0,0)
        # выше), а не внутрь content_top/content_bottom — так линия занимает
        # ровно всю ширину self._detail_view, а не только ширину карточки/
        # кнопки над ней (попытка сделать то же самое отрицательными полями
        # у обёртки внутри уже отцентрованного по отступам виджета эффекта
        # не давала).
        self._prev_sep_line = QWidget()
        self._prev_sep_line.setFixedHeight(1)
        self._prev_sep_line.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._prev_sep_line.setStyleSheet("background:#E5E7EB;")
        lay.addSpacing(12)
        lay.addWidget(self._prev_sep_line)
        lay.addSpacing(12)

        content_bottom = QVBoxLayout()
        content_bottom.setContentsMargins(24, 0, 24, 24)
        content_bottom.setSpacing(12)
        lay.addLayout(content_bottom)

        # -- Предыдущие группы -- без подписи и без вложенной scroll-области:
        # разделитель над этим блоком — теперь единственная граница секции;
        # архивные карточки просто идут одна под другой и скроллятся вместе
        # со всей панелью (см. _drawer_scroll в PlotsWidget), а не в отдельном
        # обособленном «белом поле» с собственной рамкой/пиком следующей карточки.
        self._prev_section = QWidget()
        self._prev_section.setStyleSheet("background:transparent;")
        self._prev_card_lyt = QVBoxLayout(self._prev_section)
        self._prev_card_lyt.setContentsMargins(0, 0, 0, 0)
        self._prev_card_lyt.setSpacing(8)

        content_bottom.addWidget(self._prev_section)
        self._refresh_prev_section()

        lay.addStretch()  # свободное место уходит сюда — карточки прижаты к верху

        # -- Footer -- «Удалить участок»/«Закрыть» переехали иконками в шапку
        # (drow, рядом с карандашом правки) — для режима правки футер больше
        # не нужен. Для создания нового участка (сейчас недостижимо — см.
        # PlotsWidget._add_plot) футер с «Отмена» остаётся как был.
        if not self._is_edit:
            sep2 = QWidget()
            sep2.setFixedHeight(1)
            sep2.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            sep2.setStyleSheet("background:#E5E7EB;")
            lay.addWidget(sep2)

            footer = QWidget()
            f_lay = QHBoxLayout(footer)
            f_lay.setContentsMargins(24, 12, 24, 24)
            f_lay.setSpacing(8)
            f_lay.addStretch()
            btn_cancel = QPushButton("Отмена")
            btn_cancel.setObjectName("btnFooterCancel")
            btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_cancel.clicked.connect(lambda: self._finish(False))
            f_lay.addWidget(btn_cancel)
            # «Сохранить» — иконка-галочка в шапке (cap_row), не в футере
            lay.addWidget(footer)
            self._footer_view = footer
        else:
            self._footer_view = None
        self._footer_edit = None

        if not self._is_edit:
            self._update_save_state()

    def _build_group_card(self, ctx: "_GroupCardCtx", target_lay: QVBoxLayout,
                          header_text: str):
        """Строит визуальный блок группы (шапка + карточка) в target_lay и
        сохраняет ссылки на виджеты в ctx для _refresh_group_card/
        _refresh_contacts_preview. Общий для активной и архивных групп —
        различается только цвет карточки и подписи (см. ctx.is_active)."""
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 4, 0, 0)
        lbl = QLabel(header_text)
        lbl.setStyleSheet(
            "font-size:12px; color:#6B7280; background:transparent;")
        hdr.addWidget(lbl, stretch=1)
        ctx.docs_badge_box = QWidget()
        ctx.docs_badge_box.setStyleSheet("background:transparent;")
        ctx.docs_badge_lay = QHBoxLayout(ctx.docs_badge_box)
        ctx.docs_badge_lay.setContentsMargins(0, 0, 0, 0)
        hdr.addWidget(ctx.docs_badge_box)
        if ctx.is_active:
            # Дата начала активной группы — ВСЕГДА редактируемый date-пикер
            # (без отдельного режима «включить правку» — правится напрямую).
            since_prefix = QLabel("Активна с:")
            since_prefix.setStyleSheet(
                "font-size:12px; color:#6B7280; background:transparent;")
            hdr.addWidget(since_prefix)

            ctx.since_date_edit = QDateEdit(calendarPopup=True, displayFormat="dd.MM.yyyy")
            ctx.since_date_edit.setFixedWidth(110)
            ctx.since_date_edit.setStyleSheet(
                "QDateEdit{background:#FFFFFF;border:1px solid #C9D8E2;"
                "border-radius:6px;padding:2px 4px 2px 6px;font-size:12px;color:#1F2937;}"
                "QDateEdit::drop-down{subcontrol-origin:padding;subcontrol-position:right;"
                "width:18px;border:none;border-left:1px solid #C9D8E2;background:transparent;"
                "border-top-right-radius:6px;border-bottom-right-radius:6px;}"
                "QDateEdit::drop-down:hover{background:#DCE7EC;}"
                "QDateEdit::down-arrow{width:8px;height:8px;}")
            ctx.since_date_edit.dateChanged.connect(
                lambda _, c=ctx: self._on_since_changed(c))
            hdr.addWidget(ctx.since_date_edit)
        else:
            ctx.since_lbl = QLabel()
            ctx.since_lbl.setStyleSheet(
                "font-size:12px; color:#6B7280; background:transparent;")
            hdr.addWidget(ctx.since_lbl)

        target_lay.addLayout(hdr)

        # -- Карточка группы (одна колонка, по макету) --
        # ВАЖНО: селектор должен быть по ID (#groupCard), а не голым "QFrame{...}" —
        # QLabel в Qt сам является подклассом QFrame, и голый тип-селектор
        # каскадируется на все дочерние QLabel (бордер/радиус), у которых эти
        # конкретные свойства не переопределены их собственным stylesheet.
        bg, border = ("#EBF4F6", "#C9D8E2") if ctx.is_active else ("#F8F9FA", "#E5E7EB")
        ctx.card = QFrame()
        ctx.card.setObjectName("groupCard")
        ctx.card.setStyleSheet(
            f"QFrame#groupCard{{background:{bg};border:1px solid {border};border-radius:8px;}}")
        card_lay = QVBoxLayout(ctx.card)
        card_lay.setContentsMargins(14, 12, 14, 12)
        card_lay.setSpacing(4)

        ctx.name_lbl = QLabel()
        ctx.name_lbl.setStyleSheet(
            "font-size:14px; font-weight:700; color:#07414F; background:transparent;")
        ctx.name_lbl.setWordWrap(True)
        card_lay.addWidget(ctx.name_lbl)

        ctx.counts_lbl = QLabel()
        ctx.counts_lbl.setStyleSheet(
            "font-size:12px; color:#6B7280; background:transparent;")
        card_lay.addWidget(ctx.counts_lbl)

        # Тонкая линия от левого до правого края карточки — вылезает за
        # отступы card_lay (14px) отрицательными полями обёртки, иначе была
        # бы видна с отступом, как рамка внутри рамки. Обычный QWidget, а не
        # QFrame(HLine) — у QFrame стиль рисует рамку с фаской (градиент
        # светлого/тёмного), и background/color в QSS её не перекрывают.
        sep_line = QWidget()
        sep_line.setFixedHeight(1)
        sep_line.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sep_line.setStyleSheet("background:#E5E7EB;")
        _sep_wrap = QHBoxLayout()
        _sep_wrap.setContentsMargins(-14, 0, -14, 0)
        _sep_wrap.addWidget(sep_line)
        card_lay.addLayout(_sep_wrap)

        _debt_hdr = QLabel("Задолженность" if ctx.is_active else "Долг на дату закрытия")
        _debt_hdr.setStyleSheet(
            "font-size:12px; color:#6B7280; background:transparent;")
        card_lay.addWidget(_debt_hdr)

        # Декоративная (некликабельная) белая рамка — тот же стиль, что и у
        # карточек контактов, просто без hover-подсветки.
        ctx.debt_rows_box = QWidget()
        ctx.debt_rows_box.setObjectName("debtBox")
        ctx.debt_rows_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        ctx.debt_rows_box.setStyleSheet(
            "QWidget#debtBox{background:#FFFFFF;border:1px solid #E5E7EB;border-radius:8px;}")
        ctx.debt_rows_lay = QVBoxLayout(ctx.debt_rows_box)
        ctx.debt_rows_lay.setContentsMargins(10, 8, 10, 8)
        ctx.debt_rows_lay.setSpacing(4)
        card_lay.addWidget(ctx.debt_rows_box)

        # ── Список контактов (превью + аккордеон) ──────────────────────
        _contacts_hdr = QLabel("Список контактов")
        _contacts_hdr.setStyleSheet(
            "font-size:12px; color:#6B7280; background:transparent;")
        card_lay.addWidget(_contacts_hdr)

        # Превью: до 3 контактов, показываются сразу
        ctx.preview_box = QWidget()
        ctx.preview_box.setStyleSheet("background:transparent;")
        ctx.preview_lay = QVBoxLayout(ctx.preview_box)
        ctx.preview_lay.setContentsMargins(0, 0, 0, 0)
        ctx.preview_lay.setSpacing(6)
        card_lay.addWidget(ctx.preview_box)

        # Строка действий: «Показать всех»/«Свернуть» слева, «Добавить
        # контакт» справа — оба кликабельный текст, без рамки/фона кнопки.
        _actions_row = QWidget()
        _actions_row.setStyleSheet("background:transparent;")
        _actions_lay = QHBoxLayout(_actions_row)
        _actions_lay.setContentsMargins(0, 0, 0, 0)
        _actions_lay.setSpacing(8)

        # «Показать всех» / «Свернуть» — слева, со стрелкой после текста
        # (вниз — развернуть, вверх — свернуть).
        ctx.show_all_row = QWidget()
        ctx.show_all_row.setStyleSheet("background:transparent;")
        ctx.show_all_row.setCursor(Qt.CursorShape.PointingHandCursor)
        _sa_lay = QHBoxLayout(ctx.show_all_row)
        _sa_lay.setContentsMargins(0, 4, 0, 4)
        _sa_lay.setSpacing(2)
        _SA_SS = "font-size:12px; color:#07414F; background:transparent;"
        _SA_SS_HOVER = "font-size:12px; color:#062F38; background:transparent;"
        ctx.show_all_lbl = QLabel("Показать всех")
        ctx.show_all_lbl.setStyleSheet(_SA_SS)
        ctx.show_all_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        _sa_lay.addWidget(ctx.show_all_lbl)
        ctx.show_all_arrow = QLabel(chr(0xE5CF))
        ctx.show_all_arrow.setFont(_mat_font(16))
        ctx.show_all_arrow.setStyleSheet("color:#07414F; background:transparent;")
        ctx.show_all_arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        _sa_lay.addWidget(ctx.show_all_arrow)

        def _sa_enter(e, c=ctx):
            c.show_all_lbl.setStyleSheet(_SA_SS_HOVER)
            c.show_all_arrow.setStyleSheet("color:#062F38; background:transparent;")

        def _sa_leave(e, c=ctx):
            c.show_all_lbl.setStyleSheet(_SA_SS)
            c.show_all_arrow.setStyleSheet("color:#07414F; background:transparent;")

        ctx.show_all_row.enterEvent = _sa_enter
        ctx.show_all_row.leaveEvent = _sa_leave
        ctx.show_all_row.mouseReleaseEvent = (
            lambda e, c=ctx: self._on_toggle_contacts_full(c)
            if e.button() == Qt.MouseButton.LeftButton else None)
        ctx.show_all_row.setVisible(False)
        _actions_lay.addWidget(ctx.show_all_row)
        _actions_lay.addStretch(1)

        # «Добавить контакт» — справа, с иконкой person_add перед текстом.
        _add_contact_row = QWidget()
        _add_contact_row.setStyleSheet("background:transparent;")
        _add_contact_row.setCursor(Qt.CursorShape.PointingHandCursor)
        _ac_lay = QHBoxLayout(_add_contact_row)
        _ac_lay.setContentsMargins(0, 4, 0, 4)
        _ac_lay.setSpacing(4)
        _AC_SS = "font-size:12px; color:#07414F; background:transparent;"
        _AC_SS_HOVER = "font-size:12px; color:#062F38; background:transparent;"
        _ac_icon = QLabel(chr(0xE7FE))
        _ac_icon.setFont(_mat_font(16))
        _ac_icon.setStyleSheet("color:#07414F; background:transparent;")
        _ac_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        _ac_lay.addWidget(_ac_icon)
        _ac_lbl = QLabel("Добавить контакт")
        _ac_lbl.setStyleSheet(_AC_SS)
        _ac_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        _ac_lay.addWidget(_ac_lbl)

        def _ac_enter(e):
            _ac_lbl.setStyleSheet(_AC_SS_HOVER)
            _ac_icon.setStyleSheet("color:#062F38; background:transparent;")

        def _ac_leave(e):
            _ac_lbl.setStyleSheet(_AC_SS)
            _ac_icon.setStyleSheet("color:#07414F; background:transparent;")

        _add_contact_row.enterEvent = _ac_enter
        _add_contact_row.leaveEvent = _ac_leave
        _add_contact_row.mouseReleaseEvent = (
            lambda e, c=ctx: self._on_add_contact(c)
            if e.button() == Qt.MouseButton.LeftButton else None)
        _actions_lay.addWidget(_add_contact_row)

        card_lay.addWidget(_actions_row)

        target_lay.addWidget(ctx.card)

        if ctx.is_active:
            ctx.new_group_btn = QPushButton(" Создать новую группу")
            ctx.new_group_btn.setIcon(_mat_icon(0xE7F0, 18, color="#374151"))
            ctx.new_group_btn.setIconSize(QSize(18, 18))
            ctx.new_group_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            ctx.new_group_btn.setStyleSheet(
                "QPushButton{background:#E5E7EB;color:#374151;border:1px solid #D1D5DB;"
                "border-radius:8px;padding:12px 16px;font-size:14px;font-weight:600;}"
                "QPushButton:hover{background:#D1D5DB;}"
                "QPushButton:disabled{background:#F3F4F6;color:#9CA3AF;border:1px solid #E5E7EB;}")
            ctx.new_group_btn.clicked.connect(self._on_archive_active_group)
            target_lay.addSpacing(6)
            target_lay.addWidget(ctx.new_group_btn)

    def _on_header_edit(self):
        """Тоггл режима правки номера+площади.
        Если режим не активен — входим. Если активен — выходим без сохранения (откат)."""
        if self._header_editing:
            self._set_header_edit(False, revert=True)
        else:
            self._set_header_edit(True)

    def _set_header_edit(self, editing: bool, *, revert: bool = False):
        if not self._is_edit:
            return
        self._header_editing = editing
        if editing:
            self.inp_num.setReadOnly(False)
            self.inp_area.setReadOnly(False)
            self._num_title.setVisible(False)
            self._area_display.setVisible(False)
            self._edit_block.setVisible(True)
            self._save_btn.setVisible(True)
            self._edit_btn.setIcon(_mat_icon(0xF88D, 18, fill=1, color="#07414F"))
            self.inp_num.setFocus()
            self.inp_num.selectAll()
            self._refresh_save_icon()
        else:
            if revert:
                self.inp_num.setText(self._orig_num)
                self.inp_area.setText(self._orig_area)
            self.inp_num.setReadOnly(True)
            self.inp_area.setReadOnly(True)
            self._lbl_num_taken.hide()
            self._edit_block.setVisible(False)
            self._save_btn.setVisible(False)
            self._edit_btn.setIcon(_mat_icon(0xF88D, 18, fill=0, color="#6B7280"))
            self._num_title.setVisible(True)
            self._area_display.setVisible(True)
            self._refresh_displays()

    def _on_header_save(self):
        """Коммит номера+площади (кнопка-галочка). Блок при пустом/дубле."""
        num = self.inp_num.text().strip()
        if not num or self._is_num_taken():
            return  # остаёмся в правке — причина видна по бейджу/серой кнопке
        area = self.inp_area.text().strip()
        if num != self._orig_num:
            self._dirty_fields.add("num")
        if area != self._orig_area:
            self._dirty_fields.add("area")
        self._orig_num = num
        self._orig_area = area
        self._set_header_edit(False)

    def _refresh_save_icon(self):
        if getattr(self, "_save_btn", None) is None:
            return
        ok = bool(self.inp_num.text().strip()) and not self._is_num_taken()
        self._save_btn.setEnabled(ok)
        self._save_btn.setIcon(
            _mat_icon(0xE161, 18, fill=0, color="#07414F" if ok else "#9CA3AF"))

    def _refresh_displays(self):
        self._num_title.setText(
            f"Участок № {self._orig_num}" if self._orig_num else "Участок № —")
        self._area_display.setText(
            f"Площадь {self._orig_area} м²" if self._orig_area else "Площадь не указана")

    def _on_field_changed(self, field_name: str):
        """Обновляет бейдж дубля и состояние сохранения при вводе."""
        if field_name == "num":
            self._check_num_duplicate()
        if self._is_edit:
            if self._header_editing:
                self._refresh_save_icon()
        else:
            self._update_save_state()

    def _check_num_duplicate(self):
        """Показывает пилюлю «номер занят» при совпадении с существующим номером."""
        current = self.inp_num.text().strip()
        self._lbl_num_taken.setVisible(bool(current and current in self._existing_nums))

    def _on_close(self) -> bool:
        """Закрывает панель: если есть закоммиченные изменения — saved=True.

        Возвращает False, если пользователь отменил закрытие из-за
        несохранённых правок в открытой карточке контакта (см.
        _attempt_close_contact) — панель остаётся как есть, вызывающий код
        (например, переход на другой участок) должен прерваться."""
        if not self._confirm_pending_contact_edits():
            return False
        # Незакоммиченную правку номера/площади откатываем (без предупреждения)
        if self._is_edit and self._header_editing:
            self._set_header_edit(False, revert=True)
        if self._dirty_fields or self._group_dirty:
            self._build_result()
            self._finish(True)
        else:
            self._finish(False)
        return True

    def _all_card_ctxs(self) -> list:
        """Все карточные контексты панели: активная группа + все архивные —
        для операций, затрагивающих раскрытые контакты глобально."""
        return [self._active_ctx, *self._archived_cards]

    def _confirm_pending_contact_edits(self) -> bool:
        """Если есть открытая карточка контакта (в любой группе — активной
        или архивной) с несохранёнными правками — спрашивает через диалог
        (см. _attempt_close_contact). True — можно продолжать закрытие/
        переход на другой участок."""
        for ctx in self._all_card_ctxs():
            for idx in list(ctx.expanded_contacts):
                if not self._attempt_close_contact(ctx, idx):
                    return False
        return True

    def _finish(self, saved: bool):
        """Завершает работу панели: отключает focusChanged и эмитит closed(saved)."""
        self.detach()
        self.closed.emit(saved)

    def _on_delete_clicked(self):
        """Удаление участка — с подтверждением; решение исполняет хост (PlotsWidget)."""
        confirmed = _ConfirmDialog.confirm(
            self, "Удалить этот участок?",
            "Будет удалена вся информация участка, включая документы.",
            confirm_text="Да, удалить")
        if confirmed:
            self.detach()
            self.deleted.emit()

    def detach(self):
        """Зарезервировано: ранее отключало глобальный focusChanged.

        Сейчас панель не подключается к focusChanged (правка номера/площади —
        через явные кнопки), поэтому метод оставлен как безопасный no-op для
        совместимости с вызовами из _finish и хоста."""
        return

    def _build_result(self):
        """Собирает итоговый словарь и сохраняет в self._result."""
        num = self.inp_num.text().strip()
        area_raw = self.inp_area.text().strip().replace(",", ".")
        area_val = None
        if area_raw:
            try:
                area_val = float(area_raw)
                if area_val <= 0:
                    area_val = None
            except ValueError:
                pass
        archived_by_id = {id(c.orig): c.group for c in self._archived_cards}
        final_groups = [
            self._active_group if g.get("until") is None
            else archived_by_id.get(id(g), g)
            for g in self._groups
        ]
        result = {"num": num, "groups": final_groups}
        if area_val is not None:
            result["area"] = area_val
        for k in ("billing_type", "meter_commission_date", "meter_act_number",
                  "meter_location", "norm_kw", "norm_start_date",
                  "direct_contract_date", "direct_contract_number",
                  "billing_history", "ownership_history"):
            if k in self._plot_data:
                result[k] = self._plot_data[k]
        self._result = result

    def _sync_owners_from_registry(self, owners: list) -> None:
        """Подтягивает актуальные ФИО/телефон/email/ОПД/заявление из реестра
        людей для контактов активной группы.

        Эти поля не зависят от участка (см. докстринг core/people.py) —
        реестр является источником истины (привязка — по стабильному person_id,
        а не по имени, поэтому переживает переименование). Без этой
        синхронизации значение, изменённое для человека на ОДНОМ участке,
        продолжает висеть на его контактах на ДРУГИХ участках, пока их не
        пересохранят вручную (например, повторным выбором ФИО из
        автодополнения)."""
        changed = False
        for o in owners:
            if not isinstance(o, dict):
                continue
            person = people_reg.get(self._people, o.get("person_id"))
            if not person:
                continue
            for field in ("name", "phone", "email", "opd_doc", "member_doc"):
                reg_val = str(person.get(field, ""))
                own_val = str(o.get(field, ""))
                if reg_val == own_val:
                    continue
                if reg_val:
                    o[field] = reg_val
                elif field != "name" and field in o:
                    # "name" не удаляем даже если в реестре почему-то пусто —
                    # у контакта всегда должно оставаться хоть какое-то ФИО.
                    del o[field]
                changed = True
        if changed:
            self._group_dirty = True

    def _refresh_group_card(self, ctx: "_GroupCardCtx"):
        """Обновляет карточку группы (активной или архивной) — имя, дату/
        период, бейдж документов, счётчики, долг и превью контактов."""
        owners = ownership.group_owners(ctx.group)
        self._sync_owners_from_registry(owners)
        if _ensure_single_primary(owners):
            self._group_dirty = True

        # Имя главного участника (is_visible=True), иначе первый в списке
        main = next((o for o in owners if isinstance(o, dict) and o.get("is_visible")),
                    owners[0] if owners else None)
        name = ownership.owner_name(main) if main else "(нет лиц)"
        ctx.name_lbl.setText(name)

        if ctx.new_group_btn is not None:
            ctx.new_group_btn.setEnabled(bool(owners))
            ctx.new_group_btn.setToolTip(
                "" if owners else "Добавьте хотя бы один контакт, чтобы создать новую группу")

        # Дата начала (активная — date-пикер) / период (архивная — лейбл)
        if ctx.is_active:
            since = ownership.group_since(ctx.group)
            ctx.since_date_edit.blockSignals(True)
            ctx.since_date_edit.setDate(
                QDate(since.year, since.month, since.day) if since
                else QDate.currentDate())
            ctx.since_date_edit.setMinimumDate(self._since_min_date())
            ctx.since_date_edit.blockSignals(False)
        else:
            ctx.since_lbl.setText(self._group_period_text(ctx))

        # Бейдж «не хватает документов» по всей группе (в заголовке)
        while ctx.docs_badge_lay.count():
            item = ctx.docs_badge_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        _have, _total = self._group_docs_progress(owners)
        if _total and _have < _total:
            ctx.docs_badge_lay.addWidget(
                _make_docs_badge(ctx.docs_badge_box, _have, _total))

        # Счётчики
        n_members = sum(1 for o in owners if isinstance(o, dict) and o.get("is_member"))
        count_lines = []
        if n_members:
            count_lines.append(f"Члены СНТ: {n_members}")
        ctx.counts_lbl.setText("  ·  ".join(count_lines) if count_lines else "")

        # Долг/Аванс
        self._refresh_debt_card(ctx)

        # Превью контактов (до 3)
        self._refresh_contacts_preview(ctx)

    @staticmethod
    def _group_period_text(ctx: "_GroupCardCtx") -> str:
        """Текст справа в шапке архивной группы: период начало–окончание.
        У активной группы дата показывается в самом date-пикере (см.
        _build_group_card/_refresh_group_card), этот метод для неё не
        вызывается."""
        since = ownership.group_since(ctx.group)
        until = ownership.group_until(ctx.group)
        since_txt = since.strftime("%d.%m.%Y") if since else "начало"
        until_txt = until.strftime("%d.%m.%Y") if until else "—"
        return f"{since_txt} – {until_txt}"

    def _since_min_date(self) -> QDate:
        """Минимально допустимая дата начала активной группы — не раньше
        начала самой свежей архивной группы (иначе периоды групп
        пересекутся по датам). Виджет сам не даёт выбрать более раннюю дату."""
        if self._archived_cards:
            prev_since = ownership.group_since(self._archived_cards[0].group)
            if prev_since is not None:
                return QDate(prev_since.year, prev_since.month, prev_since.day).addDays(1)
        return QDate(1900, 1, 1)

    def _on_since_changed(self, ctx: "_GroupCardCtx"):
        """Дата начала активной группы изменена в date-пикере (поле всегда
        редактируемо — без отдельного режима правки). Если есть архивная
        группа — синхронно сдвигает её дату окончания (и пересчитывает
        замороженный долг на эту дату), чтобы периоды групп не пересекались
        и не оставляли разрыв. Программные обновления значения (см.
        _refresh_group_card) сигнал не порождают — blockSignals там же."""
        qd = ctx.since_date_edit.date()
        new_since = date(qd.year(), qd.month(), qd.day())
        ctx.group["since"] = new_since.isoformat()
        if self._archived_cards:
            prev = self._archived_cards[0]
            prev.group["until"] = new_since.isoformat()
            prev.group["debt_at_close"] = self._compute_group_debt(new_since, prev.group)
            self._refresh_group_card(prev)
        self._group_dirty = True
        self._refresh_group_card(ctx)
        self._update_save_state()

    @staticmethod
    def _group_docs_progress(owners) -> tuple[int, int]:
        """(загружено, требуется) обязательных документов по всей группе —
        той же логикой, что и бейдж у каждого контакта в списке контактов."""
        doc_field = {"opd": "opd_doc", "egrn": "egrn_doc", "member": "member_doc"}
        have = total = 0
        for o in owners:
            if not isinstance(o, dict):
                continue
            role_key = ("member" if o.get("is_member")
                        else "owner" if o.get("is_owner")
                        else "contact")
            req = _DOC_REQUIRED[role_key]
            for k in ("opd", "egrn", "member"):
                if req[k]:
                    total += 1
                    if o.get(doc_field[k]):
                        have += 1
        return have, total

    def _refresh_debt_card(self, ctx: "_GroupCardCtx"):
        """Долг по ЧВ и электроэнергии — построчно в секции «Задолженность».

        Для активной группы — живой расчёт на сегодня; для архивной —
        замороженное значение на дату закрытия (``debt_at_close``, см.
        _apply_replace), НЕ пересчитывается."""
        from core.utils import fmt_money

        def _make_row(label: str, debt) -> QWidget:
            row = QWidget()
            row.setStyleSheet("background:transparent;")
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.setSpacing(8)
            lbl = QLabel(label)
            lbl.setStyleSheet("font-size:12px; color:#374151; background:transparent;")
            row_lay.addWidget(lbl, stretch=1)
            if debt is None:
                amt_txt, color = "—", "#6B7280"
            elif debt < -0.005:
                amt_txt, color = f"-{fmt_money(abs(debt))}", "#2E7D32"
            elif debt > 0.005:
                amt_txt, color = fmt_money(debt), "#B45309"
            else:
                amt_txt, color = fmt_money(0), "#6B7280"
            amt = QLabel(amt_txt)
            amt.setStyleSheet(
                f"font-size:12px; font-weight:600; color:{color}; background:transparent;")
            row_lay.addWidget(amt)
            return row

        while ctx.debt_rows_lay.count():
            item = ctx.debt_rows_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not ctx.is_active:
            d = ctx.group.get("debt_at_close") or {}
            ctx.debt_rows_lay.addWidget(_make_row("Членские взносы", d.get("vznosy")))
            ctx.debt_rows_lay.addWidget(_make_row("Электроэнергия", d.get("energy")))
            return

        plot_num = str(self._plot_data.get("num", ""))
        since = ownership.group_since(ctx.group)
        try:
            from core import vznosy as vzn
            rates = vzn.load_rates()
            adj = vzn.load_adjustments()
            area = vzn.plot_area_map().get(plot_num)
            gb = vzn.balance_for_active_group(
                plot_num, area, date.today(), rates, adj, self._df, since=since)
            ctx.debt_rows_lay.addWidget(_make_row("Членские взносы", gb.debt))
        except Exception:
            pass
        try:
            from core import energy as en
            egb = en.balance_for_active_group(
                plot_num, date.today(), en.load_meters(), en.load_rates(),
                en.load_replacements(), en.load_baseline(), self._df, since=since)
            ctx.debt_rows_lay.addWidget(_make_row("Электроэнергия", egb.debt))
        except Exception:
            pass

    def _pending_contact_overrides(self, ctx: "_GroupCardCtx") -> dict:
        """Текущие (ещё НЕ сохранённые) значения полей развёрнутых карточек.

        Используется при перерисовке списка, которую вызвало постороннее
        действие (например, клик по звезде ДРУГОГО контакта), а не явное
        сохранение или сворачивание этой карточки — чтобы не откатить то, что
        пользователь уже начал печатать, к последним сохранённым данным.
        В отличие от старой _flush_expanded_contacts — НИЧЕГО не пишет в
        owners: сохранение теперь явное (кнопка «Сохранить» или выбор
        «Сохранить» в диалоге, см. _attempt_close_contact)."""
        result: dict = {}
        for idx in list(ctx.expanded_contacts):
            refs = ctx.contact_input_refs.get(idx)
            if not refs:
                continue
            inp_name, inp_phone, inp_email, opd_w, egrn_w, mem_w = refs
            try:
                result[idx] = {
                    "name": inp_name.text().strip(),
                    "phone": inp_phone.text().strip(),
                    "email": inp_email.text().strip(),
                    "opd_doc": opd_w.get_path(),
                    "egrn_doc": egrn_w.get_path(),
                    "member_doc": mem_w.get_path(),
                }
            except RuntimeError:
                # Виджеты уже уничтожены Qt (например, контакт выпал за
                # PREVIEW_LIMIT при переключении «Показать все»/«Свернуть»).
                # Восстанавливать нечего — забываем протухшую ссылку.
                ctx.contact_input_refs.pop(idx, None)
        return result

    def _contact_is_dirty(self, ctx: "_GroupCardCtx", idx: int) -> bool:
        """Есть ли у развёрнутой карточки idx несохранённые правки —
        сравнение текущих полей со снимком на момент разворачивания."""
        refs = ctx.contact_input_refs.get(idx)
        snap = ctx.contact_snapshots.get(idx)
        if not refs or snap is None:
            return False
        inp_name, inp_phone, inp_email, opd_w, egrn_w, mem_w = refs
        try:
            current = {
                "name": inp_name.text().strip(),
                "phone": inp_phone.text().strip(),
                "email": inp_email.text().strip(),
                "opd_doc": opd_w.get_path(),
                "egrn_doc": egrn_w.get_path(),
                "member_doc": mem_w.get_path(),
            }
        except RuntimeError:
            return False
        return current != snap

    def _discard_contact(self, ctx: "_GroupCardCtx", idx: int):
        """Сворачивает карточку idx БЕЗ сохранения — откатывает к тому, что
        уже есть в owners[idx]. Пустой ещё-не-сохранённый черновик при этом
        тихо удаляется (как раньше делал _save_and_collapse для пустого ФИО)."""
        ctx.contact_input_refs.pop(idx, None)
        ctx.contact_snapshots.pop(idx, None)
        ctx.expanded_contacts.discard(idx)
        owners = ctx.group.get("owners", []) or []
        o = owners[idx] if 0 <= idx < len(owners) else None
        if (isinstance(o, dict) and not ownership.owner_name(o)
                and not o.get("phone") and not o.get("email")
                and not o.get("opd_doc") and not o.get("egrn_doc")
                and not o.get("member_doc")):
            self._delete_contact_silently(ctx, idx)
        else:
            self._refresh_contacts_preview(ctx)
            self._update_save_state()

    def _attempt_close_contact(self, ctx: "_GroupCardCtx", idx: int) -> bool:
        """Пытается закрыть (свернуть) карточку idx — при уходе на другой
        контакт, закрытии панели участка или переходе на другой участок.

        Если правок нет — сворачивает молча. Если есть — спрашивает через
        диалог «Сохранить / Не сохранять / Отмена». Возвращает True, если
        можно продолжать (карточка закрыта — сохранена или правки отброшены),
        False — пользователь отменил, карточка остаётся открытой (и вызвавшее
        действие тоже должно быть отменено)."""
        if idx not in ctx.contact_input_refs:
            ctx.expanded_contacts.discard(idx)
            ctx.contact_snapshots.pop(idx, None)
            return True
        if not self._contact_is_dirty(ctx, idx):
            self._discard_contact(ctx, idx)
            return True
        owners = ctx.group.get("owners", []) or []
        o = owners[idx] if 0 <= idx < len(owners) else None
        name = ownership.owner_name(o) if isinstance(o, dict) else ""
        choice = _Save3WayDialog.ask(
            self,
            f"Сохранить изменения в «{name}»?" if name else "Сохранить изменения контакта?",
            "Есть несохранённые правки ФИО, контактов или документов.")
        if choice == "save":
            return self._save_and_collapse(ctx, idx)
        if choice == "discard":
            self._discard_contact(ctx, idx)
            return True
        return False

    def _refresh_contacts_preview(self, ctx: "_GroupCardCtx"):
        """Обновляет превью контактов под карточкой группы (активной или архивной)."""
        owners = ownership.group_owners(ctx.group)
        _pending_overrides = self._pending_contact_overrides(ctx)
        _AppTooltip.hide()
        # Очистка: скрываем перед удалением, чтобы не всплывали как top-level окна
        while ctx.preview_lay.count():
            item = ctx.preview_lay.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.deleteLater()

        PREVIEW_LIMIT = 3
        if ctx.contacts_expanded:
            shown = list(enumerate(owners))
        else:
            shown = list(enumerate(owners[:PREVIEW_LIMIT]))
        has_more = len(owners) > PREVIEW_LIMIT

        # Контакты, выпавшие за PREVIEW_LIMIT (или вовсе исчезнувшие) при этой
        # перерисовке, только что получили deleteLater() выше — их виджеты
        # больше не существуют, хотя индекс мог остаться «развёрнутым».
        # Забываем ссылки, чтобы никто потом не обратился к удалённому
        # C/C++ объекту (см. _pending_contact_overrides/_contact_is_dirty).
        _shown_idxs = {i for i, _ in shown}
        for _stale_idx in [i for i in ctx.contact_input_refs if i not in _shown_idxs]:
            ctx.contact_input_refs.pop(_stale_idx, None)
            ctx.contact_snapshots.pop(_stale_idx, None)

        _LABEL_SS = "font-size:10px; color:#9CA3AF; background:transparent;"

        for idx, o in shown:
            row_idx = idx
            is_open = idx in ctx.expanded_contacts

            # Строковый контейнер (vert): заголовок + (опционально) детали
            card = QWidget(self)
            card.setObjectName("contactPreviewCard")
            card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            card.setStyleSheet(
                "QWidget#contactPreviewCard{background:#FFFFFF;border:1px solid #E5E7EB;"
                "border-radius:8px;}"
                "QWidget#contactPreviewCard:hover{background:#E8F0F5;border:1px solid #C9D8E2;}")
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card_vl = QVBoxLayout(card)
            card_vl.setContentsMargins(8, 4, 8, 4)
            card_vl.setSpacing(0)

            # ── Заголовок строки ──────────────────────────────────────
            hdr = QWidget(card)
            hdr.setStyleSheet("background:transparent;")
            hdr.setCursor(Qt.CursorShape.PointingHandCursor)
            rl = QHBoxLayout(hdr)
            rl.setContentsMargins(0, 2, 0, 2)
            rl.setSpacing(6)

            # Имя (нужно для цвета иконки)
            full_name = ownership.owner_name(o) if isinstance(o, dict) else str(o)
            # Незавершённый ввод в уже открытой карточке — предпочитаем его
            # сохранённым данным при перерисовке, вызванной посторонним
            # действием (см. _pending_contact_overrides).
            _pending = _pending_overrides.get(idx) if is_open else None
            if _pending is not None:
                full_name = _pending["name"]

            # Звёздочка «избранный» на цветном фоне роли — объединяет
            # прежнюю декоративную иконку-бюст и отдельную кнопку-звезду,
            # экономит место в строке.
            role_key0 = ("member" if isinstance(o, dict) and o.get("is_member")
                         else "owner" if isinstance(o, dict) and o.get("is_owner")
                         else "contact")
            role_bg, role_fg = _ROLE_COLORS[role_key0]

            is_primary = isinstance(o, dict) and bool(o.get("is_visible"))
            star_btn = QPushButton(chr(0xE838), hdr)
            star_btn._role_bg = role_bg
            star_btn._role_fg = role_fg
            star_btn.setFont(_F_STAR_FILLED if is_primary else _F_STAR_OUTLINE)
            star_btn.setFixedSize(18, 18)
            star_btn.setFlat(True)
            star_btn.setStyleSheet(
                f"QPushButton{{background:{role_bg};border:none;border-radius:9px;"
                f"padding:0;color:{'#07414F' if is_primary else role_fg};}}")
            star_btn.setCursor(Qt.CursorShape.PointingHandCursor)

            def _star_enter(e, btn=star_btn, primary=is_primary):
                if not primary:
                    btn.setFont(_F_STAR_FILLED)
                    btn.setStyleSheet(
                        f"QPushButton{{background:{btn._role_bg};border:none;"
                        f"border-radius:9px;padding:0;color:#07414F;}}")

            def _star_leave(e, btn=star_btn, primary=is_primary):
                if not primary:
                    btn.setFont(_F_STAR_OUTLINE)
                    btn.setStyleSheet(
                        f"QPushButton{{background:{btn._role_bg};border:none;"
                        f"border-radius:9px;padding:0;color:{btn._role_fg};}}")

            star_btn.enterEvent = _star_enter
            star_btn.leaveEvent = _star_leave
            star_btn.clicked.connect(lambda _, i=idx, c=ctx: self._set_inline_primary(c, i))
            rl.addWidget(star_btn)

            # Телефон (свернуто) — определяем заранее, чтобы знать, останется
            # ли место у ФИО в свёрнутой строке.
            phone = (o.get("phone", "") if isinstance(o, dict) else "").strip()
            email_val = (o.get("email", "") if isinstance(o, dict) else "").strip()
            if _pending is not None:
                phone = _pending["phone"]
                email_val = _pending["email"]
            _name_limit = 20 if (phone and not is_open) else 40
            display_name = _truncate_name(full_name, _name_limit) if full_name else "—"
            lbl = QLabel(display_name, hdr)
            lbl.setStyleSheet(
                "font-size:12px; color:#1F2937; background:transparent;")
            lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            lbl.setSizePolicy(
                QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
            lbl.setMinimumWidth(0)
            if full_name and display_name != full_name:
                hdr.installEventFilter(_TooltipFilter(full_name, hdr))
            rl.addWidget(lbl, stretch=1)

            if phone and not is_open:
                ph_lbl = QLabel(phone, hdr)
                ph_lbl.setStyleSheet(
                    "font-size:11px; color:#6B7280; background:transparent;")
                ph_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                rl.addWidget(ph_lbl)

            # Бейдж «не хватает документов» (только в свёрнутом виде)
            if not is_open and isinstance(o, dict):
                _req = _DOC_REQUIRED[role_key0]
                _doc_field = {"opd": "opd_doc", "egrn": "egrn_doc", "member": "member_doc"}
                _req_keys = [k for k in ("opd", "egrn", "member") if _req[k]]
                _have = sum(1 for k in _req_keys if o.get(_doc_field[k]))
                _total = len(_req_keys)
                if _total and _have < _total:
                    rl.addWidget(_make_docs_badge(hdr, _have, _total))

            # Кнопки «убрать из списка» / «удалить контакт» — только в развёрнутом
            # виде, левее карандаша. Первая лишь отвязывает контакт от ЭТОЙ группы
            # (данные человека и его документы не трогает); вторая — необратимо
            # удаляет запись и относящуюся к участку Выписку ЕГРН.
            if is_open:
                btn_remove_list = QPushButton(hdr)
                btn_remove_list.setFixedSize(22, 22)
                btn_remove_list.setIconSize(QSize(16, 16))
                btn_remove_list.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_remove_list.setStyleSheet(
                    "QPushButton{background:transparent;border:none;border-radius:4px;}"
                    "QPushButton:hover{background:#F3F4F6;}")
                btn_remove_list.setIcon(_mat_icon(0xE15B, 16, color="#6B7280"))
                btn_remove_list.installEventFilter(
                    _TooltipFilter("Убрать из списка (без удаления данных)", btn_remove_list))
                btn_remove_list.clicked.connect(
                    lambda _, i=row_idx, c=ctx: self._remove_contact_from_list(c, i))
                rl.addWidget(btn_remove_list)

                btn_del_contact = QPushButton(hdr)
                btn_del_contact.setFixedSize(22, 22)
                btn_del_contact.setIconSize(QSize(16, 16))
                btn_del_contact.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_del_contact.setStyleSheet(
                    "QPushButton{background:transparent;border:none;border-radius:4px;}"
                    "QPushButton:hover{background:#FEF2F2;}")
                btn_del_contact.setIcon(_mat_icon(0xEF66, 16, color="#DC2626"))
                btn_del_contact.installEventFilter(
                    _TooltipFilter("Удалить контакт", btn_del_contact))
                btn_del_contact.clicked.connect(
                    lambda _, i=row_idx, c=ctx: self._delete_contact(c, i))
                rl.addWidget(btn_del_contact)

            # Кнопка справа: карандаш (свёрнуто, только на hover) — открыть на
            # правку; ЯВНАЯ «Сохранить» (развёрнуто) — вместо прежнего
            # карандаша-филл, чтобы момент сохранения был однозначным, а не
            # угадывался по сворачиванию (см. _attempt_close_contact — клик
            # по фону шапки/другому контакту теперь спрашивает через диалог,
            # если правки не сохранены явно этой кнопкой).
            btn_edit = QPushButton(hdr)
            btn_edit.setFixedSize(22, 22)
            btn_edit.setIconSize(QSize(16, 16))
            btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
            if is_open:
                btn_edit.setStyleSheet(
                    "QPushButton{background:transparent;border:none;border-radius:4px;}"
                    "QPushButton:hover{background:#F3F4F6;}")
                btn_edit.setIcon(_mat_icon(0xE161, 16, fill=0, color="#07414F"))
                btn_edit.installEventFilter(_TooltipFilter("Сохранить", btn_edit))
                btn_edit.clicked.connect(
                    lambda _, i=row_idx, c=ctx: self._save_and_collapse(c, i))
            else:
                btn_edit.setStyleSheet(
                    "QPushButton{background:transparent;border:none;}"
                    "QPushButton:hover{background:transparent;}")
                # Пустая иконка — кнопка резервирует место, но невидима до hover
                btn_edit.setIcon(QIcon())
                btn_edit.clicked.connect(
                    lambda _, i=row_idx, c=ctx: self._toggle_contact_detail(c, i))

                def _hdr_enter(e, btn=btn_edit):
                    btn.setIcon(_mat_icon(0xE3C9, 16, fill=0, color="#6B7280"))

                def _hdr_leave(e, btn=btn_edit):
                    btn.setIcon(QIcon())

                hdr.enterEvent = _hdr_enter
                hdr.leaveEvent = _hdr_leave
            rl.addWidget(btn_edit)

            # Шеврон: стрелка вниз (свернуто) / вверх (развёрнуто)
            chevron_lbl = QLabel(chr(0xE5CE) if is_open else chr(0xE5CF), hdr)
            chevron_lbl.setFont(_mat_font(16))
            chevron_lbl.setStyleSheet("color:#9CA3AF; background:transparent;")
            chevron_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            rl.addWidget(chevron_lbl)

            card_vl.addWidget(hdr)

            # ── Детали (только если раскрыто / режим правки) ─────────
            if is_open:
                det = QWidget(card)
                det.setStyleSheet("QWidget{background:transparent;}")
                det_lay = QVBoxLayout(det)
                det_lay.setContentsMargins(24, 4, 8, 6)
                det_lay.setSpacing(4)

                # ФИО — своя колонка с тем же интервалом метка/поле (2px),
                # что и у Телефона/Email ниже, для единообразия отступов.
                fio_col = QVBoxLayout()
                fio_col.setSpacing(2)
                lbl_fio_row = QHBoxLayout()
                lbl_fio_row.setContentsMargins(0, 0, 0, 0)
                lbl_fio_row.setSpacing(6)
                lbl_fio = QLabel("ФИО", styleSheet=_LABEL_SS, parent=det)
                lbl_fio_row.addWidget(lbl_fio)
                dup_pill = _make_warn_pill(det, "занято — не будет сохранено")
                dup_pill.hide()
                # Резервируем место скрытой пилюли — иначе её появление меняет
                # высоту строки и «дёргает» вниз всё, что расположено ниже
                # (безопасно: после неё addStretch(), переполнения по ширине нет —
                # см. заметку про retainSizeWhenHidden в qt_win11_gotchas).
                _dup_sp = dup_pill.sizePolicy()
                _dup_sp.setRetainSizeWhenHidden(True)
                dup_pill.setSizePolicy(_dup_sp)
                lbl_fio_row.addWidget(dup_pill)
                lbl_fio_row.addStretch(1)
                fio_col.addLayout(lbl_fio_row)
                inp_name = QLineEdit(full_name, parent=det)
                inp_name.setPlaceholderText("Фамилия Имя Отчество")
                inp_name.setStyleSheet(_INPUT_SS)
                inp_name.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
                # Защита от дублей: ФИО остальных контактов ЭТОЙ группы (кроме
                # себя) не предлагаются в автодополнении — иначе можно выбрать
                # уже добавленного человека второй раз.
                _other_names_norm = {
                    people_reg.norm_name(ownership.owner_name(ow))
                    for j, ow in enumerate(owners)
                    if j != idx and isinstance(ow, dict) and ownership.owner_name(ow)
                }
                _avail_names = [
                    n for n in _people_names(self._people)
                    if people_reg.norm_name(n) not in _other_names_norm
                ]
                _name_completer = QCompleter(_avail_names, inp_name)
                _name_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                _name_completer.setFilterMode(Qt.MatchFlag.MatchContains)
                inp_name.setCompleter(_name_completer)
                name_row = QHBoxLayout()
                name_row.setContentsMargins(0, 0, 0, 0)
                name_row.setSpacing(4)
                name_row.addWidget(inp_name, stretch=1)
                name_row.addWidget(_make_copy_btn(inp_name))
                fio_col.addLayout(name_row)
                det_lay.addLayout(fio_col)

                def _check_dup_name(_t="", inp=inp_name, pill=dup_pill,
                                     others=_other_names_norm):
                    pill.setVisible(
                        bool(inp.text().strip())
                        and people_reg.norm_name(inp.text()) in others)

                inp_name.textChanged.connect(_check_dup_name)
                _check_dup_name()

                # Телефон + Email (на одном уровне)
                contact_row = QHBoxLayout()
                contact_row.setSpacing(8)
                _contact_inputs = []
                for lbl_txt, val, placeholder, key in [
                    ("Телефон", _normalize_phone(phone), "+7 (xxx) xxx-xx-xx", "phone"),
                    ("Email", email_val, "example@mail.ru", "email"),
                ]:
                    col = QVBoxLayout()
                    col.setSpacing(2)
                    lbl_contact = QLabel(lbl_txt, styleSheet=_LABEL_SS, parent=det)
                    col.addWidget(lbl_contact)
                    inp = QLineEdit(val, parent=det)
                    inp.setPlaceholderText(placeholder)
                    inp.setStyleSheet(_INPUT_SS)
                    if key == "phone":
                        inp.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
                        _setup_phone_input(inp)
                    inp_row = QHBoxLayout()
                    inp_row.setContentsMargins(0, 0, 0, 0)
                    inp_row.setSpacing(4)
                    inp_row.addWidget(inp)
                    inp_row.addWidget(_make_copy_btn(inp))
                    col.addLayout(inp_row)
                    contact_row.addLayout(col, stretch=1)
                    _contact_inputs.append(inp)
                det_lay.addLayout(contact_row)

                inp_phone, inp_email = _contact_inputs

                # Роль — сегментированный контрол
                det_lay.addWidget(QLabel("Роль", styleSheet=_LABEL_SS, parent=det))
                _role = ("member" if o.get("is_member")
                         else "owner" if o.get("is_owner")
                         else "contact")

                # _ClipFrame обрезает дочерние кнопки маской по скруглённому
                # прямоугольнику и рисует рамку поверх — иначе прямые углы
                # кнопок вылезают за скругление рамки или оставляют щель.
                seg_frame = _ClipFrame(QColor("#CCCCCC"), 8)
                seg_lay = QHBoxLayout(seg_frame)
                seg_lay.setContentsMargins(0, 0, 0, 0)
                seg_lay.setSpacing(0)
                seg_group = QButtonGroup(seg_frame)
                seg_group.setExclusive(True)
                role_tags = []
                _SEG_SS = (
                    "QPushButton{font-size:11px; padding:6px 0; border:none; border-radius:0px;"
                    "border-right:1px solid #D8DAD8; background:#F7F7F5; color:#555555;}"
                    "QPushButton:hover:!checked{background:#EDEDEB;}"
                    "QPushButton#seg_last{border-right:none;}"
                    "QPushButton[role=\"contact\"]:checked{background:#E5E7EB; color:#6B7280; font-weight:500;}"
                    "QPushButton[role=\"owner\"]:checked{background:#C9D8E2; color:#07414F; font-weight:500;}"
                    "QPushButton[role=\"member\"]:checked{background:#D6EBD5; color:#2E7D32; font-weight:500;}")
                _roles = [("Контакт", "contact"), ("Собственник", "owner"), ("Член СНТ", "member")]
                for idx, (text, rk) in enumerate(_roles):
                    seg = QPushButton(text, seg_frame)
                    seg.setCheckable(True)
                    seg.setChecked(rk == _role)
                    seg.setCursor(Qt.CursorShape.PointingHandCursor)
                    seg.setProperty("role", rk)
                    if idx == len(_roles) - 1:
                        seg.setObjectName("seg_last")
                    seg_group.addButton(seg)
                    seg.setStyleSheet(_SEG_SS)
                    seg.clicked.connect(
                        lambda checked, rk2=rk, tags=role_tags, sb=star_btn, ow=o, c=ctx:
                            self._set_inline_role(c, ow, rk2, tags, sb))
                    role_tags.append((rk, seg))
                    seg_lay.addWidget(seg, 1)
                det_lay.addWidget(seg_frame)
                seg_frame.finish_setup()

                # Документы
                docs_hdr = QLabel("Документы", styleSheet=_LABEL_SS, parent=det)
                det_lay.addWidget(docs_hdr)

                _role = ("member" if o.get("is_member")
                         else "owner" if o.get("is_owner")
                         else "contact")
                _req = _DOC_REQUIRED[_role]

                opd_path    = o.get("opd_doc", "")    if isinstance(o, dict) else ""
                egrn_path   = o.get("egrn_doc", "")   if isinstance(o, dict) else ""
                member_path = o.get("member_doc", "") if isinstance(o, dict) else ""
                if _pending is not None:
                    opd_path = _pending["opd_doc"]
                    egrn_path = _pending["egrn_doc"]
                    member_path = _pending["member_doc"]

                opd_w  = _DocFieldWidget(opd_path, upload_tip="Согласие на ОПД")
                egrn_w = _DocFieldWidget(egrn_path, upload_tip="Выписка ЕГРН")
                mem_w  = _DocFieldWidget(member_path, upload_tip="Заявление в СНТ")

                opd_w.set_required(_req["opd"])
                egrn_w.set_required(_req["egrn"])
                mem_w.set_required(_req["member"])

                # Автодополнение по ФИО: подставляет телефон/email/ОПД/заявление
                # уже известного человека из реестра (см. _on_inline_name_committed).
                _name_completer.activated.connect(
                    lambda _t, ow=o, n=inp_name, p=inp_phone, e=inp_email, odw=opd_w, mdw=mem_w:
                        QTimer.singleShot(
                            0, lambda: self._on_inline_name_committed(ow, n, p, e, odw, mdw)))
                inp_name.editingFinished.connect(
                    lambda ow=o, n=inp_name, p=inp_phone, e=inp_email, odw=opd_w, mdw=mem_w:
                        self._on_inline_name_committed(ow, n, p, e, odw, mdw))

                _lbl_ss = "font-size:12px; color:#6B7280; background:transparent;"
                docs_box = QVBoxLayout()
                docs_box.setContentsMargins(0, 0, 0, 0)
                docs_box.setSpacing(4)

                for doc_w in [opd_w, egrn_w, mem_w]:
                    docs_box.addWidget(doc_w)

                det_lay.addLayout(docs_box)

                card_vl.addWidget(det)

                # Сохраняем ссылки на поля для явного сохранения/сравнения
                ctx.contact_input_refs[row_idx] = (inp_name, inp_phone, inp_email,
                                                 opd_w, egrn_w, mem_w)
                # Снимок «уже сохранённого» состояния — только при первом
                # разворачивании; на повторных перерисовках (см. _pending_
                # contact_overrides) базу для сравнения трогать нельзя, иначе
                # несохранённая правка перестанет считаться несохранённой.
                if row_idx not in ctx.contact_snapshots:
                    ctx.contact_snapshots[row_idx] = {
                        "name": ownership.owner_name(o) if isinstance(o, dict) else "",
                        "phone": (o.get("phone", "") if isinstance(o, dict) else "").strip(),
                        "email": (o.get("email", "") if isinstance(o, dict) else "").strip(),
                        "opd_doc": o.get("opd_doc", "") if isinstance(o, dict) else "",
                        "egrn_doc": o.get("egrn_doc", "") if isinstance(o, dict) else "",
                        "member_doc": o.get("member_doc", "") if isinstance(o, dict) else "",
                    }

            # ── Обработчики ────────────────────────────────────────────
            # Клик по заголовку (фону, не по кнопке «Сохранить»): попытка
            # закрыть карточку — с диалогом, если есть несохранённые правки.
            hdr.mouseReleaseEvent = (
                lambda event, i=row_idx, c=ctx: self._toggle_contact_detail(c, i)
                if event.button() == Qt.MouseButton.LeftButton else None)

            ctx.preview_lay.addWidget(card)

        if not shown:
            empty_lbl = QLabel("(нет лиц)")
            empty_lbl.setStyleSheet(
                "font-size:12px; color:#9CA3AF; background:transparent;")
            ctx.preview_lay.addWidget(empty_lbl)

        if has_more:
            ctx.show_all_row.setVisible(True)
            ctx.show_all_lbl.setText(
                "Свернуть" if ctx.contacts_expanded else "Показать всех")
            ctx.show_all_arrow.setText(
                chr(0xE5CE) if ctx.contacts_expanded else chr(0xE5CF))
        else:
            ctx.show_all_row.setVisible(False)
            ctx.contacts_expanded = False

    def _toggle_contact_detail(self, ctx: "_GroupCardCtx", idx: int):
        """Тоггл раскрытия контакта. Открыт → попытка закрыть (см.
        _attempt_close_contact — если есть несохранённые правки, спросит
        через диалог; явное сохранение — отдельная кнопка «Сохранить» в
        шапке карточки). Закрыт → открыть.

        Развёрнутым может быть только один контакт СРАЗУ ПО ВСЕЙ ПАНЕЛИ
        (активная + все архивные группы): при открытии нового все остальные
        (в т.ч. в других карточках) сначала пытаются закрыться тем же путём.
        Если пользователь отменил закрытие (в диалоге или из-за валидации) —
        переключение отменяется, старая карточка остаётся открытой."""
        if idx in ctx.expanded_contacts:
            self._attempt_close_contact(ctx, idx)
            return
        for other_ctx in self._all_card_ctxs():
            for other_idx in list(other_ctx.expanded_contacts):
                if other_ctx is ctx and other_idx == idx:
                    continue
                if not self._attempt_close_contact(other_ctx, other_idx):
                    return
        ctx.expanded_contacts.add(idx)
        self._refresh_contacts_preview(ctx)

    def _save_and_collapse(self, ctx: "_GroupCardCtx", idx: int) -> bool:
        """Сохраняет поля развёрнутого контакта idx и сворачивает его.
        Возвращает False, если сворачивание заблокировано валидацией —
        в этом случае контакт остаётся развёрнутым.

        Пустое ФИО не сохраняется: если контакт при этом совсем пустой
        (ни телефона, ни email, ни документов) — он тихо удаляется как
        неначатый черновик. Если данные всё же есть — карточка остаётся
        открытой, а поле ФИО подсвечивается как обязательное.

        ФИО, совпадающее (без учёта регистра/пробелов) с другим контактом
        этой же группы, тоже блокирует сохранение — защита от дублей."""
        refs = ctx.contact_input_refs.get(idx)
        if not refs:
            ctx.expanded_contacts.discard(idx)
            ctx.contact_snapshots.pop(idx, None)
            return True
        inp_name, inp_phone, inp_email, opd_w, egrn_w, mem_w = refs
        name = inp_name.text().strip()
        if name:
            owners = ownership.group_owners(ctx.group)
            is_dup = any(
                j != idx and isinstance(ow, dict)
                and people_reg.norm_name(ownership.owner_name(ow)) == people_reg.norm_name(name)
                for j, ow in enumerate(owners))
            if is_dup:
                inp_name.setStyleSheet(_INPUT_ERROR_SS)
                inp_name.setFocus()

                def _clear_dup_err(_t="", w=inp_name):
                    w.setStyleSheet(_INPUT_SS)
                    try:
                        w.textChanged.disconnect(_clear_dup_err)
                    except (TypeError, RuntimeError):
                        pass

                inp_name.textChanged.connect(_clear_dup_err)
                return False
        if not name:
            has_other_data = bool(
                inp_phone.text().strip() or inp_email.text().strip()
                or opd_w.get_path() or egrn_w.get_path() or mem_w.get_path())
            if has_other_data:
                inp_name.setStyleSheet(_INPUT_ERROR_SS)
                inp_name.setFocus()

                def _clear_err(_t="", w=inp_name):
                    w.setStyleSheet(_INPUT_SS)
                    try:
                        w.textChanged.disconnect(_clear_err)
                    except (TypeError, RuntimeError):
                        pass

                inp_name.textChanged.connect(_clear_err)
                return False
            ctx.contact_input_refs.pop(idx, None)
            ctx.contact_snapshots.pop(idx, None)
            ctx.expanded_contacts.discard(idx)
            self._delete_contact_silently(ctx, idx)
            return True
        ctx.contact_input_refs.pop(idx, None)
        ctx.contact_snapshots.pop(idx, None)
        ctx.expanded_contacts.discard(idx)
        self._save_inline_contact(
            ctx, idx, name, inp_phone.text(), inp_email.text(),
            opd_w.get_path(), egrn_w.get_path(), mem_w.get_path())
        return True

    def _set_inline_role(self, ctx: "_GroupCardCtx", owner: dict, role_key: str,
                         tags: list[tuple[str, QPushButton]], star_btn: QPushButton):
        """Устанавливает роль контакта (radio-логика) и обновляет вид in-place."""
        if not isinstance(owner, dict):
            return
        owner["is_owner"] = (role_key == "owner")
        owner["is_member"] = (role_key == "member")
        self._group_dirty = True
        for rk, btn in tags:
            is_active = (rk == role_key)
            btn.setChecked(is_active)
        # Обновить цвет фона звезды-«избранного» под новую роль
        role_bg, role_fg = _ROLE_COLORS[role_key]
        star_btn._role_bg = role_bg
        star_btn._role_fg = role_fg
        is_primary = bool(owner.get("is_visible"))
        star_btn.setStyleSheet(
            f"QPushButton{{background:{role_bg};border:none;border-radius:9px;"
            f"padding:0;color:{'#07414F' if is_primary else role_fg};}}")
        # Обновить обязательность документов для развёрнутой карточки
        idx = None
        owners = ctx.group.get("owners", []) or []
        for i, o in enumerate(owners):
            if o is owner:
                idx = i
                break
        if idx is not None:
            refs = ctx.contact_input_refs.get(idx)
            if refs and len(refs) == 6:
                _req = _DOC_REQUIRED[role_key]
                refs[3].set_required(_req["opd"])
                refs[4].set_required(_req["egrn"])
                refs[5].set_required(_req["member"])

    def _set_inline_primary(self, ctx: "_GroupCardCtx", idx: int):
        """Делает контакт по индексу избранным (is_visible=True), остальные — нет."""
        owners = ctx.group.get("owners", []) or []
        for i, o in enumerate(owners):
            if isinstance(o, dict):
                o["is_visible"] = (i == idx)
        self._group_dirty = True
        # Обновить ФИО в шапке группы
        main = owners[idx] if 0 <= idx < len(owners) else None
        ctx.name_lbl.setText(
            ownership.owner_name(main) if isinstance(main, dict) else "(нет лиц)")
        self._refresh_contacts_preview(ctx)

    def _delete_contact_silently(self, ctx: "_GroupCardCtx", idx: int):
        """Удаляет контакт по индексу без диалога подтверждения
        (используется для автосброса незаполненного черновика).

        Если удалённый был избранным (или после удаления остался ровно один
        контакт) — новый избранный назначается в _refresh_group_card
        (см. _ensure_single_primary)."""
        owners = ctx.group.get("owners", []) or []
        if idx < 0 or idx >= len(owners):
            return
        owners.pop(idx)
        self._group_dirty = True
        ctx.expanded_contacts.clear()
        ctx.contact_input_refs.clear()
        ctx.contact_snapshots.clear()
        self._refresh_group_card(ctx)
        self._update_save_state()

    def _remove_contact_from_list(self, ctx: "_GroupCardCtx", idx: int):
        """Убирает контакт из списка ЭТОЙ группы — с подтверждением, но без
        удаления чего-либо: сам человек и его документы (в т.ч. ОПД и заявление
        в СНТ, закэшированные в реестре людей) не затрагиваются и остаются
        доступны для повторного добавления. В отличие от _delete_contact —
        необратимой операции."""
        owners = ctx.group.get("owners", []) or []
        if idx < 0 or idx >= len(owners):
            return
        o = owners[idx]
        name = ownership.owner_name(o) if isinstance(o, dict) else ""
        confirmed = _ConfirmDialog.confirm(
            self,
            f"Убрать «{name}» из списка?" if name else "Убрать этот контакт из списка?",
            "Контакт останется в реестре людей — его данные и документы "
            "не удаляются, его можно будет добавить снова.",
            confirm_text="Убрать")
        if not confirmed:
            return
        self._delete_contact_silently(ctx, idx)

    def _delete_contact(self, ctx: "_GroupCardCtx", idx: int):
        """Полностью и безвозвратно удаляет человека — с подтверждением.

        Убирает его отовсюду: из списка ЭТОЙ группы, из реестра людей, и (через
        сигнал personDeleted) со ВСЕХ остальных участков, где он указан —
        вместе со всеми его документами (ОПД, заявление в СНТ — они одни на
        человека; и Выписками ЕГРН — они у каждого участка свои, поэтому
        удаляются по одной на каждом участке, включая этот).

        Если контакт ещё не сохранён (нет person_id — например, только что
        добавленный пустой черновик), в реестре и на других участках удалять
        нечего — просто убирается запись на этом участке."""
        owners = ctx.group.get("owners", []) or []
        if idx < 0 or idx >= len(owners):
            return
        o = owners[idx]
        if not isinstance(o, dict):
            o = {}
        name = ownership.owner_name(o)
        confirmed = _ConfirmDialog.confirm(
            self,
            f"Удалить «{name}» полностью?" if name else "Удалить этот контакт?",
            "Человек и все его документы (ОПД, заявление в СНТ, выписки ЕГРН "
            "на всех участках) будут удалены безвозвратно.",
            confirm_text="Да, удалить")
        if not confirmed:
            return
        person_id = o.get("person_id")
        person = people_reg.get(self._people, person_id) if person_id else None
        if person:
            for field in ("opd_doc", "member_doc"):
                path = person.get(field, "")
                if path:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            self._people[:] = [p for p in self._people if p is not person]
            try:
                people_reg.save_people(self._people)
            except Exception:
                pass
        egrn_path = o.get("egrn_doc", "")
        if egrn_path:
            try:
                os.remove(egrn_path)
            except OSError:
                pass
        self._delete_contact_silently(ctx, idx)
        if person_id:
            self.personDeleted.emit(person_id)

    def _on_add_contact(self, ctx: "_GroupCardCtx"):
        """Добавляет пустой контакт и сразу открывает его для заполнения."""
        if not self._confirm_pending_contact_edits():
            return
        owners = ctx.group.setdefault("owners", [])
        owners.append(_make_owner("", is_owner=False))
        self._group_dirty = True
        ctx.expanded_contacts.clear()
        ctx.contact_input_refs.clear()
        ctx.contact_snapshots.clear()
        ctx.expanded_contacts.add(len(owners) - 1)
        ctx.contacts_expanded = True
        self._refresh_group_card(ctx)
        self._update_save_state()

    def _on_inline_name_committed(self, owner, inp_name: "QLineEdit",
                                  inp_phone: "QLineEdit", inp_email: "QLineEdit",
                                  opd_w: "_DocFieldWidget", mem_w: "_DocFieldWidget"):
        """ФИО введено/выбрано в инлайн-карточке → привязать человека из
        реестра и подставить пустые телефон/email/ОПД/заявление из реестра
        (переиспользование). ЕГРН не подставляется — она привязана к
        конкретному объекту, а не к человеку. Аналог _on_name_committed
        для старого редактора.

        Если ФИО совпало с ДРУГИМ существующим человеком в реестре — переключаем
        привязку (сначала стираем то, что было автоподставлено от ПРЕЖНЕГО
        человека, сравнивая с его данными в реестре, чтобы не затереть то, что
        пользователь ввёл вручную).

        Если совпадений нет вообще — считаем, что это ТОТ ЖЕ человек, которому
        просто исправляют ФИО (опечатка и т.п.): person_id НЕ трогаем, а само
        переименование запишется в его запись реестра при сохранении карточки
        (_save_inline_contact). Раньше здесь безусловно отвязывали person_id
        при любом несовпадении — из-за этого правка ФИО существующего контакта
        создавала в реестре ДУБЛЬ вместо переименования."""
        if not isinstance(owner, dict):
            return
        name = inp_name.text().strip()
        if not name:
            owner.pop("person_id", None)
            return
        new_person = people_reg.find_by_name(self._people, name)
        old_person_id = owner.get("person_id")
        new_person_id = new_person.get("id") if new_person else None
        if new_person_id and new_person_id != old_person_id:
            old_person = people_reg.get(self._people, old_person_id)
            if old_person:
                if (inp_phone.text().strip() and old_person.get("phone")
                        and _normalize_phone(old_person["phone"]) == inp_phone.text().strip()):
                    inp_phone.clear()
                if (inp_email.text().strip() and old_person.get("email")
                        and old_person["email"] == inp_email.text().strip()):
                    inp_email.clear()
                if opd_w.get_path() and opd_w.get_path() == old_person.get("opd_doc"):
                    opd_w.delete_path()
                    owner.pop("opd_doc", None)
                if mem_w.get_path() and mem_w.get_path() == old_person.get("member_doc"):
                    mem_w.delete_path()
                    owner.pop("member_doc", None)
            owner["person_id"] = new_person_id
            if not inp_phone.text().strip() and new_person.get("phone"):
                inp_phone.setText(_normalize_phone(new_person["phone"]))
            if not inp_email.text().strip() and new_person.get("email"):
                inp_email.setText(new_person.get("email", ""))
            if not opd_w.get_path() and new_person.get("opd_doc"):
                opd_w.set_path(new_person["opd_doc"])
                owner["opd_doc"] = new_person["opd_doc"]
            if not mem_w.get_path() and new_person.get("member_doc"):
                mem_w.set_path(new_person["member_doc"])
                owner["member_doc"] = new_person["member_doc"]

    def _save_inline_contact(self, ctx: "_GroupCardCtx", idx: int, name: str, phone: str,
                             email: str, opd_doc: str = "", egrn_doc: str = "",
                             member_doc: str = ""):
        """Сохраняет отредактированные ФИО/телефон/email контакта по индексу."""
        owners = ctx.group.get("owners", []) or []
        if idx < 0 or idx >= len(owners):
            return
        o = owners[idx]
        if not isinstance(o, dict):
            return
        name = name.strip()
        phone = phone.strip()
        email = email.strip()
        # Значения ДО перезаписи — чтобы отличить «поле было и его явно очистили»
        # от «поле изначально пустое», см. ниже сброс кэша реестра.
        prev_phone = str(o.get("phone", ""))
        prev_email = str(o.get("email", ""))
        prev_opd = str(o.get("opd_doc", ""))
        prev_member = str(o.get("member_doc", ""))
        o["name"] = name
        o["phone"] = phone
        o["email"] = email

        # Привязка к человеку в реестре: по уже известному person_id, иначе
        # по совпадению ФИО, иначе — регистрируем нового (как в _on_accept
        # старого редактора), чтобы он был доступен для автодополнения впредь.
        person = people_reg.get(self._people, o.get("person_id"))
        if person is None and name:
            person = people_reg.find_by_name(self._people, name)
        registry_changed = False
        if person is None and name:
            person = people_reg.create_person(
                name, phone, email, opd_doc=opd_doc, member_doc=member_doc)
            self._people.append(person)
            registry_changed = True
        if person:
            o["person_id"] = person["id"]
            # ФИО тоже не зависит от участка: если это переименование того же
            # человека (person_id не менялся, см. _on_inline_name_committed),
            # проносим новое имя и в реестр — иначе его старые появления на
            # других участках продолжат показывать старое ФИО.
            if name and person.get("name") != name:
                person["name"] = name
                registry_changed = True
            # Телефон/email/ОПД/заявление в СНТ не зависят от участка —
            # кэшируем в реестре, чтобы подтягивать их для этого человека
            # где угодно (в отличие от ЕГРН, привязанной к конкретному объекту).
            if phone and person.get("phone") != phone:
                person["phone"] = phone
                registry_changed = True
            elif not phone and prev_phone and person.get("phone") == prev_phone:
                # Явно очистили ранее заполненное поле — забываем и в реестре,
                # иначе оно тут же вернётся при следующем выборе этого ФИО.
                person.pop("phone", None)
                registry_changed = True
            if email and person.get("email") != email:
                person["email"] = email
                registry_changed = True
            elif not email and prev_email and person.get("email") == prev_email:
                person.pop("email", None)
                registry_changed = True
            if opd_doc and person.get("opd_doc") != opd_doc:
                person["opd_doc"] = opd_doc
                registry_changed = True
            elif not opd_doc and prev_opd and person.get("opd_doc") == prev_opd:
                person.pop("opd_doc", None)
                registry_changed = True
            if member_doc and person.get("member_doc") != member_doc:
                person["member_doc"] = member_doc
                registry_changed = True
            elif not member_doc and prev_member and person.get("member_doc") == prev_member:
                person.pop("member_doc", None)
                registry_changed = True
        elif "person_id" in o:
            del o["person_id"]
        if registry_changed:
            try:
                people_reg.save_people(self._people)
            except Exception:
                pass

        if opd_doc:
            o["opd_doc"] = opd_doc
        elif "opd_doc" in o:
            del o["opd_doc"]
        if egrn_doc:
            o["egrn_doc"] = egrn_doc
        elif "egrn_doc" in o:
            del o["egrn_doc"]
        if member_doc:
            o["member_doc"] = member_doc
        elif "member_doc" in o:
            del o["member_doc"]
        self._group_dirty = True
        # Обновить ФИО в шапке, если это избранный контакт
        if o.get("is_visible"):
            ctx.name_lbl.setText(name or "(нет имени)")
        self._refresh_contacts_preview(ctx)
        self._update_save_state()

    def _refresh_prev_section(self):
        """Перестраивает секцию «Предыдущие группы»: для каждой архивной
        группы создаёт рабочую копию (см. докстринг класса о «Отмена») и
        полноценный блок тем же билдером, что и активная группа."""
        archived = ownership.archived_groups({"groups": self._groups})
        self._prev_section.setVisible(bool(archived))
        self._prev_sep_line.setVisible(bool(archived))
        # Очищаем и перестраиваем все карточки предыдущих групп. Своей
        # scroll-области больше нет — карточки просто идут одна под другой
        # и скроллятся вместе со всей панелью (см. _drawer_scroll в
        # PlotsWidget), поэтому фиксировать/подгонять высоту не нужно.
        while self._prev_card_lyt.count():
            item = self._prev_card_lyt.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._archived_cards = []
        for g in archived:
            copy = dict(g)
            copy["owners"] = list(g.get("owners", []) or [])
            ctx = _GroupCardCtx(copy, is_active=False, orig=g)
            block = QWidget()
            block.setStyleSheet("background:transparent;")
            block_lyt = QVBoxLayout(block)
            block_lyt.setContentsMargins(0, 0, 0, 0)
            block_lyt.setSpacing(4)
            self._build_group_card(ctx, block_lyt, "Архивная группа")
            self._refresh_group_card(ctx)
            self._prev_card_lyt.addWidget(block)
            self._archived_cards.append(ctx)

    def _on_edit_active_group(self):
        """Открывает редактор контактов (под-панель)."""
        self._open_contacts(("active", None), self._active_group, is_new=False)

    def _on_toggle_contacts_full(self, ctx: "_GroupCardCtx"):
        """Переключает превью (3) ↔ полный список.

        Свёртывание в превью может унести за пределы видимости открытую
        карточку (если она за пределами первых трёх) — тогда её виджеты будут
        уничтожены и восстановить несохранённый ввод будет неоткуда, поэтому
        сначала спрашиваем через тот же диалог, что и при закрытии участка."""
        if not self._confirm_pending_contact_edits():
            return
        ctx.contacts_expanded = not ctx.contacts_expanded
        self._refresh_contacts_preview(ctx)

    def _on_archive_active_group(self):
        """«Создать новую группу»: текущая активная группа уходит в архив,
        на её месте — пустая новая, сразу редактируемая инлайн в этой же
        карточке (как активная группа при создании нового участка) — без
        перехода на отдельный экран.

        Кнопка неактивна, пока в активной группе нет ни одного лица (см.
        _refresh_group_card) — проверка ниже оставлена только как защита от
        рассинхронизации состояния кнопки, без модального предупреждения."""
        if not ownership.group_owners(self._active_group):
            return
        confirmed, contact_name, since = _NewGroupDialog.ask(
            self, "Создать новую группу?",
            "Текущая активная группа будет перемещена в архив (с долгом на "
            "дату начала новой группы). Укажите дату начала и ФИО первого "
            "контакта новой группы.",
            self._people, min_since=ownership.group_since(self._active_group))
        if not confirmed:
            return
        # Дата закрытия текущей = дата начала новой (выбранная в диалоге).
        # contact_name гарантированно не пуст — кнопка «Создать» неактивна
        # при пустом ФИО (см. _NewGroupDialog), поэтому владелец здесь есть
        # всегда.
        owner = _make_owner(contact_name, is_owner=False)
        owner["person_id"] = self._link_or_create_person(contact_name)
        new_active = {"since": since.isoformat(), "until": None, "owners": [owner]}
        self._apply_replace(new_active)

    def _link_or_create_person(self, name: str) -> str:
        """Находит человека в реестре по ФИО или регистрирует нового —
        та же логика привязки, что и при инлайн-сохранении контакта
        (см. _save_inline_contact), чтобы имя сразу попало в реестр людей
        (автодополнение/переиспользование на других участках)."""
        person = people_reg.find_by_name(self._people, name)
        if person is None:
            person = people_reg.create_person(name, "", "")
            self._people.append(person)
            try:
                people_reg.save_people(self._people)
            except Exception:
                pass
        return person["id"]

    # ── Под-панель «Список контактов» (Фаза 2) ───────────────────────────
    def _open_contacts(self, ctx, group: dict, is_new: bool):
        """Показывает редактор состава группы как под-панель (вместо модалки)."""
        self._contacts_ctx = ctx
        panel = GroupEditDialog(group, is_new=is_new, parent=self)
        panel.closed.connect(self._on_contacts_closed)
        self._contacts_panel = panel
        while self._contacts_slot.count():
            it = self._contacts_slot.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._contacts_slot.addWidget(panel)
        self._detail_view.setVisible(False)
        self._contacts_view.setVisible(True)

    def _on_contacts_back(self):
        if self._contacts_panel is not None:
            self._contacts_panel.back()

    def _on_contacts_closed(self, saved: bool):
        panel = self._contacts_panel
        ctx = self._contacts_ctx or ("active", None)
        if saved and panel is not None:
            result = panel.get_result()
            kind = ctx[0]
            if kind == "active":
                self._active_group = result
                self._active_ctx.group = result
                self._group_dirty = True
                self._refresh_group_card(self._active_ctx)
                self._update_save_state()
            elif kind == "replace":
                self._apply_replace(result)
        # Вернуться к детали, убрать панель контактов
        self._contacts_view.setVisible(False)
        self._detail_view.setVisible(True)
        while self._contacts_slot.count():
            it = self._contacts_slot.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._contacts_panel = None
        self._contacts_ctx = None

    def _apply_replace(self, new_active: dict):
        """Архивирует текущую активную группу и ставит новую на её место."""
        since_new = ownership.group_since(new_active)
        exit_date = since_new or date.today()
        if since_new is None:
            new_active["since"] = exit_date.isoformat()
        debt_at_close = self._compute_group_debt(exit_date)
        archived = dict(self._active_group)
        archived["until"] = exit_date.isoformat()
        archived["debt_at_close"] = debt_at_close
        updated = []
        for g in self._groups:
            if g.get("until") is None:
                updated.append(archived)
            else:
                updated.append(g)
        updated.append(new_active)
        self._groups = updated
        self._active_group = new_active
        self._active_ctx.group = new_active
        self._group_dirty = True
        self._refresh_group_card(self._active_ctx)
        self._refresh_prev_section()
        self._update_save_state()

    def _compute_group_debt(self, as_of, group: dict | None = None) -> dict:
        """Долг по группе на дату as_of. group по умолчанию — активная
        (для случая архивации); при сдвиге даты уже архивной группы (см.
        _on_since_changed) вызывается явно с этой архивной группой."""
        result = {"vznosy": 0.0, "energy": 0.0}
        plot_num = str(self._plot_data.get("num", ""))
        if not plot_num:
            return result
        since = ownership.group_since(group if group is not None else self._active_group)
        try:
            from core import vznosy as vzn
            rates = vzn.load_rates()
            adj = vzn.load_adjustments()
            area = vzn.plot_area_map().get(plot_num)
            gb = vzn.balance_for_active_group(
                plot_num, area, as_of, rates, adj, self._df, since=since)
            result["vznosy"] = round(gb.debt, 2)
        except Exception:
            pass
        try:
            from core import energy as en
            meters = en.load_meters()
            en_rates = en.load_rates()
            replacements = en.load_replacements()
            baseline = en.load_baseline()
            egb = en.balance_for_active_group(
                plot_num, as_of, meters, en_rates, replacements,
                baseline, self._df, since=since)
            result["energy"] = round(egb.debt, 2)
        except Exception:
            pass
        return result

    def _is_num_taken(self) -> bool:
        current = self.inp_num.text().strip()
        return bool(current and current in self._existing_nums)

    def _has_named_owner(self) -> bool:
        """Есть ли в активной группе хотя бы один контакт с непустым ФИО.

        Отличается от простой проверки «список owners не пуст»: пустой
        черновик, добавленный кнопкой «Добавить контакт», но так и не
        заполненный/сохранённый, тоже попадает в owners (см. _on_add_contact) —
        без этой проверки участок можно было создать с таким «пустым»
        контактом, который выглядит как сохранённый, а на деле не заполнен."""
        return any(isinstance(o, dict) and ownership.owner_name(o)
                   for o in ownership.group_owners(self._active_group))

    def _update_save_state(self):
        if self._btn_save is None or self._is_edit:
            return
        num_ok = bool(self.inp_num.text().strip()) and not self._is_num_taken()
        ok = self._has_named_owner() and num_ok
        self._btn_save.setEnabled(ok)
        self._btn_save.setCursor(
            Qt.CursorShape.PointingHandCursor if ok else Qt.CursorShape.ArrowCursor)
        # Иконка-галочка: активная (бирюза) когда можно сохранить, иначе серая
        self._btn_save.setIcon(
            _mat_icon(0xE161, 18, fill=1, color="#07414F" if ok else "#9CA3AF"))

    def _on_accept(self):
        """Сохранение нового участка (кнопка «Сохранить» в футере)."""
        # Открытая карточка контакта с несохранёнными правками — сначала
        # спрашиваем (см. _attempt_close_contact), иначе «Создать» могло
        # тихо отбросить только что введённые, но не сохранённые ФИО/контакты.
        if not self._confirm_pending_contact_edits():
            return
        num = self.inp_num.text().strip()
        if not num:
            QMessageBox.warning(self, "Ошибка", "Укажите номер участка")
            return
        area_raw = self.inp_area.text().strip().replace(",", ".")
        if area_raw:
            try:
                v = float(area_raw)
                if v <= 0:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(self, "Ошибка",
                                    "Площадь должна быть положительным числом")
                return
        if not self._has_named_owner():
            QMessageBox.warning(self, "Ошибка",
                                "В активной группе должно быть хотя бы одно лицо с заполненным ФИО")
            return
        self._build_result()
        self._finish(True)

    def get_result(self) -> dict:
        return getattr(self, "_result", {})

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog { background: #FFFFFF; color: #1F2937; }
            QLabel  { background: transparent; color: #1F2937; font-size: 13px; }
            QLineEdit {
                background: #F8F9FA; border: 1px solid #D1D5DB;
                border-radius: 5px; color: #1F2937; padding: 7px 10px; font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid #07414F; }
            QLineEdit:read-only {
                background: transparent; border: 1px solid transparent;
                color: #1F2937; font-weight: 600;
            }
            QPushButton#btnSecondary {
                background: #E5E7EB; color: #6B7280; border: 1px solid #D1D5DB;
                border-radius: 6px; padding: 8px 14px; font-size: 13px;
            }
            QPushButton#btnSecondary:hover { background: #D1D5DB; color: #374151; }
            QPushButton#btnEditMode {
                background: transparent; color: #07414F;
                border: 1.5px solid #C9D8E2; border-radius: 6px;
                padding: 5px 14px; font-size: 12px;
            }
            QPushButton#btnEditMode:hover { background: #EBF4F6; border-color: #07414F; }
            QPushButton#btnFooterSave {
                background: #07414F; color: white; border: none;
                border-radius: 6px; padding: 8px 20px; font-size: 13px; font-weight: 600;
            }
            QPushButton#btnFooterSave:hover { background: #0B5A6E; }
            QPushButton#btnFooterSave:disabled { background: #E5E7EB; color: #9CA3AF; }
            QPushButton#btnFooterClose, QPushButton#btnFooterCancel {
                background: #E5E7EB; color: #6B7280; border: 1px solid #D1D5DB;
                border-radius: 6px; padding: 8px 20px; font-size: 13px;
            }
            QPushButton#btnFooterClose:hover, QPushButton#btnFooterCancel:hover {
                background: #D1D5DB; color: #374151;
            }
            QPushButton {
                background: #F8F9FA; border: 1px solid #D1D5DB;
                border-radius: 5px; color: #07414F; font-size: 14px;
            }
            QPushButton:hover { background: #E8F0F5; border: 1px solid #07414F; }
            QPushButton:pressed { background: #D5E5ED; }
        """)
