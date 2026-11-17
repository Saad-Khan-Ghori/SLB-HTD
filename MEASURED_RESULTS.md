# SLB-HTD: Measured Results

**Run date:** 3 November 2026. **Source:** 60 timed `hey` trials across E1–E4, with per-trial client, proxy, and backend logs in `experiments/raw_runs/`. Each condition has three repetitions. This report replaces the numerical conclusions in the archived modelled report; the measured tables are in `experiments/raw_60_runs.csv` and `experiments/summary_tables_E1_to_E4.csv`.

## Measurement and validation

The benchmark used seven Linux network namespaces, three delayed HTTP backends (0, 50, and 150 ms), one request per TCP connection, and 0%, 2%, or 5% transit-link loss as specified by the trial labels. Throughput is the `hey` requests-per-second value; p95 is the `hey` 95th percentile. Error rate counts non-200 HTTP responses and client connection errors. Values below are mean ± sample standard deviation over three runs. All 60 expected trial tags and their client, proxy, and backend logs are present. Outside E3, backend request counts exactly equal completed client requests; one E3 trial differs by one request at the stop boundary.

**Important limitation:** The client recorded 6,473 `dial tcp ... cannot assign requested address` errors across the run. This is consistent with source-port exhaustion from repeated short connections and limits policy and scaling comparisons. The 0% loss results also vary substantially between repetitions. Treat this run as a measured baseline with a client-side bottleneck, not a clean measurement of proxy capacity.

## E1: Round robin versus least connections

| SLB policy | Requests/s | p95 (ms) | Error rate (%) | B1 / B2 / B3 request share |
| --- | ---: | ---: | ---: | --- |
| Round-Robin | 555.9 ± 112.6 | 193.9 ± 11.1 | 0.82 ± 1.42 | 33.3% / 33.3% / 33.3% |
| Least-Connections | 479.0 ± 122.4 | 231.2 ± 15.8 | 4.11 ± 0.39 | 63.0% / 24.9% / 12.0% |

Least connections did steer more traffic to B1, but it did not improve throughput or p95 in this run. Its three trials had 1,814 client source-port errors, versus 317 for round robin. The large run-to-run spread prevents a strong policy ranking from these data.

## E2: Transit packet loss

| Loss | Proxy | Requests/s | p95 (ms) | Error rate (%) |
| ---: | --- | ---: | ---: | ---: |
| 0% | SLB | 549.2 ± 112.8 | 196.5 ± 11.6 | 0.80 ± 1.12 |
| 0% | HAProxy | 623.0 ± 91.6 | 178.9 ± 16.4 | 0.00 ± 0.00 |
| 2% | SLB | 385.0 ± 7.0 | 265.3 ± 5.0 | 0.00 ± 0.00 |
| 2% | HAProxy | 412.8 ± 5.0 | 230.7 ± 25.4 | 0.00 ± 0.00 |
| 5% | SLB | 226.9 ± 11.1 | 1,101.0 ± 18.3 | 0.00 ± 0.00 |
| 5% | HAProxy | 231.8 ± 4.6 | 1,097.4 ± 3.6 | 0.00 ± 0.00 |

For both proxies, measured throughput fell and p95 increased as packet loss rose. At 5% loss, p95 was about 1.1 seconds for both. The SLB 0% condition includes 323 client source-port errors; the 2% and 5% E2 conditions reported none. Do not interpret the 0% comparison as an isolated proxy overhead measurement.

## E3: Backend failure and recovery

| Loss | Proxy | Requests/s | Error rate (%) | Detection (s) | Recovery (s) |
| ---: | --- | ---: | ---: | ---: | ---: |
| 0% | SLB | 517.8 ± 7.2 | 2.59 ± 0.21 | 2.99 ± 0.06 | 1.98 ± 0.04 |
| 0% | HAProxy | 620.2 ± 11.9 | 0.18 ± 0.01 | — | 1.38 ± 0.05 |
| 2% | SLB | 388.4 ± 4.7 | 1.88 ± 0.11 | 2.98 ± 0.02 | 1.97 ± 0.04 |
| 2% | HAProxy | 386.8 ± 9.7 | 0.24 ± 0.01 | — | 1.26 ± 0.08 |
| 5% | SLB | 234.1 ± 2.8 | 1.78 ± 0.20 | 2.97 ± 0.01 | 1.98 ± 0.01 |
| 5% | HAProxy | 230.7 ± 3.8 | 0.35 ± 0.01 | — | 1.29 ± 0.03 |

B2 was stopped around 30 seconds and restarted around 45 seconds in each 60-second trial. SLB logged a DOWN transition after about 3.0 seconds; B2 served its first post-restart request after about 2.0 seconds. Recovery is measured from restart to that first B2 request for both proxies. HAProxy health-transition messages have no timestamps, so its detection time is **unavailable**, not zero. The higher SLB error rates include failure-window HTTP 502 responses and client source-port errors; HAProxy returned HTTP 502/503 during failover. Exact failure causes are distinguishable in each `hey` output.

## E4: Concurrency scaling

| Clients | Proxy | Requests/s | p95 (ms) | Error rate (%) |
| ---: | --- | ---: | ---: | ---: |
| 50 | SLB | 541.1 ± 115.4 | 203.4 ± 24.5 | 0.00 ± 0.00 |
| 50 | HAProxy | 643.7 ± 53.2 | 175.3 ± 8.3 | 0.00 ± 0.00 |
| 200 | SLB | 569.4 ± 210.6 | 846.0 ± 543.6 | 5.09 ± 8.82 |
| 200 | HAProxy | 1,516.2 ± 259.4 | 350.5 ± 93.7 | 0.00 ± 0.00 |
| 500 | SLB | 532.8 ± 130.5 | 2,052.8 ± 1,433.5 | 4.60 ± 7.97 |
| 500 | HAProxy | 1,640.6 ± 22.8 | 700.1 ± 37.5 | 0.00 ± 0.00 |

HAProxy throughput increased from about 644 to 1,641 requests/s across 50–500 clients, with rising p95 latency. SLB throughput remained near 530–570 requests/s and its p95 rose sharply. One SLB trial at 200 clients and one at 500 clients hit 1,506 and 1,615 client source-port errors respectively; therefore these E4 numbers do not isolate the proxy's maximum capacity.

## Interpretation and next measurement

The live namespace topology, scheduling, health checks, and failover generated auditable logs. The clearest measured trend is the effect of transit loss on tail latency. The E1 and E4 comparisons need a controlled rerun after addressing the client source-port bottleneck and stabilizing the load generator. Preserve this run unchanged as the baseline, record any network or kernel setting changes, then rerun all compared conditions under the same settings. Live packet captures were not part of this 60-run suite and should be collected separately with `run_poc_demo.bash` if packet-level evidence is needed.
