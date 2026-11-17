#!/usr/bin/env python3
"""Build the measured-results report from the validated 60-trial CSV and raw logs."""

from __future__ import annotations

import csv
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "experiments" / "raw_runs"
TABLE = ROOT / "experiments" / "raw_60_runs.csv"
OUT = ROOT / "MEASURED_RESULTS.md"


def mean_sd(group: list[dict[str, str]], key: str, digits: int = 1) -> str:
    values = [float(row[key]) for row in group if row[key]]
    if not values:
        return "—"
    return f"{statistics.mean(values):,.{digits}f} ± {statistics.stdev(values):,.{digits}f}"


def main() -> None:
    with TABLE.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 60:
        raise ValueError(f"Expected 60 measured trials, found {len(rows)}")
    groups: dict[tuple[str, str, str, int, int], list[dict[str, str]]] = {}
    port_errors = 0
    missing = []
    for row in rows:
        key = (row["exp_id"], row["proxy"], row["policy"], int(row["loss_pct"]), int(row["concurrency"]))
        groups.setdefault(key, []).append(row)
        tag = f"{row['exp_id']}_{row['proxy'].lower()}_{'lc' if row['policy'] == 'Least-Connections' else 'rr'}_loss{row['loss_pct']}_c{row['concurrency']}_r{row['run']}"
        for suffix in ("_hey.txt", "_proxy.log", "_b1.log", "_b2.log", "_b3.log") + (("_events.txt",) if row["exp_id"] == "E3" else ()):
            if not (RAW / f"{tag}{suffix}").is_file():
                missing.append(f"{tag}{suffix}")
        hey = (RAW / f"{tag}_hey.txt").read_text(encoding="utf-8", errors="replace")
        port_errors += sum(int(n) for n in re.findall(r"^\s*\[(\d+)\]\s+Get .*cannot assign requested address", hey, re.MULTILINE))
    if missing or len(groups) != 20 or any(len(group) != 3 for group in groups.values()):
        raise ValueError(f"Incomplete evidence: {len(missing)} missing files, {len(groups)} groups")

    parts = [
        "# SLB-HTD: Measured Results",
        "",
        "**Run date:** 3 November 2026. **Source:** 60 timed `hey` trials across E1–E4, with per-trial client, proxy, and backend logs in `experiments/raw_runs/`. Each condition has three repetitions. This report replaces the numerical conclusions in the archived modelled report; the measured tables are in `experiments/raw_60_runs.csv` and `experiments/summary_tables_E1_to_E4.csv`.",
        "",
        "## Measurement and validation",
        "",
        "The benchmark used seven Linux network namespaces, three delayed HTTP backends (0, 50, and 150 ms), one request per TCP connection, and 0%, 2%, or 5% transit-link loss as specified by the trial labels. Throughput is the `hey` requests-per-second value; p95 is the `hey` 95th percentile. Error rate counts non-200 HTTP responses and client connection errors. Values below are mean ± sample standard deviation over three runs. All 60 expected trial tags and their client, proxy, and backend logs are present. Outside E3, backend request counts exactly equal completed client requests; one E3 trial differs by one request at the stop boundary.",
        "",
        f"**Important limitation:** The client recorded {port_errors:,} `dial tcp ... cannot assign requested address` errors across the run. This is consistent with source-port exhaustion from repeated short connections and limits policy and scaling comparisons. The 0% loss results also vary substantially between repetitions. Treat this run as a measured baseline with a client-side bottleneck, not a clean measurement of proxy capacity.",
        "",
        "## E1: Round robin versus least connections",
        "",
        "| SLB policy | Requests/s | p95 (ms) | Error rate (%) | B1 / B2 / B3 request share |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for policy in ("Round-Robin", "Least-Connections"):
        group = groups[("E1", "SLB", policy, 0, 50)]
        totals = [statistics.mean(int(row[f"b{i}_requests"]) for row in group) for i in (1, 2, 3)]
        share = " / ".join(f"{100 * count / sum(totals):.1f}%" for count in totals)
        parts.append(f"| {policy} | {mean_sd(group, 'rps')} | {mean_sd(group, 'p95_ms')} | {mean_sd(group, 'error_rate_pct', 2)} | {share} |")
    parts += [
        "",
        "Least connections did steer more traffic to B1, but it did not improve throughput or p95 in this run. Its three trials had 1,814 client source-port errors, versus 317 for round robin. The large run-to-run spread prevents a strong policy ranking from these data.",
        "",
        "## E2: Transit packet loss",
        "",
        "| Loss | Proxy | Requests/s | p95 (ms) | Error rate (%) |",
        "| ---: | --- | ---: | ---: | ---: |",
    ]
    for loss in (0, 2, 5):
        for proxy in ("SLB", "HAProxy"):
            group = groups[("E2", proxy, "Round-Robin", loss, 50)]
            parts.append(f"| {loss}% | {proxy} | {mean_sd(group, 'rps')} | {mean_sd(group, 'p95_ms')} | {mean_sd(group, 'error_rate_pct', 2)} |")
    parts += [
        "",
        "For both proxies, measured throughput fell and p95 increased as packet loss rose. At 5% loss, p95 was about 1.1 seconds for both. The SLB 0% condition includes 323 client source-port errors; the 2% and 5% E2 conditions reported none. Do not interpret the 0% comparison as an isolated proxy overhead measurement.",
        "",
        "## E3: Backend failure and recovery",
        "",
        "| Loss | Proxy | Requests/s | Error rate (%) | Detection (s) | Recovery (s) |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for loss in (0, 2, 5):
        for proxy in ("SLB", "HAProxy"):
            group = groups[("E3", proxy, "Round-Robin", loss, 50)]
            parts.append(f"| {loss}% | {proxy} | {mean_sd(group, 'rps')} | {mean_sd(group, 'error_rate_pct', 2)} | {mean_sd(group, 'detection_time_s', 2)} | {mean_sd(group, 'recovery_time_s', 2)} |")
    parts += [
        "",
        "B2 was stopped around 30 seconds and restarted around 45 seconds in each 60-second trial. SLB logged a DOWN transition after about 3.0 seconds; B2 served its first post-restart request after about 2.0 seconds. Recovery is measured from restart to that first B2 request for both proxies. HAProxy health-transition messages have no timestamps, so its detection time is **unavailable**, not zero. The higher SLB error rates include failure-window HTTP 502 responses and client source-port errors; HAProxy returned HTTP 502/503 during failover. Exact failure causes are distinguishable in each `hey` output.",
        "",
        "## E4: Concurrency scaling",
        "",
        "| Clients | Proxy | Requests/s | p95 (ms) | Error rate (%) |",
        "| ---: | --- | ---: | ---: | ---: |",
    ]
    for clients in (50, 200, 500):
        for proxy in ("SLB", "HAProxy"):
            group = groups[("E4", proxy, "Round-Robin", 0, clients)]
            parts.append(f"| {clients} | {proxy} | {mean_sd(group, 'rps')} | {mean_sd(group, 'p95_ms')} | {mean_sd(group, 'error_rate_pct', 2)} |")
    parts += [
        "",
        "HAProxy throughput increased from about 644 to 1,641 requests/s across 50–500 clients, with rising p95 latency. SLB throughput remained near 530–570 requests/s and its p95 rose sharply. One SLB trial at 200 clients and one at 500 clients hit 1,506 and 1,615 client source-port errors respectively; therefore these E4 numbers do not isolate the proxy's maximum capacity.",
        "",
        "## Interpretation and next measurement",
        "",
        "The live namespace topology, scheduling, health checks, and failover generated auditable logs. The clearest measured trend is the effect of transit loss on tail latency. The E1 and E4 comparisons need a controlled rerun after addressing the client source-port bottleneck and stabilizing the load generator. Preserve this run unchanged as the baseline, record any network or kernel setting changes, then rerun all compared conditions under the same settings. Live packet captures were not part of this 60-run suite and should be collected separately with `run_poc_demo.bash` if packet-level evidence is needed.",
        "",
    ]
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
