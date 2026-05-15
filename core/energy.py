"""Чистое ядро расчёта по электроэнергии — без UI и Qt.

Все функции принимают подготовленные структуры (dict/list/DataFrame)
и не лезут в файловую систему. Загрузку из JSON делают вспомогательные
функции `load_*`, чтобы вызывающий код мог их подменить или закешировать.
"""
from __future__ import annotations

import calendar
import json
import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd

DATA_DIR = "data"
METERS_FILE = os.path.join(DATA_DIR, "snt_meters.json")
RATES_FILE = os.path.join(DATA_DIR, "snt_rates.json")
REPLACEMENTS_FILE = os.path.join(DATA_DIR, "snt_meter_replacements.json")
BASELINE_FILE = os.path.join(DATA_DIR, "snt_energy_baseline.json")
COMMON_METER_FILE = os.path.join(DATA_DIR, "snt_common_meter.json")
PLOTS_FILE = os.path.join(DATA_DIR, "snt_plots.json")

CAT_ELECTRO_FROM_OWNERS = "Электроэнергия (от садоводов)"
CAT_MIXED = "Членские взносы + Электроэнергия"
CAT_TO_SUPPLIER = "Оплата электроэнергии (поставщик)"
CATS_ELECTRO_INCOME = {CAT_ELECTRO_FROM_OWNERS, CAT_MIXED}


# ── загрузка данных ───────────────────────────────────────────────────

def _read_json(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def load_meters() -> dict:
    """{"plot:year:month": "значение"}"""
    return _read_json(METERS_FILE, {})


def load_rates() -> list:
    """[{"date": "YYYY-MM-DD", "rate": "X.XX", "note": "..."}]"""
    return _read_json(RATES_FILE, [])


def load_replacements() -> dict:
    """{"plot": [{"date", "old_final", "new_initial", "note"}]}"""
    return _read_json(REPLACEMENTS_FILE, {})


def save_replacements(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(REPLACEMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_baseline() -> dict:
    """{"start_date": "YYYY-MM-DD", "balances": {"plot": "сумма"}}"""
    return _read_json(BASELINE_FILE, {"start_date": "", "balances": {}})


def save_baseline(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(BASELINE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_common_meter() -> dict:
    """{"YYYY:M": "значение общего счётчика СНТ на конец месяца"}"""
    return _read_json(COMMON_METER_FILE, {})


def save_common_meter(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(COMMON_METER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_plots() -> list:
    return _read_json(PLOTS_FILE, [])


def owners_map() -> dict:
    return {str(p.get("num", "")): list(p.get("owners", []) or []) for p in load_plots()}


# ── базовые утилиты ───────────────────────────────────────────────────

def reading_date(year: int, month: int) -> date:
    """Дата снятия показания — последний день месяца."""
    return date(year, month, calendar.monthrange(year, month)[1])


def _to_float(s) -> Optional[float]:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    txt = str(s).strip().replace(" ", "").replace(",", ".")
    if not txt:
        return None
    try:
        return float(txt)
    except ValueError:
        return None


def _parse_iso(s: str) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def plot_readings(plot: str, meters: dict) -> list[tuple[int, int, float]]:
    """Все непустые показания участка, отсортированные по (year, month)."""
    out: list[tuple[int, int, float]] = []
    prefix = f"{plot}:"
    for key, val in meters.items():
        if not key.startswith(prefix):
            continue
        parts = key.split(":")
        if len(parts) != 3 or parts[0] != plot:
            continue
        v = _to_float(val)
        if v is None:
            continue
        try:
            y, m = int(parts[1]), int(parts[2])
        except ValueError:
            continue
        out.append((y, m, v))
    out.sort()
    return out


# ── тариф ─────────────────────────────────────────────────────────────

def rate_at(d: date, rates: list) -> Optional[float]:
    """Тариф ₽/кВт·ч, действующий на дату d (последний с date ≤ d)."""
    best_d: Optional[date] = None
    best_v: Optional[float] = None
    for r in rates or []:
        rd = _parse_iso(r.get("date", ""))
        if rd is None or rd > d:
            continue
        v = _to_float(r.get("rate"))
        if v is None:
            continue
        if best_d is None or rd > best_d:
            best_d = rd
            best_v = v
    return best_v


# ── расход и замены ───────────────────────────────────────────────────

def _replacements_between(plot: str, prev_d: date, cur_d: date,
                          replacements: dict) -> list[dict]:
    """Замены счётчика, попадающие в интервал (prev_d, cur_d]."""
    items = replacements.get(str(plot), []) or []
    out = []
    for r in items:
        d = _parse_iso(r.get("date", ""))
        of = _to_float(r.get("old_final"))
        ni = _to_float(r.get("new_initial"))
        if d is None or of is None or ni is None:
            continue
        if prev_d < d <= cur_d:
            out.append({"date": d, "old_final": of, "new_initial": ni})
    out.sort(key=lambda r: r["date"])
    return out


def consumption_kwh(plot: str, year: int, month: int,
                    meters: dict, replacements: dict) -> Optional[float]:
    """
    Расход за месяц в кВт·ч. None если предыдущего показания нет
    (первое снятое показание — расход не определён).
    Учитывает записи о замене счётчика.
    """
    readings = plot_readings(plot, meters)
    if not readings:
        return None

    cur_idx = next(
        (i for i, (y, m, _) in enumerate(readings) if (y, m) == (year, month)),
        None,
    )
    if cur_idx is None or cur_idx == 0:
        return None

    py, pm, pv = readings[cur_idx - 1]
    cy, cm, cv = readings[cur_idx]

    repls = _replacements_between(
        plot, reading_date(py, pm), reading_date(cy, cm), replacements
    )
    if not repls:
        return cv - pv

    consumed = 0.0
    last = pv
    for r in repls:
        consumed += r["old_final"] - last
        last = r["new_initial"]
    consumed += cv - last
    return consumed


def charge(plot: str, year: int, month: int,
           meters: dict, rates: list, replacements: dict) -> Optional[float]:
    """Начислено в ₽ за месяц. None если нет расхода или тарифа."""
    kwh = consumption_kwh(plot, year, month, meters, replacements)
    if kwh is None:
        return None
    r = rate_at(reading_date(year, month), rates)
    if r is None:
        return None
    return kwh * r


def all_charges(plot: str, meters: dict, rates: list, replacements: dict,
                up_to: Optional[date] = None) -> list[dict]:
    """
    Список помесячных начислений для участка:
    [{year, month, prev_value, value, kwh, rate, amount}, ...]
    Первое показание включено с amount=None (нет расхода).
    """
    readings = plot_readings(plot, meters)
    out: list[dict] = []
    for i, (y, m, v) in enumerate(readings):
        rd = reading_date(y, m)
        if up_to and rd > up_to:
            continue
        if i == 0:
            out.append({
                "year": y, "month": m, "prev_value": None, "value": v,
                "kwh": None, "rate": None, "amount": None,
            })
            continue
        kwh = consumption_kwh(plot, y, m, meters, replacements)
        rate = rate_at(rd, rates)
        amount = kwh * rate if (kwh is not None and rate is not None) else None
        out.append({
            "year": y, "month": m,
            "prev_value": readings[i - 1][2],
            "value": v,
            "kwh": kwh, "rate": rate, "amount": amount,
        })
    return out


# ── платежи из выписки ────────────────────────────────────────────────

def _ensure_df(df) -> Optional[pd.DataFrame]:
    if df is None or len(df) == 0:
        return None
    needed = {"Дата", "Поступление", "Категория", "Участок"}
    if not needed.issubset(df.columns):
        return None
    return df


def _row_amount_for_plot(row: pd.Series, plot: str) -> float:
    plots = [p.strip() for p in str(row.get("Участок", "")).split(",") if p.strip()]
    if plot not in plots:
        return 0.0
    amount = _to_float(row.get("Поступление"))
    if amount is None:
        return 0.0
    if row.get("Категория") == CAT_MIXED:
        amount /= 2
    amount /= len(plots)
    return amount


def payments_total(plot: str, df, date_from: Optional[date] = None,
                   date_to: Optional[date] = None) -> float:
    """Сумма платежей за электричество для участка в интервале [from, to]."""
    df = _ensure_df(df)
    if df is None:
        return 0.0
    d = df[df["Категория"].isin(CATS_ELECTRO_INCOME)].copy()
    if d.empty:
        return 0.0
    dates = d["Дата"].dt.date
    if date_from is not None:
        d = d[dates >= date_from]
        dates = d["Дата"].dt.date
    if date_to is not None:
        d = d[dates <= date_to]
    total = 0.0
    for _, row in d.iterrows():
        total += _row_amount_for_plot(row, plot)
    return total


def payments_breakdown(plot: str, df) -> list[dict]:
    """Хронологический список платежей по электричеству для участка."""
    df = _ensure_df(df)
    if df is None:
        return []
    d = df[df["Категория"].isin(CATS_ELECTRO_INCOME)].copy()
    if d.empty:
        return []
    out = []
    for _, row in d.iterrows():
        amount = _row_amount_for_plot(row, plot)
        if amount <= 0:
            continue
        out.append({
            "date": row["Дата"].date() if pd.notna(row["Дата"]) else None,
            "amount": amount,
            "mixed": row.get("Категория") == CAT_MIXED,
            "purpose": str(row.get("Назначение", "")),
        })
    out.sort(key=lambda r: r["date"] or date.min)
    return out


# ── баланс ────────────────────────────────────────────────────────────

@dataclass
class Balance:
    plot: str
    charged: float
    paid: float
    baseline: float
    debt: float
    last_reading: Optional[tuple[int, int, float]]
    months_without_payment: Optional[int]


def balance(plot: str, as_of: date, meters: dict, rates: list,
            replacements: dict, baseline: dict, df) -> Balance:
    base = _to_float(baseline.get("balances", {}).get(str(plot))) or 0.0
    base_start = _parse_iso(baseline.get("start_date", ""))

    charged = 0.0
    last: Optional[tuple[int, int, float]] = None
    for c in all_charges(plot, meters, rates, replacements, up_to=as_of):
        if c["amount"] is not None:
            charged += c["amount"]
        last = (c["year"], c["month"], c["value"])

    paid = payments_total(plot, df, date_from=base_start, date_to=as_of)
    debt = base + charged - paid

    # Сколько месяцев подряд без платежей электро (от последнего платежа до as_of)
    months_without_payment: Optional[int] = None
    breakdown = payments_breakdown(plot, df)
    breakdown = [p for p in breakdown if p["date"] and p["date"] <= as_of]
    if breakdown:
        last_pay = breakdown[-1]["date"]
        months_without_payment = (
            (as_of.year - last_pay.year) * 12 + (as_of.month - last_pay.month)
        )
    elif last is not None:
        ly, lm, _ = last
        months_without_payment = (
            (as_of.year - ly) * 12 + (as_of.month - lm)
        )

    return Balance(
        plot=plot,
        charged=charged,
        paid=paid,
        baseline=base,
        debt=debt,
        last_reading=last,
        months_without_payment=months_without_payment,
    )


# ── аномалии показаний ────────────────────────────────────────────────

@dataclass
class Anomaly:
    plot: str
    type: str          # "drop" | "spike" | "gap"
    year: int
    month: int
    detail: str


def _iter_months_between(prev_y: int, prev_m: int,
                          cur_y: int, cur_m: int):
    y, m = prev_y, prev_m
    while True:
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
        if (y, m) >= (cur_y, cur_m):
            return
        yield y, m


def anomalies(plot: str, meters: dict, replacements: dict) -> list[Anomaly]:
    readings = plot_readings(plot, meters)
    if len(readings) < 2:
        return []

    out: list[Anomaly] = []
    consumptions: list[float] = []
    kwh_by_idx: dict[int, float] = {}

    for i in range(1, len(readings)):
        py, pm, pv = readings[i - 1]
        y, m, v = readings[i]

        kwh = consumption_kwh(plot, y, m, meters, replacements)
        if kwh is None:
            continue

        if kwh < 0:
            out.append(Anomaly(
                plot=plot, type="drop", year=y, month=m,
                detail=f"показание {v:g} меньше предыдущего {pv:g}",
            ))
        else:
            consumptions.append(kwh)
            kwh_by_idx[i] = kwh

        for gy, gm in _iter_months_between(py, pm, y, m):
            out.append(Anomaly(
                plot=plot, type="gap", year=gy, month=gm,
                detail="пропуск месяца между заполненными",
            ))

    if len(consumptions) >= 4:
        sc = sorted(consumptions)
        median = sc[len(sc) // 2]
        if median > 0:
            for i, kwh in kwh_by_idx.items():
                if kwh > 3 * median:
                    y, m, _ = readings[i]
                    out.append(Anomaly(
                        plot=plot, type="spike", year=y, month=m,
                        detail=f"расход {kwh:g} кВт·ч > 3× медианы {median:g}",
                    ))

    return out


# ── сверка с поставщиком ──────────────────────────────────────────────

@dataclass
class Reconciliation:
    period_from: date
    period_to: date
    charged_total: float        # начислено всем садоводам
    collected_total: float      # оплачено садоводами
    paid_to_supplier: float     # списано на Пермэнергосбыт
    common_kwh: Optional[float]  # расход общего счётчика, если есть
    private_kwh: float           # сумма расходов частных счётчиков
    loss_kwh: Optional[float]    # = common_kwh - private_kwh
    loss_rub: Optional[float]    # потери в ₽ по тарифу на конец периода


def reconcile(date_from: date, date_to: date, plots: list[str],
              meters: dict, rates: list, replacements: dict,
              common_meter: dict, df) -> Reconciliation:
    # Начислено всем
    charged_total = 0.0
    private_kwh = 0.0
    for p in plots:
        for c in all_charges(p, meters, rates, replacements, up_to=date_to):
            rd = reading_date(c["year"], c["month"])
            if rd < date_from or rd > date_to:
                continue
            if c["amount"] is not None:
                charged_total += c["amount"]
            if c["kwh"] is not None:
                private_kwh += c["kwh"]

    # Собрано с садоводов
    collected_total = 0.0
    d = _ensure_df(df)
    if d is not None:
        sub = d[d["Категория"].isin(CATS_ELECTRO_INCOME)].copy()
        if not sub.empty:
            dates = sub["Дата"].dt.date
            sub = sub[(dates >= date_from) & (dates <= date_to)]
            for _, row in sub.iterrows():
                amount = _to_float(row.get("Поступление")) or 0.0
                if row.get("Категория") == CAT_MIXED:
                    amount /= 2
                collected_total += amount

    # Уплачено в Пермэнергосбыт
    paid_to_supplier = 0.0
    if d is not None:
        sub = d[d["Категория"] == CAT_TO_SUPPLIER].copy()
        if not sub.empty:
            dates = sub["Дата"].dt.date
            sub = sub[(dates >= date_from) & (dates <= date_to)]
            for _, row in sub.iterrows():
                amount = _to_float(row.get("Списание")) or 0.0
                paid_to_supplier += amount

    # Общий счётчик
    common_kwh: Optional[float] = None
    loss_kwh: Optional[float] = None
    loss_rub: Optional[float] = None
    if common_meter:
        period_readings = []
        for key, val in common_meter.items():
            parts = key.split(":")
            if len(parts) != 2:
                continue
            try:
                y, m = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            v = _to_float(val)
            if v is None:
                continue
            rd = reading_date(y, m)
            period_readings.append((rd, v))
        period_readings.sort()
        within = [(d_, v) for (d_, v) in period_readings
                  if date_from <= d_ <= date_to]
        if len(within) >= 2:
            common_kwh = within[-1][1] - within[0][1]
            loss_kwh = common_kwh - private_kwh
            tariff = rate_at(date_to, rates)
            if tariff is not None and loss_kwh is not None:
                loss_rub = loss_kwh * tariff

    return Reconciliation(
        period_from=date_from,
        period_to=date_to,
        charged_total=charged_total,
        collected_total=collected_total,
        paid_to_supplier=paid_to_supplier,
        common_kwh=common_kwh,
        private_kwh=private_kwh,
        loss_kwh=loss_kwh,
        loss_rub=loss_rub,
    )


# ── палитра для UI ────────────────────────────────────────────────────

def debt_color(debt: float, monthly_avg: float = 0.0) -> str:
    """Возвращает hex-цвет для индикации уровня долга."""
    if debt <= 0:
        return "#2e7d32"        # аванс / ноль — зелёный
    threshold = max(monthly_avg, 500.0)
    if debt <= threshold:
        return "#f9a825"        # жёлтый — небольшой долг
    if debt <= 3 * threshold:
        return "#ef6c00"        # оранжевый
    return "#c62828"            # красный — крупный долг
