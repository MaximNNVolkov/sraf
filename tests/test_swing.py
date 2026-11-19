"""Regression tests for market data, swing plans and scheduled state recovery."""
import sys
import tempfile
import sqlite3
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from usdrubf.moex import Candle, MSK, Quote
from usdrubf.tbank import TBankClient, parse_time
from usdrubf.analysis import snapshot
from usdrubf.process import STRATEGY_VERSION, collect_compact, plan_for, replay_plan
from usdrubf.trading import Decision, SetupState, TradePlan, connect, load_plan, save_plan
from tinkoff_positions import relevant_orders
import moex_monitor_tf as monitor
import moex_morning_structured as morning
import moex_day_digest as digest

T0 = datetime(2026, 11, 12, 8, 7, tzinfo=MSK)


def candle(hour, close=100.1, high=101, low=99.5, complete=True):
    return Candle(T0.replace(minute=0) + timedelta(hours=hour), 100, high, low, close, 10, complete)


class FakeBank(TBankClient):
    def __init__(self):
        super().__init__(token="test-only")
        self._instrument = {"uid": "test", "classCode": "SPBFUT", "instrumentType": "futures",
                            "minPriceIncrement": {"units": "0", "nano": 10000000}}
        self.requests = []

    def _call(self, service, payload):
        self.requests.append((service, payload))
        if service.endswith("GetCandles"):
            return {"candles": [{"time": payload["from"], "isComplete": False,
                    **{k: {"units": "100", "nano": 0} for k in ("open", "high", "low", "close")}, "volume": "2"}]}
        return {"lastPrices": [{"time": "2026-11-12T08:00:00.123456789Z", "price": {"units": "100"},
                                 "lastPriceType": "LAST_PRICE_EXCHANGE"}]}


class BankTest(unittest.TestCase):
    def test_nanosecond_utc_timestamp_on_python310(self):
        dt = parse_time("2026-11-12T08:00:00.123456789Z")
        self.assertEqual((dt.hour, dt.microsecond), (11, 123456))

    def test_quote_explicitly_requests_exchange_price(self):
        client = FakeBank()
        quote = client.get_quote()
        self.assertEqual((quote.last, quote.source), (100, "T-Invest API"))
        self.assertEqual(client.requests[0][1]["lastPriceType"], "LAST_PRICE_EXCHANGE")

    def test_history_chunking_native_completion_and_exchange_source(self):
        client = FakeBank()
        bars = client.get_candles(240, T0, T0 + timedelta(days=130))
        self.assertEqual(len(client.requests), 3)
        self.assertEqual(len(bars), 3)
        self.assertFalse(bars[-1].is_complete)
        self.assertTrue(all(p["candleSourceType"] == "CANDLE_SOURCE_EXCHANGE" for _, p in client.requests))

    def test_active_regular_orders_are_not_lost(self):
        orders = [{"ticker": "USDRUBF", "executionReportStatus": s} for s in
                  ("EXECUTION_REPORT_STATUS_NEW", "EXECUTION_REPORT_STATUS_PARTIALLYFILL", "EXECUTION_REPORT_STATUS_FILL")]
        self.assertEqual(len(relevant_orders({"orders": orders}, "orders")), 2)

    def test_collector_excludes_incomplete_candles_on_every_timeframe(self):
        class Client:
            tick_size = 0.01

            def get_candles(self, interval, start, end):
                step = {60: timedelta(hours=1), 240: timedelta(hours=4),
                        24: timedelta(days=1), 7: timedelta(weeks=1)}[interval]
                bars = [Candle(T0 - step * (70 - i), 100, 101, 99, 100, 1) for i in range(70)]
                return bars + [Candle(T0, 100, 999, 1, 999, 1, False)]

            def get_quote(self):
                return Quote("X", "SPBFUT", 100, None, None, None, None, None, None, None,
                             None, None, T0, T0, T0.date().isoformat(), source="T-Invest API")

            def trading_status(self):
                return {"tradingStatus": "SECURITY_TRADING_STATUS_NORMAL_TRADING"}

        payload, h1, d1 = collect_compact("X", now=T0, client=Client())
        self.assertFalse(payload["stale"])
        for tf in ("w1", "d1", "h4", "h1"):
            self.assertEqual(payload[tf]["bars"], 70)
            self.assertEqual(payload[tf]["close"], 100)
        self.assertTrue(all(c.is_complete for c in h1.candles + d1.candles))


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.db = connect(":memory:")
        self.plan = TradePlan("X", Decision.LONG_SETUP, "test", entry=100, stop=95, tp1=110, tp2=120,
                              retest_required=True, created_at=T0.isoformat(), strategy_version=STRATEGY_VERSION,
                              expires_at=(T0 + timedelta(days=1)).isoformat(), holding_period="3–20 дней")
        self.pid = save_plan(self.db, self.plan, T0.date().isoformat())

    def tearDown(self):
        self.db.close()

    def replay(self, bars, state=SetupState.WAITING, now=None):
        return replay_plan(self.db, self.pid, self.plan, state, bars, now or T0 + timedelta(hours=12))

    def test_two_hour_gap_replays_both_hours_and_no_preentry_target(self):
        bars = [candle(1), candle(2, high=125)]
        state = self.replay(bars)
        self.assertEqual(state, SetupState.ACTIVE)
        self.assertEqual(self.db.execute("select count(*) from plan_events").fetchone()[0], 3)
        # An already consumed candle cannot be credited as a post-entry target.
        self.assertEqual(self.replay(bars, state), SetupState.ACTIVE)
        self.assertEqual(self.replay(bars + [candle(3, high=121)], state), SetupState.TP2)

    def test_unfinished_and_precreation_bars_cannot_confirm(self):
        self.assertEqual(self.replay([candle(0), candle(1, complete=False)]), SetupState.WAITING)

    def test_retest_requires_later_hour(self):
        self.assertEqual(self.replay([candle(1)]), SetupState.HOURLY_CONFIRMED)

    def test_preentry_stop_invalidates_plan(self):
        self.assertEqual(self.replay([candle(1, low=94)]), SetupState.CANCELLED)

    def test_ambiguous_hour_uses_stop_priority(self):
        bars = [candle(1), candle(2), candle(3, low=94, high=125)]
        self.assertEqual(self.replay(bars), SetupState.STOPPED)

    def test_entry_expires_but_active_scenario_survives_next_day(self):
        bars = [candle(1), candle(2)]
        state = self.replay(bars)
        self.assertEqual(state, SetupState.ACTIVE)
        self.assertEqual(self.replay(bars, state, T0 + timedelta(days=3)), SetupState.ACTIVE)
        self.assertEqual(self.replay([], SetupState.WAITING, T0 + timedelta(days=3)), SetupState.CANCELLED)

    def test_do_not_chase_when_real_confirmation_price_loses_rr(self):
        self.assertEqual(self.replay([candle(1), candle(2, close=109, high=110)]), SetupState.CANCELLED)

    def test_repeated_save_preserves_state_and_levels(self):
        state = self.replay([candle(1), candle(2)])
        different = TradePlan("X", Decision.NO_TRADE, "changed")
        save_plan(self.db, different, T0.date().isoformat())
        _, saved, loaded_state = load_plan(self.db, "X", T0.date().isoformat())
        self.assertEqual((saved.entry, loaded_state), (100, state))
        with self.assertRaises(ValueError):
            save_plan(self.db, different, T0.date().isoformat(), replace=True)

    def test_evening_uses_saved_state_and_historical_day_not_future_events(self):
        self.plan.ticker = "IMOEXF"
        self.pid = save_plan(self.db, self.plan, T0.date().isoformat())
        self.replay([candle(1), candle(2)])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.db"
            copy = sqlite3.connect(path)
            self.db.backup(copy)
            copy.close()
            text = "\n".join(digest.render_saved_plan(T0.date().isoformat(), path))
            self.assertIn("ACTIVE", text)
            self.assertIn("не фактические сделки", text)
            before = "\n".join(digest.render_saved_plan((T0 - timedelta(days=1)).date().isoformat(), path))
            self.assertNotIn("ACTIVE", before)

    def test_short_is_symmetric(self):
        self.plan = TradePlan("X", Decision.SHORT_SETUP, "test", entry=100, stop=105, tp1=90, tp2=80,
                              created_at=T0.isoformat(), strategy_version=STRATEGY_VERSION, retest_required=True)
        self.pid = save_plan(self.db, self.plan, T0.date().isoformat(), replace=True)
        self.assertEqual(self.replay([candle(1, close=99.9), candle(2, close=99.9)]), SetupState.ACTIVE)


class PlanTest(unittest.TestCase):
    def context(self):
        h1 = snapshot("1ч", [candle(i, high=102, low=98) for i in range(60)])
        d1 = snapshot("1д", [candle(i, high=102, low=98) for i in range(60)])
        d1.atr14 = 4
        trend = {"close": 100, "ma50": 95, "ma50_previous": 94, "atr14": 4,
                 "supports": [{"price": 99}, {"price": 96}],
                 "resistances": [{"price": 105}, {"price": 112}]}
        payload = {"ticker": "X", "generated_at": T0.isoformat(), "stale": False, "price": 100,
                   "tick_size": 0.5, "h4": dict(trend), "d1": dict(trend),
                   "w1": {"close": 100, "ma20": 95, "ma20_previous": 94}}
        return payload, h1, d1

    def test_swing_pullback_uses_structural_stop_with_daily_atr(self):
        payload, h, d = self.context()
        plan = plan_for(payload, h, d)
        self.assertEqual((plan.direction, plan.setup_type, plan.entry, plan.stop), ("LONG", "pullback", 99, 95))
        self.assertGreaterEqual(plan.rr_tp2, 2)
        self.assertEqual(plan.entry / 0.5, round(plan.entry / 0.5))

    def test_weekly_opposition_or_open_position_blocks_entry(self):
        payload, h, d = self.context()
        self.assertEqual(plan_for(payload, h, d, True).decision, Decision.NO_TRADE)
        payload["w1"] = {"close": 90, "ma20": 95, "ma20_previous": 96}
        self.assertEqual(plan_for(payload, h, d).decision, Decision.NO_TRADE)

    def test_old_candle_history_blocks_otherwise_valid_plan(self):
        payload, h, d = self.context()
        payload["history_issues"] = ["Недостаточно свежая история 4ч"]
        self.assertEqual(plan_for(payload, h, d).decision, Decision.NO_TRADE)

    def test_closed_session_reference_can_prepare_morning_plan(self):
        payload, h, d = self.context()
        payload.update(stale=True, delay_minutes=600, market_open=False,
                       trading_status="SECURITY_TRADING_STATUS_NOT_AVAILABLE_FOR_TRADING")
        self.assertEqual(plan_for(payload, h, d).direction, "LONG")
        payload["market_open"] = True
        self.assertEqual(plan_for(payload, h, d).decision, Decision.NO_TRADE)

    def test_no_fabricated_targets_when_structure_is_missing(self):
        payload, h, d = self.context()
        payload["h4"]["resistances"] = payload["d1"]["resistances"] = [{"price": 105}]
        self.assertEqual(plan_for(payload, h, d).decision, Decision.NO_TRADE)

    def test_wrong_side_tp1_rejected(self):
        plan = TradePlan("X", Decision.LONG_SETUP, "test", entry=100, stop=95, tp1=90, tp2=120)
        self.assertEqual(plan.decision, Decision.NO_TRADE)


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        self.db = connect(":memory:")
        self.broker = {"positions": [], "orders": [], "stop_orders": []}
        self.context = PlanTest().context()
        self.collect = patch.object(monitor, "collect_compact", return_value=self.context)
        self.collect.start()

    def tearDown(self):
        self.collect.stop()
        self.db.close()

    def test_monitor_does_not_create_new_plan(self):
        payload = monitor.monitor_ticker("X", self.db, self.broker)
        self.assertEqual(payload["market_setup"]["state"], "NO_MORNING_PLAN")
        self.assertEqual(self.db.execute("select count(*) from plans").fetchone()[0], 0)

    def test_cancelled_waiting_plan_creates_fresh_context(self):
        payload, _, d1 = self.context
        h1 = snapshot("1ч", [candle(-2, low=94)])
        plan = TradePlan("X", Decision.LONG_SETUP, "test", entry=100, stop=95, tp1=110, tp2=120,
                         created_at=(T0 - timedelta(hours=3)).isoformat(), strategy_version=STRATEGY_VERSION,
                         expires_at=(T0 + timedelta(days=1)).isoformat(), author="agent")
        save_plan(self.db, plan, T0.date().isoformat())
        with patch.object(monitor, "collect_compact", return_value=(payload, h1, d1)):
            result = monitor.monitor_ticker("X", self.db, self.broker)
        self.assertEqual(result["market_setup"]["state"], "AWAITING_AGENT")
        self.assertEqual(result["agent_handoff"]["task"], "CREATE_PLAN")
        self.assertEqual(result["replacement_for"]["state"], "CANCELLED")

    def test_expired_pending_replacement_is_refreshed_instead_of_hanging(self):
        payload, h1, d1 = self.context
        plan = TradePlan("X", Decision.LONG_SETUP, "test", entry=100, stop=95,
                         tp1=110, tp2=120, created_at=T0.isoformat(),
                         strategy_version=STRATEGY_VERSION,
                         expires_at=(T0 + timedelta(days=1)).isoformat(), author="agent")
        pid = save_plan(self.db, plan, T0.date().isoformat())
        self.db.execute("UPDATE plans SET state='CANCELLED' WHERE plan_id=?", (pid,))
        with patch.object(monitor, "collect_compact", return_value=(payload, h1, d1)):
            first = monitor.monitor_ticker("X", self.db, self.broker)
        first_context = first["agent_handoff"]["context_id"]

        later = dict(payload)
        later["generated_at"] = (T0 + timedelta(minutes=61)).isoformat()
        with patch.object(monitor, "collect_compact", return_value=(later, h1, d1)):
            second = monitor.monitor_ticker("X", self.db, self.broker)

        self.assertEqual(second["market_setup"]["state"], "AWAITING_AGENT")
        self.assertNotEqual(second["agent_handoff"]["context_id"], first_context)
        statuses = self.db.execute(
            "SELECT context_id,status FROM analysis_contexts ORDER BY rowid"
        ).fetchall()
        self.assertEqual([(r["context_id"], r["status"]) for r in statuses],
                         [(first_context, "EXPIRED"),
                          (second["agent_handoff"]["context_id"], "PENDING")])

    def test_cancelled_plan_does_not_create_replacement_when_broker_blocks_entry(self):
        payload, h1, d1 = self.context
        plan = TradePlan("X", Decision.LONG_SETUP, "test", entry=100, stop=95,
                         tp1=110, tp2=120, created_at=T0.isoformat(),
                         strategy_version=STRATEGY_VERSION, author="agent")
        pid = save_plan(self.db, plan, T0.date().isoformat())
        self.db.execute("UPDATE plans SET state='CANCELLED' WHERE plan_id=?", (pid,))
        self.broker["positions"] = [{"ticker": "X", "lots": 1}]
        with patch.object(monitor, "collect_compact", return_value=(payload, h1, d1)):
            result = monitor.monitor_ticker("X", self.db, self.broker)
        self.assertEqual(result["market_setup"]["state"], "CANCELLED")
        self.assertEqual(self.db.execute("SELECT count(*) FROM analysis_contexts").fetchone()[0], 0)

    def test_broker_position_and_pending_orders_remain_separate(self):
        self.broker["positions"] = [{"ticker": "X", "lots": 1}]
        self.broker["orders"] = [{"ticker": "X"}]
        result = monitor.monitor_ticker("X", self.db, self.broker)
        self.assertEqual(result["position_status"], "OPEN_AT_BROKER")
        self.assertTrue(result["new_entry_blocked"])

    def test_morning_preserves_active_previous_day_plan(self):
        payload, h, d = self.context
        plan = plan_for(payload, h, d)
        plan.created_at = (T0 - timedelta(days=1)).isoformat()
        pid = save_plan(self.db, plan, (T0 - timedelta(days=1)).date().isoformat())
        self.db.execute("update plans set state='ACTIVE',last_candle_time=? where plan_id=?",
                        (h.candles[-1].begin.isoformat(), pid))
        self.db.commit()
        with patch.object(morning, "collect_compact", return_value=self.context):
            result = morning.morning_ticker("X", self.db, self.broker)
        self.assertTrue(result["carried_from_previous_day"])
        self.assertEqual(result["market_setup"]["plan_id"], pid)


if __name__ == "__main__":
    unittest.main()
