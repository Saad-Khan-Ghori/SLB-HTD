#!/usr/bin/env python3
"""
CS3001 Computer Networks (Fall 2026) - FAST School of Computing, Karachi
Project: Software Load Balancer with Health Checks and Dynamic Traffic Distribution (SLB-HTD)
File: generate_pcapng_and_diagrams.py
Primary Author: Saad Khan Ghori (24K-0565)

Description:
    1. Constructs illustrative PCAPNG (`.pcapng`) files containing
       Ethernet II + IPv4 + TCP + HTTP/1.1 frames for C1, C2, and C3.
       These are generated examples under `illustrative/captures/`, not live captures.
       These files open directly in Wireshark / `tshark` / `tcpdump -r` and display the
       exact IP addresses, ports, TCP flags (SYN, SYN-ACK, ACK, PSH-ACK, FIN-ACK, RST-ACK),
       and HTTP messages (`GET / HTTP/1.1`, `GET /health HTTP/1.1`, `HTTP/1.1 200 OK`,
       `HTTP/1.1 502 Bad Gateway`).
    2. Renders annotated illustrative packet figures and a topology diagram under
       `illustrative/figures/`.
"""

from __future__ import annotations

import os
import socket
import struct
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches


def ipv4_checksum(header: bytes) -> int:
    if len(header) % 2 == 1:
        header += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(header) // 2), header))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return (~s) & 0xFFFF


def tcp_checksum(src_ip: str, dst_ip: str, tcp_header_and_payload: bytes) -> int:
    pseudo = (
        socket.inet_aton(src_ip)
        + socket.inet_aton(dst_ip)
        + struct.pack("!BBH", 0, socket.IPPROTO_TCP, len(tcp_header_and_payload))
    )
    data = pseudo + tcp_header_and_payload
    if len(data) % 2 == 1:
        data += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return (~s) & 0xFFFF


def build_eth_ipv4_tcp_frame(
    src_mac: bytes,
    dst_mac: bytes,
    src_ip: str,
    dst_ip: str,
    src_port: int,
    dst_port: int,
    seq: int,
    ack: int,
    flags: int,
    payload: bytes = b"",
    ttl: int = 64,
    ip_id: int = 1000,
) -> bytes:
    # Ethernet II header (14 bytes)
    eth_hdr = dst_mac + src_mac + struct.pack("!H", 0x0800)

    # TCP header (20 bytes)
    data_offset = (5 << 4)
    window = 64240
    tcp_hdr_zero = struct.pack(
        "!HHIIBBHHH",
        src_port,
        dst_port,
        seq & 0xFFFFFFFF,
        ack & 0xFFFFFFFF,
        data_offset,
        flags,
        window,
        0,
        0,
    )
    csum = tcp_checksum(src_ip, dst_ip, tcp_hdr_zero + payload)
    tcp_hdr = struct.pack(
        "!HHIIBBHHH",
        src_port,
        dst_port,
        seq & 0xFFFFFFFF,
        ack & 0xFFFFFFFF,
        data_offset,
        flags,
        window,
        csum,
        0,
    )

    # IPv4 header (20 bytes)
    total_len = 20 + len(tcp_hdr) + len(payload)
    ip_hdr_zero = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        total_len,
        ip_id & 0xFFFF,
        0x4000,
        ttl,
        socket.IPPROTO_TCP,
        0,
        socket.inet_aton(src_ip),
        socket.inet_aton(dst_ip),
    )
    ip_csum = ipv4_checksum(ip_hdr_zero)
    ip_hdr = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        total_len,
        ip_id & 0xFFFF,
        0x4000,
        ttl,
        socket.IPPROTO_TCP,
        ip_csum,
        socket.inet_aton(src_ip),
        socket.inet_aton(dst_ip),
    )

    return eth_hdr + ip_hdr + tcp_hdr + payload


def pad32(data: bytes) -> bytes:
    rem = len(data) % 4
    return data if rem == 0 else data + b"\x00" * (4 - rem)


def make_pcapng_option(opt_code: int, value: bytes) -> bytes:
    padded = pad32(value)
    return struct.pack("<HH", opt_code, len(value)) + padded


def write_pcapng_file(filepath: str, iface_name: str, packets: list[tuple[float, bytes]]) -> None:
    """Writes a standard PCAPNG file with Section Header, Interface Description, and Enhanced Packet Blocks."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    blocks: list[bytes] = []

    # 1. Section Header Block (SHB) - Block Type 0x0A0D0D0A
    shb_body = struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)
    shb_len = 12 + len(shb_body)
    blocks.append(struct.pack("<II", 0x0A0D0D0A, shb_len) + shb_body + struct.pack("<I", shb_len))

    # 2. Interface Description Block (IDB) - Block Type 0x00000001 (LinkType 1 = Ethernet)
    idb_opts = (
        make_pcapng_option(2, iface_name.encode("utf-8"))
        + struct.pack("<HH", 0, 0)
    )
    idb_body = struct.pack("<HHI", 1, 0, 65535) + idb_opts
    idb_len = 12 + len(idb_body)
    blocks.append(struct.pack("<II", 0x00000001, idb_len) + idb_body + struct.pack("<I", idb_len))

    # 3. Enhanced Packet Blocks (EPB) - Block Type 0x00000006
    for ts_sec, frame in packets:
        ts_us = int(round(ts_sec * 1_000_000))
        ts_high = (ts_us >> 32) & 0xFFFFFFFF
        ts_low = ts_us & 0xFFFFFFFF
        cap_len = len(frame)
        padded_frame = pad32(frame)
        epb_body = struct.pack("<IIIII", 0, ts_high, ts_low, cap_len, cap_len) + padded_frame
        epb_len = 12 + len(epb_body)
        blocks.append(struct.pack("<II", 0x00000006, epb_len) + epb_body + struct.pack("<I", epb_len))

    with open(filepath, "wb") as f:
        for b in blocks:
            f.write(b)


# TCP flag constants
SYN = 0x02
ACK = 0x10
SYN_ACK = SYN | ACK
PSH_ACK = 0x08 | ACK
FIN_ACK = 0x01 | ACK
RST_ACK = 0x04 | ACK


def build_http_exchange_packets(
    t0: float,
    src_mac: bytes,
    dst_mac: bytes,
    src_ip: str,
    dst_ip: str,
    src_port: int,
    dst_port: int,
    req_bytes: bytes,
    resp_bytes: bytes,
    req_ttl: int = 64,
    resp_ttl: int = 64,
    server_delay_s: float = 0.002,
    ip_id_base: int = 1000,
) -> list[tuple[float, bytes]]:
    """Constructs a complete 10-packet TCP 3-way handshake + HTTP request/response + FIN teardown."""
    c_seq = 100000
    s_seq = 500000
    pkts: list[tuple[float, bytes]] = []

    # 1. SYN
    pkts.append((t0 + 0.0000, build_eth_ipv4_tcp_frame(src_mac, dst_mac, src_ip, dst_ip, src_port, dst_port, c_seq, 0, SYN, b"", req_ttl, ip_id_base + 1)))
    # 2. SYN, ACK
    pkts.append((t0 + 0.0003, build_eth_ipv4_tcp_frame(dst_mac, src_mac, dst_ip, src_ip, dst_port, src_port, s_seq, c_seq + 1, SYN_ACK, b"", resp_ttl, ip_id_base + 2)))
    c_seq += 1
    s_seq += 1
    # 3. ACK
    pkts.append((t0 + 0.0005, build_eth_ipv4_tcp_frame(src_mac, dst_mac, src_ip, dst_ip, src_port, dst_port, c_seq, s_seq, ACK, b"", req_ttl, ip_id_base + 3)))
    # 4. HTTP GET Request (PSH, ACK)
    pkts.append((t0 + 0.0007, build_eth_ipv4_tcp_frame(src_mac, dst_mac, src_ip, dst_ip, src_port, dst_port, c_seq, s_seq, PSH_ACK, req_bytes, req_ttl, ip_id_base + 4)))
    c_seq += len(req_bytes)
    # 5. Server ACK of request
    pkts.append((t0 + 0.0010, build_eth_ipv4_tcp_frame(dst_mac, src_mac, dst_ip, src_ip, dst_port, src_port, s_seq, c_seq, ACK, b"", resp_ttl, ip_id_base + 5)))
    # 6. HTTP 200 OK Response (PSH, ACK)
    t_resp = t0 + 0.0010 + server_delay_s
    pkts.append((t_resp, build_eth_ipv4_tcp_frame(dst_mac, src_mac, dst_ip, src_ip, dst_port, src_port, s_seq, c_seq, PSH_ACK, resp_bytes, resp_ttl, ip_id_base + 6)))
    s_seq += len(resp_bytes)
    # 7. Client ACK of response
    pkts.append((t_resp + 0.0003, build_eth_ipv4_tcp_frame(src_mac, dst_mac, src_ip, dst_ip, src_port, dst_port, c_seq, s_seq, ACK, b"", req_ttl, ip_id_base + 7)))
    # 8. Server FIN, ACK (Connection: close)
    pkts.append((t_resp + 0.0005, build_eth_ipv4_tcp_frame(dst_mac, src_mac, dst_ip, src_ip, dst_port, src_port, s_seq, c_seq, FIN_ACK, b"", resp_ttl, ip_id_base + 8)))
    s_seq += 1
    # 9. Client FIN, ACK
    pkts.append((t_resp + 0.0008, build_eth_ipv4_tcp_frame(src_mac, dst_mac, src_ip, dst_ip, src_port, dst_port, c_seq, s_seq, FIN_ACK, b"", req_ttl, ip_id_base + 9)))
    c_seq += 1
    # 10. Server final ACK
    pkts.append((t_resp + 0.0010, build_eth_ipv4_tcp_frame(dst_mac, src_mac, dst_ip, src_ip, dst_port, src_port, s_seq, c_seq, ACK, b"", resp_ttl, ip_id_base + 10)))
    return pkts


def generate_all_pcapng_files(cap_dir: str) -> None:
    base_ts = 1792400400.000000  # Oct 2026 epoch timestamp
    mac_client = b"\x02\x00\x00\x10\x00\x50"
    mac_r1_cl  = b"\x02\x00\x00\x10\x00\x01"
    mac_r2_dmz = b"\x02\x00\x00\x20\x00\x01"
    mac_lb_dmz = b"\x02\x00\x00\x20\x00\x10"
    mac_lb_br  = b"\x02\x00\x00\xbe\x00\x10"
    mac_b1     = b"\x02\x00\x00\xbe\x00\x11"
    mac_b2     = b"\x02\x00\x00\xbe\x00\x12"
    mac_b3     = b"\x02\x00\x00\xbe\x00\x13"

    client_get = (
        b"GET / HTTP/1.1\r\n"
        b"Host: 192.168.20.10:8080\r\n"
        b"User-Agent: curl/8.5.0\r\n"
        b"Accept: */*\r\n\r\n"
    )
    proxy_get_b1 = (
        b"GET / HTTP/1.1\r\n"
        b"Host: 192.168.20.10:8080\r\n"
        b"User-Agent: curl/8.5.0\r\n"
        b"Accept: */*\r\n"
        b"Connection: close\r\n\r\n"
    )
    b1_body = b"Backend: B1 (172.20.0.11:8001) | Delay: 0ms | ReqID: 1 | Path: /\n"
    b2_body = b"Backend: B2 (172.20.0.12:8002) | Delay: 50ms | ReqID: 1 | Path: /\n"
    b3_body = b"Backend: B3 (172.20.0.13:8003) | Delay: 150ms | ReqID: 1 | Path: /\n"

    def make_200_resp(name: bytes, body: bytes) -> bytes:
        return (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"X-Backend-Name: " + name + b"\r\n"
            b"Connection: close\r\n\r\n"
            + body
        )

    # 1. C1 (ns-client eth0): Client -> LB (TTL=64 on request, TTL=62 on response after 2 routers!)
    c1_pkts = []
    c1_pkts.extend(build_http_exchange_packets(
        base_ts + 0.100, mac_client, mac_r1_cl,
        "192.168.10.50", "192.168.20.10", 48210, 8080,
        client_get, make_200_resp(b"B1", b1_body),
        req_ttl=64, resp_ttl=62, server_delay_s=0.003, ip_id_base=1100
    ))
    c1_pkts.extend(build_http_exchange_packets(
        base_ts + 0.400, mac_client, mac_r1_cl,
        "192.168.10.50", "192.168.20.10", 48212, 8080,
        client_get, make_200_resp(b"B2", b2_body),
        req_ttl=64, resp_ttl=62, server_delay_s=0.053, ip_id_base=1200
    ))
    c1_pkts.extend(build_http_exchange_packets(
        base_ts + 0.800, mac_client, mac_r1_cl,
        "192.168.10.50", "192.168.20.10", 48214, 8080,
        client_get, make_200_resp(b"B3", b3_body),
        req_ttl=64, resp_ttl=62, server_delay_s=0.153, ip_id_base=1300
    ))
    write_pcapng_file(os.path.join(cap_dir, "c1_client_to_lb.pcapng"), "ns-client:eth0", c1_pkts)

    # 2. C2 (ns-lb eth0): DMZ side of LB (TTL=62 on incoming request after R1 & R2, TTL=64 on response!)
    c2_pkts = []
    c2_pkts.extend(build_http_exchange_packets(
        base_ts + 0.1002, mac_r2_dmz, mac_lb_dmz,
        "192.168.10.50", "192.168.20.10", 48210, 8080,
        client_get, make_200_resp(b"B1", b1_body),
        req_ttl=62, resp_ttl=64, server_delay_s=0.0028, ip_id_base=1100
    ))
    c2_pkts.extend(build_http_exchange_packets(
        base_ts + 0.4002, mac_r2_dmz, mac_lb_dmz,
        "192.168.10.50", "192.168.20.10", 48212, 8080,
        client_get, make_200_resp(b"B2", b2_body),
        req_ttl=62, resp_ttl=64, server_delay_s=0.0528, ip_id_base=1200
    ))
    c2_pkts.extend(build_http_exchange_packets(
        base_ts + 0.8002, mac_r2_dmz, mac_lb_dmz,
        "192.168.10.50", "192.168.20.10", 48214, 8080,
        client_get, make_200_resp(b"B3", b3_body),
        req_ttl=62, resp_ttl=64, server_delay_s=0.1528, ip_id_base=1300
    ))
    write_pcapng_file(os.path.join(cap_dir, "c2_dmz_to_lb.pcapng"), "ns-lb:eth0", c2_pkts)

    # 3. C3 (ns-lb br-be): Backend bridge showing new TCP connections from 172.20.0.10 + /health probes
    c3_pkts = []
    for cycle in range(2):
        t_c = base_ts + cycle * 1.000
        for idx, (b_ip, b_port, b_mac, b_name) in enumerate([
            ("172.20.0.11", 8001, mac_b1, b"B1"),
            ("172.20.0.12", 8002, mac_b2, b"B2"),
            ("172.20.0.13", 8003, mac_b3, b"B3"),
        ]):
            probe_req = (
                f"GET /health HTTP/1.1\r\nHost: {b_ip}:{b_port}\r\n"
                f"User-Agent: SLB-HTD-HealthChecker/1.0\r\nConnection: close\r\n\r\n"
            ).encode("ascii")
            h_body = b"HEALTHY " + b_name + b"\n"
            probe_resp = (
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n"
                + f"Content-Length: {len(h_body)}\r\nConnection: close\r\n\r\n".encode("ascii")
                + h_body
            )
            c3_pkts.extend(build_http_exchange_packets(
                t_c + idx * 0.004, mac_lb_br, b_mac,
                "172.20.0.10", b_ip, 52100 + cycle * 10 + idx * 2, b_port,
                probe_req, probe_resp,
                req_ttl=64, resp_ttl=64, server_delay_s=0.0006, ip_id_base=2000 + cycle * 100 + idx * 20
            ))

    # Add proxied requests on C3 (from 172.20.0.10 -> B1, B2, B3)
    c3_pkts.extend(build_http_exchange_packets(
        base_ts + 0.101, mac_lb_br, mac_b1,
        "172.20.0.10", "172.20.0.11", 54010, 8001,
        proxy_get_b1, make_200_resp(b"B1", b1_body),
        req_ttl=64, resp_ttl=64, server_delay_s=0.001, ip_id_base=3100
    ))
    c3_pkts.extend(build_http_exchange_packets(
        base_ts + 0.401, mac_lb_br, mac_b2,
        "172.20.0.10", "172.20.0.12", 54012, 8002,
        proxy_get_b1, make_200_resp(b"B2", b2_body),
        req_ttl=64, resp_ttl=64, server_delay_s=0.050, ip_id_base=3200
    ))
    c3_pkts.extend(build_http_exchange_packets(
        base_ts + 0.801, mac_lb_br, mac_b3,
        "172.20.0.10", "172.20.0.13", 54014, 8003,
        proxy_get_b1, make_200_resp(b"B3", b3_body),
        req_ttl=64, resp_ttl=64, server_delay_s=0.150, ip_id_base=3300
    ))
    c3_pkts.sort(key=lambda x: x[0])
    write_pcapng_file(os.path.join(cap_dir, "c3_backend_bridge_normal_and_health.pcapng"), "ns-lb:br-be", c3_pkts)

    # 4. C3 Failover and Recovery capture (B2 stopped -> 3 failed SYN/RST probes -> B2 restarted -> 2 OK probes -> UP)
    fail_pkts: list[tuple[float, bytes]] = []
    # 3 consecutive failed probes to B2 (172.20.0.12:8002) at t=+1.0s, +2.0s, +3.0s
    for probe_i in range(3):
        tp = base_ts + 10.0 + probe_i * 1.0
        sport = 56000 + probe_i * 2
        fail_pkts.append((
            tp,
            build_eth_ipv4_tcp_frame(mac_lb_br, mac_b2, "172.20.0.10", "172.20.0.12", sport, 8002, 700000 + probe_i * 100, 0, SYN, b"", 64, 4100 + probe_i * 2)
        ))
        fail_pkts.append((
            tp + 0.0003,
            build_eth_ipv4_tcp_frame(mac_b2, mac_lb_br, "172.20.0.12", "172.20.0.10", 8002, sport, 0, 700001 + probe_i * 100, RST_ACK, b"", 64, 4101 + probe_i * 2)
        ))

    # B2 restarted at t=+14.5s -> 2 consecutive successful /health probes at t=+15.0s and t=+16.0s
    probe_b2_req = (
        b"GET /health HTTP/1.1\r\nHost: 172.20.0.12:8002\r\n"
        b"User-Agent: SLB-HTD-HealthChecker/1.0\r\nConnection: close\r\n\r\n"
    )
    probe_b2_resp = (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n"
        b"Content-Length: 11\r\nConnection: close\r\n\r\nHEALTHY B2\n"
    )
    for rise_i in range(2):
        tr = base_ts + 15.0 + rise_i * 1.0
        fail_pkts.extend(build_http_exchange_packets(
            tr, mac_lb_br, mac_b2,
            "172.20.0.10", "172.20.0.12", 56100 + rise_i * 2, 8002,
            probe_b2_req, probe_b2_resp,
            req_ttl=64, resp_ttl=64, server_delay_s=0.0006, ip_id_base=4500 + rise_i * 20
        ))

    # First proxied client request served by B2 again at t=+16.25s after rise=2 marks B2 UP
    fail_pkts.extend(build_http_exchange_packets(
        base_ts + 16.25, mac_lb_br, mac_b2,
        "172.20.0.10", "172.20.0.12", 56200, 8002,
        proxy_get_b1, make_200_resp(b"B2", b2_body),
        req_ttl=64, resp_ttl=64, server_delay_s=0.050, ip_id_base=4700
    ))
    fail_pkts.sort(key=lambda x: x[0])
    write_pcapng_file(os.path.join(cap_dir, "c3_failover_and_recovery.pcapng"), "ns-lb:br-be", fail_pkts)


def render_annotated_wireshark_screenshots(fig_dir: str) -> None:
    """
    Renders illustrative packet tables from constructed examples, not live captures
    for Section 8 of the Technical Report:
      1. Figure C1/C2/C3 Split-TCP & Reverse Proxy Handshake + HTTP Forwarding
      2. Figure C3 Active Health Check Probes, 3x Failed Probes (RST -> DOWN), and 2x Rise Recovery
    """
    os.makedirs(fig_dir, exist_ok=True)

    # --- Figure 1: Annotated Packet Capture (C1 Client-to-LB vs C3 LB-to-Backend Split-TCP) ---
    fig, ax = plt.subplots(figsize=(12.5, 5.6), dpi=200)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    # Title Banner
    ax.add_patch(patches.Rectangle((0, 92), 100, 8, facecolor="#1B365D", edgecolor="none"))
    ax.text(2, 96, "ILLUSTRATIVE ONLY — C1 (ns-client:eth0) & C3 (ns-lb:br-be) Split-TCP Proxy Path",
            color="white", fontsize=11, fontweight="bold", va="center")

    # Column Headers
    headers = [("No.", 2), ("Time (s)", 7), ("Cap", 15), ("Source IP:Port", 21), ("Destination IP:Port", 40), ("Proto", 59), ("Flags / Len", 66), ("Annotated Info & HTTP Payload", 78)]
    ax.add_patch(patches.Rectangle((0, 86), 100, 6, facecolor="#E2E8F0", edgecolor="#CBD5E1"))
    for h_text, xpos in headers:
        ax.text(xpos, 89, h_text, fontsize=8.5, fontweight="bold", color="#1E293B", va="center")

    rows_data = [
        ("1", "0.1000", "C1", "192.168.10.50:48210", "192.168.20.10:8080", "TCP", "[SYN] TTL=64", "Client -> LB TCP Handshake (Seq=0, Win=64240)", "#EFF6FF"),
        ("2", "0.1003", "C1", "192.168.20.10:8080", "192.168.10.50:48210", "TCP", "[SYN, ACK] TTL=62", "LB -> Client SYN-ACK (TTL=62 proves 2 router hops R2->R1)", "#EFF6FF"),
        ("3", "0.1005", "C1", "192.168.10.50:48210", "192.168.20.10:8080", "TCP", "[ACK] TTL=64", "Client completes 3-way handshake with SLB (Seq=1, Ack=1)", "#EFF6FF"),
        ("4", "0.1007", "C1", "192.168.10.50:48210", "192.168.20.10:8080", "HTTP", "[PSH, ACK] Len=78", "GET / HTTP/1.1 (Host: 192.168.20.10:8080)", "#DCFCE7"),
        ("5", "0.1010", "C3", "172.20.0.10:54010",   "172.20.0.11:8001",   "TCP", "[SYN] TTL=64", "SLB opens NEW backend TCP conn from br-be (172.20.0.10)", "#FEF9C3"),
        ("6", "0.1012", "C3", "172.20.0.11:8001",   "172.20.0.10:54010",   "TCP", "[SYN, ACK] TTL=64", "Backend B1 accepts connection on isolated 172.20.0.0/24", "#FEF9C3"),
        ("7", "0.1015", "C3", "172.20.0.10:54010",   "172.20.0.11:8001",   "HTTP", "[PSH, ACK] Len=97", "GET / HTTP/1.1 (Injected header: Connection: close)", "#DCFCE7"),
        ("8", "0.1025", "C3", "172.20.0.11:8001",   "172.20.0.10:54010",   "HTTP", "[PSH, ACK] Len=186", "HTTP/1.1 200 OK (Body: 'Backend: B1 ... Delay: 0ms')", "#DCFCE7"),
        ("9", "0.1030", "C3", "172.20.0.11:8001",   "172.20.0.10:54010",   "TCP", "[FIN, ACK] TTL=64", "Backend B1 closes backend connection after 1 response", "#F1F5F9"),
        ("10", "0.1038", "C1", "192.168.20.10:8080", "192.168.10.50:48210", "HTTP", "[PSH, ACK] TTL=62", "SLB relays HTTP/1.1 200 OK back to Client over R2->R1", "#DCFCE7"),
        ("11", "0.1043", "C1", "192.168.20.10:8080", "192.168.10.50:48210", "TCP", "[FIN, ACK] TTL=62", "SLB closes Client TCP connection (1 req = 1 conn)", "#F1F5F9"),
    ]

    y = 80.5
    for row in rows_data:
        no, ts, cap, src, dst, proto, flags, info, bg = row
        ax.add_patch(patches.Rectangle((0, y - 3.2), 100, 6.2, facecolor=bg, edgecolor="#CBD5E1", linewidth=0.6))
        ax.text(2,  y, no, fontsize=8, family="monospace", va="center")
        ax.text(7,  y, ts, fontsize=8, family="monospace", va="center")
        ax.text(15, y, cap, fontsize=8, fontweight="bold", color="#1B365D", va="center")
        ax.text(21, y, src, fontsize=8, family="monospace", va="center")
        ax.text(40, y, dst, fontsize=8, family="monospace", va="center")
        ax.text(59, y, proto, fontsize=8, fontweight="bold", va="center")
        ax.text(66, y, flags, fontsize=7.8, family="monospace", va="center")
        ax.text(78, y, info, fontsize=7.8, va="center")
        y -= 6.5

    # Callout footer annotation box
    ax.add_patch(patches.Rectangle((0, 0.2), 100, 10.8, facecolor="#F8FAFC", edgecolor="#1B365D", linewidth=1.2))
    ax.text(1.5, 8.6, "Constructed packet example (not measured evidence):", fontsize=8.8, fontweight="bold", color="#1B365D", va="center")
    ax.text(1.5, 3.8,
            "1. Split-TCP Termination: Client talks ONLY to 192.168.20.10:8080 (Frames 1–4); SLB initiates a separate TCP session from 172.20.0.10 to 172.20.0.11:8001 (Frames 5–9).\n"
            "2. Router Hop Decrement: Packets arriving from LB at Client (Frames 2, 10, 11) have IPv4 TTL=62 (64 - 2 routers: ns-r2 and ns-r1), illustrating routing across 3 subnets.",
            fontsize=8.0, color="#334155", va="center", linespacing=1.35)

    fig.tight_layout()
    fig.savefig(os.path.join(fig_dir, "fig_pcap_handshake_and_proxy.png"), bbox_inches="tight")
    plt.close(fig)

    # --- Figure 2: Annotated Packet Capture (C3 Health Probes, Failure Detection & Recovery) ---
    fig, ax = plt.subplots(figsize=(12.5, 5.4), dpi=200)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    ax.add_patch(patches.Rectangle((0, 92), 100, 8, facecolor="#7F1D1D", edgecolor="none"))
    ax.text(2, 96, "ILLUSTRATIVE ONLY — C3 (ns-lb:br-be) Active Health Checks, Backend 2 Failure (fall=3) & Recovery (rise=2)",
            color="white", fontsize=10.5, fontweight="bold", va="center")

    ax.add_patch(patches.Rectangle((0, 86), 100, 6, facecolor="#E2E8F0", edgecolor="#CBD5E1"))
    for h_text, xpos in headers:
        ax.text(xpos, 89, h_text, fontsize=8.5, fontweight="bold", color="#1E293B", va="center")

    fail_rows = [
        ("1", "09.000", "C3", "172.20.0.10:52102", "172.20.0.12:8002", "HTTP", "[PSH, ACK] GET", "GET /health HTTP/1.1 -> 200 OK ('HEALTHY B2') [B2 is UP]", "#DCFCE7"),
        ("2", "10.000", "C3", "172.20.0.10:56000", "172.20.0.12:8002", "TCP",  "[SYN] Seq=0",    "Health Probe #1 after B2 stopped at t=9.5s", "#FEE2E2"),
        ("3", "10.000", "C3", "172.20.0.12:8002", "172.20.0.10:56000", "TCP",  "[RST, ACK]",     "Connection Refused (Fail 1/3 — B2 still marked UP)", "#FEE2E2"),
        ("4", "11.000", "C3", "172.20.0.10:56002", "172.20.0.12:8002", "TCP",  "[SYN] -> [RST]", "Health Probe #2 Refused (Fail 2/3 — B2 still marked UP)", "#FEE2E2"),
        ("5", "12.000", "C3", "172.20.0.10:56004", "172.20.0.12:8002", "TCP",  "[SYN] -> [RST]", "Health Probe #3 Refused (Fail 3/3 -> SLB marks B2 DOWN!)", "#FECACA"),
        ("6", "12.450", "C3", "172.20.0.10:54110", "172.20.0.11:8001", "HTTP", "[PSH, ACK] 200", "Proxied requests bypass B2; served only by B1 & B3", "#EFF6FF"),
        ("7", "15.000", "C3", "172.20.0.10:56100", "172.20.0.12:8002", "HTTP", "GET /health 200", "B2 restarted at t=14.5s: Probe OK (Rise 1/2 — still DOWN)", "#FEF9C3"),
        ("8", "16.000", "C3", "172.20.0.10:56102", "172.20.0.12:8002", "HTTP", "GET /health 200", "Second consecutive Probe OK (Rise 2/2 -> SLB marks B2 UP!)", "#DCFCE7"),
        ("9", "16.250", "C3", "172.20.0.10:56200", "172.20.0.12:8002", "HTTP", "GET / -> 200 OK", "First proxied client request served by B2 after recovery!", "#DCFCE7"),
    ]

    y = 80.0
    for row in fail_rows:
        no, ts, cap, src, dst, proto, flags, info, bg = row
        ax.add_patch(patches.Rectangle((0, y - 3.4), 100, 6.6, facecolor=bg, edgecolor="#CBD5E1", linewidth=0.6))
        ax.text(2,  y, no, fontsize=8, family="monospace", va="center")
        ax.text(7,  y, ts, fontsize=8, family="monospace", va="center")
        ax.text(15, y, cap, fontsize=8, fontweight="bold", color="#7F1D1D", va="center")
        ax.text(21, y, src, fontsize=8, family="monospace", va="center")
        ax.text(40, y, dst, fontsize=8, family="monospace", va="center")
        ax.text(59, y, proto, fontsize=8, fontweight="bold", va="center")
        ax.text(66, y, flags, fontsize=7.8, family="monospace", va="center")
        ax.text(78, y, info, fontsize=7.8, va="center")
        y -= 7.2

    ax.add_patch(patches.Rectangle((0, 0.2), 100, 11.2, facecolor="#F8FAFC", edgecolor="#7F1D1D", linewidth=1.2))
    ax.text(1.5, 9.0, "Illustrative failover (illustrative/captures/c3_failover_and_recovery.pcapng):", fontsize=8.8, fontweight="bold", color="#7F1D1D", va="center")
    ax.text(1.5, 4.0,
            "• Detection Time (fall=3, inter=1s): B2 is stopped at t=9.50s; probes fail with TCP [RST, ACK] at t=10.0s, 11.0s, and 12.0s -> Detection Time = 2.50 s.\n"
            "• Recovery Time (rise=2, inter=1s): B2 restarts at t=14.50s; probes succeed with HTTP 200 OK at t=15.0s and 16.0s -> First client request served at t=16.25s (1.75 s).",
            fontsize=8.0, color="#334155", va="center", linespacing=1.35)

    fig.tight_layout()
    fig.savefig(os.path.join(fig_dir, "fig_pcap_health_and_failover.png"), bbox_inches="tight")
    plt.close(fig)


def render_topology_architecture_diagram(fig_dir: str) -> None:
    """Renders the 7-namespace, 2-router, 4-subnet architecture diagram with capture points C1-C3."""
    os.makedirs(fig_dir, exist_ok=True)
    fig, ax = plt.subplots(figsize=(12.0, 5.2), dpi=200)
    ax.set_xlim(0, 120)
    ax.set_ylim(0, 62)
    ax.axis("off")

    # Top flow boxes: Client -> R1 -> R2 -> LB
    boxes = [
        (3,  36, 22, 20, "#EFF6FF", "#1D4ED8", "ns-client (Client)",
         ["eth0: 192.168.10.50/24", "+ .51, .52 (IP-Hash)", "gw: 192.168.10.1", "Tools: hey, curl, ping"]),
        (33, 36, 22, 20, "#F8FAFC", "#334155", "ns-r1 (Router 1)",
         ["eth0: 192.168.10.1/24", "eth1: 10.0.0.1/30", "ip_forward = 1", "route 192.168.20.0/24", "  via 10.0.0.2"]),
        (64, 36, 22, 20, "#F8FAFC", "#334155", "ns-r2 (Router 2)",
         ["eth0: 10.0.0.2/30", "eth1: 192.168.20.1/24", "ip_forward = 1", "route 192.168.10.0/24", "  via 10.0.0.1"]),
        (94, 36, 23, 20, "#ECFDF5", "#047857", "ns-lb (SLB / HAProxy)",
         ["eth0: 192.168.20.10/24", "br-be: 172.20.0.10/24", "gw: 192.168.20.1", "ip_forward = 0 (Proxy)", "TCP Listen :8080"]),
    ]

    for x, y, w, h, fc, ec, title, lines in boxes:
        ax.add_patch(patches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6", facecolor=fc, edgecolor=ec, linewidth=1.6))
        ax.text(x + w / 2, y + h - 2.6, title, ha="center", va="center", fontsize=9.5, fontweight="bold", color=ec)
        for i, line in enumerate(lines):
            ax.text(x + 1.4, y + h - 6.2 - i * 2.8, line, ha="left", va="center", fontsize=7.8, family="monospace", color="#1E293B")

    # Subnet links & capture points C1, netem, C2
    # Link 1: 192.168.10.0/24
    ax.annotate("", xy=(33, 46), xytext=(25, 46), arrowprops=dict(arrowstyle="<->", lw=1.8, color="#1D4ED8"))
    ax.text(29, 50.5, "Client Subnet\n192.168.10.0/24", ha="center", va="center", fontsize=7.8, fontweight="bold", color="#1D4ED8")
    ax.add_patch(patches.Circle((27.5, 43.2), 1.9, facecolor="#7C3AED", edgecolor="none"))
    ax.text(27.5, 43.2, "C1", ha="center", va="center", fontsize=7.5, fontweight="bold", color="white")

    # Link 2: Transit 10.0.0.0/30 + tc netem
    ax.annotate("", xy=(64, 46), xytext=(55, 46), arrowprops=dict(arrowstyle="<->", lw=2.0, color="#DC2626"))
    ax.text(59.5, 51.0, "Transit Link\n10.0.0.0/30", ha="center", va="center", fontsize=7.8, fontweight="bold", color="#DC2626")
    ax.text(59.5, 41.8, "tc netem\nloss 0/2/5%", ha="center", va="center", fontsize=7.5, fontweight="bold", color="#DC2626")

    # Link 3: DMZ 192.168.20.0/24
    ax.annotate("", xy=(94, 46), xytext=(86, 46), arrowprops=dict(arrowstyle="<->", lw=1.8, color="#047857"))
    ax.text(90, 50.5, "DMZ Subnet\n192.168.20.0/24", ha="center", va="center", fontsize=7.8, fontweight="bold", color="#047857")
    ax.add_patch(patches.Circle((91.5, 43.2), 1.9, facecolor="#7C3AED", edgecolor="none"))
    ax.text(91.5, 43.2, "C2", ha="center", va="center", fontsize=7.5, fontweight="bold", color="white")

    # Linux bridge bar br-be
    ax.add_patch(patches.FancyBboxPatch((25, 22), 76, 5.2, boxstyle="round,pad=0.3", facecolor="#E0F2FE", edgecolor="#0284C7", linewidth=1.4))
    ax.text(63, 24.6, "Linux Bridge br-be inside ns-lb (172.20.0.10/24) — Isolated Backend Subnet 172.20.0.0/24",
            ha="center", va="center", fontsize=8.8, fontweight="bold", color="#0369A1")

    # Link from ns-lb down to br-be + C3
    ax.annotate("", xy=(98, 27.5), xytext=(98, 36), arrowprops=dict(arrowstyle="<->", lw=1.8, color="#0284C7"))
    ax.add_patch(patches.Circle((102.0, 31.8), 1.9, facecolor="#7C3AED", edgecolor="none"))
    ax.text(102.0, 31.8, "C3", ha="center", va="center", fontsize=7.5, fontweight="bold", color="white")

    # 3 Backend Boxes
    backends = [
        (22, 2, 24, 15, "ns-b1 (Backend 1)", "eth0: 172.20.0.11/24", "HTTP :8001 | /health", "Artificial Delay: 0 ms"),
        (51, 2, 24, 15, "ns-b2 (Backend 2)", "eth0: 172.20.0.12/24", "HTTP :8002 | /health", "Artificial Delay: 50 ms"),
        (80, 2, 24, 15, "ns-b3 (Backend 3)", "eth0: 172.20.0.13/24", "HTTP :8003 | /health", "Artificial Delay: 150 ms"),
    ]
    for bx, by, bw, bh, btitle, l1, l2, l3 in backends:
        ax.add_patch(patches.FancyBboxPatch((bx, by), bw, bh, boxstyle="round,pad=0.5", facecolor="#FFFBEB", edgecolor="#B45309", linewidth=1.4))
        ax.text(bx + bw / 2, by + bh - 2.5, btitle, ha="center", va="center", fontsize=8.8, fontweight="bold", color="#92400E")
        ax.text(bx + 1.2, by + bh - 6.0, l1, fontsize=7.6, family="monospace", color="#1E293B")
        ax.text(bx + 1.2, by + bh - 9.0, l2, fontsize=7.6, family="monospace", color="#1E293B")
        ax.text(bx + 1.2, by + bh - 12.0, l3, fontsize=7.6, fontweight="bold", color="#B45309")
        ax.annotate("", xy=(bx + bw / 2, by + bh), xytext=(bx + bw / 2, 22), arrowprops=dict(arrowstyle="<->", lw=1.5, color="#B45309"))

    fig.tight_layout()
    fig.savefig(os.path.join(fig_dir, "fig_network_topology.png"), bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    project_root = os.path.dirname(os.path.abspath(__file__))
    cap_dir = os.path.join(project_root, "illustrative", "captures")
    fig_dir = os.path.join(project_root, "illustrative", "figures")

    generate_all_pcapng_files(cap_dir)
    render_annotated_wireshark_screenshots(fig_dir)
    render_topology_architecture_diagram(fig_dir)
    print(f"[ILLUSTRATIVE ONLY] Constructed packets in {cap_dir} and diagrams in {fig_dir}; these are not live captures.")


if __name__ == "__main__":
    main()
