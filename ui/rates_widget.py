"""Виджеты управления тарифами (электроэнергия и членские взносы)."""
from __future__ import annotations

import json
import os

from PyQt6.QtCore import Qt, QDate, QPoint
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QCheckBox, QDateEdit, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.utils import DATA_DIR
from ui.buttons import GhostButton, PrimaryButton
from ui.dialogs import ConfirmDialog as _ConfirmDialog
from ui.theme import C, menu_qss


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
    """Вкладка тарифов на электроэнергию (₽/кВт·ч)."""

    DATA_FILE = os.path.join(DATA_DIR, "snt_rates.json")

    def __init__(self):
        super().__init__()
        self._rates: list = self._load()  # [{"date": "YYYY-MM-DD", "rate": "X.XX", "note": "..."}]
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

        # Заголовок
        top = QHBoxLayout()
        title = QLabel("Нормативы (тарифы на электроэнергию)", objectName="pageTitle")
        top.addWidget(title)
        top.addStretch()
        btn_add = PrimaryButton("Добавить тариф", icon="add")
        btn_add.clicked.connect(self._add_rate)
        top.addWidget(btn_add)
        lay.addLayout(top)

        hint = QLabel(
            "Двойной клик по ячейке — редактировать.  "
            "ПКМ по строке — удалить.  "
            "Записи отсортированы по дате (новые сверху)."
        )
        hint.setStyleSheet("color: #9CA3AF; background: transparent; font-size: 11px;")
        lay.addWidget(hint)

        # Форма добавления (скрыта по умолчанию)
        self.form_frame = QFrame()
        self.form_frame.setObjectName("filterFrame")
        self.form_frame.setVisible(False)
        form_lay = QHBoxLayout(self.form_frame)
        form_lay.setContentsMargins(16, 12, 16, 12)
        form_lay.setSpacing(12)

        form_lay.addWidget(QLabel("Дата:", objectName="filterLabel"))
        self.inp_date = QDateEdit()
        self.inp_date.setObjectName("datePicker")
        self.inp_date.setCalendarPopup(True)
        self.inp_date.setDate(QDate.currentDate())
        self.inp_date.setDisplayFormat("dd.MM.yyyy")
        form_lay.addWidget(self.inp_date)

        form_lay.addWidget(QLabel("₽/кВт·ч:", objectName="filterLabel"))
        self.inp_rate = QLineEdit()
        self.inp_rate.setObjectName("searchInput")
        self.inp_rate.setPlaceholderText("например: 4.50")
        self.inp_rate.setFixedWidth(120)
        form_lay.addWidget(self.inp_rate)

        form_lay.addWidget(QLabel("Примечание:", objectName="filterLabel"))
        self.inp_note = QLineEdit()
        self.inp_note.setObjectName("searchInput")
        self.inp_note.setPlaceholderText("необязательно")
        form_lay.addWidget(self.inp_note, stretch=1)

        btn_ok = PrimaryButton("Добавить")
        btn_ok.clicked.connect(self._confirm_add)
        form_lay.addWidget(btn_ok)

        btn_cancel = GhostButton(icon="close", tooltip="Скрыть форму")
        btn_cancel.clicked.connect(lambda: self.form_frame.setVisible(False))
        form_lay.addWidget(btn_cancel)

        lay.addWidget(self.form_frame)

        # Таблица
        self.table = QTableWidget()
        self.table.setObjectName("summaryTable")
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Дата вступления в силу", "Тариф (₽/кВт·ч)", "Примечание"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 180)
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

        # Сортировка: новые сверху
        rates = sorted(self._rates, key=lambda r: r.get("date", ""), reverse=True)

        self.table.setRowCount(len(rates))
        for r_idx, entry in enumerate(rates):
            # Дата
            raw_date = entry.get("date", "")
            try:
                from datetime import datetime
                d = datetime.strptime(raw_date, "%Y-%m-%d")
                display_date = d.strftime("%d.%m.%Y")
            except Exception:
                display_date = raw_date

            # Цвет: первая строка (самый актуальный тариф) — выделена
            is_current = (r_idx == 0)
            bg = "#E6F4EA" if is_current else "#F9FAFB"
            fg_date = "#059669" if is_current else "#374151"

            for c_idx, (text, fg) in enumerate([
                (display_date,           fg_date),
                (entry.get("rate", ""), "#07414F" if is_current else "#374151"),
                (entry.get("note", ""), "#9CA3AF"),
            ]):
                it = QTableWidgetItem(text)
                it.setBackground(QColor(bg))
                it.setForeground(QColor(fg))
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                # Дата — не редактируем напрямую (только через форму добавления)
                if c_idx == 0:
                    it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(r_idx, c_idx, it)

            self.table.setRowHeight(r_idx, 34)

        self.table.blockSignals(False)

        current = rates[0] if rates else None
        if current:
            self.status_lbl.setText(
                f"Актуальный тариф: {current.get('rate', '?')} ₽/кВт·ч  "
                f"(с {rates[0].get('date', '?')})  ·  всего записей: {len(rates)}"
            )
        else:
            self.status_lbl.setText("Нет записей — добавьте первый тариф")

    def _add_rate(self):
        self.inp_rate.clear()
        self.inp_note.clear()
        self.form_frame.setVisible(True)
        self.inp_rate.setFocus()

    def _confirm_add(self):
        rate_text = self.inp_rate.text().strip().replace(",", ".")
        if not rate_text:
            self.inp_rate.setFocus()
            return
        try:
            float(rate_text)
        except ValueError:
            _mark_invalid_input(self.inp_rate)
            return

        date_str = self.inp_date.date().toString("yyyy-MM-dd")
        entry = {"date": date_str, "rate": rate_text, "note": self.inp_note.text().strip()}
        self._rates.append(entry)
        self._save()
        self.form_frame.setVisible(False)
        self._rebuild_table()

    def _on_cell_edited(self, item: QTableWidgetItem):
        if self.table.signalsBlocked():
            return
        # Пересчитываем индекс в отсортированном списке
        rates_sorted = sorted(self._rates, key=lambda r: r.get("date", ""), reverse=True)
        r_idx = item.row()
        if r_idx >= len(rates_sorted):
            return
        col = item.column()
        val = item.text().strip()
        entry = rates_sorted[r_idx]
        # Находим оригинальную запись в self._rates
        orig_idx = next(
            (i for i, e in enumerate(self._rates) if e is entry), None
        )
        if orig_idx is None:
            return
        if col == 1:
            self._rates[orig_idx]["rate"] = val.replace(",", ".")
        elif col == 2:
            self._rates[orig_idx]["note"] = val
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


class VznosyRatesWidget(QWidget):
    """Управление периодами членских взносов."""

    DATA_FILE = os.path.join(DATA_DIR, "snt_vznosy_rates.json")

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

    @staticmethod
    def _fmt_date(iso: str) -> str:
        try:
            from datetime import datetime
            return datetime.strptime(iso, "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            return iso or "—"

    def _sorted_periods(self) -> list:
        """Периоды, отсортированные по date_from по убыванию (новые сверху)."""
        def _key(r):
            return r.get("date_from", r.get("date", ""))
        return sorted(self._rates, key=_key, reverse=True)

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        top = QHBoxLayout()
        title = QLabel("Периоды членских взносов", objectName="pageTitle")
        top.addWidget(title)
        top.addStretch()
        btn_add = PrimaryButton("Добавить период", icon="add")
        btn_add.clicked.connect(self._add_rate)
        top.addWidget(btn_add)
        lay.addLayout(top)

        hint = QLabel(
            "Каждый период — отдельная строка тарифа.  "
            "ПКМ по строке — удалить.  "
            "Двойной клик по сумме — редактировать."
        )
        hint.setStyleSheet("color: #9CA3AF; background: transparent; font-size: 11px;")
        lay.addWidget(hint)

        # ── Форма добавления периода ──────────────────────────────
        self.form_frame = QFrame()
        self.form_frame.setObjectName("filterFrame")
        self.form_frame.setVisible(False)
        form_lay = QHBoxLayout(self.form_frame)
        form_lay.setContentsMargins(16, 12, 16, 12)
        form_lay.setSpacing(10)

        form_lay.addWidget(QLabel("С:", objectName="filterLabel"))
        self.inp_date_from = QDateEdit()
        self.inp_date_from.setObjectName("datePicker")
        self.inp_date_from.setCalendarPopup(True)
        self.inp_date_from.setDate(QDate.currentDate())
        self.inp_date_from.setDisplayFormat("dd.MM.yyyy")
        self.inp_date_from.setFixedWidth(115)
        form_lay.addWidget(self.inp_date_from)

        form_lay.addWidget(QLabel("По:", objectName="filterLabel"))
        self.inp_date_to = QDateEdit()
        self.inp_date_to.setObjectName("datePicker")
        self.inp_date_to.setCalendarPopup(True)
        self.inp_date_to.setDate(QDate.currentDate())
        self.inp_date_to.setDisplayFormat("dd.MM.yyyy")
        self.inp_date_to.setFixedWidth(115)
        form_lay.addWidget(self.inp_date_to)

        self.chk_open_end = QCheckBox("Открытый")
        self.chk_open_end.setToolTip("Не указывать конечную дату (последний активный период)")
        self.chk_open_end.stateChanged.connect(
            lambda s: self.inp_date_to.setEnabled(not bool(s))
        )
        form_lay.addWidget(self.chk_open_end)

        form_lay.addWidget(QLabel("Сумма (₽):", objectName="filterLabel"))
        self.inp_amount = QLineEdit()
        self.inp_amount.setObjectName("searchInput")
        self.inp_amount.setPlaceholderText("10000")
        self.inp_amount.setFixedWidth(100)
        form_lay.addWidget(self.inp_amount)

        self.chk_per_sqm = QCheckBox("₽/м²")
        self.chk_per_sqm.setToolTip("Сумма указана в рублях за м²")
        self.chk_per_sqm.stateChanged.connect(self._on_toggle_per_sqm)
        form_lay.addWidget(self.chk_per_sqm)

        form_lay.addWidget(QLabel("₽/м²:", objectName="filterLabel"))
        self.inp_rate_sqm = QLineEdit()
        self.inp_rate_sqm.setObjectName("searchInput")
        self.inp_rate_sqm.setPlaceholderText("15.00")
        self.inp_rate_sqm.setFixedWidth(80)
        self.inp_rate_sqm.setEnabled(False)
        form_lay.addWidget(self.inp_rate_sqm)

        form_lay.addWidget(QLabel("Примечание:", objectName="filterLabel"))
        self.inp_note = QLineEdit()
        self.inp_note.setObjectName("searchInput")
        self.inp_note.setPlaceholderText("необязательно")
        form_lay.addWidget(self.inp_note, stretch=1)

        btn_ok = PrimaryButton("Сохранить")
        btn_ok.clicked.connect(self._confirm_add)
        form_lay.addWidget(btn_ok)

        btn_cancel = GhostButton(icon="close", tooltip="Скрыть форму")
        btn_cancel.clicked.connect(lambda: self.form_frame.setVisible(False))
        form_lay.addWidget(btn_cancel)

        lay.addWidget(self.form_frame)

        # ── Таблица периодов ──────────────────────────────────────
        self.table = QTableWidget()
        self.table.setObjectName("summaryTable")
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["Период с", "По (включительно)", "Сумма (₽)", "Цена за м² (₽)", "Примечание"]
        )
        hdr = self.table.horizontalHeader()
        for c, (mode, w) in enumerate([
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
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.itemChanged.connect(self._on_cell_edited)
        lay.addWidget(self.table)

        self.status_lbl = QLabel("", objectName="statusLabel")
        lay.addWidget(self.status_lbl)

    def _on_toggle_per_sqm(self, state):
        on = bool(state)
        self.inp_amount.setEnabled(not on)
        self.inp_rate_sqm.setEnabled(on)
        if on:
            self.inp_amount.clear()
            self.inp_rate_sqm.setFocus()
        else:
            self.inp_rate_sqm.clear()
            self.inp_amount.setFocus()

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
            bg = "#E6F4EA" if is_current else "#F9FAFB"
            fg_date = "#059669" if is_current else "#374151"
            fg_value = "#07414F" if is_current else "#374151"

            amount_text = "—" if is_per_sqm else str(entry.get("amount", ""))
            rate_sqm_text = str(entry.get("rate_sqm", "")) if is_per_sqm else "—"
            date_to_text = self._fmt_date(date_to) if date_to else "открытый"

            cells = [
                (self._fmt_date(date_from), fg_date,  True),   # read-only
                (date_to_text,             fg_date,  True),   # read-only
                (amount_text,              fg_value, is_per_sqm),
                (rate_sqm_text,            fg_value, not is_per_sqm),
                (entry.get("note", ""),    "#9CA3AF", False),
            ]
            for c_idx, (text, fg, read_only) in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setBackground(QColor(bg))
                it.setForeground(QColor(fg))
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                if read_only:
                    it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(r_idx, c_idx, it)

            self.table.setRowHeight(r_idx, 34)

        self.table.blockSignals(False)

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
        self.inp_amount.clear()
        self.inp_rate_sqm.clear()
        self.inp_note.clear()
        self.chk_per_sqm.setChecked(False)
        self.chk_open_end.setChecked(False)
        self.inp_date_to.setEnabled(True)
        self.form_frame.setVisible(True)
        self.inp_amount.setFocus()

    def _confirm_add(self):
        per_sqm = self.chk_per_sqm.isChecked()
        if per_sqm:
            raw = self.inp_rate_sqm.text().strip().replace(",", ".")
            target = self.inp_rate_sqm
        else:
            raw = self.inp_amount.text().strip().replace(",", ".")
            target = self.inp_amount

        if not raw:
            target.setFocus()
            return
        try:
            v = float(raw)
            if v <= 0:
                raise ValueError
        except ValueError:
            _mark_invalid_input(target)
            return

        date_from_str = self.inp_date_from.date().toString("yyyy-MM-dd")
        entry: dict = {
            "date_from": date_from_str,
            "amount": "" if per_sqm else raw,
            "per_sqm": per_sqm,
            "rate_sqm": raw if per_sqm else "",
            "note": self.inp_note.text().strip(),
        }
        if not self.chk_open_end.isChecked():
            entry["date_to"] = self.inp_date_to.date().toString("yyyy-MM-dd")

        self._rates.append(entry)
        self._save()
        self.form_frame.setVisible(False)
        self._rebuild_table()

    def _on_cell_edited(self, item: QTableWidgetItem):
        if self.table.signalsBlocked():
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
        if col == 2 and not entry.get("per_sqm"):
            self._rates[orig_idx]["amount"] = val.replace(",", ".")
        elif col == 3 and entry.get("per_sqm"):
            self._rates[orig_idx]["rate_sqm"] = val.replace(",", ".")
        elif col == 4:
            self._rates[orig_idx]["note"] = val
        self._save()
        self._rebuild_table()

    def _context_menu(self, pos: QPoint):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu(self)
        menu.setStyleSheet(menu_qss(danger=True))
        act_del = QAction("Удалить период", self)
        act_del.triggered.connect(lambda: self._delete_rate(row))
        menu.addAction(act_del)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _delete_rate(self, row: int):
        periods = self._sorted_periods()
        if row >= len(periods):
            return
        entry = periods[row]
        date_from = entry.get("date_from", entry.get("date", ""))
        date_to = entry.get("date_to", "")
        period_str = self._fmt_date(date_from)
        if date_to:
            period_str += f" — {self._fmt_date(date_to)}"
        if entry.get("per_sqm"):
            desc = f"{entry.get('rate_sqm', '')} ₽/м²"
        else:
            desc = f"{entry.get('amount', '')} ₽"
        confirmed = _ConfirmDialog.confirm(
            self, "Удаление периода",
            f"Удалить период {period_str} ({desc})?",
        )
        if confirmed:
            self._rates = [e for e in self._rates if e is not entry]
            self._save()
            self._rebuild_table()
