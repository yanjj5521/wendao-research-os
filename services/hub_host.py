"""Lifecycle for the optional small-team host, started from the desktop app.

The central database remains separate from personal research data, but the
administrator no longer needs a command script or a second visible program.
"""
from __future__ import annotations

import socket
from threading import Lock, Thread
from typing import Any

from desktop_shell import start_local_service
from hub.app import app as hub_app
from hub.db import init_hub_db

_lock = Lock()
_server: Any | None = None
_thread: Thread | None = None
_port = 5050


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return "本机网络地址"


def status() -> dict[str, object]:
    running = bool(_server and _thread and _thread.is_alive() and not _server.should_exit)
    return {
        "running": running,
        "port": _port,
        "local_url": f"http://127.0.0.1:{_port}",
        "lan_url": f"http://{_lan_ip()}:{_port}" if running else "",
    }


def start(data_root, *, port: int = 5050) -> dict[str, object]:
    global _server, _thread, _port
    with _lock:
        current = status()
        if current["running"]:
            return current
        init_hub_db()
        _port = max(1024, min(int(port or 5050), 65535))
        _server, _thread = start_local_service(
            hub_app, host="0.0.0.0", port=_port, data_root=data_root, proxy_headers=True
        )
        return status()


def stop() -> None:
    global _server
    with _lock:
        if _server is not None:
            _server.should_exit = True
        _server = None

