import asyncio
import json
import logging
from typing import List

from core.connectors.base import BaseConnector
from core.models import BookSnapshot

log = logging.getLogger(__name__)


class BitgetConnector(BaseConnector):
    name = "bitget"
    ws_url = "wss://ws.bitget.com/v2/ws/public"
    rest_tickers = "https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES"
    chunk_size = 100

    async def discover_all(self) -> List[str]:
        data = await self._get_json(self.rest_tickers)
        tickers = [
            (t["symbol"], float(t.get("usdtVolume") or 0))
            for t in data.get("data", [])
            if t["symbol"].endswith("USDT")
        ]
        tickers.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in tickers]

    async def _on_connected(self, ws, chunk: List[str]) -> None:
        args = [{"instType": "USDT-FUTURES", "channel": "trade", "instId": s} for s in chunk]
        await self._send(ws, {"op": "subscribe", "args": args})
        if self._depth_symbol and self._depth_symbol in chunk:
            await self._subscribe_depth(ws, self._depth_symbol)
        asyncio.ensure_future(self._ping_loop(ws))

    async def _subscribe_depth(self, ws, symbol: str) -> None:
        await self._send(ws, {"op": "subscribe", "args": [{"instType": "USDT-FUTURES", "channel": "books5", "instId": symbol}]})

    async def _unsubscribe_depth(self, ws, symbol: str) -> None:
        await self._send(ws, {"op": "unsubscribe", "args": [{"instType": "USDT-FUTURES", "channel": "books5", "instId": symbol}]})

    async def _ping_loop(self, ws) -> None:
        try:
            while self._running:
                await asyncio.sleep(25)
                await ws.send("ping")
        except Exception:
            pass

    def handle_message(self, raw) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if msg.get("event") == "error":
            log.warning("[%s] subscribe error: %s", self.name, msg)
            return
        action = msg.get("action")
        if action not in ("snapshot", "update"):
            return
        arg = msg.get("arg", {})
        channel = arg.get("channel")
        symbol = arg.get("instId", "")
        for d in msg.get("data", []):
            if channel == "trade":
                asyncio.ensure_future(
                    self._emit_trade(
                        symbol,
                        int(d["ts"]),
                        float(d["price"]),
                        float(d["size"]),
                        d.get("side") == "buy",
                    )
                )
            elif channel == "books5":
                book = BookSnapshot(
                    ts_ms=int(d.get("ts") or 0),
                    bids=[(float(p), float(q)) for p, q in d.get("bids", [])],
                    asks=[(float(p), float(q)) for p, q in d.get("asks", [])],
                )
                asyncio.ensure_future(self._emit_book(symbol, book))
