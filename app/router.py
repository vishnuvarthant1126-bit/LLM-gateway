"""Fallback router: tries providers in priority order.

For each provider:
  1. skip it if its circuit breaker is open
  2. call it with a timeout; on a retryable error, retry with exponential backoff
  3. if it still fails, record the failure and fall back to the next provider
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field

from .circuit_breaker import CircuitBreaker
from .config import Settings
from .metrics import Metrics
from .providers import Completion, Provider, ProviderError


@dataclass
class Attempt:
    provider: str
    outcome: str            # ok | error | timeout | circuit_open
    latency_ms: float = 0.0
    error: str | None = None


@dataclass
class RouteResult:
    completion: Completion
    provider: Provider
    attempts: list[Attempt]
    total_ms: float
    overhead_ms: float      # gateway time excluding provider calls and deliberate backoff sleeps
    fallback_used: bool
    cost_usd: float


class AllProvidersFailed(Exception):
    def __init__(self, attempts: list[Attempt], total_ms: float):
        super().__init__("all providers failed")
        self.attempts = attempts
        self.total_ms = total_ms


class Router:
    def __init__(self, providers: list[Provider], settings: Settings, metrics: Metrics):
        self.providers = providers
        self.by_name = {p.name: p for p in providers}
        self.settings = settings
        self.metrics = metrics
        self.breakers = {
            p.name: CircuitBreaker(settings.breaker_failure_threshold, settings.breaker_recovery_s)
            for p in providers
        }

    def _order(self, preferred: str | None) -> list[Provider]:
        if preferred and preferred in self.by_name:
            return [self.by_name[preferred]] + [p for p in self.providers if p.name != preferred]
        return list(self.providers)

    async def complete(self, messages: list[dict], max_tokens: int, preferred: str | None = None) -> RouteResult:
        start = time.perf_counter()
        attempts: list[Attempt] = []
        backoff_total = 0.0
        order = self._order(preferred)

        for idx, provider in enumerate(order):
            breaker = self.breakers[provider.name]
            for try_no in range(self.settings.max_retries + 1):
                if not breaker.allow():
                    attempts.append(Attempt(provider.name, "circuit_open"))
                    self.metrics.record_attempt(provider.name, ok=False, skipped=True)
                    break

                t0 = time.perf_counter()
                error: str | None = None
                outcome = "ok"
                retryable = True
                completion: Completion | None = None
                try:
                    completion = await asyncio.wait_for(
                        provider.complete(messages, max_tokens), timeout=self.settings.request_timeout_s)
                except asyncio.TimeoutError:
                    outcome, error = "timeout", f"no response within {self.settings.request_timeout_s}s"
                except ProviderError as exc:
                    outcome, error, retryable = "error", str(exc), exc.retryable
                except Exception as exc:  # never let one adapter bug take the gateway down
                    outcome, error = "error", f"{exc.__class__.__name__}: {exc}"
                latency_ms = (time.perf_counter() - t0) * 1000

                if completion is not None:
                    breaker.record_success()
                    attempts.append(Attempt(provider.name, "ok", latency_ms))
                    self.metrics.record_attempt(provider.name, ok=True)
                    total_ms = (time.perf_counter() - start) * 1000
                    provider_ms = sum(a.latency_ms for a in attempts)
                    overhead = max(0.0, total_ms - provider_ms - backoff_total * 1000)
                    cost = provider.cost(completion.prompt_tokens, completion.completion_tokens)
                    fallback = provider is not order[0]
                    self.metrics.record_success(provider.name, total_ms, overhead, completion.prompt_tokens,
                                                completion.completion_tokens, cost, fallback)
                    return RouteResult(completion, provider, attempts, total_ms, overhead, fallback, cost)

                breaker.record_failure()
                attempts.append(Attempt(provider.name, outcome, latency_ms, error))
                self.metrics.record_attempt(provider.name, ok=False)
                if not retryable or try_no == self.settings.max_retries:
                    break
                delay = self.settings.backoff_base_s * (2 ** try_no) * (1 + random.random())
                backoff_total += delay
                await asyncio.sleep(delay)

        total_ms = (time.perf_counter() - start) * 1000
        self.metrics.record_failure(total_ms)
        raise AllProvidersFailed(attempts, total_ms)

    def provider_status(self) -> list[dict]:
        out = []
        for p in self.providers:
            b = self.breakers[p.name]
            info = {"name": p.name, "kind": p.kind, "model": p.model, "circuit": b.state,
                    "consecutive_failures": b.consecutive_failures, "times_opened": b.times_opened}
            if p.kind == "mock":
                info["mode"] = p.mode
                info["failure_rate"] = p.failure_rate
            out.append(info)
        return out
