"""物种目录：六只初始宠物，各有独特外观参数、属性与性格。

ELEMENT_CHART 供未来战斗/捕捉加成使用；STARTERS 是初始三选一。
"""
from __future__ import annotations

from .models import Species

SPECIES: dict[str, Species] = {
    "emberpup": Species(
        id="emberpup", name="焰尾犬", element="火", rarity="普通", title="火山脚下的冒失鬼",
        flavor="尾巴上有一簇永不熄灭的小火苗，激动时会喷出火星。",
        base_stats={"hp": 44, "atk": 58, "def": 40, "spd": 62},
        colors={"body": "#e8703c", "belly": "#ffd9a8", "accent": "#ffb14e", "extra": "#8c3a1e"},
        shape={"body_ratio": 0.78, "ear": "pointy", "tail": "flame", "wings": False, "pattern": "none"},
        appetite=0.9, temper="活泼", habitat="crater", capture_base=0.42,
        lines=("汪！今天也要全力以赴！", "闻到烤红薯的味道了吗？是我！", "别摸尾巴，会烫的！"),
    ),
    "dewfrog": Species(
        id="dewfrog", name="露珠蛙", element="水", rarity="普通", title="荷塘里的雨预报员",
        flavor="背上的露珠永远不会干，天要下雨时会先鼓起来。",
        base_stats={"hp": 52, "atk": 44, "def": 58, "spd": 48},
        colors={"body": "#4e9e6f", "belly": "#d8f0e2", "accent": "#8fd4b4", "extra": "#2c6e4e"},
        shape={"body_ratio": 0.86, "ear": "none", "tail": "none", "wings": False, "pattern": "spots"},
        appetite=0.7, temper="温顺", habitat="mistwood", capture_base=0.42,
        lines=("呱——今天湿度刚好。", "背上的露珠，要留着喝吗？", "雨天我们一起出去吧。"),
    ),
    "mosshell": Species(
        id="mosshell", name="苔背龟", element="草", rarity="普通", title="背着小森林的老好人",
        flavor="壳上的苔藓是一小片会呼吸的森林，走得很慢但从不迷路。",
        base_stats={"hp": 62, "atk": 38, "def": 66, "spd": 30},
        colors={"body": "#7fae5a", "belly": "#e8ecc9", "accent": "#5b7a3c", "extra": "#3d5427"},
        shape={"body_ratio": 0.9, "ear": "none", "tail": "stub", "wings": False, "pattern": "stripes"},
        appetite=0.5, temper="温顺", habitat="plains", capture_base=0.42,
        lines=("慢一点，也没关系。", "壳上长了新苔藓，你看。", "急什么呢，先晒晒太阳。"),
    ),
    "voltfinch": Species(
        id="voltfinch", name="雷雀", element="雷", rarity="稀有", title="雨天电线上的闪电",
        flavor="落下来的时候带着一声轻雷，羽毛蓬起来就是充能完毕。",
        base_stats={"hp": 40, "atk": 62, "def": 36, "spd": 72},
        colors={"body": "#f2c94c", "belly": "#fff3c4", "accent": "#3d4b63", "extra": "#7f5bc0"},
        shape={"body_ratio": 0.68, "ear": "crest", "tail": "bolt", "wings": True, "pattern": "none"},
        appetite=1.1, temper="活泼", habitat="crater", capture_base=0.3,
        lines=("啾——滋！静电预警！", "落雷之前，先眨一下眼。", "你今天的效率，像闪电一样吗？"),
    ),
    "lunebunny": Species(
        id="lunebunny", name="月牙兔", element="光", rarity="稀有", title="月影湖边的新月",
        flavor="耳朵弯成一牙新月，夜里会发出淡淡的月光。",
        base_stats={"hp": 46, "atk": 50, "def": 44, "spd": 66},
        colors={"body": "#e9e4f5", "belly": "#ffffff", "accent": "#b9a6e8", "extra": "#6f5aa8"},
        shape={"body_ratio": 0.74, "ear": "long", "tail": "puff", "wings": False, "pattern": "none"},
        appetite=0.8, temper="胆小", habitat="moonlake", capture_base=0.3,
        lines=("月光下，思路会很清晰哦。", "别突然大声说话嘛……", "今晚的月亮，弯得像我耳朵。"),
    ),
    "umbrae": Species(
        id="umbrae", name="墨影猫", element="暗", rarity="史诗", title="档案馆里的高傲影子",
        flavor="只在书架的影子里打盹，醒来时会用尾巴替你翻页。",
        base_stats={"hp": 48, "atk": 60, "def": 46, "spd": 64},
        colors={"body": "#3a3f52", "belly": "#8d93ad", "accent": "#c9b3ff", "extra": "#141724"},
        shape={"body_ratio": 0.76, "ear": "pointy", "tail": "long", "wings": False, "pattern": "stripes"},
        appetite=0.75, temper="高傲", habitat="mistwood", capture_base=0.2,
        lines=("哼，勉强陪你研究一会儿。", "影子里比灯光下更适合思考。", "你的资料，我替你翻好了。"),
    ),
}

STARTERS = ("emberpup", "dewfrog", "mosshell")

# 元素克制：attacker → strong/weak（捕捉加成与未来对战共用）
ELEMENT_CHART: dict[str, dict[str, list[str]]] = {
    "火": {"strong": ["草"], "weak": ["水"]},
    "水": {"strong": ["火"], "weak": ["雷"]},
    "草": {"strong": ["水"], "weak": ["火"]},
    "雷": {"strong": ["水"], "weak": ["草"]},
    "光": {"strong": ["暗"], "weak": []},
    "暗": {"strong": ["光"], "weak": []},
}


def get(species_id: str) -> Species | None:
    return SPECIES.get(species_id)


def element_bonus(ball_element: str, target: Species) -> float:
    """捕捉/对战时的元素加成系数（预留）。"""
    chart = ELEMENT_CHART.get(ball_element, {"strong": [], "weak": []})
    if target.element in chart["strong"]:
        return 1.3
    if target.element in chart["weak"]:
        return 0.75
    return 1.0
