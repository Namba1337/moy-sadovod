"""Общие утилиты ядра — без UI и Qt, без I/O."""
from __future__ import annotations

import json
import os
from typing import Optional

import pandas as pd


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


def fmt_money(v) -> str:
    if v is None:
        return "—"
    v = float(v)
    if abs(v) < 0.005:
        return "0,00 ₽"
    sign = "−" if v < 0 else ""
    return f"{sign}{abs(v):,.2f} ₽".replace(",", " ").replace(".", ",")
