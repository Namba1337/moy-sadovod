import os
import re
from datetime import date

from PyQt6.QtCore import Qt, QDate, QModelIndex
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog,
    QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSpinBox, QTreeView, QVBoxLayout, QWidget,
)

from core import energy
from core.utils import fmt_money
from ui.energy_card import PlotCardDialog
from ui.rates_widget import RatesWidget
from ui.plots_widget import (
    _SortHeaderView, _ClipFrame, _TREE_STYLE, _SB_W, _FlatTableModel,
    _DEBT_COLOR_LIGHT,
)
from ui.detail_widget import _FramelessDialog, _exec_dialog, _AlertDialog


# ============================================================================ #
#  Модель данных                                                               #
# ============================================================================ #

class _EnergyModel(_FlatTableModel):
    """Плоская модель для таблицы долгов по электроэнергии."""

    COLUMNS = [
        "Участок", "Владелец", "Последнее показание", "Дата показ.",
        "Без показ., мес.",
        "Начислено", "Оплачено", "Стартовое", "Долг / Аванс",
        "Без оплаты, мес.", "Тип расчёта",
    ]


# ============================================================================ #
#  Виджет вкладки «Электроэнергия»                                             #
# ============================================================================ #

class _NormsDialog(_FramelessDialog):
    """Кастомная (без нативного чрома) карточка для RatesWidget — единственная
    точка стилизации служебных objectName-ов виджета (pageTitle/filterFrame/
    btnPrimary и т.п.), которые вне контекста QMainWindow не получают
    глобальный QSS приложения. См. аналог _RatesDialog в vznosy_debt_widget.py."""

    def __init__(self, rates_widget: RatesWidget, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Нормативы — тарифы на электроэнергию")
        self.setModal(True)
        self.setMinimumSize(820, 520)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(12, 10, 12, 0)
        header.addStretch()
        btn_close = QPushButton("✕", objectName="btnPanelClose")
        btn_close.setFixedSize(24, 24)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_close.clicked.connect(self.reject)
        header.addWidget(btn_close)
        lay.addLayout(header)

        lay.addWidget(rates_widget, stretch=1)
        self.setStyleSheet(self._frame_qss() + """
            QLabel { background: transparent; color: #374151; }
            QLabel#pageTitle { font-size: 18px; font-weight: 700; color: #1F2937; }
            QLabel#filterLabel { color: #9AA3AE; font-size: 13px; }
            QLabel#statusLabel { color: #6B7280; font-size: 12px; }
            QFrame#filterFrame {
                background: #F8F9FA; border: 1px solid #E3E8EE; border-radius: 8px;
            }
            QLineEdit#searchInput {
                background: #FFFFFF; border: 1px solid #D5DCE4; border-radius: 6px;
                color: #1F2937; padding: 7px 12px; font-size: 13px;
            }
            QLineEdit#searchInput:focus { border: 1px solid #07414F; }
            QDateEdit#datePicker {
                background: #FFFFFF; border: 1px solid #D5DCE4; border-radius: 6px;
                color: #1F2937; padding: 7px 10px; font-size: 13px;
            }
            QDateEdit#datePicker::drop-down { border: none; width: 18px; }
            QPushButton#btnPrimary {
                background: #07414F; color: #FFFFFF; border: none; border-radius: 6px;
                padding: 8px 18px; font-size: 13px; font-weight: 600;
            }
            QPushButton#btnPrimary:hover   { background: #0B5A6E; }
            QPushButton#btnPrimary:pressed { background: #062F38; }
            QPushButton#btnSecondary {
                background: #FFFFFF; color: #3C4654;
                border: 1px solid #D5DCE4; border-radius: 6px;
                padding: 7px 12px; font-size: 13px;
            }
            QPushButton#btnSecondary:hover { background: #F0F3F7; color: #1F2937; }
            QPushButton#btnPanelClose {
                background: transparent; border: none; color: #9CA3AF;
                font-size: 15px; font-weight: 600; border-radius: 12px;
            }
            QPushButton#btnPanelClose:hover { background: #F3F4F6; color: #374151; }
            QTableWidget#summaryTable {
                background: #FFFFFF; border: 1px solid #E3E8EE; border-radius: 8px;
                gridline-color: #EAEDF1; color: #1F2937; font-size: 12px;
                selection-background-color: #E8F0F5; selection-color: #07414F;
            }
            QTableWidget#summaryTable QHeaderView::section {
                background: #EEF1F5; color: #6B7280; border: none;
                border-right: 1px solid #E3E8EE; border-bottom: 2px solid #E3E8EE;
                padding: 8px 10px; font-size: 12px; font-weight: 600;
            }
            QTableWidget#summaryTable::item {
                padding: 4px 10px; border-bottom: 1px solid #EAEDF1;
            }
        """)


class _EnergySettingsDialog(_FramelessDialog):
    """Общие настройки электроэнергии на всё СНТ (решение общего собрания,
    не за отдельный участок): автопереход при отсутствии показаний +
    глобальные значения по умолчанию для расчётного метода (норматив,
    окно усреднения). Участок использует своё собственное значение, если
    оно задано в его карточке («⚙ Тип расчёта»); иначе — глобальное отсюда,
    причём живьём: смена значения здесь сразу отражается на всех участках
    без собственного override (см. energy.norm_kw_of()/avg_window_months_of())."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки электроэнергии")
        self.setMinimumWidth(460)
        self.setModal(True)
        settings = energy.load_auto_settings()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 18)
        lay.setSpacing(14)

        title = QLabel("Настройки электроэнергии")
        title.setStyleSheet("font-size:14px;font-weight:700;color:#111827;")
        lay.addWidget(title)

        # ── Автопереход ──────────────────────────────────────────────
        sect1 = QLabel("Автопереход при отсутствии показаний")
        sect1.setStyleSheet("font-size:12px;font-weight:600;color:#07414F;")
        lay.addWidget(sect1)

        info = QLabel(
            "Если участок типа «Счётчик» не передаёт показания N месяцев "
            "подряд — начисление за эти месяцы автоматически оценивается. "
            "Тип расчёта на самом участке при этом НЕ меняется: как только "
            "придёт реальное показание, закрывающее разрыв, начисление за "
            "пропущенные месяцы автоматически пересчитается по факту."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#6B7280;font-size:12px;")
        lay.addWidget(info)

        self.chk_enabled = QCheckBox("Включить автопереход")
        self.chk_enabled.setChecked(bool(settings.get("enabled")))
        lay.addWidget(self.chk_enabled)

        row = QHBoxLayout()
        row.addWidget(QLabel("Порог отсутствия показаний, мес.:"))
        self.spin_months = QSpinBox()
        self.spin_months.setRange(1, 24)
        self.spin_months.setValue(
            int(settings.get("months") or energy.DEFAULT_AUTO_SWITCH_MONTHS))
        row.addWidget(self.spin_months)
        row.addStretch()
        lay.addLayout(row)

        # ── Глобальные значения по умолчанию ────────────────────────
        sect2 = QLabel("Значения по умолчанию для расчётного метода")
        sect2.setStyleSheet("font-size:12px;font-weight:600;color:#07414F;margin-top:6px;")
        lay.addWidget(sect2)

        info2 = QLabel(
            "Используются, только если на конкретном участке (кнопка "
            "«⚙ Тип расчёта») своё значение не задано — например, "
            "региональный норматив на освещение можно указать один раз "
            "здесь, а не на каждом участке."
        )
        info2.setWordWrap(True)
        info2.setStyleSheet("color:#6B7280;font-size:12px;")
        lay.addWidget(info2)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Норматив мощности по умолчанию, кВт:"))
        self.inp_default_norm = QLineEdit()
        self.inp_default_norm.setPlaceholderText("не задано")
        default_norm = settings.get("default_norm_kw")
        if default_norm not in (None, ""):
            self.inp_default_norm.setText(f"{float(default_norm):g}")
        self.inp_default_norm.setFixedWidth(120)
        row2.addWidget(self.inp_default_norm)
        row2.addStretch()
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Окно усреднения по умолчанию, мес.:"))
        self.spin_default_window = QSpinBox()
        self.spin_default_window.setRange(1, 24)
        default_window = settings.get("default_avg_window_months")
        self.spin_default_window.setValue(
            int(default_window) if default_window else energy.OWN_AVERAGE_WINDOW_MONTHS)
        row3.addWidget(self.spin_default_window)
        row3.addStretch()
        lay.addLayout(row3)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Сохранить")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        self.setStyleSheet(self._frame_qss() + """
            QLabel { background: transparent; color: #374151; font-size: 13px; }
            QCheckBox { color: #374151; background: transparent; font-size: 13px; }
            QLineEdit, QSpinBox {
                background: #F8F9FA; border: 1px solid #D1D5DB; border-radius: 5px;
                color: #374151; padding: 6px 8px; font-size: 13px;
            }
            QLineEdit:focus, QSpinBox:focus { border: 1px solid #07414F; }
            QDialogButtonBox QPushButton {
                background: #07414F; color: white; border: none;
                border-radius: 6px; padding: 7px 18px; font-size: 13px; font-weight: 600;
            }
            QDialogButtonBox QPushButton:hover { background: #0B5A6E; }
            QDialogButtonBox QPushButton[text='Отмена'] { background: #E5E7EB; color: #6B7280; }
        """)

    def _on_accept(self):
        raw_norm = self.inp_default_norm.text().strip().replace(",", ".")
        default_norm_kw = None
        if raw_norm:
            try:
                nv = float(raw_norm)
            except ValueError:
                _AlertDialog.show_alert(self, "Настройки электроэнергии",
                                        "Норматив по умолчанию должен быть числом.")
                return
            if nv <= 0:
                _AlertDialog.show_alert(self, "Настройки электроэнергии",
                                        "Норматив по умолчанию должен быть положительным.")
                return
            default_norm_kw = nv

        energy.save_auto_settings({
            "enabled": self.chk_enabled.isChecked(),
            "months": self.spin_months.value(),
            "default_norm_kw": default_norm_kw,
            "default_avg_window_months": self.spin_default_window.value(),
        })
        self.accept()


class EnergyDebtWidget(QWidget):
    """Вкладка контроля долгов по электроэнергии."""

    def __init__(self):
        super().__init__()
        self.setAutoFillBackground(False)
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

        self.chk_active_only = QCheckBox("Только активный договор")
        self.chk_active_only.setChecked(True)
        self.chk_active_only.setToolTip(
            "Если включено — начисления и оплаты считаются только с даты начала "
            "текущей активной группы (договора). Если выключено — вся история "
            "участка, включая прежних собственников."
        )
        self.chk_active_only.setStyleSheet(
            "QCheckBox{color:#374151;background:transparent;font-size:12px;}"
            "QCheckBox::indicator{width:15px;height:15px;}"
        )
        self.chk_active_only.stateChanged.connect(self._rebuild)
        top.addWidget(self.chk_active_only)

        self.chk_stale_readings = QCheckBox("Без показаний ≥ 3 мес.")
        self.chk_stale_readings.setToolTip(
            "Участки со счётчиком, которые не передают показания 3 месяца "
            "подряд и дольше — по порядку действий СНТ таким пора переходить "
            "на расчётный метод (кнопка «⚙ Тип расчёта» на карточке участка)."
        )
        self.chk_stale_readings.setStyleSheet(
            "QCheckBox{color:#374151;background:transparent;font-size:12px;}"
            "QCheckBox::indicator{width:15px;height:15px;}"
        )
        self.chk_stale_readings.stateChanged.connect(self._rebuild)
        top.addWidget(self.chk_stale_readings)

        top.addStretch()

        top.addWidget(QLabel("на дату:", objectName="filterLabel"))
        self.date_as_of = QDateEdit(calendarPopup=True, objectName="datePicker",
                                    displayFormat="dd.MM.yyyy")
        self.date_as_of.setDate(QDate.currentDate())
        self.date_as_of.setMaximumDate(QDate.currentDate())
        self.date_as_of.dateChanged.connect(self._rebuild)
        top.addWidget(self.date_as_of)

        self.cb_only_debt = QComboBox(objectName="filterCombo")
        self.cb_only_debt.addItems(["Все участки", "Только должники", "Только аванс/0"])
        self.cb_only_debt.currentIndexChanged.connect(self._rebuild)
        top.addWidget(self.cb_only_debt)

        self.btn_mass_pdf = QPushButton("📄  Квитанции должникам", objectName="btnSecondary")
        self.btn_mass_pdf.clicked.connect(self._export_debtor_receipts)
        top.addWidget(self.btn_mass_pdf)

        btn_rates = QPushButton("📐  Нормативы", objectName="btnSecondary")
        btn_rates.clicked.connect(self._open_rates_dialog)
        top.addWidget(btn_rates)

        btn_settings = QPushButton("⚙  Настройки", objectName="btnSecondary")
        btn_settings.setToolTip(
            "Автопереход при отсутствии показаний + глобальные значения "
            "по умолчанию (норматив, окно усреднения) для расчётного метода."
        )
        btn_settings.clicked.connect(self._open_energy_settings_dialog)
        top.addWidget(btn_settings)

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
            lb.setStyleSheet(f"color:{color};background:transparent;font-size:11px;")
            legend.addWidget(lb)
        legend.addStretch()
        lay.addLayout(legend)

        # ── Модель ──────────────────────────────────────────────────────────
        self.model = _EnergyModel(self)

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

        # Поиск в шапке: Участок, Владелец
        self.hdr_view.add_search_col(_EnergyModel.COLUMNS.index("Участок"))
        self.hdr_view.add_search_col(_EnergyModel.COLUMNS.index("Владелец"))
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

        # Сверка с поставщиком
        self.recon_lbl = QLabel("", objectName="statusLabel")
        self.recon_lbl.setWordWrap(True)
        self.recon_lbl.setStyleSheet(
            "background:#F9FAFB;border:1px solid #E5E7EB;border-radius:6px;"
            "padding:10px 14px;color:#374151;font-size:12px;"
        )

        self.status_lbl = QLabel("Загрузите выписку на вкладке «Детализация»",
                                  objectName="statusLabel")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        table_vbox = QVBoxLayout()
        table_vbox.setSpacing(4)
        table_vbox.setContentsMargins(0, 0, 0, 0)
        table_vbox.addWidget(table_outer, stretch=1)
        table_vbox.addWidget(self.recon_lbl)
        table_vbox.addWidget(self.status_lbl)
        lay.addLayout(table_vbox)

        self.rates = RatesWidget()

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

    # -- сортировка / поиск -------------------------------------------------- #

    def _on_sort_changed(self, col, order):
        self.model.sort(col, order)
        self.hdr_view.setSortIndicator(col, order)

    def _on_search_changed(self, col, text):
        self._search_filters[col] = text.strip().lower()
        self._rebuild()

    # -- публичный API ------------------------------------------------------- #

    def _open_rates_dialog(self):
        dlg = _NormsDialog(self.rates, self)
        _exec_dialog(dlg, self)
        dlg.layout().removeWidget(self.rates)
        self.rates.setParent(self)  # type: ignore[arg-type]
        self._rebuild()

    def _open_energy_settings_dialog(self):
        dlg = _EnergySettingsDialog(self)
        if _exec_dialog(dlg, self) == QDialog.DialogCode.Accepted:
            self._rebuild()

    def refresh(self, df):
        self._df = df
        self._rebuild()

    def _plot_list(self) -> list[str]:
        plots = energy.load_plots()
        nums = [str(p.get("num", "")) for p in plots if p.get("num")]
        # Plus all plots with readings even if not in registry
        meters = energy.load_meters()
        for key in meters:
            parts = key.split(":")
            if parts and parts[0] not in nums:
                nums.append(parts[0])
        def _key(s):
            try:
                return (0, int(s))
            except ValueError:
                return (1, s)
        return sorted(set(nums), key=_key)

    def _rebuild(self):
        from core import ownership as own
        as_of = self.date_as_of.date().toPyDate()
        meters = energy.load_meters()
        rates = energy.load_rates()
        repls = energy.load_replacements()
        baseline = energy.load_baseline()
        if self._df is not None and not self._df.empty:
            baseline["start_date"] = self._df["Дата"].min().date().isoformat()
        owners = energy.owners_map()
        plot_recs = energy.plots_by_num()
        plots = self._plot_list()
        active_only = self.chk_active_only.isChecked()
        auto_settings = energy.load_auto_settings()
        stale_threshold = auto_settings.get("months") or energy.DEFAULT_AUTO_SWITCH_MONTHS
        self.chk_stale_readings.setText(f"Без показаний ≥ {stale_threshold} мес.")
        self.chk_stale_readings.setToolTip(
            f"Участки со счётчиком, которые не передают показания "
            f"{stale_threshold} мес. подряд и дольше. "
            + ("Начисление для них уже ведётся автоматически по среднему "
               "потреблению (автопереход включён)." if auto_settings.get("enabled")
               else "По порядку действий СНТ таким пора переходить на "
                    "расчётный метод (кнопка «⚙ Тип расчёта» на карточке участка).")
        )

        total_debt = 0.0
        total_charged = 0.0
        total_paid = 0.0
        debt_count = 0
        debts_map: dict[str, dict] = {}
        type_counts = {energy.BILLING_METER: 0, energy.BILLING_CALCULATED: 0,
                       energy.BILLING_DIRECT: 0}
        type_charged = {energy.BILLING_METER: 0.0, energy.BILLING_CALCULATED: 0.0}
        direct_debt_total = 0.0
        plots_list = list(plot_recs.values())
        stale_readings_count = 0
        auto_estimated_count = 0

        # Индекс платежей — один проход по выписке вместо скана на каждый
        # участок; plots_list передаётся в ядро, чтобы оно не перечитывало
        # snt_plots.json с диска на каждый вызов.
        pay_idx = energy.payments_index(self._df, energy.CATS_ELECTRO_INCOME)

        rows: list[dict] = []
        for plot in plots:
            bt = energy.billing_type_of(plot, plots_list)
            type_counts[bt] = type_counts.get(bt, 0) + 1
            bal = energy.balance(plot, as_of, meters, rates, repls, baseline, self._df,
                                 plots=plots_list, auto_settings=auto_settings,
                                 pay_index=pay_idx)
            if bal.auto_estimated:
                auto_estimated_count += 1
            if active_only:
                since = own.group_since(own.active_group(plot_recs.get(plot, {})) or {})
                gb = energy.balance_for_active_group(
                    plot, as_of, meters, rates, repls, baseline, self._df, since=since,
                    plots=plots_list, auto_settings=auto_settings, pay_index=pay_idx)
                charged, paid, base_amt, debt = gb.charged, gb.paid, gb.baseline, gb.debt
            else:
                charged, paid, base_amt, debt = bal.charged, bal.paid, bal.baseline, bal.debt

            owner = ", ".join(owners.get(plot, [])) or "—"
            last_reading_text = "—"
            last_date_text = "—"
            reading_sort = -1.0
            date_sort = -1.0
            if bal.last_reading:
                ly, lm, lv = bal.last_reading
                last_reading_text = f"{lv:g}"
                last_date_text = f"{lm:02d}.{ly}"
                reading_sort = float(lv)
                date_sort = float(ly * 100 + lm)

            color = energy.debt_color(debt, monthly_avg=300.0)
            debts_map[plot] = {"debt": debt, "color": color,
                                "charged": charged, "paid": paid,
                                "billing_type": bt}

            if bt == energy.BILLING_DIRECT:
                direct_debt_total += debt
            else:
                total_debt += debt
                total_charged += charged
                total_paid += paid
                type_charged[bt] = type_charged.get(bt, 0.0) + charged
                if debt > 0.5:
                    debt_count += 1

            try:
                plot_sort = float(str(plot).split(",")[0])
            except ValueError:
                plot_sort = 0.0

            mwp = bal.months_without_payment
            mwr = energy.months_without_reading(plot, meters, as_of, plots=plots_list)
            if mwr is not None and mwr >= stale_threshold:
                stale_readings_count += 1
            row = {
                "_text_Участок": f"уч. {plot}",
                "_sort_Участок": plot_sort,
                "_fg_Участок": "#07414F",
                "_bold_Участок": True,

                "_text_Владелец": owner,
                "_sort_Владелец": owner,
                "_fg_Владелец": "#374151",

                "_text_Последнее показание": last_reading_text,
                "_sort_Последнее показание": reading_sort,
                "_fg_Последнее показание": "#374151",

                "_text_Дата показ.": last_date_text,
                "_sort_Дата показ.": date_sort,
                "_fg_Дата показ.": "#9CA3AF",

                "_text_Без показ., мес.": "—" if mwr is None else str(mwr),
                "_sort_Без показ., мес.": mwr or 0,
                "_fg_Без показ., мес.": "#DC2626" if (mwr or 0) >= stale_threshold else "#374151",

                "_text_Начислено": fmt_money(charged),
                "_sort_Начислено": charged,
                "_fg_Начислено": "#f9a825" if charged else "#9CA3AF",

                "_text_Оплачено": fmt_money(paid),
                "_sort_Оплачено": paid,
                "_fg_Оплачено": "#059669" if paid else "#9CA3AF",

                "_text_Стартовое": fmt_money(base_amt) if base_amt else "—",
                "_sort_Стартовое": base_amt,
                "_fg_Стартовое": "#c97c7c" if base_amt else "#9CA3AF",

                "_text_Долг / Аванс": fmt_money(debt),
                "_sort_Долг / Аванс": debt,
                "_bg_Долг / Аванс": _DEBT_COLOR_LIGHT.get(color, color),
                "_bold_Долг / Аванс": True,

                "_text_Без оплаты, мес.": "—" if mwp is None else str(mwp),
                "_sort_Без оплаты, мес.": mwp or 0,
                "_fg_Без оплаты, мес.": "#DC2626" if (mwp or 0) > 3 else "#374151",

                "_text_Тип расчёта": (
                    f"{energy.BILLING_LABELS.get(bt, bt)} · авто-оценка"
                    if bal.auto_estimated else energy.BILLING_LABELS.get(bt, bt)),
                "_sort_Тип расчёта": bt,
                "_bg_Тип расчёта": "#E8F0F5" if bt == energy.BILLING_DIRECT else None,
                "_fg_Тип расчёта": ("#B45309" if bal.auto_estimated else
                                    "#07414F" if bt == energy.BILLING_DIRECT else "#6B7280"),
            }
            rows.append(row)

        # Поиск в шапке
        for col_idx, text in self._search_filters.items():
            if not text:
                continue
            col_name = _EnergyModel.COLUMNS[col_idx]
            if col_name == "Участок":
                rows = [r for r in rows if text in r.get("_text_Участок", "").lower()]
            elif col_name == "Владелец":
                rows = [r for r in rows if text in r.get("_text_Владелец", "").lower()]

        # Фильтр по долгу
        mode = self.cb_only_debt.currentText()
        if mode == "Только должники":
            rows = [r for r in rows if r.get("_sort_Долг / Аванс", 0.0) > 0.5]
        elif mode == "Только аванс/0":
            rows = [r for r in rows if r.get("_sort_Долг / Аванс", 0.0) <= 0.5]

        if self.chk_stale_readings.isChecked():
            rows = [r for r in rows if r.get("_sort_Без показ., мес.", 0) >= stale_threshold]

        self.model.load(rows)

        # Ширины колонок
        widths = [85, 240, 140, 95, 110, 110, 110, 95, 120, 110, 120]
        for h in (self.hdr_view, self.tree.header()):
            h.setStretchLastSection(False)
            for c, w in enumerate(widths):
                if c == 1:
                    h.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
                else:
                    h.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
                    h.resizeSection(c, w)

        self._last_debts = debts_map

        direct_note = ""
        if type_counts.get(energy.BILLING_DIRECT):
            direct_note = (
                f"  ·  прямой договор: {type_counts[energy.BILLING_DIRECT]} "
                f"(вне баланса СНТ, долг {fmt_money(direct_debt_total)} закрывается вручную)"
            )
        stale_note = (f"  ·  без показаний ≥{stale_threshold} мес: {stale_readings_count}"
                      if stale_readings_count else "")
        auto_note = (f"  ·  автооценка активна: {auto_estimated_count}"
                     if auto_estimated_count else "")
        self.status_lbl.setText(
            f"Участков: {len(plots)}  ·  "
            f"счётчик: {type_counts.get(energy.BILLING_METER, 0)} "
            f"({fmt_money(type_charged.get(energy.BILLING_METER, 0.0))}), "
            f"расчётный: {type_counts.get(energy.BILLING_CALCULATED, 0)} "
            f"({fmt_money(type_charged.get(energy.BILLING_CALCULATED, 0.0))})"
            f"{direct_note}  ·  должников: {debt_count}  ·  "
            f"общий долг (тип 1+2): {fmt_money(total_debt)}{stale_note}{auto_note}"
        )

        # Сверка
        try:
            df = self._df
            if df is None or df.empty:
                date_from = energy._parse_iso(baseline.get("start_date", "")) or date(as_of.year, 1, 1)
                date_to = as_of
            else:
                date_from = max(
                    df["Дата"].min().date(),
                    energy._parse_iso(baseline.get("start_date", "")) or df["Дата"].min().date(),
                )
                date_to = as_of
            common = energy.load_common_meter()
            rec = energy.reconcile(date_from, date_to, plots,
                                    meters, rates, repls, common, df,
                                    plot_records=plots_list)
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

    def _open_card(self, index: QModelIndex):
        if not index.isValid():
            return
        node = index.internalPointer()
        if node is None:
            return
        plot = node.get("_text_Участок", "").replace("уч. ", "").strip()
        if not plot:
            return
        as_of = self.date_as_of.date().toPyDate()
        dlg = PlotCardDialog(plot, self._df, self, as_of=as_of)
        _exec_dialog(dlg, self)
        self._rebuild()

    def _export_debtor_receipts(self):
        if self._df is None or len(self._df) == 0:
            _AlertDialog.show_alert(self, "Квитанции", "Сначала загрузите выписку.")
            return
        meters = energy.load_meters()
        rates = energy.load_rates()
        repls = energy.load_replacements()
        baseline = energy.load_baseline()
        plots_recs = energy.load_plots()
        by_num = {str(p.get("num", "")): p for p in plots_recs}
        as_of = self.date_as_of.date().toPyDate()

        # Должник = участок, где задолженность у ТЕКУЩЕГО собственника.
        pay_idx = energy.payments_index(self._df, energy.CATS_ELECTRO_INCOME)
        debtors: list[tuple[str, str]] = []
        for plot in self._plot_list():
            rec = by_num.get(plot, {})
            owners_list = rec.get("owners", []) or []
            rows = energy.balances_by_owner(
                plot, as_of, meters, rates, repls, baseline, self._df,
                owners_list, ownership_form=rec.get("ownership_form"),
                plots=plots_recs, pay_index=pay_idx)
            cur_debt = sum(r.debt for r in rows if r.is_current)
            if cur_debt > 0.5:
                cur_name = next((r.name for r in rows if r.is_current), "")
                debtors.append((plot, cur_name))

        if not debtors:
            _AlertDialog.show_alert(self, "Квитанции", "Должников нет — квитанции не нужны.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Папка для квитанций")
        if not folder:
            return
        try:
            from core import receipt
        except ImportError as e:
            _AlertDialog.show_alert(self, "Ошибка", f"Не удалось импортировать модуль квитанций:\n{e}")
            return

        ok = 0
        errors = []
        for plot, cur_name in debtors:
            surname = cur_name.split()[0] if cur_name else ""
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
            _AlertDialog.show_alert(
                self, "Квитанции",
                f"Создано: {ok}\nОшибки ({len(errors)}):\n" + "\n".join(errors[:10])
            )
        else:
            _AlertDialog.show_alert(
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
            ws.title = "Долги по электроэнергии"
            ws.append(headers)
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal="center")
            for row in rows:
                ws.append(row)
            wb.save(path)
            _AlertDialog.show_alert(self, "Экспорт завершён", f"Файл сохранён:\n{path}")
        except Exception as e:
            _AlertDialog.show_alert(self, "Ошибка экспорта", str(e))
