import sys
import unittest

sys.path.insert(0, ".")

from core.detectors.flicker import FlickerDetector
from core.detectors.icebergs import IcebergDetector
from core.detectors.walls import WallsDetector
from core.models import BookSnapshot, Trade

MIN_SCORE = 25


def book(ts, bids, asks):
    return BookSnapshot(ts_ms=ts, bids=bids, asks=asks)


class TestFlicker(unittest.TestCase):
    def test_fast_requoting_detected(self):
        d = FlickerDetector("ex", "SYM")
        t = 10_000_000
        for i in range(30):
            size = 1.0 + (i % 2)
            d.on_book(book(t, [(100.0, size)], [(100.1, 1.0)]))
            t += 100
        score, info = d.state()
        self.assertGreaterEqual(score, MIN_SCORE)
        self.assertIn("обн/с", info)

    def test_calm_book_zero(self):
        d = FlickerDetector("ex", "SYM")
        t = 10_000_000
        for i in range(30):
            d.on_book(book(t, [(100.0, 5.0)], [(100.1, 5.0)]))
            t += 100
        score, _ = d.state()
        self.assertEqual(score, 0)


class TestWalls(unittest.TestCase):
    def test_wall_vanish_on_approach(self):
        d = WallsDetector("ex", "SYM")
        t = 10_000_000
        d.on_book(book(t, [(99.9, 1.0)], [(100.2, 1.0), (100.5, 50.0)]))
        t += 1000
        d.on_book(book(t, [(99.9, 1.0)], [(100.2, 1.0), (100.5, 50.0)]))
        t += 1000
        d.on_book(book(t, [(100.0, 1.0)], [(100.2, 1.0), (100.5, 50.0)]))
        t += 1000
        d.on_book(book(t, [(100.1, 1.0)], [(100.2, 1.0)]))
        score, info = d.state()
        self.assertGreater(score, 0)
        self.assertIn("ask", info)

    def test_wall_vanish_with_trade_through_no_alert(self):
        d = WallsDetector("ex", "SYM")
        t = 10_000_000
        d.on_book(book(t, [(99.9, 1.0)], [(100.2, 1.0), (100.5, 50.0)]))
        t += 1000
        d.on_book(book(t, [(100.0, 1.0)], [(100.2, 1.0), (100.5, 50.0)]))
        d.on_trade(Trade.of("ex", "SYM", t, 100.5, 50.0, True))
        t += 1000
        d.on_book(book(t, [(100.1, 1.0)], [(100.2, 1.0)]))
        score, _ = d.state()
        self.assertEqual(score, 0)

    def test_stable_wall_no_alert(self):
        d = WallsDetector("ex", "SYM")
        t = 10_000_000
        for i in range(20):
            d.on_book(book(t, [(99.9, 1.0)], [(100.2, 1.0), (100.5, 50.0)]))
            t += 1000
        score, _ = d.state()
        self.assertEqual(score, 0)


class TestIceberg(unittest.TestCase):
    def test_hidden_refill_detected(self):
        d = IcebergDetector("ex", "SYM")
        t = 10_000_000
        d.on_book(book(t, [(99.9, 1.0)], [(100.1, 0.5)]))
        for i in range(6):
            t += 500
            d.on_trade(Trade.of("ex", "SYM", t, 100.1, 2.0, True))
            d.on_book(book(t + 1, [(99.9, 1.0)], [(100.1, 0.5)]))
        score, info = d.state()
        self.assertGreaterEqual(score, MIN_SCORE)
        self.assertIn("айсберг", info)
        self.assertIn("100.1", info)

    def test_normal_trades_no_alert(self):
        d = IcebergDetector("ex", "SYM")
        t = 10_000_000
        d.on_book(book(t, [(99.9, 1.0)], [(100.1, 10.0)]))
        for i in range(6):
            t += 500
            d.on_trade(Trade.of("ex", "SYM", t, 100.1, 0.5, True))
            d.on_book(book(t + 1, [(99.9, 1.0)], [(100.1, 10.0)]))
        score, _ = d.state()
        self.assertEqual(score, 0)


if __name__ == "__main__":
    unittest.main()
