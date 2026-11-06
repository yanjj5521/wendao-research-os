"""时间工具的唯一实现。

数据库、迁移、账号与中心库都用到"本地时区、秒级精度"的时间戳。此前四个模块
各写了一遍，任何一处改格式都会让新旧记录混排——统一到这里。
"""

from __future__ import annotations

from datetime import datetime


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
