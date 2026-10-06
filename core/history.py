import logging
import os
import sqlite3
import threading
import time
from collections import deque
from typing import Deque, List, Optional, Tuple

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    is_buy INTEGER NOT NULL,
    is_robot INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_key_ts ON trades (exchange, symbol, ts_ms);
CREATE TABLE IF NOT EXISTS keys (
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    PRIMARY KEY (exchange, symbol)
);
"""


class History:
    """Персистентная история сделок (24 ч) для записываемых символов."""

    def __init__(self, path: str = "history.db", retention_h: int = 24):
        self.retention_h = retention_h
        new_db = not os.path.exists(path)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.commit()
        self._db_lock = threading.Lock()
        self._lock = threading.Lock()
        self._queue: Deque[Tuple] = deque()
        self._keys = set()
        for exch, sym in self._conn.execute("SELECT exchange, symbol FROM keys"):
            self._keys.add((exch, sym))
        self._stop = threading.Event()
        self._writer = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer.start()
        if new_db:
            log.info("history: created %s", path)

    def keys(self) -> List[Tuple[str, str]]:
        with self._lock:
            return list(self._keys)

    def add_key(self, exchange: str, symbol: str) -> None:
        key = (exchange.lower(), symbol)
        with self._lock:
            if key in self._keys:
                return
            self._keys.add(key)
        with self._db_lock:
            self._conn.execute("INSERT OR IGNORE INTO keys (exchange, symbol) VALUES (?, ?)", key)
            self._conn.commit()

    def record(self, exchange: str, symbol: str, ts_ms: int, price: float, size: float, is_buy: bool, is_robot: bool) -> None:
        key = (exchange, symbol)
        with self._lock:
            if key not in self._keys:
                return
            self._queue.append((exchange, symbol, ts_ms, price, size, 1 if is_buy else 0, 1 if is_robot else 0))

    def insert_many(self, exchange: str, symbol: str, rows: List[Tuple[int, float, float, bool, bool]]) -> int:
        """Неблокирующая вставка (бэктфил): уходит в очередь writer-потока."""
        if not rows:
            return 0
        self.add_key(exchange, symbol)
        with self._lock:
            for ts, price, size, b, r in rows:
                self._queue.append((exchange, symbol, ts, price, size, 1 if b else 0, 1 if r else 0))
        return len(rows)

    def insert_many_klines(self, exchange: str, symbol: str, rows: List[Tuple[int, float, float, bool]]) -> int:
        """Агрегатные минутные бары (klines): rows = [(ts, price, size, is_buy), ...]."""
        if not rows:
            return 0
        self.add_key(exchange, symbol)
        with self._lock:
            for ts, price, size, b in rows:
                self._queue.append((exchange, symbol, ts, price, size, 1 if b else 0, 0))
        return len(rows)

    def minute_set(self, exchange: str, symbol: str) -> set:
        """Множество минутных меток, уже покрытых строками истории."""
        with self._db_lock:
            cur = self._conn.execute(
                "SELECT DISTINCT (ts_ms / 60000) * 60000 FROM trades WHERE exchange=? AND symbol=?",
                (exchange, symbol),
            )
            return {int(r[0]) for r in cur}

    def query(self, exchange: str, symbol: str, before_ms: int, limit: int = 8000) -> List[dict]:
        with self._db_lock:
            cur = self._conn.execute(
                "SELECT ts_ms, price, size, is_buy, is_robot FROM trades "
                "WHERE exchange=? AND symbol=? AND ts_ms<? ORDER BY ts_ms DESC LIMIT ?",
                (exchange, symbol, before_ms, limit),
            )
        rows = [
            {"t": ts, "s": size, "b": bool(b), "r": bool(r)}
            for ts, _, size, b, r in cur
        ]
        rows.reverse()
        return rows

    def earliest_ts(self, exchange: str, symbol: str) -> Optional[int]:
        with self._db_lock:
            cur = self._conn.execute(
                "SELECT MIN(ts_ms) FROM trades WHERE exchange=? AND symbol=?",
                (exchange, symbol),
            )
        row = cur.fetchone()
        return row[0] if row and row[0] else None

    def cleanup_now(self) -> int:
        cutoff = int(time.time() * 1000) - self.retention_h * 3600 * 1000
        with self._db_lock:
            cur = self._conn.execute("DELETE FROM trades WHERE ts_ms < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    def _writer_loop(self) -> None:
        last_cleanup = 0.0
        while not self._stop.is_set():
            batch = []
            with self._lock:
                while self._queue:
                    batch.append(self._queue.popleft())
            if batch:
                try:
                    with self._db_lock:
                        self._conn.executemany(
                            "INSERT INTO trades (exchange, symbol, ts_ms, price, size, is_buy, is_robot) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            batch,
                        )
                        self._conn.commit()
                except sqlite3.Error:
                    log.exception("history write failed (%d rows lost)", len(batch))
            now = time.monotonic()
            if now - last_cleanup > 3600:
                last_cleanup = now
                try:
                    with self._db_lock:
                        self.cleanup_now()
                        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    log.exception("history cleanup failed")
            self._stop.wait(1.0)

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def close(self) -> None:
        self._stop.set()
        self._writer.join(timeout=3)
        with self._db_lock:
            self._conn.commit()
            self._conn.close()
