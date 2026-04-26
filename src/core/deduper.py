"""In-memory dedup of observations by (station, raw_hash).

Persists soft-state to DB via storage.save_observation; this module is the
fast-path filter. After a process restart the DB layer is the source of truth.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from ..models import MetarObservation


class Deduper:
    def __init__(self, capacity: int = 4096) -> None:
        self._seen: set[tuple[str, str]] = set()
        self._order: deque[tuple[str, str]] = deque(maxlen=capacity)
        self._capacity = capacity

    def is_new(self, obs: MetarObservation) -> bool:
        key = (obs.station, obs.raw_hash)
        if key in self._seen:
            return False
        self._seen.add(key)
        if len(self._order) == self._capacity:
            old = self._order.popleft()
            self._seen.discard(old)
        self._order.append(key)
        return True

    def warm_up(self, hashes: Iterable[tuple[str, str]]) -> None:
        for k in hashes:
            self._seen.add(k)
            self._order.append(k)
