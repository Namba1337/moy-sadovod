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

from core.utils import _read_json, _ensure_df, DATA_DIR
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

def load_meters() -> dict:
    """{"plot:year:month": "значение"}"""
    return _read_json(METERS_FILE, {})


def save_meters(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(METERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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


def load_common_meter() -> dict:
    """{"YYYY:M": "значение общего счётчика СНТ на конец месяца"}"""
    return _read_json(COMMON_METER_FILE, {})


def load_plots() -> list:
    return _read_json(PLOTS_FILE, [])


def save_plots(data: list) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PLOTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def owners_map() -> dict:
    """Возвращает {num: [str, ...]} — имена из активной группы (или old owners)."""
    from core.ownership import active_group, group_owners, owner_name
    result = {}
    for p in load_plots():
        num = str(p.get("num", ""))
        if not num:
            continue
        if "groups" in p:
            ag = active_group(p)
            owners = group_owners(ag) if ag else []
        else:
            owners = p.get("owners", []) or []
        result[num] = [owner_name(o) for o in owners]
    return result


# ── тип расчёта за электроэнергию ─────────────────────────────────────
# meter      — счётчик введён в эксплуатацию через СНТ, расчёт по показаниям
# calculated — расчётный метод (норматив × 24 × дни × тариф)
# direct     — прямой договор с Пермэнергосбытом, через СНТ не начисляется
BILLING_METER = "meter"
BILLING_CALCULATED = "calculated"
BILLING_DIRECT = "direct"
BILLING_TYPES = (BILLING_METER, BILLING_CALCULATED, BILLING_DIRECT)
BILLING_LABELS = {
    BILLING_METER: "Счётчик",
    BILLING_CALCULATED: "Расчётный метод",
    BILLING_DIRECT: "Прямой договор",
}


def plots_by_num(plots: Optional[list] = None) -> dict:
    """{num(str): plot_dict}. Загружает реестр, если plots не передан."""
    src = plots if plots is not None else load_plots()
    return {str(p.get("num", "")): p for p in src}


def plot_record(plot: str, plots: Optional[list] = None) -> dict:
    return plots_by_num(plots).get(str(plot), {}) or {}


def billing_type_of(plot: str, plots: Optional[list] = None) -> str:
    """Текущий тип расчёта участка. Неизвестные/пустые → meter (по умолчанию)."""
    bt = plot_record(plot, plots).get("billing_type") or BILLING_METER
    return bt if bt in BILLING_TYPES else BILLING_METER


def migrate_billing_types() -> int:
    """Проставляет billing_type=meter всем участкам без него.
    Действующие начисления не меняются. Возвращает число изменённых записей."""
    plots = load_plots()
    changed = 0
    for p in plots:
        if not p.get("billing_type"):
            p["billing_type"] = BILLING_METER
            changed += 1
    if changed:
        save_plots(plots)
    return changed


def billing_segments(plot: str, plots: Optional[list] = None) -> list[tuple]:
    """История применения типов расчёта во времени.

    Возвращает список (date_from|None, date_to|None, billing_type),
    отсортированный по времени. Если истории смен нет — один сегмент
    (None, None, текущий_тип). Границы берутся из billing_history:
    [{"date": "YYYY-MM-DD", "from": тип, "to": тип, ...}] — дата = момент,
    с которого действует новый тип.
    """
    rec = plot_record(plot, plots)
    cur = billing_type_of(plot, plots)
    hist = []
    for h in rec.get("billing_history", []) or []:
        d = _parse_iso(h.get("date", ""))
        to = h.get("to")
        if d is None or to not in BILLING_TYPES:
            continue
        hist.append((d, h.get("from") if h.get("from") in BILLING_TYPES else None, to))
    hist.sort(key=lambda x: x[0])

    if not hist:
        return [(None, None, cur)]

    from datetime import timedelta
    segs: list[tuple] = []
    first_from = hist[0][1] or BILLING_METER
    segs.append((None, hist[0][0] - timedelta(days=1), first_from))
    for i, (d, _frm, to) in enumerate(hist):
        end = (hist[i + 1][0] - timedelta(days=1)) if i + 1 < len(hist) else None
        segs.append((d, end, to))
    return segs


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


# ── расчётный метод (норматив) ────────────────────────────────────────

def _days_in_month_within(year: int, month: int,
                          start: date, end: date) -> int:
    """Кол-во дней месяца (year, month), попадающих в [start, end] включительно."""
    last_day = calendar.monthrange(year, month)[1]
    m_start = date(year, month, 1)
    m_end = date(year, month, last_day)
    seg_start = max(m_start, start)
    seg_end = min(m_end, end)
    if seg_end < seg_start:
        return 0
    return (seg_end - seg_start).days + 1


def calculated_charges(plot: str, norm_kw: Optional[float], start: Optional[date],
                       rates: list, up_to: Optional[date] = None) -> list[dict]:
    """Помесячные начисления расчётным методом.

    amount = норматив(кВт) × 24 × дни_в_периоде × тариф.
    Формат строк совместим с all_charges (+ флаг calculated и поле days).
    """
    out: list[dict] = []
    if norm_kw is None or start is None:
        return out
    up_to = up_to or date.today()
    if up_to < start:
        return out

    y, m = start.year, start.month
    while (y, m) <= (up_to.year, up_to.month):
        days = _days_in_month_within(y, m, start, up_to)
        rd = reading_date(y, m)
        rate = rate_at(rd, rates)
        kwh = norm_kw * 24 * days
        amount = kwh * rate if rate is not None else None
        out.append({
            "year": y, "month": m, "prev_value": None, "value": None,
            "kwh": kwh, "rate": rate, "amount": amount,
            "calculated": True, "days": days,
        })
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# ── диспетчер начислений по типу расчёта ──────────────────────────────

def charges_for_plot(plot: str, meters: dict, rates: list, replacements: dict,
                     up_to: Optional[date] = None,
                     plots: Optional[list] = None) -> list[dict]:
    """Начисления участка с учётом типа расчёта и истории его смены.

    Тип 1 (meter)      — по показаниям прибора учёта.
    Тип 2 (calculated) — по нормативу.
    Тип 3 (direct)     — пусто (через СНТ не начисляется).
    Каждый сегмент истории считается своим методом, что корректно
    обрабатывает смену типа в середине истории.
    """
    up_to = up_to or date.today()
    rec = plot_record(plot, plots)
    norm = _to_float(rec.get("norm_kw"))
    norm_start = _parse_iso(rec.get("norm_start_date", ""))

    out: list[dict] = []
    for seg_from, seg_to, bt in billing_segments(plot, plots):
        seg_end = up_to if seg_to is None else min(seg_to, up_to)
        if seg_end < (seg_from or date.min):
            continue
        if bt == BILLING_DIRECT:
            continue
        if bt == BILLING_CALCULATED:
            c_start = norm_start or seg_from
            if seg_from is not None and (c_start is None or c_start < seg_from):
                c_start = seg_from
            out += calculated_charges(plot, norm, c_start, rates, up_to=seg_end)
        else:  # meter
            for c in all_charges(plot, meters, rates, replacements, up_to=seg_end):
                rd = reading_date(c["year"], c["month"])
                if seg_from is not None and rd < seg_from:
                    continue
                out.append(c)
    out.sort(key=lambda c: (c["year"], c["month"]))
    return out


def waiting_for_readings(plot: str, meters: dict,
                         plots: Optional[list] = None) -> bool:
    """Тип 1 без переданных показаний — статус «ожидание показаний»."""
    if billing_type_of(plot, plots) != BILLING_METER:
        return False
    return not plot_readings(plot, meters)


# ── платежи из выписки ────────────────────────────────────────────────

def _row_amount_for_plot(row: pd.Series, plot: str) -> float:
    plots = [p.strip() for p in str(row.get("Участок", "")).split(",") if p.strip()]
    if plot not in plots:
        return 0.0
    amount = _to_float(row.get("Сумма"))
    if amount is None or amount <= 0:
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
            replacements: dict, baseline: dict, df,
            plots: Optional[list] = None) -> Balance:
    base = _to_float(baseline.get("balances", {}).get(str(plot))) or 0.0
    base_start = _parse_iso(baseline.get("start_date", ""))

    charged = 0.0
    last: Optional[tuple[int, int, float]] = None
    for c in charges_for_plot(plot, meters, rates, replacements,
                              up_to=as_of, plots=plots):
        if c["amount"] is not None:
            charged += c["amount"]
        if c["value"] is not None:
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


# ── разбивка баланса по собственникам ─────────────────────────────────

@dataclass
class EnergyOwnerBalance:
    name: str
    is_current: bool
    since: Optional[date]
    until: Optional[date]
    charged: float
    paid: float
    baseline: float
    debt: float


def _energy_owner_weights(active: list, form: Optional[str]) -> list[float]:
    """Веса деления суммы между совладельцами для электроэнергии.

    Площади тут нет (энергия — по потреблению), поэтому: долевая → по доле
    в праве (``share``), индивидуальная/совместная → поровну. При незаданном
    виде права (старые данные) — по доле, иначе поровну (``effective_weights``).
    """
    from core import ownership as own
    n = len(active)
    if n == 0:
        return []
    if form in (own.FORM_INDIVIDUAL, own.FORM_JOINT):
        return [1.0 / n] * n
    return own.effective_weights(active)


def balances_by_owner(plot: str, as_of: date, meters: dict, rates: list,
                      replacements: dict, baseline: dict, df,
                      owners: list,
                      ownership_form: Optional[str] = None,
                      plots: Optional[list] = None) -> list[EnergyOwnerBalance]:
    """Раскладывает долг по электроэнергии по собственникам во времени.

    Атрибуция: месячное начисление → собственнику на дату снятия показания
    (конец месяца); начальное сальдо (baseline) → собственнику на дату начала
    периода baseline; платёж → собственнику на дату платежа. Деление между
    совладельцами — по виду права (см. :func:`_energy_owner_weights`).

    Долг прежнего собственника остаётся за ним. Сумма по всем владельцам
    реконсилируется с :func:`balance`. Если у участка нет истории владения,
    всё сходится на текущих собственниках (поведение как прежде).
    """
    from core import ownership as own
    owners = owners or []

    base = _to_float(baseline.get("balances", {}).get(str(plot))) or 0.0
    base_start = _parse_iso(baseline.get("start_date", ""))

    charged_acc: dict[int, float] = {}
    paid_acc: dict[int, float] = {}
    base_acc: dict[int, float] = {}
    unknown_charged = 0.0
    unknown_paid = 0.0
    unknown_base = 0.0

    def _spread(acc: dict, owners_active: list, amount: float):
        weights = _energy_owner_weights(owners_active, ownership_form)
        for o, w in zip(owners_active, weights):
            acc[id(o)] = acc.get(id(o), 0.0) + amount * w

    # Начальное сальдо → собственник на дату начала baseline
    if abs(base) > 0.0:
        active = own.owners_at(owners, base_start or date.min)
        if active:
            _spread(base_acc, active, base)
        else:
            unknown_base += base

    # Помесячные начисления → собственник на дату показания (конец месяца)
    for c in charges_for_plot(plot, meters, rates, replacements,
                              up_to=as_of, plots=plots):
        amt = c["amount"]
        if amt is None:
            continue
        cdate = reading_date(c["year"], c["month"])
        active = own.owners_at(owners, cdate)
        if not active:
            unknown_charged += amt
            continue
        _spread(charged_acc, active, amt)

    # Платежи → собственник на дату платежа (в окне [base_start, as_of])
    for p in payments_breakdown(plot, df):
        d = p["date"]
        if d is None or d > as_of:
            continue
        if base_start is not None and d < base_start:
            continue
        active = own.owners_at(owners, d)
        if not active:
            unknown_paid += p["amount"]
            continue
        _spread(paid_acc, active, p["amount"])

    # Сборка по собственникам
    rows: list[tuple[bool, date, EnergyOwnerBalance]] = []
    for o in owners:
        if not own.is_owner(o):
            continue
        oid = id(o)
        c = charged_acc.get(oid, 0.0)
        pd_ = paid_acc.get(oid, 0.0)
        b = base_acc.get(oid, 0.0)
        current = own.is_active_at(o, as_of)
        if not current and c == 0.0 and pd_ == 0.0 and b == 0.0:
            continue
        rows.append((current, own.owner_until(o) or date.max,
                     EnergyOwnerBalance(
                         name=own.owner_name(o),
                         is_current=current,
                         since=own.owner_since(o),
                         until=own.owner_until(o),
                         charged=c,
                         paid=pd_,
                         baseline=b,
                         debt=b + c - pd_,
                     )))

    rows.sort(key=lambda t: (not t[0], -t[1].toordinal()))
    out = [r[2] for r in rows]

    if abs(unknown_charged) > 0.0 or abs(unknown_paid) > 0.0 or abs(unknown_base) > 0.0:
        out.append(EnergyOwnerBalance(
            name="Собственник не определён",
            is_current=False, since=None, until=None,
            charged=unknown_charged, paid=unknown_paid, baseline=unknown_base,
            debt=unknown_base + unknown_charged - unknown_paid,
        ))
    return out


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
              common_meter: dict, df,
              plot_records: Optional[list] = None) -> Reconciliation:
    # Начислено всем (участки на прямом договоре исключаются автоматически)
    charged_total = 0.0
    private_kwh = 0.0
    for p in plots:
        for c in charges_for_plot(p, meters, rates, replacements,
                                  up_to=date_to, plots=plot_records):
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
                amount = _to_float(row.get("Сумма")) or 0.0
                if amount <= 0:
                    continue
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
                amount = _to_float(row.get("Сумма")) or 0.0
                paid_to_supplier += abs(amount)

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


# ── баланс активной группы ────────────────────────────────────────────

from dataclasses import dataclass as _dc


@_dc
class EnergyGroupBalance:
    charged: float
    paid: float
    baseline: float
    debt: float


def balance_for_active_group(plot: str, as_of: date, meters: dict, rates: list,
                              replacements: dict, baseline: dict, df,
                              since: Optional[date] = None,
                              plots: Optional[list] = None) -> EnergyGroupBalance:
    """Баланс активной группы по электроэнергии, ограниченный датой since.

    Начальное сальдо (baseline) учитывается только если оно не предшествует
    периоду группы (либо since=None).
    """
    base_start = _parse_iso(baseline.get("start_date", ""))
    base = _to_float(baseline.get("balances", {}).get(str(plot))) or 0.0

    # Baseline включаем только если начало baseline >= since (или since не задан)
    include_base = (since is None) or (base_start is not None and base_start >= since)
    effective_base = base if include_base else 0.0

    charged = sum(
        c["amount"] for c in charges_for_plot(plot, meters, rates, replacements,
                                               up_to=as_of, plots=plots)
        if c["amount"] is not None
        and (since is None or reading_date(c["year"], c["month"]) >= since)
    )

    effective_since = since if since is not None else base_start
    paid = payments_total(plot, df, date_from=effective_since, date_to=as_of)

    return EnergyGroupBalance(
        charged=charged,
        paid=paid,
        baseline=effective_base,
        debt=effective_base + charged - paid,
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
