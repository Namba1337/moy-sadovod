"""Автоматическая категоризация строк банковской выписки."""
from __future__ import annotations

import json
import re

import pandas as pd
from PyQt6.QtGui import QColor

from core.utils import DATA_DIR

_CATEGORIES_FILE = DATA_DIR / "snt_categories.json"

_DEFAULT_CATEGORY_COLORS = {
    "Членские взносы (авто)":                   QColor(30,  74, 120),
    "Членские взносы + Электроэнергия (авто)":  QColor(20,  90, 100),
    "Электроэнергия (от садоводов) (авто)":     QColor(90,  80,  10),
    "Оплата электроэнергии (поставщик) (авто)": QColor(110, 55,  10),
    "Налоги и штрафы (авто)":                   QColor(120, 30,  30),
    "Программное обеспечение (авто)":           QColor(80,  40, 110),
    "Материалы и работы (авто)":                QColor(20,  90,  40),
    "Банковские комиссии (авто)":               QColor(80,  65,  20),
    "Возврат (авто)":                           QColor(10,  90,  75),
    "Прочее (авто)":                            QColor(55,  55,  60),
}

CATEGORY_COLORS = dict(_DEFAULT_CATEGORY_COLORS)

# Категории, которые нельзя удалить или переименовать.
PROTECTED_CATEGORIES: frozenset[str] = frozenset([
    "Электроэнергия (от садоводов)",
    "Членские взносы",
])


# ---------------------------------------------------------------------------
# Хелперы для чтения/записи файла категорий
# ---------------------------------------------------------------------------

def _load_raw() -> dict:
    try:
        if _CATEGORIES_FILE.exists():
            with open(_CATEGORIES_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {"categories": d}
    except Exception:
        pass
    return {}


def _save_raw(data: dict):
    try:
        _CATEGORIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_CATEGORIES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_user_categories() -> list[str]:
    """Загружает список категорий из файла, при отсутствии — встроенные.
    Защищённые категории всегда присутствуют в начале списка."""
    raw = _load_raw()
    cats = raw.get("categories")
    base = list(cats) if isinstance(cats, list) and cats \
        else list(_DEFAULT_CATEGORY_COLORS.keys())
    # Добавляем защищённые в начало, если их нет
    for p in reversed(sorted(PROTECTED_CATEGORIES)):
        if p not in base:
            base.insert(0, p)
    return base


def load_user_category_colors() -> dict[str, QColor]:
    """Загружает пользовательские цвета категорий."""
    result: dict[str, QColor] = {}
    for name, rgb in _load_raw().get("colors", {}).items():
        if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
            result[name] = QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]))
    return result


def save_user_categories(cats: list[str]):
    """Сохраняет список категорий, сохраняя имеющиеся цвета."""
    data = _load_raw()
    data["categories"] = cats
    _save_raw(data)


def save_user_category_color(cat: str, color: QColor):
    """Сохраняет цвет одной категории."""
    data = _load_raw()
    colors = data.get("colors", {})
    colors[cat] = [color.red(), color.green(), color.blue()]
    data["colors"] = colors
    _save_raw(data)


def delete_user_category(name: str):
    """Удаляет категорию из файла и из памяти (CATEGORY_COLORS, ALL_CATEGORIES)."""
    data = _load_raw()
    cats = data.get("categories", list(_DEFAULT_CATEGORY_COLORS.keys()))
    if name in cats:
        cats.remove(name)
    data["categories"] = cats
    data.get("colors", {}).pop(name, None)
    _save_raw(data)
    try:
        ALL_CATEGORIES.remove(name)
    except ValueError:
        pass
    CATEGORY_COLORS.pop(name, None)


def rename_user_category(old_name: str, new_name: str):
    """Переименовывает категорию в файле и в памяти (CATEGORY_COLORS, ALL_CATEGORIES)."""
    data = _load_raw()
    # список категорий
    cats = data.get("categories", list(_DEFAULT_CATEGORY_COLORS.keys()))
    if old_name in cats:
        cats[cats.index(old_name)] = new_name
    data["categories"] = cats
    # цвета: переносим ключ
    colors = data.get("colors", {})
    if old_name in colors:
        colors[new_name] = colors.pop(old_name)
    data["colors"] = colors
    _save_raw(data)
    # обновляем глобальные переменные в памяти
    try:
        ALL_CATEGORIES[ALL_CATEGORIES.index(old_name)] = new_name
    except ValueError:
        pass
    if old_name in CATEGORY_COLORS:
        CATEGORY_COLORS[new_name] = CATEGORY_COLORS.pop(old_name)


# Применяем пользовательские цвета поверх дефолтных
CATEGORY_COLORS.update(load_user_category_colors())

ALL_CATEGORIES = load_user_categories()


def _ensure_category(name: str):
    """Добавляет категорию в ALL_CATEGORIES (и в файл), если её там ещё нет."""
    if name not in ALL_CATEGORIES:
        ALL_CATEGORIES.append(name)
        if name not in CATEGORY_COLORS and name in _DEFAULT_CATEGORY_COLORS:
            CATEGORY_COLORS[name] = _DEFAULT_CATEGORY_COLORS[name]
        save_user_categories(list(ALL_CATEGORIES))


def categorize_row(row: dict) -> str:
    text      = str(row.get("Назначение", "")).lower()
    contragent = str(row.get("Контрагент", "")).lower()

    if "пермэнергосбыт" in contragent or "пермская энергосбытовая" in contragent:
        result = "Оплата электроэнергии (поставщик) (авто)"
    elif (  # electro + member
        any(w in text for w in [
            "электроэнерги", "электричеств", "эл/энерги", "эл.энерги",
            "эл.знерги", "злектроэнерги", "эл энерги", "элект.энерги",
            "электро энерги", "свет", "э/э", "квт", "кВт",
            "зл.знерги", "электорэнерги", "потреблен", "электролени",
            "электротовар", "эликтричеств", "эл,энерги", "эл. энерги",
        ])
        and any(w in text for w in [
            "членск", "членнск", "чл.взн", "чл взн", "чл взнос",
            "взносы", "взнос", "садоводческий взнос", "садоводческое товарищество",
            "общественные нужды", "обществен нужды", "жкх", "ежегодный взнос",
        ])
    ):
        result = "Членские взносы + Электроэнергия (авто)"
    elif any(w in text for w in [
        "электроэнерги", "электричеств", "эл/энерги", "эл.энерги",
        "эл.знерги", "злектроэнерги", "эл энерги", "элект.энерги",
        "электро энерги", "свет", "э/э", "квт", "кВт",
        "зл.знерги", "электорэнерги", "потреблен", "электролени",
        "электротовар", "эликтричеств", "эл,энерги", "эл. энерги",
    ]):
        result = "Электроэнергия (от садоводов) (авто)"
    elif any(w in text for w in [
        "членск", "членнск", "чл.взн", "чл взн", "чл взнос",
        "взносы", "взнос", "садоводческий взнос", "садоводческое товарищество",
        "общественные нужды", "обществен нужды", "жкх", "ежегодный взнос",
    ]):
        result = "Членские взносы (авто)"
    elif re.search(r"долг|аванс|уч\.19;|2026\s*год|2025\s*год", text):
        result = "Членские взносы (авто)"
    elif ("контур" in text or "контур" in contragent
            or "программ" in text or "эвм" in text
            or "бухгалтер" in text or "модуль" in text):
        result = "Программное обеспечение (авто)"
    elif any(w in text for w in [
        "налог","ифнс","казначейств","взыскани","штраф","пени",
        "нк рф","енс","страховани","фз №125","требование",
    ]):
        result = "Налоги и штрафы (авто)"
    elif "комисси" in text or "рко" in text or "задолженност" in text:
        result = "Банковские комиссии (авто)"
    elif "возврат" in text:
        result = "Возврат (авто)"
    elif any(w in text for w in [
        "материал","уборка снега","транспортн","подряд",
        "строит","хозяйственн","счет на оплату","счёт на оплату",
        "оплата по счету","оплата по счёту","оплата по договору",
    ]) or any(w in contragent for w in ["ип ", "ооо "]):
        result = "Материалы и работы (авто)"
    else:
        result = "Прочее (авто)"

    _ensure_category(result)
    return result


def apply_categorization(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Категория"] = df.apply(lambda row: categorize_row(row.to_dict()), axis=1)
    cols = list(df.columns)
    cols.remove("Категория")
    idx = cols.index("Назначение") + 1 if "Назначение" in cols else len(cols)
    cols.insert(idx, "Категория")
    return df[cols]
