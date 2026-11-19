#!/usr/bin/env python3
"""Collect morning evidence. The agent must submit its own decision separately."""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, REPO)
sys.path.insert(0, f"{REPO}/scripts")
from usdrubf.agent import active_plan, create_context
from usdrubf.process import SUPPORTED_STRATEGIES, collect_compact, replay_plan
from usdrubf.trading import SetupState, connect, load_plan
from tinkoff_positions import fetch_state, load_project_env
from moex_monitor_tf import attach_broker


def _context_fact(result: dict) -> str:
    facts = []
    for key in ("w1", "d1", "h4"):
        snapshot = result.get(key, {})
        if snapshot.get("close") is not None:
            facts.append(f"{snapshot.get('timeframe', key)} close {snapshot['close']}")
    return " · ".join(facts) if facts else f"текущая цена {result['price']}"


def morning_human(result: dict) -> str:
    """One-screen Telegram summary; detailed evidence remains in structured JSON."""
    setup = result.get("market_setup", {})
    positions = result.get("open_positions", [])
    stops = result.get("broker_stop_orders", [])

    if positions:
        position = "; ".join(
            f"{item['direction']} {item['lots']} лот. от {item['average_price']}"
            for item in positions
        )
    else:
        position = "нет"
    broker_stop = f"активен ({len(stops)})" if stops else "нет"

    lines = [
        f"{result['ticker']} · утренний отчёт",
        f"Позиция: {position}",
        f"Стоп брокера: {broker_stop}",
        f"Цена: {result['price']} · {result['source']} · {result['last_trade_time']}",
    ]
    stale = result.get("stale")
    if stale:
        lines.append("Цена последней сессии; новый вход по ней не предлагается.")
    if setup.get("decision") in {None, "NO_TRADE"}:
        lines.append(f"Сейчас: сделки нет. {setup.get('reason', '')}".strip())
        return "\n".join(lines)

    entry = setup["entry"]
    direction = {"LONG_SETUP": "LONG", "SHORT_SETUP": "SHORT"}.get(
        setup["decision"], setup["decision"]
    )
    if positions:
        lines.extend([
            f"Цели позиции: {setup['tp1']} → {setup['tp2']}",
            f"Контекст: {_context_fact(result)}.",
        ])
        position_direction = positions[0].get("direction")
        if position_direction not in {"LONG", "SHORT"}:
            position_direction = "SHORT" if setup.get("decision") == "SHORT_SETUP" else "LONG"
        sign = 1 if position_direction == "LONG" else -1
        if stale:
            position_action = "нет — нужна свежая цена для проверки стопа и целей"
        elif sign * (result["price"] - setup["stop"]) <= 0:
            position_action = "закрыть позицию — цена у уровня отмены сценария"
        elif sign * (result["price"] - setup["tp2"]) >= 0:
            position_action = f"проверить полную фиксацию — достигнута TP2 {setup['tp2']}"
        elif sign * (result["price"] - setup["tp1"]) >= 0:
            position_action = f"проверить частичную фиксацию — достигнута TP1 {setup['tp1']}"
        else:
            position_action = "нет — удерживать позицию"
        lines.append(f"Действие: {position_action}.")
    else:
        lines.extend([
            f"План: {direction} при подтверждении уровня {entry}.",
            f"Вход {entry} · стоп {setup['stop']}",
            f"Цели {setup['tp1']} → {setup['tp2']} · R/R до {setup['rr_tp2']}",
            f"Контекст: {_context_fact(result)}.",
            ("Сейчас: нужна свежая цена, сделки нет."
             if stale else "Сейчас: ждать подтверждения, сделки нет."),
        ])
    return "\n".join(lines)


def morning_ticker(ticker, db, broker):
    payload, h1, _ = collect_compact(ticker)
    attach_broker(payload, broker)
    now = datetime.fromisoformat(payload["generated_at"])
    saved = active_plan(db, ticker)
    if saved:
        pid, plan, state = saved
        if plan.strategy_version in SUPPORTED_STRATEGIES:
            state = replay_plan(db, pid, plan, state, h1.candles, now)
        if state in {SetupState.ACTIVE, SetupState.TP1}:
            payload["market_setup"] = {**plan.to_dict(), "state": state.value, "plan_id": pid}
            payload["carried_from_previous_day"] = True
            return create_context(db, payload)
    existing = load_plan(db, ticker, now.date().isoformat())
    if existing and existing[1].author == "agent":
        pid, plan, state = existing
        state = replay_plan(db, pid, plan, state, h1.candles, now)
        payload["market_setup"] = {**plan.to_dict(), "state": state.value, "plan_id": pid}
    # No default NO_TRADE and no backend entry search. Pending is not a decision.
    return create_context(db, payload)


def main(ticker: str) -> int:
    load_project_env()
    broker = fetch_state()
    with connect(os.environ.get("MOEX_TRADING_DB", str(Path(REPO) / "trading.db"))) as db:
        payload = morning_ticker(ticker, db, broker)
    print("## MOEX_STRUCTURED_JSON")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("## MOEX_MORNING")
    print(morning_human(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
