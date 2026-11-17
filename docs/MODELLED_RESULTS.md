# Status of the supplied results

Last reviewed: 2026-11-17. Review after major project changes.

The CSV tables and technical report in `modelled/` are archived **modelled examples**. They are not verified results from 60 captured `hey` trials. The previous analysis code generated per-run metrics with seeded random draws around assumed throughput, latency, and error values. Its short local calibration did not measure the full namespace experiments.

The report also describes four packet captures as collected evidence, but no capture files were supplied in this checkout. `generate_pcapng_and_diagrams.py` constructs illustrative PCAPNG packets under `illustrative/`; its output must never be described as a live capture. Use `run_poc_demo.bash` with `tcpdump` to make live `.pcap` captures and preserve their provenance.

The current measured run is summarized in [`MEASURED_RESULTS.md`](../MEASURED_RESULTS.md) and `output/pdf/SLB_HTD_Measured_Results.pdf`. Its per-trial logs are under `experiments/raw_runs/` and its measured CSVs are under `experiments/`. Retain these logs with the tables. The archived modelled report's numerical and capture claims have not been retroactively re-labelled inside that PDF; use the measured report for this run.
