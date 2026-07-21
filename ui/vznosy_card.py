"""Карточки и диалоги для вкладки «Долги по членским взносам»."""
from __future__ import annotations

from datetime import date

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox, QDateEdit, QDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTabWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from core import energy
from core.utils import fmt_money
from ui.buttons import GhostButton, PrimaryButton, SecondaryButton
from ui.common import ClipFrame, style_date_popup
from ui.dialogs import (
    AlertDialog as _AlertDialog,
    BaseDialog as _FramelessDialog,
    ConfirmDialog as _ConfirmDialog,
    exec_dialog as _exec_dialog,
)
from ui.plots_widget import _is_visible
from ui.theme import C, FS, RAD


def _group_primary_name(group: dict, empty: str = "—") -> str:
    """ФИО «под звёздочкой» (is_visible) для группы/договора, иначе первый
    собственник — тот же принцип, что и в главном списке участков и
    таблице долгов ЧВ (см. _plot_primary_owner в ui.plots_widget), вместо
    полного списка через запятую (core.ownership.group_label): для
    архивных групп список всех совладельцев в одной ячейке был слишком
    длинным, а нужен только тот, кто был отмечен звёздочкой именно у ЭТОГО
    (в т.ч. прошлого) договора — у каждой группы своя запись owners."""
    from core import ownership as own
    owners = own.group_owners(group)
    if not owners:
        return empty
    visible = next((o for o in owners if _is_visible(o)), None)
    main = visible or next((o for o in owners if own.is_owner(o)), owners[0])
    name = own.owner_name(main) if main else ""
    return name if name else empty


def _clip_table(table: QTableWidget) -> ClipFrame:
    """Оборачивает таблицу в ClipFrame — маска под скруглённые углы вкладки
    (QTabWidget::pane имеет border-radius:6px), иначе квадратная заливка
    строк таблицы (viewport со скроллом рисует поверх собственной QSS-рамки
    таблицы) вылезает за скруглённый край панели — тот же баг, что уже
    чинили в «Периодах членских взносов» (rates_widget.py)."""
    frame = ClipFrame(QColor(0, 0, 0, 0), RAD.FRAME)
    fl = QVBoxLayout(frame)
    fl.setContentsMargins(0, 0, 0, 0)
    fl.addWidget(table)
    frame.finish_setup()
    return frame


class VznosyAdjustmentDialog(_FramelessDialog):
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

        lay.addLayout(self.make_header(f"Корректировка ЧВ на участке {plot}"))

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
        style_date_popup(self.inp_date)
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

        btn_cancel = SecondaryButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_save = PrimaryButton("Сохранить")
        btn_save.clicked.connect(self._on_accept)
        lay.addLayout(self.make_button_row(btn_cancel, btn_save))

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
                _AlertDialog.show_alert(self, "Ошибка",
                                        "Сумма должна быть неотрицательным числом")
                return
            result["amount"] = f"{v:g}"

        self._result = result
        self.accept()

    def get_result(self) -> dict | None:
        return self._result


class VznosyCardDialog(_FramelessDialog):
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
        from core import ownership as own
        active = own.active_group(energy.plot_record(self._plot))
        owners_text = (_group_primary_name(active, empty="владельцы не указаны")
                       if active else "владельцы не указаны")
        area = vznosy.plot_area_map().get(self._plot)
        area_text = f"{area:g} м²" if area is not None else "площадь не указана"

        # ── Верхний ряд: заголовок + действие + закрыть ───────────
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self.head = QLabel(
            f"<b>Участок {self._plot}</b>  ·  {owners_text}  ·  {area_text}"
        )
        self.head.setStyleSheet("font-size:14px;color:#111827;background:transparent;")
        top_row.addWidget(self.head, 1)

        btn_adj = SecondaryButton("Корректировка начислений", icon="tune")
        btn_adj.clicked.connect(lambda: self._add_adjustment("charge_override"))
        top_row.addWidget(btn_adj)

        btn_close = QPushButton("✕", objectName="btnPanelClose")
        btn_close.setFixedSize(24, 24)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_close.clicked.connect(self.accept)
        top_row.addWidget(btn_close)

        lay.addLayout(top_row)

        self.warning_lbl = QLabel("")
        self.warning_lbl.setStyleSheet(
            "color:#B91C1C;background:#FEF2F2;border:1px solid #FCA5A5;"
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
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "Период", "Группа/договор", "Тариф", "Начислено", "Оплачено за период",
            "Корректировка", "Баланс нараст.",
        ])
        # «Тариф» — самая длинная по содержимому колонка (напр. «15 ₽/м² ·
        # 545 м² → 8 175,00 ₽») — растягивается на остаток ширины, чтобы
        # таблица целиком помещалась в окно без горизонтальной прокрутки;
        # остальные колонки — под фиксированный, но компактный контент.
        hdr = self.table.horizontalHeader()
        widths = [150, 130, None, 95, 120, 140, 105]
        for c, w in enumerate(widths):
            if w is None:
                hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
            else:
                hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
                self.table.setColumnWidth(c, w)
        hdr.setStretchLastSection(False)
        t1_lay.addWidget(_clip_table(self.table), 1)
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
        t2_lay.addWidget(_clip_table(self.owners_table), 1)
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
        # «Примечание» растягивается на остаток — тот же приём, что и у
        # «Тариф» в разбивке по периодам (см. выше), чтобы не было
        # горизонтальной прокрутки; последняя колонка — узкая, только под
        # кнопку удаления, стретчиться незачем.
        ahdr = self.adj_table.horizontalHeader()
        adj_widths = [100, 160, 70, 100, None, 36]
        for c, w in enumerate(adj_widths):
            if w is None:
                ahdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
            else:
                ahdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
                self.adj_table.setColumnWidth(c, w)
        ahdr.setStretchLastSection(False)
        t3_lay.addWidget(_clip_table(self.adj_table), 1)
        self.tabs.addTab(tab3, "Ручные операции и корректировки")

        lay.addWidget(self.tabs, 1)

        # ── Подсказка-сводка — под таблицей, как и в других местах
        # приложения (см. objectName="statusLabel" в rates_widget.py,
        # vznosy_debt_widget.py).
        self.summary_lbl = QLabel("", objectName="statusLabel")
        self.summary_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        lay.addWidget(self.summary_lbl)

        self.setStyleSheet(self.base_qss() + f"""
            QTabWidget#vznosyTabs::pane {{
                border: 1px solid {C.BORDER_LIGHT}; border-radius: 6px;
                background: {C.BG_SURFACE};
            }}
            QTabBar::tab {{
                background: {C.BG_HOVER}; color: {C.TEXT_MUTED};
                border: 1px solid {C.BORDER_LIGHT};
                border-bottom: none; border-top-left-radius: 6px;
                border-top-right-radius: 6px; padding: 8px 16px;
                font-size: {FS.SMALL}px; font-weight: 500; margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background: {C.BG_SURFACE}; color: {C.BRAND}; font-weight: 700;
                border-bottom: 2px solid {C.BRAND};
            }}
            QTabBar::tab:hover:!selected {{
                background: {C.BORDER_LIGHT}; color: {C.TEXT_BODY};
            }}
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

        # Обновить заголовок (площадь и/или активная группа могли измениться)
        from core import ownership as own
        active = own.active_group(energy.plot_record(self._plot))
        owners_text = (_group_primary_name(active, empty="владельцы не указаны")
                       if active else "владельцы не указаны")
        area_text = f"{area:g} м²" if area is not None else "площадь не указана"
        self.head.setText(
            f"<b>Участок {self._plot}</b>  ·  {owners_text}  ·  {area_text}"
        )

        # Баннер о площади
        if bal.area_missing_warning:
            self.warning_lbl.setText(
                "Для этого участка не указана площадь — тариф «за м²» не применён. "
                "Откройте вкладку «Участки» и укажите площадь."
            )
            self.warning_lbl.setVisible(True)
        else:
            self.warning_lbl.setVisible(False)

        # Главная таблица периодов
        plot_rec = energy.plot_record(self._plot)
        self.table.setRowCount(len(bal.breakdown))
        cum = 0.0
        for r, y in enumerate(bal.breakdown):
                # Метка периода
                if y.period_to:
                    period_label = (f"{y.period_from.strftime('%d.%m.%Y')}"
                                    f"—{y.period_to.strftime('%d.%m.%Y')}")
                else:
                    period_label = f"{y.period_from.strftime('%d.%m.%Y')}—..."

                # Группа/договор, действовавшая в периоде — ФИО «под
                # звёздочкой» этой группы, не полный список совладельцев
                # (см. _group_primary_name).
                g_from = own.group_at(plot_rec, y.period_from)
                group_text = _group_primary_name(g_from) if g_from else "—"
                group_color = "#374151" if g_from else "#9CA3AF"
                if y.period_to:
                    g_to = own.group_at(plot_rec, y.period_to)
                    key_from = (own.group_since(g_from), own.group_until(g_from)) if g_from else None
                    key_to = (own.group_since(g_to), own.group_until(g_to)) if g_to else None
                    if key_to != key_from:
                        to_text = _group_primary_name(g_to) if g_to else "—"
                        group_text = f"{group_text} → {to_text}"
                        group_color = "#B45309"

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
                    note_color = "#9CA3AF"
                elif y.overridden:
                    ov = vznosy._period_override(self._plot, period_key,
                                                 y.period_from.year, adj)
                    kind = ov.get("kind", "") if ov else ""
                    kind_label = "освобождён" if kind in ("exempt_period", "exempt_year") else "переопределено"
                    if ov and ov.get("note"):
                        note_text = f"{kind_label}: {ov['note']}"
                    else:
                        note_text = kind_label
                    note_color = "#B45309"

                # Накопительный баланс
                if not y.ignored:
                    if y.amount is not None:
                        cum += y.amount
                    cum -= paid_period
                cum_text = fmt_money(cum) if not y.ignored else "—"
                cum_color = "#DC2626" if cum > 0 else ("#059669" if cum < 0 else None)

                self._set_year_row(r, [
                    (period_label, None),
                    (group_text, group_color),
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

            btn = GhostButton(icon="delete", tooltip="Удалить корректировку",
                              size=22, icon_size=14, danger=True)
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
                        "name": _group_primary_name(active, empty="(нет лиц)"),
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
                    "name": _group_primary_name(g, empty="(без ФИО)"),
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
        dim_color = QColor("#9CA3AF")   # приглушённый цвет для игнорируемых строк
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
        if _exec_dialog(dlg, self) != QDialog.DialogCode.Accepted:
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
            if not _ConfirmDialog.confirm(
                self, "Удалить корректировку",
                "Удалить эту корректировку?",
            ):
                return
            items.pop(idx)
            if items:
                adj[self._plot] = items
            else:
                adj.pop(self._plot, None)
            vznosy.save_adjustments(adj)
            self._rebuild()

