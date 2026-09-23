"""In-memory metrics: counters, latency percentiles, per-provider stats, recent events."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, max(0, int(round(p / 100 * (len(sorted_values) - 1)))))
    return sorted_values[idx]


@dataclass
class ProviderStats:
    requests: int = 0        # attempts sent to this provider
    successes: int = 0
    failures: int = 0
    skipped_open: int = 0    # times skipped because its circuit was open
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class Metrics:
    started_at: float = field(default_factory=time.time)
    total: int = 0
    succeeded: int = 0
    failed: int = 0          # all providers failed (HTTP 503)
    rate_limited: int = 0    # HTTP 429
    fallbacks: int = 0       # succeeded, but not on the first-choice provider
    total_cost_usd: float = 0.0
    latencies_ms: deque = field(default_factory=lambda: deque(maxlen=5000))
    overheads_ms: deque = field(default_factory=lambda: deque(maxlen=5000))
    completions: deque = field(default_factory=lambda: deque(maxlen=20000))
    events: deque = field(default_factory=lambda: deque(maxlen=40))
    providers: dict = field(default_factory=dict)

    def provider(self, name: str) -> ProviderStats:
        return self.providers.setdefault(name, ProviderStats())

    def record_attempt(self, name: str, ok: bool, skipped: bool = False) -> None:
        p = self.provider(name)
        if skipped:
            p.skipped_open += 1
            return
        p.requests += 1
        if ok:
            p.successes += 1
        else:
            p.failures += 1

    def record_success(self, provider: str, latency_ms: float, overhead_ms: float,
                       prompt_tokens: int, completion_tokens: int, cost: float, fallback: bool) -> None:
        self.total += 1
        self.succeeded += 1
        self.fallbacks += int(fallback)
        self.total_cost_usd += cost
        self.latencies_ms.append(latency_ms)
        self.overheads_ms.append(overhead_ms)
        self.completions.append(time.time())
        p = self.provider(provider)
        p.prompt_tokens += prompt_tokens
        p.completion_tokens += completion_tokens
        p.cost_usd += cost
        self.events.appendleft({"t": time.time(), "kind": "fallback" if fallback else "ok",
                                "provider": provider, "latency_ms": round(latency_ms, 1)})

    def record_failure(self, latency_ms: float) -> None:
        self.total += 1
        self.failed += 1
        self.events.appendleft({"t": time.time(), "kind": "failed", "provider": "-",
                                "latency_ms": round(latency_ms, 1)})

    def record_rate_limited(self) -> None:
        self.total += 1
        self.rate_limited += 1
        self.events.appendleft({"t": time.time(), "kind": "rate_limited", "provider": "-", "latency_ms": 0})

    def snapshot(self) -> dict:
        lat = sorted(self.latencies_ms)
        ovh = sorted(self.overheads_ms)
        now = time.time()
        recent = sum(1 for t in self.completions if now - t <= 10)
        handled = self.succeeded + self.failed
        return {
            "uptime_s": round(now - self.started_at, 1),
            "total_requests": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "rate_limited": self.rate_limited,
            "fallbacks": self.fallbacks,
            "success_rate": round(self.succeeded / handled * 100, 2) if handled else None,
            "requests_per_sec_10s": round(recent / 10, 2),
            "latency_ms": {"p50": round(_percentile(lat, 50), 1), "p95": round(_percentile(lat, 95), 1),
                           "p99": round(_percentile(lat, 99), 1)},
            "gateway_overhead_ms": {"mean": round(sum(ovh) / len(ovh), 3) if ovh else 0.0,
                                    "p95": round(_percentile(ovh, 95), 3)},
            "total_cost_usd": round(self.total_cost_usd, 6),
            "providers_stats": {n: vars(s) | {"cost_usd": round(s.cost_usd, 6)} for n, s in self.providers.items()},
            "recent_events": list(self.events),
        }
