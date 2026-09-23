"""Load test + failure-injection benchmark for the gateway.

Start the gateway first (mock providers), then:
    python benchmarks/benchmark.py --url http://localhost:8000 --requests 2000 --concurrency 50

It runs several phases (healthy, primary down, two providers down, flaky, total outage),
sends N requests in each, and writes benchmarks/results.json and benchmarks/results.md.
Numbers measure the GATEWAY against mock providers on your own machine: they show routing
overhead, failover behaviour and throughput, not real vendor latency.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx

PHASES = [
    ("Healthy (all providers up)", {}),
    ("Primary down", {"mock-primary": ("down", None)}),
    ("Primary + secondary down", {"mock-primary": ("down", None), "mock-secondary": ("down", None)}),
    ("Primary hangs (timeouts)", {"mock-primary": ("slow", None)}),
    ("Primary flaky (30% errors)", {"mock-primary": ("flaky", 0.3)}),
    ("Total outage (all down)", {"mock-primary": ("down", None), "mock-secondary": ("down", None),
                                 "mock-tertiary": ("down", None)}),
]


def pct(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1, int(round(p / 100 * (len(sorted_vals) - 1))))] if sorted_vals else 0.0


async def run_phase(client, url, headers, n, concurrency):
    sem = asyncio.Semaphore(concurrency)
    lat, overhead, statuses, fallbacks = [], [], [], 0
    body = {"messages": [{"role": "user", "content": "benchmark request"}], "max_tokens": 32}

    async def one():
        nonlocal fallbacks
        async with sem:
            t0 = time.perf_counter()
            try:
                r = await client.post(f"{url}/chat", json=body, headers=headers)
                code = r.status_code
                if code == 200:
                    d = r.json()
                    overhead.append(d["gateway_overhead_ms"])
                    fallbacks += int(d["fallback_used"])
            except httpx.HTTPError:
                code = 0
            lat.append((time.perf_counter() - t0) * 1000)
            statuses.append(code)

    start = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(n)))
    wall = time.perf_counter() - start
    ok = sum(1 for s in statuses if s == 200)
    lat_ok = sorted(l for l, s in zip(lat, statuses) if s == 200)
    return {
        "requests": n, "success_rate_pct": round(ok / n * 100, 2), "fallback_pct": round(fallbacks / n * 100, 2),
        "http_503": statuses.count(503), "http_429": statuses.count(429), "errors": statuses.count(0),
        "rps": round(n / wall, 1),
        "latency_ms": {"p50": round(pct(lat_ok, 50), 1), "p95": round(pct(lat_ok, 95), 1), "p99": round(pct(lat_ok, 99), 1)},
        "gateway_overhead_ms": {"mean": round(statistics.mean(overhead), 3) if overhead else None,
                                "p95": round(pct(sorted(overhead), 95), 3) if overhead else None},
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--key", default="bench-key", help="API key with a high rate limit")
    ap.add_argument("--requests", type=int, default=2000)
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument("--recovery-wait", type=float, default=0.0, help="seconds to pause between phases")
    args = ap.parse_args()
    headers = {"X-API-Key": args.key}
    results = []

    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=args.concurrency * 2)) as client:
        assert (await client.get(f"{args.url}/health")).status_code == 200, "gateway is not reachable"
        for name, modes in PHASES:
            await client.post(f"{args.url}/admin/reset", headers=headers)
            for provider, (mode, rate) in modes.items():
                await client.post(f"{args.url}/admin/providers/{provider}/mode", headers=headers,
                                  json={"mode": mode, **({"failure_rate": rate} if rate is not None else {})})
            print(f"Running: {name} ...", flush=True)
            res = await run_phase(client, args.url, headers, args.requests, args.concurrency)
            res["phase"] = name
            results.append(res)
            print(f"  success {res['success_rate_pct']}%  rps {res['rps']}  p95 {res['latency_ms']['p95']} ms  "
                  f"overhead {res['gateway_overhead_ms']['mean']} ms", flush=True)
            if args.recovery_wait:
                await asyncio.sleep(args.recovery_wait)
        await client.post(f"{args.url}/admin/reset", headers=headers)

    out = Path(__file__).parent
    (out / "results.json").write_text(json.dumps(results, indent=2))
    lines = ["| Scenario | Success | Served by fallback | Req/s | p50 | p95 | p99 | Gateway overhead (mean) |",
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        oh = r["gateway_overhead_ms"]["mean"]
        has = r["success_rate_pct"] > 0
        lat = (lambda k: f"{r['latency_ms'][k]} ms") if has else (lambda k: "n/a")
        lines.append(f"| {r['phase']} | {r['success_rate_pct']}% | {r['fallback_pct']}% | {r['rps']} | "
                     f"{lat('p50')} | {lat('p95')} | {lat('p99')} | {'n/a' if oh is None else f'{oh} ms'} |")
    (out / "results.md").write_text("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
