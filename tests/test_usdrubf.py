import unittest
from datetime import datetime, timedelta

from usdrubf.analysis import check_abc, check_impulse, rsi, sma, snapshot, zigzag
from usdrubf.moex import MSK, Candle, MoexClient, _rows, resample
from usdrubf.report import merge_tail, render

T0 = datetime(2026, 10, 21, 10, 0, tzinfo=MSK)


def candle(i, o, h, l, c, v=1.0, step=1):
    return Candle(T0 + timedelta(minutes=i * step), o, h, l, c, v)


def path(prices, step=1):
    """Свечи, проходящие через заданные цены (по одной свече на отрезок)."""
    out = []
    for i, (a, b) in enumerate(zip(prices, prices[1:])):
        out.append(candle(i, a, max(a, b), min(a, b), b, step=step))
    return out


class ResampleTest(unittest.TestCase):
    def test_ohlcv_aggregation(self):
        m1 = [candle(i, 80 + i, 80.5 + i, 79.5 + i, 80.2 + i, v=10) for i in range(30)]
        m15 = resample(m1, 15)
        self.assertEqual(len(m15), 2)
        first = m15[0]
        self.assertEqual(first.begin, T0)
        self.assertEqual(first.open, 80)
        self.assertEqual(first.close, 80.2 + 14)
        self.assertEqual(first.high, 80.5 + 14)
        self.assertEqual(first.low, 79.5)
        self.assertEqual(first.volume, 150)
        self.assertEqual(m15[1].begin, T0 + timedelta(minutes=15))

    def test_bucket_alignment_with_gaps(self):
        m1 = [candle(7, 1, 1, 1, 1), candle(16, 2, 2, 2, 2)]
        self.assertEqual([c.begin.minute for c in resample(m1, 15)], [0, 15])

    def test_merge_tail(self):
        h = [candle(0, 1, 1, 1, 1, step=60), candle(1, 2, 2, 2, 2, step=60)]
        fresh = [candle(1, 9, 9, 9, 9, step=60), candle(2, 3, 3, 3, 3, step=60)]
        self.assertEqual([c.close for c in merge_tail(h, fresh)], [1, 2, 3])


class IndicatorTest(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(sma([1, 2, 3, 4], 2), 3.5)
        self.assertIsNone(sma([1], 2))

    def test_rsi_extremes(self):
        self.assertEqual(rsi([float(i) for i in range(30)]), 100.0)
        self.assertLess(rsi([float(30 - i) for i in range(30)]), 1)
        self.assertIsNone(rsi([1.0] * 5))


class ZigzagTest(unittest.TestCase):
    def test_pivots_alternate_and_match(self):
        prices = [80, 81, 80.5, 82, 81.5, 83]
        piv = zigzag(path(prices), threshold=0.4)
        self.assertEqual([p.price for p in piv], prices)
        self.assertEqual([p.kind for p in piv], ["L", "H", "L", "H", "L", "H"])

    def test_small_moves_ignored(self):
        prices = [80, 81, 80.9, 82]
        piv = zigzag(path(prices), threshold=0.4)
        self.assertEqual([p.price for p in piv], [80, 82])


class ElliottTest(unittest.TestCase):
    def test_valid_impulse_up(self):
        w = check_impulse([80, 81, 80.5, 82.6, 82.0, 83.0])
        self.assertTrue(w.valid)
        self.assertEqual(w.direction, "вверх")
        self.assertEqual(w.ratios["w3/w1"], 2.1)
        self.assertEqual(w.invalidation, 81)

    def test_wave2_beyond_start(self):
        w = check_impulse([80, 81, 79.9, 82.6, 82.0, 83.0])
        self.assertFalse(w.valid)

    def test_wave3_shortest(self):
        w = check_impulse([80, 81, 80.5, 81.2, 81.1, 82.5])
        self.assertFalse(w.valid)

    def test_wave4_overlap(self):
        w = check_impulse([80, 81, 80.5, 82.6, 80.9, 83.0])
        self.assertFalse(w.valid)
        self.assertTrue(any("диагонал" in n for n in w.notes))

    def test_impulse_down_mirror(self):
        w = check_impulse([83, 82, 82.5, 80.4, 81.0, 80.0])
        self.assertTrue(w.valid)
        self.assertEqual(w.direction, "вниз")

    def test_abc_down(self):
        w = check_abc([82.29, 81.74, 81.94, 81.61])
        self.assertTrue(w.valid)
        self.assertTrue(all(ok for _, ok in w.rules))
        self.assertEqual(w.ratios["C/A"], 0.6)


class ReportTest(unittest.TestCase):
    def test_render_offline(self):
        from usdrubf.moex import Quote

        prices = [80, 81, 80.5, 82.6, 82.0, 83.0, 82.2]
        h1 = snapshot("1ч", path(prices, step=60))
        d1 = snapshot("1д", path(prices, step=1440))
        q = Quote("USDRUBF", "RFUD", 82.2, 82.19, 82.21, 80, 83, 80, 82.0, 0.2, 1, 2,
                  T0, T0, "2026-10-21")
        text = render(q, h1, d1, now=T0 + timedelta(hours=2))
        self.assertIn("MOEX ISS", text)
        self.assertIn("НЕ является текущей", text)
        self.assertIn("Таймфрейм 1ч", text)
        self.assertIn("Таймфрейм 1д", text)

    def test_render_uses_secid_and_decimals(self):
        from usdrubf.moex import Quote

        prices = [12.0, 12.1, 12.05, 12.2, 12.15, 12.3, 12.25]
        h1 = snapshot("1ч", path(prices, step=60))
        d1 = snapshot("1д", path(prices, step=1440))
        q = Quote("CNYRUB_TOM", "CETS", 12.09, None, None, 12.041, 12.1605, 12.0305, None, 0.22, None, None,
                  T0, T0, "2026-10-21", engine="currency", market="selt", decimals=4)
        text = render(q, h1, d1, now=T0)
        self.assertIn("# CNYRUB_TOM", text)
        self.assertIn("**12.0900**", text)
        self.assertIn("рынок currency/selt", text)
        self.assertNotIn("USDRUBF", text)
        self.assertNotIn("Расчётная цена", text)  # только для фьючерсов


class ChartTest(unittest.TestCase):
    def test_svg_has_waves_and_levels(self):
        from usdrubf.chart import ChartLevel, render_svg

        s = snapshot("15м", path([80, 81, 80.5, 82.6, 82.0, 83.0, 82.2], step=15), zigzag_atr=0.3)
        svg = render_svg(s, waves=["0", "A", "B", "C"], levels=[ChartLevel(82.5, "стоп 82.50", "#555555")])
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("стоп 82.50", svg)
        for label in ("A", "B", "C"):
            self.assertIn(f">{label}</text>", svg)

    def test_daily_axis_shows_dates(self):
        from usdrubf.chart import render_svg

        svg = render_svg(snapshot("1д", path([80, 81, 80.5, 82.6, 82.0, 83.0, 82.2], step=1440)))
        self.assertIn(">21.10.26</text>", svg)
        self.assertNotIn("00:00</text>", svg)

    def test_cli_level_parsing(self):
        from usdrubf.__main__ import parse_level, parse_waves

        lv = parse_level("80.90:вход 80.90:green")
        self.assertEqual((lv.price, lv.label, lv.color), (80.90, "вход 80.90", "#1e9e6a"))
        self.assertEqual(parse_waves("60=0, A,B,C"), (60, ["0", "A", "B", "C"]))


class ParseTest(unittest.TestCase):
    def test_rows(self):
        data = {"marketdata": {"columns": ["SECID", "LAST"], "data": [["USDRUBF", 81.68]]}}
        self.assertEqual(_rows(data, "marketdata"), [{"SECID": "USDRUBF", "LAST": 81.68}])

    def test_unsupported_interval(self):
        with self.assertRaises(ValueError):
            MoexClient().get_candles(15, T0.date())


def table(cols, *rows):
    return {"columns": cols, "data": [list(r) for r in rows]}


BOARD_COLS = ["boardid", "market", "engine", "is_primary", "decimals", "is_traded"]


class FakeClient(MoexClient):
    """MoexClient с подменённым транспортом: ответы ISS по префиксу пути."""

    def __init__(self, responses, *args, **kw):
        super().__init__(*args, **kw)
        self.responses, self.calls = responses, []

    def _get(self, path, params=None):
        self.calls.append(path)
        return next(v for k, v in self.responses.items() if path.startswith(k))


class ResolveTest(unittest.TestCase):
    BOARDS = {"securities/SBER": {"boards": table(
        BOARD_COLS, ["EQBR", "shares", "stock", 0, 2, 0], ["TQBR", "shares", "stock", 1, 2, 1],
        ["RPEY", "repo", "stock", 0, 4, 1])}}

    def test_primary_board(self):
        c = FakeClient(self.BOARDS, "sber")
        self.assertEqual(c.secid, "SBER")
        self.assertEqual(c._sec_path, "engines/stock/markets/shares/boards/TQBR/securities/SBER")
        c._sec_path
        self.assertEqual(c.calls, ["securities/SBER"])  # режим определяется один раз

    def test_explicit_market(self):
        c = FakeClient(self.BOARDS, "SBER", market="repo")
        self.assertEqual(c.resolve().board, "RPEY")
        self.assertEqual(c.resolve().decimals, 4)

    def test_unknown_secid(self):
        from usdrubf.moex import MoexError

        c = FakeClient({"securities/": {"boards": table(BOARD_COLS)}}, "NOPE")
        with self.assertRaises(MoexError):
            c.get_quote()

    def test_index_quote(self):
        md = table(["SECID", "BOARDID", "LAST", "CURRENTVALUE", "LASTCHANGEPRC", "UPDATETIME", "SYSTIME", "TRADEDATE"],
                   ["IMOEX", "SNDX", None, 2491.04, 1.07, "19:08:53", "2026-10-26 19:08:53", None])
        c = FakeClient({
            "securities/IMOEX": {"boards": table(BOARD_COLS, ["SNDX", "index", "stock", 1, 2, 1])},
            "engines/stock/markets/index/boards/SNDX/securities/IMOEX": {"marketdata": md},
        }, "IMOEX")
        q = c.get_quote()
        self.assertEqual((q.last, q.change_pct, q.trade_date), (2491.04, 1.07, "2026-10-26"))
        self.assertEqual((q.engine, q.market, q.decimals), ("stock", "index", 2))
        self.assertEqual(q.last_trade_time, datetime(2026, 10, 26, 19, 8, 53, tzinfo=MSK))


if __name__ == "__main__":
    unittest.main()
