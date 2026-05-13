import sys
import pandas as pd
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QStackedWidget, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit, QComboBox,
    QDateEdit, QHBoxLayout, QFrame, QFileDialog, QMessageBox, QSizePolicy
)
from PyQt6.QtCore import Qt, QDate, QSortFilterProxyModel
from PyQt6.QtGui import QFont, QColor, QIcon
import os


class DetailWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.df_full = None  # Полный датафрейм
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # --- Заголовок + кнопка загрузки ---
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

        # --- Панель фильтров ---
        filter_frame = QFrame()
        filter_frame.setObjectName("filterFrame")
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(16, 12, 16, 12)
        filter_layout.setSpacing(12)

        # Поиск по контрагенту / назначению
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍  Поиск по контрагенту или назначению...")
        self.search_input.setObjectName("searchInput")
        self.search_input.textChanged.connect(self.apply_filters)
        filter_layout.addWidget(self.search_input, stretch=3)

        # Тип операции
        self.combo_type = QComboBox()
        self.combo_type.setObjectName("filterCombo")
        self.combo_type.addItems(["Все операции", "Поступления", "Списания"])
        self.combo_type.currentIndexChanged.connect(self.apply_filters)
        filter_layout.addWidget(self.combo_type, stretch=1)

        # Дата от
        lbl_from = QLabel("с")
        lbl_from.setObjectName("filterLabel")
        filter_layout.addWidget(lbl_from)

        self.date_from = QDateEdit()
        self.date_from.setObjectName("datePicker")
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate(2021, 1, 1))
        self.date_from.setDisplayFormat("dd.MM.yyyy")
        self.date_from.dateChanged.connect(self.apply_filters)
        filter_layout.addWidget(self.date_from)

        # Дата до
        lbl_to = QLabel("по")
        lbl_to.setObjectName("filterLabel")
        filter_layout.addWidget(lbl_to)

        self.date_to = QDateEdit()
        self.date_to.setObjectName("datePicker")
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setDisplayFormat("dd.MM.yyyy")
        self.date_to.dateChanged.connect(self.apply_filters)
        filter_layout.addWidget(self.date_to)

        # Кнопка сброса фильтров
        btn_reset = QPushButton("✕  Сбросить")
        btn_reset.setObjectName("btnSecondary")
        btn_reset.clicked.connect(self.reset_filters)
        filter_layout.addWidget(btn_reset)

        layout.addWidget(filter_frame)

        # --- Статус / счётчик строк ---
        self.status_label = QLabel("Файл не загружен")
        self.status_label.setObjectName("statusLabel")
        layout.addWidget(self.status_label)

        # --- Таблица ---
        self.table = QTableWidget()
        self.table.setObjectName("mainTable")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.setShowGrid(True)
        layout.addWidget(self.table)

        # Итоги
        summary_layout = QHBoxLayout()
        self.lbl_income = QLabel("Поступления: —")
        self.lbl_income.setObjectName("summaryIncome")
        self.lbl_expense = QLabel("Списания: —")
        self.lbl_expense.setObjectName("summaryExpense")
        summary_layout.addWidget(self.lbl_income)
        summary_layout.addStretch()
        summary_layout.addWidget(self.lbl_expense)
        layout.addLayout(summary_layout)

    # ------------------------------------------------------------------ #
    #  ЗАГРУЗКА ФАЙЛА
    # ------------------------------------------------------------------ #
    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть файл выписки", "", "Excel файлы (*.xlsx *.xls)"
        )
        if not path:
            return
        try:
            df = pd.read_excel(path, engine="openpyxl")

            # Ожидаемые столбцы из выписки СберБизнес
            expected = ["Номер", "Номер счёта", "Дата", "Контрагент cчёт",
                        "Контрагент", "Поступление", "Списание", "Назначение"]

            # Убираем колонки «Валюта» (их две — «Валюта» и «Валюта.1»)
            cols_to_keep = [c for c in df.columns
                            if not str(c).strip().startswith("Валюта") and str(c).strip() != ""]
            df = df[cols_to_keep]

            # Переименуем «Контрагент cчёт» если опечатка в источнике
            df.rename(columns={"Контрагент cчёт": "Контрагент счёт"}, inplace=True)

            # Преобразуем дату
            df["Дата"] = pd.to_datetime(df["Дата"], dayfirst=True, errors="coerce")

            self.df_full = df

            # Устанавливаем диапазон дат из файла
            min_date = df["Дата"].min()
            max_date = df["Дата"].max()
            if pd.notna(min_date):
                self.date_from.setDate(QDate(min_date.year, min_date.month, min_date.day))
            if pd.notna(max_date):
                self.date_to.setDate(QDate(max_date.year, max_date.month, max_date.day))

            self.apply_filters()

        except Exception as e:
            QMessageBox.critical(self, "Ошибка загрузки", f"Не удалось загрузить файл:\n{e}")

    # ------------------------------------------------------------------ #
    #  ФИЛЬТРАЦИЯ
    # ------------------------------------------------------------------ #
    def apply_filters(self):
        if self.df_full is None:
            return

        df = self.df_full.copy()

        # Фильтр по дате
        d_from = self.date_from.date().toPyDate()
        d_to = self.date_to.date().toPyDate()
        df = df[
            (df["Дата"].dt.date >= d_from) &
            (df["Дата"].dt.date <= d_to)
        ]

        # Фильтр по типу
        op_type = self.combo_type.currentText()
        if op_type == "Поступления":
            df = df[df["Поступление"].notna() & (df["Поступление"] > 0)]
        elif op_type == "Списания":
            df = df[df["Списание"].notna() & (df["Списание"] > 0)]

        # Поиск по тексту
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
        if self.df_full is not None:
            min_date = self.df_full["Дата"].min()
            max_date = self.df_full["Дата"].max()
            if pd.notna(min_date):
                self.date_from.setDate(QDate(min_date.year, min_date.month, min_date.day))
            if pd.notna(max_date):
                self.date_to.setDate(QDate(max_date.year, max_date.month, max_date.day))
        self.apply_filters()

    # ------------------------------------------------------------------ #
    #  ЗАПОЛНЕНИЕ ТАБЛИЦЫ
    # ------------------------------------------------------------------ #
    def _fill_table(self, df: pd.DataFrame):
        self.table.setSortingEnabled(False)
        self.table.clearContents()

        columns = list(df.columns)
        self.table.setColumnCount(len(columns))
        self.table.setRowCount(len(df))
        self.table.setHorizontalHeaderLabels(columns)

        for row_idx, (_, row) in enumerate(df.iterrows()):
            for col_idx, col in enumerate(columns):
                val = row[col]

                if col == "Дата" and pd.notna(val):
                    text = val.strftime("%d.%m.%Y")
                elif col in ("Поступление", "Списание") and pd.notna(val) and val != "":
                    try:
                        text = f"{float(val):,.2f} ₽".replace(",", " ")
                    except:
                        text = str(val)
                else:
                    text = "" if pd.isna(val) else str(val)

                item = QTableWidgetItem(text)

                # Цвет для поступлений/списаний
                if col == "Поступление" and text:
                    item.setForeground(QColor("#2e7d32"))
                elif col == "Списание" and text:
                    item.setForeground(QColor("#c62828"))

                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                self.table.setItem(row_idx, col_idx, item)

        # Ширина столбцов
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # Назначение — растягивается
        if "Назначение" in columns:
            idx = columns.index("Назначение")
            header.setSectionResizeMode(idx, QHeaderView.ResizeMode.Stretch)

        self.table.setSortingEnabled(True)

        # Итоги
        total_in = df["Поступление"].sum() if "Поступление" in df.columns else 0
        total_out = df["Списание"].sum() if "Списание" in df.columns else 0
        self.lbl_income.setText(f"✅  Поступления: {total_in:,.2f} ₽".replace(",", " "))
        self.lbl_expense.setText(f"🔴  Списания: {total_out:,.2f} ₽".replace(",", " "))

        count = len(df)
        self.status_label.setText(f"Показано записей: {count}")


# ====================================================================== #
#  ГЛАВНОЕ ОКНО
# ====================================================================== #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("СНТ — Финансовый учёт")
        self.setMinimumSize(1200, 700)
        self.resize(1400, 800)
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Левая панель навигации ----
        self.nav = QListWidget()
        self.nav.setObjectName("navPanel")
        self.nav.setFixedWidth(200)

        # Логотип / название
        logo_item = QListWidgetItem("💼  СНТ Учёт")
        logo_item.setFlags(Qt.ItemFlag.NoItemFlags)
        logo_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_item.setForeground(QColor("#90caf9"))
        font_logo = QFont()
        font_logo.setPointSize(13)
        font_logo.setBold(True)
        logo_item.setFont(font_logo)
        self.nav.addItem(logo_item)

        # Разделитель
        sep = QListWidgetItem("")
        sep.setFlags(Qt.ItemFlag.NoItemFlags)
        self.nav.addItem(sep)

        # Пункты меню
        items = [
            ("📋  Детализация", 0),
            # В будущем:
            # ("📊  Аналитика", 1),
            # ("⚙️  Настройки", 2),
        ]
        self.nav_indices = {}
        for label, idx in items:
            item = QListWidgetItem(label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self.nav.addItem(item)
            self.nav_indices[self.nav.count() - 1] = idx

        self.nav.currentRowChanged.connect(self._on_nav_changed)

        root.addWidget(self.nav)

        # ---- Правая область контента ----
        self.stack = QStackedWidget()
        self.stack.setObjectName("contentArea")

        self.detail_widget = DetailWidget()
        self.stack.addWidget(self.detail_widget)

        root.addWidget(self.stack, stretch=1)

        # Выбрать первый пункт
        self.nav.setCurrentRow(2)

    def _on_nav_changed(self, row):
        if row in self.nav_indices:
            self.stack.setCurrentIndex(self.nav_indices[row])

    def _apply_styles(self):
        self.setStyleSheet("""
            QMainWindow {
                background: #0f1923;
            }

            /* ---- Навигация ---- */
            QListWidget#navPanel {
                background: #0d1b2a;
                border: none;
                border-right: 1px solid #1e3a5f;
                padding: 0;
                outline: 0;
            }
            QListWidget#navPanel::item {
                color: #8eb3d4;
                padding: 13px 20px;
                font-size: 14px;
                border-left: 3px solid transparent;
            }
            QListWidget#navPanel::item:selected {
                background: #1a2e45;
                color: #64b5f6;
                border-left: 3px solid #1976d2;
            }
            QListWidget#navPanel::item:hover:!selected {
                background: #132336;
                color: #b0cfe8;
            }

            /* ---- Основная область ---- */
            QStackedWidget#contentArea {
                background: #111e2b;
            }
            QWidget {
                background: #111e2b;
                color: #cdd9e5;
                font-size: 13px;
                font-family: 'Segoe UI', 'Arial', sans-serif;
            }

            /* ---- Заголовок страницы ---- */
            QLabel#pageTitle {
                font-size: 20px;
                font-weight: 700;
                color: #e8f4fd;
                background: transparent;
            }

            /* ---- Фрейм фильтров ---- */
            QFrame#filterFrame {
                background: #162131;
                border: 1px solid #1e3a5f;
                border-radius: 8px;
            }
            QLabel#filterLabel {
                color: #7a9bb8;
                background: transparent;
                font-size: 13px;
            }

            /* ---- Поле поиска ---- */
            QLineEdit#searchInput {
                background: #0d1b2a;
                border: 1px solid #2a4a6b;
                border-radius: 6px;
                color: #cdd9e5;
                padding: 7px 12px;
                font-size: 13px;
            }
            QLineEdit#searchInput:focus {
                border: 1px solid #1976d2;
            }

            /* ---- Комбобокс ---- */
            QComboBox#filterCombo {
                background: #0d1b2a;
                border: 1px solid #2a4a6b;
                border-radius: 6px;
                color: #cdd9e5;
                padding: 7px 12px;
                font-size: 13px;
            }
            QComboBox#filterCombo::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background: #0d1b2a;
                border: 1px solid #2a4a6b;
                color: #cdd9e5;
                selection-background-color: #1a2e45;
            }

            /* ---- DateEdit ---- */
            QDateEdit#datePicker {
                background: #0d1b2a;
                border: 1px solid #2a4a6b;
                border-radius: 6px;
                color: #cdd9e5;
                padding: 7px 10px;
                font-size: 13px;
            }
            QDateEdit#datePicker::drop-down {
                border: none;
                width: 20px;
            }

            /* ---- Кнопки ---- */
            QPushButton#btnPrimary {
                background: #1565c0;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 18px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#btnPrimary:hover {
                background: #1976d2;
            }
            QPushButton#btnPrimary:pressed {
                background: #0d47a1;
            }
            QPushButton#btnSecondary {
                background: #1e3a5f;
                color: #8eb3d4;
                border: 1px solid #2a4a6b;
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 13px;
            }
            QPushButton#btnSecondary:hover {
                background: #243f63;
                color: #cdd9e5;
            }

            /* ---- Таблица ---- */
            QTableWidget#mainTable {
                background: #0d1b2a;
                alternate-background-color: #0f1f30;
                border: 1px solid #1e3a5f;
                border-radius: 8px;
                gridline-color: #1a2e45;
                color: #cdd9e5;
                font-size: 12px;
                selection-background-color: #1a3a5a;
                selection-color: #e8f4fd;
            }
            QTableWidget#mainTable QHeaderView::section {
                background: #0a1520;
                color: #64b5f6;
                border: none;
                border-right: 1px solid #1e3a5f;
                border-bottom: 2px solid #1976d2;
                padding: 8px 10px;
                font-size: 12px;
                font-weight: 600;
            }
            QTableWidget#mainTable::item {
                padding: 6px 10px;
                border-bottom: 1px solid #162131;
            }

            /* ---- Скроллбары ---- */
            QScrollBar:vertical {
                background: #0d1b2a;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #2a4a6b;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar:horizontal {
                background: #0d1b2a;
                height: 8px;
            }
            QScrollBar::handle:horizontal {
                background: #2a4a6b;
                border-radius: 4px;
            }

            /* ---- Статус и итоги ---- */
            QLabel#statusLabel {
                color: #5a7fa0;
                background: transparent;
                font-size: 12px;
            }
            QLabel#summaryIncome {
                color: #4caf50;
                background: transparent;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#summaryExpense {
                color: #ef5350;
                background: transparent;
                font-size: 13px;
                font-weight: 600;
            }
        """)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("СНТ Финансовый учёт")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
