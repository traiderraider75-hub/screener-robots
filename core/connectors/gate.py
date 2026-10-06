import asyncio
import json
import logging
import time
from typing import List

from core.connectors.base import BaseConnector
from core.models import BookSnapshot

log = logging.getLogger(__name__)


class GateConnector(BaseConnector):
    name = "gate"
    ws_url = "wss://fx-ws.gateio.ws/v4/ws/usdt"
    rest_tickers = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
    contract_sizes = {"BTC_USDT": 0.0001, "ETH_USDT": 0.001, "SOL_USDT": 0.01}
    default_contract_size = 0.001

    async def discover_all(self) -> List[str]:
        data = await self._get_json(self.rest_tickers)
        tickers = [
            (t["contract"], float(t.get("volume_24h_quote") or 0))
            for t in data
            if t["contract"].endswith("_USDT")
        ]
        tickers.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in tickers]

    async def _on_connected(self, ws, chunk: List[str]) -> None:
        ts = int(time.time())
        for s in chunk:
            await self._send(ws, {"time": ts, "channel": "futures.trades", "event": "subscribe", "payload": [s]})
        if self._depth_symbol and self._depth_symbol in chunk:
            await self._subscribe_depth(ws, self._depth_symbol)
        asyncio.ensure_future(self._ping_loop(ws))

    async def _subscribe_depth(self, ws, symbol: str) -> None:
        await self._send(ws, {"time": int(time.time()), "channel": "futures.order_book", "event": "subscribe", "payload": [symbol, "20", "0.1"]})

    async def _unsubscribe_depth(self, ws, symbol: str) -> None:
        await self._send(ws, {"time": int(time.time()), "channel": "futures.order_book", "event": "unsubscribe", "payload": [symbol]})

    async def _ping_loop(self, ws) -> None:
        try:
            while self._running:
                await asyncio.sleep(15)
                await self._send(ws, {"time": int(time.time()), "channel": "futures.ping", "event": "ping"})
        except Exception:
            pass

    def handle_message(self, raw) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if msg.get("event") != "update" and msg.get("event") != "all":
            return
        channel = msg.get("channel")
        result = msg.get("result")
        if channel == "futures.trades" and isinstance(result, list):
            for d in result:
                symbol = d["contract"]
                size = float(d["size"])
                asyncio.ensure_future(
                    self._emit_trade(
                        symbol,
                        int(d.get("create_time_ms") or d.get("create_time", 0) * 1000),
                        float(d["price"]),
                        abs(size),
                        size > 0,
                    )
                )
        elif channel in ("futures.book_ticker", "futures.order_book") and isinstance(result, dict):
            d = result
            symbol = d.get("s") or d.get("contract", "")
            bids = _parse_levels(d.get("b") or d.get("bids"))
            asks = _parse_levels(d.get("a") or d.get("asks"))
            book = BookSnapshot(
                ts_ms=int(d.get("t") or msg.get("time_ms") or 0),
                bids=bids,
                asks=asks,
            )
            asyncio.ensure_future(self._emit_book(symbol, book))


def _parse_levels(v):
    if not isinstance(v, list):
        return []
    out = []
    for it in v:
        if isinstance(it, (list, tuple)) and len(it) >= 2:
            try:
                price, size = float(it[0]), float(it[1])
            except (TypeError, ValueError):
                continue
            if size > 0:
                out.append((price, size))
    return out
