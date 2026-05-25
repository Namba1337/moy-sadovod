import hashlib
import json
import os

import pandas as pd
from PyQt6.QtCore import Qt, QDate, QPoint, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QMessageBox, QPushButton, QStyledItemDelegate, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ui.categorization import CATEGORY_COLORS, ALL_CATEGORIES, apply_categorization, categorize_row
from ui.plot_detection import apply_plot_column, get_plot, _PLOTS_FILE


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


def _add_tag(tags_str: str, tag: str) -> str:
    """Добавляет тег в строку тегов, не дублируя существующие."""
    existing = {t.strip() for t in (tags_str or "").split(",") if t.strip()}
    existing.add(tag)
    return ", ".join(sorted(existing))


def _ensure_meta_columns(df: "pd.DataFrame") -> "pd.DataFrame":
    """Добавляет _hash и Теги, если их ещё нет."""
    df = df.copy()
    if "_hash" not in df.columns:
        df["_hash"] = df.apply(lambda r: _compute_hash(r.to_dict()), axis=1)
    if "Теги" not in df.columns:
        df["Теги"] = ""
    return df


class _SortItem(QTableWidgetItem):
    """QTableWidgetItem с корректной числовой и датовой сортировкой."""
    _DATE_FMT = "%d.%m.%Y"

    def __lt__(self, other: "QTableWidgetItem") -> bool:
        a, b = self.text().strip(), other.text().strip()
        if not a:
            return False  # пустые значения — в конец
        if not b:
            return True
        try:
            def _num(s: str) -> float:
                return float(
                    s.replace(" ", "").replace(" ", "")
                     .replace("₽", "").replace(",", ".")
                )
            return _num(a) < _num(b)
        except ValueError:
            pass
        try:
            from datetime import datetime
            return datetime.strptime(a, self._DATE_FMT) < datetime.strptime(b, self._DATE_FMT)
        except ValueError:
            pass
        return a < b


class _EditMarkDelegate(QStyledItemDelegate):
    """Рисует иконку карандаша (Material Icons e3c9) в правой части ячейки,
    если ячейка была отредактирована вручную."""

    _CHAR = ""
    _ICON_FONT: QFont | None = None

    @classmethod
    def _icon_font(cls) -> QFont:
        if cls._ICON_FONT is None:
            f = QFont("Material Icons")
            f.setPixelSize(14)
            cls._ICON_FONT = f
        return cls._ICON_FONT

    def __init__(self, manual_cells: set, table: QTableWidget, parent=None):
        super().__init__(parent)
        self._manual_cells = manual_cells
        self._table = table

    def paint(self, painter, option, index):
        super().paint(painter, option, index)

        item = self._table.item(index.row(), index.column())
        if item is None:
            return
        df_idx = item.data(Qt.ItemDataRole.UserRole)
        hdr = self._table.horizontalHeaderItem(index.column())
        if df_idx is None or hdr is None:
            return
        if (df_idx, hdr.text()) not in self._manual_cells:
            return

        from PyQt6.QtWidgets import QStyle
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
        self._btn_snt  = QPushButton("СНТ Учёт",              objectName="fmtInactive")
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
                "Файл в формате программы СНТ Учёт — столбцы уже приведены к нужному виду")

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
        self.combo_cat = QComboBox()
        for cat in ALL_CATEGORIES:
            self.combo_cat.addItem(cat)
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


class DetailWidget(QWidget):
    dataLoaded = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setAutoFillBackground(True)
        self.df_full = None
        self._manual_rows: set[int] = set()
        self._manual_cells: set[tuple[int, str]] = set()
        self._setup_ui()
        self.table.setItemDelegate(
            _EditMarkDelegate(self._manual_cells, self.table, self)
        )

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
        self.table.setAlternatingRowColors(True)
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
        existing_hashes = (
            set(self.df_full["_hash"])
            if "_hash" in self.df_full.columns else set()
        )
        row_data["_hash"] = new_hash
        row_data["Теги"] = _add_tag("", "Дубль") if new_hash in existing_hashes else ""

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
                existing_hashes = set(existing["_hash"])
                seen = set(existing_hashes)
                tags_list = []
                for _, row in df.iterrows():
                    h = row["_hash"]
                    if h in seen:
                        tags_list.append(_add_tag("", "Дубль"))
                    else:
                        tags_list.append("")
                        seen.add(h)
                df["Теги"] = tags_list
                new_start = int(existing.index.max()) + 1 if len(existing) > 0 else 0
                df = df.reset_index(drop=True)
                df.index = df.index + new_start
                self.df_full = pd.concat([existing, df])
            else:
                seen: set[str] = set()
                tags_list = []
                for _, row in df.iterrows():
                    h = row["_hash"]
                    if h in seen:
                        tags_list.append(_add_tag("", "Дубль"))
                    else:
                        tags_list.append("")
                        seen.add(h)
                df["Теги"] = tags_list
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
        drop_cols = {"Номер", "Номер счёта", "Контрагент счёт", "Контрагент cчёт"}
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
        self.table.viewport().update()

    def refresh_plot_column(self):
        """Пересчитывает столбец «Участок» по актуальным данным из snt_plots.json.
        Строки, вручную отредактированные пользователем, не перезаписываются."""
        if self.df_full is None:
            return
        df_new = apply_plot_column(self.df_full)
        if "Участок" not in self.df_full.columns:
            self.df_full = df_new
        else:
            auto_mask = ~self.df_full.index.isin(self._manual_rows)
            self.df_full.loc[auto_mask, "Участок"] = df_new.loc[auto_mask, "Участок"]
        self.apply_filters()

    def apply_filters(self):
        if self.df_full is None:
            return
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

    def _export_excel(self):
        if self.df_full is None:
            QMessageBox.warning(self, "Нет данных", "Сначала загрузите файл выписки.")
            return

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
            df = df[df["Категория"] == cat_filter]

        search = self.search_input.text().strip().lower()
        if search:
            mask = (
                df["Контрагент"].astype(str).str.lower().str.contains(search, na=False) |
                df["Назначение"].astype(str).str.lower().str.contains(search, na=False)
            )
            df = df[mask]

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

    def _fill_table(self, df: pd.DataFrame):
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.clearContents()

        # Скрываем служебные колонки (начинаются с "_")
        columns = [c for c in df.columns if not str(c).startswith("_")]
        self.table.setColumnCount(len(columns))
        self.table.setRowCount(len(df))
        self.table.setHorizontalHeaderLabels(columns)

        col_widths = {
            "Дата": 95, "Контрагент": 260, "Сумма": 140,
            "Назначение": 340, "Категория": 210, "Участок": 80, "Теги": 120,
        }

        for row_idx, (df_idx, row) in enumerate(df.iterrows()):
            cat       = str(row.get("Категория", "Прочее"))
            row_color = CATEGORY_COLORS.get(cat, QColor(55, 55, 60))

            for col_idx, col in enumerate(columns):
                val = row[col]
                if col == "Сумма":
                    num = pd.to_numeric(val, errors="coerce")
                    if pd.notna(num) and num > 0:
                        text = f"{num:,.2f} ₽".replace(",", " ")
                        fg   = QColor("#059669")
                    elif pd.notna(num) and num < 0:
                        text = f"−{abs(num):,.2f} ₽".replace(",", " ")
                        fg   = QColor("#DC2626")
                    else:
                        text = ""
                        fg   = QColor("#374151")
                    item = _SortItem(text)
                    item.setForeground(fg)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
                elif col == "Дата" and pd.notna(val):
                    text = val.strftime("%d.%m.%Y")
                    item = _SortItem(text)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                elif col == "Теги":
                    text = "" if pd.isna(val) else str(val)
                    item = _SortItem(text)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                    if "Дубль" in text:
                        item.setForeground(QColor("#D97706"))
                else:
                    text = "" if pd.isna(val) else str(val)
                    item = _SortItem(text)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

                item.setBackground(row_color)
                item.setData(Qt.ItemDataRole.UserRole, df_idx)
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
        self.table.blockSignals(False)

        if "Сумма" in df.columns:
            s = pd.to_numeric(df["Сумма"], errors="coerce")
            total_in  = s[s > 0].sum()
            total_out = s[s < 0].abs().sum()
        else:
            total_in = total_out = 0.0
        self.lbl_income.setText(f"✅  Поступления: {total_in:,.2f} ₽".replace(",", " "))
        self.lbl_expense.setText(f"🔴  Списания: {total_out:,.2f} ₽".replace(",", " "))
        self.status_label.setText(f"Показано записей: {len(df)}")

    def _show_context_menu(self, pos: QPoint):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        self.table.selectRow(row)

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #F8F9FA;
                border: 1px solid #D1D5DB;
                color: #374151;
                font-size: 13px;
                padding: 4px;
            }
            QMenu::item {
                padding: 8px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: #EEF2FF;
                color: #6366F1;
            }
            QMenu::separator {
                height: 1px;
                background: #E5E7EB;
                margin: 4px 8px;
            }
        """)

        act_dup = QAction("Дублировать строку", self)
        act_dup.triggered.connect(lambda: self._duplicate_row(row))
        menu.addAction(act_dup)

        menu.addSeparator()

        act_del = QAction("Удалить строку", self)
        act_del.triggered.connect(lambda: self._delete_row(row))
        menu.addAction(act_del)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _duplicate_row(self, row: int):
        """Вставляет копию строки row сразу под ней."""
        col_count = self.table.columnCount()
        insert_at = row + 1

        src_item0 = self.table.item(row, 0)
        src_df_idx = src_item0.data(Qt.ItemDataRole.UserRole) if src_item0 else None

        new_df_idx = None
        if self.df_full is not None and src_df_idx is not None and src_df_idx in self.df_full.index:
            new_df_idx = int(self.df_full.index.max()) + 1
            self.df_full.loc[new_df_idx] = self.df_full.loc[src_df_idx].copy()

        # ВАЖНО: отключаем сортировку перед вставкой строки.
        # При setSortingEnabled(True) вызов setItem() немедленно пересортировывает
        # таблицу внутри самого вызова (blockSignals не помогает — сортировка
        # не идёт через сигналы). Строка «улетает» на другую позицию, все
        # последующие item(row, col) читают чужие данные → краш.
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.insertRow(insert_at)
        for col in range(col_count):
            src_item = self.table.item(row, col)
            if src_item:
                new_item = _SortItem(src_item.text())
                new_item.setBackground(src_item.background())
                new_item.setForeground(src_item.foreground())
                new_item.setTextAlignment(src_item.textAlignment())
                if new_df_idx is not None:
                    new_item.setData(Qt.ItemDataRole.UserRole, new_df_idx)
                self.table.setItem(insert_at, col, new_item)
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)

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
            item0 = self.table.item(row, 0)
            df_idx = item0.data(Qt.ItemDataRole.UserRole) if item0 else None
            self.table.removeRow(row)
            if self.df_full is not None and df_idx is not None and df_idx in self.df_full.index:
                self.df_full = self.df_full.drop(index=df_idx)
            self._update_summary()

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

        if col == "Категория":
            cat = item.text().strip()
            row_color = CATEGORY_COLORS.get(cat, QColor(55, 55, 60))
            for c in range(self.table.columnCount()):
                cell = self.table.item(item.row(), c)
                if cell:
                    cell.setBackground(row_color)

        if col == "Сумма":
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
            raw_num = item.text().replace(" ", "").replace("−", "-").replace("₽", "").replace(",", ".")
            try:
                num_val = float(raw_num)
                item.setForeground(QColor("#059669") if num_val > 0 else QColor("#DC2626"))
            except ValueError:
                item.setForeground(QColor("#374151"))

        self.table.blockSignals(False)

        if self.df_full is not None:
            df_idx = item.data(Qt.ItemDataRole.UserRole)
            if df_idx is not None and df_idx in self.df_full.index:
                new_text = item.text().strip()
                if col == "Сумма":
                    raw = new_text.replace(" ", "").replace("−", "-").replace("₽", "").replace(",", ".")
                    try:
                        self.df_full.at[df_idx, "Сумма"] = float(raw) if raw else float("nan")
                    except ValueError:
                        pass
                elif col == "Дата":
                    try:
                        self.df_full.at[df_idx, col] = pd.to_datetime(new_text, dayfirst=True)
                    except Exception:
                        pass
                else:
                    self.df_full.at[df_idx, col] = new_text
                    if col == "Категория" and new_text:
                        if self.combo_cat.findText(new_text) == -1:
                            self.combo_cat.addItem(new_text)
                self._manual_rows.add(df_idx)
                self._manual_cells.add((df_idx, col))

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
                    raw = cell.text().replace(" ", "").replace("₽", "").replace("−", "-").replace(",", ".")
                    try:
                        val = float(raw)
                    except ValueError:
                        continue
                    if name == "Сумма":
                        if val > 0:
                            total_in  += val
                        elif val < 0:
                            total_out += abs(val)

        self.lbl_income.setText(f"✅  Поступления: {total_in:,.2f} ₽".replace(",", " "))
        self.lbl_expense.setText(f"🔴  Списания: {total_out:,.2f} ₽".replace(",", " "))
        self.status_label.setText(f"Показано записей: {self.table.rowCount()}")
