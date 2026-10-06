import asyncio
import json
import time

import websockets


async def dump(name, url, sub, seconds=8, show=3):
    print(f"\n== {name}")
    try:
        async with websockets.connect(url, open_timeout=15, max_size=2**23) as ws:
            for m in sub():
                await ws.send(m)
            deadline = time.monotonic() + seconds
            shown = 0
            while time.monotonic() < deadline and shown < show:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
                print(raw[:260])
                shown += 1
    except Exception as e:
        print(f"ENDED {type(e).__name__}: {e}")


async def main():
    await dump("BINANCE /ws path", "wss://fstream.binance.com/ws/btcusdt@aggTrade", lambda: [], show=2)
    await dump(
        "BINANCE combined 2 streams",
        "wss://fstream.binance.com/stream?streams=btcusdt@aggTrade/btcusdt@depth20@100ms",
        lambda: [],
        show=4,
    )
    for method, param in (
        ("sub.depth", {"symbol": "BTC_USDT"}),
        ("sub.limit.depth", {"symbol": "BTC_USDT", "limit": 20}),
    ):
        await dump(
            f"MEXC {method}",
            "wss://contract.mexc.com/edge",
            lambda m=method, p=param: [json.dumps({"method": m, "param": p})],
            seconds=5,
            show=2,
        )
    for payload in (["BTC_USDT", "20", "100ms"], ["BTC_USDT", "5", "100ms"]):
        await dump(
            f"GATE order_book {payload}",
            "wss://fx-ws.gateio.ws/v4/ws/usdt",
            lambda p=payload: [json.dumps({"time": int(time.time()), "channel": "futures.order_book", "event": "subscribe", "payload": p})],
            seconds=5,
            show=2,
        )


if __name__ == "__main__":
    asyncio.run(main())
