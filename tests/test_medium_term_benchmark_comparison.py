import hashlib
import json
import tempfile
import unittest
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

from scripts.moex_medium_term_benchmark_comparison import (
    METHODS,
    coin_forecasts,
    coin_seed,
    parse_args,
    render_html,
    summarize_methods,
)
from scripts.moex_medium_term_trend_backtest import (
    DailyCandle,
    candidate_forecasts,
    evaluate_candidates,
)


def series(count=180, start=date(2024, 1, 1)):
    candles = []
    price = 100.0
    for index in range(count):
        price += 1.0 if index % 9 else -0.4
        candles.append(DailyCandle(start + timedelta(days=index), price, price + 1, price - 1, price, 10))
    return candles


class StrictHTMLParser(HTMLParser):
    def error(self, message):  # pragma: no cover - required by old Python
        raise AssertionError(message)


class BenchmarkLogicTest(unittest.TestCase):
    def test_coin_is_reproducible_and_ticker_specific(self):
        imoex_seed = coin_seed(42, "IMOEXF")
        self.assertEqual(coin_forecasts(100, imoex_seed), coin_forecasts(100, imoex_seed))
        self.assertNotEqual(imoex_seed, coin_seed(42, "USDRUBF"))
        self.assertEqual(len(coin_forecasts(23, imoex_seed)), 23)

    def test_every_eligible_date_is_evaluated(self):
        candles = series(180)
        as_of = candles[-1].day + timedelta(days=1)
        candidates, _ = candidate_forecasts("IMOEXF", candles, candles[60].day, as_of)
        checks = evaluate_candidates(candidates, candles, as_of)
        results = summarize_methods("IMOEXF", checks, coin_seed(42, "IMOEXF"))
        self.assertEqual(set(results), set(METHODS))
        self.assertTrue(all(item.total == len(candidates) for item in results.values()))
        self.assertEqual(results["always_up"].correct, results["model"].actual_up)
        self.assertEqual(results["always_down"].correct, results["model"].actual_down)

    def test_neutral_is_retained_and_wrong_for_all_binary_methods(self):
        candles = series(180)
        as_of = candles[-1].day + timedelta(days=1)
        candidates, _ = candidate_forecasts("IMOEXF", candles, candles[60].day, as_of)
        chosen = candidates[0]
        changed = list(candles)
        fact = changed[chosen.candle_index + 10]
        changed[chosen.candle_index + 10] = DailyCandle(fact.day, chosen.close, chosen.close, chosen.close, chosen.close, fact.volume)
        check = evaluate_candidates([chosen], changed, as_of)
        results = summarize_methods("IMOEXF", check, 7)
        self.assertTrue(all(item.actual_neutral == 1 and item.correct == 0 for item in results.values()))

    def test_default_output_is_separate_report(self):
        args = parse_args([])
        self.assertEqual(args.output, "reports/moex_medium_term_benchmark_comparison.html")
        self.assertFalse(hasattr(args, "refresh"))


class BenchmarkReportTest(unittest.TestCase):
    def test_report_contains_methods_caveats_and_audit(self):
        candles = series(180)
        as_of = candles[-1].day + timedelta(days=1)
        all_results, metadata, exclusions, bounds, seeds = {}, {}, {}, {}, {}
        for ticker in ("IMOEXF", "USDRUBF"):
            candidates, excluded = candidate_forecasts(ticker, candles, candles[60].day, as_of)
            checks = evaluate_candidates(candidates, candles, as_of)
            seed = coin_seed(42, ticker)
            all_results[ticker] = summarize_methods(ticker, checks, seed)
            metadata[ticker] = {
                "source": "MOEX ISS", "fetched_at_msk": "2026-01-01T00:00:00+03:00",
                "csv_sha256": "abc", "request_urls": ["https://iss.moex.com/example"],
            }
            exclusions[ticker], bounds[ticker], seeds[ticker] = excluded, (checks[0].forecast_day, checks[-1].forecast_day), seed
        audit = {
            "data_mode": "офлайн", "generated_at_msk": "2026-01-01T00:00:00+03:00",
            "python_version": "CPython test", "command": "python test.py", "script_sha256": "def",
        }
        document = render_html(all_results, metadata, exclusions, bounds, seeds, candles[60].day, as_of, 42, audit)
        for required in (
            "Always UP", "Always DOWN", "Bernoulli(0,5)", "50%", "все eligible-даты",
            "NEUTRAL", "перекрывающиеся 10-сессионные горизонты", "не являются независимыми",
            "нельзя трактовать", "Fetch timestamp", "CSV SHA-256", "SHA-256 скрипта", "Команда",
            "Cron и брокерские интерфейсы не использовались",
        ):
            self.assertIn(required, document)
        self.assertEqual(document.count("<tbody>"), 2)
        self.assertEqual(document.count("<tr>"), 12)  # 2 headers + 8 results + 2 audit rows
        parser = StrictHTMLParser()
        parser.feed(document)
        parser.close()

    def test_fixture_hash_helper_is_real_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.csv"
            path.write_bytes(b"x")
            manifest = {"csv_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            (Path(tmp) / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(manifest["csv_sha256"], "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881")


if __name__ == "__main__":
    unittest.main()
