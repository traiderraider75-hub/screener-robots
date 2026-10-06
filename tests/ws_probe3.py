import asyncio
import json
import time

import websockets


async def dump(name, url, sub, seconds=10, show=4):
    print(f"\n== {name}")
    try:
        async with websockets.connect(url, open_timeout=15, max_size=2**23) as ws:
            for m in sub():
                await ws.send(m)
            deadline = time.monotonic() + seconds
            shown = 0
            while time.monotonic() < deadline and shown < show:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
                print(raw[:300])
                shown += 1
    except Exception as e:
        print(f"ENDED {type(e).__name__}: {e}")


async def main():
    await dump("BINANCE aggTrade", "wss://fstream.binance.com/stream?streams=btcusdt@aggTrade", lambda: [], seconds=8, show=2)

    async def mexc():
        async with websockets.connect("wss://contract.mexc.com/edge", open_timeout=15, max_size=2**23) as ws:
            for method, param in [
                ("sub.depth.limit", {"symbol": "BTC_USDT", "limit": 20}),
            ]:
                await ws.send(json.dumps({"method": method, "param": param}))
            t = time.monotonic()
            while time.monotonic() - t < 8:
                raw = await asyncio.wait_for(ws.recv(), timeout=8)
                d = json.loads(raw)
                if "rs." in str(d.get("channel", "")) or "push" in str(d.get("channel", "")):
                    print(("MEXC " + method)[:20], raw[:300])

    try:
        await mexc()
    except Exception as e:
        print(f"MEXC ENDED {type(e).__name__}: {e}")

    for channel in ("trades", "trade"):
        await dump(
            f"BITGET {channel}",
            "wss://ws.bitget.com/v2/ws/public",
            lambda ch=channel: [json.dumps({"op": "subscribe", "args": [{"instType": "USDT-FUTURES", "channel": ch, "instId": "BTCUSDT"}]})],
            seconds=5,
            show=2,
        )

    for interval, level in (("100ms", "5"), ("1s", "20")):
        await dump(
            f"GATE order_book {interval} L{level}",
            "wss://fx-ws.gateio.ws/v4/ws/usdt",
            lambda iv=interval, lv=level: [json.dumps({"time": int(time.time()), "channel": "futures.order_book", "event": "subscribe", "payload": ["BTC_USDT", iv, lv]})],
            seconds=5,
            show=2,
        )


if __name__ == "__main__":
    asyncio.run(main())
