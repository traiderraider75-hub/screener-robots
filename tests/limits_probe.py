import asyncio
import json
import time

import aiohttp
import websockets


async def get_json(url):
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            r.raise_for_status()
            return await r.json()


async def symbols_binance():
    data = await get_json("https://fapi.binance.com/fapi/v1/ticker/24hr")
    return [t["symbol"] for t in data if t["symbol"].endswith("USDT") and not t["symbol"].startswith(("USDC", "BTCDOM"))]


async def symbols_mexc():
    data = await get_json("https://contract.mexc.com/api/v1/contract/ticker")
    return [t["symbol"] for t in data.get("data", []) if t["symbol"].endswith("_USDT")]


async def symbols_bitget():
    data = await get_json("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES")
    return [t["symbol"] for t in data.get("data", []) if t["symbol"].endswith("USDT")]


async def symbols_gate():
    data = await get_json("https://api.gateio.ws/api/v4/futures/usdt/tickers")
    return [t["contract"] for t in data if t["contract"].endswith("_USDT")]


async def measure(name, url, subscribe_fn, n_symbols, seconds=15):
    try:
        async with websockets.connect(url, open_timeout=20, max_size=2**24, ping_interval=None) as ws:
            t0 = time.monotonic()
            await subscribe_fn(ws, n_symbols)
            sub_s = time.monotonic() - t0
            count = 0
            errors = []
            t0 = time.monotonic()
            deadline = t0 + seconds
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
                    count += 1
                    head = raw[:200] if isinstance(raw, str) else raw[:200].decode("utf-8", "ignore")
                    if count <= 2 or '"error"' in head or '"code"' in head:
                        if len(errors) < 3:
                            errors.append(head)
                except asyncio.TimeoutError:
                    break
            dt = time.monotonic() - t0
            print(f"{name}: symbols={n_symbols} sub_time={sub_s:.1f}s msgs={count} rate={count / dt:.0f}/s")
            for e in errors:
                print(f"   sample: {e}")
    except Exception as e:
        print(f"{name}: FAILED {type(e).__name__}: {str(e)[:150]}")


async def main():
    bs = await symbols_binance()
    mx = await symbols_mexc()
    bg = await symbols_bitget()
    gt = await symbols_gate()
    print(f"ALL FUTURES: binance={len(bs)} mexc={len(mx)} bitget={len(bg)} gate={len(gt)} total={len(bs)+len(mx)+len(bg)+len(gt)}")

    async def sub_binance(ws, n):
        streams = []
        for s in bs[:n]:
            streams.append(f"{s.lower()}@trade")
        await ws.send(json.dumps({"method": "SUBSCRIBE", "params": streams, "id": 1}))

    await measure("BINANCE 150 syms trade", "wss://fstream.binance.com/stream", sub_binance, 150)

    async def sub_mexc(ws, n):
        for s in mx[:n]:
            await ws.send(json.dumps({"method": "sub.deal", "param": {"symbol": s}}))

    await measure("MEXC 60 syms deal", "wss://contract.mexc.com/edge", sub_mexc, 60)
    await measure("MEXC 150 syms deal", "wss://contract.mexc.com/edge", sub_mexc, 150)

    async def sub_bitget(ws, n):
        args = [{"instType": "USDT-FUTURES", "channel": "trade", "instId": s} for s in bg[:n]]
        await ws.send(json.dumps({"op": "subscribe", "args": args}))

    await measure("BITGET 100 syms trade", "wss://ws.bitget.com/v2/ws/public", sub_bitget, 100)
    await measure("BITGET 250 syms trade", "wss://ws.bitget.com/v2/ws/public", sub_bitget, 250)

    async def sub_gate(ws, n):
        ts = int(time.time())
        for s in gt[:n]:
            await ws.send(json.dumps({"time": ts, "channel": "futures.trades", "event": "subscribe", "payload": [s]}))

    await measure("GATE 100 syms trades", "wss://fx-ws.gateio.ws/v4/ws/usdt", sub_gate, 100)
    await measure("GATE 250 syms trades", "wss://fx-ws.gateio.ws/v4/ws/usdt", sub_gate, 250)


if __name__ == "__main__":
    asyncio.run(main())
