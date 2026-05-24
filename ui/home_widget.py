"""Стартовая страница «Главная» — дашборд СНТ.

Все показатели — реальные, считаются модулем core.dashboard по выписке.
Источник данных: DataFrame с вкладки «Детализация» (через сигнал
dataLoaded), а при его отсутствии — файл data/detail_transactions.json.

Состав дашборда:
  1. Обзор СНТ — пять карточек-показателей.
  2. Сравнительный столбчатый график прихода и расхода по месяцам
     с тумблером сравнения с прошлым периодом.
  3. Две кольцевые диаграммы — структура прихода и структура расхода.
"""
from __future__ import annotations

import math

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea,
    QSizePolicy, QAbstractButton, QToolTip,
)
from PyQt6.QtCore import Qt, QPointF, QRectF, QSize
from PyQt6.QtGui import (
    QFont, QColor, QPainter, QPen, QBrush, QPainterPath,
)

from core import dashboard

# ── Material Icons (классические глифы) ────────────────────────────────
_IC_BALANCE  = chr(0xe84f)   # account_balance
_IC_COLLECT  = chr(0xe263)   # monetization_on
_IC_SPEND    = chr(0xe8a1)   # payment
_IC_ELECTRO  = chr(0xe3e7)   # flash_on
_IC_DEBT     = chr(0xe002)   # warning
_IC_BARS     = chr(0xe26b)   # bar_chart
_IC_DONUT    = chr(0xe917)   # donut_large

_SNT_NAME = "Заря"

# ── Цвета ──────────────────────────────────────────────────────────────
_C_INCOME  = "#2E9E5B"
_C_EXPENSE = "#E0524A"

# Стабильные цвета сегментов кольцевых диаграмм по категориям выписки.
_CAT_COLORS = {
    "Членские взносы":                   "#2F7D55",
    "Электроэнергия (от садоводов)":     "#3E7CB1",
    "Оплата электроэнергии (поставщик)": "#C25E5E",
    "Материалы и работы":                "#E0A23C",
    "Налоги и штрафы":                   "#7C6CC4",
    "Программное обеспечение":           "#4FA39A",
    "Банковские комиссии":               "#9AA84A",
    "Подотчётные суммы":                 "#C77FB5",
    "Возврат":                           "#8A93A5",
    "Прочее":                            "#A0A8B4",
}
_FALLBACK_PALETTE = ["#5B8DEF", "#E08A3C", "#56B98D", "#C56B9A",
                     "#9B7BD4", "#C9A23C", "#6FBF73", "#B36A9E"]

# Короткие подписи для легенды кольцевых диаграмм.
_CAT_SHORT = {
    "Оплата электроэнергии (поставщик)": "Электроэнергия поставщику",
    "Электроэнергия (от садоводов)":     "Электроэнергия (садоводы)",
}

_HOME_QSS = """
QScrollArea#homeScroll { background: #F0F3F9; border: none; }
QWidget#homeContent    { background: #F0F3F9; }

QLabel#cardSubtitle { color: #9AA3AE; background: transparent; font-size: 12px; }

QFrame#kpiCard {
    background: #F6F8FA; border: 1px solid #E6EAEF; border-radius: 11px;
}
QLabel#kpiCaption  { color: #6B7280; background: transparent; font-size: 12px; }
QLabel#kpiValue    {
    color: #1F2937; background: transparent; font-size: 16px; font-weight: 700;
}
QLabel#kpiSubtitle { color: #9AA3AE; background: transparent; font-size: 11px; }
QLabel#kpiTrend    { background: transparent; font-size: 11px; font-weight: 700; }

QLabel#toggleLabel { color: #4B5563; background: transparent; font-size: 12px; }
QLabel#legendText  { color: #6B7280; background: transparent; font-size: 12px; }

QLabel#sliceName   { color: #374151; background: transparent; font-size: 12px; }
QLabel#slicePct    {
    color: #1F2937; background: transparent; font-size: 12px; font-weight: 700;
}
QLabel#sliceValue  { color: #9AA3AE; background: transparent; font-size: 11px; }
QLabel#emptyHint   { color: #9AA3AE; background: transparent; font-size: 12px; }
"""


# ── Форматирование ─────────────────────────────────────────────────────

def _icon_font(px: int) -> QFont:
    f = QFont("Material Icons")
    f.setPixelSize(px)
    return f


def _money(v, kop: bool = False) -> str:
    """«406 690 ₽» / «−12 300,50 ₽»."""
    if v is None:
        return "—"
    v = float(v)
    neg = v < -0.005
    body = f"{abs(v):,.{2 if kop else 0}f}".replace(",", " ").replace(".", ",")
    return f"{'−' if neg else ''}{body} ₽"


def _short(v: float) -> str:
    """Компактная подпись оси: 25к, 1.2М."""
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:.1f}".rstrip("0").rstrip(".") + "М"
    if a >= 1000:
        return f"{v / 1000:.0f}к"
    return f"{v:.0f}"


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    """Русский выбор формы: forms = (1, 2-4, 5+)."""
    n = abs(n) % 100
    n1 = n % 10
    if 10 < n < 20:
        return forms[2]
    if 1 < n1 < 5:
        return forms[1]
    if n1 == 1:
        return forms[0]
    return forms[2]


def _nice_ceil(v: float) -> float:
    """Округляет вверх до «красивого» значения для оси Y."""
    if v <= 0:
        return 1000.0
    mag = 10 ** math.floor(math.log10(v))
    for k in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if k * mag >= v:
            return k * mag
    return 10 * mag


def _slice_color(name: str, idx: int) -> str:
    return _CAT_COLORS.get(name, _FALLBACK_PALETTE[idx % len(_FALLBACK_PALETTE)])


def _card(object_name: str = "dashCard") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(objectName=object_name)
    lyt = QVBoxLayout(frame)
    lyt.setContentsMargins(20, 18, 20, 18)
    lyt.setSpacing(12)
    return frame, lyt


# ── Тумблер ────────────────────────────────────────────────────────────

class _ToggleSwitch(QAbstractButton):
    """Компактный переключатель-«пилюля»."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 22)

    def sizeHint(self) -> QSize:
        return QSize(40, 22)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        enabled = self.isEnabled()

        if not enabled:
            track = QColor("#E1E5EB")
        elif on:
            track = QColor("#2F7D55")
        else:
            track = QColor("#CBD2DC")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(0, 0, self.width(), self.height(),
                          self.height() / 2, self.height() / 2)

        d = self.height() - 6
        x = (self.width() - d - 3) if on else 3
        p.setBrush(QColor("#FFFFFF") if enabled else QColor("#F4F6F8"))
        p.drawEllipse(int(x), 3, int(d), int(d))


# ── Карточка-показатель ────────────────────────────────────────────────

class _KpiCard(QFrame):
    """Карточка обзора: иконка с подписью, крупное значение,
    необязательная стрелка тренда и поясняющая строка снизу."""

    def __init__(self, icon: str, caption: str, accent: str, parent=None):
        super().__init__(parent)
        self.setObjectName("kpiCard")
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Fixed)

        lyt = QVBoxLayout(self)
        lyt.setContentsMargins(15, 12, 15, 12)
        lyt.setSpacing(7)

        top = QHBoxLayout()
        top.setSpacing(7)
        ic = QLabel(icon)
        ic.setFont(_icon_font(17))
        ic.setFixedWidth(19)
        ic.setStyleSheet(f"color:{accent}; background:transparent;")
        top.addWidget(ic, alignment=Qt.AlignmentFlag.AlignTop)
        caption_lbl = QLabel(caption, objectName="kpiCaption")
        caption_lbl.setWordWrap(True)
        caption_lbl.setFixedHeight(32)
        caption_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft
                                 | Qt.AlignmentFlag.AlignTop)
        top.addWidget(caption_lbl, stretch=1)
        lyt.addLayout(top)

        val_row = QHBoxLayout()
        val_row.setSpacing(6)
        self._value = QLabel("—", objectName="kpiValue")
        val_row.addWidget(self._value)
        self._trend = QLabel("", objectName="kpiTrend")
        self._trend.setVisible(False)
        val_row.addWidget(self._trend, alignment=Qt.AlignmentFlag.AlignVCenter)
        val_row.addStretch()
        lyt.addLayout(val_row)

        self._subtitle = QLabel("", objectName="kpiSubtitle")
        self._subtitle.setWordWrap(True)
        lyt.addWidget(self._subtitle)
        lyt.addStretch()

    def set(self, value: str, subtitle: str = "",
            trend: tuple | None = None):
        self._value.setText(value)
        self._subtitle.setText(subtitle)
        self._subtitle.setVisible(bool(subtitle))

        if trend is None or trend[0] is None:
            self._trend.setVisible(False)
            return
        pct, good_when_up = trend
        up = pct >= 0
        arrow = "▲" if up else "▼"
        good = (up == good_when_up)
        color = _C_INCOME if good else _C_EXPENSE
        self._trend.setText(f"{arrow} {abs(pct):.0f}%")
        self._trend.setStyleSheet(f"color: {color}; background: transparent;")
        self._trend.setToolTip("По сравнению с тем же отрезком прошлого периода")
        self._trend.setVisible(True)

    # Свои подсказки размера: пять карточек должны помещаться в один ряд
    # и сжиматься на узких окнах — переносы подписи делают это безопасным.
    def minimumSizeHint(self) -> QSize:
        return QSize(150, 116)

    def sizeHint(self) -> QSize:
        return QSize(210, 116)


# ── Столбчатый график приход/расход ───────────────────────────────────

class _BarChart(QWidget):
    """Сгруппированные столбцы прихода и расхода по месяцам.
    При включённом сравнении поверх накладываются пунктирные линии
    показателей прошлого периода."""

    _PAD_L, _PAD_R, _PAD_T, _PAD_B = 58, 16, 16, 32

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(264)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self._months: list = []
        self._months_prev: list = []
        self._compare = False

    def set_data(self, months: list, months_prev: list):
        self._months = list(months or [])
        self._months_prev = list(months_prev or [])
        self.update()

    def set_compare(self, on: bool):
        self._compare = bool(on)
        self.update()

    def has_prev(self) -> bool:
        return any((m.income or m.expense) for m in self._months_prev)

    # ── геометрия ──────────────────────────────────────────────────────
    def _plot_rect(self) -> QRectF:
        return QRectF(
            self._PAD_L, self._PAD_T,
            max(1.0, self.width() - self._PAD_L - self._PAD_R),
            max(1.0, self.height() - self._PAD_T - self._PAD_B),
        )

    def _y_max(self) -> float:
        vals: list[float] = []
        for m in self._months:
            vals += [m.income, m.expense]
        if self._compare:
            for m in self._months_prev:
                vals += [m.income, m.expense]
        peak = max(vals) if vals else 0.0
        return _nice_ceil(peak) if peak > 0 else 1000.0

    @staticmethod
    def _bar_path(x: float, y: float, w: float, h: float, r: float) -> QPainterPath:
        r = max(0.0, min(r, w / 2, h))
        path = QPainterPath()
        path.moveTo(x, y + h)
        path.lineTo(x, y + r)
        path.quadTo(x, y, x + r, y)
        path.lineTo(x + w - r, y)
        path.quadTo(x + w, y, x + w, y + r)
        path.lineTo(x + w, y + h)
        path.closeSubpath()
        return path

    # ── отрисовка ──────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        plot = self._plot_rect()
        n = len(self._months)
        y_max = self._y_max()

        # сетка + подписи оси Y
        p.setFont(QFont("Segoe UI", 8))
        steps = 4
        for s in range(steps + 1):
            val = y_max * s / steps
            y = plot.bottom() - plot.height() * s / steps
            p.setPen(QPen(QColor("#EBEEF2"), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QPen(QColor("#9AA3AE"), 1))
            p.drawText(QRectF(0, y - 9, self._PAD_L - 10, 18),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       _short(val))

        if n == 0:
            return

        slot_w = plot.width() / n
        bar_w = min(slot_w * 0.30, 26.0)
        gap = slot_w * 0.06

        def y_of(v: float) -> float:
            return plot.bottom() - plot.height() * max(0.0, v) / y_max

        # столбцы текущего периода
        for i, m in enumerate(self._months):
            cx = plot.left() + slot_w * (i + 0.5)
            for value, color, dx in (
                (m.income, QColor(_C_INCOME), -(gap / 2 + bar_w)),
                (m.expense, QColor(_C_EXPENSE), gap / 2),
            ):
                if value <= 0:
                    continue
                top = y_of(value)
                h = plot.bottom() - top
                p.fillPath(self._bar_path(cx + dx, top, bar_w, h, 3),
                           QBrush(color))

            p.setPen(QPen(QColor("#8A93A0"), 1))
            p.setFont(QFont("Segoe UI", 8))
            p.drawText(QRectF(cx - slot_w / 2, plot.bottom() + 6, slot_w, 16),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                       m.label)

        # пунктир прошлого периода
        if self._compare and self._months_prev:
            for attr, color in (("income", QColor(_C_INCOME)),
                                 ("expense", QColor(_C_EXPENSE))):
                pts = []
                for i, m in enumerate(self._months_prev[:n]):
                    cx = plot.left() + slot_w * (i + 0.5)
                    pts.append(QPointF(cx, y_of(getattr(m, attr))))
                if len(pts) < 2:
                    continue
                line = QColor(color)
                line.setAlpha(200)
                pen = QPen(line, 2.0, Qt.PenStyle.DashLine,
                           Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                path = QPainterPath(pts[0])
                for pt in pts[1:]:
                    path.lineTo(pt)
                p.strokePath(path, pen)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(color))
                for pt in pts:
                    p.drawEllipse(pt, 2.6, 2.6)

    # ── подсказка ──────────────────────────────────────────────────────
    def mouseMoveEvent(self, event):
        n = len(self._months)
        if n == 0:
            return
        plot = self._plot_rect()
        x = event.position().x()
        if not (plot.left() <= x <= plot.right()):
            QToolTip.hideText()
            return
        idx = int((x - plot.left()) / (plot.width() / n))
        idx = max(0, min(n - 1, idx))
        m = self._months[idx]
        txt = (f"<b>{m.label}</b><br>"
               f"<span style='color:{_C_INCOME}'>Приход:</span> {_money(m.income)}<br>"
               f"<span style='color:{_C_EXPENSE}'>Расход:</span> {_money(m.expense)}")
        if self._compare and idx < len(self._months_prev):
            mp = self._months_prev[idx]
            txt += (f"<br><i>Прошлый период:</i><br>"
                    f"&nbsp;Приход: {_money(mp.income)}<br>"
                    f"&nbsp;Расход: {_money(mp.expense)}")
        QToolTip.showText(event.globalPosition().toPoint(), txt, self)
        super().mouseMoveEvent(event)


# ── Кольцевая диаграмма ────────────────────────────────────────────────

class _DoughnutRing(QWidget):
    """Кольцо структуры потока + сумма в центре."""

    _THICK = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(162, 162)
        self._slices: list = []      # [(name, amount, color)]
        self._total = 0.0
        self._caption = ""

    def set_data(self, slices: list, caption: str):
        self._slices = list(slices or [])
        self._total = sum(a for _, a, _ in self._slices)
        self._caption = caption
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        side = min(self.width(), self.height())
        inset = self._THICK / 2 + 3
        rect = QRectF(inset, inset, side - 2 * inset, side - 2 * inset)

        if self._total <= 0:
            pen = QPen(QColor("#EBEEF2"), self._THICK)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(pen)
            p.drawArc(rect, 0, 360 * 16)
            p.setPen(QColor("#9AA3AE"))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "нет данных")
            return

        start = 90 * 16
        for _name, amount, color in self._slices:
            span = -int(round(amount / self._total * 360 * 16))
            pen = QPen(QColor(color), self._THICK)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(pen)
            p.drawArc(rect, start, span)
            start += span

        # сумма в центре
        p.setPen(QColor("#1F2937"))
        f = QFont("Segoe UI", 13)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, side / 2 - 18, side, 22),
                   Qt.AlignmentFlag.AlignCenter, _short(self._total) + " ₽")
        p.setPen(QColor("#9AA3AE"))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(QRectF(0, side / 2 + 2, side, 16),
                   Qt.AlignmentFlag.AlignCenter, self._caption)


class _LegendRow(QWidget):
    """Строка легенды: метка-цвет, название и процент, ниже — сумма."""

    def __init__(self, name: str, amount: float, color: str,
                 total: float, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(1)

        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)

        dot = QLabel()
        dot.setFixedSize(11, 11)
        dot.setStyleSheet(f"background:{color}; border-radius:3px;")
        line.addWidget(dot, alignment=Qt.AlignmentFlag.AlignVCenter)

        name_lbl = QLabel(_CAT_SHORT.get(name, name), objectName="sliceName")
        name_lbl.setToolTip(name)
        line.addWidget(name_lbl, stretch=1)

        pct = amount / total * 100.0 if total else 0.0
        pct_lbl = QLabel(f"{pct:.0f}%", objectName="slicePct")
        pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight
                             | Qt.AlignmentFlag.AlignVCenter)
        line.addWidget(pct_lbl)
        outer.addLayout(line)

        val_lbl = QLabel(_money(amount), objectName="sliceValue")
        val_lbl.setContentsMargins(19, 0, 0, 0)
        outer.addWidget(val_lbl)


class _DoughnutCard(QFrame):
    """Карточка структуры потока: заголовок, кольцо и легенда."""

    def __init__(self, icon: str, title: str, caption: str, parent=None):
        super().__init__(parent)
        self.setObjectName("dashCard")
        self._caption = caption

        lyt = QVBoxLayout(self)
        lyt.setContentsMargins(20, 18, 20, 18)
        lyt.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        ic = QLabel(icon)
        ic.setFont(_icon_font(20))
        ic.setStyleSheet("color:#57A05C; background:transparent;")
        head.addWidget(ic)
        head.addWidget(QLabel(title, objectName="cardTitle"))
        head.addStretch()
        lyt.addLayout(head)

        self._subtitle = QLabel("", objectName="cardSubtitle")
        lyt.addWidget(self._subtitle)
        lyt.addSpacing(8)

        body = QHBoxLayout()
        body.setSpacing(16)
        self._ring = _DoughnutRing()
        body.addWidget(self._ring, alignment=Qt.AlignmentFlag.AlignTop)

        self._legend = QVBoxLayout()
        self._legend.setSpacing(10)
        legend_box = QWidget()
        legend_box.setStyleSheet("background: transparent;")
        legend_box.setLayout(self._legend)
        body.addWidget(legend_box, stretch=1)

        lyt.addLayout(body)
        lyt.addStretch()

    def _clear_legend(self):
        while self._legend.count():
            item = self._legend.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def set_slices(self, slices: list, period_label: str = ""):
        self._subtitle.setText(
            f"{self._caption} · период {period_label}" if period_label
            else self._caption)

        colored = [(s.name, s.amount, _slice_color(s.name, i))
                   for i, s in enumerate(slices)]
        self._ring.set_data(colored, "всего")

        self._clear_legend()
        total = sum(a for _, a, _ in colored) or 1.0
        if not colored:
            self._legend.addWidget(QLabel("Нет операций за период",
                                          objectName="emptyHint"))
            return
        for name, amount, color in colored:
            self._legend.addWidget(_LegendRow(name, amount, color, total))
        self._legend.addStretch()

    # Подсказки размера: две карточки делят ряд поровну и сжимаются на
    # узких окнах; меньший минимум не даёт дашборду расползтись по ширине.
    def minimumSizeHint(self) -> QSize:
        return QSize(432, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:
        return QSize(540, super().sizeHint().height())


# ── Дашборд ────────────────────────────────────────────────────────────

class HomeWidget(QWidget):
    """Дашборд «Главная»."""

    def __init__(self):
        super().__init__()
        self.setAutoFillBackground(True)
        self.setStyleSheet(_HOME_QSS)
        self._data = None
        self._setup_ui()
        self.refresh(None)

    # ── построение интерфейса ──────────────────────────────────────────
    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(objectName="homeScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget(objectName="homeContent")
        content.setAutoFillBackground(True)
        scroll.setWidget(content)

        lyt = QVBoxLayout(content)
        lyt.setContentsMargins(28, 24, 28, 24)
        lyt.setSpacing(18)

        lyt.addWidget(self._overview_card())
        lyt.addWidget(self._chart_card())
        lyt.addLayout(self._doughnuts_row())
        lyt.addStretch()

    def _overview_card(self) -> QFrame:
        frame, lyt = _card()
        lyt.addWidget(QLabel(f'Обзор СНТ «{_SNT_NAME}»',
                             objectName="cardTitleGreen"))

        self._kpi_balance = _KpiCard(_IC_BALANCE, "Текущий баланс", "#2F7D55")
        self._kpi_collected = _KpiCard(_IC_COLLECT, "Собрано средств", "#2E9E5B")
        self._kpi_spent = _KpiCard(_IC_SPEND, "Потрачено средств", "#E0A23C")
        self._kpi_electro = _KpiCard(_IC_ELECTRO, "Потрачено на электричество",
                                     "#3E7CB1")
        self._kpi_debt = _KpiCard(_IC_DEBT, "Задолженность по взносам", "#C25E5E")

        row = QHBoxLayout()
        row.setSpacing(11)
        for card in (self._kpi_balance, self._kpi_collected, self._kpi_spent,
                     self._kpi_electro, self._kpi_debt):
            row.addWidget(card, stretch=1)
        lyt.addLayout(row)
        return frame

    def _chart_card(self) -> QFrame:
        frame, lyt = _card()

        header = QHBoxLayout()
        header.setSpacing(8)
        ic = QLabel(_IC_BARS)
        ic.setFont(_icon_font(20))
        ic.setStyleSheet("color:#57A05C; background:transparent;")
        header.addWidget(ic)
        header.addWidget(QLabel("Приход и расход по месяцам",
                                objectName="cardTitle"))
        header.addStretch()

        self._toggle_label = QLabel("Сравнить с прошлым периодом",
                                    objectName="toggleLabel")
        header.addWidget(self._toggle_label)
        self._toggle = _ToggleSwitch()
        self._toggle.toggled.connect(self._on_toggle)
        header.addWidget(self._toggle)
        lyt.addLayout(header)

        self._chart_period = QLabel("", objectName="cardSubtitle")
        lyt.addWidget(self._chart_period)

        self._chart = _BarChart()
        lyt.addWidget(self._chart, stretch=1)

        lyt.addLayout(self._chart_legend())
        return frame

    def _chart_legend(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addStretch()
        for color, text, dashed in (
            (_C_INCOME, "Приход", False),
            (_C_EXPENSE, "Расход", False),
            ("#9AA3AE", "Прошлый период", True),
        ):
            row.addLayout(self._legend_chip(color, text, dashed))
        row.addStretch()
        return row

    @staticmethod
    def _legend_chip(color: str, text: str, dashed: bool) -> QHBoxLayout:
        chip = QHBoxLayout()
        chip.setSpacing(7)
        mark = QLabel()
        if dashed:
            mark.setFixedSize(18, 12)
            mark.setStyleSheet(
                f"background: transparent; border: none;"
                f"border-top: 2px dashed {color};"
            )
        else:
            mark.setFixedSize(13, 13)
            mark.setStyleSheet(f"background:{color}; border-radius:3px;")
        chip.addWidget(mark, alignment=Qt.AlignmentFlag.AlignVCenter)
        chip.addWidget(QLabel(text, objectName="legendText"))
        return chip

    def _doughnuts_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(18)
        self._income_dn = _DoughnutCard(_IC_DONUT, "Структура прихода",
                                        "Откуда приходят средства")
        self._expense_dn = _DoughnutCard(_IC_DONUT, "Структура расхода",
                                         "На что расходуются средства")
        row.addWidget(self._income_dn, stretch=1)
        row.addWidget(self._expense_dn, stretch=1)
        return row

    # ── данные ─────────────────────────────────────────────────────────
    def refresh(self, df=None):
        """Пересчитывает дашборд. df — выписка с вкладки «Детализация»;
        если None — берётся data/detail_transactions.json."""
        try:
            src = df if df is not None else dashboard.load_transactions_df()
            data = dashboard.build(src)
        except Exception:
            data = None
        self._apply(data)

    def _apply(self, data):
        self._data = data

        if data is None:
            for card in (self._kpi_balance, self._kpi_collected,
                         self._kpi_spent, self._kpi_electro, self._kpi_debt):
                card.set("—")
            self._chart.set_data([], [])
            self._chart_period.setText("Нет данных")
            self._toggle.setChecked(False)
            self._toggle.setEnabled(False)
            self._income_dn.set_slices([])
            self._expense_dn.set_slices([])
            return

        period_label = data.current.label if data.current else "—"

        if data.current:
            self._chart_period.setText(
                f"Период членских взносов {data.current.label}: "
                f"{data.current.date_from:%d.%m.%Y} — "
                f"{data.current.date_to:%d.%m.%Y}"
            )
        else:
            self._chart_period.setText("Периоды членских взносов не заданы")

        # карточки обзора
        self._kpi_balance.set(_money(data.balance), subtitle="за всё время")
        self._kpi_collected.set(
            _money(data.collected),
            subtitle=f"за период {period_label}",
            trend=(data.collected_trend, True))
        self._kpi_spent.set(
            _money(data.spent),
            subtitle=f"за период {period_label}",
            trend=(data.spent_trend, False))
        self._kpi_electro.set(
            _money(data.electricity),
            subtitle=f"за период {period_label}",
            trend=(data.electricity_trend, False))

        d = data.debt
        if d.debtor_count:
            debt_sub = (f"{d.debtor_count} "
                        f"{_plural(d.debtor_count, ('должник', 'должника', 'должников'))}"
                        f" из {d.plot_count}")
        else:
            debt_sub = "должников нет"
        self._kpi_debt.set(_money(d.total_debt), subtitle=debt_sub)

        # график
        self._chart.set_data(data.months, data.months_prev)
        has_prev = self._chart.has_prev()
        self._toggle.setEnabled(has_prev)
        if not has_prev:
            self._toggle.setChecked(False)
            self._toggle_label.setToolTip("Нет данных за прошлый период")
        else:
            self._toggle_label.setToolTip("")
        self._chart.set_compare(self._toggle.isChecked())

        # кольцевые диаграммы
        self._income_dn.set_slices(data.income_slices, period_label)
        self._expense_dn.set_slices(data.expense_slices, period_label)

    def _on_toggle(self, checked: bool):
        self._chart.set_compare(checked)
