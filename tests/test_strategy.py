import sys
import unittest

sys.path.insert(0, ".")

from core.detectors.levels import LevelsDetector
from core.detectors.mm import MMDetector
from core.models import BookSnapshot, Trade
from core.strategy import StrategyEngine


class FakeAlerts:
    def __init__(self):
        self.calls = []

    def notify(self, title, message):
        self.calls.append((title, message))


class FakeScanner:
    def __init__(self, allowed):
        self.allowed = allowed

    def matches(self, exch, sym):
        return (exch, sym) in self.allowed


class FakeDetector:
    def __init__(self, signals):
        self._signals = signals

    def all_signals(self):
        return self._signals


class FakeScreener:
    def __init__(self, scanner, detector):
        self.scanner = scanner
        self.detector = detector
        self.alerts = FakeAlerts()


def make_robot(exch, sym, direction, score=80):
    from core.models import RobotSignal
    return RobotSignal(exchange=exch, symbol=sym, tick_ms=500, prints=6, strength=1.0,
                       buy_volume=1.0 if direction == "LONG" else 0.0,
                       sell_volume=0.0 if direction == "LONG" else 1.0,
                       avg_size=0.2, first_ms=0, last_ms=0, score=score,
                       regularity=1.0, market_reaction="WITH", fingerprint="t500")


def star_chart(level=100.0, start=1791000000000):
    """График: база 104, два касания уровня 100 -> для запуска уровней."""
    pts = []
    for i in range(30):
        pts.append((104.5, 103.5, 104.0))
    for h, l, c in [(101.0, 99.9, 100.5), (100.8, 99.95, 100.6), (101.5, 100.2, 101.0), (103.0, 101.5, 102.5)]:
        pts.append((h, l, c))
    for i in range(30):
        pts.append((104.5, 103.5, 104.0))
    for h, l, c in [(101.0, 99.9, 100.4), (100.8, 99.92, 100.6), (101.5, 100.2, 101.2), (103.0, 101.5, 102.5)]:
        pts.append((h, l, c))
    for i in range(30):
        pts.append((104.5, 103.5, 104.0))
    kl = []
    t = start
    for h, l, c in pts:
        kl.append([t, str(c), str(h), str(l), str(c), "1"])
        t += 60000
    return kl


class TestStrategy(unittest.TestCase):
    def _engine(self, signals=None, star=True):
        allowed = {("binance", "STAR")} if star else set()
        eng = StrategyEngine(FakeScreener(FakeScanner(allowed), FakeDetector(signals or [])),
                             {"mm_score_min": 30})
        eng.levels.build("binance", "STAR", star_chart())
        return eng

    def _mm_holding(self, eng, level=100.0):
        """Наполняет ММ-детектор: плотный bid на уровне стоит долго."""
        d = eng.mm("binance", "STAR")
        t = 10_000_000
        for i in range(60):
            t += 1000
            d.on_book(BookSnapshot(ts_ms=t, bids=[(level, 8.0), (level - 0.01, 5.0)],
                                   asks=[(level + 0.02, 3.0), (level + 0.03, 3.0)]))
        return d

    def _third_approach_tick(self, eng, t=1791100000000):
        t += 5 * 60000
        eng.on_trade("binance", "STAR", t, 100.4)
        return t + 1000

    def test_robot_with_mm_not_holding_breakout(self):
        eng = self._engine(signals=[make_robot("binance", "STAR", "LONG")])
        t = self._third_approach_tick(eng)
        # подход снизу вверх -> движение вверх; робот LONG = по движению; ММ не держит
        eng.on_trade("binance", "STAR", t, 100.1)
        self.assertEqual(len(eng.signals), 1, "сигнал на 3-м подходе должен выйти")
        sig = eng.signals[0]
        self.assertEqual(sig["verdict"], "BREAKOUT")
        self.assertEqual(sig["approach_no"], 3)

    def test_mm_holding_reject(self):
        eng = self._engine(signals=[make_robot("binance", "STAR", "LONG")])
        self._mm_holding(eng)
        t = self._third_approach_tick(eng)
        eng.on_trade("binance", "STAR", t, 100.1)
        self.assertEqual(len(eng.signals), 1)
        self.assertEqual(eng.signals[0]["verdict"], "REJECT")

    def test_robot_against_reject(self):
        # движение вверх, робот SHORT = против движения
        eng = self._engine(signals=[make_robot("binance", "STAR", "SHORT")])
        t = self._third_approach_tick(eng)
        eng.on_trade("binance", "STAR", t, 100.1)
        self.assertEqual(eng.signals[0]["verdict"], "REJECT")
        self.assertEqual(eng.signals[0]["robot"]["direction"], "SHORT")

    def test_no_robots_uncertain(self):
        eng = self._engine()
        t = self._third_approach_tick(eng)
        eng.on_trade("binance", "STAR", t, 100.1)
        self.assertEqual(eng.signals[0]["verdict"], "UNCERTAIN")

    def test_non_star_no_signal(self):
        eng = self._engine()
        t = 1791100000000 + 5 * 60000
        eng.on_trade("gate", "NOTSTAR", t, 100.4)
        eng.on_trade("gate", "NOTSTAR", t + 1000, 100.1)
        self.assertEqual(len(eng.signals), 0)

    def test_news_negatively_affects(self):
        class FakeNews:
            def sentiment(self, sym):
                return ("neg", [])
        eng = self._engine()  # роботов нет -> без новостей UNCERTAIN
        eng.news = FakeNews()
        t = self._third_approach_tick(eng)
        eng.on_trade("binance", "STAR", t, 100.1)
        sig = eng.signals[0]
        self.assertEqual(sig["news"], "neg")
        self.assertIn("новости против движения", sig["why"])

    def test_dedup_approaches(self):
        eng = self._engine(signals=[make_robot("binance", "STAR", "LONG")])
        t = self._third_approach_tick(eng)
        eng.on_trade("binance", "STAR", t, 100.1)
        t += 1000
        eng.on_trade("binance", "STAR", t, 100.2)  # продолжение того же подхода
        t += 1000
        eng.on_trade("binance", "STAR", t, 100.15)
        self.assertEqual(len(eng.signals), 1, "один подход — один сигнал")


if __name__ == "__main__":
    unittest.main()
