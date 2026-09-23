import pytest

from app.config import KeyPolicy, Settings


@pytest.fixture
def settings():
    return Settings(
        api_keys={"test-key": KeyPolicy(5, 1), "big-key": KeyPolicy(10_000, 10_000)},
        provider_order=["mock-primary", "mock-secondary", "mock-tertiary"],
        request_timeout_s=0.3,
        max_retries=1,
        backoff_base_s=0.001,
        breaker_failure_threshold=3,
        breaker_recovery_s=0.2,
        mock_latency_ms=1,
    )
