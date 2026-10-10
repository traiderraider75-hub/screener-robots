import logging
import time
from typing import Dict, List, Optional, Tuple

from core.detectors.levels import LevelsDetector, SymbolLevels
from core.detectors.mm import MMDetector

log = logging.getLogger(__name__)


class StrategyEngine:
    """Стратегия «3-й подход к уровню».

    Для ★-монет (scanner.matches) следит за подходами цены к уровням с 2+ касаниями.
    На 3-м (и последующих) подходе формирует вердикт:
      - роботы: есть ли и в какую сторону (против движения / по движению / нет)
      - ММ: держит ли уровень
      - новости: сентимент за окно (label от NewsWatcher или None)
      - ИТОГ: ПЕРЕСЕЧЕНИЕ / ОТКАТ / НЕОПРЕДЕЛЕННОСТЬ
    """

    APPROACH_GAP_MS = 4 * 3600 * 1000  # подходы к уровню в пределах 4ч — серия

    def __init__(self, screener, cfg: Optional[dict] = None):
        cfg = cfg or {}
        self.screener = screener
        self.levels = LevelsDetector(float(cfg.get("atr_mult", 0.5)))
        self.mm_by_key: Dict[Tuple[str, str], MMDetector] = {}
        self.news = None  # назначается снаружи (NewsWatcher)
        self.mm_score_min = int(cfg.get("mm_score_min", 30))
        self.max_signals = int(cfg.get("max_signals", 100))
        self.signals: List[dict] = []
        self.last_signals: List[dict] = []
        self._built: set = set()

    # ---------- сборка данных ----------

    async def ensure_levels(self, exchange: str, symbol: str, get_json, klines_url: str) -> None:
        key = (exchange, symbol)
        if key in self._built:
            return
        try:
            import asyncio as _asyncio

            url = klines_url.format(sym=symbol, limit=1440)
            klines = await get_json(url)
            loop = _asyncio.get_event_loop()
            # тяжёлый кластеринг — в отдельном потоке, чтобы не блокировать event loop
            await loop.run_in_executor(None, self.levels.build, exchange, symbol, klines)
            self._built.add(key)
            log.info("levels built %s:%s (%d levels)", exchange, symbol, len(self.levels.get(exchange, symbol).levels))
        except Exception as e:
            log.warning("levels build failed %s:%s: %s: %s", exchange, symbol, type(e).__name__, e)

    def mm(self, exchange: str, symbol: str) -> MMDetector:
        key = (exchange, symbol)
        d = self.mm_by_key.get(key)
        if d is None:
            d = MMDetector(exchange, symbol)
            self.mm_by_key[key] = d
        return d

    # ---------- поток данных ----------

    def on_trade(self, exchange: str, symbol: str, ts_ms: int, price: float) -> None:
        """Вызывается на каждой сделке ★-монеты (из Screener._on_trade)."""
        if not self.screener.scanner.matches(exchange, symbol):
            return
        approach = self.levels.on_trade(exchange, symbol, ts_ms, price)
        if approach is None:
            return
        idx, direction, approach_no, side = approach
        if approach_no < 3:
            return
        self._make_verdict(exchange, symbol, idx, direction, approach_no, side, ts_ms, price)

    def _robot_relation(self, exchange: str, symbol: str, side: str) -> Tuple[str, Optional[dict]]:
        """ ('against'|'with'|'none', robot_row) относительно направления подхода. """
        sigs = [s for s in self.screener.detector.all_signals()
                if s.exchange == exchange and s.symbol == symbol]
        if not sigs:
            return "none", None
        best = max(sigs, key=lambda s: s.score)
        # подход вниз (from_above) — движение вниз; робот SHORT = с движением
        approach_down = side == "from_above"
        robot_down = best.direction == "SHORT"
        if best.direction == "—" or (best.buy_volume == 0 and best.sell_volume == 0):
            return "none", best.to_row()
        if approach_down == robot_down:
            return "with", best.to_row()
        return "against", best.to_row()

    def _news_label(self, exchange: str, symbol: str) -> Optional[str]:
        if self.news is None:
            return None
        try:
            return self.news.sentiment(symbol)[0]
        except Exception:
            return None

    def _make_verdict(self, exchange: str, symbol: str, idx: int, direction: str,
                      approach_no: int, side: str, ts_ms: int, price: float) -> None:
        sl = self.levels.get(exchange, symbol)
        lvl = sl.levels[idx] if 0 <= idx < len(sl.levels) else None
        mm = self.mm(exchange, symbol)
        mm_score, mm_info = mm.state()
        mm_holds, mm_why = mm.holding_level(lvl.price, sl.tolerance) if lvl else (False, "нет уровня")

        robot_rel, robot_row = self._robot_relation(exchange, symbol, side)
        news = self._news_label(exchange, symbol)

        # направление подхода: from_above = цена идёт вниз к поддержке;
        # пробой = движение продолжится в сторону подхода
        approach_down = side == "from_above"

        # факторы
        f_robot_with = robot_rel == "with"
        f_robot_against = robot_rel == "against"
        f_mm_holds = mm_holds and mm_score >= self.mm_score_min
        f_news_against = (news == "neg" and not approach_down) or (news == "pos" and approach_down)
        f_news_with = (news == "pos" and not approach_down) or (news == "neg" and approach_down)

        if f_robot_with and not f_mm_holds:
            verdict = "BREAKOUT"
            why = ["робот в сторону движения", "ММ не держит уровень"]
        elif f_mm_holds or f_robot_against:
            verdict = "REJECT"
            why = []
            if f_mm_holds:
                why.append("ММ держит уровень")
            if f_robot_against:
                why.append("робот против движения")
        else:
            verdict = "UNCERTAIN"
            why = ["роботов нет" if robot_rel == "none" else "робот без явного направления",
                   "ММ не держит уровень" if not f_mm_holds else "ММ держит"]

        if f_news_against:
            why.append("новости против движения")
        elif f_news_with:
            why.append("новости поддерживают движение")

        signal = {
            "ts": ts_ms,
            "exchange": exchange.upper(),
            "symbol": symbol,
            "level": round(lvl.price, 8) if lvl else None,
            "level_direction": direction,
            "approach_no": approach_no,
            "side": side,
            "price": price,
            "verdict": verdict,
            "why": why,
            "robot": robot_row,
            "mm_score": mm_score,
            "mm_info": mm_info,
            "mm_holds": mm_holds,
            "news": news,
        }
        self.last_signals.append(signal)
        self.signals.append(signal)
        if len(self.signals) > self.max_signals:
            self.signals = self.signals[-self.max_signals:]
        log.info("VERDICT %s:%s approach#%d level %.8g -> %s (%s)",
                 exchange, symbol, approach_no, lvl.price if lvl else 0, verdict, "; ".join(why))
        try:
            label = "ПРОБОЙ" if verdict == "BREAKOUT" else "ОТБОЙ" if verdict == "REJECT" else "НЕОПРЕД."
            self.screener.alerts.notify(
                "3-й подход {}: {} {}".format(symbol, exchange.upper(), label),
                "; ".join(why)[:120],
            )
        except Exception:
            pass

    # ---------- API для UI ----------

    def take_signals(self) -> List[dict]:
        out = self.last_signals
        self.last_signals = []
        return out

    def all_signals(self) -> List[dict]:
        return list(reversed(self.signals))

    def table_fields(self, exchange: str, symbol: str, full: bool = True) -> dict:
        key = (exchange, symbol)
        sl = self.levels.get(exchange, symbol)
        n_levels = len(sl.significant(2))
        in_approach = None
        holds = None
        mm = self.mm_by_key.get(key)
        if full and sl._current_approach is not None:
            idx, direction, start = sl._current_approach
            lvl = sl.levels[idx] if 0 <= idx < len(sl.levels) else None
            if lvl:
                in_approach = {"no": lvl.touches + lvl.approaches - 1, "level": round(lvl.price, 8),
                               "direction": direction}
            if mm:
                near = sl.near_level()
                if near:
                    _idx, lvl2 = near
                    holds, _why = mm.holding_level(lvl2.price, sl.tolerance)
        mm_score, mm_info = mm.state() if mm else (0, "")
        news = self._news_label(exchange, symbol) if full else None
        return {
            "levels": n_levels,
            "approach": in_approach,
            "mm_score": mm_score,
            "mm_holds": holds,
            "news": news,
        }

    def verdict_panel(self, exchange: str, symbol: str) -> dict:
        tf = self.table_fields(exchange, symbol)
        key = (exchange, symbol)
        recent = [s for s in self.signals[-5:] if (s["exchange"].lower(), s["symbol"]) == key]
        return {"fields": tf, "recent": recent}
