"""Юнит-тесты для core.ownership.

Запуск: python -m unittest tests.test_ownership
"""
import unittest
from datetime import date

from core import ownership as own


class AccessorsTests(unittest.TestCase):
    def test_string_owner(self):
        self.assertEqual(own.owner_name("Иванов И.И."), "Иванов И.И.")
        self.assertTrue(own.is_owner("Иванов И.И."))
        self.assertIsNone(own.owner_area("Иванов И.И."))
        self.assertIsNone(own.owner_since("Иванов И.И."))
        self.assertIsNone(own.owner_until("Иванов И.И."))

    def test_dict_new_format(self):
        o = {"name": "Петров", "is_owner": True, "area": 300,
             "since": "2024-06-01", "until": "2025-01-01", "share": "1/2"}
        self.assertEqual(own.owner_name(o), "Петров")
        self.assertTrue(own.is_owner(o))
        self.assertEqual(own.owner_area(o), 300.0)
        self.assertEqual(own.owner_since(o), date(2024, 6, 1))
        self.assertEqual(own.owner_until(o), date(2025, 1, 1))
        self.assertEqual(own.owner_share(o), 0.5)

    def test_legacy_relation_is_owner(self):
        self.assertTrue(own.is_owner({"name": "X", "relation": "Собственник"}))
        self.assertTrue(own.is_owner({"name": "X", "relation": "Главный собственник"}))
        self.assertTrue(own.is_owner({"name": "X"}))  # нет ни is_owner, ни relation

    def test_contact_not_owner(self):
        self.assertFalse(own.is_owner({"name": "X", "is_owner": False}))

    def test_bad_dates_are_none(self):
        o = {"name": "X", "since": "не дата", "until": ""}
        self.assertIsNone(own.owner_since(o))
        self.assertIsNone(own.owner_until(o))


class ParseShareTests(unittest.TestCase):
    def test_fraction(self):
        self.assertAlmostEqual(own.parse_share("1/2"), 0.5)
        self.assertAlmostEqual(own.parse_share("2/9"), 2 / 9)

    def test_decimal_and_comma(self):
        self.assertAlmostEqual(own.parse_share("0.25"), 0.25)
        self.assertAlmostEqual(own.parse_share("0,25"), 0.25)

    def test_numeric(self):
        self.assertAlmostEqual(own.parse_share(0.5), 0.5)
        self.assertAlmostEqual(own.parse_share(1), 1.0)

    def test_invalid_and_zero(self):
        self.assertIsNone(own.parse_share(""))
        self.assertIsNone(own.parse_share("abc"))
        self.assertIsNone(own.parse_share("1/0"))
        self.assertIsNone(own.parse_share(0))
        self.assertIsNone(own.parse_share(-1))
        self.assertIsNone(own.parse_share(None))
        self.assertIsNone(own.parse_share(True))  # bool не доля


class EffectiveWeightsTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(own.effective_weights([]), [])

    def test_all_missing_equal_split(self):
        w = own.effective_weights([{"name": "A"}, {"name": "B"}, {"name": "C"}])
        self.assertEqual(len(w), 3)
        for x in w:
            self.assertAlmostEqual(x, 1 / 3)
        self.assertAlmostEqual(sum(w), 1.0)

    def test_all_explicit_normalized(self):
        # 1/3 каждому, в сумме 0.999... → нормируется ровно к 1.0
        w = own.effective_weights([
            {"name": "A", "share": "1/3"},
            {"name": "B", "share": "1/3"},
            {"name": "C", "share": "1/3"},
        ])
        self.assertAlmostEqual(sum(w), 1.0)
        for x in w:
            self.assertAlmostEqual(x, 1 / 3)

    def test_explicit_unequal(self):
        w = own.effective_weights([
            {"name": "A", "share": "1/2"},
            {"name": "B", "share": "1/4"},
            {"name": "C", "share": "1/4"},
        ])
        self.assertAlmostEqual(w[0], 0.5)
        self.assertAlmostEqual(w[1], 0.25)
        self.assertAlmostEqual(w[2], 0.25)

    def test_mixed_known_and_missing(self):
        # A=1/2 задан, B и C делят остаток 1/2 поровну → по 1/4
        w = own.effective_weights([
            {"name": "A", "share": "1/2"},
            {"name": "B"},
            {"name": "C"},
        ])
        self.assertAlmostEqual(sum(w), 1.0)
        self.assertAlmostEqual(w[0], 0.5)
        self.assertAlmostEqual(w[1], 0.25)
        self.assertAlmostEqual(w[2], 0.25)

    def test_money_reconciles(self):
        owners = [{"name": "A", "share": "1/2"}, {"name": "B"}, {"name": "C"}]
        charge = 10000.0
        parts = [charge * x for x in own.effective_weights(owners)]
        self.assertAlmostEqual(sum(parts), charge)


class ActivityTests(unittest.TestCase):
    def test_no_dates_always_active(self):
        o = {"name": "X"}
        self.assertTrue(own.is_active_at(o, date(2000, 1, 1)))
        self.assertTrue(own.is_active_at(o, date(2030, 1, 1)))

    def test_since_inclusive(self):
        o = {"name": "X", "since": "2024-06-01"}
        self.assertFalse(own.is_active_at(o, date(2024, 5, 31)))
        self.assertTrue(own.is_active_at(o, date(2024, 6, 1)))

    def test_until_exclusive(self):
        o = {"name": "X", "until": "2024-06-01"}
        self.assertTrue(own.is_active_at(o, date(2024, 5, 31)))
        self.assertFalse(own.is_active_at(o, date(2024, 6, 1)))

    def test_transfer_boundary_no_overlap(self):
        seller = {"name": "Seller", "until": "2024-06-01"}
        buyer = {"name": "Buyer", "since": "2024-06-01"}
        # На дату перехода активен только покупатель
        active = own.owners_at([seller, buyer], date(2024, 6, 1))
        self.assertEqual([own.owner_name(o) for o in active], ["Buyer"])
        # За день до — только продавец
        active = own.owners_at([seller, buyer], date(2024, 5, 31))
        self.assertEqual([own.owner_name(o) for o in active], ["Seller"])

    def test_owners_at_filters_contacts(self):
        owners = [{"name": "Owner", "is_owner": True},
                  {"name": "Contact", "is_owner": False}]
        active = own.owners_at(owners, date(2024, 1, 1))
        self.assertEqual([own.owner_name(o) for o in active], ["Owner"])
        active_all = own.owners_at(owners, date(2024, 1, 1), only_owners=False)
        self.assertEqual(len(active_all), 2)


class HistoryTests(unittest.TestCase):
    def test_has_history(self):
        self.assertFalse(own.has_history([{"name": "X"}]))
        self.assertFalse(own.has_history(["Иванов"]))
        self.assertTrue(own.has_history([{"name": "X", "since": "2024-01-01"}]))
        self.assertTrue(own.has_history([{"name": "X", "until": "2024-01-01"}]))

    def test_transfer_dates(self):
        owners = [{"name": "S", "until": "2024-06-01"},
                  {"name": "B", "since": "2024-06-01", "until": "2025-03-01"},
                  {"name": "C", "since": "2025-03-01"}]
        self.assertEqual(own.transfer_dates(owners),
                         [date(2024, 6, 1), date(2025, 3, 1)])


class SegmentsTests(unittest.TestCase):
    def test_no_history_single_open_segment(self):
        owners = [{"name": "A"}, {"name": "B"}]
        segs = own.ownership_segments(owners)
        self.assertEqual(len(segs), 1)
        a, b, active = segs[0]
        self.assertIsNone(a)
        self.assertIsNone(b)
        self.assertEqual([own.owner_name(o) for o in active], ["A", "B"])

    def test_sequential_transfer(self):
        owners = [{"name": "S", "until": "2024-06-01"},
                  {"name": "B", "since": "2024-06-01"}]
        segs = own.ownership_segments(owners)
        # (None, 2024-06-01, [S]), (2024-06-01, None, [B])
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0][0], None)
        self.assertEqual(segs[0][1], date(2024, 6, 1))
        self.assertEqual([own.owner_name(o) for o in segs[0][2]], ["S"])
        self.assertEqual(segs[1][0], date(2024, 6, 1))
        self.assertEqual(segs[1][1], None)
        self.assertEqual([own.owner_name(o) for o in segs[1][2]], ["B"])

    def test_gap_in_ownership(self):
        # Продал 2024-06-01, новый купил 2025-01-01 → провал
        owners = [{"name": "S", "until": "2024-06-01"},
                  {"name": "B", "since": "2025-01-01"}]
        segs = own.ownership_segments(owners)
        self.assertEqual(len(segs), 3)
        # средний сегмент — пустой
        self.assertEqual(segs[1][0], date(2024, 6, 1))
        self.assertEqual(segs[1][1], date(2025, 1, 1))
        self.assertEqual(segs[1][2], [])

    def test_co_owners_then_sale(self):
        # A+B владеют совместно, затем оба продают C на 2024-06-01
        owners = [{"name": "A", "until": "2024-06-01"},
                  {"name": "B", "until": "2024-06-01"},
                  {"name": "C", "since": "2024-06-01"}]
        segs = own.ownership_segments(owners)
        self.assertEqual(len(segs), 2)
        self.assertEqual([own.owner_name(o) for o in segs[0][2]], ["A", "B"])
        self.assertEqual([own.owner_name(o) for o in segs[1][2]], ["C"])


class OwnershipFormInferenceTests(unittest.TestCase):
    def test_explicit_wins(self):
        rec = {"ownership_form": "joint",
               "owners": [{"name": "A", "share": "1/2"}]}
        self.assertEqual(own.plot_ownership_form(rec), "joint")

    def test_single_owner_individual(self):
        rec = {"owners": [{"name": "A", "is_owner": True}]}
        self.assertEqual(own.plot_ownership_form(rec), own.FORM_INDIVIDUAL)

    def test_multi_with_shares_shared(self):
        rec = {"owners": [{"name": "A", "share": "1/2"},
                          {"name": "B", "share": "1/2"}]}
        self.assertEqual(own.plot_ownership_form(rec), own.FORM_SHARED)

    def test_multi_without_shares_joint(self):
        rec = {"owners": [{"name": "A", "is_owner": True},
                          {"name": "B", "is_owner": True}]}
        self.assertEqual(own.plot_ownership_form(rec), own.FORM_JOINT)

    def test_contacts_ignored_in_inference(self):
        # один собственник + контактное лицо → индивидуальная
        rec = {"owners": [{"name": "A", "is_owner": True},
                          {"name": "Контакт", "is_owner": False}]}
        self.assertEqual(own.plot_ownership_form(rec), own.FORM_INDIVIDUAL)


class LabelTests(unittest.TestCase):
    def test_label(self):
        owners = [{"name": "Иванов", "is_owner": True},
                  {"name": "Контакт", "is_owner": False}]
        self.assertEqual(own.owners_label(owners), "Иванов")
        self.assertEqual(own.owners_label(owners, only_owners=False),
                         "Иванов, Контакт")
        self.assertEqual(own.owners_label([]), "—")


if __name__ == "__main__":
    unittest.main()
