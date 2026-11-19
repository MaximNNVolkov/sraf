# MOEX Trading Project

## Purpose

A single project for the user's IMOEXF and USDRUBF trading workflow. The account objective is 100,000 ₽ by 2027-01-01; it never overrides risk rules or causes forced trades.

## Module 1 — Market monitoring and reports

**Purpose:** collect T-Invest exchange data, build market structure and deliver scheduled reports.

- Core: `usdrubf/tbank.py`, `usdrubf/moex.py`, `usdrubf/analysis.py`, `usdrubf/report.py`, `usdrubf/process.py`.
- Report scripts: `scripts/moex_imoexf_tf.py`, `scripts/moex_usdrubf_tf.py`, `scripts/moex_monitor_tf.py`, `scripts/moex_day_digest.py`, `scripts/tf_report.py`.
- Scheduled jobs:
  - morning reports: 08:07 MSK on weekdays;
  - H4-close control: 12:01, 16:01 and 20:01 MSK on weekdays;
  - evening digest: 21:01 MSK on weekdays.
- Instruments: IMOEXF and USDRUBF.
- Live prices older than 20 minutes are marked stale; closed-session prices may be labelled references for morning planning. Broker positions are still shown.
- Medium-term hierarchy: W1 regime, D1 direction, H4 setup confirmation. H1 is
  limited to breakout/retest replay and never determines trend. See `docs/STRATEGY.md`.
- The evening script builds a new structured snapshot from T-Invest market data,
  a read-only T-Invest broker snapshot and `MOEX_TRADING_DB`. It replays completed
  H1 candles before reading the saved scenario. Cron/Telegram output is appended
  only as a chronology and is not a source for prices, H/L, levels or state.
- `scripts/moex_health_check.py` checks, per ticker, context freshness, the latest
  `ACCEPTED`/`REVIEWED`, overdue `PENDING`, broker snapshot success and the last
  H1 replay. It opens SQLite read-only and uses only broker read endpoints. Its
  human output and exit code (`0` only for `OK`) are suitable for cron.

## Module 2 — Open-position monitoring

**Purpose:** reconcile recorded positions and real T-Investments state before every position assessment.

- Local journal: `trading.db`; generated display: `OPEN_POSITIONS.md`.
- Broker source of truth: T-Investments read-only API.
- Required checks: portfolio, active regular orders, and active stop/take-profit orders.
- Do not persist or expose the broker token. The token must be supplied only to the active session or process environment.
- Any broker-side change must be read back before it is reported as successful.

## Module 3 — Strategy and risk management

**Purpose:** let the agent select one setup per instrument, validate it and monitor the saved scenario.

- Agent feedback: `usdrubf/agent.py`, `scripts/moex_agent_plan.py`; immutable contexts, structured submissions and acceptance/rejection receipts. See `docs/AGENT_PROTOCOL.md`.
- Strategy state machine and trade journal: `usdrubf/trading.py`.
- Entry selection: fixed medium-term plan (3–20 trading days) derived from W1/D1 and
  confirmed on H4, with an agent-chosen structural stop and R/R to TP2 of at least
  1:2. A closed H1 plus later retest is only the technical trigger for a saved
  breakout/retest setup. Active scenarios persist across days; controls replay each
  closed hour once without reporting hourly noise as a trend change.
- Risk percentages below are policy; automatic equity-based sizing and daily-loss enforcement are not implemented.
- Risk per trade: 1–2% of current account equity.
- Daily loss limit: 2–3% of equity; after reaching it, no new trades until the next trading day.
- No averaging down. Unallocated money is parked in TMON.
- A recommendation must state entry, stop, TP1, and TP2. Lots and ruble risk may
  be stated only when the contract multiplier and account balance are confirmed;
  otherwise it must explicitly state that lot size and ruble risk are not calculated
  (`sizing_status=NOT_CALCULATED`, `ruble_risk_verified=false`). It must not place
  or change an order without the user's explicit instruction.

## Layout

- `usdrubf/` — reusable market, analysis, strategy and journal package.
- `scripts/` — all cron and report entry points.
- `skills/moex-analytics-report/` — Hermes instructions for all three modules.
- `docs/` — architecture and operational documentation.
- `tests/` — automated tests.

Hermes launchers such as `moex-evening-summary.sh` and `moex-levels-2h.sh` are
regular wrapper files under `~/.hermes/scripts/`; they are not symlinks. Some
Python compatibility entry points may be symlinks into this repository. Source
and operational wrapper are therefore distinct and should be inspected separately.
