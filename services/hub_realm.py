"""把个人端的完整境界名同步到中心，只推送快照，不回写任何数据。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from datetime import datetime, timedelta

from core.database import connect, get_setting, set_setting, total_xp
from core.version import APP_VERSION
from services import progression


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


def sync_if_due(*, force: bool = False, min_interval: timedelta = timedelta(hours=6)) -> dict[str, Any] | None:
    """自动同步：境界名或修为变化、或距上次超过 6 小时才推，避免每次点同步都发请求。

    返回推送结果；不需要推送或失败时返回 None（调用方静默）。
    """
    url = get_setting("hub_url", "").strip()
    token = get_setting("hub_api_token", "").strip()
    if not url or not token:
        return None
    current = snapshot()
    signature = f"{current['realm_name']}|{current['realm_index']}|{current['cultivation']}"
    if not force and signature == get_setting("hub_realm_signature", ""):
        return None
    if not force:
        try:
            pushed_at = datetime.fromisoformat(get_setting("hub_realm_pushed_at", ""))
        except ValueError:
            pushed_at = None
        if pushed_at and datetime.now().astimezone() - pushed_at < min_interval:
            return None
    try:
        result = push(url, token)
    except (OSError, ValueError):
        return None
    set_setting("hub_realm_signature", signature)
    set_setting("hub_realm_pushed_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    return result
