#!/usr/bin/env python3
"""Run production and staging jobs with separate state and credentials."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
JOBS = {
    "morning-imoexf": "moex_imoexf_tf.py",
    "morning-usdrubf": "moex_usdrubf_tf.py",
    "monitor": "moex_monitor_tf.py",
    "evening": "moex_day_digest.py",
    "health": "moex_health_check.py",
    "agent": "moex_agent_plan.py",
    "positions": "tinkoff_positions.py",
    "backtest": "moex_direction_backtest.py",
    "execution-backtest": "moex_execution_rules_backtest.py",
    "trend-backtest": "moex_medium_term_trend_backtest.py",
    "benchmark": "moex_medium_term_benchmark_comparison.py",
    "walkforward": "moex_medium_term_walkforward_validation.py",
    "random-execution": "usdrubf_current_model_10_random_execution_tests.py",
}
RESEARCH = {"test", "backtest", "execution-backtest", "trend-backtest", "benchmark", "walkforward", "random-execution"}


def prepare(profile: str) -> tuple[dict[str, str], Path]:
    if profile not in {"prod", "staging"}:
        raise ValueError("Unknown environment")
    work = ROOT / "runtime" / profile
    work.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    # Never inherit the production account/token or file/path overrides in staging.
    if profile == "staging":
        for key in tuple(env):
            if key.startswith(("TINKOFF_", "MOEX_")):
                env.pop(key)
    env.update({
        "MOEX_ENV": profile,
        "MOEX_ENV_FILE": str(ROOT / (".env" if profile == "prod" else ".env.staging")),
        "MOEX_TRADING_DB": str(ROOT / "trading.db" if profile == "prod" else work / "trading.db"),
        "PYTHONPATH": str(ROOT) + os.pathsep + env.get("PYTHONPATH", ""),
    })
    if profile == "staging":
        env["MOEX_CRON_JOBS_CONFIG"] = str(work / "cron" / "jobs.json")
        env["MOEX_CRON_OUTPUT_ROOT"] = str(work / "cron" / "output")
    return env, work


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=["prod", "staging"])
    parser.add_argument("job", choices=["cli", "test", *JOBS])
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.profile == "prod" and args.job in RESEARCH:
        parser.error("Тесты и бэктесты запускаются в staging")
    extra = args.args[1:] if args.args[:1] == ["--"] else args.args
    # Explicit output/DB arguments are trusted user overrides, documented below.
    env, work = prepare(args.profile)
    python = ROOT / ".venv" / "bin" / "python"
    executable = str(python) if python.is_file() else sys.executable
    if args.job == "cli":
        command = [executable, "-m", "usdrubf", *extra]
    elif args.job == "test":
        command = [executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"), *extra]
    else:
        command = [executable, str(ROOT / "scripts" / JOBS[args.job]), *extra]
    print(f"MOEX {args.profile}: {args.job}; cwd={work}", file=sys.stderr, flush=True)
    return subprocess.call(command, cwd=work, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
