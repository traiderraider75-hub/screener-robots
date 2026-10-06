import random
import statistics
import sys
import unittest

sys.path.insert(0, ".")

from core.detectors.robots import RobotDetector
from core.models import Trade

CFG = {
    "size_k": 3.0,
    "min_prints": 5,
    "tick_tolerance": 0.15,
    "min_regularity": 0.6,
    "tick_bucket_ms": 10,
}


def make_detector():
    fired = []
    det = RobotDetector(window_s=600, cfg=CFG, alert_cb=lambda s: fired.append(s))
    return det, fired


class TestRobotDetector(unittest.TestCase):
    def test_metronome_detected(self):
        det, fired = make_detector()
        t = 1_000_000
        price = 100.0
        rnd = random.Random(1)
        for i in range(60):
            t += 500
            price += 0.02
            det.on_trade(Trade.of("ex", "SYM", t, price, 5.0, True))
            if rnd.random() < 0.7:
                det.on_trade(Trade.of("ex", "SYM", t + 120, price, 0.1, True))
        self.assertTrue(any(s.tick_ms == 500 for s in det.all_signals()))
        self.assertTrue(fired, "activation alert must fire once")
        sig = det.all_signals()[0]
        self.assertEqual(sig.direction, "LONG")
        self.assertGreaterEqual(sig.prints, 5)
        self.assertEqual(sig.tick_ms, 500)
        self.assertEqual(sig.market_reaction, "WITH")

    def test_short_robot(self):
        det, _ = make_detector()
        t = 2_000_000
        price = 50.0
        rnd = random.Random(2)
        for i in range(40):
            t += 250
            price -= 0.01
            det.on_trade(Trade.of("ex", "SYM", t, price, 2.0, False))
            if rnd.random() < 0.7:
                det.on_trade(Trade.of("ex", "SYM", t + 60, price, 0.2, False))
        sig = det.all_signals()[0]
        self.assertEqual(sig.direction, "SHORT")
        self.assertEqual(sig.tick_ms, 250)
        self.assertEqual(sig.market_reaction, "WITH")

    def test_random_no_false_positive(self):
        det, fired = make_detector()
        rnd = random.Random(42)
        t = 3_000_000
        for i in range(3000):
            t += rnd.randint(20, 900)
            size = rnd.expovariate(1 / 0.1)
            if rnd.random() < 0.03:
                size *= 4.0
            det.on_trade(Trade.of("ex", "SYM", t, 100.0, size, rnd.random() < 0.5))
        self.assertEqual(det.all_signals(), [])
        self.assertEqual(fired, [])

    def test_human_noise_not_detected(self):
        det, _ = make_detector()
        t = 4_000_000
        sizes = [0.1] * 100
        for i in range(100):
            t += 1500 + random.randint(-400, 400)
            det.on_trade(Trade.of("ex", "SYM", t, 100.0, 1.5, True))
        self.assertEqual(det.all_signals(), [])

    def test_trade_tagged_as_robot(self):
        det, _ = make_detector()
        t = 5_000_000
        trades = []
        for i in range(60):
            t += 1000
            trades.append(Trade.of("ex", "SYM", t, 100.0, 0.1, True))
            det.on_trade(trades[-1])
            t += 500
            trades.append(Trade.of("ex", "SYM", t, 100.0, 5.0, True))
            det.on_trade(trades[-1])
        tagged = [tr for tr in trades if tr.is_robot]
        self.assertGreaterEqual(len(tagged), 5)


if __name__ == "__main__":
    unittest.main()
