import sys
import time
import unittest

sys.path.insert(0, ".")

from core.news import NewsWatcher


class TestNews(unittest.TestCase):
    def _fill(self, nw, items):
        for ts, title in items:
            tickers = nw._extract_tickers(title)
            nw._items.append({"ts_ms": ts, "title": title, "tickers": tickers, "label": nw._classify(title)})

    def test_classify(self):
        self.assertEqual(NewsWatcher._classify("Binance Futures Will Launch CTUSDT Perpetual Contract"), "pos")
        self.assertEqual(NewsWatcher._classify("Binance Will Delist XYZUSDT Perpetual Contract"), "neg")
        self.assertEqual(NewsWatcher._classify("Notice of Maintenance Downtime"), "neutral")
        self.assertEqual(NewsWatcher._classify("Binance Will List ABC 1-50x Leverage"), "pos")

    def test_ticker_extract(self):
        nw = NewsWatcher()
        t = nw._extract_tickers("Binance Futures Will Launch CTUSDT Perpetual Contract")
        self.assertIn("CT", t)
        t2 = nw._extract_tickers("Binance Will Delist XYZBUSD on 2026-01-01")
        self.assertIn("XYZ", t2)

    def test_sentiment_window_4h(self):
        nw = NewsWatcher(window_h=4)
        now = time.time() * 1000
        self._fill(nw, [
            (now - 5 * 3600 * 1000, "Old news about CTUSDT hack"),          # вне окна
            (now - 2 * 3600 * 1000, "Binance Futures Will Launch CTUSDT Perpetual"),  # pos
        ])
        label, items = nw.sentiment("CTUSDT")
        self.assertEqual(label, "pos")
        self.assertEqual(len(items), 1)

    def test_neg_overrides_pos(self):
        nw = NewsWatcher(window_h=4)
        now = time.time() * 1000
        self._fill(nw, [
            (now - 3 * 3600 * 1000, "Binance Will List ABCUSDT Perpetual"),
            (now - 1 * 3600 * 1000, "Binance Will Delist ABCUSDT Due To Investigation"),
        ])
        label, items = nw.sentiment("ABCUSDT")
        self.assertEqual(label, "neg")
        self.assertEqual(len(items), 2)

    def test_no_news_returns_none(self):
        nw = NewsWatcher(window_h=4)
        label, items = nw.sentiment("ZZZUSDT")
        self.assertIsNone(label)
        self.assertEqual(items, [])

    def test_sentiment_matches_by_base_and_alias(self):
        nw = NewsWatcher(window_h=4)
        nw.register_alias("CTUSDT")
        now = time.time() * 1000
        self._fill(nw, [(now - 3600 * 1000, "Binance Will List CTUSDT Perpetual Contract")])
        label, _ = nw.sentiment("CT_USDT")  # формат MEXC/Gate
        self.assertEqual(label, "pos")


if __name__ == "__main__":
    unittest.main()
