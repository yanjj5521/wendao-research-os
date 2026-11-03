"""同行会身份解析：同一个程序里的两种"同行会"必须区分开。

- ``host``：这台电脑就是联机中心。``/team`` 打开的是本机中心，邀请码、
  成员管理、福利与版本包都在这里。
- ``member``：这台电脑只是成员，真正承载团队数据的是别人电脑上的中心。
  「进入同行会」必须指向那个地址；本机 ``/team`` 只是一份空副本，
  拿本机副本去登录会得到"邀请码不存在"这种看似故障的结果。
- ``unbound``：还没有选择身份，既没开中心，也没连接别人。

联机页、``/team`` 入口和侧栏入口都只从这里读身份，避免"连了别人的中心，
点同行会却进了本机空壳"的错配。身份完全由本机设置推导，不新增持久化字段，
因此升级不会改动任何既有用户配置。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from core.database import get_setting

ROLE_HOST = "host"
ROLE_MEMBER = "member"
ROLE_UNBOUND = "unbound"

_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0", "[::1]"}


def local_hosts() -> set[str]:
    """本机所有可作为中心入口的主机名/IP。"""
    hosts = set(_LOOPBACK)
    try:
        from services.network_access import discover_adapters

        for adapter in discover_adapters():
            if adapter.address:
                hosts.add(str(adapter.address))
    except Exception:  # 网络探测失败不影响身份判断，退化为只认回环
        pass
    return hosts


def url_host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").strip().lower()
    except ValueError:
        return ""


def is_local_url(url: str) -> bool:
    host = url_host(url)
    return bool(host) and host in {item.lower() for item in local_hosts()}


def _host_status() -> dict[str, Any]:
    try:
        from services import hub_host

        return dict(hub_host.status())
    except Exception:
        return {"running": False, "local_url": "", "preferred_url": "", "access_urls": []}


def _headnet_is_center() -> bool:
    try:
        from services import headscale_vpn

        state = headscale_vpn.status()
        return bool(state.get("running")) and str(state.get("role") or "") == "center"
    except Exception:
        return False


def resolve(host_status: dict[str, Any] | None = None) -> dict[str, Any]:
    """推导当前身份，并给出「进入同行会」应该打开哪里。"""
    host = dict(host_status) if host_status is not None else _host_status()
    url = get_setting("hub_url", "").strip().rstrip("/")
    token = get_setting("hub_api_token", "").strip()
    provider = get_setting("sync_provider", "disabled")
    configured = provider == "legacy_hub" and bool(url) and bool(token)
    self_center = configured and is_local_url(url)
    running = bool(host.get("running")) or _headnet_is_center()

    if configured and not self_center:
        role = ROLE_MEMBER
    elif running or configured:
        role = ROLE_HOST
    else:
        role = ROLE_UNBOUND

    local_url = str(host.get("local_url") or "").rstrip("/")
    preferred = str(host.get("preferred_url") or local_url).rstrip("/")

    if role == ROLE_MEMBER:
        center_url = url
        entry_url = url
        entry_kind = "external"
        badge = "成员"
        label = "我是成员 · 连接他人的联机中心"
        detail = (
            f"你的同行会数据在 {url} 上。点「进入同行会」会直接打开这个中心；"
            "本机这份只是空副本，不要在本机副本里注册或找邀请码。"
        )
        action_label = "打开我连接的同行会"
    elif role == ROLE_HOST:
        center_url = preferred or local_url
        entry_url = "/team/"
        entry_kind = "embedded"
        badge = "中心"
        label = "我是中心 · 由本机承载同行会"
        if running:
            detail = "本机中心正在运行；成员用你分发的地址登录，版本包与邀请码都在这里发布。"
        else:
            detail = (
                "还没有启动本机中心。启动后「进入同行会」打开的是你自己的中心，"
                "成员才能用你的地址登录。"
            )
        action_label = "进入我的同行会中心"
    else:
        center_url = ""
        entry_url = "/online#identity"
        entry_kind = "setup"
        badge = "未选择"
        label = "还没选择身份"
        detail = "同行会有两种用法：自己开中心当管理员，或连接别人的中心当成员。先选一种。"
        action_label = "选择我的身份"

    return {
        "role": role,
        "badge": badge,
        "label": label,
        "detail": detail,
        "action_label": action_label,
        "center_url": center_url,
        "entry_url": entry_url,
        "entry_kind": entry_kind,
        "host_running": running,
        "host_url": local_url or preferred,
        "member_url": url if configured and not self_center else "",
        "member_connected": bool(configured and not self_center),
        "provider": provider,
        "token_configured": bool(token),
    }


def member_entry_allowed() -> bool:
    """成员身份下是否已经具备打开对方中心的地址。"""
    return resolve()["role"] == ROLE_MEMBER
