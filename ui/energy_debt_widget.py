from PyQt6.QtCore import Qt, QDate, QModelIndex, QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QDialog,
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSpinBox, QStyleFactory, QVBoxLayout, QWidget,
)

from core import energy
from core.utils import fmt_money
from ui.buttons import PrimaryButton, SecondaryButton
from ui.common import (
    CalendarArrowFlip,
    NoJumpDateEdit,
    SortHeaderView as _SortHeaderView,
    ClipFrame as _ClipFrame,
    FlatTableModel as _FlatTableModel,
    MainTableTreeView,
    TREE_STYLE as _TREE_STYLE,
    SB_W as _SB_W,
    style_date_popup,
)
from ui.dialogs import (
    AlertDialog as _AlertDialog,
    BaseDialog as _FramelessDialog,
    exec_dialog as _exec_dialog,
)
from ui.energy_card import PlotCardDialog
from ui.icons import get_icon as _get_icon, icon_png_path as _icon_png_path
from ui.plots_widget import _FilterTabButton
from ui.rates_widget import RatesWidget
from ui.theme import C, FS


# Стиль поля даты — как «на дату:» на вкладке «Взносы» (ui.vznosy_debt_widget):
# белый фон, синевато-серая рамка, свой шеврон вместо системной стрелки.
_DATE_PILL_BORDER = "#C9D8E2"
_DATE_PILL_HOVER   = "#DCE7EC"


# ============================================================================ #
#  Модель данных                                                               #
# ============================================================================ #

class _EnergyModel(_FlatTableModel):
    """Плоская модель для таблицы долгов по электроэнергии."""

    COLUMNS = [
        "Участок", "Владелец", "Без показ., мес.",
        "Начислено", "Оплачено", "Долг / Аванс",
        "Без оплаты, мес.", "Тип расчёта",
    ]
    # Заголовки «№ уч.» / «Собственник» — как в «Взносах»; внутренний ключ
    # COLUMNS (_text_Участок/_text_Владелец и т.д.) не меняется.
    HEADER_LABELS = {"Участок": "№ уч.", "Владелец": "Собственник"}


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


class _EnergySettingsDialog(_FramelessDialog):
    """Общие настройки электроэнергии на всё СНТ (решение общего собрания,
    не за отдельный участок): автопереход при отсутствии показаний +
    глобальные значения по умолчанию для расчётного метода (норматив,
    окно усреднения). Участок использует своё собственное значение, если
    оно задано в его карточке («Тип расчёта»); иначе — глобальное отсюда,
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

        lay.addLayout(self.make_header("Настройки электроэнергии"))

        # ── Автопереход ──────────────────────────────────────────────
        sect1 = QLabel("Автопереход при отсутствии показаний")
        sect1.setStyleSheet(
            f"font-size:{FS.SMALL}px;font-weight:600;color:{C.BRAND};")
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
        sect2.setStyleSheet(
            f"font-size:{FS.SMALL}px;font-weight:600;color:{C.BRAND};margin-top:6px;")
        lay.addWidget(sect2)

        info2 = QLabel(
            "Используются, только если на конкретном участке (кнопка "
            "«Тип расчёта») своё значение не задано — например, "
            "региональный норматив на освещение можно указать один раз "
            "здесь, а не на каждом участке. Этими же значениями пользуется "
            "и автопереход выше — для оценки «хвоста» участков-счётчиков."
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

        btn_cancel = SecondaryButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_save = PrimaryButton("Сохранить")
        btn_save.clicked.connect(self._on_accept)
        lay.addLayout(self.make_button_row(btn_cancel, btn_save))

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
        self._search_text = ""
        self._filter_mode = "all"
        self._setup_ui()
        self._rebuild()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(8)
        lbl_title = QLabel("Электроэнергия")
        lbl_title.setStyleSheet(
            "font-size:14px; font-weight:700; color:#1F2937; background:transparent;")
        top.addWidget(lbl_title)
        top.addStretch()

        top.addWidget(QLabel("на дату:", objectName="filterLabel"))
        # NoJumpDateEdit + ручной QSS — тот же стиль, что и «на дату:» на
        # вкладке «Взносы» (ui.vznosy_debt_widget), вместо блёклого общего
        # QDateEdit#datePicker: белый фон, синевато-серая рамка, свой шеврон.
        self.date_as_of = NoJumpDateEdit(calendarPopup=True, displayFormat="dd.MM.yyyy")
        style_date_popup(self.date_as_of)
        self.date_as_of.setFixedWidth(110)
        _arr_dn = _icon_png_path("expand_more", 12, color="#6B7280")
        _arr_up = _icon_png_path("expand_less", 12, color="#6B7280")
        self.date_as_of.setStyleSheet(
            "QDateEdit{background:#FFFFFF;border:1px solid " + _DATE_PILL_BORDER + ";"
            "border-radius:6px;padding:2px 4px 2px 6px;font-size:12px;color:#1F2937;}"
            "QDateEdit::drop-down{subcontrol-origin:padding;subcontrol-position:right;"
            "width:18px;border:none;border-left:1px solid " + _DATE_PILL_BORDER + ";"
            "background:transparent;border-top-right-radius:6px;border-bottom-right-radius:6px;}"
            "QDateEdit::drop-down:hover{background:" + _DATE_PILL_HOVER + ";}"
            f"QDateEdit::down-arrow{{image:url({_arr_dn});width:12px;height:12px;}}"
            f'QDateEdit[calOpen="true"]::down-arrow{{image:url({_arr_up});}}')
        CalendarArrowFlip(self.date_as_of)
        self.date_as_of.setDate(QDate.currentDate())
        self.date_as_of.setMaximumDate(QDate.currentDate())
        self.date_as_of.dateChanged.connect(self._rebuild)
        top.addWidget(self.date_as_of)

        btn_rates = SecondaryButton("Нормативы", icon="ruler")
        btn_rates.clicked.connect(self._open_rates_dialog)
        top.addWidget(btn_rates)

        btn_settings = SecondaryButton("Настройки", icon="settings")
        btn_settings.setToolTip(
            "Автопереход при отсутствии показаний + глобальные значения "
            "по умолчанию (норматив, окно усреднения) для расчётного метода."
        )
        btn_settings.clicked.connect(self._open_energy_settings_dialog)
        top.addWidget(btn_settings)

        btn_excel = SecondaryButton("Экспорт в Excel", icon="excel")
        btn_excel.clicked.connect(self._export_excel)
        top.addWidget(btn_excel)

        lay.addLayout(top)

        # ── Вкладки-фильтры: Все / Должники / Нет долгов / Без показаний ──────
        # Тот же компонент, что и в «Операциях» (_FilterTabButton) — единый
        # визуальный язык вкладок-фильтров по всему приложению.
        tabs_row = QHBoxLayout()
        tabs_row.setContentsMargins(0, 4, 0, 0)
        tabs_row.setSpacing(20)
        self._filter_tab_group = QButtonGroup(self)
        self._filter_tab_group.setExclusive(True)
        self._filter_tab_buttons: dict[str, _FilterTabButton] = {}
        for mode, label in (("all", "Все"), ("debtors", "Должники"),
                            ("nodebt", "Нет долгов"), ("stale", "Без показаний")):
            btn = _FilterTabButton(label)
            btn.clicked.connect(lambda checked, m=mode: self._on_filter_tab(m))
            self._filter_tab_group.addButton(btn)
            self._filter_tab_buttons[mode] = btn
            tabs_row.addWidget(btn)
        tabs_row.addStretch()
        self._filter_tab_buttons["all"].setChecked(True)

        lay.addLayout(tabs_row)

        # ── Поиск по участку/собственнику — тот же приём, что и в «Взносах» ───
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 4, 0, 4)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Поиск по участку или собственнику")
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(220)
        self._search.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._search.setStyleSheet(
            "QLineEdit{background:transparent;border:none;border-bottom:2px solid #D1D5DB;"
            "border-radius:0;padding:6px 2px;font-size:13px;color:#1F2937;}"
            "QLineEdit:focus{border-bottom:2px solid #07414F;}")
        self._search.textChanged.connect(self._on_search_text)
        search_row.addWidget(self._search, stretch=1)

        # Переключатель «Только активный договор» — справа от поиска, тот же
        # визуальный приём, что и на вкладке «Взносы».
        self.chk_active_only = QPushButton(" Только активный договор")
        self.chk_active_only.setCheckable(True)
        self.chk_active_only.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_active_only.setIconSize(QSize(20, 20))
        self.chk_active_only.setStyleSheet(
            "QPushButton{background:transparent;border:none;border-radius:6px;"
            "padding:4px 8px;font-size:12px;color:#374151;}"
            "QPushButton:hover{background:#F3F4F6;}")
        self.chk_active_only.setToolTip(
            "Если включено — начисления и оплаты считаются только с даты начала "
            "текущей активной группы (договора). Если выключено — вся история "
            "участка, включая прежних собственников."
        )
        self.chk_active_only.setChecked(True)
        self._refresh_active_only_icon()
        self.chk_active_only.toggled.connect(self._on_toggle_active_only)
        search_row.addWidget(self.chk_active_only)

        lay.addLayout(search_row)

        # ── Модель ──────────────────────────────────────────────────────────
        self.model = _EnergyModel(self)

        # ── Шапка (внешняя) ─────────────────────────────────────────────────
        self.hdr_view = _SortHeaderView(sort_left=True)
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
        self.tree = MainTableTreeView(objectName="mainTable")
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
        # Win11: нативный overlay-скроллбар игнорирует ширину/цвет из QSS,
        # пока стиль виджета не Fusion — тот же приём, что и в списке
        # участков (plots_widget.list_view).
        self._tree_sb_style = QStyleFactory.create("Fusion")
        if self._tree_sb_style is not None:
            self.tree.setStyle(self._tree_sb_style)
            self.tree.verticalScrollBar().setStyle(self._tree_sb_style)
        self.tree.doubleClicked.connect(self._open_card)

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

    # -- сортировка -------------------------------------------------------- #

    def _on_sort_changed(self, col, order):
        self.model.sort(col, order)
        self.hdr_view.setSortIndicator(col, order)

    # -- поиск ----------------------------------------------------------------- #

    def _on_search_text(self, text: str):
        self._search_text = text.strip().lower()
        self._rebuild()

    # -- вкладки-фильтры ------------------------------------------------------- #

    def _on_filter_tab(self, mode: str):
        self._filter_mode = mode
        self._rebuild()

    # -- переключатель «Только активный договор» ------------------------------ #

    def _refresh_active_only_icon(self):
        checked = self.chk_active_only.isChecked()
        cp = 0xE9F6 if checked else 0xE9F5  # toggle_on / toggle_off
        self.chk_active_only.setIcon(
            _get_icon(cp, 20, fill=1 if checked else 0,
                      color="#07414F" if checked else "#9CA3AF"))

    def _on_toggle_active_only(self, checked: bool):
        self._refresh_active_only_icon()
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
        btn_stale = self._filter_tab_buttons["stale"]
        btn_stale.set_label(f"Без показаний ≥ {stale_threshold} мес.")
        btn_stale.setToolTip(
            f"Участки со счётчиком, которые не передают показания "
            f"{stale_threshold} мес. подряд и дольше. "
            + ("Начисление для них уже ведётся автоматически по среднему "
               "потреблению (автопереход включён)." if auto_settings.get("enabled")
               else "По порядку действий СНТ таким пора переходить на "
                    "расчётный метод (кнопка «Тип расчёта» на карточке участка).")
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
                charged, paid, debt = gb.charged, gb.paid, gb.debt
            else:
                charged, paid, debt = bal.charged, bal.paid, bal.debt

            owner = ", ".join(owners.get(plot, [])) or "—"

            level = energy.debt_level(debt, monthly_avg=300.0)
            debts_map[plot] = {"debt": debt, "level": level,
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
                "_text_Участок": plot,
                "_sort_Участок": plot_sort,
                "_fg_Участок": "#07414F",
                "_bold_Участок": True,

                "_text_Владелец": owner,
                "_sort_Владелец": owner,
                "_fg_Владелец": "#374151",

                "_text_Без показ., мес.": "—" if mwr is None else str(mwr),
                "_sort_Без показ., мес.": mwr or 0,
                "_fg_Без показ., мес.": "#DC2626" if (mwr or 0) >= stale_threshold else "#374151",

                "_text_Начислено": fmt_money(charged),
                "_sort_Начислено": charged,
                "_fg_Начислено": "#f9a825" if charged else "#9CA3AF",

                "_text_Оплачено": fmt_money(paid),
                "_sort_Оплачено": paid,
                "_fg_Оплачено": "#059669" if paid else "#9CA3AF",

                "_text_Долг / Аванс": fmt_money(debt),
                "_sort_Долг / Аванс": debt,
                "_bg_Долг / Аванс": C.DEBT_BG[level],
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

        # Применяем поиск — один запрос сразу по Участку и Владельцу
        text = self._search_text
        if text:
            rows = [
                r for r in rows
                if text in str(r.get("_text_Участок", "")).lower()
                or text in r.get("_text_Владелец", "").lower()
            ]

        # Вкладка-фильтр
        if self._filter_mode == "debtors":
            rows = [r for r in rows if r.get("_sort_Долг / Аванс", 0.0) > 0.5]
        elif self._filter_mode == "nodebt":
            rows = [r for r in rows if r.get("_sort_Долг / Аванс", 0.0) <= 0.5]
        elif self._filter_mode == "stale":
            rows = [r for r in rows if r.get("_sort_Без показ., мес.", 0) >= stale_threshold]

        self.model.load(rows)

        # Ширины колонок
        widths = [85, 240, 110, 110, 110, 120, 110, 120]
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

    def _open_card(self, index: QModelIndex):
        if not index.isValid():
            return
        node = index.internalPointer()
        if node is None:
            return
        plot = str(node.get("_text_Участок", "")).strip()
        if not plot:
            return
        as_of = self.date_as_of.date().toPyDate()
        dlg = PlotCardDialog(plot, self._df, self, as_of=as_of)
        _exec_dialog(dlg, self)
        self._rebuild()

    def _export_excel(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт таблицы", "электроэнергия_долги.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        if not path.endswith(".xlsx"):
            path += ".xlsx"

        headers = [
            self.model.headerData(c, Qt.Orientation.Horizontal)
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
