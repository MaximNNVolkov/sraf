import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

from usdrubf.trading import (
    Decision, SetupState, TradePlan, advance_plan, build_plan, calculate_pnl,
    close_trade, connect, load_plan, migrate_legacy_markdown, open_positions,
    open_trade, save_plan, set_stop,
)


class PlanTest(unittest.TestCase):
    def test_legacy_daily_unique_schema_migrates_without_losing_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            legacy = sqlite3.connect(path)
            legacy.executescript("""
                CREATE TABLE plans(
                  plan_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, trade_date TEXT NOT NULL,
                  payload TEXT NOT NULL, state TEXT NOT NULL, state_time TEXT NOT NULL,
                  last_candle_time TEXT, activated_at TEXT, UNIQUE(ticker, trade_date)
                );
                CREATE TABLE plan_events(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  plan_id TEXT NOT NULL REFERENCES plans(plan_id), old_state TEXT,
                  new_state TEXT NOT NULL, price REAL, happened_at TEXT NOT NULL, note TEXT
                );
            """)
            legacy.execute("INSERT INTO plans VALUES(?,?,?,?,?,?,NULL,NULL)",
                           ("X-2026-01-01", "X", "2026-01-01", "{}",
                            "CANCELLED", "2026-01-01T08:00:00+03:00"))
            legacy.execute("INSERT INTO plan_events(plan_id,new_state,happened_at) VALUES(?,?,?)",
                           ("X-2026-01-01", "CANCELLED", "2026-01-01T08:00:00+03:00"))
            legacy.commit()
            legacy.close()

            migrated = connect(path)
            row = migrated.execute("SELECT revision,root_plan_id FROM plans").fetchone()
            self.assertEqual(tuple(row), (0, "X-2026-01-01"))
            self.assertEqual(migrated.execute("SELECT count(*) FROM plan_events").fetchone()[0], 1)
            self.assertEqual(migrated.execute("PRAGMA foreign_key_check").fetchall(), [])
            migrated.close()

    def test_rejects_low_rr_and_never_emits_two_directions(self):
        p = TradePlan("X", Decision.LONG_SETUP, "x", entry=10, stop=9, tp1=10.5, tp2=11)
        self.assertEqual(p.decision, Decision.NO_TRADE)
        good = build_plan("X", "LONG", [9], [10, 11, 13])
        self.assertEqual(good.decision, Decision.LONG_SETUP)
        self.assertEqual(good.rr_tp2, 3)

    def test_missing_tp2_is_no_trade(self):
        p = TradePlan("X", Decision.SHORT_SETUP, "x", entry=10, stop=11, tp1=9)
        self.assertEqual(p.decision, Decision.NO_TRADE)

    def test_uses_confirmed_pullback_when_breakout_has_no_valid_rr(self):
        plan = build_plan("X", "LONG", [100, 95], [110, 120])
        self.assertEqual(plan.decision, Decision.LONG_SETUP)
        self.assertEqual((plan.entry, plan.stop, plan.tp1, plan.tp2), (100, 95, 110, 120))
        self.assertEqual(plan.confirmation, "hourly_reclaim_after_retest")
        self.assertTrue(plan.retest_required)
        self.assertEqual(plan.rr_tp2, 4)


class StateMachineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.tmp.name) / "t.db")
        self.plan = TradePlan("X", Decision.LONG_SETUP, "x", entry=10, stop=9, tp1=11, tp2=13,
                              confirmation="hourly_close_above", retest_required=True)
        self.pid = save_plan(self.db, self.plan, "2026-01-01")

    def tearDown(self):
        self.tmp.cleanup()

    def test_crossing_is_not_confirmation_and_is_not_forgotten(self):
        s = advance_plan(self.db, self.pid, self.plan, SetupState.WAITING, 10.2, candle_close=9.9)
        self.assertEqual(s, SetupState.CROSSED)
        s = advance_plan(self.db, self.pid, self.plan, s, 9.8, candle_close=9.9)
        self.assertEqual(s, SetupState.CROSSED)

    def test_confirmation_retest_target(self):
        s = advance_plan(self.db, self.pid, self.plan, SetupState.WAITING, 10.2, candle_close=10.1)
        self.assertEqual(s, SetupState.HOURLY_CONFIRMED)
        s = advance_plan(self.db, self.pid, self.plan, s, 10.3, candle_close=10.2, retest_confirmed=True)
        self.assertEqual(s, SetupState.ACTIVE)
        s = advance_plan(self.db, self.pid, self.plan, s, 11.2, candle_high=11.2, candle_low=10.5)
        self.assertEqual(s, SetupState.TP1)
        s = advance_plan(self.db, self.pid, self.plan, s, 13.1, candle_high=13.1, candle_low=11)
        self.assertEqual(s, SetupState.TP2)

    def test_stop(self):
        s = advance_plan(self.db, self.pid, self.plan, SetupState.ACTIVE, 8.9, candle_high=10, candle_low=8.9)
        self.assertEqual(s, SetupState.STOPPED)


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_open_stop_partial_close_close(self):
        tid = open_trade(self.db, "IMOEXF", "SHORT", 2500, 4)
        set_stop(self.db, tid, 2510)
        close_trade(self.db, tid, 2490, 1, "partial")
        row = open_positions(self.db)[0]
        self.assertEqual((row["quantity"], row["current_stop"]), (3, 2510))
        close_trade(self.db, tid, 2480, reason="target")
        self.assertEqual(open_positions(self.db), [])

    def test_migration_is_idempotent(self):
        md = Path(self.tmp.name) / "p.md"
        md.write_text("# X\n\n## IMOEXF\n- Статус: ОТКРЫТА\n- Направление: ШОРТ\n- Цена входа: 2457.0\n- Объём: 4 лота\n", encoding="utf-8")
        self.assertEqual(migrate_legacy_markdown(self.db, md), 1)
        self.assertEqual(migrate_legacy_markdown(self.db, md), 0)
        self.assertEqual(len(open_positions(self.db)), 1)

    def test_pnl_requires_value_per_point(self):
        self.assertIsNone(calculate_pnl("LONG", 10, 12, 4)["net_rub"])
        self.assertEqual(calculate_pnl("LONG", 10, 12, 4, 10, 5)["net_rub"], 75)
        self.assertEqual(calculate_pnl("SHORT", 12, 10, 4, 10)["gross_rub"], 80)


class DigestFactsTest(unittest.TestCase):
    def test_observation_is_not_called_official_without_hl(self):
        path = str(Path(__file__).resolve().parents[1] / "scripts" / "moex_day_digest.py")
        spec = importlib.util.spec_from_file_location("digest_test", path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        facts = {t: {"series": [("10:00", 1.0)], "high": None, "low": None,
                     "high_t": None, "low_t": None} for t in mod.TICKERS}
        text = "\n".join(mod.render_facts(facts))
        self.assertIn("максимум среди наблюдений", text)
        self.assertNotIn("максимум дня", text)


if __name__ == "__main__":
    unittest.main()
