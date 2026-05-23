import json
import os

import pandas as pd
from PyQt6.QtCore import Qt, QPoint, pyqtSignal
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from core.utils import DATA_DIR
from ui.plot_detection import _PLOTS_FILE


def _plot_num_key(s: str):
    try:
        return (0, int(s), s)
    except ValueError:
        return (1, 0, s)


def _load_plot_order() -> list[str]:
    """Возвращает список номеров участков из snt_plots.json, отсортированный по _plot_num_key."""
    try:
        if os.path.exists(_PLOTS_FILE):
            with open(_PLOTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            nums = [str(e.get("num", "")) for e in data if e.get("num")]
            return sorted(set(nums), key=_plot_num_key)
    except Exception:
        pass
    return []


class PlotsWidget(QWidget):
    """Вкладка участков: ручное добавление и управление списком."""

    plotsUpdated = pyqtSignal()

    DATA_FILE = os.path.join(DATA_DIR, "snt_plots.json")

    def __init__(self):
        super().__init__()
        self.setAutoFillBackground(True)
        self._plots: list = self._load()
        self._sort_col: int = 0
        self._sort_asc: bool = True
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
                json.dump(self._plots, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        self.plotsUpdated.emit()

    def reload(self):
        self._plots = self._load()
        self._rebuild_table()
        self.plotsUpdated.emit()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        top = QHBoxLayout()
        top.addStretch()
        btn_import = QPushButton("📥  Импорт из Excel")
        btn_import.setObjectName("btnSecondary")
        btn_import.clicked.connect(self._import_from_excel)
        top.addWidget(btn_import)
        btn_add = QPushButton("＋  Добавить участок")
        btn_add.setObjectName("btnPrimary")
        btn_add.clicked.connect(self._add_plot)
        top.addWidget(btn_add)
        layout.addLayout(top)

        self.status_label = QLabel("", objectName="statusLabel")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(objectName="mainTable")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self.table.horizontalHeader().setCursor(Qt.CursorShape.PointingHandCursor)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.table)

    def _on_header_clicked(self, col: int):
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        self._rebuild_table()

    def _sorted_plots(self) -> list:
        col = self._sort_col
        asc = self._sort_asc
        if col == 0:
            key = lambda p: _plot_num_key(str(p.get("num", "")))
        elif col == 1:
            key = lambda p: ((p.get("owners") or [""])[0].lower())
        else:
            def key(p):
                try:
                    v = p.get("area")
                    if v in (None, ""):
                        return float("inf") if asc else float("-inf")
                    return float(v)
                except (TypeError, ValueError):
                    return float("inf") if asc else float("-inf")
        return sorted(self._plots, key=key, reverse=not asc)

    def _rebuild_table(self):
        self.table.blockSignals(True)
        self.table.clearContents()

        plots_sorted = self._sorted_plots()

        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Участок", "Собственники", "Площадь, м²"])
        hdr = self.table.horizontalHeader()
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(
            self._sort_col,
            Qt.SortOrder.AscendingOrder if self._sort_asc else Qt.SortOrder.DescendingOrder,
        )
        self.table.setRowCount(len(plots_sorted))

        for r_idx, plot in enumerate(plots_sorted):
            num_item = QTableWidgetItem(f"уч. {plot.get('num', '?')}")
            num_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            num_item.setForeground(QColor("#6366F1"))
            f = num_item.font(); f.setBold(True); num_item.setFont(f)
            self.table.setItem(r_idx, 0, num_item)

            owner_widget = self._build_owners_cell(plot)
            self.table.setCellWidget(r_idx, 1, owner_widget)

            area_raw = plot.get("area")
            try:
                area_v = float(area_raw) if area_raw not in (None, "") else None
            except (TypeError, ValueError):
                area_v = None
            area_item = QTableWidgetItem(f"{area_v:g}" if area_v is not None else "—")
            area_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            area_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            area_item.setForeground(QColor("#374151" if area_v is not None else "#9CA3AF"))
            self.table.setItem(r_idx, 2, area_item)

            self.table.setRowHeight(r_idx, 28)

        hdr = self.table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self.table.blockSignals(False)
        self.status_label.setText(f"Участков: {len(plots_sorted)}")

    def _build_owners_cell(self, plot: dict) -> QWidget:
        owners = plot.get("owners", []) or []
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(8)

        if owners:
            first_owner = owners[0]
            first_label = QLabel(first_owner)
            first_label.setStyleSheet("color:#374151;font-size:13px;")
            first_label.setToolTip("\n".join(owners))
            layout.addWidget(first_label, 1)

            extra = len(owners) - 1
            if extra > 0:
                btn_more = QPushButton(f"+{extra}")
                btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_more.setStyleSheet(
                    "QPushButton{background:transparent;color:#82cfff;border:none;"
                    "font-weight:700;padding:0px;margin:0px;}"
                    "QPushButton:hover{text-decoration:underline;}"
                )
                btn_more.clicked.connect(lambda _, p=plot: self._show_owners_popup(p))
                layout.addWidget(btn_more, 0, Qt.AlignmentFlag.AlignRight)
        else:
            label = QLabel("—")
            label.setStyleSheet("color:#9CA3AF;font-size:13px;")
            layout.addWidget(label)

        return container

    def _show_owners_popup(self, plot: dict):
        dlg = OwnersPopup(plot.get("num", "?"), plot.get("owners", []), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            updated = dlg.get_owners()
            if updated != plot.get("owners", []):
                idx = self._plots.index(plot)
                self._plots[idx] = {**plot, "owners": updated}
                self._save()
                self._rebuild_table()

    def _import_from_excel(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл Excel", "",
            "Excel файлы (*.xlsx *.xls *.xlsm)"
        )
        if not path:
            return

        try:
            df = pd.read_excel(path, dtype=str)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка чтения файла", str(e))
            return

        col_num = None
        col_name = None
        col_area = None
        for col in df.columns:
            col_lower = str(col).lower().strip()
            if col_num is None and ("участк" in col_lower or col_lower in ("№", "n", "номер")):
                col_num = col
            if col_name is None and ("ф.и.о" in col_lower or "фио" in col_lower or "имя" in col_lower or col_lower == "ф.и.о."):
                col_name = col
            if col_area is None and ("площад" in col_lower or "кв.м" in col_lower or "м²" in col_lower or "м2" in col_lower):
                col_area = col

        if col_num is None or col_name is None:
            QMessageBox.warning(
                self, "Неверный формат",
                f"Не удалось найти нужные столбцы.\n"
                f"Ожидается: «№ участка» и «Ф.И.О.»\n"
                f"Найдены столбцы: {', '.join(str(c) for c in df.columns)}"
            )
            return

        imported: dict[str, dict] = {}
        for _, row in df.iterrows():
            num = str(row[col_num]).strip()
            name = str(row[col_name]).strip()
            if not num or num.lower() in ("nan", "none", "") or not name or name.lower() in ("nan", "none", ""):
                continue
            entry = imported.setdefault(num, {"owners": [], "area": None})
            if name not in entry["owners"]:
                entry["owners"].append(name)
            if col_area is not None and entry["area"] is None:
                raw = str(row[col_area]).strip().replace(",", ".")
                if raw and raw.lower() not in ("nan", "none"):
                    try:
                        v = float(raw)
                        if v > 0:
                            entry["area"] = v
                    except ValueError:
                        pass

        if not imported:
            QMessageBox.warning(self, "Пустой файл", "В файле не найдено данных об участках.")
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Импорт участков")
        msg.setText(
            f"Найдено {len(imported)} участков в файле.\n\n"
            "Как импортировать?"
        )
        btn_replace = msg.addButton("Заменить всё", QMessageBox.ButtonRole.DestructiveRole)
        btn_merge   = msg.addButton("Объединить",   QMessageBox.ButtonRole.AcceptRole)
        btn_cancel  = msg.addButton("Отмена",        QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_merge)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked is btn_cancel:
            return

        if clicked is btn_replace:
            new_plots = []
            for num, entry in imported.items():
                item = {"num": num, "owners": entry["owners"]}
                if entry["area"] is not None:
                    item["area"] = entry["area"]
                new_plots.append(item)
            self._plots = new_plots
        else:
            existing = {p["num"]: p for p in self._plots}
            for num, entry in imported.items():
                owners = entry["owners"]
                area = entry["area"]
                if num in existing:
                    current_owners = existing[num].get("owners", [])
                    for o in owners:
                        if o not in current_owners:
                            current_owners.append(o)
                    existing[num]["owners"] = current_owners
                    if area is not None and existing[num].get("area") in (None, "", 0):
                        existing[num]["area"] = area
                else:
                    item = {"num": num, "owners": owners}
                    if area is not None:
                        item["area"] = area
                    existing[num] = item
            self._plots = list(existing.values())

        self._save()
        self._rebuild_table()
        QMessageBox.information(
            self, "Импорт завершён",
            f"Импортировано {len(imported)} участков."
        )

    def _add_plot(self):
        dlg = PlotEditDialog(plot_data=None, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            result = dlg.get_result()
            if result:
                self._plots.append(result)
                self._save()
                self._rebuild_table()

    def _context_menu(self, pos: QPoint):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return

        plots_sorted = self._sorted_plots()
        if row >= len(plots_sorted):
            return

        plot = plots_sorted[row]
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu{background:#F8F9FA;border:1px solid #D1D5DB;color:#374151;
                  font-size:13px;padding:4px;}
            QMenu::item{padding:8px 20px;border-radius:4px;}
            QMenu::item:selected{background:#EEF2FF;color:#DC2626;}
        """)

        act_edit = QAction("✏️  Редактировать", self)
        act_edit.triggered.connect(lambda: self._edit_plot(row, plot))
        menu.addAction(act_edit)

        act_del = QAction("Удалить", self)
        act_del.triggered.connect(lambda: self._delete_plot(row, plot))
        menu.addAction(act_del)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _edit_plot(self, row: int, plot: dict):
        dlg = PlotEditDialog(plot_data=plot, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            result = dlg.get_result()
            if result:
                idx = self._plots.index(plot)
                self._plots[idx] = result
                self._save()
                self._rebuild_table()

    def _delete_plot(self, row: int, plot: dict):
        reply = QMessageBox.question(
            self, "Удаление участка",
            f"Удалить участок № {plot.get('num', '?')}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._plots = [p for p in self._plots if p is not plot]
            self._save()
            self._rebuild_table()


class OwnersPopup(QDialog):
    """Диалог просмотра/редактирования списка собственников участка."""

    def __init__(self, plot_num: str, owners: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Собственники — уч. {plot_num}")
        self.setMinimumWidth(420)
        self.setModal(True)
        self._owners = list(owners)
        self._inputs: list[QLineEdit] = []
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(10)

        title = QLabel("Список собственников")
        title.setStyleSheet("font-size:14px;font-weight:700;color:#111827;")
        lay.addWidget(title)

        self._scroll_widget = QWidget()
        self._form_lay = QVBoxLayout(self._scroll_widget)
        self._form_lay.setSpacing(6)
        self._form_lay.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._scroll_widget)
        scroll.setStyleSheet(
            "QScrollArea{background:#F8F9FA;border:1px solid #E5E7EB;border-radius:6px;}"
        )
        scroll.setMinimumHeight(140)
        scroll.setMaximumHeight(300)
        lay.addWidget(scroll)

        for name in self._owners:
            self._add_owner_row(name)

        btn_add = QPushButton("＋  Добавить собственника")
        btn_add.setObjectName("btnSecondary")
        btn_add.clicked.connect(lambda: self._add_owner_row(""))
        lay.addWidget(btn_add)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btns.setStyleSheet(
            "QPushButton{background:#4F46E5;color:white;border:none;border-radius:6px;"
            "padding:7px 18px;font-size:13px;font-weight:600;}"
            "QPushButton:hover{background:#6366F1;}"
            "QPushButton[text='Cancel']{background:#E5E7EB;color:#6B7280;}"
        )
        lay.addWidget(btns)

    def _add_owner_row(self, name: str):
        row_widget = QWidget()
        row_widget.setStyleSheet("background:transparent;")
        rlay = QHBoxLayout(row_widget)
        rlay.setContentsMargins(6, 2, 6, 2)
        rlay.setSpacing(6)

        inp = QLineEdit(name)
        inp.setPlaceholderText("Фамилия Имя Отчество")
        inp.setStyleSheet(
            "background:#F8F9FA;border:1px solid #D1D5DB;border-radius:5px;"
            "color:#374151;padding:6px 10px;font-size:13px;"
        )
        self._inputs.append(inp)
        rlay.addWidget(inp, stretch=1)

        btn_del = QPushButton("✕")
        btn_del.setFixedSize(28, 28)
        btn_del.setStyleSheet(
            "QPushButton{background:#2a1a1a;border:1px solid #5a2a2a;"
            "border-radius:5px;color:#DC2626;font-size:13px;}"
            "QPushButton:hover{background:#3a2020;}"
        )
        btn_del.clicked.connect(lambda _, w=row_widget, i=inp: self._remove_row(w, i))
        rlay.addWidget(btn_del)

        self._form_lay.addWidget(row_widget)

    def _remove_row(self, row_widget: QWidget, inp: QLineEdit):
        if inp in self._inputs:
            self._inputs.remove(inp)
        row_widget.setParent(None)
        row_widget.deleteLater()

    def get_owners(self) -> list[str]:
        return [inp.text().strip() for inp in self._inputs if inp.text().strip()]

    def _apply_styles(self):
        self.setStyleSheet(
            "QDialog{background:#FFFFFF;color:#374151;}"
            "QLabel{background:transparent;color:#374151;}"
        )


class PlotEditDialog(QDialog):
    """Диалог добавления / редактирования участка."""

    def __init__(self, plot_data: dict | None = None, parent=None):
        super().__init__(parent)
        self._is_edit = plot_data is not None
        self._plot_data = plot_data or {}
        self.setWindowTitle("Редактировать участок" if self._is_edit else "Новый участок")
        self.setMinimumWidth(460)
        self.setModal(True)
        self._owner_inputs: list[QLineEdit] = []
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 20)
        lay.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.inp_num = QLineEdit(str(self._plot_data.get("num", "")))
        self.inp_num.setPlaceholderText("например: 15 или 15/207")
        if self._is_edit:
            self.inp_num.setReadOnly(True)
            self.inp_num.setStyleSheet(
                "background:#F3F4F6;border:1px solid #E5E7EB;"
                "border-radius:5px;color:#9CA3AF;padding:7px 10px;"
            )
        form.addRow("Номер участка:", self.inp_num)

        area_raw = self._plot_data.get("area")
        area_text = ""
        if area_raw not in (None, "", 0):
            try:
                area_text = f"{float(area_raw):g}"
            except (TypeError, ValueError):
                area_text = str(area_raw)
        self.inp_area = QLineEdit(area_text)
        self.inp_area.setPlaceholderText("например: 612 (необязательно)")
        form.addRow("Площадь, м²:", self.inp_area)
        lay.addLayout(form)

        own_label = QLabel("Собственники:")
        own_label.setStyleSheet("color:#9CA3AF;")
        lay.addWidget(own_label)

        self._owners_container = QWidget()
        self._owners_container.setStyleSheet("background:transparent;")
        self._owners_vlay = QVBoxLayout(self._owners_container)
        self._owners_vlay.setSpacing(6)
        self._owners_vlay.setContentsMargins(0, 0, 0, 0)

        existing_owners = self._plot_data.get("owners", [""])
        if not existing_owners:
            existing_owners = [""]
        for name in existing_owners:
            self._add_owner_field(name)

        lay.addWidget(self._owners_container)

        btn_add_owner = QPushButton("＋  Добавить собственника")
        btn_add_owner.setObjectName("btnSecondary")
        btn_add_owner.clicked.connect(lambda: self._add_owner_field(""))
        lay.addWidget(btn_add_owner)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#E5E7EB;background:#E5E7EB;max-height:1px;")
        lay.addWidget(sep)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Сохранить")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _add_owner_field(self, name: str):
        row = QWidget()
        row.setStyleSheet("background:transparent;")
        rlay = QHBoxLayout(row)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(6)

        inp = QLineEdit(name)
        inp.setPlaceholderText("Фамилия Имя Отчество")
        self._owner_inputs.append(inp)
        rlay.addWidget(inp, stretch=1)

        btn = QPushButton("✕")
        btn.setFixedSize(28, 28)
        btn.setStyleSheet(
            "QPushButton{background:#2a1a1a;border:1px solid #5a2a2a;"
            "border-radius:5px;color:#DC2626;font-size:12px;}"
            "QPushButton:hover{background:#3a2020;}"
        )
        btn.clicked.connect(lambda _, r=row, i=inp: self._remove_owner_field(r, i))
        rlay.addWidget(btn)
        self._owners_vlay.addWidget(row)

    def _remove_owner_field(self, row: QWidget, inp: QLineEdit):
        if len(self._owner_inputs) <= 1:
            inp.clear()
            return
        if inp in self._owner_inputs:
            self._owner_inputs.remove(inp)
        row.setParent(None)
        row.deleteLater()

    def _on_accept(self):
        num = self.inp_num.text().strip()
        if not num:
            QMessageBox.warning(self, "Ошибка", "Укажите номер участка")
            return
        owners = [i.text().strip() for i in self._owner_inputs if i.text().strip()]

        area_raw = self.inp_area.text().strip().replace(",", ".")
        area_val: float | None = None
        if area_raw:
            try:
                area_val = float(area_raw)
                if area_val <= 0:
                    raise ValueError("non-positive")
            except ValueError:
                QMessageBox.warning(self, "Ошибка",
                                    "Площадь должна быть положительным числом")
                return

        result = {"num": num, "owners": owners}
        if area_val is not None:
            result["area"] = area_val
        self._result = result
        self.accept()

    def get_result(self) -> dict:
        return getattr(self, "_result", {})

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog { background: #FFFFFF; color: #374151; }
            QLabel  { background: transparent; color: #374151; font-size: 13px; }
            QLineEdit {
                background: #F8F9FA; border: 1px solid #D1D5DB;
                border-radius: 5px; color: #374151; padding: 7px 10px; font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid #6366F1; }
            QPushButton#btnSecondary {
                background: #E5E7EB; color: #6B7280; border: 1px solid #D1D5DB;
                border-radius: 6px; padding: 7px 14px; font-size: 13px;
            }
            QPushButton#btnSecondary:hover { background: #E5E7EB; color: #374151; }
            QDialogButtonBox QPushButton {
                background: #4F46E5; color: white; border: none;
                border-radius: 6px; padding: 8px 20px; font-size: 13px; font-weight: 600;
            }
            QDialogButtonBox QPushButton:hover { background: #6366F1; }
            QDialogButtonBox QPushButton[text='Отмена'] {
                background: #E5E7EB; color: #6B7280;
            }
        """)


class DocCell(QWidget):
    """
    Ячейка документа: иконка-статус (✔️/—) + кнопка скрепки для прикрепления файла.
    Эмитит сигнал при изменении.
    """
    changed = pyqtSignal()

    def __init__(self, plot_num: str, doc_key: str,
                 file_path: str = "", parent=None):
        super().__init__(parent)
        self._plot = plot_num
        self._doc_key = doc_key
        self._path = file_path

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(6)

        self.lbl_status = QLabel()
        self.lbl_status.setFixedWidth(20)
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_status)

        self.btn_attach = QPushButton()
        self.btn_attach.setFixedSize(28, 28)
        self.btn_attach.setToolTip("Прикрепить / заменить файл")
        self.btn_attach.clicked.connect(self._on_attach)
        lay.addWidget(self.btn_attach)

        self.btn_open = QPushButton("↗️")
        self.btn_open.setFixedSize(28, 28)
        self.btn_open.setToolTip("Открыть прикреплённый файл")
        self.btn_open.clicked.connect(self._on_open)
        lay.addWidget(self.btn_open)

        lay.addStretch()
        self._refresh()

    def _refresh(self):
        has = bool(self._path and os.path.exists(self._path))
        if has:
            self.lbl_status.setText("✔️")
            self.lbl_status.setStyleSheet("color:#059669;font-size:14px;font-weight:700;")
            self.btn_attach.setText("📎")
            self.btn_attach.setStyleSheet(
                "QPushButton{background:#0d3b1a;border:1px solid #2e7d32;"
                "border-radius:5px;font-size:13px;}"
                "QPushButton:hover{background:#1b5e20;}"
            )
            self.btn_open.setEnabled(True)
            self.btn_open.setStyleSheet(
                "QPushButton{background:#F0F2F5;border:1px solid #D1D5DB;"
                "border-radius:5px;color:#6366F1;font-size:12px;}"
                "QPushButton:hover{background:#E5E7EB;}"
            )
        else:
            self.lbl_status.setText("—")
            self.lbl_status.setStyleSheet("color:#6B7280;font-size:14px;font-weight:700;")
            self.btn_attach.setText("📎")
            self.btn_attach.setStyleSheet(
                "QPushButton{background:#F0F2F5;border:1px solid #D1D5DB;"
                "border-radius:5px;font-size:13px;}"
                "QPushButton:hover{background:#E5E7EB;}"
            )
            self.btn_open.setEnabled(False)
            self.btn_open.setStyleSheet(
                "QPushButton{background:#0d1720;border:1px solid #1b2a3c;"
                "border-radius:5px;color:#9CA3AF;font-size:12px;}"
            )

    def _on_attach(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Прикрепить файл", "", "Все файлы (*.*)"
        )
        if not path:
            return
        self._path = path
        self._refresh()
        self.changed.emit()

    def _on_open(self):
        if not self._path or not os.path.exists(self._path):
            QMessageBox.warning(self, "Ошибка", "Файл не найден")
            return
        try:
            os.startfile(self._path)
        except Exception:
            QMessageBox.warning(self, "Ошибка", "Не удалось открыть файл")

    def get_path(self) -> str:
        return self._path


class DocsWidget(QWidget):
    """Вкладка документов: таблица по участку и типу документа."""

    DATA_FILE = os.path.join(DATA_DIR, "snt_docs.json")
    DOC_TYPES = [
        "Паспорт",
        "Договор",
        "Схема участка",
        "Прочие документы",
    ]

    def __init__(self):
        super().__init__()
        self.setAutoFillBackground(True)
        self._docs = self._load()
        self._cells: dict[tuple[str, str], DocCell] = {}
        self._setup_ui()
        self._rebuild_table()

    def _load(self) -> dict:
        try:
            if os.path.exists(self.DATA_FILE):
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save(self):
        try:
            with open(self.DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self._docs, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def reload(self):
        self._docs = self._load()
        self._cells.clear()
        self._rebuild_table()

    def refresh_plots(self):
        self._cells.clear()
        self._rebuild_table()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        top_bar = QHBoxLayout()
        title = QLabel("Документы")
        title.setObjectName("pageTitle")
        top_bar.addWidget(title)
        top_bar.addStretch()

        btn_save = QPushButton("Сохранить")
        btn_save.setObjectName("btnPrimary")
        btn_save.clicked.connect(self._save)
        top_bar.addWidget(btn_save)
        layout.addLayout(top_bar)

        self.status_label = QLabel("Документы не загружены", objectName="statusLabel")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(objectName="mainTable")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        layout.addWidget(self.table)

    def _rebuild_table(self):
        self.table.blockSignals(True)
        self.table.clearContents()

        plot_order = _load_plot_order()
        rows = len(plot_order)
        cols = 1 + len(self.DOC_TYPES)
        self.table.setRowCount(rows)
        self.table.setColumnCount(cols)

        headers = ["Участок"] + self.DOC_TYPES
        self.table.setHorizontalHeaderLabels(headers)

        for r_idx, plot in enumerate(plot_order):
            plot_item = QTableWidgetItem(f"уч. {plot}")
            plot_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            plot_item.setForeground(QColor("#6366F1"))
            f = plot_item.font(); f.setBold(True); plot_item.setFont(f)
            self.table.setItem(r_idx, 0, plot_item)

            plot_docs = self._docs.get(str(plot), {})
            for c_idx, doc_key in enumerate(self.DOC_TYPES, start=1):
                cell = DocCell(str(plot), doc_key, plot_docs.get(doc_key, ""), self)
                cell.changed.connect(
                    lambda _, p=str(plot), d=doc_key, w=cell: self._on_doc_changed(p, d, w)
                )
                self.table.setCellWidget(r_idx, c_idx, cell)
                self._cells[(str(plot), doc_key)] = cell
            self.table.setRowHeight(r_idx, 34)

        self.table.blockSignals(False)
        self._update_status()

    def _on_doc_changed(self, plot: str, doc_key: str, cell: DocCell):
        self._docs.setdefault(str(plot), {})[doc_key] = cell.get_path()
        self._save()
        self._update_status()

    def _update_status(self):
        total = 0
        attached = 0
        for plot in _load_plot_order():
            for doc_key in self.DOC_TYPES:
                path = self._docs.get(str(plot), {}).get(doc_key, "")
                total += 1
                if path:
                    attached += 1
        self.status_label.setText(
            f"Документов: {attached} из {total} прикреплено"
        )
