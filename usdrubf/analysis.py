"""Индикаторы, экстремумы (зигзаг), уровни и проверка правил Эллиотта.

Модуль ничего не «угадывает»: он считает фактические значения по свечам и
проверяет обязательные правила для кандидатов разметки. Решение о сделке
принимается поверх этих данных.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .moex import Candle


# --- индикаторы ------------------------------------------------------------


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI по Уайлдеру."""
    if len(closes) <= period:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


def atr(candles: list[Candle], period: int = 14) -> float | None:
    if len(candles) <= period:
        return None
    trs = [
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        for p, c in zip(candles, candles[1:])
    ]
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


# --- экстремумы ------------------------------------------------------------


@dataclass(frozen=True)
class Pivot:
    time: datetime
    price: float
    kind: str  # "H" — максимум, "L" — минимум


def zigzag(candles: list[Candle], threshold: float) -> list[Pivot]:
    """Чередующиеся экстремумы: разворот фиксируется при ходе цены на `threshold` от экстремума.

    Последняя точка списка — текущий, ещё не подтверждённый экстремум.
    """
    if not candles:
        return []
    pivots: list[Pivot] = []
    trend = 0  # 1 — растём (ищем максимум), -1 — падаем (ищем минимум)
    hi = lo = ext = candles[0]
    for c in candles[1:]:
        if trend == 0:
            hi = c if c.high > hi.high else hi
            lo = c if c.low < lo.low else lo
            if hi.high - lo.low >= threshold:
                if hi.begin < lo.begin:
                    pivots.append(Pivot(hi.begin, hi.high, "H"))
                    trend, ext = -1, lo
                else:
                    pivots.append(Pivot(lo.begin, lo.low, "L"))
                    trend, ext = 1, hi
            if trend == 0:
                continue
            # направление определилось — та же свеча обрабатывается уже в тренде
        if trend == 1:
            if c.high > ext.high:
                ext = c
            elif ext.high - c.low >= threshold:
                pivots.append(Pivot(ext.begin, ext.high, "H"))
                trend, ext = -1, c
        else:
            if c.low < ext.low:
                ext = c
            elif c.high - ext.low >= threshold:
                pivots.append(Pivot(ext.begin, ext.low, "L"))
                trend, ext = 1, c
    if trend == 1:
        pivots.append(Pivot(ext.begin, ext.high, "H"))
    elif trend == -1:
        pivots.append(Pivot(ext.begin, ext.low, "L"))
    return _dedupe(pivots)


def _dedupe(pivots: list[Pivot]) -> list[Pivot]:
    out: list[Pivot] = []
    for p in pivots:
        if out and out[-1].kind == p.kind:
            better = p.price > out[-1].price if p.kind == "H" else p.price < out[-1].price
            if better:
                out[-1] = p
            continue
        out.append(p)
    return out


# --- уровни ----------------------------------------------------------------


@dataclass
class Level:
    price: float
    touches: int
    kind: str  # "сопротивление" / "поддержка"


def levels(pivots: list[Pivot], price: float, tolerance: float, limit: int = 3) -> tuple[list[Level], list[Level]]:
    """Кластеризует экстремумы в уровни. Возвращает (сопротивления выше цены, поддержки ниже)."""
    clusters: list[list[float]] = []
    for p in sorted(pivots, key=lambda x: x.price):
        if clusters and p.price - clusters[-1][-1] <= tolerance:
            clusters[-1].append(p.price)
        else:
            clusters.append([p.price])
    lv = [(sum(c) / len(c), len(c)) for c in clusters]
    res = sorted((Level(round(p, 2), n, "сопротивление") for p, n in lv if p > price), key=lambda l: l.price)
    sup = sorted((Level(round(p, 2), n, "поддержка") for p, n in lv if p < price), key=lambda l: -l.price)
    return res[:limit], sup[:limit]


# --- Эллиотт ---------------------------------------------------------------


@dataclass
class WaveCheck:
    pattern: str  # "импульс 1-2-3-4-5" / "коррекция A-B-C"
    direction: str  # "вверх" / "вниз"
    points: list[float]
    valid: bool
    rules: list[tuple[str, bool]] = field(default_factory=list)
    ratios: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    invalidation: float | None = None


def check_impulse(points: list[float]) -> WaveCheck:
    """points = [p0, p1, p2, p3, p4, p5] — начало волны 1 и концы волн 1..5."""
    if len(points) != 6:
        raise ValueError("Для импульса нужны 6 точек")
    up = points[1] > points[0]
    s = 1 if up else -1
    p = points
    w = [abs(p[i + 1] - p[i]) for i in range(5)]
    rules = [
        ("Волна 2 не заходит за начало волны 1", s * (p[2] - p[0]) > 0),
        ("Волна 3 не самая короткая из 1, 3, 5", not (w[2] < w[0] and w[2] < w[4])),
        ("Волна 4 не заходит в область волны 1", s * (p[4] - p[1]) > 0),
        ("Волна 3 выходит за конец волны 1", s * (p[3] - p[1]) > 0),
    ]
    notes = []
    if not rules[2][1] and rules[0][1] and rules[1][1]:
        notes.append("Перекрытие 1 и 4 допустимо только для диагонали (клина)")
    if s * (p[5] - p[3]) <= 0:
        notes.append("Волна 5 не обновила экстремум волны 3 — усечение или разметка неверна")
    ratios = {
        "w2/w1": _r(w[1], w[0]),
        "w3/w1": _r(w[2], w[0]),
        "w4/w3": _r(w[3], w[2]),
        "w5/w1": _r(w[4], w[0]),
        "w5/w3": _r(w[4], w[2]),
    }
    return WaveCheck(
        pattern="импульс 1-2-3-4-5",
        direction="вверх" if up else "вниз",
        points=list(points),
        valid=all(ok for _, ok in rules[:3]),
        rules=rules,
        ratios=ratios,
        notes=notes + [
            f"Конец волны 4 ({p[4]:.2f}): возврат за него после завершения волны 5 — признак окончания импульса"
        ],
        # пока волна 5 не завершена, заход в область волны 1 ломает разметку
        invalidation=p[1] if all(ok for _, ok in rules[:3]) else None,
    )


def check_abc(points: list[float]) -> WaveCheck:
    """points = [p0, pA, pB, pC] — начало коррекции и концы волн A, B, C."""
    if len(points) != 4:
        raise ValueError("Для ABC нужны 4 точки")
    p = points
    down = p[1] < p[0]
    s = -1 if down else 1
    a, b, c = abs(p[1] - p[0]), abs(p[2] - p[1]), abs(p[3] - p[2])
    rules = [
        ("Волна B не заходит за начало A (иначе — плоская/расширенная)", s * (p[2] - p[0]) > 0),
        ("Волна C выходит за конец A", s * (p[3] - p[1]) > 0),
    ]
    notes = []
    if not rules[0][1]:
        notes.append("B за началом A — возможна расширенная плоская коррекция")
    return WaveCheck(
        pattern="коррекция A-B-C",
        direction="вниз" if down else "вверх",
        points=list(points),
        valid=rules[1][1],
        rules=rules,
        ratios={"B/A": _r(b, a), "C/A": _r(c, a)},
        notes=notes,
        invalidation=p[0],
    )


def wave_candidates(pivots: list[Pivot]) -> list[WaveCheck]:
    """Кандидаты разметки по последним экстремумам зигзага.

    Последний экстремум не подтверждён, поэтому последняя волна каждого
    кандидата может быть ещё не завершена.
    """
    prices = [p.price for p in pivots]
    out: list[WaveCheck] = []
    if len(prices) >= 6:
        out.append(check_impulse(prices[-6:]))
    if len(prices) >= 4:
        out.append(check_abc(prices[-4:]))
    for w in out:
        w.notes.append("Последняя точка — неподтверждённый экстремум, волна может продолжиться")
    return out


def _r(a: float, b: float) -> float:
    return round(a / b, 3) if b else float("nan")


# --- снимок таймфрейма -----------------------------------------------------


@dataclass
class Snapshot:
    timeframe: str
    candles: list[Candle]
    close: float
    sma50: float | None
    sma200: float | None
    rsi14: float | None
    atr14: float | None
    pivots: list[Pivot]
    resistances: list[Level]
    supports: list[Level]
    waves: list[WaveCheck]


def snapshot(timeframe: str, candles: list[Candle], zigzag_atr: float = 2.0) -> Snapshot:
    if not candles:
        raise ValueError(f"Нет свечей для {timeframe}")
    closes = [c.close for c in candles]
    a = atr(candles) or (max(c.high for c in candles) - min(c.low for c in candles)) / 10
    piv = zigzag(candles, a * zigzag_atr)
    res, sup = levels(piv, closes[-1], tolerance=a * 0.5)
    return Snapshot(
        timeframe=timeframe,
        candles=candles,
        close=closes[-1],
        sma50=sma(closes, 50),
        sma200=sma(closes, 200),
        rsi14=rsi(closes),
        atr14=atr(candles),
        pivots=piv,
        resistances=res,
        supports=sup,
        waves=wave_candidates(piv),
    )
