"""Карта участков СНТ с интерактивными маркерами."""
from __future__ import annotations

import json
import os

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QFont, QPen, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QMessageBox, QInputDialog,
    QGraphicsEllipseItem, QGraphicsTextItem, QGraphicsView,
    QGraphicsScene,
)

DATA_DIR = "data"


class _PlotMarker(QGraphicsEllipseItem):
    """Кликабельный кружок с номером участка."""
    R = 16

    def __init__(self, plot_num: str, owners: list, on_click,
                 color: str | None = None, debt: float | None = None):
        r = self.R
        super().__init__(-r, -r, r * 2, r * 2)
        self._plot_num = plot_num
        self._owners   = owners
        self._on_click = on_click
        self._base_color = QColor(color) if color else QColor("#1565c0")
        self._hover_color = self._lighten(self._base_color)
        self._debt = debt
        self.setBrush(self._base_color)
        self.setPen(QPen(self._lighten(self._base_color), 2))
        self.setZValue(1)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptHoverEvents(True)

        if debt is not None:
            tip = f"Уч. {plot_num}"
            if owners:
                tip += "\n" + ", ".join(owners)
            if abs(debt) > 0.005:
                if debt > 0:
                    tip += f"\nДолг: {debt:,.2f} ₽".replace(",", " ")
                else:
                    tip += f"\nАванс: {abs(debt):,.2f} ₽".replace(",", " ")
            else:
                tip += "\nБез долга"
            self.setToolTip(tip)

        lbl = QGraphicsTextItem(plot_num, self)
        lbl.setDefaultTextColor(QColor("#ffffff"))
        f = QFont(); f.setPointSize(8); f.setBold(True)
        lbl.setFont(f)
        br = lbl.boundingRect()
        lbl.setPos(-br.width() / 2, -br.height() / 2)

    @staticmethod
    def _lighten(color: QColor) -> QColor:
        h, s, v, a = color.getHsv()
        return QColor.fromHsv(h, max(0, s - 40), min(255, v + 40), a)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click(self._plot_num, self._owners)
        super().mousePressEvent(event)

    def hoverEnterEvent(self, event):
        self.setBrush(self._hover_color)
        self.setPen(QPen(QColor("#ffffff"), 2))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.setBrush(self._base_color)
        self.setPen(QPen(self._lighten(self._base_color), 2))
        super().hoverLeaveEvent(event)


class _MapView(QGraphicsView):
    """QGraphicsView с зумом колесом мыши."""
    def __init__(self, scene, map_widget):
        super().__init__(scene)
        self._map_widget = map_widget
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setStyleSheet("background:#0f1923; border:none;")

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self._map_widget._placing_mode):
            pos = self.mapToScene(event.pos())
            self._map_widget._on_map_click(pos)
        else:
            super().mousePressEvent(event)


class MapWidget(QWidget):
    """Схема-карта участков: загрузи изображение, расставь участки кликом."""

    COORDS_FILE = os.path.join(DATA_DIR, "snt_map_plots.json")
    IMAGE_FILE  = os.path.join(DATA_DIR, "snt_map_image.json")

    def __init__(self):
        super().__init__()
        self._placing_mode = False
        self._image_path   = self._load_image_path()
        self._debts: dict = {}
        self._color_by_debt = True
        self._setup_ui()
        self.reload_map()

    # ── Персистентность ──────────────────────────────────────────────────

    def _load_image_path(self) -> str:
        try:
            if os.path.exists(self.IMAGE_FILE):
                with open(self.IMAGE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f).get("path", "")
        except Exception:
            pass
        return ""

    def _save_image_path(self, path: str):
        try:
            with open(self.IMAGE_FILE, "w", encoding="utf-8") as f:
                json.dump({"path": path}, f, ensure_ascii=False)
        except Exception:
            pass

    def _load_plot_coords(self) -> dict:
        """{"20": [x_px, y_px], ...} — пиксельные координаты на схеме."""
        try:
            if os.path.exists(self.COORDS_FILE):
                with open(self.COORDS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_plot_coords(self, coords: dict):
        try:
            with open(self.COORDS_FILE, "w", encoding="utf-8") as f:
                json.dump(coords, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_plots_owners(self) -> dict:
        try:
            if os.path.exists(os.path.join(DATA_DIR, "snt_plots.json")):
                with open(os.path.join(DATA_DIR, "snt_plots.json"), "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {str(p["num"]): p.get("owners", []) for p in data}
        except Exception:
            pass
        return {}

    # ── UI ───────────────────────────────────────────────────────────────

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QWidget()
        bar.setStyleSheet("background:#0d1b2a; border-bottom:1px solid #1e3a5f;")
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(20, 8, 20, 8)
        bar_lay.setSpacing(10)

        title = QLabel("Карта участков", objectName="pageTitle")
        bar_lay.addWidget(title)
        bar_lay.addStretch()

        self._hint_lbl = QLabel("")
        self._hint_lbl.setStyleSheet("color:#5a8ab0; font-size:12px;")
        bar_lay.addWidget(self._hint_lbl)

        btn_load = QPushButton("🖼  Загрузить схему")
        btn_load.setObjectName("btnSecondary")
        btn_load.clicked.connect(self._pick_image)
        bar_lay.addWidget(btn_load)

        self._btn_place = QPushButton("📍  Расставить участки")
        self._btn_place.setObjectName("btnSecondary")
        self._btn_place.setCheckable(True)
        self._btn_place.toggled.connect(self._toggle_place_mode)
        bar_lay.addWidget(self._btn_place)

        self._btn_color = QPushButton("🎨  По долгу")
        self._btn_color.setObjectName("btnSecondary")
        self._btn_color.setCheckable(True)
        self._btn_color.setChecked(True)
        self._btn_color.toggled.connect(self._toggle_color_mode)
        bar_lay.addWidget(self._btn_color)

        lay.addWidget(bar)

        legend = QWidget()
        legend.setStyleSheet("background:#0d1b2a;border-bottom:1px solid #1e3a5f;")
        legend_lay = QHBoxLayout(legend)
        legend_lay.setContentsMargins(20, 4, 20, 4)
        legend_lay.setSpacing(20)
        for color, text in [
            ("#2e7d32", "■  без долга / аванс"),
            ("#f9a825", "■  небольшой"),
            ("#ef6c00", "■  средний"),
            ("#c62828", "■  крупный"),
        ]:
            lb = QLabel(text)
            lb.setStyleSheet(f"color:{color};background:transparent;font-size:11px;")
            legend_lay.addWidget(lb)
        legend_lay.addStretch()
        lay.addWidget(legend)

        self._scene = QGraphicsScene()
        self._view  = _MapView(self._scene, self)
        self._view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        lay.addWidget(self._view, stretch=1)

        self._info = QLabel("Кликните на участок чтобы увидеть информацию")
        self._info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._info.setStyleSheet(
            "background:#0d1b2a; color:#cdd9e5; font-size:13px;"
            "padding:8px; border-top:1px solid #1e3a5f;"
        )
        lay.addWidget(self._info)

    # ── Логика карты ─────────────────────────────────────────────────────

    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите изображение схемы СНТ", "",
            "Изображения (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if path:
            self._image_path = path
            self._save_image_path(path)
            self.reload_map()

    def _toggle_color_mode(self, on: bool):
        self._color_by_debt = on
        self.reload_map()

    def _toggle_place_mode(self, on: bool):
        self._placing_mode = on
        if on:
            self._btn_place.setStyleSheet(
                "QPushButton{background:#b71c1c;color:white;border-radius:6px;padding:4px 12px;}"
            )
            self._hint_lbl.setText("Режим расстановки: кликните на схеме → выберите участок")
            self._view.setDragMode(QGraphicsView.DragMode.NoDrag)
            self._view.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._btn_place.setStyleSheet("")
            self._hint_lbl.setText("")
            self._view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self._view.setCursor(Qt.CursorShape.ArrowCursor)

    def _on_map_click(self, pos):
        """Вызывается из _MapView в режиме расстановки."""
        owners   = self._load_plots_owners()
        all_nums = sorted(
            owners.keys() or [str(i) for i in range(1, 51)],
            key=lambda x: (len(x), x)
        )
        num, ok = QInputDialog.getItem(
            self, "Выбор участка",
            "Какой участок разместить здесь?",
            all_nums, 0, False
        )
        if ok and num:
            coords = self._load_plot_coords()
            coords[num] = [pos.x(), pos.y()]
            self._save_plot_coords(coords)
            self.reload_map()

    def _on_plot_click(self, plot_num: str, owners: list):
        text = " · ".join(owners) if owners else "нет данных"
        self._info.setText(f"  Участок {plot_num}  —  {text}")

    def reload_map(self):
        self._scene.clear()

        if self._image_path and os.path.exists(self._image_path):
            px = QPixmap(self._image_path)
            if px.isNull():
                QMessageBox.warning(self, "Ошибка", "Не удалось загрузить изображение.")
                self._image_path = ""
                self.reload_map()
                return
            item = self._scene.addPixmap(px)
            self._scene.setSceneRect(QRectF(0, 0, px.width(), px.height()))
        else:
            w, h = 820, 520
            self._scene.setSceneRect(QRectF(0, 0, w, h))
            self._scene.addRect(
                QRectF(0, 0, w, h),
                QPen(Qt.PenStyle.NoPen),
                QColor("#0a1520")
            )
            t = self._scene.addText(
                "Загрузите схему карты СНТ\n\n"
                "Нажмите «🖼 Загрузить схему» и выберите скриншот или скан карты.\n"
                "Затем нажмите «📍 Расставить участки» и кликайте по нужным местам.",
                QFont("", 13)
            )
            t.setDefaultTextColor(QColor("#5a8ab0"))
            br = t.boundingRect()
            t.setPos((w - br.width()) / 2, (h - br.height()) / 2)

        coords = self._load_plot_coords()
        owners = self._load_plots_owners()
        debts = self._debts if self._color_by_debt else {}
        for plot_num, pos in coords.items():
            if len(pos) < 2:
                continue
            owner_list = owners.get(str(plot_num), [])
            info = debts.get(str(plot_num))
            color = info["color"] if info else None
            debt = info["debt"] if info else None
            marker = _PlotMarker(plot_num, owner_list, self._on_plot_click,
                                 color=color, debt=debt)
            marker.setPos(pos[0], pos[1])
            self._scene.addItem(marker)

    def set_debts(self, debts: dict):
        """Принимает {plot_num: {"debt": float, "color": "#..."}}; перерисовывает."""
        self._debts = debts or {}
        self.reload_map()
