#!/usr/bin/env python3
"""Read-only T-Investments position monitor for the MOEX trading project.

Requires TINKOFF_TOKEN in the process environment. It never stores or prints it.
Optionally set TINKOFF_ACCOUNT_ID; the project account is the default.
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from usdrubf.tbank import call, load_project_env, quotation

BASE = "https://invest-public-api.tbank.ru/rest/tinkoff.public.invest.api.contract.v1"
ACCOUNT_ID = os.environ.get("TINKOFF_ACCOUNT_ID", "2140796034")
TRACKED = {"IMOEXF", "USDRUBF"}


def tracked_positions(portfolio: dict) -> list[dict]:
    result = []
    for item in portfolio.get("positions", []):
        ticker = item.get("ticker")
        if ticker not in TRACKED or quotation(item.get("quantityLots")) == 0:
            continue
        lots = quotation(item.get("quantityLots"))
        result.append({
            "ticker": ticker,
            "direction": "LONG" if (lots or 0) > 0 else "SHORT" if (lots or 0) < 0 else None,
            "lots": abs(lots) if lots is not None else None,
            "average_price": quotation(item.get("averagePositionPrice")),
            "current_price": quotation(item.get("currentPrice")),
            "price_currency": item.get("currentPrice", {}).get("currency"),
            "unrealized_pnl_rub": quotation(item.get("expectedYield")),
            "daily_pnl_rub": quotation(item.get("dailyYield")),
            "blocked": bool(item.get("blocked")),
        })
    return result


def relevant_orders(data: dict, key: str) -> list[dict]:
    return [
        order for order in data.get(key, [])
        if order.get("ticker") in TRACKED and (
            (key == "orders" and order.get("executionReportStatus") in {
                "EXECUTION_REPORT_STATUS_NEW", "EXECUTION_REPORT_STATUS_PARTIALLYFILL"})
            or (key == "stopOrders" and order.get("status") == "STOP_ORDER_STATUS_ACTIVE")
        )
    ]


def fetch_state() -> dict:
    """Return live broker state; never fall back to the local trade journal."""
    token = os.environ.get("TINKOFF_TOKEN")
    if not token:
        raise RuntimeError("задайте TINKOFF_TOKEN в окружении или project .env")
    account_id = os.environ.get("TINKOFF_ACCOUNT_ID", "" if os.environ.get("MOEX_ENV") == "staging" else ACCOUNT_ID)
    if not account_id:
        raise RuntimeError("задайте TINKOFF_ACCOUNT_ID для тестового стенда")
    with ThreadPoolExecutor(max_workers=3) as pool:
        portfolio_future = pool.submit(call, "OperationsService/GetPortfolio", {"accountId": account_id}, token)
        orders_future = pool.submit(call, "OrdersService/GetOrders", {"accountId": account_id}, token)
        stops_future = pool.submit(call, "StopOrdersService/GetStopOrders", {"accountId": account_id}, token)
        portfolio = portfolio_future.result()
        orders = orders_future.result()
        stops = stops_future.result()
    return {
        "account_id": account_id,
        "portfolio_value_rub": quotation(portfolio.get("totalAmountPortfolio")),
        "cash_rub": quotation(portfolio.get("totalAmountCurrencies")),
        "daily_pnl_rub": quotation(portfolio.get("dailyYield")),
        "positions": tracked_positions(portfolio),
        "orders": relevant_orders(orders, "orders"),
        "stop_orders": relevant_orders(stops, "stopOrders"),
    }


def main() -> int:
    load_project_env()
    try:
        state = fetch_state()
    except RuntimeError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
