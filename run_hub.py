from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import tempfile
import threading
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import quote

import uvicorn

from services.network_access import member_access_urls


DEFAULT_HOST = "0.0.0.0"
LOCAL_HOST = "127.0.0.1"
DEFAULT_PORT = 5050


def preconfigure_runtime() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--portable", action="store_true", help="Keep hub data beside the executable.")
    parser.add_argument("--data-dir", default="", help="Use an explicit hub data directory.")
    parser.add_argument("--open-data", action="store_true", help="Open the active hub data directory.")
    parser.add_argument("--self-check", action="store_true", help="Initialize the hub and exit.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Preferred listen port.")
    parser.add_argument(
        "--host",
        default="",
        help="Advanced IPv4 bind address; default 0.0.0.0 listens on wired, wireless and VPN adapters.",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Bind only to this computer instead of the local network.",
    )
    args = parser.parse_args()
    executable_root = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent
    )
    if args.portable:
        os.environ["RESEARCH_OS_DATA_DIR"] = str(executable_root / "hub_data")
    elif args.data_dir:
        os.environ["RESEARCH_OS_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())
    elif args.self_check:
        os.environ["RESEARCH_OS_DATA_DIR"] = tempfile.mkdtemp(prefix="research-hub-self-check-")
    elif getattr(sys, "frozen", False) and not os.environ.get("RESEARCH_OS_DATA_DIR", "").strip():
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            root = Path(local_app_data).expanduser().resolve()
        else:
            root = Path.home() / "AppData" / "Local"
        os.environ["RESEARCH_OS_DATA_DIR"] = str(root / "ResearchCultivationOSHub")
    return args


def is_hub(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{LOCAL_HOST}:{port}/health", timeout=1.0) as response:
            payload = json.load(response)
        return response.status == 200 and payload.get("database") == "ready"
    except Exception:
        return False


def port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def choose_port(host: str, preferred: int) -> tuple[int, bool]:
    preferred = max(1024, min(int(preferred or DEFAULT_PORT), 65535))
    for port in range(preferred, min(preferred + 20, 65536)):
        if is_hub(port):
            return port, True
        if port_available(host, port):
            return port, False
    raise RuntimeError(f"No free hub port was found between {preferred} and {preferred + 19}.")


def lan_ip() -> str:
    rows = member_access_urls(DEFAULT_PORT)
    return str(rows[0]["address"]) if rows else "this-computer-LAN-IP"


def open_browser(port: int) -> None:
    webbrowser.open_new(f"http://{LOCAL_HOST}:{port}")


def packaged_self_check() -> None:
    from fastapi.testclient import TestClient

    from hub.app import app
    from hub.db import HUB_ADMIN_PATH, connect_hub, get_hub_setting, init_hub_db
    from core.version import APP_VERSION

    init_hub_db()
    with TestClient(app) as client:
        for path in (
            "/health",
            "/.well-known/research-cultivation-os",
            "/api/v1/ping",
            "/register",
        ):
            response = client.get(path)
            if response.status_code != 200:
                raise SystemExit(
                    f"Packaged hub self-check failed: {path} returned HTTP {response.status_code}."
                )
        # The credential file is intentionally destroyed after the first
        # password change. Existing hubs therefore have no readable bootstrap
        # password; their self-check must still pass the public/auth boundaries.
        if HUB_ADMIN_PATH.exists():
            login_page = client.get("/")
            csrf_match = re.search(r'name="_csrf" value="([^"]+)"', login_page.text)
            password_match = re.search(
                r"密码:\s*(.+)",
                HUB_ADMIN_PATH.read_text(encoding="utf-8"),
            )
            if not csrf_match or not password_match:
                raise SystemExit(
                    "Packaged hub self-check failed: admin login bootstrap is incomplete."
                )
            login_response = client.post(
                "/login",
                data={
                    "username": "admin",
                    "password": password_match.group(1).strip(),
                    "_csrf": csrf_match.group(1),
                },
                follow_redirects=False,
            )
            if login_response.status_code != 303:
                raise SystemExit(
                    "Packaged hub self-check failed: administrator login was rejected."
                )
            admin_page = client.get("/admin")
            if admin_page.status_code != 200 or "管理控制台" not in admin_page.text:
                raise SystemExit(
                    "Packaged hub self-check failed: administrator console could not render."
                )
        else:
            guest_admin = client.get("/admin", follow_redirects=False)
            if guest_admin.status_code not in {302, 303, 401, 403}:
                raise SystemExit(
                    "Packaged hub self-check failed: guest admin page was not protected."
                )
    with connect_hub() as conn:
        admins = int(
            conn.execute("SELECT COUNT(*) n FROM accounts WHERE role='admin'").fetchone()["n"]
        )
        if admins != 1:
            raise SystemExit("Packaged hub self-check failed: initial admin lifecycle is invalid.")
        if get_hub_setting(conn, "version") != APP_VERSION:
            raise SystemExit("Packaged hub self-check failed: hub database version is stale.")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise SystemExit("Packaged hub self-check failed: foreign keys are inconsistent.")
        legacy_fks = []
        for table in (
            "hub_profiles", "hub_asset_transactions", "hub_inventory", "hub_sync_events",
            "hub_invites", "hub_releases", "hub_resource_cards", "hub_audit_log",
        ):
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                continue
            if any(
                str(row[2]) == "hub_users"
                for row in conn.execute(f'PRAGMA foreign_key_list("{table}")')
            ):
                legacy_fks.append(table)
        if legacy_fks:
            raise SystemExit(
                "Packaged hub self-check failed: legacy identity foreign keys remain: "
                + ", ".join(legacy_fks)
            )
    print(f"Research System Hub {APP_VERSION} self-check PASS")


if __name__ == "__main__":
    args = preconfigure_runtime()
    from core.runtime_paths import DATA_ROOT, ensure_data_layout

    ensure_data_layout()
    if args.open_data:
        try:
            os.startfile(DATA_ROOT)  # type: ignore[attr-defined]
        except AttributeError:
            webbrowser.open(DATA_ROOT.as_uri())
        raise SystemExit(0)
    if args.self_check:
        packaged_self_check()
        raise SystemExit(0)

    from hub.db import HUB_ADMIN_PATH, connect_hub, init_hub_db, set_hub_setting
    from core.version import APP_VERSION

    init_hub_db()
    host = LOCAL_HOST if args.local_only else (args.host.strip() or os.environ.get("HUB_BIND_HOST", "").strip() or DEFAULT_HOST)
    port, already_running = choose_port(host, args.port)
    if already_running:
        print(f"Research Hub is already running: http://{LOCAL_HOST}:{port}")
        open_browser(port)
        raise SystemExit(0)

    member_urls = [] if args.local_only else member_access_urls(port, bind_host=host)
    preferred_url = str(member_urls[0]["url"]) if member_urls else ""
    with connect_hub() as conn:
        set_hub_setting(conn, "runtime_base_url", preferred_url)
        conn.commit()

    print(f"\nResearch System Hub v{APP_VERSION}")
    print(f"Admin page on this computer: http://{LOCAL_HOST}:{port}")
    if member_urls:
        for item in member_urls:
            print(f"Member address ({item['label']}): {item['url']}")
        print(f"Android one-tap pairing link: researchos://connect?hub={quote(preferred_url, safe='')}")
        print("Allow the Hub TCP port on trusted LAN/VPN profiles in Windows Firewall.")
        print("VPN members must all join the same virtual network and be allowed to reach the host.")
    elif not args.local_only:
        print("No active LAN/VPN IPv4 address was detected; configure a fixed VPN/public base URL in the admin console.")
    print(f"Hub data: {DATA_ROOT}")
    if HUB_ADMIN_PATH.exists():
        print(f"First-run admin credentials: {HUB_ADMIN_PATH}")
    print("Keep this window open. Press Ctrl+C or close it to stop the hub.\n")
    threading.Timer(1.0, open_browser, args=(port,)).start()
    uvicorn.run(
        "hub.app:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
