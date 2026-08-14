"""Small bounded TTL cache for search and extracted documents."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    value: T
    expires_at: float


class TTLCache(Generic[T]):
    def __init__(self, max_items: int = 256) -> None:
        self.max_items = max(1, max_items)
        self._items: OrderedDict[str, _Entry[T]] = OrderedDict()
        self._lock = RLock()

    def get(self, key: str) -> Optional[T]:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            if entry.expires_at <= time.monotonic():
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return entry.value

    def set(self, key: str, value: T, ttl_s: float) -> None:
        with self._lock:
            self._items[key] = _Entry(value, time.monotonic() + max(0.0, ttl_s))
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
