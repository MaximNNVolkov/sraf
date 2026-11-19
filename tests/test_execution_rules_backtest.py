import unittest
from datetime import date, timedelta

from scripts.moex_execution_rules_backtest import (
    DailyCandle, Signal, build_signals, evaluate_scenario, phase0_days,
    render_html, summarize, wilder_atr,
)


def candle(index, open_, high, low, close):
    return DailyCandle(date(2024, 1, 1) + timedelta(days=index), open_, high, low, close, 1)


class ExecutionRulesTest(unittest.TestCase):
    def test_wilder_atr_is_seeded_and_recursive_without_future(self):
        candles = [candle(i, 10, 12, 9, 11) for i in range(15)]
        values = wilder_atr(candles)
        self.assertIsNone(values[12])
        self.assertEqual(values[13], 3)
        changed = list(candles)
        changed[14] = candle(14, 10, 100, 1, 11)
        self.assertEqual(values[13], wilder_atr(changed)[13])
        self.assertAlmostEqual(wilder_atr(changed)[14], (13 * 3 + 99) / 14)

    def test_next_open_levels_are_based_on_open_but_atr_stays_at_t(self):
        candles = [candle(i, 100, 100, 100, 100) for i in range(11)]
        candles[1] = candle(1, 110, 111.1, 109.5, 110)
        signal = Signal("IMOEXF", 0, candles[0].day, "UP", 100, 110, 1, "UP", True)
        close_case = evaluate_scenario(signal, candles, "close_t", 1.0)
        open_case = evaluate_scenario(signal, candles, "next_open", 1.0)
        self.assertTrue(close_case.tp1_before_stop)
        self.assertTrue(open_case.tp1_before_stop)
        self.assertFalse(open_case.stop_before_tp1)

    def test_same_bar_stop_and_target_is_conservative_and_not_exact_r(self):
        candles = [candle(i, 100, 100, 100, 100) for i in range(11)]
        candles[1] = candle(1, 100, 102.5, 98.5, 100)
        signal = Signal("IMOEXF", 0, candles[0].day, "UP", 100, 100, 1, "UP", True)
        result = evaluate_scenario(signal, candles, "close_t", 1.0)
        self.assertTrue(result.stop_before_tp1)
        self.assertTrue(result.ambiguous_tp1)
        self.assertTrue(result.ambiguous_tp2)
        self.assertIsNone(result.tp1_r)
        self.assertIsNone(result.tp2_r)

    def test_short_direction_and_not_reached(self):
        candles = [candle(i, 100, 100.2, 99.8, 100) for i in range(11)]
        signal = Signal("USDRUBF", 0, candles[0].day, "DOWN", 100, 100, 1, "NEUTRAL", False)
        result = evaluate_scenario(signal, candles, "close_t", 2.0)
        self.assertTrue(result.neither_stop_nor_tp1)
        self.assertFalse(result.directional_correct)

    def test_exact_r_excludes_ambiguous_and_unreached(self):
        candles = [candle(i, 100, 100, 100, 100) for i in range(11)]
        signals = [Signal("IMOEXF", 0, candles[0].day, "UP", 100, 100, 1, "UP", True)]
        item = evaluate_scenario(signals[0], candles, "close_t", 1.0)
        summary = summarize([item])
        self.assertEqual(summary["tp1_exact_n"], 0)
        self.assertIsNone(summary["tp1_mean_r"])

    def test_phase_zero_is_exact_non_overlapping_stride(self):
        signals = [Signal("IMOEXF", i, date(2024, 1, 1) + timedelta(days=i), "UP", 1, 1, 1, "UP", True) for i in range(25)]
        self.assertEqual(phase0_days(signals), {signals[0].day, signals[10].day, signals[20].day})

    def test_build_signals_uses_full_t_plus_ten_and_keeps_neutral(self):
        candles = [candle(i, 100, 101, 99, 100 + i / 10) for i in range(90)]
        # make the first eligible fact exactly neutral
        candles[69] = candle(69, candles[59].close, candles[59].close, candles[59].close, candles[59].close)
        signals, coverage = build_signals("IMOEXF", candles, candles[-1].day + timedelta(days=1))
        self.assertEqual(signals[0].index, 59)
        self.assertEqual(signals[-1].index, 79)
        self.assertEqual(signals[0].fact, "NEUTRAL")
        self.assertFalse(signals[0].direction_correct)
        self.assertEqual(coverage["no_full_horizon"], 10)

    def test_html_contains_audit_method_and_warnings(self):
        candles = [candle(i, 100, 101, 99, 100 + i / 10) for i in range(90)]
        signals = {}
        coverage = {}
        scenarios = []
        manifests = {}
        as_of = candles[-1].day + timedelta(days=1)
        for ticker in ("IMOEXF", "USDRUBF"):
            signals[ticker], coverage[ticker] = build_signals(ticker, candles, as_of)
            manifests[ticker] = {"csv_sha256": "abc", "rows": 90, "first_day": "x", "last_day": "y", "fetched_at_msk": "z", "request_urls": ["https://iss.moex.com/x"]}
            for signal in signals[ticker]:
                for variant in ("close_t", "next_open"):
                    for multiple in (1.0, 1.5, 2.0):
                        scenarios.append(evaluate_scenario(signal, candles, variant, multiple))
        audit = {"generated_at_msk": "now", "script_sha256": "def", "dependency_sha256": "ghi", "python_version": "test", "command": "python test.py"}
        document = render_html(signals, scenarios, coverage, manifests, audit, as_of)
        for text in ("STOP_FIRST/ambiguous", "t[0::10]", "без p-value", "комиссии", "SHA-256 генератора", "Точная команда", "Нейтральный факт", "open t+1"):
            self.assertIn(text, document)


if __name__ == "__main__":
    unittest.main()
