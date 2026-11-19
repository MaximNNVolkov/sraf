import unittest
from datetime import date, timedelta
from html.parser import HTMLParser

from scripts.moex_medium_term_trend_backtest import DailyCandle, candidate_forecasts, evaluate_candidates
from scripts.moex_medium_term_walkforward_validation import (
    PRIMARY_PHASE, build_phases, calendar_midpoint, parse_args, render_html,
    score_methods, split_primary,
)


def series(count=190, start=date(2024, 1, 1), flat_fact=False):
    candles = []
    price = 100.0
    for index in range(count):
        price += 0 if flat_fact else (1.0 if index % 9 else -0.4)
        candles.append(DailyCandle(start + timedelta(days=index), price, price + 1, price - 1, price, 10))
    return candles


def checks_for(candles):
    as_of = candles[-1].day + timedelta(days=1)
    candidates, excluded = candidate_forecasts("IMOEXF", candles, candles[60].day, as_of)
    return evaluate_candidates(candidates, candles, as_of), excluded, as_of


class StrictHTMLParser(HTMLParser):
    def error(self, message):  # pragma: no cover
        raise AssertionError(message)


class WalkForwardLogicTest(unittest.TestCase):
    def test_ten_phases_are_deterministic_non_overlapping_and_partition_eligible(self):
        checks, _, _ = checks_for(series())
        phases = build_phases(checks)
        self.assertEqual([item.offset for item in phases], list(range(10)))
        self.assertEqual(phases[PRIMARY_PHASE].checks, tuple(checks[0::10]))
        self.assertEqual(sum(len(item.checks) for item in phases), len(checks))
        for phase in phases:
            for left, right in zip(phase.checks, phase.checks[1:]):
                self.assertEqual(left.fact_day, right.forecast_day)

    def test_neutral_is_wrong_and_reduces_coin_expectation(self):
        checks, _, _ = checks_for(series(flat_fact=True))
        scores = score_methods(checks[:12])
        self.assertTrue(all(scores[key].correct == 0 for key in scores))
        self.assertTrue(all(item.actual == "NEUTRAL" for item in checks[:12]))

    def test_calendar_split_is_strict_and_exhaustive(self):
        checks, _, as_of = checks_for(series())
        primary = build_phases(checks)[0].checks
        boundary = calendar_midpoint(checks[0].forecast_day, as_of)
        periods = split_primary(primary, boundary)
        self.assertTrue(all(item.forecast_day < boundary for item in periods["early"]))
        self.assertTrue(all(item.forecast_day >= boundary for item in periods["late"]))
        self.assertEqual(periods["early"] + periods["late"], primary)

    def test_separate_report_defaults_have_no_tuning_or_refresh_options(self):
        args = parse_args([])
        self.assertEqual(args.output, "reports/moex_medium_term_walkforward_validation.html")
        self.assertFalse(hasattr(args, "seed"))
        self.assertFalse(hasattr(args, "refresh"))


class WalkForwardReportTest(unittest.TestCase):
    def test_html_records_phases_periods_dates_exclusions_and_audit(self):
        candles = series()
        checks, excluded, as_of = checks_for(candles)
        phases = build_phases(checks)
        start = candles[60].day
        boundary = calendar_midpoint(start, as_of)
        periods = split_primary(phases[0].checks, boundary)
        phase_map = {ticker: phases for ticker in ("IMOEXF", "USDRUBF")}
        period_map = {ticker: periods for ticker in phase_map}
        metadata = {ticker: {
            "source": "MOEX ISS", "first_day": "2024-01-01", "last_day": "2024-07-08",
            "rows": 190, "csv_sha256": "abc", "fetched_at_msk": "2026-01-01T00:00:00+03:00",
            "request_urls": ["https://iss.moex.com/example"],
        } for ticker in phase_map}
        audit = {"data_mode": "офлайн", "generated_at_msk": "2026-01-01T00:00:00+03:00",
                 "python_version": "CPython test", "command": "python test.py", "script_sha256": "def"}
        document = render_html(phase_map, period_map, metadata,
                               {ticker: excluded for ticker in phase_map},
                               {ticker: len(checks) for ticker in phase_map}, start, as_of, boundary, audit)
        for required in (
            "eligible[0::10]", "все 10 фаз", "не объединяются", "Ранний", "Поздний",
            "Always UP", "Always DOWN", "Ожидание монеты", "NEUTRAL", "Wilson 95%",
            "только описательно", "CSV SHA-256", "SHA-256 скрипта", "MOEX ISS URLs",
            "Брокер, заявки, сеть и cron не использовались", str(boundary),
            str(phases[0].checks[0].forecast_day), str(phases[0].checks[-1].fact_day),
        ):
            self.assertIn(required, document)
        # Wilson numeric intervals occur only in the two phase-0 model cards.
        self.assertEqual(document.count("Описательный Wilson 95%:"), 2)
        parser = StrictHTMLParser()
        parser.feed(document)
        parser.close()


if __name__ == "__main__":
    unittest.main()
