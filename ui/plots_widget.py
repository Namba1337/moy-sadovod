import json
import os
from datetime import date, datetime

import pandas as pd
from PyQt6.QtCore import (
    Qt, QDate, QEvent, QModelIndex, QAbstractItemModel, QObject, QPoint, QRect, QRectF,
    QRegularExpression, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QBitmap, QColor, QFont, QFontMetrics, QPainter, QPen, QPolygon,
    QRegion, QRegularExpressionValidator,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDateEdit, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QTableWidget, QTableWidgetItem, QTreeView, QVBoxLayout, QWidget,
)

from core import ownership
from core.utils import DATA_DIR, fmt_money
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
                share: str = "") -> dict:
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
    # Поля истории владения (могут быть проставлены мастером «Сменить
    # собственника»). Сохраняем как есть, чтобы редактор контактов их не терял.
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
    COLUMNS = ["№", "Площадь, м²", "Контактное лицо",
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
                if col == "Контактное лицо":
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
                if col == "Контактное лицо":
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
        if node.kind == "owner" and col in ("Контактное лицо", "Площадь, м²"):
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        node = index.internalPointer()
        col = self.COLUMNS[index.column()]
        if node.kind == "owner" and col in ("Контактное лицо", "Площадь, м²"):
            text = str(value).strip()
            owners = node.plot_ref.get("owners", [])
            if not (0 <= node.owner_idx < len(owners)):
                return False
            old = owners[node.owner_idx]
            # Сохраняем ВСЕ поля владельца (телефон, e-mail, документы,
            # since/until/share), меняя только редактируемую ячейку.
            new = dict(old) if isinstance(old, dict) else {"name": str(old), "is_owner": True}
            if col == "Контактное лицо":
                if not text:
                    return False
                new["name"] = text
                owners[node.owner_idx] = new
                pn = node.parent
                if pn is not None:
                    fio_col = self.COLUMNS.index("Контактное лицо")
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
        elif col == "Контактное лицо":
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
        fio_col = self.COLUMNS.index("Контактное лицо")
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
        fio_col = self.COLUMNS.index("Контактное лицо")
        fio_mi = self.createIndex(plot_node.row(), fio_col, plot_node)
        self.dataChanged.emit(fio_mi, fio_mi)


# ============================================================================ #
#  Делегат столбца «Контактное лицо»                                          #
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
    _FIO_COL    = PlotsTreeModel.COLUMNS.index("Контактное лицо")

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
        if obj is self._view.viewport():
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

        fio_col_idx = PlotsTreeModel.COLUMNS.index("Контактное лицо")
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

        # Поиск в шапке для столбцов № и Контактное лицо
        self.hdr_view.add_search_col(PlotsTreeModel.COLUMNS.index("№"))
        self.hdr_view.add_search_col(PlotsTreeModel.COLUMNS.index("Контактное лицо"))
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
            elif col == PlotsTreeModel.COLUMNS.index("Контактное лицо"):
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
        col_fio      = PlotsTreeModel.COLUMNS.index("Контактное лицо")
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

        # Индикаторы в шапке «Контактное лицо»
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

    def _delete_plot(self, plot: dict):
        reply = QMessageBox.question(
            self, "Удаление участка",
            f"Удалить участок № {plot.get('num', '?')}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            if hasattr(self, "_check_delegate"):
                self._check_delegate.remove_plot(str(plot.get("num", "")))
            self._plots = [p for p in self._plots if p is not plot]
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


class _EgrnDocWidget(_MemberDocWidget):
    """Кнопки управления выпиской ЕГРН участка."""

    def __init__(self, doc_path: str = "", parent=None):
        super().__init__(
            doc_path,
            open_char=chr(0xE73A),  # real_estate_agent
            upload_tip="Загрузить выписку ЕГРН",
            open_tip="Открыть выписку ЕГРН",
            parent=parent,
        )


# ============================================================================ #
#  PlotEditDialog                                                              #
# ============================================================================ #

class PlotEditDialog(QDialog):
    """Диалог добавления / редактирования участка."""

    _IC_FONT = "Material Symbols Rounded"

    def __init__(self, plot_data: dict | None = None, parent=None, df=None):
        super().__init__(parent)
        self._is_edit = plot_data is not None
        self._plot_data = plot_data or {}
        self._df = df
        # Выбывшие собственники (с until) не редактируются здесь, но сохраняются
        # вместе с участком ради истории владения.
        self._departed: list = [
            o for o in self._plot_data.get("owners", []) or []
            if ownership.owner_until(o)
        ]
        self.setWindowTitle("Редактировать участок" if self._is_edit else "Новый участок")
        self.setMinimumWidth(920)
        self.setModal(True)
        self._owner_inputs:      list[QLineEdit]         = []
        self._owner_checks:      list[_IconCheckBox]      = []
        self._owner_visible:     list[_IconRadioButton]   = []
        self._owner_member_docs: list[_MemberDocWidget]   = []
        self._owner_opd_docs:    list[_OpdDocWidget]      = []
        self._owner_share:       list[QLineEdit]          = []   # доля в праве
        self._owner_m2:          list[QLabel]             = []   # производная площадь (read-only)
        self._owner_phones:      list[QLineEdit]          = []
        self._owner_emails:      list[QLineEdit]          = []
        self._owner_since:       list[QLineEdit]          = []
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
        self.inp_area.textChanged.connect(self._update_derived_m2)
        area_row = QHBoxLayout()
        area_row.setSpacing(6)
        area_row.addWidget(self.inp_area, stretch=1)
        self._btn_distribute = QPushButton(chr(0xE897))
        self._btn_distribute.setFixedSize(32, 32)
        _f_ic = QFont("Material Symbols Rounded")
        _f_ic.setPixelSize(18)
        self._btn_distribute.setFont(_f_ic)
        self._btn_distribute.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_distribute.setToolTip("Поровну распределить доли между собственниками")
        self._btn_distribute.clicked.connect(self._distribute_shares)
        area_row.addWidget(self._btn_distribute)
        form.addRow("Площадь, м²:", area_row)

        # Вид права (как в выписке ЕГРН) — определяет, как делить начисление
        self.cb_form = QComboBox()
        for f in ownership.FORMS:
            self.cb_form.addItem(ownership.FORM_LABELS[f], f)
        cur_form = ownership.plot_ownership_form(self._plot_data)
        i = self.cb_form.findData(cur_form)
        if i >= 0:
            self.cb_form.setCurrentIndex(i)
        self.cb_form.currentIndexChanged.connect(self._on_form_changed)
        form.addRow("Вид права:", self.cb_form)

        self._egrn_doc = _EgrnDocWidget(self._plot_data.get("egrn_doc", ""))
        form.addRow("Выписка ЕГРН:", self._egrn_doc)
        lay.addLayout(form)

        # Заголовок секции владельцев с иконкой-подсказкой для чекбокса
        hdr_row = QWidget()
        hdr_row.setStyleSheet("background:transparent;")
        hdr_lay = QHBoxLayout(hdr_row)
        hdr_lay.setContentsMargins(0, 0, 0, 0)
        hdr_lay.setSpacing(6)
        lbl_fio = QLabel("Контактное лицо")
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
        self._lbl_share_hdr = QLabel("Доля")
        self._lbl_share_hdr.setFixedWidth(64)
        self._lbl_share_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_share_hdr.setStyleSheet("color:#9CA3AF; font-size:12px;")
        hdr_lay.addWidget(self._lbl_share_hdr)
        lbl_m2 = QLabel("м² (расч.)")
        lbl_m2.setFixedWidth(72)
        lbl_m2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_m2.setStyleSheet("color:#9CA3AF; font-size:12px;")
        hdr_lay.addWidget(lbl_m2)
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
        lbl_since_hdr = QLabel("Владеет с")
        lbl_since_hdr.setFixedWidth(92)
        lbl_since_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_since_hdr.setStyleSheet("color:#9CA3AF; font-size:12px;")
        hdr_lay.addWidget(lbl_since_hdr)
        # Заглушка под кнопки «В архив» (только в режиме редактирования) и удаления
        btn_stub = QWidget()
        btn_stub.setFixedWidth(62 if self._is_edit else 28)
        btn_stub.setStyleSheet("background:transparent;")
        hdr_lay.addWidget(btn_stub)
        lay.addWidget(hdr_row)

        self._owners_container = QWidget()
        self._owners_container.setStyleSheet("background:transparent;")
        self._owners_vlay = QVBoxLayout(self._owners_container)
        self._owners_vlay.setSpacing(6)
        self._owners_vlay.setContentsMargins(0, 0, 0, 0)

        # Показываем только текущих владельцев (без until); выбывшие — в архиве.
        current_owners = [o for o in self._plot_data.get("owners", []) or []
                          if not ownership.owner_until(o)]
        for owner in current_owners:
            self._add_owner_field(_owner_name(owner), _is_owner(owner),
                                  _owner_share_str(owner), _is_visible(owner),
                                  _owner_member_doc(owner), _owner_opd_doc(owner),
                                  _owner_phone(owner), _owner_email(owner),
                                  since=ownership.owner_since(owner))
        self._sync_visible_auto()

        lay.addWidget(self._owners_container)

        btn_add_owner = QPushButton("＋  Добавить собственника")
        btn_add_owner.setObjectName("btnSecondary")
        btn_add_owner.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_owner.clicked.connect(lambda: self._add_owner_field("", True, ""))
        lay.addWidget(btn_add_owner)

        # ── Секция «Архив (бывшие собственники)» ───────────────────────────
        self._archive_lbl = QLabel("Архив (бывшие собственники)")
        self._archive_lbl.setStyleSheet(
            "color:#6B7280; font-size:12px; font-weight:600; margin-top:6px;")
        lay.addWidget(self._archive_lbl)
        self._archive_container = QWidget()
        self._archive_container.setStyleSheet("background:transparent;")
        self._archive_vlay = QVBoxLayout(self._archive_container)
        self._archive_vlay.setSpacing(4)
        self._archive_vlay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._archive_container)
        self._render_archive()

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#E5E7EB;background:#E5E7EB;max-height:1px;")
        lay.addWidget(sep)

        self._area_warning = QLabel()
        self._area_warning.setObjectName("areaWarning")
        self._area_warning.setWordWrap(True)
        self._area_warning.setStyleSheet(
            "color:#9CA3AF; background:transparent; font-size:12px;"
        )
        lay.addWidget(self._area_warning)

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

        self._apply_form_ui()
        self._update_derived_m2()
        self._update_save_state()

    def _add_owner_field(self, name: str, is_owner: bool = True,
                         share: str = "", is_visible: bool = False,
                         doc_path: str = "", opd_path: str = "",
                         phone: str = "", email: str = "", since=None):
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

        # Доля в праве (как в ЕГРН: 1/2, 21/100). Площадь доли — производная.
        share_inp = QLineEdit(share or "")
        share_inp.setPlaceholderText("1/2")
        share_inp.setFixedWidth(64)
        share_inp.setEnabled(is_owner)
        share_inp.textChanged.connect(self._update_save_state)
        share_inp.textChanged.connect(self._update_derived_m2)
        chk.stateChanged.connect(lambda checked, s=share_inp: (
            s.setEnabled(bool(checked)),
            s.clear() if not checked else None,
        ))
        chk.stateChanged.connect(lambda _: self._update_derived_m2())
        self._owner_share.append(share_inp)
        rlay.addWidget(share_inp)

        m2_lbl = QLabel("—")
        m2_lbl.setFixedWidth(72)
        m2_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m2_lbl.setStyleSheet("color:#9CA3AF; background:transparent; font-size:12px;")
        self._owner_m2.append(m2_lbl)
        rlay.addWidget(m2_lbl)

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

        since_text = since.strftime("%d.%m.%Y") if isinstance(since, date) else ""
        since_inp = QLineEdit(since_text)
        since_inp.setPlaceholderText("дд.мм.гггг")
        since_inp.setFixedWidth(92)
        since_inp.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"^\d{0,2}\.?\d{0,2}\.?\d{0,4}$"), since_inp
        ))
        self._owner_since.append(since_inp)
        rlay.addWidget(since_inp)

        # Кнопка «В архив» (фиксирует дату ухода и снимок долга) — только при
        # редактировании существующего участка.
        if self._is_edit:
            btn_arch = QPushButton(chr(0xE149))   # archive
            btn_arch.setFixedSize(28, 28)
            btn_arch.setFont(QFont("Material Symbols Rounded", 16))
            btn_arch.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_arch.setToolTip("В архив (зафиксировать дату ухода и долг)")
            btn_arch.setStyleSheet(
                "QPushButton{background:transparent;border:none;color:#6B7280;}"
                "QPushButton:hover{color:#07414F;}"
            )
            btn_arch.clicked.connect(
                lambda _, r=row, i=inp, c=chk, v=vis, m=mem_doc, o=opd_doc,
                       sh=share_inp, ph=phone_inp, em=email_inp, sc=since_inp, ml=m2_lbl:
                    self._archive_owner(r, i, c, v, m, o, sh, ph, em, sc, ml)
            )
            rlay.addWidget(btn_arch)

        btn = QPushButton(chr(0xE92B))
        btn.setFixedSize(28, 28)
        btn.setFont(QFont("Material Symbols Rounded", 16))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#DC2626;}"
            "QPushButton:hover{color:#B91C1C;}"
        )
        btn.clicked.connect(
            lambda _, r=row, i=inp, c=chk, v=vis, m=mem_doc, o=opd_doc, sh=share_inp,
                   ph=phone_inp, em=email_inp, sc=since_inp, ml=m2_lbl:
                self._remove_owner_field(r, i, c, v, m, o, sh, ph, em, sc, ml)
        )
        rlay.addWidget(btn)
        self._owners_vlay.addWidget(row)
        self._apply_form_to_row(share_inp, m2_lbl, chk)
        self._update_save_state()
        self._update_derived_m2()

    def _remove_owner_field(self, row: QWidget, inp: QLineEdit,
                             chk: _IconCheckBox, vis: _IconRadioButton,
                             mem_doc: "_MemberDocWidget", opd_doc: "_OpdDocWidget",
                             share_inp: QLineEdit, phone_inp: QLineEdit,
                             email_inp: QLineEdit, since_inp: QLineEdit | None = None,
                             m2_lbl: "QLabel | None" = None):
        for lst, item in ((self._owner_inputs,       inp),
                          (self._owner_checks,        chk),
                          (self._owner_visible,       vis),
                          (self._owner_member_docs,   mem_doc),
                          (self._owner_opd_docs,      opd_doc),
                          (self._owner_share,         share_inp),
                          (self._owner_m2,            m2_lbl),
                          (self._owner_phones,        phone_inp),
                          (self._owner_emails,        email_inp),
                          (self._owner_since,         since_inp)):
            if item is not None and item in lst:
                lst.remove(item)
        row.setParent(None)
        row.deleteLater()
        self._update_save_state()
        self._update_derived_m2()
        QTimer.singleShot(0, self.adjustSize)

    def _current_form(self) -> str:
        return self.cb_form.currentData() if hasattr(self, "cb_form") else ownership.FORM_JOINT

    def _owner_indices(self) -> list[int]:
        """Индексы строк-собственников (галочка «собственник» включена)."""
        return [i for i, c in enumerate(self._owner_checks) if c.isChecked()]

    def _update_save_state(self):
        if self._btn_save is None:
            return
        fio_ok = (
            all(inp.text().strip() for inp in self._owner_inputs)
            if self._owner_inputs else True
        )
        form = self._current_form()
        owner_idxs = self._owner_indices()

        shares_ok = True
        warn = ""
        if form == ownership.FORM_INDIVIDUAL:
            if len(owner_idxs) > 1:
                shares_ok = False
                warn = "Индивидуальная собственность — должен быть один собственник (доля 1/1)."
        elif form == ownership.FORM_SHARED:
            # Сумма долей должна равняться 1
            total = 0.0
            all_filled = True
            for i in owner_idxs:
                v = ownership.parse_share(self._owner_share[i].text())
                if v is None:
                    all_filled = False
                else:
                    total += v
            if not owner_idxs:
                shares_ok = True
            elif not all_filled:
                shares_ok = False
                warn = "Долевая собственность — укажите долю каждого собственника (напр. 1/2)."
            elif abs(total - 1.0) > 1e-6:
                shares_ok = False
                warn = f"Сумма долей должна равняться 1 (сейчас {total:.4f}).".rstrip("0").rstrip(".")
        # FORM_JOINT — доли не нужны, ограничений нет

        ok = fio_ok and shares_ok
        self._btn_save.setEnabled(ok)
        self._btn_save.setCursor(
            Qt.CursorShape.PointingHandCursor if ok else Qt.CursorShape.ArrowCursor
        )
        if warn:
            self._area_warning.setStyleSheet(
                "color:#B45309; background:transparent; font-size:12px; font-weight:600;")
            self._area_warning.setText(f"ℹ  {warn}")
        else:
            note = {
                ownership.FORM_INDIVIDUAL: "Индивидуальная собственность: вся сумма начисляется одному.",
                ownership.FORM_SHARED: "Долевая собственность: начисление делится по доле каждого.",
                ownership.FORM_JOINT: "Совместная собственность: делится поровну; ответственность солидарная.",
            }.get(form, "")
            self._area_warning.setStyleSheet(
                "color:#9CA3AF; background:transparent; font-size:12px;")
            self._area_warning.setText(f"ℹ  {note}")

    def _distribute_shares(self):
        """Поровну распределить доли между собственниками (для долевой)."""
        idxs = self._owner_indices()
        idxs = [i for i in idxs if self._owner_inputs[i].text().strip()]
        n = len(idxs)
        if n == 0:
            return
        for i in idxs:
            self._owner_share[i].setText(f"1/{n}")
        self._update_derived_m2()
        self._update_save_state()

    # ── вид права / доли / производная площадь ───────────────────────────

    def _plot_area_value(self) -> float | None:
        raw = self.inp_area.text().strip().replace(",", ".")
        if not raw:
            return None
        try:
            v = float(raw)
            return v if v > 0 else None
        except ValueError:
            return None

    def _weights_for_form(self, owner_rows: list, form: str) -> list[float]:
        """Веса собственников по виду права (для производной площади/хранения)."""
        n = len(owner_rows)
        if n == 0:
            return []
        if form == ownership.FORM_SHARED:
            shares = [ownership.parse_share(r.get("share", "")) for r in owner_rows]
            if all(s is not None for s in shares) and sum(shares) > 0:  # type: ignore[arg-type]
                tot = sum(shares)  # type: ignore[arg-type]
                return [s / tot for s in shares]  # type: ignore[operator]
            return [1.0 / n] * n
        # individual / joint → поровну (для individual обычно один собственник)
        return [1.0 / n] * n

    def _derived_area_for_share(self, share: str) -> float | None:
        area_val = self._plot_area_value()
        s = ownership.parse_share(share)
        if area_val is None or s is None:
            return None
        return round(s * area_val, 2)

    def _on_form_changed(self):
        self._apply_form_ui()
        self._update_derived_m2()
        self._update_save_state()

    def _apply_form_to_row(self, share_inp, _m2_lbl=None, _chk=None):
        """Поле «Доля» в строке видно только для долевой собственности."""
        show_share = (self._current_form() == ownership.FORM_SHARED)
        share_inp.setVisible(show_share)

    def _apply_form_ui(self):
        is_shared = (self._current_form() == ownership.FORM_SHARED)
        for s in self._owner_share:
            s.setVisible(is_shared)
        if hasattr(self, "_lbl_share_hdr"):
            self._lbl_share_hdr.setVisible(is_shared)
        if hasattr(self, "_btn_distribute"):
            self._btn_distribute.setVisible(is_shared)

    def _update_derived_m2(self):
        if not self._owner_m2:
            return
        area_val = self._plot_area_value()
        form = self._current_form()
        owner_positions = [i for i, c in enumerate(self._owner_checks) if c.isChecked()]
        owner_rows = [{"share": self._owner_share[i].text()} for i in owner_positions]
        weights = self._weights_for_form(owner_rows, form)
        wmap = {pos: weights[k] for k, pos in enumerate(owner_positions)}
        for i, lbl in enumerate(self._owner_m2):
            w = wmap.get(i)
            if area_val is None or w is None:
                lbl.setText("—")
            else:
                lbl.setText(f"{round(w * area_val, 2):g}")

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
        form = self._current_form()

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

        # Сырьё по строкам
        raw_rows = []
        for inp, chk, vis, mem_doc, opd_doc, share_inp, phone_inp, email_inp, since_inp in zip(
                self._owner_inputs, self._owner_checks, self._owner_visible,
                self._owner_member_docs, self._owner_opd_docs, self._owner_share,
                self._owner_phones, self._owner_emails, self._owner_since):
            name = inp.text().strip()
            if not name:
                continue
            raw_rows.append({
                "name": name, "is_owner": chk.isChecked(), "visible": vis.isChecked(),
                "mem": mem_doc.get_path(), "opd": opd_doc.get_path(),
                "phone": phone_inp.text().strip(), "email": email_inp.text().strip(),
                "since": self._date_text_to_iso(since_inp.text()),
                "share": share_inp.text().strip(),
            })

        # Веса собственников по виду права → производная площадь доли
        owner_rows = [r for r in raw_rows if r["is_owner"]]
        weights = self._weights_for_form(owner_rows, form)

        owners = []
        wi = 0
        for r in raw_rows:
            share_to_store = ""
            area_to_store: float | None = None
            if r["is_owner"]:
                w = weights[wi] if wi < len(weights) else None
                wi += 1
                if area_val is not None and w is not None:
                    area_to_store = round(w * area_val, 2)
                if form == ownership.FORM_INDIVIDUAL:
                    share_to_store = "1/1"
                elif form == ownership.FORM_SHARED:
                    share_to_store = r["share"]
                # FORM_JOINT — доли не выделены, share не храним
            owners.append(_make_owner(
                r["name"], r["is_owner"], area_to_store, r["visible"],
                r["mem"], r["opd"], r["phone"], r["email"],
                since=r["since"], share=share_to_store))

        # Текущие (из виджетов) + выбывшие (хранятся ради истории владения).
        result = {"num": num, "owners": owners + self._departed,
                  "ownership_form": form}
        if area_val is not None:
            result["area"] = area_val
        egrn_path = self._egrn_doc.get_path()
        if egrn_path:
            result["egrn_doc"] = egrn_path
        for k in ("billing_type", "meter_commission_date", "meter_act_number",
                  "meter_location", "norm_kw", "norm_start_date",
                  "direct_contract_date", "direct_contract_number", "billing_history",
                  "ownership_history"):
            if k in self._plot_data:
                result[k] = self._plot_data[k]
        self._result = result
        self.accept()

    def get_result(self) -> dict:
        return getattr(self, "_result", {})

    @staticmethod
    def _date_text_to_iso(text: str) -> str:
        """'дд.мм.гггг' → 'гггг-мм-дд'. Пусто/ошибка → ''."""
        text = (text or "").strip()
        if not text:
            return ""
        try:
            return datetime.strptime(text, "%d.%m.%Y").date().isoformat()
        except ValueError:
            return ""

    # ── архив (бывшие собственники) ──────────────────────────────────────

    def _render_archive(self):
        """Перерисовывает секцию архива из self._departed."""
        while self._archive_vlay.count():
            item = self._archive_vlay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        has = bool(self._departed)
        self._archive_lbl.setVisible(has)
        self._archive_container.setVisible(has)
        if not has:
            return

        for idx, owner in enumerate(self._departed):
            row = QWidget()
            row.setStyleSheet("background:transparent;")
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)

            until = ownership.owner_until(owner)
            until_txt = until.strftime("%d.%m.%Y") if until else "—"
            debt = owner.get("debt_at_exit") if isinstance(owner, dict) else None
            debt_txt = (f"  ·  долг на дату ухода: {fmt_money(debt)}"
                        if debt is not None else "")
            lbl = QLabel(f"{_owner_name(owner)}  ·  владел по {until_txt}{debt_txt}")
            lbl.setStyleSheet("color:#6B7280; background:transparent; font-size:12px;")
            h.addWidget(lbl, stretch=1)

            btn_restore = QPushButton("↩ Вернуть")
            btn_restore.setObjectName("btnSecondary")
            btn_restore.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_restore.clicked.connect(lambda _, i=idx: self._restore_owner(i))
            h.addWidget(btn_restore)

            btn_del = QPushButton(chr(0xE92B))
            btn_del.setFixedSize(28, 28)
            btn_del.setFont(QFont("Material Symbols Rounded", 16))
            btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_del.setToolTip("Удалить из архива безвозвратно")
            btn_del.setStyleSheet(
                "QPushButton{background:transparent;border:none;color:#DC2626;}"
                "QPushButton:hover{color:#B91C1C;}")
            btn_del.clicked.connect(lambda _, i=idx: self._delete_archived(i))
            h.addWidget(btn_del)

            self._archive_vlay.addWidget(row)

    def _archive_owner(self, row, inp, chk, vis, mem_doc, opd_doc,
                       share_inp, phone_inp, email_inp, since_inp, m2_lbl=None):
        """Переносит текущего собственника в архив: спрашивает дату ухода,
        фиксирует снимок долга, проставляет until."""
        name = inp.text().strip()
        if not name:
            QMessageBox.warning(self, "В архив", "Сначала укажите ФИО собственника.")
            return
        exit_date = self._ask_exit_date()
        if exit_date is None:
            return
        exit_iso = exit_date.isoformat()

        share = share_inp.text().strip()
        # Площадь доли — производная (доля × площадь участка), хранится для совместимости.
        area_v = self._derived_area_for_share(share)
        owner = _make_owner(name, chk.isChecked(), area_v, vis.isChecked(),
                            mem_doc.get_path(), opd_doc.get_path(),
                            phone_inp.text().strip(), email_inp.text().strip(),
                            since=self._date_text_to_iso(since_inp.text()),
                            until=exit_iso, share=share)
        owner["debt_at_exit"] = self._compute_debt_snapshot(name, exit_date)

        self._departed.append(owner)
        self._remove_owner_field(row, inp, chk, vis, mem_doc, opd_doc,
                                 share_inp, phone_inp, email_inp, since_inp, m2_lbl)
        self._render_archive()

    def _restore_owner(self, idx: int):
        """Возвращает собственника из архива в текущие (снимает until/снимок)."""
        if not (0 <= idx < len(self._departed)):
            return
        owner = self._departed.pop(idx)
        if isinstance(owner, dict):
            owner.pop("until", None)
            owner.pop("debt_at_exit", None)
        self._add_owner_field(_owner_name(owner), _is_owner(owner),
                              _owner_share_str(owner), _is_visible(owner),
                              _owner_member_doc(owner), _owner_opd_doc(owner),
                              _owner_phone(owner), _owner_email(owner),
                              since=ownership.owner_since(owner))
        self._sync_visible_auto()
        self._render_archive()
        QTimer.singleShot(0, self.adjustSize)

    def _delete_archived(self, idx: int):
        if not (0 <= idx < len(self._departed)):
            return
        name = _owner_name(self._departed[idx])
        reply = QMessageBox.question(
            self, "Удалить из архива",
            f"Удалить «{name}» из архива безвозвратно?\n"
            f"История владения этого собственника будет потеряна.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._departed.pop(idx)
        self._render_archive()

    def _compute_debt_snapshot(self, name: str, exit_date) -> float:
        """Долг собственника по членским взносам на дату ухода (фиксируется)."""
        try:
            from core import vznosy
            rates = vznosy.load_rates()
            adj = vznosy.load_adjustments()
            area = vznosy.plot_area_map().get(str(self._plot_data.get("num", "")))
            plot_num = str(self._plot_data.get("num", ""))
            current = [o for o in self._plot_data.get("owners", []) or []
                       if not ownership.owner_until(o)]
            rows = vznosy.balances_by_owner(
                plot_num, area, exit_date, rates, adj, self._df, current,
                ownership_form=self._plot_data.get("ownership_form"))
            return round(next((r.debt for r in rows if r.name == name), 0.0), 2)
        except Exception:
            return 0.0

    def _ask_exit_date(self):
        """Маленький диалог выбора даты ухода. Возвращает date или None."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Дата выбытия собственника")
        dlg.setModal(True)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(10)
        lbl = QLabel("Дата перехода права (с этой даты участок принадлежит\n"
                     "уже новому собственнику):")
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
            "QDialogButtonBox QPushButton{background:#4F46E5;color:#fff;border:none;"
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
            QPushButton {
                background: #F8F9FA; border: 1px solid #D1D5DB;
                border-radius: 5px; color: #07414F; font-size: 14px;
            }
            QPushButton:hover { background: #E8F0F5; border: 1px solid #07414F; }
            QPushButton:pressed { background: #D5E5ED; }
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

