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
        items = vznosy.charged_years_breakdown(
            "15", area=600.0, as_of=date(2026, 6, 1),
            rates=RATES_MIXED, adjustments={},
        )
        self.assertEqual([y.year for y in items], [2024, 2025, 2026])
        self.assertEqual(items[0].amount, 10000)
        self.assertEqual(items[1].amount, 12000)
        self.assertAlmostEqual(items[2].amount, 9000.0)
        total = vznosy.charged_for_plot("15", 600.0, date(2026, 6, 1),
                                        RATES_MIXED, {})
        self.assertAlmostEqual(total, 31000.0)

    def test_area_missing_for_per_sqm(self):
        items = vznosy.charged_years_breakdown(
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
        items = vznosy.charged_years_breakdown(
            "15", area=600.0, as_of=date(2025, 12, 31),
            rates=RATES_FIXED, adjustments=adj,
        )
        self.assertEqual(items[1].amount, 5000)
        self.assertTrue(items[1].overridden)

    def test_exempt_year(self):
        adj = {"15": [{"date": "2024-01-01", "kind": "exempt_year",
                       "year": 2024, "amount": "", "note": ""}]}
        items = vznosy.charged_years_breakdown(
            "15", area=None, as_of=date(2024, 12, 31),
            rates=RATES_FIXED, adjustments=adj,
        )
        self.assertEqual(items[0].amount, 0.0)
        self.assertTrue(items[0].overridden)


class PaymentsTests(unittest.TestCase):
    def test_simple_payment(self):
        df = _make_df([
            {"Дата": "2024-06-15", "Поступление": 10000, "Списание": 0,
             "Категория": "Членские взносы", "Участок": "15", "Назначение": ""},
        ])
        self.assertEqual(vznosy.paid_for_plot("15", df, date(2024, 12, 31), {}), 10000)
        self.assertEqual(vznosy.paid_for_plot("16", df, date(2024, 12, 31), {}), 0)

    def test_mixed_halved(self):
        df = _make_df([
            {"Дата": "2024-06-15", "Поступление": 8000, "Списание": 0,
             "Категория": "Членские взносы + Электроэнергия", "Участок": "15", "Назначение": ""},
        ])
        self.assertEqual(vznosy.paid_for_plot("15", df, date(2024, 12, 31), {}), 4000)

    def test_comma_split(self):
        df = _make_df([
            {"Дата": "2024-06-15", "Поступление": 8000, "Списание": 0,
             "Категория": "Членские взносы + Электроэнергия", "Участок": "15, 16",
             "Назначение": ""},
        ])
        # 8000 / 2 (mixed) / 2 (comma) = 2000
        self.assertEqual(vznosy.paid_for_plot("15", df, date(2024, 12, 31), {}), 2000)
        self.assertEqual(vznosy.paid_for_plot("16", df, date(2024, 12, 31), {}), 2000)

    def test_manual_payment_included(self):
        df = _make_df([
            {"Дата": "2024-06-15", "Поступление": 5000, "Списание": 0,
             "Категория": "Членские взносы", "Участок": "15", "Назначение": ""},
        ])
        adj = {"15": [{"date": "2024-08-01", "kind": "payment_manual",
                       "amount": "3000", "note": "наличные"}]}
        self.assertEqual(vznosy.paid_for_plot("15", df, date(2024, 12, 31), adj), 8000)

    def test_payments_breakdown_order(self):
        df = _make_df([
            {"Дата": "2024-12-01", "Поступление": 1000, "Списание": 0,
             "Категория": "Членские взносы", "Участок": "15", "Назначение": "a"},
            {"Дата": "2024-06-01", "Поступление": 2000, "Списание": 0,
             "Категория": "Членские взносы", "Участок": "15", "Назначение": "b"},
        ])
        items = vznosy.payments_breakdown("15", df, {})
        self.assertEqual([p["amount"] for p in items], [2000, 1000])


class BalanceTests(unittest.TestCase):
    def test_full_picture(self):
        df = _make_df([
            {"Дата": "2024-06-15", "Поступление": 10000, "Списание": 0,
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


if __name__ == "__main__":
    unittest.main()
