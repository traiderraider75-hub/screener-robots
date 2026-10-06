import threading
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

from core.models import BookSnapshot, Trade


class SymbolStore:
    def __init__(self, exchange: str, symbol: str, window_s: int, session_max: int = 60000):
        self.exchange = exchange
        self.symbol = symbol
        self.key = f"{exchange}:{symbol}"
        self.ring: Deque[Trade] = deque()
        self.window_s = window_s
        self.last_price: float = 0.0
        self.last_book: Optional[BookSnapshot] = None
        self.session: List[Trade] = []
        self.session_max = session_max

    def add_trade(self, trade: Trade) -> None:
        self.ring.append(trade)
        self.last_price = trade.price
        cutoff = trade.ts_ms - self.window_s * 1000
        while self.ring and self.ring[0].ts_ms < cutoff:
            self.ring.popleft()
        self._session_add(trade)

    def _session_add(self, trade: Trade) -> None:
        self.session.append(trade)
        if len(self.session) > self.session_max:
            half = self.session_max // 2
            self.session = self.session[half::2]

    def add_book(self, book: BookSnapshot) -> None:
        self.last_book = book

    def drop_session(self) -> None:
        self.session = []


class MarketStore:
    def __init__(self, window_s: int) -> None:
        self.window_s = window_s
        self._stores: Dict[Tuple[str, str], SymbolStore] = {}
        self._lock = threading.Lock()

    def get(self, exchange: str, symbol: str) -> SymbolStore:
        key = (exchange, symbol)
        with self._lock:
            store = self._stores.get(key)
            if store is None:
                store = SymbolStore(exchange, symbol, self.window_s)
                self._stores[key] = store
            return store

    def add_trade(self, trade: Trade) -> None:
        self.get(trade.exchange, trade.symbol).add_trade(trade)

    def add_book(self, exchange: str, symbol: str, book: BookSnapshot) -> None:
        self.get(exchange, symbol).add_book(book)

    def all_keys(self) -> List[Tuple[str, str]]:
        with self._lock:
            return list(self._stores.keys())

    def symbols_by_exchange(self) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {}
        for exch, sym in self.all_keys():
            result.setdefault(exch, []).append(sym)
        for exch in result:
            result[exch].sort()
        return result

    def reset_sessions(self) -> None:
        with self._lock:
            for store in self._stores.values():
                store.drop_session()
