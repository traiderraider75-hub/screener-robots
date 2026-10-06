import asyncio
import json
import logging
import os
import sys
import threading
import time

sys.path.insert(0, ".")

from core.screener import Screener
from main import Bridge

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def run_backend(screener):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(screener.start())
    except Exception:
        pass


def main():
    config = {
        "exchanges": ["binance"],
        "monitor": {"mode": "top", "top_n": 10, "pool_max": 200, "demote_after_min": 10, "radar_threshold": 50},
        "analysis_window_min": 10,
        "detector": {"size_k": 3.0, "min_prints": 5, "tick_tolerance": 0.15, "min_regularity": 0.6},
        "history": {"path": "/tmp/test_bridge.db", "retention_h": 24, "auto_record_robots": True},
        "alerts": {"enabled": False, "sound": False},
    }
    if os.path.exists("/tmp/test_bridge.db"):
        os.unlink("/tmp/test_bridge.db")
    screener = Screener(config)
    bridge = Bridge(screener)
    t = threading.Thread(target=run_backend, args=(screener,), daemon=True)
    t.start()
    time.sleep(10)

    print("1) get_indicator ДО select:", str(bridge.get_indicator(0))[:120])

    key = "binance:BTCUSDT"
    print("2) select:", key, "->", bridge.select(key))

    poll_key = None
    last_ts = 0
    trades_all = []
    for i in range(10):
        time.sleep(0.5)
        after = last_ts if (key and key == poll_key) else 0
        raw = bridge.get_indicator(after)
        if raw is None:
            print(f"[{i}] bridge вернул None!")
            continue
        data = json.loads(raw)
        if data["full"] or data["key"] != poll_key:
            trades_all = []
        poll_key = data["key"]
        trades_all.extend(data["trades"])
        if data["trades"]:
            last_ts = data["trades"][-1]["t"]
        print(f"[{i}] full={data['full']} new={len(data['trades'])} all={len(trades_all)} price={data['price']}")

    hist = json.loads(bridge.get_history("binance", "BTCUSDT", int(time.time() * 1000) + 1, 50))
    print("3) history rows:", len(hist))
    screener.history.close()


if __name__ == "__main__":
    main()
