import sys
import unittest

sys.path.insert(0, ".")

from core.detectors.levels import LevelsDetector, SymbolLevels


def make_klines(path_points, start_ms=1791000000000, step=60000):
    """path_points: [(high, low, close), ...] минутные свечи вдоль траектории."""
    kl = []
    t = start_ms
    for h, l, c in path_points:
        kl.append([t, str(c), str(h), str(l), str(c), "1"])
        t += step
    return kl


class TestLevels(unittest.TestCase):
    def _flat_then_level(self, level=100.0):
        pts = []
        # базовая зона 104
        for i in range(30):
            pts.append((104.5, 103.5, 104.0))
        # подход 1: касание уровня и отскок
        for h, l, c in [(101.0, 99.9, 100.5), (100.8, 99.95, 100.6), (101.5, 100.2, 101.0), (103.0, 101.5, 102.5)]:
            pts.append((h, l, c))
        # возврат в базу
        for i in range(30):
            pts.append((104.5, 103.5, 104.0))
        # подход 2: снова касание
        for h, l, c in [(101.0, 99.9, 100.4), (100.8, 99.92, 100.6), (101.5, 100.2, 101.2), (103.0, 101.5, 102.5)]:
            pts.append((h, l, c))
        # снова база
        for i in range(30):
            pts.append((104.5, 103.5, 104.0))
        return make_klines(pts)

    def test_two_touches_form_level(self):
        sl = SymbolLevels("ex", "SYM")
        sl.build_from_klines(self._flat_then_level(100.0))
        sig = sl.significant(min_touches=2)
        self.assertTrue(any(abs(l.price - 100.0) <= sl.tolerance for l in sig),
                        "уровень ~100 с 2+ касаниями должен существовать")

    def test_approach_counter(self):
        sl = SymbolLevels("ex", "SYM")
        sl.build_from_klines(self._flat_then_level(100.0))
        lvl100 = None
        for i, l in enumerate(sl.levels):
            if abs(l.price - 100.0) <= sl.tolerance:
                lvl100 = i
                break
        self.assertIsNotNone(lvl100)
        # живой третий подход
        events = []
        t = 1791100000000
        t += 60 * 1000 * 5
        events.append(sl.on_tick(t, 100.4))
        t += 1000
        events.append(sl.on_tick(t, 100.1))
        t += 1000
        ev = sl.on_tick(t, 99.9)
        events.append(ev)
        approaches = [e for e in events if e is not None]
        self.assertTrue(approaches, "начало 3-го подхода должно быть детектировано")
        ev = approaches[0]
        self.assertEqual(ev[2], 3, "это должен быть 3-й подход (2 касания до этого)")

    def test_no_level_in_noise(self):
        import random
        rnd = random.Random(3)
        pts = [(100 * (1 + rnd.uniform(-0.004, 0.004)), 100 * (1 - rnd.uniform(0.001, 0.006)),
                100 * (1 + rnd.uniform(-0.003, 0.003))) for _ in range(200)]
        sl = SymbolLevels("ex", "SYM")
        sl.build_from_klines(make_klines(pts))
        strong = [l for l in sl.significant(4)]
        self.assertEqual(len(strong), 0, "в шуме не должно быть уровней с 4+ касаниями")

    def test_tolerance_bounds(self):
        sl = SymbolLevels("ex", "SYM")
        pts = [(100.5, 99.5, 100.0)] * 60
        sl.build_from_klines(make_klines(pts))
        self.assertLess(sl.tolerance, 0.008 * 100.0)
        self.assertGreater(sl.tolerance, 0.0005 * 100.0)

    def test_near_level(self):
        sl = SymbolLevels("ex", "SYM")
        sl.build_from_klines(self._flat_then_level(100.0))
        res = sl.near_level(100.05)
        self.assertIsNotNone(res)
        idx, lvl = res
        self.assertLess(abs(lvl.price - 100.0), sl.tolerance + 0.01)
        self.assertIsNone(sl.near_level(107.0))


if __name__ == "__main__":
    unittest.main()
