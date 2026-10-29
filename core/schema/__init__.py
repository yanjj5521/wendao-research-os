"""建表定义的统一出口。

个人端与中心端是两套独立的数据库，schema 分开放，避免任何一边的改动
意外影响另一边。
"""

from core.schema.hub import HUB_SCHEMA_SQL, HUB_SCHEMA_VERSION
from core.schema.personal import (
    BASE_SCHEMA_SQL,
    DATABASE_FILENAME,
    FTS_SCHEMA_SQL,
    SCHEMA_VERSION,
)

__all__ = [
    "BASE_SCHEMA_SQL",
    "DATABASE_FILENAME",
    "FTS_SCHEMA_SQL",
    "HUB_SCHEMA_SQL",
    "HUB_SCHEMA_VERSION",
    "SCHEMA_VERSION",
]
