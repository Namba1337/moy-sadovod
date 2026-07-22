"""Общие утилиты ядра — без UI и Qt, без I/O."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import pandas as pd

DATA_DIR = Path(__file__).parent.parent / "data"


def _read_json(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _ensure_df(df) -> Optional[pd.DataFrame]:
    if df is None or len(df) == 0:
        return None
    needed = {"Дата", "Сумма", "Категория", "Участок"}
    if not needed.issubset(df.columns):
        return None
    return df


def truncate_filename(name: str, limit: int = 24) -> str:
    """Сокращает длинное имя файла: первые 17 + «…» + последние 7 символов.

    Правило единое для всего приложения — карточка контакта (загрузка
    документов) и заголовок/меню проекта используют его одинаково.
    """
    if len(name) > limit:
        return name[:17] + "…" + name[-7:]
    return name


def fmt_money(v) -> str:
    if v is None:
        return "—"
    v = float(v)
    if abs(v) < 0.005:
        return "0,00 ₽"
    sign = "−" if v < 0 else ""
    return f"{sign}{abs(v):,.2f} ₽".replace(",", " ").replace(".", ",")
