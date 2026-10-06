import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, ".")

from core.radar import Radar
from core.history import History
from core.models import Trade


class TestRadar(unittest.TestCase):
    def test_metronome_triggers(self):
        r = Radar()
        t = 1_000_000
        for i in range(520):
            t += 500
            r.on_trade("ex", "SYM", t, 5.0)
            r.on_trade("ex", "SYM", t + 100, 0.1)
        score = r.score("ex", "SYM")
        self.assertGreaterEqual(score, 50)
        trig = r.triggered()
        self.assertTrue(any(s == "SYM" for _, s, _ in trig))

    def test_quiet_market_no_trigger(self):
        import random

        rnd = random.Random(7)
        r = Radar()
        t = 2_000_000
        for i in range(5000):
            t += rnd.randint(50, 2000)
            r.on_trade("ex", "SYM", t, rnd.expovariate(10) + 0.01)
        self.assertLess(r.score("ex", "SYM"), 50)

    def test_decay(self):
        r = Radar()
        t = 3_000_000
        for i in range(20):
            t += 500
            r.on_trade("ex", "SYM", t, 50.0)
        hot = r.score("ex", "SYM")
        self.assertGreater(hot, 0)
        t += 10 * 60 * 1000
        r.on_trade("ex", "SYM", t, 0.5)
        self.assertLess(r.score("ex", "SYM"), hot)


class TestHistory(unittest.TestCase):
    def _tmp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)
        return path

    def test_roundtrip_and_keys(self):
        path = self._tmp()
        try:
            h = History(path, retention_h=24)
            h.add_key("binance", "AAA")
            h.record("binance", "AAA", 1000, 10.0, 1.0, True, False)
            h.record("binance", "BBB", 1000, 10.0, 1.0, True, False)
            h.insert_many("binance", "AAA", [(500, 9.0, 2.0, False, True), (700, 9.5, 1.0, True, False)])
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and h.pending() > 0:
                time.sleep(0.05)
            self.assertEqual(h.pending(), 0)
            rows = h.query("binance", "AAA", before_ms=2000, limit=100)
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["t"], 500)
            self.assertTrue(rows[0]["r"])
            self.assertEqual(h.query("binance", "BBB", 2000), [])
            self.assertIn(("binance", "AAA"), h.keys())
            h.close()
        finally:
            os.unlink(path)

    def test_retention_cleanup(self):
        path = self._tmp()
        try:
            h = History(path, retention_h=24)
            h.add_key("ex", "S")
            old_ts = int(time.time() * 1000) - 25 * 3600 * 1000
            h.insert_many("ex", "S", [(old_ts, 1.0, 1.0, True, False)])
            fresh_ts = int(time.time() * 1000) - 1000
            h.insert_many("ex", "S", [(fresh_ts, 1.0, 1.0, True, False)])
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and h.pending() > 0:
                time.sleep(0.05)
            h.cleanup_now()
            rows = h.query("ex", "S", before_ms=int(time.time() * 1000) + 1, limit=100)
            self.assertEqual(len(rows), 1)
            h.close()
        finally:
            os.unlink(path)


class TestFastForward(unittest.TestCase):
    def test_backfill_detects_metronome(self):
        from core.backfill import _ff_tag

        rows = []
        t = 1_000_000
        for i in range(30):
            t += 500
            rows.append((t, 100.0, 5.0, True))
            rows.append((t + 100, 100.0, 0.1, True))
        tagged = _ff_tag(rows)
        robot_rows = [r for r in tagged if r[4]]
        self.assertGreaterEqual(len(robot_rows), 5)


if __name__ == "__main__":
    unittest.main()
