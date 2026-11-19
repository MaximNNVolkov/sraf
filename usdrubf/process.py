"""Medium-term W1/D1/H4 plans and chronological trigger replay (read-only)."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from .analysis import Snapshot, levels, sma, snapshot
from .moex import MSK, Candle
from .tbank import TBankClient
from .trading import Decision, TradePlan, SetupState, TERMINAL_STATES

STRATEGY_VERSION = "agent-medium-term-v3"
SUPPORTED_STRATEGIES = {"swing-v1", "agent-swing-v2", STRATEGY_VERSION}
STALE_MINUTES = 20
HISTORY_DAYS = {60: 45, 240: 180, 24: 1100, 7: 1800}


def collect_compact(ticker: str, now: datetime | None = None, client=None) -> tuple[dict, Snapshot, Snapshot]:
    now = now or datetime.now(MSK)
    client = client or TBankClient(ticker)
    snapshots = {}
    for interval, days in HISTORY_DAYS.items():
        candles = client.get_candles(interval, now - timedelta(days=days), now)
        # Native isComplete is authoritative, including daily/weekly sessions.
        closed = [c for c in candles if c.is_complete and c.begin < now]
        snapshots[interval] = snapshot({60: "1ч", 240: "4ч", 24: "1д", 7: "1н"}[interval], closed)
    # Read quote after history so its age is not increased by history downloads.
    quote = client.get_quote()
    status = client.trading_status()
    observed = max(now, datetime.now(MSK)) if client.__class__ is TBankClient else now
    delay = None if quote.last_trade_time is None else (observed - quote.last_trade_time).total_seconds() / 60
    d1 = snapshots[24]
    previous = d1.close
    payload = {
        "ticker": ticker.upper(), "generated_at": observed.isoformat(), "source": quote.source,
        "strategy_version": STRATEGY_VERSION, "holding_period": "3–20 торговых дней",
        "trading_status": status.get("tradingStatus"),
        "market_open": status.get("tradingStatus") == "SECURITY_TRADING_STATUS_NORMAL_TRADING",
        "price": quote.last, "tick_size": client.tick_size,
        "last_trade_time": quote.last_trade_time.isoformat() if quote.last_trade_time else None,
        "delay_minutes": None if delay is None else round(delay, 1),
        "stale": quote.last is None or delay is None or delay < -1 or delay > STALE_MINUTES,
        "change_pct": round((quote.last / previous - 1) * 100, 2) if quote.last is not None and previous else None,
        "change_reference": "последняя завершённая дневная свеча T-Invest",
        "history_requested_days": {str(k): v for k, v in HISTORY_DAYS.items()},
    }
    for interval, key in [(60, "h1"), (240, "h4"), (24, "d1"), (7, "w1")]:
        payload[key] = _snap(snapshots[interval])
    payload["history_issues"] = []
    for interval, max_age in [(60, 4), (240, 4), (24, 10), (7, 21)]:
        if now - snapshots[interval].candles[-1].begin > timedelta(days=max_age):
            payload["history_issues"].append(f"Недостаточно свежая история {snapshots[interval].timeframe}")
    return payload, snapshots[60], snapshots[24]


def _snap(s: Snapshot) -> dict:
    # ZigZag's final pivot is unconfirmed; exclude it from executable levels.
    resistance, support = levels(s.pivots[:-1], s.close, tolerance=(s.atr14 or 0) * 0.5, limit=8)
    closes = [c.close for c in s.candles]
    return {
        "timeframe": s.timeframe, "close": s.close, "ma20": sma(closes, 20),
        "ma20_previous": sma(closes[:-1], 20), "ma50": s.sma50, "ma200": s.sma200,
        "ma50_previous": sma(closes[:-1], 50), "rsi14": s.rsi14, "atr14": s.atr14,
        "bars": len(closes), "history_from": s.candles[0].begin.isoformat(),
        "supports": [{"price": x.price, "touches": x.touches} for x in support],
        "resistances": [{"price": x.price, "touches": x.touches} for x in resistance],
        "last_closed_candle": s.candles[-1].to_dict(),
        "recent_candles": [c.to_dict() for c in s.candles[-80:]],
        "confirmed_pivots": [{"time": p.time.isoformat(), "price": p.price, "kind": p.kind}
                             for p in s.pivots[:-1][-40:]],
        "waves": [w.__dict__ for w in s.waves],
    }


def _trend(s: dict, period: int) -> str | None:
    avg, prev = s.get(f"ma{period}"), s.get(f"ma{period}_previous")
    if avg is None or prev is None:
        return None
    return "LONG" if s["close"] > avg >= prev else "SHORT" if s["close"] < avg <= prev else None


def plan_for(payload: dict, h1: Snapshot, d1: Snapshot, has_open_position: bool = False) -> TradePlan:
    """Legacy selector for regression comparison only; not used by scheduled jobs."""
    ticker = payload["ticker"]
    now = datetime.fromisoformat(payload["generated_at"])
    expiry = (now + timedelta(days=1)).replace(hour=8, minute=7, second=0, microsecond=0)
    metadata = dict(created_at=now.isoformat(), strategy_version="swing-v1",
                    expires_at=expiry.isoformat(), holding_period="3–20 торговых дней",
                    tick_size=payload.get("tick_size"), atr_daily=d1.atr14)

    def no_trade(reason):
        return TradePlan(ticker, Decision.NO_TRADE, reason, **metadata)

    if has_open_position:
        return no_trade("Есть открытая позиция — новый вход заблокирован")
    if payload.get("history_issues"):
        return no_trade("; ".join(payload["history_issues"]))
    # Before opening, use the last session as a labelled reference, never as live.
    reference_ok = (payload.get("trading_status") is not None and not payload.get("market_open")
                    and payload.get("delay_minutes") is not None and 0 <= payload["delay_minutes"] <= 4 * 1440)
    if payload["stale"] and not reference_ok:
        return no_trade("Последняя цена устарела; утренний анализ доступен, новый вход запрещён")
    h4, daily, weekly = payload["h4"], payload["d1"], payload["w1"]
    direction = _trend(daily, 50)
    week_direction = _trend(weekly, 20)
    if direction is None or _trend(h4, 50) != direction:
        return no_trade("1д и 4ч не подтверждают единое направление")
    if weekly.get("ma20") is None:
        return no_trade("Недостаточно недельной истории для оценки контекста")
    if week_direction and week_direction != direction:
        return no_trade("Недельный тренд против дневного направления")
    a, h4_atr, tick = d1.atr14, h4["atr14"], payload.get("tick_size")
    if not a or not h4_atr or not tick or a <= 0 or h4_atr <= 0 or tick <= 0:
        return no_trade("Недостаточно данных о волатильности или шаге цены")
    sign = 1 if direction == "LONG" else -1
    price = payload["price"]

    def rounded(value, up=False):
        units = math.ceil(value / tick - 1e-8) if up else math.floor(value / tick + 1e-8)
        return round(units * tick, 9)

    supports = sorted({rounded(x["price"]) for s in (h4, daily) for x in s["supports"]})
    resistances = sorted({rounded(x["price"], True) for s in (h4, daily) for x in s["resistances"]})
    entries = supports + resistances
    candidates = []
    for entry in sorted(set(entries)):
        if abs(entry - price) > 0.75 * a:
            continue
        pullback = sign * (entry - price) <= 0
        # Entries must be a support for a long pullback / short breakout, vice versa.
        expected_levels = supports if (direction == "LONG") == pullback else resistances
        if entry not in expected_levels:
            continue
        protective = [x for x in (supports if sign == 1 else resistances) if sign * (entry - x) > tick / 2]
        if not protective:
            continue
        structure = max(protective) if sign == 1 else min(protective)
        stop = rounded(structure - sign * 0.25 * h4_atr, up=sign == -1)
        risk = sign * (entry - stop)
        # Reject intraday noise stops and excessively distant structural stops.
        if not 0.5 * a <= risk <= 1.5 * a:
            continue
        targets = sorted((x for x in (resistances if sign == 1 else supports)
                          if sign * (x - entry) >= 0.5 * risk), key=lambda x: sign * (x - entry))
        if len(targets) < 2:
            continue
        tp1 = targets[0]
        tp2 = next((x for x in targets[1:] if sign * (x - entry) >= 2 * risk), None)
        if tp2 is None:
            continue
        p = TradePlan(ticker, Decision.LONG_SETUP if sign == 1 else Decision.SHORT_SETUP,
                      "1д и 4ч согласованы; недельный контекст не противоречит; уровни 4ч/1д",
                      entry=entry, stop=stop, structural_invalidation=structure, tp1=tp1, tp2=tp2,
                      confirmation="hourly_close_then_later_retest", retest_required=True,
                      setup_type="pullback" if pullback else "breakout", **metadata)
        if p.direction:
            candidates.append(p)
    if not candidates:
        return no_trade("Нет близкого структурного входа 4ч/1д со стопом 0.5–1.5 ATR(1д) и R/R ≥ 2")
    return min(candidates, key=lambda p: (abs(p.entry - price), p.risk))


def replay_plan(db, plan_id: str, plan: TradePlan, state: SetupState,
                candles: list[Candle], now: datetime) -> SetupState:
    """Replay every completed hour once. Activation-bar extremes precede entry."""
    if not plan.direction or state in TERMINAL_STATES:
        return state
    if plan.strategy_version not in SUPPORTED_STRATEGIES:
        raise ValueError("План старой версии: нужен новый утренний план")
    row = db.execute("SELECT last_candle_time,activated_at FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
    cursor = datetime.fromisoformat(row["last_candle_time"]) if row["last_candle_time"] else None
    created = datetime.fromisoformat(plan.created_at)
    expiry = datetime.fromisoformat(plan.expires_at) if plan.expires_at else None
    confirmed = [c for c in candles if c.is_complete and c.begin + timedelta(hours=1) <= now]
    if confirmed and (cursor or created) < confirmed[0].begin - timedelta(days=4):
        raise ValueError("В истории нет всего интервала с предыдущей проверки; статус не обновлён")
    sign = 1 if plan.direction == "LONG" else -1
    e, stop, tp1, tp2 = plan.entry, plan.stop, plan.tp1, plan.tp2
    active = {SetupState.ACTIVE, SetupState.TP1}

    def record(new, when, price, note):
        nonlocal state
        old = state
        state = new
        db.execute("UPDATE plans SET state=?,state_time=? WHERE plan_id=?", (new.value, when.isoformat(), plan_id))
        db.execute("INSERT INTO plan_events(plan_id,old_state,new_state,price,happened_at,note) VALUES(?,?,?,?,?,?)",
                   (plan_id, old.value, new.value, price, when.isoformat(), note))

    with db:
        for c in confirmed:
            if c.begin < created or (cursor and c.begin <= cursor):
                continue
            when = c.begin + timedelta(hours=1)
            if state in TERMINAL_STATES:
                break
            if state not in active and expiry and when > expiry:
                record(SetupState.CANCELLED, expiry, c.close, "Истёк срок нового входа")
                break
            old = state
            hit_stop = c.low <= stop if sign == 1 else c.high >= stop
            if hit_stop:
                record(SetupState.STOPPED if old in active else SetupState.CANCELLED, when, stop,
                       "Стоп/отмена; при касании стопа и цели в одном часе приоритет стопу")
            elif old in active:
                if (c.high >= tp2 if sign == 1 else c.low <= tp2):
                    record(SetupState.TP2, when, tp2, "Цель сценария; исполнение проверяется у брокера")
                elif old == SetupState.ACTIVE and (c.high >= tp1 if sign == 1 else c.low <= tp1):
                    record(SetupState.TP1, when, tp1, "Первая цель сценария")
            else:
                touched = c.high >= e if sign == 1 else c.low <= e
                if plan.setup_type == "pullback":
                    touched = c.low <= e if sign == 1 else c.high >= e
                close_ok = sign * (c.close - e) > 0
                if old in {SetupState.WAITING, SetupState.CROSSED}:
                    if touched and old == SetupState.WAITING:
                        record(SetupState.CROSSED, when, e, "Уровень достигнут")
                    if state == SetupState.CROSSED and close_ok:
                        record(SetupState.HOURLY_CONFIRMED, when, c.close, "Закрытый час подтвердил уровень; ждём последующий ретест")
                elif old == SetupState.HOURLY_CONFIRMED:
                    retest = c.low <= e <= c.high and close_ok
                    if retest:
                        actual_risk = sign * (c.close - stop)
                        remaining_reward = sign * (tp2 - c.close)
                        if actual_risk <= 0 or remaining_reward < 2 * actual_risk:
                            record(SetupState.CANCELLED, when, c.close, "После подтверждения R/R < 2; цену не догоняем")
                        else:
                            record(SetupState.ACTIVE, when, c.close, "Сигнал входа подтверждён; это не исполненная сделка")
                            db.execute("UPDATE plans SET activated_at=? WHERE plan_id=?", (when.isoformat(), plan_id))
                            # Do not use this candle's high/low to credit pre-entry targets.
            db.execute("UPDATE plans SET last_candle_time=? WHERE plan_id=?", (c.begin.isoformat(), plan_id))
        if state not in active and state not in TERMINAL_STATES and expiry and now >= expiry:
            record(SetupState.CANCELLED, expiry, e, "Истёк срок нового входа")
    return state
