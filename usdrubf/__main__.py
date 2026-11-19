"""CLI: python -m usdrubf [--secid ТИКЕР] {quote,candles,report,chart}."""

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
from .moex import MSK, MoexClient, MoexError
from .report import collect, render, render_swing, to_json
from .tbank import TBankClient
from .trading import DEFAULT_DB, close_trade, connect, migrate_legacy_markdown, open_positions, open_trade, render_markdown, set_stop

LEVEL_COLORS = {"red": "#d64545", "green": "#1e9e6a", "blue": "#1f5fbf", "gray": "#555555", "orange": "#e08a00"}
TF_NAMES = {15: "15m", 60: "1h", 240: "4h", 1440: "1d", 10080: "1w"}


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
    try:
        return run(argv)
    except MoexError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="usdrubf", description="Биржевые данные T-Invest, торговая аналитика; ISS — дополнительный источник")
    ap.add_argument("--secid", default="USDRUBF", help="тикер MOEX: USDRUBF, SBER, CNYRUB_TOM, IMOEX, SiZ6…")
    ap.add_argument("--source", choices=["tbank", "iss"], default="tbank", help="источник данных, по умолчанию T-Invest")
    ap.add_argument("--engine", help="торговая система ISS (futures, stock, currency); по умолчанию — из основного режима")
    ap.add_argument("--market", help="рынок ISS (forts, shares, selt, index, bonds); по умолчанию — из основного режима")
    ap.add_argument("--board", help="режим торгов (RFUD, TQBR, CETS, SNDX…); по умолчанию — основной")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("quote", help="текущая котировка (JSON)")

    c = sub.add_parser("candles", help="история свечей (CSV)")
    c.add_argument("-t", "--timeframe", type=int, default=15, help="минуты: 1, 5, 10, 15, 30, 60, 240; 1440 — день")
    c.add_argument("--from", dest="start", type=date.fromisoformat, help="YYYY-MM-DD (по умолчанию — 5 дней назад)")
    c.add_argument("--till", type=date.fromisoformat)

    r = sub.add_parser("report", help="сводка для торгового агента")
    r.add_argument("--json", action="store_true", help="вывести JSON вместо Markdown")
    r.add_argument("--intraday", action="store_true", help="прежний подробный отчёт 15м/1ч вместо контекста 1н/1д/4ч/1ч")
    r.add_argument("-o", "--output", help="записать в файл")

    g = sub.add_parser("chart", help="SVG-график свечей с волнами и уровнями")
    g.add_argument("-t", "--timeframe", type=int, action="append", choices=sorted(TF_NAMES), help="15/60/240/1440/10080; по умолчанию 240 и 1440")
    g.add_argument("--last", type=int, help="сколько свечей показать (по умолчанию 80 для 15м, 120 для 1ч)")
    g.add_argument("--level", action="append", type=parse_level, default=[], metavar="ЦЕНА:ПОДПИСЬ[:ЦВЕТ]",
                   help="уровень на графике; если задан хотя бы один — автоуровни не рисуются")
    g.add_argument("--waves", action="append", type=parse_waves, default=[], metavar="ТФ=0,A,B,C",
                   help="подписи последних экстремумов зигзага для таймфрейма")
    g.add_argument("-o", "--outdir", default="charts", help="каталог для файлов (по умолчанию charts/)")
    g.add_argument("--png", action="store_true", help="дополнительно сохранить PNG (Chromium, rsvg-convert или ImageMagick)")

    t = sub.add_parser("trade", help="структурированный журнал сделок")
    t.add_argument("--db", default=str(DEFAULT_DB))
    ts = t.add_subparsers(dest="trade_cmd", required=True)
    ts.add_parser("list")
    mg = ts.add_parser("migrate"); mg.add_argument("markdown", nargs="?", default="OPEN_POSITIONS.md")
    op = ts.add_parser("open")
    op.add_argument("ticker"); op.add_argument("direction", choices=["LONG", "SHORT"])
    op.add_argument("entry", type=float); op.add_argument("--quantity", type=float)
    op.add_argument("--stop", type=float); op.add_argument("--tp1", type=float); op.add_argument("--tp2", type=float)
    ss = ts.add_parser("set-stop"); ss.add_argument("trade_id"); ss.add_argument("price", type=float)
    pc = ts.add_parser("partial-close"); pc.add_argument("trade_id"); pc.add_argument("price", type=float); pc.add_argument("quantity", type=float)
    cl = ts.add_parser("close"); cl.add_argument("trade_id"); cl.add_argument("price", type=float); cl.add_argument("--reason", default="user")
    rm = ts.add_parser("render-markdown"); rm.add_argument("--output", default="OPEN_POSITIONS.md")

    args = ap.parse_args(argv)
    if args.cmd == "trade":
        db = connect(args.db)
        if args.trade_cmd == "list":
            print(json.dumps(open_positions(db), ensure_ascii=False, indent=2))
        elif args.trade_cmd == "migrate":
            print(migrate_legacy_markdown(db, args.markdown))
        elif args.trade_cmd == "open":
            print(open_trade(db, args.ticker, args.direction, args.entry, args.quantity,
                             stop=args.stop, tp1=args.tp1, tp2=args.tp2))
        elif args.trade_cmd == "set-stop":
            set_stop(db, args.trade_id, args.price)
        elif args.trade_cmd == "partial-close":
            close_trade(db, args.trade_id, args.price, args.quantity, "partial")
        elif args.trade_cmd == "close":
            close_trade(db, args.trade_id, args.price, reason=args.reason)
        else:
            text = render_markdown(db)
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(args.output)
        return 0
    if args.source == "tbank":
        if args.engine or args.market:
            raise MoexError("Параметры --engine/--market относятся к --source iss")
        client = TBankClient(args.secid, board=args.board)
    else:
        client = MoexClient(args.secid, args.engine, args.market, args.board)

    if args.cmd == "quote":
        print(json.dumps(client.get_quote().to_dict(), ensure_ascii=False, indent=2))
    elif args.cmd == "candles":
        start = args.start or datetime.now(MSK).date() - timedelta(days=5)
        if args.timeframe == 1440:
            candles = client.get_candles(24, start, args.till)
        else:
            candles = client.get_candles_minutes(args.timeframe, start, args.till)
        w = csv.writer(sys.stdout)
        w.writerow(["begin", "open", "high", "low", "close", "volume", "is_complete"])
        for k in candles:
            w.writerow([k.begin.strftime("%Y-%m-%d %H:%M"), k.open, k.high, k.low, k.close, k.volume, k.is_complete])
    elif args.cmd == "chart":
        from .analysis import snapshot
        quote = client.get_quote()
        selected = args.timeframe or [240, 1440]
        snaps = {tf: snapshot(TF_NAMES[tf], [c for c in client.get_candles_minutes(
            tf, datetime.now(MSK).date() - timedelta(days={15: 10, 60: 45, 240: 180, 1440: 1100, 10080: 1800}[tf])) if c.is_complete]) for tf in selected}
        waves = dict(args.waves)
        os.makedirs(args.outdir, exist_ok=True)
        for tf in selected:
            svg = render_svg(
                snaps[tf],
                last=args.last or (80 if tf == 15 else 120),
                levels=args.level or None,
                waves=waves.get(tf),
                title=f"{quote.secid} · {snaps[tf].timeframe} · {quote.source}",
                decimals=quote.decimals,
            )
            path = os.path.join(args.outdir, f"{quote.secid.lower()}_{TF_NAMES[tf]}.svg")
            with open(path, "w", encoding="utf-8") as f:
                f.write(svg)
            print(path)
            if args.png:
                print(to_png(path) or "PNG не создан: нет headless Chromium (Playwright), rsvg-convert или ImageMagick")
    else:
        if args.source == "tbank" and not args.intraday:
            from .process import collect_compact
            payload, _, _ = collect_compact(args.secid, client=client)
            text = json.dumps(payload, ensure_ascii=False, indent=2) if args.json else render_swing(payload)
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
