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
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 80))
            return str(sock.getsockname()[0])
        except OSError:
            return "this-computer-LAN-IP"


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
    with connect_hub() as conn:
        admins = int(
            conn.execute("SELECT COUNT(*) n FROM accounts WHERE role='admin'").fetchone()["n"]
        )
        if admins != 1:
            raise SystemExit("Packaged hub self-check failed: initial admin lifecycle is invalid.")
        if get_hub_setting(conn, "version") != APP_VERSION:
            raise SystemExit("Packaged hub self-check failed: hub database version is stale.")
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

    from hub.db import HUB_ADMIN_PATH, init_hub_db
    from core.version import APP_VERSION

    init_hub_db()
    host = LOCAL_HOST if args.local_only else DEFAULT_HOST
    port, already_running = choose_port(host, args.port)
    if already_running:
        print(f"Research Hub is already running: http://{LOCAL_HOST}:{port}")
        open_browser(port)
        raise SystemExit(0)

    print(f"\nResearch System Hub v{APP_VERSION}")
    print(f"Admin page on this computer: http://{LOCAL_HOST}:{port}")
    if not args.local_only:
        member_url = f"http://{lan_ip()}:{port}"
        print(f"LAN address for members: {member_url}")
        print(f"Android client address: {member_url}")
        print(f"Android one-tap pairing link: researchos://connect?hub={quote(member_url, safe='')}")
        print("Windows may ask whether to allow private-network access on the first run.")
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
