"""Юнит-тесты для core.energy.

Запуск: python -m unittest tests.test_energy
"""
import unittest
from datetime import date

from core import energy
from tests._util import make_df as _make_df


RATES = [
    {"date": "2024-01-01", "rate": "5.00", "note": ""},
    {"date": "2025-05-01", "rate": "5.85", "note": ""},
    {"date": "2026-05-01", "rate": "6.28", "note": ""},
]


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


class OwnAverageTests(unittest.TestCase):
    def test_returns_none_without_history(self):
        self.assertIsNone(
            energy.own_average_kwh("20", {}, {}, before=date(2026, 1, 1)))

    def test_averages_consumption_before_cutoff(self):
        meters = {"20:2026:1": "100", "20:2026:2": "130",
                  "20:2026:3": "160", "20:2026:4": "190"}
        # расходы: 30, 30, 30 -> среднее 30
        avg = energy.own_average_kwh("20", meters, {}, before=date(2026, 5, 1))
        self.assertEqual(avg, 30.0)

    def test_ignores_readings_on_or_after_cutoff(self):
        meters = {"20:2026:1": "0", "20:2026:2": "10",
                  "20:2026:3": "1000"}  # расход за март (900) после cutoff — не в счёт
        avg = energy.own_average_kwh("20", meters, {}, before=date(2026, 3, 1))
        self.assertEqual(avg, 10.0)

    def test_window_limits_to_last_n_months(self):
        # Показания растут на 10 каждый месяц, кроме скачка на 1000 в июле —
        # с window_months=3 он не должен попасть в среднее за окт-дек.
        meters = {}
        v = 0
        for y, m in [(2025, mo) for mo in range(1, 13)]:
            meters[f"20:{y}:{m}"] = str(v)
            v += 1000 if (y, m) == (2025, 7) else 10
        avg = energy.own_average_kwh("20", meters, {}, before=date(2026, 1, 1),
                                     window_months=3)
        self.assertEqual(avg, 10.0)

    def test_normalizes_intervals_longer_than_a_month(self):
        # Показания передаются раз в год (обычная практика у части
        # садоводов) — расход интервала (275/306/250) относится к ГОДУ, а
        # не к месяцу. "Среднемесячный" расход должен делить его на 12, а
        # не усреднять годовые расходы как будто это месячные значения
        # (иначе начисление задирается в ~12 раз).
        meters = {
            "39:2022:12": "1856", "39:2023:12": "2131",
            "39:2024:12": "2437", "39:2025:12": "2687",
        }
        avg = energy.own_average_kwh("39", meters, {}, before=date(2026, 7, 1))
        # Последний интервал (12.2024 -> 12.2025) один уже покрывает 12 мес.
        self.assertAlmostEqual(avg, 250.0 / 12, places=6)

    def test_normalizes_and_spans_window_across_multiple_long_intervals(self):
        # Окно уже, чем один интервал (6 мес. при годовых показаниях) —
        # должен использоваться ближайший интервал целиком (он уже
        # покрывает окно), а не "недобор" из-за нехватки пунктов.
        meters = {
            "39:2022:12": "1856", "39:2023:12": "2131",
            "39:2024:12": "2437", "39:2025:12": "2687",
        }
        avg = energy.own_average_kwh("39", meters, {}, before=date(2026, 7, 1),
                                     window_months=6)
        self.assertAlmostEqual(avg, 250.0 / 12, places=6)


class AvgWindowMonthsOfTests(unittest.TestCase):
    def test_default_when_unset(self):
        plots = [{"num": "1"}]
        self.assertEqual(energy.avg_window_months_of("1", plots=plots), 12)

    def test_default_when_missing_plot(self):
        self.assertEqual(energy.avg_window_months_of("999"), 12)

    def test_uses_stored_value(self):
        plots = [{"num": "1", "avg_window_months": 6}]
        self.assertEqual(energy.avg_window_months_of("1", plots=plots), 6)

    def test_invalid_value_falls_back_to_default(self):
        plots = [{"num": "1", "avg_window_months": "не число"}]
        self.assertEqual(energy.avg_window_months_of("1", plots=plots), 12)
        plots2 = [{"num": "1", "avg_window_months": 0}]
        self.assertEqual(energy.avg_window_months_of("1", plots=plots2), 12)

    def test_local_value_wins_over_global(self):
        plots = [{"num": "1", "avg_window_months": 6}]
        auto_settings = {"default_avg_window_months": 9}
        self.assertEqual(
            energy.avg_window_months_of("1", plots=plots, auto_settings=auto_settings), 6)

    def test_global_value_used_when_local_unset(self):
        plots = [{"num": "1"}]
        auto_settings = {"default_avg_window_months": 9}
        self.assertEqual(
            energy.avg_window_months_of("1", plots=plots, auto_settings=auto_settings), 9)

    def test_hardcoded_default_when_neither_set(self):
        plots = [{"num": "1"}]
        auto_settings = {"default_avg_window_months": None}
        self.assertEqual(
            energy.avg_window_months_of("1", plots=plots, auto_settings=auto_settings), 12)

    def test_charges_for_plot_respects_custom_window(self):
        # Расход растёт по 10 каждый месяц, кроме одного скачка на 1000 —
        # окно 3 мес. должно исключить скачок, окно 12 мес. — включить.
        meters = {}
        v = 0
        for y, m in [(2024, mo) for mo in range(1, 13)]:
            meters[f"1:{y}:{m}"] = str(v)
            v += 1000 if (y, m) == (2024, 7) else 10
        rates = [{"date": "2020-01-01", "rate": "5.00"}]

        plots_narrow = [{
            "num": "1", "billing_type": energy.BILLING_CALCULATED,
            "calc_method": energy.CALC_METHOD_OWN_AVERAGE,
            "avg_window_months": 3,
            "norm_start_date": "2025-01-01",
        }]
        charges = energy.charges_for_plot(
            "1", meters, rates, {}, up_to=date(2025, 1, 31), plots=plots_narrow)
        self.assertEqual(charges[0]["kwh"], 10.0)

        plots_wide = [dict(plots_narrow[0], avg_window_months=12)]
        charges2 = energy.charges_for_plot(
            "1", meters, rates, {}, up_to=date(2025, 1, 31), plots=plots_wide)
        # Скачок в 1000 попадает в окно 12 мес и заметно поднимает среднее
        self.assertGreater(charges2[0]["kwh"], 50.0)


class NormKwOfTests(unittest.TestCase):
    def test_none_when_neither_set(self):
        plots = [{"num": "1"}]
        self.assertIsNone(energy.norm_kw_of("1", plots=plots))

    def test_local_value_wins_over_global(self):
        plots = [{"num": "1", "norm_kw": 1.5}]
        auto_settings = {"default_norm_kw": 2.9}
        self.assertEqual(
            energy.norm_kw_of("1", plots=plots, auto_settings=auto_settings), 1.5)

    def test_global_value_used_when_local_unset(self):
        plots = [{"num": "1"}]
        auto_settings = {"default_norm_kw": 2.9}
        self.assertEqual(
            energy.norm_kw_of("1", plots=plots, auto_settings=auto_settings), 2.9)

    def test_global_none_falls_back_to_none(self):
        plots = [{"num": "1"}]
        auto_settings = {"default_norm_kw": None}
        self.assertIsNone(
            energy.norm_kw_of("1", plots=plots, auto_settings=auto_settings))

    def test_charges_for_plot_manual_norm_uses_global_default(self):
        # Тип 2 (Расчётный метод / норматив), но норматив на самом участке НЕ
        # задан — норматив должен подтянуться из глобальных настроек.
        rates = [{"date": "2020-01-01", "rate": "5.00"}]
        plots = [{
            "num": "1", "billing_type": energy.BILLING_CALCULATED,
            "calc_method": energy.CALC_METHOD_NORM,
            "norm_start_date": "2024-01-01",
        }]
        auto_settings = {"default_norm_kw": 0.1}
        charges = energy.charges_for_plot(
            "1", {}, rates, {}, up_to=date(2024, 1, 31), plots=plots,
            auto_settings=auto_settings)
        self.assertEqual(len(charges), 1)
        self.assertAlmostEqual(charges[0]["kwh"], 0.1 * 24 * charges[0]["days"], places=6)

    def test_charges_for_plot_manual_norm_empty_without_global_default(self):
        rates = [{"date": "2020-01-01", "rate": "5.00"}]
        plots = [{
            "num": "1", "billing_type": energy.BILLING_CALCULATED,
            "calc_method": energy.CALC_METHOD_NORM,
            "norm_start_date": "2024-01-01",
        }]
        charges = energy.charges_for_plot(
            "1", {}, rates, {}, up_to=date(2024, 1, 31), plots=plots)
        self.assertEqual(charges, [])

    def test_charges_prorate_partial_month(self):
        rates = [{"date": "2024-01-01", "rate": "5.00"}]
        charges = energy.own_average_charges(
            "20", 60.0, date(2026, 1, 15), rates, up_to=date(2026, 1, 31))
        self.assertEqual(len(charges), 1)
        # 17 из 31 дня января: 60 * 17/31 * 5.00
        self.assertAlmostEqual(charges[0]["amount"], 60.0 * 17 / 31 * 5.00, places=6)

    def test_charges_for_plot_switches_to_own_average(self):
        meters = {"1:2024:1": "100", "1:2024:2": "130",
                  "1:2024:3": "160", "1:2024:4": "190"}
        rates = [{"date": "2020-01-01", "rate": "5.50"}]
        plots = [{
            "num": "1",
            "billing_type": energy.BILLING_CALCULATED,
            "calc_method": energy.CALC_METHOD_OWN_AVERAGE,
            "norm_start_date": "2024-05-01",
            "billing_history": [{
                "date": "2024-05-01", "from": energy.BILLING_METER,
                "to": energy.BILLING_CALCULATED,
            }],
        }]
        charges = energy.charges_for_plot(
            "1", meters, rates, {}, up_to=date(2024, 6, 30), plots=plots)
        by_month = {(c["year"], c["month"]): c for c in charges}
        # Февраль-апрель посчитаны по счётчику (расход 30 каждый)
        self.assertEqual(by_month[(2024, 4)]["kwh"], 30)
        self.assertIsNone(by_month[(2024, 4)].get("calc_method"))
        # Май-июнь — по среднему (30 кВт·ч/мес из истории до 01.05)
        self.assertEqual(by_month[(2024, 5)]["calc_method"],
                         energy.CALC_METHOD_OWN_AVERAGE)
        self.assertEqual(by_month[(2024, 5)]["kwh"], 30.0)
        self.assertEqual(by_month[(2024, 5)]["amount"], 30.0 * 5.50)

    def test_charges_for_plot_own_average_empty_without_history(self):
        rates = [{"date": "2020-01-01", "rate": "5.50"}]
        plots = [{
            "num": "2",
            "billing_type": energy.BILLING_CALCULATED,
            "calc_method": energy.CALC_METHOD_OWN_AVERAGE,
            "norm_start_date": "2024-05-01",
        }]
        charges = energy.charges_for_plot(
            "2", {}, rates, {}, up_to=date(2024, 6, 30), plots=plots)
        self.assertEqual(charges, [])


class MonthsWithoutReadingTests(unittest.TestCase):
    def test_none_for_non_meter_type(self):
        plots = [{"num": "1", "billing_type": energy.BILLING_CALCULATED}]
        self.assertIsNone(
            energy.months_without_reading("1", {}, date(2026, 1, 1), plots=plots))

    def test_none_without_any_baseline(self):
        # meter, но нет ни показаний, ни meter_commission_date
        # plots=[] — намеренно пустой реестр, чтобы не зависеть от того,
        # что реально лежит в data/snt_plots.json текущего проекта.
        self.assertIsNone(
            energy.months_without_reading("1", {}, date(2026, 1, 1), plots=[]))

    def test_counts_from_last_reading(self):
        meters = {"1:2025:1": "100"}
        self.assertEqual(
            energy.months_without_reading("1", meters, date(2025, 4, 1), plots=[]), 3)

    def test_zero_right_after_reading(self):
        meters = {"1:2025:1": "100"}
        self.assertEqual(
            energy.months_without_reading("1", meters, date(2025, 1, 31), plots=[]), 0)

    def test_counts_from_commission_date_when_never_reported(self):
        plots = [{"num": "1", "billing_type": energy.BILLING_METER,
                  "meter_commission_date": "2025-01-15"}]
        self.assertEqual(
            energy.months_without_reading("1", {}, date(2025, 5, 1), plots=plots), 4)


class AutoEstimateTests(unittest.TestCase):
    RATES = [{"date": "2020-01-01", "rate": "5.00"}]

    @staticmethod
    def _steady_history(plot: str, start_year: int, n_months: int, step: float = 30.0) -> dict:
        """n_months показаний подряд начиная с января start_year, расход step/мес."""
        meters = {}
        v = 0.0
        y, m = start_year, 1
        for _ in range(n_months):
            meters[f"{plot}:{y}:{m}"] = str(v)
            v += step
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return meters

    def test_disabled_by_default_no_auto_settings(self):
        meters = self._steady_history("1", 2023, 13)  # ...2024:1
        charges = energy.charges_for_plot(
            "1", meters, self.RATES, {}, up_to=date(2024, 6, 30))
        self.assertFalse(any(c.get("auto_estimate") for c in charges))

    def test_disabled_when_settings_say_so(self):
        meters = self._steady_history("1", 2023, 13)
        charges = energy.charges_for_plot(
            "1", meters, self.RATES, {}, up_to=date(2024, 6, 30),
            auto_settings={"enabled": False, "months": 3})
        self.assertFalse(any(c.get("auto_estimate") for c in charges))

    def test_not_triggered_before_threshold(self):
        meters = self._steady_history("1", 2023, 13)  # last real reading 2024:1
        # Только 2 месяца прошло — меньше порога в 3
        charges = energy.charges_for_plot(
            "1", meters, self.RATES, {}, up_to=date(2024, 3, 31),
            auto_settings={"enabled": True, "months": 3})
        self.assertFalse(any(c.get("auto_estimate") for c in charges))

    def test_triggers_after_threshold_using_own_average(self):
        meters = self._steady_history("1", 2023, 13)  # 30 кВт·ч/мес всю историю
        plots = [{"num": "1", "billing_type": energy.BILLING_METER,
                  "calc_method": energy.CALC_METHOD_OWN_AVERAGE}]
        # 5 мес. без показаний, порог (= длина расчётного цикла) — 3: начисляем
        # только 1 целиком прошедший цикл (Фев-Апр), Май-Июнь — недостроенный
        # второй цикл, ждём его завершения.
        charges = energy.charges_for_plot(
            "1", meters, self.RATES, {}, up_to=date(2024, 6, 30), plots=plots,
            auto_settings={"enabled": True, "months": 3})
        auto = {(c["year"], c["month"]): c for c in charges if c.get("auto_estimate")}
        self.assertEqual(set(auto.keys()), {(2024, 2), (2024, 3), (2024, 4)})
        for c in auto.values():
            self.assertEqual(c["kwh"], 30.0)
            self.assertEqual(c["amount"], 30.0 * 5.00)

    def test_defaults_to_own_average_when_never_configured(self):
        # Участок никогда не открывали в диалоге «Тип расчёта» — нет ни
        # calc_method, ни norm_kw. При этом история показаний есть — автопереход
        # должен сам посчитать среднее, а не молча вернуть [] из-за отсутствия
        # норматива (это баг, который был исправлен).
        meters = self._steady_history("1", 2023, 13)  # 30 кВт·ч/мес всю историю
        plots = [{"num": "1", "billing_type": energy.BILLING_METER}]
        charges = energy.charges_for_plot(
            "1", meters, self.RATES, {}, up_to=date(2024, 6, 30), plots=plots,
            auto_settings={"enabled": True, "months": 3})
        auto = {(c["year"], c["month"]): c for c in charges if c.get("auto_estimate")}
        self.assertEqual(set(auto.keys()), {(2024, 2), (2024, 3), (2024, 4)})
        for c in auto.values():
            self.assertEqual(c["kwh"], 30.0)
            self.assertEqual(c["amount"], 30.0 * 5.00)

    def test_charges_only_complete_cycles_not_open_tail(self):
        # 14 мес. без показаний, порог 6: 2 целых цикла (12 мес.) начисляются,
        # хвост в 2 мес. (недостроенный 3-й цикл) — нет, как и попросил
        # пользователь ("нет смысла начислять за то, где человек ещё не
        # обязан был вносить оплату").
        meters = {"1:2022:12": "0", "1:2023:1": "30"}  # 1 интервал, 30 кВт·ч/мес
        plots = [{"num": "1", "billing_type": energy.BILLING_METER,
                  "calc_method": energy.CALC_METHOD_OWN_AVERAGE,
                  "avg_window_months": 1}]
        charges = energy.charges_for_plot(
            "1", meters, self.RATES, {}, up_to=date(2024, 3, 31), plots=plots,
            auto_settings={"enabled": True, "months": 6})
        auto = {(c["year"], c["month"]) for c in charges if c.get("auto_estimate")}
        # с 2023:1 по 2024:3 включительно — 14 месяцев разрыва (2023:2..2024:3)
        expected = {(2023, m) for m in range(2, 13)} | {(2024, 1)}
        self.assertEqual(auto, expected)
        self.assertNotIn((2024, 2), auto)
        self.assertNotIn((2024, 3), auto)

    def test_threshold_of_one_charges_every_month_immediately(self):
        # Порог 1 мес. — цикл длиной в месяц, ведёт себя как раньше:
        # начисление сразу за каждый прошедший месяц, без накопления хвоста.
        meters = self._steady_history("1", 2023, 13)
        plots = [{"num": "1", "billing_type": energy.BILLING_METER,
                  "calc_method": energy.CALC_METHOD_OWN_AVERAGE}]
        charges = energy.charges_for_plot(
            "1", meters, self.RATES, {}, up_to=date(2024, 4, 30), plots=plots,
            auto_settings={"enabled": True, "months": 1})
        auto = {(c["year"], c["month"]) for c in charges if c.get("auto_estimate")}
        self.assertEqual(auto, {(2024, 2), (2024, 3), (2024, 4)})

    def test_respects_explicitly_chosen_norm_even_with_history(self):
        # Администратор когда-то явно выбрал "по нормативу" для этого участка —
        # этот выбор нужно уважать, а не переопределять средним, даже если
        # история показаний есть.
        meters = self._steady_history("1", 2023, 13)
        plots = [{"num": "1", "billing_type": energy.BILLING_METER,
                  "calc_method": energy.CALC_METHOD_NORM, "norm_kw": 0.1}]
        charges = energy.charges_for_plot(
            "1", meters, self.RATES, {}, up_to=date(2024, 6, 30), plots=plots,
            auto_settings={"enabled": True, "months": 3})
        auto = [c for c in charges if c.get("auto_estimate")]
        self.assertTrue(auto)
        # 0.1 кВт * 24 ч = 2.4 кВт·ч/сутки — норматив, не среднее (30/мес)
        for c in auto:
            self.assertAlmostEqual(c["kwh"], 0.1 * 24 * c["days"], places=6)

    def test_falls_back_to_norm_without_history(self):
        # meter, ни одного показания вообще, только дата ввода в эксплуатацию
        plots = [{"num": "1", "billing_type": energy.BILLING_METER,
                  "meter_commission_date": "2024-01-01", "norm_kw": 0.05}]
        charges = energy.charges_for_plot(
            "1", {}, self.RATES, {}, up_to=date(2024, 5, 1), plots=plots,
            auto_settings={"enabled": True, "months": 3})
        auto = [c for c in charges if c.get("auto_estimate")]
        self.assertTrue(auto)
        # 0.05 кВт * 24 ч = 1.2 кВт·ч/сутки — как у обычного расчётного метода
        self.assertAlmostEqual(auto[0]["kwh"], 0.05 * 24 * auto[0]["days"], places=6)

    def test_empty_without_any_basis_for_estimate(self):
        # meter, нет ни показаний, ни даты ввода, ни норматива
        plots = [{"num": "1", "billing_type": energy.BILLING_METER}]
        charges = energy.charges_for_plot(
            "1", {}, self.RATES, {}, up_to=date(2024, 5, 1), plots=plots,
            auto_settings={"enabled": True, "months": 3})
        self.assertEqual(charges, [])

    def test_recalculates_automatically_once_reading_closes_gap(self):
        """Ключевой сценарий: после того как участок передал реальное
        показание, закрывающее разрыв, автооценка для пропущенных месяцев
        должна исчезнуть — без двойного счёта."""
        meters = self._steady_history("1", 2023, 13)  # ...2024:1 = 360, шаг 30
        plots = [{"num": "1", "billing_type": energy.BILLING_METER,
                  "calc_method": energy.CALC_METHOD_OWN_AVERAGE}]
        auto_settings = {"enabled": True, "months": 3}
        as_of = date(2024, 6, 30)

        before = energy.charges_for_plot(
            "1", meters, self.RATES, {}, up_to=as_of, plots=plots,
            auto_settings=auto_settings)
        before_total = sum(c["amount"] for c in before if c["amount"] is not None)
        self.assertTrue(any(c.get("auto_estimate") for c in before))
        # Разрыв в 5 мес., порог (= цикл) 3 — начислен только 1 целый цикл
        # (Фев-Апр = 3×30 кВт·ч), Май-Июнь ждут завершения второго цикла.
        auto_before = {(c["year"], c["month"]) for c in before if c.get("auto_estimate")}
        self.assertEqual(auto_before, {(2024, 2), (2024, 3), (2024, 4)})

        # Владелец наконец передаёт реальное показание за июнь — фактический
        # расход за пропущенные месяцы оказался БОЛЬШЕ, чем оценка (200 вместо
        # 3×30=90 кВт·ч, начисленных по факту в "before").
        meters_after = dict(meters)
        meters_after["1:2024:6"] = str(360 + 200)

        after = energy.charges_for_plot(
            "1", meters_after, self.RATES, {}, up_to=as_of, plots=plots,
            auto_settings=auto_settings)
        after_total = sum(c["amount"] for c in after if c["amount"] is not None)

        # Автооценка для закрытого разрыва больше не генерируется
        self.assertFalse(any(c.get("auto_estimate") for c in after))
        by_month_after = {(c["year"], c["month"]): c for c in after}
        self.assertNotIn((2024, 2), by_month_after)
        self.assertNotIn((2024, 3), by_month_after)
        self.assertNotIn((2024, 4), by_month_after)
        self.assertNotIn((2024, 5), by_month_after)
        june = by_month_after[(2024, 6)]
        self.assertEqual(june["kwh"], 200)
        self.assertEqual(june["amount"], 200 * 5.00)

        # Реальный итог (12 обычных месяцев по 30 + июнь по факту 200) —
        # без остатка от прежней оценки на Фев-Май.
        expected_after_total = 12 * 30 * 5.00 + 200 * 5.00
        self.assertAlmostEqual(after_total, expected_after_total, places=6)
        # И разница с "before" — это ровно перерасчёт (было по оценке 90 за
        # начисленный целый цикл, стало по факту 200 за пропущенные месяцы),
        # а не сумма того и другого.
        self.assertAlmostEqual(after_total - before_total, (200 - 90) * 5.00, places=6)


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
