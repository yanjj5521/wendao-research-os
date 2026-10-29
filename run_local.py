from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import urllib.request
import webbrowser
from pathlib import Path

from launchers.server import run_server

HOST = "127.0.0.1"
START_PORT = 5000


def preconfigure_runtime() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--mode", choices=("personal", "team"), default="personal")
    parser.add_argument("--host", default="")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--pet", action="store_true", help="Run the detachable, animated research companion.")
    parser.add_argument("--no-pet", action="store_true", help="Do not start the desktop companion with the main window.")
    parser.add_argument("--portable", action="store_true", help="Keep data beside the executable.")
    parser.add_argument("--data-dir", default="", help="Use an explicit data directory.")
    parser.add_argument("--open-data", action="store_true", help="Open the active data directory.")
    parser.add_argument("--serve", action="store_true", help="Run as a headless cloud service: bind 0.0.0.0:PORT and require the access-code gate.")
    parser.add_argument("--self-check", action="store_true", help="Initialize the app and exit.")
    args = parser.parse_args()
    if args.portable:
        executable_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        os.environ["RESEARCH_OS_PORTABLE"] = "1"
        os.environ["RESEARCH_OS_DATA_DIR"] = str(executable_root / "user_data")
    elif args.data_dir:
        os.environ["RESEARCH_OS_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())
    elif args.self_check:
        # A manual self-check must never add test plans to the user's real data.
        os.environ["RESEARCH_OS_DATA_DIR"] = tempfile.mkdtemp(prefix="research-os-self-check-")
    return args


def data_root_identity() -> str:
    from core.runtime_paths import DATA_ROOT

    return hashlib.sha256(str(DATA_ROOT).casefold().encode("utf-8")).hexdigest()


def is_research_os(port: int, expected_data_identity: str, expected_version: str) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://{HOST}:{port}/api/app-identity", timeout=1.0
        ) as response:
            payload = json.load(response)
        return (
            response.status == 200
            and payload.get("application") == "wendao-research"
            and payload.get("data_root_hash") == expected_data_identity
            and payload.get("app_version") == expected_version
        )
    except Exception:
        return False


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((HOST, port))
            return True
        except OSError:
            return False


def choose_port(expected_data_identity: str, expected_version: str) -> tuple[int, bool]:
    for port in range(START_PORT, START_PORT + 20):
        if is_research_os(port, expected_data_identity, expected_version):
            return port, True
        if port_available(port):
            return port, False
    raise RuntimeError("No free local port was found between 5000 and 5019.")


def safe_print(message: str) -> None:
    stream = sys.stdout
    if stream is None:
        return
    try:
        print(message, file=stream)
    except UnicodeEncodeError:
        encoding = stream.encoding or "ascii"
        escaped = message.encode(encoding, errors="backslashreplace").decode(encoding)
        print(escaped, file=stream)


if __name__ == "__main__":
    args = preconfigure_runtime()
    from core.runtime_paths import DATA_ROOT, ensure_data_layout

    ensure_data_layout()
    if args.pet:
        from desktop_pet import run_pet

        run_pet()
        raise SystemExit(0)
    if args.open_data:
        try:
            os.startfile(DATA_ROOT)  # type: ignore[attr-defined]
        except AttributeError:
            webbrowser.open(DATA_ROOT.as_uri())
        raise SystemExit(0)

    if args.mode == "team":
        if args.self_check:
            from run_hub import packaged_self_check

            packaged_self_check()
            raise SystemExit(0)
        from launchers.team import run_team

        raise SystemExit(
            run_team(
                data_root=DATA_ROOT,
                preferred_port=args.port or 5050,
                host=args.host or "0.0.0.0",
                no_browser=args.no_browser,
                report=safe_print,
            )
        )

    if args.serve:
        # 显式云服务模式：只在 --serve 时读取 PORT 并绑定 0.0.0.0，
        # 本地桌面启动不受机器上无关的 PORT 环境变量影响。
        from web.gate import gate_enabled

        if not gate_enabled():
            raise SystemExit(
                "云端模式需要设置 RESEARCH_OS_ACCESS_CODE 访问口令，"
                "否则不应把服务暴露到公网。"
            )
        from core.version import APP_VERSION
        from web.app import app as web_app

        cloud_port = os.getenv("PORT", "").strip() or (str(args.port) if args.port else "")
        if not cloud_port:
            raise SystemExit("云端模式需要 PORT 环境变量（或 --port 参数）。")
        from launchers.server import run_server

        host = args.host or "0.0.0.0"
        safe_print(f"问道科研 {APP_VERSION} serving on {host}:{cloud_port} (cloud mode)")
        run_server(
            web_app,
            host=host,
            port=int(cloud_port),
            data_root=DATA_ROOT,
            proxy_headers=True,
        )
        raise SystemExit(0)

    from core.packaged_self_check import run_packaged_self_check
    from core.version import APP_VERSION
    from web.app import app as web_app

    if args.self_check:
        run_packaged_self_check(web_app)
        raise SystemExit(0)

    expected_data_identity = data_root_identity()
    expected_version = APP_VERSION
    port, already_running = (
        (args.port, False)
        if args.port and port_available(args.port)
        else choose_port(expected_data_identity, expected_version)
    )
    url = f"http://{HOST}:{port}"
    if args.no_browser:
        if already_running:
            safe_print(f"问道科研 is already running: {url}")
            raise SystemExit(0)
        safe_print("\n问道科研")
        safe_print(f"Open: {url}")
        safe_print(f"Data: {DATA_ROOT}")
        safe_print("Close this window or press Ctrl+C to stop the local server.\n")
        run_server(
            web_app,
            host=HOST,
            port=port,
            data_root=DATA_ROOT,
        )
        raise SystemExit(0)

    from desktop_shell import open_desktop_window, start_local_service, wait_until_ready

    pet_process = None
    if not already_running:
        if not args.no_pet:
            pet_command = [sys.executable]
            if not getattr(sys, "frozen", False):
                pet_command.append(str(Path(__file__).resolve()))
            pet_command.append("--pet")
            startup = getattr(subprocess, "STARTUPINFO", lambda: None)()
            if startup is not None:
                startup.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
            try:
                pet_process = subprocess.Popen(
                    pet_command,
                    startupinfo=startup,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError:
                pet_process = None

    server = None
    if already_running:
        safe_print(f"问道科研 desktop window: {url}")
    else:
        server, _thread = start_local_service(web_app, host=HOST, port=port, data_root=DATA_ROOT)
        if not wait_until_ready(
            lambda: is_research_os(port, expected_data_identity, expected_version)
        ):
            server.should_exit = True
            raise SystemExit("问道科研本地服务启动失败，请查看数据目录中的 logs。")
    try:
        open_desktop_window(url)
    finally:
        if server is not None:
            server.should_exit = True
        if pet_process is not None and pet_process.poll() is None:
            pet_process.terminate()

