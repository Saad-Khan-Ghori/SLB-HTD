# Project context

Last reviewed: 2026-11-17. Review after major project changes.

## Executive Summary

SLB-HTD is a Computer Networks course prototype demonstrating HTTP reverse proxy scheduling, active health checks, and routed Linux namespaces. It includes a separate browser teaching model and a 60-trial measured baseline.

## Business Objective

Explain traffic distribution and backend failure/recovery through executable demonstrations and auditable measurements; production hosting is outside the implemented scope.

## Intended Users

Students, instructors, and contributors studying load balancing and networking.

## Technology Stack

Python 3.10+ standard library (`asyncio`, `http.server`, CSV processing), Bash, and browser JavaScript/CSS/HTML. Namespace workflows use Linux `iproute2`, `curl`, `hey`, and HAProxy. Verification also uses `ping` and `traceroute` or `tracepath`. Optional tools: `tcpdump`, `ethtool`, Matplotlib for illustrations, ReportLab for PDF generation, and Node.js for simulator tests. There is no core dependency manifest or build step.

## Architecture Overview

Client → R1 → R2 → proxy → one of B1–B3. The proxy terminates TCP and opens a separate backend connection; health probes run in the background. See [architecture](docs/ARCHITECTURE.md).

## Main Features

Round robin, least connections with backend-order tie breaking, seeded CRC32 IP hash over the healthy pool, active health thresholds, structured logs, delayed backends, namespace loss injection, SLB/HAProxy benchmark comparisons, and an illustrative browser simulator.

## Module Explanations

| Path | Responsibility |
| --- | --- |
| `slb.py` | `BackendServer`, `SoftwareLoadBalancer`, scheduler, proxy, health state |
| `backend.py` | Threaded GET server; immediate `/health`, configurable delay on other paths |
| `setup_topology.bash` | Seven namespaces, static routes, bridge, netem, HAProxy configs, teardown |
| `run_poc_demo.bash` | Policy and failure/recovery demonstration, optional live captures |
| `run_experiments.bash` | 60 E1–E4 trials, three repetitions per condition |
| `analyze_results.py` | Validate trial tags, parse logs, emit measured CSVs |
| `build_measured_report.py`, `render_measured_pdf.py` | Baseline Markdown report and PDF |
| `generate_pcapng_and_diagrams.py` | Constructed packets and diagrams, for illustration only |
| `simulator/` | Independent browser model, static server, engine checks |
| `test_local_loopback.py`, `test_analyze_results.py` | Functional and analyzer checks |

## Database Structure

No database. Runtime backend state lives in memory. Logs, CSVs, reports, and captures are filesystem artifacts.

## External Integrations

Local HTTP backends and local command-line networking/load tools only. No cloud service, account setup, or external API integration is configured.

## Environment Configuration

No application environment variables or `.env` setup are required. Configure the proxy and backends with CLI flags; see `python3 slb.py --help` and `python3 backend.py --help`. Benchmark conditions and paths are defined in Bash; report paths are relative to the Python script directory. Analyzer default paths are relative to the current directory, so run from the repository root.

## Current Development Status

Teaching prototype. During the 2026-11-17 audit, loopback policy/failover/recovery checks, three analyzer unit tests, simulator engine checks, and syntax checks for all three Bash scripts passed. The existing measured run is dated November 3, 2026 in [the report](MEASURED_RESULTS.md). Namespace workflows were not rerun during the audit.

## Completed Work

Core scheduling and health behavior have functional test coverage (`test_local_loopback.py`). The retained baseline contains 60 client trial logs with service logs and measured CSVs (`experiments/`). The simulator has separate model checks (`simulator/test_engine.mjs`). Test success does not establish production readiness.

## Work in Progress

No active implementation branch or assigned task is recorded. Proposed improvements are in [the priorities document](docs/PROXY_IMPROVEMENTS.md); they are not completed features.

## Known Problems

The baseline includes 6,473 client source-port errors and cannot establish clean E1/E4 capacity rankings. HAProxy detection timing is unavailable in the baseline. The proxy forwards headers without request bodies, may append a 502 after a partially streamed response fails, and applies probe timeouts per operation. Report generation includes baseline-specific dates and interpretation text that must be reviewed for future data. Live captures are absent from this checkout.

## Important Decisions

One request per TCP connection and active-only failover match the teaching design in `slb.py`. Backends lack default routes and proxy IP forwarding is disabled (`setup_topology.bash`). The simulator is independent of live TCP and its loss model is illustrative (`simulator/README.md`). Preserve these distinctions in published results.

## Future Roadmap

The existing [priorities](docs/PROXY_IMPROVEMENTS.md) call for a controlled load-generator mitigation, protocol error handling, precise health deadlines and timestamped HAProxy transitions. Pooling, guarded retries, and consistent hashing are proposed extensions. Delivery dates and owners: **Needs verification**.

## Recommended Next Task

Preserve the baseline, measure a client source-port mitigation, and repeat compared conditions consistently before ranking policies or capacity. Record socket/TIME_WAIT counts and host CPU use, as requested in the priorities document.

## Handover Notes

Start with [README](README.md) and [AGENTS](AGENTS.md). Keep raw logs with numerical claims. Benchmark reruns delete matching logs and overwrite CSVs; archive the baseline first. Namespace setup removes existing namespaces with the same names, and the demo requires teardown. No Git repository exists in this checkout; prior release history and operational ownership are **Needs verification**.

## Glossary

| Term | Meaning |
| --- | --- |
| RR / LC | Round robin / least connections |
| IP hash | CRC32 of client address, modulo healthy backend count |
| fall / rise | Consecutive probe failures / successes required for state changes |
| E1–E4 | Policy, loss, failover, and concurrency experiments |
| C1 / C2 / C3 | Client, proxy DMZ, and backend bridge capture points |
| measured / modelled | Observed trial logs / illustrative generated examples |
