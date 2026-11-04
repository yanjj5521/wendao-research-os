"""Built-in Headscale virtual-network lifecycle.

The Go helper in ``native/headnet`` embeds Tailscale's ``tsnet`` in userspace.
It does not install a system VPN adapter. Center mode publishes the local Hub
onto the Headscale tailnet; member mode opens a loopback proxy to the center's
virtual IP. Headscale remains the self-hosted control plane.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from core.database import get_setting, init_db, set_setting
from core.runtime_paths import APP_ROOT, DATA_ROOT, USER_CONFIG_DIR
from hub.db import connect_hub, get_hub_setting, set_hub_setting
from services.network_access import http_host_allowed

_lock = threading.Lock()
_process: subprocess.Popen[str] | None = None
_state: dict[str, Any] = {}
_reader: threading.Thread | None = None
_monitor: threading.Thread | None = None
_ready_queue: queue.Queue[str] = queue.Queue()

HELPER_SUFFIX = ".exe" if os.name == "nt" else ""
HELPER_NAME = "headnet-runtime" + HELPER_SUFFIX
STATE_ROOT = USER_CONFIG_DIR / "headscale"
LOG_PATH = DATA_ROOT / "logs" / "headnet.log"


def helper_path() -> Path:
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)) / "headnet"
    else:
        bundle_root = APP_ROOT / "native" / "headnet"
    candidates = sorted(
        bundle_root.glob(f"headnet-runtime*{HELPER_SUFFIX}"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else bundle_root / HELPER_NAME


def helper_available() -> bool:
    return helper_path().is_file()


def _state_dir(role: str, hostname: str, control_url: str) -> Path:
    digest = hashlib.sha256(control_url.encode("utf-8")).hexdigest()[:10]
    return STATE_ROOT / f"{role}-{hostname}-{digest}"


def _normalize_control_url(value: str) -> tuple[str, str]:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return "", "请填写 Headscale 控制端地址。"
    if len(text) > 300 or any(ord(char) < 32 for char in text):
        return "", "Headscale 地址过长或包含控制字符。"
    try:
        parsed = urlsplit(text)
    except ValueError:
        return "", "Headscale 地址格式不正确。"
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "", "Headscale 地址必须以 http:// 或 https:// 开头。"
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return "", "Headscale 地址不能包含账号、密码、查询参数或锚点。"
    if parsed.scheme == "http" and not http_host_allowed(parsed.hostname):
        return "", "公网 Headscale 必须使用 HTTPS；HTTP 仅用于本机或内部网络。"
    return text, ""


def _normalize_hostname(value: str) -> str:
    hostname = re.sub(r"[^a-z0-9-]+", "-", str(value or "").strip().lower())
    hostname = hostname.strip("-")[:63]
    return hostname or "wendao-node"


def _normalize_peer(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        return "", "成员连接模式需要填写中心节点的虚拟 IP 或主机名。"
    text = text.removeprefix("http://").removeprefix("https://").rstrip("/")
    if "/" in text or any(char.isspace() for char in text):
        return "", "中心节点地址只能是虚拟 IP 或主机名。"
    return text, ""


def _tail(path: Path, limit: int = 3000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:]


def _log_writer(process: subprocess.Popen[str]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(f"\n--- headnet start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            assert process.stderr is not None
            for line in process.stderr:
                log_file.write(line)
                log_file.flush()
    except OSError:
        return


def _stdout_reader(process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        _ready_queue.put(line)


def _cleanup_center_url() -> None:
    try:
        with connect_hub() as conn:
            current = get_hub_setting(conn, "public_base_url", "")
            managed = get_hub_setting(conn, "headnet_public_url", "")
            previous = get_hub_setting(conn, "headnet_previous_public_url", "")
            if managed and current == managed:
                set_hub_setting(conn, "public_base_url", previous)
            set_hub_setting(conn, "headnet_public_url", "")
            set_hub_setting(conn, "headnet_previous_public_url", "")
            conn.commit()
    except Exception:
        return


def _monitor_process(process: subprocess.Popen[str]) -> None:
    process.wait()
    cleanup_needed = False
    with _lock:
        if _process is process:
            cleanup_needed = str(_state.get("role") or "") == "center"
            _state["running"] = False
            _state["exit_code"] = process.returncode
    if cleanup_needed:
        _cleanup_center_url()


def _start_helper(
    *,
    mode: str,
    control_url: str,
    auth_key: str,
    hostname: str,
    state_dir: Path,
    extra_args: list[str],
    timeout: float = 150.0,
) -> dict[str, Any]:
    global _process, _reader, _monitor, _ready_queue
    executable = helper_path()
    if not executable.is_file():
        raise FileNotFoundError(f"内置虚拟网络助手不存在：{executable}")
    state_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HEADNET_AUTH_KEY"] = auth_key
    command = [
        str(executable),
        "--mode", mode,
        "--control-url", control_url,
        "--state-dir", str(state_dir),
        "--hostname", hostname,
        *extra_args,
    ]
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _ready_queue = queue.Queue()
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    _process = process
    _reader = threading.Thread(target=_stdout_reader, args=(process,), daemon=True, name="headnet-stdout")
    _reader.start()
    threading.Thread(target=_log_writer, args=(process,), daemon=True, name="headnet-log").start()
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"虚拟网络助手提前退出（{process.returncode}）。{_tail(LOG_PATH)}")
            try:
                line = _ready_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("state") == "error":
                raise RuntimeError(str(payload.get("error") or "虚拟网络助手启动失败。"))
            if payload.get("state") == "ready":
                _monitor = threading.Thread(target=_monitor_process, args=(process,), daemon=True, name="headnet-monitor")
                _monitor.start()
                return dict(payload)
        raise TimeoutError("连接 Headscale 超时；请检查控制端地址、DERP 和预授权密钥。")
    except Exception:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        _process = None
        raise


def status() -> dict[str, Any]:
    init_db()
    with _lock:
        running = bool(_process and _process.poll() is None and _state.get("running"))
        state = dict(_state)
        if _process and _process.poll() is not None:
            running = False
        configured_control = get_setting("headnet_control_url", "")
        state.update(
            {
                "running": running,
                "configured_control_url": configured_control,
                "auth_key_configured": bool(get_setting("headnet_auth_key", "")),
                "hostname": get_setting("headnet_hostname", "wendao-node"),
                "peer": get_setting("headnet_peer", ""),
                "center_port": int(get_setting("headnet_center_port", "5050") or 5050),
                "peer_port": int(get_setting("headnet_peer_port", "5050") or 5050),
                "helper_available": helper_available(),
                "helper_path": str(helper_path()),
                "log_path": str(LOG_PATH),
            }
        )
        return state


def save_config(*, control_url: str, auth_key: str, hostname: str, peer: str, center_port: int = 5050, peer_port: int = 5050) -> dict[str, Any]:
    init_db()
    normalized_control, error = _normalize_control_url(control_url)
    if error:
        raise ValueError(error)
    normalized_peer, peer_error = _normalize_peer(peer)
    if peer.strip() and peer_error:
        raise ValueError(peer_error)
    set_setting("headnet_control_url", normalized_control)
    set_setting("headnet_hostname", _normalize_hostname(hostname))
    set_setting("headnet_peer", normalized_peer)
    set_setting("headnet_center_port", str(max(1, min(int(center_port), 65535))))
    set_setting("headnet_peer_port", str(max(1, min(int(peer_port), 65535))))
    if auth_key.strip():
        set_setting("headnet_auth_key", auth_key.strip())
    return status()


def auto_connect(role: str = "center") -> dict[str, Any]:
    """一键组网：按身份选模式、按主机名生成节点名，复用已保存的配置。

    目标是让"跨网络直连"从六个字段变成一个按钮：
    中心只需保存过一次控制端与预授权密钥，之后每次点一下即可；
    成员只需要额外知道中心的虚拟 IP。
    """
    init_db()
    current = status()
    if current.get("running"):
        return {**current, "message": "虚拟网络已在运行，无需重复接入。"}
    control_url = get_setting("headnet_control_url", "").strip()
    auth_key = get_setting("headnet_auth_key", "").strip()
    if not control_url or not auth_key:
        raise ValueError("还没有保存 Headscale 控制端地址与预授权密钥，请先填写并保存一次。")
    role = "center" if str(role) == "center" else "member"
    hostname = get_setting("headnet_hostname", "").strip() or (
        f"wendao-{role}-{socket.gethostname()[:10].lower()}"
    )
    if role == "center":
        result = start_center(
            hub_port=int(get_setting("headnet_center_port", "5050") or 5050),
            hostname_override=hostname,
        )
    else:
        peer = get_setting("headnet_peer", "").strip()
        if not peer:
            raise ValueError("作为成员需要中心的虚拟 IP：请向管理员索取后填入「中心虚拟 IP」再试。")
        result = start_member(
            peer_override=peer,
            peer_port=int(get_setting("headnet_peer_port", "5050") or 5050),
            hostname_override=hostname,
        )
    set_setting("headnet_auto", "1")
    set_setting("headnet_auto_role", role)
    return {**result, "message": "已自动接入虚拟网络。"}


def resume_auto() -> dict[str, Any] | None:
    """启动时恢复上次的一键组网；任何失败都静默，绝不阻塞主程序。"""
    try:
        init_db()
        if get_setting("headnet_auto", "0") != "1":
            return None
        role = get_setting("headnet_auto_role", "member") or "member"
        return auto_connect(role)
    except Exception:
        return None


def disable_auto() -> None:
    set_setting("headnet_auto", "0")


def _resolved_config(auth_key_override: str = "") -> tuple[str, str, str, str]:
    init_db()
    control_url = get_setting("headnet_control_url", "")
    normalized_control, error = _normalize_control_url(control_url)
    if error:
        raise ValueError(error)
    auth_key = auth_key_override.strip() or get_setting("headnet_auth_key", "").strip()
    if not auth_key:
        raise ValueError("请填写 Headscale 预授权密钥。")
    hostname = _normalize_hostname(get_setting("headnet_hostname", "wendao-node"))
    peer, peer_error = _normalize_peer(get_setting("headnet_peer", ""))
    return normalized_control, auth_key, hostname, peer if not peer_error else ""


def _wait_health(url: str, timeout: float = 25.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url.rstrip('/')}/health", timeout=3) as response:
                if response.status == 200:
                    return
                last_error = RuntimeError(f"HTTP {response.status}")
        except Exception as exc:
            last_error = exc
        time.sleep(0.35)
    raise RuntimeError(f"虚拟网络已加入，但远程同行会尚未就绪：{last_error}")


def _set_center_url(virtual_url: str) -> None:
    with connect_hub() as conn:
        current = get_hub_setting(conn, "public_base_url", "")
        managed = get_hub_setting(conn, "headnet_public_url", "")
        if current != managed:
            set_hub_setting(conn, "headnet_previous_public_url", current)
        set_hub_setting(conn, "headnet_public_url", virtual_url)
        set_hub_setting(conn, "public_base_url", virtual_url)
        conn.commit()


def start_center(*, hub_port: int, hostname_override: str = "") -> dict[str, Any]:
    control_url, auth_key, hostname, _peer = _resolved_config()
    if hostname_override.strip():
        hostname = _normalize_hostname(hostname_override)
        set_setting("headnet_hostname", hostname)
    stop()
    state_dir = _state_dir("center", hostname, control_url)
    payload = _start_helper(
        mode="serve",
        control_url=control_url,
        auth_key=auth_key,
        hostname=hostname,
        state_dir=state_dir,
        extra_args=["--target", f"127.0.0.1:{int(hub_port)}", "--port", str(int(hub_port))],
    )
    virtual_url = str(payload.get("url") or "")
    _set_center_url(virtual_url)
    set_setting("headnet_peer_port", str(int(hub_port)))
    with _lock:
        _state.update(payload)
        _state.update({"running": True, "role": "center", "virtual_url": virtual_url, "hub_port": int(hub_port)})
    set_setting("headnet_role", "center")
    return status()


def start_member(*, peer_override: str = "", peer_port: int = 5050, hostname_override: str = "") -> dict[str, Any]:
    control_url, auth_key, hostname, configured_peer = _resolved_config()
    peer, error = _normalize_peer(peer_override.strip() or configured_peer)
    if error:
        raise ValueError(error)
    if hostname_override.strip():
        hostname = _normalize_hostname(hostname_override)
        set_setting("headnet_hostname", hostname)
    stop()
    state_dir = _state_dir("member", hostname, control_url)
    payload = _start_helper(
        mode="proxy",
        control_url=control_url,
        auth_key=auth_key,
        hostname=hostname,
        state_dir=state_dir,
        extra_args=[
            "--peer", peer,
            "--peer-port", str(max(1, min(int(peer_port), 65535))),
            "--listen", "127.0.0.1:0",
        ],
    )
    local_url = str(payload.get("url") or "")
    _wait_health(local_url)
    with _lock:
        _state.update(payload)
        _state.update({"running": True, "role": "member", "local_url": local_url, "peer": peer})
    set_setting("headnet_role", "member")
    set_setting("headnet_proxy_url", local_url)
    return status()


def stop() -> dict[str, Any]:
    global _process, _monitor
    init_db()
    with _lock:
        process = _process
        role = str(_state.get("role") or "")
        _state["running"] = False
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    with _lock:
        _process = None
        _monitor = None
        _state.clear()
    if role == "center":
        _cleanup_center_url()
    set_setting("headnet_proxy_url", "")
    return status()
