import json
import os
from datetime import date, datetime

import pandas as pd
from PyQt6.QtCore import (
    Qt, QDate, QEvent, QModelIndex, QAbstractItemModel, QObject, QPoint, QRect, QRectF,
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
    QMessageBox, QPushButton, QScrollArea, QScrollBar, QSizePolicy, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTableWidget, QTableWidgetItem, QTreeView, QVBoxLayout, QWidget,
)

from core import ownership
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
    "QLabel{background:#DCFCE7;color:#16A34A;border-radius:6px;"
    "padding:2px 10px;font-size:11px;}")


def _make_anchor_label(text: str, style: str):
    """Returns (label, row_layout, dirty_badge).
    row_layout — QHBoxLayout с меткой, тостом «Скопировано» и бейджем «Данные обновлены».
    Toast: label.property('_toast'). Dirty badge: управляется извне по _refresh_dirty_badges."""
    lbl = QLabel(text)
    lbl.setStyleSheet(style)

    def _retained(w):
        sp = w.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        w.setSizePolicy(sp)
        return w

    toast = _retained(QLabel("Скопировано"))
    toast.setStyleSheet(_SS_COPY_TOAST)
    toast.hide()
    lbl.setProperty("_toast", toast)

    dirty = _retained(QLabel("Данные обновлены"))
    dirty.setStyleSheet(_SS_DIRTY_BADGE)
    dirty.hide()

    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    row.addWidget(lbl)
    row.addWidget(toast)
    row.addWidget(dirty)
    row.addStretch()
    return lbl, row, dirty


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
                is_member: bool = False) -> dict:
    d: dict = {"name": name, "is_owner": is_owner}
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
        painter.setPen(QPen(self._BORDER, 1))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())

        if node is None:
            painter.restore()
            return

        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        n = len(node.plot_ref.get("owners", []) or []) if node.kind == "plot" else 0

        f_text = QFont()
        f_text.setPixelSize(13)

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
        view.viewport().installEventFilter(self)

    def _ic_rect(self, cell_rect: QRect) -> QRect:
        return QRect(cell_rect.right() - 22, cell_rect.top(), 20, cell_rect.height())

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
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
#  PlotsWidget                                                                 #
# ============================================================================ #

class PlotsWidget(QWidget):
    """Вкладка участков: ручное добавление и управление списком."""

    plotsUpdated = pyqtSignal()

    DATA_FILE = os.path.join(DATA_DIR, "snt_plots.json")

    def __init__(self):
        super().__init__()
        self.setAutoFillBackground(True)
        self._plots: list = self._load()
        self._df = None                       # выписка — нужна мастеру для снимка долга
        self._search_filters: dict[int, str] = {}
        self._setup_ui()
        self._rebuild_table()

    def refresh(self, df):
        """Принимает загруженную выписку (для расчёта долга в мастере смены)."""
        self._df = df

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
                return data
        except Exception:
            pass
        return []

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
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        top = QHBoxLayout()
        top.addStretch()
        btn_import = QPushButton("📥  Импорт из Excel")
        btn_import.setObjectName("btnSecondary")
        btn_import.clicked.connect(self._import_from_excel)
        top.addWidget(btn_import)
        btn_add = QPushButton("＋  Добавить участок")
        btn_add.setObjectName("btnPrimary")
        btn_add.clicked.connect(self._add_plot)
        top.addWidget(btn_add)
        layout.addLayout(top)

        self.model = PlotsTreeModel(self)
        self.model.ownerDataEdited.connect(self._save)

        # ── Блок 1: шапка в своей рамке ──────────────────────────────────────
        self.hdr_view = _SortHeaderView()
        self.hdr_view.setModel(self.model)
        self.hdr_view.sortIndicatorChanged.connect(self._on_sort_changed)

        hdr_frame = QFrame()
        hdr_frame.setStyleSheet("background: #C9D8E2; border: none;")
        hdr_inner = QHBoxLayout(hdr_frame)
        hdr_inner.setContentsMargins(0, 0, 0, 0)
        hdr_inner.setSpacing(0)
        hdr_inner.addWidget(self.hdr_view)
        # Плейсхолдер под скроллбар — выравнивает шапку с телом таблицы
        sb_stub = QWidget()
        sb_stub.setFixedWidth(_SB_W)
        sb_stub.setStyleSheet("background: #C9D8E2; border: none;")
        hdr_inner.addWidget(sb_stub)

        # ── Блок 2: строки таблицы в своей рамке ─────────────────────────────
        self.tree = QTreeView(objectName="mainTable")
        self.tree.setModel(self.model)
        self.tree.header().hide()   # скрываем встроенную шапку
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setSortingEnabled(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setMouseTracking(True)
        self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.tree.setStyleSheet(_TREE_STYLE)

        fio_col_idx = PlotsTreeModel.COLUMNS.index("Контакт")
        self._fio_delegate = _FioDelegate(self.tree)
        self._fio_delegate.toggleRequested.connect(self._on_toggle)
        self.tree.setItemDelegateForColumn(fio_col_idx, self._fio_delegate)

        plot_col_idx = PlotsTreeModel.COLUMNS.index("№")
        self._plot_num_delegate = _PlotNumDelegate(self.tree)
        self.tree.setItemDelegateForColumn(plot_col_idx, self._plot_num_delegate)

        edit_col_idx = PlotsTreeModel.COLUMNS.index("_edit")
        self._edit_btn_delegate = _EditBtnDelegate(self.tree)
        self.tree.setItemDelegateForColumn(edit_col_idx, self._edit_btn_delegate)
        self.tree.clicked.connect(self._on_tree_clicked)

        check_col_idx = PlotsTreeModel.COLUMNS.index("_check")
        self._check_delegate = _CheckDelegate(self.tree)
        self.tree.setItemDelegateForColumn(check_col_idx, self._check_delegate)
        self._check_delegate.selectionChanged.connect(self._on_selection_changed)

        self.hdr_view.set_delete_col(check_col_idx)
        self.hdr_view.deleteRequested.connect(self._delete_selected)

        # Синхронизация ширин колонок между hdr_view и tree
        self.tree.header().sectionResized.connect(self._on_tree_hdr_resized)
        self.hdr_view.sectionResized.connect(self._on_hdr_view_resized)
        self._col_syncing = False

        # Поиск в шапке для столбцов № и Контакт
        self.hdr_view.add_search_col(PlotsTreeModel.COLUMNS.index("№"))
        self.hdr_view.add_search_col(PlotsTreeModel.COLUMNS.index("Контакт"))
        self.hdr_view.searchChanged.connect(self._on_search_changed)

        # ── Единый внешний контейнер: клипирует шапку + тело вместе ─────────
        table_outer = _ClipFrame(QColor("#D5DCE4"), 6)
        outer_inner = QVBoxLayout(table_outer)
        outer_inner.setContentsMargins(0, 0, 0, 0)
        outer_inner.setSpacing(0)
        outer_inner.addWidget(hdr_frame)
        outer_inner.addWidget(self.tree, stretch=1)
        table_outer.finish_setup()

        self.status_label = QLabel("", objectName="statusLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight)

        table_vbox = QVBoxLayout()
        table_vbox.setSpacing(4)
        table_vbox.setContentsMargins(0, 0, 0, 0)
        table_vbox.addWidget(table_outer, stretch=1)
        table_vbox.addWidget(self.status_label)
        layout.addLayout(table_vbox)

    def _rebuild_table(self):
        sort_col   = self.hdr_view.sortIndicatorSection()
        sort_order = self.hdr_view.sortIndicatorOrder()

        # Применяем поисковые фильтры
        plots = self._plots
        for col, text in self._search_filters.items():
            if not text:
                continue
            if col == PlotsTreeModel.COLUMNS.index("№"):
                plots = [p for p in plots if text in str(p.get("num", "")).lower()]
            elif col == PlotsTreeModel.COLUMNS.index("Контакт"):
                plots = [p for p in plots
                         if any(text in _owner_name(o).lower()
                                for o in p.get("owners", []))]

        self.model.load(plots)

        if sort_col < 0 or sort_col >= len(PlotsTreeModel.COLUMNS):
            sort_col   = 0
            sort_order = Qt.SortOrder.AscendingOrder
        self.model.sort(sort_col, sort_order)
        self.hdr_view.setSortIndicator(sort_col, sort_order)

        col_участок  = PlotsTreeModel.COLUMNS.index("№")
        col_fio      = PlotsTreeModel.COLUMNS.index("Контакт")
        col_area     = PlotsTreeModel.COLUMNS.index("Площадь, м²")
        col_phone    = PlotsTreeModel.COLUMNS.index("Контактный номер")
        col_email    = PlotsTreeModel.COLUMNS.index("E-mail")
        col_edit     = PlotsTreeModel.COLUMNS.index("_edit")
        col_check    = PlotsTreeModel.COLUMNS.index("_check")

        # Одинаковые режимы на обоих хедерах — для выравнивания колонок
        for h in (self.hdr_view, self.tree.header()):
            h.setStretchLastSection(False)
            h.setSectionResizeMode(col_участок,  QHeaderView.ResizeMode.Fixed)
            h.resizeSection(col_участок, 140)
            h.setSectionResizeMode(col_fio,      QHeaderView.ResizeMode.Stretch)
            h.setSectionResizeMode(col_area,     QHeaderView.ResizeMode.Fixed)
            h.resizeSection(col_area, 120)
            h.setSectionResizeMode(col_phone,    QHeaderView.ResizeMode.Fixed)
            h.resizeSection(col_phone, 160)
            h.setSectionResizeMode(col_email,    QHeaderView.ResizeMode.Fixed)
            h.resizeSection(col_email, 160)
            h.setSectionResizeMode(col_edit,     QHeaderView.ResizeMode.Fixed)
            h.resizeSection(col_edit, 46)
            h.setSectionResizeMode(col_check,    QHeaderView.ResizeMode.Fixed)
            h.resizeSection(col_check, 46)

        total    = len(self._plots)
        filtered = len(plots)
        if filtered < total:
            self.status_label.setText(f"Участков: {filtered} из {total}")
        else:
            self.status_label.setText(f"Участков: {total}")

        # Индикаторы в шапке «№»
        n_no_egrn = sum(1 for p in plots if not p.get("egrn_doc", ""))
        self.hdr_view.set_col_indicators(col_участок, [
            (chr(0xE73A), n_no_egrn, f"Нет выписки ЕГРН: {n_no_egrn}"),
        ])

        # Индикаторы в шапке «Контакт»
        all_owners = [o for p in plots for o in p.get("owners", []) if _is_owner(o)]
        miss_m = sum(1 for o in all_owners if not _owner_member_doc(o))
        miss_o = sum(1 for o in all_owners if not _owner_opd_doc(o))
        miss_a = sum(1 for o in all_owners if _owner_area(o) is None)
        self.hdr_view.set_col_indicators(col_fio, [
            (chr(0xF567), miss_m, f"Нет заявления о вступлении: {miss_m}"),
            (chr(0xF0DC), miss_o, f"Нет заявления на ОПД: {miss_o}"),
            ("м²",        miss_a, f"Площадь не заполнена: {miss_a}"),
        ])

    def _on_sort_changed(self, col: int, order: Qt.SortOrder):
        if col in (PlotsTreeModel.COLUMNS.index("_edit"),
                   PlotsTreeModel.COLUMNS.index("_check")):
            return
        self.model.sort(col, order)
        self.hdr_view.setSortIndicator(col, order)

    def _on_search_changed(self, col: int, text: str):
        self._search_filters[col] = text.strip().lower()
        self._rebuild_table()

    def _on_tree_hdr_resized(self, li: int, _old: int, new: int):
        if self._col_syncing:
            return
        self._col_syncing = True
        self.hdr_view.resizeSection(li, new)
        self._col_syncing = False

    def _on_hdr_view_resized(self, li: int, _old: int, new: int):
        if self._col_syncing:
            return
        self._col_syncing = True
        self.tree.setColumnWidth(li, new)
        self._col_syncing = False

    def _on_tree_clicked(self, index: QModelIndex):
        if index.column() == PlotsTreeModel.COLUMNS.index("_edit"):
            node = index.internalPointer()
            if node and node.kind == "plot":
                self._edit_plot(node.plot_ref)

    def _on_selection_changed(self):
        has = bool(self._check_delegate.get_selected())
        self.hdr_view.set_has_selection(has)

    def _delete_selected(self):
        pids = self._check_delegate.get_selected()
        if not pids:
            return
        msg = QMessageBox(self)
        msg.setWindowTitle("Удаление участков")
        msg.setText("Вы уверены, что хотите удалить выбранные участки?")
        msg.setInformativeText(
            "Внимание! Будет удалена вся информация, "
            "в том числе ранее сохранённые документы."
        )
        msg.setIcon(QMessageBox.Icon.Warning)
        btn_yes = msg.addButton("Да, удалить", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("Нет", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_yes)
        msg.exec()
        if msg.clickedButton() is not btn_yes:
            return
        self._plots = [
            p for p in self._plots
            if str(p.get("num", "")) not in pids
        ]
        self._check_delegate.clear_selection()
        self._save()
        self._rebuild_table()

    def _on_toggle(self, index: QModelIndex):
        col0 = self.model.index(index.row(), 0, self.model.parent(index))
        if self.tree.isExpanded(col0):
            self.tree.collapse(col0)
        else:
            self.tree.expand(col0)

    def _edit_plot(self, plot: dict):
        dlg = PlotEditDialog(plot_data=plot, parent=self, df=self._df)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            result = dlg.get_result()
            if result:
                for i, p in enumerate(self._plots):
                    if p is plot:
                        self._plots[i] = result
                        break
                self._save()
                self._rebuild_table()

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
        dlg = PlotEditDialog(plot_data=None, parent=self, df=self._df)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            result = dlg.get_result()
            if result:
                self._plots.append(result)
                self._save()
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

class GroupEditDialog(QDialog):
    """Редактор состава группы — сворачиваемые карточки."""

    def __init__(self, group: dict, is_new: bool = False, parent=None):
        super().__init__(parent)
        self._group = group
        self._is_new = is_new
        self.setWindowTitle("Новая группа" if is_new else "Состав группы")
        self.setFixedWidth(720)
        self.setModal(True)
        self._cards: list[dict] = []
        self._primary_idx = 0
        self._btn_save = None
        self._btn_switch_edit: QPushButton | None = None  # удалён, оставлен для совместимости
        self._footer_view: QWidget | None = None
        self._footer_edit: QWidget | None = None
        self._warning: QLabel | None = None
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(10)

        # -- Дата начала группы --
        date_row = QHBoxLayout()
        date_row.setSpacing(10)
        lbl_since = QLabel("Дата начала группы:")
        lbl_since.setStyleSheet("color:#374151; font-size:13px;")
        date_row.addWidget(lbl_since)
        self._has_since = QCheckBox("Указать дату")
        since_val = ownership.group_since(self._group)
        self._has_since.setChecked(since_val is not None)
        date_row.addWidget(self._has_since)
        self._since_edit = QDateEdit(calendarPopup=True)
        self._since_edit.setDisplayFormat("dd.MM.yyyy")
        if since_val:
            self._since_edit.setDate(QDate(since_val.year, since_val.month, since_val.day))
        else:
            self._since_edit.setDate(QDate.currentDate())
        self._since_edit.setEnabled(since_val is not None)
        self._has_since.toggled.connect(self._since_edit.setEnabled)
        date_row.addWidget(self._since_edit)
        date_row.addStretch()
        lay.addLayout(date_row)

        # -- Scroll area for person cards --
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        # Применяем стиль напрямую на виджет скроллбара, чтобы он не перекрывался
        # stylesheet диалога. setFixedWidth гарантирует физический резерв в layout.
        _vsb = scroll.verticalScrollBar()
        _vsb.setFixedWidth(10)
        _vsb.setStyleSheet("""
            QScrollBar:vertical {
                background: #E5E9ED;
                width: 10px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #9CA3AF;
                border-radius: 4px;
                min-height: 30px;
                margin: 1px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical { background: none; }
        """)

        self._cards_container = QWidget()
        self._cards_container.setStyleSheet("background:transparent;")
        self._cards_vlay = QVBoxLayout(self._cards_container)
        self._cards_vlay.setSpacing(6)
        self._cards_vlay.setContentsMargins(0, 0, 16, 0)
        self._cards_vlay.addStretch()

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

        # -- Кнопка добавить --
        self._btn_add = QPushButton("＋  Добавить лицо")
        self._btn_add.setObjectName("btnSecondary")
        self._btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_add.clicked.connect(self._on_add_person)
        lay.addWidget(self._btn_add)

        # -- Предупреждение --
        self._warning = QLabel()
        self._warning.setStyleSheet("color:#DC2626; background:transparent; font-size:12px;")
        self._warning.setWordWrap(True)
        lay.addWidget(self._warning)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#E5E7EB;background:#E5E7EB;max-height:1px;")
        lay.addWidget(sep)

        # Футер: только "Закрыть" (сохранение — через per-card кнопки)
        self._footer_view = QWidget()
        fv_lay = QHBoxLayout(self._footer_view)
        fv_lay.setContentsMargins(0, 0, 0, 0)
        fv_lay.addStretch()
        btn_close = QPushButton("Закрыть")
        btn_close.setObjectName("btnFooterClose")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(self.reject)
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
        """Обновляет доступность поля даты по текущему состоянию карточек."""
        if self._footer_view is None:
            return  # вызван до завершения _setup_ui
        editing = self._any_editing()
        self._has_since.setEnabled(editing)
        self._since_edit.setEnabled(editing and self._has_since.isChecked())
        self._update_save_state()

    def _set_card_edit_mode(self, cd: dict, mode: bool):
        cd["is_editing"] = mode
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
        cd = self._add_owner_card({}, is_primary=not self._cards, expanded=True,
                                   start_editing=True)
        # Collapse все остальные
        for other in self._cards:
            if other is not cd and not other["is_collapsed"]:
                self._apply_collapse(other, collapsed=True)

    def _add_owner_card(self, owner: dict, *, is_primary: bool = False,
                        expanded: bool = False, start_editing: bool = False):
        cd: dict = {"is_collapsed": not expanded, "is_editing": False}

        card = QFrame()
        card.setObjectName("personCard")
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
            "padding:3px 10px;font-size:12px;}"
            "QPushButton:hover{background:#F3F4F6;border-color:#9CA3AF;}")
        btn_cancel.clicked.connect(lambda _, c=cd: self._cancel_card_edit(c))
        cd["btn_cancel"] = btn_cancel
        hdr_lyt.addWidget(btn_cancel)

        btn_save_card = QPushButton("Сохранить")
        btn_save_card.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save_card.setFixedHeight(26)
        btn_save_card.setStyleSheet(
            "QPushButton{background:#07414F;color:white;border:none;"
            "border-radius:6px;padding:3px 10px;font-size:12px;}"
            "QPushButton:hover{background:#0B5A6E;}"
            "QPushButton:disabled{background:#E5E7EB;color:#9CA3AF;}")
        btn_save_card.clicked.connect(lambda _, c=cd: self._set_card_edit_mode(c, False))
        cd["btn_save_card"] = btn_save_card
        hdr_lyt.addWidget(btn_save_card)

        # ── Краткое имя (только в свёрнутом состоянии) ───────────────────
        name_summary = QLabel()
        name_summary.setStyleSheet("font-size:12px; color:#374151; background:transparent;")
        cd["name_summary"] = name_summary
        hdr_lyt.addWidget(name_summary)
        hdr_lyt.addStretch(1)

        # ── Теги статуса (только в свёрнутом состоянии) ──────────────────
        tags_w = QWidget()
        tags_w.setStyleSheet("background:transparent;")
        tags_lyt = QHBoxLayout(tags_w)
        tags_lyt.setContentsMargins(0, 0, 0, 0)
        tags_lyt.setSpacing(4)
        tag_own = QLabel("Собственник")
        tag_own.setStyleSheet(
            "font-size:10px; padding:1px 7px; border-radius:99px;"
            "background:#C9D8E2; color:#07414F;")
        tag_mem = QLabel("Член СНТ")
        tag_mem.setStyleSheet(
            "font-size:10px; padding:1px 7px; border-radius:99px;"
            "background:#D6EBD5; color:#2E7D32;")
        tags_lyt.addWidget(tag_own)
        tags_lyt.addWidget(tag_mem)
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
        name_col.addLayout(_fio_lbl_row)
        cd["name_dirty"] = _fio_dirty
        name_row_h = QHBoxLayout()
        name_row_h.setContentsMargins(0, 0, 0, 0)
        name_row_h.setSpacing(4)
        name_inp = QLineEdit(raw_name)
        name_inp.setPlaceholderText("Фамилия Имя Отчество")
        name_inp.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        name_inp.textChanged.connect(self._update_save_state)
        name_inp.textChanged.connect(lambda _, c=cd: self._update_name_summary(c))
        name_row_h.addWidget(name_inp)
        name_row_h.addWidget(_make_copy_btn(name_inp, lbl_fio))
        name_col.addLayout(name_row_h)
        cd["name_inp"] = name_inp
        content_lyt.addLayout(name_col)

        # Строка 2: Телефон / E-mail
        contact_row = QHBoxLayout()
        contact_row.setSpacing(8)
        phone_val = owner.get("phone", "") if isinstance(owner, dict) else ""
        email_val = owner.get("email", "") if isinstance(owner, dict) else ""
        for label_txt, placeholder, val, key in [
            ("Телефон", "+7 (xxx) xxx-xx-xx", phone_val, "phone"),
            ("E-mail", "email@example.com", email_val, "email"),
        ]:
            col = QVBoxLayout()
            col.setSpacing(3)
            lbl_contact, _contact_lbl_row, _contact_dirty = _make_anchor_label(
                label_txt, "font-size:12px; color:#6B7280; background:transparent;")
            col.addLayout(_contact_lbl_row)
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
            _dw.path_changed.connect(lambda _: self._update_save_state())
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
            docs_grid.addLayout(_lbl_d_row,      0, col_idx)
            docs_grid.addLayout(_doc_row(doc_w), 1, col_idx)
            cd[del_key.replace("_del", "_dirty")] = _doc_dirty
            cd[del_key] = doc_w.del_btn

        _, _lbl_mem_row, _mem_dirty = _make_anchor_label("Заявление в СНТ", _lbl_ss)
        docs_grid.addLayout(_lbl_mem_row,    2, 0)
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
        if cd.get("is_editing", False):
            return  # нельзя свернуть в режиме редактирования
        self._apply_collapse(cd, collapsed=not cd["is_collapsed"])

    def _apply_collapse(self, cd: dict, *, collapsed: bool):
        cd["is_collapsed"] = collapsed
        cd["content"].setVisible(not collapsed)
        cd["name_summary"].setVisible(collapsed)
        cd["tags_w"].setVisible(collapsed)
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
        name = cd["name_inp"].text().strip() or "(без имени)"
        cd["name_summary"].setText(name)

    def _update_tags(self, cd: dict):
        cd["tag_own"].setVisible(cd["rb_owner"].isChecked())
        cd["tag_mem"].setVisible(cd["rb_member"].isChecked())

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
        if self._warning is None:
            return  # вызван до завершения _setup_ui
        has_any = bool(self._cards)
        all_named = all(cd["name_inp"].text().strip() for cd in self._cards)
        ok = has_any and all_named
        for cd in self._cards:
            btn = cd.get("btn_save_card")
            if btn is not None:
                enabled = ok and self._is_card_dirty(cd)
                btn.setEnabled(enabled)
                btn.setCursor(
                    Qt.CursorShape.PointingHandCursor if enabled
                    else Qt.CursorShape.ArrowCursor)
        if not has_any:
            self._warning.setText("Добавьте хотя бы одно лицо")
        elif not all_named:
            self._warning.setText("Заполните хотя бы фамилию или имя для каждого лица")
        else:
            self._warning.setText("")
        for cd in self._cards:
            self._refresh_dirty_badges(cd)

    def _on_accept(self):
        owners = []
        for i, cd in enumerate(self._cards):
            name = cd["name_inp"].text().strip()
            if not name:
                continue
            owners.append(_make_owner(
                name,
                cd["rb_owner"].isChecked(),
                None,
                cd["rb_contact"].isChecked(),
                cd["member_doc"].get_path(),
                cd["opd_doc"].get_path(),
                cd["phone"].text().strip(),
                cd["email"].text().strip(),
                egrn_doc=cd["egrn_doc"].get_path(),
                is_member=cd["rb_member"].isChecked(),
            ))

        since_iso = None
        if self._has_since.isChecked():
            since_iso = self._since_edit.date().toPyDate().isoformat()

        result = dict(self._group)
        result["owners"] = owners
        result["since"] = since_iso
        self._result = result
        self.accept()

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
                border-radius: 6px; padding: 7px 14px; font-size: 13px;
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
#  GroupArchiveDialog — список архивных групп                                 #
# ============================================================================ #

class GroupArchiveDialog(QDialog):
    """Диалог со списком всех архивных групп участка.

    Позволяет просмотреть историю и при необходимости сделать группу активной
    (что архивирует текущую активную). Сигнал ``groups_changed`` испускается,
    если пользователь изменил состав групп.
    """

    groups_changed = pyqtSignal(list)  # новый список groups

    def __init__(self, plot_data: dict, active_group: dict, df=None, parent=None):
        super().__init__(parent)
        self._plot_data = plot_data
        self._active_group = active_group
        self._df = df
        self._groups = list(ownership.plot_groups(plot_data))
        self.setWindowTitle("Архив групп")
        self.setMinimumWidth(680)
        self.setModal(True)
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        title = QLabel("Архивные группы участка")
        title.setStyleSheet(
            "font-size:15px; font-weight:600; color:#374151; background:transparent;")
        lay.addWidget(title)

        archived = ownership.archived_groups({"groups": self._groups})
        if not archived:
            lbl = QLabel("Архивных групп нет.")
            lbl.setStyleSheet("color:#9CA3AF; background:transparent; font-size:13px;")
            lay.addWidget(lbl)
        else:
            for group in archived:
                lay.addWidget(self._make_group_row(group))

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#E5E7EB;background:#E5E7EB;max-height:1px;")
        lay.addWidget(sep)

        close_btn = QPushButton("Закрыть")
        close_btn.setObjectName("btnSecondary")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        lay.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def _make_group_row(self, group: dict) -> QWidget:
        card = QFrame()
        card.setStyleSheet(
            "QFrame{background:#F8F9FA;border:1px solid #E5E7EB;border-radius:8px;}"
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)

        names = ownership.group_label(group, empty="(без ФИО)")
        since = ownership.group_since(group)
        until = ownership.group_until(group)
        since_txt = since.strftime("%d.%m.%Y") if since else "начало"
        until_txt = until.strftime("%d.%m.%Y") if until else "—"

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        name_lbl = QLabel(names)
        name_lbl.setStyleSheet(
            "font-size:13px; font-weight:600; color:#374151; background:transparent;")
        name_lbl.setWordWrap(True)
        top_row.addWidget(name_lbl, stretch=1)
        period_lbl = QLabel(f"{since_txt} — {until_txt}")
        period_lbl.setStyleSheet(
            "font-size:12px; color:#6B7280; background:transparent;")
        top_row.addWidget(period_lbl)
        cl.addLayout(top_row)

        debt_v = (group.get("debt_at_close") or {}).get("vznosy")
        debt_e = (group.get("debt_at_close") or {}).get("energy")
        if debt_v is not None or debt_e is not None:
            from core.utils import fmt_money
            parts = []
            if debt_v is not None:
                parts.append(f"ЧВ: {fmt_money(debt_v)}")
            if debt_e is not None:
                parts.append(f"Электр.: {fmt_money(debt_e)}")
            debt_lbl = QLabel("Долг на дату закрытия: " + "  ·  ".join(parts))
            debt_lbl.setStyleSheet(
                "font-size:12px; color:#6B7280; background:transparent;")
            cl.addWidget(debt_lbl)

        return card

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog { background: #FFFFFF; color: #374151; }
            QLabel  { background: transparent; color: #374151; font-size: 13px; }
            QLineEdit {
                background: #F8F9FA; border: 1px solid #D1D5DB;
                border-radius: 5px; color: #374151; padding: 6px 10px; font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid #07414F; }
            QLineEdit[readOnly="true"] {
                background: transparent; border: none;
                color: #374151; padding: 6px 0;
            }
            QPushButton#btnSecondary {
                background: #E5E7EB; color: #6B7280; border: 1px solid #D1D5DB;
                border-radius: 6px; padding: 7px 14px; font-size: 13px;
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
        """)


# ============================================================================ #
#  PlotEditDialog                                                              #
# ============================================================================ #

class PlotEditDialog(QDialog):
    """Диалог добавления / редактирования участка (модель групп)."""

    _IC_FONT = "Material Symbols Rounded"

    def __init__(self, plot_data: dict | None = None, parent=None, df=None):
        super().__init__(parent)
        self._is_edit = plot_data is not None
        self._plot_data = dict(plot_data or {})
        self._df = df

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

        self.setWindowTitle("Редактировать участок" if self._is_edit else "Новый участок")
        self.setMinimumWidth(680)
        self.setModal(True)
        self._btn_save = None
        self._btn_switch_edit: QPushButton | None = None
        self._edit_mode: bool = not self._is_edit  # новый участок сразу в режиме редактирования
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        # -- Кнопка перехода в режим редактирования (только для существующего участка) --
        if self._is_edit:
            mode_bar = QHBoxLayout()
            mode_bar.setContentsMargins(0, 0, 0, 0)
            mode_bar.addStretch()
            self._btn_switch_edit = QPushButton("✎  Редактировать")
            self._btn_switch_edit.setObjectName("btnEditMode")
            self._btn_switch_edit.setCursor(Qt.CursorShape.PointingHandCursor)
            self._btn_switch_edit.clicked.connect(lambda: self._set_edit_mode(True))
            mode_bar.addWidget(self._btn_switch_edit)
            lay.addLayout(mode_bar)

        # -- Номер + Площадь в одну строку --
        fields_row = QHBoxLayout()
        fields_row.setSpacing(16)

        num_lyt = QVBoxLayout()
        num_lyt.setSpacing(4)
        num_lyt.addWidget(QLabel("Номер участка:"))
        self._lbl_num = QLabel(str(self._plot_data.get("num", "") or "—"))
        self._lbl_num.setStyleSheet(
            "font-size:14px; font-weight:600; color:#07414F; background:transparent;")
        num_lyt.addWidget(self._lbl_num)
        self.inp_num = QLineEdit(str(self._plot_data.get("num", "")))
        self.inp_num.setPlaceholderText("например: 15 или 15/207")
        self.inp_num.textChanged.connect(self._update_save_state)
        num_lyt.addWidget(self.inp_num)
        fields_row.addLayout(num_lyt, stretch=1)

        area_raw = self._plot_data.get("area")
        area_text = ""
        if area_raw not in (None, "", 0):
            try:
                area_text = f"{float(area_raw):g}"
            except (TypeError, ValueError):
                area_text = str(area_raw)
        area_lyt = QVBoxLayout()
        area_lyt.setSpacing(4)
        area_lyt.addWidget(QLabel("Площадь, м²:"))
        self._lbl_area = QLabel((area_text + " м²") if area_text else "—")
        self._lbl_area.setStyleSheet(
            "font-size:14px; font-weight:600; color:#07414F; background:transparent;")
        area_lyt.addWidget(self._lbl_area)
        self.inp_area = QLineEdit(area_text)
        self.inp_area.setPlaceholderText("например: 612")
        self.inp_area.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"^\d{0,5}([.,]\d{0,2})?$"), self.inp_area
        ))
        self.inp_area.textEdited.connect(lambda t: (
            self.inp_area.setText(t.replace(".", ",")),
            self.inp_area.setCursorPosition(self.inp_area.cursorPosition()),
        ) if "." in t else None)
        area_lyt.addWidget(self.inp_area)
        fields_row.addLayout(area_lyt, stretch=1)
        lay.addLayout(fields_row)

        # -- Заголовок активной группы --
        act_hdr = QHBoxLayout()
        act_hdr.setContentsMargins(0, 4, 0, 0)
        lbl_act = QLabel("Активная группа")
        lbl_act.setStyleSheet(
            "font-size:12px; font-weight:600; color:#6B7280; background:transparent;")
        act_hdr.addWidget(lbl_act, stretch=1)
        self._active_since_lbl = QLabel()
        self._active_since_lbl.setStyleSheet(
            "font-size:12px; color:#6B7280; background:transparent;")
        act_hdr.addWidget(self._active_since_lbl)
        lay.addLayout(act_hdr)

        # -- Карточка активной группы --
        self._active_card = QFrame()
        self._active_card.setStyleSheet(
            "QFrame#activeCard{"
            "background:#EBF4F6;border:1px solid #C9D8E2;border-radius:8px;}"
        )
        self._active_card.setObjectName("activeCard")
        card_lay = QHBoxLayout(self._active_card)
        card_lay.setContentsMargins(14, 12, 14, 12)
        card_lay.setSpacing(14)

        # Левая часть: ФИО + счётчики + отсутствующие документы
        left_w = QWidget()
        left_w.setStyleSheet("background:transparent;")
        left_lyt = QVBoxLayout(left_w)
        left_lyt.setContentsMargins(0, 0, 0, 0)
        left_lyt.setSpacing(4)

        self._active_name_lbl = QLabel()
        self._active_name_lbl.setStyleSheet(
            "font-size:14px; font-weight:700; color:#07414F; background:transparent;")
        self._active_name_lbl.setWordWrap(True)
        left_lyt.addWidget(self._active_name_lbl)

        self._active_counts_lbl = QLabel()
        self._active_counts_lbl.setStyleSheet(
            "font-size:12px; color:#6B7280; background:transparent;")
        left_lyt.addWidget(self._active_counts_lbl)

        sep_line = QFrame()
        sep_line.setFrameShape(QFrame.Shape.HLine)
        sep_line.setStyleSheet("color:#C9D8E2; background:#C9D8E2; max-height:1px;")
        left_lyt.addWidget(sep_line)

        self._active_missing_lbl = QLabel()
        self._active_missing_lbl.setStyleSheet(
            "font-size:12px; color:#9CA3AF; background:transparent;")
        left_lyt.addWidget(self._active_missing_lbl)
        left_lyt.addStretch()
        card_lay.addWidget(left_w, stretch=1)

        # Правая часть: карточка долга + кнопка редактирования
        right_w = QWidget()
        right_w.setStyleSheet("background:transparent;")
        right_w.setFixedWidth(220)
        right_lyt = QVBoxLayout(right_w)
        right_lyt.setContentsMargins(0, 0, 0, 0)
        right_lyt.setSpacing(8)

        # Карточка долга
        self._debt_card = QFrame()
        self._debt_card.setStyleSheet(
            "QFrame{background:#FFFFFF;border:1px solid #C9D8E2;border-radius:6px;}")
        debt_lay = QVBoxLayout(self._debt_card)
        debt_lay.setContentsMargins(10, 8, 10, 8)
        debt_lay.setSpacing(4)
        debt_title = QLabel("Долг / Аванс")
        debt_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        debt_title.setStyleSheet(
            "font-size:11px; font-weight:600; color:#6B7280; background:transparent;")
        debt_lay.addWidget(debt_title)

        vzn_row = QHBoxLayout()
        vzn_row.addWidget(QLabel("ЧВ:"))
        self._debt_vzn_lbl = QLabel("—")
        self._debt_vzn_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._debt_vzn_lbl.setStyleSheet("font-size:12px; font-weight:600; background:transparent;")
        vzn_row.addWidget(self._debt_vzn_lbl, stretch=1)
        debt_lay.addLayout(vzn_row)

        en_row = QHBoxLayout()
        en_row.addWidget(QLabel("Электр.:"))
        self._debt_en_lbl = QLabel("—")
        self._debt_en_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._debt_en_lbl.setStyleSheet("font-size:12px; font-weight:600; background:transparent;")
        en_row.addWidget(self._debt_en_lbl, stretch=1)
        debt_lay.addLayout(en_row)
        right_lyt.addWidget(self._debt_card)

        btn_edit_group = QPushButton("Редактировать состав")
        btn_edit_group.setObjectName("btnSecondary")
        btn_edit_group.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_edit_group.clicked.connect(self._on_edit_active_group)
        right_lyt.addWidget(btn_edit_group)
        right_lyt.addStretch()
        card_lay.addWidget(right_w)

        lay.addWidget(self._active_card)
        self._refresh_active_card()

        # -- Кнопка архивирования (только для существующего участка) --
        if self._is_edit:
            btn_archive_group = QPushButton(
                "Отправить активную группу в архив и добавить новую")
            btn_archive_group.setObjectName("btnSecondary")
            btn_archive_group.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_archive_group.clicked.connect(self._on_archive_active_group)
            lay.addWidget(btn_archive_group)

        # -- Предыдущая группа --
        self._prev_section = QWidget()
        self._prev_section.setStyleSheet("background:transparent;")
        prev_lyt = QVBoxLayout(self._prev_section)
        prev_lyt.setContentsMargins(0, 0, 0, 0)
        prev_lyt.setSpacing(6)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#E5E7EB;background:#E5E7EB;max-height:1px;")
        prev_lyt.addWidget(sep)

        prev_hdr = QHBoxLayout()
        lbl_prev = QLabel("Предыдущая группа")
        lbl_prev.setStyleSheet(
            "font-size:12px; font-weight:600; color:#6B7280; background:transparent;")
        prev_hdr.addWidget(lbl_prev, stretch=1)
        self._prev_until_lbl = QLabel()
        self._prev_until_lbl.setStyleSheet(
            "font-size:12px; color:#6B7280; background:transparent;")
        prev_hdr.addWidget(self._prev_until_lbl)
        prev_lyt.addLayout(prev_hdr)

        self._prev_card_container = QWidget()
        self._prev_card_container.setStyleSheet("background:transparent;")
        self._prev_card_lyt = QVBoxLayout(self._prev_card_container)
        self._prev_card_lyt.setContentsMargins(0, 0, 0, 0)
        prev_lyt.addWidget(self._prev_card_container)

        btn_view_arch = QPushButton("Перейти в архив групп")
        btn_view_arch.setObjectName("btnSecondary")
        btn_view_arch.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_view_arch.clicked.connect(self._on_view_archive)
        prev_lyt.addWidget(btn_view_arch)

        lay.addWidget(self._prev_section)
        self._refresh_prev_section()

        # -- Footer --
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color:#E5E7EB;background:#E5E7EB;max-height:1px;")
        lay.addWidget(sep2)

        # Режим просмотра: только "Закрыть"
        self._footer_view = QWidget()
        fv_lay = QHBoxLayout(self._footer_view)
        fv_lay.setContentsMargins(0, 0, 0, 0)
        fv_lay.addStretch()
        btn_close = QPushButton("Закрыть")
        btn_close.setObjectName("btnFooterClose")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(self.reject)
        fv_lay.addWidget(btn_close)
        lay.addWidget(self._footer_view)

        # Режим редактирования: "Отмена" + "Сохранить"
        self._footer_edit = QWidget()
        fe_lay = QHBoxLayout(self._footer_edit)
        fe_lay.setContentsMargins(0, 0, 0, 0)
        fe_lay.addStretch()
        btn_cancel = QPushButton("Отмена")
        btn_cancel.setObjectName("btnFooterCancel")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)
        fe_lay.addWidget(btn_cancel)
        self._btn_save = QPushButton("Сохранить")
        self._btn_save.setObjectName("btnFooterSave")
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save.clicked.connect(self._on_accept)
        fe_lay.addWidget(self._btn_save)
        lay.addWidget(self._footer_edit)

        self._set_edit_mode(self._edit_mode)

    def _set_edit_mode(self, mode: bool):
        self._edit_mode = mode
        # Обновляем view-лейблы текущими значениями из полей
        self._lbl_num.setText(self.inp_num.text() or "—")
        area_raw = self.inp_area.text().strip()
        self._lbl_area.setText((area_raw + " м²") if area_raw else "—")
        # Переключаем лейблы vs поля
        self._lbl_num.setVisible(not mode)
        self.inp_num.setVisible(mode)
        self._lbl_area.setVisible(not mode)
        self.inp_area.setVisible(mode)
        # Кнопка "Редактировать"
        if self._btn_switch_edit is not None:
            self._btn_switch_edit.setVisible(not mode)
        # Футер
        self._footer_view.setVisible(not mode)
        self._footer_edit.setVisible(mode)
        self._update_save_state()

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
        n_owners = sum(1 for o in owners if ownership.is_owner(o))
        n_contacts = sum(1 for o in owners if not ownership.is_owner(o))
        n_members = sum(1 for o in owners if isinstance(o, dict) and o.get("member_doc"))
        count_lines = []
        if n_owners:
            count_lines.append(f"Собственники: {n_owners}")
        if n_members:
            count_lines.append(f"Члены СНТ: {n_members}")
        if n_contacts:
            count_lines.append(f"Прочие контактные лица: {n_contacts}")
        self._active_counts_lbl.setText("  ·  ".join(count_lines) if count_lines else "")

        # Отсутствующие документы
        missing = []
        no_opd = sum(1 for o in owners
                     if isinstance(o, dict) and not o.get("opd_doc"))
        no_egrn = sum(1 for o in owners
                      if isinstance(o, dict) and not o.get("egrn_doc"))
        no_mem = sum(1 for o in owners
                     if isinstance(o, dict) and not o.get("member_doc"))
        if no_opd:
            missing.append(f"Нет заявления на ОПД: {no_opd}")
        if no_egrn:
            missing.append(f"Нет выписки ЕГРН: {no_egrn}")
        if no_mem:
            missing.append(f"Нет заявления на членство: {no_mem}")
        self._active_missing_lbl.setText("\n".join(missing))

        # Долг/Аванс
        self._refresh_debt_card()

    def _refresh_debt_card(self):
        from core.utils import fmt_money
        plot_num = str(self._plot_data.get("num", ""))
        since = ownership.group_since(self._active_group)

        # ЧВ
        try:
            from core import vznosy as vzn
            rates = vzn.load_rates()
            adj = vzn.load_adjustments()
            area = vzn.plot_area_map().get(plot_num)
            gb = vzn.balance_for_active_group(
                plot_num, area, date.today(), rates, adj, self._df, since=since)
            debt_v = gb.debt
            color_v = "#DC2626" if debt_v > 0.005 else ("#059669" if debt_v < -0.005 else "#6B7280")
            prefix = "Аванс " if debt_v < -0.005 else "Долг "
            self._debt_vzn_lbl.setText(f"{prefix}{fmt_money(abs(debt_v))}")
            self._debt_vzn_lbl.setStyleSheet(
                f"font-size:12px;font-weight:600;color:{color_v};background:transparent;")
        except Exception:
            self._debt_vzn_lbl.setText("—")

        # Электроэнергия
        try:
            from core import energy as en
            meters = en.load_meters()
            en_rates = en.load_rates()
            replacements = en.load_replacements()
            baseline = en.load_baseline()
            egb = en.balance_for_active_group(
                plot_num, date.today(), meters, en_rates, replacements,
                baseline, self._df, since=since)
            debt_e = egb.debt
            color_e = "#DC2626" if debt_e > 0.005 else ("#059669" if debt_e < -0.005 else "#6B7280")
            prefix_e = "Аванс " if debt_e < -0.005 else "Долг "
            self._debt_en_lbl.setText(f"{prefix_e}{fmt_money(abs(debt_e))}")
            self._debt_en_lbl.setStyleSheet(
                f"font-size:12px;font-weight:600;color:{color_e};background:transparent;")
        except Exception:
            self._debt_en_lbl.setText("—")

    def _refresh_prev_section(self):
        archived = ownership.archived_groups({"groups": self._groups})
        self._prev_section.setVisible(bool(archived))
        if not archived:
            return
        g = archived[0]
        until = ownership.group_until(g)
        self._prev_until_lbl.setText(
            f"Закрыта с: {until.strftime('%d.%m.%Y')}" if until else "Закрыта")
        # Очищаем и перестраиваем карточку
        while self._prev_card_lyt.count():
            item = self._prev_card_lyt.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._prev_card_lyt.addWidget(self._make_archived_preview(g))

    def _make_archived_preview(self, group: dict) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            "QFrame{background:#F8F9FA;border:1px solid #E5E7EB;border-radius:8px;}"
        )
        cl = QHBoxLayout(card)
        cl.setContentsMargins(14, 10, 14, 10)
        cl.setSpacing(12)

        # Левая часть: имена + долг
        info_w = QWidget()
        info_w.setStyleSheet("background:transparent;")
        info_lyt = QVBoxLayout(info_w)
        info_lyt.setContentsMargins(0, 0, 0, 0)
        info_lyt.setSpacing(3)

        names = ownership.group_label(group, empty="(без ФИО)")
        name_lbl = QLabel(names)
        name_lbl.setStyleSheet(
            "font-size:13px; font-weight:600; color:#374151; background:transparent;")
        name_lbl.setWordWrap(True)
        info_lyt.addWidget(name_lbl)

        debt_v = (group.get("debt_at_close") or {}).get("vznosy")
        debt_e = (group.get("debt_at_close") or {}).get("energy")
        if debt_v is not None or debt_e is not None:
            from core.utils import fmt_money
            parts = []
            if debt_v is not None:
                parts.append(f"ЧВ: {fmt_money(debt_v)}")
            if debt_e is not None:
                parts.append(f"Электр.: {fmt_money(debt_e)}")
            debt_lbl = QLabel("Долг: " + "  ·  ".join(parts))
            debt_lbl.setStyleSheet(
                "font-size:12px; color:#6B7280; background:transparent;")
            info_lyt.addWidget(debt_lbl)
        cl.addWidget(info_w, stretch=1)
        return card

    def _on_edit_active_group(self):
        dlg = GroupEditDialog(self._active_group, is_new=False, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._active_group = dlg.get_result()
            self._refresh_active_card()
            self._update_save_state()

    def _on_archive_active_group(self):
        if not ownership.group_owners(self._active_group):
            QMessageBox.warning(self, "Нет лиц",
                                "В активной группе нет ни одного лица. "
                                "Добавьте хотя бы одно перед архивированием.")
            return
        exit_date = self._ask_exit_date()
        if exit_date is None:
            return
        debt_at_close = self._compute_group_debt(exit_date)
        archived = dict(self._active_group)
        archived["until"] = exit_date.isoformat()
        archived["debt_at_close"] = debt_at_close
        new_active = {"since": exit_date.isoformat(), "until": None, "owners": []}
        dlg = GroupEditDialog(new_active, is_new=True, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_active = dlg.get_result()
        updated = []
        for g in self._groups:
            if g.get("until") is None:
                updated.append(archived)
            else:
                updated.append(g)
        updated.append(new_active)
        self._groups = updated
        self._active_group = new_active
        self._refresh_active_card()
        self._refresh_prev_section()
        self._update_save_state()

    def _on_view_archive(self):
        dlg = GroupArchiveDialog(
            {"groups": self._groups}, self._active_group, df=self._df, parent=self)
        dlg.exec()

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

    def _update_save_state(self):
        if self._btn_save is None:
            return
        has_owners = bool(ownership.group_owners(self._active_group))
        num_ok = bool(self.inp_num.text().strip())
        ok = has_owners and num_ok
        self._btn_save.setEnabled(ok)
        self._btn_save.setCursor(
            Qt.CursorShape.PointingHandCursor if ok else Qt.CursorShape.ArrowCursor)

    def _on_accept(self):
        num = self.inp_num.text().strip()
        if not num:
            QMessageBox.warning(self, "Ошибка", "Укажите номер участка")
            return
        area_raw = self.inp_area.text().strip().replace(",", ".")
        area_val = None
        if area_raw:
            try:
                area_val = float(area_raw)
                if area_val <= 0:
                    raise ValueError("non-positive")
            except ValueError:
                QMessageBox.warning(self, "Ошибка",
                                    "Площадь должна быть положительным числом")
                return
        if not ownership.group_owners(self._active_group):
            QMessageBox.warning(self, "Ошибка",
                                "В активной группе должно быть хотя бы одно лицо")
            return
        final_groups = []
        for g in self._groups:
            if g.get("until") is None:
                final_groups.append(self._active_group)
            else:
                final_groups.append(g)
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
        self.accept()

    def get_result(self) -> dict:
        return getattr(self, "_result", {})

    def _ask_exit_date(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Дата закрытия группы")
        dlg.setModal(True)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(10)
        lbl = QLabel("Дата перехода права (с этой даты начинается новая группа):")
        lbl.setStyleSheet("color:#374151; background:transparent; font-size:13px;")
        v.addWidget(lbl)
        de = QDateEdit(calendarPopup=True)
        de.setDisplayFormat("dd.MM.yyyy")
        de.setDate(QDate.currentDate())
        v.addWidget(de)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("В архив")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        v.addWidget(btns)
        dlg.setStyleSheet(
            "QDialog{background:#FFFFFF;} QLabel{background:transparent;}"
            "QDateEdit{background:#F8F9FA;border:1px solid #D1D5DB;border-radius:5px;"
            "padding:6px 8px;font-size:13px;color:#374151;}"
            "QDialogButtonBox QPushButton{background:#07414F;color:#fff;border:none;"
            "border-radius:6px;padding:7px 16px;font-size:13px;font-weight:600;}"
            "QDialogButtonBox QPushButton[text='Отмена']{background:#E5E7EB;color:#6B7280;}")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return de.date().toPyDate()

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog { background: #FFFFFF; color: #374151; }
            QLabel  { background: transparent; color: #374151; font-size: 13px; }
            QLineEdit {
                background: #F8F9FA; border: 1px solid #D1D5DB;
                border-radius: 5px; color: #374151; padding: 7px 10px; font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid #07414F; }
            QPushButton#btnSecondary {
                background: #E5E7EB; color: #6B7280; border: 1px solid #D1D5DB;
                border-radius: 6px; padding: 7px 14px; font-size: 13px;
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
