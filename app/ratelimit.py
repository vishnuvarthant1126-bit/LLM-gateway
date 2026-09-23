"""Token-bucket rate limiter, one bucket per API key.

Each key has a bucket holding up to `capacity` tokens that refills at
`refill_per_sec`. A request costs one token. This allows short bursts
(up to capacity) while enforcing a sustained average rate.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .config import KeyPolicy


@dataclass
class Decision:
    allowed: bool
    remaining: int
    retry_after_s: float


class TokenBucketLimiter:
    def __init__(self, policies: dict[str, KeyPolicy], clock: Callable[[], float] = time.monotonic):
        self._policies = policies
        self._clock = clock
        self._buckets: dict[str, list[float]] = {}  # key -> [tokens, last_refill_time]

    def check(self, key: str, cost: float = 1.0) -> Decision:
        policy = self._policies[key]
        now = self._clock()
        tokens, last = self._buckets.get(key, [policy.capacity, now])
        tokens = min(policy.capacity, tokens + (now - last) * policy.refill_per_sec)
        if tokens >= cost:
            tokens -= cost
            self._buckets[key] = [tokens, now]
            return Decision(True, int(tokens), 0.0)
        self._buckets[key] = [tokens, now]
        retry_after = (cost - tokens) / policy.refill_per_sec if policy.refill_per_sec > 0 else float("inf")
        return Decision(False, 0, retry_after)
