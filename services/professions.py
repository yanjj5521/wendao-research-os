"""修仙百艺：职业、等阶与加成。

三个职业对应三条已经存在的行动链，避免"多一套空转数值"：

- 炼丹师：`/alchemy/craft/{recipe}` 炼丹；
- 炼器师：`/world/artifacts/{key}/upgrade` 淬炼法器；
- 御兽师：灵兽结契与随行。

经验只记在 ``settings`` 里（``profession_exp`` / ``profession_active``），
不改数据库结构，升级不会重置已有进度。
"""

from __future__ import annotations

import json
from typing import Any

from core.database import get_setting, set_setting, transaction

PROFESSIONS: dict[str, dict[str, Any]] = {
    "alchemist": {
        "name": "炼丹师",
        "icon": "丹",
        "accent": "amber",
        "summary": "以灵草入炉，把一次复盘炼成可服用的丹药。",
        "actions": "炼丹、控火、辨药",
        "bonus_label": "成丹率与丹药品质",
        "entry": ("alchemy_page", "去丹房"),
    },
    "artificer": {
        "name": "炼器师",
        "icon": "器",
        "accent": "steel",
        "summary": "以玄铁星砂淬炼法器，让工具承担重复的判断。",
        "actions": "淬炼、铭纹、养器",
        "bonus_label": "淬炼成功率与法器上限",
        "entry": ("world_page", "去洞府"),
    },
    "beastmaster": {
        "name": "御兽师",
        "icon": "兽",
        "accent": "jade",
        "summary": "与灵兽结契同行，把长期陪伴变成稳定的修行节律。",
        "actions": "结契、饲灵、同行",
        "bonus_label": "结契成功率与灵兽成长",
        "entry": ("roam_page", "去灵兽园"),
    },
}

# (经验门槛, 等阶名, 说明)
TIERS: tuple[tuple[int, str, str], ...] = (
    (0, "初学", "刚入门，先把手感练稳。"),
    (120, "入门", "能独立完成一次完整流程。"),
    (360, "精熟", "手法稳定，失败率明显下降。"),
    (900, "大家", "开始形成自己的方子与章法。"),
    (2100, "宗师", "能改法、能救场，输出可复现。"),
    (4800, "天工", "一艺通神，作品可传世。"),
)

DEFAULT_ACTIVE = "alchemist"


def _read_exp() -> dict[str, int]:
    try:
        raw = json.loads(get_setting("profession_exp", "{}") or "{}")
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        key: max(0, int(raw.get(key, 0) or 0))
        for key in PROFESSIONS
    }


def tier_index(exp: int) -> int:
    index = 0
    for position, (threshold, _name, _hint) in enumerate(TIERS):
        if exp >= threshold:
            index = position
    return index


def tier_of(exp: int) -> dict[str, Any]:
    index = tier_index(exp)
    threshold, name, hint = TIERS[index]
    next_threshold = TIERS[index + 1][0] if index + 1 < len(TIERS) else None
    span = (next_threshold - threshold) if next_threshold else 0
    progress = 100 if not next_threshold else int((exp - threshold) / max(1, span) * 100)
    return {
        "index": index,
        "name": name,
        "hint": hint,
        "exp": exp,
        "threshold": threshold,
        "next_threshold": next_threshold,
        "progress": max(0, min(100, progress)),
        "maxed": next_threshold is None,
    }


def bonus(profession: str) -> float:
    """等阶加成系数：每阶 +8%，天工为 1.40。"""
    if profession not in PROFESSIONS:
        return 1.0
    return 1.0 + tier_index(_read_exp()[profession]) * 0.08


def success_chance(profession: str, base: float) -> float:
    """把等阶加成换算成成功率：每阶约 +3.6 个百分点，夹在 5%–98% 之间。"""
    return max(0.05, min(0.98, base + (bonus(profession) - 1.0) * 0.45))


def active() -> str:
    value = get_setting("profession_active", DEFAULT_ACTIVE).strip()
    return value if value in PROFESSIONS else DEFAULT_ACTIVE


def set_active(profession: str) -> None:
    if profession not in PROFESSIONS:
        raise ValueError("未知职业")
    set_setting("profession_active", profession)


def add_exp(profession: str, amount: int) -> dict[str, Any]:
    """给某职业加经验，返回是否升阶。"""
    if profession not in PROFESSIONS:
        return {"ok": False}
    amount = max(0, int(amount))
    if not amount:
        return {"ok": True, "leveled": False}
    with transaction() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='profession_exp'").fetchone()
        try:
            data = json.loads(row["value"]) if row else {}
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        before = tier_index(int(data.get(profession, 0) or 0))
        data[profession] = max(0, int(data.get(profession, 0) or 0) + amount)
        after = tier_index(data[profession])
        conn.execute(
            """INSERT INTO settings(key,value) VALUES ('profession_exp',?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (json.dumps(data, ensure_ascii=False),),
        )
    return {
        "ok": True,
        "leveled": after > before,
        "tier": TIERS[after][1],
        "exp": data[profession],
    }


def state() -> dict[str, Any]:
    """页面用的完整职业状态。"""
    exp_map = _read_exp()
    current = active()
    cards = []
    for key, spec in PROFESSIONS.items():
        tier = tier_of(exp_map[key])
        cards.append(
            {
                "key": key,
                **spec,
                "tier": tier,
                "bonus_percent": int(round((1.0 + tier["index"] * 0.08 - 1.0) * 100)),
                "is_active": key == current,
            }
        )
    return {
        "cards": cards,
        "active": current,
        "active_name": PROFESSIONS[current]["name"],
        "tiers": [{"threshold": t, "name": n, "hint": h} for t, n, h in TIERS],
    }


def summary_line(profession: str) -> str:
    tier = tier_of(_read_exp()[profession])
    return f"{PROFESSIONS[profession]['name']}·{tier['name']}（加成 {int(round((bonus(profession)-1)*100))}%）"
