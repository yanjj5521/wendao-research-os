from __future__ import annotations

from core.runtime_paths import APP_ROOT


def _read_version() -> str:
    version_file = APP_ROOT / "VERSION"
    if version_file.exists():
        value = version_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    return "1.2.0"


APP_VERSION = _read_version()
