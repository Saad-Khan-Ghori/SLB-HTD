#!/usr/bin/env bash
# ==============================================================================
# CS3001 Computer Networks (Fall 2026) - FAST School of Computing, Karachi
# Project: SLB-HTD Full 60-Run Experimental Suite Runner (E1, E2, E3, E4)
# File: run_experiments.bash
# Primary Author: Muhammad Akbar Younsi (24K-0947)
#
# Self-contained runner for all 60 experiment trials (Table 3: E1, E2, E3, E4)
# using `hey`, `tc netem`, `slb.py`, and `haproxy`, with built-in Python log
# logs for analysis with `python3 analyze_results.py --raw-dir experiments/raw_runs`.
# ==============================================================================

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "[ERROR] Please run with sudo: sudo bash run_experiments.bash" >&2
    exit 1
fi

for cmd in hey haproxy tc ip; do
    if ! command -v "${cmd}" >/dev/null 2>&1; then
        echo "[ERROR] Required command '${cmd}' is not installed. Install with: sudo apt-get install -y hey haproxy iproute2" >&2
        exit 1
    fi
done

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

RAW_DIR="${PROJECT_ROOT}/experiments/raw_runs"
mkdir -p "${RAW_DIR}"

ulimit -n 65535 || true

start_backends() {
    local run_tag="$1"
    ip netns exec ns-b1 python3 "${BACKEND_PY}" --name B1 --host 172.20.0.11 --port 8001 --delay-ms 0 --log-file "${RAW_DIR}/${run_tag}_b1.log" >/dev/null 2>&1 &
    B1_PID=$!
    ip netns exec ns-b2 python3 "${BACKEND_PY}" --name B2 --host 172.20.0.12 --port 8002 --delay-ms 50 --log-file "${RAW_DIR}/${run_tag}_b2.log" >/dev/null 2>&1 &
    B2_PID=$!
    ip netns exec ns-b3 python3 "${BACKEND_PY}" --name B3 --host 172.20.0.13 --port 8003 --delay-ms 150 --log-file "${RAW_DIR}/${run_tag}_b3.log" >/dev/null 2>&1 &
    B3_PID=$!
    sleep 0.8
}

stop_backends() {
    kill -TERM "${B1_PID:-}" "${B2_PID:-}" "${B3_PID:-}" 2>/dev/null || true
    wait "${B1_PID:-}" "${B2_PID:-}" "${B3_PID:-}" 2>/dev/null || true
}

start_proxy() {
    local proxy_type="$1"
    local policy="$2"
    local run_tag="$3"

    if [[ "${proxy_type}" == "slb" ]]; then
        ip netns exec ns-lb python3 "${SLB_PY}" --host 192.168.20.10 --port 8080 --policy "${policy}" --log-file "${RAW_DIR}/${run_tag}_proxy.log" >/dev/null 2>&1 &
        PROXY_PID=$!
    else
        local cfg="${SCRIPT_DIR}/configs/haproxy_${policy}.cfg"
        if [[ ! -f "${cfg}" ]]; then
            cfg="${PROJECT_ROOT}/configs/haproxy_${policy}.cfg"
        fi
        ip netns exec ns-lb haproxy -f "${cfg}" > "${RAW_DIR}/${run_tag}_proxy.log" 2>&1 &
        PROXY_PID=$!
    fi
    sleep 1.2
}

stop_proxy() {
    kill -TERM "${PROXY_PID:-}" 2>/dev/null || true
    wait "${PROXY_PID:-}" 2>/dev/null || true
}

cleanup() {
    stop_proxy
    stop_backends
    bash "${TOPOLOGY_SH}" teardown >/dev/null 2>&1 || true
}
trap cleanup EXIT
bash "${TOPOLOGY_SH}" setup

run_single_trial() {
    local exp_id="$1"
    local proxy_type="$2"
    local policy="$3"
    local loss_pct="$4"
    local conns="$5"
    local duration_s="$6"
    local run_num="$7"

    local run_tag="${exp_id}_${proxy_type}_${policy}_loss${loss_pct}_c${conns}_r${run_num}"
    echo ">>> Running ${run_tag} (${duration_s}s)..."

    rm -f "${RAW_DIR}/${run_tag}"_*
    bash "${TOPOLOGY_SH}" netem "${loss_pct}" >/dev/null
    start_backends "${run_tag}"
    start_proxy "${proxy_type}" "${policy}" "${run_tag}"

    if [[ "${exp_id}" == "E3" ]]; then
        (
            sleep 30
            t_stop=$(python3 -c 'import time; print(f"{time.time():.6f}")')
            echo "STOP_EPOCH=${t_stop}" > "${RAW_DIR}/${run_tag}_events.txt"
            kill -TERM "${B2_PID}" 2>/dev/null || true
            sleep 15
            t_restart=$(python3 -c 'import time; print(f"{time.time():.6f}")')
            echo "RESTART_EPOCH=${t_restart}" >> "${RAW_DIR}/${run_tag}_events.txt"
            ip netns exec ns-b2 python3 "${BACKEND_PY}" --name B2 --host 172.20.0.12 --port 8002 --delay-ms 50 --log-file "${RAW_DIR}/${run_tag}_b2.log" >/dev/null 2>&1 &
            echo $! > "${RAW_DIR}/${run_tag}_b2_newpid.txt"
        ) &
        FAIL_CONTROLLER_PID=$!
    fi

    ip netns exec ns-client hey -c "${conns}" -z "${duration_s}s" http://192.168.20.10:8080/ > "${RAW_DIR}/${run_tag}_hey.txt" 2>&1

    if [[ "${exp_id}" == "E3" ]]; then
        wait "${FAIL_CONTROLLER_PID}" 2>/dev/null || true
        if [[ -f "${RAW_DIR}/${run_tag}_b2_newpid.txt" ]]; then
            B2_PID=$(cat "${RAW_DIR}/${run_tag}_b2_newpid.txt")
        fi
    fi

    stop_proxy
    stop_backends
}

for policy in rr lc; do
    for run in 1 2 3; do
        run_single_trial "E1" "slb" "${policy}" 0 50 30 "${run}"
    done
done

for loss in 0 2 5; do
    for proxy in slb haproxy; do
        for run in 1 2 3; do
            run_single_trial "E2" "${proxy}" "rr" "${loss}" 50 30 "${run}"
        done
    done
done

for loss in 0 2 5; do
    for proxy in slb haproxy; do
        for run in 1 2 3; do
            run_single_trial "E3" "${proxy}" "rr" "${loss}" 50 60 "${run}"
        done
    done
done

for conns in 50 200 500; do
    for proxy in slb haproxy; do
        for run in 1 2 3; do
            run_single_trial "E4" "${proxy}" "rr" 0 "${conns}" 30 "${run}"
        done
    done
done

python3 "${PROJECT_ROOT}/analyze_results.py" --raw-dir "${RAW_DIR}" --out-dir "${PROJECT_ROOT}/experiments"
echo "[SUCCESS] All 60 experiment runs and measured analysis finished in ${RAW_DIR}."
