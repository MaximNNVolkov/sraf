"""The agent owns decisions; the backend validates, persists and acknowledges."""
import copy
import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from usdrubf.agent import create_context, get_result, submit_decision
from usdrubf.moex import MSK
from usdrubf.process import STRATEGY_VERSION
from usdrubf.trading import Decision, TradePlan, connect, load_plan, save_plan
import moex_morning_structured as morning
import moex_monitor_tf as monitor

T0 = datetime(2026, 11, 16, 8, 7, tzinfo=MSK)


def evidence():
    return {"ticker": "IMOEXF", "generated_at": T0.isoformat(), "source": "T-Invest API",
            "price": 100, "tick_size": 0.5, "stale": False, "delay_minutes": 1,
            "market_open": True, "trading_status": "SECURITY_TRADING_STATUS_NORMAL_TRADING",
            "last_trade_time": T0.isoformat(), "history_issues": [], "new_entry_blocked": False,
            **{tf: {"atr14": 4, "recent_candles": [], "confirmed_pivots": [], "ma50": 120, "close": 100}
               for tf in ("w1", "d1", "h4", "h1")}}


def response(context_id, **changes):
    result = {"context_id": context_id, "ticker": "IMOEXF", "decision": "LONG_SETUP",
              "reason": "Агент видит восстановление после завершения коррекции",
              "analysis": {tf: "Обоснование по подтверждённым данным " + tf
                           for tf in ("w1", "d1", "h4", "h1", "invalidation")},
              "confidence": "medium", "setup_type": "breakout",
              "entry": 100, "stop": 95, "tp1": 105, "tp2": 115}
    result.update(changes)
    return result


class AgentProtocolTest(unittest.TestCase):
    def setUp(self):
        self.db = connect(":memory:")
        self.context = create_context(self.db, evidence())
        self.context_id = self.context["agent_handoff"]["context_id"]
        self.broker = {"positions": [], "orders": [], "stop_orders": []}

    def tearDown(self):
        self.db.close()

    def submit(self, data=None, **kw):
        return submit_decision(self.db, data or response(self.context_id), broker=self.broker,
                               market=evidence(), now=kw.pop("now", T0 + timedelta(minutes=2)), **kw)

    def test_context_does_not_make_a_trade_decision(self):
        self.assertIsNone(self.context["market_setup"]["decision"])
        self.assertEqual(self.context["market_setup"]["state"], "AWAITING_AGENT")
        self.assertEqual(self.db.execute("select count(*) from plans").fetchone()[0], 0)

    def test_accept_exact_agent_levels_even_when_legacy_ma_filter_would_block(self):
        result = self.submit()
        self.assertEqual(result["status"], "ACCEPTED")
        plan = result["market_setup"]
        self.assertEqual((plan["entry"], plan["stop"], plan["tp1"], plan["tp2"]), (100, 95, 105, 115))
        self.assertEqual((plan["author"], plan["strategy_version"]), ("agent", STRATEGY_VERSION))
        self.assertEqual(plan["analysis"]["d1"], response(self.context_id)["analysis"]["d1"])
        self.assertEqual(plan["created_at"], (T0 + timedelta(minutes=2)).isoformat())
        self.assertEqual(plan["state"], "WAITING")
        self.assertFalse(result["broker_orders_sent"])
        self.assertFalse(result["risk_checks"]["ruble_risk_verified"])
        self.assertEqual(self.db.execute("select count(*) from trades").fetchone()[0], 0)
        self.assertEqual(get_result(self.db, "IMOEXF")["market_setup"]["entry"], 100)

    def test_bad_rr_rejected_without_silent_no_trade(self):
        result = self.submit(response(self.context_id, tp2=109))
        self.assertEqual(result["status"], "REJECTED")
        self.assertTrue(any("R/R" in e for e in result["errors"]))
        self.assertEqual(self.db.execute("select count(*) from plans").fetchone()[0], 0)
        self.assertEqual(self.db.execute("select status from agent_feedback").fetchone()[0], "REJECTED")

    def test_wrong_price_order_step_and_nonfinite_values(self):
        for changes in ({"stop": 101}, {"tp1": 90}, {"entry": 100.1}, {"stop": float("nan")}, {"tp2": float("inf")}, {"entry": True}):
            with self.subTest(changes=changes):
                self.assertEqual(self.submit(response(self.context_id, **changes))["status"], "REJECTED")

    def test_agent_must_supply_multitimeframe_analysis_and_confidence(self):
        for changes in ({"analysis": {}}, {"reason": ""}, {"confidence": "certain"}, {"confidence": []}, {"decision": []}):
            self.assertEqual(self.submit(response(self.context_id, **changes))["status"], "REJECTED")

    def test_broker_recheck_blocks_position_opened_after_context(self):
        self.broker["positions"] = [{"ticker": "IMOEXF"}]
        self.assertEqual(self.submit()["status"], "REJECTED")
        self.broker["positions"] = []
        self.broker["orders"] = [{"ticker": "IMOEXF"}]
        self.assertEqual(self.submit()["status"], "REJECTED")

    def test_no_directional_acceptance_without_live_checks(self):
        result = submit_decision(self.db, response(self.context_id), now=T0)
        self.assertEqual(result["status"], "REJECTED")

    def test_expired_superseded_and_wrong_ticker_contexts_rejected(self):
        self.assertEqual(self.submit(now=T0 + timedelta(hours=2))["status"], "REJECTED")
        self.assertEqual(self.submit(response(self.context_id, ticker="USDRUBF"))["status"], "REJECTED")
        create_context(self.db, evidence())
        self.assertEqual(self.submit()["status"], "REJECTED")

    def test_repeat_returns_original_receipt_even_after_deadline(self):
        first = self.submit()
        result = submit_decision(self.db, response(self.context_id), now=T0 + timedelta(days=3))
        self.assertEqual(result["submission_id"], first["submission_id"])
        self.assertTrue(result["idempotent"])
        self.assertEqual(self.db.execute("select count(*) from plans").fetchone()[0], 1)
        self.assertEqual(self.db.execute("select count(*) from agent_feedback").fetchone()[0], 1)

    def test_fixed_daily_plan_cannot_be_overwritten_by_another_context(self):
        self.submit()
        new = create_context(self.db, evidence())
        result = self.submit(response(new["agent_handoff"]["context_id"], entry=101))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(load_plan(self.db, "IMOEXF", T0.date().isoformat())[1].entry, 100)

    def test_cancelled_agent_plan_can_be_replaced_by_fresh_context(self):
        first = self.submit()
        original_id = first["plan_id"]
        self.db.execute("UPDATE plans SET state='CANCELLED' WHERE plan_id=?", (original_id,))
        self.db.commit()
        fresh = evidence()
        fresh["replacement_for"] = {"plan_id": original_id, "state": "CANCELLED"}
        new = create_context(self.db, fresh)
        result = self.submit(response(new["agent_handoff"]["context_id"], entry=101, stop=95, tp1=105, tp2=115))
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["market_setup"]["entry"], 101)
        self.assertNotEqual(result["plan_id"], original_id)
        self.assertEqual(result["market_setup"]["revision"], 1)

    def test_replacement_revision_has_separate_plan_events(self):
        original = self.submit()
        original_id = original["plan_id"]
        self.db.execute("UPDATE plans SET state='CANCELLED' WHERE plan_id=?", (original_id,))
        fresh = evidence()
        fresh["replacement_for"] = {"plan_id": original_id, "state": "CANCELLED"}
        context = create_context(self.db, fresh)
        replacement = self.submit(response(
            context["agent_handoff"]["context_id"], entry=101, stop=95, tp1=105, tp2=115
        ))
        replacement_id = replacement["plan_id"]
        rows = self.db.execute(
            "SELECT plan_id,revision,root_plan_id FROM plans ORDER BY revision"
        ).fetchall()
        self.assertEqual([(r["plan_id"], r["revision"]) for r in rows],
                         [(original_id, 0), (replacement_id, 1)])
        self.assertEqual(rows[1]["root_plan_id"], original_id)
        original_events = self.db.execute(
            "SELECT plan_id FROM plan_events WHERE plan_id=?", (original_id,)
        ).fetchall()
        replacement_events = self.db.execute(
            "SELECT plan_id FROM plan_events WHERE plan_id=?", (replacement_id,)
        ).fetchall()
        self.assertTrue(original_events)
        self.assertTrue(replacement_events)
        self.assertTrue(all(r["plan_id"] == original_id for r in original_events))
        self.assertTrue(all(r["plan_id"] == replacement_id for r in replacement_events))

    def test_only_one_replacement_per_original_plan_and_trade_day(self):
        original = self.submit()
        original_id = original["plan_id"]
        self.db.execute("UPDATE plans SET state='CANCELLED' WHERE plan_id=?", (original_id,))
        fresh = evidence()
        fresh["replacement_for"] = {"plan_id": original_id, "state": "CANCELLED"}
        first_context = create_context(self.db, fresh)
        first = self.submit(response(
            first_context["agent_handoff"]["context_id"], entry=101, stop=95, tp1=105, tp2=115
        ))
        self.db.execute("UPDATE plans SET state='CANCELLED' WHERE plan_id=?", (first["plan_id"],))
        second_context = create_context(self.db, fresh)
        second = self.submit(response(
            second_context["agent_handoff"]["context_id"], entry=102, stop=95, tp1=110, tp2=120
        ))
        self.assertEqual(second["status"], "REJECTED")
        self.assertTrue(any("один replacement" in error for error in second["errors"]))
        self.assertEqual(self.db.execute("SELECT count(*) FROM plans").fetchone()[0], 2)

    def test_agent_no_trade_is_recorded_with_reason(self):
        data = response(
            self.context_id,
            decision="NO_TRADE",
            reason="Нет сделки до закрытия D1 выше уровня 105.5",
        )
        for k in ("entry", "stop", "tp1", "tp2", "setup_type"):
            data.pop(k)
        result = submit_decision(self.db, data, now=T0)
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["market_setup"]["decision"], "NO_TRADE")
        self.assertEqual(result["market_setup"]["reason"], data["reason"])

    def test_no_trade_rejects_inexact_awaited_conditions(self):
        reasons = (
            "Ждём подтверждения D1 выше уровня 105",  # no concrete event
            "Ждём закрытия выше уровня 105",  # no D1/H4 timeframe
            "Ждём закрытия H4 выше сопротивления",  # no numeric level
            "Ждём подтверждения",  # none of the required details
            "D1: закрытие через 2 дня",  # duration is not a price level
            "D1 RSI 50; ждём закрытия",  # indicator value is unrelated to the event
            "D1: ждём закрытия выше 2 свечей",  # candle count is not a price level
            "D1: ждём закрытия при RSI выше 50",  # indicator threshold is not a price
            "D1: ждём закрытия выше 50 по RSI",  # suffixed indicator threshold is not a price
            "D1: ждём закрытия выше 2 баров",  # bar count is not a price level
            "H4: ждём закрытия ниже 1.5 по ATR",  # ATR multiple is not a price level
            "D1: ждём закрытия выше 200 по MA",  # MA value is not a price level
            "H4: ждём закрытия выше 3 bars",  # English bar count is not a price level
        )
        for reason in reasons:
            with self.subTest(reason=reason):
                db = connect(":memory:")
                context = create_context(db, evidence())
                data = response(
                    context["agent_handoff"]["context_id"],
                    decision="NO_TRADE",
                    reason=reason,
                )
                for key in ("entry", "stop", "tp1", "tp2", "setup_type"):
                    data.pop(key)
                result = submit_decision(db, data, now=T0)
                self.assertEqual(result["status"], "REJECTED")
                self.assertTrue(any("NO_TRADE reason" in error for error in result["errors"]))
                self.assertEqual(db.execute("SELECT count(*) FROM plans").fetchone()[0], 0)
                db.close()

    def test_no_trade_accepts_awaited_price_levels(self):
        reasons = (
            "D1: ждём закрытия выше 50",
            "H4: ждём закрытия ниже уровня 2",
            "D1: ждём закрытия выше 105.5 по цене",
        )
        for reason in reasons:
            with self.subTest(reason=reason):
                db = connect(":memory:")
                context = create_context(db, evidence())
                data = response(
                    context["agent_handoff"]["context_id"],
                    decision="NO_TRADE",
                    reason=reason,
                )
                for key in ("entry", "stop", "tp1", "tp2", "setup_type"):
                    data.pop(key)
                result = submit_decision(db, data, now=T0)
                self.assertEqual(result["status"], "ACCEPTED")
                db.close()

    def test_keep_plan_records_review_without_changing_active_levels(self):
        plan = TradePlan("IMOEXF", Decision.LONG_SETUP, "existing", entry=100, stop=95, tp1=105, tp2=115,
                         strategy_version="swing-v1")
        pid = save_plan(self.db, plan, (T0 - timedelta(days=1)).date().isoformat())
        self.db.execute("update plans set state='ACTIVE' where plan_id=?", (pid,))
        self.db.commit()
        data = response(self.context_id, decision="KEEP_PLAN")
        for k in ("entry", "stop", "tp1", "tp2", "setup_type"):
            data.pop(k)
        result = submit_decision(self.db, data, now=T0)
        self.assertEqual(result["status"], "REVIEWED")
        self.assertEqual(result["plan_id"], pid)
        self.assertEqual(result["market_setup"]["stop"], 95)
        self.assertEqual(self.submit()["status"], "REJECTED")

    def test_keep_plan_does_not_require_no_trade_wait_condition(self):
        plan = TradePlan(
            "IMOEXF", Decision.LONG_SETUP, "existing", entry=100, stop=95,
            tp1=105, tp2=115, strategy_version="swing-v1",
        )
        pid = save_plan(self.db, plan, (T0 - timedelta(days=1)).date().isoformat())
        self.db.execute("UPDATE plans SET state='ACTIVE' WHERE plan_id=?", (pid,))
        self.db.commit()
        data = response(
            self.context_id, decision="KEEP_PLAN", reason="Текущий план сохраняется",
        )
        for key in ("entry", "stop", "tp1", "tp2", "setup_type"):
            data.pop(key)
        result = submit_decision(self.db, data, now=T0)
        self.assertEqual(result["status"], "REVIEWED")

    def test_stale_open_market_is_rejected_but_closed_session_reference_allowed(self):
        market = evidence()
        market.update(stale=True, delay_minutes=600)
        result = submit_decision(self.db, response(self.context_id), self.broker, market, T0)
        self.assertEqual(result["status"], "REJECTED")
        market.update(market_open=False, trading_status="SECURITY_TRADING_STATUS_BREAK_IN_TRADING")
        self.assertEqual(submit_decision(self.db, response(self.context_id), self.broker, market, T0)["status"], "ACCEPTED")

    def test_short_plan_is_validated_symmetrically(self):
        result = self.submit(response(self.context_id, decision="SHORT_SETUP", stop=105, tp1=95, tp2=85))
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["market_setup"]["direction"] if "direction" in result["market_setup"] else result["market_setup"]["decision"], "SHORT_SETUP")

    def test_long_protective_levels_must_be_below_fresh_market_price(self):
        market = evidence()
        market["price"] = 97
        for changes in ({"stop": 97}, {"stop": 98}, {"structural_invalidation": 97},
                        {"structural_invalidation": 98}):
            with self.subTest(changes=changes):
                result = submit_decision(self.db, response(self.context_id, **changes),
                                         self.broker, market, T0)
                self.assertEqual(result["status"], "REJECTED")
                self.assertTrue(any("свежей рыночной цены" in error for error in result["errors"]))

    def test_short_protective_levels_must_be_above_fresh_market_price(self):
        market = evidence()
        market["price"] = 103
        base = {"decision": "SHORT_SETUP", "stop": 105, "tp1": 95, "tp2": 85}
        for changes in ({"stop": 103}, {"stop": 102}, {"structural_invalidation": 103},
                        {"structural_invalidation": 102}):
            with self.subTest(changes=changes):
                plan = {**base, **changes}
                result = submit_decision(
                    self.db, response(self.context_id, **plan), self.broker, market, T0
                )
                self.assertEqual(result["status"], "REJECTED")
                self.assertTrue(any("свежей рыночной цены" in error for error in result["errors"]))

    def test_morning_open_position_hides_entry_and_shows_only_current_action(self):
        payload = evidence()
        payload.update({
            "open_positions": [{"direction": "LONG", "lots": 1, "average_price": 100}],
            "broker_stop_orders": [{"ticker": "IMOEXF"}],
            "market_setup": {"decision": "LONG_SETUP", "entry": 100, "stop": 95,
                             "tp1": 105, "tp2": 110, "rr_tp2": 2},
        })
        report = morning.morning_human(payload)
        self.assertNotIn("Вход", report)
        self.assertIn("Действие: нет — удерживать позицию.", report)
        self.assertNotIn("Докупить", report)

    def test_morning_short_position_actions_are_symmetric(self):
        payload = evidence()
        payload.update({
            "open_positions": [{"direction": "SHORT", "lots": 1, "average_price": 100}],
            "broker_stop_orders": [],
            "market_setup": {"decision": "SHORT_SETUP", "entry": 100, "stop": 105,
                             "tp1": 95, "tp2": 90, "rr_tp2": 2},
        })
        for price, expected in ((106, "закрыть позицию"), (89, "достигнута TP2 90"),
                                (94, "достигнута TP1 95"), (100, "удерживать позицию")):
            with self.subTest(price=price):
                payload["price"] = price
                self.assertIn(expected, morning.morning_human(payload))

    def test_morning_stale_position_keeps_saved_scenario_and_payload_context(self):
        payload = evidence()
        payload.update({
            "stale": True,
            "open_positions": [{"direction": "LONG", "lots": 1, "average_price": 100}],
            "broker_stop_orders": [],
            "market_setup": {"decision": "LONG_SETUP", "entry": 100, "stop": 95,
                             "tp1": 105, "tp2": 110, "rr_tp2": 2},
        })
        report = morning.morning_human(payload)
        self.assertIn("Позиция: LONG 1 лот. от 100", report)
        self.assertIn("Цели позиции: 105 → 110", report)
        self.assertIn("Контекст: w1 close 100 · d1 close 100 · h4 close 100.", report)
        self.assertNotIn("1ч — вверх", report)
        self.assertIn("нужна свежая цена", report)

    def test_morning_stale_without_position_keeps_saved_scenario(self):
        payload = evidence()
        payload.update({
            "stale": True,
            "open_positions": [],
            "broker_stop_orders": [],
            "market_setup": {"decision": "SHORT_SETUP", "entry": 100, "stop": 105,
                             "tp1": 95, "tp2": 90, "rr_tp2": 2},
        })
        report = morning.morning_human(payload)
        self.assertIn("План: SHORT при подтверждении уровня 100.", report)
        self.assertIn("Цели 95 → 90", report)
        self.assertIn("Сейчас: нужна свежая цена, сделки нет.", report)

    def test_morning_never_calls_legacy_selector(self):
        with patch.object(morning, "collect_compact", return_value=(evidence(), None, None)), \
             patch("usdrubf.process.plan_for", side_effect=AssertionError("backend selected a trade")):
            result = morning.morning_ticker("IMOEXF", self.db, self.broker)
        self.assertEqual(result["market_setup"]["state"], "AWAITING_AGENT")
        self.assertEqual(self.db.execute("select count(*) from plans").fetchone()[0], 0)
        self.assertEqual(self.db.execute("select count(*) from analysis_contexts").fetchone()[0], 2)

    def test_previous_day_context_and_agent_analysis_are_passed_forward(self):
        self.submit()
        next_day = evidence()
        next_day["generated_at"] = (T0 + timedelta(days=1)).isoformat()
        result = create_context(self.db, next_day)
        self.assertEqual(result["previous_context"]["price"], 100)
        self.assertEqual(result["previous_agent_analysis"]["decision"], "LONG_SETUP")


if __name__ == "__main__":
    unittest.main()
