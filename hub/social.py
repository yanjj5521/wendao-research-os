"""同行会社交辅助：资产文案、转账限额与成员列表。"""

from __future__ import annotations

from typing import Any

from hub.db import ASSET_KEYS


SOCIAL_ASSET_GROUPS = (
    (
        "基础",
        (("cultivation", "修为"), ("spirit_stone", "灵石"), ("spirit_wood", "灵木")),
    ),
    (
        "灵草",
        (("qi_grass", "聚气草"), ("dew_flower", "凝露花"), ("red_ganoderma", "赤霞芝")),
    ),
    (
        "法宝",
        (("azure_sword", "青锋剑"), ("heart_mirror", "护心镜"), ("cosmos_bag", "乾坤袋")),
    ),
    (
        "天地灵物",
        (("mystic_iron", "玄铁"), ("star_sand", "星砂"), ("primordial_stone", "太初石")),
    ),
)
SOCIAL_ASSET_LABELS = {
    key: label for _, items in SOCIAL_ASSET_GROUPS for key, label in items
}
CHAT_EMOJIS = (
    "😀", "😄", "😊", "🥹", "😭", "😅", "👍", "🙏",
    "💪", "✨", "🌿", "🧪", "📚", "🔍", "💡", "🔥",
    "🎉", "☕", "🌙", "⭐", "🧠", "📌", "✅", "🧧",
)
SOCIAL_TRANSFER_LIMITS = {
    "cultivation": 500,
    "spirit_stone": 200,
    "spirit_wood": 50,
    "qi_grass": 30,
    "dew_flower": 20,
    "red_ganoderma": 10,
    "azure_sword": 5,
    "heart_mirror": 5,
    "cosmos_bag": 2,
    "mystic_iron": 50,
    "star_sand": 50,
    "primordial_stone": 5,
}


def social_text(value: str, *, limit: int, label: str) -> str:
    text = " ".join(value.strip().split())
    if not text:
        raise ValueError(f"{label}不能为空。")
    return text[:limit]


def social_asset(value: str) -> str:
    if value not in ASSET_KEYS:
        raise ValueError("请选择可流转的虚拟资产。")
    return value


def social_amount(asset: str, value: int) -> int:
    amount = int(value or 0)
    if amount < 1 or amount > SOCIAL_TRANSFER_LIMITS[asset]:
        raise ValueError(f"{SOCIAL_ASSET_LABELS[asset]}单次流转为 1—{SOCIAL_TRANSFER_LIMITS[asset]}。")
    return amount


def social_members(conn, excluded_user_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id,display_name,username FROM accounts WHERE active=1 AND id<>? ORDER BY display_name",
            (excluded_user_id,),
        )
    ]



