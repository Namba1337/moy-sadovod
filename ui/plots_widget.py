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
    QBitmap, QColor, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap,
    QPolygon, QRegion, QRegularExpressionValidator,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDateEdit, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QCompleter, QListView, QMessageBox, QPushButton, QScrollArea, QScrollBar, QSizePolicy, QStyle, QStyledItemDelegate,
    QStyleFactory, QStyleOptionViewItem, QTableWidget, QTableWidgetItem, QTreeView,
    QVBoxLayout, QWidget,
)

from core import ownership
from core import people as people_reg
from core.utils import DATA_DIR, fmt_money


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


def _mat_icon(codepoint: int, size: int = 16, fill: int = 0,
              color: str = "#07414F") -> QIcon:
    """Рендерит символ Material Symbols в QIcon (для кнопок с текстом)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setFont(_mat_font(size, fill))
    p.setPen(QColor(color))
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, chr(codepoint))
    p.end()
    return QIcon(pm)


_F_STAR_OUTLINE  = _mat_font(20, fill=0)
_F_STAR_FILLED   = _mat_font(20, fill=1)
_F_CHEVRON       = _mat_font(18, fill=0)
_F_COPY_OUTLINE  = _mat_font(18, fill=0)


_copy_toasts: list = []  # держим ссылку, чтобы GC не удалил до показа


_SS_COPY_TOAST = (
    "QLabel{background:#C9D8E2;color:#07414F;border-radius:6px;"
    "padding:2px 10px;font-size:11px;}")
_SS_DIRTY_BADGE = (
    "QLabel{background:#DCFCE7;color:#16A34A;border-radius:4px;"
    "padding:0 8px;font-size:11px;}")


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


def _setup_phone_input(inp: "QLineEdit") -> None:
    """Маскирует поле телефона без setInputMask (тонкий курсор, нет шаблона вне edit-режима).

    Отслеживает предыдущее число цифр: если цифр столько же, но текст стал
    короче — пользователь удалил разделитель; убираем последнюю цифру, чтобы
    backspace работал интуитивно.
    """
    _st = {"digits": ""}

    def _on_edited(text: str) -> None:
        raw = re.sub(r"\D", "", text)
        # Убираем ведущий код страны если за ним есть ещё цифры
        if len(raw) >= 2 and raw[0] in "78":
            raw = raw[1:]
        raw = raw[:10]
        prev = _st["digits"]
        # Цифр столько же, но текст стал короче → удалён разделитель
        if raw == prev and len(text) < len(_phone_fmt(prev)):
            raw = raw[:-1]
        _st["digits"] = raw
        result = _phone_fmt(raw)
        if result != text:
            inp.setText(result)
            inp.setCursorPosition(len(result))

    inp.textEdited.connect(_on_edited)


def _make_anchor_label(text: str, style: str):
    """Returns (label, row_widget, dirty_badge).
    row_widget — QWidget фиксированной высоты с меткой, тостом и бейджем «Данные обновлены».
    Фиксированная высота предотвращает вертикальный сдвиг элементов при появлении бейджей,
    а отсутствие retainSizeWhenHidden предотвращает горизонтальное переполнение viewport.
    Toast: label.property('_toast'). Dirty badge: управляется извне по _refresh_dirty_badges."""
    lbl = QLabel(text)
    lbl.setStyleSheet(style)

    toast = QLabel("Скопировано")
    toast.setStyleSheet(_SS_COPY_TOAST)
    toast.hide()
    lbl.setProperty("_toast", toast)

    dirty = QLabel("Данные обновлены")
    dirty.setStyleSheet(_SS_DIRTY_BADGE)
    dirty.hide()

    # Контейнер фиксированной высоты: бейджи могут появляться/исчезать,
    # не меняя высоту строки и не сдвигая элементы ниже.
    row_w = QWidget()
    row_w.setStyleSheet("background:transparent;")
    row_lay = QHBoxLayout(row_w)
    row_lay.setContentsMargins(0, 0, 0, 0)
    row_lay.setSpacing(6)
    row_lay.addWidget(lbl, 0, Qt.AlignmentFlag.AlignVCenter)
    row_lay.addWidget(toast, 0, Qt.AlignmentFlag.AlignVCenter)
    row_lay.addWidget(dirty, 0, Qt.AlignmentFlag.AlignVCenter)
    row_lay.addStretch()
    # Высота = бейдж с паддингом; задаётся после adjustSize чтобы учесть DPI
    toast.adjustSize()
    dirty.adjustSize()
    row_w.setFixedHeight(max(toast.sizeHint().height(), dirty.sizeHint().height()) + 2)
    return lbl, row_w, dirty


def _show_copy_toast(anchor) -> None:
    toast = anchor.property("_toast")
    if toast is not None:
        toast.show()
        QTimer.singleShot(1000, toast.hide)
        return
    # Фолбэк для якорей без layout-тоста (не используется для полей карточки)
    parent = anchor.parentWidget() or anchor.window()
    toast = QLabel("Скопировано", parent)
    toast.setStyleSheet(_SS_COPY_TOAST)
    toast.adjustSize()
    text_w = anchor.fontMetrics().horizontalAdvance(anchor.text())
    p = anchor.mapTo(parent, QPoint(text_w + 6, 0))
    toast.move(p.x(), p.y() + (anchor.height() - toast.height()) // 2)
    toast.raise_()
    toast.show()
    _copy_toasts.append(toast)

    def _cleanup():
        toast.deleteLater()
        try:
            _copy_toasts.remove(toast)
        except ValueError:
            pass

    QTimer.singleShot(3000, _cleanup)


def _make_copy_btn(target_inp, anchor_lbl=None) -> "QPushButton":
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
        _show_copy_toast(anchor_lbl if anchor_lbl is not None else btn)

    btn.enterEvent = _enter
    btn.leaveEvent = _leave
    btn.clicked.connect(_on_click)
    return btn


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
        width: 12px; background: #F0F4F8; border: none;
    }
    QTreeView#mainTable QScrollBar::handle:vertical {
        background: #B5C8D5; border-radius: 5px; min-height: 24px;
        margin: 2px 2px 2px 2px;
    }
    QTreeView#mainTable QScrollBar::add-line:vertical,
    QTreeView#mainTable QScrollBar::sub-line:vertical { height: 0; }
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
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1),
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
        self.list_view.setStyleSheet(
            "QListView{background:transparent;border:none;outline:0;}")
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
        drawer_lyt = QVBoxLayout(self._drawer)
        drawer_lyt.setContentsMargins(0, 0, 0, 0)
        drawer_lyt.setSpacing(0)
        self._drawer_scroll = QScrollArea()
        self._drawer_scroll.setWidgetResizable(True)
        self._drawer_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._drawer_scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self._drawer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._drawer_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Win11 overlay-scrollbar фикс (как в других скроллах проекта).
        self._drawer_sb_style = QStyleFactory.create("Fusion")
        if self._drawer_sb_style is not None:
            self._drawer_scroll.setStyle(self._drawer_sb_style)
            self._drawer_scroll.verticalScrollBar().setStyle(self._drawer_sb_style)
        drawer_lyt.addWidget(self._drawer_scroll)
        self._drawer.setVisible(False)
        body.addWidget(self._drawer)

        layout.addLayout(body)

    def _open_detail(self, plot):
        """Открывает деталь участка в правом drawer (вместо модального диалога)."""
        if self._detail_panel is not None:
            self._close_detail()
        self._editing_plot = plot  # None — новый участок
        if plot is not None:
            existing = {str(p.get("num", "")) for p in self._plots if p is not plot}
        else:
            existing = {str(p.get("num", "")) for p in self._plots}
        panel = PlotEditDialog(plot_data=plot, parent=self, df=self._df,
                               existing_nums=existing)
        panel.closed.connect(self._on_detail_closed)
        if plot is not None:
            panel.deleted.connect(self._on_detail_delete)
        self._detail_panel = panel
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
        """Иконка «Добавить участок»: fill-бирюза когда открыта панель нового
        участка, иначе серый outline (e146 add_box)."""
        active = (getattr(self, "_detail_panel", None) is not None
                  and getattr(self, "_editing_plot", None) is None)
        self._btn_add.setIcon(_mat_icon(
            0xE146, 22, fill=1 if active else 0,
            color="#07414F" if active else "#9CA3AF"))

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
            self._detail_panel.detach()  # снять focusChanged до удаления
            self._drawer_scroll.takeWidget()
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
        self._open_detail(None)


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

class _DocFieldWidget(QWidget):
    """Поле документа, стилизованное под QLineEdit.

    Edit + нет файла : граница, текст «+ Загрузить документ».
    Edit + есть файл : граница, подчёркнутое имя файла.
    View + нет файла : компактный бейдж «Отсутствует» + stretch (слева).
    View + есть файл : без рамки, подчёркнутое имя файла, кликабельно.
    """
    path_changed = pyqtSignal(str)

    # Стиль на контейнере (QWidget#_docFW), а не на внутренней QLabel —
    # чтобы обойти dialog-level "QLabel{background:transparent;}"
    _SS_BOX_EDIT = (
        "QWidget#_docFW{background:#FFFFFF;border:1px solid #D1D5DB;"
        "border-radius:6px;}")
    _SS_BOX_VIEW = "QWidget#_docFW{background:transparent;border:none;}"
    _SS_FIELD_TXT_EMPTY = (
        "QLabel{background:transparent;border:none;padding:0;"
        "font-size:12px;color:#9CA3AF;}")
    _SS_FIELD_TXT_FILE = (
        "QLabel{background:transparent;border:none;padding:0;"
        "font-size:12px;color:#07414F;}")
    _SS_ABSENT = (
        "QLabel{background:#FEF3C7;color:#B45309;border-radius:6px;"
        "padding:2px 10px;font-size:11px;}")
    _SS_NOT_REQUIRED = (
        "QLabel{background:#F3F4F6;color:#9CA3AF;border-radius:6px;"
        "padding:2px 10px;font-size:11px;}")

    def __init__(self, doc_path: str = "", *,
                 upload_tip: str = "Загрузить документ",
                 parent=None):
        super().__init__(parent)
        self._path = doc_path
        self._upload_tip = upload_tip
        self._interactive = True
        self.setObjectName("_docFW")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(6)

        # Иконка загрузки (Material Symbols, отдельная QLabel — нет проблем с выравниванием)
        _if = QFont("Material Symbols Rounded")
        _if.setPixelSize(14)
        self._icon = QLabel(chr(0xE9FC))
        self._icon.setFont(_if)
        self._icon.setFixedSize(16, 16)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setStyleSheet(
            "QLabel{background:transparent;border:none;padding:0;color:#9CA3AF;}")
        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)

        # Текстовое поле
        self._field = QLabel()
        self._field.setTextFormat(Qt.TextFormat.RichText)
        self._field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._field.setStyleSheet(self._SS_FIELD_TXT_EMPTY)
        self._field.mousePressEvent = self._on_click
        lay.addWidget(self._field)

        # Бейдж «Отсутствует» (view-режим без файла) — AlignVCenter предотвращает
        # растяжение по высоте в QHBoxLayout (иначе заполняет все 34px контейнера)
        self._absent = QLabel("Отсутствует")
        self._absent.setStyleSheet(self._SS_ABSENT)
        self._absent.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._absent.setVisible(False)
        lay.addWidget(self._absent, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Кнопка удаления — встроена сразу за полем, видна только в edit-режиме
        _df = QFont("Material Symbols Rounded")
        _df.setPixelSize(18)
        self.del_btn = QPushButton(chr(0xE92B))
        self.del_btn.setFont(_df)
        self.del_btn.setFixedSize(26, 26)
        self.del_btn.setFlat(True)
        self.del_btn.setVisible(False)
        self.del_btn.clicked.connect(self.delete_path)

        def _sync_del(path, b=self.del_btn):
            has = bool(path)
            b.setEnabled(has)
            b.setCursor(Qt.CursorShape.PointingHandCursor if has else Qt.CursorShape.ArrowCursor)
            b.setStyleSheet(
                "QPushButton{background:transparent;border:none;padding:0;"
                f"color:{'#DC2626' if has else '#D1D5DB'};}}"
                + ("QPushButton:hover{color:#B91C1C;}" if has else ""))

        self.path_changed.connect(_sync_del)
        # del_btn не добавляем в lay — он размещается снаружи поля в docs_grid

        self._refresh()

    def get_path(self) -> str:
        return self._path

    def setEnabled(self, enabled: bool):
        self._interactive = bool(enabled)
        self._refresh()

    def _display_name(self) -> str:
        name = os.path.basename(self._path)
        if len(name) > 24:
            name = name[:24 - 3 - 7] + "…" + name[-7:]
        return name

    def _refresh(self):
        has = bool(self._path)
        absent_mode = not has and not self._interactive
        edit_empty = not has and self._interactive

        self._icon.setVisible(edit_empty)
        self._field.setVisible(not absent_mode)
        self._absent.setVisible(absent_mode)

        if absent_mode:
            self.setStyleSheet(self._SS_BOX_VIEW)
        elif has:
            name = self._display_name()
            self.setStyleSheet(self._SS_BOX_EDIT)
            self._field.setStyleSheet(self._SS_FIELD_TXT_FILE)
            self._field.setText(f"<u>{name}</u>")
            self._field.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setStyleSheet(self._SS_BOX_EDIT)
            self._field.setStyleSheet(self._SS_FIELD_TXT_EMPTY)
            self._field.setText("Загрузить документ")
            self._field.setCursor(Qt.CursorShape.PointingHandCursor)

        self.path_changed.emit(self._path)

    def _on_click(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._path:
            self._on_open()
        elif self._interactive:
            self._on_upload()

    def _on_upload(self):
        path, _ = QFileDialog.getOpenFileName(
            self, self._upload_tip, "",
            "Документы и изображения (*.pdf *.jpg *.jpeg *.png *.doc *.docx);;Все файлы (*.*)"
        )
        if path:
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
        """True → «Отсутствует» (жёлтый), False → «Не требуется» (серый)."""
        if required:
            self._absent.setStyleSheet(self._SS_ABSENT)
            self._absent.setText("Отсутствует")
        else:
            self._absent.setStyleSheet(self._SS_NOT_REQUIRED)
            self._absent.setText("Не требуется")


def _make_doc_delete_btn(doc_w: "_DocFieldWidget") -> "QPushButton":
    """Кнопка удаления документа: серая когда пусто, красная когда есть файл."""
    from PyQt6.QtWidgets import QPushButton as _QB
    _f = QFont("Material Symbols Rounded")
    _f.setPixelSize(18)
    btn = _QB(chr(0xF5A2))
    btn.setFont(_f)
    btn.setFixedSize(26, 26)
    btn.setFlat(True)
    btn.setCursor(Qt.CursorShape.ArrowCursor)

    def _sync(path):
        has = bool(path)
        btn.setEnabled(has)
        btn.setCursor(
            Qt.CursorShape.PointingHandCursor if has else Qt.CursorShape.ArrowCursor)
        btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;padding:0;"
            f"color:{'#DC2626' if has else '#D1D5DB'};}}"
            + ("QPushButton:hover{color:#B91C1C;}" if has else ""))

    doc_w.path_changed.connect(_sync)
    btn.clicked.connect(doc_w.delete_path)
    _sync(doc_w.get_path())
    return btn


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

    def _people_names(self) -> list:
        seen, names = set(), []
        for p in self._people:
            nm = str(p.get("name", "")).strip()
            if nm and nm.casefold() not in seen:
                seen.add(nm.casefold())
                names.append(nm)
        return names

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
            _vsb.setStyleSheet("""
                QScrollBar:vertical { background:#E5E9ED; width:10px; margin:0; }
                QScrollBar::handle:vertical {
                    background:#9CA3AF; border-radius:4px; min-height:30px; margin:1px;
                }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
                QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:none; }
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

    def _any_editing(self) -> bool:
        return any(cd.get("is_editing", False) for cd in self._cards)

    def _update_edit_footer(self):
        """Обновляет состояние карточек."""
        if self._footer_view is None:
            return  # вызван до завершения _setup_ui
        self._update_save_state()

    def _set_card_edit_mode(self, cd: dict, mode: bool):
        cd["is_editing"] = mode
        if not mode:
            cd.pop("_is_new", None)  # после первого сохранения карточка уже не «новая»
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
        for del_key in ("opd_del", "egrn_del", "member_del"):
            cd[del_key].setVisible(mode)
        # btn_edit — всегда виден в развёрнутом виде, стиль меняется через _refresh_btn_edit
        # btn_del + btn_cancel + btn_save_card — только в режиме редактирования
        cd["btn_del"].setVisible(mode)
        cd["btn_cancel"].setVisible(mode)
        cd["btn_save_card"].setVisible(mode)
        # В режиме редактирования chevron не реагирует — меняем курсор
        chevron_cursor = Qt.CursorShape.ArrowCursor if mode else Qt.CursorShape.PointingHandCursor
        cd["chevron"].setCursor(chevron_cursor)
        self._refresh_btn_edit(cd)
        star = cd["star_btn"]
        if mode:
            if not cd["_is_primary"]:
                star.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            star.setCursor(Qt.CursorShape.ArrowCursor)
        self._update_edit_footer()

    def _on_card_edit_click(self, cd: dict):
        if cd["is_collapsed"]:
            self._apply_collapse(cd, collapsed=False)
        self._set_card_edit_mode(cd, True)

    def _on_add_person(self):
        if self._any_editing():
            return
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

        # ── Левый кластер: кнопка редактирования + отмена + сохранить ──────
        # (видны только в развёрнутом состоянии)

        btn_edit = QPushButton()
        btn_edit.setFlat(False)
        btn_edit.setFixedHeight(26)
        btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_edit.clicked.connect(lambda _, c=cd: self._on_card_edit_click(c))

        def _edit_enter(e, b=btn_edit, c=cd):
            if not c.get("is_editing", False):
                b.setIcon(_mat_icon(0xF88D, 16, fill=1, color="#07414F"))
            QPushButton.enterEvent(b, e)

        def _edit_leave(e, b=btn_edit, c=cd):
            if not c.get("is_editing", False):
                b.setIcon(_mat_icon(0xF88D, 16, fill=0, color="#07414F"))
            QPushButton.leaveEvent(b, e)

        btn_edit.enterEvent = _edit_enter
        btn_edit.leaveEvent = _edit_leave
        cd["btn_edit"] = btn_edit
        hdr_lyt.addWidget(btn_edit)

        btn_cancel = QPushButton("Отмена")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.setFixedHeight(26)
        btn_cancel.setStyleSheet(
            "QPushButton{background:transparent;color:#6B7280;"
            "border:1px solid #D1D5DB;border-radius:6px;"
            "padding:3px 8px;font-size:12px;}"
            "QPushButton:hover{background:#F3F4F6;border-color:#9CA3AF;}")
        btn_cancel.clicked.connect(lambda _, c=cd: self._cancel_card_edit(c))
        cd["btn_cancel"] = btn_cancel
        hdr_lyt.addWidget(btn_cancel)

        btn_save_card = QPushButton("Сохранить")
        btn_save_card.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save_card.setFixedHeight(26)
        btn_save_card.setStyleSheet(
            "QPushButton{background:#07414F;color:white;border:none;"
            "border-radius:6px;padding:3px 8px;font-size:12px;}"
            "QPushButton:hover{background:#0B5A6E;}"
            "QPushButton:disabled{background:#E5E7EB;color:#9CA3AF;}")
        btn_save_card.clicked.connect(lambda _, c=cd: self._set_card_edit_mode(c, False))
        cd["btn_save_card"] = btn_save_card
        hdr_lyt.addWidget(btn_save_card)

        # ── Краткое имя (только в свёрнутом состоянии) ───────────────────
        name_summary = QLabel()
        name_summary.setStyleSheet("font-size:12px; color:#374151; background:transparent;")
        name_summary.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        name_summary.setMinimumWidth(0)
        cd["name_summary"] = name_summary
        hdr_lyt.addWidget(name_summary)
        hdr_lyt.addStretch(1)

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
        name_col.addWidget(_fio_lbl_row)
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
        completer = QCompleter(self._people_names(), name_inp)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        name_inp.setCompleter(completer)
        completer.activated.connect(
            lambda _t, c=cd: QTimer.singleShot(0, lambda: self._on_name_committed(c)))
        name_inp.editingFinished.connect(lambda c=cd: self._on_name_committed(c))
        name_row_h.addWidget(name_inp)
        name_row_h.addWidget(_make_copy_btn(name_inp, lbl_fio))
        name_col.addLayout(name_row_h)
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
            inp_row.addWidget(_make_copy_btn(inp, lbl_contact))
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

        opd_w  = _DocFieldWidget(opd_path, upload_tip="Загрузить согласие на ОПД")
        egrn_w = _DocFieldWidget(egrn_path, upload_tip="Загрузить выписку ЕГРН")
        mem_w  = _DocFieldWidget(member_path, upload_tip="Загрузить заявление в СНТ")
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
            """Возвращает QHBoxLayout: [doc_w] [del_btn снаружи поля]."""
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            row.addWidget(doc_w)
            row.addWidget(doc_w.del_btn, 0, Qt.AlignmentFlag.AlignVCenter)
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
            cd[del_key] = doc_w.del_btn

        _, _lbl_mem_row, _mem_dirty = _make_anchor_label("Заявление в СНТ", _lbl_ss)
        docs_grid.addWidget(_lbl_mem_row,    2, 0)
        docs_grid.addLayout(_doc_row(mem_w), 3, 0)
        cd["member_dirty"] = _mem_dirty
        cd["member_del"]   = mem_w.del_btn

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
        self._set_card_edit_mode(cd, start_editing)
        self._update_save_state()
        return cd

    # ── Collapse / expand ─────────────────────────────────────────────

    def _toggle_card(self, cd: dict):
        if self._any_editing():
            return  # блокируем любые переключения пока активен режим редактирования
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
        self._refresh_btn_edit(cd)

    def _refresh_btn_edit(self, cd: dict):
        """Обновляет btn_edit: скрыт (свёрнуто) / обычный / выделен (редактирование)."""
        btn = cd["btn_edit"]
        if cd["is_collapsed"]:
            btn.setVisible(False)
            return
        btn.setVisible(True)
        editing = cd.get("is_editing", False)
        btn.setIconSize(QSize(16, 16))
        btn.setFont(QFont())
        btn.setText("Редактировать")
        btn.setMinimumWidth(0)
        btn.setMaximumWidth(16777215)
        if editing:
            btn.setIcon(_mat_icon(0xF88D, 16, fill=1, color="#07414F"))
            btn.setStyleSheet(
                "QPushButton{background:#C9D8E2;color:#07414F;"
                "border:1px solid #07414F;border-radius:6px;"
                "padding:4px 12px;font-size:12px;}")
            btn.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            btn.setIcon(_mat_icon(0xF88D, 16, fill=0, color="#07414F"))
            btn.setStyleSheet(
                "QPushButton{background:transparent;color:#07414F;"
                "border:1px solid #C9D8E2;border-radius:6px;"
                "padding:4px 12px;font-size:12px;}"
                "QPushButton:hover{background:#EBF4F6;border-color:#07414F;}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.adjustSize()

    # ── Обновление Summary / тегов ────────────────────────────────────

    def _update_name_summary(self, cd: dict):
        full = cd["name_inp"].text().strip()
        cd["name_summary"].setText(_short_name(full) if full else "(без имени)")

    def _on_name_committed(self, cd: dict):
        """ФИО введено/выбрано → привязать человека из реестра и подставить контакты.

        Если ФИО совпадает с записью реестра — берём её person_id и заполняем
        пустые телефон/email из реестра (переиспользование). Иначе сбрасываем
        привязку — новый человек будет создан в _on_accept. Поля остаются
        редактируемыми (правка человека в реестре — отдельный шаг, Этап B)."""
        name = cd["name_inp"].text().strip()
        person = people_reg.find_by_name(self._people, name) if name else None
        if person:
            cd["person_id"] = person.get("id")
            if not cd["phone"].text().strip() and person.get("phone"):
                cd["phone"].setText(_normalize_phone(person["phone"]))
            if not cd["email"].text().strip() and person.get("email"):
                cd["email"].setText(person.get("email", ""))
        else:
            cd["person_id"] = None
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
            return
        cur = self._card_snapshot(cd)
        for snap_key, badge_key in [
            ("name",   "name_dirty"),
            ("phone",  "phone_dirty"),
            ("email",  "email_dirty"),
            ("opd",    "opd_dirty"),
            ("egrn",   "egrn_dirty"),
            ("member", "member_dirty"),
        ]:
            b = cd.get(badge_key)
            if b:
                b.setVisible(cur[snap_key] != snap[snap_key])

    def _cancel_card_edit(self, cd: dict) -> None:
        if cd.get("_is_new"):
            self._remove_card(cd)
            return
        snap = cd.get("_snap")
        if snap is not None:
            # Блокируем сигналы на время восстановления, чтобы избежать
            # промежуточных срабатываний _update_save_state с частичным состоянием
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

        self._set_card_edit_mode(cd, False)

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
        if self._any_editing():
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
                    "background:#EBF4F6;border:1.5px solid #C9D8E2;border-radius:10px;}")
            else:
                star.setFont(_F_STAR_OUTLINE)
                star.setStyleSheet(
                    "QPushButton{background:transparent;border:none;"
                    "color:#C9D8E2;padding:0;}")
                star.setCursor(Qt.CursorShape.PointingHandCursor)
                cd["hdr_lbl"].setVisible(False)
                cd["widget"].setStyleSheet(
                    "QFrame#personCard{"
                    "background:#F9FAFB;border:1px solid #E5E7EB;border-radius:10px;}")

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
            btn = cd.get("btn_save_card")
            if btn is not None:
                named = bool(cd["name_inp"].text().strip())
                enabled = named and self._is_card_dirty(cd)
                btn.setEnabled(enabled)
                btn.setCursor(
                    Qt.CursorShape.PointingHandCursor if enabled
                    else Qt.CursorShape.ArrowCursor)
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
        new_people = []
        for i, cd in enumerate(self._cards):
            name = cd["name_inp"].text().strip()
            if not name:
                continue
            phone = cd["phone"].text().strip()
            email = cd["email"].text().strip()
            # Привязка к человеку: по person_id карточки, иначе по ФИО, иначе —
            # создаём нового. _on_accept — гарантия дедупа (даже без авто-привязки).
            person = people_reg.get(self._people, cd.get("person_id"))
            if person is None:
                person = people_reg.find_by_name(self._people, name)
            if person is None:
                person = people_reg.create_person(name, phone, email)
                self._people.append(person)
                new_people.append(person)
            o = _make_owner(
                name,
                cd["rb_owner"].isChecked(),
                None,
                cd["rb_contact"].isChecked(),
                cd["member_doc"].get_path(),
                cd["opd_doc"].get_path(),
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

        if new_people:
            try:
                people_reg.save_people(self._people)
            except Exception:
                pass

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
        self._group_dirty: bool = False     # группа изменена через "Список контактов"
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
        lay = QVBoxLayout(self._detail_view)
        # Верх 26 — чтобы «Участок № X» встал вровень с заголовком списка (у колонки верх 24).
        lay.setContentsMargins(24, 26, 24, 24)
        lay.setSpacing(12)

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

        def _icon_btn(cp: int, handler) -> QPushButton:
            b = QPushButton()
            b.setFixedSize(28, 28)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{background:transparent;border:none;border-radius:4px;padding:2px;}"
                "QPushButton:hover{background:#F3F4F6;}")
            b.setIcon(_mat_icon(cp, 18, color="#6B7280"))
            b.clicked.connect(handler)
            return b

        if self._is_edit:
            # Режим отображения: «Участок № X» + карандаш, под ним площадь
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
            self._edit_btn = _icon_btn(0xF88D, self._on_header_edit)
            drow.addWidget(self._edit_btn)
            dv.addLayout(drow)
            self._area_display = QLabel()
            self._area_display.setStyleSheet(
                "font-size:13px; color:#6B7280; background:transparent;")
            dv.addWidget(self._area_display)
            lay.addWidget(self._disp_w)
        else:
            title_lbl = QLabel(self._title)
            title_lbl.setStyleSheet(
                "font-size:16px; font-weight:700; color:#1F2937; background:transparent;")
            lay.addWidget(title_lbl)

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
        self._lbl_num_taken = QLabel("Номер занят")
        self._lbl_num_taken.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._lbl_num_taken.setStyleSheet(
            "QLabel{background:#FEE2E2;color:#DC2626;border-radius:6px;"
            "padding:1px 8px;font-size:11px;}")
        self._lbl_num_taken.hide()
        cap_row.addWidget(self._lbl_num_taken)
        cap_row.addStretch()
        if self._is_edit:
            self._save_btn = _icon_btn(0xE161, self._on_header_save)
            cap_row.addWidget(self._save_btn)
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
        lay.addWidget(self._edit_block)

        if self._is_edit:
            self.inp_num.setReadOnly(True)
            self.inp_area.setReadOnly(True)
            self._edit_block.setVisible(False)
            self._refresh_displays()

        # -- Заголовок активной группы --
        act_hdr = QHBoxLayout()
        act_hdr.setContentsMargins(0, 4, 0, 0)
        lbl_act = QLabel("Активная группа")
        lbl_act.setStyleSheet(
            "font-size:12px; color:#6B7280; background:transparent;")
        act_hdr.addWidget(lbl_act, stretch=1)
        self._active_since_lbl = QLabel()
        self._active_since_lbl.setStyleSheet(
            "font-size:12px; color:#6B7280; background:transparent;")
        act_hdr.addWidget(self._active_since_lbl)
        lay.addLayout(act_hdr)

        # -- Карточка активной группы (одна колонка, по макету) --
        self._active_card = QFrame()
        self._active_card.setObjectName("activeCard")
        self._active_card.setStyleSheet(
            "QFrame#activeCard{"
            "background:#EBF4F6;border:1px solid #C9D8E2;border-radius:8px;}"
        )
        card_lay = QVBoxLayout(self._active_card)
        card_lay.setContentsMargins(14, 12, 14, 12)
        card_lay.setSpacing(4)

        self._active_name_lbl = QLabel()
        self._active_name_lbl.setStyleSheet(
            "font-size:14px; font-weight:700; color:#07414F; background:transparent;")
        self._active_name_lbl.setWordWrap(True)
        card_lay.addWidget(self._active_name_lbl)

        self._active_counts_lbl = QLabel()
        self._active_counts_lbl.setStyleSheet(
            "font-size:12px; color:#6B7280; background:transparent;")
        card_lay.addWidget(self._active_counts_lbl)

        sep_line = QFrame()
        sep_line.setFrameShape(QFrame.Shape.HLine)
        sep_line.setStyleSheet("color:#C9D8E2; background:#C9D8E2; max-height:1px;")
        card_lay.addWidget(sep_line)

        self._active_missing_lbl = QLabel()
        self._active_missing_lbl.setStyleSheet(
            "font-size:12px; color:#9CA3AF; background:transparent;")
        self._active_missing_lbl.setWordWrap(True)
        card_lay.addWidget(self._active_missing_lbl)

        self._active_debt_line = QLabel()
        self._active_debt_line.setStyleSheet(
            "font-size:12px; color:#374151; background:transparent;")
        self._active_debt_line.setWordWrap(True)
        card_lay.addWidget(self._active_debt_line)

        # ── Список контактов (превью + аккордеон) ──────────────────────
        _sep2 = QFrame()
        _sep2.setFrameShape(QFrame.Shape.HLine)
        _sep2.setStyleSheet("color:#C9D8E2; background:#C9D8E2; max-height:1px;")
        card_lay.addWidget(_sep2)

        _contacts_hdr = QLabel("Список контактов")
        _contacts_hdr.setStyleSheet(
            "font-size:12px; color:#6B7280; background:transparent;")
        card_lay.addWidget(_contacts_hdr)

        # Превью: до 3 контактов, показываются сразу
        self._active_preview_box = QWidget()
        self._active_preview_box.setStyleSheet("background:transparent;")
        self._active_preview_lay = QVBoxLayout(self._active_preview_box)
        self._active_preview_lay.setContentsMargins(0, 0, 0, 0)
        self._active_preview_lay.setSpacing(2)
        card_lay.addWidget(self._active_preview_box)

        # Кнопка «Показать все» / «Свернуть»
        self._btn_show_all = QPushButton("Показать все")
        self._btn_show_all.setObjectName("btnSecondary")
        self._btn_show_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_show_all.setStyleSheet(
            "QPushButton{background:transparent;color:#07414F;"
            "border:1px solid #D1D5DB;border-radius:6px;"
            "padding:4px 12px;font-size:12px;}"
            "QPushButton:hover{background:#EBF4F6;border-color:#07414F;}")
        self._btn_show_all.clicked.connect(self._on_toggle_contacts_full)
        self._btn_show_all.setVisible(False)
        card_lay.addWidget(self._btn_show_all)
        self._contacts_expanded = False
        self._expanded_contacts: set[int] = set()

        lay.addWidget(self._active_card)

        self._refresh_active_card()

        # -- Предыдущие группы --
        self._prev_section = QWidget()
        self._prev_section.setStyleSheet("background:transparent;")
        prev_lyt = QVBoxLayout(self._prev_section)
        prev_lyt.setContentsMargins(0, 0, 0, 0)
        prev_lyt.setSpacing(6)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#E5E7EB;background:#E5E7EB;max-height:1px;")
        prev_lyt.addWidget(sep)

        lbl_prev = QLabel("Предыдущие группы")
        lbl_prev.setStyleSheet(
            "font-size:12px; font-weight:600; color:#6B7280; background:transparent;")
        prev_lyt.addWidget(lbl_prev)

        self._prev_card_container = QWidget()
        self._prev_card_container.setStyleSheet("background:transparent;")
        self._prev_card_lyt = QVBoxLayout(self._prev_card_container)
        self._prev_card_lyt.setContentsMargins(0, 0, 0, 0)
        self._prev_card_lyt.setSpacing(8)

        # Скролл-область: видно ~одну предыдущую группу, остальные — под скролл,
        # чтобы окно не росло по вертикали при множестве архивных групп.
        self._prev_scroll = QScrollArea()
        self._prev_scroll.setWidgetResizable(True)
        self._prev_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._prev_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._prev_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Win11 overlay-scrollbar фикс (как в GroupEditDialog): Fusion на области и
        # на самом скроллбаре, иначе место под скроллбар не резервируется и QSS игнорируется.
        self._prev_sb_style = QStyleFactory.create("Fusion")
        _pvsb = self._prev_scroll.verticalScrollBar()
        if self._prev_sb_style is not None:
            self._prev_scroll.setStyle(self._prev_sb_style)
            _pvsb.setStyle(self._prev_sb_style)
        _pvsb.setFixedWidth(10)
        _pvsb.setStyleSheet("""
            QScrollBar:vertical { background: #E5E9ED; width: 10px; margin: 0; }
            QScrollBar::handle:vertical {
                background: #9CA3AF; border-radius: 4px; min-height: 30px; margin: 1px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
        """)
        self._prev_scroll.setWidget(self._prev_card_container)
        prev_lyt.addWidget(self._prev_scroll)

        lay.addWidget(self._prev_section)
        self._refresh_prev_section()

        lay.addStretch()  # свободное место уходит сюда — карточки прижаты к верху

        # -- Footer --
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color:#E5E7EB;background:#E5E7EB;max-height:1px;")
        lay.addWidget(sep2)

        footer = QWidget()
        f_lay = QHBoxLayout(footer)
        f_lay.setContentsMargins(0, 0, 0, 0)
        f_lay.setSpacing(8)
        if not self._is_edit:
            f_lay.addStretch()
            btn_cancel = QPushButton("Отмена")
            btn_cancel.setObjectName("btnFooterCancel")
            btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_cancel.clicked.connect(lambda: self._finish(False))
            f_lay.addWidget(btn_cancel)
            # «Сохранить» — иконка-галочка в шапке (cap_row), не в футере
        else:
            btn_del = QPushButton("Удалить участок")
            btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_del.setStyleSheet(
                "QPushButton{background:transparent;color:#DC2626;border:1px solid #FCA5A5;"
                "border-radius:6px;padding:8px 14px;font-size:13px;}"
                "QPushButton:hover{background:#FEF2F2;}")
            btn_del.clicked.connect(self._on_delete_clicked)
            f_lay.addWidget(btn_del)
            f_lay.addStretch()
            btn_archive_group = QPushButton("Заменить активную группу")
            btn_archive_group.setObjectName("btnSecondary")
            btn_archive_group.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_archive_group.clicked.connect(self._on_archive_active_group)
            f_lay.addWidget(btn_archive_group)
            btn_close = QPushButton("Закрыть")
            btn_close.setObjectName("btnFooterClose")
            btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_close.clicked.connect(self._on_close)
            f_lay.addWidget(btn_close)
        lay.addWidget(footer)
        self._footer_view = footer
        self._footer_edit = None

        if not self._is_edit:
            self._update_save_state()

    def _on_header_edit(self):
        """Вход в режим правки номера+площади (кнопка-карандаш)."""
        self._set_header_edit(True)

    def _set_header_edit(self, editing: bool, *, revert: bool = False):
        if not self._is_edit:
            return
        self._header_editing = editing
        if editing:
            self.inp_num.setReadOnly(False)
            self.inp_area.setReadOnly(False)
            self._disp_w.setVisible(False)
            self._edit_block.setVisible(True)
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
            self._disp_w.setVisible(True)
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
            _mat_icon(0xE161, 18, fill=1, color="#07414F" if ok else "#9CA3AF"))

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
        """Показывает бейдж «Номер занят» при совпадении с существующим номером."""
        current = self.inp_num.text().strip()
        self._lbl_num_taken.setVisible(bool(current and current in self._existing_nums))

    def _on_close(self):
        """Закрывает панель: если есть закоммиченные изменения — saved=True."""
        # Незакоммиченную правку номера/площади откатываем (без предупреждения)
        if self._is_edit and self._header_editing:
            self._set_header_edit(False, revert=True)
        if self._dirty_fields or self._group_dirty:
            self._build_result()
            self._finish(True)
        else:
            self._finish(False)

    def _finish(self, saved: bool):
        """Завершает работу панели: отключает focusChanged и эмитит closed(saved)."""
        self.detach()
        self.closed.emit(saved)

    def _on_delete_clicked(self):
        """Удаление участка — с подтверждением; решение исполняет хост (PlotsWidget)."""
        m = QMessageBox(self)
        m.setWindowTitle("Удаление участка")
        m.setText("Удалить этот участок?")
        m.setInformativeText("Будет удалена вся информация участка, включая документы.")
        m.setIcon(QMessageBox.Icon.Warning)
        yes = m.addButton("Да, удалить", QMessageBox.ButtonRole.AcceptRole)
        m.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        m.setDefaultButton(yes)
        m.exec()
        if m.clickedButton() is yes:
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
        final_groups = [
            self._active_group if g.get("until") is None else g
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

    def _refresh_active_card(self):
        owners = ownership.group_owners(self._active_group)

        # Имя главного участника (is_visible=True), иначе первый в списке
        main = next((o for o in owners if isinstance(o, dict) and o.get("is_visible")),
                    owners[0] if owners else None)
        name = ownership.owner_name(main) if main else "(нет лиц)"
        self._active_name_lbl.setText(name)

        # Дата начала (в заголовке)
        since = ownership.group_since(self._active_group)
        self._active_since_lbl.setText(
            f"Активна с: {since.strftime('%d.%m.%Y')}" if since else "Активна с: —")

        # Счётчики
        n_members = sum(1 for o in owners if isinstance(o, dict) and o.get("is_member"))
        count_lines = []
        if n_members:
            count_lines.append(f"Члены СНТ: {n_members}")
        self._active_counts_lbl.setText("  ·  ".join(count_lines) if count_lines else "")

        # Отсутствующие документы
        self._active_missing_lbl.setText("\n".join(self._missing_docs_lines(owners)))

        # Долг/Аванс
        self._refresh_debt_card()

        # Превью контактов (до 3)
        self._refresh_contacts_preview(owners)

    @staticmethod
    def _missing_docs_lines(owners) -> list[str]:
        """Список строк об отсутствующих документах по ролям.

        Роль определяется 1-в-1 как в карточке GroupEditDialog (_add_owner_card):
        is_visible → контакт (нужна ОПД), is_owner → собственник (ОПД+ЕГРН),
        is_member → член СНТ (ОПД+ЕГРН+заявление), иначе контакт.
        """
        no_opd = no_egrn = no_mem = 0
        for o in owners:
            if not isinstance(o, dict):
                continue
            if o.get("is_visible"):
                req_opd, req_egrn, req_mem = True, False, False
            elif o.get("is_owner"):
                req_opd, req_egrn, req_mem = True, True, False
            elif o.get("is_member"):
                req_opd, req_egrn, req_mem = True, True, True
            else:
                req_opd, req_egrn, req_mem = True, False, False
            if req_opd and not o.get("opd_doc"):
                no_opd += 1
            if req_egrn and not o.get("egrn_doc"):
                no_egrn += 1
            if req_mem and not o.get("member_doc"):
                no_mem += 1
        lines = []
        if no_opd:
            lines.append(f"Отсутствует заявление на ОПД: {no_opd}")
        if no_egrn:
            lines.append(f"Отсутствует выписка ЕГРН: {no_egrn}")
        if no_mem:
            lines.append(f"Отсутствует заявление в СНТ: {no_mem}")
        return lines

    def _refresh_debt_card(self):
        """Долг по ЧВ и электроэнергии — одной строкой в карточке активной группы."""
        from core.utils import fmt_money
        plot_num = str(self._plot_data.get("num", ""))
        since = ownership.group_since(self._active_group)

        def _part(label: str, debt: float) -> str:
            if debt < -0.005:
                return f"{label} аванс {fmt_money(abs(debt))}"
            if debt > 0.005:
                return f"{label} долг {fmt_money(abs(debt))}"
            return f"{label} {fmt_money(0)}"

        parts = []
        try:
            from core import vznosy as vzn
            rates = vzn.load_rates()
            adj = vzn.load_adjustments()
            area = vzn.plot_area_map().get(plot_num)
            gb = vzn.balance_for_active_group(
                plot_num, area, date.today(), rates, adj, self._df, since=since)
            parts.append(_part("ЧВ:", gb.debt))
        except Exception:
            pass
        try:
            from core import energy as en
            egb = en.balance_for_active_group(
                plot_num, date.today(), en.load_meters(), en.load_rates(),
                en.load_replacements(), en.load_baseline(), self._df, since=since)
            parts.append(_part("Эл.:", egb.debt))
        except Exception:
            pass
        self._active_debt_line.setText("   ·   ".join(parts))

    def _refresh_contacts_preview(self, owners: list):
        """Обновляет превью контактов под карточкой активной группы."""
        # Очистка
        while self._active_preview_lay.count():
            item = self._active_preview_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        PREVIEW_LIMIT = 3
        if self._contacts_expanded:
            shown = list(enumerate(owners))
        else:
            shown = list(enumerate(owners[:PREVIEW_LIMIT]))
        has_more = len(owners) > PREVIEW_LIMIT

        for idx, o in shown:
            is_open = idx in self._expanded_contacts

            # Строковый контейнер (vert): заголовок + (опционально) детали
            card = QWidget()
            card.setStyleSheet(
                "QWidget{background:transparent;}"
                "QWidget:hover{background:#E8F0F5;border-radius:4px;}")
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card_vl = QVBoxLayout(card)
            card_vl.setContentsMargins(0, 0, 0, 0)
            card_vl.setSpacing(0)

            # ── Заголовок строки (иконка + ФИО + телефон + шеврон) ──────
            hdr = QWidget()
            hdr.setStyleSheet("background:transparent;")
            hdr.setCursor(Qt.CursorShape.PointingHandCursor)
            rl = QHBoxLayout(hdr)
            rl.setContentsMargins(0, 2, 0, 2)
            rl.setSpacing(6)

            # Иконка роли
            role_icon = QLabel()
            role_icon.setFixedSize(18, 18)
            role_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if isinstance(o, dict) and o.get("is_member"):
                role_icon.setText(chr(0xE873))
                role_icon.setStyleSheet(
                    "background:#D6EBD5; color:#2E7D32; border-radius:9px;"
                    "font-size:10px;")
            elif isinstance(o, dict) and o.get("is_owner"):
                role_icon.setText(chr(0xE25C))
                role_icon.setStyleSheet(
                    "background:#C9D8E2; color:#07414F; border-radius:9px;"
                    "font-size:10px;")
            else:
                role_icon.setText(chr(0xE0B9))
                role_icon.setStyleSheet(
                    "background:#E5E7EB; color:#6B7280; border-radius:9px;"
                    "font-size:10px;")
            role_icon.setFont(_mat_font(12))
            rl.addWidget(role_icon)

            # Имя
            full_name = ownership.owner_name(o) if isinstance(o, dict) else str(o)
            lbl = QLabel(_short_name(full_name) if full_name else "—")
            lbl.setStyleSheet(
                "font-size:12px; color:#1F2937; background:transparent;")
            lbl.setSizePolicy(
                QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
            lbl.setMinimumWidth(0)
            rl.addWidget(lbl, stretch=1)

            # Телефон (свернуто)
            phone = (o.get("phone", "") if isinstance(o, dict) else "").strip()
            if phone and not is_open:
                ph_lbl = QLabel(phone)
                ph_lbl.setStyleSheet(
                    "font-size:11px; color:#6B7280; background:transparent;")
                rl.addWidget(ph_lbl)

            # Шеврон
            chevron_lbl = QLabel(chr(0xE5CE) if is_open else chr(0xE5CF))
            chevron_lbl.setFont(_mat_font(16))
            chevron_lbl.setStyleSheet("color:#9CA3AF; background:transparent;")
            rl.addWidget(chevron_lbl)

            card_vl.addWidget(hdr)

            # ── Детали (только если раскрыто) ─────────────────────────
            if is_open:
                det = QWidget()
                det.setStyleSheet(
                    "QWidget{background:transparent;}")
                det_lay = QVBoxLayout(det)
                det_lay.setContentsMargins(24, 4, 8, 6)
                det_lay.setSpacing(4)

                # Телефон (поле ввода)
                _lbl_ph = QLabel("Телефон")
                _lbl_ph.setStyleSheet(
                    "font-size:10px; color:#9CA3AF; background:transparent;")
                det_lay.addWidget(_lbl_ph)
                inp_phone = QLineEdit(phone)
                inp_phone.setPlaceholderText("нет телефона")
                inp_phone.setStyleSheet(
                    "QLineEdit{background:#F8F9FA; border:1px solid #D1D5DB;"
                    "border-radius:4px; padding:4px 8px; font-size:12px; color:#1F2937;}"
                    "QLineEdit:focus{border:1px solid #07414F;}")
                det_lay.addWidget(inp_phone)

                # Email
                email = (o.get("email", "") if isinstance(o, dict) else "").strip()
                _lbl_em = QLabel("Email")
                _lbl_em.setStyleSheet(
                    "font-size:10px; color:#9CA3AF; background:transparent;")
                det_lay.addWidget(_lbl_em)
                inp_email = QLineEdit(email)
                inp_email.setPlaceholderText("нет email")
                inp_email.setStyleSheet(
                    "QLineEdit{background:#F8F9FA; border:1px solid #D1D5DB;"
                    "border-radius:4px; padding:4px 8px; font-size:12px; color:#1F2937;}"
                    "QLineEdit:focus{border:1px solid #07414F;}")
                det_lay.addWidget(inp_email)

                # Роль (метки)
                role_row = QHBoxLayout()
                role_row.setSpacing(6)
                for text, active in [("Контакт", not (o.get("is_owner") or o.get("is_member"))),
                                     ("Собственник", bool(o.get("is_owner"))),
                                     ("Член СНТ", bool(o.get("is_member")))]:
                    tag = QLabel(text)
                    if active:
                        tag.setStyleSheet(
                            "font-size:10px; padding:2px 8px; border-radius:6px;"
                            "background:#07414F; color:white;")
                    else:
                        tag.setStyleSheet(
                            "font-size:10px; padding:2px 8px; border-radius:6px;"
                            "background:#E5E7EB; color:#6B7280;")
                    role_row.addWidget(tag)
                role_row.addStretch()
                det_lay.addLayout(role_row)

                # Кнопка «Сохранить»
                btn_save = QPushButton("Сохранить")
                btn_save.setObjectName("btnCardSave")
                btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_save.setFixedHeight(26)
                btn_save.setStyleSheet(
                    "QPushButton{background:#07414F; color:white; border:none;"
                    "border-radius:6px; padding:4px 14px; font-size:12px;}"
                    "QPushButton:hover{background:#0B5A6E;}")
                btn_save.clicked.connect(
                    lambda _, i=idx, ph=inp_phone, em=inp_email:
                        self._save_inline_contact(i, ph.text(), em.text()))
                det_lay.addWidget(btn_save, alignment=Qt.AlignmentFlag.AlignRight)

                card_vl.addWidget(det)

            # Клик по заголовку — тоггл раскрытия
            _idx = idx
            hdr.mousePressEvent = lambda _, i=_idx: self._toggle_contact_detail(i)

            self._active_preview_lay.addWidget(card)

        if not shown:
            empty_lbl = QLabel("(нет лиц)")
            empty_lbl.setStyleSheet(
                "font-size:12px; color:#9CA3AF; background:transparent;")
            self._active_preview_lay.addWidget(empty_lbl)

        if has_more:
            self._btn_show_all.setVisible(True)
            self._btn_show_all.setText(
                "Свернуть" if self._contacts_expanded else "Показать все")
        else:
            self._btn_show_all.setVisible(False)
            self._contacts_expanded = False

    def _toggle_contact_detail(self, idx: int):
        """Тоггл раскрытия деталей контакта по индексу."""
        if idx in self._expanded_contacts:
            self._expanded_contacts.discard(idx)
        else:
            self._expanded_contacts.add(idx)
        self._refresh_contacts_preview(
            ownership.group_owners(self._active_group))

    def _save_inline_contact(self, idx: int, phone: str, email: str):
        """Сохраняет отредактированные телефон/email контакта по индексу."""
        owners = self._active_group.get("owners", []) or []
        if idx < 0 or idx >= len(owners):
            return
        o = owners[idx]
        if not isinstance(o, dict):
            return
        o["phone"] = phone.strip()
        o["email"] = email.strip()
        self._group_dirty = True
        self._refresh_contacts_preview(owners)
        self._update_save_state()

    def _refresh_prev_section(self):
        archived = ownership.archived_groups({"groups": self._groups})
        self._prev_section.setVisible(bool(archived))
        # Очищаем и перестраиваем все карточки предыдущих групп
        while self._prev_card_lyt.count():
            item = self._prev_card_lyt.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not archived:
            self._prev_scroll.setMinimumHeight(0)
            self._prev_scroll.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX — без лимита
            return
        first_card = None
        for i, g in enumerate(archived):
            c = self._make_archived_preview(g)
            self._prev_card_lyt.addWidget(c)
            if i == 0:
                first_card = c
        # Фиксируем высоту области под одну карточку (+ «peek», если групп несколько),
        # чтобы минимальный размер окна резервировал место на полную предыдущую группу,
        # а лишние группы уходили под скролл. Сначала синхронно по sizeHint (чтобы окно
        # открылось нужной высоты), затем уточняем по фактической высоте после раскладки.
        self._apply_prev_height(first_card.sizeHint().height(), len(archived))
        QTimer.singleShot(
            0, lambda c=first_card, n=len(archived): self._refine_prev_height(c, n))

    def _apply_prev_height(self, one_h: int, count: int):
        target = max(int(one_h), 130)  # «пол» на случай заниженного sizeHint до показа
        if count > 1:
            target += self._prev_card_lyt.spacing() + 20  # видно немного следующей карточки
        self._prev_scroll.setMinimumHeight(target)
        self._prev_scroll.setMaximumHeight(target)

    def _refine_prev_height(self, first_card, count: int):
        try:
            one_h = max(first_card.sizeHint().height(), first_card.height())
        except RuntimeError:
            return  # карточка уже удалена (повторный refresh)
        self._apply_prev_height(one_h, count)
        # При добавлении группы в открытом окне — подрастить высоту под новый минимум.
        need = self.minimumSizeHint().height()
        if self.height() < need:
            self.resize(self.width(), need)

    def _make_archived_preview(self, group: dict) -> QFrame:
        """Полная карточка предыдущей группы — как активная, с «Списком контактов»."""
        owners = ownership.group_owners(group)

        card = QFrame()
        card.setObjectName("prevCard")
        card.setStyleSheet(
            "QFrame#prevCard{background:#F8F9FA;border:1px solid #E5E7EB;border-radius:8px;}")
        card_lay = QHBoxLayout(card)
        card_lay.setContentsMargins(14, 12, 14, 12)
        card_lay.setSpacing(14)

        # ── Левая часть: период + ФИО + счётчики + отсутствующие документы ──
        left_w = QWidget()
        left_w.setStyleSheet("background:transparent;")
        left_lyt = QVBoxLayout(left_w)
        left_lyt.setContentsMargins(0, 0, 0, 0)
        left_lyt.setSpacing(4)

        since = ownership.group_since(group)
        until = ownership.group_until(group)
        since_txt = since.strftime("%d.%m.%Y") if since else "начало"
        until_txt = until.strftime("%d.%m.%Y") if until else "—"
        period_lbl = QLabel(f"Активна с {since_txt} по {until_txt}")
        period_lbl.setStyleSheet("font-size:11px; color:#9CA3AF; background:transparent;")
        left_lyt.addWidget(period_lbl)

        main = next((o for o in owners if isinstance(o, dict) and o.get("is_visible")),
                    owners[0] if owners else None)
        name = ownership.owner_name(main) if main else "(нет лиц)"
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet(
            "font-size:14px; font-weight:700; color:#07414F; background:transparent;")
        name_lbl.setWordWrap(True)
        left_lyt.addWidget(name_lbl)

        n_members = sum(1 for o in owners if isinstance(o, dict) and o.get("is_member"))
        if n_members:
            counts_lbl = QLabel(f"Члены СНТ: {n_members}")
            counts_lbl.setStyleSheet("font-size:12px; color:#6B7280; background:transparent;")
            left_lyt.addWidget(counts_lbl)

        missing = self._missing_docs_lines(owners)
        if missing:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("color:#E5E7EB; background:#E5E7EB; max-height:1px;")
            left_lyt.addWidget(sep)
            missing_lbl = QLabel("\n".join(missing))
            missing_lbl.setStyleSheet("font-size:12px; color:#9CA3AF; background:transparent;")
            left_lyt.addWidget(missing_lbl)
        left_lyt.addStretch()
        card_lay.addWidget(left_w, stretch=1)

        # ── Правая часть: долг на дату закрытия + «Список контактов» ──
        right_w = QWidget()
        right_w.setStyleSheet("background:transparent;")
        right_w.setFixedWidth(220)
        right_lyt = QVBoxLayout(right_w)
        right_lyt.setContentsMargins(0, 0, 0, 0)
        right_lyt.setSpacing(8)

        from core.utils import fmt_money
        debt_v = (group.get("debt_at_close") or {}).get("vznosy")
        debt_e = (group.get("debt_at_close") or {}).get("energy")
        debt_card = QFrame()
        debt_card.setStyleSheet(
            "QFrame{background:#FFFFFF;border:1px solid #E5E7EB;border-radius:6px;}")
        debt_lay = QVBoxLayout(debt_card)
        debt_lay.setContentsMargins(10, 8, 10, 8)
        debt_lay.setSpacing(4)
        debt_title = QLabel("Долг на дату закрытия")
        debt_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        debt_title.setStyleSheet(
            "font-size:11px; font-weight:600; color:#6B7280; background:transparent;")
        debt_lay.addWidget(debt_title)
        for cap, val in (("ЧВ:", debt_v), ("Электр.:", debt_e)):
            row = QHBoxLayout()
            row.addWidget(QLabel(cap))
            vlbl = QLabel(fmt_money(val) if val is not None else "—")
            vlbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            vlbl.setStyleSheet("font-size:12px; font-weight:600; background:transparent;")
            row.addWidget(vlbl, stretch=1)
            debt_lay.addLayout(row)
        right_lyt.addWidget(debt_card)

        btn_contacts = QPushButton("Список контактов")
        btn_contacts.setObjectName("btnSecondary")
        btn_contacts.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_contacts.clicked.connect(lambda _, g=group: self._on_edit_prev_group(g))
        right_lyt.addWidget(btn_contacts)
        right_lyt.addStretch()
        card_lay.addWidget(right_w)

        return card

    def _on_edit_prev_group(self, group: dict):
        """Редактирование состава предыдущей (архивной) группы — под-панель."""
        self._open_contacts(("prev", group), group, is_new=False)

    def _on_edit_active_group(self):
        """Открывает редактор контактов (под-панель)."""
        self._open_contacts(("active", None), self._active_group, is_new=False)

    def _on_toggle_contacts_full(self):
        """Переключает превью (3) ↔ полный список."""
        self._contacts_expanded = not self._contacts_expanded
        self._refresh_contacts_preview(
            ownership.group_owners(self._active_group))

    def _on_archive_active_group(self):
        if not ownership.group_owners(self._active_group):
            QMessageBox.warning(self, "Нет лиц",
                                "В активной группе нет ни одного лица. "
                                "Добавьте хотя бы одно перед архивированием.")
            return
        # Дата закрытия текущей = «Дата начала группы» новой (укажет в под-панели).
        new_active = {"since": date.today().isoformat(), "until": None, "owners": []}
        self._open_contacts(("replace", None), new_active, is_new=True)

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
                self._group_dirty = True
                self._refresh_active_card()
                self._update_save_state()
            elif kind == "prev":
                group = ctx[1]
                for i, g in enumerate(self._groups):
                    if g is group:
                        self._groups[i] = result
                        break
                self._group_dirty = True
                self._refresh_prev_section()
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
        """Архивирует текущую активную группу и ставит новую (из под-панели)."""
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
        self._group_dirty = True
        self._refresh_active_card()
        self._refresh_prev_section()
        self._update_save_state()

    def _compute_group_debt(self, as_of) -> dict:
        result = {"vznosy": 0.0, "energy": 0.0}
        plot_num = str(self._plot_data.get("num", ""))
        if not plot_num:
            return result
        since = ownership.group_since(self._active_group)
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

    def _update_save_state(self):
        if self._btn_save is None or self._is_edit:
            return
        has_owners = bool(ownership.group_owners(self._active_group))
        num_ok = bool(self.inp_num.text().strip()) and not self._is_num_taken()
        ok = has_owners and num_ok
        self._btn_save.setEnabled(ok)
        self._btn_save.setCursor(
            Qt.CursorShape.PointingHandCursor if ok else Qt.CursorShape.ArrowCursor)
        # Иконка-галочка: активная (бирюза) когда можно сохранить, иначе серая
        self._btn_save.setIcon(
            _mat_icon(0xE161, 18, fill=1, color="#07414F" if ok else "#9CA3AF"))

    def _on_accept(self):
        """Сохранение нового участка (кнопка «Сохранить» в футере)."""
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
        if not ownership.group_owners(self._active_group):
            QMessageBox.warning(self, "Ошибка",
                                "В активной группе должно быть хотя бы одно лицо")
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
