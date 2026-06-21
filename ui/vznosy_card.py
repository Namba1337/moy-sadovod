"""Карточки и диалоги для вкладки «Долги по членским взносам»."""
from __future__ import annotations

from datetime import date

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTabWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from core import energy
from core.utils import fmt_money


class VznosyAdjustmentDialog(QDialog):
    """Диалог добавления ручного платежа или переопределения начисления ЧВ."""

    KIND_LABELS = {
        "charge_override": "Переопределение начисления за период",
        "exempt_period": "Освобождение от взноса за период",
    }

    def __init__(self, plot: str, parent=None,
                 default_kind: str = "charge_override"):
        super().__init__(parent)
        self._plot = plot
        self._result: dict | None = None
        self.setWindowTitle(f"Корректировка ЧВ — уч. {plot}")
        self.setMinimumWidth(480)
        self.setModal(True)

        from core import vznosy as _vznosy
        self._periods = _vznosy.build_periods(_vznosy.load_rates())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 18)
        lay.setSpacing(12)

        title = QLabel(f"Корректировка ЧВ на участке {plot}")
        title.setStyleSheet("font-size:14px;font-weight:700;color:#111827;")
        lay.addWidget(title)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.cb_kind = QComboBox()
        for key, label in self.KIND_LABELS.items():
            self.cb_kind.addItem(label, key)
        idx = self.cb_kind.findData(default_kind)
        if idx >= 0:
            self.cb_kind.setCurrentIndex(idx)
        self.cb_kind.currentIndexChanged.connect(self._on_kind_changed)
        form.addRow("Тип:", self.cb_kind)

        self.inp_date = QDateEdit(calendarPopup=True)
        self.inp_date.setDisplayFormat("dd.MM.yyyy")
        self.inp_date.setDate(QDate.currentDate())
        form.addRow("Дата:", self.inp_date)

        self.cb_period = QComboBox()
        self._fill_period_combo()
        form.addRow("Период:", self.cb_period)

        self.inp_amount = QLineEdit()
        self.inp_amount.setPlaceholderText("например: 5000")
        form.addRow("Сумма, ₽:", self.inp_amount)

        self.inp_note = QLineEdit()
        self.inp_note.setPlaceholderText("необязательно")
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
            QLineEdit, QDateEdit, QComboBox, QSpinBox {
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

        self._on_kind_changed()

    def _fill_period_combo(self):
        self.cb_period.clear()
        for r in reversed(self._periods):   # новые сверху
            pf = r.get("date_from", "")
            pt = r.get("date_to", "")
            try:
                from datetime import datetime as _dt
                pf_label = _dt.strptime(pf, "%Y-%m-%d").strftime("%d.%m.%Y")
            except Exception:
                pf_label = pf
            try:
                from datetime import datetime as _dt
                pt_label = _dt.strptime(pt, "%Y-%m-%d").strftime("%d.%m.%Y")
            except Exception:
                pt_label = pt or "открытый"
            self.cb_period.addItem(f"{pf_label} — {pt_label}", pf)
        if not self._periods:
            self.cb_period.addItem("Нет периодов", "")

    def _on_kind_changed(self):
        kind = self.cb_kind.currentData()
        if kind == "charge_override":
            self.inp_amount.setEnabled(True)
            self.inp_amount.setPlaceholderText("новая сумма за период, ₽")
        else:  # exempt_period
            self.inp_amount.setEnabled(False)
            self.inp_amount.setText("0")

    def _on_accept(self):
        kind = self.cb_kind.currentData()
        result = {
            "date": self.inp_date.date().toString("yyyy-MM-dd"),
            "kind": kind,
            "note": self.inp_note.text().strip(),
        }
        if kind in ("charge_override", "exempt_period"):
            result["period_from"] = self.cb_period.currentData() or ""

        if kind == "exempt_period":
            result["amount"] = "0"
        else:
            raw = self.inp_amount.text().strip().replace(",", ".")
            try:
                v = float(raw)
                if v < 0:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(self, "Ошибка",
                                    "Сумма должна быть неотрицательным числом")
                return
            result["amount"] = f"{v:g}"

        self._result = result
        self.accept()

    def get_result(self) -> dict | None:
        return self._result


class VznosyCardDialog(QDialog):
    """Карточка участка по членским взносам: годы, начисления, платежи, корректировки."""

    def __init__(self, plot: str, df, parent=None, as_of: date | None = None):
        super().__init__(parent)
        self._plot = str(plot)
        self._df = df
        self._as_of = as_of or date.today()
        self.setWindowTitle(f"Участок {plot} — карточка ЧВ")
        self.setMinimumSize(1010, 640)
        self.setModal(False)

        self._setup_ui()
        self._rebuild()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 16)
        lay.setSpacing(10)

        from core import vznosy
        owners = energy.owners_map().get(self._plot, [])
        owners_text = ", ".join(owners) if owners else "владельцы не указаны"
        area = vznosy.plot_area_map().get(self._plot)
        area_text = f"{area:g} м²" if area is not None else "площадь не указана"

        self.head = QLabel(
            f"<b>Участок {self._plot}</b>  ·  {owners_text}  ·  {area_text}"
        )
        self.head.setStyleSheet("font-size:14px;color:#111827;background:transparent;")
        lay.addWidget(self.head)

        self.summary_lbl = QLabel("")
        self.summary_lbl.setStyleSheet("color:#9CA3AF;background:transparent;font-size:12px;")
        lay.addWidget(self.summary_lbl)

        self.warning_lbl = QLabel("")
        self.warning_lbl.setStyleSheet(
            "color:#DC2626;background:#2a1318;border:1px solid #6e2a30;"
            "border-radius:6px;padding:8px 12px;font-size:12px;"
        )
        self.warning_lbl.setVisible(False)
        lay.addWidget(self.warning_lbl)

        # ── Вкладки ──────────────────────────────────────────────
        self.tabs = QTabWidget(objectName="vznosyTabs")

        # Вкладка 1: разбивка по периодам
        tab1 = QWidget()
        t1_lay = QVBoxLayout(tab1)
        t1_lay.setContentsMargins(0, 4, 0, 0)

        self.table = QTableWidget(objectName="summaryTable")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Период", "Тариф", "Начислено", "Оплачено за период",
            "Корректировка", "Баланс нараст.",
        ])
        hdr = self.table.horizontalHeader()
        for c, w in enumerate([200, 240, 120, 140, 190, 130]):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(c, w)
        hdr.setStretchLastSection(False)
        t1_lay.addWidget(self.table, 1)
        self.tabs.addTab(tab1, "Разбивка по периодам")

        # Вкладка 2: разбивка по собственникам
        tab2 = QWidget()
        t2_lay = QVBoxLayout(tab2)
        t2_lay.setContentsMargins(0, 4, 0, 0)

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
        t2_lay.addWidget(self.owners_table, 1)
        self._owners_tab_idx = self.tabs.addTab(tab2, "По группам / собственникам")

        # Вкладка 3: ручные операции и корректировки
        tab3 = QWidget()
        t3_lay = QVBoxLayout(tab3)
        t3_lay.setContentsMargins(0, 4, 0, 0)

        self.adj_table = QTableWidget(objectName="summaryTable")
        self.adj_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.adj_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.adj_table.verticalHeader().setVisible(False)
        self.adj_table.setColumnCount(6)
        self.adj_table.setHorizontalHeaderLabels([
            "Дата", "Тип", "Период", "Сумма", "Примечание", "",
        ])
        ahdr = self.adj_table.horizontalHeader()
        for c, w in enumerate([110, 200, 80, 120, 380, 40]):
            ahdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            self.adj_table.setColumnWidth(c, w)
        ahdr.setStretchLastSection(False)
        t3_lay.addWidget(self.adj_table, 1)
        self.tabs.addTab(tab3, "Ручные операции и корректировки")

        lay.addWidget(self.tabs, 1)

        # ── Кнопки внизу ──────────────────────────────────────────
        bottom = QHBoxLayout()
        btn_adj = QPushButton("Корректировка начислений", objectName="btnSecondary")
        btn_adj.clicked.connect(lambda: self._add_adjustment("charge_override"))
        bottom.addWidget(btn_adj)

        btn_pdf = QPushButton("📄  Сохранить PDF-квитанцию", objectName="btnSecondary")
        btn_pdf.clicked.connect(self._on_pdf)
        bottom.addWidget(btn_pdf)

        bottom.addStretch()
        btn_close = QPushButton("Закрыть", objectName="btnPrimary")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        lay.addLayout(bottom)

        self.setStyleSheet("""
            QDialog { background: #FFFFFF; color: #374151; }
            QLabel  { background: transparent; }
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
            QTabWidget#vznosyTabs::pane {
                border: 1px solid #E5E7EB; border-radius: 6px; background: #FFFFFF;
            }
            QTabBar::tab {
                background: #F3F4F6; color: #6B7280; border: 1px solid #E5E7EB;
                border-bottom: none; border-top-left-radius: 6px;
                border-top-right-radius: 6px; padding: 8px 16px;
                font-size: 12px; font-weight: 500; margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #FFFFFF; color: #4F46E5; font-weight: 700;
                border-bottom: 2px solid #4F46E5;
            }
            QTabBar::tab:hover:!selected { background: #E5E7EB; color: #374151; }
        """)

    def _rebuild(self):
        from core import vznosy
        rates = vznosy.load_rates()
        adj = vznosy.load_adjustments()
        area = vznosy.plot_area_map().get(self._plot)
        periods = vznosy.build_periods(rates)
        bal = vznosy.balance_for_plot(self._plot, area, self._as_of,
                                       rates, adj, self._df)
        py = vznosy.paid_by_period(self._plot, self._df, self._as_of, periods, adj)

        # Обновить заголовок (площадь могла измениться)
        owners = energy.owners_map().get(self._plot, [])
        owners_text = ", ".join(owners) if owners else "владельцы не указаны"
        area_text = f"{area:g} м²" if area is not None else "площадь не указана"
        self.head.setText(
            f"<b>Участок {self._plot}</b>  ·  {owners_text}  ·  {area_text}"
        )

        # Баннер о площади
        if bal.area_missing_warning:
            self.warning_lbl.setText(
                "⚠ Для этого участка не указана площадь — тариф «за м²» не применён. "
                "Откройте вкладку «Участки» и укажите площадь."
            )
            self.warning_lbl.setVisible(True)
        else:
            self.warning_lbl.setVisible(False)

        # Главная таблица периодов
        self.table.setRowCount(len(bal.breakdown))
        cum = 0.0
        for r, y in enumerate(bal.breakdown):
                # Метка периода
                if y.period_to:
                    period_label = (f"{y.period_from.strftime('%d.%m.%Y')}"
                                    f"—{y.period_to.strftime('%d.%m.%Y')}")
                else:
                    period_label = f"{y.period_from.strftime('%d.%m.%Y')}—..."

                # Тариф
                if y.tariff is None:
                    tariff_text = "—"
                    tariff_color = "#9CA3AF"
                elif y.tariff.get("per_sqm"):
                    rate = y.tariff.get("rate_sqm", "?")
                    if y.area_missing:
                        tariff_text = f"{rate} ₽/м²  (площадь не указана)"
                        tariff_color = "#DC2626"
                    else:
                        tariff_text = f"{rate} ₽/м² · {area:g} м² → {fmt_money(y.amount)}"
                        tariff_color = "#374151"
                else:
                    tariff_text = f"{y.tariff.get('amount', '?')} ₽"
                    tariff_color = "#374151"

                # Начислено
                if y.ignored:
                    amount_text = "—"
                    amount_color = "#9CA3AF"
                elif y.amount is None:
                    amount_text = "—"
                    amount_color = "#DC2626" if y.area_missing else "#9CA3AF"
                else:
                    amount_text = fmt_money(y.amount)
                    amount_color = "#f9a825" if y.amount > 0 else "#9CA3AF"

                # Оплачено за период
                period_key = y.period_from.isoformat()
                paid_period = py.get(period_key, 0.0)
                paid_text = fmt_money(paid_period) if paid_period else "—"
                paid_color = "#059669" if paid_period else "#9CA3AF"

                # Корректировка
                note_text = "—"
                note_color = "#9CA3AF"
                if y.ignored:
                    note_text = "не учитывается"
                    note_color = "#546e7a"
                elif y.overridden:
                    ov = vznosy._period_override(self._plot, period_key,
                                                 y.period_from.year, adj)
                    kind = ov.get("kind", "") if ov else ""
                    kind_label = "освобождён" if kind in ("exempt_period", "exempt_year") else "переопределено"
                    if ov and ov.get("note"):
                        note_text = f"{kind_label}: {ov['note']}"
                    else:
                        note_text = kind_label
                    note_color = "#ffd54f"

                # Накопительный баланс
                if not y.ignored:
                    if y.amount is not None:
                        cum += y.amount
                    cum -= paid_period
                cum_text = fmt_money(cum) if not y.ignored else "—"
                cum_color = "#DC2626" if cum > 0 else ("#059669" if cum < 0 else None)

                self._set_year_row(r, [
                    (period_label, None),
                    (tariff_text, tariff_color),
                    (amount_text, amount_color),
                    (paid_text, paid_color),
                    (note_text, note_color),
                    (cum_text, cum_color),
                ], ignored=y.ignored)

        # Таблица корректировок
        plot_adjs = list(adj.get(self._plot, []) or [])
        plot_adjs_idx = list(enumerate(plot_adjs))
        plot_adjs_idx.sort(key=lambda t: t[1].get("date", ""))
        self.adj_table.setRowCount(len(plot_adjs_idx))
        for r, (orig_idx, a) in enumerate(plot_adjs_idx):
            kind = a.get("kind", "")
            kind_label = {
                "payment_manual": "Ручной платёж",
                "charge_override": "Переопр. начисления",
                "exempt_period": "Освобождение",
                "exempt_year": "Освобождение",
                "ignore_period": "Не учитывать",
                "ignore_year": "Не учитывать",
            }.get(kind, kind)
            date_text = a.get("date", "")
            try:
                from datetime import datetime as _dt
                date_text = _dt.strptime(date_text, "%Y-%m-%d").strftime("%d.%m.%Y")
            except Exception:
                pass
            # Период: period_from (новый формат) или year (старый)
            pf = a.get("period_from", "")
            if pf:
                try:
                    from datetime import datetime as _dt
                    period_text = _dt.strptime(pf, "%Y-%m-%d").strftime("%d.%m.%Y")
                except Exception:
                    period_text = pf
            elif a.get("year"):
                period_text = str(a["year"])
            else:
                period_text = "—"
            amount_v = energy._to_float(a.get("amount")) or 0.0
            amount_text = fmt_money(amount_v)
            note_text = a.get("note", "")

            for c, text in enumerate([date_text, kind_label, period_text,
                                       amount_text, note_text]):
                it = QTableWidgetItem(text)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if c in (0, 2, 3):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                self.adj_table.setItem(r, c, it)

            btn = QPushButton("✕")
            btn.setFixedSize(26, 22)
            btn.setToolTip("Удалить корректировку")
            btn.setStyleSheet(
                "QPushButton{background:#2a1318;color:#DC2626;border:1px solid #6e2a30;"
                "border-radius:4px;font-size:12px;font-weight:bold;}"
                "QPushButton:hover{background:#4a1a22;color:#ffcccc;}"
            )
            btn.clicked.connect(lambda _, i=orig_idx: self._delete_adjustment(i))
            self.adj_table.setCellWidget(r, 5, btn)
            self.adj_table.setRowHeight(r, 28)

        # Разбивка по собственникам
        self._rebuild_owners(rates, adj, area)

        # Сводка
        self.summary_lbl.setText(
            f"Начислено: {fmt_money(bal.charged)}  ·  "
            f"оплачено: {fmt_money(bal.paid)}  ·  "
            f"баланс: {fmt_money(bal.debt)}  ·  "
            f"на {self._as_of.strftime('%d.%m.%Y')}"
        )

    def _rebuild_owners(self, rates, adj, area):
        from core import vznosy
        from core import ownership as own
        rec = energy.plot_record(self._plot)

        # Новая модель: groups
        groups = own.plot_groups(rec)
        archived = own.archived_groups(rec)
        active = own.active_group(rec)
        has_groups_model = "groups" in rec

        if has_groups_model:
            # Показываем вкладку, если есть архивные группы
            show = bool(archived)
            self.tabs.setTabVisible(self._owners_tab_idx, show)
            if not show:
                return
            # Строки: сначала активная группа, затем архивные (свежие сверху)
            all_rows = []
            if active:
                since = own.group_since(active)
                try:
                    gb = vznosy.balance_for_active_group(
                        self._plot, area, self._as_of, rates, adj, self._df, since=since)
                    all_rows.append({
                        "name": own.group_label(active, empty="(нет лиц)"),
                        "period": self._fmt_ownership_period(since, None),
                        "charged": gb.charged,
                        "paid": gb.paid,
                        "debt": gb.debt,
                        "is_current": True,
                    })
                except Exception:
                    pass
            for g in archived:
                debt_v = (g.get("debt_at_close") or {}).get("vznosy", 0.0) or 0.0
                all_rows.append({
                    "name": own.group_label(g, empty="(без ФИО)"),
                    "period": self._fmt_ownership_period(own.group_since(g), own.group_until(g)),
                    "charged": None,
                    "paid": None,
                    "debt": debt_v,
                    "is_current": False,
                })
        else:
            # Старая модель: owners-based
            recs = rec.get("owners", []) or []
            owner_count = sum(1 for o in recs if own.is_owner(o))
            show = owner_count > 1 or own.has_history(recs)
            self.tabs.setTabVisible(self._owners_tab_idx, show)
            if not show:
                return
            ob_rows = vznosy.balances_by_owner(self._plot, area, self._as_of,
                                               rates, adj, self._df, recs,
                                               ownership_form=rec.get("ownership_form"))
            all_rows = [
                {"name": f"{ob.name}  ({'текущий' if ob.is_current else 'прежний'})",
                 "period": self._fmt_ownership_period(ob.since, ob.until),
                 "charged": ob.charged, "paid": ob.paid, "debt": ob.debt,
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
    def _fmt_ownership_period(since, until) -> str:
        if since and until:
            return f"{since.strftime('%d.%m.%Y')}—{until.strftime('%d.%m.%Y')}"
        if since:
            return f"с {since.strftime('%d.%m.%Y')}"
        if until:
            return f"по {until.strftime('%d.%m.%Y')}"
        return "—"

    def _set_year_row(self, r: int, cells: list, *, ignored: bool = False):
        dim_color = QColor("#3a4a56")   # приглушённый цвет для игнорируемых строк
        for c, (text, color) in enumerate(cells):
            it = QTableWidgetItem(text)
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            if ignored:
                it.setForeground(dim_color)
            elif color:
                it.setForeground(QColor(color))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(r, c, it)
        self.table.setRowHeight(r, 28)

    def _add_adjustment(self, kind: str):
        dlg = VznosyAdjustmentDialog(self._plot, self, default_kind=kind)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        result = dlg.get_result()
        if not result:
            return
        from core import vznosy
        adj = vznosy.load_adjustments()
        adj.setdefault(self._plot, []).append(result)
        vznosy.save_adjustments(adj)
        self._rebuild()

    def _delete_adjustment(self, idx: int):
        from core import vznosy
        adj = vznosy.load_adjustments()
        items = adj.get(self._plot, [])
        if 0 <= idx < len(items):
            reply = QMessageBox.question(
                self, "Удалить корректировку",
                "Удалить эту корректировку?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            items.pop(idx)
            if items:
                adj[self._plot] = items
            else:
                adj.pop(self._plot, None)
            vznosy.save_adjustments(adj)
            self._rebuild()

    def _on_pdf(self):
        try:
            from core import receipt
        except ImportError:
            QMessageBox.information(self, "Квитанции",
                                    "Модуль квитанций ещё не подключён.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить квитанцию",
            f"Уч_{self._plot}_ЧВ_{self._as_of.isoformat()}.pdf",
            "PDF (*.pdf)"
        )
        if not path:
            return
        try:
            receipt.save_vznosy_receipt_pdf(self._plot, self._df, path,
                                            as_of=self._as_of)
            QMessageBox.information(self, "Квитанция", f"Сохранено:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")

