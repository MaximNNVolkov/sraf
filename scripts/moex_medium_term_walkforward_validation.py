#!/usr/bin/env python3
"""Non-overlapping retrospective validation of the fixed MOEX model.

The script is deliberately separate from the existing backtests.  It reads only
the checksum-verified offline MOEX ISS cache and never calls a broker or network.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import platform
import shlex
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Sequence

try:
    from scripts.moex_medium_term_trend_backtest import (
        BacktestError, Check, HORIZON, MSK, TICKERS, WINDOW,
        candidate_forecasts, evaluate_candidates, read_cache, subtract_years,
        wilson_interval,
    )
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from moex_medium_term_trend_backtest import (  # type: ignore[no-redef]
        BacktestError, Check, HORIZON, MSK, TICKERS, WINDOW,
        candidate_forecasts, evaluate_candidates, read_cache, subtract_years,
        wilson_interval,
    )


PRIMARY_PHASE = 0
PHASES = tuple(range(HORIZON))


@dataclass(frozen=True)
class Score:
    correct: float
    total: int
    accuracy: float


@dataclass(frozen=True)
class PhaseResult:
    offset: int
    checks: tuple[Check, ...]
    scores: dict[str, Score]


METHOD_LABELS = {
    "model": "Модель",
    "always_up": "Always UP",
    "always_down": "Always DOWN",
    "coin_expectation": "Ожидание монеты",
}


def calendar_midpoint(start: date, end_exclusive: date) -> date:
    """Calendar boundary halfway through [start, end_exclusive)."""
    return start + (end_exclusive - start) // 2


def score_methods(checks: Sequence[Check]) -> dict[str, Score]:
    if not checks:
        raise BacktestError("Нельзя оценить пустой временной период")
    total = len(checks)
    actual_up = sum(item.actual == "UP" for item in checks)
    actual_down = sum(item.actual == "DOWN" for item in checks)
    model_correct = sum(item.correct for item in checks)
    values = {
        "model": float(model_correct),
        "always_up": float(actual_up),
        "always_down": float(actual_down),
        # A binary fair coin can never match a retained NEUTRAL outcome.
        "coin_expectation": 0.5 * (actual_up + actual_down),
    }
    return {key: Score(value, total, value / total) for key, value in values.items()}


def build_phases(checks: Sequence[Check]) -> list[PhaseResult]:
    if len(checks) < HORIZON:
        raise BacktestError(f"Нужно не меньше {HORIZON} eligible-наблюдений")
    phases: list[PhaseResult] = []
    for offset in PHASES:
        selected = tuple(checks[offset::HORIZON])
        for left, right in zip(selected, selected[1:]):
            # candidate_forecasts emits every eligible session in index order.
            if left.fact_day != right.forecast_day:
                raise BacktestError(
                    f"Фаза {offset} не является строго неперекрывающейся: "
                    f"{left.fact_day} != {right.forecast_day}"
                )
        phases.append(PhaseResult(offset, selected, score_methods(selected)))
    return phases


def split_primary(checks: Sequence[Check], boundary: date) -> dict[str, tuple[Check, ...]]:
    result = {
        "early": tuple(item for item in checks if item.forecast_day < boundary),
        "late": tuple(item for item in checks if item.forecast_day >= boundary),
    }
    if not result["early"] or not result["late"]:
        raise BacktestError(f"Пустой хронологический OOS-период при границе {boundary}")
    return result


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _count(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.1f}"


def render_html(
    phases_by_ticker: dict[str, list[PhaseResult]],
    periods_by_ticker: dict[str, dict[str, tuple[Check, ...]]],
    metadata: dict[str, dict],
    exclusions: dict[str, dict[str, int]],
    eligible_counts: dict[str, int],
    period_start: date,
    as_of: date,
    boundary: date,
    audit: dict[str, str],
) -> str:
    phase_sections: list[str] = []
    period_rows: list[str] = []
    observation_sections: list[str] = []
    audit_rows: list[str] = []
    cards: list[str] = []

    for ticker in TICKERS:
        phases = phases_by_ticker[ticker]
        primary = phases[PRIMARY_PHASE]
        model = primary.scores["model"]
        ci = wilson_interval(int(model.correct), model.total)
        cards.append(
            f'<article class="card"><h3>{ticker}</h3><div class="metric">{_pct(model.accuracy)}</div>'
            f'<p>Основная фаза 0: {_count(model.correct)}/{model.total}. '
            f'Описательный Wilson 95%: {_pct(ci[0])}–{_pct(ci[1])}.</p></article>'
        )

        rows: list[str] = []
        for phase in phases:
            first, last = phase.checks[0], phase.checks[-1]
            rows.append(
                "<tr>"
                f"<td>{phase.offset}</td><td>{len(phase.checks)}</td>"
                f"<td>{first.forecast_day} → {last.forecast_day}</td>"
                f"<td>{first.fact_day} → {last.fact_day}</td>"
                f"<td>{_pct(phase.scores['model'].accuracy)}</td>"
                f"<td>{_pct(phase.scores['always_up'].accuracy)}</td>"
                f"<td>{_pct(phase.scores['always_down'].accuracy)}</td>"
                f"<td>{_pct(phase.scores['coin_expectation'].accuracy)}</td></tr>"
            )
        phase_sections.append(
            f'<section class="panel"><h2>{ticker}: все 10 фаз</h2><div class="table-wrap"><table>'
            '<thead><tr><th>Offset</th><th>N</th><th>Forecast t: первая → последняя</th>'
            '<th>Fact t+10: первый → последний</th><th>Модель</th><th>Always UP</th>'
            f'<th>Always DOWN</th><th>Монета E</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            + _phase_ranges(phases) + "</section>"
        )

        for period_name, label in (("early", "Ранний"), ("late", "Поздний")):
            checks = periods_by_ticker[ticker][period_name]
            scores = score_methods(checks)
            first, last = checks[0], checks[-1]
            for method in ("model", "always_up", "always_down", "coin_expectation"):
                score = scores[method]
                period_rows.append(
                    "<tr>"
                    f"<td>{ticker}</td><td>{label}</td><td>{METHOD_LABELS[method]}</td>"
                    f"<td>{_count(score.correct)}/{score.total}</td><td>{_pct(score.accuracy)}</td>"
                    f"<td>{first.forecast_day} → {last.forecast_day}</td>"
                    f"<td>{first.fact_day} → {last.fact_day}</td></tr>"
                )

        detail_rows = []
        for item in primary.checks:
            detail_rows.append(
                "<tr>"
                f"<td>{item.forecast_day}</td><td>{item.fact_day}</td>"
                f"<td>{item.forecast}</td><td>{item.actual}</td>"
                f"<td>{item.factual_return_pct:+.3f}%</td>"
                f"<td>{'да' if item.correct else 'нет'}</td></tr>"
            )
        excluded = exclusions[ticker]
        observation_sections.append(
            f'<section class="panel"><h2>{ticker}: аудит основной фазы 0</h2>'
            f'<p>Все eligible t: {eligible_counts[ticker]}; основная неперекрывающаяся траектория: '
            f'{len(primary.checks)}. Исключено: до окна — {excluded["outside_period"]}; '
            f'недостаточно 60 сессий — {excluded["warmup"]}; без завершённого t+10 — '
            f'{excluded["no_completed_fact"]}; на/после as-of — {excluded["unfinished_or_after_as_of"]}.</p>'
            '<div class="table-wrap"><table><thead><tr><th>Forecast t</th><th>Fact t+10</th>'
            '<th>Прогноз</th><th>Факт</th><th>Доходность</th><th>Верно</th></tr></thead>'
            f'<tbody>{"".join(detail_rows)}</tbody></table></div></section>'
        )

        manifest = metadata[ticker]
        urls = "<br>".join(f'<code>{html.escape(str(url))}</code>' for url in manifest.get("request_urls", []))
        audit_rows.append(
            "<tr>"
            f"<td>{ticker}</td><td>{html.escape(str(manifest.get('source', 'MOEX ISS')))}</td>"
            f"<td>{html.escape(str(manifest.get('first_day')))} → {html.escape(str(manifest.get('last_day')))}</td>"
            f"<td>{manifest.get('rows')}</td><td><code>{html.escape(str(manifest.get('csv_sha256')))}</code></td>"
            f"<td><code>{html.escape(str(manifest.get('fetched_at_msk', 'не указан')))}</code></td><td>{urls}</td></tr>"
        )

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Неперекрывающаяся ретроспективная валидация среднесрочной модели MOEX</title>
<style>:root{{--ink:#17202a;--muted:#667085;--line:#d7dee8;--bg:#f4f7fa;--panel:#fff;--accent:#155eef;--warn:#a15c00}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,sans-serif}}main{{max-width:1380px;margin:auto;padding:34px 22px 72px}}h1{{font-size:clamp(29px,4vw,46px);line-height:1.1;max-width:1000px}}h2{{margin-top:32px}}.lead{{font-size:18px;color:var(--muted);max-width:1050px}}.cards{{display:grid;grid-template-columns:repeat(2,minmax(250px,1fr));gap:16px}}.card,.panel,.warning{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;margin:18px 0}}.metric{{font-size:34px;font-weight:760;color:var(--accent)}}.warning{{border-left:5px solid #eaaa08}}.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:8px;border-bottom:1px solid var(--line);text-align:right;vertical-align:top;white-space:nowrap}}th:first-child,td:first-child{{text-align:left}}th{{background:#eaf0f8}}code{{font-size:12px;word-break:break-all;white-space:normal}}.audit td{{max-width:380px;white-space:normal;word-break:break-word}}@media(max-width:680px){{.cards{{grid-template-columns:1fr}}}}</style></head>
<body><main><p>MOEX ISS · offline cache · неперекрывающиеся t+10</p>
<h1>Неперекрывающаяся ретроспективная валидация</h1>
<p class="lead">Фиксированная в данном запуске модель: окно 60 сессий, SMA20/SMA60/momentum20, горизонт ровно t+10. Параметры в расчёте не подбирались. Окно дат: [{period_start}, {as_of}), as-of {as_of} исключён. Это не незатронутый OOS-holdout: этот исторический период уже использовался в предыдущем сравнении.</p>
<div class="cards">{"".join(cards)}</div>
<section class="panel"><h2>Фиксированный ретроспективный дизайн</h2>
<p>Сначала сформированы все eligible t, используя признаки только до и включая t. Основная траектория — первая eligible-дата и затем каждый 10-й элемент (<code>eligible[0::10]</code>). Внутри каждой фазы следующий forecast t совпадает с предыдущим fact t+10, поэтому будущие интервалы не пересекаются. Фазы 1–9 используют соответствующий offset. Результаты разных фаз <strong>не объединяются и не трактуются как независимые</strong>, потому что между фазами горизонты перекрываются.</p>
<p>Факт с нулевой доходностью остаётся NEUTRAL и является ошибкой любого бинарного прогноза. Always UP/DOWN считаются на тех же датах. Для честной монеты не генерируется удобная реализация: номинальное ожидание 50% на бинарном факте, а с сохранёнными NEUTRAL итоговое E[accuracy] = 0,5 × доля ненейтральных фактов.</p>
<p>Хронологическая граница — календарная середина двухлетнего окна: <strong>{boundary}</strong>. Ранний период: t &lt; {boundary}; поздний: t ≥ {boundary}. Разбиение применяется только к основной фазе 0, без обучения, перенастройки или переноса информации из позднего периода. Граница задана по дате прогноза t; факт раннего t может доходить до границы.</p></section>
{"".join(phase_sections)}
<section class="panel"><h2>Строго хронологические OOS-периоды, только фаза 0</h2><div class="table-wrap"><table><thead><tr><th>Инструмент</th><th>Период</th><th>Метод</th><th>Верно / N</th><th>Точность</th><th>Forecast t</th><th>Fact t+10</th></tr></thead><tbody>{"".join(period_rows)}</tbody></table></div><p>Для строки монеты «верно» — математическое ожидание, поэтому оно может быть дробным; это не наблюдавшийся исход.</p></section>
<aside class="warning"><h2>Wilson CI: только описательно</h2><p>95% Wilson CI показан только в верхних карточках для точности модели на заранее выбранной основной неперекрывающейся фазе 0. Интервалы не вычисляются для остальных фаз, baseline или временных подвыборок. Даже внутри фазы рыночные режимы могут создавать зависимость, поэтому CI не является доказательством статистической значимости; p-value и заявления о превосходстве отсутствуют.</p></aside>
{"".join(observation_sections)}
<section class="panel"><h2>Аудит источника и воспроизводимости</h2><p><strong>Режим:</strong> {html.escape(audit['data_mode'])}. Перед расчётом SHA-256 CSV сверены с манифестами. Futures/forts/RFUD, interval=24.</p><div class="table-wrap"><table class="audit"><thead><tr><th>Тикер</th><th>Источник</th><th>Диапазон кэша</th><th>Строк</th><th>CSV SHA-256</th><th>Fetched MSK</th><th>MOEX ISS URLs</th></tr></thead><tbody>{"".join(audit_rows)}</tbody></table></div>
<p><strong>Сформировано:</strong> {html.escape(audit['generated_at_msk'])}<br><strong>Python:</strong> <code>{html.escape(audit['python_version'])}</code><br><strong>Команда:</strong> <code>{html.escape(audit['command'])}</code><br><strong>SHA-256 скрипта:</strong> <code>{html.escape(audit['script_sha256'])}</code></p></section>
<footer>Отдельная исследовательская проверка; существующие бэктесты не изменены. Брокер, заявки, сеть и cron не использовались.</footer>
</main></body></html>"""


def _phase_ranges(phases: Sequence[PhaseResult]) -> str:
    methods = ("model", "always_up", "always_down", "coin_expectation")
    parts = []
    for method in methods:
        values = [phase.scores[method].accuracy for phase in phases]
        parts.append(
            f"{METHOD_LABELS[method]}: min {_pct(min(values))}, max {_pct(max(values))}, "
            f"среднее фаз {_pct(sum(values) / len(values))}"
        )
    return "<p><strong>Устойчивость к фазе (не pooled N):</strong> " + "; ".join(parts) + ".</p>"


def run(args: argparse.Namespace) -> tuple[Path, dict[str, list[PhaseResult]]]:
    today = datetime.now(MSK).date()
    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else today
    except ValueError as exc:
        raise BacktestError("--as-of должен быть YYYY-MM-DD") from exc
    if as_of > today:
        raise BacktestError(f"--as-of {as_of} позже текущей московской даты {today}")
    if WINDOW != 60 or HORIZON != 10:
        raise BacktestError("Текущая фиксированная модель должна быть WINDOW=60, HORIZON=10")

    period_start = subtract_years(as_of, 2)
    boundary = calendar_midpoint(period_start, as_of)
    data_dir, output = Path(args.data_dir), Path(args.output)
    phases_by_ticker: dict[str, list[PhaseResult]] = {}
    periods_by_ticker: dict[str, dict[str, tuple[Check, ...]]] = {}
    metadata: dict[str, dict] = {}
    exclusions: dict[str, dict[str, int]] = {}
    eligible_counts: dict[str, int] = {}

    for ticker in TICKERS:
        candles, manifest = read_cache(data_dir, ticker)
        candidates, excluded = candidate_forecasts(ticker, candles, period_start, as_of)
        checks = evaluate_candidates(candidates, candles, as_of)
        phases = build_phases(checks)
        phases_by_ticker[ticker] = phases
        periods_by_ticker[ticker] = split_primary(phases[PRIMARY_PHASE].checks, boundary)
        metadata[ticker], exclusions[ticker] = manifest, excluded
        eligible_counts[ticker] = len(checks)

    script_path = Path(__file__).resolve()
    audit = {
        "data_mode": "офлайн: существующий checksum-проверенный кэш MOEX ISS; сеть не запрашивалась",
        "generated_at_msk": datetime.now(MSK).isoformat(),
        "script_sha256": hashlib.sha256(script_path.read_bytes()).hexdigest(),
        "python_version": f"{platform.python_implementation()} {platform.python_version()} ({sys.version})",
        "command": shlex.join([sys.executable, *sys.argv]),
    }
    document = render_html(
        phases_by_ticker, periods_by_ticker, metadata, exclusions, eligible_counts,
        period_start, as_of, boundary, audit,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as temp:
        temp.write(document)
        temp_path = Path(temp.name)
    os.replace(temp_path, output)
    return output, phases_by_ticker


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", help="дата среза YYYY-MM-DD; сама дата исключается")
    parser.add_argument("--data-dir", default="reports/moex_backtest_data")
    parser.add_argument("--output", default="reports/moex_medium_term_walkforward_validation.html")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        path, all_phases = run(parse_args(argv))
    except (BacktestError, OSError, json.JSONDecodeError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1
    print(path.resolve())
    for ticker in TICKERS:
        phases = all_phases[ticker]
        primary = phases[PRIMARY_PHASE].scores["model"]
        values = [phase.scores["model"].accuracy for phase in phases]
        print(
            f"{ticker}: phase 0 {int(primary.correct)}/{primary.total} = {primary.accuracy:.1%}; "
            f"10-phase range {min(values):.1%}–{max(values):.1%}, mean {sum(values)/len(values):.1%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
