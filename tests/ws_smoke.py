import asyncio
import json
import time

import websockets

BINANCE_WS = "wss://fstream.binance.com/stream?streams=btcusdt@aggTrade/btcusdt@depth20@100ms"
MEXC_WS = "wss://contract.mexc.com/edge"
BITGET_WS = "wss://ws.bitget.com/v2/ws/public"
GATE_WS = "wss://fx-ws.gateio.ws/v4/ws/usdt"

COLLECT_SECONDS = 12
MAX_SHOW = 3


def trunc(obj, limit=400):
    s = json.dumps(obj, ensure_ascii=False)
    return s[:limit] + ("..." if len(s) > limit else "")


async def probe(name, url, subscribe=None, is_relevant=None):
    print(f"\n===== {name} =====")
    try:
        async with websockets.connect(url, open_timeout=15, max_size=2**23) as ws:
            if subscribe is not None:
                for msg in subscribe():
                    await ws.send(msg)
            deadline = time.monotonic() + COLLECT_SECONDS
            shown = 0
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                try:
                    data = json.loads(raw)
                except (ValueError, TypeError):
                    print(f"[{name}] non-json: {raw[:200]}")
                    continue
                if is_relevant and not is_relevant(data):
                    continue
                if shown < MAX_SHOW:
                    print(f"[{name}] {trunc(data)}")
                    shown += 1
            print(f"[{name}] collected OK")
    except Exception as e:
        print(f"[{name}] FAILED: {type(e).__name__}: {e}")


async def main():
    await probe("BINANCE", BINANCE_WS)

    def mexc_sub():
        return [
            json.dumps({"method": "sub.deal", "param": {"symbol": "BTC_USDT"}}),
            json.dumps({"method": "sub.depth.limit", "param": {"symbol": "BTC_USDT", "limit": 20}}),
        ]

    await probe("MEXC", MEXC_WS, mexc_sub)

    def bitget_sub():
        return [
            json.dumps({
                "op": "subscribe",
                "args": [
                    {"instType": "USDT-FUTURES", "channel": "trades", "instId": "BTCUSDT"},
                    {"instType": "USDT-FUTURES", "channel": "books5", "instId": "BTCUSDT"},
                ],
            })
        ]

    def bitget_relevant(d):
        return d.get("action") in ("snapshot", "update")

    await probe("BITGET", BITGET_WS, bitget_sub, bitget_relevant)

    def gate_sub():
        ts = int(time.time())
        return [
            json.dumps({"time": ts, "channel": "futures.trades", "event": "subscribe", "payload": ["BTC_USDT"]}),
            json.dumps({"time": ts, "channel": "futures.order_book", "event": "subscribe", "payload": ["BTC_USDT", "100ms", "20"]}),
        ]

    def gate_relevant(d):
        return d.get("event") == "update"

    await probe("GATE", GATE_WS, gate_sub, gate_relevant)


if __name__ == "__main__":
    asyncio.run(main())
