import hashlib
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from scripts.moex_medium_term_trend_backtest import (
    BacktestError, DailyCandle, HORIZON, WINDOW, candidate_forecasts,
    derived_seed, evaluate_candidates, forecast_from_window, read_cache,
    render_html, sample_candidates, wilson_interval,
)


def series(count=180, start=date(2024, 1, 1)):
    result = []
    price = 100.0
    for index in range(count):
        price += 1.0 if index % 9 else -0.4
        result.append(DailyCandle(start + timedelta(days=index), price, price + 1, price - 1, price, 10))
    return result


class MediumTermLogicTest(unittest.TestCase):
    def test_forecast_uses_exactly_sixty_bars_through_t(self):
        candles = series(WINDOW + 3)
        expected = forecast_from_window(candles[:WINDOW])
        changed = list(candles)
        future = changed[WINDOW]
        changed[WINDOW] = DailyCandle(future.day, 1, 1, 1, 1, 1)
        self.assertEqual(expected, forecast_from_window(changed[:WINDOW]))
        with self.assertRaises(ValueError):
            forecast_from_window(candles[:WINDOW - 1])

    def test_last_ten_sessions_are_excluded_by_index(self):
        candles = series(150)
        as_of = candles[-1].day + timedelta(days=1)
        candidates, excluded = candidate_forecasts("IMOEXF", candles, candles[60].day, as_of)
        self.assertEqual(excluded["no_completed_fact"], HORIZON)
        self.assertEqual(candidates[-1].candle_index, len(candles) - HORIZON - 1)

    def test_t_plus_ten_price_is_attached_only_after_selection(self):
        candles = series(180)
        as_of = candles[-1].day + timedelta(days=1)
        candidates, _ = candidate_forecasts("IMOEXF", candles, candles[60].day, as_of)
        self.assertFalse(hasattr(candidates[0], "fact_close"))
        chosen = sample_candidates(candidates, 30, 123)
        target = chosen[0]
        changed = list(candles)
        fact = changed[target.candle_index + HORIZON]
        changed[target.candle_index + HORIZON] = DailyCandle(fact.day, 500, 500, 500, 500, fact.volume)
        candidates_after, _ = candidate_forecasts("IMOEXF", changed, candles[60].day, as_of)
        # The changed candle is after this candidate's t, so its forecast-only
        # record cannot change. It may legitimately affect later forecast dates.
        same_date = next(item for item in candidates_after if item.forecast_day == target.forecast_day)
        self.assertEqual(target, same_date)
        check = evaluate_candidates([target], changed, as_of)[0]
        self.assertEqual(check.fact_close, 500)

    def test_neutral_fact_is_retained_and_incorrect(self):
        candles = series(180)
        as_of = candles[-1].day + timedelta(days=1)
        candidates, _ = candidate_forecasts("IMOEXF", candles, candles[60].day, as_of)
        chosen = candidates[5]
        changed = list(candles)
        fact = changed[chosen.candle_index + HORIZON]
        changed[chosen.candle_index + HORIZON] = DailyCandle(fact.day, chosen.close, chosen.close, chosen.close, chosen.close, fact.volume)
        check = evaluate_candidates([chosen], changed, as_of)[0]
        self.assertEqual(check.actual, "NEUTRAL")
        self.assertFalse(check.correct)

    def test_sampling_and_ticker_seeds_are_reproducible(self):
        candles = series(180)
        candidates, _ = candidate_forecasts("IMOEXF", candles, candles[60].day, candles[-1].day + timedelta(days=1))
        self.assertEqual(sample_candidates(candidates, 30, 7), sample_candidates(candidates, 30, 7))
        self.assertNotEqual(derived_seed(42, "IMOEXF"), derived_seed(42, "USDRUBF"))
        with self.assertRaises(BacktestError):
            sample_candidates(candidates, 19, 7)

    def test_wilson_known_case(self):
        low, high = wilson_interval(15, 30)
        self.assertAlmostEqual(low, 0.3315, places=3)
        self.assertAlmostEqual(high, 0.6685, places=3)


class CacheAndReportTest(unittest.TestCase):
    def test_cache_checksum_is_mandatory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "IMOEXF_daily.csv"
            csv_path.write_text("day,open,high,low,close,volume,begin,end\n2024-01-01,1,2,0.5,1.5,10,,\n", encoding="utf-8")
            manifest_path = root / "IMOEXF_manifest.json"
            manifest_path.write_text(json.dumps({"csv_sha256": "bad"}), encoding="utf-8")
            with self.assertRaises(BacktestError):
                read_cache(root, "IMOEXF")
            manifest_path.write_text(json.dumps({"csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest()}), encoding="utf-8")
            self.assertEqual(read_cache(root, "IMOEXF")[0][0].close, 1.5)

    def test_html_has_all_rows_method_provenance_and_warning(self):
        candles = series(180)
        as_of = candles[-1].day + timedelta(days=1)
        selected, metadata, exclusions, eligible, seeds = {}, {}, {}, {}, {}
        for ticker in ("IMOEXF", "USDRUBF"):
            candidates, excluded = candidate_forecasts(ticker, candles, candles[60].day, as_of)
            seeds[ticker] = derived_seed(42, ticker)
            selected[ticker] = evaluate_candidates(sample_candidates(candidates, 30, seeds[ticker]), candles, as_of)
            exclusions[ticker], eligible[ticker] = excluded, len(candidates)
            metadata[ticker] = {"first_day": "2024-01-01", "last_day": "2024-06-28", "rows": 180, "fetched_at_msk": "2026-01-01T00:00:00+03:00", "csv_sha256": "abc", "request_urls": ["https://iss.moex.com/example"]}
        audit = {"data_mode": "офлайн", "generated_at_msk": "2026-01-01T00:00:00+03:00", "python_version": "CPython test", "command": "python test.py", "script_sha256": "def"}
        document = render_html(selected, metadata, exclusions, eligible, seeds, as_of, candles[60].day, 42, audit)
        for text in ("горизонт 10 торговых сессий", "только 60 завершённых close", "Последние ровно 10", "до факта", "95% ДИ Уилсона", "fetched-at", "SHA-256 CSV", "SHA-256 скрипта", "Команда", "не торговая стратегия", "все 30 наблюдений", "Доходность 10 сессий"):
            self.assertIn(text, document)
        self.assertEqual(document.count("<tbody>"), 2)
        self.assertEqual(document.count("<tr>") - 2, 60)


if __name__ == "__main__":
    unittest.main()
