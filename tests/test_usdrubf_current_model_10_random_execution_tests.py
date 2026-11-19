import hashlib
import unittest
from datetime import date, timedelta
from html.parser import HTMLParser

from scripts.usdrubf_current_model_10_random_execution_tests import (
    DECISION_UNREPRODUCIBLE, HORIZON, MASTER_SEED, DailyCandle, ExecutionCase,
    derived_seed, eligible_indices, evaluate_trade, render_html, select_indices,
)


class StrictHTMLParser(HTMLParser):
    def error(self, message):  # pragma: no cover
        raise AssertionError(message)


def candle(day, open_=100, high=101, low=99, close=100, volume=10):
    return DailyCandle(day, open_, high, low, close, volume,
                       f"{day} 00:00:00", f"{day} 23:59:59")


class CurrentModelRandomExecutionTest(unittest.TestCase):
    def test_sampling_is_uniform_without_replacement_and_seed_is_auditable(self):
        eligible = list(range(200, 400))
        first, seed, material = select_indices(eligible)
        second, seed2, material2 = select_indices(eligible)
        self.assertEqual(first, second)
        self.assertEqual(len(first), len(set(first)))
        self.assertEqual(len(first), 10)
        self.assertLessEqual(set(first), set(eligible))
        self.assertEqual((seed, material), (seed2, material2))
        self.assertEqual(seed, int(hashlib.sha256(material.encode()).hexdigest()[:16], 16))
        self.assertIn(str(MASTER_SEED), material)

    def test_eligible_requires_two_year_window_warmup_and_full_future(self):
        start = date(2024, 1, 1)
        candles = [candle(start + timedelta(days=i)) for i in range(800)]
        as_of = date(2026, 3, 1)
        got = eligible_indices(candles, as_of)
        self.assertTrue(got)
        self.assertTrue(all(candles[i].day >= date(2024, 3, 1) for i in got))
        self.assertTrue(all(i + 1 >= 200 and i + HORIZON < len(candles) for i in got))

    def test_execution_first_touch_rules(self):
        examples = [
            ("LONG", [candle(date(2026, 1, 2), high=111, low=89)], "STOP_FIRST/ambiguous"),
            ("LONG", [candle(date(2026, 1, 2), high=105, low=89)], "STOP_FIRST"),
            ("LONG", [candle(date(2026, 1, 2), high=121, low=95)], "TP2_FIRST"),
            ("SHORT", [candle(date(2026, 1, 2), high=111, low=79)], "STOP_FIRST/ambiguous"),
            ("SHORT", [candle(date(2026, 1, 2), high=105, low=89)], "TP1_FIRST"),
            ("SHORT", [candle(date(2026, 1, 2), high=105, low=95)], "NOT_REACHED"),
        ]
        for direction, future, expected in examples:
            levels = (90, 110, 120) if direction == "LONG" else (110, 90, 80)
            with self.subTest(direction=direction, expected=expected):
                self.assertEqual(evaluate_trade(direction, *levels, future), expected)

    def test_report_is_russian_parseable_and_does_not_mislabel_missing_model_as_no_trade(self):
        origin = candle(date(2025, 8, 1))
        future = tuple(candle(date(2025, 8, 2) + timedelta(days=i), close=101 + i) for i in range(10))
        features = {
            "bars": 300, "history_from": "2024-01-01T00:00:00+03:00", "close": 100,
            "ma20": 99, "ma20_previous": 98.9, "ma50": 97, "ma50_previous": 96.9,
            "ma200": 95, "rsi14": 55, "atr14": 1.5,
            "supports": [{"price": 98, "touches": 2}],
            "resistances": [{"price": 102, "touches": 3}],
            "confirmed_pivots": [], "waves": [],
        }
        cases = [ExecutionCase(i, 250 + i, origin, future, features) for i in range(1, 11)]
        manifest = {
            "source": "MOEX ISS", "rows": 500, "first_day": "2024-01-01",
            "last_day": "2026-01-01", "fetched_at_msk": "2026-01-02T12:00:00+03:00",
            "request_urls": ["https://iss.moex.com/example"],
        }
        seed, material = derived_seed(MASTER_SEED)
        document = render_html(cases, manifest, "a" * 64, as_of=date(2026, 1, 2),
                               eligible_count=200, seed=seed, seed_material=material,
                               script_sha="b" * 64)
        for required in (
            "НЕ ВОСПРОИЗВОДИТСЯ", DECISION_UNREPRODUCIBLE, "не <code>NO_TRADE</code>",
            "ровно 10", "без возвращения", "Master seed", "SHA-256-derived seed",
            "entry/stop/TP1/TP2", "STOP_FIRST/ambiguous", "Будущая D1-динамика: только факт",
            "CSV SHA-256", "SHA-256 генератора", "Полный pytest", "HTML parser",
            "не SMA-классификатор", "Открытых моделью сделок: 0",
        ):
            self.assertIn(required, document)
        self.assertEqual(document.count("<td>NOT_SIMULATED</td>"), 10)
        parser = StrictHTMLParser()
        parser.feed(document)
        parser.close()


if __name__ == "__main__":
    unittest.main()
