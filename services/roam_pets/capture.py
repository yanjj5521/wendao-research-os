"""结契引擎：灵契球概率、三次摇动判定与野外结契共用。

设计（类似宝可梦/洛克王国）：
    p = capture_base × 球加成 × (2 − hp_ratio) × 元素加成 × 状态加成
    p 截断到 [0.05, 0.95]，摇三次判定（实现见 attempt）。
"""
from __future__ import annotations

import random

from .catalog import SPECIES, element_bonus

BALLS: dict[str, dict[str, object]] = {
    "basic": {"name": "契灵球", "bonus": 1.0, "price": 30},
    "great": {"name": "上品灵契球", "bonus": 1.6, "price": 80},
    "ultra": {"name": "极品灵契球", "bonus": 2.4, "price": 180},
}

SHAKE_COUNT = 3


def catch_probability(ball_id: str, species_id: str, hp_ratio: float = 1.0,
                      status_bonus: float = 1.0, player_element: str | None = None) -> float:
    species = SPECIES.get(species_id)
    ball = BALLS.get(ball_id)
    if not species or not ball:
        raise ValueError("未知的球或物种。")
    hp_ratio = max(0.05, min(1.0, float(hp_ratio)))
    rarity_mod = {"普通": 1.0, "稀有": 0.7, "史诗": 0.45}.get(species.rarity, 1.0)
    element_mod = element_bonus(player_element, species) if player_element else 1.0
    p = float(species.capture_base) * float(ball["bonus"]) * (2 - hp_ratio) * rarity_mod * element_mod * status_bonus
    return max(0.05, min(0.95, p))


def attempt(ball_id: str, species_id: str, hp_ratio: float = 1.0,
            rng: random.Random | None = None, **kwargs) -> dict:
    """完整一次投掷：三次摇动，全部通过即结契成功。返回结构化结果供 UI/日志使用。"""
    p = catch_probability(ball_id, species_id, hp_ratio, **kwargs)
    rng = rng or random.Random()
    shakes = [rng.random() < p for _ in range(SHAKE_COUNT)]
    caught = all(shakes)
    return {
        "ball": ball_id,
        "species_id": species_id,
        "probability": round(p, 4),
        "shakes": shakes,
        "caught": caught,
    }
