"""Local-only media handling for the immersive idle screen."""

from __future__ import annotations

import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from core.database import get_setting, set_setting
from core.runtime_paths import STORAGE_ROOT

WALLPAPER_DIR = STORAGE_ROOT / "wallpapers"
WALLPAPER_DIR.mkdir(parents=True, exist_ok=True)

BUILTIN_WALLPAPERS = {
    "dawn": {"name": "曙光山门", "path": "/static/wallpapers/dawn.svg"},
    "night": {"name": "星夜观测台", "path": "/static/wallpapers/night.svg"},
    "paper": {"name": "素纸静室", "path": "/static/wallpapers/paper.svg"},
}

MAX_WALLPAPER_BYTES = 12 * 1024 * 1024
MAX_WALLPAPER_PIXELS = 32_000_000


def current_wallpaper_filename() -> str:
    filename = Path(get_setting("idle_wallpaper_file", "")).name
    return filename if filename and (WALLPAPER_DIR / filename).is_file() else ""


def custom_wallpapers() -> list[dict[str, str]]:
    """Return a small local gallery instead of replacing the previous wallpaper."""
    selected = current_wallpaper_filename()
    files = sorted(
        (path for path in WALLPAPER_DIR.glob("idle_wallpaper_*.webp") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:12]
    return [
        {
            "filename": path.name,
            "url": f"/media/wallpapers/{path.name}",
            "selected": path.name == selected,
        }
        for path in files
    ]


def save_wallpaper_bytes(data: bytes) -> str:
    if not data:
        raise ValueError("请选择一张壁纸。")
    if len(data) > MAX_WALLPAPER_BYTES:
        raise ValueError("壁纸不能超过 12 MB。")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("文件不是可识别的图片。") from exc
    if image.width * image.height > MAX_WALLPAPER_PIXELS:
        raise ValueError("壁纸像素过大，请先压缩后再上传。")
    image = ImageOps.exif_transpose(image)
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGB")
    image.thumbnail((2560, 1600), Image.Resampling.LANCZOS)
    filename = f"idle_wallpaper_{uuid.uuid4().hex[:16]}.webp"
    image.save(WALLPAPER_DIR / filename, format="WEBP", quality=88, method=6)
    # 壁纸库上限 12 张：保留最新 12 张与当前选中的那一张，其余移除。
    keep = {filename, Path(get_setting("idle_wallpaper_file", "")).name}
    ordered = sorted(
        WALLPAPER_DIR.glob("idle_wallpaper_*.webp"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    surplus = [path for index, path in enumerate(ordered) if index >= 12 and path.name not in keep]
    for path in surplus:
        path.unlink(missing_ok=True)
    set_setting("idle_wallpaper_file", filename)
    set_setting("idle_wallpaper_kind", "custom")
    return filename


def select_builtin_wallpaper(key: str) -> None:
    if key not in BUILTIN_WALLPAPERS:
        raise ValueError("未找到所选自带壁纸。")
    set_setting("idle_wallpaper_kind", "builtin")
    set_setting("idle_wallpaper_builtin", key)


def select_custom_wallpaper(filename: str) -> None:
    safe_name = Path(filename).name
    if not safe_name or not (WALLPAPER_DIR / safe_name).is_file():
        raise ValueError("未找到所选自定义壁纸。")
    set_setting("idle_wallpaper_file", safe_name)
    set_setting("idle_wallpaper_kind", "custom")


def wallpaper_url() -> str:
    if get_setting("idle_wallpaper_kind", "builtin") == "custom":
        filename = current_wallpaper_filename()
        if filename:
            return f"/media/wallpapers/{filename}"
    key = get_setting("idle_wallpaper_builtin", "dawn")
    return BUILTIN_WALLPAPERS.get(key, BUILTIN_WALLPAPERS["dawn"])["path"]
