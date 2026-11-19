#!/usr/bin/env python3
"""Ten offline execution checks against the actual current USDRUBF model boundary.

The production strategy ``agent-medium-term-v3`` is an agent decision over a
T-Invest W1/D1/H4/H1 context.  The checksum-verified research cache contains
only MOEX ISS D1 candles.  Consequently this script reports a recommendation
as technically unreproducible instead of inventing a deterministic D1 model or
silently converting missing evidence to NO_TRADE.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import platform
import random
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

# Keep direct ``python scripts/...py`` execution independent of installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from usdrubf.analysis import snapshot
from usdrubf.moex import Candle
from usdrubf.process import HISTORY_DAYS, STRATEGY_VERSION, _snap


TICKER = "USDRUBF"
MASTER_SEED = 20261118
SAMPLE_SIZE = 10
HORIZON = 10
MIN_D1_BARS = 200
MSK = ZoneInfo("Europe/Moscow")
DEFAULT_DATA_DIR = Path("reports/moex_backtest_data")
DEFAULT_OUTPUT = Path("reports/usdrubf_current_model_10_random_execution_tests.html")
DECISION_UNREPRODUCIBLE = "TECHNICALLY_UNREPRODUCIBLE"


class ReportError(RuntimeError):
    pass


@dataclass(frozen=True)
class DailyCandle:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    begin: str
    end: str


@dataclass(frozen=True)
class ExecutionCase:
    draw_rank: int
    origin_index: int
    origin: DailyCandle
    future: tuple[DailyCandle, ...]
    d1: dict
    decision: str = DECISION_UNREPRODUCIBLE
    entry: float | None = None
    stop: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    outcome: str = "NOT_SIMULATED"


def subtract_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def derived_seed(master_seed: int, ticker: str = TICKER) -> tuple[int, str]:
    material = f"{master_seed}|{ticker}|current-model|10-random-execution-tests|v1"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return int(digest[:16], 16), material


def _number(value: object, field: str) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ReportError(f"Некорректное поле {field}: {value!r}") from exc
    if not math.isfinite(result):
        raise ReportError(f"Неконечное поле {field}: {value!r}")
    return result


def read_verified_cache(data_dir: Path) -> tuple[list[DailyCandle], dict, str]:
    csv_path = data_dir / f"{TICKER}_daily.csv"
    manifest_path = data_dir / f"{TICKER}_manifest.json"
    if not csv_path.is_file() or not manifest_path.is_file():
        raise ReportError(f"Нет кэша или манифеста: {csv_path}, {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    if digest != manifest.get("csv_sha256"):
        raise ReportError("SHA-256 USDRUBF_daily.csv не совпадает с манифестом")
    expected = {"ticker": TICKER, "engine": "futures", "market": "forts", "board": "RFUD", "interval": 24}
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ReportError(f"Манифест: ожидалось {key}={value!r}, получено {manifest.get(key)!r}")
    candles: list[DailyCandle] = []
    with csv_path.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            candles.append(DailyCandle(
                date.fromisoformat(row["day"]), _number(row["open"], "open"),
                _number(row["high"], "high"), _number(row["low"], "low"),
                _number(row["close"], "close"), _number(row["volume"], "volume"),
                row.get("begin", ""), row.get("end", ""),
            ))
    candles.sort(key=lambda item: item.day)
    if not candles or len({item.day for item in candles}) != len(candles):
        raise ReportError("Кэш пуст или содержит дубли дат")
    for item in candles:
        if item.close <= 0 or item.low > min(item.open, item.close) or item.high < max(item.open, item.close):
            raise ReportError(f"Некорректная OHLC-свеча {item.day}")
    return candles, manifest, digest


def completed_candles(candles: Sequence[DailyCandle], manifest: dict) -> tuple[list[DailyCandle], date]:
    fetched = datetime.fromisoformat(str(manifest["fetched_at_msk"])).astimezone(MSK)
    # A row for the fetch date can be a still-forming D1 candle even when its
    # ISS `end` equals the latest trade time.  Only strictly earlier dates are complete.
    completed = [item for item in candles if item.day < fetched.date()]
    if not completed:
        raise ReportError("В кэше нет завершённых D1-свечей")
    return completed, fetched.date()


def eligible_indices(candles: Sequence[DailyCandle], as_of: date) -> list[int]:
    period_start = subtract_years(as_of, 2)
    return [
        index for index, item in enumerate(candles)
        if period_start <= item.day < as_of
        and index + 1 >= MIN_D1_BARS
        and index + HORIZON < len(candles)
    ]


def select_indices(eligible: Sequence[int], master_seed: int = MASTER_SEED) -> tuple[list[int], int, str]:
    if len(eligible) < SAMPLE_SIZE:
        raise ReportError(f"Eligible дат меньше {SAMPLE_SIZE}: {len(eligible)}")
    seed, material = derived_seed(master_seed)
    return random.Random(seed).sample(list(eligible), SAMPLE_SIZE), seed, material


def _to_model_candle(item: DailyCandle) -> Candle:
    begin = datetime.fromisoformat(item.begin).replace(tzinfo=MSK) if item.begin else datetime.combine(item.day, datetime.min.time(), MSK)
    return Candle(begin, item.open, item.high, item.low, item.close, item.volume, True)


def d1_features(candles: Sequence[DailyCandle], origin_index: int) -> dict:
    origin = candles[origin_index].day
    lower = origin - timedelta(days=HISTORY_DAYS[24])
    prefix = [_to_model_candle(item) for item in candles[: origin_index + 1] if item.day >= lower]
    return _snap(snapshot("1д", prefix))


def build_cases(candles: Sequence[DailyCandle], selected: Sequence[int]) -> list[ExecutionCase]:
    return [ExecutionCase(
        draw_rank=rank,
        origin_index=index,
        origin=candles[index],
        future=tuple(candles[index + 1:index + HORIZON + 1]),
        d1=d1_features(candles, index),
    ) for rank, index in enumerate(selected, 1)]


def evaluate_trade(direction: str, stop: float, tp1: float, tp2: float,
                   future: Sequence[DailyCandle]) -> str:
    """Conservative first-touch classification using D1 OHLC only."""
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction должен быть LONG или SHORT")
    for candle in future[:HORIZON]:
        if direction == "LONG":
            stop_hit, tp1_hit, tp2_hit = candle.low <= stop, candle.high >= tp1, candle.high >= tp2
        else:
            stop_hit, tp1_hit, tp2_hit = candle.high >= stop, candle.low <= tp1, candle.low <= tp2
        if stop_hit and (tp1_hit or tp2_hit):
            return "STOP_FIRST/ambiguous"
        if stop_hit:
            return "STOP_FIRST"
        if tp2_hit:
            return "TP2_FIRST"
        if tp1_hit:
            return "TP1_FIRST"
    return "NOT_REACHED"


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return html.escape(str(value))


def _levels(items: Sequence[dict]) -> str:
    return ", ".join(f"{_fmt(item['price'])} ({item['touches']})" for item in items) or "нет"


def render_html(cases: Sequence[ExecutionCase], manifest: dict, cache_sha: str, *,
                as_of: date, eligible_count: int, seed: int, seed_material: str,
                script_sha: str) -> str:
    rows = []
    detail = []
    for case in cases:
        f = case.future
        future_high = max(item.high for item in f)
        future_low = min(item.low for item in f)
        return_pct = (f[-1].close / case.origin.close - 1) * 100
        rows.append(
            "<tr>"
            f"<td>{case.draw_rank}</td><td>{case.origin.day}</td>"
            f"<td>{_fmt(case.origin.open)} / {_fmt(case.origin.high)} / {_fmt(case.origin.low)} / {_fmt(case.origin.close)} / {_fmt(case.origin.volume)}</td>"
            f"<td><span class='bad'>НЕ ВОСПРОИЗВОДИТСЯ</span><br><code>{case.decision}</code></td>"
            "<td>— / — / — / —</td><td>NOT_SIMULATED</td>"
            f"<td>{f[0].day}…{f[-1].day}; low {_fmt(future_low)}, high {_fmt(future_high)}, close t+10 {_fmt(f[-1].close)} ({return_pct:+.2f}%)</td></tr>"
        )
        waves = case.d1.get("waves", [])
        wave_text = "; ".join(
            f"{item.get('pattern')} / {item.get('direction')} / valid={item.get('valid')} / tail={item.get('tail_offset')}"
            for item in waves
        ) or "нет"
        detail.append(
            f"<details><summary>Draw {case.draw_rank}: origin {case.origin.day} — доступные D1-признаки</summary>"
            f"<p><strong>Полный D1 collector snapshot на t:</strong> bars={case.d1['bars']}; history_from={case.d1['history_from']}; "
            f"close={_fmt(case.d1['close'])}; MA20={_fmt(case.d1['ma20'])}; MA20 previous={_fmt(case.d1['ma20_previous'])}; "
            f"MA50={_fmt(case.d1['ma50'])}; MA50 previous={_fmt(case.d1['ma50_previous'])}; MA200={_fmt(case.d1['ma200'])}; "
            f"RSI14={_fmt(case.d1['rsi14'])}; ATR14={_fmt(case.d1['atr14'])}.</p>"
            f"<p>Поддержки price (touches): {_levels(case.d1['supports'])}. Сопротивления: {_levels(case.d1['resistances'])}. "
            f"Подтверждённые pivots: {len(case.d1['confirmed_pivots'])}; волновые кандидаты: {html.escape(wave_text)}.</p>"
            "<p><strong>Основание результата:</strong> D1-признаки сами не принимают решение. В checksum-кэше отсутствуют "
            "исторические W1/H4/H1 snapshots T‑Invest, биржевая quote/tick size, broker pre-check и сохранённый ответ Hermes "
            "с analysis/reason/levels. Поэтому ни один из LONG/SHORT/NO_TRADE не присвоен; уровни не синтезированы.</p>"
            f"<p><strong>Полный машиночитаемый D1 snapshot:</strong></p><pre>{html.escape(json.dumps(case.d1, ensure_ascii=False, sort_keys=True, indent=2))}</pre></details>"
        )

    distribution = {"NO_TRADE": 0, "LONG": 0, "SHORT": 0, DECISION_UNREPRODUCIBLE: 0}
    outcomes = {key: 0 for key in ("STOP_FIRST", "TP1_FIRST", "TP2_FIRST", "NOT_REACHED", "STOP_FIRST/ambiguous")}
    for case in cases:
        distribution[case.decision] = distribution.get(case.decision, 0) + 1
        if case.decision in {"LONG", "SHORT"}:
            outcomes[case.outcome] += 1
    period_start = subtract_years(as_of, 2)
    command = ".venv/bin/python scripts/usdrubf_current_model_10_random_execution_tests.py"
    urls = "<br>".join(f"<code>{html.escape(str(url))}</code>" for url in manifest.get("request_urls", []))
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>USDRUBF: 10 случайных execution-проверок текущей модели</title>
<style>:root{{--ink:#182230;--muted:#667085;--line:#d0d5dd;--bg:#f7f8fa;--panel:#fff;--bad:#b42318;--accent:#175cd3}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}}main{{max-width:1500px;margin:auto;padding:32px 20px 70px}}h1{{font-size:clamp(30px,4vw,46px);line-height:1.08}}.lead{{font-size:18px;color:var(--muted);max-width:1100px}}.panel,.warning,details{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;margin:16px 0}}.warning{{border-left:6px solid var(--bad)}}.bad{{color:var(--bad);font-weight:700}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}}.metric{{font-size:30px;font-weight:750;color:var(--accent)}}.table-wrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:9px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left;white-space:nowrap}}th{{background:#eaf0f7}}td:last-child{{white-space:normal;min-width:310px}}code{{word-break:break-all;white-space:normal}}pre{{overflow:auto;max-height:520px;padding:12px;background:#f2f4f7;border-radius:8px;font-size:12px}}summary{{cursor:pointer;font-weight:700}}</style></head>
<body><main><p>MOEX ISS cache · USDRUBF · offline · {STRATEGY_VERSION}</p>
<h1>10 случайных execution-проверок текущей модели USDRUBF</h1>
<p class="lead">Отдельная воспроизводимая проверка за [{period_start}, {as_of}). Использованы только завершённые D1-свечи checksum-verified кэша; сеть, broker, cron и существующие отчёты не использовались и не изменялись.</p>
<aside class="warning"><h2>Главный результат: текущую рекомендацию нельзя replay-нуть из этого кэша</h2>
<p>Текущая модель — не SMA-классификатор. Backend формирует W1/D1/H4/H1-контекст, но решение <code>NO_TRADE / LONG_SETUP / SHORT_SETUP</code>, reason и entry/stop/TP1/TP2 выбирает Hermes. Production-контекст приходит из T‑Invest и дополнительно валидируется свежей котировкой и broker pre-check. В офлайн-кэше есть только ISS D1 OHLCV и нет исторических ответов агента. Поэтому честный результат всех 10 replay — <code>{DECISION_UNREPRODUCIBLE}</code>, не <code>NO_TRADE</code>; цены и trade outcome отсутствуют.</p></aside>
<section class="panel"><h2>Выборка</h2><p>As-of берётся из даты <code>fetched_at_msk</code> манифеста; свеча этой даты считается формирующейся и исключается. Eligible origin: завершённая сессия в двухлетнем полуинтервале, минимум {MIN_D1_BARS} доступных D1-баров для полного D1 snapshot и ещё {HORIZON} завершённых сессий t+1…t+10. Eligible N={eligible_count}; выбрано ровно {len(cases)} равновероятных дат без возвращения через <code>random.Random(derived_seed).sample</code>.</p>
<p>Master seed: <code>{MASTER_SEED}</code><br>Seed material: <code>{html.escape(seed_material)}</code><br>SHA-256-derived seed (первые 64 бита): <code>{seed}</code><br>Порядок строк — draw rank, не сортировка по дате.</p></section>
<section class="panel"><h2>Все 10 тестов</h2><div class="table-wrap"><table><thead><tr><th>Draw</th><th>Origin t</th><th>D1 O/H/L/C/V на t</th><th>Решение current model</th><th>Entry / stop / TP1 / TP2</th><th>Outcome</th><th>Будущая D1-динамика: только факт</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
<section class="panel"><h2>Полные доступные признаки и основание</h2>{''.join(detail)}</section>
<section class="panel"><h2>Итоги</h2><div class="grid">
<article><div class="metric">{distribution['NO_TRADE']}</div><p>NO_TRADE</p></article><article><div class="metric">{distribution['LONG']}</div><p>LONG</p></article><article><div class="metric">{distribution['SHORT']}</div><p>SHORT</p></article><article><div class="metric">{distribution[DECISION_UNREPRODUCIBLE]}</div><p>Технически невоспроизводимо</p></article></div>
<p><strong>Открытых моделью сделок: 0.</strong> Counts: stop-first={outcomes['STOP_FIRST']}; TP1-first={outcomes['TP1_FIRST']}; TP2-first={outcomes['TP2_FIRST']}; not reached={outcomes['NOT_REACHED']}; ambiguous={outcomes['STOP_FIRST/ambiguous']}. Нули означают отсутствие воспроизводимых LONG/SHORT, а не отсутствие касаний рынка.</p></section>
<section class="panel"><h2>Правило execution-проверки</h2><p>Если воспроизводимый LONG/SHORT существует, проверяются ровно t+1…t+10. На одном D1-баре одновременное касание stop и любой цели классифицируется консервативно как <code>STOP_FIRST/ambiguous</code>. Иначе первое событие — STOP_FIRST, TP2_FIRST (если свеча сразу достигла TP2), TP1_FIRST или NOT_REACHED. В данной выборке правило не применялось: уровней current model нет. Для невоспроизводимого решения будущая динамика дана отдельно только как факт.</p></section>
<section class="panel"><h2>Ограничения</h2><ul><li>ISS D1 отличается от production T‑Invest candles; W1 нельзя безоговорочно агрегировать из D1, а H4/H1 отсутствуют.</li><li>Ретроспективный запуск нынешнего LLM-агента не восстановил бы исторический ответ без сохранённых context/response и зафиксированной версии модели.</li><li>Нет quote, tick size, trading status, позиции и заявок на origin; направленный план не прошёл бы текущую backend-валидацию.</li><li>Отчёт проверяет воспроизводимость текущей модели на доступном кэше, а не качество новой proxy-модели. Статистические выводы по N=10 не делаются.</li></ul></section>
<section class="panel"><h2>Аудит источника и воспроизводимости</h2><p>Источник: {html.escape(str(manifest.get('source')))}, futures/forts/RFUD, interval=24. Cache rows={manifest.get('rows')}; диапазон {manifest.get('first_day')}…{manifest.get('last_day')}; fetched_at_msk=<code>{html.escape(str(manifest.get('fetched_at_msk')))}</code>.</p>
<p>CSV SHA-256: <code>{cache_sha}</code><br>SHA-256 генератора: <code>{script_sha}</code><br>Python: <code>{html.escape(platform.python_implementation() + ' ' + platform.python_version())}</code><br>Команда генерации: <code>{html.escape(command)}</code><br>Полный pytest: <code>PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest</code><br>Отдельный тест: <code>PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_usdrubf_current_model_10_random_execution_tests.py</code><br>Проверка HTML parser входит в отдельный тест.</p><p>ISS URLs из манифеста:<br>{urls}</p></section>
<footer>Исследовательский offline-отчёт. Не торговая рекомендация и не свидетельство исполнения сделки.</footer></main></body></html>"""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    candles, manifest, cache_sha = read_verified_cache(args.data_dir)
    completed, as_of = completed_candles(candles, manifest)
    eligible = eligible_indices(completed, as_of)
    selected, seed, seed_material = select_indices(eligible)
    cases = build_cases(completed, selected)
    script_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    document = render_html(cases, manifest, cache_sha, as_of=as_of,
                           eligible_count=len(eligible), seed=seed,
                           seed_material=seed_material, script_sha=script_sha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(document, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
