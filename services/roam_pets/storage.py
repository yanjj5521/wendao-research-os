"""宠物存档：带跨进程锁、事务更新、原子写入与备份恢复。"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, TypeVar

from core.runtime_paths import USER_CONFIG_DIR

VERSION = 2
T = TypeVar("T")
LOCK_TIMEOUT_SECONDS = 8.0


def default_path() -> Path:
    return USER_CONFIG_DIR / "roam_pets.json"


def blank() -> dict:
    return {
        "version": VERSION,
        "active": None,
        "pets": {},
        "dex": {"seen": [], "caught": []},
        "window_pos": {},
        "wallet": {"coins": 160},
        "inventory": {"basic": 8, "great": 2, "ultra": 0, "berry": 4},
        "journey": {"steps": 0, "captures": 0, "battles": 0, "wins": 0},
        "battle": None,
    }


def _normalize(raw: object) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("pets"), dict):
        return None
    data = blank()
    data["active"] = raw.get("active")
    data["pets"] = raw["pets"]
    dex = raw.get("dex")
    if isinstance(dex, dict):
        data["dex"] = {
            "seen": [str(v) for v in dex.get("seen", []) if str(v).strip()],
            "caught": [str(v) for v in dex.get("caught", []) if str(v).strip()],
        }
    if isinstance(raw.get("window_pos"), dict):
        data["window_pos"] = raw["window_pos"]
    if isinstance(raw.get("wallet"), dict):
        data["wallet"]["coins"] = max(0, int(raw["wallet"].get("coins", data["wallet"]["coins"])))
    if isinstance(raw.get("inventory"), dict):
        for key in data["inventory"]:
            try:
                data["inventory"][key] = max(0, int(raw["inventory"].get(key, data["inventory"][key])))
            except (TypeError, ValueError):
                pass
    if isinstance(raw.get("journey"), dict):
        for key in data["journey"]:
            try:
                data["journey"][key] = max(0, int(raw["journey"].get(key, data["journey"][key])))
            except (TypeError, ValueError):
                pass
    if isinstance(raw.get("battle"), dict):
        data["battle"] = raw["battle"]
    data["version"] = VERSION
    return data


def _read_file(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return _normalize(raw)


def _load_path(path: Path) -> dict:
    data = _read_file(path)
    if data is not None:
        return data
    backup = path.with_suffix(path.suffix + ".bak")
    restored = _read_file(backup)
    return restored if restored is not None else blank()


def load(path: Path | None = None) -> dict:
    return _load_path(path or default_path())


@contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("宠物存档正被其他进程占用，请稍后重试。")
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_unlocked(data: dict, path: Path) -> None:
    normalized = _normalize(data)
    if normalized is None:
        raise ValueError("宠物存档结构无效，已拒绝覆盖。")
    backup = path.with_suffix(path.suffix + ".bak")
    if _read_file(path) is not None:
        shutil.copy2(path, backup)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".roam_", suffix=".tmp", delete=False
    )
    try:
        json.dump(normalized, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        handle.close()
        Path(handle.name).unlink(missing_ok=True)
        raise


def save(data: dict, path: Path | None = None) -> Path:
    path = path or default_path()
    with _file_lock(path):
        _write_unlocked(data, path)
    return path


def update(mutator: Callable[[dict], T], path: Path | None = None) -> tuple[T, dict]:
    """Read, mutate, and write while holding the cross-process lock."""
    path = path or default_path()
    with _file_lock(path):
        data = _load_path(path)
        result = mutator(data)
        _write_unlocked(data, path)
        return result, data
