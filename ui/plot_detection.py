"""Определение номера участка из строк банковской выписки."""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict as _defaultdict

import pandas as pd

from core.utils import DATA_DIR
_PLOTS_FILE = DATA_DIR / "snt_plots.json"


def _load_sadovods():
    """Загружает пары (участок, владелец) из snt_plots.json."""
    try:
        if os.path.exists(_PLOTS_FILE):
            with open(_PLOTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            result = []
            for entry in data:
                num = entry.get("num", "")
                for owner in entry.get("owners", []):
                    name = owner["name"] if isinstance(owner, dict) else owner
                    if name.strip():
                        result.append((num, name))
            return result
    except Exception:
        pass
    return []


def _build_plot_lookup(sadovods):
    sur = _defaultdict(list)
    fio = _defaultdict(list)
    for plot, name in sadovods:
        n = name.lower().strip()
        fio[n].append(plot)
        parts = n.split()
        if parts:
            s = parts[0]
            if plot not in sur[s]:
                sur[s].append(plot)
    return sur, fio


_SURNAME_MAP, _FIO_MAP = _build_plot_lookup(_load_sadovods())


def load_plot_numbers() -> list[str]:
    """Возвращает отсортированный список номеров участков из snt_plots.json."""
    try:
        if os.path.exists(_PLOTS_FILE):
            with open(_PLOTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            nums = sorted(
                {str(e.get("num", "")) for e in data if e.get("num")},
                key=lambda s: (0, int(s)) if s.isdigit() else (1, s),
            )
            return nums
    except Exception:
        pass
    return []


_PAT_PLOT = [
    re.compile(r'участ[а-яё]*\s*[№#]?\s*(\d+(?:/\d+)?)', re.I),
    re.compile(r'\bуч[.:№#]?\s*(\d+(?:/\d+)?)', re.I),
]
_PAT_MULTI = re.compile(r'(?:участ[а-яё]*|уч[.:№#]?)\s*(\d+)\s*[,и]\s*(\d+)', re.I)
_NOISE = re.compile(
    r'(?:№|n)\s*\d{3,}[/\-]\d+|м-\d+|счет[а-яё]?\s*[№#]?\s*\d{5,}'
    r'|дог[а-яё.]*\s*[№#]?\s*[\w\-/]+|нк рф|фз\s*№|требование\s*№'
    r'|решени[юя].{0,30}№|\d{4,}', re.I)


def _find_in_text(text):
    clean = _NOISE.sub(' ', text.lower())
    m = _PAT_MULTI.search(clean)
    if m:
        return [m.group(1), m.group(2)]
    res = []
    for pat in _PAT_PLOT:
        for m in pat.finditer(clean):
            v = m.group(1).strip()
            if v and v not in res:
                res.append(v)
    return res


def _find_by_name(text):
    t = text.lower()
    for fio_n, plots in _FIO_MAP.items():
        if fio_n in t:
            return list(dict.fromkeys(plots))
    seen: dict = {}
    for fio_n, plots in _FIO_MAP.items():
        parts = fio_n.split()
        if len(parts) >= 2:
            short = parts[0] + " " + parts[1]
            if len(short) > 5 and re.search(r'\b' + re.escape(short) + r'\b', t):
                for p in plots:
                    seen.setdefault(short, [])
                    if p not in seen[short]:
                        seen[short].append(p)
    for short, plots in seen.items():
        return list(dict.fromkeys(plots))
    for sur, plots in _SURNAME_MAP.items():
        if re.search(r'\b' + re.escape(sur) + r'\b', t):
            return list(dict.fromkeys(plots))
    return []


def _find_by_contragent(c):
    parts = re.split(r'/{1,}', c.lower())
    for p in parts:
        p = p.strip()
        if len(p) < 5:
            continue
        for fio_n, plots in _FIO_MAP.items():
            if fio_n in p or p in fio_n:
                return list(dict.fromkeys(plots))
        words = p.split()
        if words and words[0] in _SURNAME_MAP:
            return list(dict.fromkeys(_SURNAME_MAP[words[0]]))
    return []


def get_plot(row: dict) -> str:
    """Возвращает первый найденный номер участка или пустую строку.
    При нескольких кандидатах берётся первый — строка попадёт в замечание
    «Неизвестный участок», если значение отсутствует в базе."""
    text = str(row.get("Назначение", "") or "")
    cont = str(row.get("Контрагент",  "") or "")

    p = _find_in_text(text)
    if p:
        return str(p[0])

    p = _find_by_name(text)
    if p:
        return str(p[0])

    p = _find_by_name(cont)
    if p:
        return str(p[0])

    p = _find_by_contragent(cont)
    if p:
        return str(p[0])

    return ""


def apply_plot_column(df: pd.DataFrame) -> pd.DataFrame:
    global _SURNAME_MAP, _FIO_MAP
    _SURNAME_MAP, _FIO_MAP = _build_plot_lookup(_load_sadovods())
    df = df.copy()
    df["Участок"] = df.apply(lambda r: get_plot(r.to_dict()), axis=1)
    cols = list(df.columns)
    cols.remove("Участок")
    ins = cols.index("Категория") + 1 if "Категория" in cols else len(cols)
    cols.insert(ins, "Участок")
    return df[cols]
