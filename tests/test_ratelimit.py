from app.config import KeyPolicy
from app.ratelimit import TokenBucketLimiter


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_burst_then_block():
    clock = FakeClock()
    limiter = TokenBucketLimiter({"k": KeyPolicy(capacity=3, refill_per_sec=1)}, clock)
    assert [limiter.check("k").allowed for _ in range(4)] == [True, True, True, False]


def test_refills_over_time():
    clock = FakeClock()
    limiter = TokenBucketLimiter({"k": KeyPolicy(3, 1)}, clock)
    for _ in range(3):
        limiter.check("k")
    assert not limiter.check("k").allowed
    clock.t += 2.0                      # two tokens refill
    assert limiter.check("k").allowed
    assert limiter.check("k").allowed
    assert not limiter.check("k").allowed


def test_never_exceeds_capacity():
    clock = FakeClock()
    limiter = TokenBucketLimiter({"k": KeyPolicy(2, 1)}, clock)
    clock.t += 1000
    assert [limiter.check("k").allowed for _ in range(3)] == [True, True, False]


def test_keys_are_independent():
    clock = FakeClock()
    limiter = TokenBucketLimiter({"a": KeyPolicy(1, 1), "b": KeyPolicy(1, 1)}, clock)
    assert limiter.check("a").allowed
    assert not limiter.check("a").allowed
    assert limiter.check("b").allowed


def test_retry_after_is_reported():
    clock = FakeClock()
    limiter = TokenBucketLimiter({"k": KeyPolicy(1, 0.5)}, clock)
    limiter.check("k")
    d = limiter.check("k")
    assert not d.allowed and abs(d.retry_after_s - 2.0) < 1e-6
