# Software Load Balancer with Health Checks and Dynamic Traffic Distribution

Last reviewed: 2026-11-17. Review after major project changes.

SLB-HTD is a Python 3.10+ HTTP/1.1 reverse proxy with round robin (`rr`), least connections (`lc`), and CRC32 IP hash (`iphash`) scheduling. It probes three backends via `GET /health`, removes a backend after three failed probes, and re-admits it after two successful probes. The Linux demonstration places the client, two routers, proxy, and three backends in seven network namespaces.

## Evidence status

This is a teaching/research prototype for GET traffic, with no authentication or TLS. It does not forward request bodies or support persistent connections; keep it in a local lab. Policies are selected at startup.

The current run contains 60 measured trials. Read [MEASURED_RESULTS.md](MEASURED_RESULTS.md) or the [measured PDF](output/pdf/SLB_HTD_Measured_Results.pdf) for the results and limitations; the source logs are in `experiments/raw_runs/` and the measured CSVs are in `experiments/`. Several client trials hit source-port errors, so the E1 and E4 comparisons need a controlled rerun. The earlier modelled tables and report remain archived in [`modelled/`](modelled/); read [modelled-data caveats](docs/MODELLED_RESULTS.md) before using them. This checkout still has no live packet captures.

## Files in this checkout

| File | Purpose |
| --- | --- |
| `slb.py`, `backend.py` | Async proxy and delayed HTTP backend servers |
| `test_local_loopback.py` | Non-root functional test of policies, failover, and recovery |
| `setup_topology.bash` | Namespace setup, routing verification, loss control, teardown, and HAProxy config generation |
| `run_poc_demo.bash` | Live policy/failover demo; records traffic with `tcpdump` when installed |
| `run_experiments.bash` | Executes E1–E4 trials and retains raw logs |
| `analyze_results.py` | Builds measured CSV tables from those raw logs |
| `build_measured_report.py`, `render_measured_pdf.py` | Regenerate the measured Markdown and PDF reports |
| `generate_pcapng_and_diagrams.py` | Creates **illustrative**, constructed packets and diagrams under `illustrative/` |
| `simulator/` | Local browser simulator for policy, traffic, delay, loss, and health demonstrations |
| [`docs/PROXY_IMPROVEMENTS.md`](docs/PROXY_IMPROVEMENTS.md) | Prioritized proxy and benchmark fixes grounded in the measured run |

`configs/`, `captures/`, and `illustrative/` are generated as needed. `experiments/raw_runs/` contains the current run's auditable logs.

## Requirements

Use a Linux host or VM with Python 3.10+, `iproute2` (`ip` and `tc`), `curl`, `hey`, and HAProxy. Root access is required for network namespaces. `tcpdump` enables live packet capture; routing verification requires `ping` and either `traceroute` or `tracepath`. `ethtool` is optional for capture checksum cleanup. Node.js is required only for simulator engine checks. The proxy, backend, functional test, and measured CSV analyzer use the Python standard library. Matplotlib is needed only for illustrative diagrams. Check that each external command is installed and available on `PATH` before running the namespace workflows.

## Verify locally without root

Run commands from the repository root. No package installation, database, or environment variables are needed for the core tools; configuration is through CLI arguments (`python3 slb.py --help`, `python3 backend.py --help`). There is no build step or configured lint/type-check tool.

```bash
python3 test_local_loopback.py
python3 -m unittest -v test_analyze_results.py
node simulator/test_engine.mjs
for script in *.bash; do bash -n "$script"; done
```

The loopback test starts three backends on `127.0.0.1:18101`–`18103` and the proxy on `127.0.0.1:18180`. It asserts policy behavior, the detection-window 502 response, backend removal, and recovery. The analyzer tests check log parsing and the 60-trial manifest.

## Explore the visual simulator

```bash
python3 simulator/serve.py
```

Open `http://127.0.0.1:8765/`. Configure a policy, client source IP, request rate, backend delays, illustrative loss chance, and health thresholds. Send one numbered request at a time to see its route and result, or use playback. Backend panels show active requests and service progress; the health button advances probes manually. This browser-only tool is for explanation and does not control the live proxy or produce benchmark evidence. See [simulator/README.md](simulator/README.md) for details.

## Run the live topology and demo

```bash
sudo bash setup_topology.bash setup
sudo bash setup_topology.bash verify
sudo bash run_poc_demo.bash
sudo bash setup_topology.bash teardown
```

The demo sets up the topology itself, so the first `setup` command is needed only when inspecting the network separately. It stores logs under `experiments/logs/poc_demo/` and live `.pcap` captures under `captures/` if `tcpdump` is available. Inspect captures with, for example, `tcpdump -nn -r captures/c1_client_to_lb.pcap`.

The client (`192.168.10.50/24`) reaches the proxy (`192.168.20.10:8080`) through routers `192.168.10.1` and `10.0.0.2`. The proxy reaches B1, B2, and B3 over its `172.20.0.0/24` bridge. Their response delays are 0, 50, and 150 ms respectively. Backends have no default route; the proxy does not forward IP packets.

## Collect and analyze measurements

```bash
sudo bash run_experiments.bash
python3 analyze_results.py --raw-dir experiments/raw_runs --out-dir experiments
sudo bash setup_topology.bash teardown
```

**Before rerunning:** copy the current `experiments/` directory to a separate baseline archive. The runner deletes matching trial logs and overwrites the CSVs; the demo also replaces its logs and capture filenames. Setup tears down any existing namespaces with the same seven names and regenerates `configs/`. Use a dedicated Linux lab host. The demo needs explicit teardown, including after failures.

The experiment script executes 60 trials across E1–E4, saves each `hey` output and service log in `experiments/raw_runs/`, calls the analyzer, and tears down its namespaces. The analyzer requires all 60 trial logs by default and writes `experiments/raw_60_runs.csv` and `experiments/summary_tables_E1_to_E4.csv`. It refuses missing or malformed inputs rather than filling gaps with estimates. Retain the raw logs with any published figures or numerical claims. For inspection of an incomplete run, use `--allow-partial`.

## Refresh the measured report

```bash
python3 build_measured_report.py
python3 render_measured_pdf.py  # requires reportlab
```

The report builder is tailored to the November 3 baseline: its date and several interpretation paragraphs are hard-coded. After a new run, update and verify those statements before publishing. The Markdown report is generated with the standard library. Install ReportLab in a Python environment to regenerate the PDF.

## Presentation guidance

Demonstrate each policy (restarting the proxy between policies), B2 failure, and recovery with the loopback or namespace demo. Use constructed packets from `generate_pcapng_and_diagrams.py` only as labelled illustrations. Present the measured results with the documented client bottleneck; the archived modelled report must not be presented as the measured report.

## Project documentation

- [Project context and handover](PROJECT_CONTEXT.md)
- [Implemented architecture](docs/ARCHITECTURE.md)
- [Change log](CHANGELOG.md)
- [Contributor instructions](AGENTS.md)

There is no production deployment workflow. Use the local simulator or Linux namespace lab described above. Keep measured evidence in `experiments/`, generated PDFs in `output/pdf/`, live captures in `captures/`, and constructed examples in `illustrative/`. Source and test entry points stay at the root so existing scripts work.
