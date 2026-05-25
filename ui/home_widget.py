"""Стартовая страница «Главная» — дашборд СНТ.

Все показатели — реальные, считаются модулем core.dashboard по выписке.
Источник данных: DataFrame с вкладки «Детализация» (через сигнал
dataLoaded), а при его отсутствии — файл data/detail_transactions.json.

Состав дашборда:
  1. Обзор СНТ — пять карточек-показателей + выбор периода.
  2. Динамика приходов — сгруппированная накопительная гистограмма
     с разбивкой по категориям, выбранный и сравниваемый периоды рядом.
  3. Динамика расходов — аналогично, для расходных операций.
  4. Две кольцевые диаграммы — структура прихода и структура расхода.
"""
from __future__ import annotations

import math
from datetime import date

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea,
    QSizePolicy, QToolTip, QComboBox,
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

# Стабильные цвета сегментов по категориям выписки.
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

# Короткие подписи для легенд.
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

QLabel#periodLabel { color: #4B5563; background: transparent; font-size: 12px; }
QLabel#legendText  { color: #6B7280; background: transparent; font-size: 12px; }

QLabel#sliceName   { color: #374151; background: transparent; font-size: 12px; }
QLabel#slicePct    {
    color: #1F2937; background: transparent; font-size: 12px; font-weight: 700;
}
QLabel#sliceValue  { color: #9AA3AE; background: transparent; font-size: 11px; }
QLabel#emptyHint   { color: #9AA3AE; background: transparent; font-size: 12px; }

QComboBox#periodCombo {
    border: 1px solid #D1D5DB; border-radius: 6px;
    padding: 3px 8px; font-size: 12px; color: #374151;
    background: #FFFFFF;
    min-width: 110px;
}
QComboBox#periodCombo::drop-down { border: none; }
QComboBox#periodCombo QAbstractItemView {
    border: 1px solid #D1D5DB; border-radius: 6px; font-size: 12px;
}
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


def _bar_path(x: float, y: float, w: float, h: float, r: float) -> QPainterPath:
    """Прямоугольник со скруглёнными верхними углами."""
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
        self._trend.setToolTip("По сравнению с тем же отрезком периода сравнения")
        self._trend.setVisible(True)

    def minimumSizeHint(self) -> QSize:
        return QSize(150, 116)

    def sizeHint(self) -> QSize:
        return QSize(210, 116)


# ── Сгруппированная накопительная гистограмма ─────────────────────────

class _GroupedStackedBarChart(QWidget):
    """Для каждого месяца рисуются два рядом стоящих стека:
    - левый  — выбранный период (полная непрозрачность),
    - правый — период сравнения (пониженная непрозрачность).
    Каждый стек разбит на цветовые слои по категориям операций."""

    _PAD_L, _PAD_R, _PAD_T, _PAD_B = 58, 16, 16, 36

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self._months: list = []        # list[MonthCategoryBar] — выбранный период
        self._months_prev: list = []   # list[MonthCategoryBar] — период сравнения
        self._cur_label = ""
        self._prev_label = ""

    def set_data(self, months: list, months_prev: list,
                 cur_label: str = "", prev_label: str = ""):
        self._months = list(months or [])
        self._months_prev = list(months_prev or [])
        self._cur_label = cur_label
        self._prev_label = prev_label
        self.update()

    def has_prev(self) -> bool:
        return bool(self._months_prev) and any(
            m.total > 0 for m in self._months_prev)

    def unique_categories(self) -> list[tuple[str, str]]:
        """Уникальные (имя, цвет) в порядке первого появления."""
        seen: dict[str, str] = {}
        idx = 0
        for src in (self._months, self._months_prev):
            for mb in src:
                for cat, _ in mb.categories:
                    if cat not in seen:
                        seen[cat] = _slice_color(cat, idx)
                        idx += 1
        return list(seen.items())

    # ── геометрия ──────────────────────────────────────────────────────
    def _plot_rect(self) -> QRectF:
        return QRectF(
            self._PAD_L, self._PAD_T,
            max(1.0, self.width() - self._PAD_L - self._PAD_R),
            max(1.0, self.height() - self._PAD_T - self._PAD_B),
        )

    def _y_max(self) -> float:
        vals = [m.total for m in self._months]
        if self.has_prev():
            vals += [m.total for m in self._months_prev]
        peak = max(vals) if vals else 0.0
        return _nice_ceil(peak) if peak > 0 else 1000.0

    # ── отрисовка ──────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        plot = self._plot_rect()
        n = len(self._months)
        y_max = self._y_max()

        # Сетка + подписи оси Y
        p.setFont(QFont("Segoe UI", 8))
        for s in range(5):
            val = y_max * s / 4
            y = plot.bottom() - plot.height() * s / 4
            p.setPen(QPen(QColor("#EBEEF2"), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QPen(QColor("#9AA3AE"), 1))
            p.drawText(
                QRectF(0, y - 9, self._PAD_L - 10, 18),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                _short(val))

        if n == 0:
            return

        has_prev = self.has_prev()
        slot_w = plot.width() / n

        if has_prev:
            bar_w = min(slot_w * 0.29, 20.0)
            inner_gap = max(slot_w * 0.04, 3.0)
        else:
            bar_w = min(slot_w * 0.40, 28.0)
            inner_gap = 0.0

        def draw_stack(x0: float, month_bar, alpha: int = 230):
            y_bottom = plot.bottom()
            for i, (cat, amount) in enumerate(month_bar.categories):
                if amount <= 0:
                    continue
                h = plot.height() * amount / y_max
                if h < 0.5:
                    y_bottom -= h
                    continue
                y_top = y_bottom - h
                c = QColor(_slice_color(cat, i))
                c.setAlpha(alpha)
                p.fillPath(_bar_path(x0, y_top, bar_w, h, 3), QBrush(c))
                y_bottom = y_top

        for i, m in enumerate(self._months):
            cx = plot.left() + slot_w * (i + 0.5)

            if has_prev:
                x_cur = cx - inner_gap / 2 - bar_w
                x_prv = cx + inner_gap / 2
            else:
                x_cur = cx - bar_w / 2
                x_prv = None

            draw_stack(x_cur, m, 225)
            if has_prev and i < len(self._months_prev):
                draw_stack(x_prv, self._months_prev[i], 120)

            # Подпись месяца
            p.setPen(QPen(QColor("#8A93A0"), 1))
            p.setFont(QFont("Segoe UI", 8))
            p.drawText(
                QRectF(cx - slot_w / 2, plot.bottom() + 6, slot_w, 20),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                m.label)

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
        idx = max(0, min(n - 1, int((x - plot.left()) / (plot.width() / n))))
        m = self._months[idx]

        def _fmt_stack(mb, label: str) -> str:
            if not mb.categories:
                return f"<i>{label}: нет данных</i>"
            lines = [f"<u>{label}</u>"]
            for i, (cat, amt) in enumerate(mb.categories):
                c = _slice_color(cat, i)
                name = _CAT_SHORT.get(cat, cat)
                lines.append(
                    f"&nbsp;<span style='color:{c}'>■</span>"
                    f" {name}: {_money(amt)}")
            lines.append(f"&nbsp;<b>Итого: {_money(mb.total)}</b>")
            return "<br>".join(lines)

        parts = [f"<b>{m.label}</b><br>",
                 _fmt_stack(m, self._cur_label or "Выбранный период")]
        if self.has_prev() and idx < len(self._months_prev):
            parts.append(
                "<br>" + _fmt_stack(self._months_prev[idx],
                                    self._prev_label or "Период сравнения"))

        QToolTip.showText(event.globalPosition().toPoint(),
                          "".join(parts), self)
        super().mouseMoveEvent(event)


# ── Кольцевая диаграмма ────────────────────────────────────────────────

class _DoughnutRing(QWidget):
    """Кольцо структуры потока + сумма в центре."""

    _THICK = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(162, 162)
        self._slices: list = []
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
        self._df = None          # сохранённый DataFrame для смены периода
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
        lyt.addWidget(self._income_chart_card())
        lyt.addWidget(self._expense_chart_card())
        lyt.addLayout(self._doughnuts_row())
        lyt.addStretch()

    def _overview_card(self) -> QFrame:
        frame, lyt = _card()

        # Заголовок + выбор периода
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(QLabel(f'Обзор СНТ «{_SNT_NAME}»',
                                objectName="cardTitleGreen"))
        header.addStretch()
        header.addWidget(QLabel("Период:", objectName="periodLabel"))
        self._period_combo = QComboBox(objectName="periodCombo")
        self._period_combo.currentIndexChanged.connect(self._on_period_changed)
        header.addWidget(self._period_combo)
        lyt.addLayout(header)

        self._kpi_balance = _KpiCard(_IC_BALANCE,
                                     "Остаток на конец периода", "#2F7D55")
        self._kpi_collected = _KpiCard(_IC_COLLECT, "Собрано средств", "#2E9E5B")
        self._kpi_spent = _KpiCard(_IC_SPEND, "Потрачено средств", "#E0A23C")
        self._kpi_electro = _KpiCard(_IC_ELECTRO,
                                     "Потрачено на электричество", "#3E7CB1")
        self._kpi_debt = _KpiCard(_IC_DEBT,
                                  "Задолженность по взносам", "#C25E5E")

        row = QHBoxLayout()
        row.setSpacing(11)
        for card in (self._kpi_balance, self._kpi_collected, self._kpi_spent,
                     self._kpi_electro, self._kpi_debt):
            row.addWidget(card, stretch=1)
        lyt.addLayout(row)
        return frame

    def _income_chart_card(self) -> QFrame:
        frame, lyt = _card()

        header = QHBoxLayout()
        header.setSpacing(8)
        ic = QLabel(_IC_BARS)
        ic.setFont(_icon_font(20))
        ic.setStyleSheet("color:#2E9E5B; background:transparent;")
        header.addWidget(ic)
        header.addWidget(QLabel("Динамика приходов", objectName="cardTitle"))
        header.addStretch()
        lyt.addLayout(header)

        self._income_period_lbl = QLabel("", objectName="cardSubtitle")
        lyt.addWidget(self._income_period_lbl)

        self._income_chart = _GroupedStackedBarChart()
        lyt.addWidget(self._income_chart, stretch=1)

        # Легенда (контейнер, перестраивается в _apply)
        self._income_legend_box = QWidget()
        self._income_legend_box.setStyleSheet("background: transparent;")
        self._income_legend_box.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lyt.addWidget(self._income_legend_box)
        return frame

    def _expense_chart_card(self) -> QFrame:
        frame, lyt = _card()

        header = QHBoxLayout()
        header.setSpacing(8)
        ic = QLabel(_IC_BARS)
        ic.setFont(_icon_font(20))
        ic.setStyleSheet("color:#E0524A; background:transparent;")
        header.addWidget(ic)
        header.addWidget(QLabel("Динамика расходов", objectName="cardTitle"))
        header.addStretch()
        lyt.addLayout(header)

        self._expense_period_lbl = QLabel("", objectName="cardSubtitle")
        lyt.addWidget(self._expense_period_lbl)

        self._expense_chart = _GroupedStackedBarChart()
        lyt.addWidget(self._expense_chart, stretch=1)

        self._expense_legend_box = QWidget()
        self._expense_legend_box.setStyleSheet("background: transparent;")
        self._expense_legend_box.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lyt.addWidget(self._expense_legend_box)
        return frame

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

    # ── легенда гистограммы ────────────────────────────────────────────

    @staticmethod
    def _rebuild_chart_legend(box: QWidget,
                               categories: list[tuple[str, str]],
                               cur_label: str, prev_label: str,
                               has_prev: bool):
        """Заполняет box горизонтальной легендой: индикаторы периодов + категории."""
        # Удаляем старые дочерние виджеты
        for child in box.findChildren(QWidget):
            child.setParent(None)
            child.deleteLater()
        old_lyt = box.layout()
        if old_lyt is not None:
            while old_lyt.count():
                old_lyt.takeAt(0)
        else:
            old_lyt = QHBoxLayout(box)

        lyt = old_lyt
        lyt.setContentsMargins(0, 4, 0, 0)
        lyt.setSpacing(14)

        # Индикаторы периодов
        if has_prev and (cur_label or prev_label):
            for label, style in (
                (cur_label, "background:#4B5563; border-radius:2px;"),
                (prev_label, "background:#9CA3AF; border-radius:2px;"),
            ):
                row = QHBoxLayout()
                row.setSpacing(5)
                dot = QLabel()
                dot.setFixedSize(10, 10)
                dot.setStyleSheet(style)
                row.addWidget(dot, alignment=Qt.AlignmentFlag.AlignVCenter)
                lbl = QLabel(label, objectName="legendText")
                row.addWidget(lbl)
                lyt.addLayout(row)

            sep = QFrame()
            sep.setFixedSize(1, 14)
            sep.setStyleSheet("background: #D1D5DB;")
            lyt.addWidget(sep, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Цветовые чипы категорий
        for cat_name, color in categories[:8]:
            row = QHBoxLayout()
            row.setSpacing(5)
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background:{color}; border-radius:2px;")
            row.addWidget(dot, alignment=Qt.AlignmentFlag.AlignVCenter)
            lbl = QLabel(_CAT_SHORT.get(cat_name, cat_name),
                         objectName="legendText")
            row.addWidget(lbl)
            lyt.addLayout(row)

        lyt.addStretch()

    # ── данные ─────────────────────────────────────────────────────────
    def refresh(self, df=None):
        """Пересчитывает дашборд. df — выписка с вкладки «Детализация»;
        если None — берётся data/detail_transactions.json."""
        try:
            src = df if df is not None else dashboard.load_transactions_df()
            self._df = src
            data = dashboard.build(src)
        except Exception:
            data = None
            self._df = None
        self._apply(data, repopulate_combo=True)

    def _apply(self, data, repopulate_combo: bool = True):
        self._data = data

        # ── нет данных ─────────────────────────────────────────────────
        if data is None:
            for card in (self._kpi_balance, self._kpi_collected,
                         self._kpi_spent, self._kpi_electro, self._kpi_debt):
                card.set("—")
            self._income_chart.set_data([], [])
            self._expense_chart.set_data([], [])
            self._income_period_lbl.setText("Нет данных")
            self._expense_period_lbl.setText("")
            self._income_dn.set_slices([])
            self._expense_dn.set_slices([])
            if repopulate_combo:
                self._period_combo.blockSignals(True)
                self._period_combo.clear()
                self._period_combo.blockSignals(False)
            return

        # ── заполняем комбобокс периодов ───────────────────────────────
        if repopulate_combo and data.all_periods:
            today = date.today()
            self._period_combo.blockSignals(True)
            self._period_combo.clear()
            for p in reversed(data.all_periods):
                is_cur = p.date_from <= today <= p.date_to
                lbl = f"{p.label} (текущий)" if is_cur else p.label
                self._period_combo.addItem(lbl)
            # Позиция в комбобоксе: 0 = самый новый период
            combo_idx = len(data.all_periods) - 1 - data.selected_period_idx
            self._period_combo.setCurrentIndex(max(0, combo_idx))
            self._period_combo.blockSignals(False)

        cur = data.current
        prev = data.previous
        period_label = cur.label if cur else "—"
        prev_label = prev.label if prev else ""
        has_prev = prev is not None

        # ── карточки обзора ────────────────────────────────────────────
        if cur:
            balance_sub = (f"с начала данных по "
                           f"{min(date.today(), cur.date_to):%d.%m.%Y}")
        else:
            balance_sub = ""

        self._kpi_balance.set(_money(data.balance), subtitle=balance_sub)
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

        # ── подпись периода в заголовке графиков ──────────────────────
        if cur:
            if has_prev:
                period_hint = (f"Период {period_label} "
                               f"(левые столбцы) · Сравнение: {prev_label} "
                               f"(правые столбцы, полупрозрачные)")
            else:
                period_hint = (f"Период {period_label}: "
                               f"{cur.date_from:%d.%m.%Y} — "
                               f"{cur.date_to:%d.%m.%Y}")
        else:
            period_hint = "Периоды членских взносов не заданы"

        self._income_period_lbl.setText(period_hint)
        self._expense_period_lbl.setText(period_hint)

        # ── графики ────────────────────────────────────────────────────
        self._income_chart.set_data(
            data.months_cat, data.months_cat_prev,
            cur_label=period_label, prev_label=prev_label)
        self._expense_chart.set_data(
            data.months_cat_exp, data.months_cat_exp_prev,
            cur_label=period_label, prev_label=prev_label)

        # Легенды
        inc_cats = self._income_chart.unique_categories()
        exp_cats = self._expense_chart.unique_categories()

        self._rebuild_chart_legend(
            self._income_legend_box, inc_cats,
            period_label, prev_label, has_prev)
        self._rebuild_chart_legend(
            self._expense_legend_box, exp_cats,
            period_label, prev_label, has_prev)

        # ── кольцевые диаграммы ────────────────────────────────────────
        self._income_dn.set_slices(data.income_slices, period_label)
        self._expense_dn.set_slices(data.expense_slices, period_label)

    # ── смена периода ──────────────────────────────────────────────────
    def _on_period_changed(self, combo_idx: int):
        """Пересчитывает дашборд при выборе другого периода в комбобоксе."""
        if self._data is None or not self._data.all_periods:
            return
        n = len(self._data.all_periods)
        # Комбобокс: 0 = новейший период → период с индексом n-1
        period_idx = n - 1 - combo_idx
        period_idx = max(0, min(period_idx, n - 1))
        try:
            data = dashboard.build(self._df, selected_period_idx=period_idx)
        except Exception:
            return
        self._apply(data, repopulate_combo=False)
