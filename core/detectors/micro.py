from typing import Dict, Optional, Tuple

from core.detectors.flicker import FlickerDetector
from core.detectors.icebergs import IcebergDetector
from core.detectors.walls import WallsDetector
from core.models import BookSnapshot, Trade

MIN_SCORE = 25


class MicroState:
    __slots__ = ("hft", "hft_info", "spoof", "spoof_info", "iceberg", "iceberg_info")

    def __init__(self):
        self.hft = 0
        self.hft_info = ""
        self.spoof = 0
        self.spoof_info = ""
        self.iceberg = 0
        self.iceberg_info = ""

    def any_significant(self) -> bool:
        return self.hft >= MIN_SCORE or self.spoof >= MIN_SCORE or self.iceberg >= MIN_SCORE

    def merge_into(self, row: dict) -> dict:
        row["hft"] = self.hft
        row["hft_info"] = self.hft_info
        row["spoof"] = self.spoof
        row["spoof_info"] = self.spoof_info
        row["iceberg"] = self.iceberg
        row["iceberg_info"] = self.iceberg_info
        return row


class MicroDetector:
    """Хаб микроструктурных детекторов: фликер (HFT), стены (спуфинг), айсберги."""

    def __init__(self, cfg: Optional[dict] = None):
        cfg = cfg or {}
        self.iceberg_min_exec = float(cfg.get("iceberg_min_exec", 1.0))
        self._flicker: Dict[Tuple[str, str], FlickerDetector] = {}
        self._walls: Dict[Tuple[str, str], WallsDetector] = {}
        self._iceberg: Dict[Tuple[str, str], IcebergDetector] = {}

    def _get(self, store: dict, cls, exchange: str, symbol: str):
        key = (exchange, symbol)
        det = store.get(key)
        if det is None:
            det = cls(exchange, symbol) if cls is not IcebergDetector else cls(exchange, symbol, self.iceberg_min_exec)
            store[key] = det
        return det

    def on_trade(self, trade: Trade) -> None:
        key = (trade.exchange, trade.symbol)
        if key in self._walls:
            self._walls[key].on_trade(trade)
        if key in self._iceberg:
            self._iceberg[key].on_trade(trade)

    def on_book(self, exchange: str, symbol: str, book: BookSnapshot) -> None:
        self._get(self._flicker, FlickerDetector, exchange, symbol).on_book(book)
        self._get(self._walls, WallsDetector, exchange, symbol).on_book(book)
        self._get(self._iceberg, IcebergDetector, exchange, symbol).on_book(book)

    def state(self, exchange: str, symbol: str) -> MicroState:
        key = (exchange, symbol)
        ms = MicroState()
        fl = self._flicker.get(key)
        if fl:
            ms.hft, ms.hft_info = fl.state()
        wl = self._walls.get(key)
        if wl:
            ms.spoof, ms.spoof_info = wl.state()
        ic = self._iceberg.get(key)
        if ic:
            ms.iceberg, ms.iceberg_info = ic.state()
        return ms

    def all_states(self) -> Dict[Tuple[str, str], MicroState]:
        keys = set(self._flicker) | set(self._walls) | set(self._iceberg)
        out = {}
        for key in keys:
            ms = self.state(key[0], key[1])
            if ms.any_significant():
                out[key] = ms
        return out

    def new_spoof_events(self) -> list:
        out = []
        for det in self._walls.values():
            while det._pending_alerts:
                out.append(det._pending_alerts.pop(0))
        return out

    def new_iceberg_events(self) -> list:
        out = []
        for det in self._iceberg.values():
            while det._pending_alerts:
                out.append(det._pending_alerts.pop(0))
        return out
