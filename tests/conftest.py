"""测试统一入口：把随机成率固定为必成，让回归可复现。"""

from __future__ import annotations

import os

os.environ.setdefault("RESEARCH_OS_DETERMINISTIC", "1")
