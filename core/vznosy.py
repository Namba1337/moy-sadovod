"""Чистое ядро расчёта по членским взносам — без UI и Qt.

Модель: периоды с явными датами начала и конца (date_from / date_to).
Тариф может быть фиксированной суммой за период (`per_sqm=False`, поле `amount`)
или ценой за квадратный метр (`per_sqm=True`, поле `rate_sqm`) — тогда
сумма для участка = его площадь × цена за м².

Обратная совместимость: старый формат с полем `date` вместо `date_from`
поддерживается — нормализуется автоматически при загрузке. Корректировки
с полем `year` (ignore_year / charge_override / exempt_year) тоже поддерживаются:
год сопоставляется с периодом по году начала периода.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from core import energy
from core.utils import _read_json, _ensure_df

DATA_DIR = "data"
VZNOSY_RATES_FILE = os.path.join(DATA_DIR, "snt_vznosy_rates.json")
VZNOSY_ADJ_FILE = os.path.join(DATA_DIR, "snt_vznosy_adjustments.json")

CAT_VZNOSY = "Членские взносы"
CAT_MIXED = "Членские взносы + Электроэнергия"
CATS_VZNOSY_INCOME = {CAT_VZNOSY, CAT_MIXED}

KIND_PAYMENT_MANUAL = "payment_manual"
KIND_CHARGE_OVERRIDE = "charge_override"
KIND_EXEMPT_YEAR = "exempt_year"      # backward compat
KIND_EXEMPT_PERIOD = "exempt_period"
KIND_IGNORE_YEAR = "ignore_year"      # backward compat
KIND_IGNORE_PERIOD = "ignore_period"


# ── загрузка / сохранение ─────────────────────────────────────────────

def load_rates() -> list:
    """[{"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"|null, "amount": "...",
         "per_sqm": bool, "rate_sqm": "...", "note": "..."}]

    Старые записи с полем `date` нормализуются на лету."""
    raw = _read_json(VZNOSY_RATES_FILE, [])
    return [_normalize_rate(r) for r in raw]


def save_rates(data: list) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(VZNOSY_RATES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_adjustments() -> dict:
    """{"plot": [{"date", "kind", "amount", "period_from"?, "year"?, "note"}]}"""
    return _read_json(VZNOSY_ADJ_FILE, {})


def save_adjustments(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(VZNOSY_ADJ_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def plot_area_map() -> dict:
    """{"plot_num": float | None}"""
    out: dict = {}
    for p in energy.load_plots():
        num = str(p.get("num", ""))
        if not num:
            continue
        out[num] = energy._to_float(p.get("area"))
    return out


# ── нормализация / периоды ────────────────────────────────────────────

def _normalize_rate(r: dict) -> dict:
    """Приводит запись тарифа к формату с date_from (поддержка старого поля date)."""
    if "date_from" not in r:
        r = dict(r)
        r["date_from"] = r.pop("date", "")
    return r


def _period_key(r: dict) -> str:
    """Ключ периода — строка date_from."""
    return str(r.get("date_from", ""))


def build_periods(rates: list) -> list[dict]:
    """Возвращает список периодов, отсортированных по date_from (по возрастанию).

    Если у периода нет date_to — он открытый (последний активный).
    Поля остаются как есть (не заполняем date_to автоматически).
    """
    if not rates:
        return []
    normalized = [_normalize_rate(r) for r in rates]

    def _sort_key(r: dict):
        d = energy._parse_iso(r.get("date_from", ""))
        return d or date.min

    return sorted(normalized, key=_sort_key)


def _tariff_period_amount(r: dict, area: Optional[float]) -> Optional[float]:
    """Сумма взноса за данный период для участка с указанной площадью."""
    if r.get("per_sqm"):
        rate = energy._to_float(r.get("rate_sqm"))
        if rate is None or area is None:
            return None
        return rate * area
    return energy._to_float(r.get("amount"))


# ── backward compat ───────────────────────────────────────────────────

def tariff_at(d: date, rates: list) -> Optional[dict]:
    """Тариф ЧВ, действующий на дату d (последний с date_from ≤ d)."""
    best_d: Optional[date] = None
    best: Optional[dict] = None
    for r in rates or []:
        rd = energy._parse_iso(r.get("date_from", r.get("date", "")))
        if rd is None or rd > d:
            continue
        if best_d is None or rd > best_d:
            best_d = rd
            best = r
    return best


def tariff_amount_for_year(year: int, rates: list,
                           area: Optional[float]) -> tuple[Optional[float], Optional[dict]]:
    jan1 = date(year, 1, 1)
    tariff = tariff_at(jan1, rates)
    if tariff is None:
        earliest: Optional[tuple[date, dict]] = None
        for r in rates or []:
            rd = energy._parse_iso(r.get("date_from", r.get("date", "")))
            if rd is None:
                continue
            if earliest is None or rd < earliest[0]:
                earliest = (rd, r)
        if earliest is None or earliest[0].year != year:
            return (None, None)
        tariff = earliest[1]
    amount = _tariff_period_amount(tariff, area)
    return (amount, tariff)


# ── корректировки ─────────────────────────────────────────────────────

def _plot_adjustments(plot: str, adjustments: dict) -> list:
    return list(adjustments.get(str(plot), []) or [])


def _period_ignored(plot: str, period_from_str: str, period_year: int,
                    adjustments: dict) -> bool:
    """True если для периода выставлен флаг «Не учитывать»."""
    for adj in _plot_adjustments(plot, adjustments):
        kind = adj.get("kind")
        if kind == KIND_IGNORE_PERIOD and adj.get("period_from") == period_from_str:
            return True
        if kind == KIND_IGNORE_YEAR:
            try:
                if int(adj["year"]) == period_year:
                    return True
            except (TypeError, ValueError, KeyError):
                pass
    return False


def _period_override(plot: str, period_from_str: str, period_year: int,
                     adjustments: dict) -> Optional[dict]:
    """Если для периода есть override/exempt — вернуть последний из них."""
    out: Optional[dict] = None
    for adj in _plot_adjustments(plot, adjustments):
        kind = adj.get("kind")
        if kind == KIND_EXEMPT_PERIOD:
            if adj.get("period_from") == period_from_str:
                out = adj
        elif kind == KIND_EXEMPT_YEAR:
            try:
                if int(adj.get("year")) == period_year:
                    out = adj
            except (TypeError, ValueError):
                pass
        elif kind == KIND_CHARGE_OVERRIDE:
            if adj.get("period_from") == period_from_str:
                out = adj
            else:
                try:
                    if int(adj.get("year")) == period_year:
                        out = adj
                except (TypeError, ValueError):
                    pass
    return out


def _manual_payments(plot: str, adjustments: dict) -> list[dict]:
    """Ручные платежи (kind=payment_manual) с распарсенной датой и суммой."""
    out = []
    for adj in _plot_adjustments(plot, adjustments):
        if adj.get("kind") != KIND_PAYMENT_MANUAL:
            continue
        d = energy._parse_iso(adj.get("date", ""))
        a = energy._to_float(adj.get("amount"))
        if d is None or a is None:
            continue
        out.append({"date": d, "amount": a, "note": str(adj.get("note", ""))})
    out.sort(key=lambda r: r["date"])
    return out


# ── начисления ────────────────────────────────────────────────────────

@dataclass
class PeriodCharge:
    period_from: date
    period_to: Optional[date]       # None = открытый период (последний)
    tariff: Optional[dict]
    amount: Optional[float]         # None если per_sqm без площади
    overridden: bool = False
    area_missing: bool = False
    ignored: bool = False           # помечен «Не учитывать»


def charged_periods_breakdown(plot: str, area: Optional[float], as_of: date,
                               rates: list, adjustments: dict) -> list[PeriodCharge]:
    """Разбивка начислений по периодам до даты as_of включительно."""
    periods = build_periods(rates)
    if not periods:
        return []

    out: list[PeriodCharge] = []
    for i, r in enumerate(periods):
        pf = energy._parse_iso(r.get("date_from", ""))
        if pf is None or pf > as_of:
            continue

        # Конец периода: из поля date_to, иначе дата следующего периода - 1 день
        pt_str = r.get("date_to")
        if pt_str:
            pt = energy._parse_iso(pt_str)
        elif i + 1 < len(periods):
            next_pf = energy._parse_iso(periods[i + 1].get("date_from", ""))
            pt = (next_pf - timedelta(days=1)) if next_pf else None
        else:
            pt = None  # последний период — открытый

        tariff = r
        amount = _tariff_period_amount(tariff, area)
        area_missing = bool(tariff.get("per_sqm") and area is None)
        period_key = _period_key(r)
        period_year = pf.year

        if _period_ignored(plot, period_key, period_year, adjustments):
            out.append(PeriodCharge(
                period_from=pf, period_to=pt, tariff=tariff, amount=0.0,
                ignored=True,
            ))
            continue

        override = _period_override(plot, period_key, period_year, adjustments)
        overridden = False
        if override is not None:
            ov_kind = override.get("kind")
            if ov_kind in (KIND_EXEMPT_PERIOD, KIND_EXEMPT_YEAR):
                amount = 0.0
                overridden = True
                area_missing = False
            else:
                ov_amount = energy._to_float(override.get("amount"))
                if ov_amount is not None:
                    amount = ov_amount
                    overridden = True
                    area_missing = False

        out.append(PeriodCharge(
            period_from=pf, period_to=pt, tariff=tariff, amount=amount,
            overridden=overridden, area_missing=area_missing,
        ))
    return out


def charged_for_plot(plot: str, area: Optional[float], as_of: date,
                     rates: list, adjustments: dict) -> float:
    return sum(
        y.amount for y in charged_periods_breakdown(plot, area, as_of, rates, adjustments)
        if y.amount is not None
    )


# ── платежи ───────────────────────────────────────────────────────────

def payments_breakdown(plot: str, df, adjustments: Optional[dict] = None) -> list[dict]:
    """Хронологический список платежей по ЧВ для участка."""
    out: list[dict] = []
    d = _ensure_df(df)
    if d is not None:
        sub = d[d["Категория"].isin(CATS_VZNOSY_INCOME)].copy()
        for _, row in sub.iterrows():
            amount = energy._row_amount_for_plot(row, plot)
            if amount <= 0:
                continue
            out.append({
                "date": row["Дата"].date() if pd.notna(row["Дата"]) else None,
                "amount": amount,
                "mixed": row.get("Категория") == CAT_MIXED,
                "purpose": str(row.get("Назначение", "")),
                "source": "csv",
            })

    if adjustments:
        for m in _manual_payments(plot, adjustments):
            out.append({
                "date": m["date"],
                "amount": m["amount"],
                "mixed": False,
                "purpose": m["note"],
                "source": "manual",
            })

    out.sort(key=lambda r: r["date"] or date.min)
    return out


def paid_for_plot(plot: str, df, as_of: date,
                  adjustments: Optional[dict] = None) -> float:
    return sum(
        p["amount"] for p in payments_breakdown(plot, df, adjustments)
        if p["date"] is not None and p["date"] <= as_of
    )


def paid_by_period(plot: str, df, as_of: date,
                   periods: list, adjustments: Optional[dict] = None) -> dict[str, float]:
    """Возвращает {period_key: сумма_оплаченного} — платёж относится
    к периоду, в котором находится его дата."""
    out: dict[str, float] = {}
    for p in payments_breakdown(plot, df, adjustments):
        if p["date"] is None or p["date"] > as_of:
            continue
        for r in periods:
            pf = energy._parse_iso(r.get("date_from", ""))
            pt_str = r.get("date_to")
            pt = energy._parse_iso(pt_str) if pt_str else None
            if pf is None:
                continue
            if p["date"] >= pf and (pt is None or p["date"] <= pt):
                key = _period_key(r)
                out[key] = out.get(key, 0.0) + p["amount"]
                break
    return out


# ── баланс ────────────────────────────────────────────────────────────

@dataclass
class VznosyBalance:
    plot: str
    area: Optional[float]
    charged: float
    paid: float
    debt: float
    last_year: Optional[int]
    years_unpaid: Optional[int]
    breakdown: list[PeriodCharge] = field(default_factory=list)
    area_missing_warning: bool = False


def balance_for_plot(plot: str, area: Optional[float], as_of: date,
                     rates: list, adjustments: dict, df) -> VznosyBalance:
    periods = build_periods(rates)
    breakdown = charged_periods_breakdown(plot, area, as_of, rates, adjustments)
    charged = sum(y.amount for y in breakdown if y.amount is not None)

    py = paid_by_period(plot, df, as_of, periods, adjustments)

    # Платежи из игнорируемых периодов не учитываем в итоге —
    # иначе они превращаются в фиктивный «аванс».
    ignored_keys = {y.period_from.isoformat() for y in breakdown if y.ignored}
    paid_in_active = sum(v for k, v in py.items() if k not in ignored_keys)
    # Платежи, не попавшие ни в один период (до первого периода / в пробелах)
    total_raw = paid_for_plot(plot, df, as_of, adjustments)
    paid_in_any = sum(py.values())
    paid = paid_in_active + max(0.0, total_raw - paid_in_any)

    debt = charged - paid

    last_year: Optional[int] = None
    for y in breakdown:
        if y.amount is not None:
            last_year = y.period_from.year

    years_unpaid: Optional[int] = None
    if breakdown:
        count = 0
        for y in reversed(breakdown):
            if y.amount is None or y.amount <= 0:
                continue
            key = y.period_from.isoformat()
            if py.get(key, 0.0) < y.amount * 0.5:
                count += 1
            else:
                break
        years_unpaid = count

    return VznosyBalance(
        plot=plot,
        area=area,
        charged=charged,
        paid=paid,
        debt=debt,
        last_year=last_year,
        years_unpaid=years_unpaid,
        breakdown=breakdown,
        area_missing_warning=any(y.area_missing for y in breakdown),
    )


# ── палитра для UI ────────────────────────────────────────────────────

def debt_color(debt: float, annual_avg: float = 0.0) -> str:
    """Hex-цвет для индикации долга. Пороги в долях годовой суммы."""
    if debt <= 0:
        return "#2e7d32"
    threshold = max(annual_avg, 1000.0)
    if debt <= 0.25 * threshold:
        return "#f9a825"
    if debt <= 0.75 * threshold:
        return "#ef6c00"
    return "#c62828"
