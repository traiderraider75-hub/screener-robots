import sys
import unittest

sys.path.insert(0, ".")

from core.radar import Radar
from core.scanner import Scanner


def make_scanner(tickers):
    radar = Radar()
    sc = Scanner(radar, {"poll_s": 30, "top_gainers_n": 3, "min_trades_24h": 5000, "rvol_threshold": 5.0})
    sc._tickers = tickers
    sc._recompute_gainers()
    return sc, radar


class TestScanner(unittest.TestCase):
    def test_gainer_rank(self):
        sc, _ = make_scanner({
            ("binance", "AAA"): {"growth24": 10.0, "count24": 999999, "quote_vol24": 1e9},
            ("binance", "BBB"): {"growth24": 5.0, "count24": 999999, "quote_vol24": 1e9},
            ("binance", "CCC"): {"growth24": 50.0, "count24": 100, "quote_vol24": 1e9},
            ("binance", "DDD"): {"growth24": -1.0, "count24": 999999, "quote_vol24": 1e9},
        })
        self.assertIn("AAA", sc._gainers["binance"])
        self.assertIn("BBB", sc._gainers["binance"])
        self.assertIn("CCC", sc._gainers["binance"])
        self.assertNotIn("DDD", sc._gainers["binance"])  # отрицательный рост

    def test_matches_binance_full_criteria(self):
        sc, radar = make_scanner({
            ("binance", "AAA"): {"growth24": 10.0, "count24": 999999, "quote_vol24": 1e9},
            ("binance", "BBB"): {"growth24": 10.0, "count24": 10, "quote_vol24": 1e9},
        })
        cell = radar.cell_or_new("binance", "AAA")
        cell.seed_hours([100.0] * 24, current_vol=600.0, now_ms=1791000000000)
        cell.on_trade(1791000500000, 5.0)
        self.assertTrue(sc.matches("binance", "AAA"))  # rvol 6x, топ роста, сделки ок
        self.assertFalse(sc.matches("binance", "BBB"))  # сделок 10 < 5000

    def test_matches_other_exchanges_no_count(self):
        sc, radar = make_scanner({
            ("gate", "PUMP"): {"growth24": 30.0, "count24": None, "quote_vol24": 1e8},
        })
        cell = radar.cell_or_new("gate", "PUMP")
        cell.seed_hours([50.0] * 24, current_vol=500.0, now_ms=1791000000000)
        self.assertTrue(sc.matches("gate", "PUMP"))
        self.assertIsNone(sc.count24("gate", "PUMP"))

    def test_rvol_below_threshold(self):
        sc, radar = make_scanner({
            ("binance", "AAA"): {"growth24": 10.0, "count24": 999999, "quote_vol24": 1e9},
        })
        cell = radar.cell_or_new("binance", "AAA")
        cell.seed_hours([100.0] * 24, current_vol=200.0, now_ms=1791000000000)
        self.assertFalse(sc.matches("binance", "AAA"))  # rvol 2x < 5

    def test_binance_priority_in_candidates(self):
        sc, radar = make_scanner({
            ("gate", "ZCOIN"): {"growth24": 20.0, "count24": None, "quote_vol24": 1e8},
            ("binance", "ACOIN"): {"growth24": 15.0, "count24": 999999, "quote_vol24": 1e9},
        })
        for key in (("gate", "ZCOIN"), ("binance", "ACOIN")):
            radar.cell_or_new(*key).seed_hours([10.0] * 24, current_vol=100.0, now_ms=1791000000000)
        cands = sc.candidates()
        self.assertEqual(cands[0], ("binance", "ACOIN"))  # приоритет Binance


class TestRadarHours(unittest.TestCase):
    def test_hourly_accumulation(self):
        r = Radar()
        base = 1791000000000
        base -= base % 3600000
        r.on_trade("ex", "S", base + 1000, 10.0)
        r.on_trade("ex", "S", base + 2000, 5.0)
        r.on_trade("ex", "S", base + 3600000 + 1000, 30.0)  # следующий час
        cell = r.cell("ex", "S")
        self.assertEqual(list(cell.hours), [15.0])
        self.assertEqual(cell.hour_vol, 30.0)

    def test_rvol_calc(self):
        r = Radar()
        base = 1791000000000
        base -= base % 3600000
        for h in range(10):
            r.on_trade("ex", "S", base + h * 3600000 + 1000, 100.0)
        cell = r.cell("ex", "S")
        cell.hours.clear()
        for v in [50.0] * 24:
            cell.hours.append(v)
        cell.hour_vol = 500.0
        self.assertAlmostEqual(cell.rvol(), 10.0)


if __name__ == "__main__":
    unittest.main()
