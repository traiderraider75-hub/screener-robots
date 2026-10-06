from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Trade:
    ts_ms: int
    price: float
    size: float
    is_buy: bool
    exchange: str
    symbol: str
    is_robot: bool = False

    @classmethod
    def of(cls, exchange: str, symbol: str, ts_ms: int, price: float, size: float, is_buy: bool) -> "Trade":
        return cls(ts_ms=ts_ms, price=price, size=size, is_buy=is_buy, exchange=exchange, symbol=symbol)


@dataclass
class BookSnapshot:
    ts_ms: int
    bids: List[Tuple[float, float]]
    asks: List[Tuple[float, float]]

    def best_bid(self):
        return self.bids[0] if self.bids else None

    def best_ask(self):
        return self.asks[0] if self.asks else None


@dataclass
class RobotSignal:
    exchange: str
    symbol: str
    tick_ms: int
    prints: int
    strength: float
    buy_volume: float
    sell_volume: float
    avg_size: float
    first_ms: int
    last_ms: int
    score: int
    regularity: float
    market_reaction: str
    fingerprint: str
    duration_s: int = 0

    @property
    def direction(self) -> str:
        if self.buy_volume >= self.sell_volume:
            return "LONG"
        return "SHORT"

    def to_row(self) -> dict:
        return {
            "key": f"{self.exchange}:{self.symbol}:{self.fingerprint}",
            "exchange": self.exchange.upper(),
            "symbol": self.symbol,
            "tick_ms": self.tick_ms,
            "prints": self.prints,
            "strength": round(self.strength, 4),
            "direction": self.direction,
            "score": self.score,
            "regularity": round(self.regularity, 2),
            "reaction": self.market_reaction,
            "duration_s": self.duration_s,
            "first_ms": self.first_ms,
            "last_ms": self.last_ms,
        }
