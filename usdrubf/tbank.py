"""Read-only T-Invest market data. No silent fallback to delayed ISS data."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as daytime, timedelta, timezone
from pathlib import Path

from .moex import Board, Candle, MSK, MoexError, Quote

BASE = "https://invest-public-api.tbank.ru/rest/tinkoff.public.invest.api.contract.v1"


def load_project_env() -> None:
    path = Path(os.environ.get("MOEX_ENV_FILE", str(Path(__file__).resolve().parents[1] / ".env")))
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if key.strip() and (not os.environ.get("MOEX_ENV") or key.strip().startswith("TINKOFF_")):
                    os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def quotation(value: dict | None) -> float | None:
    return None if not value else int(value.get("units", 0)) + int(value.get("nano", 0)) / 1e9


def parse_time(value: str) -> datetime:
    value = re.sub(r"(\.\d{6})\d+", r"\1", value)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(MSK)


def call(service: str, payload: dict, token: str) -> dict:
    # All callers are readers; retry only transient server/network errors.
    for attempt in range(3):
        request = urllib.request.Request(
            f"{BASE}.{service}", data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise MoexError(f"T-Invest {service}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 2:
                raise MoexError(f"T-Invest {service}: сеть недоступна") from exc
        time.sleep(2 ** attempt)
    raise MoexError("T-Invest недоступен")


# Conservative windows fit both the documented period and candle-count limits.
INTERVALS = {
    1: ("CANDLE_INTERVAL_1_MIN", 1), 5: ("CANDLE_INTERVAL_5_MIN", 7),
    10: ("CANDLE_INTERVAL_10_MIN", 7), 15: ("CANDLE_INTERVAL_15_MIN", 14),
    30: ("CANDLE_INTERVAL_30_MIN", 14), 60: ("CANDLE_INTERVAL_HOUR", 60),
    120: ("CANDLE_INTERVAL_2_HOUR", 60), 240: ("CANDLE_INTERVAL_4_HOUR", 60),
    24: ("CANDLE_INTERVAL_DAY", 365), 7: ("CANDLE_INTERVAL_WEEK", 365),
}


class TBankClient:
    def __init__(self, secid: str = "USDRUBF", *, token: str | None = None,
                 board: str | None = None):
        load_project_env()
        self.secid = secid.upper()
        self.token = token or os.environ.get("TINKOFF_TOKEN")
        if not self.token:
            raise MoexError("Для T-Invest задайте TINKOFF_TOKEN в окружении или .env")
        self.board = board
        self._instrument: dict | None = None

    def _call(self, service: str, payload: dict) -> dict:
        return call(service, payload, self.token)

    @property
    def instrument(self) -> dict:
        if self._instrument is None:
            data = self._call("InstrumentsService/FindInstrument", {"query": self.secid})
            matches = [i for i in data.get("instruments", []) if i.get("ticker") == self.secid
                       and (self.board is None or i.get("classCode") == self.board)]
            if len(matches) != 1:
                raise MoexError(f"T-Invest: {self.secid} найден неоднозначно или недоступен; задайте --board")
            self._instrument = self._call("InstrumentsService/GetInstrumentBy", {
                "idType": "INSTRUMENT_ID_TYPE_UID", "id": matches[0]["uid"]})["instrument"]
        return self._instrument

    @property
    def tick_size(self) -> float:
        tick = quotation(self.instrument.get("minPriceIncrement"))
        if tick is None or tick <= 0:
            raise MoexError(f"Неизвестен шаг цены {self.secid}")
        return tick

    def resolve(self) -> Board:
        i = self.instrument
        futures = i.get("instrumentType") == "futures"
        decimals = max(0, len(f"{self.tick_size:.9f}".rstrip("0").split(".")[-1]))
        return Board("futures" if futures else "stock", "forts" if futures else i.get("instrumentType", ""),
                     i["classCode"], decimals)

    def get_quote(self) -> Quote:
        rows = self._call("MarketDataService/GetLastPrices", {
            "instrumentId": [self.instrument["uid"]], "lastPriceType": "LAST_PRICE_EXCHANGE"}).get("lastPrices", [])
        if not rows or rows[0].get("lastPriceType") != "LAST_PRICE_EXCHANGE":
            raise MoexError(f"Нет биржевой цены T-Invest для {self.secid}")
        row, b = rows[0], self.resolve()
        timestamp = parse_time(row["time"])
        return Quote(self.secid, b.board, quotation(row.get("price")), None, None, None, None, None,
                     None, None, None, None, timestamp, datetime.now(MSK), timestamp.date().isoformat(),
                     source="T-Invest API", engine=b.engine, market=b.market, decimals=b.decimals)

    def trading_status(self) -> dict:
        return self._call("MarketDataService/GetTradingStatus", {"instrumentId": self.instrument["uid"]})

    def get_candles(self, interval: int, start: date | datetime,
                    end: date | datetime | None = None) -> list[Candle]:
        if interval not in INTERVALS:
            raise ValueError(f"Неподдерживаемый интервал T-Invest: {interval}")
        name, days = INTERVALS[interval]
        left = start if isinstance(start, datetime) else datetime.combine(start, daytime(), MSK)
        right = (end if isinstance(end, datetime) else datetime.combine(end + timedelta(days=1), daytime(), MSK)) if end else datetime.now(MSK)
        if left.tzinfo is None or right.tzinfo is None:
            raise ValueError("Время запроса должно содержать часовой пояс")
        result = {}
        while left < right:
            stop = min(left + timedelta(days=days), right)
            data = self._call("MarketDataService/GetCandles", {
                "instrumentId": self.instrument["uid"], "interval": name,
                "from": left.astimezone(timezone.utc).isoformat(),
                "to": stop.astimezone(timezone.utc).isoformat(),
                "candleSourceType": "CANDLE_SOURCE_EXCHANGE",
            })
            for row in data.get("candles", []):
                c = Candle(parse_time(row["time"]), *(quotation(row[k]) for k in ("open", "high", "low", "close")),
                           float(row["volume"]), is_complete=row.get("isComplete", False))
                if c.begin < right:
                    result[c.begin] = c
            left = stop
        return [result[k] for k in sorted(result)]

    def get_candles_minutes(self, minutes: int, start: date | datetime,
                            end: date | datetime | None = None) -> list[Candle]:
        return self.get_candles({1440: 24, 10080: 7}.get(minutes, minutes), start, end)

    def recent(self, minutes: int, days: int) -> list[Candle]:
        return self.get_candles_minutes(minutes, datetime.now(MSK).date() - timedelta(days=days))
