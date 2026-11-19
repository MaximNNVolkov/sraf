#!/usr/bin/env python3
"""Two-hour checks of the fixed morning swing plan and real broker positions."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[1])
PROJECT_PYTHON = f"{REPO}/.venv/bin/python"
if __name__ == "__main__" and os.path.realpath(sys.executable) != os.path.realpath(PROJECT_PYTHON):
    os.execv(PROJECT_PYTHON, [PROJECT_PYTHON, __file__, *sys.argv[1:]])
sys.path.insert(0, REPO)
sys.path.insert(0, f"{REPO}/scripts")
from usdrubf.process import STRATEGY_VERSION, SUPPORTED_STRATEGIES, collect_compact, replay_plan
from usdrubf.agent import active_plan, create_context
from usdrubf.trading import SetupState, latest_plan
from usdrubf.trading import connect
from tinkoff_positions import fetch_state, load_project_env

TICKERS = ("IMOEXF", "USDRUBF")
DB = os.environ.get("MOEX_TRADING_DB", str(Path(REPO) / "trading.db"))


def attach_broker(payload: dict, broker: dict) -> None:
    ticker = payload["ticker"]
    payload["open_positions"] = [p for p in broker["positions"] if p["ticker"] == ticker]
    payload["broker_orders"] = [p for p in broker["orders"] if p.get("ticker") == ticker]
    payload["broker_stop_orders"] = [p for p in broker["stop_orders"] if p.get("ticker") == ticker]
    payload["new_entry_blocked"] = bool(payload["open_positions"] or payload["broker_orders"])
    payload["position_status"] = "OPEN_AT_BROKER" if payload["open_positions"] else "NO_POSITION_AT_BROKER"


def monitor_ticker(ticker: str, db, broker: dict) -> dict:
    payload, h1, _ = collect_compact(ticker)
    attach_broker(payload, broker)
    saved = active_plan(db, ticker) or latest_plan(db, ticker)
    now = datetime.fromisoformat(payload["generated_at"])
    pending = db.execute("SELECT context_id,trade_date,status,payload FROM analysis_contexts WHERE ticker=? ORDER BY rowid DESC LIMIT 1", (ticker,)).fetchone()
    pending_payload = json.loads(pending["payload"]) if pending else None
    replacement_pending = bool(
        pending and pending["status"] == "PENDING" and saved
        and pending_payload.get("replacement_for", {}).get("plan_id") == saved[0]
    )
    pending_fresh = bool(
        pending and now.date().isoformat() == pending["trade_date"]
        and now <= datetime.fromisoformat(pending_payload["agent_handoff"]["submit_before"])
    )
    if replacement_pending and not pending_fresh:
        with db:
            db.execute("UPDATE analysis_contexts SET status='EXPIRED' WHERE context_id=?",
                       (pending["context_id"],))
    waiting = (pending and pending["status"] == "PENDING"
               and pending_payload["agent_handoff"]["task"] == "CREATE_PLAN"
               and not payload["new_entry_blocked"]
               and ((saved is None) or (replacement_pending and pending_fresh)))
    if saved is None or waiting:
        payload["market_setup"] = {"decision": None, "state": "AWAITING_AGENT" if pending else "NO_MORNING_PLAN",
                                    "reason": "Нет принятого плана агента; монитор новые точки входа не создаёт"}
        if pending:
            payload["pending_context_id"] = pending["context_id"]
            last = db.execute("SELECT receipt FROM agent_feedback WHERE context_id=? ORDER BY rowid DESC LIMIT 1", (pending["context_id"],)).fetchone()
            payload["last_agent_receipt"] = json.loads(last["receipt"]) if last else None
        return payload
    plan_id, plan, state = saved
    if plan.strategy_version not in SUPPORTED_STRATEGIES or (plan.author != "agent" and state not in {SetupState.ACTIVE, SetupState.TP1}):
        payload["market_setup"] = {"decision": None, "state": "LEGACY_PLAN",
                                    "reason": "Старый неактивный план бэкенда; требуется принятый план агента"}
        return payload
    # A signal is hypothetical. It never opens/closes a journal or broker position.
    state = replay_plan(db, plan_id, plan, state, h1.candles, now)
    plan_row = db.execute("SELECT trade_date,revision,root_plan_id FROM plans WHERE plan_id=?",
                          (plan_id,)).fetchone()
    replacement_used = db.execute("""SELECT 1 FROM plans
        WHERE root_plan_id=? AND trade_date=? AND revision>0 LIMIT 1""",
        (plan_row["root_plan_id"], plan_row["trade_date"])).fetchone()
    can_replace = (state == SetupState.CANCELLED
                   and plan.direction is not None
                   and plan_row["revision"] == 0
                   and plan_row["trade_date"] == now.date().isoformat()
                   and not replacement_used
                   and not payload["new_entry_blocked"])
    if can_replace:
        payload["replacement_for"] = {"plan_id": plan_id, "state": state.value,
                                      "reason": "Сценарий отменён; требуется новый анализ"}
        payload["market_setup"] = {"decision": None, "state": "NO_MORNING_PLAN",
                                   "reason": "Отменённый сценарий заменяется свежим анализом"}
        return create_context(db, payload)
    payload["market_setup"] = {**plan.to_dict(), "state": state.value, "plan_id": plan_id}
    payload["plan_events"] = [dict(r) for r in db.execute(
        "SELECT new_state,happened_at,price,note FROM plan_events WHERE plan_id=? ORDER BY id DESC LIMIT 8", (plan_id,))]
    return payload


def human(result: dict) -> str:
    setup = result["market_setup"]
    lines = [f"{result['ticker']} · {result['price']} · {result['source']}",
             f"время цены: {result['last_trade_time']} · возраст {result['delay_minutes']} мин",
             f"сценарий: {setup['decision']} · {setup['state']} · причина при формировании: {setup['reason']}"]
    if result["stale"]:
        lines.append("Цена последней сессии, не текущая; вход по ней не предлагается.")
    if setup["decision"] not in {None, "NO_TRADE"}:
        lines.append(f"уровень входа {setup['entry']} · TP1 {setup['tp1']} · TP2 {setup['tp2']} · стоп {setup['stop']} · R/R {setup['rr_tp2']}")
        lines.append("Подтверждение: закрытый час за уровнем, затем ретест на следующем или более позднем часе.")
        lines.append(f"горизонт: {setup['holding_period']} · новый вход до {setup['expires_at']}")
        if setup["state"] in {"ACTIVE", "TP1", "TP2", "STOPPED"}:
            lines.append("Это состояние рыночного сценария; факт сделки определяется по брокеру.")
    if result["open_positions"]:
        for p in result["open_positions"]:
            lines.append(f"позиция у брокера: {p['direction']} от {p['average_price']} · {p['lots']} лот. · P/L {p['unrealized_pnl_rub']} ₽")
        if not result["broker_stop_orders"]:
            lines.append("Активные защитные стоп-заявки брокера не найдены.")
    else:
        lines.append("позиция у брокера: нет")
    if result["broker_orders"]:
        lines.append(f"активных заявок: {len(result['broker_orders'])}; повторный вход заблокирован")
    return "\n".join(lines)


def main() -> int:
    load_project_env()
    broker = fetch_state()
    results, failed = [], False
    with connect(DB) as db:
        for ticker in TICKERS:
            try:
                results.append(monitor_ticker(ticker, db, broker))
            except Exception as exc:
                results.append({"ticker": ticker, "error": str(exc)})
                failed = True
    print("## MOEX_STRUCTURED_JSON")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("## MOEX_MONITOR")
    for result in results:
        print(human(result) if "error" not in result else f"{result['ticker']} — ОШИБКА: {result['error']}")
        print()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
