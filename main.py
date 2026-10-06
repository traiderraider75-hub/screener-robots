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

    def get_symbols(self):
        return json.dumps(self._on_loop(self.screener.symbol_list))

    def get_indicator(self, after_ms=0):
        after = int(after_ms) if after_ms else 0
        return json.dumps(self._on_loop(lambda: self.screener.indicator_data(self.screener.selected, after)))

    def get_robot_events(self):
        return json.dumps(self._on_loop(self.screener.take_robot_events))

    def get_stats(self):
        return json.dumps(self._on_loop(self.screener.stats))

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
