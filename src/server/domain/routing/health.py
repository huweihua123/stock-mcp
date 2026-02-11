# src/server/domain/routing/health.py
"""Provider health tracking for routing decisions."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Tuple


@dataclass
class ProviderEvent:
    ts: float
    status: str  # success | empty | error
    latency_ms: float


class ProviderHealthTracker:
    def __init__(
        self,
        window_size: int = 50,
        empty_threshold: float = 0.5,
        error_threshold: float = 0.3,
        cooldown_seconds: int = 300,
    ):
        self._window_size = window_size
        self._empty_threshold = empty_threshold
        self._error_threshold = error_threshold
        self._cooldown_seconds = cooldown_seconds
        self._events: Dict[Tuple[str, str, str], Deque[ProviderEvent]] = {}
        self._cooldown_until: Dict[Tuple[str, str, str], float] = {}

    def record(self, provider: str, asset_type: str, data_type: str, status: str, latency_ms: float = 0.0) -> None:
        key = (provider, asset_type, data_type)
        q = self._events.get(key)
        if q is None:
            q = deque(maxlen=self._window_size)
            self._events[key] = q
        q.append(ProviderEvent(ts=time.time(), status=status, latency_ms=latency_ms))

        # update cooldown
        if self._should_cooldown(q):
            self._cooldown_until[key] = time.time() + self._cooldown_seconds

    def is_available(self, provider: str, asset_type: str, data_type: str) -> bool:
        key = (provider, asset_type, data_type)
        until = self._cooldown_until.get(key)
        if until and time.time() < until:
            return False
        return True

    def _should_cooldown(self, events: Deque[ProviderEvent]) -> bool:
        if not events:
            return False
        total = len(events)
        empty = sum(1 for e in events if e.status == "empty")
        error = sum(1 for e in events if e.status == "error")
        empty_rate = empty / total
        error_rate = error / total
        return empty_rate > self._empty_threshold or error_rate > self._error_threshold
