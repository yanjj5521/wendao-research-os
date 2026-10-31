from __future__ import annotations

import re

from core.runtime_paths import APP_ROOT


def _read_version() -> str:
    version_file = APP_ROOT / "VERSION"
    if version_file.exists():
        value = version_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    return "1.3.7"


APP_VERSION = _read_version()


def version_key(value: str) -> tuple[int, ...]:
    """Return a comparable numeric key for versions such as ``1.10.2``.

    Release versions in this project are numeric dotted versions.  Non-numeric
    suffixes are intentionally ignored for ordering, while missing components
    are treated as zero (``1.3`` == ``1.3.0``).
    """
    parts = [int(part) for part in re.findall(r"\d+", str(value or ""))]
    return tuple(parts or [0])


def is_newer_version(candidate: str, current: str) -> bool:
    left = version_key(candidate)
    right = version_key(current)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))
