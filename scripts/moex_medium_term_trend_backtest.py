#!/usr/bin/env python3
"""Reproducible 10-session trend backtest for IMOEXF and USDRUBF.

The default mode verifies and uses the existing MOEX ISS daily-candle cache.
Pass ``--refresh`` to replace that cache from ISS before running the backtest.
No broker or trading APIs are used.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import os
import platform
import random
import shlex
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo


ISS_BASE = "https://iss.moex.com/iss"
TICKERS = ("IMOEXF", "USDRUBF")
ENGINE, MARKET, BOARD, INTERVAL = "futures", "forts", "RFUD", 24
WINDOW = 60
HORIZON = 10
DEFAULT_TESTS = 30
DEFAULT_SEED = 20261118
MSK = ZoneInfo("Europe/Moscow")


class BacktestError(RuntimeError):
    """Invalid input, unavailable data, or failed integrity check."""


@dataclass(frozen=True)
class DailyCandle:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    begin: str = ""
    end: str = ""


@dataclass(frozen=True)
class Candidate:
    """Forecast-only record: it deliberately has no t+10 value or direction."""

    ticker: str
    candle_index: int
    forecast_day: date
    close: float
    sma20: float
    sma60: float
    momentum20_pct: float
    forecast: str


@dataclass(frozen=True)
class Check:
    ticker: str
    forecast_day: date
    fact_day: date
    close: float
    fact_close: float
    sma20: float
    sma60: float
    momentum20_pct: float
    forecast: str
    factual_return_pct: float
    actual: str
    correct: bool


def subtract_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def _number(value: object, field: str) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise BacktestError(f"Некорректное поле {field}: {value!r}") from exc
    if not math.isfinite(result):
        raise BacktestError(f"Неконечное поле {field}: {value!r}")
    return result


def validate_candles(candles: Iterable[DailyCandle], ticker: str) -> list[DailyCandle]:
    ordered = sorted(candles, key=lambda item: item.day)
    if not ordered:
        raise BacktestError(f"Нет дневных свечей {ticker}")
    if len({item.day for item in ordered}) != len(ordered):
        raise BacktestError(f"В данных {ticker} есть дубли дат")
    for item in ordered:
        if item.close <= 0 or item.low > item.high:
            raise BacktestError(f"Некорректная свеча {ticker} {item.day}")
        if item.low > min(item.open, item.close) or item.high < max(item.open, item.close):
            raise BacktestError(f"Нарушение OHLC {ticker} {item.day}")
    return ordered


def cache_paths(data_dir: Path, ticker: str) -> tuple[Path, Path]:
    return data_dir / f"{ticker}_daily.csv", data_dir / f"{ticker}_manifest.json"


def read_cache(data_dir: Path, ticker: str) -> tuple[list[DailyCandle], dict]:
    csv_path, manifest_path = cache_paths(data_dir, ticker)
    if not csv_path.exists() or not manifest_path.exists():
        raise BacktestError(f"Нет проверенного кэша {ticker}: {csv_path} / {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    if digest != manifest.get("csv_sha256"):
        raise BacktestError(f"SHA-256 кэша {ticker} не совпал с манифестом")
    candles: list[DailyCandle] = []
    with csv_path.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            candles.append(DailyCandle(
                day=date.fromisoformat(row["day"]), open=_number(row["open"], "open"),
                high=_number(row["high"], "high"), low=_number(row["low"], "low"),
                close=_number(row["close"], "close"), volume=_number(row["volume"], "volume"),
                begin=row.get("begin", ""), end=row.get("end", ""),
            ))
    return validate_candles(candles, ticker), manifest


def _request_json(url: str, timeout: float = 30, retries: int = 4) -> dict:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "moex-medium-trend-backtest/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status != 200:
                    raise BacktestError(f"ISS вернул HTTP {response.status}")
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(min(2**attempt, 4))
    raise BacktestError(f"MOEX ISS недоступен: {last_error}")


def iss_url(ticker: str, start: date, end: date, offset: int) -> str:
    path = f"engines/{ENGINE}/markets/{MARKET}/boards/{BOARD}/securities/{ticker}/candles.json"
    params = {
        "iss.meta": "off", "iss.only": "candles",
        "candles.columns": "open,close,high,low,volume,begin,end",
        "interval": INTERVAL, "from": start.isoformat(), "till": end.isoformat(), "start": offset,
    }
    return f"{ISS_BASE}/{path}?{urllib.parse.urlencode(params)}"


def download_candles(ticker: str, start: date, end: date) -> tuple[list[DailyCandle], list[str]]:
    result: list[DailyCandle] = []
    urls: list[str] = []
    offset = 0
    while True:
        url = iss_url(ticker, start, end, offset)
        payload = _request_json(url)
        block = payload.get("candles")
        if not isinstance(block, dict) or not isinstance(block.get("columns"), list) or not isinstance(block.get("data"), list):
            raise BacktestError(f"Некорректный блок candles ISS для {ticker}")
        urls.append(url)
        data = block["data"]
        for raw in data:
            row = dict(zip(block["columns"], raw))
            begin = str(row.get("begin") or "")
            try:
                candle_day = date.fromisoformat(begin[:10])
            except ValueError as exc:
                raise BacktestError(f"Некорректная дата ISS: {begin!r}") from exc
            result.append(DailyCandle(
                candle_day, _number(row.get("open"), "open"), _number(row.get("high"), "high"),
                _number(row.get("low"), "low"), _number(row.get("close"), "close"),
                _number(row.get("volume") or 0, "volume"), begin, str(row.get("end") or ""),
            ))
        if not data:
            break
        offset += len(data)
    return validate_candles(result, ticker), urls


def write_cache(data_dir: Path, ticker: str, candles: Sequence[DailyCandle], urls: Sequence[str], start: date, end: date) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path, manifest_path = cache_paths(data_dir, ticker)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=data_dir, delete=False) as temp:
        writer = csv.DictWriter(temp, fieldnames=list(asdict(candles[0]).keys()))
        writer.writeheader()
        for candle in candles:
            row = asdict(candle)
            row["day"] = candle.day.isoformat()
            writer.writerow(row)
        temp_path = Path(temp.name)
    digest = hashlib.sha256(temp_path.read_bytes()).hexdigest()
    os.replace(temp_path, csv_path)
    manifest = {
        "source": "MOEX ISS", "ticker": ticker, "engine": ENGINE, "market": MARKET,
        "board": BOARD, "interval": INTERVAL, "requested_from": start.isoformat(),
        "requested_till": end.isoformat(), "fetched_at_msk": datetime.now(MSK).isoformat(),
        "rows": len(candles), "first_day": candles[0].day.isoformat(),
        "last_day": candles[-1].day.isoformat(), "csv_sha256": digest, "request_urls": list(urls),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sign(value: float, epsilon: float = 1e-12) -> int:
    return 1 if value > epsilon else -1 if value < -epsilon else 0


def forecast_from_window(window: Sequence[DailyCandle]) -> tuple[str, dict[str, float]]:
    """Three trend votes using exactly 60 closes ending at t."""
    if len(window) != WINDOW:
        raise ValueError(f"Ожидалось {WINDOW} свечей, получено {len(window)}")
    closes = [item.close for item in window]
    sma20 = sum(closes[-20:]) / 20
    sma60 = sum(closes) / 60
    momentum20 = closes[-1] / closes[-21] - 1
    votes = (sign(closes[-1] - sma20), sign(sma20 - sma60), sign(momentum20))
    score = sum(votes)
    # A strict UP/DOWN forecast is required. An exact zero score is resolved by
    # the long trend, then momentum, then price/SMA20; a fully flat window is DOWN.
    tie_break = next((vote for vote in (votes[1], votes[2], votes[0]) if vote), -1)
    direction = "UP" if (score if score else tie_break) > 0 else "DOWN"
    return direction, {"sma20": sma20, "sma60": sma60, "momentum20_pct": momentum20 * 100}


def candidate_forecasts(ticker: str, candles: Sequence[DailyCandle], period_start: date, as_of: date) -> tuple[list[Candidate], dict[str, int]]:
    """Build the frame without accessing any candle's t+10 price."""
    completed = [item for item in candles if item.day < as_of]
    candidates: list[Candidate] = []
    exclusions = {
        "outside_period": 0, "warmup": 0, "no_completed_fact": 0,
        "unfinished_or_after_as_of": len(candles) - len(completed),
    }
    last_eligible_index = len(completed) - HORIZON - 1
    for index, candle in enumerate(completed):
        if candle.day < period_start:
            exclusions["outside_period"] += 1
            continue
        if index < WINDOW - 1:
            exclusions["warmup"] += 1
            continue
        # This uses only the trading-session index, never the future close.
        if index > last_eligible_index:
            exclusions["no_completed_fact"] += 1
            continue
        direction, features = forecast_from_window(completed[index - WINDOW + 1:index + 1])
        candidates.append(Candidate(ticker, index, candle.day, candle.close, forecast=direction, **features))
    return candidates, exclusions


def derived_seed(master_seed: int, ticker: str) -> int:
    material = f"moex-medium-term-trend-v1\0{master_seed}\0{ticker}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def sample_candidates(candidates: Sequence[Candidate], count: int, seed: int) -> list[Candidate]:
    if count < 20:
        raise BacktestError("Число случайных дат на тикер должно быть не меньше 20")
    if len(candidates) < count:
        raise BacktestError(f"Валидных дат только {len(candidates)}, требуется {count}")
    return sorted(random.Random(seed).sample(list(candidates), count), key=lambda item: item.forecast_day)


def evaluate_candidates(selected: Sequence[Candidate], candles: Sequence[DailyCandle], as_of: date) -> list[Check]:
    """Read and attach t+10 facts only after random selection."""
    completed = [item for item in candles if item.day < as_of]
    checks: list[Check] = []
    for candidate in selected:
        fact_index = candidate.candle_index + HORIZON
        if fact_index >= len(completed):
            raise BacktestError(f"Нет завершённого t+{HORIZON} для {candidate.ticker} {candidate.forecast_day}")
        origin, fact = completed[candidate.candle_index], completed[fact_index]
        if origin.day != candidate.forecast_day or fact.day >= as_of:
            raise BacktestError(f"Нарушена привязка факта {candidate.ticker} {candidate.forecast_day}")
        factual_return = fact.close / candidate.close - 1
        actual = "UP" if factual_return > 1e-12 else "DOWN" if factual_return < -1e-12 else "NEUTRAL"
        checks.append(Check(
            candidate.ticker, candidate.forecast_day, fact.day, candidate.close, fact.close,
            candidate.sma20, candidate.sma60, candidate.momentum20_pct, candidate.forecast,
            factual_return * 100, actual, candidate.forecast == actual,
        ))
    return checks


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        raise ValueError("total must be positive")
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (centre - margin) / denominator, (centre + margin) / denominator


def _summary(checks: Sequence[Check]) -> tuple[int, int, float, tuple[float, float]]:
    correct = sum(item.correct for item in checks)
    total = len(checks)
    return correct, total, correct / total, wilson_interval(correct, total)


def _direction(value: str) -> str:
    return {"UP": "UP (вверх)", "DOWN": "DOWN (вниз)", "NEUTRAL": "NEUTRAL (0)"}[value]


def render_html(selected: dict[str, list[Check]], metadata: dict[str, dict], exclusions: dict[str, dict[str, int]], eligible_counts: dict[str, int], seeds: dict[str, int], as_of: date, period_start: date, master_seed: int, audit: dict[str, str]) -> str:
    def pct(value: float) -> str:
        return f"{value * 100:.1f}%"

    cards: list[str] = []
    sections: list[str] = []
    for ticker in TICKERS:
        summary = _summary(selected[ticker])
        cards.append(f'<article class="card"><h3>{ticker}</h3><div class="metric">{pct(summary[2])}</div><p>{summary[0]} из {summary[1]}<br>95% ДИ Уилсона: {pct(summary[3][0])}–{pct(summary[3][1])}</p></article>')
        rows: list[str] = []
        for item in selected[ticker]:
            rows.append(
                "<tr>" f"<td>{item.forecast_day}</td><td>{item.fact_day}</td>"
                f"<td>{item.close:.4f}</td><td>{item.sma20:.4f}</td><td>{item.sma60:.4f}</td>"
                f"<td>{item.momentum20_pct:+.3f}%</td><td>{_direction(item.forecast)}</td>"
                f"<td>{item.fact_close:.4f}</td><td>{item.factual_return_pct:+.3f}%</td>"
                f"<td>{_direction(item.actual)}</td><td class={'ok' if item.correct else 'bad'}>{'да' if item.correct else 'нет'}</td></tr>"
            )
        manifest = metadata[ticker]
        excluded = exclusions[ticker]
        urls = "".join(f'<li><code>{html.escape(str(url))}</code></li>' for url in manifest.get("request_urls", []))
        neutral_count = sum(item.actual == "NEUTRAL" for item in selected[ticker])
        sections.append(
            f"<section><h2>{ticker}: все {len(selected[ticker])} наблюдений</h2>"
            f"<p><strong>Точность:</strong> {summary[0]}/{summary[1]} ({pct(summary[2])}); 95% ДИ Уилсона {pct(summary[3][0])}–{pct(summary[3][1])}. "
            f"Валидных дат до случайного отбора: {eligible_counts[ticker]}; derived seed: <code>{seeds[ticker]}</code>. Нейтральных фактов: {neutral_count}.</p>"
            f"<p><strong>Кэш:</strong> {manifest['first_day']}–{manifest['last_day']}, {manifest['rows']} строк; fetched-at: <code>{html.escape(str(manifest.get('fetched_at_msk', 'не указан')))}</code>; SHA-256 CSV: <code>{html.escape(str(manifest['csv_sha256']))}</code>.</p>"
            f"<details><summary>Исключения и URL источника</summary><p>Вне двухлетнего периода: {excluded['outside_period']}; недостаточно 60 сессий: {excluded['warmup']}; последние сессии без завершённого t+10: {excluded['no_completed_fact']}; на дату среза и позже: {excluded['unfinished_or_after_as_of']}.</p><ul>{urls}</ul></details>"
            '<div class="table-wrap"><table><thead><tr><th>Дата t</th><th>Дата t+10</th><th>Close t</th><th>SMA20</th><th>SMA60</th><th>Momentum 20</th><th>Forecast</th><th>Close t+10</th><th>Доходность 10 сессий</th><th>Факт</th><th>Correct</th></tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table></div></section>"
        )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Среднесрочный тренд IMOEXF и USDRUBF — бэктест</title>
<style>:root{{--ink:#17202a;--muted:#667085;--line:#d9dee7;--bg:#f5f7fa;--panel:#fff;--accent:#1456a0;--ok:#157347;--bad:#b42318}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,sans-serif}}main{{max-width:1380px;margin:auto;padding:32px 22px 70px}}h1{{font-size:clamp(28px,4vw,46px);line-height:1.1}}h2{{margin-top:38px}}.lead{{font-size:18px;color:var(--muted)}}.cards{{display:grid;grid-template-columns:repeat(2,minmax(220px,1fr));gap:14px}}.card,.panel,section,.warning{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;margin:18px 0}}.metric{{font-size:34px;font-weight:750;color:var(--accent)}}.warning{{border-left:5px solid #e0a800}}.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:8px;border-bottom:1px solid var(--line);white-space:nowrap;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#edf2f7}}.ok{{color:var(--ok);font-weight:700}}.bad{{color:var(--bad);font-weight:700}}code{{font-size:12px;word-break:break-all}}@media(max-width:650px){{.cards{{grid-template-columns:1fr}}}}</style></head>
<body><main><p>MOEX ISS · дневные свечи · горизонт 10 торговых сессий</p><h1>Бэктест среднесрочного тренда<br>IMOEXF и USDRUBF</h1>
<p class="lead">Отдельная воспроизводимая проверка прогноза UP/DOWN на 30 случайных датах каждого тикера за два года.</p><div class="cards">{''.join(cards)}</div>
<div class="panel"><h2>Метод и защита от look-ahead</h2><p><strong>Горизонт:</strong> ровно {HORIZON} торговых сессий после t (в заданном диапазоне 3–20). Тестовые даты: {period_start}–{as_of - timedelta(days=1)}; дата среза {as_of} и всё позже не используются как завершённые данные.</p>
<p>Признаки на t используют только 60 завершённых close не позднее t: три голоса — close против SMA20, SMA20 против SMA60 и momentum 20 = close[t]/close[t−20]−1. Большинство даёт UP/DOWN; при нулевой сумме приоритет у SMA20/SMA60, затем momentum 20, затем close/SMA20; полностью плоское окно даёт DOWN.</p>
<p><strong>Selection до факта:</strong> фрейм строится по датам и индексам сессий. Последние ровно {HORIZON} завершённых сессий исключаются, потому что по позиции в календаре у них ещё нет завершённого t+10; их будущая цена при этом не читается. Из оставшихся дат выбираются 30 без возвращения, и только затем читаются close[t+10], доходность и направление факта. Нулевой факт остаётся в выборке как NEUTRAL и считается несовпадением.</p>
<p>Fixed master seed: <strong>{master_seed}</strong>. Для тикера derived seed — первые 8 байт SHA-256 от строковой формулы <code>moex-medium-term-trend-v1\\0master_seed\\0ticker</code>, где <code>\\0</code> обозначает нулевой разделитель; PRNG — Python Mersenne Twister.</p></div>
<div class="panel"><h2>Источник и воспроизводимость</h2><p><strong>Режим данных:</strong> {html.escape(audit['data_mode'])}. Источник — реальные дневные свечи MOEX ISS: futures/forts/RFUD, interval=24. Целостность каждого CSV проверена по SHA-256 из манифеста.</p><p><strong>Сформировано:</strong> {html.escape(audit['generated_at_msk'])}<br><strong>Python:</strong> <code>{html.escape(audit['python_version'])}</code><br><strong>Команда:</strong> <code>{html.escape(audit['command'])}</code><br><strong>SHA-256 скрипта:</strong> <code>{html.escape(audit['script_sha256'])}</code></p></div>
{''.join(sections)}
<div class="warning"><h2>Важно</h2><p>Это исследовательский бэктест направления, а не торговая стратегия, не инвестиционная рекомендация и не обещание результата. Он не моделирует комиссии, проскальзывание, фондирование, размер позиции и управление риском. 30 наблюдений дают широкий интервал неопределённости; прошлые результаты не гарантируют будущих.</p></div>
<footer>Брокерские операции, заявки и cron-задачи не выполнялись.</footer></main></body></html>"""


def run(args: argparse.Namespace) -> tuple[Path, dict[str, tuple[int, int, float, tuple[float, float]]]]:
    if args.tests < 20:
        raise BacktestError("--tests должен быть не меньше 20")
    today = datetime.now(MSK).date()
    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else today
    except ValueError as exc:
        raise BacktestError("--as-of должен быть YYYY-MM-DD") from exc
    if as_of > today:
        raise BacktestError(f"--as-of {as_of} позже текущей московской даты {today}")
    period_start = subtract_years(as_of, 2)
    download_start = period_start - timedelta(days=120)
    data_dir, output = Path(args.data_dir), Path(args.output)
    selected: dict[str, list[Check]] = {}
    metadata: dict[str, dict] = {}
    exclusions: dict[str, dict[str, int]] = {}
    eligible_counts: dict[str, int] = {}
    seeds: dict[str, int] = {}
    for ticker in TICKERS:
        if args.refresh:
            downloaded, urls = download_candles(ticker, download_start, as_of)
            write_cache(data_dir, ticker, downloaded, urls, download_start, as_of)
        candles, manifest = read_cache(data_dir, ticker)
        completed = [item for item in candles if item.day < as_of]
        if not completed or completed[-1].day < period_start:
            raise BacktestError(f"Кэш {ticker} не покрывает двухлетний тестовый период")
        candidates, excluded = candidate_forecasts(ticker, candles, period_start, as_of)
        ticker_seed = derived_seed(args.seed, ticker)
        chosen = sample_candidates(candidates, args.tests, ticker_seed)
        selected[ticker] = evaluate_candidates(chosen, candles, as_of)
        metadata[ticker], exclusions[ticker] = manifest, excluded
        eligible_counts[ticker], seeds[ticker] = len(candidates), ticker_seed
    script_path = Path(__file__).resolve()
    audit = {
        "data_mode": "кэш обновлён напрямую из MOEX ISS" if args.refresh else "офлайн: использован имеющийся проверенный кэш MOEX ISS; сеть не запрашивалась",
        "generated_at_msk": datetime.now(MSK).isoformat(),
        "script_sha256": hashlib.sha256(script_path.read_bytes()).hexdigest(),
        "python_version": f"{platform.python_implementation()} {platform.python_version()} ({sys.version})",
        "command": shlex.join([sys.executable, *sys.argv]),
    }
    document = render_html(selected, metadata, exclusions, eligible_counts, seeds, as_of, period_start, args.seed, audit)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as temp:
        temp.write(document)
        temp_path = Path(temp.name)
    os.replace(temp_path, output)
    return output, {ticker: _summary(selected[ticker]) for ticker in TICKERS}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", help="дата среза YYYY-MM-DD; сама дата исключается")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--tests", type=int, default=DEFAULT_TESTS)
    parser.add_argument("--data-dir", default="reports/moex_backtest_data")
    parser.add_argument("--output", default="reports/moex_medium_term_trend_backtest.html")
    parser.add_argument("--refresh", action="store_true", help="обновить проверенный кэш напрямую из MOEX ISS")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        path, summaries = run(parse_args(argv))
    except (BacktestError, OSError, json.JSONDecodeError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1
    print(path.resolve())
    for ticker, (correct, total, accuracy, interval) in summaries.items():
        print(f"{ticker}: {correct}/{total} = {accuracy:.1%}; Wilson 95% {interval[0]:.1%}–{interval[1]:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
