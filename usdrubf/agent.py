"""Agent -> backend protocol: immutable market context, validated plan, receipt.

The agent owns the thesis and price levels. This module never chooses a side or
optimizes an entry. Acceptance records a scenario, never a broker instruction.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from .moex import MSK
from .process import STRATEGY_VERSION, SUPPORTED_STRATEGIES
from .trading import Decision, SetupState, TradePlan, latest_plan, load_plan, load_plan_id

ANALYSIS_KEYS = ("w1", "d1", "h4", "h1", "invalidation")
FIELDS = {"context_id", "ticker", "decision", "reason", "analysis", "confidence", "setup_type",
          "entry", "stop", "tp1", "tp2", "structural_invalidation"}
LEVELS = ("entry", "stop", "tp1", "tp2")


def active_plan(db, ticker):
    row = db.execute("""SELECT plan_id FROM plans
                     WHERE ticker=? AND state IN ('ACTIVE','TP1')
                     ORDER BY trade_date DESC, revision DESC LIMIT 1""",
                     (ticker,)).fetchone()
    return load_plan_id(db, row["plan_id"]) if row else None


def create_context(db, payload: dict) -> dict:
    """Persist exactly the evidence the agent sees, including the broker snapshot."""
    payload = dict(payload)
    ticker = payload["ticker"]
    now = datetime.fromisoformat(payload["generated_at"]).astimezone(MSK)
    context_id = uuid.uuid4().hex
    current = payload.get("market_setup")
    locked = bool(current and current.get("state") not in {"AWAITING_AGENT", "LEGACY_PLAN", "NO_MORNING_PLAN"})
    previous = db.execute("SELECT payload FROM analysis_contexts WHERE ticker=? AND trade_date<? ORDER BY generated_at DESC LIMIT 1",
                          (ticker, now.date().isoformat())).fetchone()
    if previous:
        old = json.loads(previous["payload"])
        payload["previous_context"] = {
            "generated_at": old["generated_at"], "price": old["price"],
            **{tf: {k: v for k, v in old[tf].items() if k not in {"recent_candles", "confirmed_pivots", "waves"}}
               for tf in ("w1", "d1", "h4", "h1")},
        }
    previous_feedback = db.execute("""SELECT f.payload,f.status,f.received_at FROM agent_feedback f
        JOIN analysis_contexts c ON c.context_id=f.context_id
        WHERE c.ticker=? AND f.status IN ('ACCEPTED','REVIEWED') ORDER BY f.rowid DESC LIMIT 1""", (ticker,)).fetchone()
    if previous_feedback:
        payload["previous_agent_analysis"] = {"received_at": previous_feedback["received_at"],
                                              **json.loads(previous_feedback["payload"])}
    payload["agent_handoff"] = {
        "context_id": context_id, "task": "KEEP_PLAN" if locked else "CREATE_PLAN",
        "submit_before": (now + timedelta(minutes=60)).isoformat(),
        "required_analysis": list(ANALYSIS_KEYS),
        "decisions": ["KEEP_PLAN"] if locked else ["LONG_SETUP", "SHORT_SETUP", "NO_TRADE"],
        "submit_command": ".venv/bin/python scripts/moex_agent_plan.py submit --file PATH_TO_JSON",
        "get_command": ".venv/bin/python scripts/moex_agent_plan.py get --ticker " + ticker,
        "rule": "Агент выбирает сценарий. Сохранение подтверждено только ответом ACCEPTED/REVIEWED; поручения брокеру не отправляются.",
    }
    if not locked:
        payload["market_setup"] = {"decision": None, "state": "AWAITING_AGENT",
                                    "reason": "Ожидается анализ агента и принятый бэкендом план"}
    with db:
        db.execute("INSERT INTO analysis_contexts(context_id,ticker,trade_date,generated_at,payload) VALUES(?,?,?,?,?)",
                   (context_id, ticker, now.date().isoformat(), now.isoformat(), json.dumps(payload, ensure_ascii=False)))
    return payload


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 12000


def _valid_price(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _no_trade_wait_condition(reason) -> bool:
    """Require one clause to tie an awaited event to an explicit price level."""
    if not _text(reason):
        return False
    timeframe = re.compile(r"(?<!\w)(?:D1|H4)(?!\w)", re.IGNORECASE)
    event = re.compile(
        r"(?:закры\w*|close\w*|проб\w*|breakout\w*|возврат\w*|верн\w*|"
        r"return\w*|ретест\w*|retest\w*)",
        re.IGNORECASE,
    )
    number = r"(?<![\w.,])\d+(?:[.,]\d+)?(?![\w.,])"
    level_word = r"(?:уров\w*|отмет\w*|цен\w*|level\w*|price\w*)"
    relation = r"(?:выше|ниже|above|below)"
    level = re.compile(
        rf"(?:{level_word}\s*(?:[:=~-]\s*)?{number}|"
        rf"{relation}\s+(?:{level_word}\s+)?{number}|"
        rf"(?:проб\w*|breakout\w*|возврат\w*|верн\w*|return\w*|ретест\w*|retest\w*)"
        rf"\s+(?:{level_word}\s+)?{number})",
        re.IGNORECASE,
    )
    non_price_metric = re.compile(
        r"(?:RSI\s*\d*|ATR\s*\d*|(?:SMA|EMA|MA)\s*\d*|объ[её]м\w*|volume\w*|"
        r"свеч\w*|candle\w*|дн\w*|day\w*|процент\w*|percent\w*)",
        re.IGNORECASE,
    )
    non_price_suffix = re.compile(
        r"\s*(?:(?:по|by|on)\s+(?:(?:значен\w*|индикатор\w*)\s+)?)?"
        r"(?:RSI\s*\d*|ATR\s*\d*|(?:SMA|EMA|MA)\s*\d*|"
        r"бар\w*|bars?\b|свеч\w*|candle\w*|дн\w*|day\w*|час\w*|hour\w*|"
        r"процент\w*|percent\w*|%)",
        re.IGNORECASE,
    )
    # Keep decimal points inside a clause while preventing an unrelated number
    # in another sentence/semicolon clause from satisfying the condition.
    clauses = re.split(r"[;!?\n]+|(?<!\d)\.(?!\d)", reason)
    for clause in clauses:
        if not timeframe.search(clause):
            continue
        events = list(event.finditer(clause))
        if not events:
            continue
        for candidate in level.finditer(clause):
            if non_price_suffix.match(clause[candidate.end():]):
                continue
            prior_events = [match for match in events if match.end() <= candidate.start()]
            context_start = prior_events[-1].end() if prior_events else max(0, candidate.start() - 24)
            if non_price_metric.search(clause[context_start:candidate.start()]):
                continue
            # A bare "above/below N" before the awaited event is ambiguous;
            # require an explicit level/price noun in that ordering.
            if not prior_events and not re.search(level_word, candidate.group(), re.IGNORECASE):
                continue
            return True
    return False


def _market_issues(market):
    if market.get("history_issues"):
        return list(market["history_issues"])
    delay = market.get("delay_minutes")
    if not _valid_price(market.get("price")) or delay is None or delay < -1:
        return ["Нет корректной биржевой цены"]
    reference_ok = (market.get("trading_status") is not None and not market.get("market_open")
                    and 0 <= delay <= 4 * 1440)
    if market.get("stale") and not reference_ok:
        return ["Цена устарела во время торгов; нужен свежий контекст"]
    return []


def submit_decision(db, data: dict, broker: dict | None = None, market: dict | None = None,
                    now: datetime | None = None) -> dict:
    """Validate, serialize competing submissions, persist a decision and read it back.

    Rejections are explicit and audited. They never become an implicit NO_TRADE.
    broker/market must be freshly fetched by the CLI for a new directional plan.
    """
    now = (now or datetime.now(MSK)).astimezone(MSK)
    if not isinstance(data, dict):
        return {"status": "REJECTED", "errors": ["Нужен JSON-объект"]}
    context_id = data.get("context_id")
    if not isinstance(context_id, str):
        return {"status": "REJECTED", "errors": ["Нужен context_id из утреннего контекста"]}
    request = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=True)
    digest = hashlib.sha256(request.encode()).hexdigest()
    with db:
        db.execute("BEGIN IMMEDIATE")
        context = db.execute("SELECT * FROM analysis_contexts WHERE context_id=?", (context_id,)).fetchone()
        if not context:
            return {"status": "REJECTED", "errors": ["Контекст не найден в этой базе"]}
        previous = db.execute("SELECT receipt FROM agent_feedback WHERE context_id=? AND request_hash=? AND status IN ('ACCEPTED','REVIEWED') ORDER BY rowid DESC LIMIT 1",
                              (context_id, digest)).fetchone()
        if previous:
            receipt = json.loads(previous["receipt"])
            receipt["idempotent"] = True
            return receipt
        payload = json.loads(context["payload"])
        errors = []
        extra = set(data) - FIELDS
        if extra:
            errors.append("Неизвестные поля: " + ", ".join(sorted(extra)))
        if data.get("ticker") != context["ticker"]:
            errors.append("Тикер не совпадает с контекстом")
        if not _text(data.get("reason")):
            errors.append("Нужно обоснование reason")
        analysis = data.get("analysis")
        if not isinstance(analysis, dict) or any(not _text(analysis.get(k)) for k in ANALYSIS_KEYS):
            errors.append("Нужен анализ w1/d1/h4/h1 и invalidation текстом")
        if data.get("confidence") not in ("low", "medium", "high"):
            errors.append("confidence: low, medium или high")
        generated = datetime.fromisoformat(context["generated_at"])
        if now < generated or now - generated > timedelta(minutes=60) or now.date().isoformat() != context["trade_date"]:
            errors.append("Контекст просрочен; повторите утренний сбор данных")
        newest = db.execute("SELECT context_id FROM analysis_contexts WHERE ticker=? AND trade_date=? ORDER BY rowid DESC LIMIT 1",
                            (context["ticker"], context["trade_date"])).fetchone()
        if newest["context_id"] != context_id:
            errors.append("Этот контекст заменён более новым утренним сбором")
        if context["status"] != "PENDING":
            errors.append("Решение по контексту уже принято; перезапись запрещена")
        decision = data.get("decision")
        locked_plan = active_plan(db, context["ticker"]) or load_plan(db, context["ticker"], context["trade_date"])
        replacement_for = payload.get("replacement_for")
        replacement_row = None
        if isinstance(replacement_for, dict) and isinstance(replacement_for.get("plan_id"), str):
            replacement_row = db.execute(
                "SELECT * FROM plans WHERE plan_id=?", (replacement_for["plan_id"],)
            ).fetchone()
        replacement_valid = bool(
            replacement_row
            and replacement_row["state"] == SetupState.CANCELLED.value
            and replacement_row["ticker"] == context["ticker"]
            and replacement_row["trade_date"] == context["trade_date"]
            and replacement_row["revision"] == 0
        )
        if replacement_for is not None and not replacement_valid:
            errors.append("Некорректная связь replacement_for с отменённым исходным планом")
        plan = None
        status = "ACCEPTED"
        if decision == "KEEP_PLAN":
            status = "REVIEWED"
            if not locked_plan:
                errors.append("Нет сохранённого плана для KEEP_PLAN")
            if any(data.get(k) is not None for k in (*LEVELS, "structural_invalidation", "setup_type")):
                errors.append("KEEP_PLAN не может менять уровни или тип входа")
        elif decision in ("LONG_SETUP", "SHORT_SETUP", "NO_TRADE"):
            # Old inactive algorithmic plans can be superseded on explicit morning handoff.
            if locked_plan and (locked_plan[2] in {SetupState.ACTIVE, SetupState.TP1}
                                or (locked_plan[1].author == "agent" and locked_plan[2] != SetupState.CANCELLED)):
                errors.append("План уже зафиксирован; допускается только KEEP_PLAN")
            if locked_plan and locked_plan[1].author == "agent" and locked_plan[2] == SetupState.CANCELLED:
                if not replacement_valid:
                    errors.append("Замена отменённого плана допустима только по replacement-контексту монитора")
            if replacement_valid:
                used = db.execute("""SELECT 1 FROM plans
                    WHERE root_plan_id=? AND trade_date=? AND revision>0 LIMIT 1""",
                    (replacement_row["root_plan_id"], context["trade_date"])).fetchone()
                if used:
                    errors.append("Лимит: один replacement исходного сценария за торговый день")
            if decision == "NO_TRADE":
                if any(data.get(k) is not None for k in (*LEVELS, "structural_invalidation")):
                    errors.append("NO_TRADE не должен содержать торговые цены")
                if not _no_trade_wait_condition(data.get("reason")):
                    errors.append(
                        "NO_TRADE reason должен назвать D1 или H4, конкретный числовой уровень "
                        "и событие закрытия, пробоя, возврата или ретеста"
                    )
                plan = TradePlan(context["ticker"], Decision.NO_TRADE, data.get("reason", ""))
            else:
                errors += _market_issues(payload)
                if broker is None or market is None:
                    errors.append("Не выполнена повторная проверка брокера и текущей котировки")
                else:
                    errors += _market_issues(market)
                    if market.get("ticker") != context["ticker"]:
                        errors.append("Повторная котировка другого инструмента")
                    if any(p.get("ticker") == context["ticker"] for key in ("positions", "orders") for p in broker[key]):
                        errors.append("Есть позиция или активная заявка брокера; новый вход запрещён")
                if payload.get("new_entry_blocked"):
                    errors.append("При утреннем сборе новый вход был заблокирован")
                if data.get("setup_type") not in ("breakout", "pullback"):
                    errors.append("setup_type: breakout или pullback")
                tick = payload.get("tick_size")
                for key in (*LEVELS, "structural_invalidation"):
                    value = data.get(key)
                    if key == "structural_invalidation" and value is None:
                        continue
                    if not _valid_price(value):
                        errors.append(f"{key}: нужна положительная конечная цена")
                    elif not _valid_price(tick):
                        errors.append(f"{key}: цена не кратна шагу инструмента")
                    else:
                        units = Decimal(str(value)) / Decimal(str(tick))
                        if abs(units - units.to_integral_value()) > Decimal("0.000001"):
                            errors.append(f"{key}: цена не кратна шагу инструмента")
                if all(_valid_price(data.get(k)) for k in LEVELS):
                    sign = 1 if decision == "LONG_SETUP" else -1
                    entry, stop, tp1, tp2 = (data[k] for k in LEVELS)
                    risk = sign * (entry - stop)
                    reward = sign * (tp2 - entry)
                    if risk <= 0 or not 0 < sign * (tp1 - entry) < reward:
                        errors.append("Неверный порядок входа, стопа и целей для направления")
                    elif reward + 1e-10 < 2 * risk:
                        errors.append("R/R до TP2 ниже 2")
                    invalidation = data.get("structural_invalidation")
                    if _valid_price(invalidation) and sign * (entry - invalidation) <= 0:
                        errors.append("Структурная отмена должна быть на защитной стороне входа")
                    fresh_price = market.get("price") if market is not None else None
                    if _valid_price(fresh_price):
                        if sign * (fresh_price - stop) <= 0:
                            errors.append("Стоп должен быть строго на защитной стороне свежей рыночной цены")
                        if (_valid_price(invalidation)
                                and sign * (fresh_price - invalidation) <= 0):
                            errors.append("Структурная отмена должна быть строго на защитной стороне свежей рыночной цены")
                    if not errors:
                        plan = TradePlan(context["ticker"], decision, data["reason"], **{k: data[k] for k in LEVELS},
                                         structural_invalidation=invalidation, setup_type=data["setup_type"],
                                         retest_required=True, confirmation="hourly_close_then_later_retest")
                        if plan.decision.value != decision:
                            errors.append("План не прошёл проверку уровней TradePlan")
        else:
            errors.append("decision: LONG_SETUP, SHORT_SETUP, NO_TRADE или KEEP_PLAN")
        submission_id = uuid.uuid4().hex
        receipt = {"submission_id": submission_id, "context_id": context_id,
                   "status": "REJECTED" if errors else status, "received_at": now.isoformat(),
                   "errors": errors, "broker_orders_sent": False}
        if not errors:
            if decision == "KEEP_PLAN":
                plan_id, saved, state = locked_plan
                receipt["plan_id"] = plan_id
                receipt["market_setup"] = {**saved.to_dict(), "state": state.value}
            else:
                plan.created_at = now.isoformat()  # Facts before agent acceptance cannot trigger this plan.
                plan.strategy_version = STRATEGY_VERSION
                plan.author, plan.context_id = "agent", context_id
                plan.analysis, plan.confidence = analysis, data["confidence"]
                plan.tick_size, plan.atr_daily = payload.get("tick_size"), payload["d1"].get("atr14")
                plan.holding_period = "3–20 торговых дней"
                plan.expires_at = (now + timedelta(days=1)).replace(hour=8, minute=7, second=0, microsecond=0).isoformat()
                state = SetupState.WAITING if plan.direction else SetupState.CANCELLED
                if replacement_valid:
                    revision = 1
                    root_plan_id = replacement_row["root_plan_id"]
                    plan_id = f"{root_plan_id}-r{revision}"
                    old_state = None
                    db.execute("""INSERT INTO plans(
                        plan_id,ticker,trade_date,payload,state,state_time,revision,root_plan_id
                        ) VALUES(?,?,?,?,?,?,?,?)""",
                        (plan_id, plan.ticker, context["trade_date"],
                         json.dumps(plan.to_dict(), ensure_ascii=False), state.value,
                         now.isoformat(), revision, root_plan_id))
                else:
                    revision = 0
                    plan_id = f"{context['ticker']}-{context['trade_date']}"
                    root_plan_id = plan_id
                    old = db.execute("SELECT state FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
                    old_state = old["state"] if old else None
                    db.execute("""INSERT INTO plans(
                        plan_id,ticker,trade_date,payload,state,state_time,revision,root_plan_id
                        ) VALUES(?,?,?,?,?,?,?,?)
                        ON CONFLICT(plan_id) DO UPDATE SET payload=excluded.payload,state=excluded.state,
                        state_time=excluded.state_time,last_candle_time=NULL,activated_at=NULL""",
                        (plan_id, plan.ticker, context["trade_date"],
                         json.dumps(plan.to_dict(), ensure_ascii=False), state.value,
                         now.isoformat(), revision, root_plan_id))
                db.execute("INSERT INTO plan_events(plan_id,old_state,new_state,happened_at,note) VALUES(?,?,?,?,?)",
                           (plan_id, old_state, state.value, now.isoformat(), f"Принят план агента, context_id={context_id}"))
                # Read canonical DB state back for the receipt, not a model echo.
                _, saved, saved_state = load_plan_id(db, plan_id)
                receipt["plan_id"] = plan_id
                receipt["market_setup"] = {**saved.to_dict(), "state": saved_state.value,
                                           "plan_id": plan_id, "revision": revision}
            receipt["risk_checks"] = {"rr_tp2": receipt["market_setup"]["rr_tp2"],
                                      "sizing_status": "NOT_CALCULATED", "ruble_risk_verified": False}
            db.execute("UPDATE analysis_contexts SET status=? WHERE context_id=?", (status, context_id))
        db.execute("INSERT INTO agent_feedback VALUES(?,?,?,?,?,?,?)",
                   (submission_id, context_id, digest, request, json.dumps(receipt, ensure_ascii=False), receipt["status"], now.isoformat()))
        return receipt


def get_result(db, ticker):
    saved = active_plan(db, ticker) or latest_plan(db, ticker)
    feedback = db.execute("""SELECT receipt FROM agent_feedback f JOIN analysis_contexts c ON c.context_id=f.context_id
        WHERE c.ticker=? ORDER BY f.rowid DESC LIMIT 1""", (ticker,)).fetchone()
    return {"ticker": ticker, "market_setup": None if not saved else {
        **saved[1].to_dict(), "plan_id": saved[0],
        "revision": db.execute("SELECT revision FROM plans WHERE plan_id=?", (saved[0],)).fetchone()[0],
        "state": saved[2].value},
        "last_receipt": json.loads(feedback["receipt"]) if feedback else None}
