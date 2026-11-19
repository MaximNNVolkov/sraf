#!/usr/bin/env python3
"""Semantic, cron-friendly health check for the MOEX workflow.

The broker is queried through read-only portfolio/order endpoints. SQLite is
opened in read-only mode and is never initialized or migrated by this script.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[1])
PROJECT_PYTHON = f"{REPO}/.venv/bin/python"
if __name__ == "__main__" and os.path.realpath(sys.executable) != os.path.realpath(PROJECT_PYTHON):
    os.execv(PROJECT_PYTHON, [PROJECT_PYTHON, __file__, *sys.argv[1:]])
sys.path.insert(0, REPO)
sys.path.insert(0, f"{REPO}/scripts")

from usdrubf.moex import MSK  # noqa: E402
from tinkoff_positions import fetch_state, load_project_env  # noqa: E402

TICKERS = ("IMOEXF", "USDRUBF")
NON_REPLAY_STATES = {"TP2", "STOPPED", "CANCELLED"}


def default_db_path() -> Path:
    return Path(os.environ.get("MOEX_TRADING_DB", str(Path(REPO) / "trading.db")))


def _age_minutes(value: str, now: datetime) -> float:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=now.tzinfo)
    return round((now - stamp.astimezone(now.tzinfo)).total_seconds() / 60, 1)


def _pending_deadline(payload: str) -> str | None:
    try:
        return json.loads(payload).get("agent_handoff", {}).get("submit_before")
    except (TypeError, json.JSONDecodeError):
        return None


def _deadline_passed(value: str, now: datetime) -> bool:
    deadline = datetime.fromisoformat(value)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=now.tzinfo)
    return deadline.astimezone(now.tzinfo) < now


def ticker_health(db, ticker: str, broker_ok: bool, broker_error: str | None,
                  now: datetime, context_max_minutes: int,
                  replay_max_minutes: int) -> dict:
    issues = []
    context = db.execute(
        "SELECT context_id,generated_at,status FROM analysis_contexts "
        "WHERE ticker=? ORDER BY generated_at DESC,rowid DESC LIMIT 1", (ticker,)
    ).fetchone()
    context_info = None
    if not context:
        issues.append("нет analysis context")
    else:
        age = _age_minutes(context["generated_at"], now)
        context_info = {**dict(context), "age_minutes": age,
                        "fresh": -1 <= age <= context_max_minutes}
        if not context_info["fresh"]:
            issues.append(f"context просрочен ({age} мин)")

    feedback = None
    if context:
        feedback = db.execute(
            """SELECT context_id,status,received_at FROM agent_feedback
               WHERE context_id=? AND status IN ('ACCEPTED','REVIEWED')
               ORDER BY received_at DESC,rowid DESC LIMIT 1""",
            (context["context_id"],),
        ).fetchone()
    feedback_info = dict(feedback) if feedback else None
    if not feedback:
        issues.append("нет ACCEPTED/REVIEWED")

    overdue = []
    for pending in db.execute(
        "SELECT context_id,generated_at,payload FROM analysis_contexts "
        "WHERE ticker=? AND status='PENDING' ORDER BY generated_at", (ticker,)
    ):
        deadline = _pending_deadline(pending["payload"])
        try:
            expired = deadline is None or _deadline_passed(deadline, now)
        except ValueError:
            expired = True
        if expired:
            overdue.append({"context_id": pending["context_id"], "submit_before": deadline,
                            "reason": "deadline passed" if deadline else "deadline missing"})
    if overdue:
        issues.append(f"просроченных PENDING: {len(overdue)}")

    plan = db.execute(
        "SELECT plan_id,state,last_candle_time,payload FROM plans WHERE ticker=? "
        "ORDER BY trade_date DESC,revision DESC LIMIT 1", (ticker,)
    ).fetchone()
    replay = {"required": False, "last_h1_begin": None, "age_minutes": None, "fresh": True}
    if plan:
        try:
            directed = json.loads(plan["payload"]).get("decision") not in {None, "NO_TRADE"}
        except json.JSONDecodeError:
            directed = True
        replay["required"] = directed and plan["state"] not in NON_REPLAY_STATES
        replay["last_h1_begin"] = plan["last_candle_time"]
        if replay["required"]:
            if not plan["last_candle_time"]:
                replay["fresh"] = False
                issues.append("replay H1 ещё не выполнялся")
            else:
                replay_time = datetime.fromisoformat(plan["last_candle_time"]) + timedelta(hours=1)
                age = _age_minutes(replay_time.isoformat(), now)
                replay["age_minutes"] = age
                replay["fresh"] = -1 <= age <= replay_max_minutes
                if not replay["fresh"]:
                    issues.append(f"replay H1 просрочен ({age} мин)")

    if not broker_ok:
        issues.append(f"broker snapshot: {broker_error}")
    return {
        "ticker": ticker,
        "status": "OK" if not issues else "WARN",
        "context": context_info,
        "last_accepted_or_reviewed": feedback_info,
        "overdue_pending": overdue,
        "broker_snapshot": {"ok": broker_ok, "error": broker_error},
        "last_replay": replay,
        "issues": issues,
    }


def semantic_health(db_path: str | Path, broker_fetcher=fetch_state,
                    now: datetime | None = None, context_max_minutes: int = 24 * 60,
                    replay_max_minutes: int = 3 * 60) -> dict:
    now = now or datetime.now(MSK)
    broker_ok, broker_error = True, None
    try:
        broker = broker_fetcher()
        required = {"positions", "orders", "stop_orders"}
        if not isinstance(broker, dict) or not required.issubset(broker):
            raise RuntimeError("неполный broker snapshot")
    except Exception as exc:
        broker_ok, broker_error = False, str(exc)

    path = Path(db_path).resolve()
    try:
        db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        results = [ticker_health(db, ticker, broker_ok, broker_error, now,
                                 context_max_minutes, replay_max_minutes)
                   for ticker in TICKERS]
        db.close()
    except (OSError, sqlite3.Error) as exc:
        results = [{"ticker": ticker, "status": "ERROR", "issues": [f"SQLite: {exc}"],
                    "broker_snapshot": {"ok": broker_ok, "error": broker_error}}
                   for ticker in TICKERS]
    status = "OK" if all(item["status"] == "OK" for item in results) else "WARN"
    if any(item["status"] == "ERROR" for item in results):
        status = "ERROR"
    return {"status": status, "checked_at": now.isoformat(), "db": str(path), "tickers": results}


def human(result: dict) -> str:
    lines = [f"MOEX_HEALTH {result['status']} · {result['checked_at']}"]
    for item in result["tickers"]:
        detail = "; ".join(item.get("issues", [])) or "semantic checks passed"
        lines.append(f"{item['ticker']} {item['status']} · {detail}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    load_project_env()
    result = semantic_health(args.db or default_db_path())
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else human(result))
    return 0 if result["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
