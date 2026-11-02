from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Callable

from hub.app import app as team_app
from hub.db import HUB_ADMIN_PATH, connect_hub, init_hub_db, set_hub_setting
from launchers.server import run_server
from run_hub import DEFAULT_HOST, LOCAL_HOST, choose_port
from services.network_access import member_access_urls


def run_team(
    *,
    data_root: Path,
    preferred_port: int,
    host: str,
    no_browser: bool,
    report: Callable[[str], None],
) -> int:
    init_hub_db()
    bind_host = host or DEFAULT_HOST
    port, already_running = choose_port(bind_host, preferred_port)
    local_url = f"http://{LOCAL_HOST}:{port}"
    local_bind = bind_host in {LOCAL_HOST, "localhost", "::1"}
    member_urls = [] if local_bind else member_access_urls(port, bind_host=bind_host)
    preferred_url = str(member_urls[0]["url"]) if member_urls else ""
    with connect_hub() as conn:
        set_hub_setting(conn, "runtime_base_url", preferred_url)
        conn.commit()
    if already_running:
        if not no_browser:
            from desktop_shell import open_desktop_window

            open_desktop_window(local_url, title="问道科研 · 同行会中心")
        return 0

    report(f"问道科研团队中心：{local_url}")
    for item in member_urls:
        report(f"成员地址（{item['label']}）：{item['url']}")
    if not member_urls and not local_bind:
        report("未检测到可用的局域网或 VPN IPv4 地址；可在管理控制台设置固定连接地址。")
    if HUB_ADMIN_PATH.exists():
        report(f"管理员凭据：{HUB_ADMIN_PATH}")
    if no_browser:
        run_server(
            team_app,
            host=bind_host,
            port=port,
            data_root=data_root,
            proxy_headers=True,
        )
        return 0

    from desktop_shell import open_desktop_window, start_local_service, wait_until_ready

    server, _thread = start_local_service(
        team_app, host=bind_host, port=port, data_root=data_root, proxy_headers=True
    )
    if not wait_until_ready(
        lambda: _team_ready(local_url)
    ):
        server.should_exit = True
        raise RuntimeError("同行会中心启动失败，请查看数据目录中的 logs。")
    try:
        open_desktop_window(local_url, title="问道科研 · 同行会中心")
    finally:
        server.should_exit = True
    return 0


def _team_ready(local_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{local_url}/health", timeout=1.0) as response:
            return response.status == 200
    except Exception:
        return False
