#!/usr/bin/env python3
"""Compare the fixed medium-term MOEX trend model with simple baselines.

The default mode reads the existing checksum-verified MOEX ISS cache offline.
It evaluates every eligible date in the two-year period; it never samples dates.
No broker, order, or cron interfaces are used.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import platform
import random
import shlex
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Sequence

try:
    from scripts.moex_medium_term_trend_backtest import (
        BacktestError,
        Check,
        HORIZON,
        MSK,
        TICKERS,
        WINDOW,
        candidate_forecasts,
        evaluate_candidates,
        read_cache,
        subtract_years,
        wilson_interval,
    )
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    # Support direct execution as ``python scripts/this_file.py``.
    from moex_medium_term_trend_backtest import (  # type: ignore[no-redef]
        BacktestError,
        Check,
        HORIZON,
        MSK,
        TICKERS,
        WINDOW,
        candidate_forecasts,
        evaluate_candidates,
        read_cache,
        subtract_years,
        wilson_interval,
    )


DEFAULT_SEED = 20261118
METHODS = ("model", "always_up", "always_down", "coin_50")
METHOD_LABELS = {
    "model": "Модель SMA/momentum",
    "always_up": "Always UP",
    "always_down": "Always DOWN",
    "coin_50": "Монета Bernoulli(0,5), реализация",
}


@dataclass(frozen=True)
class MethodResult:
    ticker: str
    method: str
    correct: int
    total: int
    accuracy: float
    ci_low: float
    ci_high: float
    forecast_up: int
    forecast_down: int
    actual_up: int
    actual_down: int
    actual_neutral: int
    model_minus_pp: float


def coin_seed(master_seed: int, ticker: str) -> int:
    """Derive a ticker-specific illustrative coin seed without Python hash()."""
    material = f"moex-medium-term-benchmark-v1\0{master_seed}\0{ticker}\0coin-50".encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def coin_forecasts(count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    return ["UP" if rng.getrandbits(1) else "DOWN" for _ in range(count)]


def summarize_methods(ticker: str, checks: Sequence[Check], seed: int) -> dict[str, MethodResult]:
    if not checks:
        raise BacktestError(f"Нет eligible-наблюдений {ticker}")
    forecasts = {
        "model": [item.forecast for item in checks],
        "always_up": ["UP"] * len(checks),
        "always_down": ["DOWN"] * len(checks),
        "coin_50": coin_forecasts(len(checks), seed),
    }
    actuals = [item.actual for item in checks]
    correct_by_method = {
        method: sum(forecast == actual for forecast, actual in zip(values, actuals))
        for method, values in forecasts.items()
    }
    model_accuracy = correct_by_method["model"] / len(checks)
    results: dict[str, MethodResult] = {}
    for method in METHODS:
        method_forecasts = forecasts[method]
        correct = correct_by_method[method]
        accuracy = correct / len(checks)
        low, high = wilson_interval(correct, len(checks))
        results[method] = MethodResult(
            ticker=ticker,
            method=method,
            correct=correct,
            total=len(checks),
            accuracy=accuracy,
            ci_low=low,
            ci_high=high,
            forecast_up=method_forecasts.count("UP"),
            forecast_down=method_forecasts.count("DOWN"),
            actual_up=actuals.count("UP"),
            actual_down=actuals.count("DOWN"),
            actual_neutral=actuals.count("NEUTRAL"),
            model_minus_pp=(model_accuracy - accuracy) * 100,
        )
    return results


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _pp(value: float) -> str:
    return f"{value:+.1f} п.п."


def render_html(
    results: dict[str, dict[str, MethodResult]],
    metadata: dict[str, dict],
    exclusions: dict[str, dict[str, int]],
    bounds: dict[str, tuple[date, date]],
    seeds: dict[str, int],
    period_start: date,
    as_of: date,
    master_seed: int,
    audit: dict[str, str],
) -> str:
    result_rows: list[str] = []
    instrument_sections: list[str] = []
    audit_rows: list[str] = []
    for ticker in TICKERS:
        for method in METHODS:
            item = results[ticker][method]
            delta = "— (модель)" if method == "model" else _pp(item.model_minus_pp)
            theoretical = _pct(0.5 * (item.actual_up + item.actual_down) / item.total) if method == "coin_50" else "—"
            result_rows.append(
                "<tr>"
                f"<td>{ticker}</td><td>{METHOD_LABELS[method]}</td>"
                f"<td>{item.correct}/{item.total}</td><td>{_pct(item.accuracy)}</td>"
                f"<td>{_pct(item.ci_low)}–{_pct(item.ci_high)}</td>"
                f"<td>{delta}</td><td>{theoretical}</td>"
                f"<td>{item.forecast_up}/{item.forecast_down}</td></tr>"
            )
        model = results[ticker]["model"]
        excluded = exclusions[ticker]
        first_t, last_t = bounds[ticker]
        instrument_sections.append(
            f"<article class=\"card\"><h3>{ticker}</h3>"
            f"<div class=\"metric\">{_pct(model.accuracy)}</div>"
            f"<p>Модель: {model.correct}/{model.total}; 95% Wilson {_pct(model.ci_low)}–{_pct(model.ci_high)}.</p>"
            f"<p><strong>Факты:</strong> UP {model.actual_up}, DOWN {model.actual_down}, "
            f"NEUTRAL {model.actual_neutral}. Eligible t: {first_t}–{last_t}.</p>"
            f"<p><strong>Исключено:</strong> до периода {excluded['outside_period']}; "
            f"прогрев {excluded['warmup']}; без завершённого t+10 {excluded['no_completed_fact']}; "
            f"на дату среза/позже {excluded['unfinished_or_after_as_of']}.</p>"
            f"<p>Иллюстративный coin derived seed: <code>{seeds[ticker]}</code>.</p></article>"
        )
        manifest = metadata[ticker]
        urls = "<br>".join(f"<code>{html.escape(str(url))}</code>" for url in manifest.get("request_urls", []))
        audit_rows.append(
            "<tr>"
            f"<td>{ticker}</td><td>{html.escape(str(manifest.get('source', 'MOEX ISS')))}</td>"
            f"<td>{html.escape(str(manifest.get('fetched_at_msk', 'не указан')))}</td>"
            f"<td><code>{html.escape(str(manifest['csv_sha256']))}</code></td>"
            f"<td>{urls}</td></tr>"
        )

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Сравнение модели среднесрочного тренда с baseline — IMOEXF и USDRUBF</title>
<style>:root{{--ink:#182230;--muted:#5d6675;--line:#d8dee8;--bg:#f4f6f9;--panel:#fff;--accent:#155eef;--warn:#9a6700}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,sans-serif}}main{{max-width:1280px;margin:auto;padding:34px 22px 72px}}h1{{font-size:clamp(29px,4vw,47px);line-height:1.1;max-width:950px}}h2{{margin-top:38px}}.lead{{font-size:18px;color:var(--muted);max-width:980px}}.cards{{display:grid;grid-template-columns:repeat(2,minmax(260px,1fr));gap:16px}}.card,.panel,.warning{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;margin:18px 0}}.metric{{font-size:34px;font-weight:760;color:var(--accent)}}.warning{{border-left:5px solid #eaaa08}}.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:right;vertical-align:top}}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){{text-align:left}}th{{background:#eaf0f8;white-space:nowrap}}code{{font-size:12px;word-break:break-all}}.audit td{{max-width:380px;word-break:break-word}}@media(max-width:680px){{.cards{{grid-template-columns:1fr}}}}</style></head>
<body><main><p>MOEX ISS · все eligible-даты · t+10 торговых сессий</p>
<h1>Модель среднесрочного тренда против простых baseline</h1>
<p class="lead">IMOEXF и USDRUBF, двухлетний период {period_start}–{as_of - timedelta(days=1)}. Отдельный исследовательский отчёт по checksum-проверенному кэшу.</p>
<div class="cards">{''.join(instrument_sections)}</div>
<section class="panel"><h2>Результаты</h2><div class="table-wrap"><table><thead><tr><th>Инструмент</th><th>Метод</th><th>Верно / N</th><th>Точность</th><th>95% Wilson CI</th><th>Модель минус baseline</th><th>Теория монеты</th><th>Forecast UP/DOWN</th></tr></thead><tbody>{''.join(result_rows)}</tbody></table></div>
<p>Разница дана в процентных пунктах на одном и том же наборе дат. Для строки модели разница с самой собой не вычисляется.</p></section>
<section class="panel"><h2>Строго зафиксированный тест</h2>
<p>Для каждой даты t используются ровно <strong>{WINDOW} завершённых сессий до и включая t</strong>. Признаки и правило те же: close против SMA20, SMA20 против SMA60 и momentum20 = close[t]/close[t−20]−1; большинство трёх голосов задаёт UP/DOWN, с тем же детерминированным tie-break. Факт — направление close[t+{HORIZON}]/close[t]−1 через ровно {HORIZON} торговых сессий.</p>
<p>Взяты <strong>все eligible-даты</strong> двухлетнего периода: есть 60-сессионное окно и уже завершённый факт t+10. Случайного выбора дат нет. Нулевой фактический результат сохраняется как NEUTRAL и для любого UP/DOWN-метода считается несовпадением.</p>
<p>Baseline: постоянный UP, постоянный DOWN и честная Bernoulli(0,5). У монеты теоретическая точность <strong>50%</strong> только при бинарном ненейтральном факте; при сохранённых NEUTRAL безусловное ожидание на конкретном наборе равно 0,5 × доля ненейтральных фактов. Показанная строка монеты — ровно одна воспроизводимая иллюстративная реализация, а не независимый статистический эталон. Master seed: <code>{master_seed}</code>; derived seed = первые 8 байт SHA-256 от <code>moex-medium-term-benchmark-v1\\0master_seed\\0ticker\\0coin-50</code>.</p></section>
<aside class="warning"><h2>Как читать интервалы</h2>
<p>95% Wilson CI приведён описательно для каждой пары инструмент/метод. Соседние даты имеют сильно перекрывающиеся 10-сессионные горизонты, а рыночные режимы создают серийную зависимость: наблюдения не являются независимыми Bernoulli-испытаниями. Поэтому эти интервалы нельзя трактовать как корректные inferential CI, а их перекрытие или неперекрытие — как статистическую значимость. Здесь нет p-value и заявлений о превосходстве; для таких выводов нужна более строгая временная валидация с неперекрывающимися блоками или зависимость-устойчивой процедурой и out-of-sample срезами.</p></aside>
<section class="panel"><h2>Аудит данных и запуска</h2>
<p><strong>Режим/источник:</strong> {html.escape(audit['data_mode'])}. Futures/forts/RFUD, дневные candles interval=24. Перед расчётом SHA-256 каждого CSV сверен с манифестом.</p>
<div class="table-wrap"><table class="audit"><thead><tr><th>Тикер</th><th>Источник</th><th>Fetch timestamp (MSK)</th><th>CSV SHA-256</th><th>ISS URL</th></tr></thead><tbody>{''.join(audit_rows)}</tbody></table></div>
<p><strong>Сформировано:</strong> {html.escape(audit['generated_at_msk'])}<br><strong>Python:</strong> <code>{html.escape(audit['python_version'])}</code><br><strong>Команда:</strong> <code>{html.escape(audit['command'])}</code><br><strong>SHA-256 скрипта:</strong> <code>{html.escape(audit['script_sha256'])}</code></p></section>
<footer>Исследовательский directional backtest, не инвестиционная рекомендация. Комиссии, проскальзывание и исполнимость не моделируются. Cron и брокерские интерфейсы не использовались.</footer>
</main></body></html>"""


def run(args: argparse.Namespace) -> tuple[Path, dict[str, dict[str, MethodResult]]]:
    today = datetime.now(MSK).date()
    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else today
    except ValueError as exc:
        raise BacktestError("--as-of должен быть YYYY-MM-DD") from exc
    if as_of > today:
        raise BacktestError(f"--as-of {as_of} позже текущей московской даты {today}")
    period_start = subtract_years(as_of, 2)
    data_dir = Path(args.data_dir)
    output = Path(args.output)
    all_results: dict[str, dict[str, MethodResult]] = {}
    metadata: dict[str, dict] = {}
    exclusions: dict[str, dict[str, int]] = {}
    bounds: dict[str, tuple[date, date]] = {}
    seeds: dict[str, int] = {}

    for ticker in TICKERS:
        candles, manifest = read_cache(data_dir, ticker)
        candidates, excluded = candidate_forecasts(ticker, candles, period_start, as_of)
        if not candidates:
            raise BacktestError(f"Кэш {ticker} не содержит eligible-дат за два года")
        checks = evaluate_candidates(candidates, candles, as_of)
        seed = coin_seed(args.seed, ticker)
        all_results[ticker] = summarize_methods(ticker, checks, seed)
        metadata[ticker] = manifest
        exclusions[ticker] = excluded
        bounds[ticker] = (checks[0].forecast_day, checks[-1].forecast_day)
        seeds[ticker] = seed

    script_path = Path(__file__).resolve()
    audit = {
        "data_mode": "офлайн: использован существующий checksum-проверенный кэш MOEX ISS; сеть не запрашивалась",
        "generated_at_msk": datetime.now(MSK).isoformat(),
        "script_sha256": hashlib.sha256(script_path.read_bytes()).hexdigest(),
        "python_version": f"{platform.python_implementation()} {platform.python_version()} ({sys.version})",
        "command": shlex.join([sys.executable, *sys.argv]),
    }
    document = render_html(all_results, metadata, exclusions, bounds, seeds, period_start, as_of, args.seed, audit)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as temp:
        temp.write(document)
        temp_path = Path(temp.name)
    os.replace(temp_path, output)
    return output, all_results


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", help="дата среза YYYY-MM-DD; сама дата исключается")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="master seed иллюстративной монеты")
    parser.add_argument("--data-dir", default="reports/moex_backtest_data")
    parser.add_argument("--output", default="reports/moex_medium_term_benchmark_comparison.html")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        path, results = run(parse_args(argv))
    except (BacktestError, OSError, json.JSONDecodeError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1
    print(path.resolve())
    for ticker in TICKERS:
        print(ticker)
        for method in METHODS:
            item = results[ticker][method]
            delta = "model" if method == "model" else _pp(item.model_minus_pp)
            print(f"  {method}: {item.correct}/{item.total} = {item.accuracy:.1%}; Wilson 95% {item.ci_low:.1%}–{item.ci_high:.1%}; model-minus {delta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
