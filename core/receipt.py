"""Генерация PDF-квитанций по электроэнергии для участка."""
from __future__ import annotations

import html
import os
from datetime import date, timedelta

from PyQt6.QtCore import QMarginsF
from PyQt6.QtGui import QPageLayout, QPageSize, QTextDocument
from PyQt6.QtPrintSupport import QPrinter

from core import energy
from core.utils import fmt_money


_MONTHS = ["янв", "фев", "мар", "апр", "май", "июн",
           "июл", "авг", "сен", "окт", "ноя", "дек"]

def _fmt_kwh(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.0f}"


def _fmt_balance_cols(balance: float) -> tuple[str, str]:
    """Возвращает (долг, переплата) — одно заполнено, второе '—'."""
    if balance > 0.005:
        return fmt_money(balance), "—"
    if balance < -0.005:
        return "—", fmt_money(abs(balance))
    return "—", "—"


def _build_html(plot: str, df, as_of: date,
                since: date | None = None,
                zero_opening: bool = False) -> tuple[str, dict]:
    meters = energy.load_meters()
    rates = energy.load_rates()
    repls = energy.load_replacements()
    baseline = energy.load_baseline()
    owners = energy.owners_map().get(str(plot), [])
    owner_text = ", ".join(owners) if owners else "—"

    bt = energy.billing_type_of(str(plot))
    bal = energy.balance(plot, as_of, meters, rates, repls, baseline, df)
    charges = energy.charges_for_plot(plot, meters, rates, repls, up_to=as_of)

    # Фильтр по начальной дате периода
    if since is not None:
        charges = [c for c in charges
                   if date(c["year"], c["month"], 1) >= date(since.year, since.month, 1)]

    base_start_str = baseline.get("start_date", "")
    try:
        base_start: date | None = date.fromisoformat(base_start_str) if base_start_str else None
    except (ValueError, TypeError):
        base_start = None

    pay_by_month: dict[tuple[int, int], float] = {}
    for p in energy.payments_breakdown(plot, df):
        d = p["date"]
        if d is None or d > as_of:
            continue
        if since is not None and d < since:
            continue
        if base_start is not None and d < base_start:
            continue
        key = (d.year, d.month)
        pay_by_month[key] = pay_by_month.get(key, 0.0) + p["amount"]

    # Вычисляем входящее сальдо ДО цикла строк
    if since is not None:
        day_before = since - timedelta(days=1)
        bal_before = energy.balance(plot, day_before, meters, rates, repls, baseline, df)
        opening = bal_before.debt
        opening_label = f"Сальдо на {since.strftime('%d.%m.%Y')}"
        period_subtitle = (f"СНТ · период с {since.strftime('%d.%m.%Y')}"
                           f" по {as_of.strftime('%d.%m.%Y')}")
    else:
        opening = bal.baseline
        opening_label = "Начальное сальдо"
        period_subtitle = f"СНТ · по состоянию на {as_of.strftime('%d.%m.%Y')}"

    if zero_opening:
        opening = 0.0
        opening_label = "Начальное сальдо"

    baseline_row = (
        f"<tr><td>{opening_label}:</td>"
        f"<td align='right'>{fmt_money(opening)}</td></tr>"
    )

    # Платежи из месяцев без начислений относим к ближайшему следующему
    # месяцу с показанием; платежи после последнего показания — к последнему.
    sorted_charge_yms = sorted((c["year"], c["month"]) for c in charges)
    bucketed: dict[tuple[int, int], float] = {}
    for pay_ym, amt in pay_by_month.items():
        target = next((cm for cm in sorted_charge_yms if cm >= pay_ym), None)
        if target is None and sorted_charge_yms:
            target = sorted_charge_yms[-1]
        if target is not None:
            bucketed[target] = bucketed.get(target, 0.0) + amt

    running_balance = opening
    rows_html = []
    for c in charges:
        period = f"{_MONTHS[c['month']-1]} {c['year']}"
        prev_text = f"{c['prev_value']:g}" if c["prev_value"] is not None else "—"
        curr_text = f"{c['value']:g}" if c.get("value") is not None else "—"
        rate_text = f"{c['rate']:.2f}" if c["rate"] is not None else "—"
        amount_text = fmt_money(c["amount"])
        paid = bucketed.get((c["year"], c["month"]), 0.0)

        zad_dolg, zad_perep = _fmt_balance_cols(running_balance)
        running_balance += (c["amount"] or 0.0) - paid
        itogo_dolg, itogo_perep = _fmt_balance_cols(running_balance)

        rows_html.append(
            f"<tr>"
            f"<td>{period}</td>"
            f"<td align='center'>—</td>"
            f"<td align='right'>{rate_text}</td>"
            f"<td align='right'>{prev_text}</td>"
            f"<td align='right'>{curr_text}</td>"
            f"<td align='right'>{zad_dolg}</td>"
            f"<td align='right'>{zad_perep}</td>"
            f"<td align='right'>{amount_text}</td>"
            f"<td align='right'>{fmt_money(paid) if paid else '—'}</td>"
            f"<td align='right'>{itogo_dolg}</td>"
            f"<td align='right'>{itogo_perep}</td>"
            f"</tr>"
        )

    period_debt = running_balance
    total_charged = sum((c["amount"] or 0.0) for c in charges)
    total_paid = sum(pay_by_month.values())

    debt_label = "Долг" if period_debt > 0 else ("Аванс" if period_debt < 0 else "Без задолженности")
    debt_color = "#c62828" if period_debt > 0 else ("#2e7d32" if period_debt < 0 else "#444")

    last_text = "—"
    if bal.last_reading:
        ly, lm, lv = bal.last_reading
        last_text = f"{lv:g}  ({_MONTHS[lm-1]} {ly})"

    method_note = ""
    if bt == energy.BILLING_CALCULATED:
        method_note = (
            "<div style='margin:0 0 12px 0; padding:8px 12px; background:#FEF3C7; "
            "border:1px solid #FCD34D; border-radius:5px; color:#92400E; font-size:10pt;'>"
            "Расчётный метод — прибор учёта не введён в эксплуатацию. "
            "Начисление по нормативу (норматив × 24 ч × дни × тариф).</div>"
        )
    elif bt == energy.BILLING_DIRECT:
        method_note = (
            "<div style='margin:0 0 12px 0; padding:8px 12px; background:#EEF2FF; "
            "border:1px solid #C7D2FE; border-radius:5px; color:#3730A3; font-size:10pt;'>"
            "Расчёты ведутся напрямую с Пермэнергосбытом. "
            "Начисление через СНТ не производится.</div>"
        )

    th = ("background:#eef3f9; font-size:9pt; padding:4px 5px; "
          "border:1px solid #bbb; text-align:center;")
    td_style = "font-size:9pt; padding:4px 5px;"

    body = f"""
    <html><head><meta charset="utf-8"/></head>
    <body style="font-family: 'Segoe UI', Arial, sans-serif; color:#222;">
      <h2 style="margin:0 0 4px 0;">Квитанция за электроэнергию</h2>
      <div style="color:#666; font-size:11pt; margin-bottom:14px;">
        {period_subtitle}
      </div>

      {method_note}

      <table style="margin-bottom:14px; font-size:11pt;">
        <tr><td><b>Участок №:</b></td><td>&nbsp;&nbsp;{html.escape(str(plot))}</td></tr>
        <tr><td><b>Владелец(ы):</b></td><td>&nbsp;&nbsp;{html.escape(owner_text)}</td></tr>
        <tr><td><b>Последнее показание:</b></td><td>&nbsp;&nbsp;{last_text}</td></tr>
      </table>

      <table border="1" cellspacing="0" cellpadding="0"
             style="border-collapse:collapse; width:100%;">
        <thead>
          <tr>
            <th rowspan="2" style="{th}">Период</th>
            <th rowspan="2" style="{th}">№ счётчика</th>
            <th rowspan="2" style="{th}">Тариф,<br/>руб./кВтч</th>
            <th colspan="2" style="{th}">Показания инд. прибора учёта (кВтч)</th>
            <th colspan="2" style="{th}">Задолженность</th>
            <th rowspan="2" style="{th}">Начислено</th>
            <th rowspan="2" style="{th}">Оплачено</th>
            <th colspan="2" style="{th}">Итого к оплате</th>
          </tr>
          <tr>
            <th style="{th}">Предыдущие</th>
            <th style="{th}">Текущие</th>
            <th style="{th}">Долг</th>
            <th style="{th}">Переплата</th>
            <th style="{th}">Долг</th>
            <th style="{th}">Переплата</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html) if rows_html else
           f"<tr><td colspan='11' align='center' style='color:#888;{td_style}'>Нет данных по показаниям</td></tr>"}
        </tbody>
      </table>

      <div style="margin-top:18px; font-size:11pt;">
        <table style="width:100%;">
          <tr><td>Начислено за период:</td>
              <td align="right">{fmt_money(total_charged)}</td></tr>
          <tr><td>Оплачено за период:</td>
              <td align="right">{fmt_money(total_paid)}</td></tr>
          {baseline_row}
          <tr style="font-weight:700; font-size:12pt;">
            <td style="color:{debt_color};">{debt_label}:</td>
            <td align="right" style="color:{debt_color};">{fmt_money(abs(period_debt))}</td>
          </tr>
        </table>
      </div>

      <div style="margin-top:18px; color:#666; font-size:9pt;">
        Квитанция сформирована автоматически {as_of.strftime('%d.%m.%Y')}.
        Если у вас есть вопросы по начислениям — обратитесь к председателю СНТ.
      </div>
    </body></html>
    """
    return body, {
        "owner": owner_text,
        "debt": period_debt,
        "charged": total_charged,
        "paid": total_paid,
    }


def render_pdf(html_body: str, out_path: str, landscape: bool = False) -> None:
    """Рендерит HTML в PDF через QTextDocument + QPrinter."""
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(out_path)
    orientation = (QPageLayout.Orientation.Landscape if landscape
                   else QPageLayout.Orientation.Portrait)
    layout = QPageLayout(
        QPageSize(QPageSize.PageSizeId.A4),
        orientation,
        QMarginsF(15, 15, 15, 15),
        QPageLayout.Unit.Millimeter,
    )
    printer.setPageLayout(layout)

    doc = QTextDocument()
    doc.setHtml(html_body)
    # QTextDocument.print имеет два варианта имени между PyQt-релизами
    print_fn = getattr(doc, "print", None) or getattr(doc, "print_")
    print_fn(printer)


def save_plot_receipt_pdf(plot: str, df, out_path: str,
                          as_of: date | None = None,
                          since: date | None = None,
                          zero_opening: bool = False) -> dict:
    """Сохраняет квитанцию для участка в PDF. Возвращает метаданные."""
    as_of = as_of or date.today()
    body, meta = _build_html(plot, df, as_of, since=since, zero_opening=zero_opening)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".",
                exist_ok=True)
    render_pdf(body, out_path, landscape=True)
    return meta


def _build_vznosy_html(plot: str, df, as_of: date) -> tuple[str, dict]:
    from core import vznosy
    rates = vznosy.load_rates()
    adj = vznosy.load_adjustments()
    area = vznosy.plot_area_map().get(str(plot))
    owners = energy.owners_map().get(str(plot), [])
    owner_text = ", ".join(owners) if owners else "—"

    periods = vznosy.build_periods(rates)
    bal = vznosy.balance_for_plot(str(plot), area, as_of, rates, adj, df)
    py = vznosy.paid_by_period(str(plot), df, as_of, periods, adj)

    rows_html = []
    for y in bal.breakdown:
        if y.period_to:
            period_label = f"{y.period_from.strftime('%d.%m.%Y')}—{y.period_to.strftime('%d.%m.%Y')}"
        else:
            period_label = f"{y.period_from.strftime('%d.%m.%Y')}—..."

        if y.tariff is None:
            tariff_text = "—"
        elif y.tariff.get("per_sqm"):
            rate = y.tariff.get("rate_sqm", "?")
            if y.area_missing:
                tariff_text = f"{rate} ₽/м²  (площадь не указана)"
            else:
                tariff_text = f"{rate} ₽/м² · {area:g} м²"
        else:
            tariff_text = f"{y.tariff.get('amount', '?')} ₽"

        amount_text = fmt_money(y.amount) if y.amount is not None else "—"
        if y.overridden:
            amount_text += " *"
        period_key = y.period_from.isoformat()
        paid_text = fmt_money(py.get(period_key, 0.0)) if py.get(period_key) else "—"

        rows_html.append(
            f"<tr>"
            f"<td>{period_label}</td>"
            f"<td>{tariff_text}</td>"
            f"<td align='right'>{amount_text}</td>"
            f"<td align='right'>{paid_text}</td>"
            f"</tr>"
        )

    debt_label = "К оплате" if bal.debt > 0 else ("Аванс" if bal.debt < 0 else "Без задолженности")
    debt_color = "#c62828" if bal.debt > 0 else ("#2e7d32" if bal.debt < 0 else "#444")
    area_text = f"{area:g} м²" if area is not None else "—"

    body = f"""
    <html><head><meta charset="utf-8"/></head>
    <body style="font-family: 'Segoe UI', Arial, sans-serif; color:#222;">
      <h2 style="margin:0 0 4px 0;">Квитанция по членским взносам</h2>
      <div style="color:#666; font-size:11pt; margin-bottom:14px;">
        СНТ · по состоянию на {as_of.strftime('%d.%m.%Y')}
      </div>

      <table style="margin-bottom:14px; font-size:11pt;">
        <tr><td><b>Участок №:</b></td><td>&nbsp;&nbsp;{html.escape(str(plot))}</td></tr>
        <tr><td><b>Владелец(ы):</b></td><td>&nbsp;&nbsp;{html.escape(owner_text)}</td></tr>
        <tr><td><b>Площадь:</b></td><td>&nbsp;&nbsp;{area_text}</td></tr>
      </table>

      <table border="1" cellspacing="0" cellpadding="6"
             style="border-collapse:collapse; width:100%; font-size:10pt;">
        <thead style="background:#eef3f9;">
          <tr>
            <th align="left">Период</th>
            <th align="left">Тариф</th>
            <th align="right">Начислено</th>
            <th align="right">Оплачено в этом году</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html) if rows_html else
           "<tr><td colspan='4' align='center' style='color:#888'>Нет тарифов</td></tr>"}
        </tbody>
      </table>

      <div style="margin-top:18px; font-size:11pt;">
        <table style="width:100%;">
          <tr><td>Начислено всего:</td>
              <td align="right">{fmt_money(bal.charged)}</td></tr>
          <tr><td>Оплачено всего:</td>
              <td align="right">{fmt_money(bal.paid)}</td></tr>
          <tr style="font-weight:700; font-size:12pt;">
            <td style="color:{debt_color};">{debt_label}:</td>
            <td align="right" style="color:{debt_color};">{fmt_money(abs(bal.debt))}</td>
          </tr>
        </table>
      </div>

      <div style="margin-top:18px; color:#666; font-size:9pt;">
        Квитанция сформирована автоматически {as_of.strftime('%d.%m.%Y')}.
        * — начисление переопределено вручную.
      </div>
    </body></html>
    """
    return body, {
        "owner": owner_text,
        "debt": bal.debt,
        "charged": bal.charged,
        "paid": bal.paid,
    }


def save_vznosy_receipt_pdf(plot: str, df, out_path: str,
                            as_of: date | None = None) -> dict:
    """Сохраняет квитанцию по членским взносам в PDF."""
    as_of = as_of or date.today()
    body, meta = _build_vznosy_html(plot, df, as_of)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".",
                exist_ok=True)
    render_pdf(body, out_path)
    return meta


