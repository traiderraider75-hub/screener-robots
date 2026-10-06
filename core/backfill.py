import asyncio
import logging
import time
from typing import List, Optional, Tuple

from core.connectors.binance import BinanceConnector
from core.connectors.gate import GateConnector
from core.detectors.robots import SymbolRobotDetector
from core.history import History
from core.models import Trade

log = logging.getLogger(__name__)

MAX_REQUESTS = 1500
BATCH = 1000
KLINES_BATCH = 1500
DETECTOR_WINDOW_S = 3600
DETECTOR_CFG = {"size_k": 3.0, "min_prints": 5, "tick_tolerance": 0.15, "min_regularity": 0.6, "tick_bucket_ms": 10}
KLINES_LIVE_MARGIN_MS = 180000


def _ff_tag(rows: List[Tuple[int, float, float, bool]]) -> List[Tuple[int, float, float, bool, bool]]:
    """Fast-forward прогон детектора: восстанавливает флаги роботов на исторических тиках."""
    det = SymbolRobotDetector("hist", "hist", DETECTOR_WINDOW_S, DETECTOR_CFG)
    out = []
    for ts, price, size, is_buy in rows:
        tr = Trade.of("hist", "hist", ts, price, size, is_buy)
        det.on_trade(tr)
        out.append((ts, price, size, is_buy, tr.is_robot))
    return out


async def run_backfill(conn, exchange: str, symbol: str, since_ms: int, history: History) -> int:
    if conn is None:
        return 0
    total = 0
    try:
        if isinstance(conn, BinanceConnector):
            total += await _backfill_binance_klines(conn, exchange, symbol, since_ms, history)
            total += await _backfill_binance_ticks(conn, exchange, symbol, since_ms, history)
        elif isinstance(conn, GateConnector):
            total += await _backfill_gate_ticks(conn, exchange, symbol, since_ms, history)
    except Exception as e:
        log.warning("backfill %s:%s stopped: %s: %s (%d rows)", exchange, symbol, type(e).__name__, e, total)
    return total


async def _backfill_binance_klines(conn: BinanceConnector, exchange: str, symbol: str, since_ms: int, history: History) -> int:
    """1-минутные klines: buy = takerBuyBase, sell = остальное. Закрывает 24 часа за 1-2 запроса."""
    base = "https://fapi.binance.com/fapi/v1/klines"
    now_ms = int(time.time() * 1000)
    end_ms = now_ms - KLINES_LIVE_MARGIN_MS
    if end_ms <= since_ms:
        return 0
    covered = history.minute_set(exchange, symbol)
    total = 0
    start = since_ms
    while start < end_ms:
        url = f"{base}?symbol={symbol}&interval=1m&startTime={start}&endTime={end_ms}&limit={KLINES_BATCH}"
        data = await conn._get_json(url)
        if not data:
            break
        rows: List[Tuple[int, float, float, bool]] = []
        last_open = 0
        for k in data:
            open_time = int(k[0])
            last_open = open_time
            minute = (open_time // 60000) * 60000
            if minute in covered:
                continue
            vol = float(k[5])
            buy = float(k[9])
            sell = max(0.0, vol - buy)
            if buy > 0:
                rows.append((minute, float(k[4]), buy, True))
            if sell > 0:
                rows.append((minute + 1, float(k[4]), sell, False))
        if rows:
            total += history.insert_many_klines(exchange, symbol, rows)
        if len(data) < KLINES_BATCH:
            break
        start = last_open + 60000
        await asyncio.sleep(0.3)
    log.info("klines backfill %s:%s +%d bars", exchange, symbol, total)
    return total


async def _backfill_binance_ticks(conn: BinanceConnector, exchange: str, symbol: str, since_ms: int, history: History) -> int:
    base = "https://fapi.binance.com/fapi/v1/aggTrades"
    params = {"symbol": symbol, "limit": BATCH, "startTime": since_ms}
    covered = history.minute_set(exchange, symbol)
    total = 0
    requests = 0
    now_ms = int(time.time() * 1000)
    try:
        while requests < MAX_REQUESTS:
            data = await conn._get_json(
                base + "?" + "&".join(f"{k}={v}" for k, v in params.items())
            )
            requests += 1
            if not data:
                break
            rows: List[Tuple[int, float, float, bool]] = []
            last_id = None
            last_ts = 0
            for d in data:
                ts = int(d["T"])
                minute = (ts // 60000) * 60000
                if minute in covered:
                    continue
                rows.append((ts, float(d["p"]), float(d["q"]), not bool(d["m"])))
                last_id = int(d["a"])
                last_ts = ts
            total += history.insert_many(exchange, symbol, _ff_tag(rows))
            if last_ts >= now_ms - 2000 or last_id is None or len(data) < BATCH:
                break
            params = {"symbol": symbol, "limit": BATCH, "fromId": last_id + 1}
            await asyncio.sleep(0.6)
    except Exception as e:
        log.warning("binance tick backfill %s stopped: %s: %s (%d trades)", symbol, type(e).__name__, e, total)
    return total


async def _backfill_gate_ticks(conn: GateConnector, exchange: str, symbol: str, since_ms: int, history: History) -> int:
    base = "https://api.gateio.ws/api/v4/futures/usdt/trades"
    total = 0
    requests = 0
    from_s = max(1, since_ms // 1000)
    now_s = int(time.time()) - 2
    try:
        while requests < MAX_REQUESTS and from_s < now_s:
            url = f"{base}?contract={symbol}&from={from_s}&to={now_s}&limit={BATCH}"
            data = await conn._get_json(url)
            requests += 1
            if not data:
                break
            rows: List[Tuple[int, float, float, bool]] = []
            max_t = 0
            for d in data:
                ts = int(d.get("create_time_ms") or int(d["create_time"]) * 1000)
                size = float(d["size"])
                rows.append((ts, float(d["price"]), abs(size), size > 0))
                max_t = max(max_t, int(d["create_time"]))
            total += history.insert_many(exchange, symbol, _ff_tag(rows))
            if len(data) < BATCH:
                break
            from_s = max_t + 1
            await asyncio.sleep(0.3)
    except Exception as e:
        log.warning("gate tick backfill %s stopped: %s: %s (%d trades)", symbol, type(e).__name__, e, total)
    return total
