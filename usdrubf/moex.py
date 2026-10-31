"""Клиент ISS Московской биржи (https://iss.moex.com) для любого инструмента.

Рынок и режим торгов определяются по тикеру (основной режим из ISS), их можно задать явно.
Только стандартная библиотека. Все времена ISS — московские (Europe/Moscow).
Неавторизованный доступ к ISS отдаёт данные с задержкой ~15 минут.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

ISS_BASE = "https://iss.moex.com/iss"
MSK = ZoneInfo("Europe/Moscow")

DEFAULT_SECID = "USDRUBF"

# Интервалы, которые ISS отдаёт напрямую (минуты; 24 — день, 7 — неделя, 31 — месяц, 4 — квартал).
ISS_INTERVALS = {1, 10, 60, 24, 7, 31, 4}
ISS_PAGE = 500  # максимум строк свечей в одном ответе ISS


@dataclass(frozen=True)
class Candle:
    begin: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict:
        return {
            "begin": self.begin.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(frozen=True)
class Board:
    engine: str
    market: str
    board: str
    decimals: int = 2


@dataclass(frozen=True)
class Quote:
    secid: str
    board: str
    last: float | None
    bid: float | None
    offer: float | None
    open: float | None
    high: float | None
    low: float | None
    settle_price: float | None
    change_pct: float | None
    open_interest: float | None
    volume_today: float | None
    last_trade_time: datetime | None  # время последней сделки (MSK)
    system_time: datetime | None  # время формирования данных на ISS (MSK)
    trade_date: str | None
    source: str = "MOEX ISS"
    engine: str = ""
    market: str = ""
    decimals: int = 2

    @property
    def delay_minutes(self) -> float | None:
        if self.last_trade_time is None:
            return None
        now = datetime.now(MSK)
        return (now - self.last_trade_time).total_seconds() / 60

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        for k in ("last_trade_time", "system_time"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d


class MoexError(RuntimeError):
    pass


class MoexClient:
    def __init__(
        self,
        secid: str = DEFAULT_SECID,
        engine: str | None = None,
        market: str | None = None,
        board: str | None = None,
        timeout: float = 20,
        retries: int = 3,
    ):
        self.secid = secid.upper()
        self.engine, self.market, self.board = engine, market, board
        self.timeout = timeout
        self.retries = retries
        self._board: Board | None = None

    # --- транспорт -------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict:
        params = {"iss.meta": "off", **(params or {})}
        url = f"{ISS_BASE}/{path}.json?{urllib.parse.urlencode(params)}"
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "moex-elliott-agent/1.0"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as exc:  # noqa: BLE001 — сеть/JSON, повторяем
                last_exc = exc
                time.sleep(2**attempt)
        raise MoexError(f"ISS недоступен: {url}: {last_exc}")

    def resolve(self) -> Board:
        """Рынок и режим торгов: заданные явно, иначе основной (is_primary) режим инструмента в ISS."""
        if self._board is None:
            data = self._get(f"securities/{self.secid}", {"iss.only": "boards"})
            rows = [
                r for r in _rows(data, "boards")
                if all(want is None or r.get(k) == want
                       for k, want in (("engine", self.engine), ("market", self.market), ("boardid", self.board)))
            ]
            if not rows:
                raise MoexError(f"Инструмент {self.secid} не найден на MOEX (engine={self.engine}, market={self.market}, board={self.board})")
            r = max(rows, key=lambda r: (r.get("is_primary") == 1, r.get("is_traded") == 1))
            decimals = r.get("decimals")
            self._board = Board(r["engine"], r["market"], r["boardid"], 2 if decimals is None else int(decimals))
        return self._board

    @property
    def _sec_path(self) -> str:
        b = self.resolve()
        return f"engines/{b.engine}/markets/{b.market}/boards/{b.board}/securities/{self.secid}"

    # --- котировка -------------------------------------------------------

    def get_quote(self) -> Quote:
        data = self._get(self._sec_path, {"iss.only": "securities,marketdata"})
        md = _rows(data, "marketdata")
        if not md:
            raise MoexError(f"Нет marketdata для {self.secid}")
        m = md[0]
        b = self.resolve()
        systime = m.get("SYSTIME")
        trade_date = m.get("TRADEDATE") or (systime[:10] if systime else None)
        return Quote(
            secid=m["SECID"],
            board=m.get("BOARDID") or b.board,
            last=_first(m, "LAST", "CURRENTVALUE"),  # у индексов цены сделки нет, есть значение индекса
            bid=m.get("BID"),
            offer=m.get("OFFER"),
            open=m.get("OPEN"),
            high=m.get("HIGH"),
            low=m.get("LOW"),
            settle_price=m.get("SETTLEPRICE"),
            change_pct=_first(m, "LASTTOPREVPRICE", "LASTCHANGEPRC"),
            open_interest=m.get("OPENPOSITION"),
            volume_today=m.get("VOLTODAY"),
            last_trade_time=_combine(trade_date, m.get("UPDATETIME")),
            system_time=_parse_dt(systime),
            trade_date=trade_date,
            engine=b.engine,
            market=b.market,
            decimals=b.decimals,
        )

    # --- свечи -----------------------------------------------------------

    def get_candles(self, interval: int, start: date | datetime, end: date | datetime | None = None) -> list[Candle]:
        """Свечи ISS с автоматической пагинацией. interval: 1, 10, 60, 24, 7, 31, 4."""
        if interval not in ISS_INTERVALS:
            raise ValueError(f"ISS не поддерживает interval={interval}; используйте get_candles_minutes")
        params = {"interval": interval, "from": _fmt(start)}
        if end is not None:
            params["till"] = _fmt(end)
        out: list[Candle] = []
        offset = 0
        while True:
            rows = _rows(self._get(f"{self._sec_path}/candles", {**params, "start": offset}), "candles")
            if not rows:
                break
            out.extend(_candle(r) for r in rows)
            offset += len(rows)
            if len(rows) < ISS_PAGE:
                break
        return out

    def get_candles_minutes(self, minutes: int, start: date | datetime, end: date | datetime | None = None) -> list[Candle]:
        """Свечи произвольного минутного таймфрейма (например, 15), собранные из 1-минутных."""
        if minutes in (1, 10, 60):
            return self.get_candles(minutes, start, end)
        if minutes < 60:
            return resample(self.get_candles(1, start, end), minutes)
        if minutes % 60 == 0:
            return resample(self.get_candles(60, start, end), minutes)
        raise ValueError(f"Неподдерживаемый таймфрейм: {minutes} мин")

    def recent(self, minutes: int, days: int) -> list[Candle]:
        start = datetime.now(MSK).date() - timedelta(days=days)
        return self.get_candles_minutes(minutes, start)


# --- утилиты -------------------------------------------------------------


def resample(candles: Iterable[Candle], minutes: int) -> list[Candle]:
    """Агрегирует свечи в бакеты по `minutes` минут, выровненные от полуночи."""
    out: list[Candle] = []
    cur: dict | None = None
    for c in candles:
        mins = c.begin.hour * 60 + c.begin.minute
        bucket = c.begin.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=mins - mins % minutes)
        if cur is None or cur["begin"] != bucket:
            if cur is not None:
                out.append(Candle(**cur))
            cur = dict(begin=bucket, open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume)
        else:
            cur["high"] = max(cur["high"], c.high)
            cur["low"] = min(cur["low"], c.low)
            cur["close"] = c.close
            cur["volume"] += c.volume
    if cur is not None:
        out.append(Candle(**cur))
    return out


def _rows(data: dict, block: str) -> list[dict]:
    b = data.get(block) or {}
    cols = b.get("columns", [])
    return [dict(zip(cols, r)) for r in b.get("data", [])]


def _first(m: dict, *keys: str):
    return next((m[k] for k in keys if m.get(k) is not None), None)


def _candle(r: dict) -> Candle:
    return Candle(
        begin=_parse_dt(r["begin"]),
        open=float(r["open"]),
        high=float(r["high"]),
        low=float(r["low"]),
        close=float(r["close"]),
        volume=float(r.get("volume") or 0),
    )


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MSK)


def _combine(d: str | None, t: str | None) -> datetime | None:
    if not d or not t:
        return None
    return _parse_dt(f"{d} {t}")


def _fmt(d: date | datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S") if isinstance(d, datetime) else d.isoformat()
