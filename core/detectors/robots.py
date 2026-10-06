import statistics
from collections import Counter, deque
from typing import Deque, Dict, List, Optional, Tuple

from core.models import RobotSignal, Trade


class RobotRun:
    def __init__(self, exchange: str, symbol: str, tick_ms: int):
        self.exchange = exchange
        self.symbol = symbol
        self.tick_ms = tick_ms
        self.first_ms = 0
        self.last_ms = 0
        self.prints = 0
        self.buy_volume = 0.0
        self.sell_volume = 0.0
        self.sizes: List[float] = []
        self.start_price = 0.0
        self.last_price = 0.0

    def add(self, trade: Trade) -> None:
        if self.prints == 0:
            self.first_ms = trade.ts_ms
            self.start_price = trade.price
        self.last_ms = trade.ts_ms
        self.last_price = trade.price
        self.prints += 1
        self.sizes.append(trade.size)
        if trade.is_buy:
            self.buy_volume += trade.size
        else:
            self.sell_volume += trade.size

    def strength(self) -> float:
        return self.buy_volume + self.sell_volume

    def fingerprint(self) -> str:
        side = "B" if self.buy_volume >= self.sell_volume else "S"
        return "t{}~sz{}~{}".format(self.tick_ms, round(statistics.median(self.sizes) if self.sizes else 0, 6), side)

    def to_signal(self) -> RobotSignal:
        diffs_reg = 1.0
        med = statistics.median(self.sizes) if self.sizes else 0.0
        price_move = (self.last_price - self.start_price) / self.start_price if self.start_price else 0.0
        is_long = self.buy_volume >= self.sell_volume
        if abs(price_move) < 1e-9:
            reaction = "FLAT"
        elif (price_move > 0) == is_long:
            reaction = "WITH"
        else:
            reaction = "AGAINST"
        return RobotSignal(
            exchange=self.exchange,
            symbol=self.symbol,
            tick_ms=self.tick_ms,
            prints=self.prints,
            strength=self.strength(),
            buy_volume=self.buy_volume,
            sell_volume=self.sell_volume,
            avg_size=med,
            first_ms=self.first_ms,
            last_ms=self.last_ms,
            score=self.score(),
            regularity=round(diffs_reg, 3),
            market_reaction=reaction,
            fingerprint=self.fingerprint(),
            duration_s=int((self.last_ms - self.first_ms) / 1000),
        )

    def score(self) -> int:
        reg = 1.0
        prints_norm = min(self.prints / 20.0, 1.0)
        med = statistics.median(self.sizes) if self.sizes else 1.0
        strength_norm = min((self.strength() / max(med * 50.0, 1e-9)), 1.0)
        return int(round((reg * 40 + strength_norm * 30 + prints_norm * 30)))


class SymbolRobotDetector:
    def __init__(self, exchange: str, symbol: str, window_s: int, cfg: dict):
        self.exchange = exchange
        self.symbol = symbol
        self.window_s = window_s
        self.size_k = float(cfg.get("size_k", 3.0))
        self.min_prints = int(cfg.get("min_prints", 5))
        self.tolerance = float(cfg.get("tick_tolerance", 0.15))
        self.min_regularity = float(cfg.get("min_regularity", 0.6))
        self.tick_bucket_ms = int(cfg.get("tick_bucket_ms", 10))
        self._sizes: Deque[Tuple[int, float]] = deque()
        self._candidates: Deque[Trade] = deque()
        self._run: Optional[RobotRun] = None

    def on_trade(self, trade: Trade) -> Optional[RobotSignal]:
        self._sizes.append((trade.ts_ms, trade.size))
        self._trim(self._sizes, trade.ts_ms)
        signal: Optional[RobotSignal] = None
        if trade.size >= self.size_k * self._typical_size():
            self._candidates.append(trade)
            self._trim(self._candidates, trade.ts_ms)
            signal = self._evaluate(trade)
        self._expire_run(trade.ts_ms)
        return signal

    def _typical_size(self) -> float:
        if not self._sizes:
            return 0.0
        sizes = [s for _, s in list(self._sizes)[-2000:]]
        idx = max(0, int(len(sizes) * 0.25) - 1)
        return sorted(sizes)[idx]

    def _trim(self, dq, now_ms: int) -> None:
        cutoff = now_ms - self.window_s * 1000
        while dq:
            first = dq[0]
            ts = first.ts_ms if hasattr(first, "ts_ms") else first[0]
            if ts < cutoff:
                dq.popleft()
            else:
                break

    def _local_regular(self) -> Tuple[bool, int]:
        if len(self._candidates) < self.min_prints + 1:
            return False, 0
        recent = list(self._candidates)[-(self.min_prints + 1):]
        diffs = [b.ts_ms - a.ts_ms for a, b in zip(recent, recent[1:])]
        med = statistics.median(diffs)
        if med <= 0:
            return False, 0
        tick = max(int(round(med / self.tick_bucket_ms)) * self.tick_bucket_ms, self.tick_bucket_ms)
        for d in diffs:
            if abs(d - med) > self.tolerance * med:
                return False, 0
        return True, tick

    def _global_consistency(self, tick: int) -> float:
        if len(self._candidates) < 2:
            return 0.0
        diffs = [b.ts_ms - a.ts_ms for a, b in zip(self._candidates, list(self._candidates)[1:])]
        if not diffs:
            return 0.0
        good = sum(1 for d in diffs if abs(d - tick) <= self.tolerance * tick)
        return good / len(diffs)

    def _evaluate(self, trade: Trade) -> Optional[RobotSignal]:
        regular, tick = self._local_regular()
        if not regular:
            return None
        consistency = self._global_consistency(tick)
        if consistency < self.min_regularity:
            return None
        run = self._run
        gap_limit = max(tick * 5, 30000)
        if run is not None and run.tick_ms == tick and trade.ts_ms - run.last_ms <= gap_limit:
            run.add(trade)
            trade.is_robot = True
            return None
        new_run = RobotRun(self.exchange, self.symbol, tick)
        recent = list(self._candidates)[-(self.min_prints + 1):]
        for t in recent:
            new_run.add(t)
            t.is_robot = True
        self._run = new_run
        return new_run.to_signal()

    def _expire_run(self, now_ms: int) -> None:
        run = self._run
        if run is None:
            return
        if now_ms - run.last_ms > max(run.tick_ms * 10, 60000):
            self._run = None

    def active_signal(self) -> Optional[RobotSignal]:
        return self._run.to_signal() if self._run is not None else None


class RobotDetector:
    def __init__(self, window_s: int, cfg: dict, alert_cb=None):
        self.window_s = window_s
        self.cfg = cfg
        self._detectors: Dict[Tuple[str, str], SymbolRobotDetector] = {}
        self._alert_cb = alert_cb

    def on_trade(self, trade: Trade) -> None:
        key = (trade.exchange, trade.symbol)
        det = self._detectors.get(key)
        if det is None:
            det = SymbolRobotDetector(trade.exchange, trade.symbol, self.window_s, self.cfg)
            self._detectors[key] = det
        signal = det.on_trade(trade)
        if signal is not None and self._alert_cb is not None:
            self._alert_cb(signal)

    def all_signals(self) -> List[RobotSignal]:
        out = []
        for det in self._detectors.values():
            sig = det.active_signal()
            if sig is not None:
                out.append(sig)
        return out
