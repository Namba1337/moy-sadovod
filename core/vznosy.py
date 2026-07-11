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
from core import ownership as own
from core.utils import _read_json, _ensure_df, DATA_DIR
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

def payments_index(df) -> dict[str, list[dict]]:
    """Индекс платежей по ЧВ для ВСЕХ участков за один проход по выписке
    (см. energy.payments_index). Передаётся в balance_for_plot() и др.
    через параметр pay_index при массовых расчётах."""
    return energy.payments_index(df, CATS_VZNOSY_INCOME)


def payments_breakdown(plot: str, df, adjustments: Optional[dict] = None,
                       index: Optional[dict] = None) -> list[dict]:
    """Хронологический список платежей по ЧВ для участка.

    `index` — заранее построенный payments_index(df): избавляет от
    повторного сканирования всей выписки на каждый участок."""
    out: list[dict] = []
    if index is not None:
        out = [dict(e, source="csv") for e in index.get(str(plot), ())]
    else:
        d = _ensure_df(df)
        if d is not None:
            sub = energy._rows_matching_cats(d, CATS_VZNOSY_INCOME).copy()
            for _, row in sub.iterrows():
                amount = energy._row_amount_for_plot(row, plot, cats=CATS_VZNOSY_INCOME)
                if amount <= 0:
                    continue
                out.append({
                    "date": row["Дата"].date() if pd.notna(row["Дата"]) else None,
                    "amount": amount,
                    # mixed=True — авто-категория, ещё не разделена вручную
                    # (см. energy.parse_breakdown) — подсказка UI «раздели точнее».
                    "mixed": (row.get("Категория") == CAT_MIXED
                              and not energy.parse_breakdown(row.get("_breakdown"), total=row.get("Сумма"))),
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
                  adjustments: Optional[dict] = None,
                  index: Optional[dict] = None) -> float:
    return sum(
        p["amount"] for p in payments_breakdown(plot, df, adjustments, index=index)
        if p["date"] is not None and p["date"] <= as_of
    )


def paid_by_period(plot: str, df, as_of: date,
                   periods: list, adjustments: Optional[dict] = None,
                   index: Optional[dict] = None) -> dict[str, float]:
    """Возвращает {period_key: сумма_оплаченного} — платёж относится
    к периоду, в котором находится его дата."""
    out: dict[str, float] = {}
    for p in payments_breakdown(plot, df, adjustments, index=index):
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
                     rates: list, adjustments: dict, df,
                     pay_index: Optional[dict] = None) -> VznosyBalance:
    periods = build_periods(rates)
    breakdown = charged_periods_breakdown(plot, area, as_of, rates, adjustments)
    charged = sum(y.amount for y in breakdown if y.amount is not None)

    py = paid_by_period(plot, df, as_of, periods, adjustments, index=pay_index)

    # Платежи из игнорируемых периодов не учитываем в итоге —
    # иначе они превращаются в фиктивный «аванс».
    ignored_keys = {y.period_from.isoformat() for y in breakdown if y.ignored}
    paid_in_active = sum(v for k, v in py.items() if k not in ignored_keys)
    # Платежи, не попавшие ни в один период (до первого периода / в пробелах)
    total_raw = paid_for_plot(plot, df, as_of, adjustments, index=pay_index)
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


# ── разбивка баланса по собственникам ─────────────────────────────────

@dataclass
class OwnerBalance:
    name: str
    is_current: bool                 # владеет на дату as_of
    since: Optional[date]
    until: Optional[date]
    charged: float
    paid: float
    debt: float


def _owner_weights(active: list, tariff: Optional[dict],
                   form: Optional[str] = None) -> list[float]:
    """Веса деления суммы периода между одновременными совладельцами.

    Если задан вид права ``form`` (как в ЕГРН) — делим строго по нему:

    * ``individual`` — вся сумма единственному собственнику;
    * ``shared`` (долевая) — по доле в праве (``share``); доля применяется
      и к фиксированному взносу, и к тарифу «за м²» (площадь доли =
      доля × площадь участка), поэтому деление суммы периода по доле верно
      в обоих случаях;
    * ``joint`` (совместная) — поровну (доли не выделены).

    Если ``form`` не задан (старые данные) — прежнее поведение: тариф «за м²»
    делится по площади владельцев, иначе по доле/поровну. Так расчёт у старых
    участков не меняется, пока вид права не выбран явно.
    """
    n = len(active)
    if n == 0:
        return []
    if form == own.FORM_INDIVIDUAL or form == own.FORM_JOINT:
        return [1.0 / n] * n
    if form == own.FORM_SHARED:
        return own.effective_weights(active)

    # legacy (вид права не задан)
    if tariff is not None and tariff.get("per_sqm"):
        areas = [own.owner_area(o) for o in active]
        if all(a is not None and a > 0 for a in areas):
            total = sum(areas)  # type: ignore[arg-type]
            if total > 0:
                return [a / total for a in areas]  # type: ignore[operator]
    return own.effective_weights(active)


def _find_period(d: date, breakdown: list[PeriodCharge]) -> Optional[PeriodCharge]:
    """Период начисления, в который попадает дата d (breakdown отсортирован)."""
    for pc in breakdown:
        if d < pc.period_from:
            continue
        if pc.period_to is None or d <= pc.period_to:
            return pc
    return None


def balances_by_owner(plot: str, area: Optional[float], as_of: date,
                      rates: list, adjustments: dict, df,
                      owners: list,
                      ownership_form: Optional[str] = None,
                      pay_index: Optional[dict] = None) -> list[OwnerBalance]:
    """Раскладывает начисления и платежи участка по собственникам во времени.

    Правила атрибуции:

    * начисление за период относится к собственнику(ам), владевшим участком
      на **начало периода** (``period_from``); при долевой собственности
      делится между совладельцами (по площади для тарифа «за м²», иначе по
      доле в праве);
    * платёж относится к собственнику(ам), владевшим участком на **дату
      платежа**, и делится по тем же весам периода, в который он попал.

    Долг прежнего собственника остаётся за ним: платёж нового собственника
    гасит только его собственный баланс и не «перетекает» на чужой долг.

    Если у участка нет истории владения (ни одной даты ``since``/``until``),
    все начисления и платежи сходятся на текущих собственниках — поведение
    совпадает с прежним «весь долг на участок». Сумма ``charged``/``paid`` по
    всем владельцам реконсилируется с :func:`balance_for_plot` (за исключением
    редкого случая платежей внутри периода, помеченного «не учитывать»).

    Возвращает список :class:`OwnerBalance`: сначала текущие собственники,
    затем бывшие (свежие — выше); при «провале» в истории добавляется запись
    «Собственник не определён».
    """
    owners = owners or []
    breakdown = charged_periods_breakdown(plot, area, as_of, rates, adjustments)

    charged_acc: dict[int, float] = {}
    paid_acc: dict[int, float] = {}
    unknown_charged = 0.0
    unknown_paid = 0.0

    # Начисления → собственник на начало периода
    for pc in breakdown:
        if pc.ignored or pc.amount is None:
            continue
        active = own.owners_at(owners, pc.period_from)
        if not active:
            unknown_charged += pc.amount
            continue
        weights = _owner_weights(active, pc.tariff, ownership_form)
        for o, w in zip(active, weights):
            charged_acc[id(o)] = charged_acc.get(id(o), 0.0) + pc.amount * w

    # Платежи → собственник на дату платежа
    for p in payments_breakdown(plot, df, adjustments, index=pay_index):
        d = p["date"]
        if d is None or d > as_of:
            continue
        active = own.owners_at(owners, d)
        if not active:
            unknown_paid += p["amount"]
            continue
        pc = _find_period(d, breakdown)
        weights = _owner_weights(active, pc.tariff if pc else None, ownership_form)
        for o, w in zip(active, weights):
            paid_acc[id(o)] = paid_acc.get(id(o), 0.0) + p["amount"] * w

    # Сборка по собственникам
    rows: list[tuple[bool, date, OwnerBalance]] = []
    for o in owners:
        if not own.is_owner(o):
            continue
        oid = id(o)
        c = charged_acc.get(oid, 0.0)
        pd_ = paid_acc.get(oid, 0.0)
        current = own.is_active_at(o, as_of)
        if not current and c == 0.0 and pd_ == 0.0:
            continue  # бывший владелец без движения в его период — не показываем
        rows.append((current, own.owner_until(o) or date.max,
                     OwnerBalance(
                         name=own.owner_name(o),
                         is_current=current,
                         since=own.owner_since(o),
                         until=own.owner_until(o),
                         charged=c,
                         paid=pd_,
                         debt=c - pd_,
                     )))

    # Текущие сверху; среди прочих — свежие (больший until) выше
    rows.sort(key=lambda t: (not t[0], -t[1].toordinal()))
    out = [r[2] for r in rows]

    if unknown_charged != 0.0 or unknown_paid != 0.0:
        out.append(OwnerBalance(
            name="Собственник не определён",
            is_current=False,
            since=None,
            until=None,
            charged=unknown_charged,
            paid=unknown_paid,
            debt=unknown_charged - unknown_paid,
        ))
    return out


# ── баланс активной группы ────────────────────────────────────────────

@dataclass
class GroupBalance:
    charged: float
    paid: float
    debt: float


def balance_for_active_group(plot: str, area: Optional[float], as_of: date,
                              rates: list, adjustments: dict, df,
                              since: Optional[date] = None,
                              pay_index: Optional[dict] = None) -> GroupBalance:
    """Баланс активной группы: только платежи и начисления >= since.

    ``since`` — дата начала активной группы (может быть None для единственной
    группы без истории). Если None — считается с самого начала (то есть
    совпадает с balance_for_plot, игнорируя игнорируемые периоды).
    """
    breakdown = charged_periods_breakdown(plot, area, as_of, rates, adjustments)
    charged = sum(
        y.amount for y in breakdown
        if y.amount is not None
        and not y.ignored
        and (since is None or y.period_from >= since)
    )

    # Платежи, попавшие в период с пометкой «Не учитывать», исключаем — как
    # и в balance_for_plot (иначе такой платёж превращается в фиктивный
    # аванс: начисление за период не идёт в счёт, а платёж — идёт).
    paid = sum(
        p["amount"] for p in payments_breakdown(plot, df, adjustments, index=pay_index)
        if p["date"] is not None
        and p["date"] <= as_of
        and (since is None or p["date"] >= since)
        and not _period_at_date_is_ignored(p["date"], breakdown)
    )

    return GroupBalance(charged=charged, paid=paid, debt=charged - paid)


def _period_at_date_is_ignored(d: date, breakdown: list[PeriodCharge]) -> bool:
    pc = _find_period(d, breakdown)
    return pc is not None and pc.ignored


def balance_for_periods(plot: str, area: Optional[float], as_of: date,
                         rates: list, adjustments: dict, df,
                         since: Optional[date] = None,
                         period_keys: Optional[set[str]] = None,
                         pay_index: Optional[dict] = None) -> GroupBalance:
    """Баланс, ограниченный выбором пользователя: ``since`` — нижняя граница
    по дате начала периода (как в :func:`balance_for_active_group`, None —
    без ограничения), ``period_keys`` — явный набор периодов (``date_from``
    в ISO-формате, None — все периоды). Условия комбинируются («И»)."""
    breakdown = charged_periods_breakdown(plot, area, as_of, rates, adjustments)

    def _period_ok(pf: date) -> bool:
        if since is not None and pf < since:
            return False
        if period_keys is not None and pf.isoformat() not in period_keys:
            return False
        return True

    charged = sum(
        y.amount for y in breakdown
        if y.amount is not None and not y.ignored and _period_ok(y.period_from)
    )

    paid = 0.0
    for p in payments_breakdown(plot, df, adjustments, index=pay_index):
        if p["date"] is None or p["date"] > as_of:
            continue
        pc = _find_period(p["date"], breakdown)
        if pc is None or pc.ignored or not _period_ok(pc.period_from):
            continue
        paid += p["amount"]

    return GroupBalance(charged=charged, paid=paid, debt=charged - paid)


# ── уровень долга для UI ─────────────────────────────────────────────

def debt_level(debt: float, annual_avg: float = 0.0) -> str:
    """Уровень долга: 'ok' | 'low' | 'mid' | 'high'. Пороги в долях
    годовой суммы. Конкретные цвета подставляет UI (ui.theme.C.DEBT)."""
    if debt <= 0:
        return "ok"
    threshold = max(annual_avg, 1000.0)
    if debt <= 0.25 * threshold:
        return "low"
    if debt <= 0.75 * threshold:
        return "mid"
    return "high"
