"""Чистое ядро расчёта показателей дашборда «Главная» — без UI и Qt.

«Период» берётся из вкладки «Членские взносы» (snt_vznosy_rates.json):
текущий период — тот, в который попадает сегодняшняя дата; прошлый —
предыдущий по списку. Это позволяет сравнивать поток средств по членским
периодам (июль→июль), а не по календарным годам.

Модуль не лезет в Qt и почти не лезет в файловую систему: транзакции
принимаются готовым DataFrame, а если его нет — подхватывается
data/detail_transactions.json через load_transactions_df().
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from core import energy, vznosy
from core.utils import _read_json, DATA_DIR

TRANSACTIONS_FILE = os.path.join(DATA_DIR, "detail_transactions.json")

# Сентинел для build(): «автоматически выбрать предыдущий период» (обратная совместимость)
_AUTO = object()

# Категории выписки, важные для дашборда
CAT_VZNOSY = "Членские взносы"
CAT_ELECTRO_OWNERS = "Электроэнергия (от садоводов)"
CAT_ELECTRO_SUPPLIER = "Оплата электроэнергии (поставщик)"
CAT_MIXED = "Членские взносы + Электроэнергия"

_MONTH_RU = ["", "Янв", "Фев", "Мар", "Апр", "Май", "Июн",
             "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]


# ── загрузка / нормализация транзакций ────────────────────────────────

def _norm_plot(v) -> str:
    """Аккуратно приводит значение участка к строке (19.0 → «19»)."""
    if v is None:
        return ""
    if isinstance(v, float):
        if pd.isna(v):
            return ""
        if v == int(v):
            return str(int(v))
        return str(v)
    return str(v).strip()


def _normalize_df(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Приводит DataFrame к виду с колонками Дата(datetime), Сумма(знаковая),
    Категория, Участок. Понимает оба формата выписки: с разделёнными
    Поступление/Списание и с уже объединённой Суммой."""
    if df is None or len(df) == 0:
        return None
    df = df.copy()

    if "Дата" not in df.columns:
        return None
    if not pd.api.types.is_datetime64_any_dtype(df["Дата"]):
        s = df["Дата"]
        if pd.api.types.is_numeric_dtype(s):
            df["Дата"] = pd.to_datetime(s, unit="ms", errors="coerce")
        else:
            df["Дата"] = pd.to_datetime(s, errors="coerce", dayfirst=True)

    if "Сумма" not in df.columns:
        if "Поступление" in df.columns:
            inc = pd.to_numeric(df["Поступление"], errors="coerce").fillna(0.0)
        else:
            inc = pd.Series(0.0, index=df.index)
        if "Списание" in df.columns:
            exp = pd.to_numeric(df["Списание"], errors="coerce").fillna(0.0)
        else:
            exp = pd.Series(0.0, index=df.index)
        df["Сумма"] = inc - exp
    else:
        df["Сумма"] = pd.to_numeric(df["Сумма"], errors="coerce")

    if "Категория" not in df.columns:
        df["Категория"] = ""
    df["Категория"] = df["Категория"].fillna("").astype(str)

    if "Участок" not in df.columns:
        df["Участок"] = ""
    df["Участок"] = df["Участок"].apply(_norm_plot)

    df = df[df["Дата"].notna()].copy()
    if df.empty:
        return None
    return df


def load_transactions_df() -> Optional[pd.DataFrame]:
    """Читает data/detail_transactions.json (если есть) и возвращает
    нормализованный DataFrame. None — файла нет или он пуст/битый."""
    recs = _read_json(TRANSACTIONS_FILE, None)
    if not recs:
        return None
    try:
        return _normalize_df(pd.DataFrame(recs))
    except Exception:
        return None


# ── периоды членских взносов ──────────────────────────────────────────

@dataclass
class Period:
    date_from: date
    date_to: date
    label: str                       # «2025/26»
    amount: Optional[float] = None   # тариф периода, ₽


def _periods() -> list[Period]:
    """Список периодов ЧВ, отсортированный по дате начала. date_to берётся
    из записи, а если его нет — из начала следующего периода."""
    built = vznosy.build_periods(vznosy.load_rates())
    raw: list[tuple[date, Optional[date], dict]] = []
    for r in built:
        pf = energy._parse_iso(r.get("date_from", ""))
        if pf is None:
            continue
        pt = energy._parse_iso(r.get("date_to")) if r.get("date_to") else None
        raw.append((pf, pt, r))

    out: list[Period] = []
    for i, (pf, pt, r) in enumerate(raw):
        if pt is None:
            if i + 1 < len(raw):
                pt = raw[i + 1][0] - timedelta(days=1)
            else:
                pt = date(pf.year + 1, pf.month, pf.day) - timedelta(days=1)
        label = f"{pf.year}/{str(pt.year)[-2:]}"
        out.append(Period(pf, pt, label, energy._to_float(r.get("amount"))))
    return out


def get_all_periods() -> list[Period]:
    """Все периоды ЧВ, отсортированные от самого раннего к самому позднему."""
    return _periods()


# ── агрегаты по потокам ───────────────────────────────────────────────

def _window_mask(df: pd.DataFrame, d0: date, d1: date):
    dates = df["Дата"].dt.date
    return (dates >= d0) & (dates <= d1)


@dataclass
class FlowSums:
    income: float
    expense: float
    electricity: float        # расход на электроэнергию (поставщику)


def flow_sums(df: Optional[pd.DataFrame], d0: date, d1: date) -> FlowSums:
    """Приход, расход и расход на электричество в окне [d0, d1]."""
    if df is None:
        return FlowSums(0.0, 0.0, 0.0)
    g = df[_window_mask(df, d0, d1)]
    if g.empty:
        return FlowSums(0.0, 0.0, 0.0)
    s = pd.to_numeric(g["Сумма"], errors="coerce")
    income = float(s[s > 0].sum())
    expense = float(-s[s < 0].sum())
    el = pd.to_numeric(
        g[g["Категория"] == CAT_ELECTRO_SUPPLIER]["Сумма"], errors="coerce")
    electricity = float(-el[el < 0].sum())
    return FlowSums(income, expense, electricity)


def _period_end_balance(df: Optional[pd.DataFrame], end_date: date) -> float:
    """Накопленный остаток с первой транзакции по end_date включительно."""
    if df is None:
        return 0.0
    mask = df["Дата"].dt.date <= end_date
    return float(pd.to_numeric(df.loc[mask, "Сумма"], errors="coerce").fillna(0.0).sum())


def trend_pct(current: float, previous: Optional[float]) -> Optional[float]:
    """Процент изменения current относительно previous. None — нет базы."""
    if previous is None or abs(previous) < 1e-9:
        return None
    return (current - previous) / previous * 100.0


# ── задолженность по членским взносам ─────────────────────────────────

@dataclass
class DebtSummary:
    total_debt: float
    debtor_count: int
    plot_count: int


def vznosy_debt_summary(df: Optional[pd.DataFrame], as_of: date) -> DebtSummary:
    """Суммарный долг (ЧВ + электроэнергия) и число должников на дату as_of.

    Переплата по одной статье или у одного участка не уменьшает итог:
    для каждого участка берётся max(0, долг_ЧВ) + max(0, долг_электро).
    """
    rates = vznosy.load_rates()
    adj = vznosy.load_adjustments()
    areas = vznosy.plot_area_map()
    plot_records = energy.load_plots()
    plots = [str(p.get("num", "")) for p in plot_records if p.get("num")]

    e_meters = energy.load_meters()
    e_rates = energy.load_rates()
    e_replacements = energy.load_replacements()
    e_baseline = energy.load_baseline()

    # Индексы платежей — один проход по выписке на все участки
    vz_idx = vznosy.payments_index(df)
    en_idx = energy.payments_index(df, energy.CATS_ELECTRO_INCOME)

    total = 0.0
    debtors = 0
    for plot in plots:
        plot_debt = 0.0
        try:
            vznosy_bal = vznosy.balance_for_plot(
                plot, areas.get(plot), as_of, rates, adj, df, pay_index=vz_idx)
            plot_debt += max(0.0, vznosy_bal.debt)
        except Exception:
            pass
        try:
            e_bal = energy.balance(
                plot, as_of, e_meters, e_rates, e_replacements, e_baseline, df,
                plots=plot_records, pay_index=en_idx)
            plot_debt += max(0.0, e_bal.debt)
        except Exception:
            pass
        total += plot_debt
        if plot_debt > 0.5:
            debtors += 1
    return DebtSummary(total, debtors, len(plots))


# ── помесячная разбивка для столбчатого графика ───────────────────────

def _month_iter(start: date, count: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    for _ in range(count):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


@dataclass
class MonthBar:
    year: int
    month: int
    label: str
    income: float
    expense: float


@dataclass
class MonthCategoryBar:
    """Помесячный поток с разбивкой по категориям (для сгруппированной гистограммы)."""
    year: int
    month: int
    label: str
    categories: list  # [(name: str, amount: float), ...]

    @property
    def total(self) -> float:
        return sum(a for _, a in self.categories)


def monthly_breakdown(df: Optional[pd.DataFrame],
                      period: Optional[Period]) -> list[MonthBar]:
    """Помесячные приход/расход за 12 месяцев периода."""
    if period is None:
        return []
    months = _month_iter(period.date_from, 12)
    if df is None:
        return [MonthBar(y, m, _MONTH_RU[m], 0.0, 0.0) for (y, m) in months]

    g = df[_window_mask(df, period.date_from, period.date_to)].copy()
    out: list[MonthBar] = []
    if g.empty:
        return [MonthBar(y, m, _MONTH_RU[m], 0.0, 0.0) for (y, m) in months]

    g["_y"] = g["Дата"].dt.year
    g["_m"] = g["Дата"].dt.month
    s = pd.to_numeric(g["Сумма"], errors="coerce")
    for (y, m) in months:
        sel = s[(g["_y"] == y) & (g["_m"] == m)]
        income = float(sel[sel > 0].sum())
        expense = float(-sel[sel < 0].sum())
        out.append(MonthBar(y, m, _MONTH_RU[m], income, expense))
    return out


# ── помесячная разбивка по категориям для гистограмм ─────────────────

def monthly_category_breakdown(df: Optional[pd.DataFrame],
                               period: Optional[Period],
                               kind: str) -> list[MonthCategoryBar]:
    """Помесячный поток по категориям за 12 месяцев периода.

    kind: 'income' — положительные операции, 'expense' — отрицательные.
    Операция с ручной разбивкой (см. energy.parse_breakdown) учитывается
    по разбивке, построчно по её собственным категориям/суммам. Операция
    без разбивки, но со смешанной авто-категорией — делится поровну между
    членскими взносами и электроэнергией (как в остальных расчётах
    программы)."""
    if period is None:
        return []
    months = _month_iter(period.date_from, 12)
    if df is None:
        return [MonthCategoryBar(y, m, _MONTH_RU[m], []) for y, m in months]

    g = df[_window_mask(df, period.date_from, period.date_to)].copy()
    g["_s"] = pd.to_numeric(g["Сумма"], errors="coerce").fillna(0.0)

    # Разворачиваем строки с ЗАВЕРШЁННОЙ ручной разбивкой (сумма строк
    # разбивки == Сумма операции, см. energy.parse_breakdown — иначе,
    # например, только что начатая через контекстное меню разбивка без
    # введённых сумм молча "съела" бы деньги операции) — каждая подстрока
    # становится отдельной строкой со своей Категорией/Суммой (Дата — от
    # родительской операции, нужна для группировки по месяцу ниже).
    has_breakdown = (
        g.apply(lambda r: bool(energy.parse_breakdown(r.get("_breakdown"), total=r.get("Сумма"))), axis=1)
        if "_breakdown" in g.columns else pd.Series(False, index=g.index))
    if has_breakdown.any():
        exploded_rows = []
        for _, row in g[has_breakdown].iterrows():
            for item in energy.parse_breakdown(row.get("_breakdown"), total=row.get("Сумма")):
                item_amt = energy._to_float(item.get("Сумма"))
                if item_amt is None or item_amt <= 0:
                    continue
                r = row.copy()
                r["Категория"] = str(item.get("Категория", "")).strip() or "Прочее"
                r["_s"] = item_amt if row["_s"] >= 0 else -item_amt
                exploded_rows.append(r)
        exploded = (pd.DataFrame(exploded_rows) if exploded_rows
                    else g.iloc[0:0].copy())
        g = pd.concat([g[~has_breakdown], exploded], ignore_index=True)

    # Разворачиваем смешанную авто-категорию (только у строк БЕЗ разбивки —
    # они уже обработаны выше и больше не несут CAT_MIXED).
    mixed_mask = g["Категория"] == CAT_MIXED
    if mixed_mask.any():
        half_s = g.loc[mixed_mask, "_s"] / 2.0
        vznosy_rows = g[mixed_mask].copy()
        vznosy_rows["Категория"] = CAT_VZNOSY
        vznosy_rows["_s"] = half_s.values
        electro_rows = g[mixed_mask].copy()
        electro_rows["Категория"] = CAT_ELECTRO_OWNERS
        electro_rows["_s"] = half_s.values
        g = pd.concat([g[~mixed_mask], vznosy_rows, electro_rows],
                      ignore_index=True)

    if kind == "income":
        g = g[g["_s"] > 0].copy()
    else:
        g = g[g["_s"] < 0].copy()
        g["_s"] = g["_s"].abs()

    if g.empty:
        return [MonthCategoryBar(y, m, _MONTH_RU[m], []) for y, m in months]

    g["_y"] = g["Дата"].dt.year
    g["_m"] = g["Дата"].dt.month
    g["Категория"] = g["Категория"].fillna("").apply(
        lambda x: x.strip() or "Прочее")

    grouped = g.groupby(["_y", "_m", "Категория"])["_s"].sum()

    out: list[MonthCategoryBar] = []
    for (y, m) in months:
        try:
            month_s = grouped.xs((y, m), level=["_y", "_m"])
            cats = [(cat, float(v)) for cat, v in month_s.items() if v > 0.005]
            cats.sort(key=lambda x: x[1], reverse=True)
        except KeyError:
            cats = []
        out.append(MonthCategoryBar(y, m, _MONTH_RU[m], cats))
    return out


# ── разбивка по категориям для кольцевых диаграмм ──────────────────────

@dataclass
class CategorySlice:
    name: str
    amount: float


def category_breakdown(df: Optional[pd.DataFrame], d0: date, d1: date,
                       kind: str) -> list[CategorySlice]:
    """Суммы по категориям выписки в окне [d0, d1].

    kind: 'income' — положительные операции, 'expense' — отрицательные.
    Операция с ручной разбивкой (см. energy.parse_breakdown — пользователь
    разделил её через «Добавить распределение») учитывается ПО РАЗБИВКЕ,
    построчно по её собственным категориям/суммам. Операция без разбивки,
    но с авто-категорией «Членские взносы + Электроэнергия» (CAT_MIXED,
    это лишь подсказка «раздели вручную») делится 50/50 между членскими
    взносами и электроэнергией — так же, как в остальных расчётах программы."""
    if df is None:
        return []
    g = df[_window_mask(df, d0, d1)].copy()
    if g.empty:
        return []
    g["_s"] = pd.to_numeric(g["Сумма"], errors="coerce").fillna(0.0)
    g = g[g["_s"] > 0] if kind == "income" else g[g["_s"] < 0]

    totals: dict[str, float] = {}
    for _, row in g.iterrows():
        breakdown = energy.parse_breakdown(row.get("_breakdown"), total=row.get("Сумма"))
        if breakdown:
            for item in breakdown:
                item_amt = energy._to_float(item.get("Сумма"))
                if item_amt is None or item_amt <= 0:
                    continue
                item_cat = str(item.get("Категория", "")).strip() or "Прочее"
                totals[item_cat] = totals.get(item_cat, 0.0) + item_amt
            continue
        cat = str(row.get("Категория", "")).strip() or "Прочее"
        amt = abs(float(row["_s"]))
        if cat == CAT_MIXED:
            half = amt / 2.0
            totals[CAT_VZNOSY] = totals.get(CAT_VZNOSY, 0.0) + half
            totals[CAT_ELECTRO_OWNERS] = \
                totals.get(CAT_ELECTRO_OWNERS, 0.0) + half
        else:
            totals[cat] = totals.get(cat, 0.0) + amt

    out = [CategorySlice(n, v) for n, v in totals.items() if v > 0.005]
    out.sort(key=lambda c: c.amount, reverse=True)
    return out


# ── итоговый снимок для виджета ────────────────────────────────────────

@dataclass
class DashboardData:
    has_data: bool
    as_of: date
    current: Optional[Period]
    previous: Optional[Period]

    balance: float

    collected: float
    collected_trend: Optional[float]
    spent: float
    spent_trend: Optional[float]
    electricity: float
    electricity_trend: Optional[float]

    debt: DebtSummary

    # Все доступные периоды + индекс выбранного (для UI-комбобокса)
    all_periods: list[Period] = field(default_factory=list)
    selected_period_idx: int = 0

    # Простые месячные итоги (legacy, оставлены для совместимости)
    months: list[MonthBar] = field(default_factory=list)
    months_prev: list[MonthBar] = field(default_factory=list)

    # Помесячные данные по категориям (для grouped stacked chart)
    months_cat: list[MonthCategoryBar] = field(default_factory=list)
    months_cat_exp: list[MonthCategoryBar] = field(default_factory=list)
    months_cat_prev: list[MonthCategoryBar] = field(default_factory=list)
    months_cat_exp_prev: list[MonthCategoryBar] = field(default_factory=list)

    income_slices: list[CategorySlice] = field(default_factory=list)
    expense_slices: list[CategorySlice] = field(default_factory=list)

    # ── поля для произвольного периода сравнения ──────────────────────────
    # comparison_period_idx: None → сравнения нет
    comparison_period_idx: Optional[int] = None

    # Абсолютные значения и разницы метрик периода сравнения
    balance_comp: float = 0.0
    balance_trend: Optional[float] = None
    balance_diff: float = 0.0

    collected_comp: float = 0.0
    collected_diff: float = 0.0
    spent_comp: float = 0.0
    spent_diff: float = 0.0
    electricity_comp: float = 0.0
    electricity_diff: float = 0.0

    # Категориальные срезы периода сравнения (для вторых кольцевых диаграмм)
    income_slices_comp: list[CategorySlice] = field(default_factory=list)
    expense_slices_comp: list[CategorySlice] = field(default_factory=list)

    # Данные для задолженности с поддержкой сравнения
    debt_comp: Optional[DebtSummary] = None
    debt_trend: Optional[float] = None
    debt_diff: float = 0.0

    # Дата первой транзакции в детализации (для подписи к Балансу)
    data_start_date: Optional[date] = None


def build(df: Optional[pd.DataFrame],
          as_of: Optional[date] = None,
          selected_period_idx: Optional[int] = None,
          comparison_period_idx=_AUTO) -> DashboardData:
    """Собирает все показатели дашборда из выписки `df`.

    selected_period_idx — 0-based индекс выбранного периода (None → авто).
    comparison_period_idx — 0-based индекс периода сравнения:
      _AUTO (по умолчанию) → предыдущий период (обратная совместимость);
      None → без сравнения;
      int  → конкретный период (кроме selected_period_idx).
    """
    as_of = as_of or date.today()
    df = _normalize_df(df)

    data_start_date: Optional[date] = None
    if df is not None and not df.empty:
        _min_dt = df["Дата"].min()
        if pd.notna(_min_dt):
            data_start_date = _min_dt.date()

    all_perds = get_all_periods()

    # ── выбор основного периода ───────────────────────────────────────
    if not all_perds:
        sel_idx, cur = 0, None
    else:
        if selected_period_idx is None:
            sel_idx = None
            for i, p in enumerate(all_perds):
                if p.date_from <= as_of <= p.date_to:
                    sel_idx = i
                    break
            if sel_idx is None:
                sel_idx = len(all_perds) - 1
        else:
            sel_idx = max(0, min(selected_period_idx, len(all_perds) - 1))
        cur = all_perds[sel_idx]

    # ── выбор периода сравнения ───────────────────────────────────────
    if cur is None:
        comp_idx, comp = None, None
    elif comparison_period_idx is _AUTO:
        # По умолчанию — предыдущий по списку
        comp_idx = sel_idx - 1 if sel_idx > 0 else None
        comp = all_perds[comp_idx] if comp_idx is not None else None
    elif comparison_period_idx is None:
        comp_idx, comp = None, None
    else:
        c = int(comparison_period_idx)
        c = max(0, min(c, len(all_perds) - 1))
        if c == sel_idx:
            comp_idx, comp = None, None   # нельзя сравнивать с самим собой
        else:
            comp_idx, comp = c, all_perds[c]

    has_comp = comp is not None

    if cur is None:
        debt = vznosy_debt_summary(df, as_of)
        balance = _period_end_balance(df, as_of) if df is not None else 0.0
        return DashboardData(
            has_data=df is not None, as_of=as_of,
            current=None, previous=None,
            all_periods=all_perds, selected_period_idx=0,
            balance=balance,
            collected=0.0, collected_trend=None,
            spent=0.0, spent_trend=None,
            electricity=0.0, electricity_trend=None,
            debt=debt,
            comparison_period_idx=None,
            data_start_date=data_start_date,
        )

    # ── окна дат ──────────────────────────────────────────────────────
    # Выбранный период: если идёт сейчас — до сегодня, иначе до конца.
    sel_end = as_of if (cur.date_from <= as_of <= cur.date_to) else cur.date_to

    # Период сравнения: сопоставимое окно той же длины от его начала.
    if has_comp:
        elapsed = (sel_end - cur.date_from).days
        comp_end = min(comp.date_from + timedelta(days=elapsed), comp.date_to)
    else:
        comp_end = None

    # ── метрики выбранного периода ────────────────────────────────────
    balance = _period_end_balance(df, sel_end) if df is not None else 0.0
    cur_f = flow_sums(df, cur.date_from, sel_end)

    # ── задолженность (считается на конец выбранного периода) ─────────
    debt = vznosy_debt_summary(df, sel_end)

    # ── метрики периода сравнения ─────────────────────────────────────
    if has_comp:
        comp_f = flow_sums(df, comp.date_from, comp_end)
        balance_comp = _period_end_balance(df, comp_end) if df is not None else 0.0
        debt_comp_obj = vznosy_debt_summary(df, comp_end)
        debt_trend_val = trend_pct(debt.total_debt, debt_comp_obj.total_debt)
        debt_diff_val = debt.total_debt - debt_comp_obj.total_debt
    else:
        comp_f = FlowSums(0.0, 0.0, 0.0)
        balance_comp = 0.0
        debt_comp_obj = None
        debt_trend_val = None
        debt_diff_val = 0.0

    return DashboardData(
        has_data=df is not None,
        as_of=as_of,
        current=cur,
        previous=comp,                  # comp — период сравнения (любой)
        all_periods=all_perds,
        selected_period_idx=sel_idx,
        comparison_period_idx=comp_idx,

        balance=balance,
        collected=cur_f.income,
        collected_trend=trend_pct(cur_f.income, comp_f.income) if has_comp else None,
        spent=cur_f.expense,
        spent_trend=trend_pct(cur_f.expense, comp_f.expense) if has_comp else None,
        electricity=cur_f.electricity,
        electricity_trend=(trend_pct(cur_f.electricity, comp_f.electricity)
                           if has_comp else None),
        debt=debt,
        debt_comp=debt_comp_obj,
        debt_trend=debt_trend_val,
        debt_diff=debt_diff_val,

        # Абсолютные значения и разницы
        balance_comp=balance_comp,
        balance_trend=trend_pct(balance, balance_comp) if has_comp else None,
        balance_diff=(balance - balance_comp) if has_comp else 0.0,
        collected_comp=comp_f.income,
        collected_diff=(cur_f.income - comp_f.income) if has_comp else 0.0,
        spent_comp=comp_f.expense,
        spent_diff=(cur_f.expense - comp_f.expense) if has_comp else 0.0,
        electricity_comp=comp_f.electricity,
        electricity_diff=(cur_f.electricity - comp_f.electricity) if has_comp else 0.0,

        # Помесячная разбивка
        months=monthly_breakdown(df, cur),
        months_prev=monthly_breakdown(df, comp) if has_comp else [],
        months_cat=monthly_category_breakdown(df, cur, "income"),
        months_cat_exp=monthly_category_breakdown(df, cur, "expense"),
        months_cat_prev=(monthly_category_breakdown(df, comp, "income")
                         if has_comp else []),
        months_cat_exp_prev=(monthly_category_breakdown(df, comp, "expense")
                             if has_comp else []),

        # Категориальные срезы для кольцевых диаграмм
        income_slices=category_breakdown(df, cur.date_from, sel_end, "income"),
        expense_slices=category_breakdown(df, cur.date_from, sel_end, "expense"),
        income_slices_comp=(category_breakdown(df, comp.date_from, comp_end, "income")
                            if has_comp else []),
        expense_slices_comp=(category_breakdown(df, comp.date_from, comp_end, "expense")
                             if has_comp else []),

        data_start_date=data_start_date,
    )
