import statistics
from collections import deque
from typing import Deque, Dict, Optional, Tuple

from core.models import BookSnapshot, Trade

WINDOW_MS = 300000
SPREAD_MAX_TICKS = 3.0
RESTORE_MS = 2000
TICK_SIZE_FALLBACK = 0.0


class MMDetector:
    """Детекция маркет-мейкера по стакану: двусторонность, стабильный спред,
    мгновенное восстановление уровней; анти-признак спуфинга.
    Для стратегии: holding_level(price) — держит ли ММ уровень при подходе."""

    def __init__(self, exchange: str, symbol: str):
        self.exchange = exchange
        self.symbol = symbol
        self.last_ts = 0
        self.tick_size = TICK_SIZE_FALLBACK
        self._events: Deque[dict] = deque()
        self._spreads: Deque[Tuple[int, float]] = deque()
        self._two_sided_ms = 0
        self._prev_two_sided = False
        self._levels_seen: Dict[float, dict] = {}
        self._restores: Deque[dict] = deque()
        self._vanish_no_restore = 0
        self._prev_mid = 0.0
        # следы инвентарного смещения: после buy ask отступает?
        self._post_buy_ask_shift: Deque[float] = deque(maxlen=50)
        self._post_sell_bid_shift: Deque[float] = deque(maxlen=50)
        self._last_trade_side = None
        self._mid_at_trade = 0.0

    def _estimate_tick(self, book: BookSnapshot) -> float:
        if self.tick_size > 0:
            return self.tick_size
        if len(book.bids) >= 2:
            ts = book.bids[0][0] - book.bids[1][0]
            if ts > 0:
                self.tick_size = ts
        elif len(book.asks) >= 2:
            ts = book.asks[1][0] - book.asks[0][0]
            if ts > 0:
                self.tick_size = ts
        return self.tick_size if self.tick_size > 0 else 0.0

    def on_book(self, book: BookSnapshot) -> None:
        now = book.ts_ms
        if now < self.last_ts:
            return
        dt = now - self.last_ts if self.last_ts else 0
        self.last_ts = now
        self._prune(now)
        self._estimate_tick(book)

        bid = book.best_bid()
        ask = book.best_ask()
        if not bid or not ask or ask[0] <= bid[0]:
            return
        spread = ask[0] - bid[0]
        tick = self._estimate_tick(book)
        self._spreads.append((now, spread))
        mid = (bid[0] + ask[0]) / 2.0

        two_sided = True  # снапшот стакана всегда двусторонний; смысл — устойчивость во времени
        if self._prev_two_sided and dt:
            self._two_sided_ms += dt
        self._prev_two_sided = two_sided

        # трекинг уровней: появление/исчезновение/восстановление
        seen_now = set()
        for side, levels in (("bid", book.bids), ("ask", book.asks)):
            for price, size in levels[:5]:
                seen_now.add((side, price))
                rec = self._levels_seen.get((side, price))
                if rec is None:
                    self._levels_seen[(side, price)] = {"first": now, "last": now, "size": size, "mid": mid}
                else:
                    rec["last"] = now
                    rec["size"] = max(rec["size"], size)
        for key in list(self._levels_seen.keys()):
            if key in seen_now:
                continue
            side, price = key
            rec = self._levels_seen.pop(key)
            life = rec["last"] - rec["first"]
            if life < 500:
                continue
            # спуфинг-кандидат: уровень снят при ПРИБЛИЖЕНИИ цены, но не пробит (иначе дрейф)
            if side == "bid":
                dist_seen = rec["mid"] - price
                dist_now = mid - price
                crossed = mid < price
            else:
                dist_seen = price - rec["mid"]
                dist_now = price - mid
                crossed = mid > price
            approaching = (dist_now < dist_seen) and not crossed
            self._events.append({"ts": now, "type": "vanish", "side": side, "price": price,
                                 "life": life, "mid": rec["mid"], "mid_now": mid,
                                 "approaching": approaching})
            self._restores.append({"ts": now, "side": side, "price": price, "restored": False,
                                   "approaching": approaching})

        for r in self._restores:
            if r["restored"]:
                continue
            if (r["side"], r["price"]) in seen_now:
                r["restored"] = True
                r["restore_ms"] = now - r["ts"]
                self._events.append({"ts": now, "type": "restore", "side": r["side"],
                                     "price": r["price"], "restore_ms": r["restore_ms"]})
            elif now - r["ts"] > RESTORE_MS:
                r["restored"] = "slow"  # не вернулся за RESTORE_MS
                if r.get("approaching"):
                    self._events.append({"ts": now, "type": "spoof", "side": r["side"], "price": r["price"]})

        # инвентарное смещение: после сделки mid дрейфует в сторону инвентаря
        if self._last_trade_side is not None and self._mid_at_trade:
            shift = mid - self._mid_at_trade
            if self._last_trade_side:  # buy
                self._post_buy_ask_shift.append(shift)
            else:
                self._post_sell_bid_shift.append(shift)
            self._last_trade_side = None  # одна книга — одна оценка сдвига

        self._prev_mid = mid

    def on_trade(self, trade: Trade) -> None:
        self._last_trade_side = trade.is_buy
        if self._prev_mid:
            self._mid_at_trade = self._prev_mid

    def _prune(self, now: int) -> None:
        cutoff = now - WINDOW_MS
        while self._spreads and self._spreads[0][0] < cutoff:
            self._spreads.popleft()
        while self._events and self._events[0]["ts"] < cutoff:
            self._events.popleft()
        while self._restores and self._restores[0]["ts"] < cutoff:
            self._restores.popleft()

    # ---------- оценка ----------

    def state(self) -> Tuple[int, str]:
        now = self.last_ts
        self._prune(now)
        if not self._spreads:
            return 0, ""

        score = 0
        parts = []

        # 1) стабильный узкий спред
        spreads = [s for _, s in self._spreads]
        tick = self.tick_size or (statistics.median(spreads) or 1.0)
        med_spread = statistics.median(spreads)
        spread_ticks = med_spread / tick if tick else 99
        if len(spreads) >= 20 and spread_ticks <= SPREAD_MAX_TICKS:
            q1 = sorted(spreads)[len(spreads) // 4]
            q3 = sorted(spreads)[3 * len(spreads) // 4]
            iqr = (q3 - q1) / med_spread if med_spread else 1.0
            if iqr < 0.5:
                score += 20
                parts.append("спред {:.0f} тика стаб.".format(spread_ticks))
            else:
                score += 10
                parts.append("спред {:.0f} тика".format(spread_ticks))

        # 2) двусторонность (постоянство присутствия стакана)
        span = min(now - (self._spreads[0][0] if self._spreads else now), WINDOW_MS)
        if span > 30000 and self._two_sided_ms / max(span, 1) > 0.9:
            score += 25
            parts.append("двустор.")

        # 3) восстановления уровней
        restores = [e for e in self._events if e["type"] == "restore"]
        vanishes = [e for e in self._events if e["type"] == "vanish"]
        fast = [r for r in restores if r.get("restore_ms", 99999) <= RESTORE_MS]
        if vanishes:
            ratio = len(fast) / max(len(vanishes), 1)
            if ratio >= 0.6 and len(vanishes) >= 2:
                score += 25
                avg_ms = statistics.median([r["restore_ms"] for r in fast]) if fast else 0
                parts.append("восст. {:.1f}с".format(avg_ms / 1000))
            elif ratio >= 0.3:
                score += 12
        else:
            # рынок без снятий: стакан стабилен — плюс, но слабый
            score += 8

        # 4) инвентарное смещение
        if len(self._post_buy_ask_shift) >= 10 and len(self._post_sell_bid_shift) >= 10:
            b = statistics.mean(self._post_buy_ask_shift)
            s = statistics.mean(self._post_sell_bid_shift)
            if b < -2 * tick or s > 2 * tick:
                score += 15
                parts.append("инвент.смещение")

        # анти-признак: снятия без БЫСТРОГО восстановления (спуфинг) — обнуляет MM-score
        spoofs = [e for e in self._events if e["type"] == "spoof"]
        if len(spoofs) >= 2:
            score = 0
            parts.append("спуфинг!")

        return max(0, min(100, score)), "; ".join(parts)

    def holding_level(self, level_price: float, tolerance: float) -> Tuple[bool, str]:
        """Держит ли ММ уровень: плотность на уровне стоит/растёт при подходе.
        True если у уровня устойчивый bid (для поддержки) или ask (для сопротивления)."""
        now = self.last_ts
        self._prune(now)
        vanishes = [e for e in self._events if e["type"] == "vanish"
                    and abs(e["price"] - level_price) <= tolerance]
        restores = [r for r in self._restores if abs(r["price"] - level_price) <= tolerance and r.get("restored")]
        recent_vanish = [v for v in vanishes if now - v["ts"] <= 60000]
        # уровень снимали и не восстановили -> не держит
        if recent_vanish and not any(r["ts"] > recent_vanish[-1]["ts"] for r in restores):
            return False, "уровень снят без восстановления"
        # уровень восстанавливали быстро -> держит
        if restores:
            return True, "уровень восстанавливается ({})".format(len(restores))
        # уровень стоит долго без снятий
        for (side, price), rec in self._levels_seen.items():
            if abs(price - level_price) <= tolerance and now - rec["first"] >= 10000:
                return True, "плотность стоит {:.0f}с".format((now - rec["first"]) / 1000)
        return False, "нет данных по уровню"
