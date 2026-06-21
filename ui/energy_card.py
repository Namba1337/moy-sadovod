"""Карточки и диалоги для вкладки «Долги по электроэнергии»."""
from __future__ import annotations

import getpass
from datetime import date

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from core import energy
from core.utils import fmt_money


def _next_period_start(today: date | None = None) -> date:
    """Первое число следующего месяца — момент применения нового типа расчёта."""
    today = today or date.today()
    return date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)


class _PdfPeriodDialog(QDialog):
    """Диалог выбора начала и конца периода для PDF-квитанции."""

    _MONTHS = ["янв", "фев", "мар", "апр", "май", "июн",
               "июл", "авг", "сен", "окт", "ноя", "дек"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Период квитанции")
        self.setMinimumWidth(340)

        today = date.today()
        default_year = today.year - 3

        layout = QVBoxLayout(self)

        info = QLabel("Укажите период для квитанции.\n"
                      "Оставьте «Весь период» для полной истории.")
        info.setWordWrap(True)
        layout.addWidget(info)

        form_layout = QFormLayout()
        form_layout.setSpacing(6)

        # Начало периода
        since_row = QHBoxLayout()
        self._since_month = QComboBox()
        self._since_month.addItems(self._MONTHS)
        self._since_month.setCurrentIndex(0)
        since_row.addWidget(self._since_month)
        self._since_year = QSpinBox()
        self._since_year.setRange(2000, today.year)
        self._since_year.setValue(default_year)
        self._since_year.setGroupSeparatorShown(False)
        since_row.addWidget(self._since_year)
        form_layout.addRow("С месяца:", since_row)

        # Конец периода
        until_row = QHBoxLayout()
        self._until_month = QComboBox()
        self._until_month.addItems(self._MONTHS)
        self._until_month.setCurrentIndex(today.month - 1)
        until_row.addWidget(self._until_month)
        self._until_year = QSpinBox()
        self._until_year.setRange(2000, today.year + 1)
        self._until_year.setValue(today.year)
        self._until_year.setGroupSeparatorShown(False)
        until_row.addWidget(self._until_year)
        form_layout.addRow("По месяц:", until_row)

        layout.addLayout(form_layout)

        self._zero_cb = QCheckBox("Без учёта задолженности прошлых периодов")
        layout.addWidget(self._zero_cb)

        buttons = QDialogButtonBox(self)
        self._btn_period = buttons.addButton("За период", QDialogButtonBox.ButtonRole.AcceptRole)
        self._btn_all = buttons.addButton("Весь период", QDialogButtonBox.ButtonRole.ResetRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        self._btn_all.clicked.connect(self._accept_all)
        self._btn_period.clicked.connect(self.accept)
        layout.addWidget(buttons)

        self._use_since = True

    def _accept_all(self):
        self._use_since = False
        self.accept()

    def since_date(self) -> date | None:
        if not self._use_since:
            return None
        return date(self._since_year.value(), self._since_month.currentIndex() + 1, 1)

    def as_of_date(self) -> date:
        import calendar
        y = self._until_year.value()
        m = self._until_month.currentIndex() + 1
        last_day = calendar.monthrange(y, m)[1]
        return date(y, m, last_day)

    @property
    def zero_opening(self) -> bool:
        return self._zero_cb.isChecked()


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
        title.setStyleSheet("font-size:14px;font-weight:700;color:#111827;")
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
            QDialog { background: #FFFFFF; color: #374151; }
            QLabel { background: transparent; color: #374151; font-size: 13px; }
            QLineEdit, QDateEdit {
                background: #F8F9FA; border: 1px solid #D1D5DB;
                border-radius: 5px; color: #374151; padding: 6px 8px; font-size: 13px;
            }
            QLineEdit:focus, QDateEdit:focus { border: 1px solid #6366F1; }
            QDialogButtonBox QPushButton {
                background: #4F46E5; color: white; border: none;
                border-radius: 6px; padding: 7px 18px; font-size: 13px; font-weight: 600;
            }
            QDialogButtonBox QPushButton:hover { background: #6366F1; }
            QDialogButtonBox QPushButton[text='Отмена'] {
                background: #E5E7EB; color: #6B7280;
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


class BillingTypeDialog(QDialog):
    """Редактирование типа расчёта за электроэнергию для участка.

    Изменения сохраняются прямо в snt_plots.json. Смена типа применяется
    со следующего расчётного периода, фиксируется в истории участка.
    """

    def __init__(self, plot: str, parent=None):
        super().__init__(parent)
        self._plot = str(plot)
        self._plots = energy.load_plots()
        self._rec = next(
            (p for p in self._plots if str(p.get("num", "")) == self._plot), None
        )
        self._created = self._rec is None
        if self._rec is None:
            self._rec = {"num": self._plot}
        cur = self._rec.get("billing_type") or energy.BILLING_METER
        if cur not in energy.BILLING_TYPES:
            cur = energy.BILLING_METER
        self._orig = cur

        self.setWindowTitle(f"Тип расчёта — уч. {self._plot}")
        self.setMinimumWidth(460)
        self.setModal(True)
        self._saved = False
        self._setup_ui(cur)
        self._apply_styles()

    def _make_date_edit(self, iso: str | None) -> QDateEdit:
        de = QDateEdit(calendarPopup=True)
        de.setDisplayFormat("dd.MM.yyyy")
        d = energy._parse_iso(iso or "")
        de.setDate(QDate(d.year, d.month, d.day) if d else QDate.currentDate())
        return de

    def _setup_ui(self, cur: str):
        rec = self._rec
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 18)
        lay.setSpacing(12)

        title = QLabel(f"Тип расчёта за электроэнергию — участок {self._plot}")
        title.setStyleSheet("font-size:14px;font-weight:700;color:#111827;")
        lay.addWidget(title)

        self.cb_billing = QComboBox()
        for bt in energy.BILLING_TYPES:
            self.cb_billing.addItem(energy.BILLING_LABELS[bt], bt)
        self.cb_billing.setCurrentIndex(max(self.cb_billing.findData(cur), 0))
        lay.addWidget(self.cb_billing)

        # Тип 1 — Счётчик
        self._g_meter = QWidget()
        fm = QFormLayout(self._g_meter)
        fm.setContentsMargins(0, 4, 0, 0)
        self.de_meter_date = self._make_date_edit(rec.get("meter_commission_date"))
        self.inp_meter_act = QLineEdit(str(rec.get("meter_act_number", "")))
        self.inp_meter_loc = QLineEdit(str(rec.get("meter_location", "")))
        fm.addRow("Дата ввода в эксплуатацию:", self.de_meter_date)
        fm.addRow("Номер акта:", self.inp_meter_act)
        fm.addRow("Место установки:", self.inp_meter_loc)
        lay.addWidget(self._g_meter)

        # Тип 2 — Расчётный метод
        self._g_calc = QWidget()
        fc = QFormLayout(self._g_calc)
        fc.setContentsMargins(0, 4, 0, 0)
        norm_raw = rec.get("norm_kw")
        self.inp_norm = QLineEdit("" if norm_raw in (None, "") else f"{float(norm_raw):g}")
        self.inp_norm.setPlaceholderText("обязательно, например: 1.5")
        self.de_norm_start = self._make_date_edit(
            rec.get("norm_start_date") or _next_period_start().isoformat()
        )
        fc.addRow("Норматив мощности, кВт:", self.inp_norm)
        fc.addRow("Дата начала применения:", self.de_norm_start)
        note = QLabel("Начисление: норматив × 24 ч × дни в периоде × тариф.\n"
                      "Показания прибора учёта игнорируются.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#9CA3AF;font-size:11px;")
        fc.addRow(note)
        lay.addWidget(self._g_calc)

        # Тип 3 — Прямой договор
        self._g_direct = QWidget()
        fd = QFormLayout(self._g_direct)
        fd.setContentsMargins(0, 4, 0, 0)
        self.de_direct_date = self._make_date_edit(rec.get("direct_contract_date"))
        self.inp_direct_num = QLineEdit(str(rec.get("direct_contract_number", "")))
        fd.addRow("Дата заключения договора:", self.de_direct_date)
        fd.addRow("Номер договора с энергосбытом:", self.inp_direct_num)
        note_d = QLabel("Начисление через СНТ не производится; участок исключён "
                        "из суммарного баланса СНТ.")
        note_d.setWordWrap(True)
        note_d.setStyleSheet("color:#9CA3AF;font-size:11px;")
        fd.addRow(note_d)
        lay.addWidget(self._g_direct)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Сохранить")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        self.cb_billing.currentIndexChanged.connect(self._on_billing_changed)
        self._on_billing_changed()

    def _on_billing_changed(self):
        bt = self.cb_billing.currentData()
        self._g_meter.setVisible(bt == energy.BILLING_METER)
        self._g_calc.setVisible(bt == energy.BILLING_CALCULATED)
        self._g_direct.setVisible(bt == energy.BILLING_DIRECT)
        self.adjustSize()

    def _on_accept(self):
        rec = self._rec
        bt = self.cb_billing.currentData()

        # Сбор полей и валидация
        if bt == energy.BILLING_METER:
            rec["meter_commission_date"] = self.de_meter_date.date().toString("yyyy-MM-dd")
            rec["meter_act_number"] = self.inp_meter_act.text().strip()
            rec["meter_location"] = self.inp_meter_loc.text().strip()
        elif bt == energy.BILLING_CALCULATED:
            raw = self.inp_norm.text().strip().replace(",", ".")
            try:
                nv = float(raw)
            except ValueError:
                nv = 0.0
            if nv <= 0:
                QMessageBox.warning(self, "Расчётный метод",
                                    "Для расчётного метода обязательно укажите "
                                    "норматив мощности (положительное число, кВт).")
                return
            rec["norm_kw"] = nv
            rec["norm_start_date"] = self.de_norm_start.date().toString("yyyy-MM-dd")
        else:  # direct
            rec["direct_contract_date"] = self.de_direct_date.date().toString("yyyy-MM-dd")
            rec["direct_contract_number"] = self.inp_direct_num.text().strip()

        # Смена типа — подтверждение + запись в историю
        if bt != self._orig:
            old_lbl = energy.BILLING_LABELS.get(self._orig, self._orig)
            new_lbl = energy.BILLING_LABELS.get(bt, bt)
            eff = _next_period_start()
            msg = (f"Сменить тип расчёта «{old_lbl}» → «{new_lbl}»?\n\n"
                   f"Изменение применяется со следующего расчётного периода "
                   f"({eff.strftime('%d.%m.%Y')}). Текущий период закрывается "
                   f"по действующему методу.")
            if bt == energy.BILLING_DIRECT:
                msg += ("\n\nВнимание: имеющаяся задолженность по СНТ остаётся "
                        "за участком и закрывается вручную.")
            reply = QMessageBox.question(
                self, "Смена типа расчёта", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                user = getpass.getuser()
            except Exception:
                user = "admin"
            history = list(rec.get("billing_history", []) or [])
            history.append({
                "date": eff.isoformat(), "from": self._orig, "to": bt,
                "user": user, "ts": date.today().isoformat(), "reason": "",
            })
            rec["billing_history"] = history

        rec["billing_type"] = bt
        if self._created:
            self._plots.append(rec)
        energy.save_plots(self._plots)
        self._saved = True
        self.accept()

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog { background: #FFFFFF; color: #374151; }
            QLabel  { background: transparent; color: #374151; font-size: 13px; }
            QLineEdit, QDateEdit {
                background: #F8F9FA; border: 1px solid #D1D5DB;
                border-radius: 5px; color: #374151; padding: 6px 8px; font-size: 13px;
            }
            QLineEdit:focus, QDateEdit:focus { border: 1px solid #6366F1; }
            QComboBox {
                background: #F8F9FA; border: 1px solid #D1D5DB; border-radius: 5px;
                padding: 7px 10px; font-size: 13px; color: #374151;
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView {
                background: #FFFFFF; border: 1px solid #D1D5DB;
                color: #374151; selection-background-color: #EEF2FF;
            }
            QDialogButtonBox QPushButton {
                background: #4F46E5; color: white; border: none;
                border-radius: 6px; padding: 7px 18px; font-size: 13px; font-weight: 600;
            }
            QDialogButtonBox QPushButton:hover { background: #6366F1; }
            QDialogButtonBox QPushButton[text='Отмена'] { background: #E5E7EB; color: #6B7280; }
        """)


class PlotCardDialog(QDialog):
    """Карточка участка: сводка показаний/начислений/платежей + ввод и правка показаний."""

    MONTH_NAMES = ["янв", "фев", "мар", "апр", "май", "июн",
                   "июл", "авг", "сен", "окт", "ноя", "дек"]

    def __init__(self, plot: str, df, parent=None, as_of: date | None = None):
        super().__init__(parent)
        self._plot = str(plot)
        self._df = df
        self._as_of = as_of or date.today()
        self._value_edits: dict[tuple[int, int], QLineEdit] = {}
        self.setWindowTitle(f"Участок {plot} — карточка")
        self.setMinimumSize(960, 620)
        self.setModal(False)

        self._setup_ui()
        self._rebuild()

    # ── UI ───────────────────────────────────────────────────────────────
    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 16)
        lay.setSpacing(10)

        owners = energy.owners_map().get(self._plot, [])
        owners_text = ", ".join(owners) if owners else "владельцы не указаны"
        head = QLabel(f"<b>Участок {self._plot}</b>  ·  {owners_text}")
        head.setStyleSheet("font-size:14px;color:#111827;background:transparent;")
        lay.addWidget(head)

        self.summary_lbl = QLabel("")
        self.summary_lbl.setStyleSheet("color:#9CA3AF;background:transparent;font-size:12px;")
        lay.addWidget(self.summary_lbl)

        # Плашка типа расчёта (расчётный метод / прямой договор / ожидание показаний)
        self.banner_lbl = QLabel("")
        self.banner_lbl.setWordWrap(True)
        self.banner_lbl.setVisible(False)
        lay.addWidget(self.banner_lbl)

        # ── Панель ввода нового показания ──────────────────────────
        self._entry = entry = QFrame(objectName="entryBox")
        eh = QHBoxLayout(entry)
        eh.setContentsMargins(12, 8, 12, 8)
        eh.setSpacing(8)

        lbl = QLabel("Передать показание:")
        lbl.setStyleSheet("color:#6B7280;background:transparent;font-size:12px;")
        eh.addWidget(lbl)

        self.cb_month = QComboBox()
        self.cb_month.setObjectName("filterCombo")
        for i, m in enumerate(self.MONTH_NAMES, start=1):
            self.cb_month.addItem(m, i)
        today = date.today()
        self.cb_month.setCurrentIndex(today.month - 1)
        self.cb_month.setFixedWidth(80)
        eh.addWidget(self.cb_month)

        self.cb_year = QComboBox()
        self.cb_year.setObjectName("filterCombo")
        for y in range(today.year - 5, today.year + 2):
            self.cb_year.addItem(str(y), y)
        self.cb_year.setCurrentText(str(today.year))
        self.cb_year.setFixedWidth(90)
        eh.addWidget(self.cb_year)

        self.le_value = QLineEdit()
        self.le_value.setObjectName("searchInput")
        self.le_value.setPlaceholderText("значение, кВт·ч")
        self.le_value.setFixedWidth(160)
        self.le_value.returnPressed.connect(self._on_add_reading)
        eh.addWidget(self.le_value)

        btn_add = QPushButton("Сохранить", objectName="btnPrimary")
        btn_add.clicked.connect(self._on_add_reading)
        eh.addWidget(btn_add)
        eh.addStretch()
        lay.addWidget(entry)

        # ── Таблица ────────────────────────────────────────────────
        self.table = QTableWidget(objectName="summaryTable")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "Дата / Месяц", "Показание", "Расход (кВт·ч)",
            "Тариф", "Начислено", "Оплачено",
            "Изм. баланса", "Баланс нараст.", "",
        ])
        hdr = self.table.horizontalHeader()
        for c, w in enumerate([90, 150, 110, 75, 110, 110, 110, 130, 40]):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(c, w)
        hdr.setStretchLastSection(False)
        lay.addWidget(self.table, 1)

        # ── Разбивка по группам/собственникам (видна при истории) ──
        self.owners_lbl = QLabel("По группам / собственникам")
        self.owners_lbl.setStyleSheet(
            "color:#6366F1;background:transparent;font-size:12px;margin-top:8px;")
        lay.addWidget(self.owners_lbl)
        self.owners_table = QTableWidget(objectName="summaryTable")
        self.owners_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.owners_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.owners_table.verticalHeader().setVisible(False)
        self.owners_table.setColumnCount(5)
        self.owners_table.setHorizontalHeaderLabels([
            "Собственник", "Период владения", "Начислено", "Оплачено", "Долг",
        ])
        ohdr = self.owners_table.horizontalHeader()
        for c, w in enumerate([280, 190, 120, 120, 120]):
            ohdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            self.owners_table.setColumnWidth(c, w)
        ohdr.setStretchLastSection(True)
        self.owners_table.setMaximumHeight(160)
        lay.addWidget(self.owners_table)

        # ── Подсказка по аномалиям ─────────────────────────────────
        hint = QHBoxLayout()
        hint.setSpacing(16)
        for color, text in [
            ("#DC2626", "■  показание < предыдущего"),
            ("#ffd54f", "■  аномально большой расход / замена счётчика"),
        ]:
            lb = QLabel(text)
            lb.setStyleSheet(f"color:{color};background:transparent;font-size:11px;")
            hint.addWidget(lb)
        hint.addStretch()
        lay.addLayout(hint)

        # ── Кнопки внизу ───────────────────────────────────────────
        bottom = QHBoxLayout()
        self.btn_billing = QPushButton("⚙  Тип расчёта")
        self.btn_billing.setObjectName("btnSecondary")
        self.btn_billing.clicked.connect(self._on_billing_type)
        bottom.addWidget(self.btn_billing)

        self.btn_replace = QPushButton("Зарегистрировать замену счётчика")
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
            QDialog { background: #FFFFFF; color: #374151; }
            QLabel  { background: transparent; }
            QFrame#entryBox {
                background: #F8F9FA; border: 1px solid #E5E7EB; border-radius: 8px;
            }
            QPushButton#btnPrimary {
                background: #4F46E5; color: white; border: none; border-radius: 6px;
                padding: 8px 18px; font-size: 13px; font-weight: 600;
            }
            QPushButton#btnPrimary:hover  { background: #6366F1; }
            QPushButton#btnSecondary {
                background: #E5E7EB; color: #6B7280; border: 1px solid #D1D5DB;
                border-radius: 6px; padding: 7px 14px; font-size: 13px;
            }
            QPushButton#btnSecondary:hover { background: #E5E7EB; color: #374151; }
            QLineEdit#searchInput, QComboBox#filterCombo {
                background: #F8F9FA; border: 1px solid #D1D5DB; border-radius: 6px;
                color: #374151; padding: 6px 10px; font-size: 13px;
            }
            QLineEdit#searchInput:focus { border: 1px solid #6366F1; }
            QComboBox#filterCombo::drop-down { border: none; width: 18px; }
            QComboBox QAbstractItemView {
                background: #F8F9FA; border: 1px solid #D1D5DB;
                color: #374151; selection-background-color: #EEF2FF;
            }
            QTableWidget#summaryTable {
                background: #F8F9FA; border: 1px solid #E5E7EB; border-radius: 8px;
                gridline-color: #F3F4F6; color: #374151; font-size: 12px;
                selection-background-color: #EEF2FF; selection-color: #111827;
            }
            QTableWidget#summaryTable QHeaderView::section {
                background: #F9FAFB; color: #6366F1; border: none;
                border-right: 1px solid #E5E7EB; border-bottom: 2px solid #6366F1;
                padding: 6px 8px; font-size: 12px; font-weight: 600;
            }
        """)

    # ── Перестройка таблицы ──────────────────────────────────────────────
    def _rebuild(self):
        self._value_edits.clear()

        meters = energy.load_meters()
        rates = energy.load_rates()
        repls = energy.load_replacements()
        baseline = energy.load_baseline()
        if self._df is not None and not self._df.empty:
            baseline["start_date"] = self._df["Дата"].min().date().isoformat()

        base = energy._to_float(baseline.get("balances", {}).get(self._plot)) or 0.0
        base_start = energy._parse_iso(baseline.get("start_date", ""))

        # Тип расчёта определяет логику начисления и доступность ввода показаний
        bt = energy.billing_type_of(self._plot)
        self._entry.setVisible(bt == energy.BILLING_METER)
        self._configure_banner(bt, meters)

        # ── Хронология: каждое снятие показания + каждый платёж — отдельная строка
        charges = energy.charges_for_plot(self._plot, meters, rates, repls, up_to=self._as_of)
        payments = [
            p for p in energy.payments_breakdown(self._plot, self._df)
            if p["date"] is not None
            and (base_start is None or p["date"] >= base_start)
            and p["date"] <= self._as_of
        ]
        events: list[tuple[str, date, dict]] = []
        for c in charges:
            events.append(("charge", energy.reading_date(c["year"], c["month"]), c))
        for p in payments:
            events.append(("payment", p["date"], p))
        for repl in repls.get(self._plot, []):
            d = energy._parse_iso(repl.get("date", ""))
            if d and d <= self._as_of:
                events.append(("replacement", d, repl))
        # charge < replacement < payment при совпадении даты
        _kind_order = {"charge": 0, "replacement": 1, "payment": 2}
        events.sort(key=lambda e: (e[1], _kind_order.get(e[0], 9)))

        anomaly_map: dict[tuple[int, int], str] = {}
        for a in energy.anomalies(self._plot, meters, repls):
            if a.type in ("drop", "spike"):
                anomaly_map[(a.year, a.month)] = a.type

        self.table.setRowCount(len(events) + (1 if base != 0 else 0))
        r0 = 0
        cum = base
        if base != 0:
            label = "Начальное сальдо"
            if base_start:
                label += f" ({base_start.isoformat()})"
            self._set_row(0, [
                (label, None), ("—", None), ("—", None), ("—", None),
                ("—", None), ("—", None),
                (fmt_money(base), None),
                (fmt_money(base), self._debt_color(base)),
                ("", None),
            ], bold=True)
            r0 = 1

        for i, (kind, evdate, payload) in enumerate(events):
            r = r0 + i
            if kind == "charge":
                c = payload
                y, m = c["year"], c["month"]
                label = f"{m:02d}.{y}"
                kwh_text = f"{c['kwh']:.0f}" if c["kwh"] is not None else "—"
                rate_text = f"{c['rate']:.2f}" if c["rate"] is not None else "—"
                amount = c["amount"] or 0.0
                charged_text = fmt_money(c["amount"]) if c["amount"] is not None else "—"
                cum += amount
                mbal_text = fmt_money(amount) if amount else "0 ₽"
                is_calc = bool(c.get("calculated"))
                # Тип 2: показание не вводится — вместо редактора пометка «расчётный метод»
                value_cell = (f"расч. · {c.get('days', 0)} дн", "#6366F1") if is_calc else None
                self._set_row(r, [
                    (label, None),
                    value_cell,                        # «Показание» — редактируемое поле (тип 1)
                    (kwh_text, None),
                    (rate_text, None),
                    (charged_text, "#f9a825" if c["amount"] else None),
                    ("—", None),
                    (mbal_text, None),
                    (fmt_money(cum), self._debt_color(cum)),
                    ("", None) if is_calc else None,   # кнопка удаления (тип 1)
                ])
                if not is_calc:
                    self._install_value_editor(r, y, m, c["value"], anomaly_map.get((y, m)))
                    self._install_delete_button(r, y, m)
            elif kind == "payment":
                p = payload
                paid = p["amount"]
                cum -= paid
                label = f"💳  {p['date'].strftime('%d.%m.%Y')}"
                if p.get("mixed"):
                    label += " ⅟₂"   # подсказка что платёж пополам с членскими
                self._set_row(r, [
                    (label, "#059669"),
                    ("—", None),
                    ("—", None),
                    ("—", None),
                    ("—", None),
                    (fmt_money(paid), "#059669"),
                    (fmt_money(-paid), None),
                    (fmt_money(cum), self._debt_color(cum)),
                    ("", None),
                ])
                # tooltip с назначением платежа на всю строку
                if p.get("purpose"):
                    for col in range(self.table.columnCount()):
                        it = self.table.item(r, col)
                        if it is not None:
                            it.setToolTip(p["purpose"])
            else:  # replacement
                repl = payload
                old_f = energy._to_float(repl.get("old_final"))
                new_i = energy._to_float(repl.get("new_initial"))
                reading_text = (
                    f"{old_f:g} → {new_i:g}"
                    if old_f is not None and new_i is not None else "—"
                )
                self._set_row(r, [
                    (f"[замена] {evdate.strftime('%d.%m.%Y')}", "#ffd54f"),
                    (reading_text, "#ffd54f"),
                    ("—", None), ("—", None), ("—", None), ("—", None), ("—", None),
                    (fmt_money(cum), self._debt_color(cum)),
                    None,
                ])
                note = repl.get("note", "").strip()
                tip = f"Замена счётчика: конечное {old_f:g}, начальное {new_i:g}"
                if note:
                    tip += f"\nПричина: {note}"
                for col in range(self.table.columnCount()):
                    it = self.table.item(r, col)
                    if it is not None:
                        it.setToolTip(tip)
                self._install_delete_replacement_button(r, repl.get("date", ""))

        # Итоги — через energy.balance(), чтобы совпадали с вкладкой «Долги»
        bal = energy.balance(self._plot, self._as_of, meters, rates, repls, baseline, self._df)
        if events or base != 0:
            self.summary_lbl.setText(
                f"Начислено всего: {fmt_money(bal.baseline + bal.charged)}  ·  "
                f"оплачено всего: {fmt_money(bal.paid)}  ·  "
                f"итоговый баланс: {fmt_money(bal.debt)}  ·  "
                f"на {self._as_of.strftime('%d.%m.%Y')}"
            )
        else:
            self.summary_lbl.setText(
                "Показаний и платежей по этому участку пока нет — внесите первое выше."
            )

        # Разбивка по собственникам
        self._rebuild_owners(meters, rates, repls, baseline)

    def _rebuild_owners(self, meters, rates, repls, baseline):
        from core import ownership as own
        rec = energy.plot_record(self._plot)

        has_groups_model = "groups" in rec
        if has_groups_model:
            archived = own.archived_groups(rec)
            active = own.active_group(rec)
            show = bool(archived)
            self.owners_lbl.setVisible(show)
            self.owners_table.setVisible(show)
            if not show:
                return
            all_rows = []
            if active:
                since = own.group_since(active)
                try:
                    egb = energy.balance_for_active_group(
                        self._plot, self._as_of, meters, rates, repls,
                        baseline, self._df, since=since)
                    all_rows.append({
                        "name": own.group_label(active, empty="(нет лиц)"),
                        "period": self._fmt_own_period(since, None),
                        "charged": egb.baseline + egb.charged,
                        "paid": egb.paid,
                        "debt": egb.debt,
                        "is_current": True,
                    })
                except Exception:
                    pass
            for g in archived:
                debt_e = (g.get("debt_at_close") or {}).get("energy", 0.0) or 0.0
                all_rows.append({
                    "name": own.group_label(g, empty="(без ФИО)"),
                    "period": self._fmt_own_period(own.group_since(g), own.group_until(g)),
                    "charged": None,
                    "paid": None,
                    "debt": debt_e,
                    "is_current": False,
                })
        else:
            recs = rec.get("owners", []) or []
            owner_count = sum(1 for o in recs if own.is_owner(o))
            show = owner_count > 1 or own.has_history(recs)
            self.owners_lbl.setVisible(show)
            self.owners_table.setVisible(show)
            if not show:
                return
            ob_rows = energy.balances_by_owner(
                self._plot, self._as_of, meters, rates, repls, baseline, self._df,
                recs, ownership_form=rec.get("ownership_form"))
            all_rows = [
                {"name": f"{ob.name}  ({'текущий' if ob.is_current else 'прежний'})",
                 "period": self._fmt_own_period(ob.since, ob.until),
                 "charged": ob.baseline + ob.charged, "paid": ob.paid, "debt": ob.debt,
                 "is_current": ob.is_current}
                for ob in ob_rows
            ]

        self.owners_table.setRowCount(len(all_rows))
        for r, row in enumerate(all_rows):
            debt_color = ("#DC2626" if row["debt"] > 0.005
                          else ("#059669" if row["debt"] < -0.005 else "#6B7280"))
            cells = [
                (row["name"], "#374151" if row["is_current"] else "#9CA3AF"),
                (row["period"], "#6B7280"),
                (fmt_money(row["charged"]) if row["charged"] is not None else "—", "#374151"),
                (fmt_money(row["paid"]) if row["paid"] is not None else "—",
                 "#059669" if row.get("paid") else "#9CA3AF"),
                (fmt_money(row["debt"]), debt_color),
            ]
            for c, (text, color) in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setTextAlignment(
                    (Qt.AlignmentFlag.AlignLeft if c == 0 else Qt.AlignmentFlag.AlignCenter)
                    | Qt.AlignmentFlag.AlignVCenter)
                if color:
                    it.setForeground(QColor(color))
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.owners_table.setItem(r, c, it)
            self.owners_table.setRowHeight(r, 28)

    @staticmethod
    def _fmt_own_period(since, until) -> str:
        if since and until:
            return f"{since.strftime('%d.%m.%Y')}—{until.strftime('%d.%m.%Y')}"
        if since:
            return f"с {since.strftime('%d.%m.%Y')}"
        if until:
            return f"по {until.strftime('%d.%m.%Y')}"
        return "—"

    def _configure_banner(self, bt: str, meters: dict):
        """Плашка под сводкой: расчётный метод / прямой договор / ожидание показаний."""
        if bt == energy.BILLING_CALCULATED:
            self._set_banner(
                "Расчётный метод — прибор учёта не введён в эксплуатацию. "
                "Начисление по нормативу (норматив × 24 ч × дни × тариф).",
                "#92400E", "#FEF3C7", "#FCD34D",
            )
        elif bt == energy.BILLING_DIRECT:
            self._set_banner(
                "Расчёты ведутся напрямую с Пермэнергосбытом. "
                "Начисление через СНТ не производится, участок исключён из баланса СНТ.",
                "#3730A3", "#EEF2FF", "#C7D2FE",
            )
        elif energy.waiting_for_readings(self._plot, meters):
            self._set_banner(
                "Ожидание показаний — показания прибора учёта ещё не переданы, "
                "начисление не производится.",
                "#9A3412", "#FFF7ED", "#FED7AA",
            )
        else:
            self.banner_lbl.setVisible(False)

    def _set_banner(self, text: str, fg: str, bg: str, border: str):
        self.banner_lbl.setText(text)
        self.banner_lbl.setStyleSheet(
            f"color:{fg};background:{bg};border:1px solid {border};"
            "border-radius:6px;padding:8px 12px;font-size:12px;"
        )
        self.banner_lbl.setVisible(True)

    # ── Помощники строк ──────────────────────────────────────────────────
    def _set_row(self, r: int, cells: list, bold: bool = False):
        for c, cell in enumerate(cells):
            if cell is None:
                # Placeholder под ячейку-виджет: пустой item, чтобы сохранить выделение строки
                ph = QTableWidgetItem("")
                ph.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(r, c, ph)
                continue
            text, color = cell
            it = QTableWidgetItem(text)
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            if color:
                it.setForeground(QColor(color))
            if bold:
                f = it.font(); f.setBold(True); it.setFont(f)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(r, c, it)
        self.table.setRowHeight(r, 30)

    def _install_value_editor(self, r: int, year: int, month: int,
                              value: float, anomaly: str | None):
        edit = QLineEdit(f"{value:g}")
        edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        edit.setStyleSheet(self._value_edit_style(anomaly))
        if anomaly == "drop":
            edit.setToolTip("Показание меньше предыдущего")
        elif anomaly == "spike":
            edit.setToolTip("Расход существенно больше обычного")
        edit.editingFinished.connect(
            lambda y=year, m=month, e=edit: self._on_value_committed(y, m, e)
        )
        self._value_edits[(year, month)] = edit
        self.table.setCellWidget(r, 1, edit)

    def _install_delete_button(self, r: int, year: int, month: int):
        btn = QPushButton("✕")
        btn.setFixedSize(26, 22)
        btn.setToolTip(f"Удалить показание за {month:02d}.{year}")
        btn.setStyleSheet(
            "QPushButton{background:#2a1318;color:#DC2626;border:1px solid #6e2a30;"
            "border-radius:4px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#4a1a22;color:#ffcccc;}"
        )
        btn.clicked.connect(lambda _, y=year, m=month: self._on_delete_reading(y, m))
        self.table.setCellWidget(r, 8, btn)

    def _install_delete_replacement_button(self, r: int, repl_date: str):
        btn = QPushButton("✕")
        btn.setFixedSize(26, 22)
        btn.setToolTip(f"Удалить запись о замене счётчика от {repl_date}")
        btn.setStyleSheet(
            "QPushButton{background:#2a2200;color:#ffd54f;border:1px solid #7a6000;"
            "border-radius:4px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#4a3c00;color:#ffe57f;}"
        )
        btn.clicked.connect(lambda _, d=repl_date: self._on_delete_replacement(d))
        self.table.setCellWidget(r, 8, btn)

    def _on_delete_replacement(self, repl_date: str):
        reply = QMessageBox.question(
            self, "Удалить замену счётчика",
            f"Удалить запись о замене счётчика от {repl_date} на уч. {self._plot}?\n"
            "Расчёт расхода электроэнергии будет пересчитан.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        repls = energy.load_replacements()
        plot_repls = repls.get(self._plot, [])
        repls[self._plot] = [r for r in plot_repls if r.get("date") != repl_date]
        if not repls[self._plot]:
            del repls[self._plot]
        energy.save_replacements(repls)
        self._rebuild()

    @staticmethod
    def _value_edit_style(anomaly: str | None) -> str:
        if anomaly == "drop":
            return ("background:#2a0d0d;border:1px solid #DC2626;border-radius:3px;"
                    "color:#DC2626;font-size:12px;font-weight:700;padding:3px 6px;")
        if anomaly == "spike":
            return ("background:#2a1f0d;border:1px solid #f9a825;border-radius:3px;"
                    "color:#ffd54f;font-size:12px;padding:3px 6px;")
        return ("background:#F0F2F5;border:1px solid #D1D5DB;border-radius:3px;"
                "color:#374151;font-size:12px;padding:3px 6px;")

    # ── Сохранение ───────────────────────────────────────────────────────
    def _on_add_reading(self):
        month = self.cb_month.currentData()
        year = self.cb_year.currentData()
        val = self.le_value.text().strip().replace(",", ".")
        if not val:
            return
        try:
            num = float(val)
        except ValueError:
            QMessageBox.warning(self, "Показание", "Значение должно быть числом.")
            return
        if num < 0:
            QMessageBox.warning(self, "Показание", "Значение не может быть отрицательным.")
            return
        self._store_reading(year, month, num)
        self.le_value.clear()
        self.le_value.setFocus()

    def _on_value_committed(self, year: int, month: int, edit: QLineEdit):
        text = edit.text().strip().replace(",", ".")
        meters = energy.load_meters()
        key = f"{self._plot}:{year}:{month}"
        old_raw = meters.get(key, "")
        if text == "" and old_raw == "":
            return
        if text == old_raw.strip().replace(",", "."):
            return
        if text == "":
            # очистка ячейки = удаление показания
            if self._confirm_delete(year, month):
                meters.pop(key, None)
                energy.save_meters(meters)
                self._rebuild()
            else:
                self._rebuild()  # восстановить старое значение в виджете
            return
        try:
            num = float(text)
        except ValueError:
            QMessageBox.warning(self, "Показание", "Значение должно быть числом.")
            self._rebuild()
            return
        if num < 0:
            QMessageBox.warning(self, "Показание", "Значение не может быть отрицательным.")
            self._rebuild()
            return
        self._store_reading(year, month, num)

    def _store_reading(self, year: int, month: int, value: float):
        meters = energy.load_meters()
        key = f"{self._plot}:{year}:{month}"
        # сохраняем как целое если без дробной части
        meters[key] = str(int(value)) if float(value).is_integer() else f"{value:g}"
        energy.save_meters(meters)
        self._rebuild()

    def _on_delete_reading(self, year: int, month: int):
        if not self._confirm_delete(year, month):
            return
        meters = energy.load_meters()
        meters.pop(f"{self._plot}:{year}:{month}", None)
        energy.save_meters(meters)
        self._rebuild()

    def _confirm_delete(self, year: int, month: int) -> bool:
        reply = QMessageBox.question(
            self, "Удалить показание",
            f"Удалить показание за {month:02d}.{year} на уч. {self._plot}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    # ── Прочее ───────────────────────────────────────────────────────────
    @staticmethod
    def _debt_color(v: float) -> str | None:
        if v > 0:
            return "#DC2626"
        if v < 0:
            return "#059669"
        return None

    def _on_billing_type(self):
        dlg = BillingTypeDialog(self._plot, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and getattr(dlg, "_saved", False):
            self._rebuild()
            QMessageBox.information(self, "Тип расчёта",
                                    "Тип расчёта сохранён. Расчёт пересчитан.")

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
        repls.setdefault(self._plot, []).append(result)
        repls[self._plot].sort(key=lambda r: r.get("date", ""))
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

        dlg = _PdfPeriodDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        since = dlg.since_date()
        as_of = dlg.as_of_date()
        zero_opening = dlg.zero_opening

        if since and since > as_of:
            QMessageBox.warning(self, "Период", "Начало периода позже конца.")
            return

        default_name = (
            f"Уч_{self._plot}_{since.strftime('%Y%m')}-{as_of.strftime('%Y%m')}.pdf"
            if since else f"Уч_{self._plot}_{as_of.strftime('%Y%m')}.pdf"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить квитанцию", default_name, "PDF (*.pdf)"
        )
        if not path:
            return
        try:
            receipt.save_plot_receipt_pdf(self._plot, self._df, path,
                                          as_of=as_of, since=since,
                                          zero_opening=zero_opening)
            QMessageBox.information(self, "Квитанция", f"Сохранено:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")
