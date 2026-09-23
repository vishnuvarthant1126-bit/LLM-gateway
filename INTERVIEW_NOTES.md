# Interview notes

Be ready to explain these in your own words.

**Why a gateway?** Apps that call one LLM vendor directly go down when that vendor does, and every team re-implements retries and keys. A gateway centralises reliability, limits and cost tracking.

**Token bucket vs fixed window.** A fixed window lets a client send 2x the limit around a window boundary. A token bucket allows a controlled burst (capacity) but enforces the average rate (refill). Code: `app/ratelimit.py`.

**Circuit breaker.** Without one, every request still waits for a dead provider's timeout before falling back. States: closed (normal), open (skip), half-open (one probe). Code and edge case (lost probe): `app/circuit_breaker.py`.

**Retry vs fallback.** Retry the same provider once for transient errors (429/5xx/network), with exponential backoff plus jitter so clients do not retry in lockstep. Non-retryable errors (bad request, bad shape) skip straight to the next provider. Code: `app/router.py`.

**Timeouts.** `asyncio.wait_for` cancels a hung provider call so one slow vendor cannot tie up workers.

**What you would change for production.** Shared rate-limit state in Redis; persistent metrics (Prometheus); secrets management; streaming; per-provider concurrency limits; request/response logging with PII controls; a proper auth store instead of env-var keys.

**Known limits (say them before they ask).** Rate limiter and breaker state are per process. Real-provider adapters were not run against live APIs in the sandbox. Benchmarks use mock providers.
