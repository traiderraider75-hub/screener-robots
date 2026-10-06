import asyncio
import json
import logging
from typing import List

from core.connectors.base import BaseConnector
from core.models import BookSnapshot

log = logging.getLogger(__name__)


class BinanceConnector(BaseConnector):
    name = "binance"
    ws_url = "wss://fstream.binance.com/stream"
    rest_tickers = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    chunk_size = 190

    async def discover_all(self) -> List[str]:
        data = await self._get_json(self.rest_tickers)
        tickers = [
            (t["symbol"], float(t["quoteVolume"]))
            for t in data
            if t["symbol"].endswith("USDT") and not t["symbol"].startswith(("USDC", "BTCDOM"))
        ]
        tickers.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in tickers]

    async def _on_connected(self, ws, chunk: List[str]) -> None:
        streams = [f"{s.lower()}@trade" for s in chunk]
        await ws.send('{"method":"SUBSCRIBE","params":%s,"id":1}' % json.dumps(streams))
        if self._depth_symbol and self._depth_symbol in chunk:
            await self._subscribe_depth(ws, self._depth_symbol)

    async def _subscribe_depth(self, ws, symbol: str) -> None:
        await ws.send('{"method":"SUBSCRIBE","params":["%s"],"id":2}' % (symbol.lower() + "@depth20@100ms"))

    async def _unsubscribe_depth(self, ws, symbol: str) -> None:
        await ws.send('{"method":"UNSUBSCRIBE","params":["%s"],"id":3}' % (symbol.lower() + "@depth20@100ms"))

    def handle_message(self, raw) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict) or "stream" not in msg:
            return
        data = msg.get("data") or {}
        event = data.get("e")
        if event == "trade":
            symbol = data["s"]
            asyncio.ensure_future(
                self._emit_trade(symbol, int(data["T"]), float(data["p"]), float(data["q"]), not bool(data["m"]))
            )
        elif event == "depthUpdate":
            symbol = data["s"]
            book = BookSnapshot(
                ts_ms=int(data["E"]),
                bids=[(float(p), float(q)) for p, q in data["b"] if float(q) > 0],
                asks=[(float(p), float(q)) for p, q in data["a"] if float(q) > 0],
            )
            asyncio.ensure_future(self._emit_book(symbol, book))
