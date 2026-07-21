"""Общие UI-компоненты, используемые несколькими вкладками.

Исторически жили внутри ui.plots_widget / ui.detail_widget и
кросс-импортировались оттуда как приватные имена; вынесены сюда, чтобы
вкладочные модули не зависели друг от друга. Старые имена (_AppTooltip,
_FlatTableModel, …) остаются доступными из прежних модулей как алиасы.

Состав:
  AppTooltip / TooltipFilter — кастомная всплывашка (QToolTip на Windows 11
      игнорирует стили Qt);
  FlatTableModel             — плоская модель таблиц долгов (ЧВ, Электричество);
  SortHeaderView             — шапка таблиц долгов: сортировка, поиск, корзина;
  ClipFrame / BorderOverlay  — скруглённый контейнер с обрезкой содержимого;
  TREE_STYLE                 — единый стиль главных таблиц (QTreeView#mainTable);
  INPUT_SS / INPUT_ERROR_SS / FIELD_LABEL_SS — компактные поля ввода форм
      с состоянием ошибки (см. set_input_error).
"""
from __future__ import annotations

from PyQt6.QtCore import (
    QAbstractItemModel, QEvent, QModelIndex, QObject, QPoint, QRect, QRectF,
    Qt, pyqtSignal,
)
from PyQt6.QtGui import (
    QBitmap, QColor, QFont, QFontMetrics, QPainter, QPen, QPolygon, QRegion,
    QTextCharFormat,
)
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QCalendarWidget, QDateEdit, QFrame, QHeaderView, QLabel,
    QLineEdit, QSpinBox, QToolButton, QTreeView, QWidget,
)

from ui.theme import C, FS, SCROLLBAR_W, calendar_qss, tree_qss

# ──────────────────────────────────────────────────────────────────────────
#  Стили компактных полей ввода (формы карточек/диалогов)
# ──────────────────────────────────────────────────────────────────────────

INPUT_SS = (
    f"QLineEdit{{background:{C.BG_SUBTLE}; border:1px solid {C.BORDER};"
    f"border-radius:4px; padding:4px 8px; font-size:{FS.SMALL}px; color:{C.TEXT};}}"
    f"QLineEdit:focus{{border:1px solid {C.BRAND};}}")
INPUT_ERROR_SS = (
    f"QLineEdit{{background:{C.DANGER_BG}; border:1px solid {C.DANGER};"
    f"border-radius:4px; padding:4px 8px; font-size:{FS.SMALL}px; color:{C.TEXT};}}"
    f"QLineEdit:focus{{border:1px solid {C.DANGER};}}")
FIELD_LABEL_SS = f"font-size:10px; color:{C.TEXT_FAINT}; background:transparent;"


class NoJumpDateEdit(QDateEdit):
    """QDateEdit(calendarPopup=True) без паразитного «прыжка» даты и без
    контекстного меню; ручной ввод (клик по секции, набор цифр, стрелки
    клавиатуры, колесо) остаётся включённым как обычно.

    Баг: даже когда up/down-кнопки не нарисованы (calendarPopup=True
    визуально показывает только кнопку календаря), Qt внутри всё равно
    держит их геометрию для хит-теста. Из-за этого узкая полоса пикселей
    прямо ПЕРЕД кнопкой календаря (видимо пустая) на самом деле реагирует
    как невидимая spin-кнопка — обычный клик там молча инкрементит/
    декрементит текущую секцию на 1 без всякой подсказки, что произошло
    (наблюдалось на «Дата начала» активной группы участка). ButtonSymbols.
    NoButtons убирает эту логику целиком; кнопка календаря — отдельный
    механизм, попап продолжает открываться как раньше."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

    def contextMenuEvent(self, event):
        event.ignore()


class CalendarArrowFlip(QObject):
    """Переключает стрелку dropdown у QDateEdit при открытии/закрытии
    календаря. QSS не даёт состояния «попап открыт» для ::down-arrow
    у QDateEdit (в отличие от :on у QComboBox), поэтому по Show/Hide
    попапа выставляем на поле динамическое свойство calOpen и
    перечитываем стиль — QSS поля матчит его селектором
    ``QDateEdit[calOpen="true"]::down-arrow``. Живёт ребёнком QDateEdit."""

    def __init__(self, de: QDateEdit):
        super().__init__(de)
        self._de = de
        cal = de.calendarWidget()
        if cal is not None:
            cal.installEventFilter(self)

    def eventFilter(self, obj, event):
        t = event.type()
        if t in (QEvent.Type.Show, QEvent.Type.Hide):
            try:
                self._de.setProperty("calOpen", t == QEvent.Type.Show)
                st = self._de.style()
                st.unpolish(self._de)
                st.polish(self._de)
            except RuntimeError:
                pass  # QDateEdit уже удалён (закрытие приложения)
        return False


def style_date_popup(de: QDateEdit) -> None:
    """Приводит всплывающий календарь QDateEdit к светлой теме приложения.

    Штатный QCalendarWidget — top-level попап, глобальный QSS главного окна
    до него не доходит, поэтому он рендерился системной палитрой (тёмная
    шапка навигации, красные выходные). Вызывать после создания
    QDateEdit(calendarPopup=True); без calendarPopup — no-op.
    """
    cal = de.calendarWidget()
    if cal is None:
        return
    from ui.icons import get_icon, icon_png_path
    cal.setVerticalHeaderFormat(
        QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
    # Кнопки спинбокса года (клик по году в шапке): системные бевел-стрелки
    # заменяем на плоские с глифами-шевронами. Блок живёт здесь, а не в
    # calendar_qss() — QSS ссылается на PNG-файлы иконок (icon_png_path),
    # а ui.theme намеренно свободен от зависимостей.
    arr_up = icon_png_path("expand_less", 10, color=C.TEXT_BODY)
    arr_dn = icon_png_path("expand_more", 10, color=C.TEXT_BODY)
    cal.setStyleSheet(calendar_qss() + f"""
        QCalendarWidget QSpinBox::up-button, QCalendarWidget QSpinBox::down-button {{
            subcontrol-origin: border; width: 16px;
            border: none; border-radius: 3px; background: transparent;
        }}
        QCalendarWidget QSpinBox::up-button {{ subcontrol-position: top right; }}
        QCalendarWidget QSpinBox::down-button {{ subcontrol-position: bottom right; }}
        QCalendarWidget QSpinBox::up-button:hover,
        QCalendarWidget QSpinBox::down-button:hover {{ background: {C.BRAND_GHOST}; }}
        QCalendarWidget QSpinBox::up-arrow {{
            image: url({arr_up}); width: 10px; height: 10px;
        }}
        QCalendarWidget QSpinBox::down-arrow {{
            image: url({arr_dn}); width: 10px; height: 10px;
        }}
    """)
    # Цвета дней задаются QTextCharFormat-ами, QSS их не покрывает:
    # выходные — как будни, строка Пн..Вс — приглушённая.
    day_fmt = QTextCharFormat()
    day_fmt.setForeground(QColor(C.TEXT))
    for d in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
        cal.setWeekdayTextFormat(d, day_fmt)
    hdr_fmt = QTextCharFormat()
    hdr_fmt.setForeground(QColor(C.TEXT_FAINT))
    cal.setHeaderTextFormat(hdr_fmt)
    # Спинбокс года растягивается на всю свободную ширину шапки, из-за чего
    # его кнопки прокрутки (прижаты к правому краю) улетают к краю окна —
    # ограничиваем шириной «4 цифры + кнопки».
    year_edit = cal.findChild(QSpinBox, "qt_calendar_yearedit")
    if year_edit is not None:
        year_edit.setMaximumWidth(72)
        year_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
    # Стрелки навигации — глифы приложения вместо системных пиксельных стрелок
    for obj_name, icon in (("qt_calendar_prevmonth", "chevron_left"),
                           ("qt_calendar_nextmonth", "chevron_right")):
        btn = cal.findChild(QToolButton, obj_name)
        if btn is not None:
            btn.setIcon(get_icon(icon, 14, color=C.TEXT_BODY))


def set_input_error(inp: QLineEdit, error: bool) -> None:
    """Включает/выключает состояние ошибки поля ввода. Идемпотентно —
    в отличие от «дописывания» border в конец styleSheet, состояние
    полностью заменяется и корректно сбрасывается."""
    inp.setStyleSheet(INPUT_ERROR_SS if error else INPUT_SS)


# ──────────────────────────────────────────────────────────────────────────
#  Единый стиль главных таблиц + ширина их скроллбара
# ──────────────────────────────────────────────────────────────────────────

TREE_STYLE = tree_qss()

# Ширина скроллбара mainTable — заглушки в шапках таблиц долгов должны
# совпадать с ней по ширине.
SB_W = SCROLLBAR_W


class MainTableTreeView(QTreeView):
    """QTreeView#mainTable с вертикальным скроллбаром, начинающимся НИЖЕ
    заголовка, а не с самого верха фрейма.

    QAbstractScrollArea расставляет скроллбары на всю высоту фрейма
    независимо от setViewportMargins() (те резервируют место только под
    сам viewport, не под скроллбар) — из-за этого скроллбар наезжал на
    строку заголовка. updateGeometries() — штатная точка перерасчёта
    геометрии скроллбаров/viewport у QAbstractScrollArea, подрезаем скроллбар
    здесь на каждый layout/resize.

    Угол «шапка × желоб скроллбара» после подрезки никто не рисует (шапка
    заканчивается на границе вьюпорта) — оставалась белая дыра. Закрываем
    её заглушкой цвета шапки: сплошной #C9D8E2 без границ — бесшовное
    продолжение заголовка, тот же вид, что у sb_stub в шапках таблиц
    долгов (energy/vznosy_debt_widget)."""

    _HDR_BG = QColor("#C9D8E2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hdr_stub = QWidget(self)
        # Без WA_StyledBackground голый QWidget может молча игнорировать
        # QSS-фон — заглушка «есть», но ничего не рисует.
        self._hdr_stub.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._hdr_stub.setStyleSheet(
            f"background:{self._HDR_BG.name()};border:none;")
        self._hdr_stub.hide()

    def _sync_scrollbar(self):
        header = self.header()
        hh = 0 if header.isHidden() else header.height()
        sb = self.verticalScrollBar()
        if hh <= 0 or not sb.isVisibleTo(self) or sb.width() <= 0:
            self._hdr_stub.hide()
            return
        # ВАЖНО: скроллбар живёт не в самом дереве, а в служебном контейнере
        # (QAbstractScrollAreaScrollBarContainer), и sb.geometry() задан в
        # координатах КОНТЕЙНЕРА — его x() всегда 0. Все позиции считаем
        # через mapTo(self, ...) в системе координат дерева.
        top_left = sb.mapTo(self, QPoint(0, 0))
        if top_left.x() <= 0:
            # Лейаут ещё не расставил контейнер (первый показ, пустая
            # модель) — без проверки заглушка встаёт в левый верхний угол.
            self._hdr_stub.hide()
            return
        if top_left.y() < hh:
            g = sb.geometry()
            dy = hh - top_left.y()
            sb.setGeometry(g.x(), g.y() + dy, g.width(), max(0, g.height() - dy))
            top_left = sb.mapTo(self, QPoint(0, 0))
        self._hdr_stub.setGeometry(top_left.x(), 0, sb.width(), hh)
        self._hdr_stub.show()
        self._hdr_stub.raise_()

    def updateGeometries(self):
        super().updateGeometries()
        self._sync_scrollbar()

    def resizeEvent(self, event):
        # Геометрию скроллбаров QAbstractScrollArea расставляет не только в
        # updateGeometries(), но и в layoutChildren() по resize — без этого
        # хука ресайз возвращал скроллбар на всю высоту (поверх шапки),
        # а заглушка оставалась на старом месте.
        super().resizeEvent(event)
        self._sync_scrollbar()

# Светлые фоновые варианты цветов долга для строк таблиц (уровень → bg).
# Уровни возвращают core.energy.debt_level / core.vznosy.debt_level.
DEBT_COLOR_LIGHT = dict(C.DEBT_BG)


# ──────────────────────────────────────────────────────────────────────────
#  Кастомная всплывашка — обходит нативный QToolTip Windows
# ──────────────────────────────────────────────────────────────────────────

class AppTooltip:
    """Синглтон-всплывашка с гарантированным светлым оформлением.

    Используется вместо QToolTip, который на Windows 11 игнорирует стили Qt.
    """
    _w: "QLabel | None" = None

    @classmethod
    def _ensure(cls) -> "QLabel":
        if cls._w is None:
            w = QLabel()
            w.setWindowFlags(
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.WindowStaysOnTopHint
            )
            w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            w.setContentsMargins(6, 4, 6, 4)
            w.setStyleSheet(
                f"QLabel {{ background:{C.BG_SURFACE}; color:{C.TEXT_BODY}; "
                f"border:1px solid {C.BORDER}; border-radius:4px; "
                f"font-size:{FS.SMALL}px; padding:4px 6px; }}"
            )
            cls._w = w
        return cls._w

    @classmethod
    def show_at(cls, text: str, global_pos: "QPoint"):
        w = cls._ensure()
        w.setText(text)
        w.adjustSize()
        w.move(global_pos.x() + 12, global_pos.y() + 16)
        w.show()
        w.raise_()

    @classmethod
    def hide(cls):
        if cls._w is not None:
            cls._w.hide()


class TooltipFilter(QObject):
    """EventFilter — навешивает кастомную всплывашку на любой существующий виджет.

    Передавайте виджет как parent, чтобы фильтр жил столько же, сколько виджет.
    """

    def __init__(self, tip: str, parent: "QWidget"):
        super().__init__(parent)
        self._tip = tip

    def eventFilter(self, obj, event):
        from PyQt6.QtGui import QCursor
        if event.type() == QEvent.Type.Enter:
            AppTooltip.show_at(self._tip, QCursor.pos())
        elif event.type() == QEvent.Type.Leave:
            AppTooltip.hide()
        return False


# ──────────────────────────────────────────────────────────────────────────
#  Базовая плоская модель таблиц-долгов
# ──────────────────────────────────────────────────────────────────────────

class FlatTableModel(QAbstractItemModel):
    """Плоская модель для таблиц долгов. Подклассы задают ``COLUMNS``.

    Строка — dict с ключами ``_text_/_sort_/_fg_/_bg_/_bold_/_tip_<col>``.
    Используется вкладками «Членские взносы» и «Электроэнергия».
    """

    COLUMNS: list[str] = []
    # Опциональная подмена заголовка для конкретного столбца (внутренний
    # ключ COLUMNS остаётся прежним — им завязаны все _text_/_sort_/…
    # ключи строк, меняется только отображаемый текст шапки). Тот же приём,
    # что и в detail_widget.py («Участок» → «№ уч.»).
    HEADER_LABELS: dict[str, str] = {}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def load(self, rows: list[dict]):
        self.beginResetModel()
        self._rows = list(rows)
        for i, r in enumerate(self._rows):
            r["_orig_idx"] = i
        self.endResetModel()

    def top_nodes(self) -> list[dict]:
        return self._rows

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        return self.createIndex(row, column, self._rows[row])

    def parent(self, index):
        return QModelIndex()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLUMNS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = self.COLUMNS[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row.get(f"_text_{col}", "")
        if role == Qt.ItemDataRole.UserRole:
            return row.get(f"_sort_{col}", 0.0)
        if role == Qt.ItemDataRole.ForegroundRole:
            fg = row.get(f"_fg_{col}")
            return QColor(fg) if fg else None
        if role == Qt.ItemDataRole.BackgroundRole:
            bg = row.get(f"_bg_{col}")
            return QColor(bg) if bg else None
        if role == Qt.ItemDataRole.FontRole:
            if row.get(f"_bold_{col}"):
                f = QFont()
                f.setBold(True)
                return f
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.get(f"_tip_{col}", "")
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.COLUMNS):
                col = self.COLUMNS[section]
                return self.HEADER_LABELS.get(col, col)
        return None

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        self.layoutAboutToBeChanged.emit()
        if 0 <= column < len(self.COLUMNS):
            col = self.COLUMNS[column]
            sort_key = f"_sort_{col}"
            self._rows.sort(
                key=lambda r: (r.get(sort_key) is None, r.get(sort_key, 0.0)),
                reverse=(order == Qt.SortOrder.DescendingOrder),
            )
        else:
            # Столбец вне диапазона (3-е состояние сортировки — сброс):
            # возвращаем исходный порядок вставки, см. load().
            self._rows.sort(key=lambda r: r.get("_orig_idx", 0))
        self.layoutChanged.emit()


# ──────────────────────────────────────────────────────────────────────────
#  Вспомогательные виджеты для скруглённых контейнеров
# ──────────────────────────────────────────────────────────────────────────

class BorderOverlay(QWidget):
    """Прозрачный виджет-ребёнок, рисует только скруглённую рамку поверх всего."""

    def __init__(self, color: QColor, radius: int, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._color  = color
        self._radius = radius
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())
        self.raise_()

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() == QEvent.Type.Resize:
            self.setGeometry(self.parent().rect())
            self.raise_()
        return False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self._color, 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                                self._radius, self._radius)


class ClipFrame(QFrame):
    """QFrame, который через setMask обрезает всё содержимое по скруглённому
    прямоугольнику — фон, hover-выделения дочерних виджетов не вылезают за углы."""

    def __init__(self, border_color: QColor, radius: int, parent=None):
        super().__init__(parent)
        self._radius = radius
        self._overlay = None  # создаётся после добавления детей
        self.setStyleSheet("background: transparent; border: none;")
        self._border_color = border_color

    def finish_setup(self):
        """Вызвать после того, как все дочерние виджеты добавлены."""
        self._overlay = BorderOverlay(self._border_color, self._radius, self)
        self._update_mask()

    def _update_mask(self):
        sz = self.size()
        if sz.width() <= 0 or sz.height() <= 0:
            return
        bmp = QBitmap(sz)
        bmp.fill(Qt.GlobalColor.color0)
        p = QPainter(bmp)
        p.setBrush(Qt.GlobalColor.color1)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), self._radius, self._radius)
        p.end()
        self.setMask(QRegion(bmp))

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        self._update_mask()
        if self._overlay:
            self._overlay.setGeometry(self.rect())
            self._overlay.raise_()


# ──────────────────────────────────────────────────────────────────────────
#  Шапка таблицы с кастомными стрелками сортировки
# ──────────────────────────────────────────────────────────────────────────

class SortHeaderView(QHeaderView):
    """Шапка с синим фоном, стрелками сортировки и кнопкой удаления выбранных."""

    deleteRequested = pyqtSignal()
    searchChanged   = pyqtSignal(int, str)   # (col_logical, text)

    _BG       = QColor(C.BRAND_TINT)
    _FG       = QColor(C.BRAND)
    _BORDER   = QColor("#B5C8D5")
    _ARR_ON   = QColor(C.BRAND)
    _ARR_OFF  = QColor("#9AABB6")
    _DEL_OFF  = QColor(C.TEXT_FAINT)      # нет выбора — серый
    _DEL_ON   = QColor(C.DANGER)          # есть выбор — красный
    _DEL_HOV  = QColor(C.DANGER_HOVER)    # наведение — тёмно-красный

    _IC_CHARS = {chr(0xE73A), chr(0xF567), chr(0xF0DC)}  # иконки-индикаторы
    _IC_COLOR = QColor("#F59E0B")

    _SORT_W = 18   # ширина зоны иконки сортировки (режим sort_left)

    def __init__(self, parent=None, sort_left: bool = False):
        super().__init__(Qt.Orientation.Horizontal, parent)
        # sort_left=True — как в «Операциях»: стрелка сортировки слева от
        # текста, клик работает только по стрелке (не по всей секции), 3
        # состояния по кругу (по возрастанию → по убыванию → без сортировки).
        self._sort_left = sort_left
        self.setSectionsClickable(not sort_left)
        self.setSortIndicatorShown(False)
        self.setFixedHeight(34)
        self.setSortIndicator(-1 if sort_left else 0, Qt.SortOrder.AscendingOrder)
        self.setMouseTracking(True)
        self._del_col        = -1
        self._has_sel        = False
        self._del_hovered    = False
        self._fill_tag       = QFont.Tag.fromString("FILL")
        self._col_indicators: dict[int, list] = {}   # col → [(lbl, cnt, tip), ...]
        self._tip_col        = -1
        self._tip_text       = ""
        self._search_cols:   set  = set()
        self._search_active: dict = {}
        self._search_fields: dict = {}

    # -- публичный API --------------------------------------------------------

    def set_delete_col(self, col: int):
        self._del_col = col

    def _content_left(self, sec_left: int) -> int:
        """(sort_left) X, с которого начинается текст — после иконки сортировки."""
        return sec_left + 4 + self._SORT_W + 4

    def _sort_icon_zone(self, sec_rect: QRect) -> QRect:
        """(sort_left) QRect стрелки сортировки — слева, перед текстом."""
        return QRect(sec_rect.left() + 4, sec_rect.top(), self._SORT_W, sec_rect.height())

    def _indicator_zones(self, logical_index: int, rect: QRect) -> list:
        """Возвращает [(QRect, tooltip)] для каждого индикатора, справа налево."""
        indicators = self._col_indicators.get(logical_index, [])
        if not indicators:
            return []
        f_cnt = QFont(); f_cnt.setPixelSize(10); f_cnt.setBold(True)
        fm    = QFontMetrics(f_cnt)
        arr_w = 18
        cur_x = rect.right() - arr_w - 6
        zones = []
        for lbl, cnt, tip in reversed(indicators):
            lw   = 18 if lbl in self._IC_CHARS else fm.horizontalAdvance(lbl)
            cw   = fm.horizontalAdvance(str(cnt))
            zone = QRect(cur_x - cw - 2 - lw, rect.top(), lw + 2 + cw, rect.height())
            zones.append((zone, tip))
            cur_x -= lw + 2 + cw + 4
        return zones

    def set_has_selection(self, has: bool):
        if self._has_sel != has:
            self._has_sel = has
            if not has:
                self._del_hovered = False
                self.viewport().unsetCursor()
            self.viewport().update()

    def add_search_col(self, col: int):
        """Регистрирует столбец как поисковой и создаёт поле ввода."""
        self._search_cols.add(col)
        le = QLineEdit(self.viewport())
        le.setPlaceholderText("Поиск...")
        le.hide()
        le.setStyleSheet(
            "QLineEdit {"
            "  background: rgba(255,255,255,0.45);"
            "  border: 1px solid rgba(7,65,79,0.5);"
            "  border-radius: 3px;"
            f"  color: {C.BRAND};"
            f"  font-size: {FS.SMALL}px;"
            "  padding: 1px 4px;"
            "}"
        )
        le.textChanged.connect(lambda text, c=col: self.searchChanged.emit(c, text))
        self._search_fields[col] = le
        self._search_active[col] = False

    def _toggle_search(self, col: int):
        now = not self._search_active.get(col, False)
        self._search_active[col] = now
        le = self._search_fields.get(col)
        if le:
            if now:
                le.show()
                le.setFocus()
            else:
                le.hide()
                le.clear()
        self.viewport().update()

    def _compute_ind_left_x(self, logical: int, arr_left: int) -> int:
        """Левая граница зоны индикаторов (= правая граница текста/поля)."""
        indicators = self._col_indicators.get(logical, [])
        if not indicators:
            return arr_left - 2
        f_cnt = QFont(); f_cnt.setPixelSize(10); f_cnt.setBold(True)
        fm    = QFontMetrics(f_cnt)
        total = sum(
            (18 if lbl in self._IC_CHARS else fm.horizontalAdvance(lbl))
            + 2 + fm.horizontalAdvance(str(cnt)) + 4
            for lbl, cnt, _ in indicators
        ) - 4
        return arr_left - 2 - total - 6

    def _search_icon_zone(self, logical: int, sec_rect: QRect) -> QRect:
        """QRect иконки поиска или закрытия поиска для кликов/курсора."""
        IC_W   = 22
        ind_lx = self._compute_ind_left_x(logical, sec_rect.right() - 18 - 2)
        if self._search_active.get(logical, False):
            return QRect(ind_lx - IC_W - 4, sec_rect.top(), IC_W, sec_rect.height())
        label  = str(self.model().headerData(
            logical, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or "")
        f      = QFont(); f.setPixelSize(12); f.setBold(True)
        max_tw = max(0, ind_lx - sec_rect.left() - IC_W - 16)
        tw     = min(QFontMetrics(f).horizontalAdvance(label), max_tw)
        si_x   = min(sec_rect.left() + 10 + tw + 4, ind_lx - IC_W - 4)
        return QRect(si_x, sec_rect.top(), IC_W, sec_rect.height())

    # -- mouse events ---------------------------------------------------------

    def mouseMoveEvent(self, event):
        pos  = event.position().toPoint()
        x    = pos.x()
        hand = False

        # -- кнопка удаления --
        if self._del_col >= 0 and self._has_sel:
            sec_x = self.sectionViewportPosition(self._del_col)
            sec_w = self.sectionSize(self._del_col)
            hov   = sec_x <= x < sec_x + sec_w
            if hov != self._del_hovered:
                self._del_hovered = hov
                self.viewport().update()
            if hov:
                hand = True
        else:
            if self._del_hovered:
                self._del_hovered = False
                self.viewport().update()

        # -- иконки поиска --
        if not hand:
            logical = self.logicalIndexAt(x)
            if logical in self._search_cols:
                sec_x    = self.sectionViewportPosition(logical)
                sec_rect = QRect(sec_x, 0, self.sectionSize(logical), self.height())
                if self._search_icon_zone(logical, sec_rect).contains(pos):
                    hand = True

        if hand:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.viewport().unsetCursor()

        # -- тултипы индикаторов --
        tip_col, tip_txt = -1, ""
        logical = self.logicalIndexAt(x)
        if logical >= 0:
            sec_x    = self.sectionViewportPosition(logical)
            sec_rect = QRect(sec_x, 0, self.sectionSize(logical), self.height())
            for zone, tip in self._indicator_zones(logical, sec_rect):
                if zone.contains(pos):
                    tip_col, tip_txt = logical, tip
                    break
        if tip_col != self._tip_col or tip_txt != self._tip_text:
            self._tip_col  = tip_col
            self._tip_text = tip_txt
            if tip_col >= 0:
                AppTooltip.show_at(tip_txt, self.viewport().mapToGlobal(pos))
            else:
                AppTooltip.hide()

        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._del_hovered:
            self._del_hovered = False
            self.viewport().update()
        self.viewport().unsetCursor()
        if self._tip_col >= 0:
            self._tip_col  = -1
            self._tip_text = ""
            AppTooltip.hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            x   = pos.x()

            if self._del_col >= 0 and self._has_sel:
                sec_x = self.sectionViewportPosition(self._del_col)
                sec_w = self.sectionSize(self._del_col)
                if sec_x <= x < sec_x + sec_w:
                    self.deleteRequested.emit()
                    return

            logical = self.logicalIndexAt(x)
            if logical in self._search_cols:
                sec_x    = self.sectionViewportPosition(logical)
                sec_rect = QRect(sec_x, 0, self.sectionSize(logical), self.height())
                if self._search_icon_zone(logical, sec_rect).contains(pos):
                    self._toggle_search(logical)
                    return

            # sort_left: клик только по стрелке, 3 состояния по кругу — по
            # возрастанию → по убыванию → без сортировки (исходный порядок).
            if self._sort_left and logical >= 0:
                sec_x    = self.sectionViewportPosition(logical)
                sec_rect = QRect(sec_x, 0, self.sectionSize(logical), self.height())
                if self._sort_icon_zone(sec_rect).contains(pos):
                    cur_col   = self.sortIndicatorSection()
                    cur_order = self.sortIndicatorOrder()
                    if cur_col != logical:
                        self.setSortIndicator(logical, Qt.SortOrder.AscendingOrder)
                    elif cur_order == Qt.SortOrder.AscendingOrder:
                        self.setSortIndicator(logical, Qt.SortOrder.DescendingOrder)
                    else:
                        self.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
                    return

        super().mousePressEvent(event)

    # -- paint ----------------------------------------------------------------

    def paintSection(self, painter: QPainter, rect: QRect, logical_index: int):
        if not rect.isValid():
            return
        painter.save()
        painter.fillRect(rect, self._BG)

        painter.setPen(QPen(self._BORDER, 1))
        painter.drawLine(rect.right(), rect.top() + 4, rect.right(), rect.bottom() - 4)

        model = self.model()
        label = (
            str(model.headerData(logical_index, Qt.Orientation.Horizontal,
                                 Qt.ItemDataRole.DisplayRole) or "")
            if model else ""
        )
        if label:
            # Одиночный символ Material Symbols — рисуем как иконку, без стрелок
            if len(label) == 1 and 0xE000 <= ord(label) <= 0xF8FF:
                f_ic = QFont("Material Symbols Rounded")
                f_ic.setPixelSize(18)
                if logical_index == self._del_col:
                    if not self._has_sel:
                        color = self._DEL_OFF
                        fill  = 0.0
                    elif self._del_hovered:
                        color = self._DEL_HOV
                        fill  = 1.0
                    else:
                        color = self._DEL_ON
                        fill  = 0.0
                    f_ic.setVariableAxis(self._fill_tag, fill)
                    painter.setPen(color)
                else:
                    painter.setPen(self._FG)
                painter.setFont(f_ic)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
            elif self._sort_left:
                sort_rect    = self._sort_icon_zone(rect)
                content_left = self._content_left(rect.left())
                text_rect    = QRect(content_left, rect.top(),
                                     rect.right() - content_left - 4, rect.height())
                painter.setPen(self._FG)
                f = QFont(); f.setPixelSize(12); f.setBold(True)
                painter.setFont(f)
                painter.drawText(text_rect,
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 label)

                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                cx = sort_rect.left() + sort_rect.width() // 2
                cy = sort_rect.top() + sort_rect.height() // 2
                is_sorted = (self.sortIndicatorSection() == logical_index)
                asc  = is_sorted and self.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
                desc = is_sorted and self.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self._ARR_ON if asc else self._ARR_OFF)
                painter.drawPolygon(QPolygon([
                    QPoint(cx - 4, cy - 1), QPoint(cx + 4, cy - 1), QPoint(cx, cy - 6),
                ]))
                painter.setBrush(self._ARR_ON if desc else self._ARR_OFF)
                painter.drawPolygon(QPolygon([
                    QPoint(cx - 4, cy + 1), QPoint(cx + 4, cy + 1), QPoint(cx, cy + 6),
                ]))
            else:
                arr_w    = 18
                arr_rect = QRect(rect.right() - arr_w - 2, rect.top(), arr_w, rect.height())

                # Индикаторы (справа налево, перед стрелками сортировки)
                indicators = self._col_indicators.get(logical_index, [])
                ind_left_x = arr_rect.left() - 2
                if indicators:
                    f_ic  = QFont("Material Symbols Rounded"); f_ic.setPixelSize(14)
                    f_cnt = QFont(); f_cnt.setPixelSize(10); f_cnt.setBold(True)
                    fm    = QFontMetrics(f_cnt)
                    cur_x = ind_left_x
                    for lbl, cnt, _ in reversed(indicators):
                        lw    = 18 if lbl in self._IC_CHARS else fm.horizontalAdvance(lbl)
                        cw    = fm.horizontalAdvance(str(cnt))
                        lbl_r = QRect(cur_x - cw - 2 - lw, rect.top(), lw, rect.height())
                        cnt_r = QRect(cur_x - cw, rect.top(), cw, rect.height())
                        painter.setFont(f_ic if lbl in self._IC_CHARS else f_cnt)
                        painter.setPen(self._IC_COLOR)
                        painter.drawText(lbl_r, Qt.AlignmentFlag.AlignCenter, lbl)
                        painter.setFont(f_cnt)
                        painter.drawText(cnt_r, Qt.AlignmentFlag.AlignCenter, str(cnt))
                        cur_x -= lw + 2 + cw + 4
                    ind_left_x = cur_x - 4

                # Стрелки сортировки
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                cx = arr_rect.left() + arr_rect.width() // 2
                cy = arr_rect.top() + arr_rect.height() // 2
                is_sorted = (self.sortIndicatorSection() == logical_index)
                asc  = is_sorted and self.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
                desc = is_sorted and self.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self._ARR_ON if asc else self._ARR_OFF)
                painter.drawPolygon(QPolygon([
                    QPoint(cx - 4, cy - 1), QPoint(cx + 4, cy - 1), QPoint(cx, cy - 6),
                ]))
                painter.setBrush(self._ARR_ON if desc else self._ARR_OFF)
                painter.drawPolygon(QPolygon([
                    QPoint(cx - 4, cy + 1), QPoint(cx + 4, cy + 1), QPoint(cx, cy + 6),
                ]))

                # Заголовок / поле поиска
                IC_W      = 22
                is_srch   = logical_index in self._search_cols
                is_active = self._search_active.get(logical_index, False)

                if is_srch and is_active:
                    off_x    = ind_left_x - IC_W - 4
                    off_rect = QRect(off_x, rect.top(), IC_W, rect.height())
                    le       = self._search_fields[logical_index]
                    le_h     = 22
                    le_rect  = QRect(rect.left() + 8,
                                     rect.top() + (rect.height() - le_h) // 2,
                                     max(0, off_x - rect.left() - 10),
                                     le_h)
                    le.setGeometry(le_rect)
                    if not le.isVisible():
                        le.show()
                        le.setFocus()
                    f_ico = QFont("Material Symbols Rounded"); f_ico.setPixelSize(18)
                    painter.setFont(f_ico)
                    painter.setPen(self._FG)
                    painter.drawText(off_rect, Qt.AlignmentFlag.AlignCenter, chr(0xEA76))
                else:
                    if is_srch:
                        le = self._search_fields.get(logical_index)
                        if le and le.isVisible():
                            le.hide()

                    if is_srch:
                        title_max_w = max(0, ind_left_x - rect.left() - IC_W - 16)
                    else:
                        title_max_w = max(0, ind_left_x - rect.left() - 6)

                    text_rect = QRect(rect.left() + 10, rect.top(), title_max_w, rect.height())
                    painter.setPen(self._FG)
                    f = QFont(); f.setPixelSize(12); f.setBold(True)
                    painter.setFont(f)
                    painter.drawText(text_rect,
                                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                     label)

                    if is_srch:
                        fm_t  = QFontMetrics(f)
                        tw    = min(fm_t.horizontalAdvance(label), title_max_w)
                        si_x  = min(rect.left() + 10 + tw + 4, ind_left_x - IC_W - 4)
                        si_r  = QRect(si_x, rect.top(), IC_W, rect.height())
                        f_ico = QFont("Material Symbols Rounded"); f_ico.setPixelSize(18)
                        painter.setFont(f_ico)
                        painter.setPen(self._FG)
                        painter.drawText(si_r, Qt.AlignmentFlag.AlignCenter, chr(0xE8B6))

        painter.restore()
