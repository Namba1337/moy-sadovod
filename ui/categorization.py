"""Автоматическая категоризация строк банковской выписки."""
from __future__ import annotations

import re

import pandas as pd
from PyQt6.QtGui import QColor


CATEGORY_COLORS = {
    "Членские взносы":                   QColor(30,  74, 120),
    "Членские взносы + Электроэнергия":  QColor(20,  90, 100),
    "Электроэнергия (от садоводов)":     QColor(90,  80,  10),
    "Оплата электроэнергии (поставщик)": QColor(110, 55,  10),
    "Налоги и штрафы":                   QColor(120, 30,  30),
    "Программное обеспечение":           QColor(80,  40, 110),
    "Материалы и работы":                QColor(20,  90,  40),
    "Банковские комиссии":               QColor(80,  65,  20),
    "Возврат":                           QColor(10,  90,  75),
    "Подотчётные суммы":                 QColor(60,  85,  20),
    "Прочее":                            QColor(55,  55,  60),
}

ALL_CATEGORIES = list(CATEGORY_COLORS.keys())


def categorize_row(row: dict) -> str:
    text      = str(row.get("Назначение", "")).lower()
    contragent = str(row.get("Контрагент", "")).lower()

    if "пермэнергосбыт" in contragent or "пермская энергосбытовая" in contragent:
        return "Оплата электроэнергии (поставщик)"

    electro_words = [
        "электроэнерги", "электричеств", "эл/энерги", "эл.энерги",
        "эл.знерги", "злектроэнерги", "эл энерги", "элект.энерги",
        "электро энерги", "свет", "э/э", "квт", "кВт",
        "зл.знерги", "электорэнерги", "потреблен", "электролени",
        "электротовар", "эликтричеств", "эл,энерги", "эл. энерги", "эл. энерги", "эл. энерги",
    ]
    member_words = [
        "членск", "членнск", "чл.взн", "чл взн", "чл взнос",
        "взносы", "взнос", "садоводческий взнос", "садоводческое товарищество",
        "общественные нужды", "обществен нужды", "жкх", "ежегодный взнос",
    ]
    is_electro = any(w in text for w in electro_words)
    is_member  = any(w in text for w in member_words)

    if is_electro and is_member:
        return "Членские взносы + Электроэнергия"
    if is_electro:
        return "Электроэнергия (от садоводов)"
    if is_member:
        return "Членские взносы"

    if re.search(r"долг|аванс|уч\.19;|2026\s*год|2025\s*год", text):
        return "Членские взносы"

    if ("контур" in text or "контур" in contragent
            or "программ" in text or "эвм" in text
            or "бухгалтер" in text or "модуль" in text):
        return "Программное обеспечение"

    nalog_words = [
        "налог","ифнс","казначейств","взыскани","штраф","пени",
        "нк рф","енс","страховани","фз №125","требование",
    ]
    if any(w in text for w in nalog_words):
        return "Налоги и штрафы"

    if "комисси" in text or "рко" in text or "задолженност" in text:
        return "Банковские комиссии"

    if "возврат" in text:
        return "Возврат"

    if "подотчет" in text:
        return "Подотчётные суммы"

    material_words = [
        "материал","уборка снега","транспортн","подряд",
        "строит","хозяйственн","счет на оплату","счёт на оплату",
        "оплата по счету","оплата по счёту","оплата по договору",
    ]
    if any(w in text for w in material_words):
        return "Материалы и работы"
    if any(w in contragent for w in ["ип ", "ооо "]):
        return "Материалы и работы"

    return "Прочее"


def apply_categorization(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Категория"] = df.apply(lambda row: categorize_row(row.to_dict()), axis=1)
    cols = list(df.columns)
    cols.remove("Категория")
    idx = cols.index("Назначение") + 1 if "Назначение" in cols else len(cols)
    cols.insert(idx, "Категория")
    return df[cols]
