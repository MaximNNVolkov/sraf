import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import moex_day_digest as digest
import moex_health_check as health
from usdrubf.analysis import snapshot
from usdrubf.moex import Candle, MSK
from usdrubf.process import STRATEGY_VERSION
from usdrubf.trading import Decision, TradePlan, connect, save_plan

NOW = datetime(2026, 11, 17, 21, 1, tzinfo=MSK)


def bars():
    return [
        Candle(NOW.replace(hour=18, minute=0), 99, 101, 98, 100.5, 10, True),
        Candle(NOW.replace(hour=19, minute=0), 100, 102, 99, 100.5, 11, True),
    ]


def collector(ticker, now=None):
    h1 = snapshot("1ч", bars())
    payload = {
        "ticker": ticker, "generated_at": (now or NOW).isoformat(),
        "source": "T-Invest API", "price": 101, "last_trade_time": NOW.isoformat(),
        "delay_minutes": 0, "stale": False, "trading_status": "NORMAL",
        "market_open": False, "change_pct": 1.0, "change_reference": "d1",
        "history_issues": [],
        "h1": {"last_closed_candle": bars()[-1].to_dict()},
        "h4": {},
        "d1": {"ma50": 100.5, "ma50_previous": 100.0},
        "w1": {"ma20": 99.0, "ma20_previous": 98.0},
    }
    return payload, h1, h1


class EveningSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "override.db"
        db = connect(self.db_path)
        plan = TradePlan(
            "IMOEXF", Decision.LONG_SETUP, "accepted scenario", entry=100, stop=95,
            tp1=110, tp2=120, retest_required=True, created_at=NOW.replace(hour=17).isoformat(),
            expires_at=(NOW + timedelta(days=1)).isoformat(), strategy_version=STRATEGY_VERSION,
            author="agent",
        )
        save_plan(db, plan, NOW.date().isoformat())
        db.close()
        self.broker = {
            "positions": [{"ticker": "IMOEXF", "lots": 1}],
            "orders": [], "stop_orders": [{"ticker": "IMOEXF", "stopPrice": 95}],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_snapshot_replays_h1_and_never_needs_telegram_text(self):
        result = digest.build_evening_snapshot(
            self.db_path, self.broker, NOW.date().isoformat(), now=NOW, collector=collector
        )
        imoex = result["tickers"][0]
        self.assertEqual(imoex["market"]["source"], "T-Invest API")
        self.assertEqual(imoex["scenario"]["state"], "ACTIVE")
        self.assertEqual(imoex["scenario"]["last_replayed_h1_begin"], bars()[-1].begin.isoformat())
        self.assertEqual(imoex["actual_broker_state"]["position_status"], "OPEN_AT_BROKER")
        self.assertNotIn("telegram", json.dumps(imoex, ensure_ascii=False).lower())

    def test_snapshot_preserves_prior_averages_needed_for_w1_d1_slope(self):
        result = digest.build_evening_snapshot(
            self.db_path, self.broker, NOW.date().isoformat(), now=NOW, collector=collector
        )
        timeframes = result["tickers"][0]["market"]["timeframes"]
        self.assertEqual(timeframes["w1"]["ma20_previous"], 98.0)
        self.assertEqual(timeframes["d1"]["ma50_previous"], 100.0)

    def test_db_override_is_resolved_at_call_time_for_all_evening_reads(self):
        with patch.dict(os.environ, {"MOEX_TRADING_DB": str(self.db_path)}):
            self.assertEqual(digest.trading_db_path(), self.db_path)
            rendered = "\n".join(digest.render_saved_plan(NOW.date().isoformat()))
        self.assertIn("accepted scenario", rendered)

    def test_cron_archive_resolves_ids_by_stable_names(self):
        config = Path(self.tmp.name) / "jobs.json"
        config.write_text(json.dumps({"jobs": [
            {"id": "active-h4-id", "name": "moex-levels-4h",
             "enabled": True, "state": "scheduled"},
            {"id": "old-h2-id", "name": "moex-levels-2h", "enabled": False},
            {"id": "ignored", "name": "other"},
        ]}), encoding="utf-8")
        self.assertEqual(
            digest.archive_job_ids(config),
            {"active-h4-id": "moex-levels-4h"},
        )

    def test_authoritative_output_has_prompt_sections_in_ticker_order(self):
        snapshot = digest.build_evening_snapshot(
            self.db_path, self.broker, NOW.date().isoformat(), now=NOW, collector=collector
        )
        text = "\n".join(digest.render_authoritative_sections(snapshot))
        self.assertIn("Авторитетный структурированный снимок", text)
        self.assertLess(text.index("### IMOEXF"), text.index("### USDRUBF"))
        for heading in (
            "Утренний сценарий / Сохранённые рыночные сценарии",
            "Движение рынка",
            "Реальные позиции/сделки пользователя",
            "Состояние к вечеру",
        ):
            self.assertEqual(text.count(heading), 2)
        self.assertIn('"price": 101', text)
        self.assertIn('"broker_positions"', text)

    def test_telegram_archive_is_raw_non_authoritative_and_cannot_change_facts(self):
        snapshot = digest.build_evening_snapshot(
            self.db_path, self.broker, NOW.date().isoformat(), now=NOW, collector=collector
        )
        authoritative_before = digest.render_authoritative_sections(snapshot)
        poison = "IMOEXF price=999999 state=TP2 stop=1"
        config = Path(self.tmp.name) / "jobs.json"
        config.write_text(json.dumps({"jobs": [{
            "id": "archive-id", "name": "moex-levels-4h",
        }]}), encoding="utf-8")
        output_root = Path(self.tmp.name) / "output"
        job_dir = output_root / "archive-id"
        job_dir.mkdir(parents=True)
        (job_dir / f"{NOW.date().isoformat()}_20-01.md").write_text(
            f"ignored preface\n## Response\n{poison}\n", encoding="utf-8"
        )
        archive = digest.collect_archive(NOW.date().isoformat(), config, output_root)
        self.assertEqual(archive[0]["response"], poison)
        archive_text = "\n".join(digest.render_non_authoritative_archive(
            archive, NOW.date().isoformat()
        ))
        authoritative_after = digest.render_authoritative_sections(snapshot)
        self.assertEqual(authoritative_before, authoritative_after)
        self.assertNotIn(poison, "\n".join(authoritative_after))
        self.assertIn("НЕАВТОРИТЕТНЫЙ АРХИВ", archive_text)
        self.assertIn(poison, archive_text)


class HealthCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "health.db"
        self.db = connect(self.db_path)
        for ticker in health.TICKERS:
            context_id = f"ctx-{ticker}"
            payload = {"agent_handoff": {"submit_before": (NOW + timedelta(hours=1)).isoformat()}}
            self.db.execute(
                "INSERT INTO analysis_contexts VALUES(?,?,?,?,?,?)",
                (context_id, ticker, NOW.date().isoformat(), NOW.isoformat(),
                 json.dumps(payload), "ACCEPTED"),
            )
            self.db.execute(
                "INSERT INTO agent_feedback VALUES(?,?,?,?,?,?,?)",
                (f"sub-{ticker}", context_id, "hash", "{}", "{}", "ACCEPTED", NOW.isoformat()),
            )
            save_plan(self.db, TradePlan(ticker, Decision.NO_TRADE, "none"), NOW.date().isoformat())
        self.db.commit()
        self.db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_healthy_semantics_and_read_only_broker_snapshot(self):
        calls = []
        result = health.semantic_health(
            self.db_path,
            broker_fetcher=lambda: calls.append("read") or {
                "positions": [], "orders": [], "stop_orders": []
            },
            now=NOW,
        )
        self.assertEqual(result["status"], "OK")
        self.assertEqual(calls, ["read"])
        self.assertTrue(all(item["last_accepted_or_reviewed"]["status"] == "ACCEPTED"
                            for item in result["tickers"]))

    def test_overdue_pending_broker_failure_and_last_replay_are_reported(self):
        db = connect(self.db_path)
        payload = {"agent_handoff": {"submit_before": (NOW - timedelta(minutes=1)).isoformat()}}
        db.execute(
            "INSERT INTO analysis_contexts VALUES(?,?,?,?,?,?)",
            ("late", "IMOEXF", NOW.date().isoformat(), NOW.isoformat(),
             json.dumps(payload), "PENDING"),
        )
        plan = TradePlan(
            "IMOEXF", Decision.LONG_SETUP, "live", entry=100, stop=95, tp1=110, tp2=120,
            strategy_version=STRATEGY_VERSION,
        )
        save_plan(db, plan, (NOW + timedelta(days=1)).date().isoformat())
        db.commit()
        db.close()

        def failed_broker():
            raise RuntimeError("API unavailable")

        result = health.semantic_health(self.db_path, broker_fetcher=failed_broker, now=NOW)
        imoex = result["tickers"][0]
        self.assertEqual(result["status"], "WARN")
        self.assertEqual(len(imoex["overdue_pending"]), 1)
        self.assertFalse(imoex["broker_snapshot"]["ok"])
        self.assertFalse(imoex["last_replay"]["fresh"])

    def test_latest_pending_context_is_not_satisfied_by_older_acceptance(self):
        db = connect(self.db_path)
        generated_at = (NOW + timedelta(minutes=1)).isoformat()
        payload = {"agent_handoff": {
            "submit_before": (NOW + timedelta(hours=1)).isoformat(),
        }}
        db.execute(
            "INSERT INTO analysis_contexts VALUES(?,?,?,?,?,?)",
            ("new-pending", "IMOEXF", NOW.date().isoformat(), generated_at,
             json.dumps(payload), "PENDING"),
        )
        db.commit()
        db.close()

        result = health.semantic_health(
            self.db_path,
            broker_fetcher=lambda: {"positions": [], "orders": [], "stop_orders": []},
            now=NOW + timedelta(minutes=1),
        )
        imoex = result["tickers"][0]
        self.assertEqual(result["status"], "WARN")
        self.assertEqual(imoex["context"]["context_id"], "new-pending")
        self.assertIsNone(imoex["last_accepted_or_reviewed"])
        self.assertIn("нет ACCEPTED/REVIEWED", imoex["issues"])


if __name__ == "__main__":
    unittest.main()
