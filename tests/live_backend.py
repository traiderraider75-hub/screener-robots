import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, ".")

from core.screener import Screener

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main():
    config = {
        "exchanges": ["binance", "mexc", "bitget", "gate"],
        "monitor": {"mode": "all", "top_n": 15, "pool_max": 200, "demote_after_min": 10, "radar_threshold": 50},
        "analysis_window_min": 10,
        "detector": {"size_k": 3.0, "min_prints": 5, "tick_tolerance": 0.15, "min_regularity": 0.6},
        "micro": {"iceberg_min_exec": 1.0},
        "history": {"path": "/tmp/test_history.db", "retention_h": 24, "auto_record_robots": True},
        "alerts": {"enabled": False, "sound": False},
        "scanner": {"poll_s": 30, "top_gainers_n": 50, "min_trades_24h": 5000, "rvol_threshold": 5.0},
    }
    if os.path.exists("/tmp/test_history.db"):
        os.unlink("/tmp/test_history.db")
    screener = Screener(config)
    task = asyncio.create_task(screener.start())
    start = time.monotonic()
    for phase in range(6):
        await asyncio.sleep(15)
        st = screener.stats()
        rows = screener.screener_rows()
        matched = [r for r in rows if r.get("match")]
        print(
            f"[{time.monotonic() - start:.0f}s] monitored={st['monitored']} pool={st['pool']} "
            f"robots={st['robots']} ★scan={st['scan']} rows={len(rows)} matched={len(matched)}"
        )
        for r in matched[:6]:
            print(
                "   ★:", r["symbol"], r["exchange"], "growth", r["growth24"], "rvol", r["rvol"],
                "trades", r["count24"], "score", r["score"]
            )

    screener.select("binance:BTCUSDT")
    await asyncio.sleep(3)
    data = screener.indicator_data("binance:BTCUSDT")
    print("indicator:", len(data["trades"]), "trades, price", data["price"])

    task.cancel()
    for conn in screener.connectors.values():
        conn.stop()
    screener.history.close()


if __name__ == "__main__":
    asyncio.run(main())