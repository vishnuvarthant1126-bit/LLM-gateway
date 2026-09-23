"""Gateway configuration. Everything can be overridden with environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class KeyPolicy:
    """Token-bucket settings for one gateway API key."""
    capacity: float          # max burst size (tokens)
    refill_per_sec: float    # sustained requests per second


@dataclass
class Settings:
    api_keys: dict[str, KeyPolicy] = field(default_factory=dict)
    provider_order: list[str] = field(default_factory=list)

    request_timeout_s: float = 5.0        # per provider attempt
    max_retries: int = 1                  # extra tries on the SAME provider before falling back
    backoff_base_s: float = 0.05          # exponential backoff base between retries

    breaker_failure_threshold: int = 3    # consecutive failures before the circuit opens
    breaker_recovery_s: float = 10.0      # how long the circuit stays open before a probe

    mock_latency_ms: float = 30.0

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_model: str = "claude-haiku-4-5-20251001"
    gemini_model: str = "gemini-3.8-flash"


def parse_api_keys(raw: str, default_capacity: float, default_refill: float) -> dict[str, KeyPolicy]:
    """Parse 'key' or 'key:capacity:refill' entries separated by commas."""
    keys: dict[str, KeyPolicy] = {}
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        parts = entry.split(":")
        capacity = float(parts[1]) if len(parts) > 1 else default_capacity
        refill = float(parts[2]) if len(parts) > 2 else default_refill
        keys[parts[0]] = KeyPolicy(capacity, refill)
    return keys


def load_settings() -> Settings:
    env = os.environ.get
    cap = float(env("RATE_LIMIT_CAPACITY", "20"))
    refill = float(env("RATE_LIMIT_REFILL_PER_SEC", "5"))
    s = Settings(
        api_keys=parse_api_keys(env("GATEWAY_API_KEYS", "demo-key,bench-key:1000000:1000000"), cap, refill),
        request_timeout_s=float(env("REQUEST_TIMEOUT_S", "5")),
        max_retries=int(env("MAX_RETRIES", "1")),
        breaker_failure_threshold=int(env("BREAKER_FAILURE_THRESHOLD", "3")),
        breaker_recovery_s=float(env("BREAKER_RECOVERY_S", "10")),
        mock_latency_ms=float(env("MOCK_LATENCY_MS", "30")),
        openai_api_key=env("OPENAI_API_KEY", ""),
        anthropic_api_key=env("ANTHROPIC_API_KEY", ""),
        gemini_api_key=env("GEMINI_API_KEY", ""),
        openai_model=env("OPENAI_MODEL", "gpt-4o-mini"),
        anthropic_model=env("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        gemini_model=env("GEMINI_MODEL", "gemini-3.8-flash"),
    )

    order_env = env("PROVIDER_ORDER", "").strip()
    if order_env:
        s.provider_order = [p.strip() for p in order_env.split(",") if p.strip()]
    elif env("USE_MOCKS", "").lower() in ("1", "true", "yes"):
        s.provider_order = ["mock-primary", "mock-secondary", "mock-tertiary"]
    else:
        real = [n for n, k in (("openai", s.openai_api_key),
                               ("anthropic", s.anthropic_api_key),
                               ("gemini", s.gemini_api_key)) if k]
        s.provider_order = real or ["mock-primary", "mock-secondary", "mock-tertiary"]
    return s
