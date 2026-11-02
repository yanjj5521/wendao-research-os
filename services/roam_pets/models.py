"""数据模型：物种定义与宠物实例状态。

物种（Species）是静态目录数据；实例（PetState）是玩家的养成存档。
两者都可 JSON 序列化，存档见 storage.py。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

ELEMENTS = ("火", "水", "草", "雷", "光", "暗")
RARITIES = ("普通", "稀有", "史诗")
TEMPERS = ("活泼", "温顺", "高傲", "胆小")


@dataclass(frozen=True)
class Species:
    """一只漫游宠物的物种定义。

    colors/shape 驱动过程化渲染（frames.build_frame）：
    - colors: body/belly/accent/extra 四色，避开窗口透明键色
    - shape:  body_ratio 体型、ear 耳朵、tail 尾巴、wings 翅膀、pattern 花纹
    """

    id: str
    name: str
    element: str
    rarity: str
    title: str
    flavor: str
    base_stats: dict[str, int]
    colors: dict[str, str]
    shape: dict[str, object]
    appetite: float  # 每小时饥饿增量
    temper: str
    habitat: str  # 所属地图 id（maps.MAPS）
    capture_base: float  # 捕捉基础概率（capture.py）
    lines: tuple[str, ...] = field(default_factory=tuple)

    def stat(self, key: str) -> int:
        return int(self.base_stats.get(key, 5))


@dataclass
class PetState:
    species_id: str
    nickname: str
    level: int = 1
    exp: int = 0
    hunger: int = 55  # 0=快饿死 100=很饱
    energy: int = 80
    mood: int = 60
    bond: int = 0
    hatched_at: str = ""
    last_seen: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PetState":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})
