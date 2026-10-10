import statistics
import time
from typing import Dict, List, Optional, Tuple


class Level:
    __slots__ = ("price", "touches", "last_touch_ts", "direction", "created_ts", "approaches", "zone_lo", "zone_hi")

    def __init__(self, price: float, direction: str, ts: int):
        self.price = price
        self.touches = 1
        self.last_touch_ts = ts
        self.created_ts = ts
        self.direction = direction  # "support" | "resistance"
        self.approaches = 0
        self.zone_lo = price
        self.zone_hi = price


class SymbolLevels:
    """Уровни одной монеты: кластеризация экстремумов минуток + живые подходы."""

    def __init__(self, exchange: str, symbol: str, atr_mult: float = 0.5, max_levels: int = 12):
        self.exchange = exchange
        self.symbol = symbol
        self.atr_mult = atr_mult
        self.max_levels = max_levels
        self.levels: List[Level] = []
        self.tolerance: float = 0.0
        self.last_price: float = 0.0
        self.raw_klines: List[List] = []
        self._current_approach: Optional[Tuple[int, str, int]] = None  # (idx, side, start_ms)

    # ---------- построение из минутных свечей ----------

    def build_from_klines(self, klines: List[List]) -> None:
        """klines: Binance 1m формат [[open_time, o, h, l, c, ...], ...] (по возрастанию)."""
        if not klines:
            return
        self.raw_klines = klines
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        closes = [float(k[4]) for k in klines]
        times = [int(k[0]) for k in klines]
        self.tolerance = self._adaptive_tolerance(highs, lows, closes)

        swings: List[Tuple[int, float, str]] = []  # (ts, price, "high"|"low")
        for i in range(2, len(klines) - 2):
            if highs[i] >= highs[i - 1] and highs[i] >= highs[i - 2] and highs[i] >= highs[i + 1] and highs[i] >= highs[i + 2]:
                swings.append((times[i], highs[i], "high"))
            if lows[i] <= lows[i - 1] and lows[i] <= lows[i - 2] and lows[i] <= lows[i + 1] and lows[i] <= lows[i + 2]:
                swings.append((times[i], lows[i], "low"))

        self._cluster_swings(swings, closes[-1] if closes else 0.0)
        self.last_price = closes[-1] if closes else 0.0

    def _adaptive_tolerance(self, highs: List[float], lows: List[float], closes: List[float]) -> float:
        """0.5 * ATR(14) по часовому сэмплингу минуток (грубая оценка волатильности)."""
        if len(closes) < 30:
            return 0.0015 * (closes[-1] if closes else 1.0)
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        atr = statistics.median(trs[-14 * 60:]) if len(trs) >= 14 else statistics.median(trs)
        price = closes[-1]
        tol = self.atr_mult * atr
        # ограничение: 0.05% .. 0.8% от цены
        tol = max(min(tol, 0.008 * price), 0.0005 * price)
        return tol

    def _cluster_swings(self, swings: List[Tuple[int, float, str]], ref_price: float) -> None:
        """Зонная кластеризация: зона = [min, max] членов; одно касание = один визит
        (серия свингов без выхода из зоны считается одним касанием)."""
        self.levels = []
        if not swings:
            return
        zones: List[dict] = []  # {"lo","hi","visits":[ts,...],"price_sum","n","last_ts","kinds"}

        def new_zone(ts, price, kind):
            return {"lo": price, "hi": price, "visits": [ts], "sum": price, "n": 1,
                    "last_ts": ts, "kinds": {kind}}

        def in_zone(z, price):
            return z["lo"] - self.tolerance <= price <= z["hi"] + self.tolerance

        for ts, price, kind in swings:
            target = None
            for z in zones:
                if in_zone(z, price):
                    target = z
                    break
            if target is None:
                zones.append(new_zone(ts, price, kind))
                continue
            # внутри зоны: визит, если после прошлого свинга цена уходила из зоны
            if ts - target["last_ts"] > 15 * 60000:
                target["visits"].append(ts)
                target["sum"] += price
                target["n"] += 1
            target["lo"] = min(target["lo"], price)
            target["hi"] = max(target["hi"], price)
            target["last_ts"] = max(target["last_ts"], ts)
            target["kinds"].add(kind)

        # слияние пересекающихся зон
        merged = True
        while merged:
            merged = False
            zones.sort(key=lambda z: z["lo"])
            for i in range(len(zones) - 1):
                a, b = zones[i], zones[i + 1]
                if a["hi"] + self.tolerance >= b["lo"] - self.tolerance:
                    a["lo"] = min(a["lo"], b["lo"])
                    a["hi"] = max(a["hi"], b["hi"])
                    a["sum"] += b["sum"]
                    a["n"] += b["n"]
                    a["visits"] += b["visits"]
                    a["last_ts"] = max(a["last_ts"], b["last_ts"])
                    a["kinds"] |= b["kinds"]
                    del zones[i + 1]
                    merged = True
                    break

        zones.sort(key=lambda z: len(z["visits"]), reverse=True)
        for z in zones[: self.max_levels]:
            if len(z["visits"]) < 2:
                continue
            price = z["sum"] / z["n"]
            if price < ref_price:
                direction = "support"
            elif price > ref_price:
                direction = "resistance"
            else:
                direction = "high" if "high" in z["kinds"] else "support"
            lvl = Level(price, direction, max(z["visits"]))
            lvl.touches = len(z["visits"])
            lvl.approaches = len(z["visits"]) - 1
            lvl.zone_lo = z["lo"]
            lvl.zone_hi = z["hi"]
            self.levels.append(lvl)
        self.levels.sort(key=lambda l: l.price, reverse=True)

    # ---------- живой трекинг подходов ----------

    def on_tick(self, ts_ms: int, price: float) -> Optional[Tuple[int, str, int, str]]:
        """Возвращает (idx, direction, approach_no, side) при НАЧАЛЕ нового подхода."""
        self.last_price = price
        self._grow_candle(ts_ms, price)
        if not self.levels:
            return None
        for idx, lvl in enumerate(self.levels):
            dist = abs(price - lvl.price)
            inside = dist <= self.tolerance
            key = (idx, lvl.direction)
            if inside:
                if self._current_approach is None or self._current_approach[:2] != key:
                    # новый подход к уровню
                    if self._current_approach is not None and self._current_approach[:2] == key:
                        return None
                    lvl.approaches += 1
                    lvl.last_touch_ts = ts_ms
                    side = "from_below" if price > lvl.price else "from_above"
                    self._current_approach = (idx, lvl.direction, ts_ms)
                    # подход №: базово touches (касания при формировании) + инкременты живых
                    return (idx, lvl.direction, lvl.touches + lvl.approaches - 1, side)
                # продолжение текущего подхода
                lvl.last_touch_ts = ts_ms
                return None
        # цена вне всех зон — подход завершён
        self._current_approach = None
        return None

    def _grow_candle(self, ts_ms: int, price: float) -> None:
        """Живое доращивание последней минутной свечи из ленты сделок."""
        if not self.raw_klines:
            return
        minute = (ts_ms // 60000) * 60000
        last = self.raw_klines[-1]
        if int(last[0]) == minute:
            h = float(last[2]); l = float(last[3])
            if price > h:
                last[2] = str(price)
            if price < l:
                last[3] = str(price)
            last[4] = str(price)  # close
        elif minute > int(last[0]):
            self.raw_klines.append([minute, str(price), str(price), str(price), str(price), "0"])
            if len(self.raw_klines) > 1500:
                del self.raw_klines[:200]

    def near_level(self, price: Optional[float] = None) -> Optional[Tuple[int, Level]]:
        if price is None:
            price = self.last_price
        if not price or not self.levels:
            return None
        best = None
        best_dist = None
        for idx, lvl in enumerate(self.levels):
            d = abs(price - lvl.price)
            if best_dist is None or d < best_dist:
                best_dist = d
                best = idx
        if best is not None and best_dist <= self.tolerance:
            return (best, self.levels[best])
        return None

    def significant(self, min_touches: int = 2) -> List[Level]:
        return [l for l in self.levels if l.touches >= min_touches]


class LevelsDetector:
    """Хаб уровней по символам. Кормится klines (24ч) и живыми тиками."""

    def __init__(self, atr_mult: float = 0.5):
        self.atr_mult = atr_mult
        self._by_key: Dict[Tuple[str, str], SymbolLevels] = {}

    def get(self, exchange: str, symbol: str) -> SymbolLevels:
        key = (exchange, symbol)
        sl = self._by_key.get(key)
        if sl is None:
            sl = SymbolLevels(exchange, symbol, atr_mult=self.atr_mult)
            self._by_key[key] = sl
        return sl

    def build(self, exchange: str, symbol: str, klines: List[List]) -> SymbolLevels:
        sl = self.get(exchange, symbol)
        sl.build_from_klines(klines)
        return sl

    def on_trade(self, exchange: str, symbol: str, ts_ms: int, price: float):
        key = (exchange, symbol)
        if key not in self._by_key:
            return None
        return self._by_key[key].on_tick(ts_ms, price)

    def count_significant(self, exchange: str, symbol: str, min_touches: int = 2) -> int:
        key = (exchange, symbol)
        sl = self._by_key.get(key)
        return len(sl.significant(min_touches)) if sl else 0
