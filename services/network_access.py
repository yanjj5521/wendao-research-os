"""Discover usable member-facing Hub URLs across wired, wireless and VPN adapters.

The server already binds ``0.0.0.0``.  This module does not create a VPN or
punch through NAT; it discovers every active IPv4 adapter so the administrator
can share a route that actually exists, including Tailscale-style CGNAT
addresses and VPN interface names.
"""
from __future__ import annotations

import ipaddress
import json
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_VPN_MARKERS = (
    "vpn", "wireguard", "tailscale", "zerotier", "hamachi", "openvpn",
    "wintun", "securelink", "anyconnect", "fortinet", "globalprotect",
    "sangfor", "easyconnect", "tun", "tap",
)
_WIRELESS_MARKERS = ("wlan", "wi-fi", "wifi", "wireless", "无线")
_WIRED_MARKERS = ("ethernet", "eth", "有线", "以太网")
_VIRTUAL_MARKERS = ("vethernet", "hyper-v", "vmware", "virtualbox", "docker", "wsl", "veth", "br-")
_PRIVATE_SUFFIXES = (
    ".local", ".lan", ".internal", ".home.arpa", ".ts.net", ".tailscale.net", ".vpn",
)
_KIND_ORDER = {"wired": 0, "wireless": 1, "lan": 2, "vpn": 3, "virtual": 4, "public": 5}
_KIND_LABEL = {
    "wired": "有线局域网",
    "wireless": "无线局域网",
    "lan": "局域网",
    "vpn": "VPN / 虚拟局域网",
    "virtual": "虚拟网卡",
    "public": "公网地址",
}

_cache_lock = threading.Lock()
_cache_at = 0.0
_cache_value: tuple[AdapterAddress, ...] = ()
_CACHE_SECONDS = 5.0


@dataclass(frozen=True)
class AdapterAddress:
    address: str
    interface: str = ""
    kind: str = "lan"


def _valid_ipv4(value: object) -> str | None:
    try:
        address = ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None
    if address.version != 4:
        return None
    if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local:
        return None
    return str(address)


def classify_address(address: str, interface: str = "") -> str:
    name = (interface or "").casefold()
    if any(marker in name for marker in _VPN_MARKERS):
        return "vpn"
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        parsed = None
    if parsed and parsed in _CGNAT:
        return "vpn"
    if any(marker in name for marker in _WIRELESS_MARKERS):
        return "wireless"
    if any(marker in name for marker in _VIRTUAL_MARKERS):
        return "virtual"
    if any(marker in name for marker in _WIRED_MARKERS):
        return "wired"
    if parsed and (parsed.is_private or parsed.is_loopback or parsed.is_link_local or parsed in _CGNAT):
        return "lan"
    return "public"


def _windows_adapters() -> list[AdapterAddress]:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return []
    script = (
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();"
        "Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred -ErrorAction Stop | "
        "Where-Object {$_.IPAddress -ne '0.0.0.0'} | "
        "Select-Object IPAddress,InterfaceAlias | ConvertTo-Json -Compress"
    )
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        payload = json.loads(result.stdout.lstrip("\ufeff").strip())
    except json.JSONDecodeError:
        return []
    rows = payload if isinstance(payload, list) else [payload]
    found: list[AdapterAddress] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        address = _valid_ipv4(row.get("IPAddress"))
        if not address:
            continue
        interface = str(row.get("InterfaceAlias") or "").strip()
        found.append(AdapterAddress(address, interface, classify_address(address, interface)))
    return found


def _linux_adapters() -> list[AdapterAddress]:
    ip_tool = shutil.which("ip")
    if not ip_tool:
        return []
    try:
        result = subprocess.run(
            [ip_tool, "-j", "address", "show"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    try:
        payload = json.loads((result.stdout or "[]").lstrip("\ufeff").strip())
    except json.JSONDecodeError:
        return []
    found: list[AdapterAddress] = []
    for interface_row in payload if isinstance(payload, list) else []:
        interface = str(interface_row.get("ifname") or "")
        for info in interface_row.get("addr_info") or []:
            if info.get("family") != "inet":
                continue
            address = _valid_ipv4(info.get("local"))
            if address:
                found.append(AdapterAddress(address, interface, classify_address(address, interface)))
    return found


def _route_address() -> str | None:
    for target in ("1.1.1.1", "192.0.2.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((target, 80))
                address = _valid_ipv4(sock.getsockname()[0])
                if address:
                    return address
        except OSError:
            continue
    return None


def _hostname_addresses() -> list[str]:
    found: list[str] = []
    try:
        for row in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            address = _valid_ipv4(row[4][0])
            if address and address not in found:
                found.append(address)
    except OSError:
        pass
    return found


def _discover_uncached() -> tuple[AdapterAddress, ...]:
    system = platform.system().lower()
    if system == "windows":
        raw = _windows_adapters()
    elif system == "linux":
        raw = _linux_adapters()
    else:
        raw = []

    merged: dict[str, AdapterAddress] = {}
    for item in raw:
        previous = merged.get(item.address)
        if previous is None or (not previous.interface and item.interface):
            merged[item.address] = item
    route = _route_address()
    if route and route not in merged:
        merged[route] = AdapterAddress(route, "系统默认路由", classify_address(route, "系统默认路由"))
    for address in _hostname_addresses():
        if address not in merged:
            merged[address] = AdapterAddress(address, "本机主机名", classify_address(address, "本机主机名"))
    return tuple(
        sorted(
            merged.values(),
            key=lambda item: (_KIND_ORDER.get(item.kind, 9), item.address),
        )
    )


def discover_adapters(*, refresh: bool = False) -> list[AdapterAddress]:
    global _cache_at, _cache_value
    now = time.monotonic()
    with _cache_lock:
        if not refresh and _cache_value and now - _cache_at < _CACHE_SECONDS:
            return list(_cache_value)
        _cache_value = _discover_uncached()
        _cache_at = now
        return list(_cache_value)


def clear_discovery_cache() -> None:
    global _cache_at, _cache_value
    with _cache_lock:
        _cache_at = 0.0
        _cache_value = ()


def build_access_urls(
    adapters: Iterable[AdapterAddress],
    port: int,
    *,
    scheme: str = "http",
) -> list[dict[str, object]]:
    port = max(1, min(int(port), 65535))
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in adapters:
        address = _valid_ipv4(item.address)
        if not address or address in seen:
            continue
        seen.add(address)
        kind = item.kind if item.kind in _KIND_LABEL else classify_address(address, item.interface)
        rows.append(
            {
                "address": address,
                "interface": item.interface,
                "kind": kind,
                "label": _KIND_LABEL[kind],
                "url": f"{scheme}://{address}:{port}",
            }
        )
    rows.sort(key=lambda row: (_KIND_ORDER.get(str(row["kind"]), 9), str(row["address"])))
    for index, row in enumerate(rows):
        row["recommended"] = index == 0
    return rows


def member_access_urls(
    port: int,
    *,
    scheme: str = "http",
    bind_host: str = "",
) -> list[dict[str, object]]:
    adapters = discover_adapters()
    target = (bind_host or "").strip()
    if target and target not in {"0.0.0.0", "::", "*"}:
        adapters = [item for item in adapters if item.address == target]
    return build_access_urls(adapters, port, scheme=scheme)


def preferred_member_url(port: int, *, bind_host: str = "") -> str:
    rows = member_access_urls(port, bind_host=bind_host)
    return str(rows[0]["url"]) if rows else f"http://127.0.0.1:{int(port)}"


def http_host_allowed(host: str) -> bool:
    value = str(host or "").strip().strip("[]").casefold()
    if not value:
        return False
    if value in {"localhost", "127.0.0.1", "::1"}:
        return True
    if value.endswith(_PRIVATE_SUFFIXES):
        return True
    if "." not in value:
        return True
    explicit = {
        item.strip().casefold()
        for item in os.environ.get("HUB_ALLOW_HTTP_HOSTS", "").split(",")
        if item.strip()
    }
    if value in explicit:
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.is_unspecified or address.is_multicast:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or (address.version == 4 and address in _CGNAT)
    )


def normalize_base_url(value: str) -> tuple[str, str]:
    """Return a safe base URL or an error message; blank means auto-detect."""
    text = str(value or "").strip()
    if not text:
        return "", ""
    if len(text) > 300 or any(ord(char) < 32 for char in text):
        return "", "地址过长或包含控制字符。"
    if "://" not in text:
        text = "http://" + text
    try:
        parsed = urlsplit(text)
        parsed_port = parsed.port
    except ValueError:
        return "", "地址格式不正确。"
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "", "地址必须使用 http:// 或 https://，并包含主机名。"
    if parsed.username or parsed.password:
        return "", "地址不能包含用户名或密码。"
    if parsed.query or parsed.fragment:
        return "", "地址不能包含查询参数或锚点。"
    host = parsed.hostname
    if parsed.scheme == "http" and not http_host_allowed(host):
        return "", "公网地址必须使用 HTTPS；HTTP 仅限本机、局域网、VPN 或内部域名。"
    if parsed_port is not None and not 1 <= parsed_port <= 65535:
        return "", "端口号无效。"
    path = parsed.path.rstrip("/")
    normalized = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return normalized, ""
