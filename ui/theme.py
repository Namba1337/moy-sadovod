"""Дизайн-код приложения: цвета, отступы, радиусы, шрифты + QSS-фабрики.

Единственный источник оформления. Виджеты не хардкодят hex-цвета и размеры,
а берут токены отсюда:

    from ui.theme import C, SP, RAD, FS

QSS-фабрики возвращают готовые блоки стилей. Ключевая из них —
``dialog_qss()``: глобальный стиль ставится на QMainWindow и до отдельных
top-level окон (диалогов) НЕ доходит, поэтому каждый диалог раньше копировал
себе кнопки/поля ввода. Теперь BaseDialog (ui.dialogs) подключает общий блок
автоматически.
"""
from __future__ import annotations


# ──────────────────────────────────────────────────────────────────────────
#  Цвета (семантические роли)
# ──────────────────────────────────────────────────────────────────────────

class C:
    # Бренд (primary-кнопки, акценты, активная навигация)
    BRAND         = "#07414F"
    BRAND_HOVER   = "#0B5A6E"
    BRAND_PRESSED = "#062F38"
    BRAND_TINT    = "#C9D8E2"   # выделение строк, шапки таблиц долгов
    BRAND_FAINT   = "#E8F0F5"   # hover-подложки, лёгкие заливки
    BRAND_GHOST   = "#EBF4F6"   # hover иконочных кнопок

    # Текст
    TEXT       = "#1F2937"      # заголовки, значения (поглотил #111827)
    TEXT_BODY  = "#374151"      # основной текст      (поглотил #3C4654)
    TEXT_MUTED = "#6B7280"      # вторичный           (поглотил #6B7686, #4B5563)
    TEXT_FAINT = "#9CA3AF"      # подписи, hints      (поглотил #9AA3AE)

    # Фоны
    BG_WINDOW  = "#E9EDF3"      # фон окна и сайдбара
    BG_SURFACE = "#FFFFFF"      # карточки, contentFrame, диалоги
    BG_SUBTLE  = "#F8F9FA"      # поля ввода, entryBox (поглотил #F6F8FA, #F9FAFB)
    BG_HOVER   = "#F3F4F6"      # hover нейтральных элементов
    BG_ALT_ROW = "#F0F4F8"      # чередование строк    (поглотил #E8ECF4)

    # Рамки
    BORDER       = "#D5DCE4"    # основная (поглотила #D1D5DB, #D8DDE6, #CDD3DC)
    BORDER_LIGHT = "#E5E7EB"    # разделители (поглотила #E3E8EE/EF, #EAEDF1, #E6EAEF)

    # Состояния
    SUCCESS       = "#059669"
    SUCCESS_BG    = "#E6F4EA"
    DANGER        = "#DC2626"
    DANGER_HOVER  = "#B91C1C"
    DANGER_BG     = "#FEF2F2"
    DANGER_BORDER = "#FCA5A5"
    DANGER_MUTED  = "#F3A6A6"   # disabled-состояние danger-кнопки
    WARNING       = "#B45309"
    WARNING_BG    = "#FEF3C7"

    # Данные (семантика денег в сводках/графиках)
    INCOME  = "#2E9E5B"
    EXPENSE = DANGER

    # Шкала долга: легенда и заливка строк — ОДНИ и те же цвета.
    # core.energy/core.vznosy возвращают уровень ('ok'/'low'/'mid'/'high'),
    # hex подставляет UI отсюда.
    DEBT = {
        "ok":   "#059669",
        "low":  "#F9A825",
        "mid":  "#EF6C00",
        "high": "#DC2626",
    }
    DEBT_BG = {                 # светлая заливка строки под уровень
        "ok":   "#C8E6C9",
        "low":  "#FFF9C4",
        "mid":  "#FFE0B2",
        "high": "#FFCDD2",
    }

    # Служебные (таблицы, скроллбары, навигация)
    TABLE_HEADER_BG = "#EEF1F5"   # шапки таблиц (поглотила #E9EDF5)
    SCROLL_HANDLE       = "#C3CAD3"
    SCROLL_HANDLE_HOVER = "#97A1AE"
    NAV_HOVER = "#DDE2EC"         # hover пунктов сайдбара (голубоватый, в тон окну)

    # Кнопки заголовка окна — системная идиома Windows, не трогаем.
    WIN_CLOSE_HOVER   = "#C42B1C"
    WIN_CLOSE_PRESSED = "#B22418"


# ──────────────────────────────────────────────────────────────────────────
#  Метрики
# ──────────────────────────────────────────────────────────────────────────

class SP:
    """Отступы, сетка 4px."""
    XS, S, M, L, XL, PAGE = 4, 8, 12, 16, 20, 24


class RAD:
    """Радиусы скругления."""
    CONTROL = 6    # кнопки, поля ввода, комбобоксы
    FRAME   = 8    # фреймы-подложки, entryBox, таблицы-сводки
    DIALOG  = 12   # карточки диалогов
    PAGE    = 14   # contentFrame (окно вкладки)


class FS:
    """Размеры шрифта, px (pt не используем — на HiDPI масштаб «плывёт»)."""
    CAPTION = 11   # подписи легенд, hints, даты
    SMALL   = 12   # текст таблиц, вторичные подписи
    BODY    = 13   # обычный текст, кнопки, поля ввода
    H3      = 15   # заголовки панелей/секций
    H2      = 17   # заголовки диалогов
    H1      = 20   # заголовок вкладки (pageTitle)


# Высота стандартной кнопки/поля (задаётся паддингами, это справочная величина)
CONTROL_HEIGHT = 32

# Ширина скроллбара главных таблиц (QTreeView). Должна совпадать с шириной
# заглушки в шапках таблиц долгов (ui.common.SB_W).
TREE_SCROLLBAR_W = 12

# Ширина обычных скроллбаров (scrollbar_qss) — панели, списки, диалоги.
SCROLLBAR_W = 6


# ──────────────────────────────────────────────────────────────────────────
#  QSS-фабрики
# ──────────────────────────────────────────────────────────────────────────

def input_qss(selectors: str = "QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox") -> str:
    """Поля ввода внутри диалогов (generic-селекторы по типу)."""
    return f"""
        {selectors} {{
            background: {C.BG_SUBTLE}; border: 1px solid {C.BORDER};
            border-radius: {RAD.CONTROL}px; color: {C.TEXT};
            padding: 6px 10px; font-size: {FS.BODY}px;
        }}
        {", ".join(s.strip() + ":focus" for s in selectors.split(","))} {{
            border: 1px solid {C.BRAND};
        }}
        QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: 18px; }}
        QComboBox QAbstractItemView {{
            background: {C.BG_SURFACE}; border: 1px solid {C.BORDER};
            color: {C.TEXT}; selection-background-color: {C.BRAND_TINT};
            selection-color: {C.BRAND};
        }}
    """


def checkbox_qss() -> str:
    return f"""
        QCheckBox {{
            color: {C.TEXT_BODY}; background: transparent;
            font-size: {FS.BODY}px; spacing: 8px;
        }}
        QCheckBox::indicator {{
            width: 16px; height: 16px; border-radius: 4px;
            border: 1px solid {C.BORDER}; background: {C.BG_SUBTLE};
        }}
        QCheckBox::indicator:checked {{
            background: {C.BRAND}; border-color: {C.BRAND};
        }}
        QCheckBox::indicator:hover {{ border-color: {C.BRAND}; }}
    """


def menu_qss(danger: bool = False) -> str:
    """Контекстные меню. danger=True — подсветка пунктов красной зоной
    (для меню, состоящих из удаляющих действий)."""
    sel_bg = C.DANGER_BG if danger else C.BG_HOVER
    sel_fg = C.DANGER if danger else C.TEXT
    return f"""
        QMenu {{
            background: {C.BG_SURFACE}; border: 1px solid {C.BORDER};
            color: {C.TEXT_BODY}; font-size: {FS.BODY}px; padding: 4px;
        }}
        QMenu::item {{ padding: 8px 16px; border-radius: 4px; }}
        QMenu::item:selected {{ background: {sel_bg}; color: {sel_fg}; }}
        QMenu::separator {{ height: 1px; background: {C.BORDER_LIGHT}; margin: 4px 8px; }}
    """


def scrollbar_qss(width: int = SCROLLBAR_W, track: str = "transparent") -> str:
    """Скроллбары: капсульный бегунок на всю ширину желоба.

    ЕДИНСТВЕННЫЙ источник стиля скроллбаров — подключён и в глобальный QSS
    главного окна (main.py), и в dialog_qss() (стиль всех BaseDialog).

    НЕ писать локальные правила QScrollBar «частично»: непереопределённые
    свойства (например margin) наследуются из этого блока при каскаде,
    ручка сужается, и Qt молча отбрасывает border-radius больше половины
    ширины — торцы становятся острыми. Нужен другой трек (например сплошной
    цвет вместо transparent внутри QScrollArea, где сквозь прозрачность
    просвечивает базовый фон #F0F3F9) — вызывать scrollbar_qss(track=...)
    целиком, а не копировать правила.
    """
    r = width // 2
    return f"""
        QScrollBar:vertical {{
            background: {track}; width: {width}px; border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {C.SCROLL_HANDLE}; border-radius: {r}px;
            min-height: 30px; margin: 0;
        }}
        QScrollBar::handle:vertical:hover {{ background: {C.SCROLL_HANDLE_HOVER}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: {track}; }}
        QScrollBar:horizontal {{
            background: {track}; height: {width}px; border: none;
        }}
        QScrollBar::handle:horizontal {{
            background: {C.SCROLL_HANDLE}; border-radius: {r}px;
            min-width: 30px; margin: 0;
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: {track}; }}
    """


def tree_qss() -> str:
    """Единый стиль главных таблиц (QTreeView#mainTable) — вкладки
    Детализация / ЧВ / Электричество. Единственная копия (раньше две
    расходящиеся: plots_widget._TREE_STYLE и detail_widget._TREE_STYLE)."""
    return f"""
        QTreeView#mainTable {{
            background: {C.BG_SURFACE}; border: none;
            color: {C.TEXT}; font-size: {FS.BODY}px;
            selection-background-color: {C.BRAND_TINT}; selection-color: {C.BRAND};
            alternate-background-color: {C.BG_ALT_ROW};
            outline: 0;
        }}
        QTreeView#mainTable::item {{
            padding: 6px 10px; border-bottom: 1px solid {C.BORDER_LIGHT};
        }}
        /* Ховер строки целиком намеренно не подсвечиваем (просили убрать —
           конфликтовал/мигал с точечными hover-эффектами внутри ячеек,
           см. _CategoryDelegate.paint). */
        QTreeView#mainTable::item:selected {{
            background: {C.BRAND_TINT}; color: {C.BRAND};
        }}
        QTreeView#mainTable::branch {{ background: transparent; }}
        QTreeView#mainTable::branch:has-children:!has-siblings:closed,
        QTreeView#mainTable::branch:closed:has-children:has-siblings,
        QTreeView#mainTable::branch:open:has-children:!has-siblings,
        QTreeView#mainTable::branch:open:has-children:has-siblings {{ image: none; }}
        QTreeView#mainTable QScrollBar:vertical {{
            width: {TREE_SCROLLBAR_W}px; background: transparent; border: none;
        }}
        QTreeView#mainTable QScrollBar::handle:vertical {{
            /* margin 2px сужает ручку до {TREE_SCROLLBAR_W - 4}px — радиус
               не больше её половины, иначе Qt отбросит скругление. */
            background: #B5C8D5; border-radius: {(TREE_SCROLLBAR_W - 4) // 2}px;
            min-height: 24px; margin: 2px;
        }}
        QTreeView#mainTable QScrollBar::add-line:vertical,
        QTreeView#mainTable QScrollBar::sub-line:vertical {{ height: 0; }}
        QTreeView#mainTable QScrollBar::add-page:vertical,
        QTreeView#mainTable QScrollBar::sub-page:vertical {{ background: none; }}
    """


def summary_table_qss() -> str:
    """Таблицы-сводки (QTableWidget#summaryTable): тарифы, карточки участка.
    Единственная копия (раньше три расходящиеся: main.py, _NormsDialog,
    PlotCard/VznosyCard)."""
    return f"""
        QTableWidget#summaryTable {{
            background: {C.BG_SURFACE}; border: 1px solid {C.BORDER_LIGHT};
            border-radius: {RAD.FRAME}px;
            gridline-color: {C.BG_HOVER}; color: {C.TEXT_BODY}; font-size: {FS.SMALL}px;
            selection-background-color: {C.BRAND_FAINT}; selection-color: {C.BRAND};
        }}
        QTableWidget#summaryTable QHeaderView::section {{
            background: {C.TABLE_HEADER_BG}; color: {C.TEXT_MUTED}; border: none;
            border-right: 1px solid {C.BORDER_LIGHT};
            border-bottom: 2px solid {C.BORDER_LIGHT};
            padding: 8px 10px; font-size: {FS.SMALL}px; font-weight: 600;
        }}
        QTableWidget#summaryTable::item {{
            padding: 4px 10px; border-bottom: 1px solid {C.BG_HOVER};
        }}
    """


def calendar_qss() -> str:
    """Всплывающий календарь QDateEdit (штатный QCalendarWidget).

    Попап — отдельное top-level окно, глобальный QSS главного окна до него
    не доходит, поэтому стиль вешается на сам QCalendarWidget точечно —
    см. ui.common.style_date_popup (там же — QTextCharFormat-правки, которые
    QSS не покрывает: красные выходные, строка Пн..Вс)."""
    return f"""
        QCalendarWidget QWidget#qt_calendar_navigationbar {{
            background: {C.BG_SURFACE};
            border-bottom: 1px solid {C.BORDER_LIGHT};
        }}
        QCalendarWidget QToolButton {{
            background: transparent; border: none; border-radius: {RAD.CONTROL}px;
            color: {C.TEXT}; font-size: {FS.BODY}px; font-weight: 600;
            padding: 4px 8px; icon-size: 14px;
        }}
        QCalendarWidget QToolButton:hover {{ background: {C.BRAND_GHOST}; }}
        QCalendarWidget QToolButton:pressed {{ background: {C.BRAND_TINT}; }}
        QCalendarWidget QToolButton::menu-indicator {{ image: none; }}
        QCalendarWidget QMenu {{
            background: {C.BG_SURFACE}; border: 1px solid {C.BORDER};
            color: {C.TEXT_BODY}; font-size: {FS.BODY}px; padding: 4px;
        }}
        QCalendarWidget QMenu::item {{ padding: 6px 14px; border-radius: 4px; }}
        QCalendarWidget QMenu::item:selected {{ background: {C.BG_HOVER}; color: {C.TEXT}; }}
        QCalendarWidget QSpinBox {{
            background: {C.BG_SURFACE}; border: 1px solid {C.BORDER};
            border-radius: 4px; color: {C.TEXT}; font-size: {FS.BODY}px;
            padding: 2px 6px;
            selection-background-color: {C.BRAND}; selection-color: {C.BG_SURFACE};
        }}
        QCalendarWidget QWidget {{ alternate-background-color: {C.BG_SURFACE}; }}
        QCalendarWidget QAbstractItemView {{
            background: {C.BG_SURFACE}; outline: 0;
            color: {C.TEXT}; font-size: {FS.BODY}px;
            selection-background-color: {C.BRAND};
            selection-color: {C.BG_SURFACE};
        }}
        QCalendarWidget QAbstractItemView:disabled {{ color: {C.TEXT_FAINT}; }}
    """


def dialog_qss() -> str:
    """Общий блок для BaseDialog: подписи, поля ввода, чекбоксы, скроллбары,
    сводные таблицы. Кнопки сюда не входят — они стилизуют себя сами
    (ui.buttons), поэтому одинаковы и в диалогах, и во вкладках."""
    return f"""
        QLabel {{ background: transparent; color: {C.TEXT_BODY}; font-size: {FS.BODY}px; }}
        QLabel#dlgTitle {{
            color: {C.TEXT}; font-size: {FS.H2}px; font-weight: 700;
        }}
        QLabel#fieldLabel {{
            color: {C.TEXT_FAINT}; font-size: 10px; background: transparent;
        }}
        QLabel#sectionLabel {{
            color: {C.TEXT_FAINT}; font-size: {FS.CAPTION}px; font-weight: 600;
            letter-spacing: 0.5px;
        }}
        QPushButton#btnPanelClose {{
            background: transparent; border: none; color: {C.TEXT_FAINT};
            font-size: {FS.H3}px; font-weight: 600; border-radius: 12px;
        }}
        QPushButton#btnPanelClose:hover {{
            background: {C.BG_HOVER}; color: {C.TEXT_BODY};
        }}
        QFrame#divider {{ background: {C.BORDER_LIGHT}; max-height: 1px; }}
        QScrollArea {{ background: transparent; border: none; }}

        /* «Страничные» objectName-ы (RatesWidget и т.п. могут жить внутри
           диалога, куда глобальный QSS главного окна не доходит). */
        QLabel#pageTitle {{
            font-size: {FS.H2}px; font-weight: 700;
            color: {C.TEXT}; background: transparent;
        }}
        QLabel#filterLabel {{
            color: {C.TEXT_FAINT}; background: transparent; font-size: {FS.BODY}px;
        }}
        QLabel#statusLabel {{
            color: {C.TEXT_FAINT}; background: transparent; font-size: {FS.SMALL}px;
        }}
        QFrame#filterFrame {{
            background: {C.BG_SUBTLE}; border: 1px solid {C.BORDER_LIGHT};
            border-radius: {RAD.FRAME}px;
        }}
        QLineEdit#searchInput {{
            background: {C.BG_SURFACE}; border: 1px solid {C.BORDER};
            border-radius: {RAD.CONTROL}px; color: {C.TEXT};
            padding: 7px 12px; font-size: {FS.BODY}px;
        }}
        QLineEdit#searchInput:focus {{ border: 1px solid {C.BRAND}; }}
        QComboBox#filterCombo, QDateEdit#datePicker {{
            background: {C.BG_SURFACE}; border: 1px solid {C.BORDER};
            border-radius: {RAD.CONTROL}px; color: {C.TEXT};
            padding: 7px 10px; font-size: {FS.BODY}px;
        }}
        QComboBox#filterCombo::drop-down, QDateEdit#datePicker::drop-down {{
            border: none; width: 18px;
        }}
        {input_qss()}
        {checkbox_qss()}
        {scrollbar_qss()}
        {summary_table_qss()}
    """
