"""Юнит-тесты для core.energy.

Запуск: python -m unittest tests.test_energy
"""
import unittest
from datetime import date

import pandas as pd

from core import energy


RATES = [
    {"date": "2024-01-01", "rate": "5.00", "note": ""},
    {"date": "2025-05-01", "rate": "5.85", "note": ""},
    {"date": "2026-05-01", "rate": "6.28", "note": ""},
]


def _make_df(records):
    df = pd.DataFrame(records)
    df["Дата"] = pd.to_datetime(df["Дата"])
    return df


class RateAtTests(unittest.TestCase):
    def test_before_first_returns_none(self):
        self.assertIsNone(energy.rate_at(date(2023, 12, 31), RATES))

    def test_exact_date_returns_that_rate(self):
        self.assertEqual(energy.rate_at(date(2025, 5, 1), RATES), 5.85)

    def test_after_last_uses_last_active(self):
        self.assertEqual(energy.rate_at(date(2030, 1, 1), RATES), 6.28)

    def test_between_uses_previous(self):
        self.assertEqual(energy.rate_at(date(2025, 12, 31), RATES), 5.85)
        self.assertEqual(energy.rate_at(date(2026, 4, 30), RATES), 5.85)
        self.assertEqual(energy.rate_at(date(2026, 5, 2), RATES), 6.28)

    def test_skips_malformed_entries(self):
        rates = [{"date": "wrong", "rate": "9.99"}] + RATES
        self.assertEqual(energy.rate_at(date(2025, 6, 1), rates), 5.85)


class ConsumptionTests(unittest.TestCase):
    def test_no_previous_returns_none(self):
        meters = {"20:2026:1": "100"}
        self.assertIsNone(energy.consumption_kwh("20", 2026, 1, meters, {}))

    def test_simple_difference(self):
        meters = {"20:2026:1": "100", "20:2026:2": "180"}
        self.assertEqual(energy.consumption_kwh("20", 2026, 2, meters, {}), 80)

    def test_with_meter_replacement_in_between(self):
        meters = {"20:2026:1": "12000", "20:2026:6": "150"}
        replacements = {
            "20": [{
                "date": "2026-03-15",
                "old_final": "12450",
                "new_initial": "0",
                "note": "поверка",
            }],
        }
        # (12450 - 12000) + (150 - 0) = 600
        self.assertEqual(
            energy.consumption_kwh("20", 2026, 6, meters, replacements), 600
        )

    def test_replacement_outside_period_ignored(self):
        meters = {"20:2026:1": "100", "20:2026:2": "180"}
        replacements = {
            "20": [{"date": "2025-12-15", "old_final": "0", "new_initial": "0"}],
        }
        self.assertEqual(
            energy.consumption_kwh("20", 2026, 2, meters, replacements), 80
        )

    def test_skips_missing_month(self):
        meters = {"20:2026:3": "100", "20:2026:5": "260"}
        self.assertEqual(
            energy.consumption_kwh("20", 2026, 5, meters, {}), 160
        )


class ChargeTests(unittest.TestCase):
    def test_uses_tariff_at_reading_date(self):
        meters = {"20:2026:3": "100", "20:2026:4": "200"}
        self.assertEqual(
            energy.charge("20", 2026, 4, meters, RATES, {}), 100 * 5.85
        )

    def test_for_may_uses_new_tariff(self):
        meters = {"20:2026:4": "200", "20:2026:5": "300"}
        self.assertEqual(
            energy.charge("20", 2026, 5, meters, RATES, {}), 100 * 6.28
        )

    def test_returns_none_when_no_prev_reading(self):
        meters = {"20:2026:5": "300"}
        self.assertIsNone(energy.charge("20", 2026, 5, meters, RATES, {}))

    def test_all_charges_first_entry_has_no_amount(self):
        meters = {"20:2026:3": "100", "20:2026:4": "200"}
        charges = energy.all_charges("20", meters, RATES, {})
        self.assertEqual(len(charges), 2)
        self.assertIsNone(charges[0]["amount"])
        self.assertIsNone(charges[0]["kwh"])
        self.assertEqual(charges[1]["amount"], 100 * 5.85)


class BalanceTests(unittest.TestCase):
    def test_with_baseline_and_payments(self):
        meters = {"5:2026:3": "0", "5:2026:4": "100"}
        df = _make_df([{
            "Дата": "2026-04-15", "Сумма": 400,
            "Категория": "Электроэнергия (от садоводов)", "Участок": "5",
        }])
        baseline = {"start_date": "2026-01-01", "balances": {"5": "200"}}
        bal = energy.balance(
            "5", date(2026, 5, 31), meters, RATES, {}, baseline, df
        )
        self.assertEqual(bal.charged, 100 * 5.85)
        self.assertEqual(bal.paid, 400)
        self.assertEqual(bal.baseline, 200)
        self.assertAlmostEqual(bal.debt, 200 + 585 - 400)

    def test_mixed_payment_is_split_in_half(self):
        meters = {"5:2026:3": "0", "5:2026:4": "100"}
        df = _make_df([{
            "Дата": "2026-04-15", "Сумма": 800,
            "Категория": "Членские взносы + Электроэнергия", "Участок": "5",
        }])
        bal = energy.balance(
            "5", date(2026, 5, 31), meters, RATES, {}, {"balances": {}}, df
        )
        self.assertEqual(bal.paid, 400)

    def test_multi_plot_payment_split_proportionally(self):
        meters = {"5:2026:3": "0", "5:2026:4": "100"}
        df = _make_df([{
            "Дата": "2026-04-15", "Сумма": 600,
            "Категория": "Электроэнергия (от садоводов)", "Участок": "5, 7",
        }])
        bal = energy.balance(
            "5", date(2026, 5, 31), meters, RATES, {}, {"balances": {}}, df
        )
        self.assertEqual(bal.paid, 300)


class AnomalyTests(unittest.TestCase):
    def test_drop_detected_when_reading_decreases(self):
        meters = {"5:2026:3": "200", "5:2026:4": "150"}
        out = energy.anomalies("5", meters, {})
        self.assertIn("drop", [a.type for a in out])

    def test_gap_detected_between_filled_months(self):
        meters = {"5:2026:1": "100", "5:2026:4": "400"}
        gaps = [(a.year, a.month) for a in energy.anomalies("5", meters, {})
                if a.type == "gap"]
        self.assertIn((2026, 2), gaps)
        self.assertIn((2026, 3), gaps)

    def test_spike_detected_only_with_enough_history(self):
        meters = {f"5:2026:{m}": str(100 * m) for m in range(1, 6)}
        meters["5:2026:6"] = "2000"
        out = energy.anomalies("5", meters, {})
        self.assertTrue(any(a.type == "spike" for a in out))

    def test_drop_no_false_positive_after_replacement(self):
        meters = {"5:2026:1": "12000", "5:2026:6": "150"}
        repls = {"5": [
            {"date": "2026-03-15", "old_final": "12450", "new_initial": "0"}
        ]}
        out = energy.anomalies("5", meters, repls)
        self.assertFalse([a for a in out if a.type == "drop"])


class ReconcileTests(unittest.TestCase):
    def test_basic(self):
        meters = {"5:2026:3": "0", "5:2026:4": "100"}
        df = _make_df([
            {"Дата": "2026-04-15", "Сумма": 500,
             "Категория": "Электроэнергия (от садоводов)", "Участок": "5"},
            {"Дата": "2026-04-20", "Сумма": -600,
             "Категория": "Оплата электроэнергии (поставщик)", "Участок": ""},
        ])
        rec = energy.reconcile(
            date(2026, 1, 1), date(2026, 12, 31),
            plots=["5"], meters=meters, rates=RATES, replacements={},
            common_meter={}, df=df,
        )
        self.assertEqual(rec.charged_total, 100 * 5.85)
        self.assertEqual(rec.collected_total, 500)
        self.assertEqual(rec.paid_to_supplier, 600)
        self.assertIsNone(rec.common_kwh)


class BalancesByOwnerTests(unittest.TestCase):
    def test_no_history_single_owner_reconciles(self):
        meters = {"5:2026:3": "0", "5:2026:4": "100"}
        df = _make_df([{
            "Дата": "2026-04-15", "Сумма": 400,
            "Категория": "Электроэнергия (от садоводов)", "Участок": "5",
        }])
        baseline = {"start_date": "2026-01-01", "balances": {"5": "200"}}
        owners = [{"name": "Иванов", "is_owner": True}]
        bal = energy.balance("5", date(2026, 5, 31), meters, RATES, {}, baseline, df)
        rows = energy.balances_by_owner("5", date(2026, 5, 31), meters, RATES, {},
                                        baseline, df, owners)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].charged, bal.charged)
        self.assertAlmostEqual(rows[0].paid, bal.paid)
        self.assertAlmostEqual(rows[0].baseline, bal.baseline)
        self.assertAlmostEqual(rows[0].debt, bal.debt)
        self.assertTrue(rows[0].is_current)

    def test_sequential_transfer(self):
        meters = {"5:2025:11": "0", "5:2025:12": "100",
                  "5:2026:1": "100", "5:2026:2": "260"}
        df = _make_df([
            {"Дата": "2025-06-15", "Сумма": 400,
             "Категория": "Электроэнергия (от садоводов)", "Участок": "5"},
            {"Дата": "2026-06-15", "Сумма": 300,
             "Категория": "Электроэнергия (от садоводов)", "Участок": "5"},
        ])
        baseline = {"start_date": "2025-01-01", "balances": {"5": "200"}}
        owners = [
            {"name": "Продавец", "is_owner": True, "until": "2026-01-01"},
            {"name": "Покупатель", "is_owner": True, "since": "2026-01-01"},
        ]
        as_of = date(2026, 12, 31)
        bal = energy.balance("5", as_of, meters, RATES, {}, baseline, df)
        rows = energy.balances_by_owner("5", as_of, meters, RATES, {},
                                        baseline, df, owners)
        by = {r.name: r for r in rows}
        # Продавец: baseline 200 + начисление дек.2025 (585) − оплата 400 = 385
        self.assertAlmostEqual(by["Продавец"].baseline, 200)
        self.assertAlmostEqual(by["Продавец"].charged, 585)
        self.assertAlmostEqual(by["Продавец"].paid, 400)
        self.assertAlmostEqual(by["Продавец"].debt, 385)
        self.assertFalse(by["Продавец"].is_current)
        # Покупатель: начисление фев.2026 (936) − оплата 300
        self.assertAlmostEqual(by["Покупатель"].charged, 936)
        self.assertAlmostEqual(by["Покупатель"].paid, 300)
        self.assertAlmostEqual(by["Покупатель"].debt, 636)
        self.assertTrue(by["Покупатель"].is_current)
        # реконсиляция с участковым балансом
        self.assertAlmostEqual(sum(r.debt for r in rows), bal.debt)
        self.assertAlmostEqual(sum(r.charged for r in rows), bal.charged)
        self.assertAlmostEqual(sum(r.paid for r in rows), bal.paid)

    def test_co_owners_shared_by_share(self):
        meters = {"5:2026:3": "0", "5:2026:4": "100"}   # Apr 2026: 100 кВт·ч × 5.85 = 585
        owners = [
            {"name": "A", "is_owner": True, "share": "1/4"},
            {"name": "B", "is_owner": True, "share": "3/4"},
        ]
        rows = energy.balances_by_owner("5", date(2026, 5, 31), meters, RATES, {},
                                        {"balances": {}}, None, owners,
                                        ownership_form="shared")
        by = {r.name: r for r in rows}
        self.assertAlmostEqual(by["A"].charged, 585 * 0.25)
        self.assertAlmostEqual(by["B"].charged, 585 * 0.75)

    def test_co_owners_joint_equal(self):
        meters = {"5:2026:3": "0", "5:2026:4": "100"}
        owners = [
            {"name": "A", "is_owner": True},
            {"name": "B", "is_owner": True},
        ]
        rows = energy.balances_by_owner("5", date(2026, 5, 31), meters, RATES, {},
                                        {"balances": {}}, None, owners,
                                        ownership_form="joint")
        by = {r.name: r for r in rows}
        self.assertAlmostEqual(by["A"].charged, 292.5)
        self.assertAlmostEqual(by["B"].charged, 292.5)


if __name__ == "__main__":
    unittest.main()
