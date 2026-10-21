"""Сводка рыночных данных USDRUBF для торгового агента (Markdown, по-русски).

Отчёт содержит только фактические данные MOEX ISS и рассчитанные по ним
значения. Итоговый статус ЛОНГ/ШОРТ/ЖДАТЬ ставит агент по промпту
prompts/usdrubf_elliott.md на основе этой сводки.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from .analysis import Snapshot, WaveCheck, snapshot
from .moex import MSK, Candle, MoexClient, Quote, resample

STALE_MINUTES = 20  # ISS без авторизации отдаёт данные с задержкой ~15 минут


def collect(client: MoexClient, days_15m: int = 5, days_1h: int = 30) -> tuple[Quote, Snapshot, Snapshot]:
    quote = client.get_quote()
    today = datetime.now(MSK).date()
    m1 = client.get_candles(1, today - timedelta(days=days_15m))
    m15 = resample(m1, 15)
    # Часовые свечи ISS публикуются после закрытия часа; хвост достраиваем из минутных.
    h1 = merge_tail(client.get_candles(60, today - timedelta(days=days_1h)), resample(m1, 60))
    return quote, snapshot("15м", m15), snapshot("1ч", h1)


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


def _wave_md(w: WaveCheck) -> str:
    pts = " → ".join(f"{p:.2f}" for p in w.points)
    lines = [f"- **{w.pattern} {w.direction}**: {pts} — {'правила соблюдены' if w.valid else 'ОТВЕРГНУТ'}"]
    for name, ok in w.rules:
        lines.append(f"  - {'✓' if ok else '✗'} {name}")
    if w.ratios:
        lines.append("  - соотношения: " + ", ".join(f"{k}={v}" for k, v in w.ratios.items()))
    for n in w.notes:
        lines.append(f"  - примечание: {n}")
    if w.invalidation is not None and w.valid:
        lines.append(f"  - уровень отмены разметки: {w.invalidation:.2f}")
    return "\n".join(lines)


def _snapshot_md(s: Snapshot, pivots: int = 10, last: int = 12) -> str:
    rel = []
    for name, v in (("MA50", s.sma50), ("MA200", s.sma200)):
        if v is not None:
            rel.append(f"{'выше' if s.close > v else 'ниже'} {name} ({v:.2f})")
    out = [
        f"### Таймфрейм {s.timeframe}",
        f"Свечей: {len(s.candles)}, последняя открыта {s.candles[-1].begin:%Y-%m-%d %H:%M} МСК (может быть не закрыта), close {s.close:.2f}.",
        f"MA50: {_f(s.sma50)} · MA200: {_f(s.sma200)} · RSI14: {_f(s.rsi14, 1)} · ATR14: {_f(s.atr14, 3)}",
        f"Цена {', '.join(rel) if rel else 'н/д относительно MA (мало свечей)'}.",
        "",
        "Последние свечи (МСК, O/H/L/C/объём):",
        "",
        "| Время | O | H | L | C | Vol |",
        "|---|---|---|---|---|---|",
    ]
    for c in s.candles[-last:]:
        out.append(f"| {c.begin:%d.%m %H:%M} | {c.open:.2f} | {c.high:.2f} | {c.low:.2f} | {c.close:.2f} | {c.volume:.0f} |")
    out += ["", "Экстремумы (зигзаг 2×ATR; последний — не подтверждён):", ""]
    for p in s.pivots[-pivots:]:
        out.append(f"- {p.time:%d.%m %H:%M} {'макс' if p.kind == 'H' else 'мин'} {p.price:.2f}")
    out += ["", "Уровни по кластерам экстремумов:", ""]
    for lv in s.resistances:
        out.append(f"- сопротивление {lv.price:.2f} (касаний: {lv.touches})")
    for lv in s.supports:
        out.append(f"- поддержка {lv.price:.2f} (касаний: {lv.touches})")
    if not (s.resistances or s.supports):
        out.append("- уровни не выделены")
    out += ["", "Кандидаты волновой разметки (автоматическая проверка правил):", ""]
    out += [_wave_md(w) for w in s.waves] or ["- недостаточно экстремумов для разметки"]
    return "\n".join(out)


def render(quote: Quote, m15: Snapshot, h1: Snapshot, now: datetime | None = None) -> str:
    now = now or datetime.now(MSK)
    q = quote
    lines = [
        "# USDRUBF — рыночные данные MOEX",
        "",
        f"Время формирования: {now:%Y-%m-%d %H:%M} МСК (UTC+3)",
        f"Источник: MOEX ISS, {q.secid}, режим {q.board} (рынок FORTS, вечный фьючерс доллар/рубль)",
        "",
        "## Котировка",
        "",
        f"- Последняя цена: **{_f(q.last)}** (сделка {q.last_trade_time:%Y-%m-%d %H:%M:%S} МСК)" if q.last_trade_time else f"- Последняя цена: {_f(q.last)}",
        f"- Актуальность: {_freshness(q, now)}",
        f"- Bid/Offer: {_f(q.bid)} / {_f(q.offer)}",
        f"- Дневной диапазон: O {_f(q.open)} · H {_f(q.high)} · L {_f(q.low)}",
        f"- Изменение к пред. расчётной: {_f(q.change_pct)}%",
        f"- Расчётная цена: {_f(q.settle_price)} · ОИ: {_f(q.open_interest, 0)} · Объём (контр.): {_f(q.volume_today, 0)}",
        f"- Торговая дата: {q.trade_date}",
        "",
        "## Свечи и структура",
        "",
        "15-минутные свечи собраны из 1-минутных свечей ISS; часовые — из ISS, незакрытые часы достроены из 1-минутных.",
        "",
        _snapshot_md(m15),
        "",
        _snapshot_md(h1),
        "",
    ]
    return "\n".join(lines)


def to_json(quote: Quote, m15: Snapshot, h1: Snapshot, last: int = 100) -> str:
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

    return json.dumps({"quote": quote.to_dict(), "m15": snap(m15), "h1": snap(h1)}, ensure_ascii=False, indent=2)
