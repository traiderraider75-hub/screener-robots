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
        "exchanges": ["binance"],
        "monitor": {"mode": "all", "top_n": 15, "pool_max": 200, "demote_after_min": 10, "radar_threshold": 50},
        "analysis_window_min": 10,
        "detector": {"size_k": 3.0, "min_prints": 5, "tick_tolerance": 0.15, "min_regularity": 0.6},
        "micro": {"iceberg_min_exec": 1.0},
        "history": {"path": "/tmp/test_history.db", "retention_h": 24, "auto_record_robots": True},
        "alerts": {"enabled": False, "sound": False},
        "scanner": {"poll_s": 30, "top_gainers_n": 50, "min_trades_24h": 5000, "rvol_threshold": 5.0},
        "strategy": {"atr_mult": 0.5, "mm_score_min": 30, "news_window_h": 4},
    }
    if os.path.exists("/tmp/test_history.db"):
        os.unlink("/tmp/test_history.db")
    screener = Screener(config)
    task = asyncio.create_task(screener.start())
    start = time.monotonic()
    for phase in range(5):
        await asyncio.sleep(15)
        st = screener.stats()
        rows = screener.screener_rows()
        with_levels = [r for r in rows if r.get("levels")]
        with_news = [r for r in rows if r.get("news")]
        print(f"[{time.monotonic() - start:.0f}s] monitored={st['monitored']} pool={st['pool']} ★={st['scan']} "
              f"signals={st['signals']} rows={len(rows)} with_levels={len(with_levels)} with_news={len(with_news)}")
        for r in (with_levels[:3]):
            print("   LVL:", r["symbol"], "levels", r["levels"], "approach", r["approach"], "mm", r["mm_score"])
        for r in (with_news[:3]):
            print("   NEWS:", r["symbol"], r["news"])
        for r in [x for x in rows if x.get("match")][:3]:
            print("   ★:", r["symbol"], r["exchange"], "rvol", r["rvol"], "growth", round(r["growth24"], 1))

    screener.select("binance:BTCUSDT")
    await asyncio.sleep(3)
    v = screener.verdict_data("binance:BTCUSDT")
    print("verdict fields:", v["fields"] and {k: v["fields"][k] for k in ("levels", "mm_score")}, "recent:", len(v["recent"]))

    task.cancel()
    for conn in screener.connectors.values():
        conn.stop()
    screener.history.close()


if __name__ == "__main__":
    asyncio.run(main())