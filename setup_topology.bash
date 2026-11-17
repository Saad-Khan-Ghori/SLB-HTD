#!/usr/bin/env bash
# ==============================================================================
# CS3001 Computer Networks (Fall 2026) - FAST School of Computing, Karachi
# Project: Software Load Balancer with Health Checks and Dynamic Traffic Distribution (SLB-HTD)
# File: setup_topology.bash
# Primary Author: Muhammad Akbar Younsi (24K-0947)
#
# Self-contained Topology, Routing Verification, tc netem, Teardown, and HAProxy
# Configuration Builder for the 7-Namespace Routed Architecture (Sections 3 & 4).
#
# Usage:
#   sudo bash setup_topology.bash              # Build 7 namespaces, bridge, routes & HAProxy configs
#   sudo bash setup_topology.bash verify       # Print ip route, ping RTT, traceroute & isolation proof
#   sudo bash setup_topology.bash netem <0|2|5># Set 0%, 2%, or 5% packet loss on transit link
#   sudo bash setup_topology.bash teardown     # Stop processes and delete all 7 namespaces
# ==============================================================================

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "[ERROR] Please run this script with sudo: sudo bash setup_topology.bash [setup|verify|netem <pct>|teardown]" >&2
    exit 1
fi

ACTION="${1:-setup}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACES=("ns-client" "ns-r1" "ns-r2" "ns-lb" "ns-b1" "ns-b2" "ns-b3")

do_teardown() {
    echo "Stopping processes inside namespaces and removing namespaces..."
    for ns in "${NAMESPACES[@]}"; do
        if ip netns list | grep -qw "${ns}"; then
            pids=$(ip netns pids "${ns}" 2>/dev/null || true)
            if [[ -n "${pids}" ]]; then
                kill -9 ${pids} 2>/dev/null || true
            fi
            ip netns del "${ns}" 2>/dev/null || true
        fi
    done
    echo "[SUCCESS] All SLB-HTD namespaces and background processes cleaned up."
}

do_netem() {
    local loss_pct="${1:-0}"
    ip netns exec ns-r1 tc qdisc del dev eth1 root 2>/dev/null || true
    ip netns exec ns-r2 tc qdisc del dev eth0 root 2>/dev/null || true

    if [[ "${loss_pct}" == "0" || "${loss_pct}" == "0%" ]]; then
        echo "[NETEM] Cleared packet loss (0%) on transit link (ns-r1:eth1 <-> ns-r2:eth0)."
    else
        local clean_pct="${loss_pct%\%}"
        ip netns exec ns-r1 tc qdisc add dev eth1 root netem loss "${clean_pct}%"
        ip netns exec ns-r2 tc qdisc add dev eth0 root netem loss "${clean_pct}%"
        echo "[NETEM] Applied ${clean_pct}% packet loss on egress of ns-r1:eth1 and ns-r2:eth0."
    fi
}

do_verify() {
    echo "======================================================================"
    echo "1. ROUTING TABLES (ip route)"
    echo "======================================================================"
    for ns in ns-client ns-r1 ns-r2 ns-lb ns-b1 ns-b2 ns-b3; do
        echo ">>> Namespace: ${ns}"
        ip netns exec "${ns}" ip route
        echo ""
    done

    echo "======================================================================"
    echo "2. BASE RTT MEASUREMENT (ns-client -> 192.168.20.10)"
    echo "======================================================================"
    ip netns exec ns-client ping -c 4 192.168.20.10
    echo ""

    echo "======================================================================"
    echo "3. TRACEROUTE ACROSS ROUTED TOPOLOGY (ns-client -> 192.168.20.10)"
    echo "======================================================================"
    if command -v traceroute >/dev/null 2>&1; then
        ip netns exec ns-client traceroute -n 192.168.20.10
    else
        ip netns exec ns-client tracepath -n 192.168.20.10
    fi
    echo ""

    echo "======================================================================"
    echo "4. BACKEND ISOLATION PROOF (ns-client -> 172.20.0.11 MUST FAIL)"
    echo "======================================================================"
    set +e
    ip netns exec ns-client ping -c 2 -W 1 172.20.0.11
    local ping_rc=$?
    set -e
    if [[ ${ping_rc} -ne 0 ]]; then
        echo "[EXPECTED] Direct ping from client to backend 172.20.0.11 failed (exit code ${ping_rc})."
        echo "           Backends are reachable strictly via the reverse proxy on 192.168.20.10:8080."
    else
        echo "[ERROR] Direct ping to 172.20.0.11 unexpectedly succeeded! Check ip_forward on ns-lb." >&2
        exit 1
    fi
}

write_haproxy_configs() {
    local cfg_dir="${SCRIPT_DIR}/configs"
    mkdir -p "${cfg_dir}"
    for pair in "rr:roundrobin" "lc:leastconn" "iphash:source"; do
        local short="${pair%%:*}"
        local algo="${pair##*:}"
        cat <<EOF > "${cfg_dir}/haproxy_${short}.cfg"
global
    log stdout format raw local0 info
    maxconn 4096

defaults
    log     global
    mode    http
    option  httplog
    option  httpclose
    option  dontlognull
    timeout connect 2000ms
    timeout client  10000ms
    timeout server  10000ms
    timeout check   500ms

frontend http_front
    bind 192.168.20.10:8080
    default_backend http_back_${short}

backend http_back_${short}
    balance ${algo}
    option httpchk GET /health
    http-check expect status 200
    default-server inter 1s fall 3 rise 2
    server b1 172.20.0.11:8001 check
    server b2 172.20.0.12:8002 check
    server b3 172.20.0.13:8003 check
EOF
    done
}

if [[ "${ACTION}" == "teardown" ]]; then
    do_teardown
    exit 0
elif [[ "${ACTION}" == "netem" ]]; then
    do_netem "${2:-0}"
    exit 0
elif [[ "${ACTION}" == "verify" ]]; then
    do_verify
    exit 0
fi

do_teardown >/dev/null 2>&1 || true
write_haproxy_configs

echo "[1/6] Creating 7 Linux network namespaces..."
for ns in "${NAMESPACES[@]}"; do
    ip netns add "${ns}"
    ip -n "${ns}" link set lo up
done

echo "[2/6] Creating veth pairs and Linux bridge br-be inside ns-lb..."
ip link add veth-cl type veth peer name veth-r1-cl
ip link set veth-cl netns ns-client name eth0
ip link set veth-r1-cl netns ns-r1 name eth0

ip link add veth-r1-r2 type veth peer name veth-r2-r1
ip link set veth-r1-r2 netns ns-r1 name eth1
ip link set veth-r2-r1 netns ns-r2 name eth0

ip link add veth-r2-lb type veth peer name veth-lb-r2
ip link set veth-r2-lb netns ns-r2 name eth1
ip link set veth-lb-r2 netns ns-lb name eth0

ip -n ns-lb link add name br-be type bridge
ip -n ns-lb link set br-be up

for i in 1 2 3; do
    ip link add "veth-b${i}" type veth peer name "veth-lb-b${i}"
    ip link set "veth-b${i}" netns "ns-b${i}" name eth0
    ip link set "veth-lb-b${i}" netns ns-lb
    ip -n ns-lb link set "veth-lb-b${i}" master br-be
    ip -n ns-lb link set "veth-lb-b${i}" up
done

echo "[3/6] Assigning IPv4 addresses according to Table 1 (Addressing Plan)..."
ip -n ns-client addr add 192.168.10.50/24 dev eth0
ip -n ns-client addr add 192.168.10.51/24 dev eth0
ip -n ns-client addr add 192.168.10.52/24 dev eth0
ip -n ns-client link set eth0 up

ip -n ns-r1 addr add 192.168.10.1/24 dev eth0
ip -n ns-r1 addr add 10.0.0.1/30 dev eth1
ip -n ns-r1 link set eth0 up
ip -n ns-r1 link set eth1 up

ip -n ns-r2 addr add 10.0.0.2/30 dev eth0
ip -n ns-r2 addr add 192.168.20.1/24 dev eth1
ip -n ns-r2 link set eth0 up
ip -n ns-r2 link set eth1 up

ip -n ns-lb addr add 192.168.20.10/24 dev eth0
ip -n ns-lb addr add 172.20.0.10/24 dev br-be
ip -n ns-lb link set eth0 up

ip -n ns-b1 addr add 172.20.0.11/24 dev eth0
ip -n ns-b1 link set eth0 up
ip -n ns-b2 addr add 172.20.0.12/24 dev eth0
ip -n ns-b2 link set eth0 up
ip -n ns-b3 addr add 172.20.0.13/24 dev eth0
ip -n ns-b3 link set eth0 up

echo "[4/6] Configuring IP forwarding (ON for R1/R2, OFF for LB/Client/Backends)..."
ip netns exec ns-r1 sysctl -qw net.ipv4.ip_forward=1
ip netns exec ns-r2 sysctl -qw net.ipv4.ip_forward=1
ip netns exec ns-lb sysctl -qw net.ipv4.ip_forward=0
ip netns exec ns-client sysctl -qw net.ipv4.ip_forward=0
for i in 1 2 3; do
    ip netns exec "ns-b${i}" sysctl -qw net.ipv4.ip_forward=0
done

echo "[5/6] Installing static routes according to Table 2..."
ip -n ns-client route add default via 192.168.10.1
ip -n ns-r1 route add 192.168.20.0/24 via 10.0.0.2
ip -n ns-r2 route add 192.168.10.0/24 via 10.0.0.1
ip -n ns-lb route add default via 192.168.20.1

echo "[6/6] Disabling veth checksum offload for clean tcpdump/Wireshark captures..."
if command -v ethtool >/dev/null 2>&1; then
    ip netns exec ns-client ethtool -K eth0 tx off rx off >/dev/null 2>&1 || true
    ip netns exec ns-r1 ethtool -K eth0 tx off rx off >/dev/null 2>&1 || true
    ip netns exec ns-r1 ethtool -K eth1 tx off rx off >/dev/null 2>&1 || true
    ip netns exec ns-r2 ethtool -K eth0 tx off rx off >/dev/null 2>&1 || true
    ip netns exec ns-r2 ethtool -K eth1 tx off rx off >/dev/null 2>&1 || true
    ip netns exec ns-lb ethtool -K eth0 tx off rx off >/dev/null 2>&1 || true
    ip netns exec ns-lb ethtool -K br-be tx off rx off >/dev/null 2>&1 || true
fi

echo "[SUCCESS] SLB-HTD 7-namespace topology and HAProxy configs (configs/haproxy_*.cfg) are ready."
