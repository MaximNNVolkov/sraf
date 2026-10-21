"""CLI: python -m usdrubf {quote,candles,report}."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta

from .moex import MSK, MoexClient
from .report import collect, render, to_json


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="usdrubf", description="Данные MOEX ISS для USDRUBF")
    ap.add_argument("--secid", default="USDRUBF")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("quote", help="текущая котировка (JSON)")

    c = sub.add_parser("candles", help="история свечей (CSV)")
    c.add_argument("-t", "--timeframe", type=int, default=15, help="минуты: 1, 5, 10, 15, 30, 60, 240; 1440 — день")
    c.add_argument("--from", dest="start", type=date.fromisoformat, help="YYYY-MM-DD (по умолчанию — 5 дней назад)")
    c.add_argument("--till", type=date.fromisoformat)

    r = sub.add_parser("report", help="сводка для торгового агента")
    r.add_argument("--json", action="store_true", help="вывести JSON вместо Markdown")
    r.add_argument("-o", "--output", help="записать в файл")

    args = ap.parse_args(argv)
    client = MoexClient(args.secid)

    if args.cmd == "quote":
        print(json.dumps(client.get_quote().to_dict(), ensure_ascii=False, indent=2))
    elif args.cmd == "candles":
        start = args.start or datetime.now(MSK).date() - timedelta(days=5)
        if args.timeframe == 1440:
            candles = client.get_candles(24, start, args.till)
        else:
            candles = client.get_candles_minutes(args.timeframe, start, args.till)
        w = csv.writer(sys.stdout)
        w.writerow(["begin", "open", "high", "low", "close", "volume"])
        for k in candles:
            w.writerow([k.begin.strftime("%Y-%m-%d %H:%M"), k.open, k.high, k.low, k.close, k.volume])
    else:
        quote, m15, h1 = collect(client)
        text = to_json(quote, m15, h1) if args.json else render(quote, m15, h1)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text)
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
