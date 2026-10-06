import statistics
from collections import deque
from typing import Deque, Dict, Optional, Tuple

from core.models import BookSnapshot, Trade

WALL_K = 5.0
WALL_MIN_LIFETIME_MS = 2000
WALL_MAX_LIFETIME_MS = 15000
EVENT_TTL_MS = 180000
TRADE_MEM_MS = 1500


class WallsDetector:
    """Маркер 4: крупный уровень исчезает при приближении цены без сделки через него (спуфинг)."""

    def __init__(self, exchange: str, symbol: str):
        self.exchange = exchange
        self.symbol = symbol
        self._walls: Dict[Tuple[str, float], dict] = {}
        self._events: Deque[dict] = deque()
        self._pending_alerts: list = []
        self._recent_trades: Deque[Tuple[int, float]] = deque()
        self._prev_mid: Optional[float] = None
        self.last_ts = 0

    def on_book(self, book: BookSnapshot) -> None:
        now = book.ts_ms
        if now < self.last_ts:
            return
        self.last_ts = now
        self._prune(now)

        bid = book.best_bid()
        ask = book.best_ask()
        if not bid or not ask:
            return
        mid = (bid[0] + ask[0]) / 2.0

        sizes = [s for _, s in book.bids] + [s for _, s in book.asks]
        if not sizes:
            return
        typical = statistics.median(sizes)

        seen = set()
        for side, levels in (("bid", book.bids), ("ask", book.asks)):
            for price, size in levels:
                if size < WALL_K * typical or typical <= 0:
                    continue
                key = (side, price)
                seen.add(key)
                if key not in self._walls:
                    self._walls[key] = {
                        "size": size,
                        "first_ts": now,
                        "mid_at_seen": mid,
                        "last_seen": now,
                    }
                else:
                    self._walls[key]["last_seen"] = now
                    self._walls[key]["size"] = max(self._walls[key]["size"], size)

        for key in list(self._walls.keys()):
            if key in seen:
                continue
            side, price = key
            w = self._walls[key]
            del self._walls[key]
            lifetime = w["last_seen"] - w["first_ts"]
            if lifetime < WALL_MIN_LIFETIME_MS or lifetime > WALL_MAX_LIFETIME_MS:
                continue
            mid_seen = w["mid_at_seen"]
            if side == "bid":
                dist_seen = mid_seen - price
                dist_now = mid - price
            else:
                dist_seen = price - mid_seen
                dist_now = price - mid
            if dist_now >= dist_seen:
                continue
            if self._had_trade_at(price, now):
                continue
            self._events.append(
                {"ts": now, "exchange": self.exchange, "symbol": self.symbol, "side": side,
                 "price": price, "size": w["size"], "lifetime_s": lifetime / 1000.0}
            )
            self._pending_alerts.append(self._events[-1])

        self._prev_mid = mid

    def on_trade(self, trade: Trade) -> None:
        self._recent_trades.append((trade.ts_ms, trade.price))
        while self._recent_trades and self._recent_trades[0][0] < trade.ts_ms - TRADE_MEM_MS:
            self._recent_trades.popleft()

    def _had_trade_at(self, price: float, now: int) -> bool:
        for ts, tp in self._recent_trades:
            if ts < now - TRADE_MEM_MS:
                continue
            if abs(tp - price) / max(price, 1e-9) < 1e-6:
                return True
        return False

    def _prune(self, now: int) -> None:
        cutoff = now - EVENT_TTL_MS
        while self._events and self._events[0]["ts"] < cutoff:
            self._events.popleft()
        while self._pending_alerts and self._pending_alerts[0]["ts"] < cutoff:
            self._pending_alerts.pop(0)

    def state(self) -> tuple:
        now = self.last_ts
        self._prune(now)
        score = 0.0
        for e in tuple(self._events):
            age = now - e["ts"]
            score += 45.0 * (2.0 ** (-age / 60000.0))
        score = int(min(score, 100.0))
        info = ""
        if self._events:
            last = self._events[-1]
            info = "снята стена {:.4g} {}@{:.8g}".format(
                last["size"], "bid" if last["side"] == "bid" else "ask", last["price"]
            )
        return score, info
