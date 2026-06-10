import json
import os

import pandas as pd
from PyQt6.QtCore import (
    Qt, QEvent, QModelIndex, QAbstractItemModel, QObject, QPoint, QRect, QRegularExpression,
    QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QColor, QFont, QFontMetrics, QPainter, QPen, QPolygon,
    QRegularExpressionValidator,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QMessageBox, QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QTableWidget, QTableWidgetItem, QTreeView, QVBoxLayout, QWidget,
)

from core.utils import DATA_DIR
from ui.plot_detection import _PLOTS_FILE


def _plot_num_key(s: str):
    try:
        return (0, int(s), s)
    except ValueError:
        return (1, 0, s)


def _load_plot_order() -> list[str]:
    """Возвращает список номеров участков из snt_plots.json, отсортированный по _plot_num_key."""
    try:
        if os.path.exists(_PLOTS_FILE):
            with open(_PLOTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            nums = [str(e.get("num", "")) for e in data if e.get("num")]
            return sorted(set(nums), key=_plot_num_key)
    except Exception:
        pass
    return []


def _owner_name(owner) -> str:
    if isinstance(owner, dict):
        return owner.get("name", "")
    return str(owner)


def _is_owner(owner) -> bool:
    """Является ли владелец собственником (is_owner=True по умолчанию).
    Обратная совместимость: старые записи с relation=Главный/Собственник → True."""
    if isinstance(owner, dict):
        if "is_owner" in owner:
            return bool(owner["is_owner"])
        rel = owner.get("relation", "")
        return rel in ("", "Главный собственник", "Собственник")
    return True


def _owner_area(owner) -> float | None:
    if isinstance(owner, dict):
        v = owner.get("area")
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return None


def _is_visible(owner) -> bool:
    if isinstance(owner, dict):
        return bool(owner.get("is_visible", False))
    return False


def _owner_member_doc(owner) -> str:
    if isinstance(owner, dict):
        return owner.get("member_doc", "")
    return ""


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
                email: str = "") -> dict:
    d: dict = {"name": name, "is_owner": is_owner}
    if area is not None:
        d["area"] = area
    if is_visible:
        d["is_visible"] = True
    if member_doc:
        d["member_doc"] = member_doc
    if opd_doc:
        d["opd_doc"] = opd_doc
    if phone:
        d["phone"] = phone
    if email:
        d["email"] = email
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


class _HoverTooltipLabel(QLabel):
    """QLabel с кастомной всплывашкой вместо нативного setToolTip."""

    def __init__(self, tip: str, parent=None):
        super().__init__(parent)
        self._tip = tip

    def enterEvent(self, event):
        from PyQt6.QtGui import QCursor
        _AppTooltip.show_at(self._tip, QCursor.pos())
        super().enterEvent(event)

    def leaveEvent(self, event):
        _AppTooltip.hide()
        super().leaveEvent(event)


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
    COLUMNS = ["Участок, №", "Площадь, м²", "Контактное лицо, ФИО",
               "Контактный номер", "E-mail"]

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

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if node.kind == "plot":
                if col == "Участок, №":
                    return str(node.plot_ref.get('num', '?'))
                if col == "Контактное лицо, ФИО":
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
                if col == "Контактное лицо, ФИО":
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
                if col == "Участок, №":
                    return QColor("#07414F")
                if col == "Площадь, м²":
                    area = node.plot_ref.get("area")
                    return QColor("#9CA3AF") if area in (None, "") else QColor("#374151")
                return QColor("#374151")
            elif node.kind == "owner":
                return QColor("#555F6D")

        if role == Qt.ItemDataRole.FontRole:
            if node.kind == "plot" and col == "Участок, №":
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
            return col
        return None

    # -- edit ---------------------------------------------------------------- #

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        node = index.internalPointer()
        col = self.COLUMNS[index.column()]
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if node.kind == "owner" and col in ("Контактное лицо, ФИО", "Площадь, м²"):
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        node = index.internalPointer()
        col = self.COLUMNS[index.column()]
        if node.kind == "owner" and col in ("Контактное лицо, ФИО", "Площадь, м²"):
            text = str(value).strip()
            owners = node.plot_ref.get("owners", [])
            if not (0 <= node.owner_idx < len(owners)):
                return False
            old = owners[node.owner_idx]
            if col == "Контактное лицо, ФИО":
                if not text:
                    return False
                owners[node.owner_idx] = _make_owner(
                    text, _is_owner(old), _owner_area(old), _is_visible(old),
                    _owner_member_doc(old), _owner_opd_doc(old))
                pn = node.parent
                if pn is not None:
                    fio_col = self.COLUMNS.index("Контактное лицо, ФИО")
                    self.dataChanged.emit(self.createIndex(pn.row(), fio_col, pn),
                                         self.createIndex(pn.row(), fio_col, pn))
            else:  # Площадь, м²
                if text in ("", "—"):
                    area = None
                else:
                    try:
                        area = float(text.replace(",", "."))
                        if area <= 0:
                            return False
                    except ValueError:
                        return False
                owners[node.owner_idx] = _make_owner(
                    _owner_name(old), _is_owner(old), area, _is_visible(old),
                    _owner_member_doc(old), _owner_opd_doc(old))
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
        if col == "Участок, №":
            self._root.children.sort(
                key=lambda n: _plot_num_key(str(n.plot_ref.get("num", ""))),
                reverse=reverse,
            )
        elif col == "Контактное лицо, ФИО":
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

    # -- mutations ----------------------------------------------------------- #

    def add_owner(self, plot_node: "_PlotNode", name: str,
                  is_owner: bool = True) -> QModelIndex:
        """Добавляет владельца в участок и возвращает индекс строки участка."""
        plot = plot_node.plot_ref
        owners = plot.setdefault("owners", [])
        new_idx = len(owners)
        owners.append(_make_owner(name, is_owner))
        parent_mi = self.createIndex(plot_node.row(), 0, plot_node)
        self.beginInsertRows(parent_mi, new_idx, new_idx)
        owner_node = _PlotNode("owner", plot_ref=plot, owner_idx=new_idx, parent=plot_node)
        plot_node.children.append(owner_node)
        self.endInsertRows()
        # Обновляем ФИО в родительской строке
        fio_col = self.COLUMNS.index("Контактное лицо, ФИО")
        fio_mi = self.createIndex(plot_node.row(), fio_col, plot_node)
        self.dataChanged.emit(fio_mi, fio_mi)
        return parent_mi

    def remove_owner(self, owner_node: _PlotNode):
        """Удаляет владельца из участка."""
        plot_node = owner_node.parent
        owners = owner_node.plot_ref.get("owners", [])
        idx = owner_node.owner_idx
        if idx >= len(owners):
            return
        owners.pop(idx)
        parent_mi = self.createIndex(plot_node.row(), 0, plot_node)
        child_row = owner_node.row()
        self.beginRemoveRows(parent_mi, child_row, child_row)
        plot_node.children.remove(owner_node)
        for i, child in enumerate(plot_node.children):
            child.owner_idx = i
        self.endRemoveRows()
        # Обновляем ФИО в родительской строке
        fio_col = self.COLUMNS.index("Контактное лицо, ФИО")
        fio_mi = self.createIndex(plot_node.row(), fio_col, plot_node)
        self.dataChanged.emit(fio_mi, fio_mi)


# ============================================================================ #
#  Делегат столбца «Контактное лицо, ФИО»                                     #
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
    _FIO_COL    = PlotsTreeModel.COLUMNS.index("Контактное лицо, ФИО")

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
            r = btn.height() // 2
            painter.drawRoundedRect(btn, r, r)

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


# ============================================================================ #
#  Шапка таблицы с кастомными стрелками сортировки                            #
# ============================================================================ #

class _SortHeaderView(QHeaderView):
    """Шапка с синим фоном и нарисованными стрелками сортировки."""

    _BG      = QColor("#C9D8E2")
    _FG      = QColor("#07414F")
    _BORDER  = QColor("#B5C8D5")
    _ARR_ON  = QColor("#07414F")
    _ARR_OFF = QColor("#9AABB6")

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setSectionsClickable(True)
        self.setSortIndicatorShown(False)
        self.setFixedHeight(34)
        self.setSortIndicator(0, Qt.SortOrder.AscendingOrder)

    def paintSection(self, painter: QPainter, rect: QRect, logical_index: int):
        if not rect.isValid():
            return
        painter.save()
        painter.fillRect(rect, self._BG)

        # Вертикальные разделители между колонками
        painter.setPen(QPen(self._BORDER, 1))
        painter.drawLine(rect.right(), rect.top() + 4, rect.right(), rect.bottom() - 4)

        model = self.model()
        label = (
            str(model.headerData(logical_index, Qt.Orientation.Horizontal,
                                 Qt.ItemDataRole.DisplayRole) or "")
            if model else ""
        )
        if label:
            arr_w = 18
            text_rect = QRect(rect.left() + 10, rect.top(),
                              rect.width() - arr_w - 14, rect.height())
            arr_rect  = QRect(rect.right() - arr_w - 2, rect.top(),
                              arr_w, rect.height())

            painter.setPen(self._FG)
            f = QFont()
            f.setPixelSize(12)
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(text_rect,
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             label)

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
        self._setup_ui()
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

        self.status_label = QLabel("", objectName="statusLabel")
        layout.addWidget(self.status_label)

        self.model = PlotsTreeModel(self)
        self.model.ownerDataEdited.connect(self._save)

        # ── Блок 1: шапка в своей рамке ──────────────────────────────────────
        self.hdr_view = _SortHeaderView()
        self.hdr_view.setModel(self.model)
        self.hdr_view.sortIndicatorChanged.connect(self._on_sort_changed)

        hdr_frame = QFrame(objectName="hdrBlock")
        hdr_frame.setStyleSheet("""
            QFrame#hdrBlock {
                background: #C9D8E2;
                border: 1px solid #B5C8D5;
                border-radius: 6px;
            }
        """)
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
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.tree.setSortingEnabled(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setMouseTracking(True)
        self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.tree.setStyleSheet(_TREE_STYLE)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)

        fio_col_idx = PlotsTreeModel.COLUMNS.index("Контактное лицо, ФИО")
        self._fio_delegate = _FioDelegate(self.tree)
        self._fio_delegate.toggleRequested.connect(self._on_toggle)
        self.tree.setItemDelegateForColumn(fio_col_idx, self._fio_delegate)

        # Синхронизация ширин колонок между hdr_view и tree
        self.tree.header().sectionResized.connect(self._on_tree_hdr_resized)
        self.hdr_view.sectionResized.connect(self._on_hdr_view_resized)
        self._col_syncing = False

        body_frame = QFrame(objectName="bodyBlock")
        body_frame.setStyleSheet("""
            QFrame#bodyBlock {
                border: 1px solid #D5DCE4;
                border-radius: 6px;
                background: transparent;
            }
        """)
        body_inner = QVBoxLayout(body_frame)
        body_inner.setContentsMargins(0, 0, 0, 0)
        body_inner.setSpacing(0)
        body_inner.addWidget(self.tree)

        # ── Собираем два блока со spacing ────────────────────────────────────
        table_vbox = QVBoxLayout()
        table_vbox.setSpacing(4)
        table_vbox.setContentsMargins(0, 0, 0, 0)
        table_vbox.addWidget(hdr_frame)
        table_vbox.addWidget(body_frame, stretch=1)
        layout.addLayout(table_vbox)

    def _rebuild_table(self):
        sort_col   = self.hdr_view.sortIndicatorSection()
        sort_order = self.hdr_view.sortIndicatorOrder()

        self.model.load(self._plots)

        if sort_col < 0 or sort_col >= len(PlotsTreeModel.COLUMNS):
            sort_col   = 0
            sort_order = Qt.SortOrder.AscendingOrder
        self.model.sort(sort_col, sort_order)
        self.hdr_view.setSortIndicator(sort_col, sort_order)

        col_участок  = PlotsTreeModel.COLUMNS.index("Участок, №")
        col_fio      = PlotsTreeModel.COLUMNS.index("Контактное лицо, ФИО")
        col_area     = PlotsTreeModel.COLUMNS.index("Площадь, м²")
        col_phone    = PlotsTreeModel.COLUMNS.index("Контактный номер")
        col_email    = PlotsTreeModel.COLUMNS.index("E-mail")

        # Одинаковые режимы на обоих хедерах — для выравнивания колонок
        for h in (self.hdr_view, self.tree.header()):
            h.setStretchLastSection(False)
            h.setSectionResizeMode(col_участок,  QHeaderView.ResizeMode.Fixed)
            h.resizeSection(col_участок, 110)
            h.setSectionResizeMode(col_fio,      QHeaderView.ResizeMode.Stretch)
            h.setSectionResizeMode(col_area,     QHeaderView.ResizeMode.Fixed)
            h.resizeSection(col_area, 120)
            h.setSectionResizeMode(col_phone,    QHeaderView.ResizeMode.Fixed)
            h.resizeSection(col_phone, 140)
            h.setSectionResizeMode(col_email,    QHeaderView.ResizeMode.Fixed)
            h.resizeSection(col_email, 160)

        self.status_label.setText(f"Участков: {len(self._plots)}")

    def _on_sort_changed(self, col: int, order: Qt.SortOrder):
        self.model.sort(col, order)
        self.hdr_view.setSortIndicator(col, order)

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

    def _on_toggle(self, index: QModelIndex):
        col0 = self.model.index(index.row(), 0, self.model.parent(index))
        if self.tree.isExpanded(col0):
            self.tree.collapse(col0)
        else:
            self.tree.expand(col0)

    def _context_menu(self, pos: QPoint):
        index = self.tree.indexAt(pos)
        if not index.isValid():
            return
        node = index.internalPointer()
        if node is None:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu{background:#F8F9FA;border:1px solid #D1D5DB;color:#374151;
                  font-size:13px;padding:4px;}
            QMenu::item{padding:8px 20px;border-radius:4px;}
            QMenu::item:selected{background:#EEF2FF;color:#DC2626;}
        """)

        if node.kind == "plot":
            act_edit = QAction("✏️  Редактировать", self)
            act_edit.triggered.connect(lambda: self._edit_plot(node.plot_ref))
            menu.addAction(act_edit)

            act_dist = QAction("⚖️  Распределить площадь", self)
            act_dist.triggered.connect(lambda: self._distribute_area(node))
            owners = node.plot_ref.get("owners", [])
            eligible = sum(1 for o in owners if _is_owner(o))
            act_dist.setEnabled(
                bool(node.plot_ref.get("area")) and eligible > 0
            )
            menu.addAction(act_dist)

            act_del = QAction("Удалить участок", self)
            act_del.triggered.connect(lambda: self._delete_plot(node.plot_ref))
            menu.addAction(act_del)
        elif node.kind == "owner":
            act_del = QAction("Удалить владельца", self)
            act_del.triggered.connect(lambda: self._delete_owner(node))
            menu.addAction(act_del)

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _edit_plot(self, plot: dict):
        dlg = PlotEditDialog(plot_data=plot, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            result = dlg.get_result()
            if result:
                for i, p in enumerate(self._plots):
                    if p is plot:
                        self._plots[i] = result
                        break
                self._save()
                self._rebuild_table()

    def _delete_plot(self, plot: dict):
        reply = QMessageBox.question(
            self, "Удаление участка",
            f"Удалить участок № {plot.get('num', '?')}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._plots = [p for p in self._plots if p is not plot]
            self._save()
            self._rebuild_table()

    def _delete_owner(self, owner_node: _PlotNode):
        owners = owner_node.plot_ref.get("owners", [])
        idx = owner_node.owner_idx
        if idx >= len(owners):
            return
        name = owners[idx]
        reply = QMessageBox.question(
            self, "Удаление владельца",
            f"Удалить «{name}» из участка № {owner_node.plot_ref.get('num', '?')}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.model.remove_owner(owner_node)
            self._save()

    def _distribute_area(self, plot_node: _PlotNode):
        plot = plot_node.plot_ref
        try:
            total = float(plot.get("area") or 0)
        except (TypeError, ValueError):
            total = 0
        if total <= 0:
            QMessageBox.warning(self, "Нет площади",
                                "У участка не указана площадь.")
            return

        owners = plot.get("owners", [])
        eligible_idx = [i for i, o in enumerate(owners) if _is_owner(o)]
        if not eligible_idx:
            QMessageBox.warning(self, "Нет собственников",
                                "Нет владельцев с отметкой «Собственник».")
            return

        share = round(total / len(eligible_idx), 2)
        area_col = PlotsTreeModel.COLUMNS.index("Площадь, м²")

        for child in plot_node.children:
            if child.owner_idx not in eligible_idx:
                continue
            o = owners[child.owner_idx]
            owners[child.owner_idx] = _make_owner(
                _owner_name(o), _is_owner(o), share, _is_visible(o),
                _owner_member_doc(o), _owner_opd_doc(o))
            mi = self.model.createIndex(child.row(), area_col, child)
            self.model.dataChanged.emit(mi, mi)

        self._save()

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
        dlg = PlotEditDialog(plot_data=None, parent=self)
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

class _MemberDocWidget(QWidget):
    """Кнопки управления прикреплённым документом.

    Состояние A (нет файла): vertical_align_bottom — загрузить.
    Состояние B (файл есть): open_char (открыть) + autorenew (заменить)
                              + contract_delete (удалить).
    Иконка кнопки «открыть» и тексты подсказок задаются через keyword-параметры.
    """

    def __init__(self, doc_path: str = "", *,
                 open_char: str = chr(0xF565),
                 upload_tip: str = "Загрузить заявление",
                 open_tip: str = "Открыть заявление",
                 parent=None):
        super().__init__(parent)
        self._path = doc_path
        self._upload_tip = upload_tip
        self.setFixedWidth(80)
        self.setStyleSheet("background:transparent;")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(2)

        f = QFont("Material Symbols Rounded")
        f.setPixelSize(16)

        def _btn(char: str, color: str = "#07414F", hover: str = "#0B5A6E") -> QPushButton:
            b = QPushButton(char)
            b.setFont(f)
            b.setFixedSize(24, 28)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton{{background:transparent;border:none;color:{color};}}"
                f"QPushButton:hover{{color:{hover};}}"
            )
            return b

        self._btn_upload  = _btn(chr(0xE258))
        self._btn_open    = _btn(open_char)
        self._btn_replace = _btn(chr(0xE863))
        self._btn_delete  = _btn(chr(0xF5A2), "#DC2626", "#B91C1C")

        self._btn_upload.installEventFilter(_TooltipFilter(upload_tip, self._btn_upload))
        self._btn_open.installEventFilter(_TooltipFilter(open_tip, self._btn_open))
        self._btn_replace.installEventFilter(_TooltipFilter("Заменить документ", self._btn_replace))
        self._btn_delete.installEventFilter(_TooltipFilter("Удалить документ", self._btn_delete))

        self._btn_upload.clicked.connect(self._on_upload)
        self._btn_open.clicked.connect(self._on_open)
        self._btn_replace.clicked.connect(self._on_upload)
        self._btn_delete.clicked.connect(self._on_delete)

        lay.addWidget(self._btn_upload)
        lay.addWidget(self._btn_open)
        lay.addWidget(self._btn_replace)
        lay.addWidget(self._btn_delete)

        self._refresh()

    def get_path(self) -> str:
        return self._path

    def _refresh(self):
        has = bool(self._path)
        self._btn_upload.setVisible(not has)
        self._btn_open.setVisible(has)
        self._btn_replace.setVisible(has)
        self._btn_delete.setVisible(has)

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

    def _on_delete(self):
        self._path = ""
        self._refresh()


class _OpdDocWidget(_MemberDocWidget):
    """Кнопки управления заявлением на обработку персональных данных (ОПД)."""

    def __init__(self, doc_path: str = "", parent=None):
        super().__init__(
            doc_path,
            open_char=chr(0xE8E8),
            upload_tip="Загрузить заявление на ОПД",
            open_tip="Открыть заявление на ОПД",
            parent=parent,
        )


# ============================================================================ #
#  PlotEditDialog                                                              #
# ============================================================================ #

class PlotEditDialog(QDialog):
    """Диалог добавления / редактирования участка."""

    _IC_FONT = "Material Symbols Rounded"

    def __init__(self, plot_data: dict | None = None, parent=None):
        super().__init__(parent)
        self._is_edit = plot_data is not None
        self._plot_data = plot_data or {}
        self.setWindowTitle("Редактировать участок" if self._is_edit else "Новый участок")
        self.setMinimumWidth(800)
        self.setModal(True)
        self._owner_inputs:      list[QLineEdit]         = []
        self._owner_checks:      list[_IconCheckBox]      = []
        self._owner_visible:     list[_IconRadioButton]   = []
        self._owner_member_docs: list[_MemberDocWidget]   = []
        self._owner_opd_docs:    list[_OpdDocWidget]      = []
        self._owner_areas:       list[QLineEdit]          = []
        self._owner_phones:      list[QLineEdit]          = []
        self._owner_emails:      list[QLineEdit]          = []
        self._btn_save           = None
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 20)
        lay.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.inp_num = QLineEdit(str(self._plot_data.get("num", "")))
        self.inp_num.setPlaceholderText("например: 15 или 15/207")
        form.addRow("Номер участка:", self.inp_num)

        area_raw = self._plot_data.get("area")
        area_text = ""
        if area_raw not in (None, "", 0):
            try:
                area_text = f"{float(area_raw):g}"
            except (TypeError, ValueError):
                area_text = str(area_raw)
        self.inp_area = QLineEdit(area_text)
        self.inp_area.setPlaceholderText("например: 612")
        self.inp_area.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"^\d{0,5}([.,]\d{0,2})?$"), self.inp_area
        ))
        self.inp_area.textEdited.connect(lambda t: (
            self.inp_area.setText(t.replace(".", ",")),
            self.inp_area.setCursorPosition(self.inp_area.cursorPosition()),
        ) if "." in t else None)
        self.inp_area.textChanged.connect(self._update_save_state)
        form.addRow("Площадь, м²:", self.inp_area)
        lay.addLayout(form)

        # Заголовок секции владельцев с иконкой-подсказкой для чекбокса
        hdr_row = QWidget()
        hdr_row.setStyleSheet("background:transparent;")
        hdr_lay = QHBoxLayout(hdr_row)
        hdr_lay.setContentsMargins(0, 0, 0, 0)
        hdr_lay.setSpacing(6)
        lbl_fio = QLabel("Контактное лицо, ФИО")
        lbl_fio.setStyleSheet("color:#9CA3AF; font-size:12px;")
        hdr_lay.addWidget(lbl_fio, stretch=1)
        lbl_ic = QLabel("")   # article_person
        f_ic = QFont(self._IC_FONT)
        f_ic.setPixelSize(16)
        lbl_ic.setFont(f_ic)
        lbl_ic.setFixedSize(32, 32)
        lbl_ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_ic.setStyleSheet("color:#07414F; background:transparent;")
        lbl_ic.installEventFilter(_TooltipFilter("Собственник", lbl_ic))
        hdr_lay.addWidget(lbl_ic)
        lbl_vis_hdr = QLabel(chr(0xF454))   # bookmark_star
        lbl_vis_hdr.setFont(f_ic)
        lbl_vis_hdr.setFixedSize(32, 32)
        lbl_vis_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_vis_hdr.setStyleSheet("color:#07414F; background:transparent;")
        lbl_vis_hdr.installEventFilter(_TooltipFilter("Видимый", lbl_vis_hdr))
        hdr_lay.addWidget(lbl_vis_hdr)
        lbl_member_hdr = QLabel(chr(0xE7FD))   # person
        lbl_member_hdr.setFont(f_ic)
        lbl_member_hdr.setFixedWidth(80)
        lbl_member_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_member_hdr.setStyleSheet("color:#07414F; background:transparent;")
        lbl_member_hdr.installEventFilter(_TooltipFilter("Член СНТ", lbl_member_hdr))
        hdr_lay.addWidget(lbl_member_hdr)
        lbl_opd_hdr = QLabel(chr(0xF650))   # shield_person
        lbl_opd_hdr.setFont(f_ic)
        lbl_opd_hdr.setFixedWidth(80)
        lbl_opd_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_opd_hdr.setStyleSheet("color:#07414F; background:transparent;")
        lbl_opd_hdr.installEventFilter(_TooltipFilter("Заявление на ОПД", lbl_opd_hdr))
        hdr_lay.addWidget(lbl_opd_hdr)
        lbl_area = QLabel("м²")
        lbl_area.setFixedWidth(80)
        lbl_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_area.setStyleSheet("color:#9CA3AF; font-size:12px;")
        hdr_lay.addWidget(lbl_area)
        lbl_phone_hdr = QLabel("Телефон")
        lbl_phone_hdr.setFixedWidth(120)
        lbl_phone_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_phone_hdr.setStyleSheet("color:#9CA3AF; font-size:12px;")
        hdr_lay.addWidget(lbl_phone_hdr)
        lbl_email_hdr = QLabel("E-mail")
        lbl_email_hdr.setFixedWidth(150)
        lbl_email_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_email_hdr.setStyleSheet("color:#9CA3AF; font-size:12px;")
        hdr_lay.addWidget(lbl_email_hdr)
        btn_stub = QWidget()
        btn_stub.setFixedWidth(28)
        btn_stub.setStyleSheet("background:transparent;")
        hdr_lay.addWidget(btn_stub)
        lay.addWidget(hdr_row)

        self._owners_container = QWidget()
        self._owners_container.setStyleSheet("background:transparent;")
        self._owners_vlay = QVBoxLayout(self._owners_container)
        self._owners_vlay.setSpacing(6)
        self._owners_vlay.setContentsMargins(0, 0, 0, 0)

        existing_owners = self._plot_data.get("owners", [])
        for owner in existing_owners:
            self._add_owner_field(_owner_name(owner), _is_owner(owner),
                                  _owner_area(owner), _is_visible(owner),
                                  _owner_member_doc(owner), _owner_opd_doc(owner),
                                  _owner_phone(owner), _owner_email(owner))
        self._sync_visible_auto()

        lay.addWidget(self._owners_container)

        btn_add_owner = QPushButton("＋  Добавить")
        btn_add_owner.setObjectName("btnSecondary")
        btn_add_owner.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_owner.clicked.connect(lambda: self._add_owner_field("", True, None))
        lay.addWidget(btn_add_owner)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#E5E7EB;background:#E5E7EB;max-height:1px;")
        lay.addWidget(sep)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        self._btn_save = btns.button(QDialogButtonBox.StandardButton.Ok)
        self._btn_save.setText("Сохранить")
        btn_cancel = btns.button(QDialogButtonBox.StandardButton.Cancel)
        btn_cancel.setText("Отмена")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self._update_save_state()

    def _add_owner_field(self, name: str, is_owner: bool = True,
                         area: float | None = None, is_visible: bool = False,
                         doc_path: str = "", opd_path: str = "",
                         phone: str = "", email: str = ""):
        row = QWidget()
        row.setStyleSheet("background:transparent;")
        rlay = QHBoxLayout(row)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(6)

        inp = QLineEdit(name)
        inp.setPlaceholderText("Фамилия Имя Отчество")
        inp.textChanged.connect(self._update_save_state)
        self._owner_inputs.append(inp)
        rlay.addWidget(inp, stretch=1)

        chk = _IconCheckBox(checked=is_owner)
        self._owner_checks.append(chk)
        rlay.addWidget(chk)

        effective_visible = is_visible and is_owner
        vis = _IconRadioButton(checked=effective_visible, enabled=is_owner)
        self._owner_visible.append(vis)
        chk.stateChanged.connect(lambda checked, v=vis: v.setLogicalEnabled(checked))
        chk.stateChanged.connect(lambda _: self._sync_visible_auto())
        vis.toggled.connect(lambda v=vis: self._on_visible_selected(v))
        rlay.addWidget(vis)

        mem_doc = _MemberDocWidget(doc_path)
        self._owner_member_docs.append(mem_doc)
        rlay.addWidget(mem_doc)

        opd_doc = _OpdDocWidget(opd_path)
        self._owner_opd_docs.append(opd_doc)
        rlay.addWidget(opd_doc)

        area_inp = QLineEdit("" if area is None else f"{area:g}")
        area_inp.setPlaceholderText("м²")
        area_inp.setFixedWidth(80)
        area_inp.setEnabled(is_owner)
        area_inp.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"^\d{0,5}([.,]\d{0,2})?$"), area_inp
        ))
        area_inp.textEdited.connect(lambda t, w=area_inp: (
            w.setText(t.replace(".", ",")),
            w.setCursorPosition(w.cursorPosition()),
        ) if "." in t else None)
        area_inp.textChanged.connect(self._update_save_state)
        chk.stateChanged.connect(lambda checked, a=area_inp: (
            a.setEnabled(bool(checked)),
            a.clear() if not checked else None,
        ))
        self._owner_areas.append(area_inp)
        rlay.addWidget(area_inp)

        phone_inp = QLineEdit(phone)
        phone_inp.setPlaceholderText("+7 (xxx) xxx-xx-xx")
        phone_inp.setFixedWidth(120)
        self._owner_phones.append(phone_inp)
        rlay.addWidget(phone_inp)

        email_inp = QLineEdit(email)
        email_inp.setPlaceholderText("email@example.com")
        email_inp.setFixedWidth(150)
        self._owner_emails.append(email_inp)
        rlay.addWidget(email_inp)

        btn = QPushButton("✕")
        btn.setFixedSize(28, 28)
        btn.setStyleSheet(
            "QPushButton{background:#2a1a1a;border:1px solid #5a2a2a;"
            "border-radius:5px;color:#DC2626;font-size:12px;}"
            "QPushButton:hover{background:#3a2020;}"
        )
        btn.clicked.connect(
            lambda _, r=row, i=inp, c=chk, v=vis, m=mem_doc, o=opd_doc, a=area_inp,
                   ph=phone_inp, em=email_inp:
                self._remove_owner_field(r, i, c, v, m, o, a, ph, em)
        )
        rlay.addWidget(btn)
        self._owners_vlay.addWidget(row)
        self._update_save_state()

    def _remove_owner_field(self, row: QWidget, inp: QLineEdit,
                             chk: _IconCheckBox, vis: _IconRadioButton,
                             mem_doc: "_MemberDocWidget", opd_doc: "_OpdDocWidget",
                             area_inp: QLineEdit, phone_inp: QLineEdit,
                             email_inp: QLineEdit):
        for lst, item in ((self._owner_inputs,       inp),
                          (self._owner_checks,        chk),
                          (self._owner_visible,       vis),
                          (self._owner_member_docs,   mem_doc),
                          (self._owner_opd_docs,      opd_doc),
                          (self._owner_areas,         area_inp),
                          (self._owner_phones,        phone_inp),
                          (self._owner_emails,        email_inp)):
            if item in lst:
                lst.remove(item)
        row.setParent(None)
        row.deleteLater()
        self._update_save_state()
        QTimer.singleShot(0, self.adjustSize)

    def _update_save_state(self):
        if self._btn_save is None:
            return
        fio_ok = (
            all(inp.text().strip() for inp in self._owner_inputs)
            if self._owner_inputs else True
        )
        area_ok = True
        try:
            total_raw = self.inp_area.text().replace(",", ".")
            total = float(total_raw) if total_raw.strip() else None
        except ValueError:
            total = None
        if total is not None:
            owner_sum = 0.0
            for a in self._owner_areas:
                raw = a.text().replace(",", ".")
                try:
                    owner_sum += float(raw) if raw.strip() else 0.0
                except ValueError:
                    pass
            area_ok = owner_sum <= total
        ok = fio_ok and area_ok
        self._btn_save.setEnabled(ok)
        self._btn_save.setCursor(
            Qt.CursorShape.PointingHandCursor if ok else Qt.CursorShape.ArrowCursor
        )

    def _sync_visible_auto(self):
        checked = [i for i, c in enumerate(self._owner_checks) if c.isChecked()]
        if len(checked) == 1:
            v = self._owner_visible[checked[0]]
            if not v.isChecked():
                v.setChecked(True)
                self._on_visible_selected(v)

    def _on_visible_selected(self, sender: "_IconRadioButton"):
        """Снимает все остальные радиокнопки при выборе новой."""
        for v in self._owner_visible:
            if v is not sender:
                v.setChecked(False)

    def _on_accept(self):
        num = self.inp_num.text().strip()
        if not num:
            QMessageBox.warning(self, "Ошибка", "Укажите номер участка")
            return
        owners = []
        for inp, chk, vis, mem_doc, opd_doc, area_inp, phone_inp, email_inp in zip(
                self._owner_inputs, self._owner_checks, self._owner_visible,
                self._owner_member_docs, self._owner_opd_docs, self._owner_areas,
                self._owner_phones, self._owner_emails):
            name = inp.text().strip()
            if not name:
                continue
            raw = area_inp.text().strip().replace(",", ".")
            area: float | None = None
            if raw:
                try:
                    v = float(raw)
                    if v > 0:
                        area = v
                except ValueError:
                    pass
            owners.append(_make_owner(name, chk.isChecked(), area, vis.isChecked(),
                                       mem_doc.get_path(), opd_doc.get_path(),
                                       phone_inp.text().strip(), email_inp.text().strip()))

        area_raw = self.inp_area.text().strip().replace(",", ".")
        area_val: float | None = None
        if area_raw:
            try:
                area_val = float(area_raw)
                if area_val <= 0:
                    raise ValueError("non-positive")
            except ValueError:
                QMessageBox.warning(self, "Ошибка",
                                    "Площадь должна быть положительным числом")
                return

        # Автораспределение оставшейся площади между собственниками без явно заданной
        if area_val is not None and owners:
            owner_idxs = [i for i, o in enumerate(owners) if _is_owner(o)]
            if owner_idxs:
                unset    = [i for i in owner_idxs if _owner_area(owners[i]) is None]
                assigned = sum(_owner_area(owners[i]) or 0.0 for i in owner_idxs)
                remaining = round(area_val - assigned, 10)
                if unset and remaining > 0:
                    share = round(remaining / len(unset), 2)
                    for i in unset[:-1]:
                        owners[i] = {**owners[i], "area": share}
                    last_share = round(remaining - share * (len(unset) - 1), 2)
                    owners[unset[-1]] = {**owners[unset[-1]], "area": last_share}

        result = {"num": num, "owners": owners}
        if area_val is not None:
            result["area"] = area_val
        for k in ("billing_type", "meter_commission_date", "meter_act_number",
                  "meter_location", "norm_kw", "norm_start_date",
                  "direct_contract_date", "direct_contract_number", "billing_history"):
            if k in self._plot_data:
                result[k] = self._plot_data[k]
        self._result = result
        self.accept()

    def get_result(self) -> dict:
        return getattr(self, "_result", {})

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
            QPushButton#btnSecondary:hover { background: #E5E7EB; color: #374151; }
            QDialogButtonBox QPushButton {
                background: #07414F; color: white; border: none;
                border-radius: 6px; padding: 8px 20px; font-size: 13px; font-weight: 600;
            }
            QDialogButtonBox QPushButton:hover { background: #0B5A6E; }
            QDialogButtonBox QPushButton:disabled {
                background: #E5E7EB; color: #9CA3AF; border: none;
            }
            QDialogButtonBox QPushButton[text='Отмена'] {
                background: #E5E7EB; color: #6B7280;
            }
            QDialogButtonBox QPushButton[text='Отмена']:hover {
                background: #D1D5DB; color: #374151;
            }
        """)


# ============================================================================ #
#  DocCell / DocsWidget                                                        #
# ============================================================================ #

class DocCell(QWidget):
    """
    Ячейка документа: иконка-статус (✔️/—) + кнопка скрепки для прикрепления файла.
    Эмитит сигнал при изменении.
    """
    changed = pyqtSignal()

    def __init__(self, plot_num: str, doc_key: str,
                 file_path: str = "", parent=None):
        super().__init__(parent)
        self._plot = plot_num
        self._doc_key = doc_key
        self._path = file_path

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(6)

        self.lbl_status = QLabel()
        self.lbl_status.setFixedWidth(20)
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_status)

        self.btn_attach = QPushButton()
        self.btn_attach.setFixedSize(28, 28)
        self.btn_attach.setToolTip("Прикрепить / заменить файл")
        self.btn_attach.clicked.connect(self._on_attach)
        lay.addWidget(self.btn_attach)

        self.btn_open = QPushButton("↗️")
        self.btn_open.setFixedSize(28, 28)
        self.btn_open.setToolTip("Открыть прикреплённый файл")
        self.btn_open.clicked.connect(self._on_open)
        lay.addWidget(self.btn_open)

        lay.addStretch()
        self._refresh()

    def _refresh(self):
        has = bool(self._path and os.path.exists(self._path))
        if has:
            self.lbl_status.setText("✔️")
            self.lbl_status.setStyleSheet("color:#059669;font-size:14px;font-weight:700;")
            self.btn_attach.setText("📎")
            self.btn_attach.setStyleSheet(
                "QPushButton{background:#0d3b1a;border:1px solid #2e7d32;"
                "border-radius:5px;font-size:13px;}"
                "QPushButton:hover{background:#1b5e20;}"
            )
            self.btn_open.setEnabled(True)
            self.btn_open.setStyleSheet(
                "QPushButton{background:#F0F2F5;border:1px solid #D1D5DB;"
                "border-radius:5px;color:#6366F1;font-size:12px;}"
                "QPushButton:hover{background:#E5E7EB;}"
            )
        else:
            self.lbl_status.setText("—")
            self.lbl_status.setStyleSheet("color:#6B7280;font-size:14px;font-weight:700;")
            self.btn_attach.setText("📎")
            self.btn_attach.setStyleSheet(
                "QPushButton{background:#F0F2F5;border:1px solid #D1D5DB;"
                "border-radius:5px;font-size:13px;}"
                "QPushButton:hover{background:#E5E7EB;}"
            )
            self.btn_open.setEnabled(False)
            self.btn_open.setStyleSheet(
                "QPushButton{background:#0d1720;border:1px solid #1b2a3c;"
                "border-radius:5px;color:#9CA3AF;font-size:12px;}"
            )

    def _on_attach(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Прикрепить файл", "", "Все файлы (*.*)"
        )
        if not path:
            return
        self._path = path
        self._refresh()
        self.changed.emit()

    def _on_open(self):
        if not self._path or not os.path.exists(self._path):
            QMessageBox.warning(self, "Ошибка", "Файл не найден")
            return
        try:
            os.startfile(self._path)
        except Exception:
            QMessageBox.warning(self, "Ошибка", "Не удалось открыть файл")

    def get_path(self) -> str:
        return self._path


class DocsWidget(QWidget):
    """Вкладка документов: таблица по участку и типу документа."""

    DATA_FILE = os.path.join(DATA_DIR, "snt_docs.json")
    DOC_TYPES = [
        "Паспорт",
        "Договор",
        "Схема участка",
        "Прочие документы",
    ]

    def __init__(self):
        super().__init__()
        self.setAutoFillBackground(True)
        self._docs = self._load()
        self._cells: dict[tuple[str, str], DocCell] = {}
        self._setup_ui()
        self._rebuild_table()

    def _load(self) -> dict:
        try:
            if os.path.exists(self.DATA_FILE):
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save(self):
        try:
            with open(self.DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self._docs, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def reload(self):
        self._docs = self._load()
        self._cells.clear()
        self._rebuild_table()

    def refresh_plots(self):
        self._cells.clear()
        self._rebuild_table()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        top_bar = QHBoxLayout()
        title = QLabel("Документы")
        title.setObjectName("pageTitle")
        top_bar.addWidget(title)
        top_bar.addStretch()

        btn_save = QPushButton("Сохранить")
        btn_save.setObjectName("btnPrimary")
        btn_save.clicked.connect(self._save)
        top_bar.addWidget(btn_save)
        layout.addLayout(top_bar)

        self.status_label = QLabel("Документы не загружены", objectName="statusLabel")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(objectName="mainTable")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        layout.addWidget(self.table)

    def _rebuild_table(self):
        self.table.blockSignals(True)
        self.table.clearContents()

        plot_order = _load_plot_order()
        rows = len(plot_order)
        cols = 1 + len(self.DOC_TYPES)
        self.table.setRowCount(rows)
        self.table.setColumnCount(cols)

        headers = ["Участок"] + self.DOC_TYPES
        self.table.setHorizontalHeaderLabels(headers)

        for r_idx, plot in enumerate(plot_order):
            plot_item = QTableWidgetItem(f"уч. {plot}")
            plot_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            plot_item.setForeground(QColor("#6366F1"))
            f = plot_item.font(); f.setBold(True); plot_item.setFont(f)
            self.table.setItem(r_idx, 0, plot_item)

            plot_docs = self._docs.get(str(plot), {})
            for c_idx, doc_key in enumerate(self.DOC_TYPES, start=1):
                cell = DocCell(str(plot), doc_key, plot_docs.get(doc_key, ""), self)
                cell.changed.connect(
                    lambda _, p=str(plot), d=doc_key, w=cell: self._on_doc_changed(p, d, w)
                )
                self.table.setCellWidget(r_idx, c_idx, cell)
                self._cells[(str(plot), doc_key)] = cell
            self.table.setRowHeight(r_idx, 34)

        self.table.blockSignals(False)
        self._update_status()

    def _on_doc_changed(self, plot: str, doc_key: str, cell: DocCell):
        self._docs.setdefault(str(plot), {})[doc_key] = cell.get_path()
        self._save()
        self._update_status()

    def _update_status(self):
        total = 0
        attached = 0
        for plot in _load_plot_order():
            for doc_key in self.DOC_TYPES:
                path = self._docs.get(str(plot), {}).get(doc_key, "")
                total += 1
                if path:
                    attached += 1
        self.status_label.setText(
            f"Документов: {attached} из {total} прикреплено"
        )
