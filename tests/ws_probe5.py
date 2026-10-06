import asyncio
import json
import time

import websockets


async def dump(name, url, seconds=8, show=3):
    print(f"\n== {name}")
    try:
        async with websockets.connect(url, open_timeout=15, max_size=2**23) as ws:
            deadline = time.monotonic() + seconds
            shown = 0
            while time.monotonic() < deadline and shown < show:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
                print(raw[:260])
                shown += 1
    except Exception as e:
        print(f"ENDED {type(e).__name__}: {e}")


async def main():
    for stream in ("btcusdt@trade", "btcusdt@bookTicker"):
        await dump(f"BINANCE {stream}", f"wss://fstream.binance.com/stream?streams={stream}", show=2)
    for payload in (["BTC_USDT", "20", "1000ms"], ["BTC_USDT", "100", "1000ms"]):
        async def gate(p=payload):
            try:
                async with websockets.connect("wss://fx-ws.gateio.ws/v4/ws/usdt", open_timeout=15, max_size=2**23) as ws:
                    await ws.send(json.dumps({"time": int(time.time()), "channel": "futures.order_book", "event": "subscribe", "payload": p}))
                    t = time.monotonic()
                    while time.monotonic() - t < 6:
                        raw = await asyncio.wait_for(ws.recv(), timeout=6)
                        d = json.loads(raw)
                        ev = d.get("event")
                        print(f"GATE {p} event={ev}", raw[:220])
                        if ev == "update":
                            break
            except Exception as e:
                print(f"GATE {p} ENDED {type(e).__name__}: {e}")
        await gate()


if __name__ == "__main__":
    asyncio.run(main())
