"""中心端文件存储：成员头像等中心侧文件的安全读写。

安全规则（新增存储类型前先对照）：
- 存储路径只由 account_id 推导，绝不使用用户提供的文件名或路径；
- 扩展名白名单 + 大小上限 + 非空校验；
- 写入先落临时文件再原子替换，避免半写文件；
- 成员只能上传/移除自己的头像，管理员可以重置任何人的头像（留审计）。
"""

from __future__ import annotations

from pathlib import Path

from core.runtime_paths import INSTANCE_DIR

HUB_FILES_DIR = INSTANCE_DIR / "hub_files"
AVATAR_DIR = HUB_FILES_DIR / "avatars"

ALLOWED_AVATAR_EXT = {".png": ".png", ".jpg": ".jpg", ".jpeg": ".jpg", ".webp": ".webp"}
MAX_AVATAR_BYTES = 512 * 1024


def ensure_dirs() -> None:
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)


def avatar_path(user_id: int) -> Path | None:
    for ext in (".png", ".jpg", ".webp"):
        candidate = AVATAR_DIR / f"u{user_id}{ext}"
        if candidate.exists():
            return candidate
    return None


def save_avatar(user_id: int, data: bytes, filename: str) -> str:
    """保存成员头像，返回实际使用的扩展名；任何不合规输入都抛 ValueError。"""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_AVATAR_EXT:
        raise ValueError("头像仅支持 PNG / JPG / WebP 格式。")
    if not data:
        raise ValueError("头像文件为空。")
    if len(data) > MAX_AVATAR_BYTES:
        raise ValueError("头像不能超过 512KB。")
    ensure_dirs()
    # 先清理旧头像（注意：清理必须在写入临时文件之前，
    # 否则 glob("u{id}.*") 会把刚写入的 .uploading 临时文件一并删掉）。
    for old in AVATAR_DIR.glob(f"u{user_id}.*"):
        old.unlink(missing_ok=True)
    ext = ALLOWED_AVATAR_EXT[suffix]
    target = AVATAR_DIR / f"u{user_id}{ext}"
    tmp = AVATAR_DIR / f"u{user_id}.uploading"
    tmp.write_bytes(data)
    tmp.replace(target)
    return ext


def remove_avatar(user_id: int) -> None:
    for old in AVATAR_DIR.glob(f"u{user_id}.*"):
        old.unlink(missing_ok=True)
