"""养成逻辑：领养、喂食、玩耍、时间衰减、等级与心情。全部纯函数，可测。"""
from __future__ import annotations

from datetime import datetime

from .catalog import SPECIES, STARTERS, get
from .models import PetState

MAX_LEVEL = 10
FEED_CAP = 95        # 饥饿达到该值后再喂食会被拒绝
PLAY_ENERGY_MIN = 15  # 体力低于该值拒绝玩耍


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_shape(data: dict) -> dict:
    """补齐缺失字段，容忍旧/坏存档。"""
    blank = {
        "version": 1,
        "active": None,
        "pets": {},
        "dex": {"seen": [], "caught": []},
        "window_pos": {},
    }
    for key, value in blank.items():
        data.setdefault(key, value)
    data["pets"] = {
        str(k): PetState.from_dict(v).to_dict() for k, v in data["pets"].items()
    }
    return data


def _mark(data: dict, species_id: str, caught: bool = False) -> None:
    dex = data["dex"]
    if species_id not in dex["seen"]:
        dex["seen"].append(species_id)
    if caught and species_id not in dex["caught"]:
        dex["caught"].append(species_id)


def adopt(data: dict, species_id: str, nickname: str = "") -> dict:
    species = get(species_id)
    if not species:
        raise ValueError("没有这个物种，无法领养。")
    data = ensure_shape(data)
    if species_id in data["pets"]:
        raise ValueError(f"{species.name} 已经是你的伙伴了。")
    if len(data["pets"]) >= 6:
        raise ValueError("宠物栏已满（6 只）。")
    state = PetState(
        species_id=species_id,
        nickname=(nickname or "").strip()[:12] or species.name,
        hatched_at=_now(),
        last_seen=_now(),
    )
    data["pets"][species_id] = state.to_dict()
    _mark(data, species_id, caught=True)
    if not data["active"]:
        data["active"] = species_id
    return state.to_dict()


def active_state(data: dict) -> tuple[str, dict] | None:
    data = ensure_shape(data)
    pet_id = data["active"]
    if pet_id and pet_id in data["pets"]:
        return pet_id, data["pets"][pet_id]
    for pet_id, state in data["pets"].items():
        data["active"] = pet_id
        return pet_id, state
    return None


def set_active(data: dict, pet_id: str) -> dict:
    data = ensure_shape(data)
    if pet_id not in data["pets"]:
        raise ValueError("这只宠物不在你的队伍里。")
    data["active"] = pet_id
    return data["pets"][pet_id]


def _clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(value)))


def _grant_exp(data: dict, pet_id: str, state: dict, amount: int) -> list[str]:
    events: list[str] = []
    species = SPECIES.get(pet_id)
    state["exp"] = int(state["exp"]) + amount
    while state["level"] < MAX_LEVEL and state["exp"] >= state["level"] * 10:
        state["exp"] -= state["level"] * 10
        state["level"] = int(state["level"]) + 1
        if species:
            events.append(f"{state['nickname']} 升到了 Lv.{state['level']}！")
    if state["level"] >= MAX_LEVEL:
        state["exp"] = 0
    return events


def feed(data: dict, pet_id: str | None = None) -> dict:
    """喂食：饥饿+，心情+，微量Bond。太饱会被拒绝。"""
    data = ensure_shape(data)
    pet_id = pet_id or data["active"]
    if pet_id not in data["pets"]:
        raise ValueError("先领养一只宠物吧。")
    state = data["pets"][pet_id]
    if int(state["hunger"]) >= FEED_CAP:
        raise ValueError(f"{state['nickname']} 现在很饱，吃不下更多了。")
    state["hunger"] = _clamp(int(state["hunger"]) + 22)
    state["mood"] = _clamp(int(state.get("mood", 50)) + 6)
    state["bond"] = _clamp(int(state.get("bond", 0)) + 1)
    events = _grant_exp(data, pet_id, state, 1)
    state["last_seen"] = _now()
    return {"state": state, "events": events}


def play(data: dict, pet_id: str | None = None) -> dict:
    """玩耍：体力-、饥饿-、Bond+、心情+。太累会被拒绝。"""
    data = ensure_shape(data)
    pet_id = pet_id or data["active"]
    if pet_id not in data["pets"]:
        raise ValueError("先领养一只宠物吧。")
    state = data["pets"][pet_id]
    if int(state["energy"]) < PLAY_ENERGY_MIN:
        raise ValueError(f"{state['nickname']} 累坏了，让它休息一会儿。")
    state["energy"] = _clamp(int(state["energy"]) - 18)
    state["hunger"] = _clamp(int(state["hunger"]) - 8)
    state["mood"] = _clamp(int(state.get("mood", 50)) + 10)
    state["bond"] = _clamp(int(state.get("bond", 0)) + 2)
    events = _grant_exp(data, pet_id, state, 2)
    state["last_seen"] = _now()
    return {"state": state, "events": events}


def tick(data: dict, now: datetime | None = None) -> dict:
    """按流失时间衰减饥饿、恢复体力（放置玩法入口）。"""
    data = ensure_shape(data)
    current = now or datetime.now().astimezone()
    for pet_id, state in data["pets"].items():
        species = SPECIES.get(pet_id)
        rate = species.appetite if species else 0.8
        try:
            last = datetime.fromisoformat(str(state["last_seen"]))
        except (TypeError, ValueError):
            last = current
        if last.tzinfo is None:
            last = last.astimezone()
        hours = max(0.0, (current - last).total_seconds() / 3600)
        state["hunger"] = _clamp(int(state["hunger"]) - rate * hours)
        state["energy"] = _clamp(int(state["energy"]) + 4 * hours)
        state["last_seen"] = current.isoformat(timespec="seconds")
    return data


def mood_of(state: dict) -> str:
    hunger, energy = int(state["hunger"]), int(state["energy"])
    if hunger < 25:
        return "饿了"
    if energy < 20:
        return "累了"
    if int(state.get("bond", 0)) >= 30 and hunger >= 55:
        return "开心"
    return "平静"


def speak_line(pet_id: str, mood: str) -> str:
    species = get(pet_id)
    if not species or not species.lines:
        return "……"
    index = {"开心": 0, "平静": 1, "饿了": 2, "累了": 1}.get(mood, 1) % len(species.lines)
    return species.lines[index]


def dex_view(data: dict) -> list[dict]:
    """图鉴视图：全部物种 + seen/caught 标记；未遇见的返回剪影标记。"""
    data = ensure_shape(data)
    seen = set(data["dex"]["seen"])
    caught = set(data["dex"]["caught"])
    rows = []
    for species_id, species in SPECIES.items():
        rows.append(
            {
                "id": species_id,
                "name": species.name if species_id in seen else "？？？",
                "element": species.element,
                "rarity": species.rarity,
                "title": species.title if species_id in seen else "尚未遇见",
                "flavor": species.flavor if species_id in caught else "——",
                "habitat": species.habitat,
                "status": "caught" if species_id in caught else ("seen" if species_id in seen else "unknown"),
            }
        )
    return rows


def starters_left(data: dict) -> list[str]:
    data = ensure_shape(data)
    return [sid for sid in STARTERS if sid not in data["pets"]]
