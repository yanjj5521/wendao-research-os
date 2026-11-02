"""Lifecycle for the optional small-team host, started from the desktop app.

The central database remains separate from personal research data, but the
administrator no longer needs a command script or a second visible program.
"""
from __future__ import annotations

import os
import socket
import urllib.request
from threading import Lock, Thread
from typing import Any

from desktop_shell import start_local_service, wait_until_ready
from hub.app import app as hub_app
from hub.db import connect_hub, get_hub_setting, init_hub_db, set_hub_setting
from services.network_access import member_access_urls, normalize_base_url, preferred_member_url

_lock = Lock()
_server: Any | None = None
_thread: Thread | None = None
_port = 5050


def status() -> dict[str, object]:
    running = bool(_server and _thread and _thread.is_alive() and not _server.should_exit)
    local_url = f"http://127.0.0.1:{_port}"
    access_urls = member_access_urls(_port) if running else []
    preferred = preferred_member_url(_port) if running else local_url
    custom_url = ""
    runtime_url = ""
    if running:
        with connect_hub() as conn:
            raw_custom_url = get_hub_setting(conn, "public_base_url", "") or os.environ.get("HUB_PUBLIC_URL", "").strip()
            custom_url, _ = normalize_base_url(raw_custom_url)
            runtime_url = get_hub_setting(conn, "runtime_base_url", "")
    discovered_url = str(access_urls[0]["url"]) if access_urls else ""
    effective_url = custom_url or discovered_url or runtime_url or preferred
    return {
        "running": running,
        "port": _port,
        "local_url": local_url,
        "lan_url": preferred if running else "",
        "access_urls": access_urls,
        "preferred_url": effective_url,
        "custom_url": custom_url,
        "runtime_url": runtime_url,
    }


def start(data_root, *, port: int = 5050) -> dict[str, object]:
    global _server, _thread, _port
    with _lock:
        current = status()
        if current["running"]:
            return current
        init_hub_db()
        _port = max(1024, min(int(port or 5050), 65535))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("0.0.0.0", _port))
            except OSError as exc:
                raise OSError(f"端口 {_port} 已被占用") from exc
        _server, _thread = start_local_service(
            hub_app, host="0.0.0.0", port=_port, data_root=data_root, proxy_headers=True
        )
        ready = wait_until_ready(
            lambda: _local_ready(_port),
            timeout=12.0,
        )
        if not ready:
            _server.should_exit = True
            if _thread.is_alive():
                _thread.join(timeout=5)
            _server = None
            _thread = None
            raise RuntimeError("同行会中心未能开始监听，请检查防火墙、端口和日志。")
        preferred = preferred_member_url(_port)
        with connect_hub() as conn:
            set_hub_setting(conn, "runtime_base_url", preferred)
            conn.commit()
        return status()


def _local_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.0) as response:
            return response.status == 200
    except Exception:
        return False


def stop() -> None:
    global _server, _thread
    with _lock:
        server = _server
        thread = _thread
        if server is not None:
            server.should_exit = True
        _server = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        _thread = None
        if thread is not None:
            try:
                with connect_hub() as conn:
                    set_hub_setting(conn, "runtime_base_url", "")
                    conn.commit()
            except Exception:
                pass

