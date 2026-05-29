"""
Пример архитектуры Model-View для PyQt6: QTreeView + QAbstractItemModel + делегат с QComboBox.

Демонстрирует:
  1. Высокую производительность — модель не создаёт виджеты на строки, рисует только видимое.
  2. Древовидную структуру — раскрывающиеся родитель/потомок через TreeNode.
  3. Выпадающие списки «на лету» — QComboBox создаётся делегатом только в момент
     редактирования и уничтожается сразу после, не висит в памяти.

Запуск:  python -m ui.tree_model_example
"""

from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    Qt,
)
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QMainWindow,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QWidget,
)


# --------------------------------------------------------------------------- #
#  1. Узел дерева                                                              #
# --------------------------------------------------------------------------- #
# Модель сама по себе не хранит данные «плоско» — она оборачивает дерево узлов.
# Каждый TreeNode знает своего родителя и своих детей. Этого достаточно, чтобы
# QAbstractItemModel мог построить любые индексы (index/parent) для QTreeView.


# Названия колонок (порядок задаёт расположение в таблице).
COLUMNS = ["Наименование", "Сумма", "Категория"]
COL_NAME, COL_SUM, COL_CATEGORY = range(3)

# Допустимые значения для выпадающего списка в колонке "Категория".
CATEGORIES = ["Взносы", "Электроэнергия", "Вода", "Вывоз мусора", "Ремонт", "Прочее"]


class TreeNode:
    """Один узел дерева. Хранит данные по колонкам и ссылки родитель/дети."""

    __slots__ = ("_data", "_parent", "_children")

    def __init__(self, data: list[Any], parent: Optional["TreeNode"] = None):
        # data — список значений по колонкам, например ["Май", 1500.0, "Взносы"]
        self._data: list[Any] = data
        self._parent: Optional[TreeNode] = parent
        self._children: list[TreeNode] = []

    # -- построение дерева --------------------------------------------------- #
    def add_child(self, child: "TreeNode") -> "TreeNode":
        child._parent = self
        self._children.append(child)
        return child

    # -- навигация (нужна модели) ------------------------------------------- #
    def child(self, row: int) -> Optional["TreeNode"]:
        if 0 <= row < len(self._children):
            return self._children[row]
        return None

    def child_count(self) -> int:
        return len(self._children)

    def parent(self) -> Optional["TreeNode"]:
        return self._parent

    def row(self) -> int:
        """Индекс этого узла в списке детей родителя."""
        if self._parent is not None:
            return self._parent._children.index(self)
        return 0

    def is_group(self) -> bool:
        """Узел с детьми — «группа» (строка ФИО), его сумма считается из подстрок."""
        return len(self._children) > 0

    # -- доступ к данным ---------------------------------------------------- #
    def data(self, column: int) -> Any:
        # Сумма у группы не хранится, а вычисляется из детей (автосумма).
        if column == COL_SUM and self.is_group():
            return self.sum_value()
        if 0 <= column < len(self._data):
            return self._data[column]
        return None

    def sum_value(self) -> float:
        """Рекурсивная сумма: лист отдаёт своё значение, группа — сумму детей."""
        if self.is_group():
            return sum(child.sum_value() for child in self._children)
        value = self._data[COL_SUM] if len(self._data) > COL_SUM else 0
        return float(value or 0)

    def set_data(self, column: int, value: Any) -> bool:
        # Сумму группы напрямую не редактируем — она производная от детей.
        if column == COL_SUM and self.is_group():
            return False
        if 0 <= column < len(self._data):
            self._data[column] = value
            return True
        return False


# --------------------------------------------------------------------------- #
#  2. Модель данных                                                            #
# --------------------------------------------------------------------------- #
# QAbstractItemModel — самый общий (и самый «ручной») класс модели. Для дерева
# обязательно реализовать пять методов: index, parent, rowCount, columnCount,
# data. Для редактирования добавляем flags + setData, для заголовков —
# headerData. Внутренний указатель индекса (internalPointer) хранит TreeNode.


class TreeModel(QAbstractItemModel):
    def __init__(self, root: TreeNode, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._root = root

    # -- внутренний помощник ------------------------------------------------ #
    def _node(self, index: QModelIndex) -> TreeNode:
        """Достаёт TreeNode из индекса; для невалидного индекса — корень."""
        if index.isValid():
            node = index.internalPointer()
            if node is not None:
                return node
        return self._root

    # -- построение индексов (ядро дерева) ---------------------------------- #
    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        # Сначала проверяем, что запрашиваемая ячейка вообще существует.
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        parent_node = self._node(parent)
        child = parent_node.child(row)
        if child is not None:
            # createIndex привязывает к ячейке наш узел (internalPointer).
            return self.createIndex(row, column, child)
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        child = self._node(index)
        parent_node = child.parent()
        if parent_node is None or parent_node is self._root:
            return QModelIndex()
        # Родитель всегда показывается в колонке 0.
        return self.createIndex(parent_node.row(), 0, parent_node)

    # -- размеры ------------------------------------------------------------ #
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        # Дети есть только у колонки 0 (стандарт Qt для деревьев).
        if parent.column() > 0:
            return 0
        return self._node(parent).child_count()

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(COLUMNS)

    # -- чтение данных ------------------------------------------------------ #
    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None

        node = self._node(index)
        col = index.column()

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            value = node.data(col)
            # Сумму форматируем для отображения, но для редактирования отдаём «сырое» число.
            if col == COL_SUM and value is not None:
                if role == Qt.ItemDataRole.DisplayRole:
                    return f"{value:,.2f} ₽".replace(",", " ")
                return value
            return value

        if role == Qt.ItemDataRole.TextAlignmentRole and col == COL_SUM:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        return None

    # -- запись данных ------------------------------------------------------ #
    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        node = self._node(index)
        if node.set_data(index.column(), value):
            # Сообщаем view, что ячейка изменилась — она перерисуется.
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])
            # Если поменяли сумму ребёнка — пересчитать и перерисовать суммы всех
            # родителей вверх по дереву (автосумма).
            if index.column() == COL_SUM:
                self._notify_parents_sum_changed(index)
            return True
        return False

    def _notify_parents_sum_changed(self, index: QModelIndex) -> None:
        """Идём от ячейки вверх и шлём dataChanged для суммы каждого родителя."""
        parent = self.parent(index)
        while parent.isValid():
            sum_index = parent.siblingAtColumn(COL_SUM)
            self.dataChanged.emit(sum_index, sum_index, [Qt.ItemDataRole.DisplayRole])
            parent = self.parent(parent)

    # -- флаги (какие ячейки редактируемы) ---------------------------------- #
    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        node = self._node(index)
        if index.column() == COL_CATEGORY:
            flags |= Qt.ItemFlag.ItemIsEditable
        # Сумму редактируем только у листьев — у групп она автоматическая.
        if index.column() == COL_SUM and not node.is_group():
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    # -- заголовки колонок -------------------------------------------------- #
    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(COLUMNS):
                return COLUMNS[section]
        return None


# --------------------------------------------------------------------------- #
#  3. Делегат с QComboBox                                                      #
# --------------------------------------------------------------------------- #
# Главная идея: редактор (QComboBox) НЕ хранится в модели и не висит в памяти.
# QTreeView сам вызывает createEditor только когда пользователь начинает
# редактировать ячейку (двойной клик / F2), и уничтожает редактор, как только
# редактирование закончено. Так на 10 000 строк в памяти максимум один комбобокс.


class ComboBoxDelegate(QStyledItemDelegate):
    """Рисует выпадающий список при редактировании заданной колонки."""

    def __init__(self, items: list[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._items = items

    def createEditor(self, parent: QWidget, option: QStyleOptionViewItem,
                     index: QModelIndex) -> QWidget:
        # Создаём комбобокс «на лету». parent — это viewport дерева, важно его передать.
        combo = QComboBox(parent)
        combo.addItems(self._items)
        # Необязательно: закрывать редактор сразу после выбора пункта.
        combo.activated.connect(lambda: self.commitData.emit(combo))
        return combo

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:
        # Заполняем редактор текущим значением из модели.
        current = index.model().data(index, Qt.ItemDataRole.EditRole)
        if isinstance(editor, QComboBox):
            pos = editor.findText(str(current)) if current is not None else -1
            editor.setCurrentIndex(pos if pos >= 0 else 0)

    def setModelData(self, editor: QWidget, model: QAbstractItemModel,
                     index: QModelIndex) -> None:
        # Забираем выбор из редактора и пишем обратно в модель.
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor: QWidget, option: QStyleOptionViewItem,
                             index: QModelIndex) -> None:
        # Растягиваем редактор по размеру ячейки.
        editor.setGeometry(option.rect)


# --------------------------------------------------------------------------- #
#  4. Тестовые данные                                                         #
# --------------------------------------------------------------------------- #


def build_sample_tree() -> TreeNode:
    """Корень -> строки ФИО -> детализация начислений. Корень не отображается.

    Сумма в строке ФИО не задаётся вручную — она вычисляется как сумма подстрок.
    """
    root = TreeNode(data=["", "", ""])  # невидимый корень

    # Иванов: 1500 + 1500 = 3000 (итог посчитается сам).
    ivanov = root.add_child(TreeNode(["Иванов И.И.", None, "Взносы"]))
    ivanov.add_child(TreeNode(["Электричество", 1500.0, "Электроэнергия"]))
    ivanov.add_child(TreeNode(["Членский взнос", 1500.0, "Взносы"]))

    # Петрова: 2000 + 700 + 300 = 3000.
    petrova = root.add_child(TreeNode(["Петрова А.С.", None, "Взносы"]))
    petrova.add_child(TreeNode(["Членский взнос", 2000.0, "Взносы"]))
    petrova.add_child(TreeNode(["Вода", 700.0, "Вода"]))
    petrova.add_child(TreeNode(["Вывоз мусора", 300.0, "Вывоз мусора"]))

    # Сидоров — без детализации (одиночная строка, плюса не будет).
    root.add_child(TreeNode(["Сидоров П.П.", 1500.0, "Взносы"]))

    return root


# --------------------------------------------------------------------------- #
#  5. Минимальное рабочее окно                                                #
# --------------------------------------------------------------------------- #


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Model-View пример: QTreeView + QComboBox делегат")
        self.resize(700, 400)

        # Модель.
        self.model = TreeModel(build_sample_tree())

        # View.
        self.view = QTreeView()
        self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True)
        self.view.expandAll()
        # Двойной клик / клик по уже выбранной ячейке открывает редактор.
        self.view.setEditTriggers(
            QTreeView.EditTrigger.DoubleClicked | QTreeView.EditTrigger.SelectedClicked
        )
        # Подгоняем ширину первой колонки под содержимое.
        self.view.resizeColumnToContents(COL_NAME)

        # Делегат вешаем ТОЛЬКО на колонку "Категория".
        self.view.setItemDelegateForColumn(COL_CATEGORY, ComboBoxDelegate(CATEGORIES, self.view))

        self.setCentralWidget(self.view)


def main() -> None:
    import sys

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
