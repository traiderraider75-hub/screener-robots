import asyncio
import json
import logging
from typing import List

from core.connectors.base import BaseConnector
from core.models import BookSnapshot

log = logging.getLogger(__name__)


class MexcConnector(BaseConnector):
    name = "mexc"
    ws_url = "wss://contract.mexc.com/edge"
    rest_tickers = "https://contract.mexc.com/api/v1/contract/ticker"
    contract_sizes = {"BTC_USDT": 0.0001, "ETH_USDT": 0.001, "SOL_USDT": 0.01}
    default_contract_size = 0.001

    async def discover_all(self) -> List[str]:
        data = await self._get_json(self.rest_tickers)
        tickers = [
            (t["symbol"], float(t.get("amount24") or 0))
            for t in data.get("data", [])
            if t["symbol"].endswith("_USDT")
        ]
        tickers.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in tickers]

    async def _on_connected(self, ws, chunk: List[str]) -> None:
        for s in chunk:
            await self._send(ws, {"method": "sub.deal", "param": {"symbol": s}})
        if self._depth_symbol and self._depth_symbol in chunk:
            await self._subscribe_depth(ws, self._depth_symbol)

    async def _subscribe_depth(self, ws, symbol: str) -> None:
        await self._send(ws, {"method": "sub.depth", "param": {"symbol": symbol}})

    async def _unsubscribe_depth(self, ws, symbol: str) -> None:
        await self._send(ws, {"method": "unsub.depth", "param": {"symbol": symbol}})

    def handle_message(self, raw) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        channel = msg.get("channel", "")
        if channel == "ping":
            asyncio.ensure_future(self._pong())
            return
        if channel == "push.deal":
            symbol = msg.get("symbol", "")
            for d in msg.get("data", []):
                is_buy = d.get("T") == 1
                asyncio.ensure_future(
                    self._emit_trade(symbol, int(d["t"]), float(d["p"]), float(d["v"]), is_buy)
                )
        elif channel == "push.depth":
            symbol = msg.get("symbol", "")
            data = msg.get("data", {})
            book = BookSnapshot(
                ts_ms=int(data.get("cts") or msg.get("ts") or 0),
                bids=[(float(p), float(v)) for p, v, _ in data.get("bids", []) if float(v) > 0],
                asks=[(float(p), float(v)) for p, v, _ in data.get("asks", []) if float(v) > 0],
            )
            asyncio.ensure_future(self._emit_book(symbol, book))

    async def _pong(self) -> None:
        if self._ws is not None:
            try:
                await self._send(self._ws, {"method": "pong"})
            except Exception:
                pass
