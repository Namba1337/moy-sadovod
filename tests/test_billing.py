"""Юнит-тесты для типов расчёта за электроэнергию (billing_type).

Запуск: python -m unittest tests.test_billing
"""
import unittest
from datetime import date

import pandas as pd

from core import energy as e

RATES = [{"date": "2026-01-01", "rate": "6.00"}]


def _mkdf(recs):
    df = pd.DataFrame(recs)
    df["Дата"] = pd.to_datetime(df["Дата"])
    return df


class CalculatedTests(unittest.TestCase):
    def test_full_month(self):
        ch = e.calculated_charges("9", 1.5, date(2026, 4, 1), RATES, up_to=date(2026, 4, 30))
        self.assertEqual(len(ch), 1)
        self.assertEqual(ch[0]["days"], 30)
        self.assertEqual(ch[0]["kwh"], 1.5 * 24 * 30)
        self.assertEqual(ch[0]["amount"], 1.5 * 24 * 30 * 6.0)

    def test_partial_start_and_end(self):
        ch = e.calculated_charges("9", 1.0, date(2026, 3, 10), RATES, up_to=date(2026, 4, 15))
        self.assertEqual([c["month"] for c in ch], [3, 4])
        self.assertEqual(ch[0]["days"], 22)   # 10..31 марта
        self.assertEqual(ch[1]["days"], 15)   # 1..15 апреля

    def test_no_norm_or_start_empty(self):
        self.assertEqual(e.calculated_charges("9", None, date(2026, 4, 1), RATES), [])
        self.assertEqual(e.calculated_charges("9", 1.0, None, RATES), [])


class DispatchTests(unittest.TestCase):
    def test_meter_default(self):
        plots = [{"num": "9", "billing_type": "meter"}]
        meters = {"9:2026:1": "100", "9:2026:2": "180"}
        ch = e.charges_for_plot("9", meters, RATES, {}, up_to=date(2026, 3, 1), plots=plots)
        self.assertEqual(len(ch), 2)
        self.assertEqual(ch[1]["amount"], 80 * 6.0)

    def test_direct_returns_empty(self):
        plots = [{"num": "9", "billing_type": "direct"}]
        meters = {"9:2026:1": "100", "9:2026:2": "180"}
        self.assertEqual(
            e.charges_for_plot("9", meters, RATES, {}, up_to=date(2026, 3, 1), plots=plots), []
        )

    def test_calculated_ignores_readings(self):
        plots = [{"num": "9", "billing_type": "calculated",
                  "norm_kw": 2, "norm_start_date": "2026-04-01"}]
        meters = {"9:2026:1": "100", "9:2026:2": "180"}  # показания игнорируются
        ch = e.charges_for_plot("9", meters, RATES, {}, up_to=date(2026, 4, 30), plots=plots)
        self.assertTrue(all(c.get("calculated") for c in ch))
        self.assertEqual(ch[0]["kwh"], 2 * 24 * 30)


class SegmentTests(unittest.TestCase):
    def test_meter_then_calculated_midstream(self):
        plots = [{"num": "9", "billing_type": "calculated",
                  "norm_kw": 1, "norm_start_date": "2026-04-01",
                  "billing_history": [{"date": "2026-04-01", "from": "meter", "to": "calculated"}]}]
        meters = {"9:2026:2": "100", "9:2026:3": "250"}
        ch = e.charges_for_plot("9", meters, RATES, {}, up_to=date(2026, 4, 30), plots=plots)
        meter_rows = [c for c in ch if not c.get("calculated")]
        calc_rows = [c for c in ch if c.get("calculated")]
        self.assertTrue(any(c["month"] == 3 and c["amount"] == 150 * 6.0 for c in meter_rows))
        self.assertTrue(any(c["month"] == 4 for c in calc_rows))
        self.assertFalse(any(c["month"] < 4 for c in calc_rows))

    def test_no_history_single_segment(self):
        plots = [{"num": "9", "billing_type": "meter"}]
        self.assertEqual(e.billing_segments("9", plots), [(None, None, "meter")])


class BalanceReconcileTests(unittest.TestCase):
    def test_balance_calculated(self):
        plots = [{"num": "9", "billing_type": "calculated",
                  "norm_kw": 1, "norm_start_date": "2026-04-01"}]
        bal = e.balance("9", date(2026, 4, 30), {}, RATES, {}, {"balances": {}}, None, plots=plots)
        self.assertEqual(bal.charged, 1 * 24 * 30 * 6.0)
        self.assertIsNone(bal.last_reading)

    def test_reconcile_excludes_direct(self):
        meters = {"5:2026:3": "0", "5:2026:4": "100", "7:2026:3": "0", "7:2026:4": "100"}
        plots = [{"num": "5", "billing_type": "meter"}, {"num": "7", "billing_type": "direct"}]
        rec = e.reconcile(date(2026, 1, 1), date(2026, 12, 31), ["5", "7"],
                          meters, RATES, {}, {}, None, plot_records=plots)
        self.assertEqual(rec.charged_total, 100 * 6.0)
        self.assertEqual(rec.private_kwh, 100)

    def test_waiting_for_readings(self):
        plots = [{"num": "9", "billing_type": "meter"}]
        self.assertTrue(e.waiting_for_readings("9", {}, plots))
        self.assertFalse(e.waiting_for_readings("9", {"9:2026:1": "10"}, plots))
        plots2 = [{"num": "9", "billing_type": "direct"}]
        self.assertFalse(e.waiting_for_readings("9", {}, plots2))


if __name__ == "__main__":
    unittest.main()
