"""Structured trading plans, persistent trigger state and trade journal.

The module intentionally uses only the standard library. SQLite is the canonical
store; OPEN_POSITIONS.md is a generated human-readable view.
"""
from __future__ import annotations

import json
import os
import hashlib
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from math import isfinite
from typing import Iterable

DEFAULT_DB = Path(os.environ.get("MOEX_TRADING_DB", str(Path(__file__).resolve().parents[1] / "trading.db")))


class Decision(str, Enum):
    NO_TRADE = "NO_TRADE"
    LONG_SETUP = "LONG_SETUP"
    SHORT_SETUP = "SHORT_SETUP"


class SetupState(str, Enum):
    WAITING = "WAITING"
    CROSSED = "CROSSED"
    HOURLY_CONFIRMED = "HOURLY_CONFIRMED"
    RETEST_CONFIRMED = "RETEST_CONFIRMED"
    ACTIVE = "ACTIVE"
    TP1 = "TP1"
    TP2 = "TP2"
    STOPPED = "STOPPED"
    CANCELLED = "CANCELLED"


TERMINAL_STATES = {SetupState.TP2, SetupState.STOPPED, SetupState.CANCELLED}


@dataclass
class TradePlan:
    ticker: str
    decision: Decision
    reason: str
    entry: float | None = None
    confirmation: str | None = None
    retest_required: bool = False
    stop: float | None = None
    structural_invalidation: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    risk: float | None = None
    reward_tp1: float | None = None
    reward_tp2: float | None = None
    rr_tp1: float | None = None
    rr_tp2: float | None = None
    created_at: str | None = None
    strategy_version: str | None = None
    setup_type: str = "breakout"
    expires_at: str | None = None
    holding_period: str | None = None
    tick_size: float | None = None
    atr_daily: float | None = None
    author: str = "backend"
    context_id: str | None = None
    analysis: dict = field(default_factory=dict)
    confidence: str | None = None

    def __post_init__(self) -> None:
        self.ticker = self.ticker.upper()
        if isinstance(self.decision, str):
            self.decision = Decision(self.decision)
        if self.created_at is None:
            self.created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self._calculate()
        if self.decision is not Decision.NO_TRADE:
            invalid = (
                None in (self.entry, self.stop, self.tp1, self.tp2)
                or self.entry == self.tp1
                or any(x is not None and not isfinite(x) for x in (self.entry, self.stop, self.tp1, self.tp2))
                or (self.reward_tp1 is not None and self.reward_tp1 <= 0)
                or (self.reward_tp1 is not None and self.reward_tp2 is not None and self.reward_tp1 >= self.reward_tp2)
                or self.risk is None
                or self.risk <= 0
                or self.rr_tp2 is None
                or self.rr_tp2 < 2
            )
            if invalid:
                self.decision = Decision.NO_TRADE
                self.reason = "Сценарий отклонён: некорректные уровни или R/R до TP2 ниже 1:2"
                self.confirmation = None

    @property
    def direction(self) -> str | None:
        return "LONG" if self.decision is Decision.LONG_SETUP else "SHORT" if self.decision is Decision.SHORT_SETUP else None

    def _calculate(self) -> None:
        if self.decision is Decision.NO_TRADE or self.entry is None or self.stop is None:
            return
        sign = 1 if self.decision is Decision.LONG_SETUP else -1
        self.risk = round(sign * (self.entry - self.stop), 8)
        if self.tp1 is not None:
            self.reward_tp1 = round(sign * (self.tp1 - self.entry), 8)
        if self.tp2 is not None:
            self.reward_tp2 = round(sign * (self.tp2 - self.entry), 8)
        if self.risk and self.risk > 0:
            self.rr_tp1 = round(self.reward_tp1 / self.risk, 3) if self.reward_tp1 is not None else None
            self.rr_tp2 = round(self.reward_tp2 / self.risk, 3) if self.reward_tp2 is not None else None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["decision"] = self.decision.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "TradePlan":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in allowed})


def build_plan(ticker: str, direction: str | None, supports: Iterable[float], resistances: Iterable[float], reason: str = "") -> TradePlan:
    """Select the best valid breakout or pullback setup in one trend direction.

    Every candidate must still clear TradePlan's 1:2 R/R gate.  Pullback entries
    use only confirmed clustered supports/resistances and require a retest plus
    an hourly reclaim; they are not limit-order instructions.
    """
    supports = sorted(set(float(x) for x in supports), reverse=True)
    resistances = sorted(set(float(x) for x in resistances))
    candidates: list[TradePlan] = []
    base_reason = reason or "Подтверждён направленный контекст"

    if direction == "LONG":
        if len(resistances) >= 3 and supports:
            candidates.append(TradePlan(
                ticker, Decision.LONG_SETUP, base_reason,
                entry=resistances[0], confirmation="hourly_close_above", retest_required=True,
                stop=supports[0], structural_invalidation=supports[0],
                tp1=resistances[1], tp2=resistances[2],
            ))
        for index, entry in enumerate(supports[:-1]):
            targets = [level for level in resistances if level > entry]
            if len(targets) < 2:
                continue
            stop = supports[index + 1]
            candidates.append(TradePlan(
                ticker, Decision.LONG_SETUP, f"{base_reason}; вход от подтверждённой поддержки",
                entry=entry, confirmation="hourly_reclaim_after_retest", retest_required=True,
                stop=stop, structural_invalidation=stop, tp1=targets[0], tp2=targets[1],
            ))

    if direction == "SHORT":
        if len(supports) >= 3 and resistances:
            candidates.append(TradePlan(
                ticker, Decision.SHORT_SETUP, base_reason,
                entry=supports[0], confirmation="hourly_close_below", retest_required=True,
                stop=resistances[0], structural_invalidation=resistances[0],
                tp1=supports[1], tp2=supports[2],
            ))
        for index, entry in enumerate(resistances[:-1]):
            targets = [level for level in supports if level < entry]
            if len(targets) < 2:
                continue
            stop = resistances[index + 1]
            candidates.append(TradePlan(
                ticker, Decision.SHORT_SETUP, f"{base_reason}; вход от подтверждённого сопротивления",
                entry=entry, confirmation="hourly_reject_after_retest", retest_required=True,
                stop=stop, structural_invalidation=stop, tp1=targets[0], tp2=targets[1],
            ))

    valid = [plan for plan in candidates if plan.decision is not Decision.NO_TRADE]
    if valid:
        return max(valid, key=lambda plan: (plan.rr_tp2 or 0, -(plan.risk or float("inf"))))
    return TradePlan(ticker, Decision.NO_TRADE, reason or "Нет единственного подтверждённого сценария")


def calculate_pnl(direction: str, entry: float, exit_price: float, quantity: float,
                  value_per_point: float | None = None, commission: float | None = None) -> dict:
    sign = 1 if direction.upper() == "LONG" else -1
    points = round((exit_price - entry) * sign, 8)
    gross = None if value_per_point is None else round(points * quantity * value_per_point, 2)
    net = None if gross is None else round(gross - (commission or 0), 2)
    return {"points_per_contract": points, "quantity": quantity, "gross_rub": gross,
            "commission_rub": commission, "net_rub": net}


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    init_db(db)
    return db


def init_db(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS trades(
      trade_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, direction TEXT NOT NULL,
      quantity REAL, entry_price REAL NOT NULL, entry_time TEXT, status TEXT NOT NULL DEFAULT 'OPEN',
      initial_stop REAL, current_stop REAL, structural_invalidation REAL, tp1 REAL, tp2 REAL,
      value_per_point REAL, commission REAL, exit_price REAL, exit_time TEXT, close_reason TEXT,
      source TEXT NOT NULL DEFAULT 'user', legacy_key TEXT UNIQUE, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS trade_events(
      id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id TEXT NOT NULL REFERENCES trades(trade_id),
      event_type TEXT NOT NULL, price REAL, quantity REAL, reason TEXT, source TEXT NOT NULL,
      happened_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS plans(
      plan_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, trade_date TEXT NOT NULL,
      payload TEXT NOT NULL, state TEXT NOT NULL, state_time TEXT NOT NULL,
      revision INTEGER NOT NULL DEFAULT 0,
      root_plan_id TEXT NOT NULL,
      UNIQUE(ticker, trade_date, revision)
    );
    CREATE TABLE IF NOT EXISTS plan_events(
      id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT NOT NULL REFERENCES plans(plan_id),
      old_state TEXT, new_state TEXT NOT NULL, price REAL, happened_at TEXT NOT NULL, note TEXT
    );
    CREATE TABLE IF NOT EXISTS analysis_contexts(
      context_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, trade_date TEXT NOT NULL,
      generated_at TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING'
    );
    CREATE TABLE IF NOT EXISTS agent_feedback(
      submission_id TEXT PRIMARY KEY, context_id TEXT NOT NULL REFERENCES analysis_contexts(context_id),
      request_hash TEXT NOT NULL, payload TEXT NOT NULL, receipt TEXT NOT NULL,
      status TEXT NOT NULL, received_at TEXT NOT NULL
    );
    """)
    columns = {r[1] for r in db.execute("PRAGMA table_info(plans)")}
    if "revision" not in columns:
        # The original schema used UNIQUE(ticker, trade_date), which made an
        # audited replacement impossible: it could only overwrite revision 0.
        db.commit()
        db.execute("PRAGMA foreign_keys=OFF")
        db.executescript("""
        CREATE TABLE plans_revised(
          plan_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, trade_date TEXT NOT NULL,
          payload TEXT NOT NULL, state TEXT NOT NULL, state_time TEXT NOT NULL,
          last_candle_time TEXT, activated_at TEXT,
          revision INTEGER NOT NULL DEFAULT 0,
          root_plan_id TEXT NOT NULL,
          UNIQUE(ticker, trade_date, revision)
        );
        INSERT INTO plans_revised(
          plan_id,ticker,trade_date,payload,state,state_time,last_candle_time,
          activated_at,revision,root_plan_id
        )
        SELECT plan_id,ticker,trade_date,payload,state,state_time,last_candle_time,
               activated_at,0,plan_id
        FROM plans;
        DROP TABLE plans;
        ALTER TABLE plans_revised RENAME TO plans;
        """)
        db.execute("PRAGMA foreign_keys=ON")
        columns = {r[1] for r in db.execute("PRAGMA table_info(plans)")}
    for name in ("last_candle_time", "activated_at"):
        if name not in columns:
            db.execute(f"ALTER TABLE plans ADD COLUMN {name} TEXT")
    db.commit()


def open_trade(db: sqlite3.Connection, ticker: str, direction: str, entry_price: float,
               quantity: float | None = None, entry_time: str | None = None, source: str = "user",
               stop: float | None = None, tp1: float | None = None, tp2: float | None = None,
               value_per_point: float | None = None, trade_id: str | None = None,
               legacy_key: str | None = None) -> str:
    trade_id = trade_id or uuid.uuid4().hex[:12]
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with db:
        db.execute("""INSERT INTO trades(trade_id,ticker,direction,quantity,entry_price,entry_time,status,
                   initial_stop,current_stop,tp1,tp2,value_per_point,source,legacy_key,created_at)
                   VALUES(?,?,?,?,?,?, 'OPEN',?,?,?,?,?,?,?,?)""",
                   (trade_id, ticker.upper(), direction.upper(), quantity, entry_price, entry_time,
                    stop, stop, tp1, tp2, value_per_point, source, legacy_key, now))
        db.execute("INSERT INTO trade_events(trade_id,event_type,price,quantity,source,happened_at) VALUES(?,?,?,?,?,?)",
                   (trade_id, "OPEN", entry_price, quantity, source, entry_time or now))
    return trade_id


def set_stop(db: sqlite3.Connection, trade_id: str, stop: float, source: str = "user") -> None:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with db:
        row = db.execute("SELECT initial_stop FROM trades WHERE trade_id=?", (trade_id,)).fetchone()
        if not row:
            raise ValueError(f"Сделка {trade_id} не найдена")
        db.execute("UPDATE trades SET current_stop=?, initial_stop=COALESCE(initial_stop,?) WHERE trade_id=?", (stop, stop, trade_id))
        db.execute("INSERT INTO trade_events(trade_id,event_type,price,source,happened_at) VALUES(?,?,?,?,?)",
                   (trade_id, "SET_STOP", stop, source, now))


def close_trade(db: sqlite3.Connection, trade_id: str, price: float, quantity: float | None = None,
                reason: str = "user", source: str = "user", happened_at: str | None = None) -> None:
    now = happened_at or datetime.now().astimezone().isoformat(timespec="seconds")
    with db:
        row = db.execute("SELECT * FROM trades WHERE trade_id=?", (trade_id,)).fetchone()
        if not row or row["status"] != "OPEN":
            raise ValueError(f"Открытая сделка {trade_id} не найдена")
        partial = quantity is not None and row["quantity"] is not None and quantity < row["quantity"]
        event = "PARTIAL_CLOSE" if partial else "CLOSE"
        db.execute("INSERT INTO trade_events(trade_id,event_type,price,quantity,reason,source,happened_at) VALUES(?,?,?,?,?,?,?)",
                   (trade_id, event, price, quantity, reason, source, now))
        if partial:
            db.execute("UPDATE trades SET quantity=quantity-? WHERE trade_id=?", (quantity, trade_id))
        else:
            db.execute("UPDATE trades SET status='CLOSED',exit_price=?,exit_time=?,close_reason=? WHERE trade_id=?",
                       (price, now, reason, trade_id))


def open_positions(db: sqlite3.Connection, ticker: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM trades WHERE status='OPEN'", []
    if ticker:
        sql += " AND ticker=?"; args.append(ticker.upper())
    return [dict(r) for r in db.execute(sql + " ORDER BY created_at", args)]


def migrate_legacy_markdown(db: sqlite3.Connection, path: str | Path) -> int:
    """Import old OPEN_POSITIONS.md sections once, preserving unknown fields as NULL."""
    text = Path(path).read_text(encoding="utf-8")
    count = 0
    for section in re.split(r"(?m)^##\s+", text)[1:]:
        title, *body = section.splitlines()
        fields = {}
        for line in body:
            m = re.match(r"-\s*([^:]+):\s*(.*)", line)
            if m:
                fields[m.group(1).strip()] = m.group(2).strip()
        ticker_match = re.search(r"\b(IMOEXF|USDRUBF)\b", title.upper())
        direction = fields.get("Направление", "").upper()
        entry_raw = fields.get("Цена входа", "")
        num = re.search(r"[-+]?\d+(?:[.,]\d+)?", entry_raw)
        if not ticker_match or direction not in {"ЛОНГ", "ШОРТ", "LONG", "SHORT"} or not num:
            continue
        legacy_key = hashlib.sha256(section.encode("utf-8")).hexdigest()
        if db.execute("SELECT 1 FROM trades WHERE legacy_key=?", (legacy_key,)).fetchone():
            continue
        qty_m = re.search(r"\d+(?:[.,]\d+)?", fields.get("Объём", ""))
        stop_m = re.search(r"[-+]?\d+(?:[.,]\d+)?", fields.get("Стоп", ""))
        tid = open_trade(db, ticker_match.group(1), "LONG" if direction in {"ЛОНГ", "LONG"} else "SHORT",
                         float(num.group().replace(",", ".")), float(qty_m.group().replace(",", ".")) if qty_m else None,
                         entry_time=fields.get("Дата открытия"), source="migration",
                         stop=float(stop_m.group().replace(",", ".")) if stop_m else None, legacy_key=legacy_key)
        if fields.get("Статус", "").upper() in {"ЗАКРЫТА", "CLOSED"}:
            exit_m = re.search(r"[-+]?\d+(?:[.,]\d+)?", fields.get("Цена закрытия", ""))
            with db:
                db.execute("UPDATE trades SET status='CLOSED',exit_price=?,exit_time=?,close_reason=? WHERE trade_id=?",
                           (float(exit_m.group().replace(",", ".")) if exit_m else None,
                            fields.get("Дата закрытия"), fields.get("Причина закрытия"), tid))
        count += 1
    return count


def save_plan(db: sqlite3.Connection, plan: TradePlan, trade_date: str, *, replace: bool = False) -> str:
    plan_id = f"{plan.ticker}-{trade_date}"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    state = SetupState.WAITING.value if plan.decision is not Decision.NO_TRADE else SetupState.CANCELLED.value
    with db:
        existing = db.execute("SELECT state FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
        if replace and existing:
            if existing["state"] in {SetupState.ACTIVE.value, SetupState.TP1.value}:
                raise ValueError("Нельзя перезаписать активный многодневный сценарий")
            db.execute("UPDATE plans SET payload=?,state=?,state_time=?,last_candle_time=NULL,activated_at=NULL WHERE plan_id=?",
                       (json.dumps(plan.to_dict(), ensure_ascii=False), state, now, plan_id))
            db.execute("INSERT INTO plan_events(plan_id,old_state,new_state,happened_at,note) VALUES(?,?,?,?,?)",
                       (plan_id, existing["state"], state, now, "Утренний переход на новую версию стратегии"))
            return plan_id
        db.execute("""INSERT INTO plans(
                       plan_id,ticker,trade_date,payload,state,state_time,revision,root_plan_id
                       ) VALUES(?,?,?,?,?,?,0,?)
                    ON CONFLICT(ticker,trade_date,revision) DO NOTHING""",
                   (plan_id, plan.ticker, trade_date,
                    json.dumps(plan.to_dict(), ensure_ascii=False), state, now, plan_id))
    return plan_id


def load_plan(db: sqlite3.Connection, ticker: str, trade_date: str) -> tuple[str, TradePlan, SetupState] | None:
    row = db.execute("""SELECT * FROM plans WHERE ticker=? AND trade_date=?
                      ORDER BY revision DESC LIMIT 1""", (ticker.upper(), trade_date)).fetchone()
    return None if not row else (row["plan_id"], TradePlan.from_dict(json.loads(row["payload"])), SetupState(row["state"]))


def load_plan_id(db: sqlite3.Connection, plan_id: str) -> tuple[str, TradePlan, SetupState] | None:
    row = db.execute("SELECT * FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
    return None if not row else (row["plan_id"], TradePlan.from_dict(json.loads(row["payload"])), SetupState(row["state"]))


def latest_plan(db: sqlite3.Connection, ticker: str) -> tuple[str, TradePlan, SetupState] | None:
    row = db.execute("""SELECT plan_id FROM plans WHERE ticker=?
                      ORDER BY trade_date DESC, revision DESC LIMIT 1""", (ticker.upper(),)).fetchone()
    return load_plan_id(db, row["plan_id"]) if row else None


def advance_plan(db: sqlite3.Connection, plan_id: str, plan: TradePlan, state: SetupState,
                 price: float, candle_close: float | None = None, candle_high: float | None = None,
                 candle_low: float | None = None, candle_time: str | None = None,
                 retest_confirmed: bool = False, cancel_confirmed: bool = False) -> SetupState:
    """Advance monotonically from market facts; never erase an earlier crossing."""
    if state in TERMINAL_STATES or plan.decision is Decision.NO_TRADE:
        return state
    assert plan.entry is not None and plan.stop is not None and plan.tp1 is not None and plan.tp2 is not None
    entry, stop, tp1, tp2 = plan.entry, plan.stop, plan.tp1, plan.tp2
    long = plan.decision is Decision.LONG_SETUP
    crossed = price >= entry if long else price <= entry
    confirmed = candle_close is not None and (candle_close > entry if long else candle_close < entry)
    new = state
    if state is SetupState.WAITING and crossed:
        new = SetupState.CROSSED
    if state in {SetupState.WAITING, SetupState.CROSSED} and confirmed:
        new = SetupState.HOURLY_CONFIRMED
    if new is SetupState.HOURLY_CONFIRMED:
        if plan.retest_required and retest_confirmed:
            new = SetupState.RETEST_CONFIRMED
        elif not plan.retest_required:
            new = SetupState.ACTIVE
    if new is SetupState.RETEST_CONFIRMED:
        new = SetupState.ACTIVE
    hi = candle_high if candle_high is not None else price
    lo = candle_low if candle_low is not None else price
    if new in {SetupState.ACTIVE, SetupState.TP1}:
        if (long and lo <= stop) or (not long and hi >= stop):
            new = SetupState.STOPPED
        elif (long and hi >= tp2) or (not long and lo <= tp2):
            new = SetupState.TP2
        elif (long and hi >= tp1) or (not long and lo <= tp1):
            new = SetupState.TP1
        elif cancel_confirmed:
            new = SetupState.CANCELLED
    if new != state:
        when = candle_time or datetime.now().astimezone().isoformat(timespec="seconds")
        with db:
            db.execute("UPDATE plans SET state=?,state_time=? WHERE plan_id=?", (new.value, when, plan_id))
            db.execute("INSERT INTO plan_events(plan_id,old_state,new_state,price,happened_at) VALUES(?,?,?,?,?)",
                       (plan_id, state.value, new.value, price, when))
    return new


def render_markdown(db: sqlite3.Connection) -> str:
    lines = ["# Журнал позиций", "", "Каноническое состояние: `trading.db`. Этот файл сгенерирован автоматически.", ""]
    rows = db.execute("SELECT * FROM trades ORDER BY created_at,trade_id").fetchall()
    for r in rows:
        lines += [f"## {r['ticker']} · {r['trade_id']}", "", f"- Статус: {r['status']}",
                  f"- Направление: {r['direction']}", f"- Цена входа: {r['entry_price']}",
                  f"- Дата/время входа: {r['entry_time'] or 'не указано'}",
                  f"- Объём: {r['quantity'] if r['quantity'] is not None else 'не указан'}",
                  f"- Стоп: {r['current_stop'] if r['current_stop'] is not None else 'не установлен'}",
                  f"- Структурная отмена: {r['structural_invalidation'] if r['structural_invalidation'] is not None else 'не указана'}",
                  f"- TP1 / TP2: {r['tp1'] if r['tp1'] is not None else '—'} / {r['tp2'] if r['tp2'] is not None else '—'}"]
        if r["status"] == "CLOSED":
            lines += [f"- Цена выхода: {r['exit_price'] if r['exit_price'] is not None else 'не указана'}",
                      f"- Дата/время выхода: {r['exit_time'] or 'не указано'}", f"- Причина: {r['close_reason'] or 'не указана'}"]
            if r["exit_price"] is not None:
                pnl = calculate_pnl(r["direction"], r["entry_price"], r["exit_price"], r["quantity"] or 1,
                                    r["value_per_point"], r["commission"])
                lines.append(f"- P/L: {pnl['points_per_contract']} пт.; {pnl['net_rub'] if pnl['net_rub'] is not None else '₽ не рассчитаны — неизвестна стоимость пункта'}")
        lines.append("")
    return "\n".join(lines)
