"""SVG-график свечей с волновой разметкой, MA и уровнями (только стандартная библиотека)."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from .analysis import Snapshot, WaveCheck, sma
from .report import time_format

W, H = 960, 540
PAD_L, PAD_R, PAD_T, PAD_B = 12, 170, 44, 34
LABEL_X = 62  # отступ подписей уровней от правого края области графика

UP, DOWN = "#1e9e6a", "#d64545"
MA50_COLOR, MA200_COLOR = "#e08a00", "#7b4fc9"
RES_COLOR, SUP_COLOR, INV_COLOR = "#d64545", "#1e9e6a", "#555555"
WAVE_COLOR = "#1f5fbf"


@dataclass
class ChartLevel:
    price: float
    label: str
    color: str = "#1f5fbf"
    dash: str = "6 4"


def auto_labels(w: WaveCheck) -> list[str]:
    return ["0", "1", "2", "3", "4", "5"] if w.pattern.startswith("импульс") else ["0", "A", "B", "C"]


def default_levels(s: Snapshot, decimals: int = 2) -> list[ChartLevel]:
    out = [ChartLevel(lv.price, f"сопр. {lv.price:.{decimals}f}", RES_COLOR) for lv in s.resistances]
    out += [ChartLevel(lv.price, f"подд. {lv.price:.{decimals}f}", SUP_COLOR) for lv in s.supports]
    valid = next((w for w in s.waves if w.valid), None)
    if valid and valid.invalidation is not None:
        out.append(ChartLevel(valid.invalidation, f"отмена {valid.invalidation:.{decimals}f}", INV_COLOR, "2 3"))
    return out


def render_svg(
    s: Snapshot,
    last: int = 80,
    levels: list[ChartLevel] | None = None,
    waves: list[str] | None = None,
    title: str = "",
    decimals: int = 2,
) -> str:
    """Свечи последних `last` баров, MA50/MA200, зигзаг с подписями волн и горизонтальные уровни.

    `waves` — подписи для последних len(waves) экстремумов зигзага (например, ["0", "A", "B", "C"]).
    Без него подписывается первый кандидат разметки, прошедший проверку правил.
    `levels` — уровни на графике; без него — уровни и отмена разметки из снимка.
    """
    candles = s.candles[-last:]
    offset = len(s.candles) - len(candles)
    closes = [c.close for c in s.candles]
    if levels is None:
        levels = default_levels(s, decimals)
    if waves is None:
        valid = next((w for w in s.waves if w.valid), None)
        waves = auto_labels(valid) if valid else []
    tf = time_format(s)

    lo = min(c.low for c in candles)
    hi = max(c.high for c in candles)
    # уровни рядом с ценой расширяют шкалу, далёкие (больше диапазона свечей) не показываем
    span = hi - lo or 1.0
    levels = [lv for lv in levels if lo - span <= lv.price <= hi + span]
    lo = min([lo] + [lv.price for lv in levels])
    hi = max([hi] + [lv.price for lv in levels])
    margin = (hi - lo) * 0.06 or 0.1
    lo, hi = lo - margin, hi + margin

    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    step = pw / len(candles)

    def x(i: int) -> float:
        return PAD_L + step * (i + 0.5)

    def y(p: float) -> float:
        return PAD_T + (hi - p) / (hi - lo) * ph

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        'font-family="DejaVu Sans, Arial, sans-serif" font-size="12">',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        f'<defs><clipPath id="plot"><rect x="{PAD_L}" y="{PAD_T}" width="{pw}" height="{ph}"/></clipPath></defs>',
        f'<rect x="{PAD_L}" y="{PAD_T}" width="{pw}" height="{ph}" fill="none" stroke="#cccccc"/>',
    ]

    # сетка цены
    for k in range(7):
        p = lo + (hi - lo) * k / 6
        out.append(f'<line x1="{PAD_L}" x2="{PAD_L + pw}" y1="{y(p):.1f}" y2="{y(p):.1f}" stroke="#eeeeee"/>')
        out.append(f'<text x="{PAD_L + pw + 6}" y="{y(p) + 4:.1f}" fill="#888888">{p:.{decimals}f}</text>')

    # подписи времени
    every = max(1, len(candles) // 8)
    for i in range(every // 2, len(candles), every):
        out.append(
            f'<text x="{x(i):.1f}" y="{H - 12}" fill="#888888" text-anchor="middle">{candles[i].begin:{tf}}</text>'
        )

    out.append('<g clip-path="url(#plot)">')

    # уровни
    for lv in levels:
        yy = y(lv.price)
        out.append(
            f'<line x1="{PAD_L}" x2="{PAD_L + pw}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{lv.color}" '
            f'stroke-width="1.3" stroke-dasharray="{lv.dash}"/>'
        )

    # свечи
    body = max(1.0, step * 0.6)
    for i, c in enumerate(candles):
        color = UP if c.close >= c.open else DOWN
        top, bot = y(max(c.open, c.close)), y(min(c.open, c.close))
        out.append(f'<line x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{y(c.high):.1f}" y2="{y(c.low):.1f}" stroke="{color}"/>')
        out.append(
            f'<rect x="{x(i) - body / 2:.1f}" y="{top:.1f}" width="{body:.1f}" height="{max(1.0, bot - top):.1f}" fill="{color}"/>'
        )

    # скользящие средние
    for period, color in ((50, MA50_COLOR), (200, MA200_COLOR)):
        pts = []
        for i in range(len(candles)):
            v = sma(closes[: offset + i + 1], period)
            if v is not None:
                pts.append(f"{x(i):.1f},{y(v):.1f}")
        if pts:
            out.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1.5"/>')

    # зигзаг и подписи волн
    index = {c.begin: i for i, c in enumerate(candles)}
    piv = [(index[p.time], p) for p in s.pivots if p.time in index]
    if len(piv) >= 2:
        pts = " ".join(f"{x(i):.1f},{y(p.price):.1f}" for i, p in piv)
        out.append(f'<polyline points="{pts}" fill="none" stroke="{WAVE_COLOR}" stroke-width="1.6" opacity="0.8"/>')
    # подписи выравниваются по последнему экстремуму; лишние подписи слева отбрасываются
    k = min(len(piv), len(waves))
    for (i, p), label in zip(piv[len(piv) - k:], waves[len(waves) - k:]):
        dy = -10 if p.kind == "H" else 20
        out.append(f'<circle cx="{x(i):.1f}" cy="{y(p.price):.1f}" r="3.5" fill="{WAVE_COLOR}"/>')
        out.append(
            f'<text x="{x(i):.1f}" y="{y(p.price) + dy:.1f}" fill="{WAVE_COLOR}" font-size="15" font-weight="bold" '
            f'text-anchor="middle" stroke="#ffffff" stroke-width="3" paint-order="stroke">{escape(label)}</text>'
        )
    out.append("</g>")

    # подписи уровней справа, раздвинутые, чтобы не налезали друг на друга
    prev = -1e9
    for lv in sorted(levels, key=lambda lv: -lv.price):
        ty = max(y(lv.price) + 4, prev + 14)
        prev = ty
        out.append(
            f'<text x="{PAD_L + pw + LABEL_X}" y="{ty:.1f}" fill="{lv.color}" font-weight="bold">{escape(lv.label)}</text>'
        )

    # текущая цена
    last_close = candles[-1].close
    price = f"{last_close:.{decimals}f}"
    out.append(
        f'<rect x="{PAD_L + pw + 1}" y="{y(last_close) - 9:.1f}" width="{8 + 7 * len(price)}" height="18" fill="#222222"/>'
        f'<text x="{PAD_L + pw + 4}" y="{y(last_close) + 4:.1f}" fill="#ffffff">{price}</text>'
    )

    # заголовок и легенда
    head = title or f"{s.timeframe} · MOEX ISS"
    out.append(f'<text x="{PAD_L}" y="18" font-size="15" font-weight="bold" fill="#222222">{escape(head)}</text>')
    legend = [(MA50_COLOR, "MA50"), (MA200_COLOR, "MA200"), (WAVE_COLOR, "зигзаг / волны")]
    lx = PAD_L
    for color, name in legend:
        out.append(f'<line x1="{lx}" x2="{lx + 18}" y1="34" y2="34" stroke="{color}" stroke-width="2"/>')
        out.append(f'<text x="{lx + 22}" y="38" fill="#444444">{name}</text>')
        lx += 40 + 8 * len(name)
    out.append("</svg>")
    return "\n".join(out)
