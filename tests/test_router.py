import asyncio

import pytest

from app.metrics import Metrics
from app.providers import MockProvider, build_providers
from app.router import AllProvidersFailed, Router

MSG = [{"role": "user", "content": "hi"}]


def make_router(settings):
    metrics = Metrics()
    providers = build_providers(settings)
    return Router(providers, settings, metrics), metrics


def run(coro):
    return asyncio.run(coro)


def test_uses_first_provider_when_healthy(settings):
    router, _ = make_router(settings)
    r = run(router.complete(MSG, 50))
    assert r.provider.name == "mock-primary" and not r.fallback_used


def test_falls_back_when_primary_down(settings):
    router, metrics = make_router(settings)
    router.by_name["mock-primary"].set_mode("down")
    r = run(router.complete(MSG, 50))
    assert r.provider.name == "mock-secondary" and r.fallback_used
    assert metrics.fallbacks == 1


def test_falls_back_twice(settings):
    router, _ = make_router(settings)
    router.by_name["mock-primary"].set_mode("down")
    router.by_name["mock-secondary"].set_mode("down")
    assert run(router.complete(MSG, 50)).provider.name == "mock-tertiary"


def test_timeout_triggers_fallback(settings):
    router, _ = make_router(settings)
    router.by_name["mock-primary"].set_mode("slow")
    r = run(router.complete(MSG, 50))
    assert r.provider.name == "mock-secondary"
    assert any(a.outcome == "timeout" for a in r.attempts)


def test_raises_when_all_fail(settings):
    router, metrics = make_router(settings)
    for p in router.providers:
        p.set_mode("down")
    with pytest.raises(AllProvidersFailed) as exc:
        run(router.complete(MSG, 50))
    assert metrics.failed == 1 and len(exc.value.attempts) >= 3


def test_circuit_opens_and_provider_is_skipped(settings):
    router, _ = make_router(settings)
    router.by_name["mock-primary"].set_mode("down")
    run(router.complete(MSG, 50))                        # 2 failures on primary (try + retry)
    run(router.complete(MSG, 50))                        # third failure opens the circuit
    assert router.breakers["mock-primary"].state == "open"
    r = run(router.complete(MSG, 50))
    assert r.attempts[0].outcome == "circuit_open" and r.provider.name == "mock-secondary"


def test_circuit_recovers_after_provider_heals(settings):
    router, _ = make_router(settings)
    primary = router.by_name["mock-primary"]
    primary.set_mode("down")
    for _ in range(2):
        run(router.complete(MSG, 50))
    assert router.breakers["mock-primary"].state == "open"
    primary.set_mode("healthy")
    import time; time.sleep(settings.breaker_recovery_s + 0.05)
    r = run(router.complete(MSG, 50))                    # half-open probe succeeds
    assert r.provider.name == "mock-primary"
    assert router.breakers["mock-primary"].state == "closed"


def test_preferred_provider_goes_first(settings):
    router, _ = make_router(settings)
    r = run(router.complete(MSG, 50, preferred="mock-tertiary"))
    assert r.provider.name == "mock-tertiary" and not r.fallback_used


def test_cost_and_tokens_recorded(settings):
    router, metrics = make_router(settings)
    r = run(router.complete(MSG, 50))
    assert r.cost_usd > 0 and metrics.total_cost_usd > 0
