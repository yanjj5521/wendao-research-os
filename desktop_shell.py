"""Native desktop shell around the local Research OS service.

The application still uses the same local HTTP service internally, but users
interact with it through an independently resizable Windows window rather than
an external browser tab.
"""
from __future__ import annotations

import time
from threading import Thread

import uvicorn

from launchers.server import configure_file_logging


def start_local_service(
    application: object, *, host: str, port: int, data_root, proxy_headers: bool = False
) -> tuple[uvicorn.Server, Thread]:
    configure_file_logging(data_root)
    config = uvicorn.Config(
        application,
        host=host,
        port=port,
        reload=False,
        log_level="warning",
        log_config=None,
        access_log=False,
        proxy_headers=proxy_headers,
        forwarded_allow_ips="127.0.0.1" if proxy_headers else None,
    )
    server = uvicorn.Server(config)
    thread = Thread(target=server.run, name="wendao-local-service", daemon=True)
    thread.start()
    return server, thread


def wait_until_ready(check, *, timeout: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.12)
    return False


def open_desktop_window(url: str, *, title: str = "问道科研") -> None:
    """Open a normal resizable native app window (not a browser tab)."""
    import webview

    webview.create_window(
        title,
        url,
        width=1360,
        height=900,
        min_size=(960, 640),
        resizable=True,
        confirm_close=True,
        text_select=True,
    )
    webview.start(gui="edgechromium", private_mode=False)
