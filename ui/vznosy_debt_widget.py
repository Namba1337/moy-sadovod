import os
import re

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core import energy
from core.utils import fmt_money
from ui.energy_card import _NumItem
from ui.vznosy_card import VznosyCardDialog
from ui.rates_widget import VznosyRatesWidget


class VznosyDebtWidget(QWidget):
    """Вкладка контроля долгов по членским взносам."""

    def __init__(self):
        super().__init__()
        self.setAutoFillBackground(True)
        self._df = None
        self._last_debts: dict = {}
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

        self.search = QLineEdit(objectName="searchInput")
        self.search.setPlaceholderText("Поиск по № участка или ФИО")
        self.search.setFixedWidth(280)
        self.search.textChanged.connect(self._apply_filter)
        top.addWidget(self.search)

        self.cb_only_debt = QComboBox(objectName="filterCombo")
        self.cb_only_debt.addItems(["Все участки", "Только должники", "Только оплачено/аванс"])
        self.cb_only_debt.currentIndexChanged.connect(self._apply_filter)
        top.addWidget(self.cb_only_debt)

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

        self.table = QTableWidget(objectName="summaryTable")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "Участок", "Владелец", "Площадь, м²",
            "Начислено", "Оплачено", "Долг / Аванс", "Лет без оплаты",
        ])
        hdr = self.table.horizontalHeader()
        for c, w in enumerate([85, 280, 100, 130, 130, 140, 110]):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(c, w)
        hdr.setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self._open_card)
        lay.addWidget(self.table, 1)

        self.status_lbl = QLabel("Загрузите выписку на вкладке «Детализация»",
                                  objectName="statusLabel")
        lay.addWidget(self.status_lbl)

        self.rates = VznosyRatesWidget()

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
        # Тарифы могли поменяться — пересчитаем
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

        # средняя годовая сумма по тарифу — для подсветки уровня долга
        annual_avg = 0.0
        if rates:
            sums = []
            for r in rates:
                v = energy._to_float(r.get("amount"))
                if v is not None:
                    sums.append(v)
            if sums:
                annual_avg = sum(sums) / len(sums)

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(plots))

        total_debt = 0.0
        total_charged = 0.0
        total_paid = 0.0
        debt_count = 0
        debts_map: dict[str, dict] = {}

        for r, plot in enumerate(plots):
            area = areas.get(plot)
            bal = vznosy.balance_for_plot(plot, area, as_of, rates, adj, self._df)
            owner = ", ".join(owners.get(plot, [])) or "—"

            area_text = f"{area:g}" if area is not None else "—"
            area_color = "#DC2626" if bal.area_missing_warning else (
                "#374151" if area is not None else "#9CA3AF"
            )
            area_tip = ("Не указана площадь — начисление по тарифу за м² невозможно"
                        if bal.area_missing_warning else "")

            color = vznosy.debt_color(bal.debt, annual_avg=annual_avg)
            debts_map[plot] = {"debt": bal.debt, "color": color,
                                "charged": bal.charged, "paid": bal.paid}

            total_debt += bal.debt
            total_charged += bal.charged
            total_paid += bal.paid
            if bal.debt > 0.5:
                debt_count += 1

            years_unpaid_text = "—" if bal.years_unpaid is None else str(bal.years_unpaid)
            years_unpaid_color = "#374151"
            if bal.years_unpaid and bal.years_unpaid >= 2:
                years_unpaid_color = "#DC2626" if bal.years_unpaid >= 3 else "#ffd54f"

            try:
                plot_sort = float(str(plot).split(",")[0])
            except ValueError:
                plot_sort = 0.0

            cells = [
                (f"уч. {plot}", plot_sort, None, "#6366F1", True, ""),
                (owner, owner, None, "#374151", False, ""),
                (area_text, area if area is not None else 0.0, None, area_color, False, area_tip),
                (fmt_money(bal.charged), bal.charged, None,
                 "#f9a825" if bal.charged else "#3a5a7a", False, ""),
                (fmt_money(bal.paid), bal.paid, None,
                 "#059669" if bal.paid else "#3a5a7a", False, ""),
                (fmt_money(bal.debt), bal.debt, color, "#ffffff", True, ""),
                (years_unpaid_text, float(bal.years_unpaid or 0), None,
                 years_unpaid_color, False, ""),
            ]
            for c, (text, value, bg, fg, bold, tip) in enumerate(cells):
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
                if tip:
                    it.setToolTip(tip)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(r, c, it)
            self.table.setRowHeight(r, 28)

        self.table.setSortingEnabled(True)
        self._last_debts = debts_map

        self.status_lbl.setText(
            f"Участков: {len(plots)}  ·  должников: {debt_count}  ·  "
            f"начислено всего: {fmt_money(total_charged)}  ·  "
            f"оплачено всего: {fmt_money(total_paid)}  ·  "
            f"общий долг: {fmt_money(total_debt)}"
        )

        self._apply_filter()

    def _apply_filter(self):
        text = self.search.text().strip().lower()
        mode = self.cb_only_debt.currentText()
        for r in range(self.table.rowCount()):
            plot_item = self.table.item(r, 0)
            owner_item = self.table.item(r, 1)
            debt_item = self.table.item(r, 5)
            if not plot_item or not debt_item:
                continue
            visible = True
            if text:
                hay = (plot_item.text() + " " + (owner_item.text() if owner_item else "")).lower()
                visible = text in hay
            if visible and mode == "Только должники":
                visible = isinstance(debt_item, _NumItem) and debt_item._value > 0.5
            elif visible and mode == "Только оплачено/аванс":
                visible = isinstance(debt_item, _NumItem) and debt_item._value <= 0.5
            self.table.setRowHidden(r, not visible)

    def _open_card(self, row: int, _col: int):
        plot_item = self.table.item(row, 0)
        if not plot_item:
            return
        plot = plot_item.text().replace("уч. ", "").strip()
        as_of = self.date_as_of.date().toPyDate()
        dlg = VznosyCardDialog(plot, self._df, self, as_of=as_of)
        dlg.exec()
        self._rebuild()

    def _export_debtor_receipts(self):
        if not self._last_debts:
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
        for plot, _info in debtors:
            owner = (owners.get(plot, [""])[0] or "").split()
            surname = owner[0] if owner else ""
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
            self.table.horizontalHeaderItem(c).text()
            for c in range(self.table.columnCount())
        ]
        rows = []
        for r in range(self.table.rowCount()):
            if self.table.isRowHidden(r):
                continue
            rows.append([
                (self.table.item(r, c).text() if self.table.item(r, c) else "")
                for c in range(self.table.columnCount())
            ])

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
