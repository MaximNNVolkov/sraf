import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from scripts.moex_direction_backtest import (
    BacktestError,
    DailyCandle,
    WINDOW,
    candidate_forecasts,
    derived_seed,
    evaluate_candidates,
    forecast_from_window,
    read_cache,
    render_html,
    run,
    sample_candidates,
    wilson_interval,
)


def series(count=100, start=date(2024, 1, 1)):
    out = []
    price = 100.0
    for index in range(count):
        price += 1 if index % 7 != 0 else -0.25
        out.append(DailyCandle(start + timedelta(days=index), price, price + 1, price - 1, price, 10))
    return out


class ForecastTest(unittest.TestCase):
    def test_uses_exactly_prior_window(self):
        candles = series(WINDOW + 2)
        direction, features = forecast_from_window(candles[:WINDOW])
        changed = list(candles)
        future = changed[-1]
        changed[-1] = DailyCandle(future.day, 1, 1, 1, 1, 1)
        self.assertEqual((direction, features), forecast_from_window(changed[:WINDOW]))

    def test_requires_twenty_bars(self):
        with self.assertRaises(ValueError):
            forecast_from_window(series(WINDOW - 1))

    def test_fact_is_attached_only_after_sampling(self):
        candles = series(45)
        as_of = candles[-1].day + timedelta(days=1)
        candidates, exclusions = candidate_forecasts("IMOEXF", candles, candles[20].day, as_of)
        self.assertTrue(candidates)
        first = candidates[0]
        self.assertFalse(hasattr(first, "actual"))
        checks = evaluate_candidates([first], candles, as_of)
        by_day = {c.day: c for c in candles}
        self.assertEqual(checks[0].next_close, by_day[checks[0].fact_day].close)
        self.assertEqual(exclusions["no_completed_fact"], 1)

    def test_neutral_actual_is_retained_as_incorrect(self):
        candles = series(50)
        as_of = candles[-1].day + timedelta(days=1)
        candidates, _ = candidate_forecasts("IMOEXF", candles, candles[20].day, as_of)
        chosen = candidates[3]
        changed = list(candles)
        next_candle = changed[chosen.candle_index + 1]
        changed[chosen.candle_index + 1] = DailyCandle(
            next_candle.day,
            chosen.close,
            chosen.close,
            chosen.close,
            chosen.close,
            next_candle.volume,
        )
        same_candidates, _ = candidate_forecasts("IMOEXF", changed, candles[20].day, as_of)
        same_chosen = next(c for c in same_candidates if c.forecast_day == chosen.forecast_day)
        self.assertEqual(chosen, same_chosen)
        check = evaluate_candidates([same_chosen], changed, as_of)[0]
        self.assertEqual(check.actual, "NEUTRAL")
        self.assertFalse(check.correct)

    def test_sampling_is_seeded_and_minimum_enforced(self):
        candles = series(100)
        candidates, _ = candidate_forecasts("IMOEXF", candles, candles[20].day, candles[-1].day + timedelta(days=1))
        a = sample_candidates(candidates, 20, 123)
        b = sample_candidates(candidates, 20, 123)
        self.assertEqual(a, b)
        self.assertEqual(len({c.forecast_day for c in a}), 20)
        with self.assertRaises(BacktestError):
            sample_candidates(candidates, 19, 123)

    def test_ticker_seeds_are_distinct_and_reproducible(self):
        self.assertEqual(derived_seed(42, "IMOEXF"), derived_seed(42, "IMOEXF"))
        self.assertNotEqual(derived_seed(42, "IMOEXF"), derived_seed(42, "USDRUBF"))

    def test_fact_candle_must_precede_as_of(self):
        candles = series(50)
        as_of = candles[-1].day
        candidates, _ = candidate_forecasts("IMOEXF", candles, candles[20].day, as_of)
        checks = evaluate_candidates(candidates, candles, as_of)
        self.assertTrue(checks)
        self.assertTrue(all(check.fact_day < as_of for check in checks))

    def test_future_as_of_is_rejected_before_io(self):
        tomorrow = datetime.now(ZoneInfo("Europe/Moscow")).date() + timedelta(days=1)
        args = SimpleNamespace(
            tests=30,
            as_of=tomorrow.isoformat(),
            seed=1,
            data_dir="unused",
            output="unused",
            offline=True,
        )
        with self.assertRaisesRegex(BacktestError, "позже текущей московской даты"):
            run(args)

    def test_wilson_known_case(self):
        low, high = wilson_interval(15, 30)
        self.assertAlmostEqual(low, 0.3315, places=3)
        self.assertAlmostEqual(high, 0.6685, places=3)


class CacheAndHtmlTest(unittest.TestCase):
    def test_cache_checksum_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "IMOEXF_daily.csv"
            csv_path.write_text("day,open,high,low,close,volume,begin,end\n2024-01-01,1,2,0.5,1.5,10,,\n", encoding="utf-8")
            manifest = {"csv_sha256": "bad"}
            (root / "IMOEXF_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(BacktestError):
                read_cache(root, "IMOEXF")
            manifest["csv_sha256"] = hashlib.sha256(csv_path.read_bytes()).hexdigest()
            (root / "IMOEXF_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            candles, _ = read_cache(root, "IMOEXF")
            self.assertEqual(candles[0].close, 1.5)

    def test_html_contains_method_dates_ci_and_details(self):
        candles = series(100)
        selected = {}
        metadata = {}
        exclusions = {}
        eligible_counts = {}
        seeds = {}
        for ticker in ("IMOEXF", "USDRUBF"):
            as_of = candles[-1].day + timedelta(days=1)
            candidates, excluded = candidate_forecasts(ticker, candles, candles[20].day, as_of)
            seeds[ticker] = derived_seed(42, ticker)
            sampled = sample_candidates(candidates, 20, seeds[ticker])
            selected[ticker] = evaluate_candidates(sampled, candles, as_of)
            metadata[ticker] = {
                "first_day": candles[0].day.isoformat(), "last_day": candles[-1].day.isoformat(),
                "rows": len(candles), "csv_sha256": "abc", "fetched_at_msk": "2026-01-01T00:00:00+03:00",
                "request_urls": ["https://iss.moex.com/iss/example"],
            }
            exclusions[ticker] = excluded
            eligible_counts[ticker] = len(candidates)
        audit = {
            "generated_at_msk": "2026-01-01T00:00:01+03:00",
            "script_sha256": "def",
            "python_version": "CPython test",
            "command": "python script.py --as-of 2026-01-01",
        }
        document = render_html(
            selected, metadata, exclusions, eligible_counts, seeds,
            candles[-1].day + timedelta(days=1), candles[20].day, 42, audit,
        )
        self.assertTrue(document.startswith("<!doctype html>"))
        for text in (
            "95% ДИ Уилсона", "основной seed <strong>42</strong>", "Derived seed",
            "Eligible candidates до random sample", "нейтрально", "fetched-at",
            "MOEX ISS request URLs", "SHA-256 скрипта", "CPython test", "Точная команда",
            "без совместного ДИ", "Дата t", "Close t+1", "Торговые операции не выполнялись",
        ):
            self.assertIn(text, document)
        self.assertGreaterEqual(document.count("<tbody>"), 2)


if __name__ == "__main__":
    unittest.main()
