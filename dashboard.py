#!/usr/bin/env python3
"""
Single-entry launcher for the Greeks dashboard.

Usage: python dashboard.py

What it does:
- Ensures required Python packages (ib_async, scipy, numpy) are installed
- Starts the aggregator in latest-only mode (no growing timeseries)
- Serves the dashboard HTML and JSON via a local HTTP server
- Opens your browser to the dashboard

Environment knobs (optional):
- IB_HOST, IB_PORT, IB_CLIENT_ID, IB_ACCOUNTS
- GREEKS_INTERVAL (default 2), GREEKS_HTTP_PORT (try preferred port)
"""
from __future__ import annotations

import os
import sys
import subprocess
import socket
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote


BASE_DIR = Path(__file__).resolve().parent
AGG_DIR = BASE_DIR / "greeks_aggregate"
AGG_PY = AGG_DIR / "greeks_aggregate.py"
HTML_FILE = AGG_DIR / "greeks_dashboard.html"
LATEST_FILE = AGG_DIR / "latest_data.jsonl"


def ensure_packages() -> None:
    try:
        import importlib
        for mod in ("ib_async", "scipy", "numpy"):
            importlib.import_module(mod)
    except Exception:
        print("Installing required packages: ib_async, scipy, numpy …", flush=True)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", "ib_async", "scipy", "numpy"]) 


def pick_free_port(preferred: int | None = None) -> int:
    if preferred:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", preferred))
                return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_http_server(directory: Path, preferred_port: int | None = None) -> tuple[ThreadingHTTPServer, int]:
    class CORSHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def end_headers(self):
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-store')
            super().end_headers()

    port = pick_free_port(preferred_port)
    httpd = ThreadingHTTPServer(('127.0.0.1', port), CORSHandler)
    thread = threading.Thread(target=httpd.serve_forever, name='dashboard-http', daemon=True)
    thread.start()
    return httpd, port


def start_aggregator(interval: float = 2.0) -> subprocess.Popen:
    args = [
        sys.executable, str(AGG_PY),
        "--no-timeseries",
        "--latest-file", str(LATEST_FILE),
        "--interval", str(interval),
    ]
    # Pass through optional connection settings from environment
    env = dict(os.environ)
    return subprocess.Popen(args, cwd=str(AGG_DIR), env=env)


def main() -> int:
    if not HTML_FILE.is_file():
        print(f"Missing dashboard HTML at {HTML_FILE}")
        return 2

    try:
        ensure_packages()
    except subprocess.CalledProcessError as e:
        print(f"Dependency installation failed: {e}")
        return 3

    preferred_port = int(os.getenv("GREEKS_HTTP_PORT", "8765") or 8765)
    httpd, port = start_http_server(AGG_DIR, preferred_port)
    print(f"Serving {AGG_DIR} at http://127.0.0.1:{port}/ (CORS enabled)")

    interval = float(os.getenv("GREEKS_INTERVAL", "2") or 2)
    proc = start_aggregator(interval=interval)
    time.sleep(1.0)

    url = f"http://127.0.0.1:{port}/greeks_dashboard.html?file={quote(LATEST_FILE.name)}"
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print(f"Open in browser: {url}")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            ret = proc.poll()
            if ret is not None:
                print(f"Aggregator exited with code {ret}")
                break
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

