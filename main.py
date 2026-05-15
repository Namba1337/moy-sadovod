import sys
import re
import json
import os
import shutil
import calendar
from datetime import date

DATA_DIR = "data"
import pandas as pd

from core import energy
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QStackedWidget, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit, QComboBox,
    QDateEdit, QFrame, QFileDialog, QMessageBox, QMenu, QInputDialog, QDialog,
    QGraphicsView, QGraphicsScene, QGraphicsEllipseItem, QGraphicsTextItem,
)
from PyQt6.QtCore import Qt, QDate, QPoint, QRectF, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QAction, QPainter, QPixmap, QPen


# ======================================================================= #
#  МОДУЛЬ КАТЕГОРИЗАЦИИ
# ======================================================================= #

CATEGORY_COLORS = {
    "Членские взносы":                   QColor(30,  74, 120),
    "Членские взносы + Электроэнергия":  QColor(20,  90, 100),
    "Электроэнергия (от садоводов)":     QColor(90,  80,  10),
    "Оплата электроэнергии (поставщик)": QColor(110, 55,  10),
    "Налоги и штрафы":                   QColor(120, 30,  30),
    "Программное обеспечение":           QColor(80,  40, 110),
    "Материалы и работы":                QColor(20,  90,  40),
    "Банковские комиссии":               QColor(80,  65,  20),
    "Возврат":                           QColor(10,  90,  75),
    "Подотчётные суммы":                 QColor(60,  85,  20),
    "Прочее":                            QColor(55,  55,  60),
}

ALL_CATEGORIES = list(CATEGORY_COLORS.keys())


def categorize_row(row: dict) -> str:
    text      = str(row.get("Назначение", "")).lower()
    contragent = str(row.get("Контрагент", "")).lower()

    if "пермэнергосбыт" in contragent or "пермская энергосбытовая" in contragent:
        return "Оплата электроэнергии (поставщик)"

    electro_words = [
        "электроэнерги", "электричеств", "эл/энерги", "эл.энерги",
        "эл.знерги", "злектроэнерги", "эл энерги", "элект.энерги",
        "электро энерги", "свет", "э/э", "квт", "кВт",
        "зл.знерги", "электорэнерги", "потреблен", "электролени",
        "электротовар", "эликтричеств", "эл,энерги", "эл. энерги", "эл. энерги", "эл. энерги",
    ]
    member_words = [
        "членск", "членнск", "чл.взн", "чл взн", "чл взнос",
        "взносы", "взнос", "садоводческий взнос", "садоводческое товарищество",
        "общественные нужды", "обществен нужды", "жкх", "ежегодный взнос",
    ]
    is_electro = any(w in text for w in electro_words)
    is_member  = any(w in text for w in member_words)

    if is_electro and is_member:
        return "Членские взносы + Электроэнергия"
    if is_electro:
        return "Электроэнергия (от садоводов)"
    if is_member:
        return "Членские взносы"

    if re.search(r"долг|аванс|уч\.19;|2026\s*год|2025\s*год", text):
        return "Членские взносы"

    if ("контур" in text or "контур" in contragent
            or "программ" in text or "эвм" in text
            or "бухгалтер" in text or "модуль" in text):
        return "Программное обеспечение"

    nalog_words = [
        "налог","ифнс","казначейств","взыскани","штраф","пени",
        "нк рф","енс","страховани","фз №125","требование",
    ]
    if any(w in text for w in nalog_words):
        return "Налоги и штрафы"

    if "комисси" in text or "рко" in text or "задолженност" in text:
        return "Банковские комиссии"

    if "возврат" in text:
        return "Возврат"

    if "подотчет" in text:
        return "Подотчётные суммы"

    material_words = [
        "материал","уборка снега","транспортн","подряд",
        "строит","хозяйственн","счет на оплату","счёт на оплату",
        "оплата по счету","оплата по счёту","оплата по договору",
    ]
    if any(w in text for w in material_words):
        return "Материалы и работы"
    if any(w in contragent for w in ["ип ", "ооо "]):
        return "Материалы и работы"

    return "Прочее"


def apply_categorization(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Категория"] = df.apply(lambda row: categorize_row(row.to_dict()), axis=1)
    cols = list(df.columns)
    cols.remove("Категория")
    idx = cols.index("Назначение") + 1 if "Назначение" in cols else len(cols)
    cols.insert(idx, "Категория")
    return df[cols]



# ======================================================================= #
#  МОДУЛЬ ОПРЕДЕЛЕНИЯ УЧАСТКА
# ======================================================================= #
from collections import defaultdict as _defaultdict

_PLOTS_FILE = os.path.join(DATA_DIR, "snt_plots.json")

def _load_sadovods():
    """Загружает пары (участок, владелец) из snt_plots.json."""
    try:
        if os.path.exists(_PLOTS_FILE):
            with open(_PLOTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            result = []
            for entry in data:
                num = entry.get("num", "")
                for owner in entry.get("owners", []):
                    if owner.strip():
                        result.append((num, owner))
            return result
    except Exception:
        pass
    return []

def _build_plot_lookup(sadovods):
    sur = _defaultdict(list)
    fio = _defaultdict(list)
    for plot, name in sadovods:
        n = name.lower().strip()
        fio[n].append(plot)
        parts = n.split()
        if parts:
            s = parts[0]
            if plot not in sur[s]:
                sur[s].append(plot)
    return sur, fio

_SURNAME_MAP, _FIO_MAP = _build_plot_lookup(_load_sadovods())

_PAT_PLOT = [
    re.compile(r'участ[а-яё]*\s*[№#]?\s*(\d+(?:/\d+)?)', re.I),
    re.compile(r'\bуч[.:№#]?\s*(\d+(?:/\d+)?)', re.I),
]
_PAT_MULTI = re.compile(r'(?:участ[а-яё]*|уч[.:№#]?)\s*(\d+)\s*[,и]\s*(\d+)', re.I)
_NOISE = re.compile(
    r'(?:№|n)\s*\d{3,}[/\-]\d+|м-\d+|счет[а-яё]?\s*[№#]?\s*\d{5,}'
    r'|дог[а-яё.]*\s*[№#]?\s*[\w\-/]+|нк рф|фз\s*№|требование\s*№'
    r'|решени[юя].{0,30}№|\d{4,}', re.I)

def _find_in_text(text):
    clean = _NOISE.sub(' ', text.lower())
    m = _PAT_MULTI.search(clean)
    if m:
        return [m.group(1), m.group(2)]
    res = []
    for pat in _PAT_PLOT:
        for m in pat.finditer(clean):
            v = m.group(1).strip()
            if v and v not in res:
                res.append(v)
    return res

def _find_by_name(text):
    t = text.lower()
    for fio_n, plots in _FIO_MAP.items():
        if fio_n in t:
            return list(dict.fromkeys(plots))
    for sur, plots in _SURNAME_MAP.items():
        if re.search(r'\b' + re.escape(sur) + r'\b', t):
            return list(dict.fromkeys(plots))
    return []

def _find_by_contragent(c):
    parts = re.split(r'/{1,}', c.lower())
    for p in parts:
        p = p.strip()
        if len(p) < 5:
            continue
        for fio_n, plots in _FIO_MAP.items():
            if fio_n in p or p in fio_n:
                return list(dict.fromkeys(plots))
        words = p.split()
        if words and words[0] in _SURNAME_MAP:
            return list(dict.fromkeys(_SURNAME_MAP[words[0]]))
    return []

def get_plot(row: dict) -> str:
    text = str(row.get("Назначение", "") or "")
    cont = str(row.get("Контрагент",  "") or "")
    p = _find_in_text(text)
    if p: return ", ".join(str(x) for x in p)
    p = _find_by_name(text)
    if p: return ", ".join(str(x) for x in p[:2])
    p = _find_by_contragent(cont)
    if p: return ", ".join(str(x) for x in p[:2])
    return ""

def apply_plot_column(df):
    global _SURNAME_MAP, _FIO_MAP
    _SURNAME_MAP, _FIO_MAP = _build_plot_lookup(_load_sadovods())
    df = df.copy()
    df["Участок"] = df.apply(lambda r: get_plot(r.to_dict()), axis=1)
    cols = list(df.columns)
    cols.remove("Участок")
    ins = cols.index("Категория") + 1 if "Категория" in cols else len(cols)
    cols.insert(ins, "Участок")
    return df[cols]

# ======================================================================= #
#  ВКЛАДКА ДЕТАЛИЗАЦИЯ
# ======================================================================= #

class DetailWidget(QWidget):
    dataLoaded = pyqtSignal(object)   # эмитится после успешной загрузки выписки

    def __init__(self):
        super().__init__()
        self.df_full = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        # Заголовок + кнопка
        top_bar = QHBoxLayout()
        title = QLabel("Детализация операций")
        title.setObjectName("pageTitle")
        top_bar.addWidget(title)
        top_bar.addStretch()
        self.btn_load = QPushButton("📂  Загрузить файл")
        self.btn_load.setObjectName("btnPrimary")
        self.btn_load.clicked.connect(self.load_file)
        top_bar.addWidget(self.btn_load)
        layout.addLayout(top_bar)

        # Панель фильтров
        filter_frame = QFrame()
        filter_frame.setObjectName("filterFrame")
        fl = QHBoxLayout(filter_frame)
        fl.setContentsMargins(16, 12, 16, 12)
        fl.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍  Поиск по контрагенту или назначению...")
        self.search_input.setObjectName("searchInput")
        self.search_input.textChanged.connect(self.apply_filters)
        fl.addWidget(self.search_input, stretch=3)

        self.combo_type = QComboBox()
        self.combo_type.setObjectName("filterCombo")
        self.combo_type.addItems(["Все операции", "Поступления", "Списания"])
        self.combo_type.currentIndexChanged.connect(self.apply_filters)
        fl.addWidget(self.combo_type, stretch=1)

        self.combo_cat = QComboBox()
        self.combo_cat.setObjectName("filterCombo")
        self.combo_cat.addItem("Все категории")
        for cat in ALL_CATEGORIES:
            self.combo_cat.addItem(cat)
        self.combo_cat.currentIndexChanged.connect(self.apply_filters)
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

        self.status_label = QLabel("Файл не загружен", objectName="statusLabel")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(objectName="mainTable")
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.setShowGrid(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)

        summary_layout = QHBoxLayout()
        self.lbl_income  = QLabel("Поступления: —", objectName="summaryIncome")
        self.lbl_expense = QLabel("Списания: —",    objectName="summaryExpense")
        summary_layout.addWidget(self.lbl_income)
        summary_layout.addStretch()
        summary_layout.addWidget(self.lbl_expense)
        layout.addLayout(summary_layout)

    # ------------------------------------------------------------------ #
    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть файл выписки", "", "Excel файлы (*.xlsx *.xls)")
        if not path:
            return
        try:
            df = pd.read_excel(path, engine="openpyxl")
            cols = [c for c in df.columns
                    if not str(c).strip().startswith("Валюта") and str(c).strip() != ""]
            df = df[cols]
            df.rename(columns={"Контрагент cчёт": "Контрагент счёт"}, inplace=True)
            df["Дата"] = pd.to_datetime(df["Дата"], dayfirst=True, errors="coerce")
            df = df[df["Дата"].notna()].copy()

            # ── АВТОКАТЕГОРИЗАЦИЯ + УЧАСТОК ──
            df = apply_categorization(df)
            df = apply_plot_column(df)

            self.df_full = df
            min_d, max_d = df["Дата"].min(), df["Дата"].max()
            if pd.notna(min_d):
                self.date_from.setDate(QDate(min_d.year, min_d.month, min_d.day))
            if pd.notna(max_d):
                self.date_to.setDate(QDate(max_d.year, max_d.month, max_d.day))
            self.apply_filters()
            self.dataLoaded.emit(self.df_full)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка загрузки", f"Не удалось загрузить файл:\n{e}")

    # ------------------------------------------------------------------ #
    def apply_filters(self):
        if self.df_full is None:
            return
        df = self.df_full.copy()

        d_from = self.date_from.date().toPyDate()
        d_to   = self.date_to.date().toPyDate()
        df = df[(df["Дата"].dt.date >= d_from) & (df["Дата"].dt.date <= d_to)]

        op_type = self.combo_type.currentText()
        if op_type == "Поступления":
            df = df[df["Поступление"].notna() & (df["Поступление"] > 0)]
        elif op_type == "Списания":
            df = df[df["Списание"].notna() & (df["Списание"] > 0)]

        cat_filter = self.combo_cat.currentText()
        if cat_filter != "Все категории":
            df = df[df["Категория"] == cat_filter]

        search = self.search_input.text().strip().lower()
        if search:
            mask = (
                df["Контрагент"].astype(str).str.lower().str.contains(search, na=False) |
                df["Назначение"].astype(str).str.lower().str.contains(search, na=False)
            )
            df = df[mask]

        self._fill_table(df)

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

    # ------------------------------------------------------------------ #
    def _fill_table(self, df: pd.DataFrame):
        # Блокируем сигнал itemChanged пока заполняем таблицу
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.clearContents()

        columns = list(df.columns)
        self.table.setColumnCount(len(columns))
        self.table.setRowCount(len(df))
        self.table.setHorizontalHeaderLabels(columns)

        col_widths = {
            "Номер": 70, "Номер счёта": 175, "Дата": 95,
            "Контрагент счёт": 175, "Контрагент": 260,
            "Поступление": 115, "Списание": 115,
            "Назначение": 340, "Категория": 210, "Участок": 80,
        }

        for row_idx, (_, row) in enumerate(df.iterrows()):
            cat       = str(row.get("Категория", "Прочее"))
            row_color = CATEGORY_COLORS.get(cat, QColor(55, 55, 60))

            for col_idx, col in enumerate(columns):
                val = row[col]
                if col == "Дата" and pd.notna(val):
                    text = val.strftime("%d.%m.%Y")
                elif col in ("Поступление", "Списание") and pd.notna(val) and val != "":
                    try:
                        text = f"{float(val):,.2f} ₽".replace(",", " ")
                    except Exception:
                        text = str(val)
                else:
                    text = "" if pd.isna(val) else str(val)

                item = QTableWidgetItem(text)
                item.setBackground(row_color)

                if col == "Поступление" and text:
                    item.setForeground(QColor("#81d4a0"))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
                elif col == "Списание" and text:
                    item.setForeground(QColor("#ef9a9a"))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

                self.table.setItem(row_idx, col_idx, item)

        header = self.table.horizontalHeader()
        for col_idx, col in enumerate(columns):
            w = col_widths.get(col)
            if w:
                self.table.setColumnWidth(col_idx, w)
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            else:
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.ResizeToContents)

        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)  # Снимаем блокировку

        total_in  = pd.to_numeric(df["Поступление"], errors="coerce").sum()
        total_out = pd.to_numeric(df["Списание"],    errors="coerce").sum()
        self.lbl_income.setText(f"✅  Поступления: {total_in:,.2f} ₽".replace(",", " "))
        self.lbl_expense.setText(f"🔴  Списания: {total_out:,.2f} ₽".replace(",", " "))
        self.status_label.setText(f"Показано записей: {len(df)}")


    # ------------------------------------------------------------------ #
    #  КОНТЕКСТНОЕ МЕНЮ (ПКМ)
    # ------------------------------------------------------------------ #
    def _show_context_menu(self, pos: QPoint):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        self.table.selectRow(row)

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #0d1b2a;
                border: 1px solid #2a4a6b;
                color: #cdd9e5;
                font-size: 13px;
                padding: 4px;
            }
            QMenu::item {
                padding: 8px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: #1a2e45;
                color: #64b5f6;
            }
            QMenu::separator {
                height: 1px;
                background: #1e3a5f;
                margin: 4px 8px;
            }
        """)

        act_dup = QAction("📋  Дублировать строку", self)
        act_dup.triggered.connect(lambda: self._duplicate_row(row))
        menu.addAction(act_dup)

        menu.addSeparator()

        act_del = QAction("🗑️  Удалить строку", self)
        act_del.triggered.connect(lambda: self._delete_row(row))
        menu.addAction(act_del)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _duplicate_row(self, row: int):
        """Вставляет копию строки row сразу под ней."""
        col_count = self.table.columnCount()
        insert_at = row + 1
        self.table.insertRow(insert_at)

        for col in range(col_count):
            src_item = self.table.item(row, col)
            if src_item:
                new_item = QTableWidgetItem(src_item.text())
                new_item.setBackground(src_item.background())
                new_item.setForeground(src_item.foreground())
                new_item.setTextAlignment(src_item.textAlignment())
                self.table.setItem(insert_at, col, new_item)

        # Выделяем новую строку
        self.table.selectRow(insert_at)
        self._update_summary()

    def _delete_row(self, row: int):
        """Удаляет строку из таблицы с подтверждением."""
        reply = QMessageBox.question(
            self, "Удаление строки",
            "Удалить выбранную строку?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.table.removeRow(row)
            self._update_summary()

    # ------------------------------------------------------------------ #
    #  ОБРАБОТКА РЕДАКТИРОВАНИЯ ЯЧЕЙКИ
    # ------------------------------------------------------------------ #
    def _on_item_changed(self, item: QTableWidgetItem):
        """Обновляет цвет и выравнивание после ручного редактирования."""
        # Защита от рекурсии при programmatic setBackground/setForeground
        if self.table.signalsBlocked():
            return

        col_idx = item.column()
        col_name = self.table.horizontalHeaderItem(col_idx)
        if col_name is None:
            return
        col = col_name.text()

        self.table.blockSignals(True)

        # Пересчитываем цвет всей строки если изменилась Категория
        if col == "Категория":
            cat = item.text().strip()
            row_color = CATEGORY_COLORS.get(cat, QColor(55, 55, 60))
            for c in range(self.table.columnCount()):
                cell = self.table.item(item.row(), c)
                if cell:
                    cell.setBackground(row_color)

        # Цвет и выравнивание для числовых столбцов
        if col in ("Поступление", "Списание"):
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
            if item.text():
                color = QColor("#81d4a0") if col == "Поступление" else QColor("#ef9a9a")
                item.setForeground(color)
            else:
                item.setForeground(QColor("#cdd9e5"))

        self.table.blockSignals(False)
        self._update_summary()

    def _update_summary(self):
        """Пересчитывает итоги по текущему содержимому таблицы."""
        col_headers = {
            self.table.horizontalHeaderItem(c).text(): c
            for c in range(self.table.columnCount())
            if self.table.horizontalHeaderItem(c)
        }
        total_in  = 0.0
        total_out = 0.0
        rows = self.table.rowCount()

        for r in range(rows):
            for name, c in col_headers.items():
                cell = self.table.item(r, c)
                if cell:
                    raw = cell.text().replace(" ", "").replace("₽", "").replace(",", ".")
                    try:
                        val = float(raw)
                    except ValueError:
                        continue
                    if name == "Поступление":
                        total_in  += val
                    elif name == "Списание":
                        total_out += val

        self.lbl_income.setText(f"✅  Поступления: {total_in:,.2f} ₽".replace(",", " "))
        self.lbl_expense.setText(f"🔴  Списания: {total_out:,.2f} ₽".replace(",", " "))
        self.status_label.setText(f"Показано записей: {self.table.rowCount()}")


# ======================================================================= #
#  ВКЛАДКА СВОДКА
# ======================================================================= #

# Категории, которые считаем взносами
_CAT_VZNOSY = {
    "Членские взносы",
    "Членские взносы + Электроэнергия",
}
# Категории, которые считаем электроэнергией (от садоводов)
_CAT_ELECTRO = {
    "Электроэнергия (от садоводов)",
    "Членские взносы + Электроэнергия",
}

# Порядок участков для строк таблицы
_PLOT_ORDER = [str(i) for i in range(1, 51)] + ["205", "213", "214", "15/207", "15/208", "15/211"]


class SplitCellWidget(QWidget):
    """Виджет ячейки: левая часть — авто (текст), правая — редактируемое поле."""

    # Сигнал: пользователь изменил правую часть (план)
    edited = pyqtSignal(str, int, str)   # plot, year, value

    def __init__(self, auto_text: str, auto_color: str,
                 plan_value: str, plot: str, year: int,
                 editable: bool = True):
        super().__init__()
        self._plot = plot
        self._year = year

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Левая часть — факт (авто)
        self.lbl_auto = QLabel(auto_text)
        self.lbl_auto.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_auto.setStyleSheet(
            f"color: {auto_color}; background: transparent; font-size: 11px; padding: 0 4px;"
        )
        layout.addWidget(self.lbl_auto, stretch=1)

        # Разделитель
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #1e3a5f; background: #1e3a5f; max-width: 1px;")
        layout.addWidget(sep)

        # Правая часть — план (редактируемый)
        if editable:
            self.edit_plan = QLineEdit(plan_value)
            self.edit_plan.setPlaceholderText("план")
            self.edit_plan.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.edit_plan.setStyleSheet(
                "background: transparent; border: none; color: #64b5f6;"
                "font-size: 11px; padding: 0 4px;"
            )
            self.edit_plan.editingFinished.connect(self._on_edited)
            layout.addWidget(self.edit_plan, stretch=1)
        else:
            self.edit_plan = None
            lbl = QLabel(plan_value)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            lbl.setStyleSheet("color: #64b5f6; background: transparent; font-size: 11px; padding: 0 4px;")
            layout.addWidget(lbl, stretch=1)

    def _on_edited(self):
        val = self.edit_plan.text().strip()
        self.edited.emit(self._plot, self._year, val)


class SummaryWidget(QWidget):
    """Сводная таблица: участок × год → суммы взносов и электроэнергии."""

    # Файл для хранения плановых сумм
    PLAN_FILE = os.path.join(DATA_DIR, "snt_plan.json")

    def __init__(self, mode: str = "vznosy"):
        super().__init__()
        self._df    = None
        self._mode  = mode
        self._plan  = self._load_plan()   # {"plot:year": "сумма"}
        self._years : list = []
        self._plots : list = []
        self._setup_ui()

    # ── Сохранение/загрузка плана ─────────────────────────────────────────
    def _load_plan(self) -> dict:
        try:
            if os.path.exists(self.PLAN_FILE):
                with open(self.PLAN_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_plan(self):
        try:
            with open(self.PLAN_FILE, "w", encoding="utf-8") as f:
                json.dump(self._plan, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _plan_key(self, plot: str, year: int) -> str:
        return f"{plot}:{year}"

    def _on_plan_edited(self, plot: str, year: int, value: str):
        key = self._plan_key(plot, year)
        if value:
            self._plan[key] = value
        elif key in self._plan:
            del self._plan[key]
        self._save_plan()

    # ------------------------------------------------------------------ #
    #  UI
    # ------------------------------------------------------------------ #
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        # Заголовок — зависит от режима
        is_vznosy = self._mode == "vznosy"
        title_text = "📋  Членские взносы по участкам" if is_vznosy else "⚡  Электроэнергия по участкам"
        title = QLabel(title_text, objectName="pageTitle")
        layout.addWidget(title)

        # Легенда
        hint = QHBoxLayout()
        hint.setSpacing(24)
        legend_items = [
            ("#81d4a0", "■  Оплачено"),
            ("#c97c7c", "■  Смешанный платёж"),
            ("#3a5a7a", "■  Не платил"),
        ]
        if is_vznosy:
            legend_items.append(("#64b5f6", "  Правая ячейка — план (кликните для ввода)"))
        for color, text in legend_items:
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {color}; background: transparent; font-size: 11px;")
            hint.addWidget(lbl)
        hint.addStretch()
        layout.addLayout(hint)

        # Таблица
        self.table = QTableWidget()
        self.table.setObjectName("summaryTable")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        self.table.setShowGrid(True)
        layout.addWidget(self.table)

        self.status_lbl = QLabel(
            "Загрузите файл на вкладке «Детализация»", objectName="statusLabel"
        )
        layout.addWidget(self.status_lbl)

    # ------------------------------------------------------------------ #
    def refresh(self, df):
        self._df = df
        self._rebuild()

    # ------------------------------------------------------------------ #
    def _build_pivot(self):
        """Возвращает (pivot, mixed_pivot, years, all_plots) или None."""
        if self._df is None or self._df.empty:
            return None

        df = self._df.copy()
        df = df[df["Участок"].astype(str).str.strip() != ""]
        df = df[df["Категория"].astype(str).str.strip() != ""]

        cats = _CAT_VZNOSY if self._mode == "vznosy" else _CAT_ELECTRO
        df = df[df["Категория"].isin(cats)].copy()
        if df.empty:
            return None

        mixed_mask = df["Категория"] == "Членские взносы + Электроэнергия"
        df["_сумма"] = pd.to_numeric(df["Поступление"], errors="coerce").fillna(0)
        df.loc[mixed_mask, "_сумма"] /= 2
        df["_смешанный"] = mixed_mask
        df["_год"] = df["Дата"].dt.year

        rows_exp = []
        for _, row in df.iterrows():
            plots = [p.strip() for p in str(row["Участок"]).split(",") if p.strip()]
            for p in plots:
                r = row.copy()
                r["_участок"] = p
                r["_сумма"]   = row["_сумма"] / len(plots)
                rows_exp.append(r)

        if not rows_exp:
            return None

        exp = pd.DataFrame(rows_exp)
        pivot       = exp.groupby(["_участок","_год"])["_сумма"].sum().unstack(fill_value=0)
        mixed_pivot = exp.groupby(["_участок","_год"])["_смешанный"].any().unstack(fill_value=False)

        years     = sorted(pivot.columns.tolist())
        all_plots = [p for p in _PLOT_ORDER if p in pivot.index]
        extra     = [p for p in pivot.index if p not in all_plots]
        all_plots += sorted(extra, key=lambda x: (len(x), x))

        return pivot, mixed_pivot, years, all_plots

    # ------------------------------------------------------------------ #
    def _rebuild(self):
        if self._mode == "vznosy":
            self._rebuild_vznosy()
        else:
            self._rebuild_electro()

    # ------------------------------------------------------------------ #
    #  ВЗНОСЫ — оригинальная логика (факт | план)
    # ------------------------------------------------------------------ #
    def _rebuild_vznosy(self):
        result = self._build_pivot()
        if result is None:
            self.table.clearContents()
            self.table.setRowCount(0)
            self.status_lbl.setText("Нет данных для отображения")
            return

        pivot, mixed_pivot, years, all_plots = result
        self._years = years
        self._plots = all_plots

        n_rows = len(all_plots) + 2
        n_cols = 1 + len(years) + 1

        self.table.blockSignals(True)
        self.table.clearContents()
        self.table.setRowCount(n_rows)
        self.table.setColumnCount(n_cols)

        headers = ["Участок"] + [str(y) for y in years] + ["Итого"]
        self.table.setHorizontalHeaderLabels(headers)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 75)
        for c in range(1, n_cols):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)

        def _ro_cell(text, fg="#5a7fa0", bg="#080f18", bold=False):
            it = QTableWidgetItem(text)
            it.setBackground(QColor(bg)); it.setForeground(QColor(fg))
            f = it.font(); f.setBold(bold); it.setFont(f)
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            return it

        self.table.setItem(0, 0, _ro_cell(""))
        for c in range(1, n_cols):
            self.table.setItem(0, c, _ro_cell("факт  │  план", "#3a5a7a"))
        self.table.setRowHeight(0, 18)

        grand_fact = 0.0
        grand_plan_sum = 0.0

        for r_idx, plot in enumerate(all_plots, start=1):
            plot_item = QTableWidgetItem(f"уч. {plot}")
            plot_item.setBackground(QColor("#0a1520"))
            plot_item.setForeground(QColor("#90caf9"))
            f = plot_item.font(); f.setBold(True); plot_item.setFont(f)
            plot_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            plot_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(r_idx, 0, plot_item)

            plot_fact = 0.0
            plot_plan_sum = 0.0

            for c_idx, year in enumerate(years, start=1):
                amount   = float(pivot.loc[plot, year]) if plot in pivot.index and year in pivot.columns else 0.0
                is_mixed = bool(mixed_pivot.loc[plot, year]) if plot in mixed_pivot.index and year in mixed_pivot.columns else False
                plot_fact += amount

                if amount == 0:
                    auto_text, auto_color, bg_color = "—", "#2a4a6a", "#0a1118"
                elif is_mixed:
                    auto_text  = f"{amount:,.0f}".replace(",", " ")
                    auto_color, bg_color = "#c97c7c", "#150d0d"
                else:
                    auto_text  = f"{amount:,.0f}".replace(",", " ")
                    auto_color, bg_color = "#81d4a0", "#080f0b"

                plan_val = self._plan.get(self._plan_key(plot, year), "")
                try:
                    plot_plan_sum += float(plan_val.replace(" ", "").replace(",", "."))
                except Exception:
                    pass

                self.table.setItem(r_idx, c_idx, QTableWidgetItem())
                self.table.item(r_idx, c_idx).setBackground(QColor(bg_color))
                widget = SplitCellWidget(auto_text, auto_color, plan_val, plot, year, editable=True)
                widget.setStyleSheet(f"background-color: {bg_color};")
                widget.edited.connect(self._on_plan_edited)
                self.table.setCellWidget(r_idx, c_idx, widget)

            grand_fact     += plot_fact
            grand_plan_sum += plot_plan_sum

            fact_text  = f"{plot_fact:,.0f}".replace(",", " ") if plot_fact else "—"
            fact_color = "#81d4a0" if plot_fact else "#2a4a6a"
            plan_text  = f"{plot_plan_sum:,.0f}".replace(",", " ") if plot_plan_sum else ""
            tw = SplitCellWidget(fact_text, fact_color, plan_text, plot, 0, editable=False)
            tw.setStyleSheet("background-color: #0a1520;")
            self.table.setItem(r_idx, n_cols - 1, QTableWidgetItem())
            self.table.item(r_idx, n_cols - 1).setBackground(QColor("#0a1520"))
            self.table.setCellWidget(r_idx, n_cols - 1, tw)
            self.table.setRowHeight(r_idx, 32)

        total_row = n_rows - 1
        ti = QTableWidgetItem("ИТОГО")
        ti.setBackground(QColor("#060e16")); ti.setForeground(QColor("#64b5f6"))
        f = ti.font(); f.setBold(True); ti.setFont(f)
        ti.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        ti.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(total_row, 0, ti)

        grand_total = 0.0
        for c_idx, year in enumerate(years, start=1):
            yf = float(pivot[year].sum()) if year in pivot.columns else 0.0
            grand_total += yf
            yp = sum(
                float(self._plan.get(self._plan_key(p, year), "0").replace(" ", "").replace(",", "."))
                for p in all_plots if self._plan.get(self._plan_key(p, year))
            )
            ft = f"{yf:,.0f}".replace(",", " ") if yf else "—"
            pt = f"{yp:,.0f}".replace(",", " ") if yp else ""
            w  = SplitCellWidget(ft, "#64b5f6", pt, "__total__", year, editable=False)
            w.setStyleSheet("background-color: #060e16;")
            self.table.setItem(total_row, c_idx, QTableWidgetItem())
            self.table.item(total_row, c_idx).setBackground(QColor("#060e16"))
            self.table.setCellWidget(total_row, c_idx, w)

        gft = f"{grand_fact:,.0f}".replace(",", " ")
        gpt = f"{grand_plan_sum:,.0f}".replace(",", " ") if grand_plan_sum else ""
        gw  = SplitCellWidget(gft, "#64b5f6", gpt, "__total__", 0, editable=False)
        gw.setStyleSheet("background-color: #060e16;")
        self.table.setItem(total_row, n_cols - 1, QTableWidgetItem())
        self.table.item(total_row, n_cols - 1).setBackground(QColor("#060e16"))
        self.table.setCellWidget(total_row, n_cols - 1, gw)
        self.table.setRowHeight(total_row, 34)

        self.table.blockSignals(False)
        self.status_lbl.setText(
            f"Членские взносы  ·  участков: {len(all_plots)}  ·  лет: {len(years)}  ·  "
            f"итого факт: {grand_fact:,.0f} ₽".replace(",", " ")
        )

    # ------------------------------------------------------------------ #
    #  ЭЛЕКТРОЭНЕРГИЯ — с разворачиваемыми годами по месяцам
    # ------------------------------------------------------------------ #

    MONTH_NAMES = ["янв", "фев", "мар", "апр", "май", "июн",
                   "июл", "авг", "сен", "окт", "ноя", "дек"]

    def _rebuild_electro(self):
        """Таблица электроэнергии: год можно развернуть в 12 месяцев."""
        result = self._build_pivot()
        if result is None:
            self.table.clearContents()
            self.table.setRowCount(0)
            self.status_lbl.setText("Нет данных для отображения")
            return

        # Нам нужен помесячный pivot
        df = self._df.copy()
        df = df[df["Участок"].astype(str).str.strip() != ""]
        cats = _CAT_ELECTRO
        df = df[df["Категория"].isin(cats)].copy()
        mixed_mask = df["Категория"] == "Членские взносы + Электроэнергия"
        df["_сумма"] = pd.to_numeric(df["Поступление"], errors="coerce").fillna(0)
        df.loc[mixed_mask, "_сумма"] /= 2
        df["_год"]   = df["Дата"].dt.year
        df["_месяц"] = df["Дата"].dt.month

        rows_exp = []
        for _, row in df.iterrows():
            plots = [p.strip() for p in str(row["Участок"]).split(",") if p.strip()]
            for p in plots:
                r = row.copy(); r["_участок"] = p
                r["_сумма"] = row["_сумма"] / len(plots)
                rows_exp.append(r)

        if not rows_exp:
            self.table.clearContents()
            self.table.setRowCount(0)
            self.status_lbl.setText("Нет данных для отображения")
            return

        exp = pd.DataFrame(rows_exp)
        _, _, years, all_plots = result   # all_plots и years из _build_pivot

        # pivot по году
        piv_year = exp.groupby(["_участок", "_год"])["_сумма"].sum().unstack(fill_value=0)
        # pivot по (год, месяц)
        piv_mon  = exp.groupby(["_участок", "_год", "_месяц"])["_сумма"].sum().unstack(fill_value=0)

        self._years = years
        self._plots = all_plots

        # ── Строим список колонок с учётом expanded ───────────────────────
        # _expanded_years: set лет, которые развёрнуты
        if not hasattr(self, "_expanded_years"):
            self._expanded_years = set()

        # col_defs: список (kind, year, month|None)
        #   kind = "year_header" | "month"
        col_defs = []  # (kind, year, month)
        for year in years:
            col_defs.append(("year_header", year, None))
            if year in self._expanded_years:
                months_present = sorted(
                    exp[exp["_год"] == year]["_месяц"].unique()
                )
                for m in months_present:
                    col_defs.append(("month", year, m))

        n_data_cols = len(col_defs)
        n_cols = 1 + n_data_cols + 1   # участок + данные + итого
        n_rows = len(all_plots) + 2    # подсказка + участки + итого

        self.table.blockSignals(True)
        self.table.clearContents()
        self.table.setRowCount(n_rows)
        self.table.setColumnCount(n_cols)

        # ── Заголовки ─────────────────────────────────────────────────────
        hdr_labels = ["Участок"]
        for kind, year, month in col_defs:
            if kind == "year_header":
                hdr_labels.append(str(year))
            else:
                hdr_labels.append(self.MONTH_NAMES[month - 1])
        hdr_labels.append("Итого")
        self.table.setHorizontalHeaderLabels(hdr_labels)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 75)
        for c_idx, (kind, year, month) in enumerate(col_defs, start=1):
            if kind == "year_header":
                hdr.setSectionResizeMode(c_idx, QHeaderView.ResizeMode.Interactive)
                self.table.setColumnWidth(c_idx, 110)
            else:
                hdr.setSectionResizeMode(c_idx, QHeaderView.ResizeMode.Interactive)
                self.table.setColumnWidth(c_idx, 70)
        hdr.setSectionResizeMode(n_cols - 1, QHeaderView.ResizeMode.Stretch)

        # ── Строка 0: кнопки +/− на годовых колонках ─────────────────────
        self.table.setItem(0, 0, QTableWidgetItem())
        self.table.item(0, 0).setBackground(QColor("#080f18"))
        self.table.item(0, 0).setFlags(Qt.ItemFlag.NoItemFlags)

        for c_idx, (kind, year, month) in enumerate(col_defs, start=1):
            if kind == "year_header":
                expanded = year in self._expanded_years
                btn = QPushButton("−" if expanded else "+")
                btn.setFixedSize(22, 22)
                btn.setStyleSheet("""
                    QPushButton {
                        background: #1565c0; color: white; border-radius: 4px;
                        font-size: 14px; font-weight: bold; padding: 0;
                    }
                    QPushButton:hover { background: #1976d2; }
                    QPushButton:pressed { background: #0d47a1; }
                """)
                btn.clicked.connect(lambda checked, y=year: self._toggle_year(y))

                container = QWidget()
                container.setStyleSheet("background: #080f18;")
                lay = QHBoxLayout(container)
                lay.setContentsMargins(4, 2, 4, 2)
                lay.addStretch()
                lay.addWidget(btn)
                lay.addStretch()
                self.table.setCellWidget(0, c_idx, container)
            else:
                # Заголовок месяца — подкрашенный
                it = QTableWidgetItem(self.MONTH_NAMES[month - 1].upper())
                it.setBackground(QColor("#0a1828"))
                it.setForeground(QColor("#5a8ab0"))
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                it.setFlags(Qt.ItemFlag.NoItemFlags)
                self.table.setItem(0, c_idx, it)

        it_tot = QTableWidgetItem("")
        it_tot.setBackground(QColor("#060e16"))
        it_tot.setFlags(Qt.ItemFlag.NoItemFlags)
        self.table.setItem(0, n_cols - 1, it_tot)
        self.table.setRowHeight(0, 26)

        # ── Строки участков ───────────────────────────────────────────────
        grand_fact = 0.0

        for r_idx, plot in enumerate(all_plots, start=1):
            # Столбец «Участок»
            pi = QTableWidgetItem(f"уч. {plot}")
            pi.setBackground(QColor("#0a1520")); pi.setForeground(QColor("#90caf9"))
            f = pi.font(); f.setBold(True); pi.setFont(f)
            pi.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            pi.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(r_idx, 0, pi)

            plot_fact = 0.0

            for c_idx, (kind, year, month) in enumerate(col_defs, start=1):
                if kind == "year_header":
                    amount = float(piv_year.loc[plot, year]) if plot in piv_year.index and year in piv_year.columns else 0.0
                    plot_fact += amount
                    if amount == 0:
                        text, fg, bg = "—", "#2a4a6a", "#0a1118"
                    else:
                        text = f"{amount:,.0f}".replace(",", " ")
                        fg, bg = "#81d4a0", "#080f0b"
                    it = QTableWidgetItem(text)
                    it.setBackground(QColor(bg)); it.setForeground(QColor(fg))
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                    it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    f2 = it.font(); f2.setBold(True); it.setFont(f2)
                    self.table.setItem(r_idx, c_idx, it)
                else:
                    # месячная ячейка
                    try:
                        amount = float(piv_mon.loc[(plot, year), month]) if (plot, year) in piv_mon.index and month in piv_mon.columns else 0.0
                    except Exception:
                        amount = 0.0
                    if amount == 0:
                        text, fg, bg = "—", "#1e3a5a", "#080e14"
                    else:
                        text = f"{amount:,.0f}".replace(",", " ")
                        fg, bg = "#64b5f6", "#080d14"
                    it = QTableWidgetItem(text)
                    it.setBackground(QColor(bg)); it.setForeground(QColor(fg))
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                    it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self.table.setItem(r_idx, c_idx, it)

            grand_fact += plot_fact

            # Итого по участку
            tot_amount = float(piv_year.loc[plot].sum()) if plot in piv_year.index else 0.0
            tot_text  = f"{tot_amount:,.0f}".replace(",", " ") if tot_amount else "—"
            tot_color = "#81d4a0" if tot_amount else "#2a4a6a"
            ti = QTableWidgetItem(tot_text)
            ti.setBackground(QColor("#0a1520")); ti.setForeground(QColor(tot_color))
            f3 = ti.font(); f3.setBold(True); ti.setFont(f3)
            ti.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            ti.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(r_idx, n_cols - 1, ti)
            self.table.setRowHeight(r_idx, 28)

        # ── Строка ИТОГО ──────────────────────────────────────────────────
        total_row = n_rows - 1
        ti0 = QTableWidgetItem("ИТОГО")
        ti0.setBackground(QColor("#060e16")); ti0.setForeground(QColor("#64b5f6"))
        f4 = ti0.font(); f4.setBold(True); ti0.setFont(f4)
        ti0.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        ti0.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(total_row, 0, ti0)

        grand_total = 0.0
        for c_idx, (kind, year, month) in enumerate(col_defs, start=1):
            if kind == "year_header":
                yf = float(piv_year[year].sum()) if year in piv_year.columns else 0.0
                grand_total += yf
                txt = f"{yf:,.0f}".replace(",", " ") if yf else "—"
            else:
                mf = float(piv_mon.xs(year, level="_год")[month].sum()) if year in piv_mon.index.get_level_values("_год") and month in piv_mon.columns else 0.0
                txt = f"{mf:,.0f}".replace(",", " ") if mf else "—"
            tit = QTableWidgetItem(txt)
            tit.setBackground(QColor("#060e16")); tit.setForeground(QColor("#64b5f6"))
            f5 = tit.font(); f5.setBold(True); tit.setFont(f5)
            tit.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            tit.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(total_row, c_idx, tit)

        gt_it = QTableWidgetItem(f"{grand_fact:,.0f}".replace(",", " "))
        gt_it.setBackground(QColor("#060e16")); gt_it.setForeground(QColor("#64b5f6"))
        f6 = gt_it.font(); f6.setBold(True); gt_it.setFont(f6)
        gt_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        gt_it.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(total_row, n_cols - 1, gt_it)
        self.table.setRowHeight(total_row, 30)

        self.table.blockSignals(False)
        self.status_lbl.setText(
            f"Электроэнергия  ·  участков: {len(all_plots)}  ·  лет: {len(years)}  ·  "
            f"итого: {grand_fact:,.0f} ₽".replace(",", " ")
        )

    def _toggle_year(self, year: int):
        """Разворачивает/сворачивает год в таблице электроэнергии."""
        if year in self._expanded_years:
            self._expanded_years.discard(year)
        else:
            self._expanded_years.add(year)
        self._rebuild_electro()



# ======================================================================= #
#  ВКЛАДКА «ПЕРЕДАЧА ПОКАЗАНИЙ»
# ======================================================================= #

class MeterCellWidget(QWidget):
    """Ячейка: поле ввода показания + кнопка фото."""

    photo_changed = pyqtSignal(str, int, int)   # plot, year, month

    def __init__(self, plot: str, year: int, month: int,
                 value: str = "", has_photo: bool = False):
        super().__init__()
        self._plot = plot
        self._year = year
        self._month = month
        self._anomaly: str | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 1, 3, 1)
        lay.setSpacing(4)

        self.edit = QLineEdit(value)
        self.edit.setPlaceholderText("показ.")
        self.edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._normal_edit_style = (
            "background: transparent; border: none; color: #cdd9e5;"
            "font-size: 11px;"
        )
        self.edit.setStyleSheet(self._normal_edit_style)
        lay.addWidget(self.edit, stretch=1)

        self.btn_photo = QPushButton("📎" if not has_photo else "🖼")
        self.btn_photo.setFixedSize(24, 24)
        self._set_photo_style(has_photo)
        self.btn_photo.clicked.connect(self._on_photo_click)
        lay.addWidget(self.btn_photo)

    def set_anomaly(self, kind: str | None, detail: str = ""):
        """kind: 'drop' | 'spike' | None"""
        self._anomaly = kind
        if kind == "drop":
            self.edit.setStyleSheet(
                "background:#2a0d0d;border:1px solid #c62828;border-radius:3px;"
                "color:#ef9a9a;font-size:11px;font-weight:700;"
            )
            self.setToolTip(detail or "Показание меньше предыдущего")
        elif kind == "spike":
            self.edit.setStyleSheet(
                "background:#2a1f0d;border:1px solid #f9a825;border-radius:3px;"
                "color:#ffd54f;font-size:11px;"
            )
            self.setToolTip(detail or "Расход существенно больше обычного")
        else:
            self.edit.setStyleSheet(self._normal_edit_style)
            self.setToolTip("")

    def _set_photo_style(self, has_photo: bool):
        if has_photo:
            style = ("QPushButton{background:#0d3b1a;border:1px solid #2e7d32;"
                     "border-radius:4px;font-size:13px;}"
                     "QPushButton:hover{background:#1b5e20;}")
        else:
            style = ("QPushButton{background:#162131;border:1px solid #2a4a6b;"
                     "border-radius:4px;font-size:13px;}"
                     "QPushButton:hover{background:#1e3a5f;}")
        self.btn_photo.setStyleSheet(style)

    def set_has_photo(self, has_photo: bool):
        self.btn_photo.setText("🖼" if has_photo else "📎")
        self._set_photo_style(has_photo)

    def _on_photo_click(self):
        self.photo_changed.emit(self._plot, self._year, self._month)

    def get_value(self) -> str:
        return self.edit.text().strip()


class MeterWidget(QWidget):
    """Вкладка передачи показаний счётчиков."""

    DATA_FILE  = os.path.join(DATA_DIR, "snt_meters.json")
    PHOTO_DIR  = os.path.join(DATA_DIR, "snt_photos")
    MONTH_NAMES = ["янв", "фев", "мар", "апр", "май", "июн",
                   "июл", "авг", "сен", "окт", "ноя", "дек"]

    def __init__(self):
        super().__init__()
        self._data: dict = self._load_data()   # {"plot:year:month": value}
        self._photos: dict = self._load_photos()  # {"plot:year:month": filepath}
        self._expanded_years: set = set()
        self._years: list = []
        self._active_years: set = self._load_active_years()
        self._plots: list = list(map(str, range(1, 51))) + \
                            ["205", "213", "214", "15/207", "15/208", "15/211"]
        self._cell_widgets: dict = {}   # key → MeterCellWidget
        self._setup_ui()
        self._rebuild()

    # ── Персистентность ──────────────────────────────────────────────────
    def _load_data(self) -> dict:
        try:
            if os.path.exists(self.DATA_FILE):
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_data(self):
        try:
            with open(self.DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    _YEARS_FILE = os.path.join(DATA_DIR, "snt_meters_years.json")

    def _load_active_years(self) -> set:
        import datetime
        try:
            if os.path.exists(self._YEARS_FILE):
                with open(self._YEARS_FILE, "r", encoding="utf-8") as f:
                    return set(json.load(f))
        except Exception:
            pass
        return {datetime.date.today().year}

    def _save_active_years(self):
        try:
            with open(self._YEARS_FILE, "w", encoding="utf-8") as f:
                json.dump(sorted(self._active_years), f, ensure_ascii=False)
        except Exception:
            pass

    def _load_photos(self) -> dict:
        try:
            pf = os.path.join(self.PHOTO_DIR, "_index.json")
            if os.path.exists(pf):
                with open(pf, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_photos(self):
        try:
            os.makedirs(self.PHOTO_DIR, exist_ok=True)
            pf = os.path.join(self.PHOTO_DIR, "_index.json")
            with open(pf, "w", encoding="utf-8") as f:
                json.dump(self._photos, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _cell_key(self, plot: str, year: int, month: int) -> str:
        return f"{plot}:{year}:{month}"

    # ── UI ───────────────────────────────────────────────────────────────
    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(14)

        top = QHBoxLayout()
        title = QLabel("Передача показаний счётчиков", objectName="pageTitle")
        top.addWidget(title)
        top.addStretch()

        btn_add_year = QPushButton("＋  Добавить год")
        btn_add_year.setObjectName("btnSecondary")
        btn_add_year.clicked.connect(self._add_year)
        top.addWidget(btn_add_year)

        btn_replace = QPushButton("🔧  Замена счётчика")
        btn_replace.setObjectName("btnSecondary")
        btn_replace.clicked.connect(self._register_replacement)
        top.addWidget(btn_replace)

        btn_save = QPushButton("💾  Сохранить всё")
        btn_save.setObjectName("btnPrimary")
        btn_save.clicked.connect(self._save_all)
        top.addWidget(btn_save)
        lay.addLayout(top)

        # Легенда
        hint = QHBoxLayout()
        hint.setSpacing(20)
        for color, text in [
            ("#cdd9e5", "  📎 — нет фото   🖼 — фото прикреплено"),
            ("#2e7d32", "■  ячейка с фото"),
            ("#ef9a9a", "■  показание < предыдущего"),
            ("#ffd54f", "■  аномально большой расход"),
        ]:
            lb = QLabel(text)
            lb.setStyleSheet(f"color: {color}; background: transparent; font-size: 11px;")
            hint.addWidget(lb)
        hint.addStretch()
        lay.addLayout(hint)

        self.table = QTableWidget()
        self.table.setObjectName("summaryTable")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        lay.addWidget(self.table)

        self.status_lbl = QLabel("", objectName="statusLabel")
        lay.addWidget(self.status_lbl)

    # ── Построение таблицы ────────────────────────────────────────────────
    def _get_years(self) -> list:
        """Определяем годы: явно добавленные + из данных/фото + текущий."""
        years_set = set(self._active_years)
        for key in self._data:
            parts = key.split(":")
            if len(parts) >= 2:
                try:
                    years_set.add(int(parts[1]))
                except Exception:
                    pass
        for key in self._photos:
            parts = key.split(":")
            if len(parts) >= 2:
                try:
                    years_set.add(int(parts[1]))
                except Exception:
                    pass
        import datetime
        years_set.add(datetime.date.today().year)
        return sorted(years_set)

    def _rebuild(self):
        self._save_all_cells()   # сохраняем текущие значения перед перестройкой
        self._cell_widgets.clear()

        years = self._get_years()
        self._years = years
        all_plots = self._plots

        # col_defs: ("year_header", year) | ("month", year, month)
        col_defs = []
        for year in years:
            col_defs.append(("year_header", year, None))
            if year in self._expanded_years:
                for m in range(1, 13):
                    col_defs.append(("month", year, m))

        n_cols = 1 + len(col_defs)
        n_rows = 1 + len(all_plots)   # строка кнопок + участки

        self.table.blockSignals(True)
        self.table.clearContents()
        self.table.setRowCount(n_rows)
        self.table.setColumnCount(n_cols)

        # Заголовки
        hdr_labels = ["Участок"]
        for kind, year, month in col_defs:
            hdr_labels.append(str(year) if kind == "year_header" else self.MONTH_NAMES[month - 1])
        self.table.setHorizontalHeaderLabels(hdr_labels)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 75)
        for c_idx, (kind, year, month) in enumerate(col_defs, start=1):
            hdr.setSectionResizeMode(c_idx, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(c_idx, 120 if kind == "year_header" else 95)

        # Строка 0 — кнопки +/−
        it0 = QTableWidgetItem("")
        it0.setBackground(QColor("#080f18"))
        it0.setFlags(Qt.ItemFlag.NoItemFlags)
        self.table.setItem(0, 0, it0)

        for c_idx, (kind, year, month) in enumerate(col_defs, start=1):
            if kind == "year_header":
                expanded = year in self._expanded_years
                btn = QPushButton("−" if expanded else "+")
                btn.setFixedSize(22, 22)
                btn.setStyleSheet(
                    "QPushButton{background:#1565c0;color:white;border-radius:4px;"
                    "font-size:14px;font-weight:bold;padding:0;}"
                    "QPushButton:hover{background:#1976d2;}"
                )
                btn.clicked.connect(lambda _, y=year: self._toggle_year(y))
                cont = QWidget()
                cont.setStyleSheet("background:#080f18;")
                cl = QHBoxLayout(cont)
                cl.setContentsMargins(4, 2, 4, 2)
                cl.addStretch(); cl.addWidget(btn); cl.addStretch()
                self.table.setCellWidget(0, c_idx, cont)
            else:
                it = QTableWidgetItem(self.MONTH_NAMES[month - 1].upper())
                it.setBackground(QColor("#0a1828"))
                it.setForeground(QColor("#5a8ab0"))
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                it.setFlags(Qt.ItemFlag.NoItemFlags)
                self.table.setItem(0, c_idx, it)
        self.table.setRowHeight(0, 26)

        # Строки участков
        for r_idx, plot in enumerate(all_plots, start=1):
            pi = QTableWidgetItem(f"уч. {plot}")
            pi.setBackground(QColor("#0a1520"))
            pi.setForeground(QColor("#90caf9"))
            f = pi.font(); f.setBold(True); pi.setFont(f)
            pi.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            pi.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(r_idx, 0, pi)

            for c_idx, (kind, year, month) in enumerate(col_defs, start=1):
                if kind == "year_header":
                    # Годовая ячейка — показываем количество заполненных месяцев
                    filled = sum(
                        1 for m in range(1, 13)
                        if self._data.get(self._cell_key(plot, year, m), "").strip()
                    )
                    photos = sum(
                        1 for m in range(1, 13)
                        if self._photos.get(self._cell_key(plot, year, m))
                    )
                    text = f"{filled}/12 мес." if filled else "—"
                    bg = "#0a1e12" if filled else "#0a1118"
                    fg = "#81d4a0" if filled else "#2a4a6a"
                    it = QTableWidgetItem(text)
                    if photos:
                        it.setText(f"{text}  🖼{photos}")
                    it.setBackground(QColor(bg))
                    it.setForeground(QColor(fg))
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                    it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self.table.setItem(r_idx, c_idx, it)
                else:
                    # Месячная ячейка — редактируемый виджет
                    key = self._cell_key(plot, year, month)
                    val = self._data.get(key, "")
                    has_photo = bool(self._photos.get(key))
                    bg = "#0a1e12" if has_photo else "#080e14"

                    w = MeterCellWidget(plot, year, month, val, has_photo)
                    w.setStyleSheet(f"background-color: {bg};")
                    w.photo_changed.connect(self._on_photo_click)
                    self._cell_widgets[key] = w

                    self.table.setItem(r_idx, c_idx, QTableWidgetItem())
                    self.table.item(r_idx, c_idx).setBackground(QColor(bg))
                    self.table.setCellWidget(r_idx, c_idx, w)

            self.table.setRowHeight(r_idx, 30)

        self.table.blockSignals(False)

        self._apply_anomalies()

        total_filled = sum(1 for v in self._data.values() if v.strip())
        total_photos = len(self._photos)
        self.status_lbl.setText(
            f"Заполнено ячеек: {total_filled}  ·  прикреплено фото: {total_photos}"
        )

    def _apply_anomalies(self):
        """Прогоняет energy.anomalies для каждого участка и подсвечивает ячейки."""
        replacements = energy.load_replacements()
        anomaly_count = 0
        for plot in self._plots:
            for a in energy.anomalies(plot, self._data, replacements):
                if a.type == "gap":
                    continue
                key = self._cell_key(plot, a.year, a.month)
                w = self._cell_widgets.get(key)
                if w is not None:
                    w.set_anomaly(a.type, a.detail)
                    anomaly_count += 1

    def _register_replacement(self):
        # Выбор участка
        plot, ok = QInputDialog.getItem(
            self, "Замена счётчика",
            "На каком участке заменили счётчик?",
            self._plots, 0, False,
        )
        if not ok or not plot:
            return
        readings = energy.plot_readings(plot, self._data)
        last_val = readings[-1][2] if readings else None
        dlg = MeterReplacementDialog(plot, self, prev_value=last_val)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        result = dlg.get_result()
        if not result:
            return
        repls = energy.load_replacements()
        repls.setdefault(str(plot), []).append(result)
        repls[str(plot)].sort(key=lambda r: r.get("date", ""))
        energy.save_replacements(repls)
        self.status_lbl.setText(
            f"✅  Замена счётчика на уч. {plot} от {result['date']} сохранена"
        )
        self._rebuild()

    def _add_year(self):
        import datetime
        current = datetime.date.today().year
        year, ok = QInputDialog.getInt(
            self, "Добавить год", "Введите год:",
            value=current - 1, min=2000, max=current + 1, step=1
        )
        if ok:
            self._active_years.add(year)
            self._save_active_years()
            self._rebuild()

    def _toggle_year(self, year: int):
        self._save_all_cells()
        if year in self._expanded_years:
            self._expanded_years.discard(year)
        else:
            self._expanded_years.add(year)
        self._rebuild()

    # ── Сохранение ───────────────────────────────────────────────────────
    def _save_all_cells(self):
        """Считывает значения из всех активных виджетов и пишет в _data."""
        for key, w in self._cell_widgets.items():
            val = w.get_value()
            if val:
                self._data[key] = val
            elif key in self._data:
                del self._data[key]

    def _save_all(self):
        self._save_all_cells()
        self._save_data()
        self.status_lbl.setText("✅  Данные сохранены")

    # ── Работа с фото ────────────────────────────────────────────────────
    def _on_photo_click(self, plot: str, year: int, month: int):
        key = self._cell_key(plot, year, month)
        has_photo = bool(self._photos.get(key))

        action_text = "Заменить фото" if has_photo else "Прикрепить фото"
        path, _ = QFileDialog.getOpenFileName(
            self, action_text, "",
            "Изображения (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"
        )
        if not path:
            return

        # Копируем файл в папку snt_photos
        os.makedirs(self.PHOTO_DIR, exist_ok=True)
        ext = os.path.splitext(path)[1].lower()
        dest_name = f"{plot}_{year}_{month:02d}{ext}".replace("/", "-")
        dest_path = os.path.join(self.PHOTO_DIR, dest_name)
        shutil.copy2(path, dest_path)

        self._photos[key] = dest_path
        self._save_photos()

        # Обновляем кнопку в виджете
        if key in self._cell_widgets:
            self._cell_widgets[key].set_has_photo(True)
            self._cell_widgets[key].setStyleSheet("background-color: #0a1e12;")
            # Обновляем фон ячейки в таблице
            for r in range(self.table.rowCount()):
                for c in range(self.table.columnCount()):
                    if self.table.cellWidget(r, c) is self._cell_widgets[key]:
                        self.table.item(r, c).setBackground(QColor("#0a1e12"))

        self.status_lbl.setText(f"✅  Фото прикреплено: уч. {plot} {self.MONTH_NAMES[month-1]} {year}")


# ======================================================================= #
#  ВКЛАДКА «НОРМАТИВЫ»
# ======================================================================= #

class RatesWidget(QWidget):
    """Вкладка тарифов на электроэнергию (₽/кВт·ч)."""

    DATA_FILE = os.path.join(DATA_DIR, "snt_rates.json")

    def __init__(self):
        super().__init__()
        self._rates: list = self._load()  # [{"date": "YYYY-MM-DD", "rate": "X.XX", "note": "..."}]
        self._setup_ui()
        self._rebuild_table()

    def _load(self) -> list:
        try:
            if os.path.exists(self.DATA_FILE):
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        # Стартовые тарифы для СНТ (можно удалить)
        return []

    def _save(self):
        try:
            with open(self.DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self._rates, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        # Заголовок
        top = QHBoxLayout()
        title = QLabel("Нормативы (тарифы на электроэнергию)", objectName="pageTitle")
        top.addWidget(title)
        top.addStretch()
        btn_add = QPushButton("＋  Добавить тариф")
        btn_add.setObjectName("btnPrimary")
        btn_add.clicked.connect(self._add_rate)
        top.addWidget(btn_add)
        lay.addLayout(top)

        hint = QLabel(
            "Двойной клик по ячейке — редактировать.  "
            "ПКМ по строке — удалить.  "
            "Записи отсортированы по дате (новые сверху)."
        )
        hint.setStyleSheet("color: #5a7fa0; background: transparent; font-size: 11px;")
        lay.addWidget(hint)

        # Форма добавления (скрыта по умолчанию)
        self.form_frame = QFrame()
        self.form_frame.setObjectName("filterFrame")
        self.form_frame.setVisible(False)
        form_lay = QHBoxLayout(self.form_frame)
        form_lay.setContentsMargins(16, 12, 16, 12)
        form_lay.setSpacing(12)

        form_lay.addWidget(QLabel("Дата:", objectName="filterLabel"))
        self.inp_date = QDateEdit()
        self.inp_date.setObjectName("datePicker")
        self.inp_date.setCalendarPopup(True)
        self.inp_date.setDate(QDate.currentDate())
        self.inp_date.setDisplayFormat("dd.MM.yyyy")
        form_lay.addWidget(self.inp_date)

        form_lay.addWidget(QLabel("₽/кВт·ч:", objectName="filterLabel"))
        self.inp_rate = QLineEdit()
        self.inp_rate.setObjectName("searchInput")
        self.inp_rate.setPlaceholderText("например: 4.50")
        self.inp_rate.setFixedWidth(120)
        form_lay.addWidget(self.inp_rate)

        form_lay.addWidget(QLabel("Примечание:", objectName="filterLabel"))
        self.inp_note = QLineEdit()
        self.inp_note.setObjectName("searchInput")
        self.inp_note.setPlaceholderText("необязательно")
        form_lay.addWidget(self.inp_note, stretch=1)

        btn_ok = QPushButton("✓  Добавить")
        btn_ok.setObjectName("btnPrimary")
        btn_ok.clicked.connect(self._confirm_add)
        form_lay.addWidget(btn_ok)

        btn_cancel = QPushButton("✕")
        btn_cancel.setObjectName("btnSecondary")
        btn_cancel.clicked.connect(lambda: self.form_frame.setVisible(False))
        form_lay.addWidget(btn_cancel)

        lay.addWidget(self.form_frame)

        # Таблица
        self.table = QTableWidget()
        self.table.setObjectName("summaryTable")
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Дата вступления в силу", "Тариф (₽/кВт·ч)", "Примечание"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 180)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.itemChanged.connect(self._on_cell_edited)
        lay.addWidget(self.table)

        self.status_lbl = QLabel("", objectName="statusLabel")
        lay.addWidget(self.status_lbl)

    def _rebuild_table(self):
        self.table.blockSignals(True)
        self.table.clearContents()

        # Сортировка: новые сверху
        rates = sorted(self._rates, key=lambda r: r.get("date", ""), reverse=True)

        self.table.setRowCount(len(rates))
        for r_idx, entry in enumerate(rates):
            # Дата
            raw_date = entry.get("date", "")
            try:
                from datetime import datetime
                d = datetime.strptime(raw_date, "%Y-%m-%d")
                display_date = d.strftime("%d.%m.%Y")
            except Exception:
                display_date = raw_date

            # Цвет: первая строка (самый актуальный тариф) — выделена
            is_current = (r_idx == 0)
            bg = "#0d2a0d" if is_current else "#0a1520"
            fg_date = "#81d4a0" if is_current else "#cdd9e5"

            for c_idx, (text, fg) in enumerate([
                (display_date,           fg_date),
                (entry.get("rate", ""), "#64b5f6" if is_current else "#cdd9e5"),
                (entry.get("note", ""), "#7a9bb8"),
            ]):
                it = QTableWidgetItem(text)
                it.setBackground(QColor(bg))
                it.setForeground(QColor(fg))
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                # Дата — не редактируем напрямую (только через форму добавления)
                if c_idx == 0:
                    it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(r_idx, c_idx, it)

            self.table.setRowHeight(r_idx, 34)

        self.table.blockSignals(False)

        current = rates[0] if rates else None
        if current:
            self.status_lbl.setText(
                f"Актуальный тариф: {current.get('rate', '?')} ₽/кВт·ч  "
                f"(с {rates[0].get('date', '?')})  ·  всего записей: {len(rates)}"
            )
        else:
            self.status_lbl.setText("Нет записей — добавьте первый тариф")

    def _add_rate(self):
        self.inp_rate.clear()
        self.inp_note.clear()
        self.form_frame.setVisible(True)
        self.inp_rate.setFocus()

    def _confirm_add(self):
        rate_text = self.inp_rate.text().strip().replace(",", ".")
        if not rate_text:
            self.inp_rate.setFocus()
            return
        try:
            float(rate_text)
        except ValueError:
            self.inp_rate.setStyleSheet(self.inp_rate.styleSheet() + "border:1px solid #c62828;")
            return

        date_str = self.inp_date.date().toString("yyyy-MM-dd")
        entry = {"date": date_str, "rate": rate_text, "note": self.inp_note.text().strip()}
        self._rates.append(entry)
        self._save()
        self.form_frame.setVisible(False)
        self._rebuild_table()

    def _on_cell_edited(self, item: QTableWidgetItem):
        if self.table.signalsBlocked():
            return
        # Пересчитываем индекс в отсортированном списке
        rates_sorted = sorted(self._rates, key=lambda r: r.get("date", ""), reverse=True)
        r_idx = item.row()
        if r_idx >= len(rates_sorted):
            return
        col = item.column()
        val = item.text().strip()
        entry = rates_sorted[r_idx]
        # Находим оригинальную запись в self._rates
        orig_idx = next(
            (i for i, e in enumerate(self._rates) if e is entry), None
        )
        if orig_idx is None:
            return
        if col == 1:
            self._rates[orig_idx]["rate"] = val.replace(",", ".")
        elif col == 2:
            self._rates[orig_idx]["note"] = val
        self._save()
        self._rebuild_table()

    def _context_menu(self, pos: QPoint):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu{background:#0d1b2a;border:1px solid #2a4a6b;color:#cdd9e5;
                  font-size:13px;padding:4px;}
            QMenu::item{padding:8px 20px;border-radius:4px;}
            QMenu::item:selected{background:#1a2e45;color:#ef9a9a;}
        """)
        act_del = QAction("🗑️  Удалить запись", self)
        act_del.triggered.connect(lambda: self._delete_rate(row))
        menu.addAction(act_del)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _delete_rate(self, row: int):
        rates_sorted = sorted(self._rates, key=lambda r: r.get("date", ""), reverse=True)
        if row >= len(rates_sorted):
            return
        entry = rates_sorted[row]
        reply = QMessageBox.question(
            self, "Удаление тарифа",
            f"Удалить запись от {entry.get('date', '')} ({entry.get('rate', '')} ₽/кВт·ч)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._rates = [e for e in self._rates if e is not entry]
            self._save()
            self._rebuild_table()


class PlotsWidget(QWidget):
    """Вкладка участков: ручное добавление и управление списком."""

    DATA_FILE = os.path.join(DATA_DIR, "snt_plots.json")

    def __init__(self):
        super().__init__()
        self._plots: list = self._load()  # [{"num": "15", "owners": ["Иван Петров", ...]}, ...]
        self._setup_ui()
        self._rebuild_table()

    def _load(self) -> list:
        try:
            if os.path.exists(self.DATA_FILE):
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return []

    def _save(self):
        try:
            with open(self.DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self._plots, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        # Заголовок + кнопка
        top = QHBoxLayout()
        title = QLabel("Участки")
        title.setObjectName("pageTitle")
        top.addWidget(title)
        top.addStretch()
        btn_add = QPushButton("＋  Добавить участок")
        btn_add.setObjectName("btnPrimary")
        btn_add.clicked.connect(self._add_plot)
        top.addWidget(btn_add)
        layout.addLayout(top)

        self.status_label = QLabel("", objectName="statusLabel")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(objectName="mainTable")
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.table)

    def _rebuild_table(self):
        self.table.blockSignals(True)
        self.table.clearContents()

        plots_sorted = sorted(self._plots, key=lambda p: str(p.get("num", "")))
        
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Участок", "Собственники"])
        self.table.setRowCount(len(plots_sorted))

        for r_idx, plot in enumerate(plots_sorted):
            num_item = QTableWidgetItem(f"уч. {plot.get('num', '?')}")
            num_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            num_item.setForeground(QColor("#90caf9"))
            f = num_item.font(); f.setBold(True); num_item.setFont(f)
            self.table.setItem(r_idx, 0, num_item)

            owner_widget = self._build_owners_cell(plot)
            self.table.setCellWidget(r_idx, 1, owner_widget)

            self.table.setRowHeight(r_idx, 28)

        self.table.blockSignals(False)
        self.status_label.setText(f"Участков: {len(plots_sorted)}")

    def _build_owners_cell(self, plot: dict) -> QWidget:
        owners = plot.get("owners", []) or []
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(8)

        if owners:
            first_owner = owners[0]
            first_label = QLabel(first_owner)
            first_label.setStyleSheet("color:#cdd9e5;font-size:13px;")
            first_label.setToolTip("\n".join(owners))
            layout.addWidget(first_label, 1)

            extra = len(owners) - 1
            if extra > 0:
                btn_more = QPushButton(f"+{extra}")
                btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_more.setStyleSheet(
                    "QPushButton{background:transparent;color:#82cfff;border:none;"
                    "font-weight:700;padding:0px;margin:0px;}"
                    "QPushButton:hover{text-decoration:underline;}"
                )
                btn_more.clicked.connect(lambda _, p=plot: self._show_owners_popup(p))
                layout.addWidget(btn_more, 0, Qt.AlignmentFlag.AlignRight)
        else:
            label = QLabel("—")
            label.setStyleSheet("color:#7a9bb8;font-size:13px;")
            layout.addWidget(label)

        return container

    def _show_owners_popup(self, plot: dict):
        dlg = OwnersPopup(plot.get("num", "?"), plot.get("owners", []), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            updated = dlg.get_owners()
            if updated != plot.get("owners", []):
                idx = self._plots.index(plot)
                self._plots[idx] = {**plot, "owners": updated}
                self._save()
                self._rebuild_table()

    def _add_plot(self):
        dlg = PlotEditDialog(plot_data=None, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            result = dlg.get_result()
            if result:
                self._plots.append(result)
                self._save()
                self._rebuild_table()

    def _context_menu(self, pos: QPoint):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        
        plots_sorted = sorted(self._plots, key=lambda p: str(p.get("num", "")))
        if row >= len(plots_sorted):
            return
        
        plot = plots_sorted[row]
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu{background:#0d1b2a;border:1px solid #2a4a6b;color:#cdd9e5;
                  font-size:13px;padding:4px;}
            QMenu::item{padding:8px 20px;border-radius:4px;}
            QMenu::item:selected{background:#1a2e45;color:#ef9a9a;}
        """)
        
        act_edit = QAction("✏️  Редактировать", self)
        act_edit.triggered.connect(lambda: self._edit_plot(row, plot))
        menu.addAction(act_edit)
        
        act_del = QAction("🗑️  Удалить", self)
        act_del.triggered.connect(lambda: self._delete_plot(row, plot))
        menu.addAction(act_del)
        
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _edit_plot(self, row: int, plot: dict):
        dlg = PlotEditDialog(plot_data=plot, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            result = dlg.get_result()
            if result:
                # Обновляем оригинальный объект в списке
                idx = self._plots.index(plot)
                self._plots[idx] = result
                self._save()
                self._rebuild_table()

    def _delete_plot(self, row: int, plot: dict):
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


# ======================================================================= #
#  ВКЛАДКА «УЧАСТКИ»
# ======================================================================= #            

import json, os, shutil
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QMessageBox, QDialog, QLineEdit, QFormLayout, QDialogButtonBox,
    QFrame, QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon


class OwnersPopup(QDialog):
    """Диалог просмотра/редактирования списка собственников участка."""

    def __init__(self, plot_num: str, owners: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Собственники — уч. {plot_num}")
        self.setMinimumWidth(420)
        self.setModal(True)
        self._owners = list(owners)
        self._inputs: list[QLineEdit] = []
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(10)

        title = QLabel("Список собственников")
        title.setStyleSheet("font-size:14px;font-weight:700;color:#e8f4fd;")
        lay.addWidget(title)

        # Прокручиваемая область с полями
        self._scroll_widget = QWidget()
        self._form_lay = QVBoxLayout(self._scroll_widget)
        self._form_lay.setSpacing(6)
        self._form_lay.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._scroll_widget)
        scroll.setStyleSheet(
            "QScrollArea{background:#0d1b2a;border:1px solid #1e3a5f;border-radius:6px;}"
        )
        scroll.setMinimumHeight(140)
        scroll.setMaximumHeight(300)
        lay.addWidget(scroll)

        for name in self._owners:
            self._add_owner_row(name)

        btn_add = QPushButton("＋  Добавить собственника")
        btn_add.setObjectName("btnSecondary")
        btn_add.clicked.connect(lambda: self._add_owner_row(""))
        lay.addWidget(btn_add)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btns.setStyleSheet(
            "QPushButton{background:#1565c0;color:white;border:none;border-radius:6px;"
            "padding:7px 18px;font-size:13px;font-weight:600;}"
            "QPushButton:hover{background:#1976d2;}"
            "QPushButton[text='Cancel']{background:#1e3a5f;color:#8eb3d4;}"
        )
        lay.addWidget(btns)

    def _add_owner_row(self, name: str):
        row_widget = QWidget()
        row_widget.setStyleSheet("background:transparent;")
        rlay = QHBoxLayout(row_widget)
        rlay.setContentsMargins(6, 2, 6, 2)
        rlay.setSpacing(6)

        inp = QLineEdit(name)
        inp.setPlaceholderText("Фамилия Имя Отчество")
        inp.setStyleSheet(
            "background:#0d1b2a;border:1px solid #2a4a6b;border-radius:5px;"
            "color:#cdd9e5;padding:6px 10px;font-size:13px;"
        )
        self._inputs.append(inp)
        rlay.addWidget(inp, stretch=1)

        btn_del = QPushButton("✕")
        btn_del.setFixedSize(28, 28)
        btn_del.setStyleSheet(
            "QPushButton{background:#2a1a1a;border:1px solid #5a2a2a;"
            "border-radius:5px;color:#ef9a9a;font-size:13px;}"
            "QPushButton:hover{background:#3a2020;}"
        )
        btn_del.clicked.connect(lambda _, w=row_widget, i=inp: self._remove_row(w, i))
        rlay.addWidget(btn_del)

        self._form_lay.addWidget(row_widget)

    def _remove_row(self, row_widget: QWidget, inp: QLineEdit):
        if inp in self._inputs:
            self._inputs.remove(inp)
        row_widget.setParent(None)
        row_widget.deleteLater()

    def get_owners(self) -> list[str]:
        return [inp.text().strip() for inp in self._inputs if inp.text().strip()]

    def _apply_styles(self):
        self.setStyleSheet(
            "QDialog{background:#111e2b;color:#cdd9e5;}"
            "QLabel{background:transparent;color:#cdd9e5;}"
        )


class PlotEditDialog(QDialog):
    """Диалог добавления / редактирования участка."""

    def __init__(self, plot_data: dict | None = None, parent=None):
        super().__init__(parent)
        self._is_edit = plot_data is not None
        self._plot_data = plot_data or {}
        self.setWindowTitle("Редактировать участок" if self._is_edit else "Новый участок")
        self.setMinimumWidth(460)
        self.setModal(True)
        self._owner_inputs: list[QLineEdit] = []
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 20)
        lay.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Номер участка
        self.inp_num = QLineEdit(str(self._plot_data.get("num", "")))
        self.inp_num.setPlaceholderText("например: 15 или 15/207")
        if self._is_edit:
            self.inp_num.setReadOnly(True)
            self.inp_num.setStyleSheet(
                "background:#080f18;border:1px solid #1e3a5f;"
                "border-radius:5px;color:#7a9bb8;padding:7px 10px;"
            )
        form.addRow("Номер участка:", self.inp_num)
        lay.addLayout(form)

        # Собственники
        own_label = QLabel("Собственники:")
        own_label.setStyleSheet("color:#7a9bb8;")
        lay.addWidget(own_label)

        self._owners_container = QWidget()
        self._owners_container.setStyleSheet("background:transparent;")
        self._owners_vlay = QVBoxLayout(self._owners_container)
        self._owners_vlay.setSpacing(6)
        self._owners_vlay.setContentsMargins(0, 0, 0, 0)

        existing_owners = self._plot_data.get("owners", [""])
        if not existing_owners:
            existing_owners = [""]
        for name in existing_owners:
            self._add_owner_field(name)

        lay.addWidget(self._owners_container)

        btn_add_owner = QPushButton("＋  Добавить собственника")
        btn_add_owner.setObjectName("btnSecondary")
        btn_add_owner.clicked.connect(lambda: self._add_owner_field(""))
        lay.addWidget(btn_add_owner)

        # Разделитель
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#1e3a5f;background:#1e3a5f;max-height:1px;")
        lay.addWidget(sep)

        # Кнопки OK / Cancel
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Сохранить")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _add_owner_field(self, name: str):
        row = QWidget()
        row.setStyleSheet("background:transparent;")
        rlay = QHBoxLayout(row)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(6)

        inp = QLineEdit(name)
        inp.setPlaceholderText("Фамилия Имя Отчество")
        self._owner_inputs.append(inp)
        rlay.addWidget(inp, stretch=1)

        btn = QPushButton("✕")
        btn.setFixedSize(28, 28)
        btn.setStyleSheet(
            "QPushButton{background:#2a1a1a;border:1px solid #5a2a2a;"
            "border-radius:5px;color:#ef9a9a;font-size:12px;}"
            "QPushButton:hover{background:#3a2020;}"
        )
        btn.clicked.connect(lambda _, r=row, i=inp: self._remove_owner_field(r, i))
        rlay.addWidget(btn)
        self._owners_vlay.addWidget(row)

    def _remove_owner_field(self, row: QWidget, inp: QLineEdit):
        if len(self._owner_inputs) <= 1:
            inp.clear()
            return
        if inp in self._owner_inputs:
            self._owner_inputs.remove(inp)
        row.setParent(None)
        row.deleteLater()

    def _on_accept(self):
        num = self.inp_num.text().strip()
        if not num:
            QMessageBox.warning(self, "Ошибка", "Укажите номер участка")
            return
        owners = [i.text().strip() for i in self._owner_inputs if i.text().strip()]
        self._result = {"num": num, "owners": owners}
        self.accept()

    def get_result(self) -> dict:
        return getattr(self, "_result", {})

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog { background: #111e2b; color: #cdd9e5; }
            QLabel  { background: transparent; color: #cdd9e5; font-size: 13px; }
            QLineEdit {
                background: #0d1b2a; border: 1px solid #2a4a6b;
                border-radius: 5px; color: #cdd9e5; padding: 7px 10px; font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid #1976d2; }
            QPushButton#btnSecondary {
                background: #1e3a5f; color: #8eb3d4; border: 1px solid #2a4a6b;
                border-radius: 6px; padding: 7px 14px; font-size: 13px;
            }
            QPushButton#btnSecondary:hover { background: #243f63; color: #cdd9e5; }
            QDialogButtonBox QPushButton {
                background: #1565c0; color: white; border: none;
                border-radius: 6px; padding: 8px 20px; font-size: 13px; font-weight: 600;
            }
            QDialogButtonBox QPushButton:hover { background: #1976d2; }
            QDialogButtonBox QPushButton[text='Отмена'] {
                background: #1e3a5f; color: #8eb3d4;
            }
        """)


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

        # Кнопка открыть файл
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
            self.lbl_status.setStyleSheet("color:#81d4a0;font-size:14px;font-weight:700;")
            self.btn_attach.setText("🖼")
            self.btn_attach.setStyleSheet(
                "QPushButton{background:#0d3b1a;border:1px solid #2e7d32;"
                "border-radius:5px;font-size:13px;}"
                "QPushButton:hover{background:#1b5e20;}"
            )
            self.btn_open.setEnabled(True)
            self.btn_open.setStyleSheet(
                "QPushButton{background:#162131;border:1px solid #2a4a6b;"
                "border-radius:5px;color:#64b5f6;font-size:12px;}"
                "QPushButton:hover{background:#1e3a5f;}"
            )
        else:
            self.lbl_status.setText("—")
            self.lbl_status.setStyleSheet("color:#3a5a5a;font-size:14px;font-weight:700;")
            self.btn_attach.setText("📎")
            self.btn_attach.setStyleSheet(
                "QPushButton{background:#162131;border:1px solid #2a4a6b;"
                "border-radius:5px;font-size:13px;}"
                "QPushButton:hover{background:#1e3a5f;}"
            )
            self.btn_open.setEnabled(False)
            self.btn_open.setStyleSheet(
                "QPushButton{background:#0d1720;border:1px solid #1b2a3c;"
                "border-radius:5px;color:#7a9bb8;font-size:12px;}"
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

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        top_bar = QHBoxLayout()
        title = QLabel("Документы")
        title.setObjectName("pageTitle")
        top_bar.addWidget(title)
        top_bar.addStretch()

        btn_save = QPushButton("💾  Сохранить")
        btn_save.setObjectName("btnPrimary")
        btn_save.clicked.connect(self._save)
        top_bar.addWidget(btn_save)
        layout.addLayout(top_bar)

        self.status_label = QLabel("Документы не загружены", objectName="statusLabel")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(objectName="mainTable")
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        layout.addWidget(self.table)

    def _rebuild_table(self):
        self.table.blockSignals(True)
        self.table.clearContents()

        rows = len(_PLOT_ORDER)
        cols = 1 + len(self.DOC_TYPES)
        self.table.setRowCount(rows)
        self.table.setColumnCount(cols)

        headers = ["Участок"] + self.DOC_TYPES
        self.table.setHorizontalHeaderLabels(headers)

        for r_idx, plot in enumerate(_PLOT_ORDER):
            plot_item = QTableWidgetItem(f"уч. {plot}")
            plot_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            plot_item.setForeground(QColor("#90caf9"))
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
        for plot in _PLOT_ORDER:
            for doc_key in self.DOC_TYPES:
                path = self._docs.get(str(plot), {}).get(doc_key, "")
                total += 1
                if path:
                    attached += 1
        self.status_label.setText(
            f"Документов: {attached} из {total} прикреплено"
        )


# ======================================================================= #
#  ВКЛАДКА «КАРТА»
# ======================================================================= #

class _PlotMarker(QGraphicsEllipseItem):
    """Кликабельный кружок с номером участка."""
    R = 16

    def __init__(self, plot_num: str, owners: list, on_click,
                 color: str | None = None, debt: float | None = None):
        r = self.R
        super().__init__(-r, -r, r * 2, r * 2)
        self._plot_num = plot_num
        self._owners   = owners
        self._on_click = on_click
        self._base_color = QColor(color) if color else QColor("#1565c0")
        self._hover_color = self._lighten(self._base_color)
        self._debt = debt
        self.setBrush(self._base_color)
        self.setPen(QPen(self._lighten(self._base_color), 2))
        self.setZValue(1)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptHoverEvents(True)

        if debt is not None:
            tip = f"Уч. {plot_num}"
            if owners:
                tip += "\n" + ", ".join(owners)
            if abs(debt) > 0.005:
                if debt > 0:
                    tip += f"\nДолг: {debt:,.2f} ₽".replace(",", " ")
                else:
                    tip += f"\nАванс: {abs(debt):,.2f} ₽".replace(",", " ")
            else:
                tip += "\nБез долга"
            self.setToolTip(tip)

        lbl = QGraphicsTextItem(plot_num, self)
        lbl.setDefaultTextColor(QColor("#ffffff"))
        f = QFont(); f.setPointSize(8); f.setBold(True)
        lbl.setFont(f)
        br = lbl.boundingRect()
        lbl.setPos(-br.width() / 2, -br.height() / 2)

    @staticmethod
    def _lighten(color: QColor) -> QColor:
        h, s, v, a = color.getHsv()
        return QColor.fromHsv(h, max(0, s - 40), min(255, v + 40), a)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click(self._plot_num, self._owners)
        super().mousePressEvent(event)

    def hoverEnterEvent(self, event):
        self.setBrush(self._hover_color)
        self.setPen(QPen(QColor("#ffffff"), 2))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.setBrush(self._base_color)
        self.setPen(QPen(self._lighten(self._base_color), 2))
        super().hoverLeaveEvent(event)


class _MapView(QGraphicsView):
    """QGraphicsView с зумом колесом мыши."""
    def __init__(self, scene, map_widget):
        super().__init__(scene)
        self._map_widget = map_widget
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setStyleSheet("background:#0f1923; border:none;")

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self._map_widget._placing_mode):
            pos = self.mapToScene(event.pos())
            self._map_widget._on_map_click(pos)
        else:
            super().mousePressEvent(event)


class MapWidget(QWidget):
    """Схема-карта участков: загрузи изображение, расставь участки кликом."""

    COORDS_FILE = os.path.join(DATA_DIR, "snt_map_plots.json")
    IMAGE_FILE  = os.path.join(DATA_DIR, "snt_map_image.json")

    def __init__(self):
        super().__init__()
        self._placing_mode = False
        self._image_path   = self._load_image_path()
        self._debts: dict = {}
        self._color_by_debt = True
        self._setup_ui()
        self.reload_map()

    # ── Персистентность ──────────────────────────────────────────────────

    def _load_image_path(self) -> str:
        try:
            if os.path.exists(self.IMAGE_FILE):
                with open(self.IMAGE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f).get("path", "")
        except Exception:
            pass
        return ""

    def _save_image_path(self, path: str):
        try:
            with open(self.IMAGE_FILE, "w", encoding="utf-8") as f:
                json.dump({"path": path}, f, ensure_ascii=False)
        except Exception:
            pass

    def _load_plot_coords(self) -> dict:
        """{"20": [x_px, y_px], ...} — пиксельные координаты на схеме."""
        try:
            if os.path.exists(self.COORDS_FILE):
                with open(self.COORDS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_plot_coords(self, coords: dict):
        try:
            with open(self.COORDS_FILE, "w", encoding="utf-8") as f:
                json.dump(coords, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_plots_owners(self) -> dict:
        try:
            if os.path.exists(os.path.join(DATA_DIR, "snt_plots.json")):
                with open(os.path.join(DATA_DIR, "snt_plots.json"), "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {str(p["num"]): p.get("owners", []) for p in data}
        except Exception:
            pass
        return {}

    # ── UI ───────────────────────────────────────────────────────────────

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QWidget()
        bar.setStyleSheet("background:#0d1b2a; border-bottom:1px solid #1e3a5f;")
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(20, 8, 20, 8)
        bar_lay.setSpacing(10)

        title = QLabel("Карта участков", objectName="pageTitle")
        bar_lay.addWidget(title)
        bar_lay.addStretch()

        self._hint_lbl = QLabel("")
        self._hint_lbl.setStyleSheet("color:#5a8ab0; font-size:12px;")
        bar_lay.addWidget(self._hint_lbl)

        btn_load = QPushButton("🖼  Загрузить схему")
        btn_load.setObjectName("btnSecondary")
        btn_load.clicked.connect(self._pick_image)
        bar_lay.addWidget(btn_load)

        self._btn_place = QPushButton("📍  Расставить участки")
        self._btn_place.setObjectName("btnSecondary")
        self._btn_place.setCheckable(True)
        self._btn_place.toggled.connect(self._toggle_place_mode)
        bar_lay.addWidget(self._btn_place)

        self._btn_color = QPushButton("🎨  По долгу")
        self._btn_color.setObjectName("btnSecondary")
        self._btn_color.setCheckable(True)
        self._btn_color.setChecked(True)
        self._btn_color.toggled.connect(self._toggle_color_mode)
        bar_lay.addWidget(self._btn_color)

        lay.addWidget(bar)

        legend = QWidget()
        legend.setStyleSheet("background:#0d1b2a;border-bottom:1px solid #1e3a5f;")
        legend_lay = QHBoxLayout(legend)
        legend_lay.setContentsMargins(20, 4, 20, 4)
        legend_lay.setSpacing(20)
        for color, text in [
            ("#2e7d32", "■  без долга / аванс"),
            ("#f9a825", "■  небольшой"),
            ("#ef6c00", "■  средний"),
            ("#c62828", "■  крупный"),
        ]:
            lb = QLabel(text)
            lb.setStyleSheet(f"color:{color};background:transparent;font-size:11px;")
            legend_lay.addWidget(lb)
        legend_lay.addStretch()
        lay.addWidget(legend)

        self._scene = QGraphicsScene()
        self._view  = _MapView(self._scene, self)
        self._view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        lay.addWidget(self._view, stretch=1)

        self._info = QLabel("Кликните на участок чтобы увидеть информацию")
        self._info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._info.setStyleSheet(
            "background:#0d1b2a; color:#cdd9e5; font-size:13px;"
            "padding:8px; border-top:1px solid #1e3a5f;"
        )
        lay.addWidget(self._info)

    # ── Логика карты ─────────────────────────────────────────────────────

    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите изображение схемы СНТ", "",
            "Изображения (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if path:
            self._image_path = path
            self._save_image_path(path)
            self.reload_map()

    def _toggle_color_mode(self, on: bool):
        self._color_by_debt = on
        self.reload_map()

    def _toggle_place_mode(self, on: bool):
        self._placing_mode = on
        if on:
            self._btn_place.setStyleSheet(
                "QPushButton{background:#b71c1c;color:white;border-radius:6px;padding:4px 12px;}"
            )
            self._hint_lbl.setText("Режим расстановки: кликните на схеме → выберите участок")
            self._view.setDragMode(QGraphicsView.DragMode.NoDrag)
            self._view.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._btn_place.setStyleSheet("")
            self._hint_lbl.setText("")
            self._view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self._view.setCursor(Qt.CursorShape.ArrowCursor)

    def _on_map_click(self, pos):
        """Вызывается из _MapView в режиме расстановки."""
        owners   = self._load_plots_owners()
        all_nums = sorted(
            owners.keys() or [str(i) for i in range(1, 51)],
            key=lambda x: (len(x), x)
        )
        num, ok = QInputDialog.getItem(
            self, "Выбор участка",
            "Какой участок разместить здесь?",
            all_nums, 0, False
        )
        if ok and num:
            coords = self._load_plot_coords()
            coords[num] = [pos.x(), pos.y()]
            self._save_plot_coords(coords)
            self.reload_map()

    def _on_plot_click(self, plot_num: str, owners: list):
        text = " · ".join(owners) if owners else "нет данных"
        self._info.setText(f"  Участок {plot_num}  —  {text}")

    def reload_map(self):
        self._scene.clear()

        if self._image_path and os.path.exists(self._image_path):
            px = QPixmap(self._image_path)
            if px.isNull():
                QMessageBox.warning(self, "Ошибка", "Не удалось загрузить изображение.")
                self._image_path = ""
                self.reload_map()
                return
            item = self._scene.addPixmap(px)
            self._scene.setSceneRect(QRectF(0, 0, px.width(), px.height()))
        else:
            w, h = 820, 520
            self._scene.setSceneRect(QRectF(0, 0, w, h))
            self._scene.addRect(
                QRectF(0, 0, w, h),
                QPen(Qt.PenStyle.NoPen),
                QColor("#0a1520")
            )
            t = self._scene.addText(
                "Загрузите схему карты СНТ\n\n"
                "Нажмите «🖼 Загрузить схему» и выберите скриншот или скан карты.\n"
                "Затем нажмите «📍 Расставить участки» и кликайте по нужным местам.",
                QFont("", 13)
            )
            t.setDefaultTextColor(QColor("#5a8ab0"))
            br = t.boundingRect()
            t.setPos((w - br.width()) / 2, (h - br.height()) / 2)

        coords = self._load_plot_coords()
        owners = self._load_plots_owners()
        debts = self._debts if self._color_by_debt else {}
        for plot_num, pos in coords.items():
            if len(pos) < 2:
                continue
            owner_list = owners.get(str(plot_num), [])
            info = debts.get(str(plot_num))
            color = info["color"] if info else None
            debt = info["debt"] if info else None
            marker = _PlotMarker(plot_num, owner_list, self._on_plot_click,
                                 color=color, debt=debt)
            marker.setPos(pos[0], pos[1])
            self._scene.addItem(marker)

    def set_debts(self, debts: dict):
        """Принимает {plot_num: {"debt": float, "color": "#..."}}; перерисовывает."""
        self._debts = debts or {}
        self.reload_map()


# ======================================================================= #
#  ВКЛАДКА «ДОЛГИ ПО ЭЛЕКТРОЭНЕРГИИ»
# ======================================================================= #

class _NumItem(QTableWidgetItem):
    """QTableWidgetItem с числовой сортировкой."""
    def __init__(self, text: str, value: float):
        super().__init__(text)
        self._value = value

    def __lt__(self, other):
        try:
            return self._value < other._value
        except AttributeError:
            return super().__lt__(other)


class MeterReplacementDialog(QDialog):
    """Регистрация замены счётчика для участка."""

    def __init__(self, plot: str, parent=None,
                 prev_value: float | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Замена счётчика — уч. {plot}")
        self.setMinimumWidth(420)
        self.setModal(True)
        self._plot = plot
        self._result: dict | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 18)
        lay.setSpacing(12)

        title = QLabel(f"Замена счётчика на участке {plot}")
        title.setStyleSheet("font-size:14px;font-weight:700;color:#e8f4fd;")
        lay.addWidget(title)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.inp_date = QDateEdit(calendarPopup=True)
        self.inp_date.setDisplayFormat("dd.MM.yyyy")
        self.inp_date.setDate(QDate.currentDate())
        form.addRow("Дата замены:", self.inp_date)

        self.inp_old = QLineEdit("" if prev_value is None else f"{prev_value:g}")
        self.inp_old.setPlaceholderText("конечное показание старого счётчика")
        form.addRow("Старый счётчик (конечн.):", self.inp_old)

        self.inp_new = QLineEdit("0")
        self.inp_new.setPlaceholderText("начальное показание нового счётчика")
        form.addRow("Новый счётчик (нач.):", self.inp_new)

        self.inp_note = QLineEdit()
        self.inp_note.setPlaceholderText("замена по сроку поверки, срыв пломбы и т.д.")
        form.addRow("Примечание:", self.inp_note)

        lay.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Сохранить")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        self.setStyleSheet("""
            QDialog { background: #111e2b; color: #cdd9e5; }
            QLabel { background: transparent; color: #cdd9e5; font-size: 13px; }
            QLineEdit, QDateEdit {
                background: #0d1b2a; border: 1px solid #2a4a6b;
                border-radius: 5px; color: #cdd9e5; padding: 6px 8px; font-size: 13px;
            }
            QLineEdit:focus, QDateEdit:focus { border: 1px solid #1976d2; }
            QDialogButtonBox QPushButton {
                background: #1565c0; color: white; border: none;
                border-radius: 6px; padding: 7px 18px; font-size: 13px; font-weight: 600;
            }
            QDialogButtonBox QPushButton:hover { background: #1976d2; }
            QDialogButtonBox QPushButton[text='Отмена'] {
                background: #1e3a5f; color: #8eb3d4;
            }
        """)

    def _on_accept(self):
        try:
            old_v = float(self.inp_old.text().strip().replace(",", "."))
            new_v = float(self.inp_new.text().strip().replace(",", "."))
        except ValueError:
            QMessageBox.warning(self, "Ошибка", "Показания должны быть числами")
            return
        if old_v < 0 or new_v < 0:
            QMessageBox.warning(self, "Ошибка", "Показания не могут быть отрицательными")
            return
        self._result = {
            "date": self.inp_date.date().toString("yyyy-MM-dd"),
            "old_final": f"{old_v:g}",
            "new_initial": f"{new_v:g}",
            "note": self.inp_note.text().strip(),
        }
        self.accept()

    def get_result(self) -> dict | None:
        return self._result


class PlotCardDialog(QDialog):
    """Карточка участка: помесячная сводка показаний, начислений и платежей."""

    def __init__(self, plot: str, df, parent=None):
        super().__init__(parent)
        self._plot = plot
        self._df = df
        self.setWindowTitle(f"Участок {plot} — карточка")
        self.setMinimumSize(820, 560)
        self.setModal(False)

        self._setup_ui()
        self._rebuild()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 16)
        lay.setSpacing(10)

        # Шапка с владельцами
        owners = energy.owners_map().get(str(self._plot), [])
        owners_text = ", ".join(owners) if owners else "владельцы не указаны"
        head = QLabel(f"<b>Участок {self._plot}</b>  ·  {owners_text}")
        head.setStyleSheet("font-size:14px;color:#e8f4fd;background:transparent;")
        lay.addWidget(head)

        self.summary_lbl = QLabel("")
        self.summary_lbl.setStyleSheet("color:#7a9bb8;background:transparent;font-size:12px;")
        lay.addWidget(self.summary_lbl)

        self.table = QTableWidget(objectName="summaryTable")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "Месяц", "Показание", "Расход (кВт·ч)",
            "Тариф", "Начислено", "Оплачено", "Баланс мес.", "Долг нараст."
        ])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setStretchLastSection(True)
        lay.addWidget(self.table, 1)

        # Кнопки внизу
        bottom = QHBoxLayout()
        self.btn_replace = QPushButton("🔧  Зарегистрировать замену счётчика")
        self.btn_replace.setObjectName("btnSecondary")
        self.btn_replace.clicked.connect(self._on_replace)
        bottom.addWidget(self.btn_replace)

        self.btn_pdf = QPushButton("📄  Сохранить PDF-квитанцию")
        self.btn_pdf.setObjectName("btnSecondary")
        self.btn_pdf.clicked.connect(self._on_pdf)
        bottom.addWidget(self.btn_pdf)

        bottom.addStretch()

        btn_close = QPushButton("Закрыть")
        btn_close.setObjectName("btnPrimary")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        lay.addLayout(bottom)

        self.setStyleSheet("""
            QDialog { background: #111e2b; color: #cdd9e5; }
            QLabel  { background: transparent; }
            QPushButton#btnPrimary {
                background: #1565c0; color: white; border: none; border-radius: 6px;
                padding: 8px 18px; font-size: 13px; font-weight: 600;
            }
            QPushButton#btnPrimary:hover  { background: #1976d2; }
            QPushButton#btnSecondary {
                background: #1e3a5f; color: #8eb3d4; border: 1px solid #2a4a6b;
                border-radius: 6px; padding: 7px 14px; font-size: 13px;
            }
            QPushButton#btnSecondary:hover { background: #243f63; color: #cdd9e5; }
            QTableWidget#summaryTable {
                background: #0d1b2a; border: 1px solid #1e3a5f; border-radius: 8px;
                gridline-color: #1a2733; color: #cdd9e5; font-size: 12px;
                selection-background-color: #1a3a5a; selection-color: #e8f4fd;
            }
            QTableWidget#summaryTable QHeaderView::section {
                background: #0a1520; color: #64b5f6; border: none;
                border-right: 1px solid #1e3a5f; border-bottom: 2px solid #1976d2;
                padding: 6px 8px; font-size: 12px; font-weight: 600;
            }
        """)

    def _rebuild(self):
        meters = energy.load_meters()
        rates = energy.load_rates()
        repls = energy.load_replacements()
        baseline = energy.load_baseline()

        charges = energy.all_charges(self._plot, meters, rates, repls)
        if not charges:
            self.table.setRowCount(0)
            self.summary_lbl.setText("Нет показаний по этому участку")
            return

        # Платежи по месяцам (ставим в месяц даты прихода)
        pay_by_month: dict[tuple[int, int], float] = {}
        for p in energy.payments_breakdown(self._plot, self._df):
            d = p["date"]
            if d is None:
                continue
            key = (d.year, d.month)
            pay_by_month[key] = pay_by_month.get(key, 0.0) + p["amount"]

        base = energy._to_float(baseline.get("balances", {}).get(str(self._plot))) or 0.0
        base_start = energy._parse_iso(baseline.get("start_date", ""))

        rows = []
        cum = base
        for c in charges:
            y, m = c["year"], c["month"]
            amount = c["amount"] or 0.0
            paid = pay_by_month.get((y, m), 0.0)
            cum += amount - paid
            rows.append({**c, "paid": paid, "balance": cum})

        self.table.setRowCount(len(rows) + (1 if base != 0 else 0))
        r0 = 0
        if base != 0:
            label = "Начальное сальдо"
            if base_start:
                label += f" ({base_start.isoformat()})"
            self._set_row(0, [
                (label, None), ("—", None), ("—", None), ("—", None),
                ("—", None), ("—", None),
                (self._fmt_money(base), None),
                (self._fmt_money(base), self._debt_color(base)),
            ], bold=True)
            r0 = 1

        for i, row in enumerate(rows):
            r = r0 + i
            month_label = f"{energy.reading_date(row['year'], row['month']).strftime('%m.%Y')}"
            value_text = f"{row['value']:g}"
            if row["prev_value"] is not None:
                value_text += f"  ({row['prev_value']:g})"
            kwh_text = f"{row['kwh']:.0f}" if row["kwh"] is not None else "—"
            rate_text = f"{row['rate']:.2f}" if row["rate"] is not None else "—"
            charged_text = self._fmt_money(row["amount"]) if row["amount"] is not None else "—"
            paid_text = self._fmt_money(row["paid"]) if row["paid"] else "—"
            mbal = (row["amount"] or 0.0) - row["paid"]
            mbal_text = self._fmt_money(mbal) if mbal else "0 ₽"
            bal_text = self._fmt_money(row["balance"])

            self._set_row(r, [
                (month_label, None),
                (value_text, None),
                (kwh_text, None),
                (rate_text, None),
                (charged_text, "#f9a825" if row["amount"] else None),
                (paid_text, "#81d4a0" if row["paid"] else None),
                (mbal_text, None),
                (bal_text, self._debt_color(row["balance"])),
            ])

        if rows:
            last = rows[-1]
            self.summary_lbl.setText(
                f"Начислено всего: {self._fmt_money(base + sum(c['amount'] or 0 for c in rows))}  ·  "
                f"оплачено всего: {self._fmt_money(sum(r['paid'] for r in rows))}  ·  "
                f"итоговый баланс: {self._fmt_money(last['balance'])}"
            )

    def _set_row(self, r: int, cells: list, bold: bool = False):
        for c, (text, color) in enumerate(cells):
            it = QTableWidgetItem(text)
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            if color:
                it.setForeground(QColor(color))
            if bold:
                f = it.font(); f.setBold(True); it.setFont(f)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(r, c, it)
        self.table.setRowHeight(r, 28)

    @staticmethod
    def _fmt_money(v: float) -> str:
        if v is None:
            return "—"
        sign = "-" if v < 0 else ""
        return f"{sign}{abs(v):,.2f} ₽".replace(",", " ")

    @staticmethod
    def _debt_color(v: float) -> str | None:
        if v > 0:
            return "#ef9a9a"
        if v < 0:
            return "#81d4a0"
        return None

    def _on_replace(self):
        meters = energy.load_meters()
        readings = energy.plot_readings(self._plot, meters)
        last_val = readings[-1][2] if readings else None
        dlg = MeterReplacementDialog(self._plot, self, prev_value=last_val)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        result = dlg.get_result()
        if not result:
            return
        repls = energy.load_replacements()
        repls.setdefault(str(self._plot), []).append(result)
        repls[str(self._plot)].sort(key=lambda r: r.get("date", ""))
        energy.save_replacements(repls)
        self._rebuild()
        QMessageBox.information(self, "Замена счётчика",
                                "Замена сохранена. Расчёт пересчитан.")

    def _on_pdf(self):
        try:
            from core import receipt
        except ImportError:
            QMessageBox.information(self, "Квитанции",
                                    "Модуль квитанций ещё не подключён.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить квитанцию", f"Уч_{self._plot}.pdf",
            "PDF (*.pdf)"
        )
        if not path:
            return
        try:
            receipt.save_plot_receipt_pdf(self._plot, self._df, path)
            QMessageBox.information(self, "Квитанция", f"Сохранено:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")


class EnergyDebtWidget(QWidget):
    """Вкладка контроля долгов по электроэнергии."""

    def __init__(self):
        super().__init__()
        self._df = None
        self._setup_ui()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)

        top = QHBoxLayout()
        title = QLabel("Долги по электроэнергии", objectName="pageTitle")
        top.addWidget(title)
        top.addStretch()

        top.addWidget(QLabel("на дату:", objectName="filterLabel"))
        self.date_as_of = QDateEdit(calendarPopup=True, objectName="datePicker",
                                    displayFormat="dd.MM.yyyy")
        self.date_as_of.setDate(QDate.currentDate())
        self.date_as_of.dateChanged.connect(self._rebuild)
        top.addWidget(self.date_as_of)

        self.search = QLineEdit(objectName="searchInput")
        self.search.setPlaceholderText("🔍  Поиск по № участка или ФИО")
        self.search.setFixedWidth(280)
        self.search.textChanged.connect(self._apply_filter)
        top.addWidget(self.search)

        self.cb_only_debt = QComboBox(objectName="filterCombo")
        self.cb_only_debt.addItems(["Все участки", "Только должники", "Только аванс/0"])
        self.cb_only_debt.currentIndexChanged.connect(self._apply_filter)
        top.addWidget(self.cb_only_debt)

        btn = QPushButton("🔄  Пересчитать", objectName="btnPrimary")
        btn.clicked.connect(self._rebuild)
        top.addWidget(btn)

        self.btn_baseline = QPushButton("⚙  Стартовое сальдо", objectName="btnSecondary")
        self.btn_baseline.clicked.connect(self._edit_baseline)
        top.addWidget(self.btn_baseline)

        self.btn_mass_pdf = QPushButton("📄  Квитанции должникам", objectName="btnSecondary")
        self.btn_mass_pdf.clicked.connect(self._export_debtor_receipts)
        top.addWidget(self.btn_mass_pdf)

        lay.addLayout(top)

        # Легенда
        legend = QHBoxLayout()
        legend.setSpacing(20)
        for color, text in [
            ("#81d4a0", "■  без долга / аванс"),
            ("#f9a825", "■  небольшой долг"),
            ("#ef6c00", "■  средний"),
            ("#c62828", "■  крупный"),
        ]:
            lb = QLabel(text)
            lb.setStyleSheet(
                f"color:{color};background:transparent;font-size:11px;"
            )
            legend.addWidget(lb)
        legend.addStretch()
        lay.addLayout(legend)

        self.table = QTableWidget(objectName="summaryTable")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "Участок", "Владелец", "Последнее показание", "Дата показ.",
            "Начислено", "Оплачено", "Стартовое", "Долг / Аванс", "Без оплаты, мес.",
        ])
        hdr = self.table.horizontalHeader()
        for c, w in enumerate([85, 240, 140, 105, 120, 120, 100, 130, 110]):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(c, w)
        hdr.setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self._open_card)
        lay.addWidget(self.table, 1)

        # Сверка с поставщиком
        self.recon_lbl = QLabel("", objectName="statusLabel")
        self.recon_lbl.setWordWrap(True)
        self.recon_lbl.setStyleSheet(
            "background:#0a1520;border:1px solid #1e3a5f;border-radius:6px;"
            "padding:10px 14px;color:#cdd9e5;font-size:12px;"
        )
        lay.addWidget(self.recon_lbl)

        self.status_lbl = QLabel("Загрузите выписку на вкладке «Детализация»",
                                  objectName="statusLabel")
        lay.addWidget(self.status_lbl)

    def refresh(self, df):
        self._df = df
        self._rebuild()

    def get_debts(self) -> dict:
        """Для карты: {plot: {debt, color}}."""
        if not hasattr(self, "_last_debts"):
            return {}
        return self._last_debts

    def _plot_list(self) -> list[str]:
        plots = energy.load_plots()
        nums = [str(p.get("num", "")) for p in plots if p.get("num")]
        # Plus all plots with readings even if not in registry
        meters = energy.load_meters()
        for key in meters:
            parts = key.split(":")
            if parts and parts[0] not in nums:
                nums.append(parts[0])
        # сортировка: сначала числовые по возрастанию, потом сложные
        def _key(s):
            try:
                return (0, int(s))
            except ValueError:
                return (1, s)
        return sorted(set(nums), key=_key)

    def _rebuild(self):
        as_of = self.date_as_of.date().toPyDate()
        meters = energy.load_meters()
        rates = energy.load_rates()
        repls = energy.load_replacements()
        baseline = energy.load_baseline()
        owners = energy.owners_map()
        plots = self._plot_list()

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(plots))

        total_debt = 0.0
        total_charged = 0.0
        total_paid = 0.0
        debt_count = 0
        avg_monthly: list[float] = []
        debts_map: dict[str, dict] = {}

        for r, plot in enumerate(plots):
            bal = energy.balance(plot, as_of, meters, rates, repls, baseline, self._df)
            owner = ", ".join(owners.get(plot, [])) or "—"
            last_reading_text = "—"
            last_date_text = "—"
            if bal.last_reading:
                ly, lm, lv = bal.last_reading
                last_reading_text = f"{lv:g}"
                last_date_text = f"{lm:02d}.{ly}"

            # Средний месячный платёж для оценки уровня долга (для цвета на карте)
            if bal.charged > 0:
                count_charged_months = sum(
                    1 for c in energy.all_charges(plot, meters, rates, repls, as_of)
                    if c["amount"] is not None
                )
                if count_charged_months:
                    avg_monthly.append(bal.charged / count_charged_months)

            color = energy.debt_color(bal.debt, monthly_avg=300.0)
            debts_map[plot] = {"debt": bal.debt, "color": color,
                                "charged": bal.charged, "paid": bal.paid}

            total_debt += bal.debt
            total_charged += bal.charged
            total_paid += bal.paid
            if bal.debt > 0.5:
                debt_count += 1

            cells = [
                (f"уч. {plot}", plot, None, "#90caf9", True),
                (owner, owner, None, "#cdd9e5", False),
                (last_reading_text, last_reading_text, None, "#cdd9e5", False),
                (last_date_text, last_date_text, None, "#7a9bb8", False),
                (self._fmt_money(bal.charged), bal.charged, None, "#f9a825" if bal.charged else "#3a5a7a", False),
                (self._fmt_money(bal.paid), bal.paid, None, "#81d4a0" if bal.paid else "#3a5a7a", False),
                (self._fmt_money(bal.baseline) if bal.baseline else "—", bal.baseline, None,
                 "#c97c7c" if bal.baseline else "#3a5a7a", False),
                (self._fmt_money(bal.debt), bal.debt, color, "#ffffff", True),
                ("—" if bal.months_without_payment is None else str(bal.months_without_payment),
                 bal.months_without_payment or 0, None,
                 "#ef9a9a" if (bal.months_without_payment or 0) > 3 else "#cdd9e5", False),
            ]
            for c, (text, value, bg, fg, bold) in enumerate(cells):
                if isinstance(value, (int, float)):
                    it = _NumItem(text, float(value))
                else:
                    it = QTableWidgetItem(text)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                if bg:
                    it.setBackground(QColor(bg))
                if fg:
                    it.setForeground(QColor(fg))
                if bold:
                    f = it.font(); f.setBold(True); it.setFont(f)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(r, c, it)
            self.table.setRowHeight(r, 28)

        self.table.setSortingEnabled(True)
        self._last_debts = debts_map

        self.status_lbl.setText(
            f"Участков: {len(plots)}  ·  должников: {debt_count}  ·  "
            f"общий долг: {self._fmt_money(total_debt)}"
        )

        # Сверка
        try:
            df = self._df
            if df is None or df.empty:
                date_from = baseline_start = energy._parse_iso(baseline.get("start_date", "")) or date(as_of.year, 1, 1)
                date_to = as_of
            else:
                date_from = max(
                    df["Дата"].min().date(),
                    energy._parse_iso(baseline.get("start_date", "")) or df["Дата"].min().date(),
                )
                date_to = as_of
            common = energy.load_common_meter()
            rec = energy.reconcile(date_from, date_to, plots,
                                    meters, rates, repls, common, df)
            extras = ""
            if rec.common_kwh is not None:
                extras = (f"  ·  общий счётчик: {rec.common_kwh:.0f} кВт·ч"
                          f"  ·  частные: {rec.private_kwh:.0f} кВт·ч"
                          f"  ·  потери: {rec.loss_kwh:.0f} кВт·ч"
                          + (f" ({self._fmt_money(rec.loss_rub)})" if rec.loss_rub else ""))
            self.recon_lbl.setText(
                f"<b>Сверка с поставщиком</b> ({rec.period_from} — {rec.period_to}):  "
                f"начислено садоводам {self._fmt_money(rec.charged_total)}  ·  "
                f"собрано {self._fmt_money(rec.collected_total)}  ·  "
                f"уплачено в Пермэнергосбыт {self._fmt_money(rec.paid_to_supplier)}  ·  "
                f"расхождение {self._fmt_money(rec.collected_total - rec.paid_to_supplier)}"
                + extras
            )
        except Exception as e:
            self.recon_lbl.setText(f"Сверка недоступна: {e}")

        self._apply_filter()

    def _apply_filter(self):
        text = self.search.text().strip().lower()
        mode = self.cb_only_debt.currentText()
        for r in range(self.table.rowCount()):
            plot_item = self.table.item(r, 0)
            owner_item = self.table.item(r, 1)
            debt_item = self.table.item(r, 7)
            if not plot_item or not debt_item:
                continue
            visible = True
            if text:
                hay = (plot_item.text() + " " + (owner_item.text() if owner_item else "")).lower()
                visible = text in hay
            if visible and mode == "Только должники":
                visible = isinstance(debt_item, _NumItem) and debt_item._value > 0.5
            elif visible and mode == "Только аванс/0":
                visible = isinstance(debt_item, _NumItem) and debt_item._value <= 0.5
            self.table.setRowHidden(r, not visible)

    def _open_card(self, row: int, _col: int):
        plot_item = self.table.item(row, 0)
        if not plot_item:
            return
        plot = plot_item.text().replace("уч. ", "").strip()
        dlg = PlotCardDialog(plot, self._df, self)
        dlg.exec()
        # после возможной замены счётчика — пересчитать
        self._rebuild()

    def _export_debtor_receipts(self):
        if not getattr(self, "_last_debts", None):
            QMessageBox.information(self, "Квитанции", "Сначала загрузите выписку.")
            return
        debtors = [(p, info) for p, info in self._last_debts.items()
                   if info["debt"] > 0.5]
        if not debtors:
            QMessageBox.information(self, "Квитанции", "Должников нет — квитанции не нужны.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Папка для квитанций")
        if not folder:
            return
        try:
            from core import receipt
        except ImportError as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось импортировать модуль квитанций:\n{e}")
            return

        owners = energy.owners_map()
        as_of = self.date_as_of.date().toPyDate()
        ok = 0
        errors = []
        for plot, info in debtors:
            owner = (owners.get(plot, [""])[0] or "").split()
            surname = owner[0] if owner else ""
            fname = f"Уч_{plot}"
            if surname:
                safe_surname = re.sub(r"[^\w\-]", "_", surname)
                fname += f"_{safe_surname}"
            fname += f"_{as_of.isoformat()}.pdf"
            fname = fname.replace("/", "-")
            path = os.path.join(folder, fname)
            try:
                receipt.save_plot_receipt_pdf(plot, self._df, path, as_of=as_of)
                ok += 1
            except Exception as e:
                errors.append(f"уч. {plot}: {e}")
        if errors:
            QMessageBox.warning(
                self, "Квитанции",
                f"Создано: {ok}\nОшибки ({len(errors)}):\n" + "\n".join(errors[:10])
            )
        else:
            QMessageBox.information(
                self, "Квитанции",
                f"✅  Сформировано {ok} квитанций в:\n{folder}"
            )

    def _edit_baseline(self):
        baseline = energy.load_baseline()
        cur_date = baseline.get("start_date", "")
        text, ok = QInputDialog.getText(
            self, "Стартовая дата учёта",
            "Дата начала учёта (YYYY-MM-DD), от которой засчитывать платежи:",
            text=cur_date or date(date.today().year, 1, 1).isoformat()
        )
        if not ok:
            return
        try:
            d = date.fromisoformat(text.strip())
        except ValueError:
            QMessageBox.warning(self, "Ошибка", "Дата в формате YYYY-MM-DD")
            return
        baseline["start_date"] = d.isoformat()
        energy.save_baseline(baseline)
        self._rebuild()

    @staticmethod
    def _fmt_money(v) -> str:
        if v is None:
            return "—"
        if abs(v) < 0.005:
            return "0 ₽"
        sign = "-" if v < 0 else ""
        return f"{sign}{abs(v):,.2f} ₽".replace(",", " ")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("СНТ — Финансовый учёт")
        self.setMinimumSize(1280, 720)
        self.resize(1500, 860)
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.nav = QListWidget(objectName="navPanel")
        self.nav.setFixedWidth(210)

        logo = QListWidgetItem("💼  СНТ Учёт")
        logo.setFlags(Qt.ItemFlag.NoItemFlags)
        logo.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setForeground(QColor("#90caf9"))
        f = QFont(); f.setPointSize(13); f.setBold(True)
        logo.setFont(f)
        self.nav.addItem(logo)
        self.nav.addItem(QListWidgetItem(""))   # разделитель

        self.nav_indices = {}
        for label, idx in [
            ("📋  Детализация",         0),
            ("💰  Членские взносы",     1),
            ("⚡  Электроэнергия",      2),
            ("💸  Долги (электр-во)",   8),
            ("📡  Показания счётчика",  3),
            ("📐  Нормативы",           4),
            ("📍  Участки",             5),
            ("🗂  Документы",           6),
            ("🗺  Карта",               7),
        ]:
            item = QListWidgetItem(label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self.nav.addItem(item)
            self.nav_indices[self.nav.count() - 1] = idx

        self.nav.currentRowChanged.connect(self._on_nav_changed)
        root.addWidget(self.nav)

        self.stack = QStackedWidget(objectName="contentArea")
        self.detail      = DetailWidget()
        self.sum_vznosy  = SummaryWidget(mode="vznosy")
        self.sum_electro = SummaryWidget(mode="electro")
        self.meters      = MeterWidget()
        self.rates       = RatesWidget()
        self.plots       = PlotsWidget()
        self.docs        = DocsWidget()
        self.map_tab     = MapWidget()
        self.energy_debt = EnergyDebtWidget()
        self.stack.addWidget(self.detail)       # 0
        self.stack.addWidget(self.sum_vznosy)   # 1
        self.stack.addWidget(self.sum_electro)  # 2
        self.stack.addWidget(self.meters)       # 3
        self.stack.addWidget(self.rates)        # 4
        self.stack.addWidget(self.plots)        # 5
        self.stack.addWidget(self.docs)         # 6
        self.stack.addWidget(self.map_tab)      # 7
        self.stack.addWidget(self.energy_debt)  # 8
        root.addWidget(self.stack, stretch=1)

        # Подписки на загрузку выписки
        self.detail.dataLoaded.connect(self.sum_vznosy.refresh)
        self.detail.dataLoaded.connect(self.sum_electro.refresh)
        self.detail.dataLoaded.connect(self.energy_debt.refresh)

        self.nav.setCurrentRow(2)

    def _on_nav_changed(self, row):
        if row in self.nav_indices:
            idx = self.nav_indices[row]
            self.stack.setCurrentIndex(idx)
            if idx == 1:
                self.sum_vznosy.refresh(self.detail.df_full)
            elif idx == 2:
                self.sum_electro.refresh(self.detail.df_full)
            elif idx == 7:
                # карта подхватывает актуальные долги
                self.map_tab.set_debts(self.energy_debt.get_debts())
            elif idx == 8:
                self.energy_debt.refresh(self.detail.df_full)

    def _apply_styles(self):
        self.setStyleSheet("""
            QMainWindow { background: #0f1923; }
            QListWidget#navPanel {
                background: #0d1b2a; border: none;
                border-right: 1px solid #1e3a5f; padding: 0; outline: 0;
            }
            QListWidget#navPanel::item {
                color: #8eb3d4; padding: 13px 20px; font-size: 14px;
                border-left: 3px solid transparent;
            }
            QListWidget#navPanel::item:selected {
                background: #1a2e45; color: #64b5f6; border-left: 3px solid #1976d2;
            }
            QListWidget#navPanel::item:hover:!selected {
                background: #132336; color: #b0cfe8;
            }
            QStackedWidget#contentArea { background: #111e2b; }
            QWidget {
                background: #111e2b; color: #cdd9e5;
                font-size: 13px; font-family: 'Segoe UI', 'Arial', sans-serif;
            }
            QLabel#pageTitle {
                font-size: 20px; font-weight: 700; color: #e8f4fd; background: transparent;
            }
            QFrame#filterFrame {
                background: #162131; border: 1px solid #1e3a5f; border-radius: 8px;
            }
            QLabel#filterLabel { color: #7a9bb8; background: transparent; font-size: 13px; }
            QLineEdit#searchInput {
                background: #0d1b2a; border: 1px solid #2a4a6b; border-radius: 6px;
                color: #cdd9e5; padding: 7px 12px; font-size: 13px;
            }
            QLineEdit#searchInput:focus { border: 1px solid #1976d2; }
            QComboBox#filterCombo {
                background: #0d1b2a; border: 1px solid #2a4a6b; border-radius: 6px;
                color: #cdd9e5; padding: 7px 10px; font-size: 13px;
            }
            QComboBox#filterCombo::drop-down { border: none; width: 18px; }
            QComboBox QAbstractItemView {
                background: #0d1b2a; border: 1px solid #2a4a6b;
                color: #cdd9e5; selection-background-color: #1a2e45;
            }
            QDateEdit#datePicker {
                background: #0d1b2a; border: 1px solid #2a4a6b; border-radius: 6px;
                color: #cdd9e5; padding: 7px 10px; font-size: 13px;
            }
            QDateEdit#datePicker::drop-down { border: none; width: 18px; }
            QPushButton#btnPrimary {
                background: #1565c0; color: white; border: none; border-radius: 6px;
                padding: 8px 18px; font-size: 13px; font-weight: 600;
            }
            QPushButton#btnPrimary:hover  { background: #1976d2; }
            QPushButton#btnPrimary:pressed { background: #0d47a1; }
            QPushButton#btnSecondary {
                background: #1e3a5f; color: #8eb3d4; border: 1px solid #2a4a6b;
                border-radius: 6px; padding: 8px 14px; font-size: 13px;
            }
            QPushButton#btnSecondary:hover { background: #243f63; color: #cdd9e5; }
            QTableWidget#mainTable {
                background: #0d1b2a; border: 1px solid #1e3a5f; border-radius: 8px;
                gridline-color: #1a2733; color: #cdd9e5; font-size: 12px;
                selection-background-color: #1a3a5a; selection-color: #e8f4fd;
            }
            QTableWidget#mainTable QHeaderView::section {
                background: #0a1520; color: #64b5f6; border: none;
                border-right: 1px solid #1e3a5f; border-bottom: 2px solid #1976d2;
                padding: 8px 10px; font-size: 12px; font-weight: 600;
            }
            QTableWidget#mainTable::item { padding: 5px 10px; border-bottom: 1px solid #111e2b; }
            QScrollBar:vertical { background: #0d1b2a; width: 8px; border-radius: 4px; }
            QScrollBar::handle:vertical { background: #2a4a6b; border-radius: 4px; min-height: 30px; }
            QScrollBar:horizontal { background: #0d1b2a; height: 8px; }
            QScrollBar::handle:horizontal { background: #2a4a6b; border-radius: 4px; }
            QLabel#statusLabel { color: #5a7fa0; background: transparent; font-size: 12px; }
            QLabel#summaryIncome { color: #81d4a0; background: transparent; font-size: 13px; font-weight: 600; }
            QLabel#summaryExpense { color: #ef9a9a; background: transparent; font-size: 13px; font-weight: 600; }
            QTableWidget#summaryTable {
                background: #0d1b2a; border: 1px solid #1e3a5f; border-radius: 8px;
                gridline-color: #1a2733; color: #cdd9e5; font-size: 12px;
                selection-background-color: #1a3a5a; selection-color: #e8f4fd;
            }
            QTableWidget#summaryTable QHeaderView::section {
                background: #0a1520; color: #64b5f6; border: none;
                border-right: 1px solid #1e3a5f; border-bottom: 2px solid #1976d2;
                padding: 8px 10px; font-size: 12px; font-weight: 600;
            }
            QTableWidget#summaryTable::item { padding: 4px 10px; border-bottom: 1px solid #111e2b; }
        """)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    app = QApplication(sys.argv)
    app.setApplicationName("СНТ Финансовый учёт")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()