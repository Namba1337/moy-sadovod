import os
import re
from datetime import date

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core import energy
from core.utils import fmt_money
from ui.energy_card import _NumItem, PlotCardDialog
from ui.rates_widget import RatesWidget


class EnergyDebtWidget(QWidget):
    """Вкладка контроля долгов по электроэнергии."""

    def __init__(self):
        super().__init__()
        self.setAutoFillBackground(True)
        self._df = None
        self._setup_ui()

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
        self.cb_only_debt.addItems(["Все участки", "Только должники", "Только аванс/0"])
        self.cb_only_debt.currentIndexChanged.connect(self._apply_filter)
        top.addWidget(self.cb_only_debt)

        btn = QPushButton("🔄  Пересчитать", objectName="btnPrimary")
        btn.clicked.connect(self._rebuild)
        top.addWidget(btn)

        self.btn_mass_pdf = QPushButton("📄  Квитанции должникам", objectName="btnSecondary")
        self.btn_mass_pdf.clicked.connect(self._export_debtor_receipts)
        top.addWidget(self.btn_mass_pdf)

        btn_rates = QPushButton("📐  Нормативы", objectName="btnSecondary")
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
            "background:#F9FAFB;border:1px solid #E5E7EB;border-radius:6px;"
            "padding:10px 14px;color:#374151;font-size:12px;"
        )
        lay.addWidget(self.recon_lbl)

        self.status_lbl = QLabel("Загрузите выписку на вкладке «Детализация»",
                                  objectName="statusLabel")
        lay.addWidget(self.status_lbl)

        self.rates = RatesWidget()

    def _open_rates_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Нормативы — тарифы на электроэнергию")
        dlg.resize(700, 500)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.rates)
        dlg.exec()
        lay.removeWidget(self.rates)
        self.rates.setParent(self)  # type: ignore[arg-type]

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
        if self._df is not None and not self._df.empty:
            baseline["start_date"] = self._df["Дата"].min().date().isoformat()
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

            try:
                plot_sort = float(str(plot).split(",")[0])
            except ValueError:
                plot_sort = 0.0
            try:
                reading_sort = float(last_reading_text) if last_reading_text != "—" else -1.0
            except ValueError:
                reading_sort = -1.0
            date_sort = float(bal.last_reading[0] * 100 + bal.last_reading[1]) if bal.last_reading else -1.0

            cells = [
                (f"уч. {plot}", plot_sort, None, "#6366F1", True),
                (owner, owner, None, "#374151", False),
                (last_reading_text, reading_sort, None, "#374151", False),
                (last_date_text, date_sort, None, "#9CA3AF", False),
                (fmt_money(bal.charged), bal.charged, None, "#f9a825" if bal.charged else "#3a5a7a", False),
                (fmt_money(bal.paid), bal.paid, None, "#059669" if bal.paid else "#3a5a7a", False),
                (fmt_money(bal.baseline) if bal.baseline else "—", bal.baseline, None,
                 "#c97c7c" if bal.baseline else "#3a5a7a", False),
                (fmt_money(bal.debt), bal.debt, color, "#ffffff", True),
                ("—" if bal.months_without_payment is None else str(bal.months_without_payment),
                 bal.months_without_payment or 0, None,
                 "#DC2626" if (bal.months_without_payment or 0) > 3 else "#374151", False),
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
            f"общий долг: {fmt_money(total_debt)}"
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
                          + (f" ({fmt_money(rec.loss_rub)})" if rec.loss_rub else ""))
            self.recon_lbl.setText(
                f"<b>Сверка с поставщиком</b> ({rec.period_from} — {rec.period_to}):  "
                f"начислено садоводам {fmt_money(rec.charged_total)}  ·  "
                f"собрано {fmt_money(rec.collected_total)}  ·  "
                f"уплачено в Пермэнергосбыт {fmt_money(rec.paid_to_supplier)}  ·  "
                f"расхождение {fmt_money(rec.collected_total - rec.paid_to_supplier)}"
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
        as_of = self.date_as_of.date().toPyDate()
        dlg = PlotCardDialog(plot, self._df, self, as_of=as_of)
        dlg.exec()
        # после возможной правки показаний / замены счётчика — пересчитать
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

    def _export_excel(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт таблицы", "электроэнергия_долги.xlsx", "Excel (*.xlsx)")
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
            ws.title = "Долги по электроэнергии"
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
