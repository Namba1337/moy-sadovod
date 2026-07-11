import json
import os
import re
import shutil
from datetime import date

import pandas as pd
from PyQt6.QtCore import (
    Qt, QDate, QEvent, QModelIndex, QAbstractItemModel, QAbstractListModel,
    QObject, QPoint, QRect, QRectF,
    QRegularExpression, QSize, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QBitmap, QColor, QFont, QFontMetrics, QIcon, QPainter, QPen,
    QPixmap, QPolygon, QRegion, QRegularExpressionValidator,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QDateEdit, QDialog,
    QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QCompleter, QListView, QMessageBox, QPushButton, QScrollArea, QScrollBar, QSizePolicy, QStyle, QStyledItemDelegate,
    QStyleFactory,
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

# Общая инфраструктура вынесена в ui.common / ui.dialogs; здесь остаются
# алиасы прежних приватных имён — их импортируют другие вкладки.
from ui.common import (                                     # noqa: E402
    AppTooltip as _AppTooltip,
    TooltipFilter as _TooltipFilter,
    FlatTableModel as _FlatTableModel,
    SortHeaderView as _SortHeaderView,
    ClipFrame as _ClipFrame,
    BorderOverlay as _BorderOverlay,
    TREE_STYLE as _TREE_STYLE,
    SB_W as _SB_W,
    INPUT_SS as _INPUT_SS,
    INPUT_ERROR_SS as _INPUT_ERROR_SS,
    FIELD_LABEL_SS as _FIELD_LABEL_SS,
    CalendarArrowFlip,
    style_date_popup,
)
from ui.dialogs import (                                    # noqa: E402
    AlertDialog as _AlertDialog,
    PromptDialog as _BasePromptDialog,
    ConfirmDialog as _ConfirmDialog,
    Save3WayDialog as _Save3WayDialog,
    exec_dialog as _exec_dialog,
)
from ui.buttons import (                                    # noqa: E402
    DangerButton, PrimaryButton, SecondaryButton,
)


# Базовые аксессоры — единый источник логики в core.ownership
# (обратная совместимость со строками и старым полем relation внутри).
_owner_name = ownership.owner_name
_is_owner = ownership.is_owner
_owner_area = ownership.owner_area


def _is_visible(owner) -> bool:
    if isinstance(owner, dict):
        return bool(owner.get("is_visible", False))
    return False


# Рендер иконок вынесен в ui.icons (единственная реализация оверсэмплинга);
# здесь остаются тонкие обёртки под прежние сигнатуры.
from ui.icons import (                                      # noqa: E402
    icon_font as _icon_font_impl, get_icon as _get_icon_impl, icon_png_path,
)


def _mat_font(pixel_size: int = 20, fill: int = 0) -> QFont:
    """Material Symbols Rounded с нужным FILL axis (0=outline, 1=filled)."""
    return _icon_font_impl(pixel_size, fill)


def _mat_icon(codepoint: int, size: int = 16, fill: int = 0,
              color: str = "#07414F") -> QIcon:
    """Рендерит символ Material Symbols в QIcon — см. ui.icons.get_icon."""
    return _get_icon_impl(codepoint, size, color=color, fill=fill)


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


# _AppTooltip, _TooltipFilter → ui.common (импортируются выше).
# _BasePromptDialog, _ConfirmDialog, _Save3WayDialog → ui.dialogs.



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
                         cancel_text=cancel_text, danger=False, parent=parent)

        date_col = QVBoxLayout()
        date_col.setSpacing(2)
        date_col.addWidget(QLabel("Дата начала", styleSheet=_FIELD_LABEL_SS))
        self.inp_since = QDateEdit(calendarPopup=True, displayFormat="dd.MM.yyyy")
        style_date_popup(self.inp_since)
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
    (см. PlotsWidget._add_plot): номер участка и ФИО первого контакта
    обязательны, площадь — нет. Кнопка «Создать» активна только когда оба
    обязательных поля заполнены; номер, совпадающий с уже существующим
    участком, блокирует создание (пилюля «номер занят» — рядом с подписью
    поля, как и у дубля ФИО в карточке контакта). Номер обязателен, потому
    что весь остальной код использует его как ключ идентичности участка
    (выбор, кэш долга, массовое удаление) — участок без номера был бы
    неотличим от другого такого же."""

    def __init__(self, title: str, message: str, people: list,
                existing_nums: set, *, parent=None):
        super().__init__(title, message, confirm_text="Создать", danger=False,
                         parent=parent)
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
        num_ok = bool(num)
        self._confirm_btn.setEnabled(area_ok and name_ok and num_ok and not num_dup)

    @staticmethod
    def ask(parent, people: list, existing_nums: set):
        """Возвращает (num, area, name) при подтверждении, иначе None.
        area — None, если поле оставлено пустым (необязательное)."""
        dlg = _QuickAddPlotDialog(
            "Новый участок",
            "Номер участка и ФИО собственника обязательны — площадь можно "
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
    (читает участок через UserRole / plot_at). Долги — из внешнего кэша."""

    PlotRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list = []
        self._debt: dict = {"vznosy": {}, "energy": {}}
        self._selected: set[str] = set()   # num → selected
        self._show_overpay = True          # False — переплата (долг < 0) отображается как 0,00

    def set_plots(self, plots: list):
        self.beginResetModel()
        self._rows = list(plots)
        self.endResetModel()

    def set_debt_cache(self, debt: dict):
        self._debt = debt or {"vznosy": {}, "energy": {}}
        if self._rows:
            self.dataChanged.emit(self.index(0), self.index(len(self._rows) - 1))

    def set_show_overpay(self, show: bool):
        self._show_overpay = show
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

    def _fmt_debt(self, plot: dict, key: str) -> str:
        v = self._debt.get(key, {}).get(str(plot.get("num", "")))
        if v is None or abs(v) < 0.005:
            return "—"
        if v < 0 and not self._show_overpay:
            return fmt_money(0)
        return fmt_money(v)

    def vznosy_debt_text(self, plot: dict) -> str:
        return self._fmt_debt(plot, "vznosy")

    def energy_debt_text(self, plot: dict) -> str:
        return self._fmt_debt(plot, "energy")

    # -- selection ---------------------------------------------------------- #

    def is_selected(self, plot: dict) -> bool:
        return str(plot.get("num", "")) in self._selected

    def toggle_selection(self, plot: dict):
        num = str(plot.get("num", ""))
        if num in self._selected:
            self._selected.discard(num)
        else:
            self._selected.add(num)
        row = self.row_of(plot)
        if row >= 0:
            idx = self.index(row)
            self.dataChanged.emit(idx, idx)

    def select_all(self, plots: list):
        for p in plots:
            self._selected.add(str(p.get("num", "")))
        if self._rows:
            self.dataChanged.emit(self.index(0), self.index(len(self._rows) - 1))

    def deselect_all(self):
        if self._selected:
            self._selected.clear()
            if self._rows:
                self.dataChanged.emit(self.index(0), self.index(len(self._rows) - 1))

    def get_selected_nums(self) -> set[str]:
        return set(self._selected)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == self.PlotRole:
            return self._rows[index.row()]
        return None


class _PlotRowDelegate(QStyledItemDelegate):
    """Чистая строка списка: [x] № · ФИО · Членские взносы · Электроэнергия. Без сетки."""

    _ROW_H   = 36
    _CB_W    = 28
    _NUM_W   = 44
    _VZ_W    = 110
    _EN_W    = 110
    _PAD     = 14
    _FG_NUM  = QColor("#1F2937")
    _FG_NAME = QColor("#1F2937")
    _FG_DEBT = QColor("#374151")
    _FG_CB   = QColor("#07414F")
    _FG_CB_OFF = QColor("#C3CAD3")
    _SEL_BG  = QColor("#C9D8E2")
    _HOV_BG  = QColor("#EBF4F6")

    _IC_CHECKED   = chr(0xE834)   # check_box
    _IC_UNCHECKED = chr(0xE835)   # check_box_outline_blank
    _IC_FONT_NAME = "Material Symbols Rounded"

    def __init__(self, view):
        super().__init__(view)
        self._view = view
        self._hover_idx = QModelIndex()
        self._fill_tag = QFont.Tag.fromString("FILL")
        # Шрифты переиспользуются между вызовами paint() — пересоздание QFont
        # на каждую видимую строку при каждой перерисовке (скролл, hover)
        # заметно нагружает большой список.
        self._f_cb = QFont(self._IC_FONT_NAME)
        self._f_cb.setPixelSize(18)
        self._f_text = QFont()
        self._f_text.setPixelSize(13)
        self._fm_text = QFontMetrics(self._f_text)
        view.viewport().installEventFilter(self)

    def _cb_rect(self, cell_rect: QRect) -> QRect:
        """Прямоугольник чекбокса внутри строки."""
        cb_size = 18
        x = cell_rect.left() + self._PAD
        y = cell_rect.top() + (cell_rect.height() - cb_size) // 2
        return QRect(x, y, cb_size, cb_size)

    def _is_cb_hover(self, pos: "QPoint") -> bool:
        idx = self._view.indexAt(pos)
        if not idx.isValid():
            return False
        return self._cb_rect(self._view.visualRect(idx)).contains(pos)

    def eventFilter(self, obj, event):
        try:
            viewport = self._view.viewport()
        except RuntimeError:
            # self._view (C++-объект) уже удалён — обычно при закрытии
            # приложения, когда события ещё летят по хвосту очереди после
            # того, как сам QListView уничтожен. Просто игнорируем.
            return False
        if obj is viewport:
            if event.type() == QEvent.Type.MouseMove:
                pos = event.position().toPoint()
                new_hover = self._view.indexAt(pos)
                if new_hover != self._hover_idx:
                    old = self._hover_idx
                    self._hover_idx = new_hover
                    if old.isValid():
                        self._view.viewport().update(self._view.visualRect(old))
                    if new_hover.isValid():
                        self._view.viewport().update(self._view.visualRect(new_hover))
                if self._is_cb_hover(pos):
                    self._view.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
                else:
                    self._view.viewport().unsetCursor()
            elif event.type() == QEvent.Type.Leave:
                if self._hover_idx.isValid():
                    old = self._hover_idx
                    self._hover_idx = QModelIndex()
                    self._view.viewport().update(self._view.visualRect(old))
                self._view.viewport().unsetCursor()
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.LeftButton:
                    pos = event.position().toPoint()
                    idx = self._view.indexAt(pos)
                    if idx.isValid() and self._cb_rect(self._view.visualRect(idx)).contains(pos):
                        model = idx.model()
                        plot = model.plot_at(idx.row())
                        if plot is not None:
                            model.toggle_selection(plot)
                            return True
        return super().eventFilter(obj, event)

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

        # Чекбокс (слева)
        is_checked = model.is_selected(plot)
        cb_r = self._cb_rect(rect)
        f_cb = self._f_cb
        f_cb.setVariableAxis(self._fill_tag, 1.0 if is_checked else 0.0)
        painter.setFont(f_cb)
        painter.setPen(self._FG_CB if is_checked else self._FG_CB_OFF)
        painter.drawText(cb_r, Qt.AlignmentFlag.AlignCenter,
                         self._IC_CHECKED if is_checked else self._IC_UNCHECKED)

        # Сдвигаем весь контент вправо на ширину чекбокса + зазор
        content_left = rect.left() + self._PAD + self._CB_W

        f = self._f_text
        painter.setFont(f)
        vtop = rect.top() - 1
        # №
        painter.setPen(self._FG_NUM)
        num_rect = QRect(content_left, vtop, self._NUM_W, rect.height())
        painter.drawText(num_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         str(plot.get("num", "")))
        # Электроэнергия (справа)
        en_rect = QRect(rect.right() - self._PAD - self._EN_W, vtop,
                        self._EN_W, rect.height())
        painter.setPen(self._FG_DEBT)
        painter.drawText(en_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         model.energy_debt_text(plot))
        # Членские взносы
        vz_rect = QRect(en_rect.left() - 8 - self._VZ_W, vtop,
                        self._VZ_W, rect.height())
        painter.setPen(self._FG_DEBT)
        painter.drawText(vz_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         model.vznosy_debt_text(plot))
        # ФИО (между № и членскими взносами)
        name_left = content_left + self._NUM_W + 8
        name_rect = QRect(name_left, vtop, vz_rect.left() - name_left - 8, rect.height())
        name = _short_name(_plot_primary_name(plot)) or "—"
        elided = self._fm_text.elidedText(name, Qt.TextElideMode.ElideRight, name_rect.width())
        painter.setPen(self._FG_NAME)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         elided)
        painter.restore()


class _FilterTabButton(QPushButton):
    """Вкладка-фильтр списка участков: «Текст [иконка] · счётчик» с нижним
    подчёркиванием активного состояния.

    Рисуется вручную по двум причинам: QPushButton не умеет иконку между
    текстом и счётчиком, а QSS font-weight:700 активной вкладки не попадает
    в sizeHint — жирный текст обрезался справа. Ширина резервируется по
    жирному начертанию и контент центрируется, поэтому при переключении
    вкладки ничего не режется и соседи не прыгают.
    """

    _PAD_H, _PAD_TOP, _PAD_BOT = 2, 4, 8   # поля как в прежнем QSS padding
    _UNDERLINE = 2                          # прежний border-bottom
    _ICON_PX = 15                           # размер глифа-иконки
    _GAP = 4                                # зазор текст—иконка

    _CL_CHECKED = QColor("#07414F")
    _CL_HOVER   = QColor("#374151")
    _CL_NORMAL  = QColor("#6B7280")

    def __init__(self, label: str, icon_cp: int | None = None, parent=None):
        super().__init__(parent)
        self._label = label
        self._icon_cp = icon_cp
        self._count = ""
        self._icon_cache: dict[tuple[str, int], QPixmap] = {}
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        f = QFont(self.font())
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.Normal)
        self._f_norm = f
        fb = QFont(f)
        fb.setWeight(QFont.Weight.Bold)
        self._f_bold = fb

    def set_count(self, n: int):
        self._count = f" · {n}"
        self.updateGeometry()
        self.update()

    def _content_width(self, fm: QFontMetrics) -> int:
        w = fm.horizontalAdvance(self._label) + fm.horizontalAdvance(self._count)
        if self._icon_cp is not None:
            w += self._GAP + self._ICON_PX
        return w

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self._f_bold)   # резерв ширины под жирное начертание
        return QSize(self._PAD_H * 2 + self._content_width(fm),
                     self._PAD_TOP + fm.height() + self._PAD_BOT + self._UNDERLINE)

    minimumSizeHint = sizeHint

    def _icon_pm(self, color: str, fill: int) -> QPixmap:
        key = (color, fill)
        pm = self._icon_cache.get(key)
        if pm is None:
            pm = _mat_icon(self._icon_cp, self._ICON_PX, fill=fill,
                           color=color).pixmap(self._ICON_PX, self._ICON_PX)
            self._icon_cache[key] = pm
        return pm

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event):
        checked = self.isChecked()
        color = (self._CL_CHECKED if checked
                 else self._CL_HOVER if self.underMouse() else self._CL_NORMAL)
        font = self._f_bold if checked else self._f_norm
        fm = QFontMetrics(font)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(font)
        p.setPen(color)
        x = max(0, (self.width() - self._content_width(fm)) // 2)
        base = self._PAD_TOP + fm.ascent()
        p.drawText(x, base, self._label)
        x += fm.horizontalAdvance(self._label)
        if self._icon_cp is not None:
            x += self._GAP
            icon_y = self._PAD_TOP + (fm.height() - self._ICON_PX) // 2
            p.drawPixmap(x, icon_y, self._icon_pm(color.name(), 1 if checked else 0))
            x += self._ICON_PX
        if self._count:
            p.drawText(x, base, self._count)
        if checked:
            p.fillRect(0, self.height() - self._UNDERLINE, self.width(),
                       self._UNDERLINE, self._CL_CHECKED)
        p.end()


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
        # Реестр людей грузится один раз на время жизни виджета и передаётся
        # по ссылке в PlotEditDialog — иначе каждое открытие карточки участка
        # заново читало и парсило JSON с диска.
        self._people_cache: list = people_reg.load_people()
        self._df = None                       # выписка — нужна для долга в списке/детали
        self._search_text = ""
        self._filter_mode = "all"             # all | debtors | debtors_vznosy | debtors_energy
        self._sort_col: str | None = None     # num | name | vznosy | energy
        self._sort_asc = True
        self._debt_cache: dict = {}           # {"vznosy": {num: float}, "energy": {num: float}}
        self._setup_ui()
        self._rebuild_table()
        # Выровнять заглушку капшена под желоб скроллбара после раскладки
        QTimer.singleShot(0, self._sync_caption_stub)

    def refresh(self, df):
        """Принимает загруженную выписку: пересчёт долга и обновление списка."""
        self._df = df
        self._recompute_debt()
        self._rebuild_table()

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
        # Шапка списка: заголовок слева, действия списка — справа.
        # Счётчик и поиск переехали в панель групповых операций (см. ниже).
        list_hdr = QHBoxLayout()
        list_hdr.setSpacing(8)
        lbl_plots = QLabel("Участки")
        lbl_plots.setStyleSheet(
            "font-size:14px; font-weight:700; color:#1F2937; background:transparent;")
        list_hdr.addWidget(lbl_plots)
        list_hdr.addStretch()

        def _hdr_icon_btn(tooltip: str, handler) -> QPushButton:
            b = QPushButton()
            b.setFixedSize(32, 32)
            b.setIconSize(QSize(22, 22))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.installEventFilter(_TooltipFilter(tooltip, b))
            b.setStyleSheet(
                "QPushButton{background:transparent;border:none;border-radius:6px;}"
                "QPushButton:hover{background:#EBF4F6;}")
            b.clicked.connect(handler)
            return b

        # Массовые операции над выбранными участками — в одном ряду с
        # «Импорт»/«Добавить», слева от них. Активны только при выборе строк
        # (см. _refresh_toolbar); в disabled-состоянии Qt сам гасит иконку.
        self._btn_bulk_save = _hdr_icon_btn("Сохранить выбранные", self._on_bulk_save_stub)
        self._btn_bulk_save.setEnabled(False)
        list_hdr.addWidget(self._btn_bulk_save)
        self._btn_bulk_delete = _hdr_icon_btn("Удалить выбранные", self._bulk_delete)
        self._btn_bulk_delete.setEnabled(False)
        self._btn_bulk_delete.setStyleSheet(
            "QPushButton{background:transparent;border:none;border-radius:6px;}"
            "QPushButton:hover{background:#FEF2F2;}"
            "QPushButton:disabled{background:transparent;}")
        list_hdr.addWidget(self._btn_bulk_delete)
        self._btn_import = _hdr_icon_btn("Импорт из Excel", self._import_from_excel)
        list_hdr.addWidget(self._btn_import)
        self._btn_add = _hdr_icon_btn("Добавить участок", self._add_plot)
        list_hdr.addWidget(self._btn_add)
        QTimer.singleShot(0, self._refresh_icons)

        # ── Вкладки-фильтры: Все / Должники / Должники 💰 / Должники ⚡ ───────
        # Долги по взносам и электричеству различаются иконкой после слова
        # (money_bag / bolt), а не текстовым суффиксом «ЧВ»/«Эл».
        tabs_row = QHBoxLayout()
        tabs_row.setContentsMargins(0, 4, 0, 0)
        tabs_row.setSpacing(20)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self._tab_buttons: dict[str, _FilterTabButton] = {}
        self._TAB_LABELS = {
            "all":            ("Все", None),
            "debtors":        ("Должники", None),
            "debtors_vznosy": ("Должники", 0xF3EE),  # money_bag
            "debtors_energy": ("Должники", 0xEA0B),  # bolt
        }
        for mode, (label, icon_cp) in self._TAB_LABELS.items():
            btn = _FilterTabButton(label, icon_cp)
            btn.clicked.connect(lambda checked, m=mode: self._on_filter_tab(m))
            self._tab_group.addButton(btn)
            self._tab_buttons[mode] = btn
            tabs_row.addWidget(btn)
        tabs_row.addStretch()
        self._tab_buttons["all"].setChecked(True)

        # ── Панель групповых операций (всегда видима) ────────────────────────
        toolbar = QWidget()
        toolbar.setStyleSheet("background:transparent;")
        tb_l = QHBoxLayout(toolbar)
        tb_l.setContentsMargins(0, 4, 0, 4)
        tb_l.setSpacing(8)

        # Поиск — слева, тянется на всю свободную ширину ряда (stretch=1 ниже).
        self._search = QLineEdit()
        self._search.setPlaceholderText("Поиск по номеру или ФИО")
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(220)
        # Свой стиль вместо нативного: «подбородок» (нижняя линия), на фокусе —
        # бирюзовый вместо системно-синего. Толщина одинаковая, чтобы текст не прыгал.
        self._search.setStyleSheet(
            "QLineEdit{background:transparent;border:none;border-bottom:2px solid #D1D5DB;"
            "border-radius:0;padding:6px 2px;font-size:13px;color:#1F2937;}"
            "QLineEdit:focus{border-bottom:2px solid #07414F;}")
        self._search.textChanged.connect(self._on_search_text)
        tb_l.addWidget(self._search, stretch=1)

        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("font-size:12px; color:#9CA3AF; background:transparent;")
        tb_l.addWidget(self._count_lbl)

        self._selected_lbl = QLabel("")
        self._selected_lbl.setStyleSheet(
            "font-size:12px; color:#07414F; background:transparent; font-weight:600;")
        tb_l.addWidget(self._selected_lbl)

        # Переключатель «Показывать переплату» — справа; включён по умолчанию,
        # при выключении отрицательный долг (переплата) в таблице заменяется
        # на 0,00. Иконка — Material-тумблер toggle_on/toggle_off.
        self._chk_overpay = QPushButton(" Показывать переплату")
        self._chk_overpay.setCheckable(True)
        self._chk_overpay.setCursor(Qt.CursorShape.PointingHandCursor)
        self._chk_overpay.setIconSize(QSize(20, 20))
        self._chk_overpay.setStyleSheet(
            "QPushButton{background:transparent;border:none;border-radius:6px;"
            "padding:4px 8px;font-size:12px;color:#374151;}"
            "QPushButton:hover{background:#F3F4F6;}")
        self._chk_overpay.setChecked(True)
        self._refresh_overpay_icon()
        self._chk_overpay.toggled.connect(self._on_toggle_overpay)
        tb_l.addWidget(self._chk_overpay)

        self._toolbar = toolbar

        # Капшен колонок (геометрия ~ как в делегате)
        cap = QWidget()
        cap.setStyleSheet("background:transparent;")
        cap_l = QHBoxLayout(cap)
        cap_l.setContentsMargins(_PlotRowDelegate._PAD, 0, _PlotRowDelegate._PAD, 0)
        # Spacing 0: зазоры колонок задаются явными addSpacing(8) ровно там,
        # где их рисует делегат (_PlotRowDelegate.paint) — между чекбоксом и №
        # зазора нет, автоматический spacing сдвигал все заголовки на 8px.
        cap_l.setSpacing(0)
        # Master-чекбокс: глиф прижат влево, как в строках (_cb_rect — 18px
        # от левого края слота шириной _CB_W, а не по центру слота).
        _cb_font = QFont("Material Symbols Rounded")
        _cb_font.setPixelSize(18)
        self._master_cb = QLabel(chr(0xE835))  # check_box_outline_blank
        self._master_cb.setFont(_cb_font)
        self._master_cb.setStyleSheet("color:#C3CAD3; background:transparent;")
        self._master_cb.setFixedSize(_PlotRowDelegate._CB_W, _PlotRowDelegate._CB_W)
        self._master_cb.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._master_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self._master_cb.mousePressEvent = self._on_master_cb_click
        cap_l.addWidget(self._master_cb)
        cap_num = QLabel("№"); cap_num.setFixedWidth(_PlotRowDelegate._NUM_W)
        cap_name = QLabel("Контакт")
        cap_vz = QLabel("Член. взносы"); cap_vz.setFixedWidth(_PlotRowDelegate._VZ_W)
        cap_vz.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        cap_en = QLabel("Электроэнергия"); cap_en.setFixedWidth(_PlotRowDelegate._EN_W)
        cap_en.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # Заголовки кликабельны — сортировка по столбцу (как в примере-таблице).
        self._CAP_BASE = {"num": "№", "name": "Контакт",
                           "vznosy": "Член. взносы", "energy": "Электроэнергия"}
        self._cap_labels = {"num": cap_num, "name": cap_name,
                             "vznosy": cap_vz, "energy": cap_en}
        for key, c in self._cap_labels.items():
            c.setCursor(Qt.CursorShape.PointingHandCursor)
            c.mousePressEvent = lambda e, k=key: self._on_header_click(k)
        self._refresh_sort_headers()
        cap_l.addWidget(cap_num)
        cap_l.addSpacing(8)
        cap_l.addWidget(cap_name, stretch=1)
        cap_l.addSpacing(8)
        cap_l.addWidget(cap_vz)
        cap_l.addSpacing(8)
        cap_l.addWidget(cap_en)
        # Заглушка под скроллбар — чтобы колонки капшена совпали с данными
        # (делегат рисует в вьюпорте, уже на ширину желоба). Ширина выставляется
        # в _sync_caption_stub по фактической ширине желоба списка.
        self._cap_stub = QWidget()
        self._cap_stub.setStyleSheet("background:transparent;")
        cap_l.addWidget(self._cap_stub)

        self.list_model = PlotsListModel(self)
        self.list_model.dataChanged.connect(self._refresh_master_cb)
        self.list_model.dataChanged.connect(self._refresh_toolbar)
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
        table_vbox.addLayout(tabs_row)
        table_vbox.addWidget(self._toolbar)
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
                               existing_nums=existing, people=self._people_cache)
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

    def _refresh_icons(self):
        """Обновить иконки всех кнопок шапки списка одновременно."""
        self._btn_bulk_save.setIcon(_mat_icon(0xE161, 22, color="#9CA3AF"))    # save
        self._btn_bulk_delete.setIcon(_mat_icon(0xE92B, 22, color="#DC2626"))  # delete
        self._btn_import.setIcon(_mat_icon(0xEAF3, 22, color="#9CA3AF"))
        self._refresh_add_icon()

    _IC_CHECKED   = chr(0xE834)   # check_box
    _IC_UNCHECKED = chr(0xE835)   # check_box_outline_blank

    def _on_master_cb_click(self, event):
        selected = self.list_model.get_selected_nums()
        visible = self.list_model._rows
        if len(selected) >= len(visible) and len(visible) > 0:
            self.list_model.deselect_all()
        else:
            self.list_model.select_all(visible)
        self._refresh_master_cb()

    def _refresh_master_cb(self):
        selected = self.list_model.get_selected_nums()
        visible = self.list_model._rows
        if not visible:
            icon = self._IC_UNCHECKED
            color = "#C3CAD3"
        elif not selected:
            icon = self._IC_UNCHECKED
            color = "#C3CAD3"
        elif len(selected) >= len(visible):
            icon = self._IC_CHECKED
            color = "#07414F"
        else:
            icon = chr(0xE15B)  # remove (indeterminate)
            color = "#07414F"
        self._master_cb.setText(icon)
        self._master_cb.setStyleSheet(f"color:{color}; background:transparent;")

    def _refresh_toolbar(self):
        n = len(self.list_model.get_selected_nums())
        self._selected_lbl.setText(f"Выбрано: {n}" if n else "")
        self._btn_bulk_delete.setEnabled(n > 0)
        self._btn_bulk_save.setEnabled(n > 0)

    def _on_bulk_save_stub(self):
        """Заглушка — массовое сохранение выбранных участков будет добавлено позже."""
        pass

    def _refresh_overpay_icon(self):
        checked = self._chk_overpay.isChecked()
        cp = 0xE9F6 if checked else 0xE9F5  # toggle_on / toggle_off
        self._chk_overpay.setIcon(
            _mat_icon(cp, 20, fill=1 if checked else 0,
                      color="#07414F" if checked else "#9CA3AF"))

    def _on_toggle_overpay(self, checked: bool):
        self._refresh_overpay_icon()
        self.list_model.set_show_overpay(checked)

    def _is_debtor(self, plot: dict, key: str) -> bool:
        v = self._debt_cache.get(key, {}).get(str(plot.get("num", "")))
        return v is not None and v > 0.005

    def _on_filter_tab(self, mode: str):
        self._filter_mode = mode
        self._rebuild_table()

    def _refresh_tabs(self, base_plots: list):
        counts = {
            "all":            len(base_plots),
            "debtors":        sum(1 for p in base_plots
                                   if self._is_debtor(p, "vznosy") or self._is_debtor(p, "energy")),
            "debtors_vznosy": sum(1 for p in base_plots if self._is_debtor(p, "vznosy")),
            "debtors_energy": sum(1 for p in base_plots if self._is_debtor(p, "energy")),
        }
        for mode, btn in self._tab_buttons.items():
            btn.set_count(counts[mode])

    def _on_header_click(self, col: str):
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        self._refresh_sort_headers()
        self._rebuild_table()

    def _refresh_sort_headers(self):
        for key, lbl in self._cap_labels.items():
            base = self._CAP_BASE[key]
            if key == self._sort_col:
                arrow = " ↑" if self._sort_asc else " ↓"
                lbl.setText(base + arrow)
                lbl.setStyleSheet(
                    "font-size:11px; color:#07414F; font-weight:700; background:transparent;")
            else:
                lbl.setText(base)
                lbl.setStyleSheet("font-size:11px; color:#9CA3AF; background:transparent;")

    def _sort_key_for(self, col: str):
        if col == "num":
            def key(p):
                n = str(p.get("num", ""))
                try:
                    return (0, float(n))
                except ValueError:
                    return (1, n.lower())
            return key
        if col == "name":
            return lambda p: _plot_primary_name(p).lower()
        if col in ("vznosy", "energy"):
            return lambda p: self._debt_cache.get(col, {}).get(str(p.get("num", ""))) or 0.0
        return None

    def _bulk_delete(self):
        nums = self.list_model.get_selected_nums()
        if not nums:
            return
        confirmed = _ConfirmDialog.confirm(
            self, f"Удалить {len(nums)} участков?",
            "Будет удалена вся информация участков, включая документы. "
            "Это действие нельзя отменить.",
            confirm_text="Да, удалить")
        if not confirmed:
            return
        self._plots = [p for p in self._plots
                       if str(p.get("num", "")) not in nums]
        self.list_model.deselect_all()
        self._save()
        self._recompute_debt()
        self._rebuild_table()

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
        base = self._plots
        if text:
            base = [p for p in base
                    if text in str(p.get("num", "")).lower()
                    or any(text in nm.lower() for nm in _plot_search_names(p))]
        self._refresh_tabs(base)
        mode = self._filter_mode
        if mode == "debtors":
            plots = [p for p in base
                     if self._is_debtor(p, "vznosy") or self._is_debtor(p, "energy")]
        elif mode == "debtors_vznosy":
            plots = [p for p in base if self._is_debtor(p, "vznosy")]
        elif mode == "debtors_energy":
            plots = [p for p in base if self._is_debtor(p, "energy")]
        else:
            plots = base
        if self._sort_col:
            plots = sorted(plots, key=self._sort_key_for(self._sort_col),
                            reverse=not self._sort_asc)
        self.list_model.set_plots(plots)
        self.list_model.set_debt_cache(self._debt_cache)
        self._refresh_master_cb()
        self._refresh_toolbar()
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
        """Кэш долга по Членским взносам и Электроэнергии (отдельно) по всем участкам."""
        vznosy_debt: dict = {}
        energy_debt: dict = {}
        if self._df is not None:
            try:
                from core import vznosy as vzn
                from core import energy as en
                rates = vzn.load_rates(); adj = vzn.load_adjustments()
                area_map = vzn.plot_area_map()
                meters = en.load_meters(); en_rates = en.load_rates()
                repl = en.load_replacements(); base = en.load_baseline()
                today = date.today()
                # Индексы платежей — один проход по выписке на все участки
                vz_idx = vzn.payments_index(self._df)
                en_idx = en.payments_index(self._df, en.CATS_ELECTRO_INCOME)
                for p in self._plots:
                    num = str(p.get("num", ""))
                    since = ownership.group_since(ownership.active_group(p) or {})
                    try:
                        gb = vzn.balance_for_active_group(
                            num, area_map.get(num), today, rates, adj, self._df,
                            since=since, pay_index=vz_idx)
                        vznosy_debt[num] = gb.debt
                    except Exception:
                        pass
                    try:
                        egb = en.balance_for_active_group(
                            num, today, meters, en_rates, repl, base, self._df,
                            since=since, plots=self._plots, pay_index=en_idx)
                        energy_debt[num] = egb.debt
                    except Exception:
                        pass
            except Exception:
                pass
        self._debt_cache = {"vznosy": vznosy_debt, "energy": energy_debt}

    def _on_detail_delete(self):
        """Удаление участка из панели детали (подтверждение — в самой панели)."""
        plot = self._editing_plot
        if plot is not None:
            self._plots = [p for p in self._plots if p is not plot]
            self._save()
            self._recompute_debt()
            self._rebuild_table()
        self._close_detail()

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
            _AlertDialog.show_alert(self, "Ошибка чтения файла", str(e))
            return

        col_num = None
        col_name = None
        col_area = None
        col_phone = None
        col_email = None
        for col in df.columns:
            col_lower = str(col).lower().strip()
            if col_num is None and ("участк" in col_lower or col_lower in ("№", "n", "номер")):
                col_num = col
            if col_name is None and ("ф.и.о" in col_lower or "фио" in col_lower or "имя" in col_lower or col_lower == "ф.и.о."):
                col_name = col
            if col_area is None and ("площад" in col_lower or "кв.м" in col_lower or "м²" in col_lower or "м2" in col_lower):
                col_area = col
            if col_phone is None and ("телефон" in col_lower or "тел." in col_lower or col_lower == "тел" or "phone" in col_lower):
                col_phone = col
            if col_email is None and ("email" in col_lower or "e-mail" in col_lower or "почта" in col_lower):
                col_email = col

        if col_num is None or col_name is None:
            _AlertDialog.show_alert(
                self, "Неверный формат",
                f"Не удалось найти нужные столбцы.\n"
                f"Ожидается: «№ участка» и «Ф.И.О.»\n"
                f"Найдены столбцы: {', '.join(str(c) for c in df.columns)}"
            )
            return

        people = self._people_cache
        people_dirty = False

        def _clean_phone(raw: str) -> str:
            raw = raw.strip()
            # Excel часто хранит телефон как число — "89261234567.0".
            if re.fullmatch(r"\+?\d+\.0", raw):
                raw = raw[:-2]
            return raw

        def _get_or_create_person(name: str, phone: str = "", email: str = "") -> dict:
            nonlocal people_dirty
            person = people_reg.find_by_name(people, name)
            if person is None:
                person = people_reg.create_person(name, phone, email)
                people.append(person)
                people_dirty = True
            else:
                if phone and not person.get("phone"):
                    person["phone"] = phone
                    people_dirty = True
                if email and not person.get("email"):
                    person["email"] = email
                    people_dirty = True
            return person

        imported: dict[str, dict] = {}
        for _, row in df.iterrows():
            num = str(row[col_num]).strip()
            name = str(row[col_name]).strip()
            if not num or num.lower() in ("nan", "none", "") or not name or name.lower() in ("nan", "none", ""):
                continue
            phone = ""
            if col_phone is not None:
                raw_phone = str(row[col_phone]).strip()
                if raw_phone and raw_phone.lower() not in ("nan", "none"):
                    phone = _clean_phone(raw_phone)
            email = ""
            if col_email is not None:
                raw_email = str(row[col_email]).strip()
                if raw_email and raw_email.lower() not in ("nan", "none"):
                    email = raw_email
            entry = imported.setdefault(num, {"owners": [], "area": None})
            existing_names = [_owner_name(o) for o in entry["owners"]]
            if name not in existing_names:
                person = _get_or_create_person(name, phone, email)
                owner = _make_owner(name, is_member=True, person_id=person["id"],
                                     phone=phone, email=email)
                entry["owners"].append(owner)
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
            _AlertDialog.show_alert(self, "Пустой файл", "В файле не найдено данных об участках.")
            return

        dlg = _BasePromptDialog(
            "Импорт участков",
            f"Найдено {len(imported)} участков в файле.\n\nКак импортировать?",
            parent=self,
        )
        choice = {"value": "cancel"}

        def _pick(value: str):
            choice["value"] = value
            dlg.accept()

        dlg._add_button("Отмена", SecondaryButton, lambda: _pick("cancel"))
        dlg._add_button("Заменить всё", DangerButton, lambda: _pick("replace"))
        dlg._add_button("Добавить новые", PrimaryButton, lambda: _pick("merge"))
        dlg._finalize()
        _exec_dialog(dlg, self)
        if choice["value"] == "cancel":
            return

        if choice["value"] == "replace":
            new_plots = []
            for num, entry in imported.items():
                _ensure_single_primary(entry["owners"])
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
                    _ensure_single_primary(current_owners)
                    if area is not None and existing[num].get("area") in (None, "", 0):
                        existing[num]["area"] = area
                else:
                    _ensure_single_primary(owners)
                    item = {"num": num, "owners": owners}
                    if area is not None:
                        item["area"] = area
                    existing[num] = item
            self._plots = list(existing.values())

        if people_dirty:
            try:
                people_reg.save_people(people)
            except Exception:
                pass

        self._save()
        self._rebuild_table()
        _AlertDialog.show_alert(
            self, "Импорт завершён",
            f"Импортировано {len(imported)} участков.")

    def _add_plot(self):
        """Быстрое добавление участка через модальное окно (номер/площадь/
        ФИО) вместо панели справа — см. _QuickAddPlotDialog."""
        existing_nums = {str(p.get("num", "")) for p in self._plots}
        people = self._people_cache
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
            _AlertDialog.show_alert(self, "Ошибка", "Файл не найден")
            return
        try:
            os.startfile(self._path)
        except Exception:
            _AlertDialog.show_alert(self, "Ошибка", "Не удалось открыть файл")

    def delete_path(self):
        self._path = ""
        self._refresh()

    def set_path(self, path: str) -> None:
        self._path = path
        self._refresh()

    def set_required(self, required: bool) -> None:
        self._required = required
        self._refresh()


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
                 existing_nums: set | None = None, people: list | None = None):
        super().__init__(parent)
        self._is_edit = plot_data is not None
        self._plot_data = dict(plot_data or {})
        self._df = df
        self._existing_nums: set = existing_nums or set()

        # Инициализируем рабочую копию списка групп
        self._groups: list = list(ownership.plot_groups(self._plot_data))
        if not any(g.get("until") is None for g in self._groups):
            self._groups.append({"since": None, "until": None, "owners": []})

        # Рабочая копия активной группы (редактируется инлайн-аккордеоном)
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
        # Передаётся хостом (PlotsWidget), чтобы не перечитывать JSON с диска
        # на каждое открытие карточки участка; при отсутствии — грузим сами
        # (например, при автономном использовании диалога вне PlotsWidget).
        self._people: list = people if people is not None else people_reg.load_people()
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._detail_view = QWidget()
        outer.addWidget(self._detail_view)

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
            style_date_popup(ctx.since_date_edit)
            ctx.since_date_edit.setFixedWidth(110)
            # Стрелка dropdown: QSS-псевдоэлемент ::down-arrow затирает
            # системную стрелку, поэтому подставляем свои глифы-шевроны
            # (PNG — в image: QIcon не подсунуть). Вверх/вниз переключает
            # CalendarArrowFlip через свойство calOpen.
            _arr_dn = icon_png_path("expand_more", 12, color="#6B7280")
            _arr_up = icon_png_path("expand_less", 12, color="#6B7280")
            ctx.since_date_edit.setStyleSheet(
                "QDateEdit{background:#FFFFFF;border:1px solid #C9D8E2;"
                "border-radius:6px;padding:2px 4px 2px 6px;font-size:12px;color:#1F2937;}"
                "QDateEdit::drop-down{subcontrol-origin:padding;subcontrol-position:right;"
                "width:18px;border:none;border-left:1px solid #C9D8E2;background:transparent;"
                "border-top-right-radius:6px;border-bottom-right-radius:6px;}"
                "QDateEdit::drop-down:hover{background:#DCE7EC;}"
                f"QDateEdit::down-arrow{{image:url({_arr_dn});width:12px;height:12px;}}"
                f'QDateEdit[calOpen="true"]::down-arrow{{image:url({_arr_up});}}')
            CalendarArrowFlip(ctx.since_date_edit)
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
                  "meter_location", "norm_kw", "calc_method", "avg_window_months",
                  "norm_start_date", "direct_contract_date", "direct_contract_number",
                  "billing_history"):
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
            _AlertDialog.show_alert(self, "Ошибка", "Укажите номер участка")
            return
        area_raw = self.inp_area.text().strip().replace(",", ".")
        if area_raw:
            try:
                v = float(area_raw)
                if v <= 0:
                    raise ValueError
            except ValueError:
                _AlertDialog.show_alert(self, "Ошибка",
                                    "Площадь должна быть положительным числом")
                return
        if not self._has_named_owner():
            _AlertDialog.show_alert(self, "Ошибка",
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
