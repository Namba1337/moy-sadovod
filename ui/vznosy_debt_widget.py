from datetime import datetime

from PyQt6.QtCore import Qt, QDate, QModelIndex, QPoint, QSize, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDateEdit,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QPushButton, QStyleFactory, QVBoxLayout, QWidget, QWidgetAction,
)

from core import energy
from core.utils import fmt_money
from ui.buttons import SecondaryButton
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
from ui.icons import get_icon as _get_icon, icon_png_path as _icon_png_path
from ui.theme import C
from ui.dialogs import (
    BaseDialog as _FramelessDialog,
    exec_dialog as _exec_dialog,
)
from ui.plots_widget import _is_visible, _is_owner, _owner_name
from ui.vznosy_card import VznosyCardDialog
from ui.rates_widget import VznosyRatesWidget


# ============================================================================ #
#  Модель данных                                                               #
# ============================================================================ #

class _VznosyModel(_FlatTableModel):
    """Плоская модель для таблицы долгов по ЧВ."""

    COLUMNS = [
        "Участок", "Собственник", "Площадь, м²",
        "Начислено", "Оплачено", "Долг / Аванс",
    ]
    # Заголовок «№ уч.» — как в «Операциях»; внутренний ключ "Участок"
    # (_text_Участок/_sort_Участок и т.д.) не меняется.
    HEADER_LABELS = {"Участок": "№ уч."}


# ============================================================================ #
#  Кнопка мультивыбора периодов                                                #
# ============================================================================ #

# Стиль поля даты/фильтра-кнопки — как у «Дата начала:» в карточке группы
# участка (см. ctx.since_date_edit в ui.plots_widget), а не общий блёклый
# QDateEdit#datePicker/QComboBox#filterCombo: белый фон, синевато-серая
# рамка, скруглённые углы, свой шеврон вместо системной стрелки.
_DATE_PILL_BORDER = "#C9D8E2"
_DATE_PILL_HOVER   = "#DCE7EC"


class _PeriodFilterButton(QWidget):
    """Кнопка с выпадающим меню чекбоксов для выбора периодов ЧВ — визуально
    в едином стиле с полем «на дату» (см. _DATE_PILL_BORDER), а не старая
    цветная пилюля.

    Идентификатор периода — ``date_from`` в ISO-формате (как в
    :func:`core.vznosy.balance_for_periods`). Выбор по умолчанию — «все».
    При обновлении списка периодов (:meth:`set_periods`) уже снятые/отмеченные
    пользователем периоды сохраняют своё состояние; новые периоды добавляются
    выбранными."""

    selectionChanged = pyqtSignal(object)   # set[str] выбранных ключей

    _BTN_SS = (
        "QPushButton{background:#FFFFFF;border:1px solid " + _DATE_PILL_BORDER + ";"
        "border-radius:6px;padding:6px 10px;font-size:12px;color:#1F2937;text-align:left;}"
        "QPushButton:hover{background:" + _DATE_PILL_HOVER + ";}"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._periods: list[tuple[str, str]] = []   # (key, label)
        self._selected: set[str] = set()

        lyt = QHBoxLayout(self)
        lyt.setContentsMargins(0, 0, 0, 0)
        self._btn = QPushButton()
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setStyleSheet(self._BTN_SS)
        self._btn.clicked.connect(self._open_menu)
        lyt.addWidget(self._btn)
        self._update_label()

    def set_periods(self, periods: list[tuple[str, str]]):
        new_keys = {k for k, _ in periods}
        old_keys = {k for k, _ in self._periods}
        self._periods = list(periods)
        if new_keys != old_keys:
            self._selected = (self._selected & new_keys) | (new_keys - old_keys)
        self._update_label()

    def get_selected(self) -> set[str]:
        return set(self._selected)

    def is_all_selected(self) -> bool:
        return len(self._selected) == len(self._periods)

    def _update_label(self):
        n = len(self._periods)
        s = len(self._selected)
        if n == 0 or s == n:
            text = "Все периоды"
        elif s == 0:
            text = "Периоды не выбраны"
        elif s == 1:
            text = dict(self._periods).get(next(iter(self._selected)), "1 период")
        else:
            text = f"Периодов: {s} из {n}"
        self._btn.setText(f"{text}  ▾")

    def _open_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #FFFFFF; border: 1px solid #D1D5DB;
                border-radius: 8px; padding: 4px;
            }
            QMenu::item { padding: 0px; margin: 1px 0px; }
        """)

        if not self._periods:
            act = QWidgetAction(menu)
            lb = QLabel("  Нет периодов — задайте их в «Периоды»")
            lb.setStyleSheet("color:#9CA3AF; font-size:12px; padding:6px 14px;")
            act.setDefaultWidget(lb)
            menu.addAction(act)
            menu.exec(self._btn.mapToGlobal(self._btn.rect().bottomLeft() + QPoint(0, 2)))
            return

        for label_text, state in (("✓  Выбрать все", True), ("✗  Снять все", False)):
            wa = QWidgetAction(menu)
            btn = QPushButton(label_text)
            btn.setStyleSheet(
                "border:none; text-align:left; padding:5px 14px; "
                "color:#374151; font-size:12px; background:transparent;"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            s = state
            btn.clicked.connect(lambda _, st=s: (self._set_all(st), menu.close()))
            wa.setDefaultWidget(btn)
            menu.addAction(wa)

        menu.addSeparator()

        for key, label in reversed(self._periods):   # новые сверху
            wa = QWidgetAction(menu)
            cb = QCheckBox(f"  {label}")
            cb.setChecked(key in self._selected)
            cb.setStyleSheet(
                "QCheckBox { padding: 5px 14px; color: #374151; font-size: 12px;"
                "  spacing: 8px; background: transparent; }"
                "QCheckBox:hover { background: #F3F4F6; border-radius: 4px; }"
                "QCheckBox::indicator { width: 14px; height: 14px;"
                "  border: 1px solid #D1D5DB; border-radius: 3px; background: #FFFFFF; }"
                "QCheckBox::indicator:checked {"
                "  background: #07414F; border-color: #07414F; }"
            )
            cb.toggled.connect(lambda checked, k=key: self._toggle(k, checked))
            wa.setDefaultWidget(cb)
            menu.addAction(wa)

        menu.exec(self._btn.mapToGlobal(self._btn.rect().bottomLeft() + QPoint(0, 2)))

    def _toggle(self, key: str, checked: bool):
        if checked:
            self._selected.add(key)
        else:
            self._selected.discard(key)
        self._update_label()
        self.selectionChanged.emit(self._selected)

    def _set_all(self, state: bool):
        self._selected = ({k for k, _ in self._periods} if state else set())
        self._update_label()
        self.selectionChanged.emit(self._selected)


# ============================================================================ #
#  Обёртка диалога «Периоды членских взносов»                                  #
# ============================================================================ #

class _RatesDialog(_FramelessDialog):
    """Кастомная (без нативного чрома) карточка для VznosyRatesWidget.
    Стили страничных objectName-ов (pageTitle/filterFrame/…) и таблиц
    приходят из общего блока BaseDialog (ui.theme.dialog_qss)."""

    def __init__(self, rates_widget: VznosyRatesWidget, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Периоды членских взносов")
        self.setModal(True)
        self.setMinimumSize(900, 560)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # «✕» встраивается прямо в title_row виджета (тот же ряд, что и
        # название) — так название стоит выше, без отдельного пустого
        # ряда-шапки над ним. detach_close_button() убирает кнопку обратно
        # при закрытии — rates_widget переиспользуется между открытиями.
        self._rates_widget = rates_widget
        self._btn_close = QPushButton("✕", objectName="btnPanelClose")
        self._btn_close.setFixedSize(24, 24)
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_close.clicked.connect(self.reject)
        rates_widget.title_row.addWidget(self._btn_close)

        lay.addWidget(rates_widget, stretch=1)

    def detach_close_button(self):
        self._rates_widget.title_row.removeWidget(self._btn_close)
        self._btn_close.setParent(None)


# ============================================================================ #
#  Виджет вкладки «Членские взносы»                                             #
# ============================================================================ #

class VznosyDebtWidget(QWidget):
    """Вкладка контроля долгов по членским взносам."""

    def __init__(self):
        super().__init__()
        self.setAutoFillBackground(False)
        self._df = None
        self._last_debts: dict = {}
        self._col_syncing = False
        self._search_text = ""
        self._setup_ui()
        self._rebuild()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)

        # ── Заголовок вкладки + фильтры — один ряд ────────────────────────────
        top = QHBoxLayout()
        top.setSpacing(8)
        lbl_title = QLabel("Взносы")
        lbl_title.setStyleSheet(
            "font-size:14px; font-weight:700; color:#1F2937; background:transparent;")
        top.addWidget(lbl_title)
        top.addStretch()

        top.addWidget(QLabel("на дату:", objectName="filterLabel"))
        # NoJumpDateEdit + ручной QSS — тот же стиль, что и «Дата начала:» в
        # карточке группы участка (ui.plots_widget), вместо блёклого общего
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

        # Фильтр периодов — множественный выбор (чекбоксы), кнопка в том же
        # визуальном стиле, что и поле даты выше.
        self.period_filter = _PeriodFilterButton()
        self.period_filter.selectionChanged.connect(lambda _: self._rebuild())
        top.addWidget(self.period_filter)

        # Кнопки «Пересчитать» больше нет: любое изменение фильтров/даты и так
        # вызывает _rebuild (U2 из аудита UI).
        btn_rates = SecondaryButton("Периоды", icon="calendar")
        btn_rates.clicked.connect(self._open_rates_dialog)
        top.addWidget(btn_rates)

        lay.addLayout(top)

        # ── Поиск по участку/собственнику — тот же приём, что и в «Операциях» ──
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
        # визуальный приём, что и «Показывать переплату» на вкладке «Участки».
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
        self.model = _VznosyModel(self)

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

    def _on_search_text(self, text: str):
        self._search_text = text.strip().lower()
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
        dlg = _RatesDialog(self.rates, self)
        _exec_dialog(dlg, self)
        dlg.detach_close_button()
        dlg.layout().removeWidget(self.rates)
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
        from core import ownership as own
        as_of = self.date_as_of.date().toPyDate()
        rates = vznosy.load_rates()
        adj = vznosy.load_adjustments()
        areas = vznosy.plot_area_map()
        owners = energy.owners_map()
        plot_recs = energy.plots_by_num()
        plots = self._plot_list()
        active_only = self.chk_active_only.isChecked()

        periods = vznosy.build_periods(rates)
        period_opts = []
        for r in periods:
            pf, pt = r.get("date_from", ""), r.get("date_to", "")
            try:
                pf_label = datetime.strptime(pf, "%Y-%m-%d").strftime("%d.%m.%Y")
            except Exception:
                pf_label = pf
            if pt:
                try:
                    pt_label = datetime.strptime(pt, "%Y-%m-%d").strftime("%d.%m.%Y")
                except Exception:
                    pt_label = pt
            else:
                pt_label = "открытый"
            period_opts.append((pf, f"{pf_label} — {pt_label}"))
        self.period_filter.set_periods(period_opts)
        period_filter_active = not self.period_filter.is_all_selected()
        selected_period_keys = self.period_filter.get_selected() if period_filter_active else None

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

        # Индекс платежей — один проход по выписке вместо скана на каждый участок
        pay_idx = vznosy.payments_index(self._df)

        for plot in plots:
            area = areas.get(plot)
            bal = vznosy.balance_for_plot(plot, area, as_of, rates, adj, self._df,
                                          pay_index=pay_idx)
            if active_only or period_filter_active:
                since = (own.group_since(own.active_group(plot_recs.get(plot, {})) or {})
                         if active_only else None)
                gb = vznosy.balance_for_periods(
                    plot, area, as_of, rates, adj, self._df,
                    since=since, period_keys=selected_period_keys,
                    pay_index=pay_idx)
                charged, paid, debt = gb.charged, gb.paid, gb.debt
            else:
                charged, paid, debt = bal.charged, bal.paid, bal.debt

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

            level = vznosy.debt_level(debt, annual_avg=annual_avg)

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
            }
            rows.append(row)

            total_charged += charged
            total_paid += paid
            total_debt += debt
            if debt > 0.5:
                debt_count += 1
            debts_map[plot] = {"debt": debt, "charged": charged,
                               "paid": paid, "owner": owner_text}

        # Применяем поиск — один запрос сразу по Участку и Собственнику
        text = self._search_text
        if text:
            rows = [
                r for r in rows
                if text in r.get("_text_Участок", "").lower()
                or text in r.get("_text_Собственник", "").lower()
            ]

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
        _exec_dialog(dlg, self)
        self._rebuild()
