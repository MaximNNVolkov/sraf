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


def collect(client: MoexClient, days_15m: int = 5, days_1h: int = 30) -> tuple[Quote, Snapshot, Snapshot]:
    quote = client.get_quote()
    today = datetime.now(MSK).date()
    if quote.source == "T-Invest API":
        m15 = [c for c in client.get_candles_minutes(15, today - timedelta(days=days_15m)) if c.is_complete]
        h1 = [c for c in client.get_candles(60, today - timedelta(days=days_1h)) if c.is_complete]
        return quote, snapshot("15м", m15), snapshot("1ч", h1)
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
        if q.source != "MOEX ISS":
            return f"последняя биржевая цена {lag:.0f} мин назад ({q.source})"
        return f"задержка ~{lag:.0f} мин (ISS без авторизации отдаёт данные с задержкой ~15 мин)"
    return (
        f"последняя сделка {lag:.0f} мин назад — торги не идут или данные устарели; "
        "цена НЕ является текущей"
    )


def _wave_md(w: WaveCheck, nd: int = 2) -> str:
    pts = " → ".join(f"{p:.{nd}f}" for p in w.points)
    if not w.valid:
        verdict = "ОТВЕРГНУТ"
    elif any(not ok for _, ok in w.rules):
        verdict = "соблюдены с оговоркой (см. ✗)"
    else:
        verdict = "правила соблюдены"
    lines = [f"- **{w.pattern} {w.direction}**: {pts} — {verdict}"]
    if w.tail_offset:
        lines.append(f"  - окно отстоит от последнего экстремума на {w.tail_offset} точк(и) зигзага")
    for name, ok in w.rules:
        lines.append(f"  - {'✓' if ok else '✗'} {name}")
    if w.ratios:
        lines.append("  - соотношения: " + ", ".join(f"{k}={v}" for k, v in w.ratios.items()))
    for n in w.notes:
        lines.append(f"  - примечание: {n}")
    if w.invalidation is not None and w.valid:
        lines.append(f"  - уровень отмены разметки: {w.invalidation:.{nd}f}")
    return "\n".join(lines)


def _snapshot_md(s: Snapshot, nd: int = 2, pivots: int = 10, last: int = 12) -> str:
    rel = []
    for name, v in (("MA50", s.sma50), ("MA200", s.sma200)):
        if v is not None:
            rel.append(f"{'выше' if s.close > v else 'ниже'} {name} ({v:.{nd}f})")
    out = [
        f"### Таймфрейм {s.timeframe}",
        f"Свечей: {len(s.candles)}, последняя открыта {s.candles[-1].begin:%Y-%m-%d %H:%M} МСК (может быть не закрыта), close {s.close:.{nd}f}.",
        f"MA50: {_f(s.sma50, nd)} · MA200: {_f(s.sma200, nd)} · RSI14: {_f(s.rsi14, 1)} · ATR14: {_f(s.atr14, nd + 1)}",
        f"Цена {', '.join(rel) if rel else 'н/д относительно MA (мало свечей)'}.",
        "",
        "Последние свечи (МСК, O/H/L/C/объём):",
        "",
        "| Время | O | H | L | C | Vol |",
        "|---|---|---|---|---|---|",
    ]
    for c in s.candles[-last:]:
        out.append(f"| {c.begin:%d.%m %H:%M} | {c.open:.{nd}f} | {c.high:.{nd}f} | {c.low:.{nd}f} | {c.close:.{nd}f} | {c.volume:.0f} |")
    out += ["", "Экстремумы (зигзаг 2×ATR; последний — не подтверждён):", ""]
    for p in s.pivots[-pivots:]:
        out.append(f"- {p.time:%d.%m %H:%M} {'макс' if p.kind == 'H' else 'мин'} {p.price:.{nd}f}")
    out += ["", "Уровни по кластерам экстремумов:", ""]
    for lv in s.resistances:
        out.append(f"- сопротивление {lv.price:.{nd}f} (касаний: {lv.touches})")
    for lv in s.supports:
        out.append(f"- поддержка {lv.price:.{nd}f} (касаний: {lv.touches})")
    if not (s.resistances or s.supports):
        out.append("- уровни не выделены")
    out += ["", "Кандидаты волновой разметки (скользящее окно, автоматическая проверка правил):", ""]
    if not s.waves:
        out.append("- недостаточно подтверждённых экстремумов для разметки")
    else:
        if all(not w.valid for w in s.waves):
            out.append("- ни одно окно не проходит правила; ниже самые свежие отклонённые варианты")
        out += [_wave_md(w, nd) for w in s.waves]
    return "\n".join(out)


def render(quote: Quote, m15: Snapshot, h1: Snapshot, now: datetime | None = None) -> str:
    now = now or datetime.now(MSK)
    q = quote
    nd = q.decimals
    market = f"{q.engine}/{q.market}" if q.engine else "н/д"
    lines = [
        f"# {q.secid} — рыночные данные MOEX",
        "",
        f"Время формирования: {now:%Y-%m-%d %H:%M} МСК (UTC+3)",
        f"Источник: {q.source}, {q.secid}, режим {q.board} (рынок {market})",
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
        ("Свечи T-Invest API; в анализ включены только завершённые свечи." if q.source == "T-Invest API" else
         "15-минутные свечи собраны из минутных ISS; часовые — из ISS с минутным хвостом."),
        "",
        _snapshot_md(m15, nd),
        "",
        _snapshot_md(h1, nd),
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


def render_swing(payload: dict) -> str:
    """Market context only; scheduled morning scripts separately persist a plan."""
    lines = [f"# {payload['ticker']} — контекст многодневной сделки", "",
             f"Источник: {payload['source']} · сформировано {payload['generated_at']}",
             f"Цена: {payload['price']} · время {payload['last_trade_time']} · возраст {payload['delay_minutes']} мин",
             "Горизонт: 3–20 торговых дней. W1 — режим, D1 — направление, H4 — подтверждение; H1 только для пробоя/ретеста."]
    if payload["stale"]:
        lines.append("Цена последней сессии — справочная, не текущая цена входа.")
    lines.extend(payload.get("history_issues", []))
    for key in ("w1", "d1", "h4", "h1"):
        s = payload[key]
        lines += ["", f"## {s['timeframe']}",
                  f"Завершённых свечей: {s['bars']} · история с {s['history_from']}",
                  f"Последний закрытый бар: {s['last_closed_candle']['begin']} · close {s['close']}",
                  f"MA20 {s['ma20']} · MA50 {s['ma50']} · MA200 {s['ma200']} · RSI14 {s['rsi14']} · ATR14 {s['atr14']}",
                  "Поддержки: " + (", ".join(str(x["price"]) for x in s["supports"]) or "не выделены"),
                  "Сопротивления: " + (", ".join(str(x["price"]) for x in s["resistances"]) or "не выделены")]
        lines.extend(_wave_md(WaveCheck(**w)) for w in s["waves"])
    lines += ["", "Это рыночный контекст. Сохранённый план и позиция проверяются утренним скриптом/монитором."]
    return "\n".join(lines) + "\n"
