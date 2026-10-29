from __future__ import annotations

import base64
import io
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from core.database import get_setting, set_setting
from core.runtime_paths import STORAGE_ROOT

PROFILE_DIR = STORAGE_ROOT / "profile"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

MAX_AVATAR_BYTES = 5 * 1024 * 1024
MAX_AVATAR_PIXELS = 24_000_000
MAX_PORTABLE_AVATAR_BYTES = 2 * 1024 * 1024


def current_avatar_filename() -> str:
    filename = Path(get_setting("avatar_file", "")).name
    return filename if filename and (PROFILE_DIR / filename).is_file() else ""


def remove_avatar() -> None:
    filename = current_avatar_filename()
    if filename:
        (PROFILE_DIR / filename).unlink(missing_ok=True)
    set_setting("avatar_file", "")


def save_avatar_bytes(data: bytes) -> str:
    if not data:
        raise ValueError("请选择一张图片。")
    if len(data) > MAX_AVATAR_BYTES:
        raise ValueError("头像不能超过 5 MB。")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("文件不是可识别的图片。") from exc
    if image.width * image.height > MAX_AVATAR_PIXELS:
        raise ValueError("图片像素过大，请先压缩后再上传。")
    image = ImageOps.exif_transpose(image)
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    image.thumbnail((512, 512), Image.Resampling.LANCZOS)
    filename = f"avatar_{uuid.uuid4().hex[:16]}.webp"
    target = PROFILE_DIR / filename
    image.save(target, format="WEBP", quality=88, method=6)
    previous = current_avatar_filename()
    set_setting("avatar_file", filename)
    if previous and previous != filename:
        (PROFILE_DIR / previous).unlink(missing_ok=True)
    return filename


def export_avatar_payload() -> dict[str, Any] | None:
    filename = current_avatar_filename()
    if not filename:
        return None
    data = (PROFILE_DIR / filename).read_bytes()
    if len(data) > MAX_PORTABLE_AVATAR_BYTES:
        return None
    return {
        "media_type": "image/webp",
        "data_base64": base64.b64encode(data).decode("ascii"),
    }


def import_avatar_payload(payload: Any) -> str:
    if not isinstance(payload, dict) or not payload.get("data_base64"):
        return ""
    try:
        data = base64.b64decode(str(payload["data_base64"]), validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("个性化包中的头像数据无效。") from exc
    return save_avatar_bytes(data)
