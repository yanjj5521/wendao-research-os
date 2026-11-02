"""存档：user_config/roam_pets.json，原子写入。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from core.runtime_paths import USER_CONFIG_DIR

VERSION = 1


def default_path() -> Path:
    return USER_CONFIG_DIR / "roam_pets.json"


def blank() -> dict:
    return {
        "version": VERSION,
        "active": None,          # 当前跟随的宠物 species_id
        "pets": {},              # species_id -> PetState.to_dict()
        "dex": {"seen": [], "caught": []},
        "window_pos": {},        # species_id -> [x, y]
    }


def load(path: Path | None = None) -> dict:
    path = path or default_path()
    data = blank()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return data
    if not isinstance(raw, dict):
        return data
    for key in ("active", "pets", "dex", "window_pos"):
        if key in raw and raw[key] is not None:
            data[key] = raw[key]
    data["version"] = int(raw.get("version", VERSION))
    return data


def save(data: dict, path: Path | None = None) -> Path:
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".roam_", suffix=".tmp", delete=False
    )
    try:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        handle.close()
        Path(handle.name).unlink(missing_ok=True)
        raise
    return path
