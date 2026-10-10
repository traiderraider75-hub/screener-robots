import asyncio
import json
import logging
import threading
from pathlib import Path

import webview

from core.screener import Screener

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

ROOT = Path(__file__).parent


def load_config() -> dict:
    cfg_path = ROOT / "config.yaml"
    if not cfg_path.exists():
        return {}
    import yaml

    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Bridge:
    def __init__(self, screener: Screener):
        self.screener = screener

    def _on_loop(self, fn):
        loop = self.screener.loop
        if loop is None or loop.is_closed():
            return fn()
        try:
            fut = asyncio.run_coroutine_threadsafe(_call(fn), loop)
            return fut.result(timeout=15)
        except Exception:
            log.exception("bridge call failed")
            return None

    def get_screener(self):
        return json.dumps(self._on_loop(self.screener.screener_rows))

    def get_market_table(self, mode="growth"):
        mode = mode if mode in ("growth", "trades") else "growth"
        limit = int(self.screener.config.get("scanner", {}).get("market_rows_limit", 100))
        return json.dumps(self._on_loop(lambda: self.screener.market_rows(mode, limit)))

    def get_symbols(self):
        return json.dumps(self._on_loop(self.screener.symbol_list))

    def get_indicator(self, after_ms=0):
        after = int(after_ms) if after_ms else 0
        return json.dumps(self._on_loop(lambda: self.screener.indicator_data(self.screener.selected, after)))

    def get_robot_events(self):
        return json.dumps(self._on_loop(self.screener.take_robot_events))

    def get_stats(self):
        return json.dumps(self._on_loop(self.screener.stats))

    def get_verdict(self):
        return json.dumps(self._on_loop(lambda: self.screener.verdict_data(self.screener.selected)))

    def get_chart(self, key, limit=720):
        def _build():
            exch, sym = key.split(":", 1)
            if exch != "binance":
                return {"candles": [], "levels": [], "price": 0}
            sl = self.screener.strategy.levels.get(exch, sym)
            candles = sl.raw_klines[-int(limit):] if hasattr(sl, "raw_klines") else []
            levels = [
                {"price": round(l.price, 8), "lo": round(l.zone_lo, 8), "hi": round(l.zone_hi, 8),
                 "touches": l.touches, "direction": l.direction}
                for l in sl.significant(2)
            ]
            st = self.screener.store.get(exch, sym)
            return {"candles": candles, "levels": levels, "price": st.last_price}
        return json.dumps(self._on_loop(_build))

    def get_signals(self):
        return json.dumps(self._on_loop(self.screener.take_strategy_signals))

    def get_history(self, exchange, symbol, before_ms, limit=8000):
        return json.dumps(self._on_loop(
            lambda: self.screener.get_history(exchange, symbol, int(before_ms), int(limit))
        ))

    def select(self, key):
        self._on_loop(lambda: self.screener.select(key))
        return True


async def _call(fn):
    return fn()


def run_backend(screener: Screener) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(screener.start())
    except Exception:
        log.exception("backend stopped")


def main() -> None:
    config = load_config()
    screener = Screener(config)
    bridge = Bridge(screener)

    t = threading.Thread(target=run_backend, args=(screener,), daemon=True)
    t.start()

    ui = ROOT / "ui" / "index.html"
    webview.create_window(
        "Screener Robots — роботы на фьючерсах",
        url=ui.as_uri(),
        js_api=bridge,
        width=1400,
        height=900,
        min_size=(1000, 600),
    )
    webview.start()


if __name__ == "__main__":
    main()
