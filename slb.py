#!/usr/bin/env python3
"""
CS3001 Computer Networks (Fall 2026) - FAST School of Computing, Karachi
Project: Software Load Balancer with Health Checks and Dynamic Traffic Distribution (SLB-HTD)
File: src/slb.py
Authors:
    - Muhammad Wasil (24K-0790): asyncio proxy core, connection handling, Round-Robin, Least-Connections
    - Saad Khan Ghori (24K-0565): Active health checker (GET /health, fall=3, rise=2), IP-Hash (CRC32)

Description:
    An asynchronous HTTP/1.1 reverse proxy load balancer built with Python 3.10+ asyncio.
    Key design specifications (Proposal Section 5):
      1. One HTTP request per TCP connection:
         Reads client HTTP request headers, selects a healthy (UP) backend according to the
         active scheduling policy, forwards the request with 'Connection: close', streams
         the backend's response back to the client until EOF, and closes the client socket.
      2. Scheduling Policies:
         - Round-Robin ('rr')       : Cycles across currently healthy backends in order.
         - Least-Connections ('lc') : Selects the healthy backend with the fewest active
                                      proxied connections; breaks ties by backend order.
         - IP-Hash ('iphash')       : Computes CRC32(client_ip) modulo the number of
                                      currently healthy backends.
      3. Active Health Checking & Failover:
         - Probes GET /health on every backend every 1.0 s with a 500 ms timeout.
         - Marks a backend DOWN after 3 consecutive failed probes (fall = 3).
         - Marks a backend UP again after 2 consecutive successful probes (rise = 2).
         - During the detection window before 3 failed probes complete, if a client connection
           is routed to a dead backend and fails, the proxy returns HTTP 502 Bad Gateway
           without retrying on another backend.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
import os
import signal
import socket
import sys
import time
import zlib


@dataclass
class BackendServer:
    """State tracked for a single backend server in the pool."""

    name: str
    host: str
    port: int
    order_index: int
    is_up: bool = True
    active_connections: int = 0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    total_proxied_requests: int = 0
    total_failed_requests: int = 0

    @property
    def address_str(self) -> str:
        return f"{self.host}:{self.port}"


class SoftwareLoadBalancer:
    """Asyncio HTTP/1.1 Reverse Proxy Load Balancer with Active Health Checks."""

    def __init__(
        self,
        listen_host: str,
        listen_port: int,
        backends: list[BackendServer],
        policy: str = "rr",
        health_interval: float = 1.0,
        health_timeout: float = 0.5,
        fall_threshold: int = 3,
        rise_threshold: int = 2,
        proxy_connect_timeout: float = 2.0,
         proxy_read_timeout: float = 10.0,
        log_file: str | None = None,
        hash_seed: int = 0,
        allow_sim_ip_header: bool = False,
        verbose_requests: bool = False,
    ) -> None:
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.backends = backends
        self.policy = policy.lower()
        self.health_interval = health_interval
        self.health_timeout = health_timeout
        self.fall_threshold = fall_threshold
        self.rise_threshold = rise_threshold
        self.proxy_connect_timeout = proxy_connect_timeout
        self.proxy_read_timeout = proxy_read_timeout
        self.log_file = log_file
        self.hash_seed = hash_seed
        self.allow_sim_ip_header = allow_sim_ip_header
        self.verbose_requests = verbose_requests

        # Round-Robin cursor tracks the next index in the healthy backend list
        self._rr_cursor: int = 0

        if self.log_file:
            os.makedirs(os.path.dirname(os.path.abspath(self.log_file)), exist_ok=True)

    def log_event(self, category: str, message: str) -> None:
        now = time.time()
        ts = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"[{ts}] [EPOCH={now:.6f}] [{category}] {message}\n"
        sys.stdout.write(line)
        sys.stdout.flush()
        if self.log_file:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line)

    def get_healthy_backends(self) -> list[BackendServer]:
        """Returns backends currently marked UP in their configured order."""
        return [b for b in self.backends if b.is_up]

    def select_backend(self, client_ip: str) -> BackendServer | None:
        """Selects a healthy backend using the configured scheduling policy."""
        healthy = self.get_healthy_backends()
        if not healthy:
            return None

        if self.policy == "rr":
            idx = self._rr_cursor % len(healthy)
            chosen = healthy[idx]
            self._rr_cursor = (idx + 1) % len(healthy)
            return chosen

        if self.policy == "lc":
            # Least-Connections: pick healthy backend with minimum active_connections;
            # ties broken by backend order (order_index).
            return min(healthy, key=lambda b: (b.active_connections, b.order_index))

        if self.policy == "iphash":
            # IP-Hash: stable CRC32 hash of client's IPv4 address modulo healthy count
            crc = zlib.crc32(client_ip.encode("utf-8"), self.hash_seed) & 0xFFFFFFFF
            idx = crc % len(healthy)
            return healthy[idx]

        raise ValueError(f"Unknown scheduling policy: {self.policy}")

    @staticmethod
    def _rewrite_request_connection_close(raw_header_bytes: bytes) -> tuple[bytes, str, dict[str, str]]:
        """
        Parses raw HTTP request header block and ensures 'Connection: close' is set
        so the backend closes the TCP connection after sending one HTTP response.
        Returns (rewritten_bytes, request_line, headers_dict).
        """
        text = raw_header_bytes.decode("iso-8859-1", errors="replace")
        lines = text.split("\r\n")
        request_line = lines[0] if lines else "GET / HTTP/1.1"

        output_lines = [request_line]
        headers_dict: dict[str, str] = {}
        connection_seen = False

        for line in lines[1:]:
            if not line:
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                k_clean = k.strip()
                v_clean = v.strip()
                headers_dict[k_clean.lower()] = v_clean
                if k_clean.lower() == "connection":
                    output_lines.append("Connection: close")
                    connection_seen = True
                else:
                    output_lines.append(line)
            else:
                output_lines.append(line)

        if not connection_seen:
            output_lines.append("Connection: close")

        # Terminate HTTP header block with CRLF CRLF
        rewritten = ("\r\n".join(output_lines) + "\r\n\r\n").encode("iso-8859-1")
        return rewritten, request_line, headers_dict

    async def _send_502_bad_gateway(
        self, client_writer: asyncio.StreamWriter, reason: str
    ) -> None:
        body = f"502 Bad Gateway ({reason})\n".encode("utf-8")
        response = (
            b"HTTP/1.1 502 Bad Gateway\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Connection: close\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            + body
        )
        try:
            client_writer.write(response)
            await client_writer.drain()
        except OSError:
            pass

    async def handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Handles a single client TCP connection carrying one HTTP GET request."""
        peername = client_writer.get_extra_info("peername")
        client_ip = peername[0] if peername else "0.0.0.0"
        client_port = peername[1] if peername else 0

        sock = client_writer.get_extra_info("socket")
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

        try:
            # 1. Read HTTP request headers up to \r\n\r\n
            try:
                raw_headers = await asyncio.wait_for(
                    client_reader.readuntil(b"\r\n\r\n"),
                    timeout=self.proxy_read_timeout,
                )
            except (
                asyncio.IncompleteReadError,
                asyncio.LimitOverrunError,
                asyncio.TimeoutError,
                ConnectionError,
                OSError,
            ):
                return

            rewritten_req, request_line, headers_dict = (
                self._rewrite_request_connection_close(raw_headers)
            )

            # Optional header override strictly for single-NIC local unit testing
            effective_ip = client_ip
            if self.allow_sim_ip_header and "x-sim-client-ip" in headers_dict:
                effective_ip = headers_dict["x-sim-client-ip"]

            # 2. Pick a healthy backend according to policy
            backend = self.select_backend(effective_ip)
            if backend is None:
                self.log_event(
                    "ERROR",
                    f"502 Bad Gateway for {effective_ip}:{client_port} ({request_line}) - No healthy backends UP",
                )
                await self._send_502_bad_gateway(client_writer, "No healthy backend")
                return

            # 3. Increment active connections before connecting (for Least-Connections accuracy)
            backend.active_connections += 1
            backend_writer: asyncio.StreamWriter | None = None
            try:
                try:
                    backend_reader, backend_writer = await asyncio.wait_for(
                        asyncio.open_connection(backend.host, backend.port),
                        timeout=self.proxy_connect_timeout,
                    )
                except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
                    backend.total_failed_requests += 1
                    self.log_event(
                        "PROXY_FAIL",
                        f"502 Bad Gateway client={effective_ip}:{client_port} -> "
                        f"backend={backend.name}({backend.address_str}) connect failed: {type(exc).__name__}",
                    )
                    await self._send_502_bad_gateway(
                        client_writer, f"Backend {backend.name} connect error"
                    )
                    return

                b_sock = backend_writer.get_extra_info("socket")
                if b_sock is not None:
                    try:
                        b_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    except OSError:
                        pass

                # Forward request headers with Connection: close
                backend_writer.write(rewritten_req)
                await backend_writer.drain()

                # Copy backend response back to client until backend closes connection
                bytes_copied = 0
                while True:
                    chunk = await asyncio.wait_for(
                        backend_reader.read(16384),
                        timeout=self.proxy_read_timeout,
                    )
                    if not chunk:
                        break
                    client_writer.write(chunk)
                    await client_writer.drain()
                    bytes_copied += len(chunk)

                if bytes_copied == 0:
                    backend.total_failed_requests += 1
                    self.log_event(
                        "PROXY_FAIL",
                        f"502 Bad Gateway client={effective_ip}:{client_port} -> "
                        f"backend={backend.name}({backend.address_str}) empty response",
                    )
                    await self._send_502_bad_gateway(
                        client_writer, f"Backend {backend.name} empty response"
                    )
                    return

                backend.total_proxied_requests += 1
                if self.verbose_requests:
                    self.log_event(
                        "ACCESS",
                        f"client={effective_ip}:{client_port} -> backend={backend.name}({backend.address_str}) "
                        f"policy={self.policy} active_conns={backend.active_connections} req=\"{request_line}\" bytes={bytes_copied}",
                    )

            except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
                backend.total_failed_requests += 1
                self.log_event(
                    "PROXY_FAIL",
                    f"502 Bad Gateway client={effective_ip}:{client_port} -> "
                    f"backend={backend.name}({backend.address_str}) IO error: {type(exc).__name__}",
                )
                await self._send_502_bad_gateway(
                    client_writer, f"Backend {backend.name} read error"
                )
            finally:
                backend.active_connections -= 1
                if backend_writer is not None:
                    try:
                        backend_writer.close()
                        await backend_writer.wait_closed()
                    except OSError:
                        pass
        finally:
            try:
                client_writer.close()
                await client_writer.wait_closed()
            except OSError:
                pass

    async def probe_single_backend(self, backend: BackendServer) -> bool:
        """
        Sends 'GET /health HTTP/1.1' to a backend with a 500 ms timeout.
        Returns True if the backend replies with HTTP 200, False otherwise.
        """
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(backend.host, backend.port),
                timeout=self.health_timeout,
            )
            probe_req = (
                f"GET /health HTTP/1.1\r\n"
                f"Host: {backend.host}:{backend.port}\r\n"
                f"User-Agent: SLB-HTD-HealthChecker/1.0\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("ascii")
            writer.write(probe_req)
            await asyncio.wait_for(writer.drain(), timeout=self.health_timeout)

            status_line_bytes = await asyncio.wait_for(
                reader.readline(), timeout=self.health_timeout
            )
            if not status_line_bytes:
                return False
            status_line = status_line_bytes.decode("iso-8859-1", errors="replace").strip()
            parts = status_line.split(" ", 2)
            return len(parts) >= 2 and parts[1] == "200"
        except (asyncio.TimeoutError, ConnectionError, OSError):
            return False
        finally:
            if writer is not None:
                try:
                    writer.close()
                    await writer.wait_closed()
                except OSError:
                    pass

    def _update_backend_health(self, backend: BackendServer, probe_ok: bool) -> None:
        """Applies the fall=3 / rise=2 state machine for a backend."""
        if probe_ok:
            backend.consecutive_failures = 0
            if not backend.is_up:
                backend.consecutive_successes += 1
                self.log_event(
                    "HEALTH_PROBE",
                    f"Backend {backend.name} ({backend.address_str}) probe OK "
                    f"({backend.consecutive_successes}/{self.rise_threshold} to mark UP)",
                )
                if backend.consecutive_successes >= self.rise_threshold:
                    backend.is_up = True
                    backend.consecutive_successes = 0
                    self.log_event(
                        "HEALTH_STATE",
                        f"Backend {backend.name} ({backend.address_str}) marked UP "
                        f"after {self.rise_threshold} consecutive successful probes",
                    )
        else:
            backend.consecutive_successes = 0
            if backend.is_up:
                backend.consecutive_failures += 1
                self.log_event(
                    "HEALTH_PROBE",
                    f"Backend {backend.name} ({backend.address_str}) probe FAILED "
                    f"({backend.consecutive_failures}/{self.fall_threshold} to mark DOWN)",
                )
                if backend.consecutive_failures >= self.fall_threshold:
                    backend.is_up = False
                    backend.consecutive_failures = 0
                    self.log_event(
                        "HEALTH_STATE",
                        f"Backend {backend.name} ({backend.address_str}) marked DOWN "
                        f"after {self.fall_threshold} consecutive failed probes",
                    )

    async def health_check_loop(self) -> None:
        """Background task that probes all backends once every health_interval seconds."""
        self.log_event(
            "HEALTH_INIT",
            f"Starting active health checker: interval={self.health_interval}s, "
            f"timeout={self.health_timeout * 1000:.0f}ms, fall={self.fall_threshold}, rise={self.rise_threshold}",
        )
        while True:
            cycle_start = time.monotonic()
            results = await asyncio.gather(
                *(self.probe_single_backend(b) for b in self.backends),
                return_exceptions=True,
            )
            for backend, res in zip(self.backends, results):
                probe_ok = bool(res is True)
                self._update_backend_health(backend, probe_ok)

            elapsed = time.monotonic() - cycle_start
            sleep_time = max(0.0, self.health_interval - elapsed)
            await asyncio.sleep(sleep_time)


def parse_backends(backends_arg: str) -> list[BackendServer]:
    """
    Parses backend specification string like:
    'B1=172.20.0.11:8001,B2=172.20.0.12:8002,B3=172.20.0.13:8003'
    """
    servers: list[BackendServer] = []
    for idx, item in enumerate(backends_arg.split(",")):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            name, host_port = item.split("=", 1)
        else:
            name = f"B{idx + 1}"
            host_port = item
        host, port_str = host_port.rsplit(":", 1)
        servers.append(
            BackendServer(
                name=name.strip(),
                host=host.strip(),
                port=int(port_str.strip()),
                order_index=idx,
            )
        )
    return servers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SLB-HTD: Asyncio HTTP/1.1 Reverse Proxy Load Balancer with Active Health Checks"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="192.168.20.10",
        help="Listen IPv4 address (default: 192.168.20.10).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Listen TCP port (default: 8080).",
    )
    parser.add_argument(
        "--policy",
        type=str,
        choices=["rr", "lc", "iphash"],
        default="rr",
        help="Scheduling policy: rr (Round-Robin), lc (Least-Connections), iphash (IP-Hash).",
    )
    parser.add_argument(
        "--backends",
        type=str,
        default="B1=172.20.0.11:8001,B2=172.20.0.12:8002,B3=172.20.0.13:8003",
        help="Comma-separated list of NAME=IP:PORT backend specifications.",
    )
    parser.add_argument(
        "--health-interval",
        type=float,
        default=1.0,
        help="Health check interval in seconds (default: 1.0).",
    )
    parser.add_argument(
        "--health-timeout",
        type=float,
        default=0.5,
        help="Health check timeout in seconds (default: 0.5 = 500ms).",
    )
    parser.add_argument(
        "--fall",
        type=int,
        default=3,
        help="Consecutive failed probes to mark backend DOWN (default: 3).",
    )
    parser.add_argument(
        "--rise",
        type=int,
        default=2,
        help="Consecutive successful probes to mark backend UP (default: 2).",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Path to write SLB event/state logs.",
    )
    parser.add_argument(
        "--hash-seed",
        type=int,
        default=0,
        help="CRC32 initial seed for IP-Hash (default: 0; seed=3 maps .50/.51/.52 to 3 distinct backends).",
    )
    parser.add_argument(
        "--allow-sim-ip-header",
        action="store_true",
        help="Allow X-Sim-Client-IP header to override peer IP during single-IP local testing.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log every individual proxied request in addition to health/error events.",
    )
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    backends = parse_backends(args.backends)
    lb = SoftwareLoadBalancer(
        listen_host=args.host,
        listen_port=args.port,
        backends=backends,
        policy=args.policy,
        health_interval=args.health_interval,
        health_timeout=args.health_timeout,
        fall_threshold=args.fall,
        rise_threshold=args.rise,
        log_file=args.log_file,
        hash_seed=args.hash_seed,
        allow_sim_ip_header=args.allow_sim_ip_header,
        verbose_requests=args.verbose,
    )

    server = await asyncio.start_server(
        lb.handle_client,
        host=args.host,
        port=args.port,
        reuse_address=True,
        backlog=2048,
    )

    lb.log_event(
        "STARTUP",
        f"SLB-HTD listening on {args.host}:{args.port} | policy={args.policy.upper()} | "
        f"backends={[f'{b.name}({b.address_str})' for b in backends]}",
    )

    health_task = asyncio.create_task(lb.health_check_loop())
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        await stop_event.wait()
    finally:
        lb.log_event("SHUTDOWN", "Stopping SLB-HTD server and health checker...")
        health_task.cancel()
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass