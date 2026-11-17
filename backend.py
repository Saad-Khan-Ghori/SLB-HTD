#!/usr/bin/env python3
"""
CS3001 Computer Networks (Fall 2026) - FAST School of Computing, Karachi
Project: Software Load Balancer with Health Checks and Dynamic Traffic Distribution (SLB-HTD)
File: src/backend.py
Primary Author: Muhammad Akbar Younsi (24K-0947)

Description:
    Threaded HTTP/1.1 backend server built with the Python standard library.
    Each backend instance represents one server in the backend pool (172.20.0.0/24):
      - Backend 1 (ns-b1): 172.20.0.11:8001, artificial delay = 0 ms
      - Backend 2 (ns-b2): 172.20.0.12:8002, artificial delay = 50 ms
      - Backend 3 (ns-b3): 172.20.0.13:8003, artificial delay = 150 ms

    Endpoints:
      - GET /health : Returns HTTP 200 OK immediately for active health probes.
      - GET /...    : Sleeps for the configured artificial delay, then returns HTTP 200 OK
                      with the backend's name in the response body and Connection: close.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import signal
import socket
import sys
import threading
import time


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with SO_REUSEADDR and enlarged listen backlog for concurrency tests."""

    allow_reuse_address = True
    request_queue_size = 1024

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        super().server_bind()


class BackendRequestHandler(BaseHTTPRequestHandler):
    """Handles health probes (/health) and proxied client GET requests."""

    protocol_version = "HTTP/1.1"

    # Class-level attributes configured by main()
    backend_name: str = "B1"
    artificial_delay_ms: int = 0
    log_file_path: str | None = None
    log_lock = threading.Lock()
    request_counter: int = 0

    def do_GET(self) -> None:
        recv_epoch = time.time()
        path = self.path.split("?", 1)[0]

        if path == "/health":
            body_text = f"HEALTHY {self.backend_name}\n"
            body_bytes = body_text.encode("utf-8")
            try:
                self.send_response(200, "OK")
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body_bytes)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            self.close_connection = True
            self._write_access_log(
                req_type="HEALTH",
                status=200,
                recv_epoch=recv_epoch,
                done_epoch=time.time(),
            )
            return

        # Standard application request: apply artificial backend processing delay
        if self.artificial_delay_ms > 0:
            time.sleep(self.artificial_delay_ms / 1000.0)

        with self.log_lock:
            BackendRequestHandler.request_counter += 1
            req_id = BackendRequestHandler.request_counter

        done_epoch = time.time()
        host_addr, host_port = self.server.server_address[:2]
        body_text = (
            f"Backend: {self.backend_name} ({host_addr}:{host_port}) | "
            f"Delay: {self.artificial_delay_ms}ms | "
            f"ReqID: {req_id} | "
            f"Path: {self.path}\n"
        )
        body_bytes = body_text.encode("utf-8")

        try:
            self.send_response(200, "OK")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("X-Backend-Name", self.backend_name)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body_bytes)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

        self.close_connection = True
        self._write_access_log(
            req_type="PROXY",
            status=200,
            recv_epoch=recv_epoch,
            done_epoch=done_epoch,
        )

    def _write_access_log(
        self,
        req_type: str,
        status: int,
        recv_epoch: float,
        done_epoch: float,
    ) -> None:
        ts_human = datetime.fromtimestamp(done_epoch).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        client_ip, client_port = self.client_address[:2]
        duration_ms = (done_epoch - recv_epoch) * 1000.0
        line = (
            f"[{ts_human}] [EPOCH={done_epoch:.6f}] [RECV_EPOCH={recv_epoch:.6f}] "
            f"[BACKEND={self.backend_name}] [TYPE={req_type}] "
            f"client={client_ip}:{client_port} method={self.command} path={self.path} "
            f"status={status} duration_ms={duration_ms:.2f}\n"
        )
        with self.log_lock:
            sys.stdout.write(line)
            sys.stdout.flush()
            if self.log_file_path:
                with open(self.log_file_path, "a", encoding="utf-8") as f:
                    f.write(line)

    def log_message(self, format: str, *args: object) -> None:
        # Suppress default stderr logging in favor of structured _write_access_log
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SLB-HTD Threaded HTTP Backend Server with configurable artificial delay."
    )
    parser.add_argument(
        "--name",
        type=str,
        required=True,
        help="Backend identifier shown in response body and logs (e.g., B1, B2, B3).",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="IPv4 address to bind (e.g., 172.20.0.11).",
    )
    parser.add_argument(
        "--port",
        type=int,
        required=True,
        help="TCP port to listen on (e.g., 8001, 8002, 8003).",
    )
    parser.add_argument(
        "--delay-ms",
        type=int,
        default=0,
        help="Artificial response delay in milliseconds for non-/health requests (0, 50, 150).",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Optional path to append structured access log lines.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    BackendRequestHandler.backend_name = args.name
    BackendRequestHandler.artificial_delay_ms = args.delay_ms
    BackendRequestHandler.log_file_path = args.log_file

    if args.log_file:
        os.makedirs(os.path.dirname(os.path.abspath(args.log_file)), exist_ok=True)

    server = ReusableThreadingHTTPServer((args.host, args.port), BackendRequestHandler)

    def handle_signal(signum: int, _frame: object) -> None:
        now = time.time()
        ts = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        msg = f"[{ts}] [EPOCH={now:.6f}] [BACKEND={args.name}] Shutting down on signal {signum}\n"
        sys.stdout.write(msg)
        sys.stdout.flush()
        if args.log_file:
            with open(args.log_file, "a", encoding="utf-8") as f:
                f.write(msg)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    now = time.time()
    ts = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    start_msg = (
        f"[{ts}] [EPOCH={now:.6f}] [BACKEND={args.name}] "
        f"Listening on http://{args.host}:{args.port} (delay={args.delay_ms}ms)\n"
    )
    sys.stdout.write(start_msg)
    sys.stdout.flush()
    if args.log_file:
        with open(args.log_file, "a", encoding="utf-8") as f:
            f.write(start_msg)

    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()