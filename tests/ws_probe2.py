import asyncio
import json
import time

import websockets


async def grab(name, url, sub, match, seconds=15):
    print(f"== {name}")
    try:
        async with websockets.connect(url, open_timeout=15, max_size=2**23) as ws:
            for m in sub():
                await ws.send(m)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
                d = json.loads(raw)
                if match(d):
                    print(json.dumps(d, ensure_ascii=False)[:600])
                    return
    except Exception as e:
        print(f"FAILED {type(e).__name__}: {e}")


async def main():
    await grab(
        "BINANCE aggTrade",
        "wss://fstream.binance.com/stream?streams=btcusdt@aggTrade",
        lambda: [],
        lambda d: d.get("stream", "").endswith("aggTrade"),
    )
    await grab(
        "MEXC depth.limit",
        "wss://contract.mexc.com/edge",
        lambda: [json.dumps({"method": "sub.depth.limit", "param": {"symbol": "BTC_USDT", "limit": 20}})],
        lambda d: "push.depth" in str(d.get("channel", "")),
    )
    await grab(
        "BITGET trades",
        "wss://ws.bitget.com/v2/ws/public",
        lambda: [json.dumps({"op": "subscribe", "args": [{"instType": "USDT-FUTURES", "channel": "trades", "instId": "BTCUSDT"}]})],
        lambda d: d.get("arg", {}).get("channel") == "trades",
    )
    await grab(
        "GATE order_book",
        "wss://fx-ws.gateio.ws/v4/ws/usdt",
        lambda: [json.dumps({"time": int(time.time()), "channel": "futures.order_book", "event": "subscribe", "payload": ["BTC_USDT", "100ms", "20"]})],
        lambda d: d.get("channel") == "futures.order_book" and d.get("event") == "update",
    )


if __name__ == "__main__":
    asyncio.run(main())
