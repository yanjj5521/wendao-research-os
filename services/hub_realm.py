"""把个人端的完整境界名同步到中心，只推送快照，不回写任何数据。"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from typing import Any

from datetime import datetime, timedelta

from core.database import connect, get_setting, set_setting, total_xp
from core.version import APP_VERSION
from services import progression
from services.sync_backend import validate_hub_url


def snapshot() -> dict[str, Any]:
    """当前境界快照：名称、序号、修为。"""
    with connect() as conn:
        xp = int(total_xp(conn) or 0)
    try:
        labels = json.loads(get_setting("realm_labels", "{}") or "{}")
    except json.JSONDecodeError:
        labels = {}
    state = progression.realm_state(xp, labels if isinstance(labels, dict) else {})
    return {
        "realm_name": str(state.get("name", "")),
        "realm_index": int(state.get("index", 0) or 0),
        "cultivation": xp,
    }


def push(url: str, token: str) -> dict[str, Any]:
    """POST 到中心；失败抛 OSError/ValueError 由调用方转成提示。"""
    url = (url or "").strip().rstrip("/")
    token = (token or "").strip()
    if not url or not token:
        raise ValueError("先保存中心地址与 Token。")
    payload = json.dumps(snapshot(), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{url}/api/v1/profile/realm",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"WendaoResearch/{APP_VERSION}",
        },
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        body = response.read().decode("utf-8", "replace")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise ValueError("中心返回了无法解析的数据。") from None


def sync_if_due(
    *,
    force: bool = False,
    automatic: bool = False,
    raise_errors: bool = False,
    min_interval: timedelta = timedelta(hours=6),
) -> dict[str, Any] | None:
    """Sync a changed snapshot, with throttling scoped to the centre and account.

    Startup calls are automatic and respect the auto-sync preference. Explicit
    user actions can sync with auto-sync disabled, but never while the whole
    connection is disabled. Manual actions can request a useful error message.
    """
    if get_setting("sync_provider", "disabled") != "legacy_hub":
        if raise_errors:
            raise ValueError("请先启用同行会联机。")
        return None
    if automatic and get_setting("hub_auto_sync", "0") != "1":
        return None
    url = get_setting("hub_url", "").strip().rstrip("/")
    token = get_setting("hub_api_token", "").strip()
    if not url or not token:
        if raise_errors:
            raise ValueError("先保存中心地址与 Token。")
        return None
    valid_url, reason = validate_hub_url(url)
    if not valid_url:
        if raise_errors:
            raise ValueError(reason)
        return None
    current = snapshot()
    signature = f"{current['realm_name']}|{current['realm_index']}|{current['cultivation']}"
    scope = hashlib.sha256(f"{url}\0{token}".encode("utf-8")).hexdigest()
    same_scope = scope == get_setting("hub_realm_scope", "")
    if not force and same_scope and signature == get_setting("hub_realm_signature", ""):
        return None
    if not force and same_scope:
        try:
            pushed_at = datetime.fromisoformat(get_setting("hub_realm_pushed_at", ""))
            if pushed_at.tzinfo is None:
                pushed_at = pushed_at.astimezone()
        except ValueError:
            pushed_at = None
        if pushed_at and datetime.now().astimezone() - pushed_at < min_interval:
            return None
    try:
        result = push(url, token)
    except (OSError, ValueError):
        if raise_errors:
            raise
        return None
    set_setting("hub_realm_signature", signature)
    set_setting("hub_realm_scope", scope)
    set_setting("hub_realm_pushed_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    return result
