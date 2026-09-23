"""Circuit breaker: stop sending traffic to a provider that keeps failing.

closed     -> normal. Consecutive failures are counted.
open       -> provider is skipped for `recovery_s` seconds.
half_open  -> after the wait, ONE probe request is allowed through.
              success closes the circuit, failure re-opens it.
"""
from __future__ import annotations

import time
from typing import Callable


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_s: float = 10.0,
                 clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = failure_threshold
        self.recovery_s = recovery_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_started_at: float | None = None
        self.times_opened = 0

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.recovery_s:
            return "half_open"
        return "open"

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    def allow(self) -> bool:
        """May a request be sent to this provider right now?"""
        state = self.state
        if state == "closed":
            return True
        if state == "open":
            return False
        # half_open: let one probe through. If a probe never reports back
        # (e.g. client disconnected), allow another after another recovery window.
        now = self._clock()
        if self._probe_started_at is None or now - self._probe_started_at >= self.recovery_s:
            self._probe_started_at = now
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._probe_started_at = None

    def record_failure(self) -> None:
        if self._opened_at is not None:  # failed probe -> open again
            self._opened_at = self._clock()
            self._probe_started_at = None
            self.times_opened += 1
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = self._clock()
            self._probe_started_at = None
            self.times_opened += 1

    def reset(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._probe_started_at = None
