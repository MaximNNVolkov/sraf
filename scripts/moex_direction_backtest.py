#!/usr/bin/env python3
"""Reproducible next-day direction backtest for IMOEXF and USDRUBF.

The script downloads daily candles from the public MOEX ISS endpoint, keeps only
finished sessions (dates strictly before ``--as-of``), stores an auditable CSV
cache, and writes a self-contained Russian HTML report.  No trading APIs are
used.
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
ENGINE = "futures"
MARKET = "forts"
BOARD = "RFUD"
INTERVAL = 24
WINDOW = 20
DEFAULT_TESTS = 30
DEFAULT_SEED = 20261118
MSK = ZoneInfo("Europe/Moscow")


class BacktestError(RuntimeError):
    """An input, network, or data-integrity error that must stop the report."""


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
class Check:
    ticker: str
    forecast_day: date
    fact_day: date
    close: float
    next_close: float
    sma5: float
    sma20: float
    momentum5_pct: float
    momentum20_pct: float
    forecast: str
    actual: str
    correct: bool


@dataclass(frozen=True)
class Candidate:
    """A forecast eligible for sampling, without any t+1 price data."""

    ticker: str
    candle_index: int
    forecast_day: date
    close: float
    sma5: float
    sma20: float
    momentum5_pct: float
    momentum20_pct: float
    forecast: str


def subtract_years(day: date, years: int) -> date:
    """Calendar-year subtraction with a safe leap-day fallback."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def _finite_number(value: object, field: str) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise BacktestError(f"Некорректное поле {field}: {value!r}") from exc
    if not math.isfinite(result):
        raise BacktestError(f"Неконечное значение {field}: {value!r}")
    return result


def _request_json(url: str, timeout: float = 30, retries: int = 4) -> dict:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "moex-direction-backtest/1.0"}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status != 200:
                    raise BacktestError(f"ISS вернул HTTP {response.status}")
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # network and malformed JSON are both fatal after retries
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(min(2**attempt, 4))
    raise BacktestError(f"MOEX ISS недоступен: {url}: {last_error}")


def iss_candles_url(ticker: str, start: date, end: date, offset: int) -> str:
    path = (
        f"engines/{ENGINE}/markets/{MARKET}/boards/{BOARD}/"
        f"securities/{ticker}/candles.json"
    )
    params = {
        "iss.meta": "off",
        "iss.only": "candles",
        "candles.columns": "open,close,high,low,volume,begin,end",
        "interval": INTERVAL,
        "from": start.isoformat(),
        "till": end.isoformat(),
        "start": offset,
    }
    return f"{ISS_BASE}/{path}?{urllib.parse.urlencode(params)}"


def download_candles(ticker: str, start: date, end: date) -> tuple[list[DailyCandle], list[str]]:
    """Download every ISS page; never return partial data after an error."""
    candles: list[DailyCandle] = []
    urls: list[str] = []
    offset = 0
    while True:
        url = iss_candles_url(ticker, start, end, offset)
        payload = _request_json(url)
        block = payload.get("candles")
        if not isinstance(block, dict):
            raise BacktestError(f"В ответе ISS нет блока candles для {ticker}")
        columns = block.get("columns")
        data = block.get("data")
        if not isinstance(columns, list) or not isinstance(data, list):
            raise BacktestError(f"Некорректный блок candles для {ticker}")
        urls.append(url)
        for raw in data:
            row = dict(zip(columns, raw))
            begin = str(row.get("begin") or "")
            try:
                candle_day = date.fromisoformat(begin[:10])
            except ValueError as exc:
                raise BacktestError(f"Некорректная дата свечи {ticker}: {begin!r}") from exc
            candles.append(
                DailyCandle(
                    day=candle_day,
                    open=_finite_number(row.get("open"), "open"),
                    high=_finite_number(row.get("high"), "high"),
                    low=_finite_number(row.get("low"), "low"),
                    close=_finite_number(row.get("close"), "close"),
                    volume=_finite_number(row.get("volume") or 0, "volume"),
                    begin=begin,
                    end=str(row.get("end") or ""),
                )
            )
        if not data:
            break
        offset += len(data)
    return validate_candles(candles, ticker), urls


def validate_candles(candles: Iterable[DailyCandle], ticker: str) -> list[DailyCandle]:
    ordered = sorted(candles, key=lambda candle: candle.day)
    if not ordered:
        raise BacktestError(f"ISS не вернул свечи для {ticker}")
    dates = [candle.day for candle in ordered]
    if len(dates) != len(set(dates)):
        raise BacktestError(f"В данных {ticker} есть дубли дневных свечей")
    for candle in ordered:
        if candle.low > min(candle.open, candle.close) or candle.high < max(candle.open, candle.close):
            raise BacktestError(f"Нарушение OHLC у {ticker} за {candle.day}")
        if candle.low > candle.high or candle.close <= 0:
            raise BacktestError(f"Некорректная свеча {ticker} за {candle.day}")
    return ordered


def cache_paths(data_dir: Path, ticker: str) -> tuple[Path, Path]:
    return data_dir / f"{ticker}_daily.csv", data_dir / f"{ticker}_manifest.json"


def write_cache(
    data_dir: Path,
    ticker: str,
    candles: Sequence[DailyCandle],
    urls: Sequence[str],
    requested_start: date,
    requested_end: date,
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path, manifest_path = cache_paths(data_dir, ticker)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=data_dir, delete=False) as tmp:
        writer = csv.DictWriter(tmp, fieldnames=list(asdict(candles[0]).keys()))
        writer.writeheader()
        for candle in candles:
            row = asdict(candle)
            row["day"] = candle.day.isoformat()
            writer.writerow(row)
        tmp_path = Path(tmp.name)
    digest = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
    os.replace(tmp_path, csv_path)
    manifest = {
        "source": "MOEX ISS",
        "ticker": ticker,
        "engine": ENGINE,
        "market": MARKET,
        "board": BOARD,
        "interval": INTERVAL,
        "requested_from": requested_start.isoformat(),
        "requested_till": requested_end.isoformat(),
        "fetched_at_msk": datetime.now(MSK).isoformat(),
        "rows": len(candles),
        "first_day": candles[0].day.isoformat(),
        "last_day": candles[-1].day.isoformat(),
        "csv_sha256": digest,
        "request_urls": list(urls),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_cache(data_dir: Path, ticker: str) -> tuple[list[DailyCandle], dict]:
    csv_path, manifest_path = cache_paths(data_dir, ticker)
    if not csv_path.exists() or not manifest_path.exists():
        raise BacktestError(f"Нет офлайн-кэша для {ticker}: {csv_path} / {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    if digest != manifest.get("csv_sha256"):
        raise BacktestError(f"Контрольная сумма кэша {ticker} не совпала")
    candles: list[DailyCandle] = []
    with csv_path.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            candles.append(
                DailyCandle(
                    day=date.fromisoformat(row["day"]),
                    open=_finite_number(row["open"], "open"),
                    high=_finite_number(row["high"], "high"),
                    low=_finite_number(row["low"], "low"),
                    close=_finite_number(row["close"], "close"),
                    volume=_finite_number(row["volume"], "volume"),
                    begin=row.get("begin", ""),
                    end=row.get("end", ""),
                )
            )
    return validate_candles(candles, ticker), manifest


def sign(value: float, epsilon: float = 1e-12) -> int:
    return 1 if value > epsilon else -1 if value < -epsilon else 0


def forecast_from_window(window: Sequence[DailyCandle]) -> tuple[str, dict[str, float]]:
    """Majority vote of SMA spread and 5/20-bar momentum; no look-ahead."""
    if len(window) != WINDOW:
        raise ValueError(f"Ожидалось {WINDOW} свечей, получено {len(window)}")
    closes = [candle.close for candle in window]
    sma5 = sum(closes[-5:]) / 5
    sma20 = sum(closes) / WINDOW
    momentum5 = closes[-1] / closes[-6] - 1
    momentum20 = closes[-1] / closes[0] - 1
    score = sign(sma5 - sma20) + sign(momentum5) + sign(momentum20)
    direction = "UP" if score > 0 else "DOWN" if score < 0 else "NEUTRAL"
    return direction, {
        "sma5": sma5,
        "sma20": sma20,
        "momentum5_pct": momentum5 * 100,
        "momentum20_pct": momentum20 * 100,
    }


def candidate_forecasts(
    ticker: str, candles: Sequence[DailyCandle], period_start: date, as_of: date
) -> tuple[list[Candidate], dict[str, int]]:
    """Build the sampling frame without reading any t+1 OHLC value."""
    completed = [candle for candle in candles if candle.day < as_of]
    candidates: list[Candidate] = []
    exclusions = {
        "outside_period": 0,
        "warmup": 0,
        "neutral_forecast": 0,
        "no_completed_fact": 0,
        "unfinished_or_after_as_of": len(candles) - len(completed),
    }
    for index, candle in enumerate(completed):
        if candle.day < period_start:
            exclusions["outside_period"] += 1
            continue
        if index < WINDOW - 1:
            exclusions["warmup"] += 1
            continue
        forecast, features = forecast_from_window(completed[index - WINDOW + 1 : index + 1])
        if forecast == "NEUTRAL":
            exclusions["neutral_forecast"] += 1
            continue
        # Eligibility may depend on a completed t+1 candle existing, but neither
        # its close nor its direction is read before the random draw.
        if index + 1 >= len(completed):
            exclusions["no_completed_fact"] += 1
            continue
        candidates.append(
            Candidate(
                ticker=ticker,
                candle_index=index,
                forecast_day=candle.day,
                close=candle.close,
                forecast=forecast,
                **features,
            )
        )
    return candidates, exclusions


def derived_seed(seed: int, ticker: str) -> int:
    """Derive a stable, ticker-specific 64-bit seed from the primary seed."""
    material = f"moex-direction-backtest-v1\0{seed}\0{ticker}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def sample_candidates(candidates: Sequence[Candidate], count: int, seed: int) -> list[Candidate]:
    if count < 20:
        raise BacktestError("Число проверок на тикер должно быть не меньше 20")
    if len(candidates) < count:
        raise BacktestError(f"Валидных дат только {len(candidates)}, требуется {count}")
    selected = random.Random(seed).sample(list(candidates), count)
    return sorted(selected, key=lambda candidate: candidate.forecast_day)


def evaluate_candidates(
    selected: Sequence[Candidate], candles: Sequence[DailyCandle], as_of: date
) -> list[Check]:
    """Attach t+1 facts after sampling; neutral facts remain incorrect checks."""
    completed = [candle for candle in candles if candle.day < as_of]
    checks: list[Check] = []
    for candidate in selected:
        if candidate.candle_index + 1 >= len(completed):
            raise BacktestError(f"Для {candidate.ticker} {candidate.forecast_day} нет завершённого факта t+1")
        candle = completed[candidate.candle_index]
        next_candle = completed[candidate.candle_index + 1]
        if candle.day != candidate.forecast_day or next_candle.day >= as_of:
            raise BacktestError(f"Нарушена привязка завершённого факта для {candidate.ticker}")
        actual = (
            "UP" if next_candle.close > candidate.close
            else "DOWN" if next_candle.close < candidate.close
            else "NEUTRAL"
        )
        checks.append(
            Check(
                ticker=candidate.ticker,
                forecast_day=candidate.forecast_day,
                fact_day=next_candle.day,
                close=candidate.close,
                next_close=next_candle.close,
                sma5=candidate.sma5,
                sma20=candidate.sma20,
                momentum5_pct=candidate.momentum5_pct,
                momentum20_pct=candidate.momentum20_pct,
                forecast=candidate.forecast,
                actual=actual,
                correct=candidate.forecast == actual,
            )
        )
    return checks


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        raise ValueError("total must be positive")
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (centre - margin) / denominator, (centre + margin) / denominator


def _direction(direction: str) -> str:
    return {"UP": "вверх", "DOWN": "вниз", "NEUTRAL": "нейтрально"}[direction]


def _summary(checks: Sequence[Check]) -> tuple[int, int, float, tuple[float, float]]:
    total = len(checks)
    correct = sum(check.correct for check in checks)
    return correct, total, correct / total, wilson_interval(correct, total)


def render_html(
    selected: dict[str, list[Check]],
    metadata: dict[str, dict],
    exclusions: dict[str, dict[str, int]],
    eligible_counts: dict[str, int],
    derived_seeds: dict[str, int],
    as_of: date,
    period_start: date,
    seed: int,
    audit: dict[str, str],
) -> str:
    all_checks = [check for ticker in TICKERS for check in selected[ticker]]
    overall = _summary(all_checks)

    def pct(value: float) -> str:
        return f"{value * 100:.1f}%"

    cards = []
    for ticker in TICKERS:
        checks = selected[ticker]
        correct, total, accuracy, interval = _summary(checks)
        cards.append(
            f'<article class="card"><h3>{html.escape(ticker)}</h3><div class="metric">{pct(accuracy)}</div>'
            f'<p>{correct} из {total}<br>95% ДИ Уилсона: {pct(interval[0])}–{pct(interval[1])}</p></article>'
        )
    cards.append(
        f'<article class="card"><h3>Итого (описательно)</h3><div class="metric">{pct(overall[2])}</div>'
        f'<p>{overall[0]} из {overall[1]}<br>совместный ДИ не рассчитывается</p></article>'
    )

    sections = []
    for ticker in TICKERS:
        rows = []
        for check in selected[ticker]:
            rows.append(
                "<tr>"
                f"<td>{check.forecast_day.isoformat()}</td><td>{check.fact_day.isoformat()}</td>"
                f"<td>{check.close:.4f}</td><td>{check.next_close:.4f}</td>"
                f"<td>{check.sma5:.4f}</td><td>{check.sma20:.4f}</td>"
                f"<td>{check.momentum5_pct:+.3f}%</td><td>{check.momentum20_pct:+.3f}%</td>"
                f"<td>{_direction(check.forecast)}</td><td>{_direction(check.actual)}</td>"
                f'<td class="{"ok" if check.correct else "bad"}">{"да" if check.correct else "нет"}</td></tr>'
            )
        summary = _summary(selected[ticker])
        manifest = metadata[ticker]
        dates = ", ".join(check.forecast_day.isoformat() for check in selected[ticker])
        excluded = exclusions[ticker]
        neutral_actuals = sum(check.actual == "NEUTRAL" for check in selected[ticker])
        url_items = []
        for url in manifest.get("request_urls", []):
            safe_url = html.escape(str(url), quote=True)
            if str(url).startswith(f"{ISS_BASE}/"):
                url_items.append(f'<li><a href="{safe_url}" rel="noreferrer">{safe_url}</a></li>')
            else:
                url_items.append(f"<li><code>{safe_url}</code></li>")
        sections.append(
            f"<section><h2>{ticker}</h2>"
            f"<p><strong>Результат:</strong> {summary[0]}/{summary[1]} ({pct(summary[2])}); "
            f"95% ДИ {pct(summary[3][0])}–{pct(summary[3][1])}. "
            f"Исходные свечи: {manifest['first_day']}–{manifest['last_day']}, строк: {manifest['rows']}, "
            f"fetched-at: <code>{html.escape(str(manifest.get('fetched_at_msk', 'не указан')))}</code>.<br>"
            f"SHA-256 CSV: <code>{html.escape(manifest['csv_sha256'])}</code>.</p>"
            f"<p><strong>Eligible candidates до random sample:</strong> {eligible_counts[ticker]}; "
            f"выбрано — {len(selected[ticker])}; не выбрано случайной выборкой — "
            f"{eligible_counts[ticker] - len(selected[ticker])}. Derived seed: "
            f"<code>{derived_seeds[ticker]}</code>.</p>"
            f"<details><summary>Выбранные даты, все исключения и источники</summary>"
            f"<p><strong>Даты t:</strong> {dates}</p>"
            f"<p>Исключено до выборки: вне тестового периода — {excluded['outside_period']}; "
            f"недостаточно warm-up свечей — {excluded['warmup']}; нейтральный прогноз — "
            f"{excluded['neutral_forecast']}; нет завершённой свечи факта t+1 — "
            f"{excluded['no_completed_fact']}; незавершённые/на дату среза и позже — "
            f"{excluded['unfinished_or_after_as_of']}.</p>"
            f"<p>Нейтральных фактов в уже выбранной выборке — {neutral_actuals}; они не исключены "
            f"и считаются несовпадением прогноза UP/DOWN.</p>"
            f"<p><strong>MOEX ISS request URLs:</strong></p><ul>{''.join(url_items) or '<li>не указаны</li>'}</ul></details>"
            '<div class="table-wrap"><table><thead><tr><th>Дата t</th><th>Факт t+1</th>'
            '<th>Close t</th><th>Close t+1</th><th>SMA5</th><th>SMA20</th>'
            '<th>Mom 5</th><th>Mom 20</th><th>Прогноз</th><th>Факт</th><th>Верно</th>'
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>"
        )

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Бэктест направления IMOEXF и USDRUBF</title>
<style>
:root{{--ink:#17202a;--muted:#667085;--line:#d9dee7;--bg:#f5f7fa;--panel:#fff;--accent:#1456a0;--ok:#157347;--bad:#b42318}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1280px;margin:auto;padding:32px 22px 70px}} h1{{font-size:clamp(28px,4vw,46px);line-height:1.08;margin:.2em 0}} h2{{margin-top:44px}}
.lead{{font-size:18px;color:var(--muted);max-width:900px}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin:28px 0}}
.card,section,.method,.warning{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px}} .card h3{{margin:0}} .metric{{font-size:34px;font-weight:750;color:var(--accent)}}
.method,.warning{{margin:18px 0}} .warning{{border-left:5px solid #e0a800}} code{{font-size:12px;word-break:break-all}} .table-wrap{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{padding:8px 9px;border-bottom:1px solid var(--line);white-space:nowrap;text-align:right}} th:first-child,td:first-child{{text-align:left}} th{{background:#edf2f7;position:sticky;top:0}} .ok{{color:var(--ok);font-weight:700}} .bad{{color:var(--bad);font-weight:700}} ul{{overflow-wrap:anywhere}}
footer{{color:var(--muted);margin-top:30px}} @media print{{body{{background:#fff}} main{{max-width:none;padding:0}} section,.card,.method,.warning{{break-inside:avoid}}}}
</style></head><body><main>
<p>MOEX ISS · завершённые дневные свечи</p><h1>Бэктест прогноза направления<br>IMOEXF и USDRUBF</h1>
<p class="lead">Прогноз направления следующей торговой дневной свечи на случайной воспроизводимой выборке за два календарных года.</p>
<div class="cards">{''.join(cards)}</div>
<div class="method"><h2>Метод без заглядывания вперёд</h2>
<p><strong>Период тестовых дат:</strong> {period_start.isoformat()}–{(as_of - timedelta(days=1)).isoformat()}. Свечи с датой {as_of.isoformat()} и позже исключены как потенциально незавершённые. Источник — MOEX ISS, futures/forts/RFUD, интервал 24.</p>
<p>На закрытии даты <em>t</em> берутся ровно {WINDOW} close от t−19 до t. Три голоса: знак SMA5−SMA20, доходность close[t]/close[t−5]−1 и close[t]/close[t−19]−1. Большинство даёт «вверх» или «вниз». Факт — знак close[t+1]−close[t]. Данные t+1 не входят в признаки.</p>
<p><strong>Случайная выборка до чтения факта:</strong> кандидат формируется только по наличию окна, ненейтральному прогнозу и наличию завершённой дневной свечи t+1. Затем по {len(selected[TICKERS[0]])} дат выбираются без возвращения, и только после этого читается close t+1. Нейтральный факт остаётся в отчёте как «нейтрально» и всегда является несовпадением прогноза UP/DOWN.</p>
<p>PRNG Python Mersenne Twister: основной seed <strong>{seed}</strong>; для каждого тикера отдельная воспроизводимая последовательность с 64-битным derived seed = первые 8 байт SHA-256 от <code>moex-direction-backtest-v1&#92;0seed&#92;0ticker</code>. Это разделяет PRNG-последовательности, но не означает статистической независимости рыночных наблюдений. Для каждого тикера показан двусторонний 95% интервал Уилсона; общий результат только описательный, без совместного ДИ.</p></div>
<div class="method"><h2>Воспроизводимость и provenance</h2>
<p><strong>Сформировано/fetched audit:</strong> {html.escape(audit['generated_at_msk'])}. <strong>Python:</strong> <code>{html.escape(audit['python_version'])}</code>.</p>
<p><strong>Точная команда:</strong> <code>{html.escape(audit['command'])}</code></p>
<p><strong>SHA-256 скрипта:</strong> <code>{html.escape(audit['script_sha256'])}</code></p></div>
{''.join(sections)}
<div class="warning"><h2>Ограничения</h2><p>Это исследовательская проверка направления, а не торговая стратегия и не рекомендация. Малый размер случайной выборки даёт широкий доверительный интервал; тест не учитывает комиссии, проскальзывание, гэпы, размер позиции, внутридневной путь цены и множественный подбор правил. Наблюдения двух инструментов могут быть зависимы, поэтому объединённые 60 результатов не трактуются как независимая выборка и совместный доверительный интервал не приводится. Один фиксированный seed не заменяет проверку на всех датах или walk-forward настройку. Вечные фьючерсы имеют особенности расчёта и фондирования. Прошлая точность не гарантирует будущую.</p></div>
<footer>Сформировано {html.escape(audit['generated_at_msk'])}. Общая описательная точность: {overall[0]}/{overall[1]} ({pct(overall[2])}); без совместного ДИ. Торговые операции не выполнялись.</footer>
</main></body></html>"""


def run(args: argparse.Namespace) -> Path:
    if args.tests < 20:
        raise BacktestError("--tests должен быть не меньше 20")
    today_msk = datetime.now(MSK).date()
    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else today_msk
    except ValueError as exc:
        raise BacktestError("--as-of должен быть датой YYYY-MM-DD") from exc
    if as_of > today_msk:
        raise BacktestError(
            f"--as-of {as_of.isoformat()} позже текущей московской даты {today_msk.isoformat()}"
        )
    period_start = subtract_years(as_of, 2)
    # Warm-up starts comfortably before the test interval; eligibility still starts at period_start.
    download_start = period_start - timedelta(days=60)
    download_end = as_of
    data_dir = Path(args.data_dir)
    output = Path(args.output)
    selected: dict[str, list[Check]] = {}
    metadata: dict[str, dict] = {}
    exclusions: dict[str, dict[str, int]] = {}
    eligible_counts: dict[str, int] = {}
    derived_seeds: dict[str, int] = {}

    for ticker in TICKERS:
        if args.offline:
            candles, manifest = read_cache(data_dir, ticker)
        else:
            candles, urls = download_candles(ticker, download_start, download_end)
            write_cache(data_dir, ticker, candles, urls, download_start, download_end)
            candles, manifest = read_cache(data_dir, ticker)
        completed = [candle for candle in candles if candle.day < as_of]
        if not completed or completed[-1].day < period_start:
            raise BacktestError(f"Кэш {ticker} не покрывает тестовый период")
        candidates, ticker_exclusions = candidate_forecasts(ticker, candles, period_start, as_of)
        ticker_seed = derived_seed(args.seed, ticker)
        sampled = sample_candidates(candidates, args.tests, ticker_seed)
        selected[ticker] = evaluate_candidates(sampled, candles, as_of)
        metadata[ticker] = manifest
        exclusions[ticker] = ticker_exclusions
        eligible_counts[ticker] = len(candidates)
        derived_seeds[ticker] = ticker_seed

    script_path = Path(__file__).resolve()
    audit = {
        "generated_at_msk": datetime.now(MSK).isoformat(),
        "script_sha256": hashlib.sha256(script_path.read_bytes()).hexdigest(),
        "python_version": f"{platform.python_implementation()} {platform.python_version()} ({sys.version})",
        "command": shlex.join([sys.executable, *sys.argv]),
    }
    report = render_html(
        selected,
        metadata,
        exclusions,
        eligible_counts,
        derived_seeds,
        as_of,
        period_start,
        args.seed,
        audit,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as tmp:
        tmp.write(report)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, output)
    return output


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", help="дата среза YYYY-MM-DD; сама дата исключается")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--tests", type=int, default=DEFAULT_TESTS, help="проверок на тикер (>=20)")
    parser.add_argument("--data-dir", default="reports/moex_backtest_data")
    parser.add_argument("--output", default="reports/moex_direction_backtest.html")
    parser.add_argument("--offline", action="store_true", help="не обращаться к сети, проверить и использовать кэш")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        path = run(parse_args(argv))
    except (BacktestError, OSError, json.JSONDecodeError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1
    print(path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
