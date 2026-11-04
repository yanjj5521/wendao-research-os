"""当前中心的版本包：信息从哪来、包怎么落到本机。

这里要同时照顾两种身份，避免"管理员下不了自己的包、成员下不了对方的包"：

- ``host``：中心库就在本机，直接读 ``hub_releases``，取包就是本地复制 + 校验；
- ``member``：向对方中心 ``/api/v1/releases/latest`` 现取元数据，再用 Bearer 拉包；
- 网络不通时退回同步缓存里的 ``latest_release``，只用于展示，取包仍会失败并给出原因。

取包统一落到 ``storage/team_updates/downloads/``，都做 SHA-256 与解包结构校验，
因此"下载成功"等价于"文件可用"。
"""

from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.database import get_setting
from core.runtime_paths import STORAGE_ROOT
from core.version import APP_VERSION
from services import hub_identity
from services.download import file_sha256
from services.team_update import MAX_UPDATE_BYTES, download_release_package, validate_package

DOWNLOAD_DIR = STORAGE_ROOT / "team_updates" / "downloads"
USER_AGENT = f"WendaoResearch/{APP_VERSION}"


def _safe_version(value: Any, fallback: Any = "") -> str:
    text = re.sub(r"[^0-9A-Za-z._-]+", "_", str(value or fallback))[:40]
    return text or "release"


def _local_latest() -> dict[str, Any] | None:
    try:
        from hub.db import connect_hub
    except Exception:  # 中心模块不可用（极少见）时不阻塞个人端
        return None
    try:
        with connect_hub() as conn:
            row = conn.execute(
                """SELECT id,version,title,notes,file_size,sha256,created_at,file_path
                   FROM hub_releases WHERE active=1 ORDER BY id DESC LIMIT 1"""
            ).fetchone()
    except Exception:
        return None
    return dict(row) if row else None


def _remote_latest(url: str, token: str) -> dict[str, Any] | None:
    if not url or not token:
        return None
    request = urllib.request.Request(
        f"{url.rstrip('/')}/api/v1/releases/latest",
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.load(response)
    release = payload.get("release") if isinstance(payload, dict) else None
    return release if isinstance(release, dict) else None


def latest_release() -> dict[str, Any] | None:
    """按当前身份取"我这个中心的"最新版本包，失败时退回同步缓存。"""
    identity = hub_identity.resolve()
    if identity["role"] == hub_identity.ROLE_MEMBER:
        url = str(identity.get("member_url") or "")
        try:
            release = _remote_latest(url, get_setting("hub_api_token", "").strip())
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
            release = None
        if release:
            return {**release, "source": "center", "center_url": url}
    elif identity["role"] == hub_identity.ROLE_HOST:
        release = _local_latest()
        if release:
            return {**release, "source": "local", "center_url": str(identity.get("center_url") or "")}
    from services.online_sync import cached_value

    cached = cached_value("latest_release", None)
    if isinstance(cached, dict) and cached.get("id"):
        return {**cached, "source": "cache", "center_url": str(identity.get("center_url") or "")}
    return None


def local_source(release: dict[str, Any] | None) -> Path | None:
    """本机中心的版本包原文件（中心身份下"原地打开"用）。"""
    if not isinstance(release, dict) or release.get("source") != "local":
        return None
    from hub.releases import resolve_release_file

    return resolve_release_file(release)


def fetch_package(release: dict[str, Any]) -> Path:
    """把版本包取到本机并校验，返回可用的本地路径。"""
    if not isinstance(release, dict) or not release.get("id"):
        raise ValueError("没有可下载的版本包信息，请先检查更新。")
    version = _safe_version(release.get("version"), release["id"])
    download_dir = DOWNLOAD_DIR
    download_dir.mkdir(parents=True, exist_ok=True)
    target = download_dir / f"WendaoResearch-v{version}-Team-Update.zip"

    if release.get("source") == "local":
        from hub.releases import resolve_release_file

        source = resolve_release_file(release)
        if source is None:
            raise ValueError("本机中心的版本包文件不存在，请到「版本中心」重新上传。")
        shutil.copy2(source, target)
        expected = str(release.get("sha256", "")).strip().lower()
        if expected and file_sha256(target) != expected:
            target.unlink(missing_ok=True)
            raise ValueError("复制出来的版本包 SHA-256 与中心记录不一致，已删除。")
        try:
            validate_package(target)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return target

    return download_release_package(
        release_id=int(release["id"]),
        sha256=str(release.get("sha256", "")),
        version=version,
    )


def max_bytes() -> int:
    return MAX_UPDATE_BYTES
