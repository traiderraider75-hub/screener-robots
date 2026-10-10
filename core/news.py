import logging
import re
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

import aiohttp

log = logging.getLogger(__name__)

ANNOUNCE_URL = ("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
                "?type=1&pageNo=1&pageSize=50&catalogId=48")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

POSITIVE_WORDS = [
    "will add", "will list", "will launch", "adds ", "launch", "listing", "lists ",
    "perpetual contract", "leverage", "margin available", "partnership", "collateral asset",
    "trading open", "new trading pair", "futures will launch",
]
NEGATIVE_WORDS = [
    "delist", "delisting", "will remove", "delists", "suspend", "suspension", "halt",
    "hack", "exploit", "breach", "lawsuit", "settlement", "fine", "investigation",
    "disable", "disables", "terminate", "terminates", "bankrupt", "insolvent",
]
# монеты, чьи слова в заголовке — матч по тикеру без USDT
TICKER_RE = re.compile(r"\b([A-Z0-9]{2,10})USDT\b|\b([A-Z0-9]{2,10})BUSD\b|\b([A-Z0-9]{2,10})USDC\b")


class NewsWatcher:
    """Новости Binance announcements: сентимент по монете за окно (4ч по умолчанию)."""

    def __init__(self, window_h: int = 4, poll_s: int = 300):
        self.window_h = window_h
        self.poll_s = poll_s
        self._items: deque = deque(maxlen=300)  # {"ts_ms", "title", "tickers", "label"}
        self._seen_ids: set = set()
        self._alias: Dict[str, str] = {}  # BASE -> symbol (наполняет скринер)

    def register_alias(self, symbol: str) -> None:
        base = symbol.split("_")[0].replace("USDT", "").replace("USDC", "")
        if base and base not in self._alias:
            self._alias[base] = symbol

    async def poll_loop(self) -> None:
        import asyncio

        while True:
            try:
                await self.poll_once()
            except Exception as e:
                log.warning("news poll failed: %s: %s", type(e).__name__, e)
            await asyncio.sleep(self.poll_s)

    async def poll_once(self) -> int:
        async with aiohttp.ClientSession() as session:
            async with session.get(ANNOUNCE_URL, timeout=aiohttp.ClientTimeout(total=20), headers=HEADERS) as r:
                r.raise_for_status()
                data = await r.json(content_type=None)
        catalogs = data.get("data", {}).get("catalogs", [])
        added = 0
        for cat in catalogs:
            for art in cat.get("articles", []):
                aid = art.get("id")
                if aid in self._seen_ids:
                    continue
                self._seen_ids.add(aid)
                title = str(art.get("title", ""))
                ts = int(art.get("releaseDate") or 0)
                if not ts:
                    continue
                ts_ms = ts if ts > 10**12 else ts * 1000
                tickers = self._extract_tickers(title)
                label = self._classify(title)
                self._items.append({"ts_ms": ts_ms, "title": title, "tickers": tickers, "label": label})
                added += 1
        return added

    def _extract_tickers(self, title: str) -> List[str]:
        out = set()
        for m in TICKER_RE.finditer(title.upper()):
            base = m.group(1) or m.group(2) or m.group(3)
            if base and base not in ("WILL", "THE", "AND"):
                out.add(base)
        # алиасы: полные имена в заголовке
        for base, symbol in self._alias.items():
            if len(base) >= 3 and re.search(r"\b" + re.escape(base) + r"\b", title.upper()):
                out.add(base)
        return sorted(out)

    @staticmethod
    def _classify(title: str) -> str:
        low = title.lower()
        has_neg = any(w in low for w in NEGATIVE_WORDS)
        has_pos = any(w in low for w in POSITIVE_WORDS)
        if has_neg and not has_pos:
            return "neg"
        if has_pos and not has_neg:
            return "pos"
        if has_neg and has_pos:
            # делистинг сильнее листинга в одном заголовке — негатив
            return "neg" if "delist" in low or "remove" in low else "pos"
        return "neutral"

    def for_symbol(self, symbol: str, window_h: Optional[int] = None) -> List[dict]:
        base = symbol.split("_")[0].replace("USDT", "").replace("USDC", "")
        window_ms = (window_h or self.window_h) * 3600 * 1000
        cutoff = time.time() * 1000 - window_ms
        out = []
        for it in self._items:
            if it["ts_ms"] < cutoff:
                continue
            if base and base in it["tickers"]:
                out.append(it)
        return out

    def sentiment(self, symbol: str) -> Tuple[Optional[str], List[dict]]:
        """('pos'|'neg'|'neutral'|None, items). None = новостей нет вообще."""
        items = self.for_symbol(symbol)
        if not items:
            return None, []
        labels = [it["label"] for it in items if it["label"] != "neutral"]
        if not labels:
            return "neutral", items
        neg = labels.count("neg")
        pos = labels.count("pos")
        # последняя новость имеет приоритет при равенстве
        if neg == pos:
            return items[-1]["label"], items
        return ("neg" if neg > pos else "pos"), items
