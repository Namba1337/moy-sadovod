import json
import os

import pandas as pd
from PyQt6.QtCore import (
    Qt, QEvent, QModelIndex, QAbstractItemModel, QPoint, QRect, pyqtSignal,
)
from PyQt6.QtGui import QAction, QColor, QFont, QPainter, QPen, QPolygon
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu,
    QMessageBox, QPushButton, QStyle, QStyledItemDelegate, QTableWidget,
    QTableWidgetItem, QTreeView, QVBoxLayout, QWidget,
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


# ============================================================================ #
#  Отношение к участку                                                         #
# ============================================================================ #

RELATIONS = ["Главный собственник", "Собственник", "Контактное лицо"]
DEFAULT_RELATION = "Собственник"

_RELATION_BG = {
    "Главный собственник": QColor("#EEF2FF"),
    "Собственник":         QColor("#F0FDF4"),
    "Контактное лицо":     QColor("#FFF7ED"),
}
_RELATION_FG = {
    "Главный собственник": QColor("#4338CA"),
    "Собственник":         QColor("#15803D"),
    "Контактное лицо":     QColor("#C2410C"),
}


def _owner_name(owner) -> str:
    if isinstance(owner, dict):
        return owner.get("name", "")
    return str(owner)


def _owner_relation(owner) -> str:
    if isinstance(owner, dict):
        return owner.get("relation", DEFAULT_RELATION)
    return DEFAULT_RELATION


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


def _make_owner(name: str, relation: str = DEFAULT_RELATION,
                area: float | None = None) -> dict:
    d: dict = {"name": name, "relation": relation}
    if area is not None:
        d["area"] = area
    return d


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

_ADD_COL = "\x00add"  # служебный столбец с кнопкой ＋


class PlotsTreeModel(QAbstractItemModel):
    COLUMNS = ["Участок", _ADD_COL, "ФИО", "Отношение", "Площадь, м²"]

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

        if col == _ADD_COL:
            return None

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if node.kind == "plot":
                if col == "Участок":
                    return f"уч. {node.plot_ref.get('num', '?')}"
                if col == "ФИО":
                    owners = node.plot_ref.get("owners", []) or []
                    if not owners:
                        return "—"
                    main = next(
                        (o for o in owners if _owner_relation(o) == "Главный собственник"),
                        owners[0],
                    )
                    name = _owner_name(main)
                    extra = len(owners) - 1
                    return name if extra == 0 else f"{name}  (+{extra})"
                if col == "Площадь, м²":
                    area = node.plot_ref.get("area")
                    try:
                        v = float(area) if area not in (None, "") else None
                    except (TypeError, ValueError):
                        v = None
                    return f"{v:g}" if v is not None else "—"
                return ""
            elif node.kind == "owner":
                owners = node.plot_ref.get("owners", [])
                owner = owners[node.owner_idx] if 0 <= node.owner_idx < len(owners) else None
                if col == "ФИО":
                    return _owner_name(owner) if owner is not None else ""
                if col == "Отношение":
                    return _owner_relation(owner) if owner is not None else ""
                if col == "Площадь, м²":
                    if owner is None:
                        return ""
                    v = _owner_area(owner)
                    if role == Qt.ItemDataRole.EditRole:
                        return f"{v:g}" if v is not None else ""
                    return f"{v:g}" if v is not None else "—"
                return ""

        if role == Qt.ItemDataRole.ForegroundRole:
            if node.kind == "plot":
                if col == "Участок":
                    return QColor("#6366F1")
                if col == "Площадь, м²":
                    area = node.plot_ref.get("area")
                    return QColor("#9CA3AF") if area in (None, "") else QColor("#374151")
                return QColor("#374151")
            elif node.kind == "owner":
                return QColor("#555F6D")

        if role == Qt.ItemDataRole.FontRole:
            if node.kind == "plot" and col == "Участок":
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
            return "" if col == _ADD_COL else col
        return None

    # -- edit ---------------------------------------------------------------- #

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        node = index.internalPointer()
        col = self.COLUMNS[index.column()]
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if node.kind == "owner" and col in ("ФИО", "Отношение", "Площадь, м²"):
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        node = index.internalPointer()
        col = self.COLUMNS[index.column()]
        if node.kind == "owner" and col in ("ФИО", "Отношение", "Площадь, м²"):
            text = str(value).strip()
            owners = node.plot_ref.get("owners", [])
            if not (0 <= node.owner_idx < len(owners)):
                return False
            old = owners[node.owner_idx]
            if col == "ФИО":
                if not text:
                    return False
                owners[node.owner_idx] = _make_owner(
                    text, _owner_relation(old), _owner_area(old))
                pn = node.parent
                if pn is not None:
                    fio_col = self.COLUMNS.index("ФИО")
                    self.dataChanged.emit(self.createIndex(pn.row(), fio_col, pn),
                                         self.createIndex(pn.row(), fio_col, pn))
            elif col == "Отношение":
                if text not in RELATIONS:
                    return False
                if text == "Главный собственник":
                    # Разжалуем предыдущего главного собственника → Собственник
                    rel_col = self.COLUMNS.index("Отношение")
                    plot_node = node.parent
                    for sibling in plot_node.children:
                        if sibling is node:
                            continue
                        sidx = sibling.owner_idx
                        if (0 <= sidx < len(owners)
                                and _owner_relation(owners[sidx]) == "Главный собственник"):
                            o = owners[sidx]
                            owners[sidx] = _make_owner(
                                _owner_name(o), "Собственник", _owner_area(o))
                            sib_mi = self.createIndex(sibling.row(), rel_col, sibling)
                            self.dataChanged.emit(sib_mi, sib_mi)
                owners[node.owner_idx] = _make_owner(
                    _owner_name(old), text, _owner_area(old))
                pn = node.parent
                if pn is not None:
                    fio_col = self.COLUMNS.index("ФИО")
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
                    _owner_name(old), _owner_relation(old), area)
            self.dataChanged.emit(index, index)
            self.ownerDataEdited.emit()
            return True
        return False

    # -- sort ---------------------------------------------------------------- #

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if not (0 <= column < len(self.COLUMNS)):
            return
        col = self.COLUMNS[column]
        if col == _ADD_COL:
            return
        self.beginResetModel()
        reverse = order == Qt.SortOrder.DescendingOrder
        if col == "Участок":
            self._root.children.sort(
                key=lambda n: _plot_num_key(str(n.plot_ref.get("num", ""))),
                reverse=reverse,
            )
        elif col == "ФИО":
            def _fio_key(n):
                owners = n.plot_ref.get("owners") or []
                main = next(
                    (o for o in owners if _owner_relation(o) == "Главный собственник"),
                    owners[0] if owners else {},
                )
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
                  relation: str = DEFAULT_RELATION) -> QModelIndex:
        """Добавляет владельца в участок и возвращает индекс строки участка."""
        plot = plot_node.plot_ref
        owners = plot.setdefault("owners", [])
        new_idx = len(owners)
        owners.append(_make_owner(name, relation))
        parent_mi = self.createIndex(plot_node.row(), 0, plot_node)
        self.beginInsertRows(parent_mi, new_idx, new_idx)
        owner_node = _PlotNode("owner", plot_ref=plot, owner_idx=new_idx, parent=plot_node)
        plot_node.children.append(owner_node)
        self.endInsertRows()
        # Обновляем ФИО в родительской строке
        fio_col = self.COLUMNS.index("ФИО")
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
        fio_col = self.COLUMNS.index("ФИО")
        fio_mi = self.createIndex(plot_node.row(), fio_col, plot_node)
        self.dataChanged.emit(fio_mi, fio_mi)


# ============================================================================ #
#  Делегат служебного столбца ＋                                               #
# ============================================================================ #

class _AddOwnerDelegate(QStyledItemDelegate):
    """Рисует стрелку свернуть/развернуть и кнопку ＋ для строк-участков."""

    addOwnerRequested = pyqtSignal(QModelIndex)
    toggleRequested   = pyqtSignal(QModelIndex)

    _ARROW_COLOR = QColor("#5B6675")
    _PLUS_BG  = QColor("#D6F0DC")
    _PLUS_FG  = QColor("#15803D")
    _BG       = QColor("#F9FAFB")
    _BG_HOVER = QColor("#DDE4EE")
    _BG_SEL   = QColor("#C9D8E2")
    _BORDER   = QColor("#D8DDE6")
    _ARROW_W          = 22
    _BTN_W, _BTN_H    = 22, 20

    def __init__(self, view):
        super().__init__(view)
        self._view = view

    def _arrow_rect(self, cell_rect: QRect) -> QRect:
        return QRect(cell_rect.left() + 2, cell_rect.top(),
                     self._ARROW_W, cell_rect.height())

    def _btn_rect(self, cell_rect: QRect) -> QRect:
        y = cell_rect.top() + (cell_rect.height() - self._BTN_H) // 2
        x = cell_rect.left() + self._ARROW_W + 4
        return QRect(x, y, self._BTN_W, self._BTN_H)

    def paint(self, painter, option, index):
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, self._BG_SEL)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, self._BG_HOVER)
        else:
            painter.fillRect(option.rect, self._BG)
        painter.setPen(QPen(self._BORDER, 1))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())

        node = index.internalPointer()
        if node is not None and node.kind == "plot":
            if node.children:
                # isExpanded корректно работает через col-0 индекс
                col0 = self._view.model().index(index.row(), 0,
                                                self._view.model().parent(index))
                self._paint_arrow(painter, self._arrow_rect(option.rect),
                                  self._view.isExpanded(col0))
            self._paint_plus(painter, self._btn_rect(option.rect))
        painter.restore()

    def _paint_arrow(self, painter, rect: QRect, expanded: bool):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._ARROW_COLOR)
        painter.setPen(Qt.PenStyle.NoPen)
        cx = rect.left() + rect.width() // 2
        cy = rect.top() + rect.height() // 2
        if expanded:
            pts = [QPoint(cx - 4, cy - 2), QPoint(cx + 4, cy - 2), QPoint(cx, cy + 3)]
        else:
            pts = [QPoint(cx - 2, cy - 4), QPoint(cx + 3, cy), QPoint(cx - 2, cy + 4)]
        painter.drawPolygon(QPolygon(pts))
        painter.restore()

    def _paint_plus(self, painter, rect: QRect):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._PLUS_BG)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(self._PLUS_FG)
        f = QFont()
        f.setPixelSize(13)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "＋")
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if (event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton):
            node = index.internalPointer()
            pos = event.position().toPoint()
            if node is not None and node.kind == "plot":
                if node.children and self._arrow_rect(option.rect).contains(pos):
                    self.toggleRequested.emit(index)
                    return True
                if self._btn_rect(option.rect).contains(pos):
                    self.addOwnerRequested.emit(index)
                    return True
        return super().editorEvent(event, model, option, index)


# ============================================================================ #
#  Делегат столбца «Отношение»                                                 #
# ============================================================================ #

class _RelationDelegate(QStyledItemDelegate):
    """Рисует цветной badge для значения отношения; на edit создаёт QComboBox."""

    _BG       = QColor("#F9FAFB")
    _BG_HOVER = QColor("#DDE4EE")
    _BG_SEL   = QColor("#C9D8E2")
    _BORDER   = QColor("#D8DDE6")

    def paint(self, painter, option, index):
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, self._BG_SEL)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, self._BG_HOVER)
        else:
            painter.fillRect(option.rect, self._BG)
        painter.setPen(QPen(self._BORDER, 1))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())

        node = index.internalPointer()
        if node is None or node.kind != "owner":
            painter.restore()
            return

        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not text:
            painter.restore()
            return

        bg = _RELATION_BG.get(text, QColor("#F3F4F6"))
        fg = _RELATION_FG.get(text, QColor("#374151"))

        rect = option.rect.adjusted(6, 4, -6, -4)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(bg)
        painter.setPen(Qt.PenStyle.NoPen)
        radius = rect.height() // 2
        painter.drawRoundedRect(rect, radius, radius)

        painter.setPen(fg)
        f = QFont()
        f.setPixelSize(12)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def createEditor(self, parent, option, index):
        node = index.internalPointer()
        if node is None or node.kind != "owner":
            return None
        combo = QComboBox(parent)
        combo.addItems(RELATIONS)
        combo.setStyleSheet(
            "QComboBox{background:#FFFFFF;border:1px solid #6366F1;border-radius:5px;"
            "padding:4px 8px;font-size:13px;color:#374151;}"
            "QComboBox::drop-down{border:none;width:20px;}"
            "QComboBox QAbstractItemView{background:#FFFFFF;border:1px solid #D1D5DB;"
            "border-radius:4px;selection-background-color:#EEF2FF;color:#374151;}"
        )
        return combo

    def setEditorData(self, editor, index):
        val = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        idx = editor.findText(val)
        editor.setCurrentIndex(idx if idx >= 0 else 0)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


# ============================================================================ #
#  Стиль дерева                                                                #
# ============================================================================ #

_TREE_STYLE = """
    QTreeView#mainTable {
        background: #F9FAFB; border: 1px solid #D8DDE6; border-radius: 8px;
        color: #1F2937; font-size: 13px;
        selection-background-color: #C9D8E2; selection-color: #07414F;
        outline: 0;
    }
    QTreeView#mainTable::item {
        padding: 5px 8px; border-bottom: 1px solid #D8DDE6;
    }
    QTreeView#mainTable::item:hover { background: #DDE4EE; }
    QTreeView#mainTable::item:selected { background: #C9D8E2; color: #07414F; }
    QHeaderView::section {
        background: #E9EDF5; color: #4B5563; border: none;
        border-right: 1px solid #CDD3DC; border-bottom: 2px solid #C4CBD7;
        padding: 8px 10px; font-size: 12px; font-weight: 600;
    }
    QHeaderView::section:last { border-right: none; }
"""


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

        self.tree = QTreeView(objectName="mainTable")
        self.tree.setModel(self.model)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.tree.setSortingEnabled(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMouseTracking(True)
        self.tree.setStyleSheet(_TREE_STYLE)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)

        add_col_idx = PlotsTreeModel.COLUMNS.index(_ADD_COL)
        self._add_delegate = _AddOwnerDelegate(self.tree)
        self._add_delegate.addOwnerRequested.connect(self._on_add_owner)
        self._add_delegate.toggleRequested.connect(self._on_toggle)
        self.tree.setItemDelegateForColumn(add_col_idx, self._add_delegate)

        rel_col_idx = PlotsTreeModel.COLUMNS.index("Отношение")
        self._relation_delegate = _RelationDelegate(self.tree)
        self.tree.setItemDelegateForColumn(rel_col_idx, self._relation_delegate)

        layout.addWidget(self.tree)

    def _rebuild_table(self):
        hdr = self.tree.header()
        sort_col = hdr.sortIndicatorSection()
        sort_order = hdr.sortIndicatorOrder()

        self.model.load(self._plots)

        # Восстанавливаем сортировку (по умолчанию — по номеру участка по возрастанию)
        if sort_col < 0 or sort_col >= len(PlotsTreeModel.COLUMNS):
            sort_col = 0
            sort_order = Qt.SortOrder.AscendingOrder
        self.model.sort(sort_col, sort_order)

        hdr.setSortIndicator(sort_col, sort_order)
        hdr.setSortIndicatorShown(True)
        hdr.setStretchLastSection(False)

        col_участок = 0
        col_add = PlotsTreeModel.COLUMNS.index(_ADD_COL)
        col_fio = PlotsTreeModel.COLUMNS.index("ФИО")
        col_rel = PlotsTreeModel.COLUMNS.index("Отношение")
        col_area = PlotsTreeModel.COLUMNS.index("Площадь, м²")

        hdr.setSectionResizeMode(col_участок, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(col_add, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(col_add, 56)
        hdr.setSectionResizeMode(col_fio, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(col_rel, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(col_rel, 180)
        hdr.setSectionResizeMode(col_area, QHeaderView.ResizeMode.ResizeToContents)

        self.status_label.setText(f"Участков: {len(self._plots)}")

    def _on_toggle(self, index: QModelIndex):
        col0 = self.model.index(index.row(), 0, self.model.parent(index))
        if self.tree.isExpanded(col0):
            self.tree.collapse(col0)
        else:
            self.tree.expand(col0)

    def _on_add_owner(self, index: QModelIndex):
        node = index.internalPointer()
        if node is None or node.kind != "plot":
            return
        name, ok = QInputDialog.getText(
            self,
            "Добавить владельца",
            f"ФИО для участка № {node.plot_ref.get('num', '?')}:",
            QLineEdit.EchoMode.Normal,
            "",
        )
        if ok and name.strip():
            plot_mi = self.model.add_owner(node, name.strip())
            self._save()
            self.tree.expand(plot_mi)

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
            eligible = sum(
                1 for o in owners
                if _owner_relation(o) in ("Собственник", "Главный собственник")
            )
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
        eligible_idx = [
            i for i, o in enumerate(owners)
            if _owner_relation(o) in ("Собственник", "Главный собственник")
        ]
        if not eligible_idx:
            QMessageBox.warning(self, "Нет собственников",
                                "Нет владельцев с тегом «Собственник» "
                                "или «Главный собственник».")
            return

        share = round(total / len(eligible_idx), 2)
        area_col = PlotsTreeModel.COLUMNS.index("Площадь, м²")

        for child in plot_node.children:
            if child.owner_idx not in eligible_idx:
                continue
            o = owners[child.owner_idx]
            owners[child.owner_idx] = _make_owner(
                _owner_name(o), _owner_relation(o), share)
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
#  PlotEditDialog                                                              #
# ============================================================================ #

class PlotEditDialog(QDialog):
    """Диалог добавления / редактирования участка."""

    def __init__(self, plot_data: dict | None = None, parent=None):
        super().__init__(parent)
        self._is_edit = plot_data is not None
        self._plot_data = plot_data or {}
        self.setWindowTitle("Редактировать участок" if self._is_edit else "Новый участок")
        self.setMinimumWidth(520)
        self.setModal(True)
        self._owner_inputs: list[QLineEdit] = []
        self._owner_combos: list[QComboBox] = []
        self._owner_areas:  list[QLineEdit] = []
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
        if self._is_edit:
            self.inp_num.setReadOnly(True)
            self.inp_num.setStyleSheet(
                "background:#F3F4F6;border:1px solid #E5E7EB;"
                "border-radius:5px;color:#9CA3AF;padding:7px 10px;"
            )
        form.addRow("Номер участка:", self.inp_num)

        area_raw = self._plot_data.get("area")
        area_text = ""
        if area_raw not in (None, "", 0):
            try:
                area_text = f"{float(area_raw):g}"
            except (TypeError, ValueError):
                area_text = str(area_raw)
        self.inp_area = QLineEdit(area_text)
        self.inp_area.setPlaceholderText("например: 612 (необязательно)")
        form.addRow("Площадь, м²:", self.inp_area)
        lay.addLayout(form)

        own_label = QLabel("ФИО:")
        own_label.setStyleSheet("color:#9CA3AF;")
        lay.addWidget(own_label)

        self._owners_container = QWidget()
        self._owners_container.setStyleSheet("background:transparent;")
        self._owners_vlay = QVBoxLayout(self._owners_container)
        self._owners_vlay.setSpacing(6)
        self._owners_vlay.setContentsMargins(0, 0, 0, 0)

        existing_owners = self._plot_data.get("owners", [])
        if not existing_owners:
            existing_owners = [_make_owner("")]
        for owner in existing_owners:
            self._add_owner_field(_owner_name(owner), _owner_relation(owner), _owner_area(owner))

        lay.addWidget(self._owners_container)

        btn_add_owner = QPushButton("＋  Добавить")
        btn_add_owner.setObjectName("btnSecondary")
        btn_add_owner.clicked.connect(lambda: self._add_owner_field("", DEFAULT_RELATION, None))
        lay.addWidget(btn_add_owner)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#E5E7EB;background:#E5E7EB;max-height:1px;")
        lay.addWidget(sep)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Сохранить")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _add_owner_field(self, name: str, relation: str = DEFAULT_RELATION,
                         area: float | None = None):
        row = QWidget()
        row.setStyleSheet("background:transparent;")
        rlay = QHBoxLayout(row)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(6)

        inp = QLineEdit(name)
        inp.setPlaceholderText("Фамилия Имя Отчество")
        self._owner_inputs.append(inp)
        rlay.addWidget(inp, stretch=3)

        combo = QComboBox()
        combo.addItems(RELATIONS)
        idx = combo.findText(relation)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.setStyleSheet(
            "QComboBox{background:#F8F9FA;border:1px solid #D1D5DB;border-radius:5px;"
            "padding:6px 8px;font-size:12px;color:#374151;min-width:150px;}"
            "QComboBox::drop-down{border:none;width:20px;}"
        )
        self._owner_combos.append(combo)
        combo.currentIndexChanged.connect(
            lambda _, c=combo: self._enforce_single_main(c)
        )
        rlay.addWidget(combo, stretch=2)

        area_inp = QLineEdit("" if area is None else f"{area:g}")
        area_inp.setPlaceholderText("м² (необяз.)")
        area_inp.setFixedWidth(80)
        self._owner_areas.append(area_inp)
        rlay.addWidget(area_inp)

        btn = QPushButton("✕")
        btn.setFixedSize(28, 28)
        btn.setStyleSheet(
            "QPushButton{background:#2a1a1a;border:1px solid #5a2a2a;"
            "border-radius:5px;color:#DC2626;font-size:12px;}"
            "QPushButton:hover{background:#3a2020;}"
        )
        btn.clicked.connect(
            lambda _, r=row, i=inp, c=combo, a=area_inp: self._remove_owner_field(r, i, c, a)
        )
        rlay.addWidget(btn)
        self._owners_vlay.addWidget(row)

    def _enforce_single_main(self, changed: QComboBox):
        if changed.currentText() != "Главный собственник":
            return
        for c in self._owner_combos:
            if c is not changed and c.currentText() == "Главный собственник":
                c.blockSignals(True)
                c.setCurrentIndex(c.findText("Собственник"))
                c.blockSignals(False)

    def _remove_owner_field(self, row: QWidget, inp: QLineEdit,
                             combo: QComboBox, area_inp: QLineEdit):
        if len(self._owner_inputs) <= 1:
            inp.clear()
            return
        for lst, item in ((self._owner_inputs, inp),
                          (self._owner_combos, combo),
                          (self._owner_areas,  area_inp)):
            if item in lst:
                lst.remove(item)
        row.setParent(None)
        row.deleteLater()

    def _on_accept(self):
        num = self.inp_num.text().strip()
        if not num:
            QMessageBox.warning(self, "Ошибка", "Укажите номер участка")
            return
        owners = []
        for inp, combo, area_inp in zip(
                self._owner_inputs, self._owner_combos, self._owner_areas):
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
            owners.append(_make_owner(name, combo.currentText(), area))

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

        result = {"num": num, "owners": owners}
        if area_val is not None:
            result["area"] = area_val
        # Тип расчёта редактируется на вкладке «Электричество» — здесь поля
        # переносятся без изменений, чтобы не затереть конфигурацию начисления.
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
            QLineEdit:focus { border: 1px solid #6366F1; }
            QPushButton#btnSecondary {
                background: #E5E7EB; color: #6B7280; border: 1px solid #D1D5DB;
                border-radius: 6px; padding: 7px 14px; font-size: 13px;
            }
            QPushButton#btnSecondary:hover { background: #E5E7EB; color: #374151; }
            QDialogButtonBox QPushButton {
                background: #4F46E5; color: white; border: none;
                border-radius: 6px; padding: 8px 20px; font-size: 13px; font-weight: 600;
            }
            QDialogButtonBox QPushButton:hover { background: #6366F1; }
            QDialogButtonBox QPushButton[text='Отмена'] {
                background: #E5E7EB; color: #6B7280;
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
