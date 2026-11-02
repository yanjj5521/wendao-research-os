"""地图场景：队伍最高等级决定迷雾林地、火山和月影湖的解锁。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MapScene:
    id: str
    name: str
    element_hint: str
    unlock_level: int
    desc: str
    encounters: tuple[tuple[str, int], ...] = field(default_factory=tuple)  # (species_id, weight)
    locked_note: str = ""


MAPS: dict[str, MapScene] = {
    "plains": MapScene(
        id="plains", name="起始平原", element_hint="草", unlock_level=1,
        desc="研究新手的第一片草地，苔背龟在这里晒太阳。",
        encounters=(("mosshell", 6), ("emberpup", 3), ("dewfrog", 3)),
    ),
    "mistwood": MapScene(
        id="mistwood", name="雾语林", element_hint="水·暗", unlock_level=3,
        desc="雾气里能听见翻书声，墨影猫在书架般的树影间穿行。",
        encounters=(("dewfrog", 6), ("umbrae", 1)),
        locked_note="队伍最高等级达标后解锁。",
    ),
    "crater": MapScene(
        id="crater", name="焰脊火山", element_hint="火·雷", unlock_level=5,
        desc="火山口附近雷雨不断，雷雀喜欢在火光里穿行。",
        encounters=(("emberpup", 6), ("voltfinch", 2)),
        locked_note="队伍最高等级达标后解锁。",
    ),
    "moonlake": MapScene(
        id="moonlake", name="月影湖", element_hint="光", unlock_level=7,
        desc="湖面只在无风的夜晚映出完整的月亮。",
        encounters=(("lunebunny", 6),),
        locked_note="队伍最高等级达标后解锁。",
    ),
}


def unlocked_maps(level: int = 1) -> list[MapScene]:
    return [m for m in MAPS.values() if level >= m.unlock_level]


def encounters_for(map_id: str) -> list[tuple[str, int]]:
    scene = MAPS.get(map_id)
    return list(scene.encounters) if scene else []
