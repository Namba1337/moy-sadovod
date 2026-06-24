"""Юнит-тесты для core.people (реестр людей + миграция).

Запуск: python -m unittest tests.test_people
"""
import unittest

from core import people


def _plot(num, *owner_groups):
    """Хелпер: участок с группами. owner_groups — списки owners по группам."""
    groups = []
    for i, owners in enumerate(owner_groups):
        groups.append({
            "since": None,
            "until": None if i == len(owner_groups) - 1 else "2020-01-01",
            "owners": owners,
        })
    return {"num": num, "groups": groups}


class NormAndFindTests(unittest.TestCase):
    def test_norm_name(self):
        self.assertEqual(people._norm_name("  Иванов   Иван  "), "иванов иван")
        self.assertEqual(people._norm_name("ИВАНОВ иван"),
                         people._norm_name("иванов ИВАН"))
        self.assertEqual(people._norm_name(None), "")

    def test_find_by_name(self):
        ppl = [people.create_person("Иванов Иван Иванович", "111")]
        self.assertIsNotNone(people.find_by_name(ppl, "иванов  иван иванович"))
        self.assertIsNone(people.find_by_name(ppl, "Петров"))
        self.assertIsNone(people.find_by_name(ppl, ""))

    def test_create_person_has_id(self):
        p1 = people.create_person("X")
        p2 = people.create_person("X")
        self.assertTrue(p1["id"] and p2["id"])
        self.assertNotEqual(p1["id"], p2["id"])  # уникальные


class MigrationTests(unittest.TestCase):
    def test_dedup_by_full_name(self):
        plots = [
            _plot("10", [{"name": "Бодрова Анна", "is_owner": True}]),
            _plot("14", [{"name": "Бодрова Анна", "is_owner": True}]),
            _plot("11", [{"name": "Сергеева Ирина", "is_owner": True}]),
        ]
        ppl, out = people.migrate_people_from_plots(plots)
        # 3 записи владельцев → 2 человека (Бодрова склеена)
        self.assertEqual(len(ppl), 2)
        ids = [out[0]["groups"][0]["owners"][0]["person_id"],
               out[1]["groups"][0]["owners"][0]["person_id"]]
        self.assertEqual(ids[0], ids[1])  # один и тот же человек на 10 и 14

    def test_all_owners_get_person_id(self):
        plots = [_plot("1", [
            {"name": "Майер Сергей", "is_member": True},
            {"name": "Майер Денис", "is_owner": True},
        ])]
        _, out = people.migrate_people_from_plots(plots)
        for o in out[0]["groups"][0]["owners"]:
            self.assertTrue(o.get("person_id"))

    def test_empty_name_skipped(self):
        plots = [_plot("1", [{"name": "", "is_owner": True},
                             {"name": "Иванов", "is_owner": True}])]
        ppl, out = people.migrate_people_from_plots(plots)
        self.assertEqual(len(ppl), 1)
        owners = out[0]["groups"][0]["owners"]
        self.assertNotIn("person_id", owners[0])  # пустой — не человек
        self.assertTrue(owners[1].get("person_id"))

    def test_phone_email_first_nonempty_wins(self):
        plots = [
            _plot("10", [{"name": "Иванов", "phone": "", "email": "a@x"}]),
            _plot("11", [{"name": "Иванов", "phone": "555", "email": "b@y"}]),
        ]
        ppl, _ = people.migrate_people_from_plots(plots)
        self.assertEqual(len(ppl), 1)
        self.assertEqual(ppl[0]["phone"], "555")   # первый непустой
        self.assertEqual(ppl[0]["email"], "a@x")   # уже был непустой

    def test_input_not_mutated(self):
        plots = [_plot("1", [{"name": "Иванов", "is_owner": True}])]
        people.migrate_people_from_plots(plots)
        self.assertNotIn("person_id", plots[0]["groups"][0]["owners"][0])

    def test_idempotent(self):
        plots = [
            _plot("10", [{"name": "Бодрова Анна", "is_owner": True}]),
            _plot("14", [{"name": "Бодрова Анна", "is_owner": True}]),
        ]
        ppl1, out1 = people.migrate_people_from_plots(plots)
        ppl2, out2 = people.migrate_people_from_plots(out1)  # повторный прогон
        self.assertEqual(len(ppl1), len(ppl2))  # дублей не прибавилось
        # person_id стабилен между прогонами
        self.assertEqual(out1[0]["groups"][0]["owners"][0]["person_id"],
                         out2[0]["groups"][0]["owners"][0]["person_id"])

    def test_cache_name_preserved(self):
        plots = [_plot("1", [{"name": "Иванов Иван", "is_owner": True}])]
        _, out = people.migrate_people_from_plots(plots)
        self.assertEqual(out[0]["groups"][0]["owners"][0]["name"], "Иванов Иван")


if __name__ == "__main__":
    unittest.main()
