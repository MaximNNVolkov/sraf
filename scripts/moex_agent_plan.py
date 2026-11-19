#!/usr/bin/env python3
"""Local feedback CLI for Hermes. Records plans/reviews; never sends orders."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from usdrubf.agent import get_result, submit_decision
from usdrubf.moex import MSK
from usdrubf.process import STALE_MINUTES
from usdrubf.tbank import TBankClient, load_project_env
from usdrubf.trading import DEFAULT_DB, connect
from tinkoff_positions import fetch_state


def live_guard(ticker):
    broker = fetch_state()
    client = TBankClient(ticker)
    quote = client.get_quote()
    status = client.trading_status()
    delay = None if quote.last_trade_time is None else (datetime.now(MSK) - quote.last_trade_time).total_seconds() / 60
    return broker, {"ticker": ticker, "price": quote.last, "delay_minutes": delay,
                    "stale": delay is None or delay > STALE_MINUTES,
                    "trading_status": status.get("tradingStatus"),
                    "market_open": status.get("tradingStatus") == "SECURITY_TRADING_STATUS_NORMAL_TRADING"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.environ.get("MOEX_TRADING_DB", str(DEFAULT_DB)))
    sub = parser.add_subparsers(dest="command", required=True)
    submit = sub.add_parser("submit")
    submit.add_argument("--file", required=True, help="JSON path; - reads stdin")
    get = sub.add_parser("get")
    get.add_argument("--ticker", required=True, choices=["USDRUBF", "IMOEXF"])
    args = parser.parse_args(argv)
    load_project_env()
    try:
        with connect(args.db) as db:
            if args.command == "get":
                result = get_result(db, args.ticker)
            else:
                raw = sys.stdin.read(262145) if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
                if len(raw) > 262144:
                    raise ValueError("JSON превышает 256 КБ")
                data = json.loads(raw)
                broker, market = None, None
                if isinstance(data, dict) and data.get("decision") in ("LONG_SETUP", "SHORT_SETUP"):
                    # Repeated requests get the original receipt without any API dependency.
                    import hashlib
                    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=True).encode()).hexdigest()
                    accepted = db.execute("SELECT 1 FROM agent_feedback WHERE context_id=? AND request_hash=? AND status='ACCEPTED'",
                                          (data.get("context_id"), digest)).fetchone()
                    if not accepted:
                        if data.get("ticker") not in ("IMOEXF", "USDRUBF"):
                            raise ValueError("Разрешены только IMOEXF и USDRUBF")
                        broker, market = live_guard(data["ticker"])
                result = submit_decision(db, data, broker=broker, market=market)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 2 if result.get("status") == "REJECTED" else 0
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"status": "REJECTED", "errors": [str(exc)], "broker_orders_sent": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
