"""Виджеты управления тарифами (электроэнергия и членские взносы)."""
from __future__ import annotations

import json
import os
from datetime import datetime

from PyQt6.QtCore import Qt, QDate, QEvent, QPoint
from PyQt6.QtGui import QAction, QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QDateEdit, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMenu, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.utils import DATA_DIR
from ui.buttons import GhostButton, PrimaryButton
from ui.common import CalendarArrowFlip, ClipFrame, NoJumpDateEdit, style_date_popup
from ui.dialogs import ConfirmDialog as _ConfirmDialog
from ui.icons import icon_png_path
from ui.theme import C, RAD, menu_qss


def _mark_invalid_input(inp: QLineEdit) -> None:
    """Подсвечивает поле как ошибочное. Состояние полностью заменяется
    (а не дописывается в конец styleSheet) и сбрасывается при первом же
    изменении текста — исправленное значение не остаётся «красным»."""
    inp.setStyleSheet(f"border: 1px solid {C.DANGER};")

    def _reset(_=None, i=inp):
        i.setStyleSheet("")
        try:
            i.textChanged.disconnect(_reset)
        except TypeError:
            pass

    inp.textChanged.connect(_reset)


class RatesWidget(QWidget):
    """Управление тарифами на электроэнергию."""

    DATA_FILE = os.path.join(DATA_DIR, "snt_energy_rates.json")

    def __init__(self):
        super().__init__()
        self._rates: list = self._load()
        self._setup_ui()
        self._rebuild_table()

    def _load(self) -> list:
        try:
            if os.path.exists(self.DATA_FILE):
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return []

    def _save(self):
        try:
            with open(self.DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self._rates, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def reload(self):
        self._rates = self._load()
        self._rebuild_table()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        top = QHBoxLayout()
        title = QLabel("Нормативы — тарифы на электроэнергию", objectName="pageTitle")
        top.addWidget(title)
        top.addStretch()
        btn_add = PrimaryButton("Добавить тариф", icon="add")
        btn_add.clicked.connect(self._add_rate)
        top.addWidget(btn_add)
        lay.addLayout(top)

        hint = QLabel(
            "Тариф действует с указанной даты до следующего изменения.  "
            "ПКМ по строке — удалить.  Двойной клик по значению — редактировать."
        )
        hint.setStyleSheet("color: #9CA3AF; background: transparent; font-size: 11px;")
        lay.addWidget(hint)

        self.form_frame = QFrame()
        self.form_frame.setObjectName("filterFrame")
        self.form_frame.setVisible(False)
        form_lay = QHBoxLayout(self.form_frame)
        form_lay.setContentsMargins(16, 12, 16, 12)
        form_lay.setSpacing(10)

        form_lay.addWidget(QLabel("Действует с:", objectName="filterLabel"))
        self.inp_date = QDateEdit()
        self.inp_date.setObjectName("datePicker")
        self.inp_date.setCalendarPopup(True)
        style_date_popup(self.inp_date)
        self.inp_date.setDate(QDate.currentDate())
        self.inp_date.setDisplayFormat("dd.MM.yyyy")
        self.inp_date.setFixedWidth(115)
        form_lay.addWidget(self.inp_date)

        form_lay.addWidget(QLabel("Тариф (₽/кВт·ч):", objectName="filterLabel"))
        self.inp_rate = QLineEdit()
        self.inp_rate.setObjectName("searchInput")
        self.inp_rate.setPlaceholderText("5.50")
        self.inp_rate.setFixedWidth(100)
        form_lay.addWidget(self.inp_rate)

        btn_ok = PrimaryButton("Сохранить")
        btn_ok.clicked.connect(self._confirm_add)
        form_lay.addWidget(btn_ok, stretch=1)

        btn_cancel = GhostButton(icon="close", tooltip="Скрыть форму")
        btn_cancel.clicked.connect(lambda: self.form_frame.setVisible(False))
        form_lay.addWidget(btn_cancel)

        lay.addWidget(self.form_frame)

        self.table = QTableWidget()
        self.table.setObjectName("summaryTable")
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Действует с", "Тариф (₽/кВт·ч)"])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 160)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.itemChanged.connect(self._on_cell_edited)
        lay.addWidget(self.table)

        self.status_lbl = QLabel("", objectName="statusLabel")
        lay.addWidget(self.status_lbl)

    def _rebuild_table(self):
        self.table.blockSignals(True)
        self.table.clearContents()

        rates_sorted = sorted(self._rates, key=lambda r: r.get("date", ""), reverse=True)
        self.table.setRowCount(len(rates_sorted))

        for r_idx, entry in enumerate(rates_sorted):
            is_current = (r_idx == 0)
            bg = "#E6F4EA" if is_current else "#F9FAFB"
            fg = "#059669" if is_current else "#374151"

            it_date = QTableWidgetItem(entry.get("date", ""))
            it_date.setBackground(QColor(bg))
            it_date.setForeground(QColor(fg))
            it_date.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            it_date.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(r_idx, 0, it_date)

            it_rate = QTableWidgetItem(str(entry.get("rate", "")))
            it_rate.setBackground(QColor(bg))
            it_rate.setForeground(QColor(fg))
            it_rate.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(r_idx, 1, it_rate)

            self.table.setRowHeight(r_idx, 34)

        self.table.blockSignals(False)

        if rates_sorted:
            current = rates_sorted[0]
            self.status_lbl.setText(
                f"Текущий тариф: {current.get('rate', '?')} ₽/кВт·ч "
                f"(с {current.get('date', '?')})  ·  всего записей: {len(rates_sorted)}"
            )
        else:
            self.status_lbl.setText("Нет тарифов — добавьте первый")

    def _add_rate(self):
        self.inp_rate.clear()
        self.form_frame.setVisible(True)
        self.inp_rate.setFocus()

    def _confirm_add(self):
        raw = self.inp_rate.text().strip().replace(",", ".")
        if not raw:
            self.inp_rate.setFocus()
            return
        try:
            v = float(raw)
            if v <= 0:
                raise ValueError
        except ValueError:
            _mark_invalid_input(self.inp_rate)
            return

        entry = {
            "date": self.inp_date.date().toString("yyyy-MM-dd"),
            "rate": raw,
        }
        self._rates.append(entry)
        self._save()
        self.form_frame.setVisible(False)
        self._rebuild_table()

    def _on_cell_edited(self, item: QTableWidgetItem):
        if self.table.signalsBlocked():
            return
        rates_sorted = sorted(self._rates, key=lambda r: r.get("date", ""), reverse=True)
        r_idx = item.row()
        if r_idx >= len(rates_sorted) or item.column() != 1:
            return
        val = item.text().strip().replace(",", ".")
        entry = rates_sorted[r_idx]
        orig_idx = next((i for i, e in enumerate(self._rates) if e is entry), None)
        if orig_idx is None:
            return
        try:
            v = float(val)
            if v <= 0:
                raise ValueError
        except ValueError:
            self._rebuild_table()
            return
        self._rates[orig_idx]["rate"] = val
        self._save()
        self._rebuild_table()

    def _context_menu(self, pos: QPoint):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu(self)
        menu.setStyleSheet(menu_qss(danger=True))
        act_del = QAction("Удалить запись", self)
        act_del.triggered.connect(lambda: self._delete_rate(row))
        menu.addAction(act_del)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _delete_rate(self, row: int):
        rates_sorted = sorted(self._rates, key=lambda r: r.get("date", ""), reverse=True)
        if row >= len(rates_sorted):
            return
        entry = rates_sorted[row]
        confirmed = _ConfirmDialog.confirm(
            self, "Удаление тарифа",
            f"Удалить запись от {entry.get('date', '')} ({entry.get('rate', '')} ₽/кВт·ч)?",
        )
        if confirmed:
            self._rates = [e for e in self._rates if e is not entry]
            self._save()
            self._rebuild_table()


# ============================================================================ #
#  Периоды членских взносов                                                    #
# ============================================================================ #

# QTableWidget (не MainTableTreeView — маленькая таблица отдельного диалога
# настроек, полноценная модель+делегаты того не стоят), перекрашенный под
# цвета главных таблиц: шапка в тон выделения строк (BRAND_TINT/BRAND — те
# же #C9D8E2/#07414F), чередование строк — тот же BG_ALT_ROW, что и в
# «Операциях»/«Взносах». Три лёгких делегата ниже добавлены точечно для
# конкретных мест, где голый QTableWidgetItem выглядел или редактировался
# криво: чекбокс (свой глиф), дата (календарь-кнопка), текстовый ввод
# (стилизованная рамка) — это заметно меньше кода, чем полноценные
# модель+делегаты под MainTableTreeView.
_PERIODS_TABLE_QSS = """
    QTableWidget#periodsTable {
        background: #FFFFFF; border: none;
        color: #1F2937; font-size: 13px;
        selection-background-color: #C9D8E2; selection-color: #07414F;
        alternate-background-color: #F0F4F8;
        gridline-color: transparent;
        outline: 0;
    }
    QTableWidget#periodsTable::item {
        padding: 6px 10px; border-bottom: 1px solid #E5E7EB;
    }
    QTableWidget#periodsTable::item:selected {
        background: #C9D8E2; color: #07414F;
    }
    QTableWidget#periodsTable QHeaderView::section {
        background: #C9D8E2; color: #07414F; border: none;
        border-right: 1px solid #B5C8D5;
        padding: 8px 10px; font-size: 12px; font-weight: 600;
    }
"""


class _CheckIconDelegate(QStyledItemDelegate):
    """Свой глиф чекбокса (Material Symbols check_box/check_box_outline_blank) —
    тот же приём и цвета, что и у чекбоксов строк в «Операциях», вместо
    нативного Qt-квадрата (не совпадал по стилю с основной таблицей).

    Клик обрабатывается вручную (``editorEvent``), а не нативным
    ItemIsUserCheckable-хиттестом — паинт полностью свой, поэтому и клик
    полностью свой, без риска рассинхрона между тем, что нарисовано, и тем,
    где Qt ждёт клика."""

    _IC_ON  = chr(0xE834)   # check_box
    _IC_OFF = chr(0xE835)   # check_box_outline_blank
    _IC_COLOR_ON  = QColor("#07414F")
    _IC_COLOR_OFF = QColor("#C3CAD3")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fill_tag = QFont.Tag.fromString("FILL")

    def paint(self, painter, option, index):
        # Фон/выделение строки — как обычно, но без нативного чекбокса-
        # квадрата (рисуем свой глиф поверх).
        opt = QStyleOptionViewItem(option)
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        painter.save()
        f = QFont("Material Symbols Rounded")
        f.setPixelSize(18)
        f.setVariableAxis(self._fill_tag, 1.0 if checked else 0.0)
        painter.setFont(f)
        painter.setPen(self._IC_COLOR_ON if checked else self._IC_COLOR_OFF)
        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter,
                         self._IC_ON if checked else self._IC_OFF)
        painter.restore()

    def createEditor(self, parent, option, index):
        return None

    def editorEvent(self, event, model, option, index):
        if (event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton):
            cur = index.data(Qt.ItemDataRole.CheckStateRole)
            new = (Qt.CheckState.Unchecked if cur == Qt.CheckState.Checked
                   else Qt.CheckState.Checked)
            model.setData(index, new, Qt.ItemDataRole.CheckStateRole)
            return True
        return False


class _PeriodEditDelegate(QStyledItemDelegate):
    """Инлайн-редактор ячейки — тот же вид, что и у текстовых ячеек
    «Операций» (белая рамка с бирюзовой обводкой, привязка к границам
    ячейки), вместо голого системного QLineEdit."""

    _EDITOR_SS = (
        "QLineEdit{background:#FFFFFF;border:1px solid #07414F;border-radius:4px;"
        "padding:0 6px;font-size:13px;color:#1F2937;"
        "selection-background-color:#C9D8E2;selection-color:#07414F;}"
    )

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if editor is not None:
            editor.setStyleSheet(self._EDITOR_SS)
            editor.setMaximumWidth(option.rect.width())
        return editor

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


_DATE_EDITOR_SS = (
    "QDateEdit{{background:#FFFFFF;border:1px solid #07414F;border-radius:4px;"
    "padding:0 6px;font-size:13px;color:#1F2937;}}"
    "QDateEdit::drop-down{{subcontrol-origin:padding;subcontrol-position:right;"
    "width:18px;border:none;border-left:1px solid #D5DCE4;background:transparent;"
    "border-top-right-radius:4px;border-bottom-right-radius:4px;}}"
    "QDateEdit::drop-down:hover{{background:#F3F4F6;}}"
    "QDateEdit::down-arrow{{image:url({arr_dn});width:12px;height:12px;}}"
    'QDateEdit[calOpen="true"]::down-arrow{{image:url({arr_up});}}'
)


class _PeriodDateDelegate(QStyledItemDelegate):
    """Инлайн дата-пикер для «Начало» — календарь-кнопка, тот же стиль, что
    и у делегата «Дата» в «Операциях» (``_DateCellDelegate``), вместо
    текстового ввода."""

    def createEditor(self, parent, option, index):
        editor = NoJumpDateEdit(parent, calendarPopup=True)
        editor.setDisplayFormat("dd.MM.yyyy")
        style_date_popup(editor)
        arr_dn = icon_png_path("expand_more", 12, color="#6B7280")
        arr_up = icon_png_path("expand_less", 12, color="#6B7280")
        editor.setStyleSheet(_DATE_EDITOR_SS.format(arr_dn=arr_dn, arr_up=arr_up))
        CalendarArrowFlip(editor)
        editor.setMaximumWidth(option.rect.width())
        return editor

    def setEditorData(self, editor, index):
        text = str(index.data(Qt.ItemDataRole.EditRole) or "").strip()
        try:
            d = datetime.strptime(text, "%d.%m.%Y")
            editor.setDate(QDate(d.year, d.month, d.day))
        except Exception:
            editor.setDate(QDate.currentDate())

    def setModelData(self, editor, model, index):
        d = editor.date()
        model.setData(index, f"{d.day():02d}.{d.month():02d}.{d.year()}",
                      Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class VznosyRatesWidget(QWidget):
    """Управление периодами членских взносов.

    Таблица редактируется полностью инлайн (как «Операции»): новая строка
    вставляется прямо в таблицу и сразу открывается на редактирование суммы,
    без отдельной формы-попапа. Выбор строк — чекбоксы в первой колонке
    (свой Material-глиф, см. _CheckIconDelegate), удаление — отдельной
    кнопкой в шапке таблицы. «Начало»/«Конец» — дата-пикеры; открытый период
    (без даты окончания) переключается через ПКМ по «Конец»."""

    DATA_FILE = os.path.join(DATA_DIR, "snt_vznosy_rates.json")

    C_CHECK, C_FROM, C_TO, C_AMOUNT, C_SQM, C_NOTE = range(6)

    def __init__(self):
        super().__init__()
        self._rates: list = self._load()
        self._setup_ui()
        self._rebuild_table()

    def _load(self) -> list:
        try:
            if os.path.exists(self.DATA_FILE):
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    rates = json.load(f)
                for entry in rates:
                    self._sanitize_entry(entry)
                return rates
        except Exception:
            pass
        return []

    @staticmethod
    def _sanitize_entry(entry: dict) -> None:
        """Гарантирует взаимоисключение amount/rate_sqm по флагу per_sqm —
        страховка на случай правки JSON руками или старых данных. Расчёт в
        core/vznosy.py и так строго ветвится по per_sqm, но хранить в JSON
        значение неактивного поля — грязно и может ввести в заблуждение."""
        if entry.get("per_sqm"):
            entry["amount"] = ""
        else:
            entry["rate_sqm"] = ""

    def _save(self):
        try:
            with open(self.DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self._rates, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def reload(self):
        self._rates = self._load()
        self._rebuild_table()

    @staticmethod
    def _fmt_date(iso: str) -> str:
        try:
            return datetime.strptime(iso, "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            return iso or "—"

    @staticmethod
    def _parse_date(text: str) -> str | None:
        try:
            return datetime.strptime(text.strip(), "%d.%m.%Y").strftime("%Y-%m-%d")
        except Exception:
            return None

    def _sorted_periods(self) -> list:
        """Периоды, отсортированные по date_from по убыванию (новые сверху)."""
        def _key(r):
            return r.get("date_from", r.get("date", ""))
        return sorted(self._rates, key=_key, reverse=True)

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(12)

        # ── Заголовок — «✕» вставляет сюда же _RatesDialog (title_row) ──────
        self.title_row = QHBoxLayout()
        title = QLabel("Периоды членских взносов", objectName="pageTitle")
        self.title_row.addWidget(title)
        self.title_row.addStretch()
        lay.addLayout(self.title_row)

        # ── Подсказка + добавление — на одном уровне, над таблицей ─────────
        # Кнопка удаления теперь в шапке таблицы (колонка чекбоксов), не
        # здесь — см. _position_header_delete_btn().
        hint_row = QHBoxLayout()
        hint_row.setSpacing(8)
        hint = QLabel(
            "Каждый период — отдельная строка тарифа.  "
            "Двойной клик по ячейке — редактировать.  "
            "ПКМ по «Конец» — сделать открытым.  "
            "Чекбоксы слева — для удаления."
        )
        hint.setStyleSheet("color: #9CA3AF; background: transparent; font-size: 11px;")
        hint_row.addWidget(hint, stretch=1)

        btn_add = PrimaryButton("Добавить период", icon="add")
        btn_add.clicked.connect(self._add_rate)
        hint_row.addWidget(btn_add)

        lay.addLayout(hint_row)

        # ── Таблица периодов ──────────────────────────────────────
        self.table = QTableWidget()
        self.table.setObjectName("periodsTable")
        self.table.setStyleSheet(_PERIODS_TABLE_QSS)
        self.table.setAlternatingRowColors(True)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["", "Начало", "Конец", "Сумма (₽)", "Цена за м² (₽)", "Примечание"]
        )
        hdr = self.table.horizontalHeader()
        for c, (mode, w) in enumerate([
            (QHeaderView.ResizeMode.Fixed, 34),
            (QHeaderView.ResizeMode.Fixed, 130),
            (QHeaderView.ResizeMode.Fixed, 150),
            (QHeaderView.ResizeMode.Fixed, 130),
            (QHeaderView.ResizeMode.Fixed, 130),
            (QHeaderView.ResizeMode.Stretch, 0),
        ]):
            hdr.setSectionResizeMode(c, mode)
            if w:
                self.table.setColumnWidth(c, w)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_end_context_menu)

        # Чекбоксы — свой Material-глиф вместо нативного квадрата (тот же
        # вид, что и в «Операциях»); «Начало»/«Конец» — один и тот же
        # дата-пикер (открытый период у «Конец» ставится/снимается через
        # ПКМ — см. _on_end_context_menu, а не отдельным виджетом в
        # редакторе: композитный дата+чекбокс редактор был нечитаем/криво
        # сжат в узкой ячейке); остальные текстовые колонки — стилизованный
        # инлайн-редактор вместо голого системного QLineEdit.
        self._check_delegate = _CheckIconDelegate(self.table)
        self.table.setItemDelegateForColumn(self.C_CHECK, self._check_delegate)
        self._date_delegate = _PeriodDateDelegate(self.table)
        self.table.setItemDelegateForColumn(self.C_FROM, self._date_delegate)
        self.table.setItemDelegateForColumn(self.C_TO, self._date_delegate)
        self._edit_delegate = _PeriodEditDelegate(self.table)
        for c in (self.C_AMOUNT, self.C_SQM, self.C_NOTE):
            self.table.setItemDelegateForColumn(c, self._edit_delegate)

        # Кнопка удаления — прямо в шапке таблицы, над колонкой чекбоксов
        # (наложенный виджет-ребёнок хедера, тот же приём позиционирования
        # поверх геометрии заголовка, что и у _hdr_stub в MainTableTreeView).
        self.btn_delete_selected = GhostButton(
            hdr, icon="delete", tooltip="Удалить выбранные периоды",
            danger=True, size=24, icon_size=16)
        self.btn_delete_selected.setEnabled(False)
        self.btn_delete_selected.clicked.connect(self._delete_selected)
        hdr.sectionResized.connect(lambda *_: self._position_header_delete_btn())
        hdr.geometriesChanged.connect(self._position_header_delete_btn)
        self._position_header_delete_btn()

        # Скруглённые углы диалога не клипают квадратную заливку строк
        # QTableWidget (виджет со скроллом рисует свой viewport поверх
        # QSS-рамки) — заворачиваем в ClipFrame, тот же приём и тот же цвет
        # рамки, что и у главных таблиц вкладок (см. table_outer в
        # vznosy_debt_widget.py) — сама таблица без рамки (border:none в
        # _PERIODS_TABLE_QSS), иначе белые строки на белом фоне диалога
        # были не видны без обводки.
        table_frame = ClipFrame(QColor("#D5DCE4"), RAD.FRAME)
        frame_lay = QVBoxLayout(table_frame)
        frame_lay.setContentsMargins(0, 0, 0, 0)
        frame_lay.addWidget(self.table)
        table_frame.finish_setup()
        lay.addWidget(table_frame, stretch=1)

        self.status_lbl = QLabel("", objectName="statusLabel")
        lay.addWidget(self.status_lbl)

    def _position_header_delete_btn(self):
        hdr = self.table.horizontalHeader()
        x = hdr.sectionViewportPosition(self.C_CHECK)
        w = hdr.sectionSize(self.C_CHECK)
        size = self.btn_delete_selected.height()
        bx = x + (w - size) // 2
        by = (hdr.height() - size) // 2
        self.btn_delete_selected.move(bx, by)
        self.btn_delete_selected.raise_()

    def _rebuild_table(self):
        self.table.blockSignals(True)
        self.table.clearContents()

        periods = self._sorted_periods()
        self.table.setRowCount(len(periods))

        for r_idx, entry in enumerate(periods):
            date_from = entry.get("date_from", entry.get("date", ""))
            date_to = entry.get("date_to", "")
            is_current = (r_idx == 0)
            is_per_sqm = bool(entry.get("per_sqm"))
            fg_date = "#059669" if is_current else "#374151"
            fg_value = "#07414F" if is_current else "#374151"

            amount_text = "—" if is_per_sqm else str(entry.get("amount", ""))
            rate_sqm_text = str(entry.get("rate_sqm", "")) if is_per_sqm else "—"
            date_to_text = self._fmt_date(date_to) if date_to else "открытый"

            cb = QTableWidgetItem()
            cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                       | Qt.ItemFlag.ItemIsSelectable)
            cb.setCheckState(Qt.CheckState.Unchecked)
            if is_current:
                cb.setBackground(QColor("#E6F4EA"))
            self.table.setItem(r_idx, self.C_CHECK, cb)

            # Все пять колонок редактируются инлайн (см. делегаты в
            # _setup_ui) — read-only здесь ни у одной нет.
            cells = [
                (self._fmt_date(date_from), fg_date),
                (date_to_text,             fg_date),
                (amount_text,              fg_value),
                (rate_sqm_text,            fg_value),
                (entry.get("note", ""),    "#9CA3AF"),
            ]
            for offset, (text, fg) in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setForeground(QColor(fg))
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                if is_current:
                    it.setBackground(QColor("#E6F4EA"))
                self.table.setItem(r_idx, self.C_FROM + offset, it)

            self.table.setRowHeight(r_idx, 34)

        self.table.blockSignals(False)
        self._update_delete_btn_state()

        if periods:
            current = periods[0]
            date_from = current.get("date_from", current.get("date", "?"))
            date_to = current.get("date_to", "")
            period_str = self._fmt_date(date_from)
            if date_to:
                period_str += f" — {self._fmt_date(date_to)}"
            if current.get("per_sqm"):
                desc = f"{current.get('rate_sqm', '?')} ₽/м²"
            else:
                desc = f"{current.get('amount', '?')} ₽"
            self.status_lbl.setText(
                f"Актуальный период: {period_str}  ·  {desc}  ·  всего периодов: {len(periods)}"
            )
        else:
            self.status_lbl.setText("Нет периодов — добавьте первый")

    def _add_rate(self):
        # Управление таблицей целиком инлайн (как «Операции») — «Добавить
        # период» сразу вставляет строку в таблицу (открытый период с
        # сегодняшней датой) и открывает редактор суммы, вместо отдельной
        # формы-попапа.
        entry: dict = {
            "date_from": QDate.currentDate().toString("yyyy-MM-dd"),
            "amount": "",
            "per_sqm": False,
            "rate_sqm": "",
            "note": "",
        }
        self._rates.append(entry)
        self._save()
        self._rebuild_table()

        # Новый период — сегодняшняя дата, поэтому он встаёт первой строкой
        # (см. _sorted_periods); сразу открываем ячейку суммы на редактирование.
        self.table.scrollToTop()
        self.table.setCurrentCell(0, self.C_AMOUNT)
        self.table.edit(self.table.model().index(0, self.C_AMOUNT))

    def _on_item_changed(self, item: QTableWidgetItem):
        if self.table.signalsBlocked():
            return
        if item.column() == self.C_CHECK:
            self._update_delete_btn_state()
            return

        periods = self._sorted_periods()
        r_idx = item.row()
        if r_idx >= len(periods):
            return
        col = item.column()
        val = item.text().strip()
        entry = periods[r_idx]
        orig_idx = next((i for i, e in enumerate(self._rates) if e is entry), None)
        if orig_idx is None:
            return

        if col == self.C_FROM:
            iso = self._parse_date(val)
            if iso is None:
                self._rebuild_table()
                return
            self._rates[orig_idx]["date_from"] = iso
        elif col == self.C_TO:
            if not val or val.lower() == "открытый":
                self._rates[orig_idx].pop("date_to", None)
            else:
                iso = self._parse_date(val)
                if iso is None:
                    self._rebuild_table()
                    return
                self._rates[orig_idx]["date_to"] = iso
        elif col == self.C_AMOUNT:
            if val:
                try:
                    v = float(val.replace(",", "."))
                    if v <= 0:
                        raise ValueError
                except ValueError:
                    self._rebuild_table()
                    return
                self._rates[orig_idx]["amount"] = val.replace(",", ".")
                self._rates[orig_idx]["per_sqm"] = False
                self._rates[orig_idx]["rate_sqm"] = ""
        elif col == self.C_SQM:
            if val:
                try:
                    v = float(val.replace(",", "."))
                    if v <= 0:
                        raise ValueError
                except ValueError:
                    self._rebuild_table()
                    return
                self._rates[orig_idx]["rate_sqm"] = val.replace(",", ".")
                self._rates[orig_idx]["per_sqm"] = True
                self._rates[orig_idx]["amount"] = ""
        elif col == self.C_NOTE:
            self._rates[orig_idx]["note"] = val

        self._sanitize_entry(self._rates[orig_idx])
        self._save()
        self._rebuild_table()

    def _on_end_context_menu(self, pos):
        """ПКМ по «Конец»: сделать открытым (снять дату окончания) или,
        если уже открыт, сразу перейти к выбору даты — единственное место,
        где остался контекстное меню (это не удаление, а отдельная от
        чекбоксов/кнопки в шапке функция)."""
        idx = self.table.indexAt(pos)
        if not idx.isValid() or idx.column() != self.C_TO:
            return
        periods = self._sorted_periods()
        r_idx = idx.row()
        if r_idx >= len(periods):
            return
        entry = periods[r_idx]

        menu = QMenu(self)
        menu.setStyleSheet(menu_qss())
        if entry.get("date_to"):
            act = QAction("Сделать открытым (без даты окончания)", self)
            act.triggered.connect(lambda: self._make_open_ended(entry))
        else:
            act = QAction("Указать дату окончания…", self)
            act.triggered.connect(lambda: self.table.edit(idx))
        menu.addAction(act)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _make_open_ended(self, entry: dict):
        orig_idx = next((i for i, e in enumerate(self._rates) if e is entry), None)
        if orig_idx is None:
            return
        self._rates[orig_idx].pop("date_to", None)
        self._save()
        self._rebuild_table()

    def _update_delete_btn_state(self):
        n = sum(
            1 for r in range(self.table.rowCount())
            if self.table.item(r, self.C_CHECK)
            and self.table.item(r, self.C_CHECK).checkState() == Qt.CheckState.Checked
        )
        self.btn_delete_selected.setEnabled(n > 0)

    def _delete_selected(self):
        periods = self._sorted_periods()
        to_delete = [
            periods[r] for r in range(self.table.rowCount())
            if self.table.item(r, self.C_CHECK)
            and self.table.item(r, self.C_CHECK).checkState() == Qt.CheckState.Checked
            and r < len(periods)
        ]
        if not to_delete:
            return

        if len(to_delete) == 1:
            entry = to_delete[0]
            date_from = entry.get("date_from", entry.get("date", ""))
            date_to = entry.get("date_to", "")
            period_str = self._fmt_date(date_from)
            if date_to:
                period_str += f" — {self._fmt_date(date_to)}"
            desc = (f"{entry.get('rate_sqm', '')} ₽/м²" if entry.get("per_sqm")
                    else f"{entry.get('amount', '')} ₽")
            msg = f"Удалить период {period_str} ({desc})?"
        else:
            msg = f"Удалить выбранные периоды ({len(to_delete)})?"

        if not _ConfirmDialog.confirm(self, "Удаление периодов", msg):
            return

        del_ids = {id(e) for e in to_delete}
        self._rates = [e for e in self._rates if id(e) not in del_ids]
        self._save()
        self._rebuild_table()
