from collections import deque
from typing import Deque

from core.models import BookSnapshot


class FlickerDetector:
    """Маркер 1+2: частота обновлений лучшего уровня бид/аск (HFT-котирование)."""

    WINDOW_MS = 3000

    def __init__(self, exchange: str, symbol: str):
        self.exchange = exchange
        self.symbol = symbol
        self._events: Deque[int] = deque()
        self._prev = None
        self.last_ts = 0

    def on_book(self, book: BookSnapshot) -> None:
        now = book.ts_ms
        self.last_ts = max(self.last_ts, now)
        cutoff = now - self.WINDOW_MS
        while self._events and self._events[0] < cutoff:
            self._events.popleft()
        bb = book.best_bid()
        ba = book.best_ask()
        cur = (bb[0], bb[1], ba[0], ba[1]) if bb and ba else None
        if cur is None:
            return
        if self._prev is not None and cur != self._prev:
            self._events.append(now)
        self._prev = cur

    def state(self) -> tuple:
        now = self.last_ts
        cutoff = now - self.WINDOW_MS
        while self._events and self._events[0] < cutoff:
            self._events.popleft()
        rate = len(self._events) / (self.WINDOW_MS / 1000.0)
        score = int(min(rate / 15.0, 1.0) * 100)
        info = "{:.0f} обн/с".format(rate) if rate >= 3 else ""
        return score, info
