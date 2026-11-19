#!/usr/bin/env python3
"""Build a live evening snapshot and append a non-authoritative cron archive.

Market facts come from T-Invest, scenario state and the local journal come from
SQLite. Cron/Telegram output is never parsed for prices, levels or state.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[1])
PROJECT_PYTHON = f"{REPO}/.venv/bin/python"
if __name__ == "__main__" and os.path.realpath(sys.executable) != os.path.realpath(PROJECT_PYTHON):
    os.execv(PROJECT_PYTHON, [PROJECT_PYTHON, __file__, *sys.argv[1:]])
sys.path.insert(0, REPO)
sys.path.insert(0, f"{REPO}/scripts")

from usdrubf.moex import MSK  # noqa: E402
from usdrubf.process import SUPPORTED_STRATEGIES, collect_compact, replay_plan  # noqa: E402
from usdrubf.trading import connect, latest_plan  # noqa: E402
from moex_monitor_tf import attach_broker  # noqa: E402
from tinkoff_positions import fetch_state, load_project_env  # noqa: E402

TICKERS = ("IMOEXF", "USDRUBF")
RESPONSE_MARKER = "## Response"
ARCHIVE_JOB_NAMES = (
    "moex-imoexf-morning",
    "moex-usdrubf-morning",
    "moex-levels-4h",
)


def render_facts(facts: dict) -> list[str]:
    """Legacy archive renderer retained for callers; never used as market data."""
    lines = ["## Архивные наблюдения (не рыночный источник)", ""]
    for ticker in TICKERS:
        item = facts[ticker]
        series = sorted(item.get("series", []), key=lambda value: value[0])
        pool = [price for _, price in series]
        pool += [value for value in (item.get("high"), item.get("low")) if value is not None]
        if not pool:
            lines.extend([f"{ticker}: наблюдений не было", ""])
            continue
        high, low = max(pool), min(pool)
        high_label = "официальный H из котировки" if item.get("high") == high else "максимум среди наблюдений"
        low_label = "официальный L из котировки" if item.get("low") == low else "минимум среди наблюдений"
        lines.extend([f"{ticker}:", f"- {high_label}: {high}", f"- {low_label}: {low}", ""])
    return lines


def trading_db_path() -> Path:
    """Resolve the canonical DB at call time so env overrides work everywhere."""
    return Path(os.environ.get("MOEX_TRADING_DB", str(Path(REPO) / "trading.db")))


def cron_jobs_path() -> Path:
    return Path(os.environ.get(
        "MOEX_CRON_JOBS_CONFIG", str(Path.home() / ".hermes" / "cron" / "jobs.json")
    ))


def cron_output_root() -> Path:
    return Path(os.environ.get(
        "MOEX_CRON_OUTPUT_ROOT", str(Path.home() / ".hermes" / "cron" / "output")
    ))


def extract_response(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"[не удалось прочитать {path.name}: {exc}]"
    index = text.rfind(RESPONSE_MARKER)
    return "" if index == -1 else text[index + len(RESPONSE_MARKER):].strip()


def file_time(path: Path) -> str:
    parts = path.stem.split("_")
    return parts[1].replace("-", ":") if len(parts) >= 2 else path.stem


def archive_job_ids(config_path: Path | None = None) -> dict[str, str]:
    """Resolve mutable cron IDs from stable job names without changing jobs."""
    path = config_path or cron_jobs_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    wanted = set(ARCHIVE_JOB_NAMES)
    return {
        item["id"]: item["name"]
        for item in data.get("jobs", [])
        if item.get("name") in wanted and item.get("id")
    }


def collect_archive(target: str, config_path: Path | None = None,
                    output_root: Path | None = None) -> list[dict]:
    entries = []
    root = output_root or cron_output_root()
    for job_id, name in archive_job_ids(config_path).items():
        job_dir = root / job_id
        if not job_dir.is_dir():
            continue
        for path in sorted(job_dir.glob(f"{target}_*.md")):
            response = extract_response(path)
            if response:
                entries.append({"file": path.name, "job": name,
                                "time_msk": file_time(path), "response": response})
    return sorted(entries, key=lambda item: item["file"])


def _scenario(db, ticker: str, h1, now: datetime) -> dict:
    """Replay completed H1 candles, then read the resulting scenario."""
    saved = latest_plan(db, ticker)
    if saved:
        plan_id, plan, state = saved
        if plan.strategy_version in SUPPORTED_STRATEGIES:
            replay_plan(db, plan_id, plan, state, h1.candles, now)
    saved = latest_plan(db, ticker)
    if not saved:
        return {"exists": False, "state": "NO_SAVED_SCENARIO"}
    plan_id, plan, state = saved
    row = db.execute(
        "SELECT trade_date,revision,root_plan_id,last_candle_time,state_time "
        "FROM plans WHERE plan_id=?", (plan_id,)
    ).fetchone()
    events = [dict(item) for item in db.execute(
        "SELECT new_state,happened_at,price,note FROM plan_events "
        "WHERE plan_id=? ORDER BY id DESC LIMIT 8", (plan_id,)
    )]
    return {
        "exists": True,
        "plan_id": plan_id,
        "trade_date": row["trade_date"],
        "revision": row["revision"],
        "root_plan_id": row["root_plan_id"],
        "state": state.value,
        "state_time": row["state_time"],
        "last_replayed_h1_begin": row["last_candle_time"],
        "plan": plan.to_dict(),
        "events": events,
        "meaning": "Состояние рыночного сценария; не подтверждает брокерское исполнение.",
    }


def _journal_trades(db, ticker: str, target: str) -> list[dict]:
    return [dict(row) for row in db.execute(
        """SELECT * FROM trades WHERE ticker=? AND (
             status='OPEN' OR substr(COALESCE(entry_time,created_at),1,10)=?
             OR substr(COALESCE(exit_time,''),1,10)=?
           ) ORDER BY created_at""", (ticker, target, target)
    )]


def _latest_context(db, ticker: str) -> dict | None:
    row = db.execute(
        "SELECT context_id,trade_date,generated_at,status FROM analysis_contexts "
        "WHERE ticker=? ORDER BY generated_at DESC LIMIT 1", (ticker,)
    ).fetchone()
    return dict(row) if row else None


def _latest_feedback(db, ticker: str) -> dict | None:
    row = db.execute(
        """SELECT f.context_id,f.status,f.received_at FROM agent_feedback f
           JOIN analysis_contexts c ON c.context_id=f.context_id
           WHERE c.ticker=? AND f.status IN ('ACCEPTED','REVIEWED')
           ORDER BY f.received_at DESC LIMIT 1""", (ticker,)
    ).fetchone()
    return dict(row) if row else None


def _timeframe_summary(value: dict | None) -> dict:
    value = value or {}
    return {key: value.get(key) for key in (
        "timeframe", "close", "ma20", "ma20_previous", "ma50", "ma50_previous",
        "ma200", "rsi14", "atr14",
        "bars", "history_from", "supports", "resistances", "last_closed_candle",
    ) if key in value}


def build_ticker_snapshot(ticker: str, db, broker: dict, target: str,
                          now: datetime | None = None, collector=collect_compact) -> dict:
    """Build one ticker snapshot without consulting cron/Telegram text."""
    payload, h1, _ = collector(ticker, now=now) if now is not None else collector(ticker)
    attach_broker(payload, broker)
    observed = datetime.fromisoformat(payload["generated_at"])
    scenario = _scenario(db, ticker, h1, observed)
    market = {
        key: payload.get(key) for key in (
            "ticker", "generated_at", "source", "price", "last_trade_time",
            "delay_minutes", "stale", "trading_status", "market_open", "change_pct",
            "change_reference", "history_issues",
        )
    }
    market["timeframes"] = {
        key: _timeframe_summary(payload.get(key)) for key in ("h1", "h4", "d1", "w1")
    }
    return {
        "ticker": ticker,
        "market": market,
        "scenario": scenario,
        "actual_broker_state": {
            "positions": payload["open_positions"],
            "regular_orders": payload["broker_orders"],
            "stop_orders": payload["broker_stop_orders"],
            "position_status": payload["position_status"],
            "meaning": "Фактическая позиция и поручения брокера; стоп-заявка не является ценой исполнения.",
        },
        "sqlite": {
            "latest_context": _latest_context(db, ticker),
            "latest_accepted_or_reviewed": _latest_feedback(db, ticker),
            "journal_trades": _journal_trades(db, ticker, target),
        },
    }


def build_evening_snapshot(db_path: str | Path, broker: dict, target: str,
                           now: datetime | None = None, collector=collect_compact) -> dict:
    """Build the authoritative evening snapshot; archive data is not an input."""
    results = []
    with connect(db_path) as db:
        for ticker in TICKERS:
            try:
                results.append(build_ticker_snapshot(
                    ticker, db, broker, target, now=now, collector=collector
                ))
            except Exception as exc:
                results.append({"ticker": ticker, "error": str(exc)})
    return {
        "generated_at": (now or datetime.now(MSK)).isoformat(),
        "target_date": target,
        "sources": {
            "market": "T-Invest API",
            "broker": "T-Invest API (read-only portfolio/orders/stop orders)",
            "scenario_and_journal": str(Path(db_path)),
            "telegram": "archive only; never used for market/scenario/broker facts",
        },
        "broker_snapshot_ok": True,
        "tickers": results,
    }


def render_authoritative_sections(snapshot: dict) -> list[str]:
    """Render the prompt-facing facts without accepting archive input."""
    lines = [
        "## MOEX_EVENING_AUTHORITATIVE_SECTIONS",
        "Авторитетный структурированный снимок: факты ниже получены только из "
        "T-Invest API и SQLite; Telegram/cron-архив не является входом.",
    ]
    by_ticker = {item.get("ticker"): item for item in snapshot.get("tickers", [])}
    for ticker in TICKERS:
        item = by_ticker.get(ticker, {"ticker": ticker, "error": "нет снимка"})
        lines.extend(["", f"### {ticker}"])
        if "error" in item:
            lines.extend([
                "#### Состояние к вечеру",
                json.dumps({"error": item["error"]}, ensure_ascii=False, indent=2),
            ])
            continue
        sqlite = item.get("sqlite", {})
        actual = item.get("actual_broker_state", {})
        sections = (
            (
                "Утренний сценарий / Сохранённые рыночные сценарии "
                "(SQLite; не фактические сделки)",
                {
                    "latest_context": sqlite.get("latest_context"),
                    "latest_accepted_or_reviewed": sqlite.get("latest_accepted_or_reviewed"),
                    "saved_scenario": item.get("scenario"),
                },
            ),
            ("Движение рынка (T-Invest API)", item.get("market")),
            (
                "Реальные позиции/сделки пользователя",
                {
                    "broker_positions": actual.get("positions", []),
                    "broker_regular_orders": actual.get("regular_orders", []),
                    "broker_stop_orders": actual.get("stop_orders", []),
                    "broker_position_status": actual.get("position_status"),
                    "journal_trades": sqlite.get("journal_trades", []),
                    "meaning": actual.get("meaning"),
                },
            ),
            (
                "Состояние к вечеру",
                {
                    "scenario_state": item.get("scenario", {}).get("state"),
                    "scenario_state_time": item.get("scenario", {}).get("state_time"),
                    "broker_position_status": actual.get("position_status"),
                    "latest_context_status": (sqlite.get("latest_context") or {}).get("status"),
                },
            ),
        )
        for heading, value in sections:
            lines.extend([
                f"#### {heading}",
                json.dumps(value, ensure_ascii=False, indent=2),
            ])
    return lines


def render_non_authoritative_archive(archive: list[dict], target: str) -> list[str]:
    """Append raw archive text after an explicit non-authoritative boundary."""
    lines = [
        "## MOEX_TELEGRAM_ARCHIVE_NON_AUTHORITATIVE",
        "НЕАВТОРИТЕТНЫЙ АРХИВ: это только хронология доставки. Запрещено брать "
        "отсюда цены, H/L, уровни, сценарий, сделки или состояние брокера.",
    ]
    if not archive:
        lines.append(f"За {target} архивных сообщений не найдено.")
    for index, item in enumerate(archive, 1):
        lines.extend([
            f"=== [{index}/{len(archive)}] {item['time_msk']} МСК · {item['job']} ===",
            item["response"],
        ])
    return lines


def render_saved_plan(target: str, db_path=None) -> list[str]:
    """Compatibility renderer for historical inspection; honors DB override."""
    path = Path(db_path) if db_path is not None else trading_db_path()
    lines = ["## Сохранённые рыночные сценарии (не фактические сделки)", ""]
    if not path.exists():
        return lines + ["Торговая база отсутствует.", ""]
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        for ticker in TICKERS:
            row = db.execute(
                "SELECT * FROM plans WHERE ticker=? AND trade_date<=? "
                "ORDER BY trade_date DESC,revision DESC LIMIT 1", (ticker, target)
            ).fetchone()
            if not row:
                lines.append(f"{ticker}: сохранённого плана нет")
                continue
            plan = json.loads(row["payload"])
            event = db.execute(
                "SELECT new_state FROM plan_events WHERE plan_id=? "
                "AND substr(happened_at,1,10)<=? ORDER BY id DESC LIMIT 1",
                (row["plan_id"], target),
            ).fetchone()
            state = event["new_state"] if event else row["state"]
            lines.append(
                f"{ticker}: план от {row['trade_date']} · {state} · "
                f"{plan.get('decision')} · {plan.get('reason')}"
            )
        return lines + [""]
    finally:
        db.close()


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    load_project_env()
    try:
        broker = fetch_state()
    except Exception as exc:
        print("## MOEX_EVENING_ERROR")
        print(json.dumps({"broker_snapshot_ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    snapshot = build_evening_snapshot(trading_db_path(), broker, target)
    archive = collect_archive(target)
    print("\n".join(render_authoritative_sections(snapshot)))
    print("## MOEX_EVENING_SNAPSHOT_JSON")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    print("\n".join(render_non_authoritative_archive(archive, target)))
    failed = any("error" in item for item in snapshot["tickers"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
