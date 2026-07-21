"""Юнит-тесты для core.dashboard.

Запуск: python -m unittest tests.test_dashboard
"""
import json
import unittest
from datetime import date

from core import dashboard
from tests._util import make_df as _make_df


def _expense_split_df():
    """Одна операция расхода на 1000 ₽, разделённая на 2 подкатегории
    (600 + 400, со знаком родителя — как хранит ui/detail_widget.py)."""
    breakdown = json.dumps([
        {"Сумма": -600.0, "Категория": "Материалы и работы", "Участок": ""},
        {"Сумма": -400.0, "Категория": "Налоги и штрафы", "Участок": ""},
    ], ensure_ascii=False)
    return _make_df([
        {"Дата": "2024-06-15", "Сумма": -1000.0, "Категория": "Материалы и работы",
         "Участок": "", "_breakdown": breakdown},
    ])


class CategoryBreakdownSplitTests(unittest.TestCase):
    """Разбивка (сплит) операции расхода должна учитываться по подкатегориям,
    а не откатываться на верхнеуровневую категорию (см. core/energy.py::parse_breakdown)."""

    def test_expense_split_both_subcategories_counted(self):
        df = _expense_split_df()
        slices = dashboard.category_breakdown(
            df, date(2024, 6, 1), date(2024, 6, 30), "expense")
        totals = {s.name: s.amount for s in slices}
        self.assertAlmostEqual(totals.get("Материалы и работы", 0.0), 600.0)
        self.assertAlmostEqual(totals.get("Налоги и штрафы", 0.0), 400.0)
        self.assertAlmostEqual(sum(totals.values()), 1000.0)

    def test_expense_split_in_monthly_category_breakdown(self):
        df = _expense_split_df()
        period = dashboard.Period(date(2024, 1, 1), date(2024, 12, 31), "2024")
        months = dashboard.monthly_category_breakdown(df, period, "expense")
        june = next(m for m in months if m.month == 6)
        cats = dict(june.categories)
        self.assertAlmostEqual(cats.get("Материалы и работы", 0.0), 600.0)
        self.assertAlmostEqual(cats.get("Налоги и штрафы", 0.0), 400.0)


class MonthsInPeriodTests(unittest.TestCase):
    """Периоды ЧВ не всегда ровно 12 месяцев (date_to задаётся вручную) —
    графики не должны обрезать или растягивать данные мимо реальной длины периода."""

    def test_shorter_period_all_months_present(self):
        period = dashboard.Period(date(2024, 1, 1), date(2024, 8, 31), "2024 (8m)")
        df = _make_df([
            {"Дата": "2024-08-15", "Сумма": 500.0, "Категория": "Прочее", "Участок": ""},
        ])
        months = dashboard.monthly_breakdown(df, period)
        self.assertEqual(len(months), 8)
        self.assertEqual(months[-1].income, 500.0)

    def test_longer_period_not_truncated_to_12(self):
        period = dashboard.Period(date(2024, 1, 1), date(2025, 2, 28), "2024/25 long")
        df = _make_df([
            {"Дата": "2025-02-10", "Сумма": 700.0, "Категория": "Прочее", "Участок": ""},
        ])
        months = dashboard.monthly_breakdown(df, period)
        self.assertEqual(len(months), 14)
        self.assertEqual(months[-1].income, 700.0)

    def test_monthly_category_breakdown_matches_period_length(self):
        period = dashboard.Period(date(2024, 1, 1), date(2025, 2, 28), "2024/25 long")
        df = _make_df([
            {"Дата": "2025-02-10", "Сумма": 700.0, "Категория": "Прочее", "Участок": ""},
        ])
        months = dashboard.monthly_category_breakdown(df, period, "income")
        self.assertEqual(len(months), 14)
        self.assertEqual(dict(months[-1].categories).get("Прочее", 0.0), 700.0)


if __name__ == "__main__":
    unittest.main()
