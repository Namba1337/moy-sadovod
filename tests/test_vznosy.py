"""Юнит-тесты для core.vznosy.

Запуск: python -m unittest tests.test_vznosy
"""
import unittest
from datetime import date

import pandas as pd

from core import vznosy


RATES_FIXED = [
    {"date": "2024-01-01", "amount": "10000", "per_sqm": False, "rate_sqm": "", "note": ""},
    {"date": "2025-01-01", "amount": "12000", "per_sqm": False, "rate_sqm": "", "note": ""},
]

RATES_MIXED = [
    {"date": "2024-01-01", "amount": "10000", "per_sqm": False, "rate_sqm": "", "note": ""},
    {"date": "2025-01-01", "amount": "12000", "per_sqm": False, "rate_sqm": "", "note": ""},
    {"date": "2026-01-01", "amount": "",      "per_sqm": True,  "rate_sqm": "15.00", "note": ""},
]


def _make_df(records):
    df = pd.DataFrame(records)
    df["Дата"] = pd.to_datetime(df["Дата"])
    return df


class TariffAtTests(unittest.TestCase):
    def test_before_first_returns_none(self):
        self.assertIsNone(vznosy.tariff_at(date(2023, 12, 31), RATES_FIXED))

    def test_exact_date(self):
        t = vznosy.tariff_at(date(2024, 1, 1), RATES_FIXED)
        self.assertEqual(t["amount"], "10000")

    def test_between(self):
        t = vznosy.tariff_at(date(2024, 6, 1), RATES_FIXED)
        self.assertEqual(t["amount"], "10000")
        t = vznosy.tariff_at(date(2025, 6, 1), RATES_FIXED)
        self.assertEqual(t["amount"], "12000")

    def test_after_last(self):
        t = vznosy.tariff_at(date(2030, 1, 1), RATES_MIXED)
        self.assertTrue(t["per_sqm"])


class TariffAmountForYearTests(unittest.TestCase):
    def test_fixed_full_year(self):
        amount, t = vznosy.tariff_amount_for_year(2024, RATES_FIXED, area=None)
        self.assertEqual(amount, 10000)
        self.assertIsNotNone(t)

    def test_year_before_first_returns_none(self):
        amount, t = vznosy.tariff_amount_for_year(2023, RATES_FIXED, area=None)
        self.assertIsNone(amount)
        self.assertIsNone(t)

    def test_per_sqm_with_area(self):
        amount, t = vznosy.tariff_amount_for_year(2026, RATES_MIXED, area=600.0)
        self.assertAlmostEqual(amount, 9000.0)
        self.assertTrue(t["per_sqm"])

    def test_per_sqm_without_area(self):
        amount, t = vznosy.tariff_amount_for_year(2026, RATES_MIXED, area=None)
        self.assertIsNone(amount)
        self.assertIsNotNone(t)
        self.assertTrue(t["per_sqm"])

    def test_mid_year_first_tariff_applies_to_whole_year(self):
        rates = [{"date": "2025-04-05", "amount": "8000", "per_sqm": False, "rate_sqm": "", "note": ""}]
        amount, _ = vznosy.tariff_amount_for_year(2025, rates, area=None)
        self.assertEqual(amount, 8000)
        amount, _ = vznosy.tariff_amount_for_year(2024, rates, area=None)
        self.assertIsNone(amount)


class ChargedBreakdownTests(unittest.TestCase):
    def test_three_years_mixed(self):
        items = vznosy.charged_periods_breakdown(
            "15", area=600.0, as_of=date(2026, 6, 1),
            rates=RATES_MIXED, adjustments={},
        )
        self.assertEqual([y.period_from.year for y in items], [2024, 2025, 2026])
        self.assertEqual(items[0].amount, 10000)
        self.assertEqual(items[1].amount, 12000)
        self.assertAlmostEqual(items[2].amount, 9000.0)
        total = vznosy.charged_for_plot("15", 600.0, date(2026, 6, 1),
                                        RATES_MIXED, {})
        self.assertAlmostEqual(total, 31000.0)

    def test_area_missing_for_per_sqm(self):
        items = vznosy.charged_periods_breakdown(
            "15", area=None, as_of=date(2026, 6, 1),
            rates=RATES_MIXED, adjustments={},
        )
        self.assertIsNone(items[2].amount)
        self.assertTrue(items[2].area_missing)
        total = vznosy.charged_for_plot("15", None, date(2026, 6, 1),
                                        RATES_MIXED, {})
        self.assertEqual(total, 22000.0)

    def test_charge_override(self):
        adj = {"15": [{"date": "2025-01-01", "kind": "charge_override",
                       "year": 2025, "amount": "5000", "note": ""}]}
        items = vznosy.charged_periods_breakdown(
            "15", area=600.0, as_of=date(2025, 12, 31),
            rates=RATES_FIXED, adjustments=adj,
        )
        self.assertEqual(items[1].amount, 5000)
        self.assertTrue(items[1].overridden)

    def test_exempt_year(self):
        adj = {"15": [{"date": "2024-01-01", "kind": "exempt_year",
                       "year": 2024, "amount": "", "note": ""}]}
        items = vznosy.charged_periods_breakdown(
            "15", area=None, as_of=date(2024, 12, 31),
            rates=RATES_FIXED, adjustments=adj,
        )
        self.assertEqual(items[0].amount, 0.0)
        self.assertTrue(items[0].overridden)


class PaymentsTests(unittest.TestCase):
    def test_simple_payment(self):
        df = _make_df([
            {"Дата": "2024-06-15", "Сумма": 10000, "Списание": 0,
             "Категория": "Членские взносы", "Участок": "15", "Назначение": ""},
        ])
        self.assertEqual(vznosy.paid_for_plot("15", df, date(2024, 12, 31), {}), 10000)
        self.assertEqual(vznosy.paid_for_plot("16", df, date(2024, 12, 31), {}), 0)

    def test_mixed_halved(self):
        df = _make_df([
            {"Дата": "2024-06-15", "Сумма": 8000, "Списание": 0,
             "Категория": "Членские взносы + Электроэнергия", "Участок": "15", "Назначение": ""},
        ])
        self.assertEqual(vznosy.paid_for_plot("15", df, date(2024, 12, 31), {}), 4000)

    def test_comma_split(self):
        df = _make_df([
            {"Дата": "2024-06-15", "Сумма": 8000, "Списание": 0,
             "Категория": "Членские взносы + Электроэнергия", "Участок": "15, 16",
             "Назначение": ""},
        ])
        # 8000 / 2 (mixed) / 2 (comma) = 2000
        self.assertEqual(vznosy.paid_for_plot("15", df, date(2024, 12, 31), {}), 2000)
        self.assertEqual(vznosy.paid_for_plot("16", df, date(2024, 12, 31), {}), 2000)

    def test_manual_payment_included(self):
        df = _make_df([
            {"Дата": "2024-06-15", "Сумма": 5000, "Списание": 0,
             "Категория": "Членские взносы", "Участок": "15", "Назначение": ""},
        ])
        adj = {"15": [{"date": "2024-08-01", "kind": "payment_manual",
                       "amount": "3000", "note": "наличные"}]}
        self.assertEqual(vznosy.paid_for_plot("15", df, date(2024, 12, 31), adj), 8000)

    def test_payments_breakdown_order(self):
        df = _make_df([
            {"Дата": "2024-12-01", "Сумма": 1000, "Списание": 0,
             "Категория": "Членские взносы", "Участок": "15", "Назначение": "a"},
            {"Дата": "2024-06-01", "Сумма": 2000, "Списание": 0,
             "Категория": "Членские взносы", "Участок": "15", "Назначение": "b"},
        ])
        items = vznosy.payments_breakdown("15", df, {})
        self.assertEqual([p["amount"] for p in items], [2000, 1000])


class BalanceTests(unittest.TestCase):
    def test_full_picture(self):
        df = _make_df([
            {"Дата": "2024-06-15", "Сумма": 10000, "Списание": 0,
             "Категория": "Членские взносы", "Участок": "15", "Назначение": ""},
        ])
        bal = vznosy.balance_for_plot("15", 600.0, date(2026, 6, 1),
                                       RATES_MIXED, {}, df)
        self.assertAlmostEqual(bal.charged, 31000.0)
        self.assertEqual(bal.paid, 10000.0)
        self.assertAlmostEqual(bal.debt, 21000.0)
        self.assertEqual(bal.last_year, 2026)
        # 2024 paid, 2025+2026 unpaid → years_unpaid = 2
        self.assertEqual(bal.years_unpaid, 2)
        self.assertFalse(bal.area_missing_warning)

    def test_area_missing_warning(self):
        bal = vznosy.balance_for_plot("15", None, date(2026, 6, 1),
                                       RATES_MIXED, {}, None)
        self.assertTrue(bal.area_missing_warning)


RATES_PER_SQM = [
    {"date": "2024-01-01", "amount": "", "per_sqm": True, "rate_sqm": "10", "note": ""},
]


class BalancesByOwnerTests(unittest.TestCase):
    def test_no_history_single_owner_reconciles(self):
        df = _make_df([
            {"Дата": "2024-06-15", "Сумма": 10000, "Списание": 0,
             "Категория": "Членские взносы", "Участок": "15", "Назначение": ""},
        ])
        owners = [{"name": "Иванов", "is_owner": True}]
        bal = vznosy.balance_for_plot("15", None, date(2025, 12, 31),
                                       RATES_FIXED, {}, df)
        rows = vznosy.balances_by_owner("15", None, date(2025, 12, 31),
                                         RATES_FIXED, {}, df, owners)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "Иванов")
        self.assertTrue(rows[0].is_current)
        self.assertAlmostEqual(rows[0].charged, bal.charged)
        self.assertAlmostEqual(rows[0].paid, bal.paid)
        self.assertAlmostEqual(rows[0].debt, bal.debt)

    def test_sequential_transfer(self):
        df = _make_df([
            {"Дата": "2024-06-15", "Сумма": 10000, "Списание": 0,
             "Категория": "Членские взносы", "Участок": "15", "Назначение": ""},
            {"Дата": "2025-06-15", "Сумма": 5000, "Списание": 0,
             "Категория": "Членские взносы", "Участок": "15", "Назначение": ""},
        ])
        owners = [
            {"name": "Продавец", "is_owner": True, "until": "2025-01-01"},
            {"name": "Покупатель", "is_owner": True, "since": "2025-01-01"},
        ]
        rows = vznosy.balances_by_owner("15", None, date(2025, 12, 31),
                                         RATES_FIXED, {}, df, owners)
        by = {r.name: r for r in rows}
        # 2024 (10000) → продавцу, он же заплатил 10000 → долг 0
        self.assertAlmostEqual(by["Продавец"].charged, 10000)
        self.assertAlmostEqual(by["Продавец"].paid, 10000)
        self.assertAlmostEqual(by["Продавец"].debt, 0)
        self.assertFalse(by["Продавец"].is_current)
        # 2025 (12000) → покупателю, заплатил 5000 → долг 7000
        self.assertAlmostEqual(by["Покупатель"].charged, 12000)
        self.assertAlmostEqual(by["Покупатель"].paid, 5000)
        self.assertAlmostEqual(by["Покупатель"].debt, 7000)
        self.assertTrue(by["Покупатель"].is_current)
        # текущий собственник идёт первым
        self.assertEqual(rows[0].name, "Покупатель")

    def test_reconciles_with_plot_balance_after_transfer(self):
        df = _make_df([
            {"Дата": "2024-06-15", "Сумма": 10000, "Списание": 0,
             "Категория": "Членские взносы", "Участок": "15", "Назначение": ""},
            {"Дата": "2025-06-15", "Сумма": 5000, "Списание": 0,
             "Категория": "Членские взносы", "Участок": "15", "Назначение": ""},
        ])
        owners = [
            {"name": "Продавец", "is_owner": True, "until": "2025-01-01"},
            {"name": "Покупатель", "is_owner": True, "since": "2025-01-01"},
        ]
        bal = vznosy.balance_for_plot("15", None, date(2025, 12, 31),
                                       RATES_FIXED, {}, df)
        rows = vznosy.balances_by_owner("15", None, date(2025, 12, 31),
                                         RATES_FIXED, {}, df, owners)
        self.assertAlmostEqual(sum(r.charged for r in rows), bal.charged)
        self.assertAlmostEqual(sum(r.paid for r in rows), bal.paid)
        self.assertAlmostEqual(sum(r.debt for r in rows), bal.debt)

    def test_co_owners_per_sqm_split_by_area(self):
        df = _make_df([
            {"Дата": "2024-06-15", "Сумма": 3000, "Списание": 0,
             "Категория": "Членские взносы", "Участок": "15", "Назначение": ""},
        ])
        owners = [
            {"name": "A", "is_owner": True, "area": 400},
            {"name": "B", "is_owner": True, "area": 200},
        ]
        rows = vznosy.balances_by_owner("15", 600.0, date(2024, 12, 31),
                                         RATES_PER_SQM, {}, df, owners)
        by = {r.name: r for r in rows}
        # charge 2024 = 10 * 600 = 6000, делится по площади 400:200
        self.assertAlmostEqual(by["A"].charged, 4000)
        self.assertAlmostEqual(by["B"].charged, 2000)
        # платёж 3000 в 2024 (период per_sqm) делится по площади → 2000/1000
        self.assertAlmostEqual(by["A"].paid, 2000)
        self.assertAlmostEqual(by["B"].paid, 1000)

    def test_co_owners_fixed_equal_split(self):
        owners = [
            {"name": "A", "is_owner": True},
            {"name": "B", "is_owner": True},
        ]
        rows = vznosy.balances_by_owner("15", None, date(2024, 12, 31),
                                         RATES_FIXED, {}, None, owners)
        by = {r.name: r for r in rows}
        self.assertAlmostEqual(by["A"].charged, 5000)
        self.assertAlmostEqual(by["B"].charged, 5000)

    def test_unknown_owner_bucket_on_gap(self):
        # Начисления с 2024, а собственник записан только с 2025 → провал
        owners = [{"name": "Поздний", "is_owner": True, "since": "2025-01-01"}]
        rows = vznosy.balances_by_owner("15", None, date(2025, 12, 31),
                                         RATES_FIXED, {}, None, owners)
        by = {r.name: r for r in rows}
        self.assertIn("Собственник не определён", by)
        self.assertAlmostEqual(by["Собственник не определён"].charged, 10000)
        self.assertAlmostEqual(by["Поздний"].charged, 12000)

    def test_contacts_excluded(self):
        owners = [
            {"name": "Собственник", "is_owner": True},
            {"name": "Контакт", "is_owner": False},
        ]
        rows = vznosy.balances_by_owner("15", None, date(2024, 12, 31),
                                         RATES_FIXED, {}, None, owners)
        self.assertEqual([r.name for r in rows], ["Собственник"])


class OwnershipFormTests(unittest.TestCase):
    """Деление начисления по виду права (как в ЕГРН)."""

    def test_individual_all_to_one(self):
        owners = [{"name": "Один", "is_owner": True}]
        rows = vznosy.balances_by_owner(
            "15", None, date(2024, 12, 31), RATES_FIXED, {}, None, owners,
            ownership_form="individual")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].charged, 10000)

    def test_shared_fixed_by_share(self):
        owners = [
            {"name": "A", "is_owner": True, "share": "1/4"},
            {"name": "B", "is_owner": True, "share": "3/4"},
        ]
        rows = vznosy.balances_by_owner(
            "15", None, date(2024, 12, 31), RATES_FIXED, {}, None, owners,
            ownership_form="shared")
        by = {r.name: r for r in rows}
        self.assertAlmostEqual(by["A"].charged, 2500)   # 1/4 * 10000
        self.assertAlmostEqual(by["B"].charged, 7500)   # 3/4 * 10000

    def test_shared_per_sqm_uses_share_not_area(self):
        # Площадь намеренно НЕ пропорциональна доле — деление должно идти по ДОЛЕ.
        owners = [
            {"name": "A", "is_owner": True, "share": "1/4", "area": 400},
            {"name": "B", "is_owner": True, "share": "3/4", "area": 200},
        ]
        rows = vznosy.balances_by_owner(
            "15", 600.0, date(2024, 12, 31), RATES_PER_SQM, {}, None, owners,
            ownership_form="shared")
        by = {r.name: r for r in rows}
        # charge 2024 = 10 * 600 = 6000 → по доле 1/4 : 3/4
        self.assertAlmostEqual(by["A"].charged, 1500)
        self.assertAlmostEqual(by["B"].charged, 4500)

    def test_joint_equal_regardless_of_area(self):
        owners = [
            {"name": "A", "is_owner": True, "area": 400},
            {"name": "B", "is_owner": True, "area": 200},
        ]
        rows = vznosy.balances_by_owner(
            "15", 600.0, date(2024, 12, 31), RATES_PER_SQM, {}, None, owners,
            ownership_form="joint")
        by = {r.name: r for r in rows}
        # совместная → поровну, несмотря на разные площади
        self.assertAlmostEqual(by["A"].charged, 3000)
        self.assertAlmostEqual(by["B"].charged, 3000)

    def test_legacy_form_none_unchanged_per_sqm_by_area(self):
        # Без вида права (старые данные) per_sqm по-прежнему делится по площади.
        owners = [
            {"name": "A", "is_owner": True, "area": 400},
            {"name": "B", "is_owner": True, "area": 200},
        ]
        rows = vznosy.balances_by_owner(
            "15", 600.0, date(2024, 12, 31), RATES_PER_SQM, {}, None, owners)
        by = {r.name: r for r in rows}
        self.assertAlmostEqual(by["A"].charged, 4000)
        self.assertAlmostEqual(by["B"].charged, 2000)


if __name__ == "__main__":
    unittest.main()
