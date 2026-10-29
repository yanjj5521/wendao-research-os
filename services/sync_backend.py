from __future__ import annotations

import ipaddress
import json
import urllib.request
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Any, Protocol

from core.version import APP_VERSION

SYNC_CONTRACT_VERSION = "2026-07-27"
SYNC_EVENT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BackendCapabilities:
    provider: str
    label: str
    enabled: bool
    ready: bool
    production_scale: bool
    contract_version: str = SYNC_CONTRACT_VERSION
    max_batch_size: int = 100
    supports_cursor: bool = False
    supports_tenants: bool = False
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "label": self.label,
            "enabled": self.enabled,
            "ready": self.ready,
            "production_scale": self.production_scale,
            "contract_version": self.contract_version,
            "event_schema_version": SYNC_EVENT_SCHEMA_VERSION,
            "max_batch_size": self.max_batch_size,
            "supports_cursor": self.supports_cursor,
            "supports_tenants": self.supports_tenants,
            "detail": self.detail,
        }


class SyncBackend(Protocol):
    capabilities: BackendCapabilities

    def ping(self, timeout: float = 3.0) -> dict[str, Any]:
        ...

    def bootstrap(self, timeout: float = 5.0) -> dict[str, Any]:
        ...

    def push(
        self,
        events: list[dict[str, Any]],
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        ...


class DisabledBackend:
    capabilities = BackendCapabilities(
        provider="disabled",
        label="未启用",
        enabled=False,
        ready=False,
        production_scale=False,
        detail="仅保留版本化扩展接口，本机不会排队、上传或拉取任何状态。",
    )

    def ping(self, timeout: float = 3.0) -> dict[str, Any]:
        raise RuntimeError(self.capabilities.detail)

    def bootstrap(self, timeout: float = 5.0) -> dict[str, Any]:
        raise RuntimeError(self.capabilities.detail)

    def push(
        self,
        events: list[dict[str, Any]],
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        raise RuntimeError(self.capabilities.detail)


class ReservedCloudBackend:
    capabilities = BackendCapabilities(
        provider="cloud_v2",
        label="规模化云端 v2（预留）",
        enabled=False,
        ready=False,
        production_scale=True,
        supports_cursor=True,
        supports_tenants=True,
        detail="协议与适配器插槽已经固定，但服务器、登录和数据库尚未实现。",
    )

    def ping(self, timeout: float = 3.0) -> dict[str, Any]:
        raise RuntimeError(self.capabilities.detail)

    def bootstrap(self, timeout: float = 5.0) -> dict[str, Any]:
        raise RuntimeError(self.capabilities.detail)

    def push(
        self,
        events: list[dict[str, Any]],
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        raise RuntimeError(self.capabilities.detail)


class LegacyHubBackend:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.strip().rstrip("/")
        self.token = token.strip()
        ready = bool(self.base_url and self.token)
        self.capabilities = BackendCapabilities(
            provider="legacy_hub",
            label="轻量同行会（兼容）",
            enabled=True,
            ready=ready,
            production_scale=False,
            detail=(
                "适用于小范围自托管；客户端带超时、退避、熔断和幂等事件保护。"
                if ready
                else "需要填写轻量同行会地址与 Token。"
            ),
        )

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        if not self.capabilities.ready:
            raise RuntimeError(self.capabilities.detail)
        data = (
            json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None
            else None
        )
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": f"ResearchCultivationOS/{APP_VERSION}",
                "X-Sync-Contract": SYNC_CONTRACT_VERSION,
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def ping(self, timeout: float = 3.0) -> dict[str, Any]:
        return self._request("GET", "/api/v1/ping", timeout=timeout)

    def bootstrap(self, timeout: float = 5.0) -> dict[str, Any]:
        return self._request("GET", "/api/v1/bootstrap", timeout=timeout)

    def push(
        self,
        events: list[dict[str, Any]],
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/events",
            {"events": events},
            timeout=timeout,
        )

    def feedback(self, kind: str, content: str, contact: str = "", timeout: float = 6.0) -> dict[str, Any]:
        return self._request(
            "POST",
            "/feedback",
            {"kind": kind, "content": content, "contact": contact},
            timeout=timeout,
        )


def build_sync_backend(
    provider: str,
    hub_url: str = "",
    token: str = "",
) -> SyncBackend:
    provider = (provider or "disabled").strip()
    if provider == "legacy_hub":
        return LegacyHubBackend(hub_url, token)
    if provider == "cloud_v2":
        return ReservedCloudBackend()
    return DisabledBackend()


def all_backend_capabilities() -> list[dict[str, Any]]:
    return [
        DisabledBackend.capabilities.as_dict(),
        LegacyHubBackend("", "").capabilities.as_dict(),
        ReservedCloudBackend.capabilities.as_dict(),
    ]


def validate_hub_url(value: str) -> tuple[bool, str]:
    """Allow plain HTTP only on the current machine or a private LAN."""
    url = (value or "").strip().rstrip("/")
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "中心地址格式无效。"
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False, "中心地址必须是完整的 http:// 或 https:// 地址。"
    if parsed.username or parsed.password:
        return False, "中心地址不能包含用户名或密码。"
    if parsed.scheme == "https":
        return True, "HTTPS 加密连接"
    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return True, "仅限本机或局域网的 HTTP"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local):
        return True, "仅限局域网的 HTTP"
    return False, "公网中心必须使用 HTTPS；HTTP 只允许本机或私有局域网地址。"
