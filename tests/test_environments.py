"""Environment boundaries: credentials, state, and accidental production runs."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from usdrubf.tbank import load_project_env

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_env", ROOT / "scripts/run_env.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class EnvironmentTests(unittest.TestCase):
    def test_staging_does_not_inherit_production_credentials_or_paths(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, "ROOT", Path(tmp)):
            with patch.dict(os.environ, {"TINKOFF_TOKEN": "prod-token", "TINKOFF_ACCOUNT_ID": "prod-account",
                                         "MOEX_TRADING_DB": "/prod/db", "MOEX_ENV_FILE": "/prod/env"}):
                env, work = runner.prepare("staging")
            self.assertNotIn("TINKOFF_TOKEN", env)
            self.assertNotIn("TINKOFF_ACCOUNT_ID", env)
            self.assertEqual(Path(env["MOEX_TRADING_DB"]), work / "trading.db")
            self.assertEqual(Path(env["MOEX_ENV_FILE"]), Path(tmp) / ".env.staging")
            self.assertTrue(Path(env["MOEX_CRON_OUTPUT_ROOT"]).is_relative_to(work))
            prod, prod_work = runner.prepare("prod")
            self.assertEqual(Path(prod["MOEX_TRADING_DB"]), Path(tmp) / "trading.db")
            self.assertNotEqual(prod_work, work)

    def test_staging_env_file_cannot_redirect_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / ".env.staging"
            config.write_text('TINKOFF_TOKEN=staging-token\nMOEX_TRADING_DB=/prod/db\n')
            with patch.dict(os.environ, {"MOEX_ENV": "staging", "MOEX_ENV_FILE": str(config)}, clear=True):
                load_project_env()
                self.assertEqual(os.environ["TINKOFF_TOKEN"], "staging-token")
                self.assertNotIn("MOEX_TRADING_DB", os.environ)

    def test_missing_staging_file_does_not_load_root_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MOEX_ENV": "staging", "MOEX_ENV_FILE": str(Path(tmp) / "missing")}, clear=True):
                load_project_env()
                self.assertNotIn("TINKOFF_TOKEN", os.environ)

    def test_prod_rejects_research_before_launch(self):
        with patch.object(runner.subprocess, "call") as launch, contextlib.redirect_stderr(io.StringIO()):
            for job in runner.RESEARCH:
                with self.assertRaises(SystemExit):
                    runner.main(["prod", job])
            launch.assert_not_called()

    def test_cli_uses_selected_db_without_touching_other_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "staging.db"
            sentinel = Path(tmp) / "trading.db"
            sentinel.write_bytes(b"production-state")
            env = dict(os.environ, MOEX_TRADING_DB=str(db), PYTHONPATH=str(ROOT))
            result = subprocess.run([sys.executable, "-m", "usdrubf", "trade", "list"],
                                    cwd=tmp, env=env, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout), [])
            self.assertTrue(db.exists())
            self.assertEqual(sentinel.read_bytes(), b"production-state")


if __name__ == "__main__":
    unittest.main()
