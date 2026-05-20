"""Стартовая страница «Главная» — дашборд СНТ.

Все показатели и лента активности — статические заглушки (как в макете).
Подключение реальных данных планируется отдельной задачей.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import (
    QFont, QColor, QPainter, QPen, QBrush, QPainterPath, QLinearGradient,
)

# ── Material Icons glyphs ──────────────────────────────────────────────────
_IC_MEMBERS = ""   # people
_IC_DEBT    = ""   # trending_up
_IC_BALANCE = ""   # account_balance
_IC_CHECK   = ""   # check_circle
_IC_MENU    = ""   # more_horiz
_IC_PERIOD  = ""   # date_range

# ── Заглушки данных ────────────────────────────────────────────────────────
_SNT_NAME = "Заря"
_STATS = [
    (_IC_MEMBERS, "Всего членов",      "154"),
    (_IC_DEBT,    "Задолженность",     "₽340k"),
    (_IC_BALANCE, "Остаток на счёте",  "₽2.1M"),
]
_CHART_MONTHS = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн",
                 "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
_CHART_VALUES = [40, 210, 230, 215, 470, 430, 375, 560, 700, 540, 520, 720]
_CHART_MAX = 800
_ACTIVITY = [
    ("Петров А.В. — Взнос за 2024", "31 марта 2024"),
    ("Петров А.В. — Взнос за 2024", "27 марта 2024"),
    ("Петров А.В. — Взнос за 2024", "20 марта 2024"),
    ("Петров А.В. — Взнос за 2024", "12 марта 2024"),
]


def _icon_font(px: int) -> QFont:
    f = QFont("Material Icons")
    f.setPixelSize(px)
    return f


class _AreaChart(QWidget):
    """Площадной график динамики взносов, отрисовка через QPainter."""

    _PAD_L, _PAD_R, _PAD_T, _PAD_B = 46, 14, 14, 28

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    def _points(self, plot: QRectF) -> list[QPointF]:
        n = len(_CHART_VALUES)
        pts = []
        for i, v in enumerate(_CHART_VALUES):
            x = plot.left() + plot.width() * i / (n - 1)
            y = plot.bottom() - plot.height() * v / _CHART_MAX
            pts.append(QPointF(x, y))
        return pts

    @staticmethod
    def _smooth_path(pts: list[QPointF]) -> QPainterPath:
        """Сглаженная кривая через кубические сегменты (Catmull-Rom)."""
        path = QPainterPath(pts[0])
        for i in range(len(pts) - 1):
            p0 = pts[i - 1] if i > 0 else pts[i]
            p1 = pts[i]
            p2 = pts[i + 1]
            p3 = pts[i + 2] if i + 2 < len(pts) else pts[i + 1]
            c1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6.0,
                         p1.y() + (p2.y() - p0.y()) / 6.0)
            c2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6.0,
                         p2.y() - (p3.y() - p1.y()) / 6.0)
            path.cubicTo(c1, c2, p2)
        return path

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        plot = QRectF(
            self._PAD_L, self._PAD_T,
            max(1.0, self.width() - self._PAD_L - self._PAD_R),
            max(1.0, self.height() - self._PAD_T - self._PAD_B),
        )

        # ── Сетка и подписи оси Y ──────────────────────────────────────────
        p.setFont(QFont("Segoe UI", 8))
        steps = 4
        for s in range(steps + 1):
            val = _CHART_MAX * s // steps
            y = plot.bottom() - plot.height() * s / steps
            p.setPen(QPen(QColor("#EAEDF1"), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QPen(QColor("#9AA3AE"), 1))
            p.drawText(QRectF(0, y - 9, self._PAD_L - 8, 18),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       str(val))

        pts = self._points(plot)
        curve = self._smooth_path(pts)

        # ── Площадная заливка ──────────────────────────────────────────────
        area = QPainterPath(curve)
        area.lineTo(plot.right(), plot.bottom())
        area.lineTo(plot.left(), plot.bottom())
        area.closeSubpath()
        grad = QLinearGradient(0, plot.top(), 0, plot.bottom())
        grad.setColorAt(0.0, QColor(47, 125, 85, 150))
        grad.setColorAt(1.0, QColor(47, 125, 85, 14))
        p.fillPath(area, QBrush(grad))

        # ── Линия графика ──────────────────────────────────────────────────
        p.strokePath(curve, QPen(QColor("#2F7D55"), 2.4,
                                 Qt.PenStyle.SolidLine,
                                 Qt.PenCapStyle.RoundCap,
                                 Qt.PenJoinStyle.RoundJoin))

        # ── Подписи оси X ──────────────────────────────────────────────────
        p.setPen(QPen(QColor("#9AA3AE"), 1))
        for i, m in enumerate(_CHART_MONTHS):
            x = pts[i].x()
            p.drawText(QRectF(x - 24, plot.bottom() + 6, 48, 18),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, m)


class _StatCard(QWidget):
    """Карточка показателя: иконка + подпись + крупное значение."""

    def __init__(self, icon: str, caption: str, value: str, parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lyt = QHBoxLayout(self)
        lyt.setContentsMargins(18, 16, 18, 16)
        lyt.setSpacing(14)

        ic = QLabel(icon, objectName="statIcon")
        ic.setFont(_icon_font(30))
        ic.setFixedWidth(38)
        lyt.addWidget(ic)

        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(QLabel(caption, objectName="statCaption"))
        text.addWidget(QLabel(value, objectName="statValue"))
        lyt.addLayout(text)
        lyt.addStretch()


class _ActivityItem(QWidget):
    """Элемент ленты активности: галочка + название + дата."""

    def __init__(self, title: str, date: str, parent=None):
        super().__init__(parent)
        self.setObjectName("activityItem")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lyt = QHBoxLayout(self)
        lyt.setContentsMargins(10, 8, 10, 8)
        lyt.setSpacing(10)

        ic = QLabel(_IC_CHECK, objectName="activityCheck")
        ic.setFont(_icon_font(20))
        ic.setFixedWidth(24)
        lyt.addWidget(ic, alignment=Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(QLabel(title, objectName="activityTitle"))
        text.addWidget(QLabel(date, objectName="activityDate"))
        lyt.addLayout(text)
        lyt.addStretch()


def _card(object_name: str = "dashCard") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(objectName=object_name)
    lyt = QVBoxLayout(frame)
    lyt.setContentsMargins(20, 18, 20, 18)
    lyt.setSpacing(14)
    return frame, lyt


class HomeWidget(QWidget):
    """Дашборд «Главная»."""

    def __init__(self):
        super().__init__()
        self.setAutoFillBackground(True)
        self._setup_ui()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(objectName="homeScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget(objectName="homeContent")
        content.setAutoFillBackground(True)
        scroll.setWidget(content)

        lyt = QVBoxLayout(content)
        lyt.setContentsMargins(28, 24, 28, 24)
        lyt.setSpacing(18)

        lyt.addWidget(self._overview_card())
        lyt.addLayout(self._middle_row(), stretch=1)
        lyt.addLayout(self._footer())

    # ── Обзор СНТ + показатели ─────────────────────────────────────────────
    def _overview_card(self) -> QFrame:
        frame, lyt = _card()
        lyt.addWidget(QLabel(f'Обзор СНТ «{_SNT_NAME}»',
                             objectName="cardTitleGreen"))

        row = QHBoxLayout()
        row.setSpacing(16)
        for icon, caption, value in _STATS:
            row.addWidget(_StatCard(icon, caption, value), stretch=1)
        lyt.addLayout(row)
        return frame

    # ── График + лента активности ──────────────────────────────────────────
    def _middle_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(self._chart_card(), stretch=1)
        row.addWidget(self._activity_card())
        return row

    def _chart_card(self) -> QFrame:
        frame, lyt = _card()

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(QLabel("Динамика взносов (тыс. ₽)",
                                objectName="cardTitle"))
        header.addStretch()

        period = QPushButton("С начала года", objectName="chartPeriodBtn")
        period.setCursor(Qt.CursorShape.PointingHandCursor)
        header.addWidget(period)

        menu = QPushButton(_IC_MENU, objectName="chartMenuBtn")
        menu.setFont(_icon_font(18))
        menu.setFixedSize(30, 30)
        menu.setCursor(Qt.CursorShape.PointingHandCursor)
        header.addWidget(menu)

        lyt.addLayout(header)
        lyt.addWidget(_AreaChart(), stretch=1)
        return frame

    def _activity_card(self) -> QFrame:
        frame, lyt = _card()
        frame.setFixedWidth(304)
        lyt.addWidget(QLabel("Активность", objectName="cardTitle"))
        for title, date in _ACTIVITY:
            lyt.addWidget(_ActivityItem(title, date))
        lyt.addStretch()
        return frame

    # ── Футер ──────────────────────────────────────────────────────────────
    def _footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("© 2024 Мой САДОВОД", objectName="footerText"))
        row.addStretch()
        row.addWidget(QLabel("Тех. поддержка", objectName="footerText"))
        return row
