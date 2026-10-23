"""CLI: python -m usdrubf {quote,candles,report}."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta

from .chart import H, W, ChartLevel, render_svg
from .moex import MSK, MoexClient
from .report import collect, render, to_json

LEVEL_COLORS = {"red": "#d64545", "green": "#1e9e6a", "blue": "#1f5fbf", "gray": "#555555", "orange": "#e08a00"}
TF_NAMES = {15: "15m", 60: "1h"}


def parse_level(s: str) -> ChartLevel:
    """ЦЕНА:ПОДПИСЬ[:ЦВЕТ], цвет — red/green/blue/gray/orange или #rrggbb."""
    price, _, rest = s.partition(":")
    label, _, color = rest.partition(":")
    color = LEVEL_COLORS.get(color, color) or LEVEL_COLORS["blue"]
    return ChartLevel(float(price), label or price, color)


def parse_waves(s: str) -> tuple[int, list[str]]:
    """ТФ=ПОДПИСИ, например 60=0,A,B,C — подписи последних экстремумов зигзага."""
    tf, _, labels = s.partition("=")
    return int(tf), [x.strip() for x in labels.split(",") if x.strip()]


def to_png(svg_path: str) -> str | None:
    """PNG рядом с SVG: headless Chromium (Playwright), rsvg-convert или ImageMagick — что найдётся."""
    png = svg_path[:-4] + ".png"
    shells = sorted(glob.glob("/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell"))
    cmds = [
        [b, "--no-sandbox", "--disable-gpu", "--hide-scrollbars", f"--window-size={W},{H}",
         f"--screenshot={os.path.abspath(png)}", "file://" + os.path.abspath(svg_path)]
        for b in shells[-1:]
    ]
    cmds += [["rsvg-convert", "-o", png, svg_path], ["convert", "-density", "96", svg_path, png]]
    for cmd in cmds:
        if shutil.which(cmd[0]) and subprocess.run(cmd, capture_output=True, timeout=120).returncode == 0 and os.path.exists(png):
            return png
    return None


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

    g = sub.add_parser("chart", help="SVG-график свечей с волнами и уровнями")
    g.add_argument("-t", "--timeframe", type=int, action="append", choices=sorted(TF_NAMES), help="15 и/или 60 (по умолчанию оба)")
    g.add_argument("--last", type=int, help="сколько свечей показать (по умолчанию 80 для 15м, 120 для 1ч)")
    g.add_argument("--level", action="append", type=parse_level, default=[], metavar="ЦЕНА:ПОДПИСЬ[:ЦВЕТ]",
                   help="уровень на графике; если задан хотя бы один — автоуровни не рисуются")
    g.add_argument("--waves", action="append", type=parse_waves, default=[], metavar="ТФ=0,A,B,C",
                   help="подписи последних экстремумов зигзага для таймфрейма")
    g.add_argument("-o", "--outdir", default="charts", help="каталог для файлов (по умолчанию charts/)")
    g.add_argument("--png", action="store_true", help="дополнительно сохранить PNG (rsvg-convert или ImageMagick)")

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
    elif args.cmd == "chart":
        _, m15, h1 = collect(client)
        snaps = {15: m15, 60: h1}
        waves = dict(args.waves)
        os.makedirs(args.outdir, exist_ok=True)
        for tf in args.timeframe or [15, 60]:
            svg = render_svg(
                snaps[tf],
                last=args.last or (80 if tf == 15 else 120),
                levels=args.level or None,
                waves=waves.get(tf),
            )
            path = os.path.join(args.outdir, f"usdrubf_{TF_NAMES[tf]}.svg")
            with open(path, "w", encoding="utf-8") as f:
                f.write(svg)
            print(path)
            if args.png:
                print(to_png(path) or "PNG не создан: нет rsvg-convert/ImageMagick")
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
