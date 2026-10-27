"""Сводка рыночных данных инструмента MOEX для торгового агента (Markdown, по-русски).

Отчёт содержит только фактические данные MOEX ISS и рассчитанные по ним
значения. Итоговый статус ЛОНГ/ШОРТ/ЖДАТЬ ставит агент по промпту
prompts/moex_elliott.md на основе этой сводки.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from .analysis import Snapshot, WaveCheck, snapshot
from .moex import MSK, Candle, MoexClient, Quote, resample

STALE_MINUTES = 20  # ISS без авторизации отдаёт данные с задержкой ~15 минут


def collect(client: MoexClient, days_1h: int = 30, days_1d: int = 400) -> tuple[Quote, Snapshot, Snapshot]:
    quote = client.get_quote()
    today = datetime.now(MSK).date()
    # Часовые свечи ISS публикуются после закрытия часа; хвост достраиваем из минутных.
    m1 = client.get_candles(1, today - timedelta(days=1))
    h1 = merge_tail(client.get_candles(60, today - timedelta(days=days_1h)), resample(m1, 60))
    # Дневная свеча текущего дня в ISS есть и обновляется в ходе торгов.
    d1 = client.get_candles(24, today - timedelta(days=days_1d))
    return quote, snapshot("1ч", h1), snapshot("1д", d1)


def merge_tail(base: list[Candle], fresh: list[Candle]) -> list[Candle]:
    """Дополняет `base` свечами из `fresh`, начинающимися позже последней свечи `base`."""
    if not base:
        return fresh
    return base + [c for c in fresh if c.begin > base[-1].begin]


def _f(v: float | None, nd: int = 2) -> str:
    return "н/д" if v is None else f"{v:.{nd}f}"


def _freshness(q: Quote, now: datetime) -> str:
    if q.last_trade_time is None:
        return "время последней сделки неизвестно"
    lag = (now - q.last_trade_time).total_seconds() / 60
    if lag <= STALE_MINUTES:
        return f"задержка ~{lag:.0f} мин (ISS без авторизации отдаёт данные с задержкой ~15 мин)"
    return (
        f"последняя сделка {lag:.0f} мин назад — торги не идут или данные устарели; "
        "цена НЕ является текущей"
    )


def _wave_md(w: WaveCheck, nd: int = 2) -> str:
    pts = " → ".join(f"{p:.{nd}f}" for p in w.points)
    lines = [f"- **{w.pattern} {w.direction}**: {pts} — {'правила соблюдены' if w.valid else 'ОТВЕРГНУТ'}"]
    for name, ok in w.rules:
        lines.append(f"  - {'✓' if ok else '✗'} {name}")
    if w.ratios:
        lines.append("  - соотношения: " + ", ".join(f"{k}={v}" for k, v in w.ratios.items()))
    for n in w.notes:
        lines.append(f"  - примечание: {n}")
    if w.invalidation is not None and w.valid:
        lines.append(f"  - уровень отмены разметки: {w.invalidation:.{nd}f}")
    return "\n".join(lines)


def time_format(s: Snapshot) -> str:
    """Формат времени свечи: для дневного таймфрейма — только дата."""
    return "%d.%m.%y" if s.timeframe == "1д" else "%d.%m %H:%M"


def _snapshot_md(s: Snapshot, nd: int = 2, pivots: int = 10, last: int = 12) -> str:
    tf = time_format(s)
    rel = []
    for name, v in (("MA50", s.sma50), ("MA200", s.sma200)):
        if v is not None:
            rel.append(f"{'выше' if s.close > v else 'ниже'} {name} ({v:.{nd}f})")
    out = [
        f"### Таймфрейм {s.timeframe}",
        f"Свечей: {len(s.candles)}, последняя открыта {s.candles[-1].begin:{"%Y-%m-%d" if s.timeframe == "1д" else "%Y-%m-%d %H:%M"}} МСК (может быть не закрыта), close {s.close:.{nd}f}.",
        f"MA50: {_f(s.sma50, nd)} · MA200: {_f(s.sma200, nd)} · RSI14: {_f(s.rsi14, 1)} · ATR14: {_f(s.atr14, nd + 1)}",
        f"Цена {', '.join(rel) if rel else 'н/д относительно MA (мало свечей)'}.",
        "",
        "Последние свечи (МСК, O/H/L/C/объём):",
        "",
        "| Время | O | H | L | C | Vol |",
        "|---|---|---|---|---|---|",
    ]
    for c in s.candles[-last:]:
        out.append(f"| {c.begin:{tf}} | {c.open:.{nd}f} | {c.high:.{nd}f} | {c.low:.{nd}f} | {c.close:.{nd}f} | {c.volume:.0f} |")
    out += ["", "Экстремумы (зигзаг 2×ATR; последний — не подтверждён):", ""]
    for p in s.pivots[-pivots:]:
        out.append(f"- {p.time:{tf}} {'макс' if p.kind == 'H' else 'мин'} {p.price:.{nd}f}")
    out += ["", "Уровни по кластерам экстремумов:", ""]
    for lv in s.resistances:
        out.append(f"- сопротивление {lv.price:.{nd}f} (касаний: {lv.touches})")
    for lv in s.supports:
        out.append(f"- поддержка {lv.price:.{nd}f} (касаний: {lv.touches})")
    if not (s.resistances or s.supports):
        out.append("- уровни не выделены")
    out += ["", "Кандидаты волновой разметки (автоматическая проверка правил):", ""]
    out += [_wave_md(w, nd) for w in s.waves] or ["- недостаточно экстремумов для разметки"]
    return "\n".join(out)


def render(quote: Quote, h1: Snapshot, d1: Snapshot, now: datetime | None = None) -> str:
    now = now or datetime.now(MSK)
    q = quote
    nd = q.decimals
    market = f"{q.engine}/{q.market}" if q.engine else "н/д"
    lines = [
        f"# {q.secid} — рыночные данные MOEX",
        "",
        f"Время формирования: {now:%Y-%m-%d %H:%M} МСК (UTC+3)",
        f"Источник: MOEX ISS, {q.secid}, режим {q.board} (рынок {market})",
        "",
        "## Котировка",
        "",
        f"- Последняя цена: **{_f(q.last, nd)}** (сделка {q.last_trade_time:%Y-%m-%d %H:%M:%S} МСК)" if q.last_trade_time else f"- Последняя цена: {_f(q.last, nd)}",
        f"- Актуальность: {_freshness(q, now)}",
        f"- Bid/Offer: {_f(q.bid, nd)} / {_f(q.offer, nd)}",
        f"- Дневной диапазон: O {_f(q.open, nd)} · H {_f(q.high, nd)} · L {_f(q.low, nd)}",
        f"- Изменение за день: {_f(q.change_pct)}%",
        f"- Объём: {_f(q.volume_today, 0)}",
        *([f"- Расчётная цена: {_f(q.settle_price, nd)} · ОИ: {_f(q.open_interest, 0)}"] if q.engine == "futures" else []),
        f"- Торговая дата: {q.trade_date}",
        "",
        "## Свечи и структура",
        "",
        "Часовые свечи — из ISS, незакрытый час достроен из 1-минутных; дневные — из ISS (текущий день не закрыт).",
        "",
        _snapshot_md(h1, nd),
        "",
        _snapshot_md(d1, nd),
        "",
    ]
    return "\n".join(lines)


def to_json(quote: Quote, h1: Snapshot, d1: Snapshot, last: int = 100) -> str:
    def snap(s: Snapshot) -> dict:
        return {
            "timeframe": s.timeframe,
            "close": s.close,
            "sma50": s.sma50,
            "sma200": s.sma200,
            "rsi14": s.rsi14,
            "atr14": s.atr14,
            "pivots": [{"time": p.time.isoformat(), "price": p.price, "kind": p.kind} for p in s.pivots],
            "resistances": [lv.__dict__ for lv in s.resistances],
            "supports": [lv.__dict__ for lv in s.supports],
            "waves": [w.__dict__ for w in s.waves],
            "candles": [c.to_dict() for c in s.candles[-last:]],
        }

    return json.dumps({"quote": quote.to_dict(), "h1": snap(h1), "d1": snap(d1)}, ensure_ascii=False, indent=2)
