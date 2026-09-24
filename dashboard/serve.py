from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

from .hub import hub

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _start_static_server(port: int = 8100) -> http.server.ThreadingHTTPServer:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(STATIC_DIR))
    httpd = http.server.ThreadingHTTPServer(("localhost", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


async def start_dashboard(static_port: int = 8100, ws_port: int = 8766):
    """Starts the static page server (background thread) and the live
    WebSocket hub (on the caller's asyncio loop). Returns the page URL."""
    _start_static_server(static_port)
    await hub.start(host="localhost", port=ws_port)
    return f"http://localhost:{static_port}"
