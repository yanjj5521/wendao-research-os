from __future__ import annotations

from typing import Any

ARTIFACTS = {
    "qingxin_slip": {
        "name": "清心玉简", "icon": "▤", "price": 12, "category": "专注",
        "max_level": 5,
        "desc": "把今日必做抬到视觉中心。",
        "effect": "佩戴后每日任务进入清心模式，弱化非必要信息；每级再提升一点聚焦强度。",
    },
    "measuring_ruler": {
        "name": "量天尺", "icon": "⌇", "price": 36, "category": "校核",
        "desc": "守住单位、尺度与比较口径。",
        "effect": "在近期计划和课题页显示“口径校核”提示，提醒变量、单位、重复数和基线。",
    },
    "returning_furnace": {
        "name": "归一炉", "icon": "◉", "price": 48, "category": "资源",
        "max_level": 5,
        "desc": "把零散行动炼成可复用材料。",
        "effect": "每日任务首次交付时，额外获得与任务类型对应的材料；法器每级额外 +1。",
    },
    "star_compass": {
        "name": "观星罗盘", "icon": "✧", "price": 58, "category": "课题",
        "desc": "在大量分支中守住主问题。",
        "effect": "AI 协作和课题页突出当前课题、关联工作区与唯一下一行动。",
    },
    "time_sandglass": {
        "name": "一刻沙漏", "icon": "⌛", "price": 24, "category": "专注",
        "max_level": 5,
        "desc": "给模糊行动一个清晰边界。",
        "effect": "静室只保留 25 分钟快捷节奏；升至 2、4 级后依次解锁 45、60 分钟预设。",
    },
    "paper_crane": {
        "name": "传思纸鹤", "icon": "⌁", "price": 28, "category": "协作",
        "desc": "把对话带回来，不让结论散失。",
        "effect": "在 AI 协作台持续展开回存提醒，优先保存“结论—证据—唯一下一行动”。",
    },
    "prism_lens": {
        "name": "七色棱镜", "icon": "◇", "price": 42, "category": "趣味",
        "desc": "让同一证据折射出不同解释。",
        "effect": "增强主页棱镜光晕；再次点击隐藏几何节点会轮换一个反事实问题。",
    },
    "evidence_seal": {
        "name": "证据法印", "icon": "印", "price": 52, "category": "课题",
        "desc": "没有证据，就不轻易过关。",
        "effect": "在课题闸门旁显示最小证据清单：实际证据、决策理由、唯一下一步。",
    },
    "echo_bell": {
        "name": "回响铃", "icon": "◌", "price": 32, "category": "复盘",
        "desc": "让昨天的判断在恰当时刻回响。",
        "effect": "存在到期复盘时，在侧栏与洞府给出克制提醒，不扫描未授权资料。",
    },
    "archive_key": {
        "name": "万卷钥", "icon": "钥", "price": 40, "category": "检索",
        "desc": "为经常复用的证据留一条短路。",
        "effect": "洞府增加“万卷快寻”入口，可直接跳到全文检索与收藏资料。",
    },
    "trial_token": {
        "name": "反证筹", "icon": "反", "price": 46, "category": "秘境",
        "desc": "先主动寻找反例，再让现实指出漏洞。",
        "effect": "秘境默认突出反证试炼，并显示最近一次错误最集中的评分点。",
    },
    "moon_lantern": {
        "name": "月白灯", "icon": "灯", "price": 22, "category": "趣味",
        "desc": "给夜间科研留一点温柔。",
        "effect": "夜间界面出现低亮月光与一条古诗；不会增加任务或打断工作。",
    },
}

BUILDINGS = {
    "scripture_pavilion": {
        "name": "藏经阁", "icon": "阁", "desc": "文献、笔记与知识检索之所。", "asset": "spirit_wood",
        "base_cost": 8,
    },
    "alchemy_room": {
        "name": "炼丹房", "icon": "丹", "desc": "实验数据、机器学习与结构化资产之所。", "asset": "star_sand",
        "base_cost": 8,
    },
    "forge_hall": {
        "name": "炼器坊", "icon": "器", "desc": "LAMMPS、代码、SOP与可复现案例之所。", "asset": "mystic_iron",
        "base_cost": 8,
    },
    "herb_garden": {
        "name": "灵植园", "icon": "圃", "desc": "特殊任务获得的分级灵草在这里生长。", "asset": "spirit_wood",
        "base_cost": 10,
    },
}

HERB_STAGES = [
    (0, "种子", "·"),
    (8, "破土", "♩"),
    (20, "幼苗", "♧"),
    (38, "展叶", "☘"),
    (62, "含苞", "⚘"),
    (85, "灵花", "✿"),
    (100, "道果", "❋"),
]


def inventory_map(conn) -> dict[str, dict[str, Any]]:
    return {row["item_key"]: dict(row) for row in conn.execute("SELECT * FROM inventory_items")}


def equipped_artifact(conn) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM inventory_items
        WHERE item_type='artifact' AND equipped=1
        ORDER BY updated_at DESC,id DESC LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    spec = ARTIFACTS.get(str(row["item_key"]))
    if not spec:
        return None
    item.update(spec)
    item["key"] = str(row["item_key"])
    item["max_level"] = int(spec.get("max_level", 1))
    return item


def building_level(conn, key: str) -> int:
    row = conn.execute("SELECT level FROM inventory_items WHERE item_key=? AND item_type='building'", (key,)).fetchone()
    return int(row["level"]) if row else 0


def building_cost(key: str, level: int) -> int:
    return int(BUILDINGS[key]["base_cost"] * max(1, level + 1))


def herb_stage(score: int) -> dict[str, Any]:
    selected = HERB_STAGES[0]
    for stage in HERB_STAGES:
        if score >= stage[0]:
            selected = stage
        else:
            break
    return {"threshold": selected[0], "name": selected[1], "icon": selected[2]}


def track_score(conn, track: Any) -> int:
    track_id = int(track["id"])
    tasks = conn.execute(
        "SELECT COUNT(*) total,COALESCE(SUM(status='done'),0) done FROM research_plan_items WHERE track_id=?",
        (track_id,),
    ).fetchone()
    entries = conn.execute("SELECT COUNT(*) n FROM entries WHERE domain=? AND status='active'", (track["name"],)).fetchone()["n"]
    direct_missions = conn.execute(
        "SELECT COUNT(*) n FROM daily_missions WHERE track_id=? AND completed=1", (track_id,)
    ).fetchone()["n"]
    keyword_missions = conn.execute(
        "SELECT COUNT(*) n FROM daily_missions WHERE completed=1 AND track_id IS NULL AND (title LIKE ? OR category LIKE ?)",
        (f"%{track['name']}%", f"%{track['name']}%"),
    ).fetchone()["n"]
    bonus = conn.execute("SELECT bonus_points FROM track_growth WHERE track_id=?", (track_id,)).fetchone()
    score = int(entries) * 5 + int(tasks["done"] or 0) * 12 + int(direct_missions) * 8 + int(keyword_missions) * 4 + int(bonus["bonus_points"] if bonus else 0)
    return min(100, score)


def garden(conn) -> list[dict[str, Any]]:
    items = []
    for row in conn.execute("SELECT * FROM research_tracks WHERE active=1 ORDER BY sort_order,id"):
        track = dict(row)
        score = track_score(conn, row)
        track["growth"] = score
        track["herb"] = herb_stage(score)
        items.append(track)
    return items
