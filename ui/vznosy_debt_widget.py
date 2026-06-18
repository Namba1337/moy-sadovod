import os
import re

from PyQt6.QtCore import Qt, QDate, QModelIndex, QAbstractItemModel, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDateEdit, QDialog, QFileDialog,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QTreeView, QVBoxLayout, QWidget,
)

from core import energy
from core.utils import fmt_money
from ui.energy_card import _NumItem
from ui.plots_widget import (
    _SortHeaderView, _ClipFrame, _TREE_STYLE, _SB_W,
    _is_visible, _is_owner, _owner_name,
)
from ui.vznosy_card import VznosyCardDialog
from ui.rates_widget import VznosyRatesWidget

# Светлые варианты цветов долга для таблицы.
_DEBT_COLOR_LIGHT = {
    "#2e7d32": "#c8e6c9",
    "#f9a825": "#fff9c4",
    "#ef6c00": "#ffe0b2",
    "#c62828": "#ffcdd2",
}


# ============================================================================ #
#  Модель данных                                                               #
# ============================================================================ #

class _VznosyModel(QAbstractItemModel):
    """Плоская модель для таблицы долгов по ЧВ."""

    COLUMNS = [
        "Участок", "Собственник", "Площадь, м²",
        "Начислено", "Оплачено", "Долг / Аванс",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def load(self, rows: list[dict]):
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def top_nodes(self) -> list[dict]:
        return self._rows

    # -- core tree (flat) ---------------------------------------------------- #

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        return self.createIndex(row, column, self._rows[row])

    def parent(self, index):
        return QModelIndex()

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLUMNS)

    # -- read ---------------------------------------------------------------- #

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
        if column < 0 or column >= len(self.COLUMNS):
            return
        col = self.COLUMNS[column]
        sort_key = f"_sort_{col}"
        self.layoutAboutToBeChanged.emit()
        self._rows.sort(
            key=lambda r: (
                r.get(sort_key) is None,
                r.get(sort_key, 0.0),
            ),
            reverse=(order == Qt.SortOrder.DescendingOrder),
        )
        self.layoutChanged.emit()


# ============================================================================ #
#  Виджет вкладки «Членские взносы»                                             #
# ============================================================================ #

class VznosyDebtWidget(QWidget):
    """Вкладка контроля долгов по членским взносам."""

    def __init__(self):
        super().__init__()
        self.setAutoFillBackground(True)
        self._df = None
        self._last_debts: dict = {}
        self._col_syncing = False
        self._search_filters: dict[int, str] = {}
        self._setup_ui()
        self._rebuild()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)

        top = QHBoxLayout()
        top.addStretch()

        top.addWidget(QLabel("на дату:", objectName="filterLabel"))
        self.date_as_of = QDateEdit(calendarPopup=True, objectName="datePicker",
                                    displayFormat="dd.MM.yyyy")
        self.date_as_of.setDate(QDate.currentDate())
        self.date_as_of.setMaximumDate(QDate.currentDate())
        self.date_as_of.dateChanged.connect(self._rebuild)
        top.addWidget(self.date_as_of)

        btn = QPushButton("🔄  Пересчитать", objectName="btnPrimary")
        btn.clicked.connect(self._rebuild)
        top.addWidget(btn)

        self.btn_mass_pdf = QPushButton("📄  Квитанции должникам", objectName="btnSecondary")
        self.btn_mass_pdf.clicked.connect(self._export_debtor_receipts)
        top.addWidget(self.btn_mass_pdf)

        btn_rates = QPushButton("📅  Периоды", objectName="btnSecondary")
        btn_rates.clicked.connect(self._open_rates_dialog)
        top.addWidget(btn_rates)

        btn_excel = QPushButton("Экспорт в Excel", objectName="btnSecondary")
        btn_excel.clicked.connect(self._export_excel)
        top.addWidget(btn_excel)

        lay.addLayout(top)

        # Легенда
        legend = QHBoxLayout()
        legend.setSpacing(20)
        for color, text in [
            ("#059669", "■  без долга / аванс"),
            ("#f9a825", "■  небольшой долг"),
            ("#ef6c00", "■  средний"),
            ("#DC2626", "■  крупный"),
        ]:
            lb = QLabel(text)
            lb.setStyleSheet(
                f"color:{color};background:transparent;font-size:11px;"
            )
            legend.addWidget(lb)
        legend.addStretch()
        lay.addLayout(legend)

        # ── Модель ──────────────────────────────────────────────────────────
        self.model = _VznosyModel(self)

        # ── Шапка (внешняя) ─────────────────────────────────────────────────
        self.hdr_view = _SortHeaderView()
        self.hdr_view.setModel(self.model)
        self.hdr_view.sortIndicatorChanged.connect(self._on_sort_changed)

        hdr_frame = QFrame()
        hdr_frame.setStyleSheet("background: #C9D8E2; border: none;")
        hdr_inner = QHBoxLayout(hdr_frame)
        hdr_inner.setContentsMargins(0, 0, 0, 0)
        hdr_inner.setSpacing(0)
        hdr_inner.addWidget(self.hdr_view)
        sb_stub = QWidget()
        sb_stub.setFixedWidth(_SB_W)
        sb_stub.setStyleSheet("background: #C9D8E2; border: none;")
        hdr_inner.addWidget(sb_stub)

        # ── Дерево (плоское) ────────────────────────────────────────────────
        self.tree = QTreeView(objectName="mainTable")
        self.tree.setModel(self.model)
        self.tree.header().hide()
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
        self.tree.doubleClicked.connect(self._open_card)

        # Поиск в шапке: Участок, Собственник
        self.hdr_view.add_search_col(_VznosyModel.COLUMNS.index("Участок"))
        self.hdr_view.add_search_col(_VznosyModel.COLUMNS.index("Собственник"))
        self.hdr_view.searchChanged.connect(self._on_search_changed)

        # Синхронизация ширин колонок между hdr_view и tree
        self.tree.header().sectionResized.connect(self._on_tree_hdr_resized)
        self.hdr_view.sectionResized.connect(self._on_hdr_view_resized)

        # ── Единый контейнер ─────────────────────────────────────────────────
        table_outer = _ClipFrame(QColor("#D5DCE4"), 6)
        outer_inner = QVBoxLayout(table_outer)
        outer_inner.setContentsMargins(0, 0, 0, 0)
        outer_inner.setSpacing(0)
        outer_inner.addWidget(hdr_frame)
        outer_inner.addWidget(self.tree, stretch=1)
        table_outer.finish_setup()

        self.status_lbl = QLabel("Загрузите выписку на вкладке «Детализация»",
                                  objectName="statusLabel")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        table_vbox = QVBoxLayout()
        table_vbox.setSpacing(4)
        table_vbox.setContentsMargins(0, 0, 0, 0)
        table_vbox.addWidget(table_outer, stretch=1)
        table_vbox.addWidget(self.status_lbl)
        lay.addLayout(table_vbox)

        self.rates = VznosyRatesWidget()

    # -- синхронизация колонок ----------------------------------------------- #

    def _on_tree_hdr_resized(self, logical, old_size, new_size):
        if self._col_syncing:
            return
        self._col_syncing = True
        self.hdr_view.resizeSection(logical, new_size)
        self._col_syncing = False

    def _on_hdr_view_resized(self, logical, old_size, new_size):
        if self._col_syncing:
            return
        self._col_syncing = True
        self.tree.header().resizeSection(logical, new_size)
        self._col_syncing = False

    # -- сортировка ---------------------------------------------------------- #

    def _on_sort_changed(self, col, order):
        self.model.sort(col, order)
        self.hdr_view.setSortIndicator(col, order)

    # -- поиск --------------------------------------------------------------- #

    def _on_search_changed(self, col, text):
        self._search_filters[col] = text.strip().lower()
        self._rebuild()

    # -- публичный API ------------------------------------------------------- #

    def _open_rates_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Периоды членских взносов")
        dlg.resize(720, 500)
        dlg_lay = QVBoxLayout(dlg)
        dlg_lay.setContentsMargins(0, 0, 0, 0)
        dlg_lay.addWidget(self.rates)
        dlg.exec()
        dlg_lay.removeWidget(self.rates)
        self.rates.setParent(self)  # type: ignore[arg-type]
        self._rebuild()

    def refresh(self, df):
        self._df = df
        self._rebuild()

    def _plot_list(self) -> list[str]:
        plots = energy.load_plots()
        nums = [str(p.get("num", "")) for p in plots if p.get("num")]
        def _key(s):
            try:
                return (0, int(s))
            except ValueError:
                return (1, s)
        return sorted(set(nums), key=_key)

    def _rebuild(self):
        from core import vznosy
        as_of = self.date_as_of.date().toPyDate()
        rates = vznosy.load_rates()
        adj = vznosy.load_adjustments()
        areas = vznosy.plot_area_map()
        owners = energy.owners_map()
        plots = self._plot_list()

        annual_avg = 0.0
        if rates:
            sums = []
            for r in rates:
                v = energy._to_float(r.get("amount"))
                if v is not None:
                    sums.append(v)
            if sums:
                annual_avg = sum(sums) / len(sums)

        rows: list[dict] = []
        total_debt = 0.0
        total_charged = 0.0
        total_paid = 0.0
        debt_count = 0
        debts_map: dict[str, dict] = {}

        for plot in plots:
            area = areas.get(plot)
            bal = vznosy.balance_for_plot(plot, area, as_of, rates, adj, self._df)
            owners_list = owners.get(plot, []) or []
            visible = next((o for o in owners_list if _is_visible(o)), None)
            main = next((o for o in owners_list if _is_owner(o)),
                         owners_list[0] if owners_list else None)
            owner_text = _owner_name(visible or main) if (visible or main) else "—"

            area_text = f"{area:g}" if area is not None else "—"
            area_color = "#DC2626" if bal.area_missing_warning else (
                "#374151" if area is not None else "#9CA3AF"
            )
            area_tip = ("Не указана площадь — начисление по тарифу за м² невозможно"
                        if bal.area_missing_warning else "")

            color = vznosy.debt_color(bal.debt, annual_avg=annual_avg)

            try:
                plot_sort = float(str(plot).split(",")[0])
            except ValueError:
                plot_sort = 0.0

            row: dict = {
                "_text_Участок": plot,
                "_sort_Участок": plot_sort,

                "_text_Собственник": owner_text,
                "_sort_Собственник": owner_text,
                "_fg_Собственник": "#374151",

                "_text_Площадь, м²": area_text,
                "_sort_Площадь, м²": area if area is not None else 0.0,
                "_fg_Площадь, м²": area_color,
                "_tip_Площадь, м²": area_tip,

                "_text_Начислено": fmt_money(bal.charged),
                "_sort_Начислено": bal.charged,
                "_fg_Начислено": "#f9a825" if bal.charged else "#3a5a7a",

                "_text_Оплачено": fmt_money(bal.paid),
                "_sort_Оплачено": bal.paid,
                "_fg_Оплачено": "#059669" if bal.paid else "#3a5a7a",

                "_text_Долг / Аванс": fmt_money(bal.debt),
                "_sort_Долг / Аванс": bal.debt,
                "_bg_Долг / Аванс": _DEBT_COLOR_LIGHT.get(color, color),
                "_bold_Долг / Аванс": True,
            }
            rows.append(row)

            total_charged += bal.charged
            total_paid += bal.paid
            total_debt += bal.debt
            if bal.debt > 0.5:
                debt_count += 1
            debts_map[plot] = {"debt": bal.debt, "charged": bal.charged,
                               "paid": bal.paid, "owner": owner_text}

        # Применяем поиск
        for col_idx, text in self._search_filters.items():
            if not text:
                continue
            col_name = _VznosyModel.COLUMNS[col_idx]
            if col_name == "Участок":
                rows = [r for r in rows if text in r.get("_text_Участок", "").lower()]
            elif col_name == "Собственник":
                rows = [r for r in rows if text in r.get("_text_Собственник", "").lower()]

        self.model.load(rows)

        # Ширины колонок
        for h in (self.hdr_view, self.tree.header()):
            h.setStretchLastSection(False)
            h.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            h.resizeSection(0, 85)
            h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            h.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            h.resizeSection(2, 120)
            h.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
            h.resizeSection(3, 130)
            h.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
            h.resizeSection(4, 130)
            h.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
            h.resizeSection(5, 140)

        self._last_debts = debts_map
        self.status_lbl.setText(
            f"Участков: {len(plots)}  ·  должников: {debt_count}  ·  "
            f"начислено всего: {fmt_money(total_charged)}  ·  "
            f"оплачено всего: {fmt_money(total_paid)}  ·  "
            f"общий долг: {fmt_money(total_debt)}"
        )

    def _open_card(self, index: QModelIndex):
        if not index.isValid():
            return
        node = index.internalPointer()
        if node is None:
            return
        plot_text = node.get("_text_Участок", "").replace("уч. ", "").strip()
        if not plot_text:
            return
        as_of = self.date_as_of.date().toPyDate()
        dlg = VznosyCardDialog(plot_text, self._df, self, as_of=as_of)
        dlg.exec()
        self._rebuild()

    def _export_debtor_receipts(self):
        if self._df is None or len(self._df) == 0:
            QMessageBox.information(self, "Квитанции", "Сначала загрузите выписку.")
            return
        from core import vznosy
        rates = vznosy.load_rates()
        adj = vznosy.load_adjustments()
        areas = vznosy.plot_area_map()
        plot_recs = energy.plots_by_num()
        as_of = self.date_as_of.date().toPyDate()

        # Должник = участок, где задолженность есть у ТЕКУЩЕГО собственника
        # (квитанция выставляется ему; долг прежнего сюда не входит).
        debtors: list[tuple[str, str]] = []
        for plot in self._plot_list():
            rec = plot_recs.get(plot, {})
            owners_list = rec.get("owners", []) or []
            rows = vznosy.balances_by_owner(
                plot, areas.get(plot), as_of, rates, adj, self._df,
                owners_list, ownership_form=rec.get("ownership_form"))
            cur_debt = sum(r.debt for r in rows if r.is_current)
            if cur_debt > 0.5:
                cur_name = next((r.name for r in rows if r.is_current), "")
                debtors.append((plot, cur_name))

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

        ok = 0
        errors = []
        for plot, cur_name in debtors:
            surname = cur_name.split()[0] if cur_name else ""
            fname = f"Уч_{plot}_ЧВ"
            if surname:
                safe_surname = re.sub(r"[^\w\-]", "_", surname)
                fname += f"_{safe_surname}"
            fname += f"_{as_of.isoformat()}.pdf"
            fname = fname.replace("/", "-")
            path = os.path.join(folder, fname)
            try:
                receipt.save_vznosy_receipt_pdf(plot, self._df, path, as_of=as_of)
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

    def _export_excel(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт таблицы", "членские_взносы_долги.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        if not path.endswith(".xlsx"):
            path += ".xlsx"

        headers = [
            self.hdr_view.headerData(c, Qt.Orientation.Horizontal)
            for c in range(self.model.columnCount())
        ]
        rows = []
        for r in range(self.model.rowCount()):
            row_data = []
            for c in range(self.model.columnCount()):
                idx = self.model.index(r, c)
                row_data.append(self.model.data(idx, Qt.ItemDataRole.DisplayRole) or "")
            rows.append(row_data)

        try:
            import openpyxl
            from openpyxl.styles import Font, Alignment
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Долги по членским взносам"
            ws.append(headers)
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal="center")
            for row in rows:
                ws.append(row)
            wb.save(path)
            QMessageBox.information(self, "Экспорт завершён", f"Файл сохранён:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка экспорта", str(e))
