from __future__ import annotations

from typing import Any

from core.database import now_iso

ASSETS = {
    "spirit_stone": {"name": "灵石", "icon": "◆"},
    "spirit_wood": {"name": "灵木", "icon": "⌁"},
    "mystic_iron": {"name": "玄铁", "icon": "⬢"},
    "star_sand": {"name": "星砂", "icon": "✦"},
}


def balance(conn, asset_key: str = "spirit_stone") -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS total FROM asset_transactions WHERE asset_key=?",
        (asset_key,),
    ).fetchone()
    return int(row["total"] or 0)


def balances(conn) -> dict[str, int]:
    values = {key: 0 for key in ASSETS}
    for row in conn.execute("SELECT asset_key,COALESCE(SUM(amount),0) total FROM asset_transactions GROUP BY asset_key"):
        values[row["asset_key"]] = int(row["total"] or 0)
    return values


def transact(conn, asset_key: str, amount: int, reason: str, mission_id: int | None = None) -> None:
    if asset_key not in ASSETS:
        raise ValueError("未知资材")
    if amount < 0 and balance(conn, asset_key) + amount < 0:
        raise ValueError(f"{ASSETS[asset_key]['name']}不足")
    conn.execute(
        "INSERT INTO asset_transactions(asset_key,amount,reason,mission_id,created_at) VALUES (?,?,?,?,?)",
        (asset_key, int(amount), reason, mission_id, now_iso()),
    )


# 材料语义：玄铁=实验与硬件，星砂=计算与数据，灵木=文献与概念。
# 关键词表按需扩充；都不命中时默认灵木。
MATERIAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "mystic_iron": ("实验", "水泥", "膨胀石墨", "eg", "电化学", "cv", "eis", "压片", "comsol", "仿真"),
    "star_sand": ("md", "lammps", "机器学习", "ml", "代码", "数据", "数据集", "模型", "训练"),
    "spirit_wood": ("文献", "论文", "阅读", "笔记", "概念", "综述"),
}


def mission_material(mission: Any) -> str:
    category = str(mission["category"] or "")
    title = str(mission["title"] or "")
    text = f"{category} {title}".lower()
    for asset, tokens in MATERIAL_KEYWORDS.items():
        if any(token in text for token in tokens):
            return asset
    return "spirit_wood"


def mission_rewards(mission: Any) -> dict[str, int]:
    xp = max(1, int(mission["xp"] or 1))
    optional = bool(mission["optional"])
    stones = max(5, round(xp * (0.55 if optional else 0.8)))
    reward = {"spirit_stone": stones}
    material = mission_material(mission)
    reward[material] = 2 if not optional else 1
    return reward


def award_mission(conn, mission: Any) -> dict[str, int]:
    rewards = mission_rewards(mission)
    equipped = conn.execute(
        """
        SELECT item_key,level FROM inventory_items
        WHERE item_type='artifact' AND equipped=1
        ORDER BY updated_at DESC,id DESC LIMIT 1
        """
    ).fetchone()
    if equipped and equipped["item_key"] == "returning_furnace":
        material_key = next(
            (key for key in rewards if key != "spirit_stone"),
            "spirit_wood",
        )
        rewards[material_key] = rewards.get(material_key, 0) + max(
            1, int(equipped["level"] or 1)
        )
    for asset_key, amount in rewards.items():
        transact(conn, asset_key, amount, f"完成任务：{mission['title']}", int(mission["id"]))
    return rewards
