import asyncio
import json
import logging
from typing import Awaitable, Callable, Dict, List, Optional

import aiohttp
import websockets

log = logging.getLogger(__name__)

TradeCallback = Callable[[str, str, int, float, float, bool], Awaitable[None]]
BookCallback = Callable[[str, str, "object"], Awaitable[None]]


class BaseConnector:
    name = "base"
    ws_url = ""
    chunk_size = 150
    contract_sizes: Dict[str, float] = {}
    default_contract_size = 1.0

    def __init__(self, trade_cb: TradeCallback, book_cb: Optional[BookCallback] = None) -> None:
        self._trade_cb = trade_cb
        self._book_cb = book_cb
        self._symbols: List[str] = []
        self._chunks: List[List[str]] = []
        self._running = False
        self._depth_symbol: Optional[str] = None
        self._ws_by_chunk: Dict[int, object] = {}

    def contract_size(self, symbol: str) -> float:
        return self.contract_sizes.get(symbol, self.default_contract_size)

    async def discover_top(self, top_n: int) -> List[str]:
        symbols = await self.discover_all()
        return symbols[:top_n]

    async def discover_all(self) -> List[str]:
        raise NotImplementedError

    async def run(self, symbols: List[str]) -> None:
        self._symbols = symbols
        self._chunks = [symbols[i:i + self.chunk_size] for i in range(0, len(symbols), self.chunk_size)]
        self._running = True
        log.info("[%s] running %d symbols in %d connections", self.name, len(symbols), len(self._chunks))
        tasks = [asyncio.create_task(self._run_chunk(idx, chunk)) for idx, chunk in enumerate(self._chunks)]
        if tasks:
            await asyncio.gather(*tasks)

    def stop(self) -> None:
        self._running = False

    async def _run_chunk(self, idx: int, chunk: List[str]) -> None:
        if idx:
            await asyncio.sleep(min(idx * 1.5, 20))
        backoff = 2
        while self._running:
            try:
                async with websockets.connect(self.ws_url, open_timeout=20, max_size=2**24, ping_interval=20) as ws:
                    self._ws_by_chunk[idx] = ws
                    log.info("[%s] conn#%d connected (%d symbols)", self.name, idx, len(chunk))
                    backoff = 2
                    await self._on_connected(ws, chunk)
                    await self._read_loop(ws, idx)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("[%s] conn#%d lost: %s: %s", self.name, idx, type(e).__name__, e)
            finally:
                self._ws_by_chunk.pop(idx, None)
            if self._running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def set_depth_symbol(self, symbol: Optional[str]) -> None:
        old = self._depth_symbol
        self._depth_symbol = symbol
        if old != symbol:
            for idx in list(self._ws_by_chunk.keys()):
                chunk = self._chunks[idx] if idx < len(self._chunks) else []
                ws = self._ws_by_chunk.get(idx)
                if ws is None:
                    continue
                try:
                    if old in chunk:
                        await self._unsubscribe_depth(ws, old)
                    if symbol in chunk:
                        await self._subscribe_depth(ws, symbol)
                except Exception as e:
                    log.warning("[%s] depth switch failed: %s", self.name, e)

    async def _on_connected(self, ws, chunk: List[str]) -> None:
        raise NotImplementedError

    async def _subscribe_depth(self, ws, symbol: str) -> None:
        pass

    async def _unsubscribe_depth(self, ws, symbol: str) -> None:
        pass

    async def _read_loop(self, ws, idx: int) -> None:
        async for raw in ws:
            try:
                self.handle_message(raw)
            except Exception:
                log.exception("[%s] handle_message failed", self.name)

    def handle_message(self, raw) -> None:
        raise NotImplementedError

    async def _emit_trade(self, symbol: str, ts_ms: int, price: float, size_contracts: float, is_buy: bool) -> None:
        size = size_contracts * self.contract_size(symbol)
        if size <= 0:
            return
        await self._trade_cb(self.name, symbol, ts_ms, price, size, is_buy)

    async def _emit_book(self, symbol: str, book) -> None:
        if self._book_cb is not None:
            await self._book_cb(self.name, symbol, book)

    async def _send(self, ws, payload: dict) -> None:
        await ws.send(json.dumps(payload))

    async def _get_json(self, url: str, timeout: int = 20):
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                resp.raise_for_status()
                return await resp.json()
