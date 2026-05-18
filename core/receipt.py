"""Генерация PDF-квитанций по электроэнергии для участка."""
from __future__ import annotations

import html
import os
from datetime import date

from PyQt6.QtCore import QMarginsF
from PyQt6.QtGui import QPageLayout, QPageSize, QTextDocument
from PyQt6.QtPrintSupport import QPrinter

from core import energy


_MONTHS = ["янв", "фев", "мар", "апр", "май", "июн",
           "июл", "авг", "сен", "окт", "ноя", "дек"]


def _fmt_money(v: float | None) -> str:
    if v is None:
        return "—"
    if abs(v) < 0.005:
        return "0,00 ₽"
    sign = "−" if v < 0 else ""
    return f"{sign}{abs(v):,.2f} ₽".replace(",", " ").replace(".", ",")


def _fmt_kwh(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.0f}"


def _build_html(plot: str, df, as_of: date) -> tuple[str, dict]:
    meters = energy.load_meters()
    rates = energy.load_rates()
    repls = energy.load_replacements()
    baseline = energy.load_baseline()
    owners = energy.owners_map().get(str(plot), [])
    owner_text = ", ".join(owners) if owners else "—"

    bal = energy.balance(plot, as_of, meters, rates, repls, baseline, df)
    charges = energy.all_charges(plot, meters, rates, repls, up_to=as_of)

    pay_by_month: dict[tuple[int, int], float] = {}
    for p in energy.payments_breakdown(plot, df):
        d = p["date"]
        if d is None or d > as_of:
            continue
        key = (d.year, d.month)
        pay_by_month[key] = pay_by_month.get(key, 0.0) + p["amount"]

    rows_html = []
    for c in charges:
        period = f"{_MONTHS[c['month']-1]} {c['year']}"
        value = f"{c['value']:g}"
        if c["prev_value"] is not None:
            value += f" <span style='color:#888'>(пред. {c['prev_value']:g})</span>"
        rate_text = f"{c['rate']:.2f}" if c["rate"] is not None else "—"
        kwh_text = _fmt_kwh(c["kwh"])
        amount_text = _fmt_money(c["amount"])
        paid = pay_by_month.get((c["year"], c["month"]), 0.0)
        rows_html.append(
            f"<tr>"
            f"<td>{period}</td>"
            f"<td align='right'>{value}</td>"
            f"<td align='right'>{kwh_text}</td>"
            f"<td align='right'>{rate_text}</td>"
            f"<td align='right'>{amount_text}</td>"
            f"<td align='right'>{_fmt_money(paid) if paid else '—'}</td>"
            f"</tr>"
        )

    total_charged = sum((c["amount"] or 0.0) for c in charges)

    debt_label = "Долг" if bal.debt > 0 else ("Аванс" if bal.debt < 0 else "Без задолженности")
    debt_color = "#c62828" if bal.debt > 0 else ("#2e7d32" if bal.debt < 0 else "#444")

    last_text = "—"
    if bal.last_reading:
        ly, lm, lv = bal.last_reading
        last_text = f"{lv:g}  ({_MONTHS[lm-1]} {ly})"

    body = f"""
    <html><head><meta charset="utf-8"/></head>
    <body style="font-family: 'Segoe UI', Arial, sans-serif; color:#222;">
      <h2 style="margin:0 0 4px 0;">Квитанция за электроэнергию</h2>
      <div style="color:#666; font-size:11pt; margin-bottom:14px;">
        СНТ · по состоянию на {as_of.strftime('%d.%m.%Y')}
      </div>

      <table style="margin-bottom:14px; font-size:11pt;">
        <tr><td><b>Участок №:</b></td><td>&nbsp;&nbsp;{html.escape(str(plot))}</td></tr>
        <tr><td><b>Владелец(ы):</b></td><td>&nbsp;&nbsp;{html.escape(owner_text)}</td></tr>
        <tr><td><b>Последнее показание:</b></td><td>&nbsp;&nbsp;{last_text}</td></tr>
      </table>

      <table border="1" cellspacing="0" cellpadding="6"
             style="border-collapse:collapse; width:100%; font-size:10pt;">
        <thead style="background:#eef3f9;">
          <tr>
            <th align="left">Период</th>
            <th align="right">Показание</th>
            <th align="right">Расход, кВт·ч</th>
            <th align="right">Тариф, ₽/кВт·ч</th>
            <th align="right">Начислено</th>
            <th align="right">Оплачено</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html) if rows_html else
           "<tr><td colspan='6' align='center' style='color:#888'>Нет данных по показаниям</td></tr>"}
        </tbody>
      </table>

      <div style="margin-top:18px; font-size:11pt;">
        <table style="width:100%;">
          <tr><td>Начислено за период:</td>
              <td align="right">{_fmt_money(total_charged)}</td></tr>
          <tr><td>Оплачено за период:</td>
              <td align="right">{_fmt_money(bal.paid)}</td></tr>
          <tr><td>Начальное сальдо:</td>
              <td align="right">{_fmt_money(bal.baseline)}</td></tr>
          <tr style="font-weight:700; font-size:12pt;">
            <td style="color:{debt_color};">{debt_label}:</td>
            <td align="right" style="color:{debt_color};">{_fmt_money(abs(bal.debt))}</td>
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
        "debt": bal.debt,
        "charged": total_charged,
        "paid": bal.paid,
    }


def render_pdf(html_body: str, out_path: str) -> None:
    """Рендерит HTML в PDF через QTextDocument + QPrinter."""
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(out_path)
    layout = QPageLayout(
        QPageSize(QPageSize.PageSizeId.A4),
        QPageLayout.Orientation.Portrait,
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
                          as_of: date | None = None) -> dict:
    """Сохраняет квитанцию для участка в PDF. Возвращает метаданные."""
    as_of = as_of or date.today()
    body, meta = _build_html(plot, df, as_of)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".",
                exist_ok=True)
    render_pdf(body, out_path)
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

        amount_text = _fmt_money(y.amount) if y.amount is not None else "—"
        if y.overridden:
            amount_text += " *"
        period_key = y.period_from.isoformat()
        paid_text = _fmt_money(py.get(period_key, 0.0)) if py.get(period_key) else "—"

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
              <td align="right">{_fmt_money(bal.charged)}</td></tr>
          <tr><td>Оплачено всего:</td>
              <td align="right">{_fmt_money(bal.paid)}</td></tr>
          <tr style="font-weight:700; font-size:12pt;">
            <td style="color:{debt_color};">{debt_label}:</td>
            <td align="right" style="color:{debt_color};">{_fmt_money(abs(bal.debt))}</td>
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


def receipt_text(plot: str, df, as_of: date | None = None) -> str:
    """Текстовая квитанция для копирования в мессенджер."""
    as_of = as_of or date.today()
    meters = energy.load_meters()
    rates = energy.load_rates()
    repls = energy.load_replacements()
    baseline = energy.load_baseline()
    owners = energy.owners_map().get(str(plot), [])
    owner_text = ", ".join(owners) if owners else "—"
    bal = energy.balance(plot, as_of, meters, rates, repls, baseline, df)

    lines = [
        f"Квитанция за электроэнергию",
        f"СНТ · на {as_of.strftime('%d.%m.%Y')}",
        f"Участок: {plot}",
        f"Владелец: {owner_text}",
    ]
    if bal.last_reading:
        ly, lm, lv = bal.last_reading
        lines.append(f"Посл. показание: {lv:g} ({_MONTHS[lm-1]} {ly})")
    lines += [
        f"Начислено всего: {_fmt_money(bal.charged)}",
        f"Оплачено всего: {_fmt_money(bal.paid)}",
    ]
    if bal.baseline:
        lines.append(f"Начальное сальдо: {_fmt_money(bal.baseline)}")
    if bal.debt > 0:
        lines.append(f"К ОПЛАТЕ: {_fmt_money(bal.debt)}")
    elif bal.debt < 0:
        lines.append(f"Аванс: {_fmt_money(abs(bal.debt))}")
    else:
        lines.append("Задолженности нет")
    return "\n".join(lines)
