| Scenario | Success | Served by fallback | Req/s | p50 | p95 | p99 | Gateway overhead (mean) |
|---|---|---|---|---|---|---|---|
| Healthy (all providers up) | 100.0% | 0.0% | 183.1 | 198.3 ms | 699.9 ms | 1081.6 ms | 0.009 ms |
| Primary down | 100.0% | 100.0% | 205.7 | 180.5 ms | 618.9 ms | 904.4 ms | 0.018 ms |
| Primary + secondary down | 100.0% | 100.0% | 236.6 | 154.3 ms | 546.3 ms | 763.4 ms | 0.018 ms |
| Primary hangs (timeouts) | 100.0% | 100.0% | 163.1 | 189.0 ms | 933.5 ms | 1258.5 ms | 0.027 ms |
| Primary flaky (30% errors) | 100.0% | 11.65% | 171.6 | 226.3 ms | 708.6 ms | 1056.8 ms | 0.123 ms |
| Total outage (all down) | 0.0% | 0.0% | 303.7 | n/a | n/a | n/a | n/a |
