from app.circuit_breaker import CircuitBreaker


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(clock, threshold=3, recovery=10):
    return CircuitBreaker(threshold, recovery, clock)


def test_opens_after_threshold_consecutive_failures():
    b = make(FakeClock())
    for _ in range(2):
        b.record_failure()
    assert b.state == "closed" and b.allow()
    b.record_failure()
    assert b.state == "open" and not b.allow()


def test_success_resets_failure_count():
    b = make(FakeClock())
    b.record_failure(); b.record_failure(); b.record_success(); b.record_failure(); b.record_failure()
    assert b.state == "closed"


def test_half_open_allows_exactly_one_probe():
    clock = FakeClock()
    b = make(clock)
    for _ in range(3):
        b.record_failure()
    clock.t += 10
    assert b.state == "half_open"
    assert b.allow() is True      # the probe
    assert b.allow() is False     # everyone else waits


def test_probe_success_closes_circuit():
    clock = FakeClock()
    b = make(clock)
    for _ in range(3):
        b.record_failure()
    clock.t += 10
    b.allow(); b.record_success()
    assert b.state == "closed" and b.allow()


def test_probe_failure_reopens_circuit():
    clock = FakeClock()
    b = make(clock)
    for _ in range(3):
        b.record_failure()
    clock.t += 10
    b.allow(); b.record_failure()
    assert b.state == "open" and not b.allow()
    clock.t += 10
    assert b.state == "half_open"


def test_lost_probe_does_not_wedge_the_breaker():
    clock = FakeClock()
    b = make(clock)
    for _ in range(3):
        b.record_failure()
    clock.t += 10
    assert b.allow()              # probe starts, never reports back
    clock.t += 10
    assert b.allow()              # a new probe is allowed after another window
