import sys
import unittest

sys.path.insert(0, ".")

from core.detectors.mm import MMDetector
from core.models import BookSnapshot, Trade


def book(ts, bid, bid_sz, ask, ask_sz):
    return BookSnapshot(ts_ms=ts, bids=[(bid, bid_sz), (bid - 0.01, bid_sz)], asks=[(ask, ask_sz), (ask + 0.01, ask_sz)])


class TestMM(unittest.TestCase):
    def test_stable_mm_market(self):
        d = MMDetector("ex", "SYM")
        t = 10_000_000
        # стабильный спред 2 тика (0.02), уровни восстанавливаются после съедения
        for i in range(150):
            t += 1000
            d.on_book(book(t, 100.00, 5.0, 100.02, 5.0))
        # пара снятий-восстановлений уровня bid
        t += 1000
        d.on_book(book(t, 99.99, 5.0, 100.02, 5.0))
        t += 500
        d.on_book(book(t, 100.00, 5.0, 100.02, 5.0))  # восстановился быстро
        t += 2000
        d.on_book(book(t, 99.99, 5.0, 100.02, 5.0))
        t += 500
        d.on_book(book(t, 100.00, 5.0, 100.02, 5.0))
        score, info = d.state()
        self.assertGreaterEqual(score, 40, "MM-рынок должен давать высокий score")
        self.assertIn("спред", info)

    def test_spoofer_penalized(self):
        d = MMDetector("ex", "SYM")
        t = 10_000_000
        # база: плотный bid-стеной 100.00 и нормальным ask
        for i in range(150):
            t += 1000
            d.on_book(book(t, 100.00, 5.0, 100.02, 5.0))
        # 3 цикла: цена ПОДХОДИТ к стене bid 100.00 (mid 100.01 -> 100.005),
        # стена снимается и не возвращается быстро (спуфер уводит ликвидность)
        for _ in range(3):
            t += 1000
            d.on_book(book(t, 100.00, 50.0, 100.02, 5.0))  # стена усилилась, стоит
            t += 1000
            d.on_book(book(t, 100.00, 50.0, 100.03, 5.0))  # цена подходит: ask отступил
            t += 1000
            d.on_book(book(t, 99.995, 1.0, 100.03, 5.0))   # стена снята при подходе!
            t += 6000                                       # не возвращается за RESTORE_MS
            d.on_book(book(t, 99.995, 1.0, 100.03, 5.0))
            t += 6000
            d.on_book(book(t, 100.00, 5.0, 100.02, 5.0))   # позже вернулась — но поздно
        score, info = d.state()
        self.assertLess(score, 40, "спуфинг должен обнулять MM-score: " + info)

    def test_holding_level_true_when_density_stands(self):
        d = MMDetector("ex", "SYM")
        t = 10_000_000
        # плотный bid на уровне 100 стоит долго
        for i in range(60):
            t += 1000
            d.on_book(book(t, 100.00, 8.0, 100.02, 3.0))
        holds, why = d.holding_level(100.00, tolerance=0.005)
        self.assertTrue(holds, why)
        self.assertIn("стоит", why)

    def test_holding_level_false_after_unrestored_vanish(self):
        d = MMDetector("ex", "SYM")
        t = 10_000_000
        for i in range(60):
            t += 1000
            d.on_book(book(t, 100.00, 8.0, 100.02, 3.0))
        # уровень снят и не восстановлен
        t += 1000
        d.on_book(book(t, 99.98, 1.0, 100.02, 3.0))
        holds, why = d.holding_level(100.00, tolerance=0.005)
        self.assertFalse(holds, why)

    def test_inventory_shift_detected(self):
        d = MMDetector("ex", "SYM")
        t = 10_000_000
        for i in range(60):
            t += 1000
            d.on_book(book(t, 100.00, 5.0, 100.02, 5.0))
        # серия покупок: после каждой mid дрейфует вверх (следующая книга с новым mid)
        for i in range(12):
            t += 1000
            d.on_trade(Trade.of("ex", "SYM", t, 100.01, 5.0, True))
            t += 1000
            new_mid = 100.02 + 0.1 * (i + 1)
            d.on_book(book(t, new_mid - 0.01, 5.0, new_mid + 0.01, 5.0))
        self.assertGreaterEqual(len(d._post_buy_ask_shift), 10)
        self.assertGreater(sum(d._post_buy_ask_shift), 0, "после покупок mid должен расти")
        score, info = d.state()
        self.assertGreaterEqual(score, 15, "инвентарное смещение должно добавлять баллы: " + info)


if __name__ == "__main__":
    unittest.main()
