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


class TestMarketRows(unittest.TestCase):
    def _make(self):
        from core.screener import Screener
        cfg = {"alerts": {"enabled": False}, "scanner": {"market_rows_limit": 100}}
        s = Screener(cfg)
        return s

    def test_growth_sorted_max_first(self):
        s = self._make()
        s.scanner._tickers = {
            ("binance", "LOW"): {"growth24": 0.5, "count24": 100, "quote_vol24": 1e6},
            ("binance", "TOP"): {"growth24": 42.0, "count24": 200, "quote_vol24": 1e6},
            ("binance", "NEG"): {"growth24": -3.0, "count24": 300, "quote_vol24": 1e6},
            ("gate", "GTOP"): {"growth24": 15.0, "count24": None, "quote_vol24": 1e6},
        }
        rows = s.growth_rows(10)
        self.assertEqual(rows[0]["symbol"], "TOP")
        self.assertEqual(rows[1]["symbol"], "GTOP")
        self.assertEqual(rows[2]["symbol"], "LOW")
        self.assertEqual(rows[3]["symbol"], "NEG")

    def test_trades_binance_only_sorted(self):
        s = self._make()
        s.scanner._tickers = {
            ("binance", "A"): {"growth24": 1.0, "count24": 900000, "quote_vol24": 1e6},
            ("binance", "B"): {"growth24": 2.0, "count24": 1500000, "quote_vol24": 1e6},
            ("gate", "C"): {"growth24": 3.0, "count24": None, "quote_vol24": 1e6},
            ("mexc", "D"): {"growth24": 4.0, "count24": None, "quote_vol24": 1e6},
        }
        rows = s.trades_rows(10)
        self.assertEqual([r["symbol"] for r in rows], ["B", "A"])
        self.assertEqual(rows[0]["count24"], 1500000)

    def test_limit(self):
        s = self._make()
        s.scanner._tickers = {
            ("binance", "S%03d" % i): {"growth24": float(i), "count24": i, "quote_vol24": 1.0}
            for i in range(200)
        }
        self.assertEqual(len(s.growth_rows(50)), 50)
        self.assertEqual(len(s.trades_rows(30)), 30)

    def test_robot_scores_attached(self):
        from core.models import RobotSignal

        s = self._make()
        s.scanner._tickers = {
            ("binance", "R"): {"growth24": 5.0, "count24": 100, "quote_vol24": 1.0},
        }
        class FakeDet:
            def all_signals(self_inner):
                return [RobotSignal(
                    exchange="binance", symbol="R", tick_ms=500, prints=6, strength=1.0,
                    buy_volume=1.0, sell_volume=0.0, avg_size=0.2, first_ms=0, last_ms=0,
                    score=77, regularity=1.0, market_reaction="WITH", fingerprint="t500",
                )]
        s.detector = FakeDet()
        rows = s.growth_rows(10)
        self.assertEqual(rows[0]["score"], 77)


if __name__ == "__main__":
    unittest.main()
