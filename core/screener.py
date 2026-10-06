import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

from core.alerts import Alerts
from core.backfill import run_backfill
from core.connectors.base import BaseConnector
from core.connectors.binance import BinanceConnector
from core.connectors.bitget import BitgetConnector
from core.connectors.gate import GateConnector
from core.connectors.mexc import MexcConnector
from core.detectors.micro import MicroDetector
from core.detectors.robots import RobotDetector
from core.history import History
from core.models import Trade
from core.radar import Radar
from core.scanner import Scanner
from core.store import MarketStore

log = logging.getLogger(__name__)

CONNECTORS = {
    "binance": BinanceConnector,
    "mexc": MexcConnector,
    "bitget": BitgetConnector,
    "gate": GateConnector,
}


class Screener:
    def __init__(self, config: dict):
        self.config = config
        monitor = config.get("monitor", {})
        self.mode = monitor.get("mode", "top")
        self.pool_max = int(monitor.get("pool_max", 200))
        self.demote_after_min = int(monitor.get("demote_after_min", 10))
        self.top_n = int(monitor.get("top_n", 15))
        hist_cfg = config.get("history", {})
        self.auto_record_robots = bool(hist_cfg.get("auto_record_robots", True))
        self.window_s = int(config.get("analysis_window_min", 10)) * 60

        self.store = MarketStore(self.window_s)
        self.alerts = Alerts(
            enabled=bool(config.get("alerts", {}).get("enabled", True)),
            sound=bool(config.get("alerts", {}).get("sound", True)),
        )
        self.detector = RobotDetector(self.window_s, config.get("detector", {}), alert_cb=self._on_new_robot)
        self.micro = MicroDetector(config.get("micro", {}))
        self.radar = Radar(float(monitor.get("radar_threshold", 50)))
        self.scanner = Scanner(self.radar, config.get("scanner", {}))
        self.history = History(
            path=hist_cfg.get("path", "history.db"),
            retention_h=int(hist_cfg.get("retention_h", 24)),
        )
        for exch, sym in self.history.keys():
            self.store.get(exch, sym).drop_session()

        self.connectors: Dict[str, BaseConnector] = {}
        self.pool: Dict[Tuple[str, str], dict] = {}
        self.selected: Optional[str] = None
        self.new_robot_events: List[dict] = []
        self.event_rank: Dict[Tuple[str, str], int] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._maint_task: Optional[asyncio.Task] = None
        self._scan_task: Optional[asyncio.Task] = None
        self._http_session = None

    async def _get_json(self, url: str):
        import aiohttp

        if self._http_session is None:
            self._http_session = aiohttp.ClientSession()
        async with self._http_session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def start(self) -> None:
        self.loop = asyncio.get_event_loop()
        enabled = self.config.get("exchanges", ["binance", "mexc", "bitget", "gate"])
        tasks = []
        for name in enabled:
            cls = CONNECTORS.get(name)
            if cls is None:
                log.warning("unknown exchange: %s", name)
                continue
            conn = cls(self._on_trade, self._on_book)
            self.connectors[name] = conn
            tasks.append(self._start_connector(conn))
        self._maint_task = self.loop.create_task(self._maintenance_loop())
        self._scan_task = self.loop.create_task(self.scanner.run_loop(self._get_json))
        await asyncio.gather(*tasks)

    async def _start_connector(self, conn: BaseConnector) -> None:
        try:
            if self.mode == "all":
                symbols = await conn.discover_all()
            else:
                symbols = await conn.discover_top(self.top_n)
            log.info("[%s] %d symbols", conn.name, len(symbols))
        except Exception as e:
            log.error("[%s] discovery failed: %s: %s", conn.name, type(e).__name__, e)
            return
        top = set(await self._top_symbols(conn))
        for s in top:
            self.pool[(conn.name, s)] = {"reason": "top", "since": int(time.time() * 1000)}
        await conn.run(symbols)

    async def _top_symbols(self, conn: BaseConnector) -> List[str]:
        try:
            return await conn.discover_top(self.top_n)
        except Exception:
            return []

    async def _maintenance_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(2)
                self._promote_from_scanner()
                self._promote_from_radar()
                self._demote_idle()
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("maintenance tick failed")

    def _evict_weakest_radar(self) -> bool:
        """Выгнать самого слабого радарного кандидата без робота. True если освободили слот."""
        weakest = None
        weakest_score = 1 << 30
        robots = {(sig.exchange, sig.symbol) for sig in self.detector.all_signals()}
        for key, entry in self.pool.items():
            if entry["reason"] != "radar" or key in robots:
                continue
            s = self.radar.score(key[0], key[1])
            if s < weakest_score:
                weakest_score = s
                weakest = key
        if weakest is None:
            return False
        del self.pool[weakest]
        self.store.get(weakest[0], weakest[1]).drop_session()
        log.info("pool - %s:%s (evicted for scan)", weakest[0], weakest[1])
        return True

    def _promote_from_scanner(self) -> None:
        for key in self.scanner.candidates()[:50]:
            if key in self.pool:
                continue
            if len(self.pool) >= self.pool_max:
                if not self._evict_weakest_radar():
                    break
            self.pool[key] = {"reason": "scan", "since": int(time.time() * 1000)}
            log.info("pool + %s:%s (scan rvol %.1f)", key[0], key[1], self.scanner.rvol(*key))

    def _in_scope(self, exchange: str, symbol: str) -> bool:
        key = (exchange, symbol)
        if key in self.pool:
            return True
        if self.selected and self.selected == f"{exchange}:{symbol}":
            return True
        return False

    def _promote_from_radar(self) -> None:
        for (exch, sym), susp in self.radar.candidates():
            key = (exch, sym)
            if key in self.pool:
                continue
            if len(self.pool) < self.pool_max:
                self.pool[key] = {"reason": "radar", "since": int(time.time() * 1000)}
                log.info("pool + %s:%s (radar %d)", exch, sym, susp)

    def _demote_idle(self) -> None:
        now_ms = int(time.time() * 1000)
        for key, entry in list(self.pool.items()):
            if entry["reason"] in ("top", "user"):
                continue
            exch, sym = key
            has_robot = any(
                sig.exchange == exch and sig.symbol == sym for sig in self.detector.all_signals()
            )
            if has_robot:
                continue
            if entry["reason"] == "scan":
                if not self.scanner.matches(exch, sym):
                    del self.pool[key]
                    self.store.get(exch, sym).drop_session()
                    log.info("pool - %s:%s (scan filter expired)", exch, sym)
                continue
            if self.radar.score(exch, sym) < 10 and now_ms - entry["since"] > self.demote_after_min * 60000:
                del self.pool[key]
                self.store.get(exch, sym).drop_session()
                log.info("pool - %s:%s (idle)", exch, sym)

    async def _on_trade(self, exchange: str, symbol: str, ts_ms: int, price: float, size: float, is_buy: bool) -> None:
        self.radar.on_trade(exchange, symbol, ts_ms, size)
        if self.history is not None:
            self.history.record(exchange, symbol, ts_ms, price, size, is_buy, False)
        if not self._in_scope(exchange, symbol):
            return
        trade = Trade.of(exchange, symbol, ts_ms, price, size, is_buy)
        self.store.add_trade(trade)
        self.detector.on_trade(trade)
        self.micro.on_trade(trade)

    async def _on_book(self, exchange: str, symbol: str, book) -> None:
        if not self._in_scope(exchange, symbol):
            return
        self.store.add_book(exchange, symbol, book)
        self.micro.on_book(exchange, symbol, book)

    def _on_new_robot(self, signal) -> None:
        row = signal.to_row()
        self.new_robot_events.append(row)
        if len(self.new_robot_events) > 50:
            del self.new_robot_events[:-50]
        self._rank_event(signal.exchange, signal.symbol)
        if self.auto_record_robots:
            self.history.add_key(signal.exchange, signal.symbol)

    def _rank_event(self, exchange: str, symbol: str) -> None:
        self.event_rank[(exchange.lower(), symbol)] = int(time.time() * 1000)

    def select(self, key: Optional[str]) -> None:
        if key == self.selected:
            return
        self.selected = key
        if key is None or self.loop is None:
            return
        exchange, symbol = key.split(":", 1)
        self.history.add_key(exchange, symbol)
        pool_key = (exchange, symbol)
        if pool_key not in self.pool:
            self.pool[pool_key] = {"reason": "user", "since": int(time.time() * 1000)}
        for name, conn in self.connectors.items():
            target = symbol if name == exchange else None
            asyncio.run_coroutine_threadsafe(conn.set_depth_symbol(target), self.loop)
        self.loop.create_task(self._backfill_selected(exchange, symbol))

    async def _backfill_selected(self, exchange: str, symbol: str) -> None:
        try:
            earliest = self.history.earliest_ts(exchange, symbol)
            now_ms = int(time.time() * 1000)
            since = now_ms - int(self.config.get("history", {}).get("retention_h", 24)) * 3600 * 1000
            if earliest is None or earliest > since + 60000:
                added = await run_backfill(
                    self.connectors.get(exchange), exchange, symbol,
                    max(since, now_ms - 24 * 3600 * 1000), self.history,
                )
                if added:
                    log.info("backfill %s:%s +%d trades", exchange, symbol, added)
        except Exception:
            log.exception("backfill failed for %s:%s", exchange, symbol)

    def stats(self) -> dict:
        rs = self.radar.stats()
        return {
            "monitored": rs["symbols"],
            "active": rs["active"],
            "pool": len(self.pool),
            "robots": len(self.detector.all_signals()),
            "scan": self.scanner.match_count(),
        }

    def symbol_list(self) -> List[dict]:
        out = []
        for exch, symbols in self.store.symbols_by_exchange().items():
            for s in symbols:
                out.append({"key": f"{exch}:{s}", "exchange": exch.upper(), "symbol": s})
        return out

    def screener_rows(self) -> List[dict]:
        self._drain_micro_alerts()
        rows = []
        index = {}
        for sig in self.detector.all_signals():
            row = sig.to_row()
            row.update({
                "hft": 0, "hft_info": "", "spoof": 0, "spoof_info": "",
                "iceberg": 0, "iceberg_info": "", "radar": self.radar.score(sig.exchange, sig.symbol),
            })
            index[(sig.exchange, sig.symbol)] = row
            rows.append(row)
        for key, ms in self.micro.all_states().items():
            row = index.get(key)
            if row is None:
                exchange, symbol = key
                row = {
                    "key": "{}:{}:micro".format(exchange, symbol),
                    "exchange": exchange.upper(),
                    "symbol": symbol,
                    "tick_ms": 0,
                    "prints": 0,
                    "strength": 0,
                    "direction": "—",
                    "score": max(ms.hft, ms.spoof, ms.iceberg),
                    "regularity": 0,
                    "reaction": "—",
                    "duration_s": 0,
                    "first_ms": 0,
                    "last_ms": 0,
                    "radar": self.radar.score(exchange, symbol),
                }
                index[key] = row
                rows.append(row)
            ms.merge_into(row)
        for exch, sym, susp in self.radar.top_suspicious(40):
            key = (exch, sym)
            if key in index:
                index[key]["radar"] = susp
                continue
            if susp < 10:
                continue
            row = {
                "key": "{}:{}:radar".format(exch, sym),
                "exchange": exch.upper(),
                "symbol": sym,
                "tick_ms": 0,
                "prints": 0,
                "strength": 0,
                "direction": "—",
                "score": susp,
                "regularity": 0,
                "reaction": "—",
                "duration_s": 0,
                "first_ms": 0,
                "last_ms": 0,
                "hft": 0, "hft_info": "", "spoof": 0, "spoof_info": "", "iceberg": 0, "iceberg_info": "",
                "radar": susp,
            }
            index[key] = row
            rows.append(row)
        scan_added = 0
        for exch, sym in self.scanner.candidates()[:50]:
            key = (exch, sym)
            if key in index:
                continue
            scan_added += 1
            rows.append({
                "key": "{}:{}:scan".format(exch, sym),
                "exchange": exch.upper(),
                "symbol": sym,
                "tick_ms": 0,
                "prints": 0,
                "strength": 0,
                "direction": "—",
                "score": min(int(self.scanner.rvol(exch, sym)), 100),
                "regularity": 0,
                "reaction": "—",
                "duration_s": 0,
                "first_ms": 0,
                "last_ms": 0,
                "hft": 0, "hft_info": "", "spoof": 0, "spoof_info": "", "iceberg": 0, "iceberg_info": "",
                "radar": self.radar.score(exch, sym),
            })
            if scan_added >= 50:
                break
        for row in rows:
            key = (row["exchange"].lower(), row["symbol"])
            row.update(self.scanner.match_details(*key))
        now_ms = int(time.time() * 1000)

        def sort_key(r):
            key = (r["exchange"].lower(), r["symbol"])
            rank = self.event_rank.get(key, 0)
            fresh = 1 if rank and (now_ms - rank) < 60000 else 0
            has_robot = 1 if r.get("prints") else 0
            matched = 1 if r.get("match") else 0
            return (-fresh, -matched, -has_robot, -rank, -r["score"])

        rows.sort(key=sort_key)
        return rows[:300]

    def _drain_micro_alerts(self) -> None:
        for e in self.micro.new_spoof_events():
            self._rank_event(e.get("exchange", ""), e.get("symbol", ""))
            self.alerts.notify(
                "Спуфинг: {} {}".format(e.get("price"), e.get("side", "")),
                "снята стена {:.4g}".format(e.get("size", 0)),
            )
        for e in self.micro.new_iceberg_events():
            self._rank_event(e.get("exchange", ""), e.get("symbol", ""))
            self.alerts.notify(
                "Айсберг @ {:.8g}".format(e.get("price", 0)),
                "объём ~{:.4g}".format(e.get("volume", 0)),
            )

    def take_robot_events(self) -> List[dict]:
        events = self.new_robot_events
        self.new_robot_events = []
        return events

    def get_history(self, exchange: str, symbol: str, before_ms: int, limit: int = 8000) -> List[dict]:
        return self.history.query(exchange, symbol, before_ms, limit)

    def indicator_data(self, key: Optional[str], after_ms: int = 0) -> dict:
        if key is None:
            key = self.selected
        if key is None:
            return {"trades": [], "robots": [], "price": 0, "full": True}
        exchange, symbol = key.split(":", 1)
        st = self.store.get(exchange, symbol)
        if after_ms > 0:
            new_trades = [t for t in st.session[-8000:] if t.ts_ms > after_ms][:5000]
        else:
            new_trades = st.session[-5000:]
        ms = self.micro.state(exchange, symbol)
        data = {
            "key": key,
            "full": after_ms <= 0,
            "price": st.last_price,
            "bid": st.last_book.best_bid()[0] if st.last_book and st.last_book.best_bid() else None,
            "ask": st.last_book.best_ask()[0] if st.last_book and st.last_book.best_ask() else None,
            "trades": [
                {"t": t.ts_ms, "s": t.size, "b": t.is_buy, "r": t.is_robot}
                for t in new_trades
            ],
            "robots": [sig.to_row() for sig in self.detector.all_signals() if sig.exchange == exchange and sig.symbol == symbol],
            "micro": {
                "hft": ms.hft, "hft_info": ms.hft_info,
                "spoof": ms.spoof, "spoof_info": ms.spoof_info,
                "iceberg": ms.iceberg, "iceberg_info": ms.iceberg_info,
            },
        }
        return data
