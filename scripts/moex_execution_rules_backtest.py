#!/usr/bin/env python3
"""Offline, checksum-verified execution-rule backtest for the fixed D1 model.

This is a diagnostic study, not a trading simulator.  It never contacts MOEX
or a broker and never changes the shared candle cache.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import platform
import shlex
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Sequence
from zoneinfo import ZoneInfo

try:  # module import in tests / direct file execution by the audit command
    from scripts.moex_medium_term_trend_backtest import (
        BacktestError, DailyCandle, forecast_from_window, read_cache, subtract_years,
    )
except ModuleNotFoundError:
    from moex_medium_term_trend_backtest import (  # type: ignore[no-redef]
        BacktestError, DailyCandle, forecast_from_window, read_cache, subtract_years,
    )


TICKERS = ("IMOEXF", "USDRUBF")
WINDOW, HORIZON, ATR_PERIOD = 60, 10, 14
STOP_MULTIPLIERS = (1.0, 1.5, 2.0)
ENTRY_VARIANTS = ("close_t", "next_open")
MSK = ZoneInfo("Europe/Moscow")


@dataclass(frozen=True)
class Signal:
    ticker: str
    index: int
    day: date
    direction: str
    close: float
    next_open: float
    atr14: float
    fact: str
    direction_correct: bool


@dataclass(frozen=True)
class Scenario:
    ticker: str
    day: date
    entry_variant: str
    stop_multiple: float
    direction: str
    directional_correct: bool
    possible: bool
    stop_before_tp1: bool = False
    tp1_before_stop: bool = False
    tp2_before_stop: bool = False
    neither_stop_nor_tp1: bool = False
    ambiguous_tp1: bool = False
    ambiguous_tp2: bool = False
    tp1_r: float | None = None
    tp2_r: float | None = None


def wilder_atr(candles: Sequence[DailyCandle], period: int = ATR_PERIOD) -> list[float | None]:
    """Wilder ATR: first TR is H-L; seed is mean(first period TRs)."""
    if period < 1:
        raise ValueError("ATR period должен быть положительным")
    trs: list[float] = []
    for index, candle in enumerate(candles):
        previous = candles[index - 1].close if index else candle.close
        trs.append(max(candle.high - candle.low, abs(candle.high - previous), abs(candle.low - previous)))
    result: list[float | None] = [None] * len(candles)
    if len(candles) < period:
        return result
    value = sum(trs[:period]) / period
    result[period - 1] = value
    for index in range(period, len(candles)):
        value = ((period - 1) * value + trs[index]) / period
        result[index] = value
    return result


def build_signals(ticker: str, candles: Sequence[DailyCandle], as_of: date) -> tuple[list[Signal], dict[str, int]]:
    completed = [candle for candle in candles if candle.day < as_of]
    start = subtract_years(as_of, 2)
    atrs = wilder_atr(completed)
    signals: list[Signal] = []
    coverage = {"completed": len(completed), "outside_period": 0, "warmup": 0, "no_full_horizon": 0}
    for index, candle in enumerate(completed):
        if candle.day < start:
            coverage["outside_period"] += 1
            continue
        if index < WINDOW - 1:
            coverage["warmup"] += 1
            continue
        if index + HORIZON >= len(completed):
            coverage["no_full_horizon"] += 1
            continue
        direction, _ = forecast_from_window(completed[index - WINDOW + 1:index + 1])
        future_close = completed[index + HORIZON].close
        fact = "UP" if future_close > candle.close else "DOWN" if future_close < candle.close else "NEUTRAL"
        atr = atrs[index]
        if atr is None:
            raise BacktestError(f"ATR14 неожиданно недоступен: {ticker} {candle.day}")
        signals.append(Signal(
            ticker, index, candle.day, direction, candle.close, completed[index + 1].open,
            atr, fact, fact == direction,
        ))
    coverage["eligible_signals"] = len(signals)
    coverage["neutral_facts"] = sum(item.fact == "NEUTRAL" for item in signals)
    return signals, coverage


def _touch(candle: DailyCandle, level: float, direction: str, kind: str) -> bool:
    if kind == "stop":
        return candle.low <= level if direction == "UP" else candle.high >= level
    return candle.high >= level if direction == "UP" else candle.low <= level


def evaluate_scenario(signal: Signal, candles: Sequence[DailyCandle], entry_variant: str, multiple: float) -> Scenario:
    if entry_variant not in ENTRY_VARIANTS or multiple not in STOP_MULTIPLIERS:
        raise ValueError("Неизвестный вариант входа или стопа")
    entry = signal.close if entry_variant == "close_t" else signal.next_open
    risk = multiple * signal.atr14
    possible = all(math.isfinite(value) and value > 0 for value in (entry, risk))
    base = dict(ticker=signal.ticker, day=signal.day, entry_variant=entry_variant,
                stop_multiple=multiple, direction=signal.direction,
                directional_correct=signal.direction_correct, possible=possible)
    if not possible:
        return Scenario(**base)
    side = 1 if signal.direction == "UP" else -1
    stop, tp1, tp2 = entry - side * risk, entry + side * risk, entry + side * 2 * risk
    path = candles[signal.index + 1:signal.index + HORIZON + 1]

    first_tp1: str | None = None
    first_tp2: str | None = None
    ambiguous1 = ambiguous2 = False
    for candle in path:
        stop_hit = _touch(candle, stop, signal.direction, "stop")
        tp1_hit = _touch(candle, tp1, signal.direction, "target")
        tp2_hit = _touch(candle, tp2, signal.direction, "target")
        if first_tp1 is None and (stop_hit or tp1_hit):
            if stop_hit and tp1_hit:
                first_tp1, ambiguous1 = "STOP_FIRST", True
            else:
                first_tp1 = "STOP" if stop_hit else "TP1"
        if first_tp2 is None and (stop_hit or tp2_hit):
            if stop_hit and tp2_hit:
                first_tp2, ambiguous2 = "STOP_FIRST", True
            else:
                first_tp2 = "STOP" if stop_hit else "TP2"
        if first_tp1 is not None and first_tp2 is not None:
            break

    return Scenario(
        **base,
        stop_before_tp1=first_tp1 in ("STOP", "STOP_FIRST"),
        tp1_before_stop=first_tp1 == "TP1",
        tp2_before_stop=first_tp2 == "TP2",
        neither_stop_nor_tp1=first_tp1 is None,
        ambiguous_tp1=ambiguous1,
        ambiguous_tp2=ambiguous2,
        tp1_r=(-1.0 if first_tp1 == "STOP" else 1.0 if first_tp1 == "TP1" else None),
        tp2_r=(-1.0 if first_tp2 == "STOP" else 2.0 if first_tp2 == "TP2" else None),
    )


def evaluate_all(signals_by_ticker: dict[str, list[Signal]], candles_by_ticker: dict[str, list[DailyCandle]]) -> list[Scenario]:
    return [
        evaluate_scenario(signal, candles_by_ticker[ticker], variant, multiple)
        for ticker in TICKERS for signal in signals_by_ticker[ticker]
        for variant in ENTRY_VARIANTS for multiple in STOP_MULTIPLIERS
    ]


def summarize(items: Sequence[Scenario]) -> dict[str, float | int | None]:
    possible = [item for item in items if item.possible]
    total, n = len(items), len(possible)
    pct = lambda count: (100 * count / n) if n else None
    tp1_values = [item.tp1_r for item in possible if item.tp1_r is not None]
    tp2_values = [item.tp2_r for item in possible if item.tp2_r is not None]
    stop1_n = sum(item.stop_before_tp1 for item in possible)
    tp1_n = sum(item.tp1_before_stop for item in possible)
    tp2_n = sum(item.tp2_before_stop for item in possible)
    neither_n = sum(item.neither_stop_nor_tp1 for item in possible)
    direction_n = sum(item.directional_correct for item in possible)
    failure_n = sum(item.directional_correct and item.stop_before_tp1 for item in possible)
    return {
        "signals": total, "possible": n, "impossible": total - n,
        "coverage_pct": 100 * n / total if total else None,
        "stop_before_tp1_n": stop1_n, "stop_before_tp1_pct": pct(stop1_n),
        "tp1_before_stop_n": tp1_n, "tp1_before_stop_pct": pct(tp1_n),
        "tp2_before_stop_n": tp2_n, "tp2_before_stop_pct": pct(tp2_n),
        "neither_n": neither_n, "neither_pct": pct(neither_n),
        "ambiguous_tp1": sum(item.ambiguous_tp1 for item in possible),
        "ambiguous_tp2": sum(item.ambiguous_tp2 for item in possible),
        "tp1_exact_n": len(tp1_values), "tp1_mean_r": mean(tp1_values) if tp1_values else None,
        "tp2_exact_n": len(tp2_values), "tp2_mean_r": mean(tp2_values) if tp2_values else None,
        "directional_correct_n": direction_n, "directional_correct_pct": pct(direction_n),
        "key_failure_n": failure_n, "key_failure_pct": pct(failure_n),
    }


def phase0_days(signals: Sequence[Signal]) -> set[date]:
    return {item.day for item in signals[0::10]}


def _fmt(value: object, digits: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _summary_rows(scenarios: Sequence[Scenario], phase_days: dict[str, set[date]] | None = None) -> str:
    rows = []
    for ticker in TICKERS:
        for variant in ENTRY_VARIANTS:
            for multiple in STOP_MULTIPLIERS:
                selected = [item for item in scenarios if item.ticker == ticker and item.entry_variant == variant and item.stop_multiple == multiple]
                if phase_days is not None:
                    selected = [item for item in selected if item.day in phase_days[ticker]]
                s = summarize(selected)
                rows.append("<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in (
                    ticker, "close t" if variant == "close_t" else "open t+1", _fmt(multiple),
                    f"{s['possible']}/{s['signals']} ({_fmt(s['coverage_pct'])}%)",
                    f"{s['stop_before_tp1_n']}/{s['possible']} ({_fmt(s['stop_before_tp1_pct'])}%)",
                    f"{s['tp1_before_stop_n']}/{s['possible']} ({_fmt(s['tp1_before_stop_pct'])}%)",
                    f"{s['tp2_before_stop_n']}/{s['possible']} ({_fmt(s['tp2_before_stop_pct'])}%)",
                    f"{s['neither_n']}/{s['possible']} ({_fmt(s['neither_pct'])}%)",
                    f"{s['ambiguous_tp1']}/{s['ambiguous_tp2']}",
                    f"{_fmt(s['tp1_mean_r'], 3)} (n={s['tp1_exact_n']})",
                    f"{_fmt(s['tp2_mean_r'], 3)} (n={s['tp2_exact_n']})",
                )) + "</tr>")
    return "".join(rows)


def render_html(signals: dict[str, list[Signal]], scenarios: list[Scenario], coverage: dict[str, dict[str, int]],
                manifests: dict[str, dict], audit: dict, as_of: date) -> str:
    phase_days = {ticker: phase0_days(items) for ticker, items in signals.items()}
    directional_rows = []
    for ticker in TICKERS:
        base = signals[ticker]
        correct = sum(item.direction_correct for item in base)
        neutral = sum(item.fact == "NEUTRAL" for item in base)
        directional_rows.append(f"<tr><td>{ticker}</td><td>{len(base)}</td><td>{correct} ({100*correct/len(base):.1f}%)</td><td>{neutral}</td></tr>")
    failure_rows = []
    for ticker in TICKERS:
        for variant in ENTRY_VARIANTS:
            for multiple in STOP_MULTIPLIERS:
                subset = [x for x in scenarios if x.ticker == ticker and x.entry_variant == variant and x.stop_multiple == multiple]
                s = summarize(subset)
                failure_rows.append(f"<tr><td>{ticker}</td><td>{'close t' if variant == 'close_t' else 'open t+1'}</td><td>{multiple:g} ATR</td><td>{s['directional_correct_n']}/{s['possible']} ({_fmt(s['directional_correct_pct'])}%)</td><td>{s['key_failure_n']}/{s['possible']} ({_fmt(s['key_failure_pct'])}%)</td></tr>")
    provenance = []
    for ticker in TICKERS:
        manifest = manifests[ticker]
        urls = "".join(f"<li><code>{html.escape(url)}</code></li>" for url in manifest.get("request_urls", []))
        provenance.append(f"<h3>{ticker}</h3><p>CSV SHA-256: <code>{manifest['csv_sha256']}</code>; строк: {manifest.get('rows')}; диапазон: {manifest.get('first_day')}—{manifest.get('last_day')}; fetched-at MSK: {manifest.get('fetched_at_msk')}.</p><ul>{urls}</ul>")
    coverage_rows = "".join(
        f"<tr><td>{ticker}</td><td>{values['completed']}</td><td>{values['outside_period']}</td><td>{values['warmup']}</td><td>{values['no_full_horizon']}</td><td>{values['eligible_signals']}</td><td>{values['neutral_facts']}</td></tr>"
        for ticker, values in coverage.items()
    )
    command = html.escape(audit["command"])
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MOEX: execution rules backtest</title><style>
body{{font:15px/1.45 system-ui,sans-serif;color:#17202a;max-width:1500px;margin:auto;padding:28px}}h1,h2{{color:#102a43}}table{{border-collapse:collapse;width:100%;margin:12px 0 24px}}th,td{{border:1px solid #ccd6e0;padding:7px;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#eaf2f8}}code{{overflow-wrap:anywhere}}.warn{{background:#fff4d6;border-left:5px solid #d99b00;padding:12px}}.note{{color:#52606d}}@media(max-width:900px){{body{{padding:12px}}table{{font-size:12px}}}}
</style></head><body>
<h1>Исторический execution-бэктест: направление или стоп/вход</h1>
<p class="warn"><strong>Диагностическое учебное исследование.</strong> Это не реальная доходность и не торговая рекомендация: комиссии, проскальзывание, гэп-исполнение, ликвидность и гарантийное обеспечение не моделируются. Уровни считаются исполненными ровно по заданной цене.</p>
<p>Срез данных: завершённые D1-сессии строго раньше <strong>{as_of}</strong>; календарное окно t: [{subtract_years(as_of, 2)}, {as_of}). Генерация: {audit['generated_at_msk']}.</p>

<h2>Точные правила</h2>
<ol><li>Фиксированная модель без подбора: ровно 60 close через t; голоса close−SMA20, SMA20−SMA60 и momentum20 (close t / close t−20 − 1). Большинство задаёт UP/DOWN; нулевая сумма разрешается SMA20−SMA60, затем momentum20, затем close−SMA20, полностью плоское окно = DOWN.</li>
<li>ATR14 Уайлдера использует только данные по t включительно: TR=max(H−L, |H−Cprev|, |L−Cprev|); seed — среднее первых 14 TR (у первой свечи TR=H−L), далее (13·ATRprev+TR)/14.</li>
<li>A: учебный вход по close t. B: вход по фактическому open t+1. В обоих случаях стоп и TP строятся от цены входа, R = k·ATR14(t), k∈{{1;1,5;2}}, TP1=1R, TP2=2R. Проверяются ровно 10 свечей t+1…t+10.</li>
<li>Если одна D1-свеча касается стопа и цели, порядок неизвестен: <strong>STOP_FIRST/ambiguous</strong>. Для консервативной доли это stop-first; из средней R случай исключён. Средняя R дана отдельно для TP1-скобки (−1/+1R) и TP2-скобки (−1/+2R), только по точно определённым первым касаниям. «Не достигнуто» = за горизонт не затронуты ни стоп, ни TP1. Доли TP1 и TP2 не являются взаимоисключающими.</li></ol>

<h2>Все eligible-сигналы за два года</h2>
<table><thead><tr><th>Тикер</th><th>Вход</th><th>Стоп</th><th>Coverage</th><th>Stop раньше TP1</th><th>TP1 раньше stop</th><th>TP2 раньше stop</th><th>Ни stop, ни TP1</th><th>ambig TP1/TP2</th><th>mean R TP1 exact</th><th>mean R TP2 exact</th></tr></thead><tbody>{_summary_rows(scenarios)}</tbody></table>
<p class="note">Знаменатель долей — возможные входы конкретной строки. Coverage показывает возможные/eligible; невозможный вход означает не конечную или неположительную цену входа либо ATR/R.</p>

<h2>Направление и ключевой failure mode</h2>
<p>Directional correctness — знак close(t+10)−close(t) против прогноза; нейтральный факт честно не считается верным. Ключевой failure mode: направление верно, но stop достигнут раньше TP1 (включая консервативный STOP_FIRST/ambiguous).</p>
<table><thead><tr><th>Тикер</th><th>Eligible</th><th>Верное направление</th><th>Нейтральный факт</th></tr></thead><tbody>{''.join(directional_rows)}</tbody></table>
<table><thead><tr><th>Тикер</th><th>Вход</th><th>Стоп</th><th>Верное направление</th><th>Верно, но stop раньше TP1</th></tr></thead><tbody>{''.join(failure_rows)}</tbody></table>

<h2>Неперекрывающаяся sensitivity-секция phase0</h2>
<p>Для каждого тикера берутся хронологические eligible t[0::10]; одни и те же даты применяются ко всем вариантам. Это описательная проверка чувствительности: без p-value, доверительных интервалов и утверждений о статистической независимости. Остальные девять фаз здесь не перебираются.</p>
<table><thead><tr><th>Тикер</th><th>Вход</th><th>Стоп</th><th>Coverage</th><th>Stop раньше TP1</th><th>TP1 раньше stop</th><th>TP2 раньше stop</th><th>Ни stop, ни TP1</th><th>ambig TP1/TP2</th><th>mean R TP1 exact</th><th>mean R TP2 exact</th></tr></thead><tbody>{_summary_rows(scenarios, phase_days)}</tbody></table>

<h2>Coverage и исключения</h2><table><thead><tr><th>Тикер</th><th>Завершённые</th><th>До окна</th><th>Warm-up</th><th>Нет полного t+10</th><th>Eligible</th><th>Neutral facts</th></tr></thead><tbody>{coverage_rows}</tbody></table>

<h2>Аудит данных и воспроизводимость</h2>{''.join(provenance)}
<p>SHA-256 генератора: <code>{audit['script_sha256']}</code><br>SHA-256 зависимости модели/кэша: <code>{audit['dependency_sha256']}</code><br>Python: {html.escape(audit['python_version'])}<br>Точная команда: <code>{command}</code></p>
<p>Скрипт работает только с checksum-проверенным офлайн-кэшем MOEX ISS; сеть, cron и broker API не используются.</p>
</body></html>"""


def run(args: argparse.Namespace) -> Path:
    as_of = date.fromisoformat(args.as_of)
    data_dir, output = Path(args.data_dir), Path(args.output)
    candles, manifests, signals, coverage = {}, {}, {}, {}
    for ticker in TICKERS:
        candles[ticker], manifests[ticker] = read_cache(data_dir, ticker)
        signals[ticker], coverage[ticker] = build_signals(ticker, candles[ticker], as_of)
        if not signals[ticker]:
            raise BacktestError(f"Нет eligible-сигналов {ticker}")
    scenarios = evaluate_all(signals, candles)
    script_path = Path(__file__).resolve()
    dependency = Path(__file__).with_name("moex_medium_term_trend_backtest.py").resolve()
    audit = {
        "generated_at_msk": datetime.now(MSK).isoformat(),
        "script_sha256": hashlib.sha256(script_path.read_bytes()).hexdigest(),
        "dependency_sha256": hashlib.sha256(dependency.read_bytes()).hexdigest(),
        "python_version": platform.python_implementation() + " " + platform.python_version(),
        "command": shlex.join([sys.executable, str(script_path), "--as-of", args.as_of,
                               "--data-dir", str(data_dir), "--output", str(output)]),
    }
    document = render_html(signals, scenarios, coverage, manifests, audit, as_of)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return output


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default="2026-11-18", help="exclusive MSK date, YYYY-MM-DD")
    parser.add_argument("--data-dir", default="reports/moex_backtest_data")
    parser.add_argument("--output", default="reports/moex_execution_rules_backtest.html")
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        print(run(parse_args()))
    except (BacktestError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Ошибка: {exc}")
