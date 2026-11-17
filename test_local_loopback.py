#!/usr/bin/env python3
"""
CS3001 Computer Networks (Fall 2026) - FAST School of Computing, Karachi
Project: SLB-HTD Non-Root Local Loopback Functional Test Suite
File: scripts/test_local_loopback.py
Authors: Muhammad Wasil (24K-0790), Saad Khan Ghori (24K-0565), Muhammad Akbar Younsi (24K-0947)

Description:
    Verifies all functional requirements of `src/slb.py` and `src/backend.py`
    on standard loopback sockets (127.0.0.1) without requiring sudo or namespaces:
      1. Round-Robin cyclic distribution (B1 -> B2 -> B3 -> B1 -> B2 -> B3)
      2. Least-Connections dynamic traffic distribution (B1 > B2 > B3 under concurrency)
      3. IP-Hash deterministic mapping for 192.168.10.50, .51, .52
      4. Active health check failure detection (fall=3 probes @ 1s) & HTTP 502 during window
      5. Active health check recovery re-admission (rise=2 probes @ 1s)
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def fetch_backend_name(url: str, sim_ip: str | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url)
    if sim_ip:
        req.add_header("X-Sim-Client-IP", sim_ip)
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            body = resp.read().decode("utf-8", errors="replace").strip()
            for token in ("B1", "B2", "B3"):
                if f"Backend: {token}" in body:
                    return resp.status, token
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, "HTTP_ERROR"
    except Exception as e:
        return 0, str(type(e).__name__)


def main() -> None:
    procs: list[subprocess.Popen] = []
    try:
        print("[1/5] Starting 3 Threaded HTTP Backends: B1 (0ms), B2 (50ms), B3 (150ms)...")
        for name, port, delay in [("B1", 18101, 0), ("B2", 18102, 50), ("B3", 18103, 150)]:
            p = subprocess.Popen(
                [sys.executable, os.path.join(PROJECT_ROOT, "backend.py"),
                 "--name", name, "--host", "127.0.0.1", "--port", str(port), "--delay-ms", str(delay)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            procs.append(p)
        time.sleep(0.6)

        # 1. Test Round-Robin
        print("[2/5] Testing Round-Robin Policy (6 sequential requests)...")
        slb_rr = subprocess.Popen(
            [sys.executable, os.path.join(PROJECT_ROOT, "slb.py"),
             "--host", "127.0.0.1", "--port", "18180", "--policy", "rr",
             "--backends", "B1=127.0.0.1:18101,B2=127.0.0.1:18102,B3=127.0.0.1:18103"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(0.6)
        rr_seq = [fetch_backend_name("http://127.0.0.1:18180/")[1] for _ in range(6)]
        print(f"      Observed sequence: {rr_seq}")
        assert rr_seq == ["B1", "B2", "B3", "B1", "B2", "B3"], f"Unexpected RR sequence: {rr_seq}"
        slb_rr.terminate()
        slb_rr.wait()

        # 2. Test Least-Connections
        print("[3/5] Testing Least-Connections Policy (36 concurrent requests across 12 workers)...")
        slb_lc = subprocess.Popen(
            [sys.executable, os.path.join(PROJECT_ROOT, "slb.py"),
             "--host", "127.0.0.1", "--port", "18180", "--policy", "lc",
             "--backends", "B1=127.0.0.1:18101,B2=127.0.0.1:18102,B3=127.0.0.1:18103"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(0.6)
        with ThreadPoolExecutor(max_workers=12) as pool:
            lc_results = [r[1] for r in pool.map(lambda _: fetch_backend_name("http://127.0.0.1:18180/"), range(36))]
        counts = Counter(lc_results)
        print(f"      Observed distribution: B1={counts['B1']}, B2={counts['B2']}, B3={counts['B3']}")
        assert counts["B1"] > counts["B2"] >= counts["B3"], f"Expected B1 > B2 >= B3, got {counts}"
        slb_lc.terminate()
        slb_lc.wait()

        # 3. Test IP-Hash
        print("[4/5] Testing IP-Hash Policy (CRC32 on 192.168.10.50, .51, .52)...")
        slb_iph = subprocess.Popen(
            [sys.executable, os.path.join(PROJECT_ROOT, "slb.py"),
             "--host", "127.0.0.1", "--port", "18180", "--policy", "iphash", "--hash-seed", "3",
             "--allow-sim-ip-header",
             "--backends", "B1=127.0.0.1:18101,B2=127.0.0.1:18102,B3=127.0.0.1:18103"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(0.6)
        for ip in ("192.168.10.50", "192.168.10.51", "192.168.10.52"):
            mapped = [fetch_backend_name("http://127.0.0.1:18180/", sim_ip=ip)[1] for _ in range(4)]
            print(f"      Source IP {ip} -> {mapped}")
            assert len(set(mapped)) == 1, f"IP-Hash changed for {ip}: {mapped}"
        slb_iph.terminate()
        slb_iph.wait()

        # 4. Test Failover (502 during detection window -> DOWN after 3 probes -> UP after 2 probes)
        print("[5/5] Testing Active Health Check Failover (fall=3) & Recovery (rise=2)...")
        slb_fo = subprocess.Popen(
            [sys.executable, os.path.join(PROJECT_ROOT, "slb.py"),
             "--host", "127.0.0.1", "--port", "18180", "--policy", "rr",
             "--backends", "B1=127.0.0.1:18101,B2=127.0.0.1:18102,B3=127.0.0.1:18103"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(0.6)
        procs[1].terminate()
        procs[1].wait()

        # Immediately send 3 requests before 3 probes complete -> 1 of 3 should return HTTP 502!
        imm_statuses = [fetch_backend_name("http://127.0.0.1:18180/")[0] for _ in range(3)]
        print(f"      Statuses during detection window (before 3 failed probes): {imm_statuses}")
        assert 502 in imm_statuses, f"Expected HTTP 502 during detection window, got {imm_statuses}"

        time.sleep(3.3)
        down_backends = [fetch_backend_name("http://127.0.0.1:18180/")[1] for _ in range(6)]
        print(f"      Backends serving after B2 marked DOWN: {down_backends}")
        assert "B2" not in down_backends

        procs[1] = subprocess.Popen(
            [sys.executable, os.path.join(PROJECT_ROOT, "backend.py"),
             "--name", "B2", "--host", "127.0.0.1", "--port", "18102", "--delay-ms", "50"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(2.4)
        up_backends = [fetch_backend_name("http://127.0.0.1:18180/")[1] for _ in range(6)]
        print(f"      Backends serving after B2 marked UP (rise=2): {up_backends}")
        assert "B2" in up_backends
        slb_fo.terminate()
        slb_fo.wait()

        print("\n[SUCCESS] All 5 SLB-HTD functional verification tests passed!")
    finally:
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    main()
