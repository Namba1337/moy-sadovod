import sys
import re
import json
import os
import zipfile
import faulthandler
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from core import energy
from core.utils import DATA_DIR
from core.updater import APP_VERSION, UpdateChecker
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStackedWidget, QLabel, QPushButton,
    QTableWidget, QHeaderView, QLineEdit, QComboBox,
    QDateEdit, QFrame, QFileDialog, QMessageBox,
    QScrollArea, QStyleOption, QStyle,
)
from PyQt6.QtCore import (Qt, QPoint, QRect, QRectF, QTimer, pyqtSignal,
                           QPropertyAnimation, QParallelAnimationGroup,
                           QEasingCurve)
from PyQt6.QtGui import QFont, QFontMetrics, QColor, QPainter, QPixmap, QFontDatabase, QPalette, QBitmap, QPainterPath


from ui.energy_card import MeterReplacementDialog, PlotCardDialog
from ui.vznosy_card import VznosyCardDialog
from ui.rates_widget import RatesWidget, VznosyRatesWidget
from ui.energy_debt_widget import EnergyDebtWidget
from ui.vznosy_debt_widget import VznosyDebtWidget
from ui.plots_widget import PlotsWidget
from ui.detail_widget import DetailWidget
from ui.home_widget import HomeWidget


# ======================================================================= #
#  ВКЛАДКА ДЕТАЛИЗАЦИЯ
# ======================================================================= #

# Порядок участков для строк таблицы — динамически из snt_plots.json
class _TitleBar(QWidget):
    """Custom frameless-window title bar: title text + min/max/close buttons."""

    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self._window = window
        self._drag_pos = None
        self.setObjectName("titleBar")
        self.setFixedHeight(32)

        lyt = QHBoxLayout(self)
        lyt.setContentsMargins(16, 0, 0, 0)
        lyt.setSpacing(0)

        lyt.addStretch()

        self._version_btn = QPushButton(APP_VERSION, objectName="btnVersionPill")
        self._version_btn.setFixedHeight(24)
        self._version_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._version_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._version_btn.clicked.connect(window._on_update_pill_clicked)
        lyt.addWidget(self._version_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        lyt.addSpacing(10)

        icon_font = QFont("Segoe MDL2 Assets")
        icon_font.setPixelSize(10)

        for obj_name, char, slot in [
            ("btnWinMin",   "", self._minimize),
            ("btnWinMax",   "", self._toggle_max),
            ("btnWinClose", "", window.close),
        ]:
            btn = QPushButton(char, objectName=obj_name)
            btn.setFixedSize(46, 32)
            btn.setFont(icon_font)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            lyt.addWidget(btn)

        self._btn_max = self.findChild(QPushButton, "btnWinMax")

    def paintEvent(self, event):
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, p, self)

    def _minimize(self):
        self._window.showMinimized()

    def _toggle_max(self):
        if self._window.isMaximized():
            self._window.showNormal()
            if self._btn_max:
                self._btn_max.setText("")
        else:
            self._window.showMaximized()
            if self._btn_max:
                self._btn_max.setText("")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self._window.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() == Qt.MouseButton.LeftButton:
            if self._window.isMaximized():
                self._window.showNormal()
                if self._btn_max:
                    self._btn_max.setText("")
                self._drag_pos = QPoint(self._window.width() // 3, self.height() // 2)
            self._window.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_max()
        super().mouseDoubleClickEvent(event)


class _NavButton(QWidget):
    """Пункт левого сайдбара: глиф Material Icons + подпись."""
    nav_clicked = pyqtSignal(int)

    def __init__(self, icon_char: str, label: str, page_idx: int, parent=None):
        super().__init__(parent)
        self._page_idx = page_idx
        self.setObjectName("navBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(38)

        lyt = QHBoxLayout(self)
        lyt.setContentsMargins(6, 0, 0, 0)
        lyt.setSpacing(7)

        self._icon = QLabel(icon_char, objectName="navIcon")
        icon_font = QFont("Material Symbols Rounded")
        icon_font.setPixelSize(25)
        self._icon.setFont(icon_font)
        self._icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._lbl = QLabel(label, objectName="navLabel")
        self._lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        lyt.addWidget(self._icon)
        lyt.addWidget(self._lbl)
        lyt.addStretch()

    def paintEvent(self, event):
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, p, self)

    def set_active(self, active: bool):
        prop = "true" if active else "false"
        for w in (self, self._icon, self._lbl):
            w.setProperty("active", prop)
            w.style().unpolish(w)
            w.style().polish(w)

    def set_collapsed(self, collapsed: bool):
        lyt = self.layout()
        if collapsed:
            self._saved_margins = lyt.contentsMargins()
            m = self._saved_margins.left()
            lyt.setContentsMargins(m, 0, m, 0)
            icon_w = max(self._icon.sizeHint().width(), 1)
            self.setFixedWidth(m * 2 + icon_w)
        else:
            if hasattr(self, "_saved_margins"):
                lyt.setContentsMargins(self._saved_margins)
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.nav_clicked.emit(self._page_idx)
        super().mousePressEvent(event)


class _ActionButton(QWidget):
    """Кнопка действия в нижней части сайдбара: значок + подпись."""
    clicked = pyqtSignal()

    def __init__(self, icon_char: str, label: str, parent=None):
        super().__init__(parent)
        self.setObjectName("btnNavAction")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(38)

        lyt = QHBoxLayout(self)
        lyt.setContentsMargins(10, 0, 15, 0)
        lyt.setSpacing(7)

        self._icon = QLabel(icon_char, objectName="navIcon")
        icon_font = QFont("Material Symbols Rounded")
        icon_font.setPixelSize(17)
        self._icon.setFont(icon_font)
        self._icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._lbl = QLabel(label, objectName="actionLabel")
        self._lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        lyt.addWidget(self._icon)
        lyt.addWidget(self._lbl)
        lyt.addStretch()

    def paintEvent(self, event):
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, p, self)

    def set_collapsed(self, collapsed: bool):
        lyt = self.layout()
        if collapsed:
            self._saved_margins = lyt.contentsMargins()
            m = self._saved_margins.left()
            lyt.setContentsMargins(m, 0, m, 0)
            icon_w = max(self._icon.sizeHint().width(), 1)
            self.setFixedWidth(m * 2 + icon_w)
        else:
            if hasattr(self, "_saved_margins"):
                lyt.setContentsMargins(self._saved_margins)
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _BrandText(QWidget):
    """Надпись «МОЙ / САДОВОД / Бухгалтерский учет для СНТ».

    Отрисовка вручную через QPainter: положение каждой строки считается
    по tightBoundingRect (реальные пиксели глифов), поэтому межстрочные
    зазоры точны и не зависят от капризов метрик конкретного шрифта.
    Зазоры _GAP_* — явные константы, их легко подправить.
    """

    _COLOR_TITLE = QColor("#07414F")
    _COLOR_SUB   = QColor("#7A8A95")
    _GAP_TITLE   = 2     # зазор между МОЙ и САДОВОД, px
    _GAP_SUB     = 0     # зазор между САДОВОД и подписью, px
    _PAD_X       = 0     # горизонтальный отступ-страховка (запас под овершут)
    _PAD_TOP     = 4     # верхний отступ — сдвигает текст вниз относительно логотипа
    _PAD_Y       = 0     # нижний отступ-страховка

    def __init__(self, family: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        def _font(px: int, spacing: float, weight: int = 400, stretch: int = 100) -> QFont:
            f = QFont(family)
            f.setPixelSize(px)
            f.setWeight(QFont.Weight(weight))
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
            f.setStretch(stretch)
            return f

        # (текст, шрифт, цвет, оптическая коррекция X)
        # nudge<0 сдвигает строку левее: круглая «С» зрительно кажется
        # правее плоской «М», поэтому «САДОВОД» слегка выносим влево.
        self._lines = [
            ("МОЙ",                        _font(14, 0.0, 600), self._COLOR_TITLE,  0),
            ("САДОВОД",                    _font(20, 0.0, 600), self._COLOR_TITLE,  0),
            ("Бухгалтерский учет для СНТ", _font(10, 0.0, 400, 76),   self._COLOR_SUB,    0),
        ]
        self._gaps = [self._GAP_TITLE, self._GAP_SUB]
        self._layout_lines()

    def _layout_lines(self):
        """Считает draw_x / baseline каждой строки и итоговый размер."""
        placed = []
        y = self._PAD_TOP
        right_edge = 0
        last_bottom = y
        for i, (text, font, color, nudge) in enumerate(self._lines):
            fm = QFontMetrics(font)
            tbr = fm.tightBoundingRect(text)
            # tbr.top() отрицателен (над базовой линией) → baseline ниже
            baseline = y - tbr.top()
            # левый край реальных глифов в _PAD_X + оптическая коррекция
            draw_x = self._PAD_X - tbr.left() + nudge
            placed.append((text, font, color, draw_x, baseline))
            last_bottom = baseline + tbr.bottom() + 1
            right_edge = max(right_edge, draw_x + tbr.left() + tbr.width())
            if i < len(self._gaps):
                y = last_bottom + self._gaps[i]
        self._placed = placed
        self.setFixedSize(right_edge + self._PAD_X + 6,
                          last_bottom + self._PAD_Y)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        for text, font, color, draw_x, baseline in self._placed:
            p.setFont(font)
            p.setPen(color)
            p.drawText(int(draw_x), int(baseline), text)


class _RoundedFrame(QFrame):
    """Контейнер со скруглением через QSS (border-radius), как dashCard.

    Раньше использовал 1-битную `setMask`-маску для клиппинга детей, но она НЕ
    давала чистого антиалиасинга и перебивала QSS-скругление (углы оставались
    квадратными). Контент вкладок — внутри отступов и за скруглённые углы не
    вылезает, поэтому клиппинг детей не нужен: скругление рисует QSS у самого
    фрейма (нужен `WA_StyledBackground`).
    """
    _RADIUS = 14


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Window)
        self.setWindowTitle(f"Мой Садовод — v{APP_VERSION}")
        self.setMinimumSize(1280, 720)
        self.resize(1500, 860)
        self._update_checker = None
        self._update_manual = False
        self._setup_ui()
        self._apply_styles()

    # ── Native resize support on Windows ─────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            self._restore_win_resize()
        # Автоматическая проверка обновлений — один раз, через 1.5 сек
        # после первого показа окна (чтобы UI успел отрисоваться).
        if not getattr(self, "_update_check_scheduled", False):
            self._update_check_scheduled = True
            QTimer.singleShot(1500, lambda: self._check_for_updates(manual=False))

    def _restore_win_resize(self):
        """Ensure full WS_OVERLAPPEDWINDOW style + DWM frame for animations."""
        try:
            import ctypes
            hwnd = int(self.winId())
            GWL_STYLE = -16
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
            style |= (0x00040000   # WS_THICKFRAME
                    | 0x00C00000   # WS_CAPTION
                    | 0x00080000   # WS_SYSMENU
                    | 0x00020000   # WS_MINIMIZEBOX
                    | 0x00010000)  # WS_MAXIMIZEBOX
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style)

            class _MARGINS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int),
                             ("t", ctypes.c_int), ("b", ctypes.c_int)]
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                hwnd, ctypes.byref(_MARGINS(-1, -1, -1, -1))
            )

            SWP_FLAGS = 0x0020 | 0x0002 | 0x0001 | 0x0004
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FLAGS)
        except Exception:
            pass

    def nativeEvent(self, event_type, message):
        """Handle WM_NCHITTEST / WM_NCCALCSIZE for frameless resize support."""
        if sys.platform == "win32" and event_type == b"windows_generic_MSG":
            import ctypes

            class _MSG(ctypes.Structure):
                _fields_ = [
                    ("hWnd",    ctypes.c_void_p),
                    ("message", ctypes.c_uint),
                    ("wParam",  ctypes.c_size_t),
                    ("lParam",  ctypes.c_ssize_t),
                    ("time",    ctypes.c_uint),
                    ("pt_x",    ctypes.c_int),
                    ("pt_y",    ctypes.c_int),
                ]

            try:
                msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
            except Exception:
                return False, 0

            WM_NCCALCSIZE = 0x0083
            WM_NCHITTEST  = 0x0084

            if msg.message == WM_NCCALCSIZE and msg.wParam:
                if self.isMaximized():
                    # Windows сама увеличивает развёрнутое окно на толщину
                    # невидимой resize-рамки (компенсация для WS_THICKFRAME) —
                    # без встречной компенсации содержимое вылезает за
                    # границы монитора на несколько пикселей. Сжимаем
                    # предложенный client rect обратно внутрь.
                    class _RECT(ctypes.Structure):
                        _fields_ = [
                            ("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long),
                        ]

                    class _NCCALCSIZE_PARAMS(ctypes.Structure):
                        _fields_ = [("rgrc", _RECT * 3), ("lppos", ctypes.c_void_p)]

                    try:
                        params = ctypes.cast(
                            msg.lParam, ctypes.POINTER(_NCCALCSIZE_PARAMS)).contents
                        SM_CXSIZEFRAME, SM_CYSIZEFRAME, SM_CXPADDEDBORDER = 32, 33, 92
                        bx = (ctypes.windll.user32.GetSystemMetrics(SM_CXSIZEFRAME)
                              + ctypes.windll.user32.GetSystemMetrics(SM_CXPADDEDBORDER))
                        by = (ctypes.windll.user32.GetSystemMetrics(SM_CYSIZEFRAME)
                              + ctypes.windll.user32.GetSystemMetrics(SM_CXPADDEDBORDER))
                        params.rgrc[0].left   += bx
                        params.rgrc[0].top    += by
                        params.rgrc[0].right  -= bx
                        params.rgrc[0].bottom -= by
                    except Exception:
                        pass
                # Collapse non-client area so native chrome is invisible
                return True, 0

            if msg.message == WM_NCHITTEST:
                HTCLIENT      = 1
                HTCAPTION     = 2
                HTLEFT        = 10; HTRIGHT      = 11
                HTTOP         = 12; HTTOPLEFT    = 13
                HTTOPRIGHT    = 14; HTBOTTOM     = 15
                HTBOTTOMLEFT  = 16; HTBOTTOMRIGHT = 17

                x = ctypes.c_int16(msg.lParam & 0xFFFF).value
                y = ctypes.c_int16((msg.lParam >> 16) & 0xFFFF).value
                pos = self.mapFromGlobal(QPoint(x, y))
                px, py = pos.x(), pos.y()
                w, h = self.width(), self.height()
                m = 6  # resize margin in pixels

                on_l = px < m;      on_r = px > w - m - 1
                on_t = py < m;      on_b = py > h - m - 1

                if on_t and on_l:  return True, HTTOPLEFT
                if on_t and on_r:  return True, HTTOPRIGHT
                if on_b and on_l:  return True, HTBOTTOMLEFT
                if on_b and on_r:  return True, HTBOTTOMRIGHT
                if on_l:           return True, HTLEFT
                if on_r:           return True, HTRIGHT
                if on_t:           return True, HTTOP
                if on_b:           return True, HTBOTTOM

                # Title bar: HTCAPTION for native drag/snap, HTCLIENT for buttons
                tb_h = self._title_bar.height()
                btn_w = 3 * 46  # three 46px window buttons on the right
                if py < tb_h:
                    if px >= w - btn_w:
                        return True, HTCLIENT
                    # Пилюля версии — тоже кликабельный виджет, а не часть
                    # перетаскиваемой рамки (иначе WM_NCHITTEST перехватывает
                    # клик как HTCAPTION раньше, чем он доходит до Qt).
                    pill = getattr(self._title_bar, "_version_btn", None)
                    if pill is not None:
                        pill_rect = QRect(pill.mapTo(self, QPoint(0, 0)), pill.size())
                        if pill_rect.contains(px, py):
                            return True, HTCLIENT
                    return True, HTCAPTION

        return False, 0

    def _setup_ui(self):
        central = QWidget()
        central.setAutoFillBackground(True)
        self.setCentralWidget(central)

        # Outer layout: title bar + body (sidebar | content)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._title_bar = _TitleBar(self)
        outer.addWidget(self._title_bar)

        # Body: левый сайдбар + область контента
        body = QWidget(objectName="bodyArea")
        body.setAutoFillBackground(True)
        body_lyt = QHBoxLayout(body)
        body_lyt.setContentsMargins(0, 0, 8, 8)
        body_lyt.setSpacing(0)

        # ── Левый сайдбар навигации ──────────────────────────────────────
        sidebar = QWidget(objectName="sideNav")
        sidebar.setAutoFillBackground(True)
        sidebar.setMinimumWidth(250)
        sidebar.setMaximumWidth(250)
        side_lyt = QVBoxLayout(sidebar)
        side_lyt.setContentsMargins(25, 0, 0, 0)
        side_lyt.setSpacing(4)
        self._side_lyt = side_lyt

        # Шапка сайдбара: логотип + текстовый блок «МОЙ / САДОВОД»
        header = QWidget()
        header_lyt = QHBoxLayout(header)
        header_lyt.setContentsMargins(0, 0, 5, 0)
        header_lyt.setSpacing(4)

        _logo_file = Path(__file__).parent / "resources" / "images" / "logo.png"
        if _logo_file.exists():
            _pix = QPixmap(str(_logo_file))
            if not _pix.isNull():
                _logo_pix = _pix.scaledToHeight(
                    52, Qt.TransformationMode.SmoothTransformation
                )
                _lbl_logo = QLabel(objectName="navLogo")
                _lbl_logo.setPixmap(_logo_pix)
                _lbl_logo.setFixedSize(_logo_pix.width(), _logo_pix.height())
                header_lyt.addWidget(_lbl_logo, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Текстовый блок «МОЙ / САДОВОД / подпись» — собственная отрисовка
        _installed = set(QFontDatabase.families())
        _brand_family = next(
            (f for f in ("Geologica", "KOT-Eitai Gothic Bold", "KOT-Eitai Gothic",
                         "Montserrat", "Segoe UI") if f in _installed),
            "Segoe UI",
        )
        _brand = _BrandText(_brand_family)
        header_lyt.addWidget(_brand, alignment=Qt.AlignmentFlag.AlignVCenter)
        header_lyt.addStretch()

        _chf = QFont("Material Symbols Rounded")
        _chf.setPixelSize(20)
        self._chevron_btn = QPushButton(chr(0xe5cb), objectName="btnChevron")
        self._chevron_btn.setFont(_chf)
        self._chevron_btn.setFixedSize(16, 32)
        self._chevron_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._chevron_btn.clicked.connect(self._toggle_sidebar)
        header_lyt.addWidget(self._chevron_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._brand_widget = _brand
        header.setFixedHeight(52)
        side_lyt.addWidget(header)
        side_lyt.addSpacing(18)

        btn_container = QWidget()
        btn_container_lyt = QVBoxLayout(btn_container)
        btn_container_lyt.setContentsMargins(0, 0, 25, 16)
        btn_container_lyt.setSpacing(4)
        self._btn_container_lyt = btn_container_lyt

        self._nav_buttons: list[_NavButton] = []
        for icon, label, idx in [
            (chr(0xe587), "Главная",            0),
            (chr(0xf8ee), "Список участков",    3),
            (chr(0xf191), "Детализация",        1),
            (chr(0xeaec), "Членские взносы",    2),
            (chr(0xec1c), "Электричество",      4),
        ]:
            btn = _NavButton(icon, label, idx)
            btn.nav_clicked.connect(self._nav_click)
            self._nav_buttons.append(btn)
            btn_container_lyt.addWidget(btn)

        btn_container_lyt.addStretch()

        btn_save_proj = _ActionButton(chr(0xf09b), "Сохранить базу СНТ")
        btn_save_proj.clicked.connect(self._save_project)
        btn_container_lyt.addWidget(btn_save_proj)

        btn_load_proj = _ActionButton(chr(0xf090), "Загрузить базу СНТ")
        btn_load_proj.clicked.connect(self._load_project)
        btn_container_lyt.addWidget(btn_load_proj)

        side_lyt.addWidget(btn_container, stretch=1)

        self._sidebar = sidebar
        self._sidebar_expanded = True
        self._btn_save = btn_save_proj
        self._btn_load = btn_load_proj

        body_lyt.addWidget(sidebar)

        # Content stack
        self.stack = QStackedWidget(objectName="contentArea")
        self.home        = HomeWidget()
        self.detail      = DetailWidget()
        self.vznosy_debt = VznosyDebtWidget()
        self.plots       = PlotsWidget()
        self.energy_debt = EnergyDebtWidget()
        # Все вкладки намеренно НЕ autoFill: страница прозрачная, чтобы
        # проступал белый contentFrame («окно вкладки») со скруглёнными
        # углами. Каждая вкладка сама красит свои внутренние элементы.
        self.stack.addWidget(self.home)         # 0
        self.stack.addWidget(self.detail)       # 1
        self.stack.addWidget(self.vznosy_debt)  # 2
        self.stack.addWidget(self.plots)        # 3
        self.stack.addWidget(self.energy_debt)  # 4
        content_frame = _RoundedFrame(objectName="contentFrame")
        # НЕ autoFill: иначе палитра заливает КВАДРАТНЫЙ фон под QSS-скруглением.
        content_frame.setAutoFillBackground(False)
        # БЕЗ этого QSS border-radius не скругляет фон (рисуется прямоугольным) —
        # известная ловушка проекта (та же, что с бейджами на QLabel).
        content_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cf_lyt = QVBoxLayout(content_frame)
        cf_lyt.setContentsMargins(0, 0, 0, 0)
        cf_lyt.setSpacing(0)
        cf_lyt.addWidget(self.stack)
        body_lyt.addWidget(content_frame, stretch=1)

        outer.addWidget(body, stretch=1)

        # Подписки на изменение данных.
        # Раньше каждое изменение выписки веером синхронно перестраивало все
        # зависимые вкладки (дашборд, участки, оба реестра долгов) — отсюда
        # подвисания при любой правке. Теперь пересчитывается только видимая
        # вкладка; скрытые обновляются лениво при переходе на них
        # (страницы 0/2/4 и так пересчитываются в _nav_click, для «Участков»
        # используется флаг _plots_stale).
        self._plots_stale = False
        self.detail.dataLoaded.connect(self._on_statement_changed)

        # Изменение реестра участков: столбец «Участок» в детализации
        # обновляем сразу — он мутирует df_full, которым пользуются расчёты
        # всех вкладок. Остальные вкладки пересчитаются при переходе на них.
        self.plots.plotsUpdated.connect(self.detail.refresh_plot_column)

        self._nav_click(0)  # initial page: Главная

    def _on_statement_changed(self, df):
        """Выписка изменилась: пересчитываем только видимую вкладку."""
        self._plots_stale = True
        cur = self.stack.currentIndex()
        if cur == 0:
            self.home.refresh(df)
        elif cur == 2:
            self.vznosy_debt.refresh(df)
        elif cur == 3:
            self._plots_stale = False
            self.plots.refresh(df)
        elif cur == 4:
            self.energy_debt.refresh(df)

    def _nav_click(self, page_idx: int):
        for btn in self._nav_buttons:
            btn.set_active(btn._page_idx == page_idx)
        self.stack.setCurrentIndex(page_idx)
        if page_idx == 0:
            self.home.refresh(self.detail.df_full)
        elif page_idx == 2:
            self.vznosy_debt.refresh(self.detail.df_full)
        elif page_idx == 3 and self._plots_stale:
            self._plots_stale = False
            self.plots.refresh(self.detail.df_full)
        elif page_idx == 4:
            self.energy_debt.refresh(self.detail.df_full)

    _SIDEBAR_W_EXPANDED  = 250
    _SIDEBAR_W_COLLAPSED = 88

    def _toggle_sidebar(self):
        self._sidebar_expanded = not self._sidebar_expanded
        expanding = self._sidebar_expanded

        start_w = self._SIDEBAR_W_COLLAPSED if expanding else self._SIDEBAR_W_EXPANDED
        end_w   = self._SIDEBAR_W_EXPANDED  if expanding else self._SIDEBAR_W_COLLAPSED

        if not expanding:
            self._brand_widget.setVisible(False)
            self._chevron_btn.setText(chr(0xe5cc))  # chevron_right
            for btn in self._nav_buttons:
                btn._lbl.setVisible(False)
                btn.set_collapsed(True)
            for b in (self._btn_save, self._btn_load):
                b._lbl.setVisible(False)
                b.set_collapsed(True)
            self._btn_container_lyt.setContentsMargins(0, 0, 0, 16)

        if getattr(self, "_sidebar_anim", None) is not None:
            self._sidebar_anim.stop()
            self._sidebar_anim = None

        anim = QParallelAnimationGroup(self)
        for prop in (b"minimumWidth", b"maximumWidth"):
            a = QPropertyAnimation(self._sidebar, prop)
            a.setDuration(220)
            a.setStartValue(start_w)
            a.setEndValue(end_w)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.addAnimation(a)

        def _on_done():
            self._sidebar_anim = None
            if expanding:
                self._btn_container_lyt.setContentsMargins(0, 0, 25, 16)
                self._brand_widget.setVisible(True)
                self._chevron_btn.setText(chr(0xe5cb))  # chevron_left
                for btn in self._nav_buttons:
                    btn.set_collapsed(False)
                    btn._lbl.setVisible(True)
                for b in (self._btn_save, self._btn_load):
                    b.set_collapsed(False)
                    b._lbl.setVisible(True)

        anim.finished.connect(_on_done)
        self._sidebar_anim = anim
        anim.start()

    # ── Сохранение / загрузка проекта ────────────────────────────────────

    _PROJECT_JSON_FILES = [
        "snt_plots.json", "snt_rates.json",
        "snt_vznosy_rates.json", "snt_vznosy_adjustments.json",
        "snt_map_plots.json", "snt_map_image.json",
        "snt_meters.json", "snt_meters_years.json",
        "snt_meter_replacements.json", "snt_energy_baseline.json",
        "snt_common_meter.json", "snt_categories.json", "snt_people.json",
    ]

    def _save_project(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить проект СНТ", "", "Проект СНТ (*.snt)")
        if not path:
            return
        if not path.endswith(".snt"):
            path += ".snt"

        data_dir = Path(DATA_DIR)
        errors = []
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                for fname in self._PROJECT_JSON_FILES:
                    src = data_dir / fname
                    if src.exists():
                        zf.write(src, f"data/{fname}")

                # сохраняем данные вкладки «Детализация»
                if self.detail.df_full is not None:
                    try:
                        json_str = self.detail.df_full.to_json(
                            orient="records", force_ascii=False)
                        zf.writestr("data/detail_transactions.json", json_str)
                    except Exception as e:
                        errors.append(f"Детализация: {e}")
                    try:
                        cells_data = self.detail.get_manual_cells_data()
                        zf.writestr(
                            "data/detail_manual_cells.json",
                            json.dumps(cells_data, ensure_ascii=False),
                        )
                    except Exception as e:
                        errors.append(f"Пометки редактирования: {e}")

                # включаем файл карты, если он локальный
                map_cfg = data_dir / "snt_map_image.json"
                if map_cfg.exists():
                    try:
                        with open(map_cfg, encoding="utf-8") as f:
                            img_path = json.load(f).get("path", "")
                        if img_path and Path(img_path).is_file():
                            ext = Path(img_path).suffix
                            zf.write(img_path, f"map_image{ext}")
                    except Exception as e:
                        errors.append(f"Изображение карты: {e}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить проект:\n{e}")
            return

        msg = f"Проект сохранён:\n{path}"
        if errors:
            msg += "\n\nПредупреждения:\n" + "\n".join(errors)
        QMessageBox.information(self, "Сохранено", msg)

    def _load_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Загрузить проект СНТ", "", "Проект СНТ (*.snt)")
        if not path:
            return

        reply = QMessageBox.question(
            self, "Загрузка проекта",
            "Текущие данные будут заменены данными из файла.\nПродолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        data_dir = Path(DATA_DIR)
        data_dir.mkdir(exist_ok=True)
        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = zf.namelist()

                # извлекаем JSON-файлы данных (кроме транзакций — они в памяти)
                for name in names:
                    if name.startswith("data/") and name.endswith(".json"):
                        fname = name[5:]
                        if fname and fname != "detail_transactions.json":
                            dest = data_dir / fname
                            dest.write_bytes(zf.read(name))

                # восстанавливаем данные вкладки «Детализация»
                detail_df = None
                if "data/detail_transactions.json" in names:
                    try:
                        from io import StringIO
                        json_str = zf.read("data/detail_transactions.json").decode("utf-8")
                        detail_df = pd.read_json(StringIO(json_str), orient="records")
                        detail_df["Дата"] = pd.to_datetime(
                            detail_df["Дата"], unit="ms", errors="coerce")
                    except Exception as e:
                        QMessageBox.warning(
                            self, "Предупреждение",
                            f"Не удалось загрузить данные Детализации:\n{e}")

                manual_cells_data = None
                if "data/detail_manual_cells.json" in names:
                    try:
                        manual_cells_data = json.loads(
                            zf.read("data/detail_manual_cells.json").decode("utf-8"))
                    except Exception:
                        pass

                # извлекаем изображение карты
                map_name = next(
                    (n for n in names if n.startswith("map_image.")), None)
                if map_name:
                    ext = Path(map_name).suffix
                    img_dest = data_dir / f"map_image{ext}"
                    img_dest.write_bytes(zf.read(map_name))
                    map_cfg = data_dir / "snt_map_image.json"
                    with open(map_cfg, "w", encoding="utf-8") as f:
                        json.dump({"path": str(img_dest.resolve())}, f)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить проект:\n{e}")
            return

        # перезагружаем все виджеты из новых файлов
        self.plots.reload()
        self.energy_debt.rates.reload()
        self.vznosy_debt.rates.reload()

        if detail_df is not None:
            self.detail.load_dataframe(detail_df)
            if manual_cells_data is not None:
                self.detail.restore_manual_cells(manual_cells_data)
        else:
            # В загруженном проекте нет сохранённой выписки (или она не
            # распарсилась) — явно очищаем self.detail, иначе виджеты ниже
            # обновятся СТАРОЙ выпиской из предыдущего проекта, и новые
            # участки/тарифы окажутся смешаны с чужими операциями.
            self.detail._manual_rows.clear()
            self.detail._manual_cells.clear()
            self.detail._dup_pending.clear()
            self.detail.df_full = None
            self.detail.apply_filters()
            self.energy_debt.refresh(None)
            self.vznosy_debt.refresh(None)
            self.home.refresh(None)
            # Кэш долгов на «Участках» тоже считался по старой выписке
            self.plots.refresh(None)
            self._plots_stale = False

        QMessageBox.information(self, "Загружено", "Проект успешно загружен.")

    # ── Облачные обновления ─────────────────────────────────────────────

    def _on_update_pill_clicked(self) -> None:
        """Клик по пилюле версии в шапке: проверка обновлений + история релизов."""
        self._check_for_updates(manual=True)
        from ui.update_history_dialog import UpdateHistoryDialog
        from ui.detail_widget import _exec_dialog
        dlg = UpdateHistoryDialog(self)
        _exec_dialog(dlg, self)

    def _check_for_updates(self, *, manual: bool) -> None:
        """Запросить latest-release с GitHub.

        manual=True  — показываем «нет обновлений»/ошибку явно.
        manual=False — авто-проверка на старте; молчим, если нечего сказать.
        """
        # Не плодим параллельные проверки
        if getattr(self, "_update_checker", None) is not None:
            return

        checker = UpdateChecker(self)
        self._update_checker = checker
        self._update_manual = manual

        checker.updateAvailable.connect(self._on_update_available)
        checker.noUpdate.connect(self._on_no_update)
        checker.errorOccurred.connect(self._on_update_error)
        checker.check()

    def _release_checker(self) -> None:
        self._update_checker = None

    def _on_update_available(self, info) -> None:
        self._release_checker()
        # Импортируем здесь — чтобы избежать ненужной зависимости UI от ядра
        # на этапе импорта main.py (PyInstaller-frozen инициализация и т.п.).
        from ui.update_dialog import UpdateDialog
        from ui.detail_widget import _exec_dialog
        dlg = UpdateDialog(info, self)
        _exec_dialog(dlg, self)

    def _on_no_update(self) -> None:
        manual = self._update_manual
        self._release_checker()
        if manual:
            from ui.detail_widget import _AlertDialog
            _AlertDialog.show_alert(
                self, "Обновления",
                f"У вас актуальная версия ({APP_VERSION}).",
            )

    def _on_update_error(self, msg: str) -> None:
        manual = self._update_manual
        self._release_checker()
        if manual:
            from ui.detail_widget import _AlertDialog
            _AlertDialog.show_alert(
                self, "Не удалось проверить обновления",
                f"Не удалось связаться с сервером обновлений:\n{msg}",
            )
        # При авто-проверке молча игнорируем — нет интернета, нет проблемы.

    def _apply_styles(self):
        self.setStyleSheet("""
            /* ── Global ───────────────────────────────────────── */
            QMainWindow { background: #E9EDF3; }

            /* ── Custom title bar ────────────────────────────── */
            QWidget#titleBar {
                background: #E9EDF3;
            }
            QPushButton#btnVersionPill {
                background: rgba(7,65,79,0.1); border: 1px solid rgba(7,65,79,0.35);
                border-radius: 12px; color: #07414F; font-size: 12px; font-weight: 600;
                padding: 2px 14px;
            }
            QPushButton#btnVersionPill:hover   { background: rgba(7,65,79,0.18); }
            QPushButton#btnVersionPill:pressed { background: rgba(7,65,79,0.24); }
            QPushButton#btnWinMin, QPushButton#btnWinMax {
                background: transparent; border: none;
                color: #1A1A1A;
            }
            QPushButton#btnChevron {
                background: transparent; border: none;
                color: #6B7686; border-radius: 6px; padding: 0px;
            }
            QPushButton#btnChevron:hover { background: #DDE2EC; color: #3C4654; }
            QPushButton#btnWinMin:hover  { background: rgba(0,0,0,9%); color: #1A1A1A; }
            QPushButton#btnWinMax:hover  { background: rgba(0,0,0,9%); color: #1A1A1A; }
            QPushButton#btnWinMin:pressed  { background: rgba(0,0,0,16%); }
            QPushButton#btnWinMax:pressed  { background: rgba(0,0,0,16%); }
            QPushButton#btnWinClose {
                background: transparent; border: none;
                color: #1A1A1A;
            }
            QPushButton#btnWinClose:hover   { background: #C42B1C; color: #FFFFFF; }
            QPushButton#btnWinClose:pressed { background: #B22418; color: #FFFFFF; }

            /* ── Left navigation sidebar ──────────────────────── */
            QWidget#sideNav {
                background: #E9EDF3;
            }
            QLabel#navLogo { background: transparent; }
            QWidget#navBtn { background: transparent; border-radius: 8px; }
            QWidget#navBtn:hover { background: #DDE2EC; }
            QWidget#navBtn[active="true"] { background: #07414F; }
            QLabel#navIcon  { color: #6B7686; background: transparent; }
            QLabel#navLabel { color: #3C4654; background: transparent; font-size: 14px; font-weight: 550; }
            QWidget#navBtn[active="true"] QLabel#navIcon  { color: #FFFFFF; }
            QWidget#navBtn[active="true"] QLabel#navLabel {
                color: #FFFFFF; font-weight: 600;
            }

            QWidget#btnNavAction {
                background: #FFFFFF;
                border: 1px solid #D5DCE4; border-radius: 8px;
            }
            QWidget#btnNavAction:hover { background: #D7DCE8; }
            QWidget#btnNavAction:pressed { background: #CBD2E0; }
            QLabel#actionLabel { color: #3C4654; background: transparent; font-size: 13px; font-weight: 600; }

            /* ── Content area ─────────────────────────────────── */
            QWidget#bodyArea { background: #E9EDF3; }
            QFrame#contentFrame {
                background: #FFFFFF;
                border: 1px solid #D5DCE4;
                border-radius: 14px;
            }
            QStackedWidget#contentArea { background: transparent; }

            /* ── Page titles ─────────────────────────────────── */
            QLabel#pageTitle {
                font-size: 20px; font-weight: 700;
                color: #1F2937; background: transparent;
            }

            /* ── Dashboard «Главная» ──────────────────────────── */
            QScrollArea#homeScroll { background: transparent; border: none; }
            QWidget#homeContent { background: transparent; }
            QFrame#dashCard {
                background: #FFFFFF; border: 1px solid #D5DCE4; border-radius: 14px;
            }
            QLabel#cardTitleGreen {
                color: #57A05C; background: transparent;
                font-size: 17px; font-weight: 700;
            }
            QLabel#cardTitle {
                color: #1F2937; background: transparent;
                font-size: 15px; font-weight: 700;
            }
            QWidget#statCard {
                background: #F6F8FA; border: 1px solid #E6EAEF; border-radius: 10px;
            }
            QLabel#statIcon { color: #2F7D55; background: transparent; }
            QLabel#statCaption { color: #6B7280; background: transparent; font-size: 12px; }
            QLabel#statValue {
                color: #1F2937; background: transparent;
                font-size: 22px; font-weight: 700;
            }
            QWidget#activityItem { background: transparent; border-radius: 8px; }
            QWidget#activityItem:hover { background: #F1F5F9; }
            QLabel#activityCheck { color: #2E9E5B; background: transparent; }
            QLabel#activityTitle { color: #1F2937; background: transparent; font-size: 12px; }
            QLabel#activityDate { color: #9AA3AE; background: transparent; font-size: 11px; }
            QLabel#footerText { color: #9AA3AE; background: transparent; font-size: 12px; }
            QPushButton#chartPeriodBtn {
                background: #F6F8FA; color: #3C4654;
                border: 1px solid #D5DCE4; border-radius: 6px;
                padding: 5px 12px; font-size: 12px;
            }
            QPushButton#chartPeriodBtn:hover { background: #EEF1F5; }
            QPushButton#chartMenuBtn {
                background: #F6F8FA; color: #6B7280;
                border: 1px solid #D5DCE4; border-radius: 6px;
            }
            QPushButton#chartMenuBtn:hover { background: #EEF1F5; }

            /* ── Filter bar ──────────────────────────────────── */
            QFrame#filterFrame {
                background: #FFFFFF; border: 1px solid #E3E8EE; border-radius: 8px;
            }
            QLabel#filterLabel { color: #9AA3AE; background: transparent; font-size: 13px; }

            /* ── Inputs ──────────────────────────────────────── */
            QLineEdit#searchInput {
                background: #FFFFFF; border: 1px solid #D5DCE4; border-radius: 6px;
                color: #1F2937; padding: 7px 12px; font-size: 13px;
            }
            QLineEdit#searchInput:focus { border: 1px solid #07414F; }
            QComboBox#filterCombo {
                background: #FFFFFF; border: 1px solid #D5DCE4; border-radius: 6px;
                color: #1F2937; padding: 7px 10px; font-size: 13px;
            }
            QComboBox#filterCombo::drop-down { border: none; width: 18px; }
            QComboBox QAbstractItemView {
                background: #FFFFFF; border: 1px solid #D5DCE4;
                color: #1F2937; selection-background-color: #C9D8E2;
                selection-color: #07414F;
            }
            QDateEdit#datePicker {
                background: #FFFFFF; border: 1px solid #D5DCE4; border-radius: 6px;
                color: #1F2937; padding: 7px 10px; font-size: 13px;
            }
            QDateEdit#datePicker::drop-down { border: none; width: 18px; }

            /* ── Buttons ─────────────────────────────────────── */
            QPushButton#btnPrimary {
                background: #2F7D55; color: #FFFFFF; border: none; border-radius: 6px;
                padding: 8px 18px; font-size: 13px; font-weight: 600;
            }
            QPushButton#btnPrimary:hover   { background: #379061; }
            QPushButton#btnPrimary:pressed { background: #266645; }
            QPushButton#btnSecondary {
                background: #FFFFFF; color: #3C4654;
                border: 1px solid #D5DCE4; border-radius: 6px;
                padding: 8px 14px; font-size: 13px;
            }
            QPushButton#btnSecondary:hover { background: #F0F3F7; color: #1F2937; }

            /* ── Tables ──────────────────────────────────────── */
            QTableWidget#mainTable {
                background: #F9FAFB; border: 1px solid #D8DDE6; border-radius: 8px;
                gridline-color: #CDD3DC; color: #1F2937; font-size: 12px;
                selection-background-color: #C9D8E2; selection-color: #07414F;
                alternate-background-color: #E8ECF4;
            }
            QTableWidget#mainTable QHeaderView::section {
                background: #E9EDF5; color: #4B5563; border: none;
                border-right: 1px solid #CDD3DC; border-bottom: 2px solid #C4CBD7;
                padding: 8px 10px; font-size: 12px; font-weight: 600;
            }
            QTableWidget#mainTable::item {
                padding: 5px 10px; border-bottom: 1px solid #D8DDE6;
            }
            QTableWidget#mainTable::item:alternate {
                background: #E8ECF4;
            }
            QTableWidget#mainTable::item:hover {
                background: #DDE4EE;
            }
            QTableWidget#mainTable::item:selected {
                background: #C9D8E2; color: #07414F;
            }

            QTableWidget#summaryTable {
                background: #FFFFFF; border: 1px solid #E3E8EE; border-radius: 8px;
                gridline-color: #EAEDF1; color: #1F2937; font-size: 12px;
                selection-background-color: #E4F0E9; selection-color: #2F7D55;
            }
            QTableWidget#summaryTable QHeaderView::section {
                background: #EEF1F5; color: #6B7280; border: none;
                border-right: 1px solid #E3E8EE; border-bottom: 2px solid #E3E8EE;
                padding: 8px 10px; font-size: 12px; font-weight: 600;
            }
            QTableWidget#summaryTable::item {
                padding: 4px 10px; border-bottom: 1px solid #EAEDF1;
            }

            /* ── Viewport backgrounds (Qt QSS quirk fix) ────── */
            QAbstractScrollArea { background: #F0F3F9; }
            QAbstractScrollArea > QWidget { background: #F0F3F9; }
            /* QScrollBar тоже QWidget и прямой потомок QAbstractScrollArea —
               правило выше красит и его сплошным фоном поверх прозрачного
               QScrollBar:vertical{background:transparent} ниже (это же
               заливка самого виджета, а не «дорожки» скроллбара), отсюда и
               сплошная «дорожка». Явно исключаем скроллбар из этой заливки. */
            QAbstractScrollArea > QScrollBar { background: transparent; }

            /* ── Scrollbars ──────────────────────────────────── */
            /* Трек прозрачный (без «дорожки»/тени под бегунком) — меньше
               визуального шума, виден только сам бегунок. */
            QScrollBar:vertical {
                background: transparent; width: 8px; border: none;
            }
            QScrollBar::handle:vertical {
                background: #C3CAD3; border-radius: 4px; min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: #97A1AE; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
            QScrollBar:horizontal {
                background: transparent; height: 8px; border: none;
            }
            QScrollBar::handle:horizontal { background: #C3CAD3; border-radius: 4px; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

            /* ── Status / summary labels ─────────────────────── */
            QLabel#statusLabel  { color: #9AA3AE; background: transparent; font-size: 12px; }
            QLabel#summaryIncome  {
                color: #2E9E5B; background: transparent; font-size: 13px; font-weight: 600;
            }
            QLabel#summaryExpense {
                color: #DC2626; background: transparent; font-size: 13px; font-weight: 600;
            }

        """)


_CRASH_LOG = Path(os.environ.get("APPDATA", Path.home())) / "MoySadovod" / "crash.log"


def _qt_msg_handler(msg_type, context, message):
    """Перехватывает Qt-сообщения уровня Critical/Fatal — логирует и показывает диалог."""
    from PyQt6.QtCore import QtMsgType
    if msg_type in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        try:
            _CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(_CRASH_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] [Qt Critical] {message}\n")
        except Exception:
            pass
        try:
            app = QApplication.instance()
            if app is not None:
                QMessageBox.critical(
                    None,
                    "Ошибка приложения",
                    f"Критическая ошибка Qt:\n\n{message}\n\n"
                    f"Подробности сохранены в:\n{_CRASH_LOG}",
                )
        except Exception:
            pass


def _excepthook(exc_type, exc_value, exc_tb):
    import traceback
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        _CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{tb}\n")
    except Exception:
        pass
    try:
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        QMessageBox.critical(
            None,
            "Ошибка приложения",
            f"Произошла непредвиденная ошибка:\n\n"
            f"{exc_type.__name__}: {exc_value}\n\n"
            f"Подробности сохранены в:\n{_CRASH_LOG}",
        )
    except Exception:
        pass
    sys.exit(1)


def main():
    sys.excepthook = _excepthook

    try:
        _CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        faulthandler.enable(file=open(_CRASH_LOG, "a", encoding="utf-8"))
    except Exception:
        faulthandler.enable()

    os.makedirs(DATA_DIR, exist_ok=True)

    # Миграция: все участки без типа расчёта получают тип 1 (счётчик).
    # Действующие начисления при этом не изменяются.
    try:
        from core import energy
        energy.migrate_billing_types()
    except Exception:
        pass

    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("moy.sadovod.app")

    app = QApplication(sys.argv)
    app.setApplicationName("Мой Садовод")

    from PyQt6.QtCore import qInstallMessageHandler
    qInstallMessageHandler(_qt_msg_handler)

    _icon_path = Path(__file__).parent / "resources" / "images" / "logo_2.ico"
    if _icon_path.exists():
        from PyQt6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(_icon_path)))

    fonts_dir = Path(__file__).parent / "resources" / "fonts"
    for font_file in list(fonts_dir.glob("*.ttf")) + list(fonts_dir.glob("*.OTF")) + list(fonts_dir.glob("*.otf")):
        QFontDatabase.addApplicationFont(str(font_file))

    base_font = QFont("Segoe UI", 10)
    base_font.setStyleStrategy(
        QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality
    )
    base_font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(base_font)

    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window,      QColor("#F0F3F9"))
    palette.setColor(QPalette.ColorRole.Base,        QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#E8ECF4"))
    palette.setColor(QPalette.ColorRole.WindowText,  QColor("#1F2937"))
    palette.setColor(QPalette.ColorRole.Text,        QColor("#1F2937"))
    palette.setColor(QPalette.ColorRole.Button,      QColor("#F0F3F9"))
    palette.setColor(QPalette.ColorRole.ButtonText,  QColor("#1F2937"))
    palette.setColor(QPalette.ColorRole.Highlight,      QColor("#07414F"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.ToolTipBase,    QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.ToolTipText,    QColor("#374151"))
    app.setPalette(palette)

    app.setStyleSheet("""
        QToolTip {
            background: #FFFFFF;
            color: #374151;
            border: 1px solid #D1D5DB;
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 12px;
        }
    """)

    window = MainWindow()
    if _icon_path.exists():
        from PyQt6.QtGui import QIcon
        window.setWindowIcon(QIcon(str(_icon_path)))
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
