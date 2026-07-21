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
AUTO_SETTINGS_FILE = os.path.join(DATA_DIR, "snt_energy_auto_settings.json")

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


DEFAULT_AUTO_SWITCH_MONTHS = 3


def load_auto_settings() -> dict:
    """{"enabled": bool, "months": int, "default_norm_kw": float|None,
    "default_avg_window_months": int|None} — общие на всё СНТ настройки
    электроэнергии (решение общего собрания, не за отдельный участок):

    - enabled/months — автопереход участков типа «Счётчик» на оценку по
      среднему/нормативу, если показания не переданы `months` месяцев
      подряд (см. auto_estimate_charges()).
    - default_norm_kw/default_avg_window_months — глобальные значения по
      умолчанию для расчётного метода; используются участком, только если
      на нём самом это поле не задано (см. norm_kw_of()/avg_window_months_of()).
      Меняются здесь — сразу применяются ко всем участкам без своего
      значения (живое наследование, не разовая подстановка)."""
    return _read_json(AUTO_SETTINGS_FILE, {
        "enabled": False, "months": DEFAULT_AUTO_SWITCH_MONTHS,
        "default_norm_kw": None, "default_avg_window_months": None,
    })


def save_auto_settings(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(AUTO_SETTINGS_FILE, "w", encoding="utf-8") as f:
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


# ── расчётный метод (норматив / среднее по своей истории) ─────────────
# norm         — норматив мощности (кВт) × 24 ч × дни × тариф (как раньше)
# own_average  — среднемесячное потребление ЭТОГО участка за предыдущие
#                месяцы (по факту снятых показаний до начала периода) ×
#                дни/тариф. Приоритетный метод по устойчивости в суде —
#                прямая аналогия с признанной практикой (п. 59-60 Правил
#                № 354): использует реальную историю участка, а не
#                усреднённый норматив.
CALC_METHOD_NORM = "norm"
CALC_METHOD_OWN_AVERAGE = "own_average"
CALC_METHODS = (CALC_METHOD_NORM, CALC_METHOD_OWN_AVERAGE)
CALC_METHOD_LABELS = {
    CALC_METHOD_NORM: "По нормативу мощности (кВт)",
    CALC_METHOD_OWN_AVERAGE: "Среднее потребление участка (по своей истории)",
}

OWN_AVERAGE_WINDOW_MONTHS = 12


def calc_method_of(plot: str, plots: Optional[list] = None) -> str:
    """Способ расчётного метода участка. Неизвестное/пустое → norm (совместимость)."""
    cm = plot_record(plot, plots).get("calc_method") or CALC_METHOD_NORM
    return cm if cm in CALC_METHODS else CALC_METHOD_NORM


def avg_window_months_of(plot: str, plots: Optional[list] = None,
                         auto_settings: Optional[dict] = None) -> int:
    """Окно усреднения (мес.) для метода own_average — приоритет:
    значение на самом участке (`avg_window_months`) → глобальное значение
    по умолчанию (`auto_settings['default_avg_window_months']`, настраивается
    в диалоге «Автопереход») → 12 (документ допускает 6-12 мес., это верхняя
    граница диапазона — абсолютный запасной вариант, если и глобальное не
    задано)."""
    raw = plot_record(plot, plots).get("avg_window_months")
    try:
        n = int(raw)
        if n > 0:
            return n
    except (TypeError, ValueError):
        pass
    if auto_settings:
        raw_g = auto_settings.get("default_avg_window_months")
        try:
            ng = int(raw_g)
            if ng > 0:
                return ng
        except (TypeError, ValueError):
            pass
    return OWN_AVERAGE_WINDOW_MONTHS


def norm_kw_of(plot: str, plots: Optional[list] = None,
              auto_settings: Optional[dict] = None) -> Optional[float]:
    """Норматив мощности (кВт) — приоритет: значение на самом участке
    (`norm_kw`) → глобальное значение по умолчанию
    (`auto_settings['default_norm_kw']`). None — если не задано нигде
    (оценить/начислить по нормативу нечем)."""
    local = _to_float(plot_record(plot, plots).get("norm_kw"))
    if local is not None:
        return local
    if auto_settings:
        return _to_float(auto_settings.get("default_norm_kw"))
    return None


def own_average_kwh(plot: str, meters: dict, replacements: dict, before: date,
                    window_months: int = OWN_AVERAGE_WINDOW_MONTHS) -> Optional[float]:
    """Среднемесячный расход участка (кВт·ч) по факту снятых показаний ДО даты
    `before`, за последние `window_months` месяцев с известным расходом (если
    истории меньше — за фактически доступный период). None, если у участка нет
    ни одной пары показаний до этой даты (расход посчитать не из чего)."""
    readings = plot_readings(plot, meters)
    values: list[tuple[date, float]] = []
    for i in range(1, len(readings)):
        y, m, _ = readings[i]
        rd = reading_date(y, m)
        if rd >= before:
            continue
        kwh = consumption_kwh(plot, y, m, meters, replacements)
        if kwh is not None and kwh >= 0:
            values.append((rd, kwh))
    if not values:
        return None
    values.sort(key=lambda t: t[0])
    window = values[-window_months:]
    return sum(kwh for _, kwh in window) / len(window)


def own_average_charges(plot: str, avg_kwh_per_month: Optional[float], start: Optional[date],
                        rates: list, up_to: Optional[date] = None) -> list[dict]:
    """Помесячные начисления методом «среднее по своей истории».

    amount = avg_kwh_per_month × (дни_в_периоде / дней_в_месяце) × тариф.
    Формат строк совместим с all_charges/calculated_charges (+ флаги
    calculated и calc_method, + поле days)."""
    out: list[dict] = []
    if avg_kwh_per_month is None or start is None:
        return out
    up_to = up_to or date.today()
    if up_to < start:
        return out

    y, m = start.year, start.month
    while (y, m) <= (up_to.year, up_to.month):
        days = _days_in_month_within(y, m, start, up_to)
        last_day = calendar.monthrange(y, m)[1]
        rd = reading_date(y, m)
        rate = rate_at(rd, rates)
        kwh = avg_kwh_per_month * days / last_day
        amount = kwh * rate if rate is not None else None
        out.append({
            "year": y, "month": m, "prev_value": None, "value": None,
            "kwh": kwh, "rate": rate, "amount": amount,
            "calculated": True, "days": days,
            "calc_method": CALC_METHOD_OWN_AVERAGE,
        })
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


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
                     plots: Optional[list] = None,
                     auto_settings: Optional[dict] = None) -> list[dict]:
    """Начисления участка с учётом типа расчёта и истории его смены.

    Тип 1 (meter)      — по показаниям прибора учёта.
    Тип 2 (calculated) — по нормативу.
    Тип 3 (direct)     — пусто (через СНТ не начисляется).
    Каждый сегмент истории считается своим методом, что корректно
    обрабатывает смену типа в середине истории.

    Если передан `auto_settings` (см. load_auto_settings()) и он включён —
    к открытому «хвосту» участка типа «Счётчик» (после последнего показания)
    добавляется автооценка (см. auto_estimate_charges()), помеченная флагом
    "auto_estimate": True.
    """
    up_to = up_to or date.today()
    rec = plot_record(plot, plots)
    norm = norm_kw_of(plot, plots, auto_settings)
    norm_start = _parse_iso(rec.get("norm_start_date", ""))
    calc_method = calc_method_of(plot, plots)

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
            if calc_method == CALC_METHOD_OWN_AVERAGE:
                window = avg_window_months_of(plot, plots, auto_settings)
                avg = own_average_kwh(plot, meters, replacements,
                                      before=c_start or date.today(),
                                      window_months=window)
                out += own_average_charges(plot, avg, c_start, rates, up_to=seg_end)
            else:
                out += calculated_charges(plot, norm, c_start, rates, up_to=seg_end)
        else:  # meter
            for c in all_charges(plot, meters, rates, replacements, up_to=seg_end):
                rd = reading_date(c["year"], c["month"])
                if seg_from is not None and rd < seg_from:
                    continue
                out.append(c)

    if auto_settings:
        known = {(c["year"], c["month"]) for c in out}
        for c in auto_estimate_charges(plot, meters, rates, replacements,
                                       up_to, auto_settings, plots=plots):
            if (c["year"], c["month"]) not in known:
                out.append(c)

    out.sort(key=lambda c: (c["year"], c["month"]))
    return out


def waiting_for_readings(plot: str, meters: dict,
                         plots: Optional[list] = None) -> bool:
    """Тип 1 без переданных показаний — статус «ожидание показаний»."""
    if billing_type_of(plot, plots) != BILLING_METER:
        return False
    return not plot_readings(plot, meters)


def months_without_reading(plot: str, meters: dict, as_of: date,
                           plots: Optional[list] = None) -> Optional[int]:
    """Сколько месяцев подряд участок типа «Счётчик» не передаёт показания
    (см. Ситуация 1/2 в порядке действий СНТ по электроэнергии — после
    3 месяцев рекомендуется переходить на расчётный метод).

    Отсчёт — от последнего переданного показания до `as_of`. Если показаний
    не было вообще — от даты ввода счётчика в эксплуатацию (`meter_commission_date`),
    если она указана. None — если тип расчёта не «Счётчик», либо начинать
    отсчёт не от чего (нет ни показаний, ни даты ввода в эксплуатацию)."""
    if billing_type_of(plot, plots) != BILLING_METER:
        return None
    readings = plot_readings(plot, meters)
    if readings:
        ly, lm, _ = readings[-1]
        base = reading_date(ly, lm)
    else:
        base = _parse_iso(plot_record(plot, plots).get("meter_commission_date", ""))
        if base is None:
            return None
    if as_of < base:
        return 0
    return (as_of.year - base.year) * 12 + (as_of.month - base.month)


def auto_estimate_charges(plot: str, meters: dict, rates: list, replacements: dict,
                          as_of: date, auto_settings: Optional[dict],
                          plots: Optional[list] = None) -> list[dict]:
    """Автоматическая оценка начисления для ОТКРЫТОГО «хвоста» участка типа
    «Счётчик» — периода после последнего показания (или после ввода счётчика
    в эксплуатацию, если показаний не было вовсе), если он не передаётся уже
    `auto_settings['months']` месяцев и более (см. Ситуация 1/2 порядка
    действий СНТ по электроэнергии).

    Отдельного «отката» не требуется: как только придёт новое показание,
    закрывающее разрыв, эти месяцы естественным образом попадут в обычный
    расчёт по счётчику (см. all_charges) при следующем вызове, а данная
    функция для них больше ничего не сгенерирует — просто потому что разрыв
    больше не «открыт». Ничего не пишется на диск, это чисто расчётная
    надстройка над charges_for_plot().

    Метод оценки берётся из настроек САМОГО участка (`calc_method`/`norm_kw`/
    `avg_window_months` — те же поля, что и у ручного расчётного метода, если
    администратор когда-либо явно их выбирал через диалог «Тип расчёта»).
    Если участок вообще не настраивался (поля отсутствуют — типичный случай
    для нетронутого участка типа «Счётчик») — по умолчанию пробуем среднее по
    своей истории: оно не требует ничего вводить руками и использует уже
    имеющиеся показания. Норматив мощности как дефолт тут НЕ используется
    (в отличие от calc_method_of(), где дефолт — «norm», в паре с ручным
    диалогом, где выбор всегда явный) — иначе оценка на нетронутом участке
    молча возвращала бы [] из-за отсутствующего norm_kw. Если своей истории
    категорически нет (ни одного показания вообще) — используем норматив
    мощности (если задан). Если нет ни истории, ни норматива —
    оценить нечем, возвращается []."""
    if not auto_settings or not auto_settings.get("enabled"):
        return []
    if billing_type_of(plot, plots) != BILLING_METER:
        return []
    threshold = auto_settings.get("months") or DEFAULT_AUTO_SWITCH_MONTHS
    mwr = months_without_reading(plot, meters, as_of, plots=plots)
    if mwr is None or mwr < threshold:
        return []

    readings = plot_readings(plot, meters)
    if readings:
        ly, lm, _ = readings[-1]
        start = date(ly + 1, 1, 1) if lm == 12 else date(ly, lm + 1, 1)
    else:
        base = _parse_iso(plot_record(plot, plots).get("meter_commission_date", ""))
        if base is None:
            return []
        start = date(base.year, base.month, 1)
    if start > as_of:
        return []

    rec = plot_record(plot, plots)
    raw_method = rec.get("calc_method")
    if raw_method in CALC_METHODS:
        # Способ расчёта для этого участка когда-то был явно выбран
        # (в диалоге «Тип расчёта») — уважаем этот выбор.
        method = raw_method
    else:
        # Никогда не настраивалось: пробуем среднее по своей истории первым —
        # это ничего не требует руками ввести, в отличие от норматива, и
        # именно история участка — самый устойчивый в суде метод (см.
        # own_average_kwh). Норматив как способ по умолчанию (calc_method_of())
        # тут не годится — он почти наверняка не задан на нетронутом участке.
        method = CALC_METHOD_OWN_AVERAGE
    charges: list[dict] = []
    if method == CALC_METHOD_OWN_AVERAGE:
        window = avg_window_months_of(plot, plots, auto_settings)
        avg = own_average_kwh(plot, meters, replacements, before=start,
                              window_months=window)
        if avg is not None:
            charges = own_average_charges(plot, avg, start, rates, up_to=as_of)
    if not charges:
        norm = norm_kw_of(plot, plots, auto_settings)
        charges = calculated_charges(plot, norm, start, rates, up_to=as_of)
    for c in charges:
        c["auto_estimate"] = True
    return charges


# ── платежи из выписки ────────────────────────────────────────────────

def parse_breakdown(value, *, total: Optional[float] = None) -> list:
    """Читает ручную разбивку операции (см. ui/detail_widget.py::_parse_breakdown —
    та же колонка `_breakdown`, тот же формат): список {Сумма, Категория, Участок}.
    Пользователь получает эту разбивку через «Разделить операцию» — например,
    у операции с авто-категорией CAT_MIXED («Членские взносы + Электроэнергия»,
    сама по себе просто подсказка «раздели вручную»).

    Если передан `total` (сумма родительской операции) — разбивка считается
    ГОТОВОЙ (и возвращается), только если сумма её строк совпадает с `total`.
    Иначе — [] (как будто разбивки нет вообще). Это защита от промежуточного
    состояния: инлайн-добавление строки распределения через контекстное меню
    (`DetailWidget._add_split`) сразу пишет в данные новую строку с Суммой=0,
    БЕЗ проверки итога (в отличие от модального EditOperationDialog, где
    кнопка «Сохранить» заблокирована при несовпадении сумм) — до тех пор,
    пока пользователь не заполнит все строки разбивки на полную сумму,
    расчёты (задолженность, дашборд) должны падать обратно на верхнеуровневую
    категорию операции, а не терять часть денег на недописанной разбивке."""
    if isinstance(value, list):
        items = value
    elif isinstance(value, str) and value.strip():
        try:
            data = json.loads(value)
            items = data if isinstance(data, list) else []
        except (ValueError, TypeError):
            items = []
    else:
        items = []

    if total is not None and items:
        items_sum = sum(
            (_to_float(i.get("Сумма")) or 0.0) for i in items if isinstance(i, dict))
        # Строки разбивки несут знак родительской операции (так их создаёт и
        # правит ui/detail_widget.py, включая клэмпинг остатка при total < 0) —
        # сравниваем обе величины со знаком, а не items_sum с abs(total).
        if abs(items_sum - total) > 0.01:
            return []
    return items


def _row_amount_for_plot(row: pd.Series, plot: str, cats: Optional[set] = None) -> float:
    """Сумма строки, относящаяся к конкретному участку.

    Если у строки есть ручная разбивка (`_breakdown`) и передан `cats` —
    считаем ТОЛЬКО по разбивке (учитывая её собственные Категория/Сумма/
    Участок построчно, без грубого «пополам» для CAT_MIXED — пользователь
    уже сам точно указал суммы). Пустой «Участок» у строки разбивки —
    наследует участок родительской операции. Без разбивки — прежнее
    поведение (CAT_MIXED делится пополам)."""
    breakdown = (parse_breakdown(row.get("_breakdown"), total=row.get("Сумма"))
                 if cats is not None else [])
    if breakdown:
        parent_plot = str(row.get("Участок", "")).strip()
        result = 0.0
        for item in breakdown:
            if str(item.get("Категория", "")) not in cats:
                continue
            item_plot_raw = str(item.get("Участок", "")).strip() or parent_plot
            item_plots = [p.strip() for p in item_plot_raw.split(",") if p.strip()]
            if plot not in item_plots:
                continue
            amt = _to_float(item.get("Сумма"))
            if amt is None or amt <= 0:
                continue
            result += amt / len(item_plots)
        return result

    plots = [p.strip() for p in str(row.get("Участок", "")).split(",") if p.strip()]
    if plot not in plots:
        return 0.0
    amount = _to_float(row.get("Сумма"))
    # amount != amount — проверка на NaN: такая «сумма» (например, у вручную
    # добавленной операции без суммы) иначе отравляет NaN'ом весь итог.
    if amount is None or amount != amount or amount <= 0:
        return 0.0
    if row.get("Категория") == CAT_MIXED:
        amount /= 2
    amount /= len(plots)
    return amount


def _row_amount_for_cats(row: pd.Series, cats: set) -> float:
    """Сумма строки, релевантная набору категорий `cats`, БЕЗ привязки к
    конкретному участку (для СНТ-суммарных отчётов, см. reconcile()).
    С разбивкой — сумма её подходящих строк; без — прежнее «пополам»
    для CAT_MIXED."""
    breakdown = parse_breakdown(row.get("_breakdown"), total=row.get("Сумма"))
    if breakdown:
        result = 0.0
        for item in breakdown:
            if str(item.get("Категория", "")) not in cats:
                continue
            amt = _to_float(item.get("Сумма"))
            if amt is not None and amt > 0:
                result += amt
        return result

    amount = _to_float(row.get("Сумма"))
    if amount is None or amount != amount or amount <= 0:
        return 0.0
    if row.get("Категория") == CAT_MIXED:
        amount /= 2
    return amount


def _rows_matching_cats(df: pd.DataFrame, cats: set) -> pd.DataFrame:
    """Строки, чья верхнеуровневая Категория входит в `cats`, ИЛИ у которых
    есть ЗАВЕРШЁННАЯ ручная разбивка (сумма строк разбивки == Сумма
    операции — см. parse_breakdown), содержащая хотя бы одну подходящую
    категорию. Незавершённая разбивка (например, только что начатая через
    контекстное меню «Добавить распределение», ещё без введённых сумм)
    не в счёт — такая строка обрабатывается по своей верхнеуровневой
    категории, как будто разбивки ещё нет (см. parse_breakdown)."""
    mask = df["Категория"].isin(cats)
    if "_breakdown" in df.columns:
        # Разбивка есть у единиц строк — парсим JSON только там, где колонка
        # непуста и строка ещё не прошла по верхнеуровневой категории
        # (df.apply по всем строкам здесь превращался в узкое место).
        bd_col = df["_breakdown"]
        candidates = bd_col.notna() & ~mask
        if candidates.any():
            sums = df["Сумма"]
            extra = []
            for idx in df.index[candidates]:
                items = parse_breakdown(bd_col.at[idx], total=sums.at[idx])
                if any(str(i.get("Категория", "")) in cats
                       for i in items if isinstance(i, dict)):
                    extra.append(idx)
            if extra:
                mask = mask.copy()
                mask.loc[extra] = True
    return df[mask]


def payments_index(df, cats: set) -> dict[str, list[dict]]:
    """Индекс платежей по участкам за ОДИН проход по выписке.

    Возвращает ``{участок: [{date, amount, mixed, purpose}, ...]}`` —
    списки отсортированы по дате. Суммы и состав записей идентичны
    последовательным вызовам ``_rows_matching_cats`` +
    ``_row_amount_for_plot`` для каждого участка, но без O(участки × строки):
    вкладки со сводкой по всем участкам строят индекс один раз и передают
    его в ``payments_total``/``payments_breakdown``/``balance`` и т.п.
    """
    d = _ensure_df(df)
    out: dict[str, list[dict]] = {}
    if d is None:
        return out

    n = len(d)
    dates = list(d["Дата"])
    sums = list(d["Сумма"])
    cats_col = list(d["Категория"])
    plots_col = list(d["Участок"])
    bds = list(d["_breakdown"]) if "_breakdown" in d.columns else [None] * n
    purps = list(d["Назначение"]) if "Назначение" in d.columns else [""] * n

    for dt, total, cat, plot_raw, raw_bd, purp in zip(
            dates, sums, cats_col, plots_col, bds, purps):
        breakdown = []
        if isinstance(raw_bd, list) or (isinstance(raw_bd, str) and raw_bd.strip()):
            breakdown = parse_breakdown(raw_bd, total=total)

        row_amounts: dict[str, float] = {}
        if breakdown:
            parent_plot = str(plot_raw).strip()
            for item in breakdown:
                if not isinstance(item, dict):
                    continue
                if str(item.get("Категория", "")) not in cats:
                    continue
                item_plot_raw = str(item.get("Участок", "")).strip() or parent_plot
                item_plots = [p.strip() for p in item_plot_raw.split(",") if p.strip()]
                if not item_plots:
                    continue
                amt = _to_float(item.get("Сумма"))
                if amt is None or amt != amt or amt <= 0:
                    continue
                # Делим на полную длину списка, но каждому УНИКАЛЬНОМУ участку
                # засчитываем долю один раз — так же, как _row_amount_for_plot
                # при дубликате участка в списке («16, 16»).
                share = amt / len(item_plots)
                for p in dict.fromkeys(item_plots):
                    row_amounts[p] = row_amounts.get(p, 0.0) + share
        elif cat in cats:
            plots = [p.strip() for p in str(plot_raw).split(",") if p.strip()]
            amount = _to_float(total)
            if plots and amount is not None and amount == amount and amount > 0:
                if cat == CAT_MIXED:
                    amount /= 2
                share = amount / len(plots)
                for p in dict.fromkeys(plots):
                    row_amounts[p] = row_amounts.get(p, 0.0) + share

        if not row_amounts:
            continue
        entry_date = dt.date() if pd.notna(dt) else None
        mixed = (cat == CAT_MIXED and not breakdown)
        purpose = str(purp)
        for p, amount in row_amounts.items():
            out.setdefault(p, []).append({
                "date": entry_date,
                "amount": amount,
                "mixed": mixed,
                "purpose": purpose,
            })

    for lst in out.values():
        lst.sort(key=lambda r: r["date"] or date.min)
    return out


def payments_total(plot: str, df, date_from: Optional[date] = None,
                   date_to: Optional[date] = None,
                   index: Optional[dict] = None) -> float:
    """Сумма платежей за электричество для участка в интервале [from, to].

    `index` — заранее построенный payments_index(df, CATS_ELECTRO_INCOME):
    при массовых расчётах по всем участкам избавляет от повторного
    сканирования всей выписки на каждый участок."""
    if index is not None:
        total = 0.0
        for p in index.get(str(plot), ()):
            d0 = p["date"]
            if date_from is not None and (d0 is None or d0 < date_from):
                continue
            if date_to is not None and (d0 is None or d0 > date_to):
                continue
            total += p["amount"]
        return total

    df = _ensure_df(df)
    if df is None:
        return 0.0
    d = _rows_matching_cats(df, CATS_ELECTRO_INCOME).copy()
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
        total += _row_amount_for_plot(row, plot, cats=CATS_ELECTRO_INCOME)
    return total


def payments_breakdown(plot: str, df, index: Optional[dict] = None) -> list[dict]:
    """Хронологический список платежей по электричеству для участка.

    `index` — см. payments_total()."""
    if index is not None:
        return list(index.get(str(plot), ()))

    df = _ensure_df(df)
    if df is None:
        return []
    d = _rows_matching_cats(df, CATS_ELECTRO_INCOME).copy()
    if d.empty:
        return []
    out = []
    for _, row in d.iterrows():
        amount = _row_amount_for_plot(row, plot, cats=CATS_ELECTRO_INCOME)
        if amount <= 0:
            continue
        out.append({
            "date": row["Дата"].date() if pd.notna(row["Дата"]) else None,
            "amount": amount,
            # mixed=True — операция авто-помечена «Членские взносы +
            # Электроэнергия», но ещё НЕ разделена пользователем вручную
            # (см. parse_breakdown); это подсказка UI «раздели точнее».
            "mixed": (row.get("Категория") == CAT_MIXED
                      and not parse_breakdown(row.get("_breakdown"), total=row.get("Сумма"))),
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
    auto_estimated: bool = False


def balance(plot: str, as_of: date, meters: dict, rates: list,
            replacements: dict, baseline: dict, df,
            plots: Optional[list] = None,
            auto_settings: Optional[dict] = None,
            pay_index: Optional[dict] = None) -> Balance:
    base = _to_float(baseline.get("balances", {}).get(str(plot))) or 0.0
    base_start = _parse_iso(baseline.get("start_date", ""))

    charged = 0.0
    last: Optional[tuple[int, int, float]] = None
    auto_estimated = False
    for c in charges_for_plot(plot, meters, rates, replacements,
                              up_to=as_of, plots=plots, auto_settings=auto_settings):
        if c["amount"] is not None:
            charged += c["amount"]
        if c["value"] is not None:
            last = (c["year"], c["month"], c["value"])
        if c.get("auto_estimate"):
            auto_estimated = True

    paid = payments_total(plot, df, date_from=base_start, date_to=as_of,
                          index=pay_index)
    debt = base + charged - paid

    # Сколько месяцев подряд без платежей электро (от последнего платежа до as_of)
    months_without_payment: Optional[int] = None
    breakdown = payments_breakdown(plot, df, index=pay_index)
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
        auto_estimated=auto_estimated,
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
                      plots: Optional[list] = None,
                      auto_settings: Optional[dict] = None,
                      pay_index: Optional[dict] = None) -> list[EnergyOwnerBalance]:
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
                              up_to=as_of, plots=plots, auto_settings=auto_settings):
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
    for p in payments_breakdown(plot, df, index=pay_index):
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
        sub = _rows_matching_cats(d, CATS_ELECTRO_INCOME).copy()
        if not sub.empty:
            dates = sub["Дата"].dt.date
            sub = sub[(dates >= date_from) & (dates <= date_to)]
            for _, row in sub.iterrows():
                collected_total += _row_amount_for_cats(row, CATS_ELECTRO_INCOME)

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
                              plots: Optional[list] = None,
                              auto_settings: Optional[dict] = None,
                              pay_index: Optional[dict] = None) -> EnergyGroupBalance:
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
                                               up_to=as_of, plots=plots,
                                               auto_settings=auto_settings)
        if c["amount"] is not None
        and (since is None or reading_date(c["year"], c["month"]) >= since)
    )

    effective_since = since if since is not None else base_start
    paid = payments_total(plot, df, date_from=effective_since, date_to=as_of,
                          index=pay_index)

    return EnergyGroupBalance(
        charged=charged,
        paid=paid,
        baseline=effective_base,
        debt=effective_base + charged - paid,
    )


# ── уровень долга для UI ─────────────────────────────────────────────

def debt_level(debt: float, monthly_avg: float = 0.0) -> str:
    """Уровень долга: 'ok' | 'low' | 'mid' | 'high'.

    Конкретные цвета подставляет UI (ui.theme.C.DEBT / C.DEBT_BG) —
    ядро о палитре не знает."""
    if debt <= 0:
        return "ok"             # аванс / ноль
    threshold = max(monthly_avg, 500.0)
    if debt <= threshold:
        return "low"            # небольшой долг
    if debt <= 3 * threshold:
        return "mid"
    return "high"               # крупный долг
