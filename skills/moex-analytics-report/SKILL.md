---
name: moex-analytics-report
description: "MOEX medium-term reports: T-Invest W1/D1 regimes, H4-confirmed setups and fixed plans."
---

## Scope and schedule

Work in `/home/user1/moex` with IMOEXF and USDRUBF; holding horizon is 3–20 trading days. The live weekday schedule is: morning 08:07, H4-close controls at 12:01, 16:01 and 20:01, evening digest 21:01 MSK. Do not test through message delivery.

Run project commands with `.venv/bin/python`. Use a temporary `MOEX_TRADING_DB` for wrapper tests; run `.venv/bin/python -m unittest discover -s tests`.

## Codex delegation

For every MOEX project task, delegate implementation and the first draft of the user-facing answer to local Codex: `codex exec -m gpt-5.6-sol` from `/home/user1/moex`. Use read-only mode for analysis and review; use workspace-write only for requested edits. After any non-trivial change, start a separate fresh read-only Codex session as critic to review the diff, report format, safety rules, tests, cron impact, and broker-order boundary. Hermes independently verifies the result, especially tests, cron state, and live broker facts. Codex never places, changes, or cancels broker orders.

## Evidence and plan

- T-Invest is the default source. Never print tokens. Use ISS only with explicit `--source iss`; never silently fall back.
- W1 defines the regime, D1 defines working direction/ATR, and H4 alone confirms the setup and structural levels. H1 may only verify a breakout and later retest of an already H4-confirmed setup. Use completed candles only; waves are context, not a proven signal.
- Read `docs/AGENT_PROTOCOL.md`. The morning collector supplies `agent_handoff`; the agent chooses the scenario.
- Submit the decision with `scripts/moex_agent_plan.py submit`, require `ACCEPTED` or `REVIEWED`, then read `get --ticker`. Before acceptance, no plan exists. For `KEEP_PLAN`, send a fresh assessment without prices.
- The backend breakout/retest trigger requires a closed H1 beyond the level and a later H1 retest, but H1 never determines medium-term direction. The control only replays saved plans.
- An active plan remains unchanged intraday and across days. The only exception is a pre-entry `CANCELLED` plan: replace it from fresh monitor evidence only if the broker has neither a position nor an active regular order.
- With `stale=true`, label the price as from the last session; still show broker facts. On missing data/API errors, state the failure without inventing facts.

## Broker and risk

Check portfolio, regular orders, and stop orders before assessing a position. A market-plan state (including `ACTIVE`, TP, or STOP) is not execution; a stop order is not an execution price; no position does not explain how or where it was closed.

Do not propose a new entry when a position or active regular order exists. Do not calculate lot size or ruble risk without a confirmed multiplier and balance. Risk policy: 1–2% per trade, 2–3% daily loss limit, no averaging; these limits are not automated. Do not alter broker orders without explicit user instruction.

## Output

Write concise Russian without tables. Every report states current/last confirmed price, medium-term W1/D1 regime with H4 confirmation, action now, and—when no action—the exact larger-scale D1/H4 close or level awaited. Mention H1 only for the saved breakout/retest trigger. Keep broker facts separate from scenario state.

Never call `ACTIVE` an executed trade. For `NO_TRADE`, state `нет сделки` and the reason. Use `не подтверждено` for unknowns. Evening digest keeps market scenario, price observations, and real trades separate.
