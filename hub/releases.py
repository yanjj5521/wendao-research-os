"""版本包文件的统一解析与下发。

历史包路径有两种来源：早期行内写的是相对路径，现在写绝对路径；用户也可能
搬动过数据目录。只要文件名还在 ``storage/hub_releases`` 里，就应当仍可下载，
而不是抛 404 让人以为"同行会没有版本包"。

所有下载都必须落在 ``HUB_RELEASE_DIR`` 之内，防止路径穿越。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from hub.common import BASE_DIR
from hub.db import HUB_RELEASE_DIR

MISSING_MESSAGE = "版本包文件不存在：中心可能迁移过数据目录，请管理员在「版本中心」重新上传。"


def release_root() -> Path:
    try:
        return HUB_RELEASE_DIR.resolve()
    except OSError:
        return HUB_RELEASE_DIR


def resolve_release_file(row) -> Path | None:
    """把数据库记录解析成真实文件；找不到或越界都返回 None。"""
    root = release_root()
    raw = str(row["file_path"] or "").strip()
    candidates: list[Path] = []
    if raw:
        stored = Path(raw)
        candidates.append(stored if stored.is_absolute() else BASE_DIR / stored)
        if stored.name:
            candidates.append(root / stored.name)
    if row["version"]:
        candidates.extend(sorted(root.glob(f"*{row['version']}*")))
    for candidate in candidates:
        try:
            target = candidate.resolve()
        except OSError:
            continue
        if target.is_file() and (target == root or root in target.parents):
            return target
    return None


def release_download_response(row, *, filename: str = "") -> FileResponse:
    target = resolve_release_file(row)
    if target is None:
        raise HTTPException(status_code=404, detail=MISSING_MESSAGE)
    return FileResponse(
        target,
        filename=filename or target.name,
        media_type="application/zip",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
