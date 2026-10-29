from __future__ import annotations

import os
import sys
from pathlib import Path


PRODUCT_DIR_NAME = "WendaoResearchV3"
USER_STORAGE_NAMES = (
    "uploads",
    "simulations",
    "research_foundation",
    "deliveries",
    "note_images",
    "profile",
    "wallpapers",
    "sync_exports",
    "hub_backups",
    "hub_releases",
    "autobackups",
    "backups",
)


def _resource_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", "")
    if frozen_root:
        return Path(frozen_root).resolve()
    # 本模块已归位到 core/，仓库根是其上一级目录。
    source_root = Path(__file__).resolve().parent.parent
    if (source_root / "templates").is_dir():
        return source_root
    installed_share = Path(sys.prefix) / "share" / "wendao-research"
    if (installed_share / "templates").is_dir():
        return installed_share.resolve()
    return source_root


def _distribution_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _default_user_data_root() -> Path:
    explicit = os.environ.get("RESEARCH_OS_DATA_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    distribution_root = _distribution_root()
    if (
        os.environ.get("RESEARCH_OS_PORTABLE", "").strip() == "1"
        or (distribution_root / "portable.flag").is_file()
    ):
        return distribution_root / "user_data"
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data).expanduser().resolve() / PRODUCT_DIR_NAME
        return Path.home() / "AppData" / "Local" / PRODUCT_DIR_NAME
    return _resource_root() / ".runtime"


APP_ROOT = _resource_root()
DISTRIBUTION_ROOT = _distribution_root()
DATA_ROOT = _default_user_data_root()
INSTANCE_DIR = DATA_ROOT / "instance"
STORAGE_ROOT = DATA_ROOT / "storage"
USER_CONFIG_DIR = DATA_ROOT / "user_config"


def ensure_data_layout() -> None:
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for name in USER_STORAGE_NAMES:
        (STORAGE_ROOT / name).mkdir(parents=True, exist_ok=True)


ensure_data_layout()
