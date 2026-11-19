"""Read-only medium-term context: W1/D1 regime, H4 setup, optional H1 trigger."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from usdrubf.process import collect_compact


def main() -> None:
    ticker = sys.argv[1] if len(sys.argv) > 1 else "USDRUBF"
    payload, _, _ = collect_compact(ticker)
    print(f"# {ticker} — контекст решения на несколько дней или недель")
    print(f"Источник: {payload['source']} · цена {payload['price']} · время {payload['last_trade_time']}")
    if payload["stale"]:
        print("Цена справочная, последней сессии; не является текущей ценой входа.")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
