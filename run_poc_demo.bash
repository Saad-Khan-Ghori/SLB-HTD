#!/usr/bin/env bash
# ==============================================================================
# CS3001 Computer Networks (Fall 2026) - FAST School of Computing, Karachi
# Project: SLB-HTD Live Proof of Concept (PoC) Demo & Packet Capture Script
# File: run_poc_demo.bash
# Authors: Muhammad Wasil (24K-0790), Saad Khan Ghori (24K-0565), Muhammad Akbar Younsi (24K-0947)
#
# Works whether files are in the same directory or in src/ and scripts/ subdirs.
# Also records four live .pcap captures in captures/*.pcap when
# tcpdump is installed!
# ==============================================================================

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "[ERROR] Please run with sudo: sudo bash run_poc_demo.bash" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/slb.py" ]]; then
    SLB_PY="${SCRIPT_DIR}/slb.py"
    BACKEND_PY="${SCRIPT_DIR}/backend.py"
    TOPOLOGY_SH="${SCRIPT_DIR}/setup_topology.bash"
    PROJECT_ROOT="${SCRIPT_DIR}"
else
    PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
    SLB_PY="${PROJECT_ROOT}/src/slb.py"
    BACKEND_PY="${PROJECT_ROOT}/src/backend.py"
    TOPOLOGY_SH="${SCRIPT_DIR}/setup_topology.bash"
fi

LOG_DIR="${PROJECT_ROOT}/experiments/logs/poc_demo"
CAP_DIR="${PROJECT_ROOT}/captures"
mkdir -p "${LOG_DIR}" "${CAP_DIR}"
rm -f "${LOG_DIR}"/*.log "${LOG_DIR}"/*.txt

echo "======================================================================"
echo "STEP 0: Building 7-Namespace Topology & Verifying Routing / Traceroute"
echo "======================================================================"
bash "${TOPOLOGY_SH}" setup
bash "${TOPOLOGY_SH}" verify

echo "======================================================================"
echo "Starting Backends B1 (0ms), B2 (50ms), and B3 (150ms)..."
echo "======================================================================"
ip netns exec ns-b1 python3 "${BACKEND_PY}" --name B1 --host 172.20.0.11 --port 8001 --delay-ms 0 --log-file "${LOG_DIR}/b1.log" >/dev/null 2>&1 &
B1_PID=$!
ip netns exec ns-b2 python3 "${BACKEND_PY}" --name B2 --host 172.20.0.12 --port 8002 --delay-ms 50 --log-file "${LOG_DIR}/b2.log" >/dev/null 2>&1 &
B2_PID=$!
ip netns exec ns-b3 python3 "${BACKEND_PY}" --name B3 --host 172.20.0.13 --port 8003 --delay-ms 150 --log-file "${LOG_DIR}/b3.log" >/dev/null 2>&1 &
B3_PID=$!
sleep 1.0

echo "======================================================================"
echo "STEP 1 & 2: Single Request via SLB (Round-Robin Policy) + C1/C2/C3 PCAP"
echo "======================================================================"
ip netns exec ns-lb python3 "${SLB_PY}" --host 192.168.20.10 --port 8080 --policy rr --log-file "${LOG_DIR}/slb_rr.log" --verbose >/dev/null 2>&1 &
SLB_PID=$!
sleep 1.2

if command -v tcpdump >/dev/null 2>&1; then
    ip netns exec ns-client tcpdump -i eth0 -n tcp -w "${CAP_DIR}/c1_client_to_lb.pcap" >/dev/null 2>&1 &
    TCPD_C1=$!
    ip netns exec ns-lb tcpdump -i eth0 -n tcp -w "${CAP_DIR}/c2_dmz_to_lb.pcap" >/dev/null 2>&1 &
    TCPD_C2=$!
    ip netns exec ns-lb tcpdump -i br-be -n tcp -w "${CAP_DIR}/c3_backend_bridge_normal_and_health.pcap" >/dev/null 2>&1 &
    TCPD_C3=$!
    sleep 0.5
fi

echo "\$ ip netns exec ns-client curl -s http://192.168.20.10:8080/"
ip netns exec ns-client curl -s http://192.168.20.10:8080/
echo ""

echo "======================================================================"
echo "STEP 3A: Scheduling Policy 1 — Round-Robin (6 Sequential Requests)"
echo "======================================================================"
for i in $(seq 1 6); do
    echo -n "Request #${i}: "
    ip netns exec ns-client curl -s http://192.168.20.10:8080/
done
echo ""

if [[ -n "${TCPD_C1:-}" ]]; then
    sleep 1.5
    kill -INT "${TCPD_C1}" "${TCPD_C2}" "${TCPD_C3}" 2>/dev/null || true
    wait "${TCPD_C1}" "${TCPD_C2}" "${TCPD_C3}" 2>/dev/null || true
fi

kill -TERM "${SLB_PID}" 2>/dev/null || true
wait "${SLB_PID}" 2>/dev/null || true

echo "======================================================================"
echo "STEP 3B: Scheduling Policy 2 — Least-Connections (Concurrent Burst)"
echo "======================================================================"
ip netns exec ns-lb python3 "${SLB_PY}" --host 192.168.20.10 --port 8080 --policy lc --log-file "${LOG_DIR}/slb_lc.log" >/dev/null 2>&1 &
SLB_PID=$!
sleep 1.2

WORKER_PIDS=()
for w in $(seq 1 6); do
    (
        for r in $(seq 1 5); do
            ip netns exec ns-client curl -s http://192.168.20.10:8080/ >> "${LOG_DIR}/lc_responses.txt"
        done
    ) &
    WORKER_PIDS+=("$!")
done
for worker_pid in "${WORKER_PIDS[@]}"; do
    wait "${worker_pid}"
done

echo "Least-Connections response distribution across 30 concurrent requests:"
grep -o "Backend: B[123]" "${LOG_DIR}/lc_responses.txt" | sort | uniq -c
echo ""

kill -TERM "${SLB_PID}" 2>/dev/null || true
wait "${SLB_PID}" 2>/dev/null || true

echo "======================================================================"
echo "STEP 3C: Scheduling Policy 3 — IP-Hash (3 Client Source IP Addresses)"
echo "======================================================================"
ip netns exec ns-lb python3 "${SLB_PY}" --host 192.168.20.10 --port 8080 --policy iphash --hash-seed 3 --log-file "${LOG_DIR}/slb_iphash.log" >/dev/null 2>&1 &
SLB_PID=$!
sleep 1.2

for src_ip in 192.168.10.50 192.168.10.51 192.168.10.52; do
    echo ">>> Source IP: ${src_ip} (3 repeated requests)"
    for r in 1 2 3; do
        echo -n "  Try #${r}: "
        ip netns exec ns-client curl -s --interface "${src_ip}" http://192.168.20.10:8080/
    done
done
echo ""

kill -TERM "${SLB_PID}" 2>/dev/null || true
wait "${SLB_PID}" 2>/dev/null || true

echo "======================================================================"
echo "STEPS 4, 5 & 6: Health Checks, Backend Failure (fall=3) & Recovery (rise=2)"
echo "======================================================================"
ip netns exec ns-lb python3 "${SLB_PY}" --host 192.168.20.10 --port 8080 --policy rr --log-file "${LOG_DIR}/slb_failover.log" >/dev/null 2>&1 &
SLB_PID=$!
sleep 1.2

if command -v tcpdump >/dev/null 2>&1; then
    ip netns exec ns-lb tcpdump -i br-be -n tcp -w "${CAP_DIR}/c3_failover_and_recovery.pcap" >/dev/null 2>&1 &
    TCPD_FAIL=$!
    sleep 0.5
fi

echo "[Step 4] Stopping Backend 2 (172.20.0.12:8002) and waiting for 3 failed probes..."
kill -TERM "${B2_PID}" 2>/dev/null || true
wait "${B2_PID}" 2>/dev/null || true
sleep 3.5

echo "--- Load Balancer Health Log (showing 3 failed probes -> DOWN) ---"
grep "HEALTH" "${LOG_DIR}/slb_failover.log" | tail -n 6
echo ""

echo "[Step 5] Sending 6 requests while Backend 2 is DOWN (only B1 and B3 answer):"
for i in $(seq 1 6); do
    echo -n "Request #${i}: "
    ip netns exec ns-client curl -s http://192.168.20.10:8080/
done
echo ""

echo "[Step 6] Restarting Backend 2 (172.20.0.12:8002) and waiting for 2 successful probes..."
ip netns exec ns-b2 python3 "${BACKEND_PY}" --name B2 --host 172.20.0.12 --port 8002 --delay-ms 50 --log-file "${LOG_DIR}/b2.log" >/dev/null 2>&1 &
B2_PID=$!
sleep 2.5

echo "--- Load Balancer Health Log (showing 2 successful probes -> UP) ---"
grep "HEALTH" "${LOG_DIR}/slb_failover.log" | tail -n 5
echo ""

echo "[Step 6 Continued] Sending 6 requests after Backend 2 recovery (B1, B2, B3 all active):"
for i in $(seq 1 6); do
    echo -n "Request #${i}: "
    ip netns exec ns-client curl -s http://192.168.20.10:8080/
done
echo ""

if [[ -n "${TCPD_FAIL:-}" ]]; then
    kill -INT "${TCPD_FAIL}" 2>/dev/null || true
    wait "${TCPD_FAIL}" 2>/dev/null || true
fi

kill -TERM "${SLB_PID}" "${B1_PID}" "${B2_PID}" "${B3_PID}" 2>/dev/null || true
echo "[SUCCESS] Proof of Concept Demo completed. Logs in ${LOG_DIR}, live PCAPs in ${CAP_DIR}."
