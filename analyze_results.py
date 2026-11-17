#!/usr/bin/env python3
"""Summarize measured SLB-HTD runs captured by run_experiments.bash."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
import re
import statistics

FIELDS = [
    "exp_id", "proxy", "policy", "loss_pct", "concurrency", "duration_s", "run",
    "rps", "p50_ms", "p95_ms", "p99_ms", "total_requests", "failed_requests",
    "error_rate_pct", "b1_requests", "b2_requests", "b3_requests",
    "detection_time_s", "recovery_time_s",
]
TAG = re.compile(r"^(E[1-4])_(slb|haproxy)_(rr|lc)_loss(0|2|5)_c(50|200|500)_r([123])$")


def expected_tags() -> set[str]:
    tags = {f"E1_slb_{policy}_loss0_c50_r{run}" for policy in ("rr", "lc") for run in range(1, 4)}
    tags |= {f"{exp}_{proxy}_rr_loss{loss}_c50_r{run}"
             for exp in ("E2", "E3") for proxy in ("slb", "haproxy")
             for loss in (0, 2, 5) for run in range(1, 4)}
    tags |= {f"E4_{proxy}_rr_loss0_c{conns}_r{run}"
             for proxy in ("slb", "haproxy") for conns in (50, 200, 500)
             for run in range(1, 4)}
    return tags


def parse_hey(path: Path) -> dict[str, float | int]:
    content = path.read_text(encoding="utf-8", errors="replace")

    def metric(pattern: str) -> float:
        match = re.search(pattern, content, flags=re.MULTILINE)
        if not match:
            raise ValueError(f"Missing metric {pattern!r} in {path}")
        return float(match.group(1))

    rps = metric(r"^\s*Requests/sec:\s+([0-9.]+)")
    p50 = metric(r"^\s*50%\s+in\s+([0-9.]+)\s+secs") * 1000
    p95 = metric(r"^\s*95%\s+in\s+([0-9.]+)\s+secs") * 1000
    p99 = metric(r"^\s*99%\s+in\s+([0-9.]+)\s+secs") * 1000
    statuses = [(int(code), int(count)) for code, count in re.findall(
        r"^\s*\[(\d{3})\]\s+(\d+)\s+responses", content, re.MULTILINE)]
    if not statuses:
        raise ValueError(f"No HTTP status distribution in {path}")
    connection_errors = sum(int(count) for count in re.findall(
        r"^\s*\[(\d+)\]\s+(?:Get|Post|Put|Head|Delete|Patch)\s+", content, re.MULTILINE))
    total = sum(count for _, count in statuses) + connection_errors
    failed = sum(count for code, count in statuses if code != 200) + connection_errors
    if total == 0:
        raise ValueError(f"No completed or failed requests in {path}")
    return {"rps": round(rps, 2), "p50_ms": round(p50, 2), "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2), "total_requests": total, "failed_requests": failed,
            "error_rate_pct": round(100 * failed / total, 3)}


def backend_requests(path: Path) -> tuple[int, list[float]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    times = [float(t) for t in re.findall(r"\[RECV_EPOCH=([0-9.]+)\].*?\[TYPE=PROXY\]",
                                           path.read_text(encoding="utf-8", errors="replace"))]
    return len(times), times


def event_times(path: Path) -> tuple[float, float]:
    if not path.is_file():
        raise FileNotFoundError(path)
    content = path.read_text(encoding="utf-8")
    stop = re.search(r"^STOP_EPOCH=([0-9.]+)$", content, re.MULTILINE)
    restart = re.search(r"^RESTART_EPOCH=([0-9.]+)$", content, re.MULTILINE)
    if not stop or not restart:
        raise ValueError(f"Missing stop/restart event in {path}")
    return float(stop.group(1)), float(restart.group(1))


def down_time(path: Path, stop: float) -> float | None:
    if not path.is_file():
        raise FileNotFoundError(path)
    content = path.read_text(encoding="utf-8", errors="replace")
    for line in content.splitlines():
        if "B2" not in line and "/b2" not in line:
            continue
        if "marked DOWN" not in line and " is DOWN" not in line:
            continue
        epoch = re.search(r"\[EPOCH=([0-9.]+)\]", line)
        if epoch:
            return float(epoch.group(1))
        # HAProxy emits a syslog timestamp rather than an epoch field.
        stamp = re.search(r"\b([A-Z][a-z]{2}\s+\d+\s+\d\d:\d\d:\d\d)\b", line)
        if stamp:
            dt = datetime.strptime(f"{datetime.fromtimestamp(stop).year} {stamp.group(1)}", "%Y %b %d %H:%M:%S")
            return dt.timestamp()
    return None


def parse_run(raw_dir: Path, tag: str) -> dict[str, object]:
    match = TAG.fullmatch(tag)
    if not match:
        raise ValueError(f"Unexpected run tag: {tag}")
    exp, proxy, policy, loss, conns, run = match.groups()
    result: dict[str, object] = {
        "exp_id": exp, "proxy": "SLB" if proxy == "slb" else "HAProxy",
        "policy": "Least-Connections" if policy == "lc" else "Round-Robin",
        "loss_pct": int(loss), "concurrency": int(conns),
        "duration_s": 60 if exp == "E3" else 30, "run": int(run),
        **parse_hey(raw_dir / f"{tag}_hey.txt"),
    }
    backend_times: dict[str, list[float]] = {}
    for name in ("b1", "b2", "b3"):
        count, times = backend_requests(raw_dir / f"{tag}_{name}.log")
        result[f"{name}_requests"] = count
        backend_times[name] = times
    result["detection_time_s"] = ""
    result["recovery_time_s"] = ""
    if exp == "E3":
        stop, restart = event_times(raw_dir / f"{tag}_events.txt")
        down = down_time(raw_dir / f"{tag}_proxy.log", stop)
        if down is not None and down >= stop:
            result["detection_time_s"] = round(down - stop, 3)
        after_restart = [t for t in backend_times["b2"] if t >= restart]
        if after_restart:
            result["recovery_time_s"] = round(min(after_restart) - restart, 3)
    return result


def summarize(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[tuple[object, ...], list[dict[str, object]]] = {}
    for row in rows:
        key = tuple(row[k] for k in ("exp_id", "proxy", "policy", "loss_pct", "concurrency"))
        groups.setdefault(key, []).append(row)
    summary = []
    measures = ("rps", "p50_ms", "p95_ms", "p99_ms", "error_rate_pct", "failed_requests",
                "b1_requests", "b2_requests", "b3_requests", "detection_time_s", "recovery_time_s")
    for key, group in sorted(groups.items()):
        item: dict[str, object] = dict(zip(("exp_id", "proxy", "policy", "loss_pct", "concurrency"), key))
        item["runs"] = len(group)
        for measure in measures:
            values = [float(row[measure]) for row in group if row[measure] != ""]
            item[f"{measure}_mean"] = round(statistics.mean(values), 3) if values else ""
            item[f"{measure}_sd"] = round(statistics.stdev(values), 3) if len(values) > 1 else (0 if values else "")
            item[f"{measure}_min"] = round(min(values), 3) if values else ""
            item[f"{measure}_max"] = round(max(values), 3) if values else ""
        summary.append(item)
    return summary


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path("experiments/raw_runs"))
    parser.add_argument("--out-dir", type=Path, default=Path("experiments"))
    parser.add_argument("--allow-partial", action="store_true", help="Analyze an incomplete set of trial logs")
    args = parser.parse_args()
    found = {p.name[:-8] for p in args.raw_dir.glob("*_hey.txt")}
    expected = expected_tags()
    if not found:
        parser.error(f"No *_hey.txt files found in {args.raw_dir}")
    if not args.allow_partial and found != expected:
        parser.error(f"Expected 60 trials; missing {len(expected - found)}, unexpected {len(found - expected)}")
    if found - expected:
        parser.error(f"Unexpected run tags: {sorted(found - expected)}")
    rows = [parse_run(args.raw_dir, tag) for tag in sorted(found)]
    write_csv(args.out_dir / "raw_60_runs.csv", rows, FIELDS)
    summary = summarize(rows)
    write_csv(args.out_dir / "summary_tables_E1_to_E4.csv", summary, list(summary[0]))
    print(f"Analyzed {len(rows)} measured trials from {args.raw_dir}")
    print(f"Wrote CSV tables to {args.out_dir}")


if __name__ == "__main__":
    main()
