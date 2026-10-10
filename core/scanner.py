import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

from core.radar import Radar

log = logging.getLogger(__name__)

TICKER_URLS = {
    "binance": "https://fapi.binance.com/fapi/v1/ticker/24hr",
    "mexc": "https://contract.mexc.com/api/v1/contract/ticker",
    "bitget": "https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES",
    "gate": "https://api.gateio.ws/api/v4/futures/usdt/tickers",
}

KLINES_URLS = {
    "binance": ("https://fapi.binance.com/fapi/v1/klines"
                "?symbol={sym}&interval=1h&limit={limit}"),
}


class Scanner:
    """Фильтр: топ роста 24ч + количество сделок 24ч (Binance) + RVOL 1ч >= порога."""

    def __init__(self, radar: Radar, cfg: Optional[dict] = None):
        cfg = cfg or {}
        self.radar = radar
        self.poll_s = int(cfg.get("poll_s", 30))
        self.top_gainers_n = int(cfg.get("top_gainers_n", 50))
        self.min_trades_24h = int(cfg.get("min_trades_24h", 5000))
        self.rvol_threshold = float(cfg.get("rvol_threshold", 5.0))
        self.baseline_hours = int(cfg.get("baseline_hours", 24))
        self.binance_priority = bool(cfg.get("binance_priority", True))

        self._tickers: Dict[Tuple[str, str], dict] = {}
        self._gainers: Dict[str, List[str]] = {}
        self._bootstrapped: set = set()
        self._klines_budget = 400
        self.allowed_symbols: set = set()  # {(exch, sym)} вечных фьючерсов; пусто = без фильтра

    def _is_perp(self, exch: str, sym: str) -> bool:
        if not self.allowed_symbols:
            return True
        return (exch, sym) in self.allowed_symbols

    async def run_loop(self, get_json) -> None:
        while True:
            try:
                await self._poll_tickers(get_json)
            except Exception:
                log.exception("scanner tickers poll failed")
            try:
                await self._bootstrap_baselines(get_json)
            except Exception:
                log.exception("scanner bootstrap failed")
            await asyncio.sleep(self.poll_s)

    async def _poll_tickers(self, get_json) -> None:
        now = int(time.time() * 1000)
        raw: Dict[Tuple[str, str], dict] = {}

        data = await get_json(TICKER_URLS["binance"])
        for t in data:
            if t["symbol"].endswith("USDT") and "_" not in t["symbol"] and self._is_perp("binance", t["symbol"]):
                raw[("binance", t["symbol"])] = {
                    "growth24": float(t["priceChangePercent"]),
                    "count24": int(t["count"]),
                    "quote_vol24": float(t["quoteVolume"]),
                    "ts": now,
                }

        try:
            data = await get_json(TICKER_URLS["mexc"])
            for t in data.get("data", []):
                if t["symbol"].endswith("_USDT") and self._is_perp("mexc", t["symbol"]):
                    raw[("mexc", t["symbol"])] = {
                        "growth24": float(t.get("riseFallRate") or 0) * 100.0,
                        "count24": None,
                        "quote_vol24": float(t.get("amount24") or 0),
                        "ts": now,
                    }
        except Exception as e:
            log.warning("mexc tickers failed: %s", e)

        try:
            data = await get_json(TICKER_URLS["bitget"])
            for t in data.get("data", []):
                if t["symbol"].endswith("USDT") and self._is_perp("bitget", t["symbol"]):
                    raw[("bitget", t["symbol"])] = {
                        "growth24": float(t.get("change24h") or 0) * 100.0,
                        "count24": None,
                        "quote_vol24": float(t.get("usdtVolume") or 0),
                        "ts": now,
                    }
        except Exception as e:
            log.warning("bitget tickers failed: %s", e)

        try:
            data = await get_json(TICKER_URLS["gate"])
            for t in data:
                if t["contract"].endswith("_USDT") and self._is_perp("gate", t["contract"]):
                    raw[("gate", t["contract"])] = {
                        "growth24": float(t.get("change_percentage") or 0),
                        "count24": None,
                        "quote_vol24": float(t.get("volume_24h_quote") or 0),
                        "ts": now,
                    }
        except Exception as e:
            log.warning("gate tickers failed: %s", e)

        self._tickers = raw
        self._recompute_gainers()

    def _recompute_gainers(self) -> None:
        by_exchange: Dict[str, List[Tuple[float, str]]] = {}
        for (exch, sym), t in self._tickers.items():
            g = t.get("growth24")
            if g is None:
                continue
            by_exchange.setdefault(exch, []).append((g, sym))
        self._gainers = {}
        for exch, lst in by_exchange.items():
            lst.sort(reverse=True)
            self._gainers[exch] = [sym for g, sym in lst[:self.top_gainers_n] if g > 0]

    async def _bootstrap_baselines(self, get_json) -> None:
        """Часовые klines для базы RVOL: только Binance (остальные копят живьём)."""
        if self._klines_budget <= 0:
            return
        wanted: List[Tuple[str, str]] = []
        for exch in ("binance",):
            for sym in self._gainers.get(exch, []):
                wanted.append((exch, sym))
        for key in list(self.radar._cells.keys()):
            if key[0] == "binance" and key not in self._bootstrapped:
                wanted.append(key)
        for key in wanted:
            if self._klines_budget <= 0:
                return
            if key in self._bootstrapped or key not in self._tickers:
                continue
            try:
                url = KLINES_URLS["binance"].format(sym=key[1], limit=self.baseline_hours + 1)
                data = await get_json(url)
                self._klines_budget -= 1
                if not data:
                    self._bootstrapped.add(key)
                    continue
                hourly = [float(k[5]) for k in data[:-1]]
                current = float(data[-1][5])
                cell = self.radar.cell_or_new(key[0], key[1])
                cell.seed_hours(hourly, current, now_ms=int(time.time() * 1000))
                self._bootstrapped.add(key)
                await asyncio.sleep(0.3)
            except Exception as e:
                log.debug("klines bootstrap %s failed: %s", key, e)

    def growth24(self, exchange: str, symbol: str) -> Optional[float]:
        t = self._tickers.get((exchange, symbol))
        return t["growth24"] if t else None

    def count24(self, exchange: str, symbol: str) -> Optional[int]:
        t = self._tickers.get((exchange, symbol))
        return t["count24"] if t else None

    def rvol(self, exchange: str, symbol: str) -> float:
        cell = self.radar.cell(exchange, symbol)
        return cell.rvol() if cell else 0.0

    def is_top_gainer(self, exchange: str, symbol: str) -> bool:
        g = self.growth24(exchange, symbol)
        if g is None or g <= 0:
            return False
        return symbol in self._gainers.get(exchange, [])

    def matches(self, exchange: str, symbol: str) -> bool:
        if not self.is_top_gainer(exchange, symbol):
            return False
        if self.rvol(exchange, symbol) < self.rvol_threshold:
            return False
        if exchange == "binance":
            cnt = self.count24(exchange, symbol)
            if cnt is not None and cnt < self.min_trades_24h:
                return False
        return True

    def match_details(self, exchange: str, symbol: str) -> dict:
        return {
            "growth24": self.growth24(exchange, symbol),
            "count24": self.count24(exchange, symbol),
            "rvol": round(self.rvol(exchange, symbol), 1),
            "match": self.matches(exchange, symbol),
        }

    def candidates(self) -> List[Tuple[str, str]]:
        out = []
        for (exch, sym) in list(self._tickers.keys()):
            if self.matches(exch, sym):
                out.append((exch, sym))
        if self.binance_priority:
            out.sort(key=lambda k: (k[0] != "binance", -self.rvol(*k)))
        else:
            out.sort(key=lambda k: -self.rvol(*k))
        return out

    def match_count(self) -> int:
        n = 0
        for (exch, sym) in self._tickers:
            if self.matches(exch, sym):
                n += 1
        return n
