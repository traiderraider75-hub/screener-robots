import math
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

BURST_MIN_COUNT = 7
METRONOME_JITTER = 0.10
MIN_TICK_MS = 150
BIG_SIZE_K = 12.0
RATE_JUMP_K = 5.0
RATE_MIN_ABS = 20
DECAY_HALF_LIFE_S = 60.0
ADD_BIG = 25.0
ADD_METRONOME = 60.0
ADD_RATE = 25.0
MAX_SUSPICION = 100.0
SIZES_WINDOW = 200
RECENT_WINDOW = 20
TYPICAL_RECOMPUTE_EVERY = 20
COOLDOWN_BIG_MS = 60000
COOLDOWN_METRONOME_MS = 120000
COOLDOWN_RATE_MS = 30000
TRIGGER_MIN_METRO = 2
TRIGGER_METRO_WINDOW_MS = 180000


class RadarCell:
    __slots__ = ("last_ts", "suspicion", "sizes", "typical", "since_typical",
                 "recent_ts", "recent_sizes", "ema_rate", "cur_second", "cur_count",
                 "cd_big", "cd_metro", "cd_rate", "metro_ts",
                 "hour_ts", "hour_vol", "hours")

    def __init__(self):
        self.last_ts = 0
        self.suspicion = 0.0
        self.sizes: Deque[float] = deque(maxlen=SIZES_WINDOW)
        self.typical = 0.0
        self.since_typical = 0
        self.recent_ts: Deque[int] = deque(maxlen=RECENT_WINDOW)
        self.recent_sizes: Deque[float] = deque(maxlen=RECENT_WINDOW)
        self.ema_rate = 0.0
        self.cur_second = 0
        self.cur_count = 0
        self.cd_big = 0
        self.cd_metro = 0
        self.cd_rate = 0
        self.metro_ts: Deque[int] = deque(maxlen=8)
        self.hour_ts = 0
        self.hour_vol = 0.0
        self.hours: Deque[float] = deque(maxlen=24)

    def on_trade(self, ts_ms: int, size: float) -> float:
        added = 0.0
        if self.last_ts and ts_ms > self.last_ts:
            dt_s = (ts_ms - self.last_ts) / 1000.0
            self.suspicion *= math.pow(0.5, dt_s / DECAY_HALF_LIFE_S)
        self.last_ts = ts_ms

        bucket = (ts_ms // 3600000) * 3600000
        if bucket != self.hour_ts:
            if self.hour_ts:
                self.hours.append(self.hour_vol)
            self.hour_ts = bucket
            self.hour_vol = 0.0
        self.hour_vol += size

        self.sizes.append(size)
        self.since_typical += 1
        if self.typical <= 0 or self.since_typical >= TYPICAL_RECOMPUTE_EVERY:
            self.since_typical = 0
            self.typical = self._q25()

        second = ts_ms // 1000
        if second != self.cur_second:
            instant_rate = self.cur_count
            if self.ema_rate <= 0:
                self.ema_rate = instant_rate
            else:
                self.ema_rate += (instant_rate - self.ema_rate) * 0.05
            self.cur_second = second
            self.cur_count = 0
        self.cur_count += 1

        if self.typical > 0 and size >= BIG_SIZE_K * self.typical and ts_ms - self.cd_big > COOLDOWN_BIG_MS:
            self.cd_big = ts_ms
            added += ADD_BIG

        self.recent_ts.append(ts_ms)
        self.recent_sizes.append(size)
        if ts_ms - self.cd_metro > COOLDOWN_METRONOME_MS and self._metronome(size):
            self.cd_metro = ts_ms
            self.metro_ts.append(ts_ms)
            added += ADD_METRONOME

        if (self.ema_rate > 0 and self.cur_count >= RATE_MIN_ABS
                and self.cur_count >= RATE_JUMP_K * self.ema_rate
                and ts_ms - self.cd_rate > COOLDOWN_RATE_MS):
            self.cd_rate = ts_ms
            added += ADD_RATE

        if added:
            self.suspicion = min(MAX_SUSPICION, self.suspicion + added)
        return self.suspicion

    def _q25(self) -> float:
        if not self.sizes:
            return 0.0
        sizes = sorted(self.sizes)
        idx = max(0, int(len(sizes) * 0.25) - 1)
        return sizes[idx]

    def _metronome(self, size: float) -> bool:
        ts_list = []
        for ts, s in zip(self.recent_ts, self.recent_sizes):
            if abs(s - size) <= max(size * 0.01, 1e-12):
                ts_list.append(ts)
        if len(ts_list) < BURST_MIN_COUNT:
            return False
        diffs = [b - a for a, b in zip(ts_list, ts_list[1:])]
        if len(diffs) < BURST_MIN_COUNT - 1:
            return False
        med = sorted(diffs)[len(diffs) // 2]
        if med < MIN_TICK_MS:
            return False
        for d in diffs:
            if abs(d - med) > METRONOME_JITTER * med:
                return False
        return True


    def rvol(self) -> float:
        """Относительный объём: текущий час / средний завершённый час."""
        if not self.hours:
            return 0.0
        avg = sum(self.hours) / len(self.hours)
        if avg <= 0:
            return 0.0
        return self.hour_vol / avg

    def seed_hours(self, hourly_vols: List[float], current_vol: float = 0.0, now_ms: int = 0) -> None:
        """Bootstrap базы средних из часовых klines."""
        self.hours.clear()
        for v in hourly_vols[-24:]:
            self.hours.append(float(v))
        if now_ms and self.hour_ts == 0:
            self.hour_ts = (now_ms // 3600000) * 3600000
            self.hour_vol = float(current_vol)
        else:
            self.hour_vol = max(self.hour_vol, float(current_vol))


class Radar:
    """Уровень 1: дешёвый префильтр всех символов. O(1) на сделку (амортизированно)."""

    def __init__(self, notify_threshold: float = 50.0):
        self.notify_threshold = notify_threshold
        self._cells: Dict[Tuple[str, str], RadarCell] = {}
        self.last_ts = 0

    def on_trade(self, exchange: str, symbol: str, ts_ms: int, size: float) -> float:
        key = (exchange, symbol)
        cell = self._cells.get(key)
        if cell is None:
            cell = RadarCell()
            self._cells[key] = cell
        s = cell.on_trade(ts_ms, size)
        self.last_ts = max(self.last_ts, ts_ms)
        return s

    def score(self, exchange: str, symbol: str) -> int:
        cell = self._cells.get((exchange, symbol))
        return int(cell.suspicion) if cell else 0

    def cell(self, exchange: str, symbol: str) -> Optional[RadarCell]:
        return self._cells.get((exchange, symbol))

    def cell_or_new(self, exchange: str, symbol: str) -> RadarCell:
        key = (exchange, symbol)
        cell = self._cells.get(key)
        if cell is None:
            cell = RadarCell()
            self._cells[key] = cell
        return cell

    def top_suspicious(self, limit: int = 60) -> List[Tuple[str, str, int]]:
        now = self.last_ts
        out = []
        for key, cell in self._cells.items():
            if now and cell.last_ts < now - 120000:
                continue
            if cell.suspicion >= 5:
                out.append((key[0], key[1], int(cell.suspicion)))
        out.sort(key=lambda x: x[2], reverse=True)
        return out[:limit]

    def triggered(self) -> List[Tuple[str, str, int]]:
        out = []
        for key, cell in self._cells.items():
            if len(cell.metro_ts) < TRIGGER_MIN_METRO:
                continue
            if self.last_ts and cell.metro_ts[-1] < self.last_ts - TRIGGER_METRO_WINDOW_MS:
                continue
            out.append((key[0], key[1], int(cell.suspicion)))
        out.sort(key=lambda x: x[2], reverse=True)
        return out

    def candidates(self, strong_score: int = 80) -> List[Tuple[str, str, int]]:
        merged: Dict[Tuple[str, str], int] = {}
        for e, s, v in self.triggered():
            merged[(e, s)] = v
        for e, s, v in self.top_suspicious(500):
            if v >= strong_score:
                merged[(e, s)] = max(merged.get((e, s), 0), v)
        return sorted(merged.items(), key=lambda kv: kv[1], reverse=True)

    def stats(self) -> Dict[str, int]:
        now = self.last_ts
        active = 0
        suspicious = 0
        for cell in self._cells.values():
            if now and cell.last_ts >= now - 120000:
                active += 1
            if cell.suspicion >= 5:
                suspicious += 1
        return {"symbols": len(self._cells), "active": active, "suspicious": suspicious}


def monotonic_ms() -> int:
    return int(time.time() * 1000)
