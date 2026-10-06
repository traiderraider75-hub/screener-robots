from collections import deque
from typing import Deque, Dict, Optional, Tuple

from core.models import BookSnapshot, Trade

EXEC_WINDOW_MS = 90000
EVENT_TTL_MS = 180000
RATIO_K = 3.0
MIN_REPEATS = 4


class IcebergDetector:
    """Маркер 5 + скрытая ликвидность: исполненный объём по цене >> видимого размера уровня."""

    def __init__(self, exchange: str, symbol: str, min_exec: float = 1.0):
        self.exchange = exchange
        self.symbol = symbol
        self.min_exec = min_exec
        self._exec: Deque[Tuple[int, str, float, float]] = deque()
        self._displayed: Dict[str, Dict[float, float]] = {"bid": {}, "ask": {}}
        self._prev_book: Optional[BookSnapshot] = None
        self._events: Deque[dict] = deque()
        self._pending_alerts: list = []
        self._alert_seen: Dict[Tuple[str, float], int] = {}
        self.last_ts = 0

    def on_book(self, book: BookSnapshot) -> None:
        if book.ts_ms < self.last_ts:
            return
        self.last_ts = book.ts_ms
        self._prev_book = book
        for side, levels in (("bid", book.bids), ("ask", book.asks)):
            bucket = self._displayed[side]
            for price, size in levels:
                if size > bucket.get(price, 0.0):
                    bucket[price] = size

    def on_trade(self, trade: Trade) -> None:
        now = trade.ts_ms
        self.last_ts = max(self.last_ts, now)
        cutoff = now - EXEC_WINDOW_MS
        while self._exec and self._exec[0][0] < cutoff:
            old = self._exec.popleft()
            side, price = old[1], old[2]
            if not any(e[1] == side and e[2] == price for e in tuple(self._exec)):
                self._displayed[side].pop(price, None)
        side = "ask" if trade.is_buy else "bid"
        self._exec.append((now, side, trade.price, trade.size))
        self._prune_events(now)

    def _prune_events(self, now: int) -> None:
        cutoff = now - EVENT_TTL_MS
        while self._events and self._events[0]["ts"] < cutoff:
            self._events.popleft()

    def state(self) -> tuple:
        now = self.last_ts
        cutoff = now - EXEC_WINDOW_MS
        self._prune_events(now)

        agg: Dict[Tuple[str, float], dict] = {}
        for ts, side, price, size in tuple(self._exec):
            if ts < cutoff:
                continue
            key = (side, price)
            a = agg.setdefault(key, {"vol": 0.0, "n": 0, "last_ts": 0})
            a["vol"] += size
            a["n"] += 1
            a["last_ts"] = max(a["last_ts"], ts)

        score = 0.0
        best = None
        for (side, price), a in agg.items():
            displayed = self._displayed[side].get(price, 0.0)
            if displayed > 0 and a["vol"] < RATIO_K * displayed:
                continue
            if displayed <= 0 and a["n"] < MIN_REPEATS:
                continue
            if a["vol"] < self.min_exec:
                continue
            ev_score = 40.0 * (2.0 ** (-(now - a["last_ts"]) / 60000.0))
            ev_score = min(ev_score, min(100.0, a["vol"] / max(displayed, 0.1) * 15 + 30))
            if ev_score > score:
                score = ev_score
                best = (side, price, a["vol"], displayed)

        if best is None:
            return 0, ""
        side, price, vol, displayed = best
        last_alert = self._alert_seen.get((side, price), 0)
        if now - last_alert > 60000:
            self._alert_seen[(side, price)] = now
            self._pending_alerts.append(
                {"ts": now, "exchange": self.exchange, "symbol": self.symbol,
                 "side": side, "price": price, "volume": vol, "displayed": displayed}
            )
        info = "айсберг ~{:.4g} {}@{:.8g}{} (видно было {:.4g})".format(
            vol, "ask" if side == "ask" else "bid", price,
            ", сделок {}".format(agg[(side, price)]["n"]) if displayed <= 0 else "",
            displayed,
        )
        return int(min(score, 100)), info
