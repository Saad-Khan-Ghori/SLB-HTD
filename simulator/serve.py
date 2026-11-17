#!/usr/bin/env python3
"""Serve the browser-only simulator on localhost; no root access required."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent
HOST, PORT = "127.0.0.1", 8765


def main() -> None:
    handler = lambda *args, **kwargs: SimpleHTTPRequestHandler(*args, directory=str(DIRECTORY), **kwargs)
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(f"Open http://{HOST}:{PORT}/ in your browser (Ctrl+C to stop).", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
