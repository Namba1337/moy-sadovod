import hashlib
import json
import os

import pandas as pd
from PyQt6.QtCore import (
    Qt, QDate, QEvent, QPoint, QRect, QModelIndex, QAbstractItemModel, pyqtSignal,
)
from PyQt6.QtGui import QAction, QBrush, QColor, QFont, QPainter, QPen, QPolygon
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QScrollArea, QStyle, QStyledItemDelegate, QTreeView,
    QVBoxLayout, QWidget,
)

from ui.categorization import (
    CATEGORY_COLORS, ALL_CATEGORIES, apply_categorization, categorize_row,
    save_user_categories, save_user_category_color, rename_user_category,
    delete_user_category, PROTECTED_CATEGORIES,
)
from ui.plot_detection import apply_plot_column, get_plot, _PLOTS_FILE, load_plot_numbers


# =========================================================================== #
#  Вспомогательные функции для DataFrame                                      #
# =========================================================================== #

def _merge_to_summa(df: "pd.DataFrame") -> "pd.DataFrame":
    """Если в DataFrame есть Поступление/Списание — объединяет их в Сумма.
    Если Сумма уже есть — ничего не делает."""
    if "Сумма" in df.columns:
        return df
    if "Поступление" not in df.columns and "Списание" not in df.columns:
        return df
    inc = pd.to_numeric(df["Поступление"], errors="coerce") if "Поступление" in df.columns \
        else pd.Series(0.0, index=df.index)
    exp = pd.to_numeric(df["Списание"],    errors="coerce") if "Списание"    in df.columns \
        else pd.Series(0.0, index=df.index)
    summa = inc.fillna(0) - exp.fillna(0)
    summa = summa.where(summa != 0, other=float("nan"))
    pos = list(df.columns).index("Поступление") if "Поступление" in df.columns else len(df.columns)
    df = df.drop(columns=[c for c in ("Поступление", "Списание") if c in df.columns])
    df.insert(min(pos, len(df.columns)), "Сумма", summa)
    return df


def _compute_hash(row: dict) -> str:
    """SHA-256 (12 символов) по ключевым полям — стабильный ID операции."""
    parts = [
        str(row.get("Дата", ""))[:10],
        str(row.get("Сумма", "")),
        str(row.get("Контрагент") or "").strip(),
        str(row.get("Назначение") or "").strip(),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _ensure_meta_columns(df: "pd.DataFrame") -> "pd.DataFrame":
    """Добавляет _hash и _breakdown, если их ещё нет."""
    df = df.copy()
    if "_hash" not in df.columns:
        df["_hash"] = df.apply(lambda r: _compute_hash(r.to_dict()), axis=1)
    if "_breakdown" not in df.columns:
        df["_breakdown"] = None
    return df


def _parse_breakdown(value) -> list:
    """Читает разбивку операции (хранится в колонке _breakdown как JSON-строка).
    Возвращает список словарей вида {Назначение, Сумма, Категория}."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            data = json.loads(value)
            return data if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []
    return []


def _dump_breakdown(items: list) -> str:
    """Сериализует разбивку в JSON-строку для хранения в _breakdown."""
    if not items:
        return ""
    return json.dumps(items, ensure_ascii=False)


# =========================================================================== #
#  Парсинг и форматирование значений                                          #
# =========================================================================== #

def _to_num(v):
    """Приводит значение к float или None (для пустых/нечисловых)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and pd.isna(v)) else float(v)
    try:
        return float(
            str(v).replace(" ", "").replace(" ", "")
                  .replace("−", "-").replace("₽", "").replace(",", ".")
        )
    except ValueError:
        return None


def _parse_money(text: str) -> float:
    n = _to_num(text)
    return float("nan") if n is None else n


def _fmt_money(num: float) -> str:
    if num > 0:
        return f"{num:,.2f} ₽".replace(",", " ")
    if num < 0:
        return f"−{abs(num):,.2f} ₽".replace(",", " ")
    return ""


def _num_edit_str(num: float) -> str:
    """Чистое строковое представление числа для редактора (без ₽ и пробелов)."""
    return ("%g" % num)


def _to_ts(v):
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    try:
        ts = pd.Timestamp(v)
        return None if pd.isna(ts) else ts
    except (ValueError, TypeError):
        return None


def _parse_date(text: str):
    try:
        ts = pd.to_datetime(text, dayfirst=True)
        return None if pd.isna(ts) else ts
    except (ValueError, TypeError):
        return None


# =========================================================================== #
#  Узел дерева и модель                                                        #
# =========================================================================== #

# Роль, по которой делегат понимает, что ячейка была отредактирована вручную.
MANUAL_ROLE = Qt.ItemDataRole.UserRole + 1

_DEFAULT_ROW_COLOR = QColor(55, 55, 60)
# Заглушка для родительской строки, у которой есть дочерние строки-распределения.
_MULTI_OP_LABEL = "Мультиоперация"
_MULTI_OP_COLOR = QColor(110, 110, 118)   # приглушённый серый
# Служебный столбец управления (стрелка/＋/корзина). Хранится первым в модели.
_CTRL_COL = "\x00ctrl"
# Колонки, отображаемые в строке-ветке (split). «Контрагент» — копия из операции.
_SPLIT_DISPLAY_COLS = ("Контрагент", "Сумма", "Категория", "Участок")
# Из них реально редактируемые в ветке.
_SPLIT_EDIT_COLS = ("Сумма", "Категория", "Участок")


class _Node:
    """Узел дерева. kind="op" — операция (строка выписки),
    kind="split" — распределение внутри операции."""

    __slots__ = ("kind", "data", "df_idx", "parent", "children")

    def __init__(self, kind: str, data: dict, df_idx=None, parent=None):
        self.kind = kind
        self.data = data          # словарь {имя_колонки: значение}
        self.df_idx = df_idx      # индекс в df_full (только для операций)
        self.parent = parent
        self.children: list[_Node] = []

    def row(self) -> int:
        if self.parent is not None:
            return self.parent.children.index(self)
        return 0


class OperationsTreeModel(QAbstractItemModel):
    """Двухуровневая модель: операции -> распределения по категориям.

    Сумма операции НЕ вычисляется из детей — она остаётся исходным значением
    выписки. Дети-распределения это аннотация (на что пошли деньги)."""

    # Эмитится при ручном редактировании ячейки: (узел, имя_колонки).
    cellEdited = pyqtSignal(object, str)

    def __init__(self, manual_cells: set, parent=None):
        super().__init__(parent)
        self._manual = manual_cells
        self._columns: list[str] = []
        self._root = _Node("root", {})
        self._sort_col: int | None = None
        self._sort_order = Qt.SortOrder.AscendingOrder

    # -- наполнение --------------------------------------------------------- #
    def load(self, columns: list[str], records: list[tuple]):
        """records: список (df_idx, data_dict, breakdown_list)."""
        self.beginResetModel()
        self._columns = [_CTRL_COL] + list(columns)
        root = _Node("root", {})
        for df_idx, data, breakdown in records:
            op = _Node("op", data, df_idx=df_idx, parent=root)
            for b in breakdown:
                op.children.append(_Node("split", dict(b), parent=op))
            root.children.append(op)
        self._root = root
        self._apply_sort_internal()
        self.endResetModel()

    def top_nodes(self) -> list:
        return self._root.children

    def category_column(self) -> int:
        return self._columns.index("Категория") if "Категория" in self._columns else -1

    def columns(self) -> list:
        return self._columns

    def index_for_df_idx(self, df_idx) -> QModelIndex:
        for i, op in enumerate(self._root.children):
            if op.df_idx == df_idx:
                return self.createIndex(i, 0, op)
        return QModelIndex()

    # -- ядро дерева -------------------------------------------------------- #
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
        return len(self._columns)

    # -- чтение ------------------------------------------------------------- #
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer()
        col = self._columns[index.column()]

        # Служебный столбец — всё рисует делегат, модель данных не отдаёт.
        if col == _CTRL_COL:
            return None

        if role == MANUAL_ROLE:
            return node.kind == "op" and (node.df_idx, col) in self._manual

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if node.kind == "split" and col not in _SPLIT_DISPLAY_COLS:
                return ""
            # Родительская строка с дочерними → Категория и Участок скрываются заглушкой
            if (col in ("Категория", "Участок") and role == Qt.ItemDataRole.DisplayRole
                    and node.kind == "op" and node.children):
                return _MULTI_OP_LABEL
            val = node.data.get(col)
            if col == "Сумма":
                num = _to_num(val)
                if role == Qt.ItemDataRole.EditRole:
                    return "" if num is None else _num_edit_str(num)
                return "" if not num else _fmt_money(num)
            if col == "Дата":
                ts = _to_ts(val)
                return "" if ts is None else ts.strftime("%d.%m.%Y")
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return ""
            return str(val)

        if role == Qt.ItemDataRole.ForegroundRole:
            if col == "Сумма":
                num = _to_num(node.data.get(col))
                if num is not None and num > 0:
                    return QColor("#059669")
                if num is not None and num < 0:
                    return QColor("#DC2626")
                return QColor("#374151")

        if role == Qt.ItemDataRole.BackgroundRole:
            cat = str(node.data.get("Категория", ""))
            return CATEGORY_COLORS.get(cat, _DEFAULT_ROW_COLOR)

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == "Сумма":
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self._columns):
                name = self._columns[section]
                return "" if name == _CTRL_COL else name
        return None

    # -- редактирование ----------------------------------------------------- #
    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        node = index.internalPointer()
        col = self._columns[index.column()]
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if col == _CTRL_COL:
            return f
        if node.kind == "op":
            # Категория и Участок недоступны для редактирования у строк с дочерними
            if col in ("Категория", "Участок") and node.children:
                pass
            else:
                f |= Qt.ItemFlag.ItemIsEditable
        elif col in _SPLIT_EDIT_COLS:
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        if not (self.flags(index) & Qt.ItemFlag.ItemIsEditable):
            return False
        node = index.internalPointer()
        col = self._columns[index.column()]
        text = str(value).strip()

        if col == "Сумма":
            node.data["Сумма"] = _parse_money(text)
        elif col == "Дата":
            ts = _parse_date(text)
            if ts is None:
                return False
            node.data["Дата"] = ts
        else:
            node.data[col] = text

        # Перерисовываем всю строку — фон зависит от категории, которая могла измениться.
        left = self.index(index.row(), 0, index.parent())
        right = self.index(index.row(), self.columnCount() - 1, index.parent())
        self.dataChanged.emit(left, right)
        self.cellEdited.emit(node, col)
        return True

    # -- сортировка --------------------------------------------------------- #
    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if 0 <= column < len(self._columns) and self._columns[column] == _CTRL_COL:
            return
        self._sort_col = column
        self._sort_order = order
        self.beginResetModel()
        self._apply_sort_internal()
        self.endResetModel()

    def _apply_sort_internal(self):
        if self._sort_col is None or not (0 <= self._sort_col < len(self._columns)):
            return
        col = self._columns[self._sort_col]
        if col == _CTRL_COL:
            return
        reverse = self._sort_order == Qt.SortOrder.DescendingOrder

        def key(node):
            v = node.data.get(col)
            if col == "Сумма":
                num = _to_num(v)
                return (num is None, num if num is not None else 0.0)
            if col == "Дата":
                ts = _to_ts(v)
                return (ts is None, ts if ts is not None else pd.Timestamp.min)
            return (False, str(v or "").lower())

        self._root.children.sort(key=key, reverse=reverse)
        for op in self._root.children:
            op.children.sort(key=key, reverse=reverse)


# =========================================================================== #
#  Делегаты                                                                   #
# =========================================================================== #

class _CellDelegate(QStyledItemDelegate):
    """Базовый делегат: рисует иконку карандаша (Material Icons) в ячейках,
    отредактированных вручную."""

    _CHAR = ""
    _ICON_FONT: QFont | None = None

    @classmethod
    def _icon_font(cls) -> QFont:
        if cls._ICON_FONT is None:
            f = QFont("Material Icons")
            f.setPixelSize(14)
            cls._ICON_FONT = f
        return cls._ICON_FONT

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if not index.data(MANUAL_ROLE):
            return
        painter.save()
        painter.setFont(self._icon_font())
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(QColor(255, 255, 255, 180))
        else:
            painter.setPen(QColor("#9CA3AF"))
        painter.drawText(
            option.rect.adjusted(0, 0, -4, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            self._CHAR,
        )
        painter.restore()


class _CategoryDelegate(_CellDelegate):
    """Делегат колонки «Категория»: рисует цветной овальный badge;
    при редактировании создаёт QComboBox «на лету»."""

    # Цвета фона/hover/выделения совпадают с _TREE_STYLE
    _BG       = QColor("#F9FAFB")
    _BG_HOVER = QColor("#DDE4EE")
    _BG_SEL   = QColor("#C9D8E2")
    _BORDER   = QColor("#D8DDE6")
    _TXT_SEL  = QColor("#07414F")

    def __init__(self, items: list[str], parent=None):
        super().__init__(parent)
        self._items = items

    # ---- отрисовка -------------------------------------------------------- #

    def paint(self, painter, option, index):
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")

        if text == _MULTI_OP_LABEL:
            self._paint_hatched(painter, option)
            return

        color = CATEGORY_COLORS.get(text) if text else None
        if color is None:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered  = bool(option.state & QStyle.StateFlag.State_MouseOver)

        # 1. Фон ячейки
        if selected:
            painter.fillRect(rect, self._BG_SEL)
        elif hovered:
            painter.fillRect(rect, self._BG_HOVER)
        else:
            painter.fillRect(rect, self._BG)

        # 2. Нижняя граница строки
        painter.setPen(QPen(self._BORDER, 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        # 3. Цвета овала из HSL базового цвета
        h, s, l, _ = color.getHslF()
        if h < 0:
            h, s = 0.0, 0.0
        pill_bg = QColor.fromHslF(h, min(s * 0.65, 1.0), 0.91)
        pill_bd = QColor.fromHslF(h, min(s * 1.00, 1.0), 0.52)
        pill_tx = QColor.fromHslF(h, min(s * 1.20, 1.0), 0.18)

        # 4. Геометрия овала
        v = max(3, (rect.height() - 20) // 2)
        pill = rect.adjusted(6, v, -6, -v)
        radius = pill.height() // 2

        painter.setPen(QPen(pill_bd, 1))
        painter.setBrush(pill_bg)
        painter.drawRoundedRect(pill, radius, radius)

        # 5. Текст
        painter.setPen(self._TXT_SEL if selected else pill_tx)
        painter.setFont(option.font)
        painter.drawText(
            pill.adjusted(8, 0, -4, 0),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            text,
        )

        # 6. Карандаш (ручное редактирование)
        if index.data(MANUAL_ROLE):
            painter.setFont(self._icon_font())
            painter.setPen(
                QColor(255, 255, 255, 180) if selected else QColor("#9CA3AF")
            )
            painter.drawText(
                rect.adjusted(0, 0, -4, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                self._CHAR,
            )

        painter.restore()

    def _paint_hatched(self, painter, option):
        """Овал с диагональной штриховкой для строки-мультиоперации."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered  = bool(option.state & QStyle.StateFlag.State_MouseOver)

        if selected:
            painter.fillRect(rect, self._BG_SEL)
        elif hovered:
            painter.fillRect(rect, self._BG_HOVER)
        else:
            painter.fillRect(rect, self._BG)

        painter.setPen(QPen(self._BORDER, 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        v = max(3, (rect.height() - 20) // 2)
        pill = rect.adjusted(6, v, -6, -v)
        radius = pill.height() // 2

        # Светлый фон овала
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(234, 234, 238))
        painter.drawRoundedRect(pill, radius, radius)

        # Диагональная штриховка
        painter.setBrush(QBrush(QColor(185, 185, 193), Qt.BrushStyle.BDiagPattern))
        painter.drawRoundedRect(pill, radius, radius)

        # Граница
        painter.setPen(QPen(QColor(165, 165, 173), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(pill, radius, radius)

        painter.restore()

    # ---- редактирование -------------------------------------------------- #

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(self._items)
        combo.activated.connect(lambda: self.commitData.emit(combo))
        return combo

    def setEditorData(self, editor, index):
        current = index.data(Qt.ItemDataRole.EditRole)
        if isinstance(editor, QComboBox):
            pos = -1
            if current:
                for i in range(len(self._items)):
                    if editor.itemText(i) == str(current):
                        pos = i
                        break
            editor.setCurrentIndex(pos if pos >= 0 else 0)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _CategoryPillButton(QWidget):
    """Кнопка-пилюля для выбора категории.

    Внешне: нейтральная кнопка «Все категории ▾» → при выборе категории
    превращается в цветной овал с её цветом из CATEGORY_COLORS.

    Реализует подмножество API QComboBox, чтобы существующий код работал
    без изменений: currentText(), blockSignals(), clear(), addItem(),
    addItems(), findText(), setCurrentIndex().
    """

    currentTextChanged = pyqtSignal()

    _NEUTRAL = "Все категории"

    def __init__(self, neutral_label: str = "Все категории", parent=None):
        super().__init__(parent)
        self._NEUTRAL = neutral_label
        self._items: list[str] = []
        self._current_idx: int = 0

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._btn = QPushButton()
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn.clicked.connect(self._open_menu)
        lay.addWidget(self._btn)

        self._update_display()

    # ---- QComboBox-compatible API ---------------------------------------- #

    def currentText(self) -> str:
        if 0 <= self._current_idx < len(self._items):
            return self._items[self._current_idx]
        return ""

    def clear(self):
        self._items.clear()
        self._current_idx = 0
        self._update_display()

    def addItem(self, text: str):
        self._items.append(text)

    def addItems(self, texts):
        self._items.extend(list(texts))

    def findText(self, text: str) -> int:
        try:
            return self._items.index(text)
        except ValueError:
            return -1

    def setCurrentIndex(self, idx: int):
        if self._items:
            idx = max(0, min(idx, len(self._items) - 1))
        else:
            idx = 0
        self._current_idx = idx
        self._update_display()
        self.currentTextChanged.emit()

    # ---- Display --------------------------------------------------------- #

    def _chip_style(self, r: int, g: int, b: int) -> str:
        return (
            f"QPushButton {{"
            f"  background: rgba({r},{g},{b},38);"
            f"  border: 1px solid rgba({r},{g},{b},160);"
            f"  border-radius: 11px;"
            f"  color: rgb({max(0,r-40)},{max(0,g-40)},{max(0,b-40)});"
            f"  padding: 4px 14px; font-size: 12px; font-weight: 500;"
            f"  min-height: 22px;"
            f"}}"
            f"QPushButton:hover {{ background: rgba({r},{g},{b},70); }}"
        )

    def _neutral_style(self) -> str:
        return (
            "QPushButton {"
            "  background: #FFFFFF; border: 1px solid #D5DCE4; border-radius: 6px;"
            "  color: #1F2937; padding: 6px 10px; font-size: 13px; min-height: 22px;"
            "}"
            "QPushButton:hover { background: #F9FAFB; }"
        )

    def _update_display(self):
        text = self.currentText()
        if not text or text == self._NEUTRAL:
            self._btn.setText(f"{self._NEUTRAL}  ▾")
            self._btn.setStyleSheet(self._neutral_style())
            return

        color = CATEGORY_COLORS.get(text)
        if color:
            self._btn.setStyleSheet(
                self._chip_style(color.red(), color.green(), color.blue())
            )
        else:
            self._btn.setStyleSheet(
                "QPushButton {"
                "  background: #EFF1F5; border: 1px solid #C4C9D4; border-radius: 11px;"
                "  color: #374151; padding: 4px 14px; font-size: 12px; font-weight: 500;"
                "  min-height: 22px;"
                "}"
                "QPushButton:hover { background: #E2E5EC; }"
            )
        self._btn.setText(text)

    def _open_menu(self):
        if not self._items:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu {"
            "  background: #FFFFFF; border: 1px solid #D5DCE4;"
            "  border-radius: 8px; padding: 4px;"
            "}"
            "QMenu::item {"
            "  padding: 6px 16px; font-size: 13px; color: #1F2937; border-radius: 4px;"
            "}"
            "QMenu::item:selected { background: #F3F4F6; }"
        )
        for i, item in enumerate(self._items):
            act = menu.addAction(item)
            act.setCheckable(True)
            act.setChecked(i == self._current_idx)
            act.triggered.connect(lambda checked, idx=i: self.setCurrentIndex(idx))
        menu.exec(
            self._btn.mapToGlobal(self._btn.rect().bottomLeft() + QPoint(0, 2))
        )


class _PlotDelegate(_CellDelegate):
    """Делегат колонки «Участок»: выпадающий список номеров участков из БД.
    Для строк-мультиопераций рисует заштрихованный овал вместо значения."""

    _BG       = QColor("#F9FAFB")
    _BG_HOVER = QColor("#DDE4EE")
    _BG_SEL   = QColor("#C9D8E2")
    _BORDER   = QColor("#D8DDE6")

    def __init__(self, items: list[str], parent=None):
        super().__init__(parent)
        self._items = items

    def paint(self, painter, option, index):
        if index.data(Qt.ItemDataRole.DisplayRole) == _MULTI_OP_LABEL:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            rect = option.rect
            if option.state & QStyle.StateFlag.State_Selected:
                painter.fillRect(rect, self._BG_SEL)
            elif option.state & QStyle.StateFlag.State_MouseOver:
                painter.fillRect(rect, self._BG_HOVER)
            else:
                painter.fillRect(rect, self._BG)

            painter.setPen(QPen(self._BORDER, 1))
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())

            v = max(3, (rect.height() - 20) // 2)
            pill = rect.adjusted(6, v, -6, -v)
            radius = pill.height() // 2

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(234, 234, 238))
            painter.drawRoundedRect(pill, radius, radius)

            painter.setBrush(QBrush(QColor(185, 185, 193), Qt.BrushStyle.BDiagPattern))
            painter.drawRoundedRect(pill, radius, radius)

            painter.setPen(QPen(QColor(165, 165, 173), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(pill, radius, radius)

            painter.restore()
            return

        super().paint(painter, option, index)

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItem("")           # пустой пункт — участок не указан
        combo.addItems(self._items)
        combo.activated.connect(lambda: self.commitData.emit(combo))
        return combo

    def setEditorData(self, editor, index):
        current = index.data(Qt.ItemDataRole.EditRole)
        if isinstance(editor, QComboBox):
            text = str(current).strip() if current else ""
            if text:
                pos = editor.findText(text)
                if pos < 0:
                    # Значения нет в базе — вставляем как первый пункт
                    editor.insertItem(1, text)
                    pos = 1
                editor.setCurrentIndex(pos)
            else:
                editor.setCurrentIndex(0)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


# =========================================================================== #
#  Делегат служебного столбца (стрелка / ＋ / корзина)                          #
# =========================================================================== #

class _CtrlDelegate(QStyledItemDelegate):
    """Рисует кнопки управления в первом (служебном) столбце для операций:
    стрелку сворачивания (если есть ветки) и зелёную «＋» (добавить ветку).
    Корзина веток живёт в колонке «Контрагент» (см. _BranchColumnDelegate).

    Клики ловятся в editorEvent и транслируются наружу сигналами. Геометрия
    кнопок одинакова для всех строк (indentation у дерева = 0)."""

    addBranchRequested = pyqtSignal(QModelIndex)
    toggleRequested = pyqtSignal(QModelIndex)

    _ARROW_COLOR = QColor("#5B6675")
    _PLUS_BG = QColor("#D6F0DC")
    _PLUS_FG = QColor("#15803D")
    _BG = QColor("#F9FAFB")
    _BG_HOVER = QColor("#DDE4EE")
    _BG_SEL = QColor("#C9D8E2")
    _BORDER = QColor("#D8DDE6")
    _PLUS_W, _PLUS_H = 24, 20

    def __init__(self, view):
        super().__init__(view)
        self._view = view

    @staticmethod
    def _icon_font(px: int = 15) -> QFont:
        f = QFont("Material Icons")
        f.setPixelSize(px)
        return f

    # -- геометрия кнопок (от левого края ячейки) --------------------------- #
    def _arrow_rect(self, rect) -> QRect:
        return QRect(rect.left() + 2, rect.top(), 22, rect.height())

    def _plus_rect(self, rect) -> QRect:
        y = rect.top() + (rect.height() - self._PLUS_H) // 2
        return QRect(rect.left() + 26, y, self._PLUS_W, self._PLUS_H)

    # -- отрисовка ---------------------------------------------------------- #
    def paint(self, painter, option, index):
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, self._BG_SEL)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, self._BG_HOVER)
        else:
            painter.fillRect(option.rect, self._BG)
        painter.setPen(self._BORDER)
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())

        node = index.internalPointer()
        if node is None:
            painter.restore()
            return
        rect = option.rect
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if node.kind == "op":
            model = self._view.model()
            if model is not None and model.hasChildren(index):
                self._paint_arrow(painter, self._arrow_rect(rect), self._view.isExpanded(index))
            self._paint_btn(painter, self._plus_rect(rect), "", self._PLUS_BG, self._PLUS_FG)
        painter.restore()

    def _paint_arrow(self, painter, rect, expanded):
        painter.save()
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

    def _paint_btn(self, painter, rect, glyph, bg, fg):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(fg)
        painter.setFont(self._icon_font(15))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)
        painter.restore()

    # -- клики -------------------------------------------------------------- #
    def editorEvent(self, event, model, option, index):
        if (event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton):
            node = index.internalPointer()
            pos = event.position().toPoint()
            rect = option.rect
            if node is not None:
                if node.kind == "op":
                    if model.hasChildren(index) and self._arrow_rect(rect).contains(pos):
                        self.toggleRequested.emit(index)
                        return True
                    if self._plus_rect(rect).contains(pos):
                        self.addBranchRequested.emit(index)
                        return True
        return super().editorEvent(event, model, option, index)


class _BranchColumnDelegate(_CellDelegate):
    """Делегат колонки «Контрагент». Для строк-веток (split) рисует слева
    соединительные линии дерева (├─ / └─) и кнопку-корзину, а сам текст
    контрагента сдвигает вправо — так визуально видно вложенность ветки в
    операцию. Для строк-операций ведёт себя как обычный _CellDelegate
    (текст + карандаш ручной правки)."""

    deleteBranchRequested = pyqtSignal(QModelIndex)

    _LINE_COLOR = QColor("#9AA4B5")
    _TRASH_BG = QColor("#FEE2E2")
    _TRASH_FG = QColor("#B91C1C")
    _BG = QColor("#F9FAFB")
    _BG_HOVER = QColor("#DDE4EE")
    _BG_SEL = QColor("#C9D8E2")
    _BORDER = QColor("#D8DDE6")
    _TXT = QColor("#1F2937")
    _TXT_SEL = QColor("#07414F")
    _TRUNK_X = 16        # X вертикального «ствола» от левого края ячейки
    _ELBOW_END = 30      # докуда идёт горизонтальное «ответвление»
    _TRASH_X, _TRASH_W, _TRASH_H = 34, 24, 20
    _TEXT_PAD = 64       # отступ текста контрагента вправо

    def _trash_rect(self, rect) -> QRect:
        y = rect.top() + (rect.height() - self._TRASH_H) // 2
        return QRect(rect.left() + self._TRASH_X, y, self._TRASH_W, self._TRASH_H)

    def paint(self, painter, option, index):
        node = index.internalPointer()
        if node is None or node.kind != "split":
            super().paint(painter, option, index)
            return

        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # фон + нижняя граница строки (как у соседних ячеек)
        if selected:
            painter.fillRect(rect, self._BG_SEL)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, self._BG_HOVER)
        else:
            painter.fillRect(rect, self._BG)
        painter.setPen(self._BORDER)
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        # соединительные линии дерева: ствол + ответвление (└─ для последней ветки)
        model = index.model()
        is_last = index.row() == model.rowCount(index.parent()) - 1
        midy = rect.top() + rect.height() // 2
        tx = rect.left() + self._TRUNK_X
        painter.setPen(QPen(self._LINE_COLOR, 1.4))
        painter.drawLine(tx, rect.top(), tx, midy if is_last else rect.bottom())
        painter.drawLine(tx, midy, rect.left() + self._ELBOW_END, midy)

        # кнопка-корзина
        tr = self._trash_rect(rect)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._TRASH_BG)
        painter.drawRoundedRect(tr, 5, 5)
        painter.setPen(self._TRASH_FG)
        f = QFont("Material Icons")
        f.setPixelSize(15)
        painter.setFont(f)
        painter.drawText(tr, Qt.AlignmentFlag.AlignCenter, "")

        # текст контрагента — со сдвигом вправо
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if text:
            painter.setFont(option.font)
            painter.setPen(self._TXT_SEL if selected else self._TXT)
            txt_rect = rect.adjusted(self._TEXT_PAD, 0, -6, 0)
            elided = painter.fontMetrics().elidedText(
                str(text), Qt.TextElideMode.ElideRight, txt_rect.width())
            painter.drawText(
                txt_rect,
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                elided,
            )
        painter.restore()

    def editorEvent(self, event, model, option, index):
        node = index.internalPointer()
        if (node is not None and node.kind == "split"
                and event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton):
            if self._trash_rect(option.rect).contains(event.position().toPoint()):
                self.deleteBranchRequested.emit(index)
                return True
        return super().editorEvent(event, model, option, index)


# =========================================================================== #
#  Вспомогательные UI-виджеты                                                 #
# =========================================================================== #

class _ElidedLabel(QLabel):
    """QLabel с усечением '...' при нехватке места.
    Ключевое отличие от QLabel: minimumWidth = 0, поэтому соседние виджеты
    в QHBoxLayout не «уезжают» за границу при длинном тексте."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._full = text
        self.setMinimumWidth(0)

    def setText(self, text: str):
        self._full = text
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setPen(QColor("#1F2937"))
        p.setFont(self.font())
        elided = p.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, self.width()
        )
        p.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            elided,
        )


# =========================================================================== #
#  Всплывающая палитра цветов                                                 #
# =========================================================================== #

class _ColorPickerPopup(QFrame):
    """Всплывающая панель выбора цвета из предустановленной палитры."""

    colorSelected = pyqtSignal(QColor)

    # Палитра из 16 цветов (2 строки × 8): светлые + тёмные тона
    PALETTE: list[QColor] = [
        QColor(217, 217, 217), QColor(244, 204, 204), QColor(252, 229, 205), QColor(255, 242, 204),
        QColor(217, 234, 211), QColor(208, 224, 227), QColor(207, 226, 255), QColor(217, 210, 233),
        QColor( 68,  68,  68), QColor(153,   0,   0), QColor(180,  95,   6), QColor(120,  63,   4),
        QColor( 39,  78,  19), QColor( 12,  52,  61), QColor( 28,  69, 135), QColor( 53,  28, 117),
    ]

    def __init__(self):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet("""
            QFrame {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 8px;
            }
        """)
        grid = QGridLayout(self)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(5)
        for i, color in enumerate(self.PALETTE):
            btn = QPushButton()
            btn.setFixedSize(22, 22)
            r, g, b = color.red(), color.green(), color.blue()
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgb({r},{g},{b});
                    border: 1.5px solid rgba(0,0,0,0.12);
                    border-radius: 11px;
                }}
                QPushButton:hover {{ border: 2.5px solid #4F46E5; }}
            """)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(lambda checked, c=color: self._pick(c))
            grid.addWidget(btn, i // 8, i % 8)

    def _pick(self, color: QColor):
        self.colorSelected.emit(color)
        self.close()

    def show_near(self, widget: QWidget):
        pos = widget.mapToGlobal(QPoint(0, widget.height() + 2))
        self.adjustSize()
        self.move(pos)
        self.show()
        self.raise_()


# =========================================================================== #
#  Панель редактора категорий                                                 #
# =========================================================================== #

class CategoryEditorPanel(QDialog):
    """Диалог редактирования списка категорий."""

    categoriesChanged = pyqtSignal(list)
    categoryRenamed   = pyqtSignal(str, str)   # (old_name, new_name)

    _PANEL_STYLE = """
        QDialog {
            background: #FFFFFF;
        }
        QPushButton#addCatBtn {
            background: #4F46E5; color: white; border: none;
            border-radius: 6px; padding: 7px 12px; font-size: 12px; font-weight: 600;
        }
        QPushButton#addCatBtn:hover { background: #6366F1; }
        QLineEdit#newCatInput {
            background: #F8F9FA; border: 1px solid #D1D5DB;
            border-radius: 5px; color: #374151; padding: 6px 8px; font-size: 12px;
        }
        QLineEdit#newCatInput:focus { border: 1px solid #6366F1; }
        QScrollArea { background: transparent; border: none; }
        QWidget#scrollContents { background: transparent; }
    """

    def __init__(self, categories: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Редактор категорий")
        self.setModal(False)
        self.setMinimumSize(360, 480)
        self._categories = list(categories)
        self._active_color_cat: str | None = None
        self._color_popup = _ColorPickerPopup()
        self._color_popup.colorSelected.connect(self._on_color_selected)
        self._setup_ui()
        self.setStyleSheet(self._PANEL_STYLE)

    def set_categories(self, cats: list[str]):
        self._categories = list(cats)
        self._rebuild_list()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Scrollable category list
        self._scroll_contents = QWidget(objectName="scrollContents")
        self._list_layout = QVBoxLayout(self._scroll_contents)
        self._list_layout.setContentsMargins(0, 2, 0, 2)
        self._list_layout.setSpacing(3)

        scroll = QScrollArea()
        scroll.setWidget(self._scroll_contents)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(scroll, stretch=1)

        # Add new category row
        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self._new_input = QLineEdit(objectName="newCatInput")
        self._new_input.setPlaceholderText("Новая категория...")
        self._new_input.returnPressed.connect(self._on_add)
        add_btn = QPushButton("Добавить", objectName="addCatBtn")
        add_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        add_btn.clicked.connect(self._on_add)
        add_row.addWidget(self._new_input, stretch=1)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        self._rebuild_list()

    def _rebuild_list(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for cat in self._categories:
            self._list_layout.addWidget(self._make_cat_row(cat))
        self._list_layout.addStretch()

    def _make_cat_row(self, cat: str) -> QWidget:
        row = QWidget()
        row.setStyleSheet(
            "QWidget { background: #F8F9FA; border-radius: 5px; }"
            "QWidget:hover { background: #EEF2FF; }"
        )
        hl = QHBoxLayout(row)
        hl.setContentsMargins(8, 4, 5, 4)
        hl.setSpacing(6)

        color = CATEGORY_COLORS.get(cat, _DEFAULT_ROW_COLOR)
        r, g, b = color.red(), color.green(), color.blue()

        # Кнопка-кружок: отображает текущий цвет, по клику открывает палитру
        color_btn = QPushButton()
        color_btn.setFixedSize(22, 22)
        color_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgb({r},{g},{b});
                border: 1.5px solid rgba(0,0,0,0.15);
                border-radius: 11px;
            }}
            QPushButton:hover {{ border: 2px solid #4F46E5; }}
        """)
        color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        color_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        color_btn.setToolTip("Изменить цвет")
        color_btn.clicked.connect(lambda checked, c=cat, b=color_btn: self._open_color_picker(c, b))

        is_protected = cat in PROTECTED_CATEGORIES

        lbl = QLineEdit(cat)
        lbl.setMinimumWidth(0)
        lbl.setReadOnly(is_protected)
        lbl.setStyleSheet("""
            QLineEdit {
                background: transparent; border: none;
                font-size: 12px; color: #1F2937; padding: 1px 2px;
            }
            QLineEdit:focus {
                background: #FFFFFF; border: 1px solid #6366F1;
                border-radius: 3px; padding: 1px 4px;
            }
            QLineEdit:read-only { color: #6B7280; }
        """)
        if not is_protected:
            lbl.editingFinished.connect(
                lambda e=lbl, old=cat: self._on_rename(old, e.text().strip(), e)
            )

        if is_protected:
            action_btn = QLabel("🔒")
            action_btn.setFixedSize(26, 26)
            action_btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
            action_btn.setStyleSheet("background: transparent; font-size: 13px;")
            action_btn.setToolTip("Обязательная категория — удаление недоступно")
        else:
            action_btn = QPushButton("✕")
            action_btn.setStyleSheet("""
                QPushButton {
                    background: transparent; border: none;
                    color: #9CA3AF; font-size: 14px; font-weight: 600;
                    border-radius: 4px;
                }
                QPushButton:hover { background: #FEE2E2; color: #B91C1C; }
            """)
            action_btn.setFixedSize(26, 26)
            action_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            action_btn.setToolTip("Удалить категорию")
            action_btn.clicked.connect(lambda checked, c=cat: self._on_delete(c))

        hl.addWidget(color_btn)
        hl.addWidget(lbl, stretch=1)
        hl.addWidget(action_btn)
        return row

    def _on_rename(self, old_name: str, new_name: str, editor: "QLineEdit"):
        if old_name in PROTECTED_CATEGORIES:
            editor.setText(old_name)
            return
        if not new_name or new_name == old_name:
            editor.setText(old_name)
            return
        if new_name in self._categories:
            editor.setText(old_name)
            reply = QMessageBox.question(
                self,
                "Объединить категории?",
                f"Категория «{new_name}» уже существует.\n\n"
                f"Объединить «{old_name}» с «{new_name}»?\n"
                f"Все строки с категорией «{old_name}» получат категорию «{new_name}»,\n"
                f"а «{old_name}» будет удалена из списка.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._do_merge(old_name, new_name)
            return
        rename_user_category(old_name, new_name)
        self._categories[self._categories.index(old_name)] = new_name
        self._rebuild_list()
        self.categoryRenamed.emit(old_name, new_name)

    def _do_merge(self, old_name: str, new_name: str):
        """Удаляет old_name из списка; все строки df с этой категорией
        получат new_name через сигнал categoryRenamed."""
        delete_user_category(old_name)
        if old_name in self._categories:
            self._categories.remove(old_name)
        self._rebuild_list()
        self.categoryRenamed.emit(old_name, new_name)

    def _open_color_picker(self, cat: str, btn: QPushButton):
        self._active_color_cat = cat
        self._color_popup.show_near(btn)

    def _on_color_selected(self, color: QColor):
        if not self._active_color_cat:
            return
        import ui.categorization as _cat_mod
        _cat_mod.CATEGORY_COLORS[self._active_color_cat] = color
        save_user_category_color(self._active_color_cat, color)
        self._active_color_cat = None
        self._rebuild_list()
        self.categoriesChanged.emit(list(self._categories))

    def _on_add(self):
        name = self._new_input.text().strip()
        if not name or name in self._categories:
            return
        self._categories.append(name)
        self._new_input.clear()
        self._rebuild_list()
        self._persist_and_emit()

    def _on_delete(self, cat: str):
        if cat in self._categories:
            self._categories.remove(cat)
            self._rebuild_list()
            self._persist_and_emit()

    def _persist_and_emit(self):
        save_user_categories(self._categories)
        self.categoriesChanged.emit(list(self._categories))


# =========================================================================== #
#  Диалоги (без изменений)                                                    #
# =========================================================================== #

class LoadSettingsDialog(QDialog):
    """Диалог настроек перед загрузкой файла выписки."""

    _STYLE = """
        QDialog { background: #FFFFFF; color: #374151; }
        QLabel  { background: transparent; }
        QLabel#sectionLabel {
            color: #9CA3AF; font-size: 11px; font-weight: 600;
            letter-spacing: 0.5px; text-transform: uppercase;
        }
        QPushButton#fmtActive {
            background: #4F46E5; color: #ffffff; border: none;
            border-radius: 6px; padding: 8px 20px; font-size: 13px; font-weight: 600;
        }
        QPushButton#fmtInactive {
            background: #E5E7EB; color: #6B7280; border: 1px solid #D1D5DB;
            border-radius: 6px; padding: 8px 20px; font-size: 13px;
        }
        QPushButton#fmtInactive:hover { background: #E5E7EB; color: #374151; }
        QPushButton#btnPrimary {
            background: #4F46E5; color: white; border: none;
            border-radius: 6px; padding: 8px 20px; font-size: 13px; font-weight: 600;
        }
        QPushButton#btnPrimary:hover  { background: #6366F1; }
        QPushButton#btnPrimary:pressed { background: #4338CA; }
        QPushButton#btnSecondary {
            background: #E5E7EB; color: #6B7280; border: 1px solid #D1D5DB;
            border-radius: 6px; padding: 7px 16px; font-size: 13px;
        }
        QPushButton#btnSecondary:hover { background: #E5E7EB; color: #374151; }
        QCheckBox {
            color: #374151; background: transparent; font-size: 13px; spacing: 8px;
        }
        QCheckBox::indicator {
            width: 16px; height: 16px; border-radius: 4px;
            border: 1px solid #D1D5DB; background: #F8F9FA;
        }
        QCheckBox::indicator:checked {
            background: #4F46E5; border-color: #4F46E5;
            image: url(none);
        }
        QCheckBox::indicator:hover { border-color: #818CF8; }
        QFrame#divider { background: #E5E7EB; max-height: 1px; }
    """

    def __init__(self, parent=None, has_existing_data: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Загрузка детализации")
        self.setModal(True)
        self.setFixedWidth(400)
        self._fmt = "sber"
        self._has_existing = has_existing_data
        self._setup_ui()
        self.setStyleSheet(self._STYLE)

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 20)
        lay.setSpacing(14)

        title = QLabel("Загрузка детализации")
        title.setStyleSheet("font-size:15px; font-weight:700; color:#111827;")
        lay.addWidget(title)

        div0 = QFrame(objectName="divider")
        div0.setFixedHeight(1)
        lay.addWidget(div0)

        lay.addWidget(QLabel("ФОРМАТ ФАЙЛА", objectName="sectionLabel"))

        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(8)
        self._btn_sber = QPushButton("СберБизнес (операции)", objectName="fmtActive")
        self._btn_snt  = QPushButton("Мой Садовод",            objectName="fmtInactive")
        self._btn_sber.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_snt .setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_sber.clicked.connect(lambda: self._set_fmt("sber"))
        self._btn_snt .clicked.connect(lambda: self._set_fmt("snt"))
        fmt_row.addWidget(self._btn_sber)
        fmt_row.addWidget(self._btn_snt)
        fmt_row.addStretch()
        lay.addLayout(fmt_row)

        self._fmt_hint = QLabel()
        self._fmt_hint.setWordWrap(True)
        self._fmt_hint.setStyleSheet("color:#9CA3AF; font-size:11px; background:transparent;")
        lay.addWidget(self._fmt_hint)
        self._update_hint()

        div1 = QFrame(objectName="divider")
        div1.setFixedHeight(1)
        lay.addWidget(div1)

        lay.addWidget(QLabel("АВТОМАТИЧЕСКОЕ РАСПРЕДЕЛЕНИЕ", objectName="sectionLabel"))

        self.chk_cat  = QCheckBox("Категория")
        self.chk_plot = QCheckBox("Участок")
        self.chk_cat .setChecked(True)
        self.chk_plot.setChecked(True)
        lay.addWidget(self.chk_cat)
        lay.addWidget(self.chk_plot)

        div2 = QFrame(objectName="divider")
        div2.setFixedHeight(1)
        lay.addWidget(div2)

        if self._has_existing:
            lay.addWidget(QLabel("РЕЖИМ ЗАГРУЗКИ", objectName="sectionLabel"))
            self.chk_merge = QCheckBox("Добавить к существующим данным")
            self.chk_merge.setChecked(True)
            self.chk_merge.setToolTip(
                "Новые операции будут добавлены к уже загруженным.\n"
                "Дубли будут помечены тегом «Дубль»."
            )
            lay.addWidget(self.chk_merge)

            div3 = QFrame(objectName="divider")
            div3.setFixedHeight(1)
            lay.addWidget(div3)
        else:
            self.chk_merge = None

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_cancel = QPushButton("Отмена",       objectName="btnSecondary")
        btn_ok     = QPushButton("Выбрать файл", objectName="btnPrimary")
        btn_cancel.clicked.connect(self.reject)
        btn_ok    .clicked.connect(self.accept)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        lay.addLayout(btn_row)

    def _set_fmt(self, fmt: str):
        self._fmt = fmt
        self._btn_sber.setObjectName("fmtActive"   if fmt == "sber" else "fmtInactive")
        self._btn_snt .setObjectName("fmtInactive" if fmt == "sber" else "fmtActive")
        for btn in (self._btn_sber, self._btn_snt):
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self._update_hint()

    def _update_hint(self):
        if self._fmt == "sber":
            self._fmt_hint.setText(
                "Стандартная выгрузка операций из СберБизнес (.xlsx)")
        else:
            self._fmt_hint.setText(
                "Файл в формате программы Мой Садовод — столбцы уже приведены к нужному виду")

    @property
    def fmt(self) -> str:
        return self._fmt

    @property
    def auto_cat(self) -> bool:
        return self.chk_cat.isChecked()

    @property
    def auto_plot(self) -> bool:
        return self.chk_plot.isChecked()

    @property
    def merge_mode(self) -> bool:
        return self.chk_merge.isChecked() if self.chk_merge else False


class AddRowDialog(QDialog):
    """Диалог ручного добавления операции в таблицу Детализации."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Добавить операцию")
        self.setMinimumWidth(500)
        self.setModal(True)
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 20)
        lay.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.date_edit = QDateEdit(QDate.currentDate(), calendarPopup=True)
        self.date_edit.setDisplayFormat("dd.MM.yyyy")
        self.date_edit.setObjectName("datePicker")
        form.addRow("Дата:", self.date_edit)

        self.inp_summa = QLineEdit()
        self.inp_summa.setPlaceholderText("например: 1500 (поступление) или -500 (списание)")
        form.addRow("Сумма, ₽:", self.inp_summa)

        self.inp_cont = QLineEdit()
        self.inp_cont.setPlaceholderText("Организация или ФИО")
        form.addRow("Контрагент:", self.inp_cont)

        self.inp_nazn = QLineEdit()
        self.inp_nazn.setPlaceholderText("Назначение платежа")
        form.addRow("Назначение:", self.inp_nazn)

        cat_row = QHBoxLayout()
        cat_row.setSpacing(6)
        self.combo_cat = _CategoryPillButton(neutral_label="")
        for cat in ALL_CATEGORIES:
            self.combo_cat.addItem(cat)
        if ALL_CATEGORIES:
            self.combo_cat.setCurrentIndex(0)
        cat_row.addWidget(self.combo_cat, stretch=1)
        btn_auto = QPushButton("Определить")
        btn_auto.setObjectName("btnSecondary")
        btn_auto.setFixedWidth(110)
        btn_auto.clicked.connect(self._auto_detect)
        cat_row.addWidget(btn_auto)
        cat_widget = QWidget()
        cat_widget.setLayout(cat_row)
        form.addRow("Категория:", cat_widget)

        self.combo_plot = QComboBox()
        self.combo_plot.setEditable(True)
        self.combo_plot.addItem("")
        self._fill_plot_combo()
        form.addRow("Участок:", self.combo_plot)

        lay.addLayout(form)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#E5E7EB;background:#E5E7EB;max-height:1px;")
        lay.addWidget(sep)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Добавить")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _fill_plot_combo(self):
        try:
            if os.path.exists(_PLOTS_FILE):
                with open(_PLOTS_FILE, "r", encoding="utf-8") as f:
                    plots = json.load(f)
                nums = sorted(
                    set(str(p.get("num", "")) for p in plots if p.get("num")),
                    key=lambda s: (0, int(s), s) if s.isdigit() else (1, 0, s),
                )
                for num in nums:
                    self.combo_plot.addItem(num)
        except Exception:
            pass

    def _auto_detect(self):
        row = {"Назначение": self.inp_nazn.text(), "Контрагент": self.inp_cont.text()}
        cat = categorize_row(row)
        idx = self.combo_cat.findText(cat)
        if idx >= 0:
            self.combo_cat.setCurrentIndex(idx)
        plot = get_plot(row)
        if plot:
            self.combo_plot.setEditText(plot)

    def _on_accept(self):
        raw = (self.inp_summa.text().strip()
               .replace(",", ".").replace("−", "-").replace("−", "-").replace(" ", ""))
        if not raw:
            QMessageBox.warning(self, "Ошибка", "Укажите сумму операции")
            return
        try:
            float(raw)
        except ValueError:
            QMessageBox.warning(self, "Ошибка", "Некорректный формат суммы")
            return
        self.accept()

    def get_result(self) -> dict:
        raw = (self.inp_summa.text().strip()
               .replace(",", ".").replace("−", "-").replace("−", "-").replace(" ", ""))
        d = self.date_edit.date()
        return {
            "Дата":        pd.Timestamp(d.year(), d.month(), d.day()),
            "Контрагент":  self.inp_cont.text().strip(),
            "Сумма":       float(raw),
            "Назначение":  self.inp_nazn.text().strip(),
            "Категория":   self.combo_cat.currentText(),
            "Участок":     self.combo_plot.currentText().strip(),
        }

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog { background: #FFFFFF; color: #374151; }
            QLabel  { background: transparent; color: #374151; font-size: 13px; }
            QLineEdit, QComboBox, QDateEdit {
                background: #F8F9FA; border: 1px solid #D1D5DB;
                border-radius: 5px; color: #374151; padding: 7px 10px; font-size: 13px;
            }
            QLineEdit:focus, QComboBox:focus, QDateEdit:focus { border: 1px solid #6366F1; }
            QPushButton#btnSecondary {
                background: #E5E7EB; color: #6B7280; border: 1px solid #D1D5DB;
                border-radius: 6px; padding: 7px 14px; font-size: 13px;
            }
            QPushButton#btnSecondary:hover { background: #D1D5DB; color: #374151; }
            QDialogButtonBox QPushButton {
                background: #4F46E5; color: white; border: none;
                border-radius: 6px; padding: 8px 20px; font-size: 13px; font-weight: 600;
            }
            QDialogButtonBox QPushButton:hover { background: #6366F1; }
            QDialogButtonBox QPushButton[text='Отмена'] {
                background: #E5E7EB; color: #6B7280; border: 1px solid #D1D5DB;
            }
        """)


# =========================================================================== #
#  Виджет вкладки «Детализация»                                               #
# =========================================================================== #

class DetailWidget(QWidget):
    dataLoaded = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setAutoFillBackground(True)
        self.df_full = None
        self._manual_rows: set[int] = set()
        self._manual_cells: set[tuple[int, str]] = set()
        self._cat_col: int | None = None
        self._cont_col: int | None = None
        self._plot_col: int | None = None
        self._active_warning: str | None = None
        self._setup_ui()

    # ----------------------------------------------------------------- UI -- #
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        top_bar = QHBoxLayout()
        top_bar.addStretch()
        self.btn_load = QPushButton("Загрузить файл")
        self.btn_load.setObjectName("btnPrimary")
        self.btn_load.clicked.connect(self.load_file)
        top_bar.addWidget(self.btn_load)

        btn_add_row = QPushButton("＋  Добавить операцию")
        btn_add_row.setObjectName("btnSecondary")
        btn_add_row.clicked.connect(self._add_row)
        top_bar.addWidget(btn_add_row)

        btn_cat = QPushButton("Категории", objectName="btnSecondary")
        btn_cat.clicked.connect(self._show_cat_editor)
        top_bar.addWidget(btn_cat)

        btn_excel = QPushButton("Экспорт в Excel", objectName="btnSecondary")
        btn_excel.clicked.connect(self._export_excel)
        top_bar.addWidget(btn_excel)

        layout.addLayout(top_bar)

        filter_frame = QFrame()
        filter_frame.setObjectName("filterFrame")
        fl = QHBoxLayout(filter_frame)
        fl.setContentsMargins(16, 12, 16, 12)
        fl.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск по контрагенту или назначению...")
        self.search_input.setObjectName("searchInput")
        self.search_input.textChanged.connect(self.apply_filters)
        fl.addWidget(self.search_input, stretch=3)

        self.combo_type = QComboBox()
        self.combo_type.setObjectName("filterCombo")
        self.combo_type.addItems(["Все операции", "Поступления", "Списания"])
        self.combo_type.currentIndexChanged.connect(self.apply_filters)
        fl.addWidget(self.combo_type, stretch=1)

        self.combo_cat = _CategoryPillButton()
        self.combo_cat.addItem("Все категории")
        for cat in ALL_CATEGORIES:
            self.combo_cat.addItem(cat)
        self.combo_cat.currentTextChanged.connect(self.apply_filters)
        fl.addWidget(self.combo_cat, stretch=2)

        fl.addWidget(QLabel("с", objectName="filterLabel"))
        self.date_from = QDateEdit(calendarPopup=True, objectName="datePicker",
                                   displayFormat="dd.MM.yyyy")
        self.date_from.setDate(QDate(2021, 1, 1))
        self.date_from.dateChanged.connect(self.apply_filters)
        fl.addWidget(self.date_from)

        fl.addWidget(QLabel("по", objectName="filterLabel"))
        self.date_to = QDateEdit(calendarPopup=True, objectName="datePicker",
                                 displayFormat="dd.MM.yyyy")
        self.date_to.setDate(QDate.currentDate())
        self.date_to.dateChanged.connect(self.apply_filters)
        fl.addWidget(self.date_to)

        btn_reset = QPushButton("✕  Сбросить", objectName="btnSecondary")
        btn_reset.clicked.connect(self.reset_filters)
        fl.addWidget(btn_reset)

        layout.addWidget(filter_frame)

        self._warnings_bar = QWidget()
        self._warnings_bar.setVisible(False)
        self._warnings_layout = QHBoxLayout(self._warnings_bar)
        self._warnings_layout.setContentsMargins(0, 0, 0, 0)
        self._warnings_layout.setSpacing(6)
        layout.addWidget(self._warnings_bar)

        # --- дерево (Model-View) --------------------------------------- #
        self.model = OperationsTreeModel(self._manual_cells, self)
        self.model.cellEdited.connect(self._on_cell_edited)

        self.tree = QTreeView(objectName="mainTable")
        self.tree.setModel(self.model)
        self.tree.setUniformRowHeights(True)
        # Стрелка/＋/корзина живут в служебном столбце-делегате, поэтому штатную
        # «ёлочку» дерева отключаем, а отступ обнуляем — геометрия кнопок едина.
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.tree.setSortingEnabled(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.setMouseTracking(True)
        self.tree.header().setStretchLastSection(True)
        self.tree.setStyleSheet(self._TREE_STYLE)

        self._cell_delegate = _CellDelegate(self.tree)
        self._category_delegate = _CategoryDelegate(ALL_CATEGORIES, self.tree)
        self._plot_delegate = _PlotDelegate(load_plot_numbers(), self.tree)
        self._ctrl_delegate = _CtrlDelegate(self.tree)
        self._ctrl_delegate.addBranchRequested.connect(self._on_add_branch)
        self._ctrl_delegate.toggleRequested.connect(self._on_toggle)
        self._branch_delegate = _BranchColumnDelegate(self.tree)
        self._branch_delegate.deleteBranchRequested.connect(self._on_delete_branch)
        self.tree.setItemDelegate(self._cell_delegate)
        self.tree.setItemDelegateForColumn(0, self._ctrl_delegate)

        # Панель редактора категорий — скрыта по умолчанию, выезжает справа.
        self._cat_editor_panel = CategoryEditorPanel(ALL_CATEGORIES, self)
        self._cat_editor_panel.categoriesChanged.connect(self._on_categories_changed)
        self._cat_editor_panel.categoryRenamed.connect(self._on_category_renamed)

        layout.addWidget(self.tree)

        summary_layout = QHBoxLayout()
        self.lbl_records = QLabel("Записей: —", objectName="summaryRecords")
        summary_layout.addWidget(self.lbl_records)
        summary_layout.addStretch()
        layout.addLayout(summary_layout)

    # QTreeView нуждается в собственных правилах: глобальный QSS целит в
    # QTableWidget#mainTable и на дерево не распространяется.
    _TREE_STYLE = """
        QTreeView#mainTable {
            background: #F9FAFB; border: 1px solid #D8DDE6; border-radius: 8px;
            color: #1F2937; font-size: 12px;
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
    """

    # ------------------------------------------ редактор категорий ---------- #
    def _show_cat_editor(self):
        self._cat_editor_panel.set_categories(list(ALL_CATEGORIES))
        self._cat_editor_panel.show()
        self._cat_editor_panel.raise_()
        self._cat_editor_panel.activateWindow()

    def _on_categories_changed(self, new_cats: list[str]):
        import ui.categorization as _cat_mod
        _cat_mod.ALL_CATEGORIES[:] = new_cats
        self._category_delegate._items = list(new_cats)

        current = self.combo_cat.currentText()
        self.combo_cat.blockSignals(True)
        self.combo_cat.clear()
        self.combo_cat.addItem("Все категории")
        self.combo_cat.addItems(new_cats)
        idx = self.combo_cat.findText(current)
        self.combo_cat.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_cat.blockSignals(False)

        self.apply_filters()

    def _on_category_renamed(self, old_name: str, new_name: str):
        import ui.categorization as _cat_mod
        # Переименовываем во всех строках df_full
        if self.df_full is not None and "Категория" in self.df_full.columns:
            self.df_full.loc[self.df_full["Категория"] == old_name, "Категория"] = new_name
        # Делегат уже обновлён через rename_user_category → ALL_CATEGORIES
        self._category_delegate._items = list(_cat_mod.ALL_CATEGORIES)
        # Комбобокс фильтра: меняем только изменившийся пункт
        current = self.combo_cat.currentText()
        if current == old_name:
            current = new_name
        self.combo_cat.blockSignals(True)
        self.combo_cat.clear()
        self.combo_cat.addItem("Все категории")
        self.combo_cat.addItems(_cat_mod.ALL_CATEGORIES)
        idx = self.combo_cat.findText(current)
        self.combo_cat.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_cat.blockSignals(False)
        self.apply_filters()

    # --------------------------------------------------- наполнение модели -- #
    # Желаемый порядок видимых столбцов по умолчанию.
    _COLUMN_ORDER = ["Дата", "Контрагент", "Назначение", "Сумма", "Категория", "Участок"]

    def _rebuild_model(self, df: "pd.DataFrame"):
        visible = [c for c in df.columns if not str(c).startswith("_")]
        ordered = [c for c in self._COLUMN_ORDER if c in visible]
        ordered += [c for c in visible if c not in self._COLUMN_ORDER]
        columns = ordered
        records = []
        for df_idx, row in df.iterrows():
            data = {c: row[c] for c in columns}
            breakdown = _parse_breakdown(row.get("_breakdown"))
            records.append((df_idx, data, breakdown))

        self.model.load(columns, records)
        self._apply_header_layout(self.model.columns())

        # Делегат комбобокса — только на колонку «Категория».
        cat = self.model.category_column()
        if self._cat_col is not None and self._cat_col != cat:
            self.tree.setItemDelegateForColumn(self._cat_col, self._cell_delegate)
        if cat >= 0:
            self.tree.setItemDelegateForColumn(cat, self._category_delegate)
        self._cat_col = cat

        # Делегат с линиями дерева и корзиной — на колонку «Контрагент».
        cols = self.model.columns()
        cont = cols.index("Контрагент") if "Контрагент" in cols else -1
        if self._cont_col is not None and self._cont_col != cont:
            self.tree.setItemDelegateForColumn(self._cont_col, self._cell_delegate)
        if cont >= 0:
            self.tree.setItemDelegateForColumn(cont, self._branch_delegate)
        self._cont_col = cont

        # Делегат выпадающего списка — на колонку «Участок».
        plot = cols.index("Участок") if "Участок" in cols else -1
        if self._plot_col is not None and self._plot_col != plot:
            self.tree.setItemDelegateForColumn(self._plot_col, self._cell_delegate)
        if plot >= 0:
            self.tree.setItemDelegateForColumn(plot, self._plot_delegate)
        self._plot_col = plot

        self._refresh_summary()

    def _apply_header_layout(self, columns: list[str]):
        col_widths = {
            _CTRL_COL: 84, "Дата": 95, "Контрагент": 260, "Сумма": 140,
            "Назначение": 340, "Категория": 210, "Участок": 80,
        }
        header = self.tree.header()
        for col_idx, col in enumerate(columns):
            w = col_widths.get(col)
            if w:
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
                self.tree.setColumnWidth(col_idx, w)
            else:
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.ResizeToContents)

    # -------------------------------------------------- замечания ----------- #
    def _compute_warnings(self) -> dict:
        """Считает ошибки по всему df_full (не только по отфильтрованным строкам).
        Возвращает {dupes, split_mismatch, no_cat, total} где total — кол-во
        уникальных строк с хотя бы одной ошибкой."""
        out = {"dupes": 0, "split_mismatch": 0, "no_cat": 0, "need_split": 0, "no_plot": 0, "total": 0}
        if self.df_full is None or self.df_full.empty:
            return out

        issue_idx: set = set()

        if "_hash" in self.df_full.columns:
            mask = self.df_full.duplicated(subset=["_hash"], keep=False)
            out["dupes"] = int(mask.sum())
            issue_idx.update(self.df_full.index[mask].tolist())

        if "Категория" in self.df_full.columns:
            _bd_empty = {"", "None", "nan", "[]"}

            def _no_cat(row):
                bd_raw = row.get("_breakdown")
                bd = _parse_breakdown(bd_raw) if bd_raw else []
                if bd:
                    # Мультиоперация: считаем «нет категории», если хоть у одного ребёнка пусто
                    return any(
                        not str(it.get("Категория") or "").strip() for it in bd
                    )
                cat = row.get("Категория")
                return cat is None or (isinstance(cat, float) and pd.isna(cat)) \
                    or str(cat).strip() == ""

            mask = self.df_full.apply(_no_cat, axis=1)
            out["no_cat"] = int(mask.sum())
            issue_idx.update(self.df_full.index[mask].tolist())

        if "_breakdown" in self.df_full.columns:
            _empty = {"", "None", "nan", "[]"}
            has_bd = (
                self.df_full["_breakdown"].notna() &
                ~self.df_full["_breakdown"].astype(str).str.strip().isin(_empty)
            )
            for idx, row in self.df_full[has_bd].iterrows():
                items = _parse_breakdown(row.get("_breakdown"))
                if not items:
                    continue
                parent = _to_num(row.get("Сумма")) or 0.0
                child  = sum(_to_num(it.get("Сумма")) or 0.0 for it in items)
                if abs(parent - child) > 0.005:
                    out["split_mismatch"] += 1
                    issue_idx.add(idx)

        if "Категория" in self.df_full.columns:
            mask = self.df_full["Категория"].astype(str) == "Членские взносы + Электроэнергия (авто)"
            out["need_split"] = int(mask.sum())
            issue_idx.update(self.df_full.index[mask].tolist())

        if "Категория" in self.df_full.columns and "Участок" in self.df_full.columns:
            known_plots = set(self._plot_delegate._items)

            def _needs_plot_cat(cat: str) -> bool:
                return cat.startswith("Членские взносы") or \
                       cat.startswith("Электроэнергия (от садоводов)")

            def _bad_plot(plot) -> bool:
                s = str(plot).strip() if plot is not None else ""
                return not s or s not in known_plots

            def _no_plot_row(row) -> bool:
                bd = _parse_breakdown(row.get("_breakdown")) if row.get("_breakdown") else []
                if bd:
                    return any(
                        _needs_plot_cat(str(it.get("Категория") or "")) and
                        _bad_plot(it.get("Участок"))
                        for it in bd
                    )
                return _needs_plot_cat(str(row.get("Категория") or "")) and \
                       _bad_plot(row.get("Участок"))

            mask = self.df_full.apply(_no_plot_row, axis=1)
            out["no_plot"] = int(mask.sum())
            issue_idx.update(self.df_full.index[mask].tolist())

        out["total"] = len(issue_idx)
        return out

    def _refresh_summary(self):
        nodes = self.model.top_nodes()
        self.lbl_records.setText(f"Записей: {len(nodes)}")
        self._rebuild_warnings_bar()

    def _rebuild_warnings_bar(self):
        # Очищаем старые чипы
        while self._warnings_layout.count():
            item = self._warnings_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        w = self._compute_warnings()
        chips: list[tuple[str, str]] = []
        if w["dupes"]:
            chips.append(("dupes",         f"Дубли ({w['dupes']})"))
        if w["split_mismatch"]:
            chips.append(("split_mismatch", f"Сумма дочерних строк ({w['split_mismatch']})"))
        if w["no_cat"]:
            chips.append(("no_cat",         f"Нет категории ({w['no_cat']})"))
        if w["need_split"]:
            chips.append(("need_split",     f"Необходимо распределить операцию ({w['need_split']})"))
        if w["no_plot"]:
            chips.append(("no_plot",        f"Неизвестный участок ({w['no_plot']})"))

        if not chips:
            self._active_warning = None
            self._warnings_bar.setVisible(False)
            return

        # Сбрасываем активный фильтр, если его замечание исчезло
        if self._active_warning not in {k for k, _ in chips}:
            self._active_warning = None

        lbl = QLabel(f"⚠  Замечания ({w['total']}):")
        lbl.setStyleSheet(
            "color:#D97706; font-size:12px; font-weight:600; background:transparent;"
        )
        self._warnings_layout.addWidget(lbl)

        for key, text in chips:
            btn = QPushButton(text)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if self._active_warning == key:
                btn.setStyleSheet("""
                    QPushButton {
                        background: #F59E0B; color: #FFFFFF;
                        border: 1px solid #D97706; border-radius: 11px;
                        padding: 3px 12px; font-size: 11px; font-weight: 600;
                    }
                    QPushButton:hover { background: #D97706; }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background: #FEF3C7; color: #92400E;
                        border: 1px solid #F59E0B; border-radius: 11px;
                        padding: 3px 12px; font-size: 11px;
                    }
                    QPushButton:hover { background: #FDE68A; }
                """)
            btn.clicked.connect(lambda checked, k=key: self._on_warning_tag_clicked(k))
            self._warnings_layout.addWidget(btn)

        self._warnings_layout.addStretch()
        self._warnings_bar.setVisible(True)

    def _on_warning_tag_clicked(self, key: str):
        self._active_warning = None if self._active_warning == key else key
        self.apply_filters()

    # --------------------------------------------------------- разбивка --- #
    def _set_breakdown(self, df_idx, items: list):
        if self.df_full is None:
            return
        if "_breakdown" not in self.df_full.columns:
            self.df_full["_breakdown"] = None
        self.df_full.at[df_idx, "_breakdown"] = _dump_breakdown(items)

    def _add_split(self, op_node: _Node):
        if self.df_full is None or op_node.df_idx not in self.df_full.index:
            return
        items = _parse_breakdown(self.df_full.loc[op_node.df_idx].get("_breakdown"))
        # Правила ветки: «Контрагент» копируется из операции; «Назначение» не
        # используется; «Сумма»/«Категория»/«Участок» заполняются вручную.
        items.append({
            "Контрагент": str(op_node.data.get("Контрагент") or ""),
            "Сумма": 0.0,
            "Категория": ALL_CATEGORIES[0],
            "Участок": "",
        })
        self._set_breakdown(op_node.df_idx, items)
        target = op_node.df_idx
        self.apply_filters()
        idx = self.model.index_for_df_idx(target)
        if idx.isValid():
            self.tree.expand(idx)
        # Разбивка — аннотация, на суммы/долги не влияет: dataLoaded не эмитим.

    def _delete_split(self, split_node: _Node):
        parent = split_node.parent
        if parent is None or self.df_full is None or parent.df_idx not in self.df_full.index:
            return
        items = [dict(c.data) for c in parent.children if c is not split_node]
        self._set_breakdown(parent.df_idx, items)
        target = parent.df_idx
        self.apply_filters()
        idx = self.model.index_for_df_idx(target)
        if idx.isValid() and items:
            self.tree.expand(idx)
        # Разбивка — аннотация, на суммы/долги не влияет: dataLoaded не эмитим.

    # ----------------------------------- клики по столбцу управления ------- #
    def _on_add_branch(self, index: QModelIndex):
        node = index.internalPointer()
        if node is not None and node.kind == "op":
            self._add_split(node)

    def _on_delete_branch(self, index: QModelIndex):
        node = index.internalPointer()
        if node is not None and node.kind == "split":
            self._delete_split(node)

    def _on_toggle(self, index: QModelIndex):
        if self.tree.isExpanded(index):
            self.tree.collapse(index)
        else:
            self.tree.expand(index)

    # ----------------------------------------------------- добавить строку -- #
    def _add_row(self):
        dlg = AddRowDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        row_data = dlg.get_result()

        if self.df_full is None:
            self.df_full = pd.DataFrame(
                columns=["Дата", "Контрагент", "Сумма", "Назначение", "Категория", "Участок"]
            )
            self.df_full = self.df_full.astype({"Дата": "datetime64[ns]", "Сумма": float})
            today = QDate.currentDate()
            self.date_from.setDate(QDate(today.year() - 1, 1, 1))
            self.date_to.setDate(today)

        new_idx = int(self.df_full.index.max()) + 1 if len(self.df_full) > 0 else 0

        new_hash = _compute_hash(row_data)
        row_data["_hash"] = new_hash

        new_row = {
            col: row_data.get(col, "" if col != "Сумма" else float("nan"))
            for col in self.df_full.columns
        }
        self.df_full.loc[new_idx] = new_row
        self.df_full["Дата"] = pd.to_datetime(self.df_full["Дата"], errors="coerce")

        self._manual_rows.add(new_idx)
        for col in row_data:
            if col in self.df_full.columns:
                self._manual_cells.add((new_idx, col))

        row_ts = row_data["Дата"]
        row_qdate = QDate(row_ts.year, row_ts.month, row_ts.day)
        if row_qdate < self.date_from.date():
            self.date_from.setDate(row_qdate)
        if row_qdate > self.date_to.date():
            self.date_to.setDate(row_qdate)

        self.apply_filters()
        self.dataLoaded.emit(self.df_full)

    # --------------------------------------------------------- загрузка --- #
    def load_file(self):
        settings_dlg = LoadSettingsDialog(self, has_existing_data=self.df_full is not None)
        if settings_dlg.exec() != QDialog.DialogCode.Accepted:
            return

        fmt        = settings_dlg.fmt
        auto_cat   = settings_dlg.auto_cat
        auto_plot  = settings_dlg.auto_plot
        merge_mode = settings_dlg.merge_mode

        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть файл выписки", "", "Excel файлы (*.xlsx *.xls)")
        if not path:
            return
        try:
            df = pd.read_excel(path, engine="openpyxl")
            cols = [c for c in df.columns
                    if not str(c).strip().startswith("Валюта") and str(c).strip() != ""]
            df = df[cols]

            if fmt == "sber":
                drop_cols = {"Номер", "Номер счёта", "Контрагент счёт", "Контрагент cчёт"}
                df.drop(columns=[c for c in drop_cols if c in df.columns], inplace=True)

            df = _merge_to_summa(df)

            df["Дата"] = pd.to_datetime(df["Дата"], dayfirst=True, errors="coerce")
            df = df[df["Дата"].notna()].copy()

            if auto_cat:
                df = apply_categorization(df)
            if auto_plot:
                df = apply_plot_column(df)

            if "Категория" not in df.columns:
                df["Категория"] = ""
            if "Участок" not in df.columns:
                df["Участок"] = ""

            df["Участок"] = df["Участок"].apply(
                lambda v: "" if pd.isna(v) else
                str(int(v)) if isinstance(v, float) and v == int(v) else str(v)
            )

            df = _ensure_meta_columns(df)

            if merge_mode and self.df_full is not None:
                existing = _ensure_meta_columns(self.df_full)
                new_start = int(existing.index.max()) + 1 if len(existing) > 0 else 0
                df = df.reset_index(drop=True)
                df.index = df.index + new_start
                self.df_full = pd.concat([existing, df])
            else:
                self._manual_rows.clear()
                self._manual_cells.clear()
                self.df_full = df

            min_d = self.df_full["Дата"].min()
            max_d = self.df_full["Дата"].max()
            if pd.notna(min_d):
                self.date_from.setDate(QDate(min_d.year, min_d.month, min_d.day))
            if pd.notna(max_d):
                self.date_to.setDate(QDate(max_d.year, max_d.month, max_d.day))
            self.apply_filters()
            self.dataLoaded.emit(self.df_full)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка загрузки", f"Не удалось загрузить файл:\n{e}")

    def load_dataframe(self, df: "pd.DataFrame"):
        """Восстанавливает DataFrame из сохранённого проекта без диалога выбора файла."""
        self._manual_rows.clear()
        self._manual_cells.clear()
        drop_cols = {"Номер", "Номер счёта", "Контрагент счёт", "Контрагент cчёт", "Теги"}
        df = df.drop(columns=[c for c in drop_cols if c in df.columns])
        df = _merge_to_summa(df)
        df = _ensure_meta_columns(df)
        self.df_full = df
        min_d, max_d = df["Дата"].min(), df["Дата"].max()
        if pd.notna(min_d):
            self.date_from.setDate(QDate(min_d.year, min_d.month, min_d.day))
        if pd.notna(max_d):
            self.date_to.setDate(QDate(max_d.year, max_d.month, max_d.day))
        self.apply_filters()
        self.dataLoaded.emit(self.df_full)

    def get_manual_cells_data(self) -> list:
        """Сериализует _manual_cells для сохранения в проект."""
        return [[int(df_idx), col] for df_idx, col in self._manual_cells]

    def restore_manual_cells(self, data: list):
        """Восстанавливает _manual_cells и _manual_rows после загрузки проекта."""
        self._manual_cells.clear()
        self._manual_rows.clear()
        for item in data:
            if len(item) == 2:
                df_idx, col = int(item[0]), str(item[1])
                self._manual_cells.add((df_idx, col))
                self._manual_rows.add(df_idx)
        self.tree.viewport().update()

    def refresh_plot_column(self):
        """Пересчитывает столбец «Участок» по актуальным данным из snt_plots.json.
        Строки, вручную отредактированные пользователем, не перезаписываются."""
        # Обновляем список в делегате — вдруг добавились новые участки
        self._plot_delegate._items = load_plot_numbers()
        if self.df_full is None:
            return
        df_new = apply_plot_column(self.df_full)
        if "Участок" not in self.df_full.columns:
            self.df_full = df_new
        else:
            auto_mask = ~self.df_full.index.isin(self._manual_rows)
            self.df_full.loc[auto_mask, "Участок"] = df_new.loc[auto_mask, "Участок"]
        self.apply_filters()

    # ----------------------------------------------------------- фильтры -- #
    def _filtered_df(self) -> "pd.DataFrame":
        df = self.df_full.copy()

        d_from = self.date_from.date().toPyDate()
        d_to   = self.date_to.date().toPyDate()
        df = df[(df["Дата"].dt.date >= d_from) & (df["Дата"].dt.date <= d_to)]

        op_type = self.combo_type.currentText()
        if op_type == "Поступления" and "Сумма" in df.columns:
            df = df[pd.to_numeric(df["Сумма"], errors="coerce") > 0]
        elif op_type == "Списания" and "Сумма" in df.columns:
            df = df[pd.to_numeric(df["Сумма"], errors="coerce") < 0]

        cat_filter = self.combo_cat.currentText()
        if cat_filter != "Все категории":
            def _cat_matches(row):
                bd = _parse_breakdown(row.get("_breakdown"))
                if bd:
                    # Мультиоперация: ищем категорию среди дочерних
                    return any(it.get("Категория") == cat_filter for it in bd)
                return row.get("Категория") == cat_filter
            df = df[df.apply(_cat_matches, axis=1)]

        search = self.search_input.text().strip().lower()
        if search:
            mask = (
                df["Контрагент"].astype(str).str.lower().str.contains(search, na=False) |
                df["Назначение"].astype(str).str.lower().str.contains(search, na=False)
            )
            df = df[mask]

        # Фильтр по активному замечанию
        if self._active_warning == "dupes":
            if "_hash" in df.columns:
                df = df[df.duplicated(subset=["_hash"], keep=False)]
        elif self._active_warning == "no_cat":
            if "Категория" in df.columns:
                def _no_cat_row(row):
                    bd_raw = row.get("_breakdown")
                    bd = _parse_breakdown(bd_raw) if bd_raw else []
                    if bd:
                        return any(
                            not str(it.get("Категория") or "").strip() for it in bd
                        )
                    cat = row.get("Категория")
                    return cat is None or (isinstance(cat, float) and pd.isna(cat)) \
                        or str(cat).strip() == ""
                df = df[df.apply(_no_cat_row, axis=1)]
        elif self._active_warning == "need_split":
            if "Категория" in df.columns:
                df = df[
                    df["Категория"].astype(str) == "Членские взносы + Электроэнергия (авто)"
                ]
        elif self._active_warning == "no_plot":
            if "Категория" in df.columns and "Участок" in df.columns:
                known_plots = set(self._plot_delegate._items)

                def _no_plot_filter(row) -> bool:
                    def _needs(cat): return (
                        str(cat or "").startswith("Членские взносы") or
                        str(cat or "").startswith("Электроэнергия (от садоводов)")
                    )
                    def _bad(plot):
                        s = str(plot).strip() if plot is not None else ""
                        return not s or s not in known_plots

                    bd = _parse_breakdown(row.get("_breakdown")) if row.get("_breakdown") else []
                    if bd:
                        return any(_needs(it.get("Категория")) and _bad(it.get("Участок"))
                                   for it in bd)
                    return _needs(row.get("Категория")) and _bad(row.get("Участок"))

                df = df[df.apply(_no_plot_filter, axis=1)]
        elif self._active_warning == "split_mismatch":
            if "_breakdown" in df.columns:
                _empty = {"", "None", "nan", "[]"}
                has_bd = (
                    df["_breakdown"].notna() &
                    ~df["_breakdown"].astype(str).str.strip().isin(_empty)
                )
                bad = []
                for idx, row in df[has_bd].iterrows():
                    items = _parse_breakdown(row.get("_breakdown"))
                    if not items:
                        continue
                    parent = _to_num(row.get("Сумма")) or 0.0
                    child  = sum(_to_num(it.get("Сумма")) or 0.0 for it in items)
                    if abs(parent - child) > 0.005:
                        bad.append(idx)
                df = df[df.index.isin(bad)]

        return df

    def apply_filters(self):
        if self.df_full is None:
            return
        self._rebuild_model(self._filtered_df())

    def reset_filters(self):
        self.search_input.clear()
        self.combo_type.setCurrentIndex(0)
        self.combo_cat.setCurrentIndex(0)
        if self.df_full is not None:
            min_d, max_d = self.df_full["Дата"].min(), self.df_full["Дата"].max()
            if pd.notna(min_d):
                self.date_from.setDate(QDate(min_d.year, min_d.month, min_d.day))
            if pd.notna(max_d):
                self.date_to.setDate(QDate(max_d.year, max_d.month, max_d.day))
        self.apply_filters()

    # ------------------------------------------------------------ экспорт -- #
    def _export_excel(self):
        if self.df_full is None:
            QMessageBox.warning(self, "Нет данных", "Сначала загрузите файл выписки.")
            return

        df = self._filtered_df()
        if df.empty:
            QMessageBox.information(self, "Нет данных", "После применения фильтров нет строк для экспорта.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт детализации", "детализация.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        if not path.endswith(".xlsx"):
            path += ".xlsx"

        try:
            import openpyxl
            from openpyxl.styles import Font, Alignment, PatternFill
            from openpyxl.utils import get_column_letter
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Детализация"

            headers = [c for c in df.columns if not str(c).startswith("_")]
            summa_col_idx = headers.index("Сумма") + 1 if "Сумма" in headers else None

            ws.append(headers)
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal="center")

            for _, row in df.iterrows():
                out_row = []
                for col in headers:
                    val = row[col]
                    if col == "Сумма":
                        num = pd.to_numeric(val, errors="coerce")
                        out_row.append(float(num) if pd.notna(num) else "")
                    elif col == "Дата":
                        out_row.append(val.strftime("%d.%m.%Y") if pd.notna(val) else "")
                    else:
                        out_row.append("" if pd.isna(val) else val)
                ws.append(out_row)

            if summa_col_idx is not None:
                green_fill = PatternFill("solid", fgColor="C8E6C9")
                red_fill   = PatternFill("solid", fgColor="FFCDD2")
                summa_letter = get_column_letter(summa_col_idx)
                for r in range(2, ws.max_row + 1):
                    cell = ws[f"{summa_letter}{r}"]
                    if isinstance(cell.value, (int, float)):
                        cell.fill  = green_fill if cell.value >= 0 else red_fill
                        cell.number_format = '#,##0.00 ₽'
                        cell.alignment = Alignment(horizontal="right")

            for col_cells in ws.columns:
                max_len = max((len(str(c.value or "")) for c in col_cells), default=0)
                ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 4, 60)

            wb.save(path)
            QMessageBox.information(self, "Экспорт завершён", f"Файл сохранён:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка экспорта", str(e))

    # ------------------------------------------------------ редактирование -- #
    def _on_cell_edited(self, node: _Node, col: str):
        """Слот модели: переносит правку ячейки обратно в df_full."""
        if self.df_full is None:
            return

        if node.kind == "op":
            df_idx = node.df_idx
            if df_idx is not None and df_idx in self.df_full.index:
                if col == "Сумма":
                    self.df_full.at[df_idx, "Сумма"] = node.data["Сумма"]
                elif col == "Дата":
                    self.df_full.at[df_idx, "Дата"] = node.data["Дата"]
                else:
                    self.df_full.at[df_idx, col] = node.data[col]
                    if col == "Категория" and node.data[col]:
                        if self.combo_cat.findText(node.data[col]) == -1:
                            self.combo_cat.addItem(node.data[col])
                self._manual_rows.add(df_idx)
                self._manual_cells.add((df_idx, col))
        else:  # split — сохраняем разбивку родителя
            parent = node.parent
            if parent is not None and parent.df_idx in self.df_full.index:
                items = [dict(c.data) for c in parent.children]
                self._set_breakdown(parent.df_idx, items)

        # ВАЖНО: НЕ эмитим dataLoaded на каждую правку ячейки — на этом сигнале
        # висят тяжёлые пересчёты долгов (взносы/электроэнергия/home), из-за чего
        # после выхода из редактирования ПК зависал на 5-6 сек. Долги
        # пересчитываются при загрузке/добавлении/удалении операций.
        self._refresh_summary()

    # -------------------------------------------------------- контекст-меню -- #
    def _show_context_menu(self, pos: QPoint):
        index = self.tree.indexAt(pos)
        if not index.isValid():
            return
        node = index.internalPointer()
        self.tree.setCurrentIndex(index)

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #F8F9FA; border: 1px solid #D1D5DB; color: #374151;
                font-size: 13px; padding: 4px;
            }
            QMenu::item { padding: 8px 20px; border-radius: 4px; }
            QMenu::item:selected { background: #EEF2FF; color: #6366F1; }
            QMenu::separator { height: 1px; background: #E5E7EB; margin: 4px 8px; }
        """)

        if node.kind == "op":
            act_split = QAction("Добавить распределение", self)
            act_split.triggered.connect(lambda: self._add_split(node))
            menu.addAction(act_split)
            menu.addSeparator()
            act_dup = QAction("Дублировать операцию", self)
            act_dup.triggered.connect(lambda: self._duplicate_op(node))
            menu.addAction(act_dup)
            act_del = QAction("Удалить операцию", self)
            act_del.triggered.connect(lambda: self._delete_op(node))
            menu.addAction(act_del)
        else:
            act_del = QAction("Удалить распределение", self)
            act_del.triggered.connect(lambda: self._delete_split(node))
            menu.addAction(act_del)

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _duplicate_op(self, op_node: _Node):
        if self.df_full is None or op_node.df_idx not in self.df_full.index:
            return
        new_idx = int(self.df_full.index.max()) + 1
        self.df_full.loc[new_idx] = self.df_full.loc[op_node.df_idx].copy()
        self.apply_filters()
        idx = self.model.index_for_df_idx(new_idx)
        if idx.isValid():
            self.tree.setCurrentIndex(idx)
        self.dataLoaded.emit(self.df_full)

    def _delete_op(self, op_node: _Node):
        reply = QMessageBox.question(
            self, "Удаление строки", "Удалить выбранную операцию?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self.df_full is not None and op_node.df_idx in self.df_full.index:
            self.df_full = self.df_full.drop(index=op_node.df_idx)
        self.apply_filters()
        self.dataLoaded.emit(self.df_full)
